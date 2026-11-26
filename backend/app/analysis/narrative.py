"""Plain-English Technical Narrative Generator.

Translates 100% real mathematical outputs (structure swings, BOS/CHoCH, Order Blocks, FVGs,
Liquidity Sweeps, Equal Highs/Lows, Dealing Range, EMAs, RSI/MACD/Volume, S&R, and MTF scores)
into a clear, step-by-step plain-English report for traders — zero fabricated predictions.
"""


def _fp(val: float | None) -> str:
    if val is None:
        return "—"
    a = abs(val)
    if a >= 1000:
        return f"{val:,.2f}"
    if a >= 1:
        return f"{val:.4f}"
    if a >= 0.01:
        return f"{val:.5f}"
    return f"{val:.4g}"


def _build_next_move_map(
    symbol: str,
    tf_up: str,
    price: float,
    ind: dict,
    st: dict,
    smc: dict,
    sr: dict,
    summary: dict | None,
    sentiment: dict | None,
) -> dict:
    """Compute the rule-based 'What's Likely Next' Scenario Map:
    1. Confluence Weight Split (Long vs Short)
    2. #1 Primary Liquidity / Imbalance Magnet
    3. Scenario A (Bullish Path: Hold/Trigger -> Targets)
    4. Scenario B (Bearish Path: Reject/Breakdown -> Targets)
    5. Futures Positioning & Squeeze Read (Funding, OI, Retail vs Top-Trader L/S)
    """
    atr = max(float(ind.get("atr14") or (price * 0.01) or 1e-8), 1e-8)
    l_tech = float((summary or {}).get("long_tech_score") or 0.0)
    s_tech = float((summary or {}).get("short_tech_score") or 0.0)
    l_opp = float((summary or {}).get("long_opp_score") or 0.0)
    s_opp = float((summary or {}).get("short_opp_score") or 0.0)

    l_raw = max(1.0, l_tech * 0.65 + l_opp * 0.35)
    s_raw = max(1.0, s_tech * 0.65 + s_opp * 0.35)
    bull_pct = round(l_raw / (l_raw + s_raw) * 100.0, 1)
    bear_pct = round(100.0 - bull_pct, 1)
    if bull_pct >= 56.0:
        favored = "BULLISH"
        lean_label = f"Bullish Continuation / Rebound Favored ({bull_pct}% vs {bear_pct}% confluence weight)"
    elif bear_pct >= 56.0:
        favored = "BEARISH"
        lean_label = f"Bearish Continuation / Rejection Favored ({bear_pct}% vs {bull_pct}% confluence weight)"
    else:
        favored = "BALANCED"
        lean_label = f"Balanced / Two-Way Rotational Chop ({bull_pct}% Bull vs {bear_pct}% Bear confluence weight)"

    # Gather real above-price & below-price magnets (EQH/EQL, unfilled FVG, unmitigated OB, PDH/PDL, PWH/PWL, Swings)
    up_magnets: list[dict] = []
    dn_magnets: list[dict] = []

    def _add_mag(lvl: float | None, label: str, weight_bonus: float):
        if lvl is None or lvl <= 0 or price <= 0:
            return
        dist_pct = (lvl - price) / price * 100.0
        dist_atr = abs(lvl - price) / atr
        if dist_atr < 0.08 or dist_atr > 12.0:
            return
        # Closer unfinished liquidity (0.3 - 3.5 ATR) has strongest pull
        prox_score = max(0.0, 10.0 - dist_atr * 2.0) + weight_bonus
        entry = {
            "price": float(lvl),
            "label": label,
            "dist_pct": round(dist_pct, 2),
            "dist_atr": round(dist_atr, 2),
            "score": round(prox_score, 2),
        }
        if lvl > price:
            up_magnets.append(entry)
        else:
            dn_magnets.append(entry)

    for eq in (smc or {}).get("equal_levels", []):
        if not eq.get("swept"):
            _add_mag(eq["price"], f"Unswept {eq['kind']} Stop Pool", 5.0)
    for g in (smc or {}).get("fvgs", []):
        _add_mag(g["mid"], f"Unfilled {g['kind'].capitalize()} FVG Magnet", 3.8)
    for o in (smc or {}).get("order_blocks", []):
        edge = o["bottom"] if o["kind"] == "bearish" else o["top"]
        _add_mag(edge, f"{'Fresh' if not o.get('mitigated') else 'Active'} {o['kind'].capitalize()} Order Block", 3.5)
    if sr:
        _add_mag(sr.get("pdh"), "Previous Day High (PDH Liquidity)", 4.0)
        _add_mag(sr.get("pdl"), "Previous Day Low (PDL Liquidity)", 4.0)
        _add_mag(sr.get("pwh"), "Previous Week High (PWH Liquidity)", 3.5)
        _add_mag(sr.get("pwl"), "Previous Week Low (PWL Liquidity)", 3.5)
        for sup in sr.get("supports", [])[:3]:
            _add_mag(sup["price"], f"Swing Support ({sup['source']})", 2.5)
        for res in sr.get("resistances", [])[:3]:
            _add_mag(res["price"], f"Swing Resistance ({res['source']})", 2.5)
    if st:
        if st.get("last_swing_high"):
            _add_mag(st["last_swing_high"]["price"], f"Protected Swing High ({st['last_swing_high']['label']})", 3.2)
        if st.get("last_swing_low"):
            _add_mag(st["last_swing_low"]["price"], f"Protected Swing Low ({st['last_swing_low']['label']})", 3.2)

    up_magnets.sort(key=lambda x: x["score"], reverse=True)
    dn_magnets.sort(key=lambda x: x["score"], reverse=True)

    # Pick #1 Primary Magnet biased by confluence direction
    best_up = up_magnets[0] if up_magnets else None
    best_dn = dn_magnets[0] if dn_magnets else None
    if best_up and best_dn:
        up_eff = best_up["score"] + (2.5 if favored == "BULLISH" else 0.0)
        dn_eff = best_dn["score"] + (2.5 if favored == "BEARISH" else 0.0)
        primary_magnet = best_up if up_eff >= dn_eff else best_dn
    else:
        primary_magnet = best_up or best_dn

    # Scenario A: Bullish Path
    bull_obs = [o for o in (smc or {}).get("order_blocks", []) if o["kind"] == "bullish"]
    last_sl = (st or {}).get("last_swing_low")
    last_sh = (st or {}).get("last_swing_high")
    ns = (sr or {}).get("nearest_support")
    nr = (sr or {}).get("nearest_resistance")

    bull_hold = (
        f"{_fp(bull_obs[-1]['bottom'])}–{_fp(bull_obs[-1]['top'])} (Bullish OB)"
        if bull_obs
        else (f"{_fp(last_sl['price'])} ({last_sl['label']} swing low)" if last_sl else (f"{_fp(ns['price'])} support" if ns else f"{_fp(price - atr)}"))
    )
    bull_trigger = (
        f"a {tf_up} candle close above {_fp(last_sh['price'])} ({last_sh['label']})"
        if last_sh and last_sh["price"] > price
        else (f"a clean reclaim & hold above {_fp(nr['price'])}" if nr else "sustained 15m higher-lows + bullish BOS")
    )
    up_sorted_price = sorted({round(m["price"], 8): m for m in up_magnets}.values(), key=lambda m: m["price"])
    bull_targets_str = (
        " → ".join(f"{_fp(m['price'])} ({m['label']}, +{m['dist_pct']}%)" for m in up_sorted_price[:2])
        if up_sorted_price
        else f"{_fp(price + 1.5 * atr)} → {_fp(price + 3.0 * atr)} (ATR expansion projection)"
    )
    bull_scenario = (
        f"If buyers defend {bull_hold} and print {bull_trigger}, expect upside continuation pulling toward {bull_targets_str}."
    )

    # Scenario B: Bearish Path
    bear_obs = [o for o in (smc or {}).get("order_blocks", []) if o["kind"] == "bearish"]
    bear_cap = (
        f"{_fp(bear_obs[-1]['bottom'])}–{_fp(bear_obs[-1]['top'])} (Bearish Supply OB)"
        if bear_obs
        else (f"{_fp(last_sh['price'])} ({last_sh['label']} swing high)" if last_sh else (f"{_fp(nr['price'])} resistance" if nr else f"{_fp(price + atr)}"))
    )
    bear_trigger = (
        f"a {tf_up} candle close below {_fp(last_sl['price'])} ({last_sl['label']})"
        if last_sl and last_sl["price"] < price
        else (f"rejection below {bear_cap} + 15m bearish CHoCH" if bear_obs else f"a breakdown below {_fp(ns['price'])}" if ns else "15m lower-high + bearish BOS")
    )
    dn_sorted_price = sorted({round(m["price"], 8): m for m in dn_magnets}.values(), key=lambda m: m["price"], reverse=True)
    bear_targets_str = (
        " → ".join(f"{_fp(m['price'])} ({m['label']}, {m['dist_pct']}%)" for m in dn_sorted_price[:2])
        if dn_sorted_price
        else f"{_fp(price - 1.5 * atr)} → {_fp(price - 3.0 * atr)} (ATR breakdown projection)"
    )
    bear_scenario = (
        f"If price stays capped beneath {bear_cap} or prints {bear_trigger}, expect downside draw toward {bear_targets_str}."
    )

    # Futures Positioning & Squeeze Read (Funding + OI + Retail vs Top Trader L/S)
    sent_summary = None
    sent_metrics: dict = {}
    if sentiment:
        fr = sentiment.get("funding_rate_pct")
        oi = sentiment.get("open_interest")
        rl = sentiment.get("retail_long_pct")
        tl = sentiment.get("top_trader_long_pct")
        basis = sentiment.get("basis_pct")

        if fr is not None:
            sent_metrics["funding_rate_pct"] = fr
        if basis is not None:
            sent_metrics["basis_pct"] = basis
        if oi:
            sent_metrics["open_interest_usd"] = round(float(oi) * price, 0)
        if rl is not None:
            sent_metrics["retail_long_pct"] = rl
            sent_metrics["retail_short_pct"] = round(100.0 - rl, 1)
        if tl is not None:
            sent_metrics["top_trader_long_pct"] = tl
            sent_metrics["top_trader_short_pct"] = round(100.0 - tl, 1)

        clues: list[str] = []
        if fr is not None:
            if fr >= 0.03:
                clues.append(f"Elevated positive funding (+{fr:.4f}%): perpetual longs are paying a premium — crowded late longs create downside stop-hunt / long-squeeze risk if support cracks.")
            elif fr <= -0.015:
                clues.append(f"Negative funding ({fr:.4f}%): shorts are aggressive and paying longs — high fuel for an explosive short-squeeze if price breaks overhead resistance.")
            else:
                clues.append(f"Neutral funding ({'+' if fr >= 0 else ''}{fr:.4f}%): perpetual leverage is balanced without extreme crowding.")
        if rl is not None:
            if rl >= 66.0:
                clues.append(f"Retail Crowd Skew: {rl:.1f}% of accounts are net LONG — contrarian warning to wait for a liquidity sweep rather than chasing green candles.")
            elif rl <= 38.0:
                clues.append(f"Retail Crowd Skew: {100.0 - rl:.1f}% of accounts are net SHORT — contrarian fuel supporting upside squeezes.")
        if rl is not None and tl is not None and abs(tl - rl) >= 7.0:
            whales_side = "LONG" if tl > rl else "SHORT"
            clues.append(
                f"Smart-Money Divergence: Top Binance traders are {tl:.1f}% Long vs Retail at {rl:.1f}% Long (whales leaning {whales_side} relative to the crowd)."
            )
        if clues:
            sent_summary = " ".join(clues)

    return {
        "favored": favored,
        "bull_weight_pct": bull_pct,
        "bear_weight_pct": bear_pct,
        "lean_label": lean_label,
        "primary_magnet": (
            f"{_fp(primary_magnet['price'])} — {primary_magnet['label']} "
            f"({'+' if primary_magnet['dist_pct'] >= 0 else ''}{primary_magnet['dist_pct']}% / {primary_magnet['dist_atr']} ATR away)"
            if primary_magnet
            else "No clean nearby liquidity magnet within 12 ATR"
        ),
        "bull_scenario": bull_scenario,
        "bear_scenario": bear_scenario,
        "positioning_read": sent_summary,
        "positioning_metrics": sent_metrics,
    }


