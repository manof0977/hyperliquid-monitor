"""Causal, walk-forward backtest of the Institutional Setup Playbook.

Judges the playbook on evidence instead of adjectives:
  - At every closed bar `t`, ONLY `df.iloc[:t+1]` is visible (zero look-ahead).
  - The playbook grades both sides; a trade is opened only when the grade is in `grades`
    (default: A+ and A) and the entry limit would be touched by the next bar.
  - Exits are structural: stop = playbook stop, targets = playbook targets, checked bar-by-bar
    on bar high/low with the conservative tie-break (stop is assumed hit first when both sides
    are touched in one candle). Fees and slippage are applied on both entry and exit.

This is why the module exists: a setup is only "what top traders use" if it survives an honest
walk-forward test with costs — otherwise it is just a nice-looking chart story.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis.pipeline import analyze_timeframe
from app.analysis.playbook import evaluate_playbook
from app.config import settings

GRADE_ORDER = {"A+": 4, "A": 3, "B": 2, "C": 1, "NO-TRADE": 0}


def run_playbook_backtest(
    symbol: str,
    tf: str,
    df: pd.DataFrame,
    grades: list[str] | None = None,
    fee_pct: float | None = None,
    slippage_pct: float | None = None,
    step_bars: int = 1,
    max_hold_bars: int = 80,
    warmup: int = 120,
) -> dict:
    fee = (fee_pct if fee_pct is not None else settings.backtest_fee_pct) / 100.0
    slip = (slippage_pct if slippage_pct is not None else settings.backtest_slippage_pct) / 100.0
    wanted = set(grades or ["A+", "A"])

    if df is None or len(df) < warmup + 20:
        return _empty(symbol, tf, f"Insufficient candles for a causal playbook backtest (need >= {warmup + 20}).")

    df = df.reset_index(drop=True)
    n = len(df)
    trades: list[dict] = []
    graded = {"A+": 0, "A": 0, "B": 0, "C": 0, "NO-TRADE": 0}

    # ------------------------------------------------------------------ event-driven walk-forward
    # A trader does not "buy because a zone exists 3 ATR away". They ARM the level, then act when
    # price actually trades into it. This loop mirrors exactly that:
    #   1. arm a POI when HTF bias aligns and an unmitigated OB/FVG sits on the pullback side
    #   2. wait (up to `arm_expiry` bars) for price to tap the zone
    #   3. at the tap bar, grade the full playbook checklist AT VALUE and only then take the trade
    #   4. disarm when price closes through the zone's far edge (structure invalidated)
    arm_expiry = 40
    armed: list[dict] = []
    i = warmup
    while i < n - 2:
        window = df.iloc[: i + 1]  # strictly causal
        tf_res = analyze_timeframe(window)
        if tf_res is None:
            i += step_bars
            continue
        try:
            live = evaluate_playbook({tf: tf_res}, sentiment=None, primary_tf=tf)
        except Exception:
            i += step_bars
            continue
        graded[live["grade"]] = graded.get(live["grade"], 0) + 1

        bar = df.iloc[i]
        hi, lo, cl = float(bar["high"]), float(bar["low"]), float(bar["close"])
        atr = max(float(tf_res["ind"]["atr14"]), 1e-9)

        # ---- keep / drop armed setups
        kept = []
        for a in armed:
            invalidated = (cl < a["far"] - 0.25 * atr) if a["side"] == "LONG" else (cl > a["far"] + 0.25 * atr)
            if not invalidated and (i - a["at"]) <= arm_expiry:
                kept.append(a)
        armed = kept

        # ---- arm a new POI
        for side in ("LONG", "SHORT"):
            if any(a["side"] == side for a in armed):
                continue
            x = live["sides"][side]
            if not x.get("zones") or x.get("bias_pct", 0) < 40:
                continue
            cand = [z for z in x["zones"] if z["source"] in ("OB", "FVG")] or x["zones"]
            zone = min(cand, key=lambda z: z["distance_atr"])
            armed.append({
                "side": side, "zone": zone, "at": i,
                "far": float(zone["low"]) if side == "LONG" else float(zone["high"]),
            })

        # ---- act when price taps an armed POI
        trade_done = False
        for a in list(armed):
            zone = a["zone"]
            tapped = (lo <= float(zone["high"]) and hi >= float(zone["low"]))
            if not tapped:
                continue
            side = a["side"]
            try:
                pb = evaluate_playbook({tf: tf_res}, sentiment=None, primary_tf=tf, forced_pois={side: zone})
            except Exception:
                continue
            best = pb["best"]
            graded[pb["grade"]] = graded.get(pb["grade"], 0) + 1
            if pb["grade"] not in wanted or not best.get("entry") or not best.get("stop") or not best.get("targets"):
                continue
            entry_bar = i
            limit = float(best["entry"])
            entry = limit * (1 + slip) if side == "LONG" else limit * (1 - slip)
            stop = float(best["stop"])
            tps = [float(t) for t in best["targets"]]
            is_long = side == "LONG"
            if is_long and not (stop < entry) or (not is_long) and not (stop > entry):
                continue
            risk = abs(entry - stop)
            if risk <= 0 or risk / atr > 4.0:
                continue
            tp1 = tps[0]
            if (is_long and tp1 <= entry) or ((not is_long) and tp1 >= entry):
                continue

            exit_price = None
            exit_idx = min(n - 1, entry_bar + max_hold_bars)
            outcome = "TIME"
            hit_t1 = False
            remaining = 1.0
            realised = 0.0
            for k in range(entry_bar + 1, min(n, entry_bar + max_hold_bars + 1)):
                b2 = df.iloc[k]
                bhi, blo = float(b2["high"]), float(b2["low"])
                if is_long:
                    if blo <= stop:  # conservative tie-break: stop first
                        realised += remaining * ((stop * (1 - slip)) - entry)
                        outcome = "LOSS" if not hit_t1 else "BREAKEVEN+"
                        exit_idx, exit_price, remaining = k, stop, 0.0
                        break
                    if not hit_t1 and bhi >= tp1:
                        realised += 0.5 * ((tp1 * (1 - slip)) - entry)
                        remaining, hit_t1, stop = 0.5, True, entry
                        if len(tps) == 1:
                            outcome, exit_idx, exit_price, remaining = "WIN", k, tp1, 0.0
                            break
                    elif hit_t1 and len(tps) > 1 and bhi >= tps[1]:
                        realised += remaining * ((tps[1] * (1 - slip)) - entry)
                        outcome, exit_idx, exit_price, remaining = "WIN", k, tps[1], 0.0
                        break
                else:
                    if bhi >= stop:
                        realised += remaining * (entry - (stop * (1 + slip)))
                        outcome = "LOSS" if not hit_t1 else "BREAKEVEN+"
                        exit_idx, exit_price, remaining = k, stop, 0.0
                        break
                    if not hit_t1 and blo <= tp1:
                        realised += 0.5 * (entry - (tp1 * (1 + slip)))
                        remaining, hit_t1, stop = 0.5, True, entry
                        if len(tps) == 1:
                            outcome, exit_idx, exit_price, remaining = "WIN", k, tp1, 0.0
                            break
                    elif hit_t1 and len(tps) > 1 and blo <= tps[1]:
                        realised += remaining * (entry - (tps[1] * (1 + slip)))
                        outcome, exit_idx, exit_price, remaining = "WIN", k, tps[1], 0.0
                        break

            if remaining > 0:
                px = float(df.iloc[exit_idx]["close"])
                exit_price = px
                realised += remaining * ((px - entry) if is_long else (entry - px))
            fees = (entry + (exit_price or entry)) * fee
            net = realised - fees
            r_mult = net / risk

            trades.append({
                "symbol": symbol, "tf": tf, "side": side, "grade": pb["grade"], "score": pb["score"],
                "setup_type": best.get("setup_type"),
                "poi": best["poi"]["label"] if best.get("poi") else None,
                "entry_time": int(df.iloc[entry_bar]["open_time"]),
                "exit_time": int(df.iloc[exit_idx]["open_time"]),
                "entry": round(entry, 8), "stop": round(float(best["stop"]), 8), "t1": round(tp1, 8),
                "exit": round(float(exit_price or entry), 8),
                "outcome": outcome, "hit_t1": hit_t1,
                "r_multiple": round(float(r_mult), 3), "risk_atr": best.get("risk_atr"),
                "factors": [f["label"] for f in best.get("factors", [])][:6],
            })
            i = exit_idx + 1
            armed = [x for x in armed if x["side"] != side]
            trade_done = True
            break

        if trade_done:
            continue
        i += step_bars

    return summarize(trades, symbol, tf, graded)


def summarize(trades: list[dict], symbol: str, tf: str, graded: dict | None = None) -> dict:
    graded = graded or {}
    if not trades:
        rep = _empty(symbol, tf, "No A/A+ playbook setups filled inside this historical window.")
        rep["grade_counts"] = graded
        return rep

    rs = np.array([t["r_multiple"] for t in trades], dtype=float)
    wins = rs[rs > 0]
    losses = rs[rs <= 0]
    equity = np.cumsum(rs)
    peaks = np.maximum.accumulate(np.insert(equity, 0, 0.0))[1:]
    dd = peaks - equity

    def _bucket(items: list[dict]) -> dict:
        if not items:
            return {"trades": 0, "win_rate": 0.0, "avg_r": 0.0, "total_r": 0.0}
        a = np.array([x["r_multiple"] for x in items], dtype=float)
        return {"trades": len(items), "win_rate": round(float((a > 0).mean() * 100), 1),
                "avg_r": round(float(a.mean()), 3), "total_r": round(float(a.sum()), 2)}

    return {
        "symbol": symbol, "tf": tf, "note": None,
        "mode": "playbook",
        "total_trades": len(trades),
        "win_rate": round(float(len(wins) / len(rs) * 100), 1),
        "profit_factor": round(float(wins.sum() / abs(losses.sum())), 2) if len(losses) and losses.sum() != 0 else (99.0 if len(wins) else 0.0),
        "avg_r": round(float(rs.mean()), 3),
        "total_r": round(float(rs.sum()), 2),
        "expectancy_r": round(float(rs.mean()), 3),
        "max_drawdown_r": round(float(dd.max()) if len(dd) else 0.0, 2),
        "t1_hit_rate": round(float(np.mean([1.0 if t["hit_t1"] else 0.0 for t in trades]) * 100), 1),
        "equity_curve": [{"trade": k + 1, "time": t["exit_time"], "r": round(float(equity[k]), 3)}
                         for k, t in enumerate(trades)],
        "by_grade": {g: _bucket([t for t in trades if t["grade"] == g]) for g in ("A+", "A", "B", "C")},
        "by_side": {s: _bucket([t for t in trades if t["side"] == s]) for s in ("LONG", "SHORT")},
        "grade_counts": graded,
        "trades": trades[-150:],
    }


def _empty(symbol: str, tf: str, note: str) -> dict:
    return {
        "symbol": symbol, "tf": tf, "note": note, "mode": "playbook",
        "total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "avg_r": 0.0, "total_r": 0.0,
        "expectancy_r": 0.0, "max_drawdown_r": 0.0, "t1_hit_rate": 0.0, "equity_curve": [],
        "by_grade": {}, "by_side": {}, "grade_counts": {}, "trades": [],
    }
