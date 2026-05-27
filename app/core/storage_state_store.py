"""Redis-backed Playwright `storage_state` cache.

Cloudflare's `cf_clearance` cookie remains valid for ~30 minutes after a
challenge is solved. Without persistence, every fresh browser launch in the
worker eats a new challenge — wasteful and detectable (a real user does not
re-solve the same challenge once a minute).

This module saves and loads the `storage_state` dict (cookies + localStorage +
sessionStorage) keyed by a stable session id (Step 5's
`FingerprintProfile.session_id` or Step 6's `ProxySession.session_id`). Two
launches that present the same session_id reuse the same cookies; different
session ids get independent jars.

TTL is 25 minutes — under the cf_clearance 30-minute lifetime so we always
discard before Cloudflare would invalidate it on the server side.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_PREFIX = "scrape:storage_state:indeed:"
_TTL_SECONDS = 25 * 60  # cf_clearance is ~30m; 5m safety buffer


def _redis():
    import redis as _redis_lib
    from app.core.settings_workers import get_worker_settings
    return _redis_lib.from_url(get_worker_settings().REDIS_URL, decode_responses=True)


def _key(session_id: str) -> str:
    # `session_id` is opaque from this module's perspective; callers should
    # already have ensured uniqueness (e.g. proxy_session_id + country).
    return f"{_PREFIX}{session_id}"


def load(session_id: str) -> Optional[dict]:
    """Return the cached storage_state dict, or None if missing/expired.

    Best-effort: any Redis error is logged and treated as a miss so a single
    Redis blip cannot break the scrape — the caller will just see a cold session.
    """
    try:
        raw = _redis().get(_key(session_id))
        if not raw:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.warning("storage_state.load(%s) failed: %s", session_id, exc)
        return None


def save(session_id: str, storage_state: dict) -> None:
    """Persist `storage_state` for ~25 minutes. Best-effort."""
    try:
        if not storage_state:
            return
        _redis().setex(_key(session_id), _TTL_SECONDS, json.dumps(storage_state, default=str))
        logger.debug("storage_state.save(%s) cookies=%d",
                     session_id, len(storage_state.get("cookies") or []))
    except Exception as exc:
        logger.warning("storage_state.save(%s) failed: %s", session_id, exc)


def evict(session_id: str) -> None:
    """Drop the cached state — call this after a Cloudflare block on a
    previously-warm session, so the next launch starts cold."""
    try:
        _redis().delete(_key(session_id))
    except Exception as exc:
        logger.warning("storage_state.evict(%s) failed: %s", session_id, exc)


def has_cf_clearance(storage_state: Optional[dict]) -> bool:
    """True if the cached state actually contains a cf_clearance cookie.

    Useful as a quick check before counting a cache hit as "warm" — Indeed
    sometimes sets other cookies (CTK, indeed_csrf_token) on a cold visit
    without Cloudflare ever challenging, and reusing those is fine but
    doesn't skip the CF gauntlet.
    """
    if not storage_state:
        return False
    for c in storage_state.get("cookies") or []:
        if c.get("name") == "cf_clearance":
            return True
    return False