def build_plain_english_report(
    symbol: str,
    tf: str,
    tf_detail: dict | None,
    summary: dict | None,
    sentiment: dict | None = None,
) -> dict | None:
    if not tf_detail and not summary:
        return None

    tf_up = tf.upper()
    ind = (tf_detail or {}).get("ind", {})
    div = (tf_detail or {}).get("div", {})
    st = (tf_detail or {}).get("structure", {})
    smc = (tf_detail or {}).get("smc", {})
    sr = (tf_detail or {}).get("sr", {})

    price = float((summary or {}).get("price") or ind.get("close") or 0.0)
    chg = float((summary or {}).get("change_24h") or 0.0)
    status = (summary or {}).get("status", "NO SETUP")
    side = (summary or {}).get("active_side", "LONG")
    l_tech = float((summary or {}).get("long_tech_score") or 0.0)
    s_tech = float((summary or {}).get("short_tech_score") or 0.0)
    l_opp = float((summary or {}).get("long_opp_score") or 0.0)
    s_opp = float((summary or {}).get("short_opp_score") or 0.0)
    biases: dict[str, str] = (summary or {}).get("biases") or {}
    early_reasons: list[str] = (summary or {}).get("early_reasons") or []

    # ---------- Headline & Verdict Tone ----------
    if "CONFIRMED" in status:
        tone = "bullish" if status.startswith("LONG") else "bearish"
        headline = (
            f"{symbol} has an active {status} setup at {_fp(price)} — higher-timeframe structure, "
            f"entry-zone proximity, lower-timeframe break confirmation, and Risk/Reward all align."
        )
    elif "DEVELOPING" in status:
        tone = "bullish" if status.startswith("LONG") else "bearish"
        headline = (
            f"{symbol} is in a {status} phase at {_fp(price)} — directional bias and chart structure are constructive, "
            f"but you should wait for price to enter the zone and print a lower-timeframe confirmation break before entering."
        )
    elif "WATCH" in status or early_reasons:
        tone = "warning"
        headline = (
            f"{symbol} is on {status if 'WATCH' in status else 'EARLY WATCH'} at {_fp(price)} — "
            f"early pre-breakout conditions are forming, so monitor key levels closely without jumping in prematurely."
        )
    elif status == "INVALIDATED":
        tone = "neutral"
        headline = (
            f"The previous setup on {symbol} was INVALIDATED after price crossed its structural stop level — "
            f"stand aside until fresh structure forms."
        )
    else:
        tone = "neutral"
        dominant = "Long" if l_tech >= s_tech else "Short"
        headline = (
            f"{symbol} currently shows NO ACTIONABLE SETUP at {_fp(price)} "
            f"(Long Technical: {l_tech}/100 vs Short Technical: {s_tech}/100; slight {dominant} lean, but confirmation rules are not met)."
        )

    sections: list[dict] = []

    # ---------- 1. Executive Summary ----------
    exec_bullets: list[str] = [
        f"Live Price: {_fp(price)} ({'+' if chg >= 0 else ''}{chg:.2f}% over the last 24 hours).",
        f"Long Chart Quality: {l_tech}/100 ({(summary or {}).get('long_tier', '—')}) with Long Trade Opportunity Score {l_opp}/100.",
        f"Short Chart Quality: {s_tech}/100 ({(summary or {}).get('short_tier', '—')}) with Short Trade Opportunity Score {s_opp}/100.",
    ]
    if early_reasons:
        exec_bullets.append("Early-Warning Radar Triggers: " + "; ".join(early_reasons) + ".")
    if "CONFIRMED" in status:
        action_paragraph = (
            f"Plain-English Takeaway: {symbol} meets every mathematical requirement for a {side} setup right now. "
            f"Price is interacting with a real structural zone, lower-timeframe candle-close structure has shifted in favor of {side.lower()}s, "
            f"and there is clean room to structural targets relative to the invalidation level."
        )
    elif "DEVELOPING" in status:
        action_paragraph = (
            f"Plain-English Takeaway: {symbol} has a real {side} thesis building, but a high score alone is NOT an entry trigger. "
            f"Patience pays here — let price pull into the highlighted Entry Zone and confirm with a closed-candle Break of Structure (BOS) or Change of Character (CHoCH) on the 15m/5m chart."
        )
    elif early_reasons or "WATCH" in status:
        action_paragraph = (
            f"Plain-English Takeaway: {symbol} is approaching an important decision area before a conventional breakout happens. "
            f"Keep it on your Watchlist and watch how candles react around the nearest Order Block or liquidity pool."
        )
    else:
        action_paragraph = (
            f"Plain-English Takeaway: Right now {symbol} is either mid-range, choppy across timeframes, or too far from a clean invalidation level "
            f"to offer a favorable Risk-to-Reward trade. Capital preservation means waiting for price to come to a real structural level."
        )
    sections.append({
        "title": "1. Executive Summary & Plain-English Verdict",
        "summary": action_paragraph,
        "bullets": exec_bullets,
    })

    # ---------- 2. Multi-Timeframe Alignment ----------
    htf_keys = [k for k in ("1w", "1d", "4h") if k in biases]
    ltf_keys = [k for k in ("1h", "15m", "5m") if k in biases]
    bull_tfs = [k.upper() for k, v in biases.items() if v == "BULLISH"]
    bear_tfs = [k.upper() for k, v in biases.items() if v == "BEARISH"]
    neut_tfs = [k.upper() for k, v in biases.items() if v == "NEUTRAL"]

    htf_bull = sum(1 for k in htf_keys if biases.get(k) == "BULLISH")
    htf_bear = sum(1 for k in htf_keys if biases.get(k) == "BEARISH")
    ltf_bull = sum(1 for k in ltf_keys if biases.get(k) == "BULLISH")
    ltf_bear = sum(1 for k in ltf_keys if biases.get(k) == "BEARISH")

    if len(bull_tfs) >= 5:
        mtf_story = "Strong full-spectrum bullish alignment: nearly every timeframe from Weekly/Daily down to intraday is pointing up, meaning pullbacks into demand carry trend tailwind."
    elif len(bear_tfs) >= 5:
        mtf_story = "Strong full-spectrum bearish alignment: nearly every timeframe from Weekly/Daily down to intraday is pointing down, meaning rallies into supply favor continuation shorts."
    elif htf_bull >= 2 and ltf_bear >= 1:
        mtf_story = "Higher timeframes (Weekly/Daily/4H) are bullish while lower timeframes are pulling back — this is the classic 'counter-trend pullback inside a bigger uptrend' where Smart Money looks for discount bullish Order Blocks."
    elif htf_bear >= 2 and ltf_bull >= 1:
        mtf_story = "Higher timeframes (Weekly/Daily/4H) are bearish while lower timeframes are bouncing — this represents a relief bounce inside a macro downtrend, watching overhead bearish Order Blocks for rejection."
    else:
        mtf_story = "Timeframes are currently mixed/transitional, which lowers conviction until intraday structure aligns with the 4H and Daily trend."

    sections.append({
        "title": "2. Multi-Timeframe Alignment (1W → 5M)",
        "summary": mtf_story,
        "bullets": [
            f"Bullish Timeframes ({len(bull_tfs)}): {', '.join(bull_tfs) if bull_tfs else 'None'}",
            f"Bearish Timeframes ({len(bear_tfs)}): {', '.join(bear_tfs) if bear_tfs else 'None'}",
            f"Neutral / Ranging Timeframes ({len(neut_tfs)}): {', '.join(neut_tfs) if neut_tfs else 'None'}",
        ],
    })

    # ---------- 3. Market Structure (Swings, BOS & CHoCH) on Selected Timeframe ----------
    if st:
        trend = st.get("trend", "neutral")
        swings = st.get("swings", [])
        last_sh = st.get("last_swing_high")
        last_sl = st.get("last_swing_low")
        last_br = st.get("last_break")
        recent_labels = " → ".join(s["label"] for s in swings[-6:]) if swings else "Insufficient swings"

        sl_part = f" at {_fp(last_sl['price'])}" if last_sl else ""
        sh_part = f" at {_fp(last_sh['price'])}" if last_sh else ""
        if trend == "bullish":
            st_desc = (
                f"On the {tf_up} chart, confirmed candle-close market structure is BULLISH (printing higher highs/higher lows). "
                f"Buyers remain in structural control as long as candle closes hold above the most recent protected swing low{sl_part}."
            )
        elif trend == "bearish":
            st_desc = (
                f"On the {tf_up} chart, confirmed candle-close market structure is BEARISH (printing lower highs/lower lows). "
                f"Sellers remain in structural control unless a candle closes decisively above the most recent swing high{sh_part}."
            )
        else:
            st_desc = f"On the {tf_up} chart, market structure is currently NEUTRAL / ranging without a confirmed directional break."

        st_bullets = [f"Recent 3-bar confirmed swing sequence ({tf_up}): {recent_labels}."]
        if last_br:
            meaning = (
                "trend continuation (Break of Structure)"
                if last_br["kind"] == "BOS"
                else "early trend reversal signal (Change of Character)"
            )
            st_bullets.append(
                f"Most recent structural break: {last_br['direction'].upper()} {last_br['kind']} via candle close beyond {_fp(last_br['level'])} — signaling {meaning}."
            )
        if last_sh:
            st_bullets.append(f"Protected Swing High ({last_sh['label']}): {_fp(last_sh['price'])} (a candle close above this confirms bullish continuation/reversal).")
        if last_sl:
            st_bullets.append(f"Protected Swing Low ({last_sl['label']}): {_fp(last_sl['price'])} (a candle close below this confirms bearish continuation/breakdown).")

        sections.append({
            "title": f"3. {tf_up} Market Structure (BOS, CHoCH & Swing Sequence)",
            "summary": st_desc,
            "bullets": st_bullets,
        })

    # ---------- 4. Smart Money Concepts (OB, FVG, Sweeps, EQH/EQL, Premium/Discount) ----------
    if smc:
        obs = smc.get("order_blocks", [])
        fvgs = smc.get("fvgs", [])
        sweeps = smc.get("sweeps", [])
        eqs = [e for e in smc.get("equal_levels", []) if not e.get("swept")]
        dr = smc.get("dealing_range")
        zone = smc.get("zone", "equilibrium")

        bull_obs = [o for o in obs if o["kind"] == "bullish"]
        bear_obs = [o for o in obs if o["kind"] == "bearish"]
        bull_fvgs = [g for g in fvgs if g["kind"] == "bullish"]
        bear_fvgs = [g for g in fvgs if g["kind"] == "bearish"]

        if zone == "discount":
            z_txt = "DISCOUNT zone (lower 45% of the active dealing range) — institutionally favorable territory to look for LONG entries rather than chasing shorts at the lows."
        elif zone == "premium":
            z_txt = "PREMIUM zone (upper 45% of the active dealing range) — institutionally favorable territory to look for SHORT rejections or take profit on longs rather than buying the highs."
        else:
            z_txt = "EQUILIBRIUM (middle 50% fair-value area of the dealing range) — price is near the midpoint where patience is needed until an extreme is tested."

        smc_bullets = []
        if dr:
            smc_bullets.append(
                f"Dealing Range: Low {_fp(dr['low'])} ↔ Equilibrium (50%) {_fp(dr['eq'])} ↔ High {_fp(dr['high'])} "
                f"(current price sits at {dr['position'] * 100:.1f}% of the range)."
            )
        if bull_obs:
            b = bull_obs[-1]
            smc_bullets.append(
                f"Active Bullish Order Blocks ({len(bull_obs)}): Nearest demand OB spans {_fp(b['bottom'])} – {_fp(b['top'])} "
                f"({'already tapped/mitigated once' if b.get('mitigated') else 'fresh & unmitigated'} — formed before a {b.get('break_kind', 'BOS')} impulse)."
            )
        else:
            smc_bullets.append("Active Bullish Order Blocks: None uninvalidated on this timeframe.")

        if bear_obs:
            b = bear_obs[-1]
            smc_bullets.append(
                f"Active Bearish Order Blocks ({len(bear_obs)}): Nearest supply OB spans {_fp(b['bottom'])} – {_fp(b['top'])} "
                f"({'already tapped/mitigated once' if b.get('mitigated') else 'fresh & unmitigated'} — formed before a {b.get('break_kind', 'BOS')} drop)."
            )
        else:
            smc_bullets.append("Active Bearish Order Blocks: None uninvalidated on this timeframe.")

        if bull_fvgs or bear_fvgs:
            parts = []
            if bull_fvgs:
                g = bull_fvgs[-1]
                parts.append(f"{len(bull_fvgs)} Bullish FVG(s) (nearest magnet {_fp(g['bottom'])} – {_fp(g['top'])})")
            if bear_fvgs:
                g = bear_fvgs[-1]
                parts.append(f"{len(bear_fvgs)} Bearish FVG(s) (nearest magnet {_fp(g['bottom'])} – {_fp(g['top'])})")
            smc_bullets.append("Unfilled Fair Value Gaps (3-candle imbalances): " + "; ".join(parts) + ".")
        else:
            smc_bullets.append("Unfilled Fair Value Gaps: All recent imbalances on this timeframe have been filled.")

        if sweeps:
            sw = sweeps[-1]
            imp = "bullish reversal clue (stopped out sell-side stops below a swing low and closed back above)" if sw["kind"] == "bullish" else "bearish rejection clue (grabbed buy-side stops above a swing high and closed back below)"
            smc_bullets.append(f"Recent Liquidity Sweep: Wick to {_fp(sw['wick'])} swept level {_fp(sw['level'])} — {imp}.")
        if eqs:
            e = eqs[-1]
            pool_desc = "resting buy-stops above Equal Highs" if e["kind"] == "EQH" else "resting sell-stops below Equal Lows"
            smc_bullets.append(f"Un-swept Liquidity Pool ({e['kind']}): {_fp(e['price'])} ({pool_desc} often act as a price magnet).")

        sections.append({
            "title": f"4. {tf_up} Smart Money Concepts (Order Blocks, FVG & Liquidity)",
            "summary": f"Price is currently trading in the {z_txt}",
            "bullets": smc_bullets,
        })

    # ---------- 5. EMAs, Momentum & Support / Resistance ----------
    if ind:
        stack = str(ind.get("ema_stack", "mixed"))
        rsi_v = float(ind.get("rsi14", 50.0))
        macd_h = float(ind.get("macd_hist", 0.0))
        vr = float(ind.get("vol_ratio", 1.0))
        spread_atr = float(ind.get("ema_spread_atr", 99.0))
        atr_v = float(ind.get("atr14", 0.0))

        if stack == "bullish":
            ema_txt = "EMAs are stacked in clean bullish order (EMA 20 > EMA 50 > EMA 100 > EMA 200), confirming sustained trend support."
        elif stack == "bearish":
            ema_txt = "EMAs are stacked in clean bearish order (EMA 20 < EMA 50 < EMA 100 < EMA 200), confirming sustained downward trend pressure."
        else:
            ema_txt = "EMAs (20/50/100/200) are interwoven/mixed, indicating consolidation or a trend transition."
        if spread_atr < 1.2:
            ema_txt += f" Note: All 4 EMAs are tightly compressed within {spread_atr:.2f} ATR (an EMA Squeeze), which frequently precedes a sharp volatility expansion."

        rsi_state = "overbought (>70, watch for exhaustion)" if rsi_v >= 70 else ("oversold (<30, watch for relief bounce)" if rsi_v <= 30 else ("bullish momentum (>55)" if rsi_v >= 55 else ("bearish momentum (<45)" if rsi_v <= 45 else "neutral mid-band (45–55)")))
        div_notes = []
        if div.get("bullish_rsi"):
            div_notes.append("Bullish RSI Divergence (lower price low vs higher RSI low)")
        if div.get("bearish_rsi"):
            div_notes.append("Bearish RSI Divergence (higher price high vs lower RSI high)")
        if div.get("bullish_macd"):
            div_notes.append("Bullish MACD Histogram Divergence")
        if div.get("bearish_macd"):
            div_notes.append("Bearish MACD Histogram Divergence")

        ind_bullets = [
            f"EMA Ribbon: EMA20 {_fp(ind.get('ema20'))} | EMA50 {_fp(ind.get('ema50'))} | EMA100 {_fp(ind.get('ema100'))} | EMA200 {_fp(ind.get('ema200'))}.",
            f"RSI (14): {rsi_v:.1f} — {rsi_state}.",
            f"MACD (12,26,9) Histogram: {macd_h:.4g} ({'positive bullish impulse' if macd_h > 0 else 'negative bearish impulse'}).",
            f"Volume Activity: {vr:.2f}x the 20-candle average ({'strong institutional participation' if vr >= 1.5 else ('above average' if vr >= 1.1 else 'light / routine volume')}) | ATR(14) Volatility: {_fp(atr_v)} per {tf_up} bar.",
        ]
        if div_notes:
            ind_bullets.append("Momentum Divergence Detected: " + ", ".join(div_notes) + ".")

        ns = sr.get("nearest_support") if sr else None
        nr = sr.get("nearest_resistance") if sr else None
        if ns:
            ind_bullets.append(
                f"Nearest Support ({ns['source']}): {_fp(ns['price'])} ({abs(ns['distance_pct']):.2f}% / {abs(ns['distance_atr']):.2f} ATR below current price)."
            )
        if nr:
            ind_bullets.append(
                f"Nearest Resistance ({nr['source']}): {_fp(nr['price'])} (+{abs(nr['distance_pct']):.2f}% / {abs(nr['distance_atr']):.2f} ATR above current price)."
            )
        if sr and (sr.get("pdh") or sr.get("pwh")):
            ind_bullets.append(
                f"Benchmark Session Levels: Previous Day High/Low {_fp(sr.get('pdh'))} / {_fp(sr.get('pdl'))} | Previous Week High/Low {_fp(sr.get('pwh'))} / {_fp(sr.get('pwl'))}."
            )

        sections.append({
            "title": f"5. {tf_up} EMAs, Momentum, Volume & Key Support/Resistance",
            "summary": ema_txt,
            "bullets": ind_bullets,
        })

    # ---------- 6. Actionable Structural Trade Plan & Missing-Criteria Checklist ----------
    if summary:
        plan_key = "long_plan" if side == "LONG" else "short_plan"
        plan = summary.get(plan_key) or {}
        ez = summary.get("entry_zone")
        entry = summary.get("entry")
        inv = summary.get("invalidation")
        tgts = summary.get("targets") or []
        rr = summary.get("rr") or 0.0

        plan_bullets = []
        if ez and entry and inv:
            risk_pct = abs(entry - inv) / entry * 100 if entry else 0.0
            plan_bullets.append(
                f"Primary {side} Entry Zone ({plan.get('zone_source', 'Structural')}): {_fp(ez[0])} to {_fp(ez[1])} "
                f"(Suggested Execution Reference: {_fp(entry)})."
            )
            plan_bullets.append(
                f"Structural Invalidation (Hard Stop-Loss): {_fp(inv)} ({risk_pct:.2f}% risk from entry — if price crosses this level, the {side.lower()} market structure thesis is mathematically proven wrong)."
            )
            if tgts:
                t_str = " → ".join(f"TP{i + 1}: {_fp(t)}" for i, t in enumerate(tgts))
                plan_bullets.append(f"Structural Profit Targets: {t_str} (Estimated Risk/Reward: {rr}R).")
            else:
                plan_bullets.append("Profit Targets: No overhead/below structural target spaced ≥ 0.4 ATR was found yet.")

            # Checklist of what is satisfied vs still needed
            checks = []
            tech_now = l_tech if side == "LONG" else s_tech
            opp_now = l_opp if side == "LONG" else s_opp
            checks.append(f"[{'PASS' if tech_now >= 60 else 'WAIT'}] Technical Chart Score ≥ 60 (currently {tech_now}/100)")
            checks.append(f"[{'PASS' if opp_now >= 70 else 'WAIT'}] Opportunity Score ≥ 70 for Confirmed (currently {opp_now}/100)")
            checks.append(f"[{'PASS' if plan.get('dist_atr', 99) <= 0.8 else 'WAIT'}] Price within 0.8 ATR of Entry Zone (currently {plan.get('dist_atr', '—')} ATR away)")
            checks.append(f"[{'PASS' if plan.get('ltf_confirmed') else 'WAIT'}] Lower-Timeframe (15m/5m) closed-candle BOS/CHoCH confirmation")
            checks.append(f"[{'PASS' if rr >= 1.5 else 'WAIT'}] Structural Risk/Reward ≥ 1.5R (currently {rr}R)")
            plan_bullets.append("Confirmation Rule Checklist: " + " • ".join(checks))
            plan_summary = (
                f"Below is the rule-based {side} blueprint derived from actual Order Blocks, Fair Value Gaps, and swing levels (never fixed arbitrary percentages)."
            )
        else:
            plan_summary = (
                f"No clean structural {side} Entry Zone is positioned favorably relative to current price right now, so no trade levels are forced."
            )
            plan_bullets.append("Wait for a new impulse + BOS/CHoCH to leave behind a fresh unmitigated Order Block or Fair Value Gap.")

        sections.append({
            "title": f"6. Step-by-Step {side} Trade Plan & Confirmation Checklist",
            "summary": plan_summary,
            "bullets": plan_bullets,
        })

    next_move_map = _build_next_move_map(
        symbol=symbol,
        tf_up=tf_up,
        price=price,
        ind=ind,
        st=st,
        smc=smc,
        sr=sr,
        summary=summary,
        sentiment=sentiment,
    )

    return {
        "symbol": symbol,
        "tf": tf,
        "tone": tone,
        "headline": headline,
        "next_move_map": next_move_map,
        "sections": sections,
    }
