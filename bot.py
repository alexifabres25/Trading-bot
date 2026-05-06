"""
Crypto Trading Bot — point d'entrée principal.

Stratégie :
  - Signal d'entrée  : croisement EMA 9/21 + filtre RSI 14 + ADX sur 1h
  - Filtre tendance  : EMA 9/21 sur 4h + EMA 200 sur weekly (macro)
  - Take Profit      : 2× le risque (ratio 2:1 risque/récompense)
  - Filtre ATR       : bloque les entrées en volatilité extrême
  - Filtre sentiment : Fear & Greed + news CryptoPanic
  - Risk management  : stop-loss 2 %, Kelly, DD scaling, RM_EquityPercent
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
from exchange.sync import reconcile, recover_positions
from learning.analyzer import analyze_and_adapt
from learning.journal import record_entry, record_exit
from risk.manager import update_equity, update_risk_multiplier
from notifications.telegram_bot import send_error, send_status, send_trade_alert
from learning.health import is_paused, maybe_send_daily_report, record_outcome
from risk.manager import calculate_position_size, calculate_stop_loss, update_trailing_stop
from strategy.indicators import (
    add_indicators, get_indicator_context, get_supertrend_stop, is_volatility_extreme,
)
from strategy.signal import BUY, HOLD, SELL, generate_1h_signal, get_4h_trend, get_weekly_trend
from news.sentiment import get_fear_greed, should_block_buy
from notifications.telegram_commands import start_command_listener, stop_command_listener

# Rate-limit pour les notifications de diagnostic (1 par heure par symbole)
_last_scan_ts: dict[str, float] = {}
_last_block_ts: dict[str, float] = {}
_NOTIF_COOLDOWN = 3600.0  # 1 heure

# ── Logging ────────────────────────────────────────────────────────────────────
_log_handlers = [logging.StreamHandler()]
try:
    _log_file = config.STATE_FILE.replace("state", "bot").replace(".json", ".log")
    from pathlib import Path as _Path
    _Path(_log_file).parent.mkdir(parents=True, exist_ok=True)
    _log_handlers.append(logging.FileHandler(_log_file))
except Exception:
    pass  # Railway : logs stdout suffisent si le fichier est inaccessible

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger(config.BOT_NAME)


# ── State (positions ouvertes) ─────────────────────────────────────────────────

_STATE_KEY = "trading:state"


def load_state() -> dict:
    from storage.store import load
    return load(_STATE_KEY, config.STATE_FILE, default={})


def save_state(state: dict):
    from storage.store import save
    save(_STATE_KEY, config.STATE_FILE, state)


# ── Calcul du Take Profit ──────────────────────────────────────────────────────

def _calculate_take_profit(entry_price: float) -> float:
    """TP = entrée + 2 × distance du stop (ratio 2:1 par défaut)."""
    stop_distance = entry_price * config.STOP_LOSS_PCT
    return round(entry_price + config.TAKE_PROFIT_RATIO * stop_distance, 8)


# ── Diagnostic Telegram ────────────────────────────────────────────────────────

def _send_market_scan(
    symbol: str, price: float, signal_1h: str,
    trend_4h: str, trend_weekly: str, df_1h,
):
    """Rapport horaire envoyé sur Telegram — montre l'état de chaque filtre."""
    now = time.monotonic()
    if now - _last_scan_ts.get(symbol, 0) < _NOTIF_COOLDOWN:
        return
    _last_scan_ts[symbol] = now

    try:
        df = add_indicators(df_1h, config.EMA_FAST, config.EMA_SLOW, config.RSI_PERIOD)
        df = df.dropna()
        if df.empty:
            return
        rsi = float(df["rsi"].iloc[-1])
        adx = float(df["adx"].iloc[-1]) if "adx" in df.columns else 0.0
        ema_f = float(df[f"ema_{config.EMA_FAST}"].iloc[-1])
        ema_s = float(df[f"ema_{config.EMA_SLOW}"].iloc[-1])
        spread = (ema_f - ema_s) / ema_s * 100

        fg = get_fear_greed()
        fg_txt = f"{fg['value']}/100 ({fg['label']})"

        blockers = []
        if signal_1h != BUY:
            blockers.append(f"Signal 1h = `{signal_1h}` (pas de croisement EMA)")
        if trend_4h == "bear":
            blockers.append("Tendance 4h `bear`")
        if trend_weekly != "bull":
            blockers.append("Weekly EMA 200 `bear` ⛔ (bloque tous les achats)")
        if adx < config.ADX_TREND_MIN:
            blockers.append(f"ADX `{adx:.1f}` < {config.ADX_TREND_MIN} (marché en range)")
        if fg["signal"] == "block":
            blockers.append(f"Fear & Greed `{fg_txt}` (extrême)")

        status_icon = "🟢" if not blockers else "🔴"
        block_lines = "\n".join(f"  • {b}" for b in blockers) if blockers else "  • aucun blocage"

        send_status(
            f"📡 *Scan {symbol}* _(rapport horaire)_\n"
            f"Prix      : `{price:.4f} USDT`\n"
            f"Signal 1h : `{signal_1h}`  |  4h : `{trend_4h}`  |  Weekly : `{trend_weekly}`\n"
            f"RSI       : `{rsi:.1f}`  |  ADX : `{adx:.1f}`  |  EMA spread : `{spread:+.3f}%`\n"
            f"F&G Index : `{fg_txt}`\n"
            f"{status_icon} *Statut* : {'Signal prêt' if not blockers else 'En attente'}\n"
            f"*Blocages* :\n{block_lines}"
        )
    except Exception as exc:
        logger.warning(f"[{symbol}] Erreur scan diagnostic : {exc}")


