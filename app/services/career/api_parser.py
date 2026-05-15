"""Parse job records from ATS JSON API responses."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _location_name_from_value(value: Any) -> Optional[str]:
    """Extract human-readable location from API field shapes."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") and "name" in text:
            return None
        return text or None
    if isinstance(value, dict):
        name = value.get("name") or value.get("city") or value.get("label")
        if name:
            return str(name).strip()
        parts = [value.get("city"), value.get("state"), value.get("country")]
        joined = ", ".join(p for p in parts if p)
        return joined or None
    if isinstance(value, list):
        names: List[str] = []
        for entry in value:
            part = _location_name_from_value(entry)
            if part and part not in names:
                names.append(part)
        return "; ".join(names) if names else None
    return None


def extract_api_location(item: dict) -> Optional[str]:
    for key in ("location", "locations", "office", "offices", "workLocation", "city"):
        if key not in item:
            continue
        loc = _location_name_from_value(item[key])
        if loc:
            return loc
    return None


def extract_api_url(item: dict) -> Optional[str]:
    for key in (
        "absolute_url",
        "absoluteUrl",
        "url",
        "link",
        "applyUrl",
        "apply_url",
        "hostedUrl",
        "hosted_url",
        "jobUrl",
        "job_url",
    ):
        val = item.get(key)
        if val and isinstance(val, str) and val.startswith("http"):
            return val.strip()
    return None


def extract_api_title(item: dict) -> Optional[str]:
    for key in ("title", "name", "position", "jobTitle", "job_title", "positionTitle", "text"):
        val = item.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return None


def extract_api_company(item: dict, fallback: str) -> str:
    for key in ("company_name", "companyName", "company"):
        val = item.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    return fallback


def extract_api_company_url(item: dict, job_url: Optional[str], fallback: Optional[str]) -> Optional[str]:
    if job_url and "greenhouse.io" not in job_url and "lever.co" not in job_url:
        from urllib.parse import urlparse
        parsed = urlparse(job_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    for key in ("company_url", "companyUrl", "careers_url"):
        val = item.get(key)
        if val and isinstance(val, str) and val.startswith("http"):
            return val
    return fallback


def extract_api_description(item: dict) -> Optional[str]:
    for key in ("description", "descriptionPlain", "summary", "details", "content"):
        val = item.get(key)
        if val and isinstance(val, str):
            return val[:500]
    return None


def extract_api_employment_type(item: dict) -> Optional[str]:
    for key in ("employmentType", "employment_type", "type", "jobType", "commitment"):
        val = item.get(key)
        if val and isinstance(val, str):
            return val
    return None


def extract_api_salary(item: dict) -> Optional[str]:
    for key in ("salary", "compensation", "salaryRange", "pay"):
        val = item.get(key)
        if not val:
            continue
        if isinstance(val, dict):
            lo, hi = val.get("min"), val.get("max")
            if lo or hi:
                return f"{lo or ''} - {hi or ''}".strip(" -")
        return str(val)
    return None


def extract_api_posted_date(item: dict) -> Optional[str]:
    for key in ("first_published", "postedDate", "createdAt", "publishedAt", "datePosted", "updated_at"):
        val = item.get(key)
        if val:
            return str(val)
    return None


def parse_api_job_item(
    item: dict,
    company_name: str,
    company_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return a dict of Job fields from one API posting object."""
    if not isinstance(item, dict):
        return None
    title = extract_api_title(item)
    if not title:
        return None
    job_url = extract_api_url(item)
    company = extract_api_company(item, company_name)
    return {
        "title": title,
        "company": company,
        "company_url": extract_api_company_url(item, job_url, company_url),
        "location": extract_api_location(item),
        "description": extract_api_description(item),
        "url": job_url,
        "employment_type": extract_api_employment_type(item),
        "salary_range": extract_api_salary(item),
        "posted_date": extract_api_posted_date(item),
    }


def collect_job_arrays(data: Any) -> List[list]:
    arrays: List[list] = []
    if isinstance(data, list):
        arrays.append(data)
    elif isinstance(data, dict):
        for key in ("jobs", "positions", "openings", "postings", "results", "data", "items"):
            if key in data and isinstance(data[key], list):
                arrays.append(data[key])
    return arrays
