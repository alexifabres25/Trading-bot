"""
Commandes Telegram — le bot répond aux messages de l'utilisateur.

Commandes disponibles :
  /journal  → 10 derniers trades avec PnL
  /status   → positions ouvertes + équité actuelle
  /help     → liste des commandes
"""

import logging
import threading
import time

import requests

import config
from notifications.telegram_bot import send_message

logger = logging.getLogger(__name__)

_BASE_URL = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"
_offset = 0
_running = False


def _get_updates() -> list:
    global _offset
    try:
        resp = requests.get(
            f"{_BASE_URL}/getUpdates",
            params={"offset": _offset, "timeout": 10, "allowed_updates": ["message"]},
            timeout=15,
        )
        updates = resp.json().get("result", [])
        if updates:
            _offset = updates[-1]["update_id"] + 1
        return updates
    except Exception as exc:
        logger.debug(f"[Telegram cmd] getUpdates : {exc}")
        return []


def _handle_journal():
    from learning.journal import load_journal
    journal = load_journal()
    closed = [t for t in journal if t.get("status") == "closed"]

    if not closed:
        return "Aucun trade fermé pour l'instant."

    recent = closed[-10:][::-1]
    lines = [f"📋 *10 derniers trades*\n"]
    for t in recent:
        icon = "✅" if t.get("outcome") == "win" else "❌"
        pnl = t.get("pnl_net_usdt", 0)
        pnl_pct = t.get("pnl_net_pct", 0)
        date = t.get("exit_time", "")[:10]
        reason = t.get("exit_reason", "?")
        lines.append(
            f"{icon} *{t['symbol']}* — {date}\n"
            f"   Entrée `{t['entry_price']:.2f}` → Sortie `{t['exit_price']:.2f}`\n"
            f"   PnL net : `{pnl:+.2f} USDT` ({pnl_pct:+.2f}%) — _{reason}_\n"
        )

    wins = [t for t in closed if t.get("outcome") == "win"]
    win_rate = len(wins) / len(closed) * 100 if closed else 0
    total_pnl = sum(t.get("pnl_net_usdt", 0) for t in closed)
    lines.append(
        f"\n📊 *Total* : {len(closed)} trades | "
        f"Win rate `{win_rate:.0f}%` | PnL net `{total_pnl:+.2f} USDT`"
    )
    return "\n".join(lines)


def _handle_status(positions: dict, client=None):
    from risk.manager import _load_equity_state, get_current_drawdown
    equity = _load_equity_state()
    current = equity.get("current_equity", config.CAPITAL)
    peak = equity.get("peak_equity", config.CAPITAL)
    dd = get_current_drawdown()

    lines = [f"🤖 *Statut — {config.BOT_NAME}*\n"]

    if not positions:
        lines.append("Positions : _aucune position ouverte_\n")
    else:
        for symbol, pos in positions.items():
            entry = pos["entry_price"]
            sl = pos["stop_loss"]
            tp = pos.get("take_profit", 0)
            qty = pos["amount"]
            try:
                price = client.get_current_price(symbol) if client else entry
                pnl_pct = (price / entry - 1) * 100
                lines.append(
                    f"📈 *{symbol}* — Long\n"
                    f"   Entrée `{entry:.2f}` | Prix `{price:.2f}` ({pnl_pct:+.2f}%)\n"
                    f"   SL `{sl:.2f}` | TP `{tp:.2f}` | Qté `{qty:.6f}`\n"
                )
            except Exception:
                lines.append(f"📈 *{symbol}* — Long @ `{entry:.2f}`\n")

    lines.append(
        f"💰 Équité : `{current:.2f} USDT`"
        + (f" | DD : `{dd:.1%}`" if dd > 0 else " | Au pic ✅")
    )
    return "\n".join(lines)


def _handle_update(update: dict, positions: dict, client=None):
    msg = update.get("message", {})
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = msg.get("text", "").strip().lower()

    if chat_id != str(config.TELEGRAM_CHAT_ID):
        return

    if text in ("/journal", "/trades", "/history"):
        send_message(_handle_journal())
    elif text in ("/status", "/positions"):
        send_message(_handle_status(positions, client))
    elif text in ("/help", "/start"):
        send_message(
            f"🤖 *{config.BOT_NAME}* — Commandes disponibles\n\n"
            "/journal → 10 derniers trades avec PnL\n"
            "/status  → positions ouvertes + équité\n"
            "/help    → cette aide"
        )


def start_command_listener(positions: dict, client=None):
    """Lance l'écoute des commandes Telegram dans un thread séparé."""
    global _running

    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.info("[Telegram cmd] Non configuré — listener désactivé")
        return

    _running = True

    def _loop():
        logger.info("[Telegram cmd] Listener démarré")
        while _running:
            updates = _get_updates()
            for update in updates:
                try:
                    _handle_update(update, positions, client)
                except Exception as exc:
                    logger.warning(f"[Telegram cmd] Erreur traitement : {exc}")
            time.sleep(3)

    t = threading.Thread(target=_loop, daemon=True, name="telegram-cmd")
    t.start()


def stop_command_listener():
    global _running
    _running = False
