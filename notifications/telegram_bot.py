import logging

import requests

import config

logger = logging.getLogger(__name__)

_BASE_URL = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"


def send_message(text: str) -> bool:
    """Envoie un message Markdown au chat configuré."""
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.debug("Telegram non configuré — notification ignorée.")
        return False
    try:
        resp = requests.post(
            f"{_BASE_URL}/sendMessage",
            json={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning(f"Échec envoi Telegram : {exc}")
        return False


def send_trade_alert(action: str, pair: str, price: float, qty: float, stop_loss: float | None = None):
    """Alerte d'ordre (achat ou vente)."""
    icon = "🟢" if "BUY" in action else "🔴"
    lines = [
        f"{icon} *{action} — {pair}*",
        f"Prix       : `{price:.4f} USDT`",
        f"Quantité   : `{qty:.6f}`",
        f"Valeur     : `{price * qty:.2f} USDT`",
    ]
    if stop_loss is not None:
        lines.append(f"Stop-Loss  : `{stop_loss:.4f} USDT`")
    send_message("\n".join(lines))


def send_status(message: str):
    send_message(f"ℹ️ {message}")


def send_error(message: str):
    send_message(f"⚠️ *ERREUR* : {message}")
