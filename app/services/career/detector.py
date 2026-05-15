"""ATS job board detection and API URL construction."""
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# Mirrors generic_career_scraper JOB_BOARD_PATTERNS with api builders
JOB_BOARD_PATTERNS: Dict[str, Dict[str, Any]] = {
    "greenhouse": {
        "domains": ["greenhouse.io", "boards.greenhouse.io"],
        "api_pattern": r"/boards/([^/]+)/jobs",
        "api_url_template": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    },
    "lever": {
        "domains": ["lever.co", "jobs.lever.co"],
        "api_pattern": r"https://api.lever.co/v0/postings/([^/]+)",
        "api_url_template": "https://api.lever.co/v0/postings/{slug}?mode=json",
    },
    "workday": {
        "domains": ["myworkdayjobs.com"],
        "api_pattern": r"/wday/cxs/([^/]+)/jobs",
    },
    "smartrecruiters": {
        "domains": ["smartrecruiters.com"],
        "api_pattern": r"https://api.smartrecruiters.com/v1/companies/([^/]+)/postings",
        "api_url_template": "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
    },
    "ashbyhq": {
        "domains": ["ashbyhq.com"],
        "api_pattern": r"ashbyhq.com/api/posting-api/job-board/([^/]+)",
        "api_url_template": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    },
    "workable": {
        "domains": ["apply.workable.com"],
        "api_url_template": "https://apply.workable.com/api/v1/widget/accounts/{slug}/jobs",
    },
}


def detect_job_board(url: str) -> Optional[Dict[str, Any]]:
    url_lower = url.lower()
    for board_name, config in JOB_BOARD_PATTERNS.items():
        if any(domain in url_lower for domain in config["domains"]):
            return {"name": board_name, "config": config}
    return None


def _extract_slug(url: str, config: Dict[str, Any]) -> Optional[str]:
    parsed = urlparse(url)
    path = parsed.path or ""
    host = (parsed.netloc or "").lower()

    if "boards.greenhouse.io" in host or "greenhouse.io" in host:
        m = re.search(r"/([^/?#]+)/jobs", path) or re.search(r"/boards/([^/?#]+)", path)
        if m:
            return m.group(1)
        parts = [p for p in path.strip("/").split("/") if p]
        if parts and parts[0] not in ("jobs", "embed"):
            return parts[0]

    if "lever.co" in host:
        m = re.search(r"/([^/?#]+)", path)
        if m and m.group(1) not in ("v0", "postings"):
            return m.group(1)

    if "ashbyhq.com" in host:
        m = re.search(r"/([^/?#]+)", path)
        if m:
            return m.group(1)

    if "smartrecruiters.com" in host:
        m = re.search(r"/([^/?#]+)", path)
        if m and m.group(1) != "careers":
            return m.group(1)

    if "workable.com" in host:
        m = re.search(r"/([^/?#]+)", path)
        if m:
            return m.group(1)

    api_pattern = config.get("api_pattern")
    if api_pattern:
        m = re.search(api_pattern, url)
        if m:
            return m.group(1)
    return None


def build_ats_api_urls(url: str) -> List[str]:
    """Build known ATS JSON API URLs from a career page URL."""
    board = detect_job_board(url)
    if not board:
        return []

    config = board["config"]
    template = config.get("api_url_template")
    if not template:
        return []

    slug = _extract_slug(url, config)
    if not slug:
        return []

    return [template.format(slug=slug)]
