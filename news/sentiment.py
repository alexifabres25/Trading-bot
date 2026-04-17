"""
Sentiment de marché — deux sources gratuites, sans API key :

1. Fear & Greed Index (Alternative.me)
   Score 0-100 mis à jour toutes les heures.
   Utilisé par des fonds professionnels pour éviter d'acheter en excès
   d'euphorie ou de paniquer en excès de peur.

2. CryptoPanic news feed
   Dernières news crypto avec votes bullish/bearish de la communauté.
   Permet de détecter les événements majeurs négatifs (hack, ban, crash).

Règles d'utilisation dans le bot :
  Extreme Greed (>= 80) → pas d'achat (marché surévalué, correction probable)
  Extreme Fear  (<= 20) → pas d'achat (panique, tendance baissière forte)
  News très négatives   → pas d'achat (événement exceptionnel en cours)
  Reste                 → signal technique prime, sentiment = contexte
"""

import logging
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

_FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
_CRYPTOPANIC_URL = "https://cryptopanic.com/api/free/v1/posts/?auth_token=free&public=true&kind=news"

# Cache en mémoire (évite de spammer les APIs à chaque cycle de 5 min)
_cache: dict = {}
_CACHE_TTL_SECONDS = 900  # 15 minutes


def _is_fresh(key: str) -> bool:
    entry = _cache.get(key)
    if not entry:
        return False
    return (datetime.now(timezone.utc).timestamp() - entry["ts"]) < _CACHE_TTL_SECONDS


def get_fear_greed() -> dict:
    """
    Retourne le Fear & Greed Index actuel.
    {"value": 72, "label": "Greed", "signal": "neutral"}

    Labels : Extreme Fear | Fear | Neutral | Greed | Extreme Greed
    Signal bot :
      value <= 20 → "block"   (Extreme Fear  — ne pas acheter)
      value >= 80 → "block"   (Extreme Greed — ne pas acheter)
      reste       → "neutral" (signal technique prime)
    """
    if _is_fresh("fear_greed"):
        return _cache["fear_greed"]["data"]

    try:
        resp = requests.get(_FEAR_GREED_URL, timeout=8)
        resp.raise_for_status()
        data = resp.json()["data"][0]
        value = int(data["value"])
        label = data["value_classification"]

        if value <= 20:
            signal = "block"
        elif value >= 80:
            signal = "block"
        else:
            signal = "neutral"

        result = {"value": value, "label": label, "signal": signal}
        _cache["fear_greed"] = {"ts": datetime.now(timezone.utc).timestamp(), "data": result}
        logger.info(f"[Sentiment] Fear & Greed : {value}/100 ({label}) → {signal}")
        return result

    except Exception as exc:
        logger.warning(f"[Sentiment] Fear & Greed indisponible : {exc}")
        return {"value": 50, "label": "Neutral", "signal": "neutral"}


def get_news_sentiment(symbol: str) -> dict:
    """
    Retourne le sentiment des dernières news CryptoPanic pour la paire.
    {"bullish": 3, "bearish": 1, "signal": "neutral"}

    Signal bot :
      bearish > bullish × 2 ET bearish >= 3 → "block" (news très négatives)
      reste                                  → "neutral"
    """
    currency = symbol.split("/")[0]  # "BTC/USDT" → "BTC"
    cache_key = f"news_{currency}"

    if _is_fresh(cache_key):
        return _cache[cache_key]["data"]

    try:
        url = f"{_CRYPTOPANIC_URL}&currencies={currency}"
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        posts = resp.json().get("results", [])[:20]  # 20 dernières news

        bullish = sum(1 for p in posts if p.get("votes", {}).get("positive", 0) > 0)
        bearish = sum(1 for p in posts if p.get("votes", {}).get("negative", 0) > 0)

        signal = "block" if (bearish > bullish * 2 and bearish >= 3) else "neutral"

        result = {"bullish": bullish, "bearish": bearish, "signal": signal}
        _cache[cache_key] = {"ts": datetime.now(timezone.utc).timestamp(), "data": result}
        logger.info(
            f"[Sentiment] News {currency} : {bullish} bullish / {bearish} bearish → {signal}"
        )
        return result

    except Exception as exc:
        logger.warning(f"[Sentiment] News {currency} indisponibles : {exc}")
        return {"bullish": 0, "bearish": 0, "signal": "neutral"}


def should_block_buy(symbol: str) -> tuple[bool, str]:
    """
    Point d'entrée principal — appelé avant chaque BUY.
    Retourne (True, raison) si le sentiment bloque l'achat, (False, "") sinon.
    """
    fg = get_fear_greed()
    if fg["signal"] == "block":
        reason = f"Fear & Greed {fg['value']}/100 ({fg['label']})"
        logger.warning(f"[Sentiment] BUY bloqué — {reason}")
        return True, reason

    news = get_news_sentiment(symbol)
    if news["signal"] == "block":
        reason = f"News très négatives ({news['bearish']} bearish vs {news['bullish']} bullish)"
        logger.warning(f"[Sentiment] BUY bloqué — {reason}")
        return True, reason

    return False, ""


def get_sentiment_summary() -> str:
    """Résumé court pour le rapport Telegram journalier."""
    fg = get_fear_greed()
    emoji = "😱" if fg["value"] <= 25 else "😰" if fg["value"] <= 45 else \
            "😐" if fg["value"] <= 55 else "😄" if fg["value"] <= 75 else "🤑"
    return f"{emoji} Fear & Greed : `{fg['value']}/100` ({fg['label']})"
