"""
Analyseur post-trade — détecte les erreurs répétées et ajuste les paramètres.

Erreurs classifiées :
  stop_trop_serre      → stop déclenché en < 4h (la volatilité l'a avalé)
  entree_surachat      → RSI > 62 à l'entrée (acheté trop tard dans le mouvement)
  croisement_faible    → EMA spread < 0.3% (signal peu convaincu, bruit de marché)
  tendance_4h_neutre   → entrée sans confirmation de tendance (marché en range)
  marche_contre        → aucune erreur identifiable, le marché s'est retourné

Après chaque erreur dominante détectée sur 5+ trades perdants consécutifs,
le bot ajuste dynamiquement ses seuils et envoie une alerte Telegram.
"""

import logging
from collections import Counter

import config
from learning.journal import load_journal
from notifications.telegram_bot import send_status

logger = logging.getLogger(__name__)

# ── Seuils de classification ───────────────────────────────────────────────────
PREMATURE_STOP_HOURS = 4      # stop en < 4h = trop serré
HIGH_RSI_ENTRY = 62           # RSI > 62 = proche surachat
WEAK_EMA_SPREAD = 0.3         # spread < 0.3% = croisement faible
MIN_TRADES_TO_ANALYZE = 30    # minimum 30 trades pour éviter le surapprentissage

# ── Limites d'adaptation (garde-fous) ─────────────────────────────────────────
TRAIL_STOP_MIN = 0.015        # 1.5% minimum
TRAIL_STOP_MAX = 0.04         # 4% maximum
RSI_OB_MIN = 55               # RSI overbought minimum
RSI_OB_MAX = 75               # RSI overbought maximum


def classify_loss(trade: dict) -> list[str]:
    """Identifie les causes d'un trade perdant. Retourne une liste d'erreurs."""
    errors = []

    if trade.get("duration_hours", 999) < PREMATURE_STOP_HOURS:
        errors.append("stop_trop_serre")

    if trade.get("entry_rsi", 0) > HIGH_RSI_ENTRY:
        errors.append("entree_surachat")

    if abs(trade.get("entry_ema_spread_pct", 1)) < WEAK_EMA_SPREAD:
        errors.append("croisement_faible")

    if trade.get("entry_4h_trend") == "neutral":
        errors.append("tendance_4h_neutre")

    if not errors:
        errors.append("marche_contre")

    return errors


def _stats(trades: list[dict]) -> dict:
    wins = [t for t in trades if t.get("outcome") == "win"]
    losses = [t for t in trades if t.get("outcome") == "loss"]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    return {
        "total": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "ratio": round(ratio, 2),
    }


