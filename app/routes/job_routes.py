
from fastapi import APIRouter, Query, HTTPException, Body
from typing import List, Optional, Dict, Any, Callable
from datetime import datetime
from enum import Enum
import asyncio
import uuid
from app.models.job_model import Job # pylint: disable=import-error
from app.core.config import settings # pylint: disable=import-error
from app.core.scrape_executor import scrape_execution_context, ScrapeInProgressError, ScrapeTimeoutError, get_execution_status # pylint: disable=import-error
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
from pydantic import BaseModel

router = APIRouter()

# In-memory job storage with thread safety
_job_storage: Dict[str, Dict[str, Any]] = {}
_job_lock: Optional[asyncio.Lock] = None


def _get_job_lock() -> asyncio.Lock:
    """Get or create the job lock (lazy initialization for async context)"""
    global _job_lock
    if _job_lock is None:
        _job_lock = asyncio.Lock()
    return _job_lock


class JobStatus(str, Enum):
    """Job status enum"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


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


@router.get("/jobs", response_model=List[Job])
async def get_jobs(
    query: str = Query(..., description="Search term, e.g. 'python developer'"),
    location: Optional[str] = Query(None, description="Job location (flexible format like LinkedIn). Examples: 'remote', 'New York, NY', 'Lahore, Pakistan', 'USA', 'California, USA'"),
    job_type: Optional[str] = Query(None, description="Job type filter: 'remote', 'hybrid', 'onsite', 'On-site'"),
    salary_min: Optional[int] = Query(None, description="Minimum salary filter (e.g., 50000)"),
    salary_max: Optional[int] = Query(None, description="Maximum salary filter (e.g., 100000)"),
    experience_level: Optional[str] = Query(None, description="Experience level filter: 'intern', 'assistant', 'entry', 'junior', 'mid', 'mid-senior', 'senior', 'director', 'executive'"),
    employment_type: Optional[str] = Query(None, description="Employment type filter: 'Full-Time', 'Part-Time', 'Contract', 'Internship'"),
    days_old: Optional[int] = Query(None, description="Filter jobs posted within last N days (e.g., 30 for last 30 days)"),
    max_results: int = Query(20, description="Maximum number of results (default: 20)"),
):
    """
    Get jobs from Indeed using enhanced browser automation (Selenium)
    
    ⭐ ENHANCED VERSION: Now works like ZipRecruiter with comprehensive data extraction and advanced filtering!
    
    Features:
    - Extracts salary ranges, company URLs, job descriptions
    - Job types, experience levels, benefits, requirements, skills
    - Raw job card data (HTML + text) for complete information access
    - Dynamic location filtering (flexible format like LinkedIn search URLs)
    - Salary range filtering
    - Experience level filtering
    - Employment type filtering
    - Pagination support for more results
    - Better error handling and debugging
    
    Location Filter (Dynamic - works like LinkedIn):
    - Accepts any location format that Indeed supports
    - Examples: 'remote', 'New York, NY', 'Lahore, Pakistan', 'USA', 'California, USA'
    - 'San Francisco, CA', 'London, UK', 'Toronto, ON', etc.
    - Any valid location string will be URL-encoded and passed to Indeed
    
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
        # Execute with single-concurrency enforcement and guaranteed cleanup
        jobs = await _execute_sync_scrape(
            scrape_indeed_selenium,
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
    except CloudflareBlockedError as e:
        # Indeed is blocked - return clear error with solution
        raise HTTPException(
            status_code=503,
            detail=f"Indeed blocked by Cloudflare. {str(e)}. Solutions: 1) Configure PROXY_URL in .env file 2) Use /api/jobs/ziprecruiter-enhanced endpoint 3) Wait and retry"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return jobs


@router.get("/jobs/indeed-playwright", response_model=List[Job])
async def get_indeed_playwright_jobs(
    query: str = Query(..., description="Search term, e.g. 'python developer'"),
    location: Optional[str] = Query(None, description="Job location (flexible format). Examples: 'remote', 'New York, NY', 'Lahore, Pakistan', 'USA', 'California'"),
    job_type: Optional[str] = Query(None, description="Job type filter: 'remote', 'hybrid', 'onsite', 'On-site'"),
    salary_min: Optional[int] = Query(None, description="Minimum salary filter (e.g., 50000)"),
    salary_max: Optional[int] = Query(None, description="Maximum salary filter (e.g., 100000)"),
    experience_level: Optional[str] = Query(None, description="Experience level filter: 'intern', 'assistant', 'entry', 'junior', 'mid', 'mid-senior', 'senior', 'director', 'executive'"),
    employment_type: Optional[str] = Query(None, description="Employment type filter: 'Full-Time', 'Part-Time', 'Contract', 'Internship'"),
    days_old: Optional[int] = Query(None, description="Filter jobs posted within last N days (e.g., 30 for last 30 days)"),
    max_results: int = Query(20, description="Maximum number of results (default: 20)"),
    fetch_full_details: bool = Query(True, description="Fetch full job details by visiting each job page (slower but more data). Set to false for faster scraping (~5s vs ~50s)"),
):
    """
    Get jobs from Indeed using Playwright (More stable alternative to Selenium)
    
    ⭐ PLAYWRIGHT VERSION: Better resource management, no ChromeDriver issues!
    
    Benefits over Selenium:
    - No ChromeDriver version mismatches (Playwright bundles its own browser)
    - Better memory management and stability
    - More reliable in headless mode
    - Better error handling
    
    Features:
    - Extracts job title, company, location, URL, description
    - Dynamic location filtering (flexible format)
    - Salary range filtering
    - Experience level filtering
    - Employment type filtering
    - Date filtering
    
    Location Filter (Dynamic):
    - Accepts any location format that Indeed supports
    - Examples: 'remote', 'New York, NY', 'Lahore, Pakistan', 'USA', 'California, USA'
    - 'San Francisco, CA', 'London, UK', 'Toronto, ON', etc.
    
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
    
    Installation Required:
    ```bash
    pip install playwright
    python -m playwright install chromium
    ```
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Playwright is not installed. Install with: pip install playwright && python -m playwright install chromium"
        )
    
    try:
        async with scrape_execution_context():
            jobs = await scrape_indeed_playwright(
                query, location, max_results, job_type,
                salary_min, salary_max, experience_level, employment_type, days_old,
                fetch_full_details=fetch_full_details
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
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Playwright is not installed. Install with: pip install playwright && python -m playwright install chromium. Error: {str(e)}"
        )
    except PlaywrightCloudflareBlockedError as e:
        # Indeed is blocked - return clear error with solution
        raise HTTPException(
            status_code=503,
            detail=f"Indeed blocked by Cloudflare. {str(e)}. Solutions: 1) Configure PROXY_URL in .env file 2) Use /api/jobs/ziprecruiter-enhanced endpoint 3) Wait and retry"
        )
    except Exception as e:
        error_detail = str(e)
        # Log the full error for debugging on Railway
        import traceback
        print(f"❌ [PLAYWRIGHT ENDPOINT] Error: {error_detail}")
        print(f"   Traceback:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=error_detail)

    return jobs


@router.get("/jobs/indeed-self-test")
async def indeed_self_test(q: str = Query("python developer"), l: Optional[str] = Query("remote")):
    """Quickly test Indeed scraping with small limits to verify Cloudflare workarounds."""
    try:
        jobs = await _execute_sync_scrape(scrape_indeed_selenium, q, l, max_results=5)
        return {
            "ok": True,
            "count": len(jobs),
            "note": "If count is 0 repeatedly, Cloudflare may still be blocking.",
        }
    except ScrapeInProgressError as e:
        return {
            "ok": False,
            "busy": True,
            "detail": str(e),
            "hint": "Another scrape is in progress. Please wait and try again.",
        }
    except ScrapeTimeoutError as e:
        return {
            "ok": False,
            "timeout": True,
            "detail": str(e),
        }
    except CloudflareBlockedError as e:
        return {
            "ok": False,
            "blocked": True,
            "detail": str(e),
            "hint": "Set PROXY_URL in .env, increase BACKOFF_MAX, or retry later.",
        }
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
    try:
        async with scrape_execution_context():
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error scraping {request.url}: {str(e)}")

    return jobs


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
    try:
        async with scrape_execution_context():
            jobs = await scrape_generic_career_page(url, max_results, search_query)
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
        raise HTTPException(status_code=500, detail=f"Error scraping {url}: {str(e)}")

    return jobs


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
    
    Returns:
    - Combined and deduplicated list of Job objects from all URLs
    - Jobs are deduplicated based on title + company
    """
    try:
        async with scrape_execution_context():
            jobs = await scrape_multiple_career_pages(
                urls=request.urls,
                max_results_per_url=request.max_results_per_url,
                search_query=request.search_query,
                total_max_results=request.total_max_results
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error scraping multiple URLs: {str(e)}")

    return jobs


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
    """Background task to process a scrape job"""
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
                _job_storage[job_id]["result"] = jobs
                _job_storage[job_id]["updated_at"] = datetime.utcnow().isoformat()
                _job_storage[job_id]["progress"] = {"jobs_found": len(jobs)}
        
    except Exception as e:
        error_msg = str(e)
        async with _get_job_lock():
            if job_id in _job_storage:
                _job_storage[job_id]["status"] = JobStatus.FAILED
                _job_storage[job_id]["error"] = error_msg
                _job_storage[job_id]["updated_at"] = datetime.utcnow().isoformat()


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
    # Create request object from query parameters
    request = CareerPageRequest(url=url, max_results=max_results, search_query=search_query)
    
    # Generate unique job ID
    job_id = str(uuid.uuid4())
    
    # Create job record
    async with _get_job_lock():
        _job_storage[job_id] = {
            "job_id": job_id,
            "status": JobStatus.PENDING,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": None,
            "url": request.url,
            "max_results": request.max_results,
            "search_query": request.search_query,
            "result": None,
            "error": None,
            "progress": None
        }
    
    # Start background task
    asyncio.create_task(_process_scrape_job(job_id, request.url, request.max_results, request.search_query))
    
    return ScrapeJobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Job created successfully. Poll the status_url to get results.",
        status_url=f"/api/jobs/scrape-status/{job_id}",
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
    # Generate unique job ID
    job_id = str(uuid.uuid4())
    
    # Create job record
    async with _get_job_lock():
        _job_storage[job_id] = {
            "job_id": job_id,
            "status": JobStatus.PENDING,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": None,
            "url": request.url,
            "max_results": request.max_results,
            "search_query": request.search_query,
            "result": None,
            "error": None,
            "progress": None
        }
    
    # Start background task
    asyncio.create_task(_process_scrape_job(job_id, request.url, request.max_results, request.search_query))
    
    return ScrapeJobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Job created successfully. Poll the status_url to get results.",
        status_url=f"/api/jobs/scrape-status/{job_id}",
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
    async with _get_job_lock():
        job_data = _job_storage.get(job_id)
    
    if not job_data:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
    return ScrapeJobStatusResponse(
        job_id=job_data["job_id"],
        status=job_data["status"],
        created_at=job_data["created_at"],
        updated_at=job_data.get("updated_at"),
        progress=job_data.get("progress"),
        result=job_data.get("result"),
        error=job_data.get("error")
    )


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


