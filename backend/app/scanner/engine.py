"""Background Scanner Engine — orchestrates symbol discovery, incremental kline sync,
combined WebSocket live streams, multi-TF analysis, caching & alert dispatch.

NEVER fabricates prices or candles: if Binance cannot be reached (e.g. restricted network),
`connection_status` reports `disconnected` with the exact network error and zero fake rows.
"""
import asyncio
import logging
import time
import pandas as pd

from app.binance.client import BinanceFuturesClient
from app.config import TIMEFRAMES, settings
from app.db import (
    CANDLE_COLS, last_candle_time, load_candles_df, save_symbols, upsert_candles
)
from app.analysis.pipeline import analyze_symbol, analyze_timeframe
from app.analysis.narrative import build_plain_english_report
from app.alerts.telegram import maybe_emit_alert

log = logging.getLogger("scanner")


class ScannerEngine:
    def __init__(self):
        self.client = BinanceFuturesClient()
        self.symbols_meta: dict[str, dict] = {}       # all eligible USDT PERPETUAL contracts
        self.tickers: dict[str, dict] = {}            # live 24h price/chg/vol for all contracts
        self.results: dict[str, dict] = {}            # MTF analysis summary keyed by symbol
        self.candle_cache: dict[tuple[str, str], pd.DataFrame] = {}
        self.last_scan_ts: int | None = None
        self.scanning: bool = False
        self.scan_progress: tuple[int, int] = (0, 0)
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._ws_task: asyncio.Task | None = None
        self._ws_symbols: list[str] = []
        self.bridge_active: bool = False
        self.bridge_ws_active: bool = False
        self.bridge_source: str | None = None
        self.bridge_last_ts: int | None = None
        self._on_demand_requests: list[dict] = []  # queued (symbol, tf) requests for browser bridge
        self._last_tf_sync: dict[tuple[str, str], int] = {}
        self.funding_cache: dict[str, dict] = {}
        self.sentiment_cache: dict[str, dict] = {}

    async def start(self):
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._bootstrap_and_loop(), name="scanner-loop"),
            asyncio.create_task(self._ticker_refresh_loop(), name="ticker-loop"),
        ]

    async def stop(self):
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        if self._ws_task:
            self._ws_task.cancel()
        await self.client.close()

    def status_snapshot(self) -> dict:
        results = list(self.results.values())
        long_cands = sum(1 for r in results if r["long_tech_score"] >= settings.thresholds.developing)
        short_cands = sum(1 for r in results if r["short_tech_score"] >= settings.thresholds.developing)
        confirmed = sum(1 for r in results if "CONFIRMED" in r["status"])
        developing = sum(1 for r in results if "DEVELOPING" in r["status"])
        early = sum(1 for r in results if r.get("early_watch"))
        now_ms = int(time.time() * 1000)
        bridge_fresh = bool(self.bridge_last_ts and (now_ms - self.bridge_last_ts) < 90_000)
        if not bridge_fresh:
            self.bridge_active = False
            self.bridge_ws_active = False
        connected = self.client.connected or bridge_fresh
        ws_live = self.client.ws_connected or self.bridge_ws_active
        feed_mode = (
            "direct" if self.client.connected
            else (f"browser-bridge ({self.bridge_source})" if bridge_fresh else "offline")
        )
        return {
            "binance_connected": connected,
            "direct_connected": self.client.connected,
            "bridge_connected": bridge_fresh,
            "feed_mode": feed_mode,
            "ws_connected": ws_live,
            "last_error": None if connected else self.client.last_error,
            "last_ok_ts": self.client.last_ok_ts or self.bridge_last_ts,
            "last_scan_ts": self.last_scan_ts,
            "scanning": self.scanning,
            "scan_progress": {"done": self.scan_progress[0], "total": self.scan_progress[1]},
            "eligible_contracts": len(self.symbols_meta),
            "total_scanned": len(results),
            "long_candidates": long_cands,
            "short_candidates": short_cands,
            "confirmed_signals": confirmed,
            "developing_setups": developing,
            "early_watch_count": early,
        }

    async def _bootstrap_and_loop(self):
        while not self._stop.is_set():
            try:
                if not self.symbols_meta:
                    discovered = await self.client.discover_usdt_perpetuals()
                    now_ms = int(time.time() * 1000)
                    await save_symbols(discovered, now_ms)
                    self.symbols_meta = {d["symbol"]: d for d in discovered}
                    log.info("Discovered %d USDT PERPETUAL TRADING contracts", len(self.symbols_meta))
                elif not self.client.connected:
                    # Probe direct backend reachability without clobbering active browser-bridge data
                    await self.client.discover_usdt_perpetuals()

                tickers = await self.client.fetch_24h_tickers()
                for sym, t in tickers.items():
                    if sym in self.symbols_meta:
                        self.tickers[sym] = t
                try:
                    self.funding_cache.update(await self.client.fetch_premium_index_all())
                except Exception:
                    pass

                # Rank by 24h USDT quote volume so liquid contracts are scanned first; scan_limit <= 0 scans ALL coins
                ranked = sorted(
                    self.symbols_meta.keys(),
                    key=lambda s: self.tickers.get(s, {}).get("volume_24h", 0.0),
                    reverse=True,
                )
                target_syms = ranked if settings.scan_limit <= 0 else ranked[: settings.scan_limit]
                self._ensure_ws(target_syms)
                await self.scan_symbols(target_syms)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("Scan cycle error (will retry): %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(10, settings.scan_interval_sec))
            except asyncio.TimeoutError:
                pass

    async def _ticker_refresh_loop(self):
        """Fallback REST ticker refresh when WS miniTicker is unavailable."""
        while not self._stop.is_set():
            await asyncio.sleep(20)
            if self.client.ws_connected or not self.symbols_meta:
                continue
            try:
                tickers = await self.client.fetch_24h_tickers()
                for sym, t in tickers.items():
                    if sym in self.symbols_meta:
                        self.tickers[sym] = t
            except Exception:
                pass

    def _ensure_ws(self, top_symbols: list[str]):
        top_set = top_symbols[:50]  # 1 !miniTicker@arr + 50*3 kline streams = 151 <= 200 Binance cap
        if self._ws_task and not self._ws_task.done() and top_set == self._ws_symbols:
            return
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
        self._ws_symbols = top_set
        streams = ["!miniTicker@arr"]
        for s in top_set:
            sl = s.lower()
            for tf in ("5m", "15m", "1h"):
                streams.append(f"{sl}@kline_{tf}")
        self._ws_task = asyncio.create_task(
            self.client.run_combined_ws(streams, self._on_mini_tickers, self._on_ws_kline, self._stop),
            name="binance-ws",
        )

    async def _on_mini_tickers(self, items: list[dict]):
        for it in items:
            sym = it.get("s")
            if not sym or sym not in self.symbols_meta:
                continue
            close_p = float(it.get("c", 0) or 0)
            open_p = float(it.get("o", 0) or 0)
            chg = ((close_p - open_p) / open_p * 100.0) if open_p > 0 else 0.0
            cur = self.tickers.setdefault(sym, {"symbol": sym})
            cur["price"] = close_p
            cur["change_24h"] = round(chg, 3)
            cur["volume_24h"] = float(it.get("q", cur.get("volume_24h", 0)) or 0)
            cur["high_24h"] = float(it.get("h", cur.get("high_24h", 0)) or 0)
            cur["low_24h"] = float(it.get("l", cur.get("low_24h", 0)) or 0)
            if sym in self.results:
                self.results[sym]["price"] = close_p
                self.results[sym]["change_24h"] = cur["change_24h"]
                self.results[sym]["volume_24h"] = cur["volume_24h"]

    async def _on_ws_kline(self, symbol: str, tf: str, row: tuple, is_closed: bool):
        if not symbol or not tf:
            return
        key = (symbol, tf)
        df = self.candle_cache.get(key)
        if df is not None and not df.empty:
            if int(df.iloc[-1]["open_time"]) == row[0]:
                df.iloc[-1] = list(row)
            elif row[0] > int(df.iloc[-1]["open_time"]):
                new_df = pd.DataFrame([row], columns=CANDLE_COLS)
                self.candle_cache[key] = pd.concat([df, new_df], ignore_index=True).tail(400)
        if is_closed:
            await upsert_candles(symbol, tf, [row])
            # Re-evaluate symbol incrementally on closed candle
            if symbol in self.results:
                asyncio.create_task(self.analyze_one(symbol, sync_network=False))

    async def ensure_symbol_frames(self, symbol: str, sync_network: bool = True) -> dict[str, pd.DataFrame]:
        frames: dict[str, pd.DataFrame] = {}
        now_ms = int(time.time() * 1000)
        ttl_map = {"5m": 60_000, "15m": 120_000, "1h": 180_000, "4h": 300_000, "1d": 600_000, "1w": 900_000}

        async def _load_one(tf: str):
            key = (symbol, tf)
            target_bars = 99 if tf in ("1d", "1w") else settings.kline_limit
            last_sync = self._last_tf_sync.get(key, 0)
            if sync_network and (now_ms - last_sync) >= ttl_map.get(tf, 60_000):
                last_t = await last_candle_time(symbol, tf)
                rows = await self.client.sync_symbol_tf(symbol, tf, last_t, target_limit=target_bars)
                await upsert_candles(symbol, tf, rows)
                self._last_tf_sync[key] = now_ms
            df = self.candle_cache.get(key)
            if df is None or df.empty or sync_network:
                df = await load_candles_df(symbol, tf, limit=max(settings.kline_limit, 320))
            if not df.empty:
                self.candle_cache[key] = df
                frames[tf] = df
            elif key in self.candle_cache:
                frames[tf] = self.candle_cache[key]

        await asyncio.gather(*(_load_one(tf) for tf in TIMEFRAMES))
        return frames

    async def analyze_one(self, symbol: str, sync_network: bool = True) -> dict | None:
        frames = await self.ensure_symbol_frames(symbol, sync_network=sync_network)
        if not frames or "1h" not in frames:
            return None
        prev = self.results.get(symbol)
        prev_status = prev.get("status") if prev else None
        # Offload CPU-bound pandas/numpy work to worker thread so event loop stays snappy
        scored = await asyncio.to_thread(analyze_symbol, frames, prev)
        if not scored:
            return None
        tk = self.tickers.get(symbol, {})
        fallback_p = float(next(iter(frames.values()))["close"].iloc[-1])
        price = tk.get("price") or fallback_p
        record = {
            "symbol": symbol,
            "base_asset": self.symbols_meta.get(symbol, {}).get("baseAsset", symbol.replace("USDT", "")),
            "price": price,
            "change_24h": tk.get("change_24h", 0.0),
            "volume_24h": tk.get("volume_24h", 0.0),
            "updated_at": int(time.time() * 1000),
            **{k: v for k, v in scored.items() if k != "mtf_details"},
        }
        self.results[symbol] = record
        await maybe_emit_alert(symbol, prev_status, record, price)
        return {**record, "mtf_details": scored["mtf_details"]}

    async def scan_symbols(self, symbols: list[str]):
        self.scanning = True
        self.scan_progress = (0, len(symbols))
        batch = 10
        done = 0
        try:
            for i in range(0, len(symbols), batch):
                if self._stop.is_set():
                    break
                chunk = symbols[i: i + batch]
                await asyncio.gather(*(self._safe_analyze(s) for s in chunk))
                done += len(chunk)
                self.scan_progress = (done, len(symbols))
            self.last_scan_ts = int(time.time() * 1000)
        finally:
            self.scanning = False

    async def _safe_analyze(self, symbol: str):
        try:
            await self.analyze_one(symbol, sync_network=True)
        except Exception as exc:
            log.debug("Symbol %s scan skipped: %s", symbol, exc)

    async def build_chart_payload(self, symbol: str, tf: str) -> dict:
        """Full OHLCV + EMAs + structural overlays + active trade levels + Plain-English report."""
        symbol = symbol.upper()
        if tf not in TIMEFRAMES:
            tf = "1h"
        df = self.candle_cache.get((symbol, tf))
        if df is None or df.empty:
            if self.client.connected:
                try:
                    frames = await self.ensure_symbol_frames(symbol, sync_network=True)
                    df = frames.get(tf)
                except Exception:
                    df = await load_candles_df(symbol, tf, limit=320)
            else:
                df = await load_candles_df(symbol, tf, limit=320)
                if df.empty:
                    self.queue_bridge_symbol(symbol)
        if df is None or df.empty:
            summary = self.results.get(symbol)
            return {
                "symbol": symbol,
                "tf": tf,
                "candles": [],
                "overlays": None,
                "summary": summary,
                "explanation": build_plain_english_report(symbol, tf, None, summary),
            }

        d_df = self.candle_cache.get((symbol, "1d")) or await load_candles_df(symbol, "1d", 60)
        w_df = self.candle_cache.get((symbol, "1w")) or await load_candles_df(symbol, "1w", 60)
        tf_detail = await asyncio.to_thread(
            analyze_timeframe, df, d_df if not d_df.empty else None, w_df if not w_df.empty else None
        )
        if symbol not in self.results:
            try:
                await self.analyze_one(symbol, sync_network=False)
            except Exception:
                pass

        df_ind = tf_detail["df"] if tf_detail else df
        candles = [
            {
                "time": int(r.open_time // 1000),
                "open": float(r.open), "high": float(r.high),
                "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
                "ema20": float(r.ema20) if hasattr(r, "ema20") and pd.notna(r.ema20) else None,
                "ema50": float(r.ema50) if hasattr(r, "ema50") and pd.notna(r.ema50) else None,
                "ema100": float(r.ema100) if hasattr(r, "ema100") and pd.notna(r.ema100) else None,
                "ema200": float(r.ema200) if hasattr(r, "ema200") and pd.notna(r.ema200) else None,
                "rsi14": float(r.rsi14) if hasattr(r, "rsi14") and pd.notna(r.rsi14) else None,
            }
            for r in df_ind.itertuples()
        ]
        overlays = None
        if tf_detail:
            overlays = {
                "structure": tf_detail["structure"],
                "smc": tf_detail["smc"],
                "sr": tf_detail["sr"],
                "ind": tf_detail["ind"],
                "div": tf_detail["div"],
            }
        summary = self.results.get(symbol)
        sentiment = await self._get_symbol_sentiment(symbol, tf)
        return {
            "symbol": symbol,
            "tf": tf,
            "candles": candles,
            "overlays": overlays,
            "summary": summary,
            "sentiment": sentiment,
            "explanation": build_plain_english_report(symbol, tf, tf_detail, summary, sentiment=sentiment),
        }

    async def _get_symbol_sentiment(self, symbol: str, tf: str) -> dict | None:
        now_ms = int(time.time() * 1000)
        cached = self.sentiment_cache.get(symbol)
        if self.client.connected and (not cached or (now_ms - cached.get("_ts", 0)) > 180_000):
            try:
                fresh = await self.client.fetch_symbol_sentiment(symbol, period=tf)
                fresh["_ts"] = now_ms
                self.sentiment_cache[symbol] = fresh
                cached = fresh
            except Exception:
                pass
        merged: dict = {}
        if symbol in self.funding_cache:
            merged.update(self.funding_cache[symbol])
        if cached:
            merged.update({k: v for k, v in cached.items() if k != "_ts"})
        return merged if merged else None

    async def ingest_bridge_sentiment(self, symbol: str, data: dict):
        symbol = symbol.upper()
        now_ms = int(time.time() * 1000)
        if "funding_rate_pct" in data or "mark_price" in data:
            self.funding_cache[symbol] = {
                k: data[k] for k in ("funding_rate_pct", "mark_price", "index_price", "basis_pct", "next_funding_time") if k in data
            }
        entry = {**(self.sentiment_cache.get(symbol) or {}), **data, "_ts": now_ms}
        self.sentiment_cache[symbol] = entry

    # ---------- Browser-Direct Real Binance Bridge Ingestion ----------
    def queue_bridge_symbol(self, symbol: str):
        symbol = symbol.upper()
        if not any(x["symbol"] == symbol for x in self._on_demand_requests):
            self._on_demand_requests.append({"symbol": symbol, "tfs": list(TIMEFRAMES)})

    async def ingest_bridge_bootstrap(self, symbols: list[dict], tickers: list[dict], source: str = "fapi.binance.com"):
        now_ms = int(time.time() * 1000)
        self.bridge_active = True
        self.bridge_source = source
        self.bridge_last_ts = now_ms
        if symbols:
            await save_symbols(symbols, now_ms)
            self.symbols_meta = {d["symbol"]: d for d in symbols}
        if tickers:
            for t in tickers:
                sym = t.get("symbol")
                if sym and (not self.symbols_meta or sym in self.symbols_meta):
                    self.tickers[sym] = {
                        "symbol": sym,
                        "price": float(t.get("price", 0) or 0),
                        "change_24h": float(t.get("change_24h", 0) or 0),
                        "volume_24h": float(t.get("volume_24h", 0) or 0),
                        "high_24h": float(t.get("high_24h", 0) or 0),
                        "low_24h": float(t.get("low_24h", 0) or 0),
                    }
                    if sym in self.results:
                        self.results[sym]["price"] = self.tickers[sym]["price"]
                        self.results[sym]["change_24h"] = self.tickers[sym]["change_24h"]
                        self.results[sym]["volume_24h"] = self.tickers[sym]["volume_24h"]

    async def get_bridge_sync_plan(self) -> dict:
        """Return prioritized list of (symbol, tf, start_time, limit) jobs across ALL eligible contracts."""
        from app.config import TF_MS
        if self.client.connected:
            return {"needed": False, "jobs": [], "top_symbols": []}
        ranked = sorted(
            self.symbols_meta.keys(),
            key=lambda s: self.tickers.get(s, {}).get("volume_24h", 0.0),
            reverse=True,
        )
        target_ranked = ranked if settings.scan_limit <= 0 else ranked[: settings.scan_limit]

        # 1. Explicit on-demand coin clicks first, 2. Un-scanned contracts next (so ALL coins finish first!), 3. Stale refreshes
        priority_syms: list[str] = []
        while self._on_demand_requests:
            req = self._on_demand_requests.pop(0)
            if req["symbol"] not in priority_syms:
                priority_syms.append(req["symbol"])

        unscanned = [s for s in target_ranked if s not in self.results and s not in priority_syms]
        scanned = [s for s in target_ranked if s in self.results and s not in priority_syms]
        ordered = priority_syms + unscanned + scanned

        now_ms = int(time.time() * 1000)
        ttl_map = {"5m": 180_000, "15m": 240_000, "1h": 360_000, "4h": 600_000, "1d": 900_000, "1w": 1_800_000}
        jobs: list[dict] = []
        for sym in ordered:
            missing_tfs: list[dict] = []
            for tf in TIMEFRAMES:
                target_bars = 99 if tf in ("1d", "1w") else settings.kline_limit
                df = self.candle_cache.get((sym, tf))
                last_t = int(df.iloc[-1]["open_time"]) if (df is not None and not df.empty) else await last_candle_time(sym, tf)
                if last_t is None:
                    missing_tfs.append({"tf": tf, "limit": target_bars, "start_time": None})
                else:
                    last_sync = self._last_tf_sync.get((sym, tf), 0)
                    if (now_ms - last_sync) >= ttl_map.get(tf, 180_000):
                        step = TF_MS[tf]
                        gap = max(3, int((now_ms - last_t) // step) + 2)
                        missing_tfs.append({
                            "tf": tf,
                            "limit": min(gap, target_bars),
                            "start_time": last_t if gap < target_bars else None,
                        })
            if missing_tfs:
                jobs.append({"symbol": sym, "tfs": missing_tfs})
            if len(jobs) >= 18:
                break
        return {"needed": True, "jobs": jobs, "top_symbols": ranked[:40], "total_target": len(target_ranked)}

    async def ingest_bridge_klines(self, symbol: str, frames_raw: dict[str, list[list]], source: str = "fapi.binance.com") -> dict | None:
        symbol = symbol.upper()
        now_ms = int(time.time() * 1000)
        self.bridge_active = True
        self.bridge_source = source
        self.bridge_last_ts = now_ms
        for tf, raw_rows in frames_raw.items():
            if tf not in TIMEFRAMES or not raw_rows:
                continue
            rows = [
                (int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), int(k[6]))
                for k in raw_rows
            ]
            await upsert_candles(symbol, tf, rows)
            self._last_tf_sync[(symbol, tf)] = now_ms
            df = await load_candles_df(symbol, tf, limit=max(settings.kline_limit, 320))
            if not df.empty:
                self.candle_cache[(symbol, tf)] = df
        self.last_scan_ts = now_ms
        return await self.analyze_one(symbol, sync_network=False)

    async def ingest_bridge_ws_batch(self, tickers: list[dict] | None, klines: list[dict] | None, ws_open: bool = True):
        self.bridge_active = True
        self.bridge_ws_active = ws_open
        self.bridge_last_ts = int(time.time() * 1000)
        if tickers:
            await self._on_mini_tickers(tickers)
        if klines:
            for ev in klines:
                row = (
                    int(ev["t"]), float(ev["o"]), float(ev["h"]),
                    float(ev["l"]), float(ev["c"]), float(ev["v"]), int(ev["T"]),
                )
                await self._on_ws_kline(ev["s"], ev["i"], row, bool(ev.get("x")))

