"""Causal bar-by-bar backtester — zero look-ahead bias, configurable fees & slippage.

Walks closed bars sequentially `bar = warmup .. N-2`:
  - At bar `t`, runs structure / SMC / indicator / scoring ONLY on `df.iloc[: t+1]`.
  - If a signal reaches CONFIRMED (or DEVELOPING if user opts in) and no trade is open,
    opens at bar `t+1` open +/- slippage.
  - Manages trade on subsequent bars `k >= t+1` using bar high/low (conservative: SL checked before TP if both hit in same bar).
"""
import numpy as np
import pandas as pd
from app.analysis.pipeline import analyze_timeframe
from app.analysis.scoring import evaluate_symbol
from app.config import settings


def run_backtest(
    symbol: str,
    tf: str,
    df: pd.DataFrame,
    min_tech_score: float = 60.0,
    allow_developing: bool = True,
    fee_pct: float | None = None,
    slippage_pct: float | None = None,
    step_bars: int = 2,
) -> dict:
    fee = (fee_pct if fee_pct is not None else settings.backtest_fee_pct) / 100.0
    slip = (slippage_pct if slippage_pct is not None else settings.backtest_slippage_pct) / 100.0

    if df is None or len(df) < 90:
        return _empty_report(symbol, tf, "Insufficient historical candles loaded from Binance (need >= 90 bars).")

    df = df.reset_index(drop=True)
    trades: list[dict] = []
    warmup = 65
    i = warmup
    n = len(df)

    while i < n - 2:
        window = df.iloc[: i + 1]  # strictly causal slice up to bar i
        tf_res = analyze_timeframe(window)
        if tf_res is None:
            i += step_bars
            continue
        ev = evaluate_symbol({tf: tf_res})
        st = ev["status"]
        qualifies = st in ("LONG CONFIRMED", "SHORT CONFIRMED") or (
            allow_developing and st in ("LONG DEVELOPING", "SHORT DEVELOPING")
        )
        side = "LONG" if st.startswith("LONG") else "SHORT"
        tech = ev["long_tech_score"] if side == "LONG" else ev["short_tech_score"]
        plan = ev["long_plan"] if side == "LONG" else ev["short_plan"]

        if not (qualifies and tech >= min_tech_score and plan.get("has_plan") and plan.get("targets")):
            i += step_bars
            continue

        # Enter at NEXT bar open with adverse slippage
        next_bar = df.iloc[i + 1]
        raw_entry = float(next_bar["open"])
        entry = raw_entry * (1 + slip) if side == "LONG" else raw_entry * (1 - slip)
        sl = float(plan["invalidation"])
        tp = float(plan["targets"][0])

        # Sanity check that SL & TP are on correct sides of actual fill
        if side == "LONG" and not (sl < entry < tp):
            i += step_bars
            continue
        if side == "SHORT" and not (tp < entry < sl):
            i += step_bars
            continue

        risk = abs(entry - sl)
        if risk <= 0:
            i += step_bars
            continue

        outcome = None
        exit_price = entry
        exit_idx = i + 1
        max_hold = min(n - 1, i + 60)
        for k in range(i + 1, max_hold + 1):
            b = df.iloc[k]
            bh, bl = float(b["high"]), float(b["low"])
            if side == "LONG":
                # Conservative tie-break: stop checked before target on same candle
                if bl <= sl:
                    exit_price = sl * (1 - slip)
                    outcome = "LOSS"
                    exit_idx = k
                    break
                if bh >= tp:
                    exit_price = tp * (1 - slip)
                    outcome = "WIN"
                    exit_idx = k
                    break
            else:
                if bh >= sl:
                    exit_price = sl * (1 + slip)
                    outcome = "LOSS"
                    exit_idx = k
                    break
                if bl <= tp:
                    exit_price = tp * (1 + slip)
                    outcome = "WIN"
                    exit_idx = k
                    break
        if outcome is None:
            exit_idx = max_hold
            exit_price = float(df.iloc[exit_idx]["close"])
            outcome = "TIME"

        gross_move = (exit_price - entry) if side == "LONG" else (entry - exit_price)
        fee_cost = (entry + exit_price) * fee
        net_pnl = gross_move - fee_cost
        r_mult = net_pnl / risk

        score_bucket = "85-100" if tech >= 85 else ("75-84" if tech >= 75 else ("60-74" if tech >= 60 else "<60"))
        trades.append({
            "symbol": symbol, "tf": tf, "side": side, "status": st,
            "score": tech, "score_bucket": score_bucket,
            "entry_time": int(next_bar["open_time"]),
            "exit_time": int(df.iloc[exit_idx]["open_time"]),
            "entry": round(entry, 6), "exit": round(exit_price, 6),
            "sl": round(sl, 6), "tp": round(tp, 6),
            "r_multiple": round(r_mult, 3),
            "outcome": "WIN" if r_mult > 0 else "LOSS",
        })
        i = exit_idx + 1  # no overlapping trades on same symbol/TF

    return summarize_trades(trades, symbol=symbol, tf=tf)


