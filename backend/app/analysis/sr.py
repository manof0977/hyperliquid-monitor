"""Support & Resistance — swing clusters, PDH/PDL, PWH/PWL, supply/demand zones, distance in % and ATR."""
import pandas as pd


def _cluster_levels(prices: list[float], tol: float) -> list[dict]:
    if not prices:
        return []
    pts = sorted(prices)
    clusters: list[list[float]] = [[pts[0]]]
    for p in pts[1:]:
        if abs(p - sum(clusters[-1]) / len(clusters[-1])) <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    out = [{"price": sum(c) / len(c), "touches": len(c)} for c in clusters]
    return sorted(out, key=lambda x: x["touches"], reverse=True)


def detect_sr(
    df: pd.DataFrame,
    structure: dict,
    smc: dict,
    daily_df: pd.DataFrame | None = None,
    weekly_df: pd.DataFrame | None = None,
) -> dict:
    if df.empty:
        return {"supports": [], "resistances": [], "pdh": None, "pdl": None, "pwh": None, "pwl": None, "nearest_support": None, "nearest_resistance": None}

    cur = float(df["close"].iloc[-1])
    atr = float(df["atr14"].iloc[-1]) if "atr14" in df.columns and pd.notna(df["atr14"].iloc[-1]) else cur * 0.01
    atr = max(atr, 1e-8)

    swings = structure.get("swings", [])
    sh_prices = [s["price"] for s in swings if s["kind"] == "high"]
    sl_prices = [s["price"] for s in swings if s["kind"] == "low"]
    clusters = _cluster_levels(sh_prices + sl_prices, tol=0.5 * atr)

    def _enrich(lvl: float, source: str, touches: int = 1) -> dict:
        dist_pct = (lvl - cur) / cur * 100
        dist_atr = (lvl - cur) / atr
        return {
            "price": float(lvl), "source": source, "touches": touches,
            "distance_pct": round(dist_pct, 3), "distance_atr": round(dist_atr, 2),
        }

    pdh = pdl = pwh = pwl = None
    if daily_df is not None and len(daily_df) >= 2:
        prev_d = daily_df.iloc[-2]
        pdh, pdl = float(prev_d["high"]), float(prev_d["low"])
    if weekly_df is not None and len(weekly_df) >= 2:
        prev_w = weekly_df.iloc[-2]
        pwh, pwl = float(prev_w["high"]), float(prev_w["low"])

    supports: list[dict] = []
    resistances: list[dict] = []
    for c in clusters[:12]:
        item = _enrich(c["price"], "swing", c["touches"])
        (supports if c["price"] <= cur else resistances).append(item)

    for tag, val in (("PDH", pdh), ("PDL", pdl), ("PWH", pwh), ("PWL", pwl)):
        if val is not None:
            item = _enrich(val, tag, 2)
            (supports if val <= cur else resistances).append(item)

    # Supply / demand zones from unmitigated order blocks
    supply_zones = [ob for ob in smc.get("order_blocks", []) if ob["kind"] == "bearish"]
    demand_zones = [ob for ob in smc.get("order_blocks", []) if ob["kind"] == "bullish"]

    supports.sort(key=lambda x: abs(x["distance_pct"]))
    resistances.sort(key=lambda x: abs(x["distance_pct"]))

    return {
        "supports": supports[:8],
        "resistances": resistances[:8],
        "pdh": pdh, "pdl": pdl, "pwh": pwh, "pwl": pwl,
        "supply_zones": supply_zones[-5:],
        "demand_zones": demand_zones[-5:],
        "nearest_support": supports[0] if supports else None,
        "nearest_resistance": resistances[0] if resistances else None,
        "atr": atr,
    }
