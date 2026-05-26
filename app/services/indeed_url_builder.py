"""
Indeed search URL builder.

Translates user-facing API parameters into Indeed's native query string,
including the faceted `sc=0kf:...` encoding required for experience level
and remote/hybrid/onsite filtering. The previous URL builder embedded in
`indeed_playwright_service.py` silently dropped most filters; this module
makes the encoding explicit and testable.
"""
from __future__ import annotations

from typing import List, Optional
from urllib.parse import quote_plus


BASE_URL = "https://www.indeed.com/jobs"


# Experience level → Indeed's `explvl()` token. Indeed only exposes three
# buckets in its SERP facet; map adjacent terms onto the closest one.
EXPERIENCE_MAP = {
    "entry": "ENTRY_LEVEL",
    "entry-level": "ENTRY_LEVEL",
    "entry level": "ENTRY_LEVEL",
    "junior": "ENTRY_LEVEL",
    "jr": "ENTRY_LEVEL",
    "intern": "ENTRY_LEVEL",
    "internship": "ENTRY_LEVEL",
    "assistant": "ENTRY_LEVEL",
    "mid": "MID_LEVEL",
    "mid-level": "MID_LEVEL",
    "mid level": "MID_LEVEL",
    "intermediate": "MID_LEVEL",
    "mid-senior": "MID_LEVEL",
    "mid senior": "MID_LEVEL",
    "senior": "SENIOR_LEVEL",
    "senior-level": "SENIOR_LEVEL",
    "senior level": "SENIOR_LEVEL",
    "sr": "SENIOR_LEVEL",
    "lead": "SENIOR_LEVEL",
    "principal": "SENIOR_LEVEL",
    "staff": "SENIOR_LEVEL",
    "manager": "SENIOR_LEVEL",
    "director": "SENIOR_LEVEL",
    "executive": "SENIOR_LEVEL",
    "vp": "SENIOR_LEVEL",
}

# Employment type → Indeed's `jt=` value.
EMPLOYMENT_TYPE_MAP = {
    "full-time": "fulltime",
    "fulltime": "fulltime",
    "full time": "fulltime",
    "part-time": "parttime",
    "parttime": "parttime",
    "part time": "parttime",
    "contract": "contract",
    "internship": "internship",
    "intern": "internship",
    "temporary": "temporary",
    "temp": "temporary",
}

# Remote/hybrid/onsite → Indeed's `attr()` token in the `sc=0kf:` facet.
# DSQF7 = Remote, PAXZC = Hybrid, CF3CP = In-person (onsite).
REMOTE_TYPE_ATTR = {
    "remote": "DSQF7",
    "work from home": "DSQF7",
    "wfh": "DSQF7",
    "telecommute": "DSQF7",
    "telework": "DSQF7",
    "hybrid": "PAXZC",
    "onsite": "CF3CP",
    "on-site": "CF3CP",
    "in-person": "CF3CP",
    "in person": "CF3CP",
}

VALID_SORT = {"date", "relevance"}
VALID_RADIUS = {0, 5, 10, 15, 25, 50, 100}


def _format_location(location: str) -> str:
    """Light normalization for the `l=` parameter; URL-encoding is applied by the caller."""
    return location.strip()


def _experience_token(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return EXPERIENCE_MAP.get(value.lower().strip())


def _employment_jt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return EMPLOYMENT_TYPE_MAP.get(value.lower().strip())


def _remote_attr(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return REMOTE_TYPE_ATTR.get(value.lower().strip())


def build_indeed_search_url(
    query: str,
    location: Optional[str] = None,
    job_type: Optional[str] = None,
    employment_type: Optional[str] = None,
    experience_level: Optional[str] = None,
    salary_min: Optional[int] = None,
    days_old: Optional[int] = None,
    sort: Optional[str] = None,
    radius: Optional[int] = None,
    start: int = 0,
) -> str:
    """Construct a fully-encoded Indeed SERP URL.

    Multiple faceted filters (experience level, remote/hybrid attr) are merged
    into a single `sc=0kf:facet1;facet2;` value, matching Indeed's own URL form
    when you click multiple SERP filters in the browser.
    """
    if not query or not query.strip():
        raise ValueError("query is required")

    parts: List[str] = [f"q={quote_plus(query.strip())}"]

    if location:
        parts.append(f"l={quote_plus(_format_location(location))}")

    jt = _employment_jt(employment_type)
    if jt:
        parts.append(f"jt={jt}")

    if days_old and int(days_old) > 0:
        parts.append(f"fromage={int(days_old)}")

    sc_facets: List[str] = []
    explvl = _experience_token(experience_level)
    if explvl:
        sc_facets.append(f"explvl({explvl})")

    remote_attr = _remote_attr(job_type)
    if remote_attr:
        sc_facets.append(f"attr({remote_attr})")

    if salary_min and int(salary_min) > 0:
        # Indeed encodes salary as a faceted attribute too. The legacy `&salary=`
        # param was deprecated; this is the form that actually filters.
        sc_facets.append(f"salary({int(salary_min)})")

    if sc_facets:
        sc_value = "0kf:" + ";".join(sc_facets) + ";"
        parts.append(f"sc={quote_plus(sc_value)}")

    if radius is not None:
        r = int(radius)
        if r in VALID_RADIUS:
            parts.append(f"radius={r}")

    if sort and sort.lower() in VALID_SORT and sort.lower() != "relevance":
        parts.append(f"sort={sort.lower()}")

    if start and int(start) > 0:
        parts.append(f"start={int(start)}")

    return f"{BASE_URL}?" + "&".join(parts)
