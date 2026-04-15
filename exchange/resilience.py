"""
Résilience API : retry avec backoff exponentiel + circuit breaker.

Couvre :
  - Latence : mesure chaque appel, alerte si > 1s
  - Reconnexion : retry × 4 avec délais 2s → 4s → 8s → 16s
  - Circuit breaker : coupe après N échecs consécutifs, réessaie après timeout
"""

import functools
import logging
import time
from typing import Callable, Type

import config

logger = logging.getLogger(__name__)


def with_retry(
    max_attempts: int | None = None,
    base_delay: float | None = None,
    exceptions: tuple[Type[Exception], ...] = (Exception,),
    label: str = "",
) -> Callable:
    """
    Décorateur : réessaie avec backoff exponentiel.
    Les paramètres par défaut viennent de config pour être configurables.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            _max = max_attempts or config.API_MAX_RETRIES
            _delay = base_delay or config.API_BASE_DELAY
            fname = label or func.__name__
            delay = _delay

            for attempt in range(1, _max + 1):
                try:
                    t0 = time.monotonic()
                    result = func(*args, **kwargs)
                    elapsed_ms = (time.monotonic() - t0) * 1000

                    if elapsed_ms > 2000:
                        logger.warning(f"[Latence] {fname} : {elapsed_ms:.0f} ms ⚠️")
                    elif elapsed_ms > 800:
                        logger.info(f"[Latence] {fname} : {elapsed_ms:.0f} ms")

                    return result

                except exceptions as exc:
                    if attempt == _max:
                        logger.error(
                            f"[Retry] {fname} — échec définitif après {_max} tentatives : {exc}"
                        )
                        raise
                    logger.warning(
                        f"[Retry] {fname} — tentative {attempt}/{_max} : {exc}  "
                        f"→ retry dans {delay:.0f}s"
                    )
                    time.sleep(delay)
                    delay *= 2

        return wrapper
    return decorator


class CircuitBreaker:
    """
    Coupe-circuit : après N échecs consécutifs, bloque les appels
    pendant reset_timeout secondes, puis teste la reconnexion.

    États :
      closed    → fonctionnement normal
      open      → bloqué (trop d'erreurs récentes)
      half-open → test unique pour vérifier si l'API répond
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int | None = None,
        reset_timeout: float | None = None,
    ):
        self.name = name
        self.threshold = failure_threshold or config.API_CIRCUIT_BREAKER_THRESHOLD
        self.reset_timeout = reset_timeout or config.API_CIRCUIT_BREAKER_TIMEOUT
        self.failures = 0
        self._opened_at: float | None = None
        self._state = "closed"

    @property
    def is_open(self) -> bool:
        if self._state == "open":
            if time.monotonic() - (self._opened_at or 0) > self.reset_timeout:
                self._state = "half-open"
                logger.info(
                    f"[Circuit {self.name}] Half-open — test de reconnexion"
                )
                return False
            return True
        return False

    def record_success(self):
        if self._state != "closed":
            logger.info(f"[Circuit {self.name}] Fermé ✅ — connexion rétablie")
        self.failures = 0
        self._state = "closed"
        self._opened_at = None

    def record_failure(self, exc: Exception):
        self.failures += 1
        logger.warning(f"[Circuit {self.name}] Échec #{self.failures} : {exc}")
        if self.failures >= self.threshold:
            self._state = "open"
            self._opened_at = time.monotonic()
            logger.error(
                f"[Circuit {self.name}] OUVERT 🔴 après {self.failures} échecs — "
                f"pause {self.reset_timeout:.0f}s"
            )
