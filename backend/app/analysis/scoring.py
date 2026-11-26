"""Scoring Engine, Signal State Machine & Early Opportunity Detector.

TECHNICAL CHART SCORE (0-100):
  Market structure ........ 25
  SMC & liquidity ......... 25
  EMA & trend ............. 20
  Momentum & volume ....... 15
  Multi-TF agreement ...... 15

TRADE OPPORTUNITY SCORE (0-100):
  Entry-zone proximity .... 25
  Setup confirmation ...... 25
  Risk / reward ........... 25
  Invalidation clarity .... 15
  Remaining room to target  10

A high score NEVER auto-generates an entry signal — CONFIRMED requires explicit closed-candle
BOS/CHoCH + price inside/rejecting a real structural zone + valid structural R:R.
"""
from app.config import settings

TF_WEIGHTS = {"1w": 3.0, "1d": 3.0, "4h": 2.5, "1h": 2.0, "15m": 1.5, "5m": 1.0}


def tf_bias(tf_data: dict) -> str:
    """Single-timeframe directional bias: BULLISH | BEARISH | NEUTRAL."""
    if not tf_data:
        return "NEUTRAL"
    st = tf_data["structure"]["trend"]
    ind = tf_data["ind"]
    stack = ind.get("ema_stack", "mixed")
    c = ind.get("close", 0)
    ema50 = ind.get("ema50", c)
    bull_pts = (2 if st == "bullish" else 0) + (2 if stack == "bullish" else (1 if c > ema50 else 0))
    bear_pts = (2 if st == "bearish" else 0) + (2 if stack == "bearish" else (1 if c < ema50 else 0))
    if bull_pts >= 3 and bull_pts > bear_pts:
        return "BULLISH"
    if bear_pts >= 3 and bear_pts > bull_pts:
        return "BEARISH"
    return "NEUTRAL"


def _tech_score_side(side: str, primary: dict, biases: dict[str, str]) -> tuple[float, dict]:
    is_long = side == "LONG"
    target_dir = "bullish" if is_long else "bearish"
    target_bias = "BULLISH" if is_long else "BEARISH"

    st = primary["structure"]
    smc = primary["smc"]
    ind = primary["ind"]
    div = primary["div"]
    n_bars = primary["n_bars"]

    # 1. Market structure (0-25)
    ms = 0.0
    if st["trend"] == target_dir:
        ms += 12
    lb = st.get("last_break")
    if lb and lb["direction"] == target_dir:
        recency = max(0.0, 1.0 - (n_bars - lb["index"]) / 40.0)
        ms += (8 if lb["kind"] == "BOS" else 10) * (0.5 + 0.5 * recency)
    swings = st.get("swings", [])[-4:]
    if is_long:
        ms += min(5.0, sum(2.5 for s in swings if s["label"] in ("HH", "HL")))
    else:
        ms += min(5.0, sum(2.5 for s in swings if s["label"] in ("LH", "LL")))
    ms = min(25.0, ms)

    # 2. SMC & liquidity (0-25)
    smc_pts = 0.0
    obs = [o for o in smc["order_blocks"] if o["kind"] == target_dir]
    fvgs = [g for g in smc["fvgs"] if g["kind"] == target_dir]
    sweeps = [w for w in smc["sweeps"] if w["kind"] == target_dir and (n_bars - w["index"]) <= 20]
    if obs:
        smc_pts += 9
    if fvgs:
        smc_pts += 6
    if sweeps:
        smc_pts += 6
    zone = smc.get("zone", "equilibrium")
    if (is_long and zone == "discount") or (not is_long and zone == "premium"):
        smc_pts += 4
    smc_pts = min(25.0, smc_pts)

    # 3. EMA & trend (0-20)
    ema_pts = 0.0
    c = ind.get("close", 0)
    if ind.get("ema_stack") == target_dir:
        ema_pts += 12
    else:
        checks = [c > ind.get(k, c) for k in ("ema20", "ema50", "ema100", "ema200")]
        cnt = sum(checks) if is_long else sum(not x for x in checks)
        ema_pts += cnt * 2.5
    if (is_long and ind.get("ema20", 0) > ind.get("ema50", 0)) or (not is_long and ind.get("ema20", 0) < ind.get("ema50", 0)):
        ema_pts += 4
    if ind.get("ema_spread_atr", 99) < 1.5 and ind.get("ema_spread_atr", 99) > ind.get("ema_spread_prev", 99):
        ema_pts += 4  # squeeze starting to expand
    ema_pts = min(20.0, ema_pts)

    # 4. Momentum & volume (0-15)
    mom = 0.0
    r = ind.get("rsi14", 50)
    mh = ind.get("macd_hist", 0)
    vr = ind.get("vol_ratio", 1.0)
    if is_long:
        if 45 <= r <= 68:
            mom += 5
        elif 30 <= r < 45:
            mom += 3
        if mh > 0:
            mom += 4
        if div.get("bullish_rsi") or div.get("bullish_macd"):
            mom += 3
    else:
        if 32 <= r <= 55:
            mom += 5
        elif 55 < r <= 70:
            mom += 3
        if mh < 0:
            mom += 4
        if div.get("bearish_rsi") or div.get("bearish_macd"):
            mom += 3
    if vr >= 1.3:
        mom += 3
    elif vr >= 1.05:
        mom += 1.5
    mom = min(15.0, mom)

    # 5. Multi-TF agreement (0-15)
    total_w = sum(TF_WEIGHTS.get(tf, 1.0) for tf in biases)
    agree_w = sum(TF_WEIGHTS.get(tf, 1.0) for tf, b in biases.items() if b == target_bias)
    mtf = (agree_w / total_w * 15.0) if total_w > 0 else 0.0

    total = round(ms + smc_pts + ema_pts + mom + mtf, 1)
    return total, {
        "structure": round(ms, 1), "smc": round(smc_pts, 1),
        "ema": round(ema_pts, 1), "momentum": round(mom, 1), "mtf": round(mtf, 1),
    }


