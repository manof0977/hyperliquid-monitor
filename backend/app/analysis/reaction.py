"""Pre-Indicator Price-Action & Liquidity Reaction Engine.

Answers the exact question price-action / SMC traders ask BEFORE lagging indicators
(EMA crossovers, MACD, RSI) flip:
  1. May price BOUNCE from here?        (Trend-continuation tap into Demand OB / Bullish FVG / PDL / Support)
  2. May price PULL BACK from here?     (Rubber-band ATR overextension from EMA20 into Premium / Supply)
  3. May price REVERSE from here?       (Liquidity stop-sweep + rejection wick at major Demand/Supply wall)
  4. Or is price in MID-RANGE?          (No resting institutional orders here — wait for exact zone above/below)

100% mathematical & transparent: zero fabricated AI predictions, zero look-ahead bias,
and all invalidation/target levels come strictly from real chart structure.
"""


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


def _nearest_demand_zone(price: float, atr: float, smc: dict, sr: dict, st: dict) -> dict | None:
    """Find the closest real institutional Demand / Bounce zone at or below current price."""
    candidates: list[dict] = []

    for ob in (smc or {}).get("order_blocks", []):
        if ob["kind"] != "bullish":
            continue
        low, high = float(ob["bottom"]), float(ob["top"])
        if low <= price * 1.008:
            in_z = low <= price <= high
            dist = 0.0 if in_z else min(abs(price - high), abs(price - low)) / atr
            if dist <= 6.5:
                fresh = not ob.get("mitigated")
                score = (12.0 if in_z else max(0.0, 9.0 - dist * 1.6)) + (2.5 if fresh else 0.0)
                candidates.append({
                    "label": f"{'Unmitigated' if fresh else 'Active'} Bullish Demand OB",
                    "low": low,
                    "high": high,
                    "in_zone": in_z,
                    "distance_atr": round(dist, 2),
                    "score": score,
                })

    for g in (smc or {}).get("fvgs", []):
        if g["kind"] != "bullish":
            continue
        low, high = float(g["bottom"]), float(g["top"])
        if low <= price * 1.008:
            in_z = low <= price <= high
            dist = 0.0 if in_z else min(abs(price - high), abs(price - low)) / atr
            if dist <= 6.0:
                score = 10.5 if in_z else max(0.0, 7.8 - dist * 1.5)
                candidates.append({
                    "label": "Unfilled Bullish FVG Imbalance",
                    "low": low,
                    "high": high,
                    "in_zone": in_z,
                    "distance_atr": round(dist, 2),
                    "score": score,
                })

    if sr:
        for sup in sr.get("supports", [])[:3]:
            sp = float(sup["price"])
            if sp <= price * 1.005:
                band_lo, band_hi = sp - 0.15 * atr, sp + 0.15 * atr
                in_z = band_lo <= price <= band_hi
                dist = 0.0 if in_z else abs(price - sp) / atr
                if dist <= 6.0:
                    candidates.append({
                        "label": f"Key Support ({sup['source']})",
                        "low": band_lo,
                        "high": band_hi,
                        "in_zone": in_z,
                        "distance_atr": round(dist, 2),
                        "score": 9.0 if in_z else max(0.0, 6.8 - dist * 1.4),
                    })
        for tag, val in (("Previous Day Low (PDL)", sr.get("pdl")), ("Previous Week Low (PWL)", sr.get("pwl"))):
            if val and float(val) <= price * 1.006:
                sp = float(val)
                band_lo, band_hi = sp - 0.15 * atr, sp + 0.15 * atr
                in_z = band_lo <= price <= band_hi
                dist = 0.0 if in_z else abs(price - sp) / atr
                if dist <= 6.0:
                    candidates.append({
                        "label": tag,
                        "low": band_lo,
                        "high": band_hi,
                        "in_zone": in_z,
                        "distance_atr": round(dist, 2),
                        "score": 9.5 if in_z else max(0.0, 7.2 - dist * 1.4),
                    })

    if st and st.get("last_swing_low"):
        sl_p = float(st["last_swing_low"]["price"])
        if sl_p <= price * 1.006:
            band_lo, band_hi = sl_p - 0.15 * atr, sl_p + 0.15 * atr
            in_z = band_lo <= price <= band_hi
            dist = 0.0 if in_z else abs(price - sl_p) / atr
            if dist <= 6.0:
                candidates.append({
                    "label": f"Protected Swing Low ({st['last_swing_low']['label']})",
                    "low": band_lo,
                    "high": band_hi,
                    "in_zone": in_z,
                    "distance_atr": round(dist, 2),
                    "score": 9.2 if in_z else max(0.0, 7.0 - dist * 1.4),
                })

    if not candidates:
        return None
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    inv = best["low"] - 0.25 * atr
    dr = (smc or {}).get("dealing_range")
    nr = (sr or {}).get("nearest_resistance")
    tgt_candidates = [
        p for p in [
            dr["eq"] if dr else None,
            nr["price"] if nr else None,
            dr["high"] if dr else None,
        ]
        if p is not None and float(p) >= best["high"] + 0.55 * atr
    ]
    tgt = min(tgt_candidates) if tgt_candidates else (best["high"] + 1.6 * atr)
    return {
        "label": best["label"],
        "low": round(best["low"], 8),
        "high": round(best["high"], 8),
        "in_zone": best["in_zone"],
        "distance_atr": best["distance_atr"],
        "invalidation": round(inv, 8),
        "target_1": round(float(tgt), 8),
    }


