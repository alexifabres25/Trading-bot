# Crypto Trading Bot

Bot de trading automatique BTC/USDT et ETH/USDT sur Binance.  
Stratégie EMA 9/21 + RSI 14, alertes Telegram, risk management avancé.

---

## Stratégie

| Timeframe | Indicateurs | Rôle |
|-----------|-------------|------|
| 1h | EMA 9 / EMA 21 + RSI 14 + ADX 14 | Signal d'entrée / sortie |
| 4h | EMA 9 / EMA 21 | Filtre de tendance |

**Entrée (BUY)** :
- Croisement haussier EMA 9 > EMA 21 sur 1h
- RSI < 70 (pas en surachat)
- ADX ≥ 20 (marché directionnel, pas en range)
- Tendance 4h non baissière (bull ou neutral)

**Sortie (SELL)** :
- Croisement baissier EMA 9 < EMA 21 sur 1h
- OU tendance 4h devient baissière
- OU stop-loss atteint (2 % sous l'entrée)

**Stop dynamique** (par ordre de priorité) :
1. **Supertrend** (ATR-based) — stop adaptatif à la volatilité réelle
2. **Trailing stop** classique à 2 % — fallback si Supertrend indisponible

---

## Risk Management

### Taille de position — RM_EquityPercent
La taille de chaque position est calculée sur l'**équité réelle du moment**, pas sur un capital fixe :

```
risk_usdt = équité_actuelle × risk_pct_dynamique
distance_SL = prix_entrée × 2 %
quantité = risk_usdt / distance_SL
```

Cela produit un **effet compound naturel** (positions plus grandes en gain, plus petites en perte).

### Kelly Criterion (Quarter-Kelly)
Le risque par trade est calculé dynamiquement depuis le track record réel du bot :

```
f* = (W/R × win_rate - loss_rate) / W/R  ×  0.25
```

Fallback sur `RISK_PER_TRADE` si moins de 5 trades en journal.

### Drawdown Scaling
Réduction progressive du risque par palier :

| Drawdown | Risque max |
|----------|-----------|
| < 1 %    | Normal (Kelly) |
| 1–2 %    | 0.75 % |
| 2–4 %    | 0.50 % |
| 4–6 %    | 0.25 % |
| > 6 %    | 0.10 % (mode survie) |

### Multiplicateur progressif
- Perte → risque × 0.90 (-10 %)
- Gain  → risque × 1.05 (+5 %)
- Plancher : 20 % du risque de base — Plafond : 100 %

---

## Health Monitor

Surveille la santé du bot en permanence et gère l'autonomie totale :

- **Pause automatique** après N pertes consécutives (défaut : 5)
- **Pause automatique** si drawdown journalier > 4 %
- **Reprise automatique** après 24 h sans intervention
- **Rapport journalier** Telegram à 8h UTC

---

## Apprentissage automatique

Après chaque trade fermé, le bot analyse les erreurs récentes et ajuste ses paramètres :

| Erreur détectée | Ajustement |
|----------------|-----------|
| Stop déclenché en < 4h | Trailing stop élargi (+15 %) |
| Entrée RSI > 62 | Seuil RSI overbought abaissé (-3 pts) |
| Croisement EMA < 0.3 % | Filtre spread EMA activé |

Fenêtre glissante sur les 50 derniers trades (anti-surapprentissage).

---

## Installation

```bash
# 1. Cloner le repo
git clone https://github.com/alexifabres25/trading-bot.git
cd trading-bot

# 2. Environnement virtuel
python -m venv venv
source venv/bin/activate      # Linux / Mac
# venv\Scripts\activate       # Windows

# 3. Dépendances
pip install -r requirements.txt

# 4. Configuration
cp .env.example .env
# Éditer .env avec tes clés API et token Telegram
```

---

## Configuration — `.env`

```bash
# ── Exchange ───────────────────────────────────────────
BINANCE_API_KEY=your_api_key_here
BINANCE_SECRET=your_secret_here
USE_TESTNET=true          # true = testnet (recommandé pour débuter)
DRY_RUN=false             # true = simulation sans ordres réels

# ── Telegram ───────────────────────────────────────────
TELEGRAM_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# ── Capital & risque ───────────────────────────────────
CAPITAL=400               # Capital de référence USDT
RISK_PER_TRADE=0.01       # 1 % du capital par trade (fallback Kelly)
STOP_LOSS_PCT=0.02        # Stop-loss 2 % sous le prix d'entrée

# ── Paires & timeframes ────────────────────────────────
TRADING_PAIRS=BTC/USDT,ETH/USDT
TIMEFRAME_SHORT=1h
TIMEFRAME_LONG=4h

# ── Stratégie ──────────────────────────────────────────
EMA_FAST=9
EMA_SLOW=21
RSI_PERIOD=14
RSI_OVERSOLD=30
RSI_OVERBOUGHT=70
ADX_TREND_MIN=20          # ADX minimum pour entrer (évite les ranges)

# ── Boucle ─────────────────────────────────────────────
LOOP_INTERVAL=300         # Secondes entre chaque analyse (5 min)
```

---

## Modes de fonctionnement

| Mode | `USE_TESTNET` | `DRY_RUN` | Description |
|------|-------------|-----------|-------------|
| Simulation totale | any | `true` | Aucun ordre, teste la logique |
| Paper trading | `true` | `false` | Ordres réels sur le testnet Binance |
| Live trading | `false` | `false` | ⚠️ Argent réel |

> Pour le testnet : créer des clés API sur https://testnet.binance.vision

---

## Lancement

```bash
# Lancement simple
python bot.py

# Avec watchdog (redémarrage automatique en cas de crash)
bash start.sh

# Bot agressif (risque 2 %, altcoins)
bash start.sh --env .env.aggressive
```

Le bot analyse les paires toutes les **5 minutes** et envoie des alertes Telegram pour chaque ordre, rapport journalier, pause et reprise.

---

## Structure du projet

```
├── bot.py                      # Point d'entrée, boucle principale
├── config.py                   # Tous les paramètres (env → config)
├── start.sh                    # Watchdog avec redémarrage automatique
│
├── exchange/
│   ├── client.py               # Binance via ccxt (retry, circuit breaker, slippage)
│   ├── resilience.py           # Retry backoff + circuit breaker
│   └── sync.py                 # Réconciliation portefeuille au démarrage
│
├── strategy/
│   ├── indicators.py           # EMA, RSI, ADX, Supertrend (pandas-ta)
│   └── signal.py               # Signaux BUY / SELL / HOLD
│
├── risk/
│   └── manager.py              # Kelly + DD scaling + trailing stop + RM_Equity%
│
├── learning/
│   ├── journal.py              # Journal de trades (contexte complet)
│   ├── analyzer.py             # Analyse post-trade + adaptation des params
│   └── health.py               # Health monitor + rapport journalier
│
├── notifications/
│   └── telegram_bot.py         # Alertes Telegram (ordres, erreurs, rapports)
│
├── .env.example                # Template de configuration (à copier en .env)
├── .env.aggressive.example     # Profil agressif (altcoins, risque 2 %)
├── .env.conservative.example   # Profil conservateur
└── requirements.txt
```

---

## Alertes Telegram

| Événement | Message |
|-----------|---------|
| BUY exécuté | Prix, quantité, valeur, stop-loss |
| SELL exécuté | Prix, PnL estimé (+/- USDT, %) |
| Pause automatique | Raison + heure de reprise |
| Reprise automatique | Confirmation |
| Rapport journalier | Trades du jour, PnL, win rate global |
| Apprentissage | Bilan tous les 5 trades fermés |
| Erreur critique | Stack trace + contexte |

---

## Résilience API

- **Retry** : 4 tentatives avec backoff 2s → 4s → 8s → 16s
- **Circuit breaker** : coupe après 5 échecs, réessaie après 2 minutes
- **Latence** : alerte si appel > 800 ms, warning si > 2 s
- **Slippage** : mesuré sur chaque ordre, alerte si > 0.3 %
- **Sync au démarrage** : réconcilie l'état interne avec les vrais soldes Binance

---

## Avertissement

Ce bot est un outil éducatif. Le trading de cryptomonnaies comporte des risques importants de perte en capital. Commencer systématiquement par le mode simulation ou testnet avant d'engager de l'argent réel.
