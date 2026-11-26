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




# ---------------------------------------------------------------------------
# Pre-Indicator Price-Action Reaction Engine
# ---------------------------------------------------------------------------
from app.analysis.reaction import classify_pre_indicator_reaction   # noqa: E402


def _empty_detail(n_bars: int = 120) -> dict:
    return {
        "n_bars": n_bars,
        "ind": {"close": 100.0, "atr14": 1.0, "ema20": 100.0, "ema_stack": "mixed"},
        "structure": {"trend": "neutral", "last_swing_high": None, "last_swing_low": None},
        "smc": {"order_blocks": [], "fvgs": [], "sweeps": [], "dealing_range": None},
        "sr": {"supports": [], "resistances": [], "pdh": None, "pdl": None, "pwh": None, "pwl": None,
               "nearest_support": None, "nearest_resistance": None},
        "div": {},
    }


def test_reaction_handles_missing_detail():
    rx = classify_pre_indicator_reaction(None)
    assert rx["state"] == "MID_RANGE_WAIT"
    assert rx["bias_lean"] == "NEUTRAL_WAIT"


def test_reaction_mid_range_when_no_zones():
    rx = classify_pre_indicator_reaction(_empty_detail())
    assert rx["state"] == "MID_RANGE_WAIT"
    assert rx["bounce_zone"] is None and rx["rejection_zone"] is None
    assert "Mid-Range" in rx["summary"]
    assert rx["why_before_indicators"]


def test_reaction_bounce_zone_inside_demand_ob():
    d = _empty_detail()
    d["ind"].update({"close": 99.8, "atr14": 1.0, "ema20": 101.5, "ema_stack": "bullish",
                     "closed_lower_wick_pct": 52.0, "vol_ratio": 1.6})
    d["structure"]["trend"] = "bullish"
    d["smc"]["order_blocks"] = [{"kind": "bullish", "top": 100.2, "bottom": 99.2, "mitigated": False}]
    d["smc"]["dealing_range"] = {"high": 110.0, "low": 95.0, "eq": 102.5, "position": 0.32}
    rx = classify_pre_indicator_reaction(d, biases={"4h": "BULLISH"}, tf_label="1H")
    assert rx["state"] == "BOUNCE_ZONE"
    assert rx["bias_lean"] == "BULLISH_REACTION"
    assert rx["bounce_zone"]["in_zone"] is True
    # Invalidation must sit structurally BELOW the zone, target strictly ABOVE it
    assert rx["bounce_zone"]["invalidation"] < rx["bounce_zone"]["low"]
    assert rx["bounce_zone"]["target_1"] > rx["bounce_zone"]["high"]
    assert round(rx["bounce_zone"]["low"] - rx["bounce_zone"]["invalidation"], 6) == 0.25


def test_reaction_pullback_due_on_atr_stretch_into_supply():
    d = _empty_detail()
    d["ind"].update({"close": 104.0, "atr14": 1.0, "ema20": 100.0, "ema_stack": "bullish",
                     "ema20_stretch_atr": 4.0, "consec_bull_bars": 5, "closed_upper_wick_pct": 41.0})
    d["structure"]["trend"] = "bullish"
    d["smc"]["order_blocks"] = [{"kind": "bearish", "top": 104.4, "bottom": 103.6, "mitigated": False}]
    d["smc"]["dealing_range"] = {"high": 105.0, "low": 95.0, "eq": 100.0, "position": 0.9}
    rx = classify_pre_indicator_reaction(d, tf_label="1H")
    assert rx["state"] == "PULLBACK_DUE"
    assert rx["bias_lean"] == "BEARISH_REACTION"
    assert rx["rejection_zone"]["in_zone"] is True
    assert rx["rejection_zone"]["invalidation"] > rx["rejection_zone"]["high"]
    assert rx["rejection_zone"]["target_1"] < rx["rejection_zone"]["low"]
    assert "Pullback" in rx["summary"]


def test_reaction_bottom_reversal_on_liquidity_sweep():
    d = _empty_detail()
    d["ind"].update({"close": 97.6, "atr14": 1.0, "ema20": 100.4, "ema_stack": "bearish",
                     "closed_lower_wick_pct": 58.0, "vol_ratio": 2.1})
    d["structure"]["trend"] = "bearish"
    d["smc"]["sweeps"] = [{"kind": "bullish", "index": 118, "level": 97.5, "wick": 96.9}]
    d["smc"]["dealing_range"] = {"high": 104.0, "low": 96.0, "eq": 100.0, "position": 0.2}
    rx = classify_pre_indicator_reaction(d, tf_label="4H")
    assert rx["state"] == "BULLISH_REVERSAL_ZONE"
    assert rx["bias_lean"] == "BULLISH_REACTION"
    assert "Sell-Side Liquidity Sweep" in " ".join(rx["why_before_indicators"])


def test_reaction_top_reversal_on_buy_side_sweep():
    d = _empty_detail()
    d["ind"].update({"close": 103.2, "atr14": 1.0, "ema20": 101.0, "ema_stack": "bullish",
                     "closed_upper_wick_pct": 61.0})
    d["structure"]["trend"] = "bullish"
    d["smc"]["sweeps"] = [{"kind": "bearish", "index": 119, "level": 103.0, "wick": 103.9}]
    d["smc"]["dealing_range"] = {"high": 104.0, "low": 96.0, "eq": 100.0, "position": 0.9}
    rx = classify_pre_indicator_reaction(d, tf_label="1H")
    assert rx["state"] == "BEARISH_REVERSAL_ZONE"
    assert rx["bias_lean"] == "BEARISH_REACTION"


def test_reaction_never_repaints_or_invents_levels():
    """Same input must always give identical output, and all levels must come from the input structure."""
    d = _empty_detail()
    d["ind"].update({"close": 99.9, "atr14": 1.0, "ema20": 101.0, "ema_stack": "bullish"})
    d["structure"]["trend"] = "bullish"
    d["smc"]["order_blocks"] = [{"kind": "bullish", "top": 100.1, "bottom": 99.1, "mitigated": False}]
    a = classify_pre_indicator_reaction(d, biases={"1d": "BULLISH"})
    b = classify_pre_indicator_reaction(d, biases={"1d": "BULLISH"})
    assert a == b
    assert a["bounce_zone"]["high"] <= 100.1 and a["bounce_zone"]["low"] >= 99.1


def test_reaction_stays_out_of_confirmed_signal_state_machine():
    """Reaction metadata must never appear in, or mutate, the candle-close CONFIRMED status logic."""
    df = _make_trend_df(200, direction=1)
    mtf = {"1h": analyze_timeframe(df), "4h": analyze_timeframe(df), "1d": analyze_timeframe(df)}
    scored = evaluate_symbol(mtf)
    assert "reaction_state" in scored and "reaction_lean" in scored
    # Confirm the pre-indicator radar can't ever emit a CONFIRMED status string
    assert "CONFIRMED" not in scored["reaction_label"]
    assert "CONFIRMED" not in scored["reaction_state"]
