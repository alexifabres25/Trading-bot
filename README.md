# Crypto Trading Bot

Bot de trading automatique BTC/USDT et ETH/USDT sur Binance.

## Stratégie

| Timeframe | Indicateurs | Rôle |
|-----------|------------|------|
| 1h | EMA 9 / EMA 21 + RSI 14 | Signal d'entrée / sortie |
| 4h | EMA 9 / EMA 21 | Filtre de tendance |

- **Achat** : croisement haussier EMA 9/21 sur 1h + RSI < 70 + tendance 4h non baissière
- **Vente** : croisement baissier EMA 9/21 sur 1h OU tendance 4h baissière OU stop-loss atteint
- **Stop-loss** : 2 % sous le prix d'entrée
- **Taille de position** : 1 % du capital risqué par trade

## Installation

```bash
# 1. Cloner le repo
git clone https://github.com/alexifabres25/trading-bot.git
cd trading-bot

# 2. Créer un environnement virtuel
python -m venv venv
source venv/bin/activate      # Linux / Mac
# venv\Scripts\activate       # Windows

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Configurer les variables d'environnement
cp .env.example .env
# Éditer .env avec tes clés API
```

## Configuration

Copie `.env.example` en `.env` et remplis les valeurs :

```
BINANCE_API_KEY=...   # Clé API Binance
BINANCE_SECRET=...    # Secret API Binance
USE_TESTNET=true      # true = testnet (recommandé pour débuter)
DRY_RUN=false         # true = simulation sans aucun ordre réel

TELEGRAM_TOKEN=...    # Token du bot Telegram (via @BotFather)
TELEGRAM_CHAT_ID=...  # Ton chat ID Telegram

CAPITAL=400           # Capital de référence en USDT
```

## Modes de fonctionnement

| Mode | `USE_TESTNET` | `DRY_RUN` | Description |
|------|-------------|-----------|-------------|
| Simulation totale | `true` ou `false` | `true` | Aucun ordre passé, utile pour tester la logique |
| Paper trading | `true` | `false` | Ordres réels sur le testnet Binance (pas d'argent réel) |
| Live trading | `false` | `false` | ⚠️ Argent réel |

> **Pour le testnet Binance**, crée des clés API séparées sur https://testnet.binance.vision

## Lancement

```bash
python bot.py
```

Le bot analyse les paires toutes les 5 minutes et envoie des alertes Telegram pour chaque ordre.

## Structure du projet

```
├── bot.py                  # Point d'entrée, boucle principale
├── config.py               # Tous les paramètres
├── exchange/
│   └── client.py           # Connexion Binance via ccxt
├── strategy/
│   ├── indicators.py       # Calcul EMA, RSI (pandas-ta)
│   └── signal.py           # Génération des signaux BUY / SELL / HOLD
├── risk/
│   └── manager.py          # Taille de position, calcul stop-loss
├── notifications/
│   └── telegram_bot.py     # Alertes Telegram
├── state.json              # Positions ouvertes (créé automatiquement)
└── bot.log                 # Journal du bot (créé automatiquement)
```

## Avertissement

Ce bot est un outil éducatif. Le trading de cryptomonnaies comporte des risques importants.
Commence toujours par tester en mode simulation ou testnet avant de risquer de l'argent réel.
