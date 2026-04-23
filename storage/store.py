"""
Couche de persistance unifiée — Redis (Upstash) ou fichiers locaux.

Si UPSTASH_REDIS_REST_URL et UPSTASH_REDIS_REST_TOKEN sont définis :
  → toutes les données sont stockées dans Redis (survivent aux redémarrages Railway)

Sinon :
  → fallback sur les fichiers locaux (développement / local)

Clés Redis utilisées :
  trading:state    → positions ouvertes (dict)
  trading:equity   → équité et pic historique (dict)
  trading:health   → état du health monitor (dict)
  trading:journal  → historique complet des trades (list)
"""

import json
import logging
import os
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_URL   = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
REDIS_ENABLED = bool(_URL and _TOKEN)

if REDIS_ENABLED:
    logger.info("[Store] Mode Redis (Upstash) — persistance complète activée")
else:
    logger.info("[Store] Mode fichiers locaux (UPSTASH_REDIS_REST_URL non défini)")


# ── Primitives Redis ───────────────────────────────────────────────────────────

def _redis_get(key: str):
    try:
        resp = requests.get(
            f"{_URL}/get/{key}",
            headers={"Authorization": f"Bearer {_TOKEN}"},
            timeout=5,
        )
        result = resp.json().get("result")
        return json.loads(result) if result else None
    except Exception as exc:
        logger.warning(f"[Redis] GET {key} : {exc}")
        return None


def _redis_set(key: str, value) -> bool:
    try:
        serialized = json.dumps(value, default=str)
        resp = requests.post(
            f"{_URL}/set/{key}",
            headers={
                "Authorization": f"Bearer {_TOKEN}",
                "Content-Type": "application/json",
            },
            data=serialized.encode(),
            timeout=5,
        )
        return resp.json().get("result") == "OK"
    except Exception as exc:
        logger.warning(f"[Redis] SET {key} : {exc}")
        return False


# ── API publique ───────────────────────────────────────────────────────────────

def load(key: str, file_path: str, default):
    """
    Charge une valeur depuis Redis ou depuis le fichier local.
    key       : clé Redis (ex: "trading:journal")
    file_path : chemin fichier fallback (ex: config.JOURNAL_FILE)
    default   : valeur par défaut si rien n'est trouvé
    """
    if REDIS_ENABLED:
        result = _redis_get(key)
        return result if result is not None else default

    path = Path(file_path)
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as exc:
            logger.warning(f"[Store] Lecture {file_path} : {exc}")
    return default


def save(key: str, file_path: str, value) -> bool:
    """
    Sauvegarde dans Redis ET dans le fichier local (double persistance).
    key       : clé Redis
    file_path : chemin fichier local
    value     : données à sauvegarder
    """
    # Toujours écrire en local (backup)
    try:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(value, f, indent=2, default=str)
    except Exception as exc:
        logger.warning(f"[Store] Écriture {file_path} : {exc}")

    # Écrire dans Redis si disponible
    if REDIS_ENABLED:
        return _redis_set(key, value)

    return True
