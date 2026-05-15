"""Employment type normalization."""
import re
from typing import Optional

CANONICAL_MAP = {
    "full-time": "Full-time",
    "full time": "Full-time",
    "fulltime": "Full-time",
    "part-time": "Part-time",
    "part time": "Part-time",
    "contract": "Contract",
    "contractor": "Contract",
    "internship": "Internship",
    "intern": "Internship",
    "temporary": "Temporary",
    "temp": "Temporary",
    "freelance": "Freelance",
}


def normalize_employment_type(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    key = re.sub(r"\s+", " ", value.strip().lower())
    for pattern, canonical in CANONICAL_MAP.items():
        if pattern in key:
            return canonical
    return value.strip().title() if value.strip() else None