def _build_trade_plan(side: str, primary: dict, ltf: dict | None, primary_tf: str = "1h") -> dict:
    """Construct structural entry zone, invalidation & 3 structural targets + Opportunity Score."""
    is_long = side == "LONG"
    target_dir = "bullish" if is_long else "bearish"
    tf_lbl = primary_tf.upper()
    ind = primary["ind"]
    st = primary["structure"]
    smc = primary["smc"]
    sr = primary["sr"]
    cur = ind["close"]
    atr = max(ind.get("atr14", cur * 0.01), 1e-8)

    # Candidate entry zones: ONLY unviolated zones containing current price OR sitting on the pullback side
    # LONG -> Demand zone containing price OR below price (top <= cur)
    # SHORT -> Supply zone containing price OR above price (bottom >= cur)
    zones: list[tuple[float, float, str, str]] = []
    for ob in smc["order_blocks"]:
        if ob["kind"] == target_dir:
            contains = ob["bottom"] <= cur <= ob["top"]
            valid_pullback = (ob["top"] <= cur) if is_long else (ob["bottom"] >= cur)
            if contains or valid_pullback:
                zones.append((ob["bottom"], ob["top"], "OB", f"{tf_lbl} {'Bullish Demand' if is_long else 'Bearish Supply'} Order Block ({ob['break_kind']})"))
    for g in smc["fvgs"]:
        if g["kind"] == target_dir:
            contains = g["bottom"] <= cur <= g["top"]
            valid_pullback = (g["top"] <= cur) if is_long else (g["bottom"] >= cur)
            if contains or valid_pullback:
                zones.append((g["bottom"], g["top"], "FVG", f"{tf_lbl} {'Bullish' if is_long else 'Bearish'} Fair Value Gap (3-bar imbalance)"))
    if not zones:
        if is_long and sr.get("nearest_support") and sr["nearest_support"]["price"] <= cur:
            ns = sr["nearest_support"]
            p = ns["price"]
            zones.append((p - 0.25 * atr, min(cur, p + 0.20 * atr), "SR", f"{tf_lbl} Support Cluster ({ns['source']}, {ns.get('touches', 1)} touches)"))
        elif not is_long and sr.get("nearest_resistance") and sr["nearest_resistance"]["price"] >= cur:
            nr = sr["nearest_resistance"]
            p = nr["price"]
            zones.append((max(cur, p - 0.20 * atr), p + 0.25 * atr, "SR", f"{tf_lbl} Resistance Cluster ({nr['source']}, {nr.get('touches', 1)} touches)"))

    if not zones:
        return {
            "opportunity_score": 0.0, "opp_breakdown": {}, "has_plan": False,
            "entry_zone": None, "entry": None, "invalidation": None,
            "targets": [], "target_details": [], "rr": 0.0, "in_zone": False, "ltf_confirmed": False,
            "order_type": None, "entry_reason": None, "invalidation_reason": None,
        }

    # Pick zone closest to current price (zones containing price have distance 0)
    zones.sort(key=lambda z: 0.0 if (z[0] <= cur <= z[1]) else min(abs(cur - z[0]), abs(cur - z[1])))
    z_low, z_high, z_src, z_desc = zones[0]
    in_zone = z_low <= cur <= z_high
    suggested_entry = cur if in_zone else (z_high if is_long else z_low)
    order_type = (
        f"In-Zone {side} (price is currently inside {z_src})"
        if in_zone
        else (f"Limit BUY on pullback to {z_src} top" if is_long else f"Limit SELL on rally into {z_src} base")
    )

    # Structural invalidation: beyond zone & beyond nearest swing extreme (buffer 0.25 ATR)
    if is_long:
        sl_node = st.get("last_swing_low")
        sl_struct = sl_node["price"] if (sl_node and sl_node["price"] <= z_high) else z_low
        anchor_lbl = f"{tf_lbl} Swing Low ({sl_node['label']})" if (sl_node and sl_struct < z_low) else f"{z_src} bottom"
        invalidation = min(z_low, sl_struct) - 0.25 * atr
        if invalidation >= suggested_entry:
            invalidation = z_low - 0.5 * atr
        inv_reason = f"0.25 ATR below {anchor_lbl}"
    else:
        sh_node = st.get("last_swing_high")
        sh_struct = sh_node["price"] if (sh_node and sh_node["price"] >= z_low) else z_high
        anchor_lbl = f"{tf_lbl} Swing High ({sh_node['label']})" if (sh_node and sh_struct > z_high) else f"{z_src} top"
        invalidation = max(z_high, sh_struct) + 0.25 * atr
        if invalidation <= suggested_entry:
            invalidation = z_high + 0.5 * atr
        inv_reason = f"0.25 ATR above {anchor_lbl}"

    risk = abs(suggested_entry - invalidation)

    # Structural targets from opposing SR levels, PDH/PDL, PWH/PWL, dealing range extreme, opposing OBs
    cand_targets: list[tuple[float, str]] = []
    if is_long:
        for r in sr.get("resistances", []):
            if r["price"] > suggested_entry + 0.3 * atr:
                cand_targets.append((r["price"], f"Resistance ({r['source']})"))
        if smc.get("dealing_range") and smc["dealing_range"]["high"] > suggested_entry + 0.3 * atr:
            cand_targets.append((smc["dealing_range"]["high"], f"{tf_lbl} Dealing Range High"))
        for o in smc["order_blocks"]:
            if o["kind"] == "bearish" and o["bottom"] > suggested_entry + 0.3 * atr:
                cand_targets.append((o["bottom"], f"{tf_lbl} Opposing Bearish Supply OB"))
        cand_targets.sort(key=lambda x: x[0])
    else:
        for s in sr.get("supports", []):
            if s["price"] < suggested_entry - 0.3 * atr:
                cand_targets.append((s["price"], f"Support ({s['source']})"))
        if smc.get("dealing_range") and smc["dealing_range"]["low"] < suggested_entry - 0.3 * atr:
            cand_targets.append((smc["dealing_range"]["low"], f"{tf_lbl} Dealing Range Low"))
        for o in smc["order_blocks"]:
            if o["kind"] == "bullish" and o["top"] < suggested_entry - 0.3 * atr:
                cand_targets.append((o["top"], f"{tf_lbl} Opposing Bullish Demand OB"))
        cand_targets.sort(key=lambda x: x[0], reverse=True)

    # Deduplicate targets spaced at least 0.4 ATR apart
    targets: list[float] = []
    target_details: list[dict] = []
    for t_price, t_src in cand_targets:
        if not targets or abs(t_price - targets[-1]) >= 0.4 * atr:
            t_round = round(t_price, 8)
            targets.append(t_round)
            target_details.append({
                "price": t_round,
                "source": t_src,
                "rr": round(abs(t_price - suggested_entry) / risk, 2) if risk > 0 else 0.0,
            })
        if len(targets) == 3:
            break

    rr = (abs(targets[0] - suggested_entry) / risk) if (targets and risk > 0) else 0.0
    rr2 = (abs(targets[1] - suggested_entry) / risk) if (len(targets) > 1 and risk > 0) else rr

    # Opportunity Score components
    # 1. Proximity (0-25): 0 ATR away => 25, 3 ATR away => 0
    dist_atr = 0.0 if in_zone else min(abs(cur - z_low), abs(cur - z_high)) / atr
    prox = max(0.0, 25.0 * (1.0 - dist_atr / 3.0))

    # 2. Setup confirmation (0-25): HTF break + LTF BOS/CHoCH + sweep
    conf = 0.0
    ltf_confirmed = False
    if st["trend"] == target_dir:
        conf += 8
    if z_src == "OB":
        conf += 5
    elif z_src == "FVG":
        conf += 3
    if ltf:
        lb = ltf["structure"].get("last_break")
        if lb and lb["direction"] == target_dir and (ltf["n_bars"] - lb["index"]) <= 12:
            conf += 9
            ltf_confirmed = True
        if any(w["kind"] == target_dir and (ltf["n_bars"] - w["index"]) <= 15 for w in ltf["smc"]["sweeps"]):
            conf += 3
    conf = min(25.0, conf)

    # 3. Risk/Reward (0-25) based on T2 (or T1 if single target)
    eff_rr = rr2 if len(targets) > 1 else rr
    if eff_rr >= 3.0:
        rr_pts = 25.0
    elif eff_rr >= 2.0:
        rr_pts = 18.0 + (eff_rr - 2.0) * 7.0
    elif eff_rr >= 1.5:
        rr_pts = 10.0 + (eff_rr - 1.5) * 16.0
    elif eff_rr >= 1.0:
        rr_pts = (eff_rr - 1.0) * 20.0
    else:
        rr_pts = 0.0

    # 4. Invalidation clarity (0-15): risk between 0.5 and 2.5 ATR is ideal
    risk_atr = risk / atr
    if 0.4 <= risk_atr <= 2.5:
        inv_pts = 15.0
    elif risk_atr <= 4.0:
        inv_pts = max(3.0, 15.0 - (risk_atr - 2.5) * 6.0)
    else:
        inv_pts = 2.0

    # 5. Remaining room to target (0-10)
    room_atr = abs(targets[0] - cur) / atr if targets else 0.0
    room_pts = min(10.0, max(0.0, room_atr * 3.0))

    opp = round(prox + conf + rr_pts + inv_pts + room_pts, 1)
    return {
        "has_plan": True,
        "opportunity_score": opp,
        "opp_breakdown": {
            "proximity": round(prox, 1), "confirmation": round(conf, 1),
            "rr": round(rr_pts, 1), "invalidation": round(inv_pts, 1), "room": round(room_pts, 1),
        },
        "zone_source": z_src,
        "entry_reason": z_desc,
        "order_type": order_type,
        "invalidation_reason": inv_reason,
        "entry_zone": [round(z_low, 8), round(z_high, 8)],
        "entry": round(suggested_entry, 8),
        "invalidation": round(invalidation, 8),
        "targets": [round(t, 8) for t in targets],
        "target_details": target_details,
        "rr": round(eff_rr, 2),
        "dist_atr": round(dist_atr, 2),
        "in_zone": in_zone,
        "ltf_confirmed": ltf_confirmed,
    }