def _notify_buy_blocked(symbol: str, price: float, blockers: list[str]):
    """Telegram une fois par heure quand un BUY signal est actif mais bloqué."""
    now = time.monotonic()
    if now - _last_block_ts.get(symbol, 0) < _NOTIF_COOLDOWN:
        return
    _last_block_ts[symbol] = now

    block_lines = "\n".join(f"  • {b}" for b in blockers)
    send_status(
        f"⚠️ *{symbol}* — Signal BUY actif mais bloqué\n"
        f"Prix : `{price:.4f} USDT`\n"
        f"*Raisons* :\n{block_lines}"
    )


# ── Logique par paire ──────────────────────────────────────────────────────────

def process_pair(client: BinanceClient, symbol: str, positions: dict):
    """Analyse les signaux et gère la position pour une paire."""
    logger.info(f"[{symbol}] Analyse en cours...")

    df_1h = client.fetch_ohlcv(symbol, config.TIMEFRAME_SHORT, config.CANDLES_LIMIT)
    df_4h = client.fetch_ohlcv(symbol, config.TIMEFRAME_LONG, config.CANDLES_LIMIT)

    signal_1h = generate_1h_signal(df_1h)
    trend_4h  = get_4h_trend(df_4h)
    price     = client.get_current_price(symbol)

    if config.WEEKLY_TREND_FILTER:
        df_weekly    = client.fetch_ohlcv(symbol, config.TIMEFRAME_WEEKLY, config.CANDLES_WEEKLY)
        trend_weekly = get_weekly_trend(df_weekly)
    else:
        df_weekly    = None
        trend_weekly = "bull"  # filtre désactivé → on considère toujours haussier

    logger.info(
        f"[{symbol}]  signal 1h={signal_1h:<4}  "
        f"tendance 4h={trend_4h:<7}  weekly={trend_weekly:<4}  prix={price:.4f}"
    )

    # Rapport horaire envoyé sur Telegram (visibilité sans accès aux logs Railway)
    _send_market_scan(symbol, price, signal_1h, trend_4h, trend_weekly, df_1h)

    position = positions.get(symbol)

    # ── 1. Mettre à jour le stop (Supertrend adaptatif en priorité) ───────────
    if position:
        st_stop = get_supertrend_stop(df_1h)
        if st_stop and st_stop > position["stop_loss"]:
            logger.info(
                f"[{symbol}] Supertrend stop : {position['stop_loss']:.4f} → {st_stop:.4f}"
            )
            position["stop_loss"] = round(st_stop, 8)
        elif config.TRAILING_STOP:
            max_price, new_stop = update_trailing_stop(price, position)
            if new_stop > position["stop_loss"]:
                logger.info(
                    f"[{symbol}] Trailing stop : {position['stop_loss']:.4f} → {new_stop:.4f}"
                )
                position["max_price"] = max_price
                position["stop_loss"] = new_stop

    # ── 2. Vérifier le stop-loss ──────────────────────────────────────────────
    if position and price <= position["stop_loss"]:
        logger.info(
            f"[{symbol}] STOP-LOSS déclenché  prix={price:.4f}  stop={position['stop_loss']:.4f}"
        )
        _close_position(client, symbol, position, price, positions, reason="stop-loss")
        return

    # ── 3. Vérifier le take profit ────────────────────────────────────────────
    if position and config.TAKE_PROFIT_ENABLED:
        tp = position.get("take_profit")
        if tp and price >= tp:
            logger.info(
                f"[{symbol}] TAKE-PROFIT atteint  prix={price:.4f}  tp={tp:.4f}"
            )
            _close_position(client, symbol, position, price, positions, reason="take-profit")
            return

    # ── 4. Fermer sur signal SELL, retournement 4h ou retournement weekly ─────
    if position and (signal_1h == SELL or trend_4h == "bear" or trend_weekly == "bear"):
        if trend_weekly == "bear":
            reason = "tendance weekly baissière (EMA 200)"
        elif signal_1h == SELL:
            reason = f"signal={signal_1h}"
        else:
            reason = "tendance 4h baissière"
        logger.info(f"[{symbol}] Fermeture position ({reason})")
        _close_position(client, symbol, position, price, positions, reason=reason)
        return

    # ── 5. Ouvrir une position — tous les filtres doivent être verts ──────────
    if not position and signal_1h == BUY:
        # Collecte tous les blocages pour diagnostic transparent
        blockers = []
        if trend_4h == "bear":
            blockers.append("Tendance 4h `bear` — EMA 9/21 bearish sur 4h")
        if trend_weekly != "bull":
            blockers.append(
                f"Weekly EMA 200 `{trend_weekly}` — prix sous EMA 200 hebdo ⛔"
            )
        if config.ATR_FILTER_ENABLED and is_volatility_extreme(df_1h):
            blockers.append("Volatilité ATR extrême — bougie anormale détectée")
        if not blockers:
            blocked_sentiment, reason_sentiment = should_block_buy(symbol)
            if blocked_sentiment:
                blockers.append(f"Sentiment : {reason_sentiment}")

        if blockers:
            logger.info(f"[{symbol}] BUY bloqué — {' | '.join(blockers)}")
            _notify_buy_blocked(symbol, price, blockers)
            return

        logger.info(
            f"[{symbol}] Signal BUY confirmé "
            f"(4h={trend_4h}, weekly={trend_weekly})"
        )
        ctx = get_indicator_context(df_1h, config.EMA_FAST, config.EMA_SLOW, config.RSI_PERIOD)
        _open_position(client, symbol, price, positions, trend_4h, ctx)
        return

    logger.info(f"[{symbol}] Aucune action — signal={signal_1h}  position={'ouverte' if position else 'fermée'}")


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
                f"[{symbol}] Solde insuffisant (valeur estimée={notional:.2f} USDT < 10 USDT)"
            )
            return

        if config.ORDER_TYPE == "limit":
            order = client.place_limit_buy(symbol, qty, price)
        else:
            order = client.place_market_buy(symbol, qty, expected_price=price)

        filled_price = float(order.get("average") or order.get("price") or price)
        slippage_pct = order.get("slippage_pct", 0.0)
        stop_loss    = calculate_stop_loss(filled_price)
        take_profit  = _calculate_take_profit(filled_price)

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
            "take_profit": take_profit,
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
            f"stop={stop_loss:.4f}  tp={take_profit:.4f}  qty={qty:.6f}"
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
            f"[{symbol}] Position fermée  pnl={pnl:+.4f} USDT ({pnl_pct:+.2f} %)  raison={reason}"
        )
        del positions[symbol]

        outcome = "win" if pnl > 0 else "loss"
        update_risk_multiplier(outcome)
        record_outcome(pnl)
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
    logger.info(
        f"Capital : {config.CAPITAL} USDT  |  "
        f"Risque/trade : {config.RISK_PER_TRADE*100:.0f}%  |  "
        f"SL : {config.STOP_LOSS_PCT*100:.1f}%  |  "
        f"TP : {config.TAKE_PROFIT_RATIO}:1"
    )
    logger.info("=" * 60)

    client = BinanceClient()
    from storage.store import REDIS_ENABLED
    redis_status = "✅ Redis actif (Upstash)" if REDIS_ENABLED else "⚠️ Fichiers locaux (Redis non configuré)"
    send_status(
        f"*{config.BOT_NAME}* démarré — mode *{mode}*\n"
        f"Paires   : {', '.join(config.TRADING_PAIRS)}\n"
        f"Capital  : {config.CAPITAL} USDT\n"
        f"SL : {config.STOP_LOSS_PCT*100:.1f}%  |  TP : {config.TAKE_PROFIT_RATIO}:1  |  "
        f"Risque : {config.RISK_PER_TRADE*100:.0f}%/trade\n"
        f"Mémoire  : {redis_status}"
    )

    positions = load_state()
    if positions:
        logger.info(f"{len(positions)} position(s) rechargée(s) depuis {config.STATE_FILE}")
        positions = reconcile(client, positions)
        save_state(positions)
    elif not config.DRY_RUN:
        # state.json vide (redémarrage Railway) → scanner les soldes réels
        logger.info("[Sync] state.json vide — scan des soldes Binance pour détecter les positions")
        positions = recover_positions(client, config.TRADING_PAIRS)
        if positions:
            save_state(positions)
            logger.info(f"[Sync] {len(positions)} position(s) récupérée(s) depuis Binance")

    # Force le premier rapport dès le démarrage (ignore le cooldown)
    for sym in config.TRADING_PAIRS:
        _last_scan_ts[sym] = 0.0

    # Écoute des commandes Telegram (/journal, /status, /help)
    start_command_listener(positions, client)

    while True:
        try:
            maybe_send_daily_report()

            if is_paused():
                logger.info(
                    f"[Health] Trading suspendu — "
                    f"prochaine vérification dans {config.LOOP_INTERVAL}s"
                )
            else:
                current_balance = client.get_usdt_balance()
                # Équité totale = USDT libre + valeur des positions ouvertes
                total_equity = current_balance
                for sym, pos in positions.items():
                    try:
                        sym_price = client.get_current_price(sym)
                        total_equity += pos["amount"] * sym_price
                    except Exception:
                        total_equity += pos["amount"] * pos["entry_price"]
                update_equity(total_equity)

                for symbol in config.TRADING_PAIRS:
                    process_pair(client, symbol, positions)
                save_state(positions)

        except KeyboardInterrupt:
            logger.info("Arrêt demandé par l'utilisateur.")
            stop_command_listener()
            send_status("Bot arrêté manuellement.")
            break
        except Exception as exc:
            logger.error(f"Erreur inattendue dans la boucle principale : {exc}", exc_info=True)
            send_error(f"Erreur critique : {exc}")

        logger.info(f"Pause de {config.LOOP_INTERVAL}s avant la prochaine analyse...")
        time.sleep(config.LOOP_INTERVAL)


if __name__ == "__main__":
    main()
