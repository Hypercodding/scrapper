
from fastapi import APIRouter, Query, HTTPException, Body, Response
from typing import List, Optional, Dict, Any, Callable, Tuple
from datetime import datetime
from enum import Enum
import asyncio
import logging
import uuid
from urllib.parse import urlparse
from app.models.job_model import Job # pylint: disable=import-error
from app.core.config import settings # pylint: disable=import-error
from app.core.scrape_executor import scrape_execution_context, ScrapeInProgressError, ScrapeTimeoutError, get_execution_status, SCRAPE_TIMEOUT # pylint: disable=import-error
from app.core.browser_executor import hard_kill_all_browsers, verify_cleanup # pylint: disable=import-error
from app.services.indeed_selenium_service import (
    scrape_indeed_selenium,
    CloudflareBlockedError,
    check_chrome_process_count
) # pylint: disable=import-error
try:
    from app.services.indeed_playwright_service import scrape_indeed_playwright, CloudflareBlockedError as PlaywrightCloudflareBlockedError
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    PlaywrightCloudflareBlockedError = None
from app.services.ziprecruiter_service import scrape_ziprecruiter # pylint: disable=import-error
from app.services.ziprecruiter_enhanced_service import scrape_ziprecruiter_enhanced # pylint: disable=import-error
from app.services.simplyhired_playwright_service import (
    scrape_simplyhired_playwright,
    CloudflareBlockedError as SimplyHiredCloudflareBlockedError
) # pylint: disable=import-error
from app.services.generic_career_scraper import scrape_generic_career_page, scrape_multiple_career_pages # pylint: disable=import-error
from app.core.job_store import JobStore, JobStatus as RedisJobStatus, get_job_store
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _enqueue_career_scrape(
    url: str,
    max_results: Optional[int] = None,
    search_query: Optional[str] = None,
) -> str:
    """Create Redis job record and dispatch Celery task."""
    store = get_job_store()
    job_id = store.create(url=url, max_results=max_results, search_query=search_query)
    try:
        from app.workers.tasks import scrape_career_page_task
        task = scrape_career_page_task.delay(job_id, url, max_results, search_query)
        store.update(job_id, celery_task_id=task.id)
    except Exception as exc:
        logger.error("Failed to enqueue Celery task: %s", exc)
        store.set_failed(job_id, f"Failed to enqueue scrape: {exc}")
        raise HTTPException(
            status_code=503,
            detail="Job queue unavailable. Ensure Redis and Celery worker are running.",
        ) from exc
    return job_id


def _enqueue_indeed_scrape(
    query: str,
    location: Optional[str] = None,
    max_results: int = 20,
    job_type: Optional[str] = None,
    salary_min: Optional[int] = None,
    salary_max: Optional[int] = None,
    experience_level: Optional[str] = None,
    employment_type: Optional[str] = None,
    days_old: Optional[int] = None,
    fetch_full_details: bool = True,
) -> str:
    """Create Redis job record and dispatch scrape_indeed_task to the indeed worker queue."""
    store = get_job_store()
    # Store minimal metadata in the job record
    job_id = store.create(url=f"https://www.indeed.com/jobs?q={query}", max_results=max_results)
    try:
        from app.workers.tasks import scrape_indeed_task
        task = scrape_indeed_task.delay(
            job_id, query, location, max_results,
            job_type, salary_min, salary_max,
            experience_level, employment_type, days_old,
            fetch_full_details,
        )
        store.update(job_id, celery_task_id=task.id)
    except Exception as exc:
        logger.error("Failed to enqueue Indeed Celery task: %s", exc)
        store.set_failed(job_id, f"Failed to enqueue Indeed scrape: {exc}")
        raise HTTPException(
            status_code=503,
            detail=(
                "Indeed job queue unavailable. "
                "Ensure Redis and the indeed-worker service are running."
            ),
        ) from exc
    return job_id


def _validate_indeed_params(
    query: str,
    max_results: int,
    job_type: Optional[str],
    experience_level: Optional[str],
) -> None:
    """Raise HTTPException for obviously bad Indeed request parameters."""
    if not query or not query.strip():
        raise HTTPException(status_code=400, detail="query is required and cannot be blank.")
    if len(query) > 300:
        raise HTTPException(status_code=400, detail="query is too long (max 300 chars).")
    if max_results < 1 or max_results > 1000:
        raise HTTPException(status_code=400, detail="max_results must be between 1 and 1000.")
    valid_job_types = {"remote", "hybrid", "onsite", "on-site", "work from home", "wfh", None}
    if job_type and job_type.lower() not in valid_job_types:
        raise HTTPException(status_code=400, detail=f"Invalid job_type '{job_type}'.")
    valid_experience = {"intern", "internship", "assistant", "entry", "junior", "mid", "mid-senior", "senior", "director", "manager", "executive", None}
    if experience_level and experience_level.lower() not in valid_experience:
        raise HTTPException(status_code=400, detail=f"Invalid experience_level '{experience_level}'.")


router = APIRouter()


def _validate_career_url(url: str) -> None:
    """Validate career page URL for generic scraping. Raises HTTPException if invalid."""
    if not url or not isinstance(url, str):
        raise HTTPException(status_code=400, detail="URL is required and must be a non-empty string.")
    url = url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be blank.")
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid URL: {str(e)}")
    if not parsed.scheme:
        raise HTTPException(status_code=400, detail="URL must include scheme (e.g. https://).")
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="URL must use http or https.")
    if not parsed.netloc:
        raise HTTPException(status_code=400, detail="URL must have a host (e.g. example.com).")
    if len(url) > 2048:
        raise HTTPException(status_code=400, detail="URL is too long.")


def _validate_career_url_safe(url: str) -> Tuple[bool, Optional[str]]:
    """Validate URL without raising. Returns (True, None) if valid else (False, error_message)."""
    if not url or not isinstance(url, str):
        return False, "URL is required and must be a non-empty string."
    url = url.strip()
    if not url:
        return False, "URL cannot be blank."
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"Invalid URL: {str(e)}"
    if not parsed.scheme:
        return False, "URL must include scheme (e.g. https://)."
    if parsed.scheme not in ("http", "https"):
        return False, "URL must use http or https."
    if not parsed.netloc:
        return False, "URL must have a host (e.g. example.com)."
    if len(url) > 2048:
        return False, "URL is too long."
    return True, None


