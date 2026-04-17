"""
Optimiseur de paramètres — grid search sur les combinaisons EMA/RSI/SL/TP.

Lance un backtest pour chaque combinaison et retourne les meilleurs paramètres
selon le score composite : Sharpe × profit_factor (pénalise les stratégies
avec peu de trades ou un drawdown excessif).

Usage :
    python backtest/run.py --optimize
"""

import itertools
import logging

from backtest.engine import run_backtest, BacktestResult

logger = logging.getLogger(__name__)

# ── Grille de paramètres ───────────────────────────────────────────────────────
PARAM_GRID = {
    "ema_fast":        [7, 9, 12],
    "ema_slow":        [18, 21, 26],
    "rsi_overbought":  [65, 70, 75],
    "stop_loss_pct":   [0.015, 0.02, 0.025],
    "take_profit_ratio": [1.5, 2.0, 2.5],
}

# Paramètres fixes (non optimisés)
FIXED_PARAMS = {
    "rsi_period":  14,
    "rsi_oversold": 30,
    "adx_period":  14,
    "adx_min":     20,
    "fee_rate":    0.001,
}


def _score(result: BacktestResult) -> float:
    """Score composite : récompense Sharpe et profit factor, pénalise drawdown et peu de trades."""
    if result.nb_trades < 10:
        return -999.0
    if result.max_drawdown_pct > 30:
        return -999.0
    return result.sharpe_ratio * result.profit_factor


def optimize(symbol: str, days: int = 730) -> dict:
    """
    Lance le grid search sur `symbol` sur `days` jours.
    Retourne le dictionnaire de paramètres optimal.
    """
    keys = list(PARAM_GRID.keys())
    combinations = list(itertools.product(*[PARAM_GRID[k] for k in keys]))
    total = len(combinations)

    logger.info(f"[Optimizer] {symbol} — {total} combinaisons × 1 backtest = {total} runs")

    best_score = -float("inf")
    best_params = None
    best_result = None

    for idx, combo in enumerate(combinations, 1):
        params = {**FIXED_PARAMS, **dict(zip(keys, combo))}

        # Filtre rapide : ema_fast doit être < ema_slow
        if params["ema_fast"] >= params["ema_slow"]:
            continue

        try:
            result = run_backtest(symbol, days=days, params=params)
            score = _score(result)

            if score > best_score:
                best_score = score
                best_params = params
                best_result = result
                logger.info(
                    f"[Optimizer] Nouveau meilleur ({idx}/{total}) "
                    f"score={score:.3f}  "
                    f"EMA {params['ema_fast']}/{params['ema_slow']}  "
                    f"RSI_OB={params['rsi_overbought']}  "
                    f"SL={params['stop_loss_pct']:.1%}  "
                    f"TP={params['take_profit_ratio']}:1\n"
                    + result.summary()
                )

            if idx % 10 == 0:
                logger.info(f"[Optimizer] Progression : {idx}/{total}")

        except Exception as exc:
            logger.warning(f"[Optimizer] Combo {combo} échouée : {exc}")

    if best_result:
        logger.info(
            f"\n[Optimizer] ═══ RÉSULTAT OPTIMAL — {symbol} ═══\n"
            + best_result.summary()
        )

    return best_params or {}
