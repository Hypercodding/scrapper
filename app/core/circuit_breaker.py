"""Per-domain circuit breaker using Redis."""
import logging
import time
from typing import Optional

from app.core.settings_workers import get_worker_settings

logger = logging.getLogger(__name__)

CB_KEY_PREFIX = "scrape:cb:"


def _redis():
    import redis
    return redis.from_url(get_worker_settings().REDIS_URL, decode_responses=True)


def record_failure(host: str) -> int:
    settings = get_worker_settings()
    key = f"{CB_KEY_PREFIX}{host}"
    r = _redis()
    count = r.incr(key)
    if count == 1:
        r.expire(key, settings.CIRCUIT_BREAKER_WINDOW_SECONDS)
    return int(count)


def record_success(host: str) -> None:
    _redis().delete(f"{CB_KEY_PREFIX}{host}")


def is_open(host: str) -> bool:
    settings = get_worker_settings()
    key = f"{CB_KEY_PREFIX}{host}"
    count = _redis().get(key)
    if count and int(count) >= settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD:
        logger.warning("Circuit breaker open for host: %s", host)
        return True
    return False


def check_or_raise(host: str) -> None:
    if is_open(host):
        raise RuntimeError(f"Scraping temporarily disabled for {host} due to repeated failures")
