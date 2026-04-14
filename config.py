import os
from dotenv import load_dotenv

# Charge le fichier .env spécifié par BOT_CONFIG (défaut : .env)
# Permet de lancer deux bots en parallèle avec des configs différentes :
#   BOT_CONFIG=.env.conservative python bot.py
#   BOT_CONFIG=.env.aggressive   python bot.py
_env_file = os.environ.get("BOT_CONFIG", ".env")
load_dotenv(_env_file)

# ── Identifiant du bot (affiché dans les alertes Telegram) ────────────────────
BOT_NAME: str = os.getenv("BOT_NAME", "Trading Bot")

# ── Exchange ───────────────────────────────────────────────────────────────────
BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET: str = os.getenv("BINANCE_SECRET", "")
USE_TESTNET: bool = os.getenv("USE_TESTNET", "true").lower() == "true"
DRY_RUN: bool = os.getenv("DRY_RUN", "false").lower() == "true"

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Trading ────────────────────────────────────────────────────────────────────
_pairs_raw = os.getenv("TRADING_PAIRS", "BTC/USDT,ETH/USDT")
TRADING_PAIRS: list[str] = [p.strip() for p in _pairs_raw.split(",")]
CAPITAL: float = float(os.getenv("CAPITAL", "400"))
RISK_PER_TRADE: float = float(os.getenv("RISK_PER_TRADE", "0.01"))
STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "0.02"))

# ── Stratégie ──────────────────────────────────────────────────────────────────
TIMEFRAME_SHORT: str = os.getenv("TIMEFRAME_SHORT", "1h")
TIMEFRAME_LONG: str = os.getenv("TIMEFRAME_LONG", "4h")
EMA_FAST: int = int(os.getenv("EMA_FAST", "9"))
EMA_SLOW: int = int(os.getenv("EMA_SLOW", "21"))
RSI_PERIOD: int = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERSOLD: int = int(os.getenv("RSI_OVERSOLD", "30"))
RSI_OVERBOUGHT: int = int(os.getenv("RSI_OVERBOUGHT", "70"))
CANDLES_LIMIT: int = 150

# ── Boucle principale ──────────────────────────────────────────────────────────
LOOP_INTERVAL: int = int(os.getenv("LOOP_INTERVAL", "300"))
STATE_FILE: str = os.getenv("STATE_FILE", "state.json")
