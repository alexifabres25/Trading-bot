"""
Health Monitor — autonomie totale du bot.

Surveille en permanence :
  - Pertes consécutives   → pause automatique après N pertes d'affilée
  - Drawdown journalier   → pause si le compte perd trop en une journée
  - Reprise automatique   → après PAUSE_DURATION_HOURS heures, sans intervention

Rapport journalier automatique envoyé sur Telegram à DAILY_REPORT_HOUR (UTC).

Aucune décision humaine requise — le bot gère son propre état de santé.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from notifications.telegram_bot import send_status

logger = logging.getLogger(__name__)


def _health_path():
    import config
    from pathlib import Path
    return Path(config.HEALTH_FILE)


# ── Persistance ────────────────────────────────────────────────────────────────

def _load() -> dict:
    path = _health_path()
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {
        "consecutive_losses": 0,
        "daily_pnl": 0.0,
        "last_daily_reset": datetime.now(timezone.utc).date().isoformat(),
        "last_report_date": None,
        "paused_until": None,
    }


def _save(state: dict):
    path = _health_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, default=str)


# ── Vérification de pause ──────────────────────────────────────────────────────

def is_paused() -> bool:
    """
    Retourne True si le trading est suspendu.
    Gère la reprise automatique si le délai est écoulé.
    """
    state = _load()
    paused_until = state.get("paused_until")

    if not paused_until:
        return False

    resume_dt = datetime.fromisoformat(paused_until)
    now = datetime.now(timezone.utc)

    if now >= resume_dt:
        state["paused_until"] = None
        state["consecutive_losses"] = 0
        state["daily_pnl"] = 0.0
        _save(state)
        logger.info("[Health] ▶ Reprise automatique du trading")
        send_status(
            "▶ *Trading repris automatiquement*\n"
            "_Période de pause écoulée — nouvelle session_"
        )
        return False

    remaining_h = (resume_dt - now).total_seconds() / 3600
    logger.info(f"[Health] ⏸ Trading suspendu — reprise dans {remaining_h:.1f}h")
    return True


# ── Enregistrement post-trade ──────────────────────────────────────────────────

def record_outcome(pnl_usdt: float):
    """
    Appelé après chaque trade fermé.
    Met à jour les compteurs et déclenche une pause si nécessaire.
    """
    state = _load()

    # Reset journalier automatique
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("last_daily_reset") != today:
        state["daily_pnl"] = 0.0
        state["consecutive_losses"] = 0
        state["last_daily_reset"] = today

    state["daily_pnl"] = round(state.get("daily_pnl", 0.0) + pnl_usdt, 4)

    if pnl_usdt < 0:
        state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
    else:
        state["consecutive_losses"] = 0  # reset sur un gain

    _save(state)
    _check_pause(state)


def _check_pause(state: dict):
    """Évalue si les conditions de pause sont atteintes et suspend si nécessaire."""
    from risk.manager import _load_equity_state

    cons = state.get("consecutive_losses", 0)
    daily_pnl = state.get("daily_pnl", 0.0)
    equity = _load_equity_state().get("current_equity", config.CAPITAL)
    daily_dd = abs(daily_pnl) / equity if daily_pnl < 0 else 0.0

    reason = None
    if cons >= config.MAX_CONSECUTIVE_LOSSES:
        reason = (
            f"{cons} pertes consécutives "
            f"(seuil : {config.MAX_CONSECUTIVE_LOSSES})"
        )
    elif daily_dd >= config.MAX_DAILY_DD_PCT:
        reason = (
            f"Drawdown journalier {daily_dd:.1%} "
            f"(seuil : {config.MAX_DAILY_DD_PCT:.0%})"
        )

    if not reason:
        return

    resume_at = (
        datetime.now(timezone.utc) + timedelta(hours=config.PAUSE_DURATION_HOURS)
    ).isoformat()

    state["paused_until"] = resume_at
    _save(state)

    logger.warning(f"[Health] ⏸ Pause automatique — {reason}")
    send_status(
        f"⏸ *Trading suspendu automatiquement*\n"
        f"Raison : _{reason}_\n"
        f"Reprise autonome dans *{config.PAUSE_DURATION_HOURS}h* "
        f"(aucune action requise)"
    )


# ── Rapport journalier automatique ─────────────────────────────────────────────

def maybe_send_daily_report():
    """
    Envoie le rapport journalier si l'heure configurée est atteinte
    et qu'il n'a pas encore été envoyé aujourd'hui.
    """
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    if now.hour < config.DAILY_REPORT_HOUR:
        return

    state = _load()
    if state.get("last_report_date") == today:
        return  # déjà envoyé aujourd'hui

    _send_report(state, today)
    state["last_report_date"] = today
    _save(state)


def _send_report(state: dict, today: str):
    from learning.journal import load_journal
    from risk.manager import _load_equity_state, get_current_drawdown
    from news.sentiment import get_sentiment_summary

    equity_state = _load_equity_state()
    current_equity = equity_state.get("current_equity", config.CAPITAL)
    peak = equity_state.get("peak_equity", config.CAPITAL)
    dd = get_current_drawdown()

    journal = load_journal()
    today_trades = [
        t for t in journal
        if t.get("status") == "closed" and t.get("exit_time", "")[:10] == today
    ]
    all_closed = [t for t in journal if t.get("status") == "closed"]

    wins_today = [t for t in today_trades if t.get("outcome") == "win"]
    losses_today = [t for t in today_trades if t.get("outcome") == "loss"]
    daily_pnl = state.get("daily_pnl", 0.0)

    total_wins = sum(1 for t in all_closed if t.get("outcome") == "win")
    win_rate = total_wins / len(all_closed) * 100 if all_closed else 0

    lines = [
        f"📊 *Rapport journalier — {today}*",
        f"",
        f"Trades du jour : `{len(today_trades)}`  "
        f"({len(wins_today)}✅ / {len(losses_today)}❌)",
        f"PnL du jour    : `{daily_pnl:+.2f} USDT`",
        f"",
        f"Équité actuelle: `{current_equity:.2f} USDT`",
        f"Depuis le pic  : `{current_equity - peak:+.2f} USDT` ({-dd:.1%})" if dd > 0
        else f"Pic historique : ✅ `{peak:.2f} USDT`",
        f"",
        f"Win rate global: `{win_rate:.0f}%` sur {len(all_closed)} trades",
        f"",
        get_sentiment_summary(),
    ]

    if state.get("paused_until"):
        lines.append(f"\n⏸ _Trading actuellement suspendu_")

    send_status("\n".join(lines))
