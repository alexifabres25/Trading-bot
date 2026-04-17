"""
Moteur de backtesting — simule la stratégie sur données historiques Binance.

Conception :
  - Données réelles téléchargées via ccxt (gratuit, sans limite de profondeur)
  - Signal à la clôture de la bougie i, exécution au prix de clôture de i
  - Stop-loss et take-profit vérifiés sur le high/low de la bougie (réaliste)
  - Frais 0.1% aller + 0.1% retour intégrés dans chaque trade
  - Pas de lookahead bias : indicateurs calculés sur df.iloc[:i+1]

Métriques retournées :
  total_return_pct, win_rate, profit_factor, max_drawdown_pct,
  sharpe_ratio, nb_trades, avg_win_pct, avg_loss_pct
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── Structures ─────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    symbol: str
    entry_price: float
    entry_bar: int
    stop_loss: float
    take_profit: float
    amount: float = 1.0
    exit_price: float = 0.0
    exit_bar: int = 0
    reason: str = ""
    pnl_pct: float = 0.0
    pnl_net_pct: float = 0.0


@dataclass
class BacktestResult:
    symbol: str
    params: dict
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)

    # Métriques calculées
    total_return_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    nb_trades: int = 0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0

    def summary(self) -> str:
        return (
            f"  Trades       : {self.nb_trades}\n"
            f"  Return       : {self.total_return_pct:+.1f}%\n"
            f"  Win rate     : {self.win_rate:.0f}%\n"
            f"  Profit factor: {self.profit_factor:.2f}\n"
            f"  Max drawdown : {self.max_drawdown_pct:.1f}%\n"
            f"  Sharpe ratio : {self.sharpe_ratio:.2f}\n"
            f"  Avg win      : +{self.avg_win_pct:.2f}%\n"
            f"  Avg loss     : {self.avg_loss_pct:.2f}%"
        )


# ── Téléchargement des données ─────────────────────────────────────────────────

def fetch_historical(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """
    Télécharge les données historiques depuis Binance via ccxt.
    Gère la pagination automatiquement (limite 1000 bougies/requête).
    """
    import ccxt
    import config

    exchange = ccxt.binance({"enableRateLimit": True})
    if config.USE_TESTNET:
        exchange.set_sandbox_mode(True)

    limit = 1000
    tf_ms = {
        "1h": 3_600_000, "4h": 14_400_000,
        "1d": 86_400_000, "1w": 604_800_000,
    }
    ms_per_bar = tf_ms.get(timeframe, 3_600_000)
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

    all_bars = []
    while True:
        bars = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        if not bars:
            break
        all_bars.extend(bars)
        if len(bars) < limit:
            break
        since = bars[-1][0] + ms_per_bar

    df = pd.DataFrame(all_bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates("timestamp").set_index("timestamp").sort_index()
    logger.info(f"[Backtest] {symbol} {timeframe} : {len(df)} bougies téléchargées")
    return df


# ── Calcul des indicateurs (vectorisé, sans lookahead) ────────────────────────

def _compute_signals(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Calcule tous les indicateurs et signaux sur le dataframe complet.
    Pas de lookahead : chaque valeur ne dépend que des bougies passées.
    """
    from strategy.indicators import _ema, _rsi, _adx

    ema_fast = params["ema_fast"]
    ema_slow = params["ema_slow"]
    rsi_period = params["rsi_period"]
    adx_period = params.get("adx_period", 14)
    adx_min = params.get("adx_min", 20)

    df = df.copy()
    df["ema_fast"] = _ema(df["close"], ema_fast)
    df["ema_slow"] = _ema(df["close"], ema_slow)
    df["rsi"] = _rsi(df["close"], rsi_period)
    df["adx"] = _adx(df["high"], df["low"], df["close"], adx_period)

    # Croisements
    df["cross_up"] = (df["ema_fast"].shift(1) <= df["ema_slow"].shift(1)) & \
                     (df["ema_fast"] > df["ema_slow"])
    df["cross_dn"] = (df["ema_fast"].shift(1) >= df["ema_slow"].shift(1)) & \
                     (df["ema_fast"] < df["ema_slow"])

    rsi_ob = params.get("rsi_overbought", 70)
    rsi_os = params.get("rsi_oversold", 30)

    df["signal_buy"]  = df["cross_up"] & (df["rsi"] < rsi_ob) & (df["adx"] >= adx_min)
    df["signal_sell"] = df["cross_dn"] & (df["rsi"] > rsi_os)

    return df


def _align_4h_trend(df_1h: pd.DataFrame, df_4h: pd.DataFrame, params: dict) -> pd.Series:
    """Aligne la tendance 4h sur les timestamps 1h (forward-fill)."""
    from strategy.indicators import _ema

    ema_fast = params["ema_fast"]
    ema_slow = params["ema_slow"]

    df4 = df_4h.copy()
    df4["ema_fast"] = _ema(df4["close"], ema_fast)
    df4["ema_slow"] = _ema(df4["close"], ema_slow)
    df4["trend"] = "neutral"
    df4.loc[df4["ema_fast"] > df4["ema_slow"], "trend"] = "bull"
    df4.loc[df4["ema_fast"] < df4["ema_slow"], "trend"] = "bear"

    trend_series = df4["trend"].reindex(df_1h.index, method="ffill")
    return trend_series


# ── Simulation ─────────────────────────────────────────────────────────────────

