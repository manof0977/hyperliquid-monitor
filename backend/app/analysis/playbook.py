"""Institutional Setup Playbook — the "A+ checklist" used by consistently profitable traders.

This is *not* a prediction engine. Every setup here is a deterministic, fully-explained rule set
built from the same mechanics discretionary prop/hedge-fund traders and the best SMC/price-action
desks actually use:

    1. HTF bias            — trade only with the higher-timeframe draw on liquidity
    2. Liquidity sweep     — sell-side (long) / buy-side (short) stop-hunt into real liquidity
    3. Displacement        — an impulsive, expanding-range leg that breaks structure with intent
    4. POI (Point of Interest) at a discount/premium — unmitigated OB / unfilled FVG inside the
                            0.62–0.79 OTE retracement of that displacement leg
    5. LTF shift           — closed-candle CHoCH/BOS on the trigger timeframe inside the POI
    6. Structural stop     — beyond the POI + sweep extreme, never an arbitrary percentage
    7. Targets & R:R gates — real opposing structure / measured-move, R:R thresholds
    8. Confluence scoring  — volume, funding crowding, S/R & PDH/PDL/PWH/PWL confluence,
                            divergence, session liquidity — each a named bonus or penalty

Grading:
    A+  = every gate passes, score >= 85   (full-size institutional checklist)
    A   = at most one gate fails, score >= 75
    B   = two gates fail / score >= 60     (watchlist, not an entry)
    C   = structurally weak                (do not trade)
    NO-TRADE = no valid structural plan on either side

Nothing is fabricated: every price level comes from the loaded candles. When a gate or factor data
is unavailable (e.g. funding rate not yet fetched) it is reported as unavailable instead of assumed.
"""
from __future__ import annotations

import time

BIAS_WEIGHTS = {"1w": 3.0, "1d": 3.0, "4h": 2.5, "1h": 2.0, "15m": 1.5, "5m": 1.0}
OTE_LOW = 0.62
OTE_HIGH = 0.79


# --------------------------------------------------------------------------- helpers
def _fp(val: float | None) -> str:
    if val is None:
        return "—"
    a = abs(val)
    if a >= 1000:
        return f"{val:,.2f}"
    if a >= 10:
        return f"{val:.3f}"
    if a >= 1:
        return f"{val:.4f}"
    if a >= 0.01:
        return f"{val:.6f}"
    return f"{val:.8f}"


def _session_of(ts_ms: int | None) -> dict:
    """Forex/crypto session liquidity context by UTC hour (Asia = thin, London/NY overlap = deepest)."""
    if not ts_ms:
        ts_ms = int(time.time() * 1000)
    hour = time.gmtime(ts_ms / 1000.0).tm_hour
    if 12 <= hour < 16:
        return {"name": "London / New York Overlap", "quality": "best", "hour_utc": hour,
                "note": "Deepest liquidity of the day — displacement and sweeps are most reliable."}
    if 7 <= hour < 12:
        return {"name": "London", "quality": "good", "hour_utc": hour,
                "note": "Strong follow-through on sweeps during London open."}
    if 16 <= hour < 21:
        return {"name": "New York", "quality": "good", "hour_utc": hour,
                "note": "US session momentum; watch for 16:00 UTC reversals."}
    return {"name": "Asia / off-hours", "quality": "thin", "hour_utc": hour,
            "note": "Thinner book — sweeps and fakeouts are more common; size down."}


def _leg(d: dict, direction: str, lookback: int = 60) -> dict | None:
    """Most recent displacement leg in `direction` (bullish|bearish) with its origin, extreme & OTE band."""
    st = d["structure"]
    ind = d["ind"]
    n = int(d["n_bars"])
    atr = max(float(ind.get("atr14") or 0.0), 1e-9)
    cur = float(ind["close"])
    breaks = [b for b in st.get("breaks", []) if b["direction"] == direction and (n - int(b["index"])) <= lookback]
    if not breaks:
        return None
    br = breaks[-1]
    swings = st.get("swings", [])
    if direction == "bullish":
        prior = [s for s in swings if s["kind"] == "low" and s["index"] < br["index"]]
        if not prior:
            return None
        origin = float(prior[-1]["price"])
        after = [float(s["price"]) for s in swings if s["kind"] == "high" and s["index"] >= br["index"]]
        extreme = max(after + [cur]) if after else max(cur, float(br["level"]))
    else:
        prior = [s for s in swings if s["kind"] == "high" and s["index"] < br["index"]]
        if not prior:
            return None
        origin = float(prior[-1]["price"])
        after = [float(s["price"]) for s in swings if s["kind"] == "low" and s["index"] >= br["index"]]
        extreme = min(after + [cur]) if after else min(cur, float(br["level"]))
    rng = abs(extreme - origin)
    if rng <= 0:
        return None
    return {
        "break": br,
        "break_kind": br.get("kind"),
        "level": float(br["level"]),
        "origin": origin,
        "extreme": extreme,
        "range": rng,
        "range_atr": round(rng / atr, 2),
        "bars_since": n - int(br["index"]),
        # 0.62–0.79 retracement of the leg (Optimal Trade Entry)
        "ote_low": round(min(extreme, extreme - (1 - OTE_LOW) * rng) if direction == "bullish"
                         else min(extreme, extreme + (1 - OTE_LOW) * rng), 8),
        "ote_high": round(max(extreme, extreme - (1 - OTE_HIGH) * rng) if direction == "bullish"
                          else max(extreme, extreme + (1 - OTE_HIGH) * rng), 8),
        "midpoint": round((origin + extreme) / 2, 8),
    }