def detect_early_watch(primary: dict) -> list[str]:
    """Early opportunity warnings BEFORE conventional breakout confirmation."""
    reasons: list[str] = []
    ind = primary["ind"]
    smc = primary["smc"]
    sr = primary["sr"]
    cur = ind["close"]
    atr = max(ind.get("atr14", cur * 0.01), 1e-8)

    for ob in smc["order_blocks"]:
        d = min(abs(cur - ob["top"]), abs(cur - ob["bottom"])) / atr
        if not (ob["bottom"] <= cur <= ob["top"]) and d <= 0.8:
            reasons.append(f"Approaching {ob['kind']} Order Block ({d:.2f} ATR)")
            break
    for eq in smc["equal_levels"]:
        if not eq["swept"] and abs(cur - eq["price"]) / atr <= 0.8:
            reasons.append(f"Approaching {eq['kind']} liquidity pool")
            break
    if ind.get("ema_spread_prev", 99) < 1.2 and ind.get("ema_spread_atr", 0) > ind.get("ema_spread_prev", 99) * 1.04:
        reasons.append("EMA squeeze starting to expand")
    if ind.get("vol_ratio", 1.0) >= 1.5 and ind.get("vol_ratio_prev", 1.0) < 1.3:
        reasons.append(f"Volume expansion ({ind['vol_ratio']:.1f}x SMA20)")
    if ind.get("atr14", 0) > ind.get("atr_prev", 1e9) * 1.06:
        reasons.append("ATR volatility expanding")
    for tag, node in (("support", sr.get("nearest_support")), ("resistance", sr.get("nearest_resistance"))):
        if node and 0 < abs(node["distance_atr"]) <= 0.6:
            reasons.append(f"Testing key {tag} ({node['source']})")
    return reasons


