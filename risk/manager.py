import config


def calculate_stop_loss(entry_price: float) -> float:
    """Stop-loss à STOP_LOSS_PCT % sous le prix d'entrée (position longue)."""
    return round(entry_price * (1 - config.STOP_LOSS_PCT), 8)


def calculate_position_size(entry_price: float, available_usdt: float) -> float:
    """
    Taille de position basée sur le risque de 1 % du capital.

    Formule :
        risque_usdt  = CAPITAL × RISK_PER_TRADE
        distance_sl  = entry_price × STOP_LOSS_PCT
        quantité     = risque_usdt / distance_sl

    Exemple avec BTC à 60 000 USDT, capital 400 USDT :
        risque_usdt = 400 × 0.01 = 4 USDT
        distance_sl = 60 000 × 0.02 = 1 200 USDT
        quantité    = 4 / 1 200 ≈ 0.00333 BTC  (valeur ≈ 200 USDT, perte max 4 USDT)
    """
    risk_usdt = config.CAPITAL * config.RISK_PER_TRADE
    stop_distance = entry_price * config.STOP_LOSS_PCT
    qty = risk_usdt / stop_distance

    # Ne pas dépasser le solde disponible (marge de 1 % pour les frais)
    max_qty = (available_usdt / entry_price) * 0.99
    qty = min(qty, max_qty)

    return round(qty, 6)
