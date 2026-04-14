"""
Risk Manager — trois niveaux de protection imbriqués :

1. Kelly Criterion (Quarter-Kelly)
   Calcule le risque optimal depuis le vrai track record du bot.
   f* = (W/R × win_rate - loss_rate) / W/R  puis × KELLY_FRACTION (0.25)
   → Adapte la taille des positions à la performance réelle, pas à une valeur fixe.

2. Drawdown-based risk scaling (DD scaling)
   Réduit le risque par palier progressif quand le bot perd de l'argent.
   Inspiré du TrendWin EA visible dans les screenshots :
     DD < 2 %  → risque normal (Kelly)
     DD 2–4 %  → 0.75 % max
     DD 4–6 %  → 0.50 % max
     DD > 6 %  → 0.25 % max  (mode survie à 0.1 % au-delà)

3. Hard caps
   RISK_MAX_CAP (2 %) et RISK_MIN_FLOOR (0.1 %) quoi qu'il arrive.
"""

import json
import logging
from pathlib import Path

import config
from learning.journal import load_journal

logger = logging.getLogger(__name__)


# ── Suivi de l'équité (persistant) ────────────────────────────────────────────

def _load_equity_state() -> dict:
    path = Path(config.EQUITY_FILE)
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"peak_equity": config.CAPITAL, "current_equity": config.CAPITAL}


def _save_equity_state(state: dict):
    with open(config.EQUITY_FILE, "w") as f:
        json.dump(state, f, indent=2)


def update_equity(current_balance: float):
    """
    Met à jour l'équité courante et le pic historique.
    Appelé une fois par cycle de la boucle principale.
    """
    state = _load_equity_state()
    state["current_equity"] = current_balance
    if current_balance > state["peak_equity"]:
        state["peak_equity"] = current_balance
        logger.info(f"[Equity] Nouveau pic : {current_balance:.2f} USDT")
    _save_equity_state(state)


def get_current_drawdown() -> float:
    """Retourne le drawdown depuis le pic (0.0 = pas de DD, 0.06 = 6 % de DD)."""
    state = _load_equity_state()
    peak = state.get("peak_equity", config.CAPITAL)
    current = state.get("current_equity", config.CAPITAL)
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - current) / peak)


# ── Kelly Criterion ────────────────────────────────────────────────────────────

def get_kelly_risk_pct() -> float:
    """
    Calcule le risque par trade via Kelly Criterion (quarter-Kelly).

    Formule complète :
        f* = (W/R × win_rate - loss_rate) / W/R
        W/R = avg_win_pct / avg_loss_pct

    Exemple : win_rate=52 %, avg_win=3 %, avg_loss=2 %
        W/R   = 3/2 = 1.5
        f*    = (1.5 × 0.52 - 0.48) / 1.5 = 0.20 (20 %)
        ×0.25 = 5 % → plafonné à RISK_MAX_CAP = 2 %

    Si moins de 10 trades dans le journal → fallback sur RISK_PER_TRADE.
    """
    journal = load_journal()
    closed = [t for t in journal if t.get("status") == "closed"]
    lookback = closed[-config.KELLY_LOOKBACK:] if len(closed) > config.KELLY_LOOKBACK else closed

    if len(lookback) < 5:
        logger.debug(f"[Kelly] {len(lookback)} trades — fallback sur RISK_PER_TRADE")
        return config.RISK_PER_TRADE

    wins = [t for t in lookback if t.get("outcome") == "win"]
    losses = [t for t in lookback if t.get("outcome") == "loss"]

    if not wins or not losses:
        return config.RISK_PER_TRADE

    win_rate = len(wins) / len(lookback)
    loss_rate = 1 - win_rate

    # Utilise le PnL net si disponible, sinon brut
    avg_win = sum(t.get("pnl_net_pct", t["pnl_pct"]) for t in wins) / len(wins) / 100
    avg_loss = abs(sum(t.get("pnl_net_pct", t["pnl_pct"]) for t in losses) / len(losses)) / 100

    if avg_loss == 0:
        return config.RISK_PER_TRADE

    wr_ratio = avg_win / avg_loss
    kelly_full = (wr_ratio * win_rate - loss_rate) / wr_ratio
    kelly_quarter = kelly_full * config.KELLY_FRACTION

    result = max(config.RISK_MIN_FLOOR, min(kelly_quarter, config.RISK_MAX_CAP))

    logger.info(
        f"[Kelly] win_rate={win_rate:.0%}  W/R={wr_ratio:.2f}  "
        f"f*={kelly_full:.3f}  ×{config.KELLY_FRACTION}={kelly_quarter:.3f}  "
        f"→ {result:.2%} (caps [{config.RISK_MIN_FLOOR:.1%}–{config.RISK_MAX_CAP:.1%}])"
    )
    return result


# ── Multiplicateur progressif sur les pertes ──────────────────────────────────

def get_risk_multiplier() -> float:
    """Retourne le multiplicateur de risque courant (persistant entre sessions)."""
    state = _load_equity_state()
    return state.get("risk_multiplier", 1.0)