def classify_tier(score: float) -> str:
    th = settings.thresholds
    if score >= th.high_quality:
        return "High-Quality"
    if score >= th.strong:
        return "Strong"
    if score >= th.developing:
        return "Developing"
    if score >= th.weak:
        return "Weak"
    return "No Setup"


def evaluate_symbol(mtf: dict[str, dict], prev_state: dict | None = None) -> dict:
    """Combine all available timeframes into independent LONG/SHORT scores, signal status & early watch."""
    biases = {tf: tf_bias(d) for tf, d in mtf.items() if d}
    # Primary execution structure TF = 1h (fallback 4h, 15m); LTF trigger = 15m or 5m
    primary_tf = "1h" if mtf.get("1h") else ("4h" if mtf.get("4h") else ("15m" if mtf.get("15m") else next(iter(mtf.keys()))))
    primary = mtf[primary_tf]
    ltf = mtf.get("15m") or mtf.get("5m")

    long_tech, long_bd = _tech_score_side("LONG", primary, biases)
    short_tech, short_bd = _tech_score_side("SHORT", primary, biases)
    long_plan = _build_trade_plan("LONG", primary, ltf, primary_tf=primary_tf)
    short_plan = _build_trade_plan("SHORT", primary, ltf, primary_tf=primary_tf)

    early_reasons = detect_early_watch(primary)

    th = settings.thresholds

    def _side_status(side: str, tech: float, plan: dict) -> tuple[str, int]:
        opp = plan["opportunity_score"]
        has_valid_plan = bool(plan.get("has_plan") and plan.get("entry") and plan.get("invalidation"))
        if not has_valid_plan:
            return "NO SETUP", 0
        # Explicit technical confirmation required — high score alone CANNOT confirm
        if (
            tech >= th.developing
            and opp >= th.confirmed_min
            and plan.get("ltf_confirmed")
            and plan.get("dist_atr", 99) <= 0.8
            and plan.get("rr", 0) >= 1.5
            and len(plan.get("targets", [])) >= 1
        ):
            return f"{side} CONFIRMED", 3
        if tech >= th.developing and opp >= th.developing_min and plan.get("rr", 0) >= 1.2:
            return f"{side} DEVELOPING", 2
        if (tech >= th.weak and opp >= th.watch_min) or (tech >= th.weak and opp >= 20.0 and early_reasons):
            return f"{side} WATCH", 1
        return "NO SETUP", 0

    l_stat, l_rank = _side_status("LONG", long_tech, long_plan)
    s_stat, s_rank = _side_status("SHORT", short_tech, short_plan)

    # Check if previous active setup was invalidated by current price AND no higher-ranked setup took over
    cur = primary["ind"]["close"]
    if prev_state and prev_state.get("status") in ("LONG CONFIRMED", "LONG DEVELOPING") and prev_state.get("invalidation"):
        if cur < prev_state["invalidation"] and s_rank < 2:
            return _pack(biases, long_tech, short_tech, long_bd, short_bd, long_plan, short_plan,
                         "INVALIDATED", "LONG", l_stat, s_stat, early_reasons, primary, primary_tf)
    if prev_state and prev_state.get("status") in ("SHORT CONFIRMED", "SHORT DEVELOPING") and prev_state.get("invalidation"):
        if cur > prev_state["invalidation"] and l_rank < 2:
            return _pack(biases, long_tech, short_tech, long_bd, short_bd, long_plan, short_plan,
                         "INVALIDATED", "SHORT", l_stat, s_stat, early_reasons, primary, primary_tf)

    if l_rank > s_rank or (l_rank == s_rank and l_rank > 0 and (long_tech + long_plan["opportunity_score"]) >= (short_tech + short_plan["opportunity_score"])):
        status, active_side = l_stat, "LONG"
    elif s_rank > 0:
        status, active_side = s_stat, "SHORT"
    else:
        pick_long = (long_tech + long_plan["opportunity_score"]) >= (short_tech + short_plan["opportunity_score"])
        status, active_side = "NO SETUP", ("LONG" if pick_long else "SHORT")

    # Hard invariant: status prefix and active_side can NEVER disagree
    if status.startswith("SHORT"):
        active_side = "SHORT"
    elif status.startswith("LONG"):
        active_side = "LONG"

    return _pack(biases, long_tech, short_tech, long_bd, short_bd, long_plan, short_plan,
                 status, active_side, l_stat, s_stat, early_reasons, primary, primary_tf)