def _nearest_supply_zone(price: float, atr: float, smc: dict, sr: dict, st: dict, ema20: float) -> dict | None:
    """Find the closest real institutional Supply / Pullback / Top-Reversal zone at or above current price."""
    candidates: list[dict] = []

    for ob in (smc or {}).get("order_blocks", []):
        if ob["kind"] != "bearish":
            continue
        low, high = float(ob["bottom"]), float(ob["top"])
        if high >= price * 0.992:
            in_z = low <= price <= high
            dist = 0.0 if in_z else min(abs(price - low), abs(price - high)) / atr
            if dist <= 6.5:
                fresh = not ob.get("mitigated")
                score = (12.0 if in_z else max(0.0, 9.0 - dist * 1.6)) + (2.5 if fresh else 0.0)
                candidates.append({
                    "label": f"{'Unmitigated' if fresh else 'Active'} Bearish Supply OB",
                    "low": low,
                    "high": high,
                    "in_zone": in_z,
                    "distance_atr": round(dist, 2),
                    "score": score,
                })

    for g in (smc or {}).get("fvgs", []):
        if g["kind"] != "bearish":
            continue
        low, high = float(g["bottom"]), float(g["top"])
        if high >= price * 0.992:
            in_z = low <= price <= high
            dist = 0.0 if in_z else min(abs(price - low), abs(price - high)) / atr
            if dist <= 6.0:
                score = 10.5 if in_z else max(0.0, 7.8 - dist * 1.5)
                candidates.append({
                    "label": "Unfilled Bearish FVG Imbalance",
                    "low": low,
                    "high": high,
                    "in_zone": in_z,
                    "distance_atr": round(dist, 2),
                    "score": score,
                })

    if sr:
        for res in sr.get("resistances", [])[:3]:
            rp = float(res["price"])
            if rp >= price * 0.995:
                band_lo, band_hi = rp - 0.15 * atr, rp + 0.15 * atr
                in_z = band_lo <= price <= band_hi
                dist = 0.0 if in_z else abs(rp - price) / atr
                if dist <= 6.0:
                    candidates.append({
                        "label": f"Key Resistance ({res['source']})",
                        "low": band_lo,
                        "high": band_hi,
                        "in_zone": in_z,
                        "distance_atr": round(dist, 2),
                        "score": 9.0 if in_z else max(0.0, 6.8 - dist * 1.4),
                    })
        for tag, val in (("Previous Day High (PDH)", sr.get("pdh")), ("Previous Week High (PWH)", sr.get("pwh"))):
            if val and float(val) >= price * 0.994:
                rp = float(val)
                band_lo, band_hi = rp - 0.15 * atr, rp + 0.15 * atr
                in_z = band_lo <= price <= band_hi
                dist = 0.0 if in_z else abs(rp - price) / atr
                if dist <= 6.0:
                    candidates.append({
                        "label": tag,
                        "low": band_lo,
                        "high": band_hi,
                        "in_zone": in_z,
                        "distance_atr": round(dist, 2),
                        "score": 9.5 if in_z else max(0.0, 7.2 - dist * 1.4),
                    })

    if st and st.get("last_swing_high"):
        sh_p = float(st["last_swing_high"]["price"])
        if sh_p >= price * 0.994:
            band_lo, band_hi = sh_p - 0.15 * atr, sh_p + 0.15 * atr
            in_z = band_lo <= price <= band_hi
            dist = 0.0 if in_z else abs(sh_p - price) / atr
            if dist <= 6.0:
                candidates.append({
                    "label": f"Protected Swing High ({st['last_swing_high']['label']})",
                    "low": band_lo,
                    "high": band_hi,
                    "in_zone": in_z,
                    "distance_atr": round(dist, 2),
                    "score": 9.2 if in_z else max(0.0, 7.0 - dist * 1.4),
                })

    if not candidates:
        return None
    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    inv = best["high"] + 0.25 * atr
    dr = (smc or {}).get("dealing_range")
    ns = (sr or {}).get("nearest_support")
    tgt_candidates = [
        p for p in [
            ema20 if ema20 > 0 else None,
            dr["eq"] if dr else None,
            ns["price"] if ns else None,
            dr["low"] if dr else None,
        ]
        if p is not None and float(p) <= best["low"] - 0.55 * atr
    ]
    tgt = max(tgt_candidates) if tgt_candidates else (best["low"] - 1.6 * atr)
    return {
        "label": best["label"],
        "low": round(best["low"], 8),
        "high": round(best["high"], 8),
        "in_zone": best["in_zone"],
        "distance_atr": best["distance_atr"],
        "invalidation": round(inv, 8),
        "target_1": round(float(tgt), 8),
    }