def update_risk_multiplier(outcome: str):
    """
    Ajuste le multiplicateur après chaque trade.

    Perte → ×(1 - 0.10) = ×0.90  (risque réduit de 10%)
    Gain  → ×(1 + 0.05) = ×1.05  (récupération progressive de 5%)

    Exemple avec risque de base 1% :
        Perte → 0.90% → Perte → 0.81% → Perte → 0.73%
        Gain  → 0.76% → Gain  → 0.80% → Gain  → 0.84%...
        (jamais au-dessus de 1%, jamais en-dessous de 0.20%)
    """
    state = _load_equity_state()
    current = state.get("risk_multiplier", 1.0)

    if outcome == "loss":
        new = current * (1 - config.LOSS_RISK_REDUCTION)
        logger.warning(
            f"[Multiplicateur] Perte → {current:.3f} × 0.90 = {new:.3f}  "
            f"(risque effectif ×{new:.2f})"
        )
    else:
        new = current * (1 + config.WIN_RISK_RECOVERY)
        logger.info(
            f"[Multiplicateur] Gain → {current:.3f} × 1.05 = {new:.3f}  "
            f"(récupération progressive)"
        )

    new = max(config.RISK_MULTIPLIER_MIN, min(new, config.RISK_MULTIPLIER_MAX))
    state["risk_multiplier"] = round(new, 6)
    _save_equity_state(state)


# ── Drawdown scaling ───────────────────────────────────────────────────────────

def get_dynamic_risk_pct() -> float:
    """
    Risque par trade final = Kelly × DD scaling.

    Paliers (calqués sur TrendWin EA) :
        DD < 2 %  → risque Kelly × multiplicateur
        DD 2–4 %  → min(Kelly × multiplicateur, 0.75 %)   Tier 1
        DD 4–6 %  → min(Kelly × multiplicateur, 0.50 %)   Tier 2
        DD 6–?%  → min(Kelly × multiplicateur, 0.25 %)   Tier 3
        DD > 6 %  → 0.10 %                                Mode survie
    """
    # 1. Kelly depuis le track record réel
    kelly_risk = get_kelly_risk_pct()

    # 2. Multiplicateur progressif sur pertes/gains (-10% par perte, +5% par gain)
    multiplier = get_risk_multiplier()
    base_risk = kelly_risk * multiplier

    if multiplier < 1.0:
        logger.info(
            f"[Multiplicateur] Kelly={kelly_risk:.2%} × {multiplier:.3f} "
            f"= {base_risk:.2%}"
        )

    if not config.DD_SCALING_ENABLED:
        return base_risk

    dd = get_current_drawdown()

    if dd >= config.DD_TIER_3:
        risk = config.RISK_BEYOND_TIER_3
        logger.warning(
            f"[DD Scaling] ⛔ MODE SURVIE — DD={dd:.1%}  risque → {risk:.2%}"
        )
    elif dd >= config.DD_TIER_2:
        risk = min(base_risk, config.RISK_AT_DD_TIER_3)
        logger.warning(
            f"[DD Scaling] Tier 3 — DD={dd:.1%}  risque → {risk:.2%}"
        )
    elif dd >= config.DD_TIER_1:
        risk = min(base_risk, config.RISK_AT_DD_TIER_2)
        logger.info(
            f"[DD Scaling] Tier 2 — DD={dd:.1%}  risque → {risk:.2%}"
        )
    elif dd >= config.DD_TIER_1 / 2:
        risk = min(base_risk, config.RISK_AT_DD_TIER_1)
        logger.info(
            f"[DD Scaling] Tier 1 — DD={dd:.1%}  risque → {risk:.2%}"
        )
    else:
        risk = base_risk
        logger.info(f"[DD Scaling] Normal — DD={dd:.1%}  risque={risk:.2%}")

    return risk


# ── Stop-loss & trailing ───────────────────────────────────────────────────────

def calculate_stop_loss(entry_price: float) -> float:
    """Stop-loss initial à STOP_LOSS_PCT % sous le prix d'entrée."""
    return round(entry_price * (1 - config.STOP_LOSS_PCT), 8)


def update_trailing_stop(current_price: float, position: dict) -> tuple[float, float]:
    """
    Le stop monte avec le prix mais ne redescend jamais.
    Retourne (nouveau_max_price, nouveau_stop).
    """
    max_price = max(position.get("max_price", position["entry_price"]), current_price)
    trailing_stop = round(max_price * (1 - config.TRAILING_STOP_PCT), 8)
    new_stop = max(trailing_stop, position["stop_loss"])
    return max_price, new_stop


# ── Taille de position ─────────────────────────────────────────────────────────

def calculate_position_size(entry_price: float, available_usdt: float) -> float:
    """
    Quantité à acheter basée sur le risque dynamique (Kelly + DD scaling).

        risque_usdt = CAPITAL × get_dynamic_risk_pct()
        distance_SL = entry_price × STOP_LOSS_PCT
        quantité    = risque_usdt / distance_SL
    """
    risk_pct = get_dynamic_risk_pct()
    risk_usdt = config.CAPITAL * risk_pct
    stop_distance = entry_price * config.STOP_LOSS_PCT
    qty = risk_usdt / stop_distance

    max_qty = (available_usdt / entry_price) * 0.99
    qty = min(qty, max_qty)

    logger.info(
        f"[Position] risque={risk_pct:.2%}  "
        f"risk_usdt={risk_usdt:.2f}  qty={qty:.6f}"
    )
    return round(qty, 6)
