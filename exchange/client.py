"""
Client Binance — robuste pour la production.

Couvre :
  Latence     → chaque appel est chronométré, alerte si > 800 ms
  Retry       → 4 tentatives avec backoff 2s/4s/8s/16s sur toute erreur réseau
  Circuit     → coupe les appels après 5 échecs, réessaie après 2 minutes
  Slippage    → compare prix attendu vs prix réel, loggé dans chaque ordre
  Filtres     → arrondit qty/price aux précisions requises par Binance
  Type ordre  → market (défaut) ou limit avec timeout + fallback market
  Reconnexion → détectée automatiquement par le circuit breaker
"""

import logging
import time

import ccxt
import pandas as pd

import config
from exchange.resilience import CircuitBreaker, with_retry

logger = logging.getLogger(__name__)

# Circuit breaker partagé pour toute la session
_circuit = CircuitBreaker("Binance")


def _safe_call(func, *args, **kwargs):
    """Exécute un appel API en passant par le circuit breaker."""
    if _circuit.is_open:
        raise RuntimeError(
            f"[Circuit ouvert] API Binance temporairement inaccessible — "
            f"prochaine tentative dans {_circuit.reset_timeout:.0f}s"
        )
    try:
        result = func(*args, **kwargs)
        _circuit.record_success()
        return result
    except Exception as exc:
        _circuit.record_failure(exc)
        raise


