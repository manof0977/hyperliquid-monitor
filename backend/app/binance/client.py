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
    """Respects Binance 2400 weight/min budget; reserves headroom so priority chart clicks never block."""

    def __init__(self, max_per_min: int = 2150, bg_per_min: int = 1550, concurrency: int = 10):
        self.max_per_min = max_per_min
        self.bg_per_min = bg_per_min
        self.used_weight = 0
        self.window_reset = time.monotonic() + 60
        self.sem = asyncio.Semaphore(concurrency)
        self._lock = asyncio.Lock()

    async def acquire(self, weight: int = 2, priority: bool = False):
        while True:
            sleep_for = 0.0
            async with self._lock:
                now = time.monotonic()
                if now >= self.window_reset:
                    self.used_weight = 0
                    self.window_reset = now + 60
                ceiling = self.max_per_min if priority else self.bg_per_min
                if self.used_weight + weight <= ceiling:
                    self.used_weight += weight
                    return
                sleep_for = max(0.4, min(self.window_reset - now, 4.0 if priority else 12.0))
            log.debug("Rate-limit pacing (priority=%s, weight=%s), sleeping %.1fs", priority, self.used_weight, sleep_for)
            await asyncio.sleep(sleep_for)

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

    async def _get(self, path: str, params: dict | None = None, weight: int = 2, priority: bool = False):
        await self.limiter.acquire(weight, priority=priority)
        url = f"{self.base_url}{path}"
        backoff = 0.8
        for attempt in range(3):
            try:
                sess = await self.session()
                if priority:
                    async with sess.get(url, params=params) as resp:
                        self.limiter.observe_headers(resp.headers)
                        if resp.status in (429, 418):
                            retry_after = min(float(resp.headers.get("Retry-After", backoff * 2)), 4.0)
                            await asyncio.sleep(retry_after)
                            continue
                        if resp.status == 451:
                            self.connected = False
                        resp.raise_for_status()
                        data = await resp.json()
                        self.connected = True
                        self.last_error = None
                        self.last_ok_ts = int(time.time() * 1000)
                        return data
                else:
                    async with self.limiter.sem:
                        async with sess.get(url, params=params) as resp:
                            self.limiter.observe_headers(resp.headers)
                            if resp.status in (429, 418):
                                retry_after = min(float(resp.headers.get("Retry-After", backoff * 4)), 15.0)
                                log.warning("HTTP %s from Binance, backing off %.1fs", resp.status, retry_after)
                                await asyncio.sleep(retry_after)
                                continue
                            if resp.status == 451:
                                self.connected = False
                            resp.raise_for_status()
                            data = await resp.json()
                            self.connected = True
                            self.last_error = None
                            self.last_ok_ts = int(time.time() * 1000)
                            return data
            except aiohttp.ClientResponseError as exc:
                self.last_error = f"HTTP {exc.status}: {exc.message}"
                # 400 Invalid symbol / missing ratio history must NOT mark the whole Binance client disconnected
                if 400 <= exc.status < 500 and exc.status not in (429, 418):
                    raise
                if attempt == 2:
                    raise
                await asyncio.sleep(backoff)
                backoff *= 1.8
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                if attempt == 2:
                    if self.last_ok_ts is None or (int(time.time() * 1000) - self.last_ok_ts) > 90_000:
                        self.connected = False
                    raise
                await asyncio.sleep(backoff)
                backoff *= 1.8

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

    async def fetch_premium_index_all(self) -> dict[str, dict]:
        """Fetch live funding rate + mark/index basis across all USDⓈ-M contracts in a single call."""
        data = await self._get("/fapi/v1/premiumIndex", weight=10)
        out: dict[str, dict] = {}
        if not isinstance(data, list):
            return out
        for item in data:
            sym = item.get("symbol")
            if not sym:
                continue
            mark = float(item.get("markPrice", 0) or 0)
            idx = float(item.get("indexPrice", 0) or 0)
            fr = float(item.get("lastFundingRate", 0) or 0) * 100.0  # convert to %
            basis_pct = ((mark - idx) / idx * 100.0) if idx > 0 else 0.0
            out[sym] = {
                "funding_rate_pct": round(fr, 4),
                "mark_price": mark,
                "index_price": idx,
                "basis_pct": round(basis_pct, 4),
                "next_funding_time": int(item.get("nextFundingTime", 0) or 0),
            }
        return out

    async def fetch_symbol_sentiment(self, symbol: str, period: str = "1h") -> dict:
        """Fetch live Open Interest + Retail Global L/S Account Ratio + Top-Trader L/S Position Ratio."""
        p = period if period in ("5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d") else "1h"
        out: dict = {"symbol": symbol}

        async def _oi():
            try:
                d = await self._get("/fapi/v1/openInterest", params={"symbol": symbol}, weight=1)
                out["open_interest"] = float(d.get("openInterest", 0) or 0)
            except Exception:
                pass

        async def _global_ls():
            try:
                arr = await self._get(
                    "/futures/data/globalLongShortAccountRatio",
                    params={"symbol": symbol, "period": p, "limit": 2},
                    weight=1,
                )
                if isinstance(arr, list) and arr:
                    last = arr[-1]
                    out["retail_long_pct"] = round(float(last.get("longAccount", 0) or 0) * 100.0, 1)
                    out["retail_short_pct"] = round(float(last.get("shortAccount", 0) or 0) * 100.0, 1)
                    out["retail_ls_ratio"] = round(float(last.get("longShortRatio", 1) or 1), 2)
            except Exception:
                pass

        async def _top_ls():
            try:
                arr = await self._get(
                    "/futures/data/topLongShortPositionRatio",
                    params={"symbol": symbol, "period": p, "limit": 2},
                    weight=1,
                )
                if isinstance(arr, list) and arr:
                    last = arr[-1]
                    out["top_trader_long_pct"] = round(float(last.get("longAccount", 0) or 0) * 100.0, 1)
                    out["top_trader_short_pct"] = round(float(last.get("shortAccount", 0) or 0) * 100.0, 1)
                    out["top_trader_ls_ratio"] = round(float(last.get("longShortRatio", 1) or 1), 2)
            except Exception:
                pass

        await asyncio.gather(_oi(), _global_ls(), _top_ls())
        return out

    async def fetch_klines(
        self, symbol: str, interval: str, limit: int = 300, start_time: int | None = None, priority: bool = False
    ) -> list[tuple]:
        params: dict = {"symbol": symbol, "interval": interval, "limit": min(limit, 1500)}
        if start_time is not None:
            params["startTime"] = start_time
        w = 1 if limit < 100 else (2 if limit < 500 else 5)
        try:
            raw = await self._get("/fapi/v1/klines", params=params, weight=w, priority=priority)
        except Exception:
            if not priority:
                raise
            # Priority fallback to Binance Market-Data Vision mirror (separate rate-limit pool)
            sess = await self.session()
            async with sess.get("https://data-api.binance.vision/api/v3/klines", params=params) as resp:
                resp.raise_for_status()
                raw = await resp.json()
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
