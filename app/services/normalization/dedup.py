"""Job deduplication."""
from typing import List, Set, Tuple

from app.models.job_model import Job
from app.services.normalization.company import normalize_company_name
from app.services.normalization.urls import canonicalize_url


def _dedup_key(job: Job) -> Tuple[str, ...]:
    url = canonicalize_url(job.url) or ""
    if url:
        return ("url", url.lower())
    title = (job.title or "").lower().strip()
    company = normalize_company_name(job.company or "").lower()
    location = (job.location or "").lower().strip()
    return ("tuple", title, company, location)


def dedupe_jobs(jobs: List[Job]) -> List[Job]:
    seen: Set[Tuple[str, ...]] = set()
    result: List[Job] = []
    for job in jobs:
        key = _dedup_key(job)
        if key in seen:
            continue
        seen.add(key)
        result.append(job)
    return result
