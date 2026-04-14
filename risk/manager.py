import config


def calculate_stop_loss(entry_price: float) -> float:
    """Stop-loss initial à STOP_LOSS_PCT % sous le prix d'entrée."""
    return round(entry_price * (1 - config.STOP_LOSS_PCT), 8)


def update_trailing_stop(current_price: float, position: dict) -> tuple[float, float]:
    """
    Recalcule le trailing stop en fonction du plus haut atteint depuis l'entrée.

    Le stop monte avec le prix mais ne redescend jamais.
    Retourne (nouveau_max_price, nouveau_stop).

    Exemple : entrée 60 000, trail 2%
        prix → 65 000  :  stop = 65 000 × 0.98 = 63 700  ✓ (monte)
        prix → 63 000  :  stop reste 63 700               ✓ (ne descend pas)
    """
    max_price = max(position.get("max_price", position["entry_price"]), current_price)
    trailing_stop = round(max_price * (1 - config.TRAILING_STOP_PCT), 8)
    # Ne jamais descendre sous le stop initial
    new_stop = max(trailing_stop, position["stop_loss"])
    return max_price, new_stop


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