def summarize_trades(trades: list[dict], symbol: str = "PORTFOLIO", tf: str = "ALL") -> dict:
    if not trades:
        return _empty_report(symbol, tf, "No qualifying setups occurred in the tested historical window.")

    rs = np.array([t["r_multiple"] for t in trades], dtype=float)
    wins = rs[rs > 0]
    losses = rs[rs <= 0]
    win_rate = float(len(wins) / len(rs) * 100.0)
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) else 0.0
    pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)

    # Equity curve in R units + max drawdown in R
    equity = np.cumsum(rs)
    peaks = np.maximum.accumulate(np.insert(equity, 0, 0.0))[1:]
    dd = peaks - equity
    max_dd = float(dd.max()) if len(dd) else 0.0

    def _sub(items: list[dict]) -> dict:
        if not items:
            return {"trades": 0, "win_rate": 0.0, "avg_r": 0.0, "total_r": 0.0}
        arr = np.array([x["r_multiple"] for x in items], dtype=float)
        return {
            "trades": len(items),
            "win_rate": round(float((arr > 0).mean() * 100), 1),
            "avg_r": round(float(arr.mean()), 3),
            "total_r": round(float(arr.sum()), 2),
        }

    by_side = {"LONG": _sub([t for t in trades if t["side"] == "LONG"]),
               "SHORT": _sub([t for t in trades if t["side"] == "SHORT"])}
    by_tf: dict[str, dict] = {}
    for k_tf in sorted({t["tf"] for t in trades}):
        by_tf[k_tf] = _sub([t for t in trades if t["tf"] == k_tf])
    by_score: dict[str, dict] = {}
    for b in ("85-100", "75-84", "60-74", "<60"):
        by_score[b] = _sub([t for t in trades if t["score_bucket"] == b])

    return {
        "symbol": symbol, "tf": tf, "note": None,
        "total_trades": len(trades),
        "win_rate": round(win_rate, 1),
        "profit_factor": pf,
        "avg_r": round(float(rs.mean()), 3),
        "total_r": round(float(rs.sum()), 2),
        "max_drawdown_r": round(max_dd, 2),
        "equity_curve": [{"trade": idx + 1, "time": t["exit_time"], "r": round(float(equity[idx]), 3)} for idx, t in enumerate(trades)],
        "by_side": by_side,
        "by_timeframe": by_tf,
        "by_score": by_score,
        "trades": trades[-100:],
    }


def _empty_report(symbol: str, tf: str, note: str) -> dict:
    return {
        "symbol": symbol, "tf": tf, "note": note,
        "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
        "avg_r": 0.0, "total_r": 0.0, "max_drawdown_r": 0.0,
        "equity_curve": [], "by_side": {}, "by_timeframe": {}, "by_score": {}, "trades": [],
    }
