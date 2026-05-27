"""HTTP-status-aware Cloudflare / Indeed block detection.

Before: indeed_playwright_service.py inspected only response bodies and ignored
HTTP status codes (the Playwright `Response` was read at navigation time but
its `.status` was never consulted in the block-decision branches). That let
403/429 pages whose bodies happened to look like Indeed slip through as
"success" and ship card-snippet "descriptions" to clients.

This module is a pure-function block classifier. The Playwright service calls
`classify(http_status, body, title, body_len)` after every navigation; the
returned `BlockVerdict` tells the caller whether to abort, retry, or proceed.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class BlockReason(str, Enum):
    """Why we decided this response is a block.

    Stored as a string Enum so it round-trips JSON cleanly when logged.
    """

    HTTP_403 = "http_403"
    HTTP_429 = "http_429"
    HTTP_5XX = "http_5xx"
    CF_INTERSTITIAL = "cf_interstitial"     # "Just a moment..." JS challenge
    CF_TURNSTILE = "cf_turnstile"           # explicit Turnstile widget
    CF_RAY_ID_ONLY = "cf_ray_id_only"       # short page, just a Ray ID
    EMPTY_RESPONSE = "empty_response"       # 0-byte body
    DECOY_NO_RESULTS = "decoy_no_results"   # SERP that says "no jobs found" suspiciously


@dataclass(frozen=True)
class BlockVerdict:
    blocked: bool
    reason: Optional[BlockReason] = None
    detail: str = ""

    def __bool__(self) -> bool:  # truthy = blocked
        return self.blocked


# Body markers that indicate a Cloudflare interstitial regardless of HTTP status.
# Cloudflare frequently serves these with a 200 status code, which is why we
# can't rely on http_status alone.
_CF_BODY_MARKERS = (
    "Checking your browser",
    "Enable JavaScript and cookies to continue",
    "challenge-platform",
    "cf-browser-verification",
    "cf-chl-bypass",
    "/cdn-cgi/challenge-platform/",
)

# Title markers — Cloudflare's challenge page sets these even before any
# scripts run.
_CF_TITLE_MARKERS = (
    "Just a moment",
    "Attention Required",
    "Checking",
)


_OK = BlockVerdict(False)


def classify(
    http_status: int,
    body: str,
    title: str = "",
    body_len: Optional[int] = None,
) -> BlockVerdict:
    """Decide whether a page-load response represents a block.

    Order of checks matters: HTTP status first (most authoritative), then
    body shape (Cloudflare often 200s on its challenge pages), then content
    markers, then "short-Ray-ID-stub" heuristic last because it has the
    weakest signal.
    """
    if body_len is None:
        body_len = len(body) if body else 0

    if http_status == 403:
        return BlockVerdict(True, BlockReason.HTTP_403, "Indeed/Cloudflare returned 403")
    if http_status == 429:
        return BlockVerdict(True, BlockReason.HTTP_429, "rate limited (429)")
    if 500 <= http_status < 600:
        return BlockVerdict(True, BlockReason.HTTP_5XX, f"upstream {http_status}")

    if body_len == 0:
        return BlockVerdict(True, BlockReason.EMPTY_RESPONSE, "empty body")

    if "cf-turnstile" in body or "turnstile_iframe" in body:
        return BlockVerdict(True, BlockReason.CF_TURNSTILE, "Turnstile widget present")

    if any(m in body for m in _CF_BODY_MARKERS):
        return BlockVerdict(True, BlockReason.CF_INTERSTITIAL, "CF challenge body marker")

    if title and any(m in title for m in _CF_TITLE_MARKERS):
        return BlockVerdict(True, BlockReason.CF_INTERSTITIAL, f"CF title: {title!r}")

    if "Ray ID" in body and body_len < 5000:
        # CF often returns a tiny "blocked" page with just a Ray ID and
        # contact text. Real Indeed pages always have far more content.
        return BlockVerdict(True, BlockReason.CF_RAY_ID_ONLY, "short page with Ray ID")

    return _OK


def is_decoy_empty_serp(body: str) -> bool:
    """A SERP that loaded fine but says "no jobs found" — when we expected some.

    Indeed sometimes serves a decoy zero-results page to suspicious sessions
    so the scraper happily reports "done" instead of triggering a retry. The
    caller decides whether the zero-result is real (e.g. obscure query) or
    suspect (e.g. common query like "python developer remote").
    """
    if not body:
        return False
    return (
        "We didn&#39;t find any jobs that match" in body
        or "We didn't find any jobs that match" in body
    )
