"""Indeed scraper run-quality metrics, stored as minute-bucketed Redis hashes.

Each metric is a Redis hash keyed by `metrics:indeed:<name>` whose fields are
unix-minute strings and whose values are integer counters. A 25-hour TTL is
refreshed on every `incr`, so the rolling 24h window is always available.

Read via `snapshot(last_n_minutes=...)` which sums all fields whose minute is
within the requested window.

Surface in /api/health/workers so run quality is readable without log diving.
"""
from __future__ import annotations

import logging
import time
from typing import Dict

logger = logging.getLogger(__name__)

_PREFIX = "metrics:indeed:"
_TTL_SECONDS = 60 * 60 * 25  # keep 25h of minute buckets

# Known counter names — kept here for documentation and so the snapshot
# always returns the full shape even before the first increment.
KNOWN_COUNTERS = (
    "detail_attempts",   # every time the worker navigates to /viewjob?jk=...
    "detail_success",    # detail navigation returned a usable description
    "cf_blocks",         # block_detector returned CF_* or HTTP_403/429
    "captcha_seen",      # turnstile widget detected
    "captcha_solved",    # solver returned a token that submitted ok
    "desc_ok",           # description length >= MIN_DESCRIPTION_LEN
    "selector_drift",    # _extract_description_from_full_page raised
    "serp_attempts",     # search-URL navigations
    "serp_success",      # search returned cards
    "per_jk_retry_enqueued",   # scrape.indeed.retry tasks queued
    "per_jk_retry_success",    # retry task delivered a full job
)


def _redis():
    import redis as _redis_lib
    from app.core.settings_workers import get_worker_settings
    return _redis_lib.from_url(get_worker_settings().REDIS_URL, decode_responses=True)


def incr(name: str, by: int = 1) -> None:
    """Increment counter `name` for the current minute. Best-effort — never raises."""
    try:
        minute = int(time.time() // 60)
        key = f"{_PREFIX}{name}"
        r = _redis()
        pipe = r.pipeline()
        pipe.hincrby(key, str(minute), by)
        pipe.expire(key, _TTL_SECONDS)
        pipe.execute()
    except Exception as exc:
        logger.warning("metrics.incr(%s) failed: %s", name, exc)


def snapshot(last_n_minutes: int = 60) -> Dict[str, int]:
    """Sum each known counter over the trailing `last_n_minutes`.

    Always returns all KNOWN_COUNTERS keys (0 if untouched) so the response
    shape is stable for clients.
    """
    out: Dict[str, int] = {k: 0 for k in KNOWN_COUNTERS}
    try:
        now_minute = int(time.time() // 60)
        cutoff = now_minute - last_n_minutes
        r = _redis()
        pipe = r.pipeline()
        for name in KNOWN_COUNTERS:
            pipe.hgetall(f"{_PREFIX}{name}")
        results = pipe.execute()
        for name, hgetall_result in zip(KNOWN_COUNTERS, results):
            total = 0
            for minute_str, count_str in (hgetall_result or {}).items():
                try:
                    if int(minute_str) >= cutoff:
                        total += int(count_str)
                except (TypeError, ValueError):
                    continue
            out[name] = total
    except Exception as exc:
        logger.warning("metrics.snapshot failed: %s", exc)
    return out


def derived(snap: Dict[str, int]) -> Dict[str, float]:
    """Compute the human-readable ratios from a raw snapshot."""
    def safe_div(n: int, d: int) -> float:
        return round(n / d, 4) if d else 0.0

    return {
        "block_rate": safe_div(snap["cf_blocks"], snap["detail_attempts"]),
        "captcha_rate": safe_div(snap["captcha_seen"], snap["detail_attempts"]),
        "desc_ok_rate": safe_div(snap["desc_ok"], snap["detail_success"]),
        "per_jk_recovery_rate": safe_div(
            snap["per_jk_retry_success"], snap["per_jk_retry_enqueued"]
        ),
    }
