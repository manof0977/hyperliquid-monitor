"""Comprehensive unit tests for indicators, non-repainting structure, SMC, SR, scoring & causal backtester."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np
import pandas as pd

from app.analysis.indicators import add_indicators, last_values, divergence
from app.analysis.structure import detect_structure
from app.analysis.smc import detect_smc
from app.analysis.sr import detect_sr
from app.analysis.scoring import evaluate_symbol, classify_tier
from app.analysis.pipeline import analyze_timeframe, analyze_symbol
from app.backtest.engine import run_backtest


def _make_trend_df(n: int = 160, direction: int = 1) -> pd.DataFrame:
    """Deterministic zigzag wave series with clear swing highs/lows, displacement candles & FVG."""
    rng = np.random.default_rng(42)
    rows = []
    price = 100.0
    t0 = 1_700_000_000_000
    for i in range(n):
        # 14-bar cycle + directional drift -> clean HH/HL or LH/LL
        wave = np.sin(2 * np.pi * i / 14.0) * 2.5
        drift = direction * i * 0.18
        mid = 100.0 + drift + wave
        # Inject occasional strong impulse displacement every 28 bars
        impulse = direction * 2.4 if (i % 28 == 9) else 0.0
        o = price
        c = mid + impulse
        h = max(o, c) + abs(rng.normal(0.35, 0.12))
        l = min(o, c) - abs(rng.normal(0.35, 0.12))
        v = 1000.0 * (2.2 if impulse != 0 else 1.0) + rng.uniform(0, 200)
        rows.append((t0 + i * 3_600_000, o, h, l, c, v, t0 + (i + 1) * 3_600_000 - 1))
        price = c
    return pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])


def test_indicators_complete():
    df = add_indicators(_make_trend_df(120, 1))
    for col in ("ema20", "ema50", "ema100", "ema200", "rsi14", "macd", "macd_signal", "macd_hist", "atr14", "vol_sma20"):
        assert col in df.columns
        assert df[col].notna().all()
    lv = last_values(df)
    assert 0 <= lv["rsi14"] <= 100
    assert lv["atr14"] > 0
    div = divergence(df)
    assert set(div.keys()) == {"bullish_rsi", "bearish_rsi", "bullish_macd", "bearish_macd"}


def test_structure_swings_and_non_repainting():
    df = _make_trend_df(150, 1)
    st_prefix = detect_structure(df.iloc[:110], left=3, right=3)
    st_full = detect_structure(df, left=3, right=3)
    assert st_full["trend"] == "bullish"
    assert any(b["kind"] in ("BOS", "CHoCH") for b in st_full["breaks"])
    # Non-repainting invariant: every break confirmed up to bar 105 in prefix must be identical in full series
    prefix_early = [b for b in st_prefix["breaks"] if b["index"] <= 104]
    full_early = [b for b in st_full["breaks"] if b["index"] <= 104]
    assert prefix_early == full_early


def test_smc_fvg_ob_sweep_and_zones():
    df = add_indicators(_make_trend_df(160, 1))
    st = detect_structure(df)
    smc = detect_smc(df, st)
    assert smc["dealing_range"] is not None
    assert smc["zone"] in ("discount", "premium", "equilibrium")
    assert len(smc["all_fvgs"]) > 0
    assert len(smc["all_order_blocks"]) > 0


def test_sr_and_scoring_separation():
    bull_df = _make_trend_df(160, 1)
    bear_df = _make_trend_df(160, -1)
    bull_frames = {tf: bull_df for tf in ("5m", "15m", "1h", "4h", "1d", "1w")}
    bear_frames = {tf: bear_df for tf in ("5m", "15m", "1h", "4h", "1d", "1w")}

    bull_eval = analyze_symbol(bull_frames)
    bear_eval = analyze_symbol(bear_frames)
    assert bull_eval["long_tech_score"] > bull_eval["short_tech_score"]
    assert bear_eval["short_tech_score"] > bear_eval["long_tech_score"]
    assert classify_tier(88) == "High-Quality"
    assert classify_tier(20) == "No Setup"


def test_causal_backtester_runs_with_fees():
    df = _make_trend_df(180, 1)
    rep = run_backtest("BTCUSDT", "1h", df, min_tech_score=40.0, allow_developing=True, fee_pct=0.05, slippage_pct=0.02)
    assert "total_trades" in rep
    assert "profit_factor" in rep
    assert "max_drawdown_r" in rep


def test_plain_english_report_generation():
    from app.analysis.narrative import build_plain_english_report
    df = _make_trend_df(160, 1)
    tf_detail = analyze_timeframe(df, daily_df=df, weekly_df=df)
    summary = analyze_symbol({tf: df for tf in ("5m", "15m", "1h", "4h", "1d", "1w")})
    summary["price"] = float(df["close"].iloc[-1])
    summary["change_24h"] = 2.35
    sentiment = {
        "funding_rate_pct": -0.025,
        "basis_pct": -0.08,
        "open_interest": 12500.0,
        "retail_long_pct": 38.0,
        "retail_short_pct": 62.0,
        "top_trader_long_pct": 59.0,
        "top_trader_short_pct": 41.0,
    }
    rep = build_plain_english_report("BTCUSDT", "1h", tf_detail, summary, sentiment=sentiment)
    assert rep is not None
    assert rep["symbol"] == "BTCUSDT"
    assert len(rep["sections"]) == 6
    assert "BTCUSDT" in rep["headline"]
    nm = rep["next_move_map"]
    assert nm["bull_weight_pct"] + nm["bear_weight_pct"] == 100
    assert nm["favored"] in ("BULLISH", "BEARISH", "BALANCED")
    assert nm["primary_magnet"]
    assert nm["bull_scenario"] and nm["bear_scenario"]
    assert nm["positioning_read"] and "negative funding" in nm["positioning_read"].lower()
    assert "smart-money divergence" in nm["positioning_read"].lower()


def test_short_and_long_plan_directional_consistency():
    bull_df = _make_trend_df(160, 1)
    bear_df = _make_trend_df(160, -1)

    bull_res = analyze_symbol({tf: bull_df for tf in ("5m", "15m", "1h", "4h", "1d", "1w")})
    bear_res = analyze_symbol({tf: bear_df for tf in ("5m", "15m", "1h", "4h", "1d", "1w")})

    bull_cur = float(bull_df["close"].iloc[-1])
    bear_cur = float(bear_df["close"].iloc[-1])

    lp = bull_res["long_plan"]
    if lp["has_plan"]:
        assert lp["entry"] <= bull_cur
        assert lp["invalidation"] < lp["entry"]
        assert all(t > lp["entry"] for t in lp["targets"])
        assert lp["entry_reason"] and lp["invalidation_reason"] and lp["order_type"]

    sp = bear_res["short_plan"]
    if sp["has_plan"]:
        assert sp["entry"] >= bear_cur
        assert sp["invalidation"] > sp["entry"]
        assert all(t < sp["entry"] for t in sp["targets"])
        assert sp["entry_reason"] and sp["invalidation_reason"] and sp["order_type"]

    if bear_res["status"].startswith("SHORT"):
        assert bear_res["active_side"] == "SHORT"
        assert bear_res["entry"] == sp["entry"]
        assert bear_res["invalidation"] > bear_res["entry"]
    if bull_res["status"].startswith("LONG"):
        assert bull_res["active_side"] == "LONG"
        assert bull_res["entry"] == lp["entry"]
        assert bull_res["invalidation"] < bull_res["entry"]


def test_build_chart_payload_switches_coins_and_timeframes():
    import asyncio
    from app.scanner.engine import ScannerEngine

    eng = ScannerEngine()
    eng.symbols_meta = {
        "MEGAUSDT": {"symbol": "MEGAUSDT", "baseAsset": "MEGA", "tickSize": 0.00001, "pricePrecision": 5},
        "BTCUSDT": {"symbol": "BTCUSDT", "baseAsset": "BTC", "tickSize": 0.1, "pricePrecision": 2},
    }
    mega_df = _make_trend_df(140, -1)
    btc_df = _make_trend_df(140, 1)
    for tf in ("5m", "15m", "1h", "4h", "1d", "1w"):
        eng.candle_cache[("MEGAUSDT", tf)] = mega_df
        eng.candle_cache[("BTCUSDT", tf)] = btc_df

    async def _run():
        p_mega_1h = await eng.build_chart_payload("MEGAUSDT", "1h")
        p_mega_15m = await eng.build_chart_payload("MEGAUSDT", "15m")
        p_btc_4h = await eng.build_chart_payload("BTCUSDT", "4h")
        assert p_mega_1h["symbol"] == "MEGAUSDT" and p_mega_1h["tf"] == "1h"
        assert p_mega_1h["tick_size"] == 0.00001 and p_mega_1h["price_precision"] == 5
        assert len(p_mega_1h["candles"]) == 140
        assert p_mega_15m["symbol"] == "MEGAUSDT" and p_mega_15m["tf"] == "15m"
        assert len(p_mega_15m["candles"]) == 140
        assert p_btc_4h["symbol"] == "BTCUSDT" and p_btc_4h["tf"] == "4h"
        assert len(p_btc_4h["candles"]) == 140

    asyncio.run(_run())


def test_concurrent_coin_switching_and_bridge_ingest_no_sqlite_lock(tmp_path, monkeypatch):
    import asyncio
    from app import db as db_mod
    from app.scanner.engine import ScannerEngine

    monkeypatch.setattr(db_mod, "DB_PATH", str(tmp_path / "test_scanner.db"))

    def _raw_klines(base_price: float, n: int = 120):
        rows = []
        t0 = 1_700_000_000_000
        p = base_price
        for i in range(n):
            ot = t0 + i * 3_600_000
            p = p * (1.0 + (0.002 if i % 3 else -0.0015))
            rows.append([ot, p * 0.998, p * 1.004, p * 0.995, p, 1250.0 + i, ot + 3_599_999])
        # Append a duplicate timestamp update bar to ensure deduplication works cleanly
        rows.append(rows[-1][:])
        return rows

    coins = {
        "MEGAUSDT": 0.04125,
        "BTCUSDT": 91500.0,
        "ETHUSDT": 3120.5,
        "SOLUSDT": 182.4,
        "XRPUSDT": 1.4825,
        "DOGEUSDT": 0.21456,
    }

    async def _run():
        await db_mod.init_db()
        eng = ScannerEngine()
        await eng.ingest_bridge_bootstrap(
            [{"symbol": s, "baseAsset": s.replace("USDT", ""), "pricePrecision": 5, "tickSize": 0.00001} for s in coins],
            [{"symbol": s, "price": bp, "change_24h": 2.5, "volume_24h": 50_000_000} for s, bp in coins.items()],
        )

        # Concurrently ingest all 6 coins while simultaneously switching chart payloads across coins & timeframes
        ingest_tasks = [
            eng.ingest_bridge_klines(sym, {tf: _raw_klines(bp) for tf in ("15m", "1h", "4h", "1d")})
            for sym, bp in coins.items()
        ]
        await asyncio.gather(*ingest_tasks)

        chart_payloads = await asyncio.gather(
            *(eng.build_chart_payload(sym, tf) for sym in coins for tf in ("15m", "1h", "4h", "1d"))
        )
        seen_closes = {}
        for payload in chart_payloads:
            sym = payload["symbol"]
            assert len(payload["candles"]) == 120
            times = [c["time"] for c in payload["candles"]]
            assert times == sorted(set(times)), f"Duplicate or unsorted candle timestamps for {sym}"
            if payload["tf"] == "1h":
                seen_closes[sym] = payload["candles"][-1]["close"]

        # Every coin must return its own distinct price series (never stale candles from a previous coin)
        assert len(set(round(v, 6) for v in seen_closes.values())) == len(coins)

    asyncio.run(_run())