# In-memory job storage with thread safety
# Capped to prevent OOM when run continuously for many customers (e.g. on Railway)
MAX_STORED_JOBS = 200  # Evict oldest completed/failed when exceeding
_job_storage: Dict[str, Dict[str, Any]] = {}
_job_lock: Optional[asyncio.Lock] = None

# Multi-URL (company hit list) limits - prevent runaway memory and timeouts on Railway
MAX_URLS_PER_REQUEST = 50  # Reject or truncate if more
MULTI_URL_SCRAPE_TIMEOUT = 900  # 15 minutes total for entire multi-URL run
MAX_RESULTS_PER_URL_DEFAULT = 500  # Cap per-URL when not specified (avoid 999999)
MAX_TOTAL_RESULTS_DEFAULT = 2000  # Cap total when not specified


def _get_job_lock() -> asyncio.Lock:
    """Get or create the job lock (lazy initialization for async context)"""
    global _job_lock
    if _job_lock is None:
        _job_lock = asyncio.Lock()
    return _job_lock


def _evict_old_jobs_if_needed() -> None:
    """
    Evict oldest completed/failed jobs when storage exceeds MAX_STORED_JOBS.
    Prevents OOM when run continuously for many customers (e.g. on Railway).
    Must be called with _job_lock held by the caller.
    """
    if len(_job_storage) <= MAX_STORED_JOBS:
        return
    evictable = [
        (job_id, data.get("updated_at") or data.get("created_at", ""))
        for job_id, data in _job_storage.items()
        if data.get("status") in (JobStatus.COMPLETED, JobStatus.FAILED)
    ]
    evictable.sort(key=lambda x: x[1])
    to_remove = len(_job_storage) - MAX_STORED_JOBS
    for job_id, _ in evictable[:to_remove]:
        if job_id in _job_storage and _job_storage[job_id].get("status") in (
            JobStatus.COMPLETED,
            JobStatus.FAILED,
        ):
            del _job_storage[job_id]


