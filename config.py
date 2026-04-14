import os
from dotenv import load_dotenv

load_dotenv()

# ── Exchange ───────────────────────────────────────────────────────────────────
BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET: str = os.getenv("BINANCE_SECRET", "")
USE_TESTNET: bool = os.getenv("USE_TESTNET", "true").lower() == "true"
DRY_RUN: bool = os.getenv("DRY_RUN", "false").lower() == "true"

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Trading ────────────────────────────────────────────────────────────────────
TRADING_PAIRS: list[str] = ["BTC/USDT", "ETH/USDT"]
CAPITAL: float = float(os.getenv("CAPITAL", "400"))
RISK_PER_TRADE: float = 0.01   # 1 % du capital risqué par trade
STOP_LOSS_PCT: float = 0.02    # Stop-loss à 2 % sous le prix d'entrée

# ── Stratégie ──────────────────────────────────────────────────────────────────
TIMEFRAME_SHORT: str = "1h"    # Timeframe d'entrée (signaux EMA/RSI)
TIMEFRAME_LONG: str = "4h"     # Timeframe de tendance (filtre)
EMA_FAST: int = 9
EMA_SLOW: int = 21
RSI_PERIOD: int = 14
RSI_OVERSOLD: int = 30
RSI_OVERBOUGHT: int = 70
CANDLES_LIMIT: int = 150       # Nombre de bougies récupérées (warmup indicateurs)

# ── Boucle principale ──────────────────────────────────────────────────────────
LOOP_INTERVAL: int = 300       # Secondes entre chaque analyse (5 minutes)
STATE_FILE: str = "state.json"
