import pandas as pd
import pandas_ta as ta


def add_indicators(df: pd.DataFrame, ema_fast: int, ema_slow: int, rsi_period: int) -> pd.DataFrame:
    """Add EMA fast, EMA slow, and RSI columns to a copy of the dataframe."""
    df = df.copy()
    df[f"ema_{ema_fast}"] = ta.ema(df["close"], length=ema_fast)
    df[f"ema_{ema_slow}"] = ta.ema(df["close"], length=ema_slow)
    df["rsi"] = ta.rsi(df["close"], length=rsi_period)
    return df


def get_ema_trend(df: pd.DataFrame, ema_fast: int, ema_slow: int) -> str:
    """Return 'bull', 'bear', or 'neutral' based on EMA alignment on the last candle."""
    fast = df[f"ema_{ema_fast}"].iloc[-1]
    slow = df[f"ema_{ema_slow}"].iloc[-1]
    if fast > slow:
        return "bull"
    if fast < slow:
        return "bear"
    return "neutral"
