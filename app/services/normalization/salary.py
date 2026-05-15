"""Salary range normalization."""
import re
from typing import Optional

SALARY_RANGE_RE = re.compile(
    r"(\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?(?:\s*[kK])?)\s*[-–—to]+\s*"
    r"(\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?(?:\s*[kK])?)",
    re.IGNORECASE,
)


def normalize_salary_range(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = re.sub(r"\s+", " ", value.strip())
    m = SALARY_RANGE_RE.search(text)
    if m:
        return f"{m.group(1).strip()} - {m.group(2).strip()}"
    return text[:120] if text else None