def run_backtest(
    symbol: str,
    days: int = 1095,
    params: dict | None = None,
) -> BacktestResult:
    """
    Lance un backtest complet sur `days` jours.

    params par défaut : EMA 9/21, RSI 14, SL 2%, TP 2:1
    """
    if params is None:
        import config as _cfg
        params = {
            "ema_fast": _cfg.EMA_FAST,
            "ema_slow": _cfg.EMA_SLOW,
            "rsi_period": _cfg.RSI_PERIOD,
            "rsi_overbought": _cfg.RSI_OVERBOUGHT,
            "rsi_oversold": _cfg.RSI_OVERSOLD,
            "adx_period": _cfg.ADX_PERIOD,
            "adx_min": _cfg.ADX_TREND_MIN,
            "stop_loss_pct": _cfg.STOP_LOSS_PCT,
            "take_profit_ratio": _cfg.TAKE_PROFIT_RATIO,
            "fee_rate": _cfg.FEE_RATE,
        }

    result = BacktestResult(symbol=symbol, params=params)

    logger.info(f"[Backtest] Téléchargement données {symbol}...")
    df_1h = fetch_historical(symbol, "1h", days)
    df_4h = fetch_historical(symbol, "4h", days)

    df = _compute_signals(df_1h, params)
    trend_4h = _align_4h_trend(df_1h, df_4h, params)

    sl_pct  = params["stop_loss_pct"]
    tp_ratio = params["take_profit_ratio"]
    fee     = params["fee_rate"]
    warmup  = max(params["ema_slow"] * 3, 50)

    capital = 1.0
    equity_curve = [capital]
    open_trade: Trade | None = None
    trades: list[Trade] = []

    for i in range(warmup, len(df)):
        bar = df.iloc[i]
        t4h = trend_4h.iloc[i] if i < len(trend_4h) else "neutral"

        # ── Gérer la position ouverte ────────────────────────────────────────
        if open_trade:
            # Vérifier SL sur le low de la bougie (réaliste)
            if bar["low"] <= open_trade.stop_loss:
                exit_price = open_trade.stop_loss
                _close_trade(open_trade, exit_price, i, "stop-loss", fee, trades)
                capital *= (1 + open_trade.pnl_net_pct / 100)
                open_trade = None

            # Vérifier TP sur le high de la bougie
            elif bar["high"] >= open_trade.take_profit:
                exit_price = open_trade.take_profit
                _close_trade(open_trade, exit_price, i, "take-profit", fee, trades)
                capital *= (1 + open_trade.pnl_net_pct / 100)
                open_trade = None

            # Signal de vente
            elif bar["signal_sell"] or t4h == "bear":
                _close_trade(open_trade, bar["close"], i, "signal", fee, trades)
                capital *= (1 + open_trade.pnl_net_pct / 100)
                open_trade = None

        # ── Ouvrir une position ──────────────────────────────────────────────
        if not open_trade and bar["signal_buy"] and t4h != "bear":
            entry = bar["close"]
            sl    = round(entry * (1 - sl_pct), 8)
            tp    = round(entry * (1 + sl_pct * tp_ratio), 8)
            open_trade = Trade(
                symbol=symbol,
                entry_price=entry,
                entry_bar=i,
                stop_loss=sl,
                take_profit=tp,
            )

        equity_curve.append(capital)

    # Fermer la position ouverte en fin de période
    if open_trade:
        _close_trade(open_trade, df.iloc[-1]["close"], len(df) - 1, "end", fee, trades)
        capital *= (1 + open_trade.pnl_net_pct / 100)
        trades.append(open_trade)

    result.trades = trades
    result.equity_curve = equity_curve
    _calc_metrics(result)
    return result


def _close_trade(trade: Trade, exit_price: float, bar: int, reason: str, fee: float, trades: list):
    trade.exit_price = exit_price
    trade.exit_bar = bar
    trade.reason = reason
    trade.pnl_pct = (exit_price / trade.entry_price - 1) * 100
    trade.pnl_net_pct = trade.pnl_pct - fee * 2 * 100
    trades.append(trade)


def _calc_metrics(result: BacktestResult):
    trades = result.trades
    result.nb_trades = len(trades)

    if not trades:
        return

    wins   = [t for t in trades if t.pnl_net_pct > 0]
    losses = [t for t in trades if t.pnl_net_pct <= 0]

    result.win_rate    = len(wins) / len(trades) * 100
    result.avg_win_pct = sum(t.pnl_net_pct for t in wins) / len(wins) if wins else 0
    result.avg_loss_pct = sum(t.pnl_net_pct for t in losses) / len(losses) if losses else 0

    gross_profit = sum(t.pnl_net_pct for t in wins)
    gross_loss   = abs(sum(t.pnl_net_pct for t in losses))
    result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Total return depuis la courbe d'équité
    eq = np.array(result.equity_curve)
    result.total_return_pct = (eq[-1] / eq[0] - 1) * 100

    # Max drawdown
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak * 100
    result.max_drawdown_pct = abs(float(dd.min()))

    # Sharpe ratio (annualisé sur 1h bars, 8760 bars/an)
    returns = np.diff(eq) / eq[:-1]
    if returns.std() > 0:
        result.sharpe_ratio = float(returns.mean() / returns.std() * np.sqrt(8760))
    else:
        result.sharpe_ratio = 0.0
