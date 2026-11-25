"""Smart Money Concepts — Order Blocks, Fair Value Gaps, Liquidity Sweeps, Equal Highs/Lows, Premium/Discount."""
import numpy as np
import pandas as pd


def detect_smc(
    df: pd.DataFrame,
    structure: dict,
    equal_tol_atr: float = 0.25,
    displacement_atr: float = 1.0,
) -> dict:
    n = len(df)
    if n < 20:
        return {
            "order_blocks": [], "fvgs": [], "sweeps": [], "equal_levels": [],
            "dealing_range": None, "zone": "equilibrium",
        }

    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    times = df["open_time"].to_numpy()
    atrs = df["atr14"].to_numpy() if "atr14" in df.columns else np.full(n, float(np.mean(highs - lows) or 1e-8))
    cur_price = float(closes[-1])

    # ---------- 1. Fair Value Gaps (3-candle imbalance) ----------
    fvgs: list[dict] = []
    for i in range(2, n - 1):  # candle i-1 is the displacement middle candle, confirmed once i closes
        t_mid = int(times[i - 1])
        # Bullish FVG: candle[i-2].high < candle[i].low
        if highs[i - 2] < lows[i]:
            bottom, top = float(highs[i - 2]), float(lows[i])
            mitigated, invalidated = False, False
            mitigated_time = None
            for k in range(i + 1, n):
                if lows[k] <= top:
                    mitigated = True
                    mitigated_time = mit_t = int(times[k])
                if closes[k] < bottom:
                    invalidated = True
                    break
            fvgs.append({
                "kind": "bullish", "time": t_mid, "end_time": mitigated_time or int(times[-1]),
                "top": top, "bottom": bottom, "mid": (top + bottom) / 2,
                "mitigated": mitigated, "invalidated": invalidated,
            })
        # Bearish FVG: candle[i-2].low > candle[i].high
        elif lows[i - 2] > highs[i]:
            bottom, top = float(highs[i]), float(lows[i - 2])
            mitigated, invalidated = False, False
            mitigated_time = None
            for k in range(i + 1, n):
                if highs[k] >= bottom:
                    mitigated = True
                    mitigated_time = int(times[k])
                if closes[k] > top:
                    invalidated = True
                    break
            fvgs.append({
                "kind": "bearish", "time": t_mid, "end_time": mitigated_time or int(times[-1]),
                "top": top, "bottom": bottom, "mid": (top + bottom) / 2,
                "mitigated": mitigated, "invalidated": invalidated,
            })

    # ---------- 2. Order Blocks (last opposite candle before displacement + BOS/CHoCH) ----------
    order_blocks: list[dict] = []
    seen_ob_idx: set[int] = set()
    for br in structure.get("breaks", []):
        b_idx = br["index"]
        direction = br["direction"]
        # Check displacement around the break (max body in [b_idx-3 .. b_idx] >= displacement_atr * ATR)
        win_start = max(1, b_idx - 4)
        bodies = np.abs(closes[win_start:b_idx + 1] - opens[win_start:b_idx + 1])
        ref_atr = float(atrs[b_idx]) if atrs[b_idx] > 0 else 1e-8
        if len(bodies) == 0 or float(np.max(bodies)) < displacement_atr * ref_atr * 0.6:
            continue
        # Search back up to 12 candles for last opposing candle
        ob_idx = None
        for j in range(b_idx - 1, max(0, b_idx - 13), -1):
            if direction == "bullish" and closes[j] < opens[j]:
                ob_idx = j
                break
            if direction == "bearish" and closes[j] > opens[j]:
                ob_idx = j
                break
        if ob_idx is None or ob_idx in seen_ob_idx:
            continue
        seen_ob_idx.add(ob_idx)
        top = float(highs[ob_idx])
        bottom = float(lows[ob_idx])
        mitigated, invalidated = False, False
        end_t = int(times[-1])
        for k in range(b_idx + 1, n):
            if direction == "bullish":
                if lows[k] <= top:
                    mitigated = True
                if closes[k] < bottom:
                    invalidated = True
                    end_t = int(times[k])
                    break
            else:
                if highs[k] >= bottom:
                    mitigated = True
                if closes[k] > top:
                    invalidated = True
                    end_t = int(times[k])
                    break
        order_blocks.append({
            "kind": direction, "index": ob_idx, "time": int(times[ob_idx]), "end_time": end_t,
            "top": top, "bottom": bottom, "mid": (top + bottom) / 2,
            "break_kind": br["kind"], "mitigated": mitigated, "invalidated": invalidated,
        })

    # ---------- 3. Equal Highs / Equal Lows ----------
    swings = structure.get("swings", [])
    equal_levels: list[dict] = []
    sh_list = [s for s in swings if s["kind"] == "high"]
    sl_list = [s for s in swings if s["kind"] == "low"]
    last_atr = float(atrs[-1]) if atrs[-1] > 0 else cur_price * 0.005

    def _find_equals(items: list[dict], kind_name: str):
        for a_i in range(len(items)):
            for b_i in range(a_i + 1, len(items)):
                a, b = items[a_i], items[b_i]
                if abs(a["price"] - b["price"]) <= equal_tol_atr * last_atr:
                    lvl = (a["price"] + b["price"]) / 2
                    # Check not already swept by a close beyond
                    b_idx = b["index"]
                    swept = any(
                        (closes[k] > lvl if kind_name == "EQH" else closes[k] < lvl)
                        for k in range(b_idx + 1, n)
                    )
                    equal_levels.append({
                        "kind": kind_name, "price": float(lvl),
                        "t1": a["time"], "t2": b["time"], "swept": swept,
                    })

    _find_equals(sh_list[-12:], "EQH")
    _find_equals(sl_list[-12:], "EQL")

    # ---------- 4. Liquidity Sweeps (wick beyond swing, close back inside) ----------
    sweeps: list[dict] = []
    for s in swings:
        s_idx = s["index"] + 3  # earliest bar after confirmation
        s_price = s["price"]
        for k in range(max(s_idx, 1), n):
            if s["kind"] == "high":
                if highs[k] > s_price and closes[k] < s_price:
                    sweeps.append({
                        "kind": "bearish",  # swept buy-side liquidity -> bearish implication
                        "time": int(times[k]), "index": k,
                        "level": float(s_price), "wick": float(highs[k]),
                    })
                    break
                if closes[k] > s_price:
                    break  # broken cleanly, not a sweep
            else:
                if lows[k] < s_price and closes[k] > s_price:
                    sweeps.append({
                        "kind": "bullish",  # swept sell-side liquidity -> bullish implication
                        "time": int(times[k]), "index": k,
                        "level": float(s_price), "wick": float(lows[k]),
                    })
                    break
                if closes[k] < s_price:
                    break

    # ---------- 5. Dealing range & Premium / Discount ----------
    dealing_range = None
    zone = "equilibrium"
    recent = df.tail(min(100, n))
    r_high = float(recent["high"].max())
    r_low = float(recent["low"].min())
    if structure.get("last_swing_high") and structure.get("last_swing_low"):
        sh_p = structure["last_swing_high"]["price"]
        sl_p = structure["last_swing_low"]["price"]
        if sh_p > sl_p:
            r_high, r_low = float(sh_p), float(sl_p)
    if r_high > r_low:
        eq = (r_high + r_low) / 2
        pos = (cur_price - r_low) / (r_high - r_low)
        zone = "discount" if pos < 0.45 else ("premium" if pos > 0.55 else "equilibrium")
        dealing_range = {"high": r_high, "low": r_low, "eq": eq, "position": round(pos, 4)}

    active_obs = [ob for ob in order_blocks if not ob["invalidated"]][-12:]
    active_fvgs = [g for g in fvgs if not g["invalidated"] and not g["mitigated"]][-12:]
    return {
        "order_blocks": active_obs,
        "all_order_blocks": order_blocks[-20:],
        "fvgs": active_fvgs,
        "all_fvgs": fvgs[-25:],
        "sweeps": sweeps[-15:],
        "equal_levels": equal_levels[-10:],
        "dealing_range": dealing_range,
        "zone": zone,
    }