def _pack(biases, long_tech, short_tech, long_bd, short_bd, long_plan, short_plan,
          status, active_side, long_status, short_status, early_reasons, primary, primary_tf="1h") -> dict:
    active_plan = long_plan if active_side == "LONG" else short_plan
    return {
        "primary_tf": primary_tf,
        "biases": biases,
        "long_tech_score": long_tech,
        "short_tech_score": short_tech,
        "long_opp_score": long_plan["opportunity_score"],
        "short_opp_score": short_plan["opportunity_score"],
        "long_status": long_status,
        "short_status": short_status,
        "long_tier": classify_tier(long_tech),
        "short_tier": classify_tier(short_tech),
        "long_breakdown": long_bd,
        "short_breakdown": short_bd,
        "long_plan": long_plan,
        "short_plan": short_plan,
        "active_side": active_side,
        "status": status,
        "early_watch": bool(early_reasons),
        "early_reasons": early_reasons,
        "entry_zone": active_plan.get("entry_zone"),
        "entry": active_plan.get("entry"),
        "invalidation": active_plan.get("invalidation"),
        "targets": active_plan.get("targets", []),
        "target_details": active_plan.get("target_details", []),
        "order_type": active_plan.get("order_type"),
        "entry_reason": active_plan.get("entry_reason"),
        "invalidation_reason": active_plan.get("invalidation_reason"),
        "rr": active_plan.get("rr", 0.0),
        "zone": primary["smc"].get("zone", "equilibrium"),
    }
