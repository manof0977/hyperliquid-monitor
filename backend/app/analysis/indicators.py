"""Indicators — pure pandas/numpy, causal (no look-ahead)."""
import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    out = out.fillna(100).where(avg_loss != 0, 100.0)
    out = out.fillna(50.0)
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    return volume.rolling(period, min_periods=1).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    for p in (20, 50, 100, 200):
        df[f"ema{p}"] = ema(c, p)
    df["rsi14"] = rsi(c, 14)
    m, s, hist = macd(c)
    df["macd"], df["macd_signal"], df["macd_hist"] = m, s, hist
    df["atr14"] = atr(h, l, c, 14)
    df["vol_sma20"] = volume_sma(v, 20)
    df["vol_ratio"] = v / df["vol_sma20"].replace(0, np.nan)
    # EMA squeeze: spread of the 4 EMAs normalised by ATR (small = squeezed)
    emas = df[["ema20", "ema50", "ema100", "ema200"]]
    df["ema_spread_atr"] = (emas.max(axis=1) - emas.min(axis=1)) / df["atr14"].replace(0, np.nan)
    df["ema_spread_pct"] = (emas.max(axis=1) - emas.min(axis=1)) / c * 100
    # EMA stack direction
    df["ema_stack"] = np.where(
        (df["ema20"] > df["ema50"]) & (df["ema50"] > df["ema100"]) & (df["ema100"] > df["ema200"]), "bullish",
        np.where((df["ema20"] < df["ema50"]) & (df["ema50"] < df["ema100"]) & (df["ema100"] < df["ema200"]), "bearish", "mixed"))
    return df


def last_values(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    r = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else r

    def _wicks(row) -> tuple[float, float]:
        hi, lo = float(row["high"]), float(row["low"])
        op, cl = float(row["open"]), float(row["close"])
        rng = max(hi - lo, 1e-12)
        up_w = max(0.0, hi - max(op, cl)) / rng * 100.0
        lo_w = max(0.0, min(op, cl) - lo) / rng * 100.0
        return round(up_w, 1), round(lo_w, 1)

    c_up_w, c_lo_w = _wicks(prev)
    l_up_w, l_lo_w = _wicks(r)
    atr_now = max(float(r["atr14"]) if pd.notna(r["atr14"]) else 0.0, 1e-12)
    ema20_now = float(r["ema20"]) if pd.notna(r["ema20"]) else float(r["close"])
    stretch_atr = (float(r["close"]) - ema20_now) / atr_now

    # Consecutive closed directional bars ending at iloc[-2] (exhaustion run count)
    consec_bull = 0
    consec_bear = 0
    closed_slice = df.iloc[:-1] if len(df) > 1 else df
    for idx in range(len(closed_slice) - 1, max(-1, len(closed_slice) - 10), -1):
        bar = closed_slice.iloc[idx]
        if float(bar["close"]) > float(bar["open"]):
            if consec_bear > 0:
                break
            consec_bull += 1
        elif float(bar["close"]) < float(bar["open"]):
            if consec_bull > 0:
                break
            consec_bear += 1
        else:
            break

    return {
        "close": float(r["close"]), "ema20": ema20_now, "ema50": float(r["ema50"]),
        "ema100": float(r["ema100"]), "ema200": float(r["ema200"]), "rsi14": float(r["rsi14"]),
        "macd_hist": float(r["macd_hist"]), "atr14": float(r["atr14"]),
        "vol_ratio": float(r["vol_ratio"]) if pd.notna(r["vol_ratio"]) else 1.0,
        "ema_spread_atr": float(r["ema_spread_atr"]) if pd.notna(r["ema_spread_atr"]) else 99.0,
        "ema_spread_prev": float(prev["ema_spread_atr"]) if pd.notna(prev["ema_spread_atr"]) else 99.0,
        "ema_stack": str(r["ema_stack"]),
        "atr_prev": float(prev["atr14"]) if pd.notna(prev["atr14"]) else float(r["atr14"]),
        "vol_ratio_prev": float(prev["vol_ratio"]) if pd.notna(prev["vol_ratio"]) else 1.0,
        "closed_upper_wick_pct": c_up_w,
        "closed_lower_wick_pct": c_lo_w,
        "live_upper_wick_pct": l_up_w,
        "live_lower_wick_pct": l_lo_w,
        "ema20_stretch_atr": round(stretch_atr, 2),
        "consec_bull_bars": consec_bull,
        "consec_bear_bars": consec_bear,
    }


def divergence(df: pd.DataFrame, lookback: int = 30) -> dict:
    """Bullish divergence: price makes lower low while RSI makes higher low (and mirror)."""
    out = {"bullish_rsi": False, "bearish_rsi": False, "bullish_macd": False, "bearish_macd": False}
    if len(df) < lookback + 5:
        return out
    w = df.tail(lookback)
    # split window in two halves, compare extremes
    half = len(w) // 2
    a, b = w.iloc[:half], w.iloc[half:]
    price_low_a, price_low_b = a["low"].min(), b["low"].min()
    price_high_a, price_high_b = a["high"].max(), b["high"].max()
    rsi_low_a, rsi_low_b = a["rsi14"].min(), b["rsi14"].min()
    rsi_high_a, rsi_high_b = a["rsi14"].max(), b["rsi14"].max()
    macd_low_a, macd_low_b = a["macd_hist"].min(), b["macd_hist"].min()
    macd_high_a, macd_high_b = a["macd_hist"].max(), b["macd_hist"].max()
    if price_low_b < price_low_a and rsi_low_b > rsi_low_a:
        out["bullish_rsi"] = True
    if price_low_b < price_low_a and macd_low_b > macd_low_a:
        out["bullish_macd"] = True
    if price_high_b > price_high_a and rsi_high_b < rsi_high_a:
        out["bearish_rsi"] = True
    if price_high_b > price_high_a and macd_high_b < macd_high_a:
        out["bearish_macd"] = True
    return out
