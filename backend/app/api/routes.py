"""REST + WebSocket API routes."""
import asyncio
from dataclasses import asdict
import time
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.config import settings, TIMEFRAMES, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from app.db import get_watchlist, list_alerts, load_candles_df, save_kv, toggle_watchlist
from app.alerts.telegram import send_telegram_raw
from app.backtest.engine import run_backtest, summarize_trades

router = APIRouter(prefix="/api")


def get_engine():
    from app.main import engine
    return engine


@router.get("/status")
async def api_status():
    return get_engine().status_snapshot()


@router.post("/scan/trigger")
async def api_trigger_scan():
    eng = get_engine()
    if eng.client.connected and not eng.scanning and eng.symbols_meta:
        ranked = sorted(
            eng.symbols_meta.keys(),
            key=lambda s: eng.tickers.get(s, {}).get("volume_24h", 0.0),
            reverse=True,
        )[: settings.scan_limit]
        asyncio.create_task(eng.scan_symbols(ranked))
    return {"triggered": True, "scanning": eng.scanning, "direct_connected": eng.client.connected}


class BridgeBootstrapBody(BaseModel):
    source: str = "fapi.binance.com"
    symbols: list[dict] = []
    tickers: list[dict] = []


class BridgeKlinesBody(BaseModel):
    symbol: str
    source: str = "fapi.binance.com"
    frames: dict[str, list[list]]


class BridgeWsBatchBody(BaseModel):
    ws_open: bool = True
    tickers: list[dict] | None = None
    klines: list[dict] | None = None


@router.get("/bridge/plan")
async def api_bridge_plan():
    return await get_engine().get_bridge_sync_plan()


@router.post("/bridge/bootstrap")
async def api_bridge_bootstrap(body: BridgeBootstrapBody):
    eng = get_engine()
    await eng.ingest_bridge_bootstrap(body.symbols, body.tickers, source=body.source)
    return {"ok": True, "eligible_contracts": len(eng.symbols_meta), "tickers": len(eng.tickers)}


@router.post("/bridge/klines")
async def api_bridge_klines(body: BridgeKlinesBody):
    eng = get_engine()
    res = await eng.ingest_bridge_klines(body.symbol, body.frames, source=body.source)
    return {"ok": True, "symbol": body.symbol.upper(), "analyzed": res is not None, "status": res.get("status") if res else None}


@router.post("/bridge/ws")
async def api_bridge_ws(body: BridgeWsBatchBody):
    eng = get_engine()
    await eng.ingest_bridge_ws_batch(body.tickers, body.klines, ws_open=body.ws_open)
    return {"ok": True}


@router.get("/scanner")
async def api_scanner(
    search: str = "",
    status: str = "ALL",
    early_only: bool = False,
    min_score: float = 0.0,
    sort_by: str = "long_opp",
):
    eng = get_engine()
    rows = list(eng.results.values())
    # Also surface discovered contracts whose live tickers are known even before MTF scan completes
    if not rows and eng.tickers:
        for sym, tk in list(eng.tickers.items())[:200]:
            if sym in eng.symbols_meta:
                rows.append({
                    "symbol": sym, "base_asset": eng.symbols_meta[sym]["baseAsset"],
                    "price": tk["price"], "change_24h": tk["change_24h"], "volume_24h": tk["volume_24h"],
                    "biases": {}, "long_tech_score": 0.0, "short_tech_score": 0.0,
                    "long_opp_score": 0.0, "short_opp_score": 0.0,
                    "status": "SCANNING", "early_watch": False, "early_reasons": [],
                })
    q = search.strip().upper()
    if q:
        rows = [r for r in rows if q in r["symbol"]]
    if status and status != "ALL":
        rows = [r for r in rows if r["status"] == status]
    if early_only:
        rows = [r for r in rows if r.get("early_watch")]
    if min_score > 0:
        rows = [r for r in rows if max(r.get("long_tech_score", 0), r.get("short_tech_score", 0)) >= min_score]

    key_map = {
        "long_tech": lambda r: r.get("long_tech_score", 0),
        "short_tech": lambda r: r.get("short_tech_score", 0),
        "long_opp": lambda r: r.get("long_opp_score", 0),
        "short_opp": lambda r: r.get("short_opp_score", 0),
        "best_opp": lambda r: max(r.get("long_opp_score", 0), r.get("short_opp_score", 0)),
        "volume": lambda r: r.get("volume_24h", 0),
        "change": lambda r: r.get("change_24h", 0),
    }
    rows.sort(key=key_map.get(sort_by, key_map["best_opp"]), reverse=True)
    return {"items": rows, "total": len(rows), "status": eng.status_snapshot()}


@router.get("/opportunities")
async def api_opportunities():
    """Top 20 qualifying LONG and SHORT opportunities — excludes weak (<developing threshold) setups."""
    eng = get_engine()
    min_th = settings.thresholds.developing
    min_opp = settings.thresholds.watch_min
    all_rows = list(eng.results.values())

    longs = [
        r for r in all_rows
        if r.get("long_tech_score", 0) >= min_th and r.get("long_opp_score", 0) >= min_opp
    ]
    shorts = [
        r for r in all_rows
        if r.get("short_tech_score", 0) >= min_th and r.get("short_opp_score", 0) >= min_opp
    ]
    longs.sort(key=lambda r: (r["long_opp_score"] * 0.55 + r["long_tech_score"] * 0.45), reverse=True)
    shorts.sort(key=lambda r: (r["short_opp_score"] * 0.55 + r["short_tech_score"] * 0.45), reverse=True)
    early = [r for r in all_rows if r.get("early_watch")]
    early.sort(key=lambda r: max(r.get("long_tech_score", 0), r.get("short_tech_score", 0)), reverse=True)
    return {
        "min_qualifying_score": min_th,
        "longs": longs[:20],
        "shorts": shorts[:20],
        "early_watch": early[:20],
    }


