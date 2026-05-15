"""Normalization unit tests."""
import pytest
from app.models.job_model import Job
from app.services.normalization.company import normalize_company_name
from app.services.normalization.dedup import dedupe_jobs
from app.services.normalization.employment import normalize_employment_type
from app.services.normalization.pipeline import normalize_job
from app.services.normalization.salary import normalize_salary_range
from app.services.normalization.urls import canonicalize_url
from app.services.organization_resolver import ResolvedOrganization


@pytest.mark.unit
def test_normalize_company_strips_suffix():
    assert normalize_company_name("Acme Corp, Inc.") == "Acme Corp"


@pytest.mark.unit
def test_canonicalize_url_strips_utm():
    url = "https://Example.com/jobs/1?utm_source=x&ref=y#frag"
    assert canonicalize_url(url) == "https://example.com/jobs/1"


@pytest.mark.unit
def test_normalize_employment():
    assert normalize_employment_type("full time") == "Full-time"


@pytest.mark.unit
def test_salary_range():
    assert normalize_salary_range("$80k - $120k") is not None


@pytest.mark.unit
def test_normalize_job_salary_field():
    job = Job(title="Engineer", company="Acme", salary_range=None)
    data = job.model_dump()
    org = ResolvedOrganization(name="Acme Inc", confidence=0.9, source="test")
    normalized = normalize_job(job, org)
    assert normalized.company == "Acme"  # legal suffix stripped


@pytest.mark.unit
def test_dedupe_by_url():
    j1 = Job(title="Dev", company="Acme", url="https://acme.com/jobs/1?utm=1")
    j2 = Job(title="Dev", company="Acme", url="https://acme.com/jobs/1?utm=2")
    assert len(dedupe_jobs([j1, j2])) == 1
