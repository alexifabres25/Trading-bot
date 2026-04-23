"""
Synchronisation portefeuille / positions au démarrage.

Si le bot a planté avec des positions ouvertes, state.json peut être désynchronisé
avec l'état réel de l'exchange. Ce module détecte et corrige ces écarts avant
que le bot ne reprenne le trading.

Scénarios couverts :
  - Position dans state.json mais actif absent sur l'exchange (vendu manuellement
    ou ordre exécuté pendant la panne) → suppression de l'état
  - Quantité différente entre état interne et solde réel (frais, arrondi partiel)
    → ajustement silencieux
  - state.json vide/absent après redémarrage Railway → détection depuis les soldes réels
  - Telegram alert sur tout écart > 5 %
"""

import logging
from datetime import datetime, timezone

from notifications.telegram_bot import send_status

logger = logging.getLogger(__name__)


def reconcile(client, positions: dict) -> dict:
    """
    Confronte state.json aux soldes réels de l'exchange.
    Retourne les positions corrigées.
    """
    if not positions:
        logger.info("[Sync] Aucune position ouverte à vérifier.")
        return positions

    try:
        balance = client.exchange.fetch_balance()
    except Exception as exc:
        logger.warning(
            f"[Sync] Impossible de récupérer les soldes ({exc}) — "
            "état interne conservé sans vérification."
        )
        return positions

    corrected = dict(positions)
    alerts: list[str] = []

    for symbol, pos in list(positions.items()):
        base = symbol.split("/")[0]
        free = float(balance.get("free", {}).get(base, 0))
        used = float(balance.get("used", {}).get(base, 0))
        actual_qty = free + used
        expected_qty = pos["amount"]

        if expected_qty == 0:
            continue

        discrepancy = abs(actual_qty - expected_qty) / expected_qty

        if discrepancy > 0.90:
            # Position quasi absente → probablement liquidée pendant la panne
            logger.error(
                f"[Sync] {symbol} introuvable sur l'exchange "
                f"(attendu={expected_qty:.6f}, réel={actual_qty:.6f}) "
                f"→ suppression de l'état interne."
            )
            alerts.append(
                f"❌ *{symbol}* : position absente sur l'exchange\n"
                f"_(attendu {expected_qty:.4f}, trouvé {actual_qty:.4f})_"
            )
            del corrected[symbol]

        elif discrepancy > 0.05:
            # Écart mineur → ajustement (frais déduits, vente partielle, arrondi)
            logger.warning(
                f"[Sync] {symbol} : ajustement quantité "
                f"{expected_qty:.6f} → {actual_qty:.6f} ({discrepancy:.1%})"
            )
            corrected[symbol] = dict(pos)
            corrected[symbol]["amount"] = round(actual_qty, 6)
            alerts.append(
                f"⚠️ *{symbol}* : quantité ajustée "
                f"`{expected_qty:.4f}` → `{actual_qty:.4f}`"
            )
        else:
            logger.info(
                f"[Sync] {symbol} ✅  qty={actual_qty:.6f}  "
                f"écart={discrepancy:.2%}"
            )

    if alerts:
        send_status("🔄 *Sync au démarrage*\n" + "\n".join(alerts))
    else:
        logger.info("[Sync] Toutes les positions sont cohérentes avec l'exchange.")

    return corrected


def recover_positions(client, known_pairs: list) -> dict:
    """
    Détecte les positions ouvertes directement depuis les soldes Binance.
    Appelé quand state.json est vide après un redémarrage Railway.
    Empêche le bot d'ouvrir un 2ème trade sur une paire déjà en position.
    """
    import config

    recovered = {}

    try:
        balance = client.exchange.fetch_balance()
    except Exception as exc:
        logger.warning(f"[Sync] Impossible de scanner les soldes : {exc}")
        return recovered

    for symbol in known_pairs:
        base = symbol.split("/")[0]
        free = float(balance.get("free", {}).get(base, 0))
        used = float(balance.get("used", {}).get(base, 0))
        qty = free + used

        if qty < 0.00001:
            continue

        try:
            price = client.get_current_price(symbol)
        except Exception:
            continue

        if qty * price < 10:
            continue

        stop_loss = round(price * (1 - config.STOP_LOSS_PCT), 8)
        take_profit = round(price * (1 + config.STOP_LOSS_PCT * config.TAKE_PROFIT_RATIO), 8)

        recovered[symbol] = {
            "side": "long",
            "entry_price": price,
            "amount": round(qty, 6),
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "dry_run": False,
            "trade_id": None,
            "recovered": True,
        }
        logger.warning(
            f"[Sync] Position récupérée : {symbol}  qty={qty:.6f}  "
            f"prix_approx={price:.4f}  stop={stop_loss:.4f}"
        )

    if recovered:
        lines = [f"  • *{s}* : `{p['amount']:.6f}` récupéré" for s, p in recovered.items()]
        send_status(
            "🔄 *Positions récupérées après redémarrage*\n"
            "_(state.json vide — soldes Binance utilisés)_\n"
            + "\n".join(lines)
        )

    return recovered
