"""Post-scrape normalization pipeline."""
from typing import List, Optional

from app.models.job_model import Job
from app.services.normalization.company import normalize_company_name
from app.services.normalization.dedup import dedupe_jobs
from app.services.normalization.employment import normalize_employment_type
from app.services.normalization.location import normalize_location
from app.services.normalization.salary import normalize_salary_range
from app.services.normalization.urls import canonicalize_url
from app.services.organization_resolver import ResolvedOrganization


def normalize_job(job: Job, org: Optional[ResolvedOrganization] = None) -> Job:
    data = job.model_dump()
    company = data.get("company")
    if org and org.name and org.confidence >= 0.65:
        data["company"] = normalize_company_name(org.name)
    elif company:
        data["company"] = normalize_company_name(company)

    if data.get("location"):
        data["location"] = normalize_location(data["location"])
    if data.get("employment_type"):
        data["employment_type"] = normalize_employment_type(data["employment_type"])
    if data.get("job_type"):
        data["job_type"] = normalize_employment_type(data["job_type"])
    # Fix salary -> salary_range
    salary = data.pop("salary", None) if "salary" in data else None
    sr = data.get("salary_range") or salary
    data["salary_range"] = normalize_salary_range(sr)
    if data.get("url"):
        data["url"] = canonicalize_url(data["url"])
    if org and org.company_url and not data.get("company_url"):
        cu = org.company_url
        if "greenhouse.io" not in cu and "lever.co" not in cu and "ashbyhq.com" not in cu:
            data["company_url"] = cu
    return Job(**{k: v for k, v in data.items() if k in Job.model_fields})


def normalize_and_dedupe_jobs(
    jobs: List[Job],
    org: Optional[ResolvedOrganization] = None,
) -> List[Job]:
    normalized = [normalize_job(j, org) for j in jobs]
    return dedupe_jobs(normalized)
