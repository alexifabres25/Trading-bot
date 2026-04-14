"""
Journal de trades — enregistre chaque position avec son contexte complet.

Chaque entrée contient :
  - Les valeurs des indicateurs au moment de l'entrée (RSI, spread EMA, tendance 4h)
  - Le résultat final (PnL, durée, raison de sortie)
  - La classification de l'erreur si trade perdant

Cela permet à l'analyzer de détecter des patterns répétés et d'ajuster les params.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
JOURNAL_FILE = "journal.json"


def load_journal() -> list[dict]:
    path = Path(JOURNAL_FILE)
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


def save_journal(entries: list[dict]):
    with open(JOURNAL_FILE, "w") as f:
        json.dump(entries, f, indent=2, default=str)


def record_entry(
    symbol: str,
    entry_price: float,
    amount: float,
    stop_loss: float,
    rsi: float,
    ema_spread_pct: float,
    trend_4h: str,
) -> str:
    """
    Enregistre l'ouverture d'une position.
    Retourne le trade_id unique pour le retrouver à la fermeture.
    """
    trade_id = (
        f"{symbol.replace('/', '_')}_"
        f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )
    entry = {
        "trade_id": trade_id,
        "symbol": symbol,
        "status": "open",
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "entry_price": entry_price,
        "amount": amount,
        "stop_loss_initial": stop_loss,
        # Contexte indicateurs — clé pour l'analyse post-trade
        "entry_rsi": round(rsi, 2),
        "entry_ema_spread_pct": round(ema_spread_pct, 4),
        "entry_4h_trend": trend_4h,
    }
    journal = load_journal()
    journal.append(entry)
    save_journal(journal)
    logger.info(f"[Journal] Ouverture enregistrée : {trade_id}")
    return trade_id


def record_exit(
    trade_id: str,
    exit_price: float,
    exit_reason: str,
):
    """Met à jour le journal à la fermeture d'une position."""
    journal = load_journal()
    for trade in journal:
        if trade["trade_id"] != trade_id:
            continue

        entry_dt = datetime.fromisoformat(trade["entry_time"])
        now = datetime.now(timezone.utc)
        duration_h = (now - entry_dt).total_seconds() / 3600

        pnl_usdt = (exit_price - trade["entry_price"]) * trade["amount"]
        pnl_pct = (exit_price / trade["entry_price"] - 1) * 100

        trade.update({
            "status": "closed",
            "exit_time": now.isoformat(),
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl_usdt": round(pnl_usdt, 4),
            "pnl_pct": round(pnl_pct, 4),
            "duration_hours": round(duration_h, 2),
            "outcome": "win" if pnl_usdt > 0 else "loss",
        })
        logger.info(
            f"[Journal] Fermeture enregistrée : {trade_id}  "
            f"PnL={pnl_usdt:+.4f} USDT ({pnl_pct:+.2f}%)  raison={exit_reason}"
        )
        break

    save_journal(journal)
