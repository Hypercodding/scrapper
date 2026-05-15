"""Location normalization."""
import re

REMOTE_PATTERNS = [
    (re.compile(r"\bremote\b", re.I), "Remote"),
    (re.compile(r"\bhybrid\b", re.I), "Hybrid"),
    (re.compile(r"\bon-?site\b", re.I), "On-site"),
]

US_STATE = re.compile(r"^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*),\s*([A-Z]{2})$")


from typing import Optional


def normalize_location(location: Optional[str]) -> Optional[str]:
    if not location:
        return None
    text = re.sub(r"\s+", " ", location.strip())
    for pattern, label in REMOTE_PATTERNS:
        if pattern.search(text) and len(text) < 30:
            return label
    m = US_STATE.match(text)
    if m:
        return f"{m.group(1)}, {m.group(2)}"
    return text[:200] if text else None
