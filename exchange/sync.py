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
  - Telegram alert sur tout écart > 5 %
"""

import logging

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