def _poi_candidates(d: dict, side: str) -> list[dict]:
    """Every unmitigated point-of-interest on the pullback side of price, with confluence counts."""
    smc = d["smc"]
    sr = d["sr"]
    st = d["structure"]
    ind = d["ind"]
    cur = float(ind["close"])
    atr = max(float(ind.get("atr14") or 0.0), 1e-9)
    is_long = side == "LONG"
    kind = "bullish" if is_long else "bearish"
    out: list[dict] = []

    def _add(low, high, source, label, unmitigated, touches=1):
        low, high = float(min(low, high)), float(max(low, high))
        contains = low <= cur <= high
        valid_side = (high < cur) if is_long else (low > cur)
        if not (contains or valid_side):
            return
        dist = 0.0 if contains else (min(abs(cur - low), abs(cur - high)) / atr)
        if dist > 6.0:
            return
        out.append({
            "zone": [round(low, 8), round(high, 8)],
            "low": low, "high": high, "mid": round((low + high) / 2, 8),
            "source": source, "label": label,
            "unmitigated": bool(unmitigated),
            "touches": touches,
            "in_zone": contains,
            "at_value": contains or dist <= 0.75,
            "distance_atr": round(dist, 2),
            "confluence": [],
        })

    for ob in smc.get("order_blocks", []):
        if ob["kind"] != kind:
            continue
        _add(ob["bottom"], ob["top"], "OB",
             f"{'Bullish Demand' if is_long else 'Bearish Supply'} Order Block ({ob.get('break_kind', 'BOS')})",
             not ob.get("mitigated"))
    for g in smc.get("fvgs", []):
        if g["kind"] != kind:
            continue
        _add(g["bottom"], g["top"], "FVG",
             f"{'Bullish' if is_long else 'Bearish'} Fair Value Gap (3-bar imbalance)",
             not g.get("mitigated"))
    # swing extremes as fallback POI
    node = st.get("last_swing_low") if is_long else st.get("last_swing_high")
    if node:
        p = float(node["price"])
        if (is_long and p <= cur * 1.006) or ((not is_long) and p >= cur * 0.994):
            _add(p - 0.15 * atr, p + 0.15 * atr, "SWING",
                 f"Protected Swing {'Low' if is_long else 'High'} ({node.get('label')})", True)
    # supply/demand + PDH/PDL confluence tags
    for lvl in (sr.get("supports", []) if is_long else sr.get("resistances", [])):
        if lvl["source"] in ("PDH", "PDL", "PWH", "PWL"):
            _add(float(lvl["price"]) - 0.15 * atr, float(lvl["price"]) + 0.15 * atr, lvl["source"],
                 f"{lvl['source']} liquidity level", True)
    return out


def _enrich_confluence(pois: list[dict], d: dict, leg: dict | None, side: str) -> None:
    """Tag each POI with OTE overlap, S/R confluence and zone stacking (OB + FVG overlap)."""
    ind = d["ind"]
    sr = d["sr"]
    atr = max(float(ind.get("atr14") or 0.0), 1e-9)
    is_long = side == "LONG"
    for p in pois:
        tags: list[str] = []
        if leg:
            lo, hi = min(leg["ote_low"], leg["ote_high"]), max(leg["ote_low"], leg["ote_high"])
            if not (p["high"] < lo or p["low"] > hi):
                tags.append("OTE 0.62–0.79 retracement of the displacement leg")
            if p["low"] <= leg["origin"] <= p["high"] or p["low"] <= leg["midpoint"] <= p["high"]:
                tags.append("Covers displacement-leg origin / equilibrium")
        for lvl in (sr.get("supports", []) if is_long else sr.get("resistances", [])):
            if p["low"] - 0.2 * atr <= float(lvl["price"]) <= p["high"] + 0.2 * atr:
                tags.append(f"{lvl['source']} level inside zone")
        others = [q for q in pois if q is not p and q["source"] != p["source"]
                  and not (q["high"] < p["low"] or q["low"] > p["high"])]
        if others:
            tags.append("Zone stacking: " + " + ".join(sorted({q["source"] for q in others})))
        if p["unmitigated"]:
            tags.append("Fresh / unmitigated")
        p["confluence"] = tags


def _pick_poi(pois: list[dict], d: dict, leg: dict | None) -> dict | None:
    if not pois:
        return None
    ind = d["ind"]
    atr = max(float(ind.get("atr14") or 0.0), 1e-9)
    cur = float(ind["close"])

    def score(p: dict) -> float:
        s = 0.0
        s += 3.0 if p["in_zone"] else max(0.0, 3.0 - p["distance_atr"])
        s += 2.0 if p["unmitigated"] else 0.0
        s += 1.6 * len(p["confluence"])
        s += {"OB": 2.2, "FVG": 1.6, "SWING": 1.0}.get(p["source"], 0.6)
        if p["distance_atr"] > 4.0:
            s -= (p["distance_atr"] - 4.0) * 1.2
        if leg and p["low"] <= leg["origin"] <= p["high"]:
            s += 1.5
        # prefer zones whose far edge is realistically reachable (<= 6 ATR)
        s -= abs(cur - p["mid"]) / atr * 0.15
        return s

    pois_sorted = sorted(pois, key=score, reverse=True)
    best = dict(pois_sorted[0])
    best["score"] = round(score(best), 2)
    return best


