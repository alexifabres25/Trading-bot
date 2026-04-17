import pandas as pd

import config
from strategy.indicators import add_indicators, get_ema_trend, get_weekly_trend as _get_weekly_trend

BUY = "BUY"
SELL = "SELL"
HOLD = "HOLD"


def generate_1h_signal(df: pd.DataFrame) -> str:
    """
    Stratégie 1h : croisement EMA 9 / EMA 21 filtré par RSI 14.

    - BUY  : EMA 9 croise au-dessus EMA 21  ET  RSI < 70 (pas en surachat)
    - SELL : EMA 9 croise en-dessous EMA 21  ET  RSI > 30 (pas en survente)
    - HOLD : aucun croisement sur la dernière bougie
    """
    df = add_indicators(df, config.EMA_FAST, config.EMA_SLOW, config.RSI_PERIOD)
    df = df.dropna()

    if len(df) < 3:
        return HOLD

    fast = df[f"ema_{config.EMA_FAST}"]
    slow = df[f"ema_{config.EMA_SLOW}"]
    rsi = df["rsi"]

    # Croisement haussier : fast passe au-dessus de slow
    bullish_cross = (fast.iloc[-2] <= slow.iloc[-2]) and (fast.iloc[-1] > slow.iloc[-1])
    # Croisement baissier : fast passe en-dessous de slow
    bearish_cross = (fast.iloc[-2] >= slow.iloc[-2]) and (fast.iloc[-1] < slow.iloc[-1])

    # Filtre ADX — on n'entre pas si le marché est en range (pas directionnel)
    adx_ok = True
    if "adx" in df.columns:
        adx_val = df["adx"].iloc[-1]
        if not (adx_val != adx_val):  # pas NaN
            adx_ok = float(adx_val) >= config.ADX_TREND_MIN

    # Filtre spread EMA (activé dynamiquement par l'analyzer si trop de croisements faibles)
    ema_spread_min = getattr(config, "EMA_SPREAD_MIN", 0.0)
    ema_spread_pct = (fast.iloc[-1] - slow.iloc[-1]) / slow.iloc[-1] * 100

    if bullish_cross and rsi.iloc[-1] < config.RSI_OVERBOUGHT and ema_spread_pct > ema_spread_min and adx_ok:
        return BUY
    if bearish_cross and rsi.iloc[-1] > config.RSI_OVERSOLD:
        return SELL
    return HOLD


def get_4h_trend(df: pd.DataFrame) -> str:
    """Filtre de tendance 4h — alignement EMA 9/21. Retourne 'bull', 'bear' ou 'neutral'."""
    df = add_indicators(df, config.EMA_FAST, config.EMA_SLOW, config.RSI_PERIOD)
    df = df.dropna()
    return get_ema_trend(df, config.EMA_FAST, config.EMA_SLOW)


def get_weekly_trend(df: pd.DataFrame) -> str:
    """Filtre macro — EMA 200 weekly. Retourne 'bull' ou 'bear'."""
    return _get_weekly_trend(df)