def classify_pre_indicator_reaction(
    tf_detail: dict | None,
    biases: dict[str, str] | None = None,
    sentiment: dict | None = None,
    tf_label: str = "1H",
) -> dict:
    """Deterministic Pre-Indicator Price-Action Radar:
    Evaluates raw location (OB/FVG/PDH/PDL/Range), recent stop-sweeps, closed-bar rejection wicks,
    rubber-band EMA20 stretch (in ATRs), divergence, and derivatives crowding BEFORE lagging indicators flip.
    """
    if not tf_detail:
        return {
            "state": "MID_RANGE_WAIT",
            "badge_label": "AWAITING DATA",
            "bias_lean": "NEUTRAL_WAIT",
            "range_position_pct": 50.0,
            "ema20_stretch_atr": 0.0,
            "summary": "Waiting for sufficient candle history to map pre-indicator bounce, pullback, and reversal zones.",
            "why_before_indicators": [],
            "bounce_zone": None,
            "rejection_zone": None,
            "aggressive_trigger": "Wait for candle data.",
            "conservative_trigger": "Wait for candle data.",
        }

    ind = tf_detail.get("ind") or {}
    st = tf_detail.get("structure") or {}
    smc = tf_detail.get("smc") or {}
    sr = tf_detail.get("sr") or {}
    div = tf_detail.get("div") or {}
    n_bars = int(tf_detail.get("n_bars") or 100)

    price = float(ind.get("close") or 0.0)
    atr = max(float(ind.get("atr14") or (price * 0.01) or 1e-8), 1e-8)
    ema20 = float(ind.get("ema20") or price)
    stretch = float(ind.get("ema20_stretch_atr") or ((price - ema20) / atr))
    c_lo_wick = float(ind.get("closed_lower_wick_pct") or 0.0)
    c_up_wick = float(ind.get("closed_upper_wick_pct") or 0.0)
    vol_ratio = float(ind.get("vol_ratio") or 1.0)
    consec_up = int(ind.get("consec_bull_bars") or 0)
    consec_dn = int(ind.get("consec_bear_bars") or 0)
    trend = st.get("trend", "neutral")
    ema_stack = ind.get("ema_stack", "mixed")
    if ema_stack == "bearish":
        tf_side = "bearish"
    elif ema_stack == "bullish":
        tf_side = "bullish"
    else:
        tf_side = "neutral" if trend == "neutral" else trend

    dr = smc.get("dealing_range")
    raw_pos = float(dr["position"]) if dr else 0.5
    pos_pct = round(max(0.0, min(1.0, raw_pos)) * 100.0, 1)
    zone_name = smc.get("zone", "equilibrium")

    biases = biases or {}
    htf_bull = biases.get("1d") == "BULLISH" or biases.get("4h") == "BULLISH"
    htf_bear = biases.get("1d") == "BEARISH" or biases.get("4h") == "BEARISH"

    # Recent liquidity sweeps (wick beyond swing high/low that CLOSED back inside within last 6 bars)
    last_closed_idx = n_bars - 2 if n_bars > 1 else n_bars - 1
    recent_bull_sweeps = [
        w for w in smc.get("sweeps", [])
        if w["kind"] == "bullish" and (last_closed_idx - int(w.get("index", -99))) <= 6
    ]
    recent_bear_sweeps = [
        w for w in smc.get("sweeps", [])
        if w["kind"] == "bearish" and (last_closed_idx - int(w.get("index", -99))) <= 6
    ]

    bounce_z = _nearest_demand_zone(price, atr, smc, sr, st)
    reject_z = _nearest_supply_zone(price, atr, smc, sr, st, ema20)

    b_dist = bounce_z["distance_atr"] if bounce_z else 99.0
    r_dist = reject_z["distance_atr"] if reject_z else 99.0

    fr = float((sentiment or {}).get("funding_rate_pct") or 0.0) if sentiment else 0.0
    bull_div = bool(div.get("bullish_rsi") or div.get("bullish_macd"))
    bear_div = bool(div.get("bearish_rsi") or div.get("bearish_macd"))

    range_note = ""
    if dr and raw_pos > 1.0:
        range_note = f" (price is {((raw_pos - 1.0) * 100):.0f}% ABOVE the last confirmed swing-high range ceiling — extended)"
    elif dr and raw_pos < 0.0:
        range_note = f" (price is {(abs(raw_pos) * 100):.0f}% BELOW the last confirmed swing-low range floor — extended)"
    evidence: list[str] = [
        f"Range Location: {pos_pct:.0f}% of {tf_label} dealing range ({zone_name.upper()}){range_note} • Stretch from EMA20: {stretch:+.2f} ATR."
    ]
    if recent_bull_sweeps:
        sw = recent_bull_sweeps[-1]
        evidence.append(
            f"Sell-Side Liquidity Sweep: Wick pierced below {_fp(sw['level'])} (low {_fp(sw['wick'])}) and closed back above — classic stop-hunt absorption."
        )
    if recent_bear_sweeps:
        sw = recent_bear_sweeps[-1]
        evidence.append(
            f"Buy-Side Liquidity Sweep: Wick pierced above {_fp(sw['level'])} (high {_fp(sw['wick'])}) and closed back below — classic breakout trap."
        )
    if c_lo_wick >= 35.0:
        evidence.append(f"Closed-Candle Lower Rejection Wick: {c_lo_wick:.0f}% of bar range (buying absorption at lows, Vol {vol_ratio:.2f}x SMA20).")
    if c_up_wick >= 35.0:
        evidence.append(f"Closed-Candle Upper Rejection Wick: {c_up_wick:.0f}% of bar range (selling supply at highs, Vol {vol_ratio:.2f}x SMA20).")
    if bull_div:
        evidence.append("Momentum Exhaustion: Bullish divergence (lower price low vs higher oscillator low — downside momentum fading).")
    if bear_div:
        evidence.append("Momentum Exhaustion: Bearish divergence (higher price high vs lower oscillator high — upside momentum fading).")
    if sentiment and "funding_rate_pct" in sentiment:
        if fr >= 0.025:
            evidence.append(f"Crowded Long Perp Funding ({fr:+.4f}%) — elevated pullback / long-squeeze vulnerability.")
        elif fr <= -0.012:
            evidence.append(f"Negative Perp Funding ({fr:+.4f}%) — crowded late shorts provide short-squeeze fuel on any bounce.")

    near_demand = b_dist <= 0.55
    near_supply = r_dist <= 0.55

    # 1. BOTTOM REVERSAL / STOP-HUNT ZONE
    if (
        tf_side == "bearish"
        and (
            recent_bull_sweeps
            or bull_div
            or (near_demand and c_lo_wick >= 45.0 and fr <= -0.012)
        )
    ):
        state = "BULLISH_REVERSAL_ZONE"
        badge = "BOTTOM REVERSAL"
        lean = "BULLISH_REACTION"
        z_txt = f"{_fp(bounce_z['low'])}–{_fp(bounce_z['high'])} ({bounce_z['label']})" if bounce_z else _fp(price)
        summary = (
            f"May Reverse Up From Here: Lagging EMAs/trend still read bearish (scaring indicator followers), "
            f"but raw price is reacting at {z_txt} with stop-hunt / exhaustion signs where pre-indicator traders stalk early longs."
        )

    # 2. TOP REVERSAL / DISTRIBUTION ZONE
    elif (
        tf_side == "bullish"
        and (
            recent_bear_sweeps
            or bear_div
            or (near_supply and c_up_wick >= 45.0 and fr >= 0.02)
        )
    ):
        state = "BEARISH_REVERSAL_ZONE"
        badge = "TOP REVERSAL"
        lean = "BEARISH_REACTION"
        z_txt = f"{_fp(reject_z['low'])}–{_fp(reject_z['high'])} ({reject_z['label']})" if reject_z else _fp(price)
        summary = (
            f"May Reverse Down From Here: Lagging EMAs/trend still look bullish (tempting late buyers), "
            f"but raw price is testing {z_txt} after a liquidity sweep / upside exhaustion where institutional supply caps rallies."
        )

    # 3. POTENTIAL BOUNCE FROM HERE
    elif near_demand and (htf_bull or tf_side != "bearish" or pos_pct <= 40.0):
        state = "BOUNCE_ZONE"
        badge = "BOUNCE ZONE"
        lean = "BULLISH_REACTION"
        z_txt = f"{_fp(bounce_z['low'])}–{_fp(bounce_z['high'])} ({bounce_z['label']})" if bounce_z else _fp(price)
        summary = (
            f"May Bounce From Here: Price dipped directly into {z_txt} ({pos_pct:.0f}% range position). "
            f"Pre-indicator limit/reaction traders watch this band for a bounce toward {_fp(bounce_z['target_1']) if bounce_z else 'equilibrium'} "
            f"as long as {_fp(bounce_z['invalidation']) if bounce_z else 'support'} holds."
        )

    # 4. PULLBACK / RETRACEMENT RISK FROM HERE
    elif (
        stretch >= 1.65
        or (consec_up >= 4 and pos_pct >= 72.0)
        or (near_supply and (c_up_wick >= 35.0 or htf_bear or pos_pct >= 78.0))
    ):
        state = "PULLBACK_DUE"
        badge = "PULLBACK DUE"
        lean = "BEARISH_REACTION"
        z_txt = f"{_fp(reject_z['low'])}–{_fp(reject_z['high'])} ({reject_z['label']})" if reject_z else "overhead supply"
        summary = (
            f"May Take Pullback From Here: Price is stretched {stretch:+.2f} ATR above EMA20 ({_fp(ema20)}) near {z_txt} ({pos_pct:.0f}% range). "
            f"Chasing longs here has poor R:R — price-action traders expect a pullback toward EMA20 ({_fp(ema20)})"
            + (f" or Bounce Zone {_fp(bounce_z['low'])}–{_fp(bounce_z['high'])}." if bounce_z else ".")
        )
    elif stretch <= -1.65 or (consec_dn >= 4 and pos_pct <= 25.0):
        state = "PULLBACK_DUE"
        badge = "SNAPBACK DUE"
        lean = "BULLISH_REACTION"
        summary = (
            f"May Snap Back Up From Here: Price is stretched {stretch:+.2f} ATR below EMA20 ({_fp(ema20)}) after {max(consec_dn, 2)}+ heavy sell bars ({pos_pct:.0f}% range). "
            f"Shorting into deep discount carries sharp relief-bounce risk toward EMA20 ({_fp(ema20)})."
        )

    # 5. MID-RANGE NO-MAN'S LAND
    else:
        state = "MID_RANGE_WAIT"
        badge = "MID-RANGE"
        lean = "NEUTRAL_WAIT"
        b_str = f"{_fp(bounce_z['low'])}–{_fp(bounce_z['high'])} ({bounce_z['distance_atr']} ATR below)" if bounce_z else "nearest swing low"
        r_str = f"{_fp(reject_z['low'])}–{_fp(reject_z['high'])} ({reject_z['distance_atr']} ATR above)" if reject_z else "nearest swing high"
        summary = (
            f"Mid-Range (No Pre-Indicator Edge Right Here): Price sits at {pos_pct:.0f}% of the dealing range with no institutional wall right here. "
            f"Pre-indicator traders wait for price to drop into Bounce Zone {b_str} or rally into Pullback/Supply Zone {r_str}."
        )

    if lean == "BEARISH_REACTION" and not reject_z:
        agg_trig = (
            "Aggressive Pre-Indicator Style: no overhead supply wall within 6 ATR — do not chase; "
            "wait for a fresh displacement + BOS to print a new Supply Order Block or bearish FVG to sell into."
        )
        cons_trig = "Safer Early Style: stand aside until price prints a clean closed-candle bearish CHoCH at a swing high."
    elif lean == "BULLISH_REACTION" and not bounce_z:
        agg_trig = (
            "Aggressive Pre-Indicator Style: no demand wall within 6 ATR — do not catch a falling knife; "
            "wait for a fresh sell-side sweep + bullish CHoCH to print a new Demand Order Block or bullish FVG."
        )
        cons_trig = "Safer Early Style: stand aside until price prints a clean closed-candle bullish CHoCH at a swing low."
    elif lean == "BEARISH_REACTION" and reject_z:
        agg_trig = (
            f"Aggressive Pre-Indicator Style: Watch {_fp(reject_z['low'])}–{_fp(reject_z['high'])} ({reject_z['label']}) for an upper rejection wick / sweep that closes back below {_fp(reject_z['high'])}. "
            f"Hard stop: {_fp(reject_z['invalidation'])} (0.25 ATR above zone) → Pullback Target: {_fp(reject_z['target_1'])}."
        )
        cons_trig = (
            f"Safer Early Style (Ahead of HTF Indicators): Let price tap {_fp(reject_z['low'])}–{_fp(reject_z['high'])} and enter on the first closed 5m/15m bearish CHoCH."
        )
    elif bounce_z:
        agg_trig = (
            f"Aggressive Pre-Indicator Style: Watch {_fp(bounce_z['low'])}–{_fp(bounce_z['high'])} ({bounce_z['label']}) for a lower rejection wick / stop-sweep that closes back above {_fp(bounce_z['low'])}. "
            f"Hard stop: {_fp(bounce_z['invalidation'])} (0.25 ATR below zone) → Bounce Target: {_fp(bounce_z['target_1'])}."
        )
        cons_trig = (
            f"Safer Early Style (Ahead of HTF Indicators): Let price hold {_fp(bounce_z['low'])}–{_fp(bounce_z['high'])} and enter on the first closed 5m/15m bullish CHoCH."
        )
    else:
        agg_trig = "Wait for a fresh impulse + BOS to leave behind a clean Order Block or Fair Value Gap."
        cons_trig = "Wait for closed-candle 5m/15m CHoCH confirmation at a structural swing."

    return {
        "state": state,
        "badge_label": badge,
        "bias_lean": lean,
        "range_position_pct": pos_pct,
        "ema20_stretch_atr": round(stretch, 2),
        "summary": summary,
        "why_before_indicators": evidence,
        "bounce_zone": bounce_z,
        "rejection_zone": reject_z,
        "aggressive_trigger": agg_trig,
        "conservative_trigger": cons_trig,
    }