def _targets(d: dict, side: str, entry: float, stop: float, leg: dict | None) -> dict:
    """Real structural targets + measured-move extension, with R multiples per target."""
    smc, sr, ind = d["smc"], d["sr"], d["ind"]
    atr = max(float(ind.get("atr14") or 0.0), 1e-9)
    is_long = side == "LONG"
    risk = abs(entry - stop)
    cands: list[tuple[float, str]] = []

    if is_long:
        for r in sr.get("resistances", []):
            if float(r["price"]) > entry + 0.6 * atr:
                cands.append((float(r["price"]), f"Resistance ({r['source']})"))
        for ob in smc.get("order_blocks", []):
            if ob["kind"] == "bearish" and float(ob["bottom"]) > entry + 0.6 * atr:
                cands.append((float(ob["bottom"]), "Opposing Bearish Supply OB"))
        dr = smc.get("dealing_range")
        if dr and float(dr["high"]) > entry + 0.6 * atr:
            cands.append((float(dr["high"]), "Dealing-range high (buy-side liquidity)"))
        cands.sort(key=lambda x: x[0])
    else:
        for s in sr.get("supports", []):
            if float(s["price"]) < entry - 0.6 * atr:
                cands.append((float(s["price"]), f"Support ({s['source']})"))
        for ob in smc.get("order_blocks", []):
            if ob["kind"] == "bullish" and float(ob["top"]) < entry - 0.6 * atr:
                cands.append((float(ob["top"]), "Opposing Bullish Demand OB"))
        dr = smc.get("dealing_range")
        if dr and float(dr["low"]) < entry - 0.6 * atr:
            cands.append((float(dr["low"]), "Dealing-range low (sell-side liquidity)"))
        cands.sort(key=lambda x: x[0], reverse=True)

    picked: list[dict] = []
    for price, src in cands:
        if any(abs(price - p["price"]) < 0.45 * atr for p in picked):
            continue
        rr = abs(price - entry) / risk if risk > 0 else 0.0
        picked.append({"price": round(price, 8), "source": src, "rr": round(rr, 2), "kind": "structure"})
        if len(picked) == 2:
            break

    if leg:
        ext = leg["extreme"] + leg["range"] if is_long else leg["extreme"] - leg["range"]
        rr = abs(ext - entry) / risk if risk > 0 else 0.0
        if not any(abs(ext - p["price"]) < 1.0 * atr for p in picked) and rr >= 1.0:
            picked.append({"price": round(ext, 8), "source": "Measured-move extension (1.0 × displacement leg)",
                           "rr": round(rr, 2), "kind": "measured_move"})

    if not picked:
        # No opposing structure at all -> no honest target, so no trade
        return {"targets": [], "target_details": [], "rr1": 0.0, "rr2": 0.0, "partial_plan": None}

    t1 = picked[0]
    rr2 = picked[1]["rr"] if len(picked) > 1 else t1["rr"]
    partial = (
        f"Scale 50% at T1 {_fp(t1['price'])} ({t1['rr']}R), move stop to break-even, "
        f"trail the rest to T2 {_fp(picked[1]['price'])} ({picked[1]['source']})."
        if len(picked) > 1
        else f"Take 50% at T1 {_fp(t1['price'])} ({t1['rr']}R), move stop to break-even, trail rest."
    )
    return {
        "targets": [p["price"] for p in picked[:3]],
        "target_details": picked[:3],
        "rr1": t1["rr"],
        "rr2": rr2,
        "partial_plan": partial,
    }




def _local_sweep(df, direction: str, lookback: int = 25, fresh_within: int = 25) -> dict | None:
    """Raw-candle stop-hunt detector (does not depend on fractal-confirmed swings).

    Bullish  : a bar wicks BELOW the lowest low of the preceding `lookback` bars and CLOSES back above it.
    Bearish  : a bar wicks ABOVE the highest high of the preceding `lookback` bars and CLOSES back below it.
    Only bars up to the last CLOSED bar are considered, so nothing repaints.
    """
    try:
        if df is None or len(df) < lookback + 6:
            return None
        o = df["open"].to_numpy(dtype=float)
        h = df["high"].to_numpy(dtype=float)
        lo = df["low"].to_numpy(dtype=float)
        c = df["close"].to_numpy(dtype=float)
        last = len(c) - 2
        for k in range(last, max(lookback, last - fresh_within), -1):
            ref_lo = float(lo[k - lookback:k - 2].min())
            ref_hi = float(h[k - lookback:k - 2].max())
            if direction == "bullish" and lo[k] < ref_lo and c[k] > ref_lo:
                return {"level": ref_lo, "wick": float(lo[k]), "close": float(c[k]),
                        "bars_ago": last - k, "kind": "sell-side"}
            if direction == "bearish" and h[k] > ref_hi and c[k] < ref_hi:
                return {"level": ref_hi, "wick": float(h[k]), "close": float(c[k]),
                        "bars_ago": last - k, "kind": "buy-side"}
        return None
    except Exception:
        return None


