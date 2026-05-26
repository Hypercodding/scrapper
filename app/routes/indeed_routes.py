"""Indeed-only async scraping routes for the standalone Indeed API service."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

from app.core.job_store import JobStatus, get_job_store
from app.models.job_model import Job

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class IndeedScrapeRequest(BaseModel):
    query: str
    location: Optional[str] = None
    job_type: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    experience_level: Optional[str] = None
    employment_type: Optional[str] = None
    days_old: Optional[int] = None
    max_results: int = 20
    fetch_full_details: bool = True
    sort: Optional[str] = None  # "date" or "relevance" (default)
    radius: Optional[int] = None  # miles: 0 | 5 | 10 | 15 | 25 | 50 | 100


class ScrapeJobResponse(BaseModel):
    job_id: str
    status: str
    message: str
    status_url: str
    estimated_time: Optional[str] = None


class ScrapeJobStatusResponse(BaseModel):
    job_id: str
    status: str
    created_at: str
    updated_at: Optional[str] = None
    progress: Optional[dict] = None
    result: Optional[list] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_JOB_TYPES = {
    "remote", "hybrid", "onsite", "on-site", "in-person", "in person",
    "work from home", "wfh", "telecommute", "telework",
}
# Indeed only exposes three SERP buckets — extras here are mapped onto those
# buckets in app.services.indeed_url_builder.EXPERIENCE_MAP.
_VALID_EXPERIENCE = {
    "intern", "internship", "assistant",
    "entry", "entry-level", "entry level", "junior", "jr",
    "mid", "mid-level", "mid level", "mid-senior", "mid senior", "intermediate",
    "senior", "senior-level", "senior level", "sr", "lead", "principal", "staff",
    "manager", "director", "executive", "vp",
}
_VALID_SORT = {"date", "relevance"}
_VALID_RADIUS = {0, 5, 10, 15, 25, 50, 100}


def _validate(
    query: str,
    max_results: int,
    job_type: Optional[str],
    experience_level: Optional[str],
    sort: Optional[str] = None,
    radius: Optional[int] = None,
) -> None:
    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="query is required and cannot be blank.")
    if len(query) > 300:
        raise HTTPException(status_code=400, detail="query is too long (max 300 chars).")
    if not (1 <= max_results <= 1000):
        raise HTTPException(status_code=400, detail="max_results must be between 1 and 1000.")
    if job_type and job_type.lower() not in _VALID_JOB_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid job_type '{job_type}'.")
    if experience_level and experience_level.lower() not in _VALID_EXPERIENCE:
        raise HTTPException(status_code=400, detail=f"Invalid experience_level '{experience_level}'.")
    if sort is not None and sort.lower() not in _VALID_SORT:
        raise HTTPException(status_code=400, detail=f"Invalid sort '{sort}'. Use 'date' or 'relevance'.")
    if radius is not None and int(radius) not in _VALID_RADIUS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid radius {radius}. Allowed: {sorted(_VALID_RADIUS)}.",
        )


def _enqueue(
    query: str,
    location: Optional[str],
    max_results: int,
    job_type: Optional[str],
    salary_min: Optional[int],
    salary_max: Optional[int],
    experience_level: Optional[str],
    employment_type: Optional[str],
    days_old: Optional[int],
    fetch_full_details: bool,
    sort: Optional[str] = None,
    radius: Optional[int] = None,
) -> str:
    store = get_job_store()
    job_id = store.create(url=f"https://www.indeed.com/jobs?q={query}", max_results=max_results)
    try:
        from app.workers.tasks import scrape_indeed_task
        # New params (sort, radius) passed as kwargs to keep positional args
        # stable for any in-flight queued tasks during the rolling deploy.
        task = scrape_indeed_task.delay(
            job_id, query, location, max_results,
            job_type, salary_min, salary_max,
            experience_level, employment_type, days_old,
            fetch_full_details,
            sort=sort,
            radius=radius,
        )
        store.update(job_id, celery_task_id=task.id)
    except Exception as exc:
        logger.error("Failed to enqueue Indeed task: %s", exc)
        store.set_failed(job_id, str(exc))
        raise HTTPException(
            status_code=503,
            detail="Indeed worker queue unavailable. Ensure Redis and indeed-worker are running.",
        ) from exc
    return job_id


def _make_response(job_id: str) -> ScrapeJobResponse:
    return ScrapeJobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Scrape job enqueued. Poll status_url for results.",
        status_url=f"/api/jobs/{job_id}",
        estimated_time="2–10 minutes depending on max_results and proxy latency",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/jobs/search", response_model=ScrapeJobResponse, status_code=202)
async def search_jobs_get(
    query: str = Query(..., description="Search term, e.g. 'python developer'"),
    location: Optional[str] = Query(None, description="Location: 'remote', 'New York, NY', 'USA', etc."),
    job_type: Optional[str] = Query(None, description="remote | hybrid | onsite"),
    salary_min: Optional[int] = Query(None, description="Minimum salary (annual USD); applied as Indeed sc=salary() facet"),
    salary_max: Optional[int] = Query(None, description="Maximum salary (post-scrape filter only)"),
    experience_level: Optional[str] = Query(None, description="entry | mid | senior (intern→entry, manager/director→senior)"),
    employment_type: Optional[str] = Query(None, description="Full-Time | Part-Time | Contract | Internship | Temporary"),
    days_old: Optional[int] = Query(None, description="Jobs posted within last N days"),
    max_results: int = Query(20, description="1–1000"),
    fetch_full_details: bool = Query(True, description="Visit each job page for full description (slower but richer)"),
    sort: Optional[str] = Query(None, description="'date' (newest first) or 'relevance' (default)"),
    radius: Optional[int] = Query(None, description="Search radius in miles: 0 | 5 | 10 | 15 | 25 | 50 | 100"),
):
    """
    Enqueue an Indeed job search and return a job_id immediately.

    Poll GET /api/jobs/{job_id} every 15–30 seconds until status == 'completed'.
    Backed by 10 Railway worker replicas, each running one Chrome. Retries
    Cloudflare blocks up to 3× with proxy rotation and exponential backoff.
    """
    _validate(query, max_results, job_type, experience_level, sort, radius)
    job_id = _enqueue(query, location, max_results, job_type, salary_min, salary_max,
                      experience_level, employment_type, days_old, fetch_full_details,
                      sort=sort, radius=radius)
    return _make_response(job_id)


@router.post("/jobs/search", response_model=ScrapeJobResponse, status_code=202)
async def search_jobs_post(request: IndeedScrapeRequest):
    """
    Enqueue an Indeed job search (JSON body variant).

    Same as GET /jobs/search but accepts a JSON body — useful for n8n HTTP nodes.
    """
    _validate(request.query, request.max_results, request.job_type, request.experience_level,
              request.sort, request.radius)
    job_id = _enqueue(
        request.query, request.location, request.max_results,
        request.job_type, request.salary_min, request.salary_max,
        request.experience_level, request.employment_type,
        request.days_old, request.fetch_full_details,
        sort=request.sort, radius=request.radius,
    )
    return _make_response(job_id)


@router.get("/jobs/{job_id}", response_model=ScrapeJobStatusResponse)
async def get_job_status(job_id: str):
    """
    Poll the status of an enqueued Indeed scrape job.

    Status: pending → processing → completed | failed
    When completed, 'result' contains the array of job objects.
    """
    data = get_job_store().get(job_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    return ScrapeJobStatusResponse(
        job_id=data["job_id"],
        status=data["status"],
        created_at=data["created_at"],
        updated_at=data.get("updated_at"),
        progress=data.get("progress"),
        result=data.get("result"),
        error=data.get("error"),
    )


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str):
    """Cancel a pending or processing job (best-effort Celery revoke)."""
    store = get_job_store()
    data = store.get(job_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    celery_task_id = data.get("celery_task_id")
    if celery_task_id:
        try:
            from app.workers.celery_app import celery_app
            celery_app.control.revoke(celery_task_id, terminate=True)
        except Exception as exc:
            logger.warning("Could not revoke Celery task %s: %s", celery_task_id, exc)
    store.update(job_id, status=JobStatus.CANCELLED.value)
    return {"job_id": job_id, "status": "cancelled"}


@router.get("/health")
async def health():
    """Health check for Railway."""
    return {"status": "healthy", "service": "indeed-api"}


@router.get("/health/workers")
async def worker_health():
    """Check Redis connectivity and throttle slot counts."""
    try:
        from app.core.host_throttle import get_throttle_counts
        counts = get_throttle_counts()
        return {"status": "ok", "throttle_counts": counts}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}
