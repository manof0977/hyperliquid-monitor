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
    rep = build_plain_english_report("BTCUSDT", "1h", tf_detail, summary)
    assert rep is not None
    assert rep["symbol"] == "BTCUSDT"
    assert len(rep["sections"]) == 6
    assert "BTCUSDT" in rep["headline"]