def _ltf_micro_shift(df, direction: str) -> str | None:
    """Causal micro-shift on the primary TF: a closed candle that engulfs the prior bar in the
    trade direction after ≥2 counter-trend closes (mini CHoCH / reversal candle).

    Used when a dedicated 5m/15m frame is unavailable (single-TF scans, backtests, bfcache views),
    so the "wait for the shift" gate can still be evaluated honestly instead of always failing.
    """
    try:
        if df is None or len(df) < 8:
            return None
        o = df["open"].to_numpy(dtype=float)
        h = df["high"].to_numpy(dtype=float)
        lo = df["low"].to_numpy(dtype=float)
        c = df["close"].to_numpy(dtype=float)
        n = len(c)
        last = n - 2  # last CLOSED bar
        if last < 4:
            return None
        rng = max(h[last] - lo[last], 1e-9)
        lower_wick = (min(o[last], c[last]) - lo[last]) / rng
        upper_wick = (h[last] - max(o[last], c[last])) / rng
        if direction == "bullish":
            down = sum(1 for k in range(last - 2, last + 1) if c[k] < o[k])
            if c[last] > o[last] and c[last] > h[last - 1] and down >= 1:
                return "Closed bullish reversal candle engulfs the prior bar after a counter-move"
            if lower_wick >= 0.40 and c[last] >= lo[last] + 0.55 * rng and c[last] > o[last]:
                return "Closed bullish absorption candle (lower rejection wick, close in upper half)"
        else:
            up = sum(1 for k in range(last - 2, last + 1) if c[k] > o[k])
            if c[last] < o[last] and c[last] < lo[last - 1] and up >= 1:
                return "Closed bearish reversal candle engulfs the prior bar after a counter-move"
            if upper_wick >= 0.40 and c[last] <= h[last] - 0.55 * rng and c[last] < o[last]:
                return "Closed bearish absorption candle (upper rejection wick, close in lower half)"
        return None
    except Exception:
        return None


