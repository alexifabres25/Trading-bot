import pandas as pd
import pandas_ta as ta


def add_indicators(df: pd.DataFrame, ema_fast: int, ema_slow: int, rsi_period: int) -> pd.DataFrame:
    """Add EMA fast, EMA slow, RSI and ADX columns to a copy of the dataframe."""
    import config
    df = df.copy()
    df[f"ema_{ema_fast}"] = ta.ema(df["close"], length=ema_fast)
    df[f"ema_{ema_slow}"] = ta.ema(df["close"], length=ema_slow)
    df["rsi"] = ta.rsi(df["close"], length=rsi_period)
    # ADX — mesure la force de la tendance (> ADX_TREND_MIN = marché directionnel)
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=config.ADX_PERIOD)
    if adx_df is not None:
        df["adx"] = adx_df[f"ADX_{config.ADX_PERIOD}"]
    return df


def get_indicator_context(
    df: pd.DataFrame, ema_fast: int, ema_slow: int, rsi_period: int
) -> dict:
    """
    Retourne les valeurs actuelles des indicateurs pour le journal.
    Appelé au moment de l'entrée pour capturer le contexte du signal.
    """
    df = add_indicators(df, ema_fast, ema_slow, rsi_period)
    df = df.dropna()
    if df.empty:
        return {"rsi": 50.0, "ema_spread_pct": 0.0}
    fast = float(df[f"ema_{ema_fast}"].iloc[-1])
    slow = float(df[f"ema_{ema_slow}"].iloc[-1])
    return {
        "rsi": round(float(df["rsi"].iloc[-1]), 2),
        "ema_spread_pct": round((fast - slow) / slow * 100, 4),
    }


def add_supertrend(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ajoute le Supertrend au dataframe.
    Colonnes ajoutées :
      - supertrend_stop  : niveau du stop dynamique (suit la volatilité ATR)
      - supertrend_dir   : direction (1 = haussier, -1 = baissier)

    Avantage vs trailing stop fixe :
      Marché calme (ATR faible) → stop serré → risque réduit
      Marché agité (ATR élevé)  → stop large → moins de faux déclenchements
    """
    import config as _cfg
    df = df.copy()
    st = ta.supertrend(
        df["high"], df["low"], df["close"],
        length=_cfg.SUPERTREND_PERIOD,
        multiplier=_cfg.SUPERTREND_MULTIPLIER,
    )
    if st is not None and not st.empty:
        col_dir = f"SUPERTd_{_cfg.SUPERTREND_PERIOD}_{_cfg.SUPERTREND_MULTIPLIER}"
        col_l   = f"SUPERTl_{_cfg.SUPERTREND_PERIOD}_{_cfg.SUPERTREND_MULTIPLIER}"
        col_s   = f"SUPERTs_{_cfg.SUPERTREND_PERIOD}_{_cfg.SUPERTREND_MULTIPLIER}"
        if col_dir in st.columns:
            df["supertrend_dir"]  = st[col_dir]
            df["supertrend_stop"] = st[col_l].where(st[col_dir] == 1, st[col_s])
    return df


def get_supertrend_stop(df: pd.DataFrame) -> float | None:
    """Retourne le niveau du stop Supertrend sur la dernière bougie confirmée."""
    df = add_supertrend(df)
    if "supertrend_stop" not in df.columns:
        return None
    val = df["supertrend_stop"].dropna()
    return float(val.iloc[-1]) if not val.empty else None


def get_ema_trend(df: pd.DataFrame, ema_fast: int, ema_slow: int) -> str:
    """Return 'bull', 'bear', or 'neutral' based on EMA alignment on the last candle."""
    fast = df[f"ema_{ema_fast}"].iloc[-1]
    slow = df[f"ema_{ema_slow}"].iloc[-1]
    if fast > slow:
        return "bull"
    if fast < slow:
        return "bear"
    return "neutral"
