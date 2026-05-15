"""URL canonicalization."""
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

STRIP_QUERY_PREFIXES = ("utm", "utm_", "fbclid", "gclid", "mc_", "ref", "source")


def canonicalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return url
    if not parsed.scheme or not parsed.netloc:
        return url

    query = parse_qs(parsed.query, keep_blank_values=False)
    def _strip_key(key: str) -> bool:
        kl = key.lower()
        return any(kl == p or kl.startswith(p) for p in STRIP_QUERY_PREFIXES)

    filtered = {k: v for k, v in query.items() if not _strip_key(k)}
    new_query = urlencode(filtered, doseq=True)
    path = parsed.path.rstrip("/") or "/"
    netloc = parsed.netloc.lower()

    return urlunparse((
        parsed.scheme.lower(),
        netloc,
        path,
        "",
        new_query,
        "",  # no fragment
    ))