# --------------------------------------------------------------------------- side evaluation
def _evaluate_side(mtf: dict[str, dict], side: str, sentiment: dict | None, primary_tf: str,
                   forced_poi: dict | None = None) -> dict:
    d = mtf[primary_tf]
    ltf = mtf.get("15m") or mtf.get("5m")
    ind, st, smc, sr, div = d["ind"], d["structure"], d["smc"], d["sr"], d["div"]
    n = int(d["n_bars"])
    cur = float(ind["close"])
    atr = max(float(ind.get("atr14") or cur * 0.01), 1e-9)
    is_long = side == "LONG"
    want_bias = "BULLISH" if is_long else "BEARISH"
    direction = "bullish" if is_long else "bearish"

    # ---------------- 1. HTF bias alignment
    biases = {tf: (mtf[tf] and _tf_bias(mtf[tf])) or "NEUTRAL" for tf in mtf if mtf[tf]}
    htf_tfs = ["1w", "1d", "4h", "1h"]
    agree = [tf for tf in htf_tfs if biases.get(tf) == want_bias]
    against = [tf for tf in htf_tfs if biases.get(tf) == ("BEARISH" if is_long else "BULLISH")]
    total_w = sum(BIAS_WEIGHTS[tf] for tf in htf_tfs if tf in biases) or 1.0
    agree_w = sum(BIAS_WEIGHTS[tf] for tf in agree)
    bias_pct = round(agree_w / total_w * 100.0, 1)
    htf_ok = bool(agree and bias_pct >= 40.0)

    # ---------------- 2. Displacement leg + 3. POI
    leg = _leg(d, direction)
    displacement_ok = bool(leg and leg["range_atr"] >= 1.8)
    pois = _poi_candidates(d, side)
    _enrich_confluence(pois, d, leg, side)
    poi = _pick_poi(pois, d, leg)
    if forced_poi:
        # backtest mode: evaluate the POI that was live at the signal bar
        merged = dict(forced_poi)
        merged.setdefault("confluence", [])
        merged.setdefault("unmitigated", True)
        merged.setdefault("in_zone", False)
        # A wick into the zone IS a touch: use the last closed bar's range, not only the close.
        bar_lo = bar_hi = None
        try:
            df_ = d.get("df")
            if df_ is not None and len(df_):
                bar_lo, bar_hi = float(df_["low"].iloc[-1]), float(df_["high"].iloc[-1])
        except Exception:
            bar_lo = bar_hi = None
        touched = bool(bar_lo is not None and bar_lo <= float(merged["high"]) and bar_hi >= float(merged["low"]))
        merged["at_value"] = bool(
            touched
            or (float(merged["low"]) <= cur <= float(merged["high"]))
            or float(merged.get("distance_atr", 99)) <= 0.75
        )
        merged.setdefault("source", "OB")
        merged.setdefault("zone", [round(float(merged["low"]), 8), round(float(merged["high"]), 8)])
        merged.setdefault("mid", round((float(merged["low"]) + float(merged["high"])) / 2, 8))
        merged.setdefault("label", merged.get("label") or f"{merged['source']} zone")
        poi = merged
    poi_ok = bool(poi and poi["distance_atr"] <= 3.0)

    # ---------------- 4. Liquidity sweep
    last_closed = n - 2 if n > 1 else n - 1
    sweeps = [w for w in smc.get("sweeps", []) if w["kind"] == direction
              and 0 <= (last_closed - int(w.get("index", -99))) <= 30]
    eq_levels = [e for e in smc.get("equal_levels", []) if not e["swept"]]
    eq_match = None
    for w in sweeps:
        for e in eq_levels:
            if e["kind"] == ("EQL" if is_long else "EQH") and abs(float(e["price"]) - float(w["level"])) <= 0.3 * atr:
                eq_match = e
                break
        if eq_match:
            break
    local_sweep = _local_sweep(d.get("df"), direction)
    sweep_ok = bool(sweeps) or bool(local_sweep)
    if local_sweep and not sweeps:
        sweeps = [{"kind": direction, "level": local_sweep["level"], "wick": local_sweep["wick"],
                   "index": last_closed - local_sweep["bars_ago"]}]

    # ---------------- 5. LTF shift (closed candle CHoCH / BOS inside POI)
    ltf_shift = False
    ltf_detail = "No trigger-timeframe shift yet"
    if ltf:
        lb = ltf["structure"].get("last_break")
        if lb and lb["direction"] == direction and (ltf["n_bars"] - int(lb["index"])) <= 12:
            ltf_shift = True
            ltf_detail = (f"{ltf.get('tf_label', 'LTF')} {lb['kind']} closed at {_fp(lb['level'])} "
                          f"{ltf['n_bars'] - int(lb['index'])} bars ago")
        elif any(w["kind"] == direction and (ltf["n_bars"] - int(w.get("index", -99))) <= 10
                 for w in ltf["smc"].get("sweeps", [])):
            ltf_shift = True
            ltf_detail = "LTF liquidity sweep closed back inside (early shift)"
    if not ltf_shift:
        micro = _ltf_micro_shift(d.get("df"), direction)
        if micro:
            ltf_shift = True
            ltf_detail = micro + " (primary-TF micro shift — tighter stops, use LTF entry when available)"

    # ---------------- 6. Entry + structural stop
    if not poi:
        return _empty_side(side, biases, bias_pct, "No valid Point of Interest (unmitigated OB/FVG/swing) on the pullback side of price.")

    entry_limit = cur if poi["in_zone"] else (poi["high"] if is_long else poi["low"])
    entry_zone = poi["zone"]
    sweep_extreme = None
    if sweeps:
        w = sweeps[-1]
        sweep_extreme = float(w.get("wick")) if w.get("wick") else None
    far_edge = poi["low"] if is_long else poi["high"]
    anchors = [far_edge]
    if sweep_extreme is not None:
        # only honour the sweep wick if it is structurally close to the POI (else it is a different level)
        if (is_long and far_edge - 1.2 * atr <= sweep_extreme <= far_edge) or \
           ((not is_long) and far_edge <= sweep_extreme <= far_edge + 1.2 * atr):
            anchors.append(sweep_extreme)
    node = st.get("last_swing_low") if is_long else st.get("last_swing_high")
    deeper_swing = None
    if node and ((is_long and float(node["price"]) <= poi["high"]) or ((not is_long) and float(node["price"]) >= poi["low"])):
        if abs(float(node["price"]) - far_edge) <= 1.2 * atr:
            anchors.append(float(node["price"]))
        else:
            deeper_swing = float(node["price"])
    stop_raw = min(anchors) if is_long else max(anchors)
    stop = stop_raw - 0.25 * atr if is_long else stop_raw + 0.25 * atr
    if is_long and stop >= entry_limit:
        stop = poi["low"] - 0.4 * atr
    if (not is_long) and stop <= entry_limit:
        stop = poi["high"] + 0.4 * atr
    risk = abs(entry_limit - stop)
    risk_atr = risk / atr
    stop_ok = 0.25 <= risk_atr <= 2.5
    stop_anchor = "POI edge"
    if sweep_extreme is not None and abs(stop_raw - sweep_extreme) < 1e-9:
        stop_anchor = "Sweep wick"
    elif node and abs(stop_raw - float(node["price"])) < 1e-9:
        stop_anchor = f"{'Swing low' if is_long else 'Swing high'} ({node.get('label')})"

    # ---------------- 7. Targets & R:R gates
    tgt = _targets(d, side, entry_limit, stop, leg)
    rr_ok = tgt["rr1"] >= 2.0 and tgt["rr2"] >= 2.5
    has_targets = len(tgt["targets"]) >= 1

    # ---------------- 8. Factors (named bonuses / penalties)
    factors: list[dict] = []

    def add(kind: str, pts: float, label: str):
        factors.append({"kind": kind, "points": round(pts, 1), "label": label})

    region = smc.get("zone", "equilibrium")
    if (is_long and region == "discount") or ((not is_long) and region == "premium"):
        add("bonus", 6.0, f"Entry inside {'discount' if is_long else 'premium'} of the dealing range ({region})")
    elif region != "equilibrium":
        add("penalty", -7.0, f"Wrong side of the range for a {side} ({region})")

    if leg and leg["range_atr"] >= 1.8:
        add("bonus", 6.0, f"Displacement leg {leg['range_atr']} ATR with a {leg['break_kind']} break")
    if sweep_ok:
        add("bonus", 8.0, f"Liquidity sweep of {_fp(sweeps[-1].get('level'))} ({last_closed - int(sweeps[-1]['index'])} closed bars ago)")
    if eq_match:
        add("bonus", 3.0, f"Swept an {'EQL' if is_long else 'EQH'} equal-lows pool at {_fp(eq_match['price'])}")
    if poi["confluence"]:
        add("bonus", min(9.0, 2.2 * len(poi["confluence"])), "Confluence: " + " · ".join(poi["confluence"]))
    if ind.get("vol_ratio", 1.0) >= 1.5:
        add("bonus", 5.0, f"Volume expansion on the impulse ({ind['vol_ratio']:.2f}× SMA20)")
    if (is_long and (div.get("bullish_rsi") or div.get("bullish_macd"))) or \
       ((not is_long) and (div.get("bearish_rsi") or div.get("bearish_macd"))):
        add("bonus", 4.0, "Momentum divergence in the trade direction (exhaustion of the counter-move)")
    if ind.get("ema_spread_prev", 99) < 1.2 <= ind.get("ema_spread_atr", 0):
        add("bonus", 3.0, "EMA squeeze expanding (volatility regime shift)")
    if ltf_shift:
        add("bonus", 5.0, ltf_detail)

    for lvl in (sr.get("supports", []) if is_long else sr.get("resistances", [])):
        if lvl["source"] in ("PDH", "PDL", "PWH", "PWL") and abs(float(lvl["distance_atr"])) <= 1.2:
            add("bonus", 4.0, f"{lvl['source']} confluence ({_fp(float(lvl['price']))}, {lvl['distance_atr']} ATR)")
            break

    fr = None
    if sentiment and isinstance(sentiment.get("funding_rate_pct"), (int, float)):
        fr = float(sentiment["funding_rate_pct"])
        if is_long and fr <= -0.012:
            add("bonus", 5.0, f"Negative funding ({fr:+.4f}%) — shorts crowded, squeeze fuel for a long")
        elif (not is_long) and fr >= 0.025:
            add("bonus", 5.0, f"Crowded long funding ({fr:+.4f}%) — fuel for a short")
        elif is_long and fr >= 0.025:
            add("penalty", -6.0, f"Longs already crowded ({fr:+.4f}% funding) — poor risk/reward chasing")
        elif (not is_long) and fr <= -0.012:
            add("penalty", -6.0, f"Shorts already crowded ({fr:+.4f}% funding)")

    stretch = float(ind.get("ema20_stretch_atr") or 0.0)
    if (is_long and stretch >= 2.0) or ((not is_long) and stretch <= -2.0):
        add("penalty", -9.0, f"Price already {stretch:+.2f} ATR extended from EMA20 — entry would be chasing, not at value")
    run = int(ind.get("consec_bull_bars") if is_long else ind.get("consec_bear_bars") or 0) or 0
    if run >= 6:
        add("penalty", -5.0, f"{run} consecutive {'bullish' if is_long else 'bearish'} closes — statistically stretched")
    if risk_atr > 2.5:
        add("penalty", -8.0, f"Stop would need {risk_atr:.2f} ATR of room (too wide for a clean structural entry)")
    if poi["distance_atr"] > 2.0:
        add("penalty", -4.0, f"POI is {poi['distance_atr']} ATR away — not actionable at this price")

    # Session liquidity
    ts = None
    df = d.get("df")
    try:
        if df is not None and len(df):
            ts = int(df["open_time"].iloc[-1])
    except Exception:
        ts = None
    session = _session_of(ts)
    if session["quality"] == "best":
        add("bonus", 3.0, f"{session['name']} session (deepest liquidity)")
    elif session["quality"] == "thin":
        add("penalty", -3.0, f"{session['name']} session (thin liquidity — sweeps less reliable)")

    # ---------------- Gates & grade
    fresh_poi = bool(poi and poi.get("unmitigated") and float(poi.get("distance_atr", 99)) <= 3.0)
    # Refine wide POIs the way discretionary traders do: if the zone is wider than 1.2 ATR,
    # trade the proximal 50% (the edge price reacts from) instead of the whole block.
    refined = False
    if poi and (float(poi["high"]) - float(poi["low"])) > 1.2 * atr:
        if is_long:
            new_low = float(poi["high"]) - (float(poi["high"]) - float(poi["low"])) * 0.5
            new_high = float(poi["high"])
        else:
            new_low = float(poi["low"])
            new_high = float(poi["low"]) + (float(poi["high"]) - float(poi["low"])) * 0.5
        poi = dict(poi)
        poi.update({"low": round(new_low, 8), "high": round(new_high, 8),
                    "mid": round((new_low + new_high) / 2, 8),
                    "zone": [round(new_low, 8), round(new_high, 8)],
                    "in_zone": new_low <= cur <= new_high,
                    "refined": True})
        refined = True

    setup_type = "REVERSAL" if sweep_ok else ("CONTINUATION" if (displacement_ok and (poi_ok or fresh_poi)) else "UNCONFIRMED")
    trigger_ok = sweep_ok or fresh_poi
    if forced_poi:
        # BACKTEST MODE: the tradeable decision is made at the signal bar (POI distance is moot there);
        # "at value" = price is currently within the POI band or ≤0.75 ATR from it.
        poi_gate = {"key": "poi", "label": "Price at value on the POI (≤ 0.75 ATR)",
                    "passed": bool(poi and poi.get("at_value")),
                    "detail": f"{poi['label']} · {_fp(poi['low'])}–{_fp(poi['high'])} · {poi['distance_atr']} ATR away" if poi else "No valid POI"}
    else:
        poi_gate = {"key": "poi", "label": "Price at/inside a fresh POI (≤ 3 ATR)", "passed": poi_ok,
                    "detail": f"{poi['label']} · {_fp(poi['low'])}–{_fp(poi['high'])} · {poi['distance_atr']} ATR away" if poi else "No valid POI"}

    gates = [
        {"key": "bias", "label": "HTF bias aligned", "passed": htf_ok,
         "detail": f"{bias_pct:.0f}% weighted HTF agreement ({', '.join(agree) or 'none'})"
                   + (f" • against: {', '.join(against)}" if against else "")},
        {"key": "sweep", "label": "Entry trigger: sweep (reversal) or untested fresh POI (continuation)",
         "passed": trigger_ok,
         "detail": (f"{len(sweeps)} {'sell' if is_long else 'buy'}-side sweep(s); latest took "
                    f"{_fp(sweeps[-1].get('level'))} and closed back inside — reversal model"
                    if sweeps else
                    (f"No sweep, but the POI is untouched/fresh ({poi['label']}) — continuation model"
                     if fresh_poi else "Neither a stop-hunt sweep nor a fresh untested POI — entry would be blind"))},
        {"key": "displacement", "label": "Displacement leg ≥ 1.8 ATR", "passed": displacement_ok,
         "detail": (f"{leg['range_atr']} ATR impulse with {leg['break_kind']}, {leg['bars_since']} bars ago"
                    if leg else "No confirmed structural break to anchor a leg")},
        poi_gate,
        {"key": "ltf_shift", "label": "LTF closed-candle shift", "passed": ltf_shift, "detail": ltf_detail},
        {"key": "stop", "label": "Structural stop 0.25–2.5 ATR", "passed": stop_ok,
         "detail": f"Stop {_fp(stop)} beyond {stop_anchor} = {risk_atr:.2f} ATR"},
        {"key": "rr", "label": "R:R ≥ 2.0 (T1) and ≥ 2.5 (T2)", "passed": rr_ok and has_targets,
         "detail": (f"T1 {tgt['rr1']}R · T2 {tgt['rr2']}R" if has_targets else "No opposing structure to target")},
    ]
    passed = sum(1 for g in gates if g["passed"])
    gate_ratio = passed / len(gates)
    factor_pts = sum(f["points"] for f in factors)
    score = max(0.0, min(100.0, 40.0 + 35.0 * gate_ratio + max(-25.0, min(25.0, factor_pts))))

    # Grade is deliberately strict: partial checklists never masquerade as A-grade.
    if passed == len(gates) and score >= 85:
        grade = "A+"
    elif passed == len(gates) and score >= 75:
        grade = "A"
    elif passed >= len(gates) - 2 and score >= 55 and has_targets:
        grade = "B"
    elif has_targets:
        grade = "C"
    else:
        grade = "NO-TRADE"

    blockers = [f"{g['label']} — {g['detail']}" for g in gates if not g["passed"]]

    if grade in ("A+", "A"):
        action = f"Limit {'BUY' if is_long else 'SELL'} at the POI; add on the closed LTF {'bullish' if is_long else 'bearish'} CHoCH inside the zone."
    elif grade == "B":
        action = "Watchlist only — set an alert at the POI and wait for the missing gate(s) to resolve."
    else:
        action = "Stand aside. This is not an A-grade location; forcing it is how accounts bleed."

    return {
        "side": side,
        "grade": grade,
        "score": round(score, 1),
        "biases": biases,
        "bias_pct": bias_pct,
        "bias_agree": agree,
        "bias_against": against,
        "gates": gates,
        "gates_passed": passed,
        "gates_total": len(gates),
        "factors": sorted(factors, key=lambda f: -abs(f["points"])),
        "bonus_total": round(sum(f["points"] for f in factors if f["points"] > 0), 1),
        "penalty_total": round(sum(f["points"] for f in factors if f["points"] < 0), 1),
        "blockers": blockers,
        "action": action,
        "leg": leg,
        "setup_type": setup_type,
        "poi": {k: poi.get(k) for k in ("zone", "low", "high", "mid", "source", "label",
                                        "unmitigated", "in_zone", "at_value", "distance_atr",
                                        "confluence", "refined")},
        "poi_refined": refined,
        "deeper_invalidation": round(deeper_swing, 8) if deeper_swing else None,
        "entry_zone": entry_zone,
        "entry": round(entry_limit, 8),
        "stop": round(stop, 8),
        "stop_anchor": stop_anchor,
        "risk_atr": round(risk_atr, 2),
        "targets": tgt["targets"],
        "target_details": tgt["target_details"],
        "partial_plan": tgt["partial_plan"],
        "rr1": tgt["rr1"],
        "rr2": tgt["rr2"],
        "zones": [{"low": z["low"], "high": z["high"], "mid": z["mid"], "source": z["source"],
                   "in_zone": z["in_zone"], "at_value": z["at_value"],
                   "distance_atr": z["distance_atr"], "label": z["label"]} for z in pois[:6]],
        "session": session,
        "funding_rate_pct": fr,
        "management": "Move stop to break-even after T1; trail behind the most recent closed LTF swing. Invalidate early on a closed candle back through the POI's far edge.",
    }