@router.get("/coin/{symbol}")
async def api_coin(symbol: str, tf: str = Query("1h")):
    eng = get_engine()
    return await eng.build_chart_payload(symbol.upper(), tf)


@router.get("/symbols")
async def api_symbols():
    eng = get_engine()
    syms = sorted(eng.symbols_meta.keys()) if eng.symbols_meta else sorted(eng.results.keys())
    return {"symbols": syms}


@router.get("/watchlist")
async def api_get_watchlist():
    eng = get_engine()
    syms = await get_watchlist()
    items = [eng.results.get(s) or {"symbol": s, **(eng.tickers.get(s) or {}), "status": "UNSCANNED"} for s in syms]
    return {"symbols": syms, "items": items}


class WatchBody(BaseModel):
    symbol: str
    add: bool = True


@router.post("/watchlist")
async def api_post_watchlist(body: WatchBody):
    syms = await toggle_watchlist(body.symbol.upper(), body.add, int(time.time() * 1000))
    return {"symbols": syms}


@router.get("/alerts")
async def api_alerts():
    return {"items": await list_alerts(120)}


@router.post("/alerts/test")
async def api_test_alert():
    ok, detail = await send_telegram_raw("<b>Binance Intelligence Scanner</b> — test alert connection OK.")
    return {"ok": ok, "detail": detail}


@router.get("/settings")
async def api_get_settings():
    d = asdict(settings)
    d["telegram_bot_token"] = "***configured***" if (settings.telegram_bot_token or TELEGRAM_BOT_TOKEN) else ""
    d["env_telegram_present"] = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
    return d


class SettingsPatch(BaseModel):
    scan_limit: int | None = None
    scan_interval_sec: int | None = None
    swing_left: int | None = None
    swing_right: int | None = None
    equal_tolerance_atr: float | None = None
    ob_displacement_atr: float | None = None
    telegram_enabled: bool | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    backtest_fee_pct: float | None = None
    backtest_slippage_pct: float | None = None
    thresholds: dict | None = None


@router.put("/settings")
async def api_put_settings(patch: SettingsPatch):
    data = patch.model_dump(exclude_none=True)
    th = data.pop("thresholds", None)
    for k, v in data.items():
        if k == "telegram_bot_token" and v == "***configured***":
            continue
        if hasattr(settings, k):
            setattr(settings, k, v)
    if th:
        for k, v in th.items():
            if hasattr(settings.thresholds, k):
                setattr(settings.thresholds, k, int(v))
    await save_kv("settings", asdict(settings))
    return await api_get_settings()


class BacktestReq(BaseModel):
    symbols: list[str] = ["BTCUSDT"]
    timeframes: list[str] = ["1h"]
    min_tech_score: float = 60.0
    allow_developing: bool = True
    fee_pct: float | None = None
    slippage_pct: float | None = None


@router.post("/backtest")
async def api_backtest(req: BacktestReq):
    eng = get_engine()
    all_trades: list[dict] = []
    notes: list[str] = []
    for sym in req.symbols[:10]:
        s = sym.upper()
        for tf in req.timeframes:
            if tf not in TIMEFRAMES:
                continue
            df = eng.candle_cache.get((s, tf))
            if df is None or len(df) < 90:
                if eng.client.connected:
                    try:
                        rows = await eng.client.fetch_klines(s, tf, limit=500)
                        from app.db import upsert_candles
                        await upsert_candles(s, tf, rows)
                    except Exception as exc:
                        notes.append(f"{s} {tf}: live fetch unavailable ({exc})")
                df = await load_candles_df(s, tf, limit=500)
                if df.empty:
                    eng.queue_bridge_symbol(s)
                    notes.append(f"{s} {tf}: queued for live Binance sync — re-run in a few seconds once synced.")
            rep = await asyncio.to_thread(
                run_backtest, s, tf, df, req.min_tech_score, req.allow_developing, req.fee_pct, req.slippage_pct
            )
            if rep.get("note"):
                notes.append(f"{s} {tf}: {rep['note']}")
            all_trades.extend(rep.get("trades", []))

    summary = summarize_trades(
        all_trades,
        symbol=",".join(req.symbols[:5]),
        tf=",".join(req.timeframes),
    )
    if notes and summary["total_trades"] == 0:
        summary["note"] = " | ".join(notes[:4])
    return summary


@router.websocket("/ws")
async def api_ws(ws: WebSocket):
    await ws.accept()
    eng = get_engine()
    try:
        while True:
            top = sorted(
                eng.results.values(),
                key=lambda r: max(r.get("long_opp_score", 0), r.get("short_opp_score", 0)),
                reverse=True,
            )[:30]
            await ws.send_json({"type": "tick", "status": eng.status_snapshot(), "top": top})
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        return
    except Exception:
        return
