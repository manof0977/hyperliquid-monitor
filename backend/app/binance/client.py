"""Binance USDⓈ-M Perpetual Futures public market-data client.

Features:
- No API key required (public `/fapi/v1/*` endpoints + `wss://fstream.binance.com`).
- Token-bucket & header-aware (`X-MBX-USED-WEIGHT-1M`) rate-limit protection + HTTP 429/418 backoff.
- Incremental historical kline sync & missing-candle gap recovery (never re-downloads full history).
- Combined WebSocket stream (`!miniTicker@arr` + batched `<sym>@kline_<tf>`) with exponential reconnect.
"""
import asyncio
import json
import logging
import time
from typing import Callable, Awaitable
import aiohttp

from app.config import BINANCE_BASE_URL, BINANCE_WS_URL, TF_MS

log = logging.getLogger("binance")


class RateLimiter:
    """Respects Binance 2400 weight/min budget; keeps safe ceiling of 2000/min."""

    def __init__(self, max_per_min: int = 2000, concurrency: int = 12):
        self.max_per_min = max_per_min
        self.used_weight = 0
        self.window_reset = time.monotonic() + 60
        self.sem = asyncio.Semaphore(concurrency)
        self._lock = asyncio.Lock()

    async def acquire(self, weight: int = 2):
        async with self._lock:
            now = time.monotonic()
            if now >= self.window_reset:
                self.used_weight = 0
                self.window_reset = now + 60
            if self.used_weight + weight > self.max_per_min:
                sleep_for = max(0.5, self.window_reset - now)
                log.warning("Rate-limit budget near ceiling (%s), sleeping %.1fs", self.used_weight, sleep_for)
                await asyncio.sleep(sleep_for)
                self.used_weight = 0
                self.window_reset = time.monotonic() + 60
            self.used_weight += weight

    def observe_headers(self, headers):
        w = headers.get("X-MBX-USED-WEIGHT-1M") or headers.get("x-mbx-used-weight-1m")
        if w and str(w).isdigit():
            self.used_weight = max(self.used_weight, int(w))


