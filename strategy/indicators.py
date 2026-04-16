import numpy as np
import pandas as pd


# ── Implémentations pures pandas/numpy — remplace pandas-ta ───────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr = _atr(high, low, close, period)
    plus_di = 100 * plus_dm.ewm(com=period - 1, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(com=period - 1, min_periods=period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    return dx.ewm(com=period - 1, min_periods=period).mean()


def _supertrend(
    high: pd.Series, low: pd.Series, close: pd.Series,
    period: int, multiplier: float,
) -> tuple:
    atr = _atr(high, low, close, period)
    hl2 = (high + low) / 2

    raw_upper = (hl2 + multiplier * atr).to_numpy()
    raw_lower = (hl2 - multiplier * atr).to_numpy()
    closes = close.to_numpy()

    n = len(closes)
    upper = raw_upper.copy()
    lower = raw_lower.copy()
    direction = np.ones(n, dtype=int)
    stop = np.full(n, np.nan)

    for i in range(1, n):
        lower[i] = (
            raw_lower[i]
            if raw_lower[i] > lower[i - 1] or closes[i - 1] < lower[i - 1]
            else lower[i - 1]
        )
        upper[i] = (
            raw_upper[i]
            if raw_upper[i] < upper[i - 1] or closes[i - 1] > upper[i - 1]
            else upper[i - 1]
        )
        if closes[i] > upper[i - 1]:
            direction[i] = 1
        elif closes[i] < lower[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        stop[i] = lower[i] if direction[i] == 1 else upper[i]

    return (
        pd.Series(stop, index=close.index),
        pd.Series(direction, index=close.index),
    )


# ── API publique (signatures inchangées) ──────────────────────────────────────

def add_indicators(
    df: pd.DataFrame, ema_fast: int, ema_slow: int, rsi_period: int
) -> pd.DataFrame:
    """Ajoute EMA fast, EMA slow, RSI et ADX au dataframe."""
    import config
    df = df.copy()
    df[f"ema_{ema_fast}"] = _ema(df["close"], ema_fast)
    df[f"ema_{ema_slow}"] = _ema(df["close"], ema_slow)
    df["rsi"] = _rsi(df["close"], rsi_period)
    df["adx"] = _adx(df["high"], df["low"], df["close"], config.ADX_PERIOD)
    return df


def get_indicator_context(
    df: pd.DataFrame, ema_fast: int, ema_slow: int, rsi_period: int
) -> dict:
    """Retourne les valeurs actuelles des indicateurs pour le journal."""
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
    """Ajoute supertrend_stop et supertrend_dir au dataframe."""
    import config as _cfg
    df = df.copy()
    stop, direction = _supertrend(
        df["high"], df["low"], df["close"],
        _cfg.SUPERTREND_PERIOD, _cfg.SUPERTREND_MULTIPLIER,
    )
    df["supertrend_stop"] = stop
    df["supertrend_dir"] = direction
    return df


def get_supertrend_stop(df: pd.DataFrame) -> float | None:
    """Retourne le niveau du stop Supertrend sur la dernière bougie confirmée."""
    df = add_supertrend(df)
    val = df["supertrend_stop"].dropna()
    return float(val.iloc[-1]) if not val.empty else None


def get_ema_trend(df: pd.DataFrame, ema_fast: int, ema_slow: int) -> str:
    """Retourne 'bull', 'bear' ou 'neutral' selon l'alignement EMA."""
    fast = df[f"ema_{ema_fast}"].iloc[-1]
    slow = df[f"ema_{ema_slow}"].iloc[-1]
    if fast > slow:
        return "bull"
    if fast < slow:
        return "bear"
    return "neutral"