def analyze_and_adapt(symbol: str) -> dict:
    """
    Analyse les trades récents, classifie les erreurs, adapte les paramètres.
    Retourne le rapport complet.
    """
    journal = load_journal()
    closed = [t for t in journal if t.get("status") == "closed" and t["symbol"] == symbol]

    if len(closed) < MIN_TRADES_TO_ANALYZE:
        logger.info(
            f"[Apprentissage] {symbol} — {len(closed)}/{MIN_TRADES_TO_ANALYZE} trades"
            " fermés, analyse reportée."
        )
        return {}

    recent = closed[-50:]  # fenêtre glissante sur les 50 derniers trades (anti-surapprentissage)
    stats = _stats(recent)
    losses = [t for t in recent if t.get("outcome") == "loss"]

    # Classifier toutes les erreurs
    all_errors: list[str] = []
    for trade in losses:
        all_errors.extend(classify_loss(trade))

    error_counts = Counter(all_errors)
    dominant = error_counts.most_common(1)[0] if error_counts else None

    report = {**stats, "dominant_error": None, "adjustments": []}

    if not dominant or not losses:
        _log_report(symbol, report)
        return report

    error_name, error_count = dominant
    error_pct = error_count / len(losses) * 100
    report["dominant_error"] = error_name
    report["dominant_error_pct"] = round(error_pct, 0)

    # ── Décisions d'adaptation ─────────────────────────────────────────────────
    adjustments = []

    if error_name == "stop_trop_serre" and error_pct > 50:
        new_val = min(config.TRAILING_STOP_PCT * 1.15, TRAIL_STOP_MAX)
        if new_val != config.TRAILING_STOP_PCT:
            config.TRAILING_STOP_PCT = round(new_val, 4)
            adjustments.append({
                "param": "TRAILING_STOP_PCT",
                "new_value": config.TRAILING_STOP_PCT,
                "reason": f"Stop déclenché trop tôt dans {error_pct:.0f}% des pertes",
            })

    elif error_name == "entree_surachat" and error_pct > 40:
        new_val = max(config.RSI_OVERBOUGHT - 3, RSI_OB_MIN)
        if new_val != config.RSI_OVERBOUGHT:
            config.RSI_OVERBOUGHT = new_val
            adjustments.append({
                "param": "RSI_OVERBOUGHT",
                "new_value": config.RSI_OVERBOUGHT,
                "reason": f"Entrées RSI > {HIGH_RSI_ENTRY} dans {error_pct:.0f}% des pertes",
            })

    elif error_name == "croisement_faible" and error_pct > 40:
        adjustments.append({
            "param": "EMA_SPREAD_MIN",
            "new_value": WEAK_EMA_SPREAD,
            "reason": f"Croisements EMA trop faibles dans {error_pct:.0f}% des pertes",
            "note": "Filtre spread EMA activé pour les prochains trades",
        })
        # Stocke la valeur dans config pour que signal.py puisse l'utiliser
        config.EMA_SPREAD_MIN = WEAK_EMA_SPREAD

    report["adjustments"] = adjustments
    _log_report(symbol, report)
    _maybe_notify(symbol, report, len(closed))
    return report


def _log_report(symbol: str, report: dict):
    logger.info(
        f"[Apprentissage] {symbol}  "
        f"Win rate={report['win_rate']}%  "
        f"Ratio={report['ratio']}  "
        f"({report['wins']}W / {report['losses']}L)"
    )
    if report.get("dominant_error"):
        logger.warning(
            f"[Apprentissage] {symbol} — Erreur dominante : "
            f"{report['dominant_error']} ({report.get('dominant_error_pct', 0):.0f}% des pertes)"
        )
    for adj in report.get("adjustments", []):
        logger.warning(
            f"[Adaptation] {adj['param']} → {adj['new_value']}  ({adj['reason']})"
        )


def _maybe_notify(symbol: str, report: dict, total_trades: int):
    """Envoie un bilan Telegram tous les 5 trades fermés."""
    if total_trades % 5 != 0:
        return

    lines = [
        f"📊 *Bilan apprentissage — {symbol}*",
        f"Trades analysés : `{report['total']}`",
        f"Win rate        : `{report['win_rate']}%`",
        f"Gain moyen      : `+{report['avg_win_pct']}%`",
        f"Perte moyenne   : `{report['avg_loss_pct']}%`",
        f"Ratio G/P       : `{report['ratio']}`",
    ]

    if report.get("dominant_error"):
        error_labels = {
            "stop_trop_serre":   "Stop trop serré (sorti trop tôt)",
            "entree_surachat":   "Entrée près du surachat (RSI élevé)",
            "croisement_faible": "Croisement EMA faible (faux signal)",
            "tendance_4h_neutre":"Tendance 4h neutre (marché en range)",
            "marche_contre":     "Retournement de marché (non évitable)",
        }
        label = error_labels.get(report["dominant_error"], report["dominant_error"])
        lines.append(f"\n⚠️ *Erreur dominante :*\n_{label}_")

    if report.get("adjustments"):
        lines.append("\n⚙️ *Paramètres ajustés :*")
        for adj in report["adjustments"]:
            lines.append(f"• `{adj['param']}` → `{adj['new_value']}`\n  _{adj['reason']}_")
    else:
        lines.append("\n✅ Aucun ajustement nécessaire")

    send_status("\n".join(lines))