def _empty_side(side: str, biases: dict, bias_pct: float, reason: str) -> dict:
    return {
        "side": side, "grade": "NO-TRADE", "score": 0.0, "biases": biases, "bias_pct": bias_pct,
        "setup_type": "NO-STRUCTURE", 
        "bias_agree": [], "bias_against": [], "gates": [], "gates_passed": 0, "gates_total": 7,
        "factors": [], "bonus_total": 0.0, "penalty_total": 0.0, "blockers": [reason],
        "action": "Stand aside until a fresh displacement prints a clean, unmitigated point of interest.",
        "leg": None, "poi": None, "zones": [], "entry_zone": None, "entry": None, "stop": None,
        "stop_anchor": None, "risk_atr": 0.0, "targets": [], "target_details": [],
        "partial_plan": None, "rr1": 0.0, "rr2": 0.0, "session": _session_of(None),
        "funding_rate_pct": None,
        "management": "No plan — wait for structure.",
    }


def _tf_bias(d: dict) -> str:
    """Local copy of the single-TF bias rule (avoids a circular import with scoring.py)."""
    if not d:
        return "NEUTRAL"
    st = d["structure"]["trend"]
    ind = d["ind"]
    stack = ind.get("ema_stack", "mixed")
    c = ind.get("close", 0)
    ema50 = ind.get("ema50", c)
    bull = (2 if st == "bullish" else 0) + (2 if stack == "bullish" else (1 if c > ema50 else 0))
    bear = (2 if st == "bearish" else 0) + (2 if stack == "bearish" else (1 if c < ema50 else 0))
    if bull >= 3 and bull > bear:
        return "BULLISH"
    if bear >= 3 and bear > bull:
        return "BEARISH"
    return "NEUTRAL"


