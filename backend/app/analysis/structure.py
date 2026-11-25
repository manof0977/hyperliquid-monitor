"""Market structure — fractal swings (3L/3R), HH/HL/LH/LL, candle-close BOS & CHoCH.

Non-repainting rule: a swing at index `i` is only known once candle `i + right` has closed.
We walk forward chronologically and only activate a swing when `bar >= i + right`.
"""
from dataclasses import dataclass, asdict
import pandas as pd


@dataclass
class Swing:
    index: int
    time: int
    price: float
    kind: str      # 'high' | 'low'
    label: str     # 'HH' | 'LH' | 'HL' | 'LL' | 'SH' | 'SL'


@dataclass
class BreakEvent:
    index: int
    time: int
    kind: str      # 'BOS' | 'CHoCH'
    direction: str # 'bullish' | 'bearish'
    level: float
    swing_time: int


def detect_structure(df: pd.DataFrame, left: int = 3, right: int = 3) -> dict:
    n = len(df)
    if n < left + right + 5:
        return {"trend": "neutral", "swings": [], "breaks": [], "last_swing_high": None, "last_swing_low": None}

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    times = df["open_time"].to_numpy()

    # Pre-find raw fractals; a fractal at i becomes known at i + right
    raw_by_confirm: dict[int, list[tuple[int, str, float]]] = {}
    for i in range(left, n - right):
        is_sh = all(highs[i] > highs[i - k] for k in range(1, left + 1)) and all(
            highs[i] >= highs[i + k] for k in range(1, right + 1)
        )
        is_sl = all(lows[i] < lows[i - k] for k in range(1, left + 1)) and all(
            lows[i] <= lows[i + k] for k in range(1, right + 1)
        )
        if is_sh:
            raw_by_confirm.setdefault(i + right, []).append((i, "high", float(highs[i])))
        if is_sl:
            raw_by_confirm.setdefault(i + right, []).append((i, "low", float(lows[i])))

    swings: list[Swing] = []
    breaks: list[BreakEvent] = []
    prev_high: Swing | None = None
    prev_low: Swing | None = None
    active_high: Swing | None = None   # unbroken most recent swing high
    active_low: Swing | None = None    # unbroken most recent swing low
    trend = "neutral"                  # 'bullish' | 'bearish' | 'neutral'

    # Walk closed bars only (exclude last still-forming bar if desired; caller passes closed or live)
    for bar in range(n):
        for idx, kind, price in raw_by_confirm.get(bar, []):
            if kind == "high":
                label = "SH" if prev_high is None else ("HH" if price > prev_high.price else "LH")
                sw = Swing(idx, int(times[idx]), price, "high", label)
                swings.append(sw)
                prev_high = sw
                active_high = sw
            else:
                label = "SL" if prev_low is None else ("HL" if price > prev_low.price else "LL")
                sw = Swing(idx, int(times[idx]), price, "low", label)
                swings.append(sw)
                prev_low = sw
                active_low = sw

        # Candle-close break checks (exclude very last bar if incomplete — treat last bar as closed for historic; scanner passes closed bars up to -2 for structural breaks)
        if bar == n - 1:
            continue
        c = float(closes[bar])
        t = int(times[bar])
        if active_high is not None and c > active_high.price:
            ev_kind = "CHoCH" if trend == "bearish" else "BOS"
            breaks.append(BreakEvent(bar, t, ev_kind, "bullish", active_high.price, active_high.time))
            trend = "bullish"
            active_high = None  # consumed — cannot re-break same level
        elif active_low is not None and c < active_low.price:
            ev_kind = "CHoCH" if trend == "bullish" else "BOS"
            breaks.append(BreakEvent(bar, t, ev_kind, "bearish", active_low.price, active_low.time))
            trend = "bearish"
            active_low = None

    last_sh = next((s for s in reversed(swings) if s.kind == "high"), None)
    last_sl = next((s for s in reversed(swings) if s.kind == "low"), None)
    return {
        "trend": trend,
        "swings": [asdict(s) for s in swings[-40:]],
        "breaks": [asdict(b) for b in breaks[-25:]],
        "last_swing_high": asdict(last_sh) if last_sh else None,
        "last_swing_low": asdict(last_sl) if last_sl else None,
        "last_break": asdict(breaks[-1]) if breaks else None,
    }
