"""
Crypto Trading Bot — point d'entrée principal.

Stratégie :
  - Signal d'entrée  : croisement EMA 9/21 + filtre RSI 14 sur 1h
  - Filtre tendance  : alignement EMA 9/21 sur 4h (n'achète pas contre la tendance)
  - Risk management  : stop-loss 2 %, taille de position 1 % du capital
  - Alertes          : Telegram

Lancement :
  python bot.py
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import config
from exchange.client import BinanceClient
from exchange.sync import reconcile
from learning.analyzer import analyze_and_adapt
from learning.journal import record_entry, record_exit
from risk.manager import update_equity, update_risk_multiplier
from notifications.telegram_bot import send_error, send_status, send_trade_alert
from learning.health import is_paused, maybe_send_daily_report, record_outcome
from risk.manager import calculate_position_size, calculate_stop_loss, update_trailing_stop
from strategy.indicators import get_indicator_context, get_supertrend_stop
from strategy.signal import BUY, HOLD, SELL, generate_1h_signal, get_4h_trend
from news.sentiment import should_block_buy

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"{config.STATE_FILE.replace('state', 'bot').replace('.json', '.log')}"),
    ],
)
logger = logging.getLogger(config.BOT_NAME)


# ── State (positions ouvertes) ─────────────────────────────────────────────────

def load_state() -> dict:
    path = Path(config.STATE_FILE)
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(config.STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


# ── Logique par paire ──────────────────────────────────────────────────────────

def process_pair(client: BinanceClient, symbol: str, positions: dict):
    """Analyse les signaux et gère la position pour une paire."""
    logger.info(f"[{symbol}] Analyse en cours...")

    df_1h = client.fetch_ohlcv(symbol, config.TIMEFRAME_SHORT, config.CANDLES_LIMIT)
    df_4h = client.fetch_ohlcv(symbol, config.TIMEFRAME_LONG, config.CANDLES_LIMIT)

    signal_1h = generate_1h_signal(df_1h)
    trend_4h = get_4h_trend(df_4h)
    price = client.get_current_price(symbol)

    logger.info(
        f"[{symbol}]  signal 1h={signal_1h:<4}  tendance 4h={trend_4h:<7}  prix={price:.4f}"
    )

    position = positions.get(symbol)

    # ── 1. Mettre à jour le stop (Supertrend adaptatif en priorité) ───────────
    if position:
        # Supertrend : stop basé sur la volatilité réelle (ATR) — s'adapte au marché
        st_stop = get_supertrend_stop(df_1h)
        if st_stop and st_stop > position["stop_loss"]:
            logger.info(
                f"[{symbol}] Supertrend stop : {position['stop_loss']:.4f} "
                f"→ {st_stop:.4f}"
            )
            position["stop_loss"] = round(st_stop, 8)
        elif config.TRAILING_STOP:
            # Fallback : trailing stop classique si Supertrend indisponible
            max_price, new_stop = update_trailing_stop(price, position)
            if new_stop > position["stop_loss"]:
                logger.info(
                    f"[{symbol}] Trailing stop : {position['stop_loss']:.4f} "
                    f"→ {new_stop:.4f}"
                )
                position["max_price"] = max_price
                position["stop_loss"] = new_stop

    # ── 2. Vérifier le stop-loss (fixe ou trailing) ───────────────────────────
    if position and price <= position["stop_loss"]:
        logger.info(
            f"[{symbol}] STOP-LOSS déclenché  prix={price:.4f}  stop={position['stop_loss']:.4f}"
        )
        _close_position(client, symbol, position, price, positions, reason="stop-loss")
        return

    # ── 3. Fermer la position sur signal SELL ou retournement 4h ──────────────
    if position and (signal_1h == SELL or trend_4h == "bear"):
        reason = f"signal={signal_1h}" if signal_1h == SELL else "tendance 4h baissière"
        logger.info(f"[{symbol}] Fermeture position ({reason})")
        _close_position(client, symbol, position, price, positions, reason=reason)
        return

    # ── 4. Ouvrir une position sur signal BUY confirmé par la tendance 4h ─────
    # On entre si la tendance 4h est haussière ou neutre (on évite d'acheter en bear)
    if not position and signal_1h == BUY and trend_4h != "bear":
        # Filtre sentiment : Fear & Greed + news CryptoPanic
        blocked, reason = should_block_buy(symbol)
        if blocked:
            logger.info(f"[{symbol}] BUY bloqué par le sentiment — {reason}")
            return
        logger.info(f"[{symbol}] Signal BUY confirmé (tendance 4h={trend_4h})")
        ctx = get_indicator_context(df_1h, config.EMA_FAST, config.EMA_SLOW, config.RSI_PERIOD)
        _open_position(client, symbol, price, positions, trend_4h, ctx)
        return

    logger.info(f"[{symbol}] Aucune action — position={'ouverte' if position else 'fermée'}")


def _open_position(
    client: BinanceClient,
    symbol: str,
    price: float,
    positions: dict,
    trend_4h: str,
    indicator_ctx: dict,
):
    try:
        usdt_balance = client.get_usdt_balance()
        qty = calculate_position_size(price, usdt_balance)
        notional = qty * price

        if notional < 10:
            logger.warning(
                f"[{symbol}] Solde insuffisant pour un ordre minimum "
                f"(valeur estimée={notional:.2f} USDT < 10 USDT)"
            )
            return

        # Choix du type d'ordre selon config (limit réduit les frais, market = sécurité)
        if config.ORDER_TYPE == "limit":
            order = client.place_limit_buy(symbol, qty, price)
        else:
            order = client.place_market_buy(symbol, qty, expected_price=price)

        filled_price = float(order.get("average") or order.get("price") or price)
        slippage_pct = order.get("slippage_pct", 0.0)
        stop_loss = calculate_stop_loss(filled_price)

        # Enregistre dans le journal avec contexte complet + slippage réel
        trade_id = record_entry(
            symbol=symbol,
            entry_price=filled_price,
            amount=qty,
            stop_loss=stop_loss,
            rsi=indicator_ctx.get("rsi", 50.0),
            ema_spread_pct=indicator_ctx.get("ema_spread_pct", 0.0),
            trend_4h=trend_4h,
            slippage_pct=slippage_pct,
        )

        positions[symbol] = {
            "side": "long",
            "entry_price": filled_price,
            "amount": qty,
            "stop_loss": stop_loss,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "dry_run": config.DRY_RUN,
            "trade_id": trade_id,
        }

        send_trade_alert(
            "BUY (simulation)" if config.DRY_RUN else "BUY",
            symbol, filled_price, qty, stop_loss,
            bot_name=config.BOT_NAME,
        )
        logger.info(
            f"[{symbol}] Position ouverte  entrée={filled_price:.4f}  "
            f"stop={stop_loss:.4f}  qty={qty:.6f}"
        )
    except Exception as exc:
        logger.error(f"[{symbol}] Erreur ouverture position : {exc}", exc_info=True)
        send_error(f"Impossible d'acheter {symbol} : {exc}")


def _close_position(
    client: BinanceClient,
    symbol: str,
    position: dict,
    price: float,
    positions: dict,
    reason: str,
):
    try:
        client.place_market_sell(symbol, position["amount"])
        pnl = (price - position["entry_price"]) * position["amount"]
        pnl_pct = (price / position["entry_price"] - 1) * 100

        # Enregistre la sortie dans le journal
        if trade_id := position.get("trade_id"):
            record_exit(trade_id, price, reason)

        send_trade_alert(
            "SELL (simulation)" if config.DRY_RUN else "SELL",
            symbol, price, position["amount"],
            bot_name=config.BOT_NAME,
        )
        send_status(
            f"*{config.BOT_NAME}* — Position fermée sur *{symbol}* ({reason})\n"
            f"PnL estimé : `{pnl:+.2f} USDT` ({pnl_pct:+.2f} %)"
        )
        logger.info(
            f"[{symbol}] Position fermée  pnl={pnl:+.4f} USDT ({pnl_pct:+.2f} %)"
        )
        del positions[symbol]

        # 1. Multiplicateur progressif (-10%/perte, +5%/gain)
        outcome = "win" if pnl > 0 else "loss"
        update_risk_multiplier(outcome)

        # 2. Health monitor (pertes consécutives, drawdown journalier)
        record_outcome(pnl)

        # 3. Analyse post-trade : apprentissage et adaptation des paramètres
        analyze_and_adapt(symbol)

    except Exception as exc:
        logger.error(f"[{symbol}] Erreur fermeture position : {exc}", exc_info=True)
        send_error(f"Impossible de vendre {symbol} : {exc}")


# ── Boucle principale ──────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info(f"{config.BOT_NAME} — démarrage")
    mode = "DRY RUN" if config.DRY_RUN else ("TESTNET" if config.USE_TESTNET else "*** LIVE ***")
    logger.info(f"Mode    : {mode}")
    logger.info(f"Paires  : {', '.join(config.TRADING_PAIRS)}")
    logger.info(f"Capital : {config.CAPITAL} USDT  |  Risque/trade : {config.RISK_PER_TRADE*100:.0f} %  |  SL : {config.STOP_LOSS_PCT*100:.1f} %")
    logger.info("=" * 60)

    client = BinanceClient()
    send_status(
        f"*{config.BOT_NAME}* démarré — mode *{mode}*\n"
        f"Paires   : {', '.join(config.TRADING_PAIRS)}\n"
        f"Capital  : {config.CAPITAL} USDT\n"
        f"Risque   : {config.RISK_PER_TRADE*100:.0f} % / trade  |  SL : {config.STOP_LOSS_PCT*100:.1f} %"
    )

    positions = load_state()
    if positions:
        logger.info(f"{len(positions)} position(s) rechargée(s) depuis {config.STATE_FILE}")
        # Synchronisation portefeuille : vérifie que l'état correspond à l'exchange réel
        positions = reconcile(client, positions)
        save_state(positions)

    while True:
        try:
            # Rapport journalier automatique (Telegram, heure configurée)
            maybe_send_daily_report()

            # Vérification du Health Monitor — pause/reprise autonome
            if is_paused():
                logger.info(
                    f"[Health] Trading suspendu — "
                    f"prochaine vérification dans {config.LOOP_INTERVAL}s"
                )
            else:
                # Mise à jour équité (RM_EquityPercent + DD scaling)
                current_balance = client.get_usdt_balance()
                update_equity(current_balance)

                for symbol in config.TRADING_PAIRS:
                    process_pair(client, symbol, positions)
                save_state(positions)

        except KeyboardInterrupt:
            logger.info("Arrêt demandé par l'utilisateur.")
            send_status("Bot arrêté manuellement.")
            break
        except Exception as exc:
            logger.error(f"Erreur inattendue dans la boucle principale : {exc}", exc_info=True)
            send_error(f"Erreur critique : {exc}")

        logger.info(f"Pause de {config.LOOP_INTERVAL}s avant la prochaine analyse...")
        time.sleep(config.LOOP_INTERVAL)


if __name__ == "__main__":
    main()
