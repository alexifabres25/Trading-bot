#!/bin/bash
# ═══════════════════════════════════════════════════════
#  Watchdog — redémarre le bot automatiquement en cas de crash
#  Usage : bash start.sh
#          bash start.sh --env .env.aggressive
# ═══════════════════════════════════════════════════════

ENV_FILE=".env"
if [ "$1" == "--env" ] && [ -n "$2" ]; then
    ENV_FILE="$2"
fi

export BOT_CONFIG="$ENV_FILE"
echo "[ Watchdog ] Config : $ENV_FILE"
echo "[ Watchdog ] Démarrage — Ctrl+C pour arrêter proprement"
echo ""

RESTART_DELAY=15
CRASH_COUNT=0

while true; do
    python bot.py
    EXIT_CODE=$?

    # Sortie propre (Ctrl+C = code 0 ou 130)
    if [ $EXIT_CODE -eq 0 ] || [ $EXIT_CODE -eq 130 ]; then
        echo "[ Watchdog ] Arrêt normal."
        break
    fi

    CRASH_COUNT=$((CRASH_COUNT + 1))
    echo ""
    echo "[ Watchdog ] ⚠ Crash détecté (code=$EXIT_CODE, total=$CRASH_COUNT)"
    echo "[ Watchdog ] Redémarrage dans ${RESTART_DELAY}s..."
    sleep $RESTART_DELAY

    # Backoff exponentiel plafonné à 5 minutes
    RESTART_DELAY=$((RESTART_DELAY * 2))
    if [ $RESTART_DELAY -gt 300 ]; then
        RESTART_DELAY=300
    fi
done
