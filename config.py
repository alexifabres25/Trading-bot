import os
from dotenv import load_dotenv

_env_file = os.environ.get("BOT_CONFIG", ".env")
load_dotenv(_env_file)

# ── Identifiant du bot ─────────────────────────────────────────────────────────
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
RISK_PER_TRADE: float = float(os.getenv("RISK_PER_TRADE", "0.01"))  # fallback si pas assez de trades Kelly
STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "0.02"))
FEE_RATE: float = 0.001  # 0.1 % par ordre Binance (taker)

# ── Trailing stop ──────────────────────────────────────────────────────────────
TRAILING_STOP: bool = os.getenv("TRAILING_STOP", "true").lower() == "true"
TRAILING_STOP_PCT: float = float(os.getenv("TRAILING_STOP_PCT", "0.02"))

# ── Kelly Criterion ────────────────────────────────────────────────────────────
# Dimensionne le risque automatiquement selon le vrai track record du bot.
# Quarter-Kelly (0.25) = version conservatrice, réduit la variance de 75 %.
KELLY_FRACTION: float = float(os.getenv("KELLY_FRACTION", "0.25"))
KELLY_LOOKBACK: int = int(os.getenv("KELLY_LOOKBACK", "100"))   # nb trades pour calcul W/R
RISK_MAX_CAP: float = float(os.getenv("RISK_MAX_CAP", "0.02"))  # plafond 2 % quoi qu'il arrive
RISK_MIN_FLOOR: float = float(os.getenv("RISK_MIN_FLOOR", "0.001"))  # plancher 0.1 %

# ── Multiplicateur progressif sur les pertes ──────────────────────────────────
# Après chaque trade perdant  : risque × (1 - LOSS_RISK_REDUCTION)  → -10%
# Après chaque trade gagnant  : risque × (1 + WIN_RISK_RECOVERY)    → +5%
# Exemple : risque de base 1%
#   Perte → 0.90%  →  perte → 0.81%  →  gain → 0.85%  →  gain → 0.89%...
LOSS_RISK_REDUCTION: float = float(os.getenv("LOSS_RISK_REDUCTION", "0.10"))  # -10% / perte
WIN_RISK_RECOVERY: float = float(os.getenv("WIN_RISK_RECOVERY", "0.05"))      # +5% / gain
RISK_MULTIPLIER_MIN: float = 0.20   # plancher : 20% du risque de base (évite de trop écraser)
RISK_MULTIPLIER_MAX: float = 1.00   # plafond  : jamais au-dessus du risque de base

# ── Drawdown-based risk scaling ────────────────────────────────────────────────
# Réduit progressivement le risque par palier quand le bot est en drawdown.
# Valeurs calquées sur les screenshots MetaTrader (TrendWin EA).
DD_SCALING_ENABLED: bool = os.getenv("DD_SCALING_ENABLED", "true").lower() == "true"

DD_TIER_1: float = 0.02       # DD ≥ 2 % → passage au tier 1
RISK_AT_DD_TIER_1: float = 0.0075  # 0.75 %

DD_TIER_2: float = 0.04       # DD ≥ 4 % → tier 2
RISK_AT_DD_TIER_2: float = 0.005   # 0.5 %

DD_TIER_3: float = 0.06       # DD ≥ 6 % → tier 3
RISK_AT_DD_TIER_3: float = 0.0025  # 0.25 %

RISK_BEYOND_TIER_3: float = 0.001  # DD > 6 % → mode survie 0.1 %

# ── Supertrend — stop-loss adaptatif basé sur la volatilité réelle (ATR) ──────
SUPERTREND_PERIOD: int = 10
SUPERTREND_MULTIPLIER: float = 3.0

# ── Health Monitor — suspension et reprise automatiques ───────────────────────
MAX_CONSECUTIVE_LOSSES: int = 5        # pause après N pertes consécutives
MAX_DAILY_DD_PCT: float = 0.04         # pause si drawdown journalier > 4%
PAUSE_DURATION_HOURS: int = 24         # durée de pause avant reprise automatique
DAILY_REPORT_HOUR: int = 8             # heure UTC du rapport Telegram quotidien

# ── ADX — filtre de tendance (issu de la recherche algo trading) ───────────────
ADX_PERIOD: int = 14
ADX_TREND_MIN: int = int(os.getenv("ADX_TREND_MIN", "20"))  # ADX < 20 = marché en range, on n'entre pas

# ── Stratégie ──────────────────────────────────────────────────────────────────
TIMEFRAME_SHORT: str = os.getenv("TIMEFRAME_SHORT", "1h")
TIMEFRAME_LONG: str = os.getenv("TIMEFRAME_LONG", "4h")
EMA_FAST: int = int(os.getenv("EMA_FAST", "9"))
EMA_SLOW: int = int(os.getenv("EMA_SLOW", "21"))
RSI_PERIOD: int = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERSOLD: int = int(os.getenv("RSI_OVERSOLD", "30"))
RSI_OVERBOUGHT: int = int(os.getenv("RSI_OVERBOUGHT", "70"))
CANDLES_LIMIT: int = 150

# ── Type d'ordre ──────────────────────────────────────────────────────────────
# "market" = exécution immédiate (taker, frais 0.1%)
# "limit"  = maker si rempli, sinon fallback market après LIMIT_ORDER_TIMEOUT
ORDER_TYPE: str = os.getenv("ORDER_TYPE", "market")
LIMIT_ORDER_OFFSET: float = 0.0005   # +0.05% au-dessus du prix pour limit buy
LIMIT_ORDER_TIMEOUT: int = int(os.getenv("LIMIT_ORDER_TIMEOUT", "30"))  # secondes

# ── Résilience API ─────────────────────────────────────────────────────────────
API_MAX_RETRIES: int = 4
API_BASE_DELAY: float = 2.0           # délais : 2s → 4s → 8s → 16s
API_CIRCUIT_BREAKER_THRESHOLD: int = 5     # N échecs → circuit ouvert
API_CIRCUIT_BREAKER_TIMEOUT: float = 120.0 # secondes avant réessai

# ── Boucle principale ──────────────────────────────────────────────────────────
LOOP_INTERVAL: int = int(os.getenv("LOOP_INTERVAL", "300"))
STATE_FILE: str = os.getenv("STATE_FILE", "state.json")
EQUITY_FILE: str = os.getenv("EQUITY_FILE", "equity.json")