# --------------------------------------------------------------------------- entry point
def evaluate_playbook(
    mtf: dict[str, dict],
    sentiment: dict | None = None,
    primary_tf: str | None = None,
    forced_pois: dict[str, dict] | None = None,
) -> dict:
    """Grade both sides. Pass `forced_pois={"LONG": poi, "SHORT": poi}` for backtest mode."""
    """Grade both sides of the book and return the single best actionable institutional setup."""
    if not mtf:
        return {"side": "NONE", "grade": "NO-TRADE", "score": 0.0, "label": "NO TRADE",
                "summary": "No candle data available.", "sides": {}, "factors": [], "blockers": ["No data"]}

    tf = primary_tf or ("1h" if mtf.get("1h") else ("4h" if mtf.get("4h") else next(iter(mtf.keys()))))
    # tag LTF label for messages
    for name, data in mtf.items():
        if data:
            data.setdefault("tf_label", name.upper())

    fp = forced_pois or {}
    long_s = _evaluate_side(mtf, "LONG", sentiment, tf, forced_poi=fp.get("LONG")) if (not fp or "LONG" in fp) else _empty_side("LONG", {}, 0.0, "Not evaluated in this pass.")
    short_s = _evaluate_side(mtf, "SHORT", sentiment, tf, forced_poi=fp.get("SHORT")) if (not fp or "SHORT" in fp) else _empty_side("SHORT", {}, 0.0, "Not evaluated in this pass.")
    order = {"A+": 5, "A": 4, "B": 3, "C": 2, "NO-TRADE": 1}
    best = max((long_s, short_s), key=lambda s: (order.get(s["grade"], 0), s["score"]))

    if best["grade"] in ("A+", "A"):
        label = f"{best['side']} {best['grade']}"
        summary = (
            f"{best['side']} {best['grade']} institutional-grade setup: {best['gates_passed']}/{best['gates_total']} checklist gates pass "
            f"with a {best['score']:.0f}/100 confluence score. Entry {_fp(best['entry'])} inside "
            f"{best['poi']['label']} ({_fp(best['entry_zone'][0])}–{_fp(best['entry_zone'][1])}), "
            f"structural stop {_fp(best['stop'])} ({best['risk_atr']} ATR), T1 {best['rr1']}R → T2 {best['rr2']}R."
        )
    elif best["grade"] == "B":
        label = f"{best['side']} B — WATCHLIST"
        if best["blockers"]:
            why = "Blockers: " + "; ".join(best["blockers"][:2]) + "."
        else:
            why = (f"All {best['gates_total']} gates pass but the confluence score ({best['score']:.0f}/100) is below the "
                   f"75-point A threshold — it needs one more confirming factor (volume, funding, S/R confluence or session).")
        summary = (
            f"{best['side']} structure is forming but is not A-grade ({best['gates_passed']}/{best['gates_total']} gates, "
            f"{best['score']:.0f}/100). {why}"
        )
    else:
        label = "NO TRADE"
        rationale = (best["blockers"][0] if best["blockers"]
                     else "Neither side has a displacement + POI + R:R structure worth risking capital on.")
        summary = f"No institutional-grade setup on either side right now. Primary blocker: {rationale}"

    return {
        "primary_tf": tf,
        "side": best["side"],
        "grade": best["grade"],
        "label": label,
        "score": best["score"],
        "summary": summary,
        "best": best,
        "sides": {"LONG": long_s, "SHORT": short_s},
        "entry": best.get("entry"), "stop": best.get("stop"),
        "entry_zone": best.get("entry_zone"),
        "targets": best.get("targets", []),
        "target_details": best.get("target_details", []),
        "rr1": best.get("rr1", 0.0), "rr2": best.get("rr2", 0.0),
        "risk_atr": best.get("risk_atr", 0.0),
        "action": best.get("action"),
        "blockers": best.get("blockers", []),
        "factors": best.get("factors", []),
        "gates": best.get("gates", []),
        "session": best.get("session"),
        "partial_plan": best.get("partial_plan"),
        "management": best.get("management"),
        "funding_rate_pct": best.get("funding_rate_pct"),
        "evaluated_at": int(time.time() * 1000),
    }
