"""Redis-backed distributed semaphore for per-host concurrency control.

Each worker acquires a slot before launching a browser and releases it when done.
This enforces a global cap across all Railway replicas without in-process locks.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)

_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local max = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local current = tonumber(redis.call('GET', key) or '0')
if current < max then
    redis.call('SET', key, current + 1, 'EX', ttl)
    return 1
end
return 0
"""

_RELEASE_SCRIPT = """
local key = KEYS[1]
local current = tonumber(redis.call('GET', key) or '0')
if current > 0 then
    redis.call('SET', key, current - 1, 'KEEPTTL')
end
return 1
"""

_KEY_PREFIX = "scrape:throttle:"
_SLOT_TTL = 600  # seconds — slot auto-expires if worker crashes without releasing


def _redis():
    import redis as _redis_lib
    from app.core.settings_workers import get_worker_settings
    return _redis_lib.from_url(get_worker_settings().REDIS_URL, decode_responses=True)


def _acquire_slot(host: str, max_concurrent: int, timeout: float = 60.0) -> bool:
    """
    Try to acquire a slot for *host*, blocking until one is free or timeout expires.

    Returns True on success, False if timeout was reached.
    """
    r = _redis()
    acquire = r.register_script(_ACQUIRE_SCRIPT)
    key = f"{_KEY_PREFIX}{host}"
    deadline = time.monotonic() + timeout
    backoff = 1.0

    while time.monotonic() < deadline:
        result = acquire(keys=[key], args=[max_concurrent, _SLOT_TTL])
        if result == 1:
            logger.debug("Acquired throttle slot for %s", host)
            return True
        remaining = deadline - time.monotonic()
        sleep_for = min(backoff, remaining, 5.0)
        if sleep_for <= 0:
            break
        logger.debug("Throttle full for %s (%d/%d) — retrying in %.1fs", host, max_concurrent, max_concurrent, sleep_for)
        time.sleep(sleep_for)
        backoff = min(backoff * 1.5, 10.0)

    logger.warning("Could not acquire throttle slot for %s within %.1fs", host, timeout)
    return False


def _release_slot(host: str) -> None:
    try:
        r = _redis()
        release = r.register_script(_RELEASE_SCRIPT)
        release(keys=[f"{_KEY_PREFIX}{host}"], args=[])
        logger.debug("Released throttle slot for %s", host)
    except Exception as exc:
        logger.warning("Failed to release throttle slot for %s: %s", host, exc)


@contextmanager
def host_throttle(host: str, max_concurrent: int, acquire_timeout: float = 120.0):
    """
    Context manager that holds a concurrency slot for *host*.

    Usage::

        with host_throttle("indeed.com", max_concurrent=8):
            # only max_concurrent of these run at once across all workers
            result = scrape_something()

    Raises RuntimeError if the slot cannot be acquired within *acquire_timeout* seconds.
    """
    acquired = _acquire_slot(host, max_concurrent, timeout=acquire_timeout)
    if not acquired:
        raise RuntimeError(
            f"Could not acquire a scrape slot for {host} within {acquire_timeout:.0f}s. "
            "All worker slots are busy — task will retry."
        )
    try:
        yield
    finally:
        _release_slot(host)


def get_throttle_counts() -> dict:
    """Return current slot counts for all throttled hosts (for monitoring)."""
    try:
        r = _redis()
        keys = r.keys(f"{_KEY_PREFIX}*")
        return {k.replace(_KEY_PREFIX, ""): int(r.get(k) or 0) for k in keys}
    except Exception:
        return {}
