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

def _redis_cmd(*args) -> object:
    """Exécute une commande Redis via l'API pipeline Upstash."""
    try:
        resp = requests.post(
            _URL,
            headers={
                "Authorization": f"Bearer {_TOKEN}",
                "Content-Type": "application/json",
            },
            json=list(args),
            timeout=8,
        )
        resp.raise_for_status()
        return resp.json().get("result")
    except Exception as exc:
        logger.warning(f"[Redis] {args[0]} '{args[1] if len(args) > 1 else ''}' : {exc}")
        return None


def _redis_get(key: str):
    result = _redis_cmd("GET", key)
    if result is None:
        return None
    try:
        return json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return None


def _redis_set(key: str, value) -> bool:
    serialized = json.dumps(value, default=str)
    result = _redis_cmd("SET", key, serialized)
    ok = result == "OK"
    if not ok:
        logger.warning(f"[Redis] SET {key} → résultat inattendu : {result}")
    return ok


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
