import logging

import ccxt
import pandas as pd

import config

logger = logging.getLogger(__name__)


class BinanceClient:
    """Thin wrapper around ccxt.binance for the operations the bot needs."""

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
            logger.info("Mode TESTNET activé (binance testnet)")
        if self.dry_run:
            logger.info("Mode DRY RUN activé — aucun ordre réel ne sera passé")
        elif not config.USE_TESTNET:
            logger.warning("*** MODE LIVE — argent réel en jeu ***")

    # ── Market data ────────────────────────────────────────────────────────────

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 150) -> pd.DataFrame:
        raw = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df

    def get_current_price(self, symbol: str) -> float:
        ticker = self.exchange.fetch_ticker(symbol)
        return float(ticker["last"])

    # ── Account ────────────────────────────────────────────────────────────────

    def get_usdt_balance(self) -> float:
        if self.dry_run:
            return config.CAPITAL
        balance = self.exchange.fetch_balance()
        return float(balance.get("free", {}).get("USDT", 0.0))

    def get_asset_balance(self, asset: str) -> float:
        if self.dry_run:
            return 0.0
        balance = self.exchange.fetch_balance()
        return float(balance.get("free", {}).get(asset, 0.0))

    # ── Orders ─────────────────────────────────────────────────────────────────

    def place_market_buy(self, symbol: str, amount: float) -> dict:
        if self.dry_run:
            price = self.get_current_price(symbol)
            logger.info(f"[DRY RUN] BUY  {amount:.6f} {symbol} @ {price:.4f}")
            return {"average": price, "price": price, "amount": amount, "status": "closed"}
        order = self.exchange.create_market_buy_order(symbol, amount)
        logger.info(f"BUY exécuté : {symbol}  qty={amount:.6f}")
        return order

    def place_market_sell(self, symbol: str, amount: float) -> dict:
        if self.dry_run:
            price = self.get_current_price(symbol)
            logger.info(f"[DRY RUN] SELL {amount:.6f} {symbol} @ {price:.4f}")
            return {"average": price, "price": price, "amount": amount, "status": "closed"}
        # Use actual balance to avoid rounding / fee issues
        base_asset = symbol.split("/")[0]
        actual = self.get_asset_balance(base_asset)
        sell_amount = min(amount, actual)
        order = self.exchange.create_market_sell_order(symbol, sell_amount)
        logger.info(f"SELL exécuté : {symbol}  qty={sell_amount:.6f}")
        return order