class BinanceClient:
    """Wrapper ccxt.binance orienté production."""

    def __init__(self):
        self.dry_run = config.DRY_RUN
        self.exchange = ccxt.binance({
            "apiKey": config.BINANCE_API_KEY,
            "secret": config.BINANCE_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        if config.USE_TESTNET:
            self.exchange.set_sandbox_mode(True)
            logger.info("Mode TESTNET activé")
        if self.dry_run:
            logger.info("Mode DRY RUN activé — aucun ordre réel")
        elif not config.USE_TESTNET:
            logger.warning("*** MODE LIVE — argent réel ***")

    # ── Helpers internes ───────────────────────────────────────────────────────

    def _adjust_qty(self, symbol: str, qty: float) -> float:
        """Arrondit la quantité aux contraintes LOT_SIZE de l'exchange."""
        try:
            market = self.exchange.market(symbol)
            precision = market.get("precision", {}).get("amount")
            if precision is not None:
                qty = float(self.exchange.amount_to_precision(symbol, qty))
        except Exception as exc:
            logger.debug(f"[Filtre] Impossible de lire LOT_SIZE pour {symbol}: {exc}")
        return qty

    def _check_min_notional(self, symbol: str, qty: float, price: float):
        """Lève une erreur si la valeur de l'ordre est sous le minimum Binance."""
        try:
            market = self.exchange.market(symbol)
            min_cost = market.get("limits", {}).get("cost", {}).get("min", 0)
            if min_cost and qty * price < min_cost:
                raise ValueError(
                    f"Valeur {qty * price:.2f} USDT < minimum Binance {min_cost} USDT"
                )
        except ValueError:
            raise
        except Exception as exc:
            logger.debug(f"[Filtre] Impossible de vérifier min_notional: {exc}")

    # ── Market data ────────────────────────────────────────────────────────────

    @with_retry(label="fetch_ohlcv")
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 150) -> pd.DataFrame:
        raw = _safe_call(self.exchange.fetch_ohlcv, symbol, timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df

    @with_retry(label="get_current_price")
    def get_current_price(self, symbol: str) -> float:
        ticker = _safe_call(self.exchange.fetch_ticker, symbol)
        return float(ticker["last"])

    # ── Account ────────────────────────────────────────────────────────────────

    @with_retry(label="get_usdt_balance")
    def get_usdt_balance(self) -> float:
        if self.dry_run:
            return config.CAPITAL
        balance = _safe_call(self.exchange.fetch_balance)
        return float(balance.get("free", {}).get("USDT", 0.0))

    @with_retry(label="get_asset_balance")
    def get_asset_balance(self, asset: str) -> float:
        if self.dry_run:
            return 0.0
        balance = _safe_call(self.exchange.fetch_balance)
        return float(balance.get("free", {}).get(asset, 0.0))

    # ── Orders ─────────────────────────────────────────────────────────────────

    def place_market_buy(self, symbol: str, amount: float, expected_price: float = 0.0) -> dict:
        """
        Achat market avec :
          - Ajustement LOT_SIZE
          - Vérification min_notional
          - Calcul du slippage vs prix attendu
        """
        if self.dry_run:
            price = self.get_current_price(symbol)
            logger.info(f"[DRY RUN] BUY  {amount:.6f} {symbol} @ {price:.4f}")
            return {
                "average": price, "price": price,
                "amount": amount, "status": "closed",
                "slippage_pct": 0.0,
            }

        amount = self._adjust_qty(symbol, amount)
        price_est = expected_price or self.get_current_price(symbol)
        self._check_min_notional(symbol, amount, price_est)

        order = self._execute_with_retry_buy(symbol, amount)

        filled_price = float(order.get("average") or order.get("price") or price_est)
        if expected_price > 0:
            slippage_pct = (filled_price - expected_price) / expected_price * 100
            order["slippage_pct"] = round(slippage_pct, 4)
            if abs(slippage_pct) > 0.3:
                logger.warning(
                    f"[Slippage] {symbol} BUY : attendu={expected_price:.4f}  "
                    f"exécuté={filled_price:.4f}  slippage={slippage_pct:+.3f}%"
                )
            else:
                logger.info(
                    f"[Slippage] {symbol} BUY : slippage={slippage_pct:+.3f}%"
                )

        logger.info(f"BUY exécuté : {symbol}  qty={amount:.6f}  @{filled_price:.4f}")
        return order

    def place_market_sell(self, symbol: str, amount: float, expected_price: float = 0.0) -> dict:
        """
        Vente market avec ajustement sur le solde réel (absorbe les frais d'entrée).
        """
        if self.dry_run:
            price = self.get_current_price(symbol)
            logger.info(f"[DRY RUN] SELL {amount:.6f} {symbol} @ {price:.4f}")
            return {
                "average": price, "price": price,
                "amount": amount, "status": "closed",
                "slippage_pct": 0.0,
            }

        base_asset = symbol.split("/")[0]
        actual = self.get_asset_balance(base_asset)
        sell_amount = self._adjust_qty(symbol, min(amount, actual))

        if sell_amount <= 0:
            raise ValueError(f"[{symbol}] Solde {base_asset} insuffisant pour vendre")

        order = self._execute_with_retry_sell(symbol, sell_amount)

        filled_price = float(order.get("average") or order.get("price") or 0)
        if expected_price > 0 and filled_price > 0:
            slippage_pct = (filled_price - expected_price) / expected_price * 100
            order["slippage_pct"] = round(slippage_pct, 4)
            logger.info(
                f"[Slippage] {symbol} SELL : slippage={slippage_pct:+.3f}%"
            )

        logger.info(f"SELL exécuté : {symbol}  qty={sell_amount:.6f}  @{filled_price:.4f}")
        return order

    # ── Ordre limit avec fallback market ───────────────────────────────────────

    def place_limit_buy(self, symbol: str, amount: float, price: float) -> dict:
        """
        Ordre limit buy. Si non rempli après LIMIT_ORDER_TIMEOUT → annulation
        et fallback market. Réduit les frais (maker vs taker).
        """
        if self.dry_run:
            return self.place_market_buy(symbol, amount, expected_price=price)

        amount = self._adjust_qty(symbol, amount)
        limit_price = round(price * (1 + config.LIMIT_ORDER_OFFSET), 8)

        try:
            order = _safe_call(
                self.exchange.create_limit_buy_order, symbol, amount, limit_price
            )
            order_id = order["id"]
            logger.info(
                f"[Limit] BUY {symbol} qty={amount:.6f} @ {limit_price:.4f}  "
                f"(id={order_id})"
            )

            # Attente de remplissage
            deadline = time.monotonic() + config.LIMIT_ORDER_TIMEOUT
            while time.monotonic() < deadline:
                time.sleep(3)
                status = _safe_call(self.exchange.fetch_order, order_id, symbol)
                if status["status"] == "closed":
                    avg = float(status.get("average") or status.get("price") or 0)
                    logger.info(f"[Limit] {symbol} rempli @ {avg:.4f}")
                    return status
                if status["status"] == "canceled":
                    logger.warning(f"[Limit] {symbol} annulé, fallback market")
                    break

            # Timeout → annulation + market
            try:
                _safe_call(self.exchange.cancel_order, order_id, symbol)
            except Exception:
                pass
            logger.warning(
                f"[Limit] {symbol} timeout ({config.LIMIT_ORDER_TIMEOUT}s) "
                "→ fallback market"
            )

        except Exception as exc:
            logger.warning(f"[Limit] {symbol} erreur ({exc}) → fallback market")

        return self.place_market_buy(symbol, amount, expected_price=price)

    # ── Retry wrappers internes ────────────────────────────────────────────────

    @with_retry(label="create_market_buy")
    def _execute_with_retry_buy(self, symbol: str, amount: float) -> dict:
        return _safe_call(self.exchange.create_market_buy_order, symbol, amount)

    @with_retry(label="create_market_sell")
    def _execute_with_retry_sell(self, symbol: str, amount: float) -> dict:
        return _safe_call(self.exchange.create_market_sell_order, symbol, amount)