class BinanceFuturesClient:
    def __init__(self, base_url: str = BINANCE_BASE_URL, ws_url: str = BINANCE_WS_URL):
        self.base_url = base_url.rstrip("/")
        self.ws_url = ws_url.rstrip("/")
        self.limiter = RateLimiter()
        self._session: aiohttp.ClientSession | None = None
        self.connected: bool = False
        self.ws_connected: bool = False
        self.last_error: str | None = None
        self.last_ok_ts: int | None = None

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15, connect=8)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: dict | None = None, weight: int = 2):
        await self.limiter.acquire(weight)
        url = f"{self.base_url}{path}"
        backoff = 1.0
        for attempt in range(3):
            try:
                async with self.limiter.sem:
                    sess = await self.session()
                    async with sess.get(url, params=params) as resp:
                        self.limiter.observe_headers(resp.headers)
                        if resp.status in (429, 418):
                            retry_after = float(resp.headers.get("Retry-After", backoff * 4))
                            log.warning("HTTP %s from Binance, backing off %.1fs", resp.status, retry_after)
                            await asyncio.sleep(retry_after)
                            continue
                        resp.raise_for_status()
                        data = await resp.json()
                        self.connected = True
                        self.last_error = None
                        self.last_ok_ts = int(time.time() * 1000)
                        return data
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                if attempt == 2:
                    self.connected = False
                    raise
                await asyncio.sleep(backoff)
                backoff *= 2

    async def discover_usdt_perpetuals(self) -> list[dict]:
        """Auto-discover contracts: quoteAsset=USDT, contractType=PERPETUAL, status=TRADING."""
        info = await self._get("/fapi/v1/exchangeInfo", weight=1)
        out: list[dict] = []
        for s in info.get("symbols", []):
            if (
                s.get("quoteAsset") == "USDT"
                and s.get("contractType") == "PERPETUAL"
                and s.get("status") == "TRADING"
            ):
                tick = 0.01
                for f in s.get("filters", []):
                    if f.get("filterType") == "PRICE_FILTER":
                        tick = float(f.get("tickSize", 0.01))
                out.append({
                    "symbol": s["symbol"],
                    "baseAsset": s.get("baseAsset", ""),
                    "pricePrecision": int(s.get("pricePrecision", 4)),
                    "tickSize": tick,
                })
        return out

    async def fetch_24h_tickers(self) -> dict[str, dict]:
        data = await self._get("/fapi/v1/ticker/24hr", weight=40)
        out: dict[str, dict] = {}
        for t in data:
            sym = t.get("symbol")
            if not sym:
                continue
            out[sym] = {
                "symbol": sym,
                "price": float(t.get("lastPrice", 0) or 0),
                "change_24h": float(t.get("priceChangePercent", 0) or 0),
                "volume_24h": float(t.get("quoteVolume", 0) or 0),
                "high_24h": float(t.get("highPrice", 0) or 0),
                "low_24h": float(t.get("lowPrice", 0) or 0),
            }
        return out

    async def fetch_klines(
        self, symbol: str, interval: str, limit: int = 300, start_time: int | None = None
    ) -> list[tuple]:
        params: dict = {"symbol": symbol, "interval": interval, "limit": min(limit, 1500)}
        if start_time is not None:
            params["startTime"] = start_time
        w = 1 if limit < 100 else (2 if limit < 500 else 5)
        raw = await self._get("/fapi/v1/klines", params=params, weight=w)
        return [
            (int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), int(k[6]))
            for k in raw
        ]

    async def sync_symbol_tf(
        self, symbol: str, tf: str, last_open_time: int | None, target_limit: int = 300
    ) -> list[tuple]:
        """Incremental fetch + missing-candle recovery without re-downloading full history."""
        step = TF_MS[tf]
        now_ms = int(time.time() * 1000)
        if last_open_time is None:
            return await self.fetch_klines(symbol, tf, limit=target_limit)
        gap_bars = max(2, int((now_ms - last_open_time) // step) + 2)
        if gap_bars >= target_limit:
            return await self.fetch_klines(symbol, tf, limit=target_limit)
        # Re-fetch from last_open_time so the previously-forming candle is refreshed + any missed bars recovered
        return await self.fetch_klines(symbol, tf, limit=min(gap_bars + 2, 1000), start_time=last_open_time)

    async def run_combined_ws(
        self,
        streams: list[str],
        on_ticker: Callable[[list[dict]], Awaitable[None]],
        on_kline: Callable[[str, str, tuple, bool], Awaitable[None]],
        stop_event: asyncio.Event,
    ):
        """Combined stream connection (max 180 streams/socket — Binance allows 200) with auto-reconnect."""
        backoff = 2.0
        stream_path = "/".join(streams[:180])
        url = f"{self.ws_url}/stream?streams={stream_path}"
        while not stop_event.is_set():
            try:
                sess = await self.session()
                async with sess.ws_connect(url, heartbeat=20) as ws:
                    self.ws_connected = True
                    self.connected = True
                    backoff = 2.0
                    log.info("Binance combined WS connected (%d streams)", min(len(streams), 180))
                    async for msg in ws:
                        if stop_event.is_set():
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            payload = json.loads(msg.data)
                            stream = payload.get("stream", "")
                            data = payload.get("data")
                            if stream == "!miniTicker@arr" and isinstance(data, list):
                                await on_ticker(data)
                            elif "@kline_" in stream and isinstance(data, dict):
                                k = data.get("k", {})
                                row = (
                                    int(k["t"]), float(k["o"]), float(k["h"]),
                                    float(k["l"]), float(k["c"]), float(k["v"]), int(k["T"]),
                                )
                                await on_kline(data.get("s", k.get("s", "")), k.get("i", ""), row, bool(k.get("x")))
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except Exception as exc:
                self.ws_connected = False
                self.last_error = f"WS {type(exc).__name__}: {exc}"
                log.warning("WS disconnected (%s); reconnecting in %.1fs", exc, backoff)
            await asyncio.sleep(min(backoff, 30.0))
            backoff = min(backoff * 1.8, 30.0)
        self.ws_connected = False