class JobStatus(str, Enum):
    """Job status enum"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CareerPageRequest(BaseModel):
    url: str
    max_results: Optional[int] = None
    search_query: Optional[str] = None


class MultipleCareerPagesRequest(BaseModel):
    urls: List[str]
    max_results_per_url: Optional[int] = None
    search_query: Optional[str] = None
    total_max_results: Optional[int] = None


class ScrapeJobResponse(BaseModel):
    """Response model for async scrape job creation"""
    job_id: str
    status: str
    message: str
    status_url: str
    estimated_time: Optional[str] = None


class ScrapeJobStatusResponse(BaseModel):
    """Response model for job status check"""
    job_id: str
    status: str
    created_at: str
    updated_at: Optional[str] = None
    progress: Optional[Dict[str, Any]] = None
    result: Optional[List[Job]] = None
    error: Optional[str] = None


def _job_status_response(job_data: Dict[str, Any]) -> ScrapeJobStatusResponse:
    result = job_data.get("result")
    jobs = [Job(**j) for j in result] if result is not None else None
    return ScrapeJobStatusResponse(
        job_id=job_data["job_id"],
        status=job_data["status"],
        created_at=job_data["created_at"],
        updated_at=job_data.get("updated_at"),
        progress=job_data.get("progress"),
        result=jobs,
        error=job_data.get("error"),
    )


def _get_job_data(job_id: str) -> Optional[Dict[str, Any]]:
    data = get_job_store().get(job_id)
    if data:
        return data
    return _job_storage.get(job_id)


async def _execute_sync_scrape(
    scrape_func: Callable,
    *args,
    **kwargs
):
    """
    Execute a synchronous scraping function with single-concurrency enforcement.
    
    This wrapper ensures:
    1. Only one scrape runs at a time (global lock)
    2. Cleanup happens even for sync functions
    3. Proper error handling
    """
    async with scrape_execution_context():
        # Execute sync function in thread pool
        return await asyncio.to_thread(scrape_func, *args, **kwargs)


async def _execute_async_scrape(
    scrape_func: Callable,
    *args,
    **kwargs
):
    """
    Execute an asynchronous scraping function with single-concurrency enforcement.
    
    This wrapper ensures:
    1. Only one scrape runs at a time (global lock)
    2. Cleanup happens even for async functions
    3. Proper error handling
    """
    async with scrape_execution_context():
        # Execute async function directly
        return await scrape_func(*args, **kwargs)


@router.get("/jobs/indeed-async", response_model=ScrapeJobResponse, status_code=202)
async def get_indeed_jobs_async(
    query: str = Query(..., description="Search term, e.g. 'python developer'"),
    location: Optional[str] = Query(None, description="Job location. Examples: 'remote', 'New York, NY', 'USA'"),
    job_type: Optional[str] = Query(None, description="Job type: 'remote', 'hybrid', 'onsite'"),
    salary_min: Optional[int] = Query(None, description="Minimum salary filter"),
    salary_max: Optional[int] = Query(None, description="Maximum salary filter"),
    experience_level: Optional[str] = Query(None, description="Experience level: 'entry', 'mid', 'senior', etc."),
    employment_type: Optional[str] = Query(None, description="Employment type: 'Full-Time', 'Part-Time', 'Contract', 'Internship'"),
    days_old: Optional[int] = Query(None, description="Filter jobs posted within last N days"),
    max_results: int = Query(20, description="Maximum number of results (1–1000)"),
    fetch_full_details: bool = Query(True, description="Visit each job page for complete details (slower but richer data)"),
):
    """
    [ASYNC] Search Indeed jobs via distributed workers — returns immediately with a job_id.

    Use GET /api/jobs/{job_id} to poll for results. Status transitions:
    pending → processing → completed | failed

    Scale: backed by 10 Railway worker replicas (concurrency=1 each, one Chrome per replica).
    Retries Cloudflare blocks up to 3 times with proxy rotation and exponential backoff.
    """
    _validate_indeed_params(query, max_results, job_type, experience_level)
    job_id = _enqueue_indeed_scrape(
        query=query, location=location, max_results=max_results,
        job_type=job_type, salary_min=salary_min, salary_max=salary_max,
        experience_level=experience_level, employment_type=employment_type,
        days_old=days_old, fetch_full_details=fetch_full_details,
    )
    return ScrapeJobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Indeed scrape job enqueued. Poll the status_url for results.",
        status_url=f"/api/jobs/{job_id}",
        estimated_time="2–10 minutes depending on max_results and proxy latency",
    )


@router.post("/jobs/indeed-async", response_model=ScrapeJobResponse, status_code=202)
async def post_indeed_jobs_async(
    query: str = Body(...),
    location: Optional[str] = Body(None),
    job_type: Optional[str] = Body(None),
    salary_min: Optional[int] = Body(None),
    salary_max: Optional[int] = Body(None),
    experience_level: Optional[str] = Body(None),
    employment_type: Optional[str] = Body(None),
    days_old: Optional[int] = Body(None),
    max_results: int = Body(20),
    fetch_full_details: bool = Body(True),
):
    """
    [ASYNC] Search Indeed jobs via distributed workers — returns immediately with a job_id.

    Same as GET /api/jobs/indeed-async but accepts a JSON body.
    """
    _validate_indeed_params(query, max_results, job_type, experience_level)
    job_id = _enqueue_indeed_scrape(
        query=query, location=location, max_results=max_results,
        job_type=job_type, salary_min=salary_min, salary_max=salary_max,
        experience_level=experience_level, employment_type=employment_type,
        days_old=days_old, fetch_full_details=fetch_full_details,
    )
    return ScrapeJobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Indeed scrape job enqueued. Poll the status_url for results.",
        status_url=f"/api/jobs/{job_id}",
        estimated_time="2–10 minutes depending on max_results and proxy latency",
    )


@router.get("/jobs", response_model=ScrapeJobResponse, status_code=202)
async def get_jobs(
    query: str = Query(..., description="Search term, e.g. 'python developer'"),
    location: Optional[str] = Query(None),
    job_type: Optional[str] = Query(None),
    salary_min: Optional[int] = Query(None),
    salary_max: Optional[int] = Query(None),
    experience_level: Optional[str] = Query(None),
    employment_type: Optional[str] = Query(None),
    days_old: Optional[int] = Query(None),
    max_results: int = Query(20),
    response: Response = None,
):
    """
    [DEPRECATED] Use GET /api/jobs/indeed-async instead.

    This endpoint now returns 202 + job_id immediately (async pattern) rather than
    blocking until scraping completes. Existing callers should migrate to polling
    GET /api/jobs/{job_id} for results.
    """
    if response:
        response.headers["Deprecation"] = "true"
        response.headers["Link"] = '</api/jobs/indeed-async>; rel="successor-version"'
    _validate_indeed_params(query, max_results, job_type, experience_level)
    job_id = _enqueue_indeed_scrape(
        query=query, location=location, max_results=max_results,
        job_type=job_type, salary_min=salary_min, salary_max=salary_max,
        experience_level=experience_level, employment_type=employment_type,
        days_old=days_old,
    )
    return ScrapeJobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message=(
            "DEPRECATED: This endpoint is now async. "
            "Poll GET /api/jobs/{job_id} for results. "
            "Migrate callers to /api/jobs/indeed-async."
        ),
        status_url=f"/api/jobs/{job_id}",
        estimated_time="2–10 minutes",
    )


@router.get("/jobs/indeed-playwright", response_model=ScrapeJobResponse, status_code=202)
async def get_indeed_playwright_jobs(
    query: str = Query(...),
    location: Optional[str] = Query(None),
    job_type: Optional[str] = Query(None),
    salary_min: Optional[int] = Query(None),
    salary_max: Optional[int] = Query(None),
    experience_level: Optional[str] = Query(None),
    employment_type: Optional[str] = Query(None),
    days_old: Optional[int] = Query(None),
    max_results: int = Query(20),
    fetch_full_details: bool = Query(True),
    response: Response = None,
):
    """[DEPRECATED] Alias for GET /api/jobs/indeed-async."""
    if response:
        response.headers["Deprecation"] = "true"
        response.headers["Link"] = '</api/jobs/indeed-async>; rel="successor-version"'
    _validate_indeed_params(query, max_results, job_type, experience_level)
    job_id = _enqueue_indeed_scrape(
        query=query, location=location, max_results=max_results,
        job_type=job_type, salary_min=salary_min, salary_max=salary_max,
        experience_level=experience_level, employment_type=employment_type,
        days_old=days_old, fetch_full_details=fetch_full_details,
    )
    return ScrapeJobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Deprecated — use /api/jobs/indeed-async. Poll /api/jobs/{job_id} for results.",
        status_url=f"/api/jobs/{job_id}",
        estimated_time="2–10 minutes",
    )


@router.get("/jobs/indeed-self-test")
async def indeed_self_test(q: str = Query("python developer"), l: Optional[str] = Query("remote")):
    """Enqueue a small Indeed scrape (max_results=5) to verify the worker pipeline end-to-end."""
    try:
        job_id = _enqueue_indeed_scrape(query=q, location=l, max_results=5)
        return {
            "ok": True,
            "job_id": job_id,
            "status_url": f"/api/jobs/{job_id}",
            "note": "Poll status_url every 15s; check status=='completed' for results.",
        }
    except HTTPException as e:
        return {"ok": False, "error": e.detail}
    except Exception as e:
        return {"ok": False, "error": str(e)}



@router.get("/jobs/ziprecruiter", response_model=List[Job])
async def get_ziprecruiter_jobs(
    query: str = Query(..., description="Search term, e.g. 'python developer'"),
    location: Optional[str] = Query(None, description="Job location, e.g. 'remote', 'New York'"),
    max_results: int = Query(20, description="Maximum number of results (default: 20)")
):
    """
    Get jobs from ZipRecruiter using browser automation (Selenium)
    
    This may work better than Indeed as ZipRecruiter has less aggressive anti-scraping measures.
    """
    try:
        async with scrape_execution_context():
            jobs = await scrape_ziprecruiter(query, location, max_results)
    except ScrapeInProgressError as e:
        raise HTTPException(
            status_code=429,
            detail=f"Another scraping operation is currently in progress. Please wait and try again. {str(e)}"
        )
    except ScrapeTimeoutError as e:
        raise HTTPException(
            status_code=504,
            detail=f"Scraping operation timed out. {str(e)}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return jobs


@router.get("/jobs/ziprecruiter-enhanced", response_model=List[Job])
async def get_ziprecruiter_enhanced_jobs(
    query: str = Query(..., description="Search term, e.g. 'python developer'"),
    location: Optional[str] = Query(None, description="Job location, e.g. 'remote', 'Lahore', 'New York', 'USA'"),
    job_type: Optional[str] = Query(None, description="Job type filter: 'remote', 'hybrid', 'onsite', 'on-site'"),
    max_results: int = Query(20, description="Maximum number of results (default: 20)")
):
    """
    Get detailed jobs from ZipRecruiter with enhanced information extraction
    
    ⭐ ENHANCED VERSION: Extracts salary ranges, company URLs, job descriptions, 
    job types, experience levels, benefits, requirements, skills, and more!
    
    Location Examples:
    - 'remote' or 'work from home' - Remote jobs only
    - 'Lahore' - Jobs in Lahore, Pakistan
    - 'New York' - Jobs in New York, NY
    - 'USA' - Jobs in United States
    - 'Pakistan' - Jobs in Pakistan
    
    Job Type Filter:
    - 'remote' - Remote jobs only
    - 'hybrid' - Hybrid jobs only  
    - 'onsite' or 'on-site' - On-site jobs only
    
    Returns detailed job information including:
    - Job title, company, company URL
    - Location and remote type (Remote/Hybrid/On-site)
    - Salary range and job type (Full-time/Part-time/Contract)
    - Experience level (Entry/Mid/Senior/Executive)
    - Posted date and job description
    - Skills, requirements, and benefits
    - Industry and company size
    - Job ID for tracking
    """
    try:
        async with scrape_execution_context():
            jobs = await scrape_ziprecruiter_enhanced(query, location, max_results, job_type)
    except ScrapeInProgressError as e:
        raise HTTPException(
            status_code=429,
            detail=f"Another scraping operation is currently in progress. Please wait and try again. {str(e)}"
        )
    except ScrapeTimeoutError as e:
        raise HTTPException(
            status_code=504,
            detail=f"Scraping operation timed out. {str(e)}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return jobs


@router.get("/jobs/simplyhired", response_model=List[Job])
async def get_simplyhired_jobs(
    query: str = Query(..., description="Search term, e.g. 'python developer'"),
    location: Optional[str] = Query(None, description="Job location (flexible format). Examples: 'remote', 'New York, NY', 'Lahore, Pakistan', 'USA', 'California'"),
    job_type: Optional[str] = Query(None, description="Job type filter: 'remote', 'hybrid', 'onsite', 'On-site'"),
    salary_min: Optional[int] = Query(None, description="Minimum salary filter (e.g., 50000)"),
    salary_max: Optional[int] = Query(None, description="Maximum salary filter (e.g., 100000)"),
    experience_level: Optional[str] = Query(None, description="Experience level filter: 'intern', 'assistant', 'entry', 'junior', 'mid', 'mid-senior', 'senior', 'director', 'executive'"),
    employment_type: Optional[str] = Query(None, description="Employment type filter: 'Full-Time', 'Part-Time', 'Contract', 'Internship'"),
    days_old: Optional[int] = Query(None, description="Filter jobs posted within last N days (e.g., 30 for last 30 days)"),
    max_results: int = Query(20, description="Maximum number of results (default: 20)"),
):
    """
    Get jobs from SimplyHired using Playwright with stealth (better Cloudflare bypass)
    
    ⭐ ENHANCED VERSION: Comprehensive data extraction and advanced filtering!
    
    Features:
    - Extracts salary ranges, company URLs, job descriptions
    - Job types, experience levels, benefits, requirements, skills
    - Dynamic location filtering (flexible format)
    - Salary range filtering
    - Experience level filtering
    - Employment type filtering
    - Date filtering
    - Pagination support for more results
    
    Location Filter (Dynamic):
    - Accepts any location format that SimplyHired supports
    - Examples: 'remote', 'New York, NY', 'Lahore, Pakistan', 'USA', 'California, USA'
    - 'San Francisco, CA', 'London, UK', 'Toronto, ON', etc.
    - Any valid location string will be URL-encoded and passed to SimplyHired
    
    Job Type Filter:
    - 'remote' - Remote jobs only
    - 'hybrid' - Hybrid jobs only  
    - 'onsite' or 'on-site' - On-site jobs only
    
    Salary Filters:
    - salary_min: Minimum salary (e.g., 50000)
    - salary_max: Maximum salary (e.g., 100000)
    
    Experience Level Filter:
    - 'intern' / 'internship' - Internship jobs
    - 'assistant' - Assistant-level jobs
    - 'entry' / 'junior' - Entry-level jobs
    - 'mid' / 'mid-senior' - Mid-level jobs
    - 'senior' - Senior-level jobs
    - 'director' / 'manager' - Director/Manager-level jobs
    - 'executive' - Executive-level jobs
    
    Employment Type Filter:
    - 'full-time' - Full-time jobs
    - 'part-time' - Part-time jobs
    - 'contract' - Contract jobs
    - 'internship' - Internship jobs
    
    Date Filter:
    - days_old: Filter jobs posted within last N days
    - 30 - Jobs posted in last 30 days
    - 7 - Jobs posted in last 7 days
    - 1 - Jobs posted today
    """
    try:
        jobs = await _execute_async_scrape(
            scrape_simplyhired_playwright,
            query, location, max_results, job_type,
            salary_min, salary_max, experience_level, employment_type, days_old
        )
    except ScrapeInProgressError as e:
        raise HTTPException(
            status_code=429,
            detail=f"Another scraping operation is currently in progress. Please wait and try again. {str(e)}"
        )
    except ScrapeTimeoutError as e:
        raise HTTPException(
            status_code=504,
            detail=f"Scraping operation timed out. {str(e)}"
        )
    except SimplyHiredCloudflareBlockedError as e:
        # SimplyHired is blocked - return clear error with solution
        raise HTTPException(
            status_code=503,
            detail=f"SimplyHired blocked by Cloudflare. {str(e)}. Solutions: 1) Configure PROXY_URL in .env file 2) Wait and retry 3) Use alternative endpoints"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
 
    return jobs


@router.post("/jobs/scrape-url", response_model=List[Job])
async def scrape_career_page_url(request: CareerPageRequest = Body(...)):
    """
    Scrape jobs from any company career page URL
    
    This endpoint accepts any career page URL and attempts to extract job listings from it.
    Works with various career page formats and structures.
    
    Example URLs:
    - https://www.burton.com/us/en/careers
    - https://skida.com/pages/careers
    - https://thujasocks.com/pages/careers
    - https://darntough.com/pages/careers
    - https://www.turtlefur.com/pages/careers
    - https://vermontglove.com/pages/careers
    - https://orvis.com/pages/careers
    - https://www.concept2.com/company/employment
    
    Request Body:
    {
        "url": "https://example.com/careers",
        "max_results": null,  // Optional: null = get all jobs (default), or specify number like 50
        "search_query": "software engineer"  // Optional: filter by keyword
    }
    
    Features:
    - Automatically follows job board links (Greenhouse, Lever, Dayforce, etc.)
    - Extracts actual job titles from job boards
    - Optional search_query to filter jobs by keyword
    - No limit on jobs by default (gets all jobs from up to 5 pages)
    
    Returns:
    - List of Job objects with actual job titles, company, location, description, etc.
    """
    _validate_career_url(request.url)
    try:
        jobs = await scrape_generic_career_page(request.url, request.max_results, request.search_query)
    except ScrapeInProgressError as e:
        raise HTTPException(
            status_code=429,
            detail=f"Another scraping operation is currently in progress. Please wait and try again. {str(e)}"
        )
    except ScrapeTimeoutError as e:
        raise HTTPException(
            status_code=504,
            detail=f"Scraping operation timed out. {str(e)}"
        )
    except asyncio.TimeoutError as e:
        raise HTTPException(
            status_code=504,
            detail="Scraping operation timed out. The site may be slow or unresponsive."
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        err_msg = str(e)
        if any(x in err_msg.lower() for x in ("connection", "timeout", "refused", "reset", "unreachable")):
            raise HTTPException(
                status_code=502,
                detail=f"Cannot reach the career page. Please check the URL and try again. ({err_msg[:200]})"
            )
        raise HTTPException(
            status_code=500,
            detail=f"Error scraping career page: {err_msg[:500]}"
        )

    return jobs if jobs is not None else []


@router.get("/jobs/scrape-url-get", response_model=List[Job])
async def scrape_career_page_url_get(
    url: str = Query(..., description="Career page URL to scrape"),
    max_results: Optional[int] = Query(None, description="Maximum number of results (None = get all jobs from up to 5 pages)"),
    search_query: Optional[str] = Query(None, description="Search/filter jobs by keyword (e.g., 'software engineer', 'sales')")
):
    """
    Scrape jobs from any company career page URL (GET method for easy testing)
    
    ⭐ ENHANCED: Now follows job board links and extracts actual job titles!
    
    This endpoint:
    - Automatically follows links to job boards (Greenhouse, Lever, Dayforce, etc.)
    - Extracts actual job titles instead of just navigation links
    - Supports search/filtering by keyword
    
    Example usage:
    /api/jobs/scrape-url-get?url=https://www.burton.com/us/en/careers
    /api/jobs/scrape-url-get?url=https://www.burton.com/us/en/careers&max_results=50
    /api/jobs/scrape-url-get?url=https://www.burton.com/us/en/careers&search_query=designer
    
    Parameters:
    - url: Career page URL to scrape
    - max_results: Maximum number of results (None = get all jobs from up to 5 pages)
    - search_query: Optional keyword to filter jobs (searches in title, description, location)
    
    Returns:
    - List of Job objects with actual job titles, company, location, description, etc.
    """
    _validate_career_url(url)
    try:
        jobs = await scrape_generic_career_page(url, max_results, search_query)
    except ScrapeTimeoutError as e:
        raise HTTPException(
            status_code=504,
            detail=f"Scraping operation timed out. {str(e)}"
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Scraping operation timed out. The site may be slow or unresponsive."
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        err_msg = str(e)
        if any(x in err_msg.lower() for x in ("connection", "timeout", "refused", "reset", "unreachable")):
            raise HTTPException(
                status_code=502,
                detail=f"Cannot reach the career page. Please check the URL and try again. ({err_msg[:200]})"
            )
        raise HTTPException(
            status_code=500,
            detail=f"Error scraping career page: {err_msg[:500]}"
        )

    return jobs if jobs is not None else []


@router.post("/jobs/scrape-multiple-urls", response_model=List[Job])
async def scrape_multiple_career_pages_endpoint(request: MultipleCareerPagesRequest = Body(...)):
    """
    Scrape jobs from multiple company career page URLs in one request
    
    This endpoint accepts multiple career page URLs and extracts job listings from all of them.
    Results are combined and deduplicated.
    
    Request Body:
    {
        "urls": [
            "https://company1.com/careers",
            "https://company2.com/careers",
            "https://company3.com/careers"
        ],
        "max_results_per_url": null,  // Optional: null = get all jobs per URL (default)
        "search_query": "software engineer",  // Optional: filter by keyword
        "total_max_results": 50  // Optional: limits total results across all URLs
    }
    
    Features:
    - Scrapes multiple career pages in sequence
    - Automatic deduplication of jobs across URLs
    - Progress tracking and error handling per URL
    - Optional total result limit across all URLs
    - Failed URLs don't stop processing of other URLs
    - Optional search_query to filter jobs by keyword
    
    Limits (to prevent OOM/timeouts on Railway):
    - Maximum {MAX_URLS_PER_REQUEST} URLs per request
    - Results per URL and total results are capped when not specified
    
    Returns:
    - Combined and deduplicated list of Job objects from all URLs
    - Jobs are deduplicated based on title + company
    """
    # Validate URL count to prevent OOM and runaway runtime on Railway
    if len(request.urls) > MAX_URLS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Too many URLs: maximum {MAX_URLS_PER_REQUEST} per request (got {len(request.urls)}). "
            "Split into multiple requests or reduce the list."
        )
    if not request.urls:
        raise HTTPException(status_code=400, detail="At least one URL is required")

    # Validate all URLs before starting (fail fast for Railway)
    invalid = []
    for i, u in enumerate(request.urls):
        ok, msg = _validate_career_url_safe(u)
        if not ok:
            invalid.append(f"URL {i + 1}: {msg}")
    if invalid:
        raise HTTPException(
            status_code=400,
            detail="Invalid URL(s): " + "; ".join(invalid[:5]) + ("..." if len(invalid) > 5 else "")
        )

    # Cap result limits to prevent unbounded memory (defaults when not specified)
    max_per_url = request.max_results_per_url
    if max_per_url is None:
        max_per_url = MAX_RESULTS_PER_URL_DEFAULT
    else:
        max_per_url = min(max_per_url, MAX_RESULTS_PER_URL_DEFAULT)
    total_max = request.total_max_results
    if total_max is not None:
        total_max = min(total_max, MAX_TOTAL_RESULTS_DEFAULT)
    else:
        total_max = MAX_TOTAL_RESULTS_DEFAULT

    try:
        jobs = await asyncio.wait_for(
            scrape_multiple_career_pages(
                urls=request.urls,
                max_results_per_url=max_per_url,
                search_query=request.search_query,
                total_max_results=total_max,
            ),
            timeout=MULTI_URL_SCRAPE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"Scraping exceeded {MULTI_URL_SCRAPE_TIMEOUT}s timeout. "
            "Try fewer URLs or use the async job endpoint (/jobs/scrape-url-async) per URL."
        )
    except ScrapeInProgressError as e:
        raise HTTPException(
            status_code=429,
            detail=f"Another scraping operation is currently in progress. Please wait and try again. {str(e)}"
        )
    except ScrapeTimeoutError as e:
        raise HTTPException(
            status_code=504,
            detail=f"Scraping operation timed out. {str(e)}"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        err_msg = str(e)
        if any(x in err_msg.lower() for x in ("connection", "timeout", "refused", "reset", "unreachable")):
            raise HTTPException(
                status_code=502,
                detail=f"Cannot reach one or more career pages. ({err_msg[:200]})"
            )
        raise HTTPException(
            status_code=500,
            detail=f"Error scraping URLs: {err_msg[:500]}"
        )

    return jobs if jobs is not None else []


@router.get("/health/detailed", response_model=Dict[str, Any])
async def health_check_detailed():
    """
    Detailed health check endpoint for monitoring browser pool and system resources
    
    Returns:
    - chrome_processes: Number of active Chrome/ChromeDriver processes
    - status: "healthy" if process count is low, "warning" if moderate, "critical" if high
    - recommendations: Actions to take based on health status
    """
    process_count = check_chrome_process_count()
    
    # Determine health status
    if process_count == 0:
        status = "healthy"
        message = "No Chrome processes running"
        recommendations = []
    elif process_count <= 5:
        status = "healthy"
        message = f"{process_count} Chrome process(es) running (normal)"
        recommendations = []
    elif process_count <= 15:
        status = "warning"
        message = f"{process_count} Chrome processes running (elevated)"
        recommendations = [
            "Consider running /api/health/cleanup to free resources",
            "Monitor for increasing process counts"
        ]
    else:
        status = "critical"
        message = f"{process_count} Chrome processes running (CRITICAL)"
        recommendations = [
            "URGENT: Run /api/health/cleanup immediately",
            "Check for scraping operations that didn't complete properly",
            "Consider restarting the application if cleanup doesn't help"
        ]
    
    return {
        "status": status,
        "message": message,
        "chrome_processes": process_count,
        "recommendations": recommendations,
        "service": settings.PROJECT_NAME
    }


@router.post("/health/cleanup", response_model=Dict[str, Any])
async def emergency_cleanup():
    """
    Emergency cleanup endpoint to force close all Chrome resources
    
    Use this when:
    - You get "pool full" errors
    - Health check shows high process count
    - Application seems stuck or unresponsive
    - After deployment to ensure clean state
    
    This endpoint will:
    1. Hard-kill all Chrome/ChromeDriver processes (OS-level)
    2. Verify cleanup
    3. Free all system resources
    
    Returns:
    - processes_killed: Number of processes killed
    - cleanup_verified: Whether cleanup was successful
    - status: Success or error information
    """
    try:
        # Perform hard-kill cleanup
        print("🚨 EMERGENCY CLEANUP requested via API endpoint")
        processes_killed = hard_kill_all_browsers()
        
        # Verify cleanup
        cleanup_verified = verify_cleanup()
        
        return {
            "status": "success",
            "message": "Emergency cleanup completed successfully",
            "processes_killed": processes_killed,
            "cleanup_verified": cleanup_verified,
            "recommendation": "All Chrome resources freed. System is ready for new scraping operations."
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Emergency cleanup failed: {str(e)}"
        )


@router.post("/health/cleanup-soft", response_model=Dict[str, Any])
async def soft_cleanup():
    """
    Soft cleanup endpoint - uses hard-kill for reliability
    
    Note: This endpoint now uses hard-kill to ensure complete cleanup
    on Railway's constrained environment. The "soft" designation is kept
    for API compatibility.
    
    This endpoint:
    1. Hard-kills all Chrome/ChromeDriver processes
    2. Verifies cleanup
    
    Returns cleanup statistics
    """
    try:
        # Use hard-kill for reliability (especially important on Railway)
        processes_killed = hard_kill_all_browsers()
        cleanup_verified = verify_cleanup()
        
        return {
            "status": "success",
            "message": "Cleanup completed successfully",
            "processes_killed": processes_killed,
            "cleanup_verified": cleanup_verified,
            "recommendation": "All Chrome resources freed."
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Cleanup failed: {str(e)}"
        )


@router.get("/health/execution-status", response_model=Dict[str, Any])
async def get_execution_status_endpoint():
    """
    Get current scraping execution status.
    
    Returns:
    - scrape_in_progress: Whether a scrape is currently running
    - elapsed_seconds: How long the current scrape has been running (if any)
    - timeout_seconds: Maximum timeout for scraping operations
    """
    try:
        status = get_execution_status()
        return {
            **status,
            "message": "Scrape in progress" if status["scrape_in_progress"] else "No scrape in progress"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get execution status: {str(e)}"
        )


@router.get("/health/throttle-status", response_model=Dict[str, Any])
async def get_throttle_status_endpoint():
    """
    Get scraping throttle status - shows parallel execution limits
    
    This endpoint shows:
    - How many scraping operations can run in parallel
    - How many are currently running
    - How many more can be started
    - Detected Railway plan
    
    Useful for:
    - Understanding if your requests will queue
    - Monitoring parallel execution capacity
    - Debugging slow response times
    
    Note: Throttling is optional. If not enabled in your scrapers,
    this will show the recommended limits but they won't be enforced.
    """
    try:
        from app.core.throttle import get_throttle_status
        status = get_throttle_status()
        
        # Add interpretation
        if status["active_scrapes"] == 0:
            status["interpretation"] = "No scraping operations currently running"
        elif status["available_slots"] > 0:
            status["interpretation"] = f"{status['active_scrapes']} scrape(s) running, capacity for {status['available_slots']} more"
        else:
            status["interpretation"] = f"At capacity: {status['active_scrapes']} scrape(s) running, new requests will queue"
        
        return status
    except ImportError:
        # Throttling not implemented yet
        return {
            "status": "not_implemented",
            "message": "Throttling module not enabled yet",
            "recommendation": "See PARALLEL_SCRAPING_GUIDE.md for implementation instructions"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get throttle status: {str(e)}"
        )


async def _process_scrape_job(job_id: str, url: str, max_results: Optional[int], search_query: Optional[str]):
    """Background task to process a scrape job. Logs and stores any exception so the task never raises."""
    logger = logging.getLogger(__name__)
    try:
        async with _get_job_lock():
            if job_id in _job_storage:
                _job_storage[job_id]["status"] = JobStatus.PROCESSING
                _job_storage[job_id]["updated_at"] = datetime.utcnow().isoformat()
        
        try:
            # Perform scraping with single-concurrency enforcement
            async with scrape_execution_context():
                jobs = await scrape_generic_career_page(url, max_results, search_query)
            
            # Update job status
            async with _get_job_lock():
                if job_id in _job_storage:
                    _job_storage[job_id]["status"] = JobStatus.COMPLETED
                    _job_storage[job_id]["result"] = jobs if jobs is not None else []
                    _job_storage[job_id]["updated_at"] = datetime.utcnow().isoformat()
                    _job_storage[job_id]["progress"] = {"jobs_found": len(jobs) if jobs else 0}
            
        except Exception as e:
            error_msg = str(e)
            logger.exception("Scrape job %s failed: %s", job_id, error_msg)
            async with _get_job_lock():
                if job_id in _job_storage:
                    _job_storage[job_id]["status"] = JobStatus.FAILED
                    _job_storage[job_id]["error"] = error_msg
                    _job_storage[job_id]["updated_at"] = datetime.utcnow().isoformat()
    except Exception as outer:
        # Safety: catch any exception from lock/storage so the task never raises
        logger.exception("Background scrape job %s raised: %s", job_id, outer)
        try:
            async with _get_job_lock():
                if job_id in _job_storage:
                    _job_storage[job_id]["status"] = JobStatus.FAILED
                    _job_storage[job_id]["error"] = f"Internal error: {outer}"
                    _job_storage[job_id]["updated_at"] = datetime.utcnow().isoformat()
        except Exception:
            pass


@router.get("/jobs/scrape-url-async-get", response_model=ScrapeJobResponse)
async def scrape_career_page_url_async_get(
    url: str = Query(..., description="Career page URL to scrape"),
    max_results: Optional[int] = Query(None, description="Maximum number of results (None = get all jobs from up to 5 pages)"),
    search_query: Optional[str] = Query(None, description="Search/filter jobs by keyword (e.g., 'software engineer', 'sales')")
):
    """
    Scrape jobs from any company career page URL (Async Job Pattern - GET method)
    
    ⚡ USE THIS ENDPOINT FOR LONG-RUNNING REQUESTS (e.g., from n8n)
    
    This endpoint returns immediately with a job_id, allowing you to poll for results.
    This prevents timeout issues when scraping takes longer than 15 minutes.
    
    Query Parameters:
    - url: Career page URL to scrape (required)
    - max_results: Maximum number of results (optional, None = get all jobs)
    - search_query: Filter jobs by keyword (optional)
    
    Example:
    GET /api/jobs/scrape-url-async-get?url=https://monday.com/careers&search_query=Customer%20Success
    
    Response:
    {
        "job_id": "uuid-string",
        "status": "pending",
        "message": "Job created successfully",
        "status_url": "/api/jobs/scrape-status/{job_id}",
        "estimated_time": "5-30 minutes"
    }
    
    How to use:
    1. Call this endpoint - it returns immediately with job_id
    2. Poll /api/jobs/scrape-status/{job_id} every 10-30 seconds
    3. When status is "completed", the result contains the jobs
    4. When status is "failed", check the error field
    """
    _validate_career_url(url)
    request = CareerPageRequest(url=url, max_results=max_results, search_query=search_query)
    job_id = _enqueue_career_scrape(request.url, request.max_results, request.search_query)

    return ScrapeJobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Job created successfully. Poll the status_url to get results.",
        status_url=f"/api/jobs/{job_id}",
        estimated_time="5-30 minutes (depending on page complexity)"
    )


@router.post("/jobs/scrape-url-async", response_model=ScrapeJobResponse)
async def scrape_career_page_url_async(request: CareerPageRequest = Body(...)):
    """
    Scrape jobs from any company career page URL (Async Job Pattern)
    
    ⚡ USE THIS ENDPOINT FOR LONG-RUNNING REQUESTS (e.g., from n8n)
    
    This endpoint returns immediately with a job_id, allowing you to poll for results.
    This prevents timeout issues when scraping takes longer than 15 minutes.
    
    Request Body (same as /jobs/scrape-url):
    {
        "url": "https://example.com/careers",
        "max_results": null,  // Optional: null = get all jobs (default)
        "search_query": "software engineer"  // Optional: filter by keyword
    }
    
    Response:
    {
        "job_id": "uuid-string",
        "status": "pending",
        "message": "Job created successfully",
        "status_url": "/api/jobs/scrape-status/{job_id}",
        "estimated_time": "5-30 minutes"
    }
    
    How to use:
    1. Call this endpoint - it returns immediately with job_id
    2. Poll /api/jobs/scrape-status/{job_id} every 10-30 seconds
    3. When status is "completed", the result contains the jobs
    4. When status is "failed", check the error field
    
    Example workflow in n8n:
    1. HTTP Request → POST /api/jobs/scrape-url-async
    2. Extract job_id from response
    3. Loop (max 60 iterations, wait 15 seconds):
       - HTTP Request → GET /api/jobs/scrape-status/{job_id}
       - If status = "completed": break loop, return result
       - If status = "failed": break loop, return error
       - Wait 15 seconds, repeat
    """
    _validate_career_url(request.url)
    job_id = _enqueue_career_scrape(request.url, request.max_results, request.search_query)

    return ScrapeJobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Job created successfully. Poll the status_url to get results.",
        status_url=f"/api/jobs/{job_id}",
        estimated_time="5-30 minutes (depending on page complexity)"
    )


@router.get("/jobs/scrape-status/{job_id}", response_model=ScrapeJobStatusResponse)
async def get_scrape_job_status(job_id: str):
    """
    Get the status and results of an async scrape job
    
    Use this endpoint to poll for job completion. Poll every 10-30 seconds.
    
    Response when pending/processing:
    {
        "job_id": "uuid",
        "status": "processing",
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:05:00",
        "progress": null,
        "result": null,
        "error": null
    }
    
    Response when completed:
    {
        "job_id": "uuid",
        "status": "completed",
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:15:00",
        "progress": {"jobs_found": 25},
        "result": [/* array of Job objects */],
        "error": null
    }
    
    Response when failed:
    {
        "job_id": "uuid",
        "status": "failed",
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:10:00",
        "progress": null,
        "result": null,
        "error": "Error message here"
    }
    """
    job_data = _get_job_data(job_id)
    if not job_data:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return _job_status_response(job_data)


@router.delete("/jobs/scrape-status/{job_id}")
async def delete_scrape_job(job_id: str):
    """
    Delete a completed or failed job from storage
    
    This is optional - jobs are kept in memory until the server restarts.
    Use this to free memory after retrieving results.
    """
    async with _get_job_lock():
        if job_id not in _job_storage:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        
        job_status = _job_storage[job_id].get("status")
        if job_status == JobStatus.PROCESSING:
            raise HTTPException(
                status_code=400, 
                detail="Cannot delete a job that is currently processing"
            )
        
        del _job_storage[job_id]
    
    return {"message": f"Job {job_id} deleted successfully", "status": "success"}


@router.post("/jobs/scrape", response_model=ScrapeJobResponse, status_code=202)
async def enqueue_career_scrape(request: CareerPageRequest = Body(...)):
    """
    Enqueue a career page scrape (production endpoint).

    Returns immediately with job_id. Poll GET /api/jobs/{job_id} for status and results.
    """
    _validate_career_url(request.url)
    job_id = _enqueue_career_scrape(request.url, request.max_results, request.search_query)
    return ScrapeJobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Scrape job enqueued.",
        status_url=f"/api/jobs/{job_id}",
        estimated_time="5-30 minutes",
    )


@router.get("/jobs/{job_id}", response_model=ScrapeJobStatusResponse)
async def get_job_status(job_id: str):
    """Get scrape job status, progress, and results."""
    job_data = _get_job_data(job_id)
    if not job_data:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return _job_status_response(job_data)


@router.get("/jobs/{job_id}/result", response_model=List[Job])
async def get_job_result(job_id: str):
    """Return job results only when scrape is completed."""
    job_data = _get_job_data(job_id)
    if not job_data:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job_data.get("status") != JobStatus.COMPLETED.value:
        raise HTTPException(
            status_code=409,
            detail=f"Job is not completed (status={job_data.get('status')})",
        )
    result = job_data.get("result") or []
    return [Job(**j) for j in result]


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str):
    """Cancel a pending/processing scrape job."""
    job_data = _get_job_data(job_id)
    if not job_data:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    status = job_data.get("status")
    if status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value):
        get_job_store().delete(job_id)
        return {"message": f"Job {job_id} removed", "status": "success"}

    task_id = job_data.get("celery_task_id")
    if task_id:
        try:
            from app.workers.celery_app import celery_app
            celery_app.control.revoke(task_id, terminate=True)
        except Exception as exc:
            logger.warning("Failed to revoke task %s: %s", task_id, exc)

    get_job_store().set_status(job_id, RedisJobStatus.CANCELLED)
    return {"message": f"Job {job_id} cancelled", "status": "cancelled"}


@router.get("/health/workers")
async def worker_health():
    """Celery worker health and active task count."""
    try:
        from app.workers.celery_app import celery_app
        inspect = celery_app.control.inspect(timeout=3.0)
        ping = inspect.ping() or {}
        active = inspect.active() or {}
        active_count = sum(len(v) for v in active.values())
        return {
            "status": "healthy" if ping else "degraded",
            "workers": list(ping.keys()) if ping else [],
            "active_tasks": active_count,
            "ping": ping,
        }
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


@router.get("/health/queue")
async def queue_health():
    """Queue depth from Redis."""
    try:
        import redis
        from app.core.settings_workers import get_worker_settings
        r = redis.from_url(get_worker_settings().REDIS_URL)
        default_len = r.llen("scrape.default") if r.exists("scrape.default") else 0
        retry_len = r.llen("scrape.retry") if r.exists("scrape.retry") else 0
        dlq_len = r.llen("scrape.dlq") if r.exists("scrape.dlq") else 0
        return {
            "queues": {
                "scrape.default": default_len,
                "scrape.retry": retry_len,
                "scrape.dlq": dlq_len,
            }
        }
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


