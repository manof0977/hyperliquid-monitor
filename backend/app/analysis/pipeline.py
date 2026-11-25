"""Single-timeframe and multi-timeframe analysis pipeline."""
import pandas as pd
from app.analysis.indicators import add_indicators, last_values, divergence
from app.analysis.structure import detect_structure
from app.analysis.smc import detect_smc
from app.analysis.sr import detect_sr
from app.analysis.scoring import evaluate_symbol
from app.config import settings


def analyze_timeframe(
    df: pd.DataFrame,
    daily_df: pd.DataFrame | None = None,
    weekly_df: pd.DataFrame | None = None,
) -> dict | None:
    if df is None or len(df) < 25:
        return None
    df_ind = add_indicators(df)
    st = detect_structure(df_ind, left=settings.swing_left, right=settings.swing_right)
    smc = detect_smc(df_ind, st, equal_tol_atr=settings.equal_tolerance_atr, displacement_atr=settings.ob_displacement_atr)
    sr = detect_sr(df_ind, st, smc, daily_df=daily_df, weekly_df=weekly_df)
    return {
        "n_bars": len(df_ind),
        "ind": last_values(df_ind),
        "div": divergence(df_ind),
        "structure": st,
        "smc": smc,
        "sr": sr,
        "df": df_ind,
    }


def analyze_symbol(frames: dict[str, pd.DataFrame], prev_state: dict | None = None) -> dict | None:
    daily_df = frames.get("1d")
    weekly_df = frames.get("1w")
    mtf: dict[str, dict] = {}
    for tf, df in frames.items():
        res = analyze_timeframe(df, daily_df=daily_df, weekly_df=weekly_df)
        if res is not None:
            mtf[tf] = res
    if not mtf:
        return None
    scored = evaluate_symbol(mtf, prev_state=prev_state)
    scored["mtf_details"] = {
        tf: {k: v for k, v in d.items() if k != "df"} for tf, d in mtf.items()
    }
    return scored
