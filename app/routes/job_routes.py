
from fastapi import APIRouter, Query, HTTPException, Body
from typing import List, Optional
from app.models.job_model import Job # pylint: disable=import-error
from app.core.config import settings # pylint: disable=import-error
from app.services.indeed_selenium_service import scrape_indeed_selenium, CloudflareBlockedError # pylint: disable=import-error
from app.services.ziprecruiter_service import scrape_ziprecruiter # pylint: disable=import-error
from app.services.ziprecruiter_enhanced_service import scrape_ziprecruiter_enhanced # pylint: disable=import-error
from app.services.generic_career_scraper import scrape_generic_career_page # pylint: disable=import-error
from app.core.caching import get_cache, set_cache # pylint: disable=import-error
from pydantic import BaseModel

router = APIRouter()


class CareerPageRequest(BaseModel):
    url: str
    max_results: Optional[int] = 20
    search_query: Optional[str] = None


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
    max_results: int = Query(20, description="Maximum number of results (default: 20)")
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
    cache_key = f"indeed_selenium_enhanced:{query}:{location}:{job_type}:{salary_min}:{salary_max}:{experience_level}:{employment_type}:{days_old}:{max_results}"
    cached = await get_cache(cache_key)
    if cached:
        return cached

    try:
        jobs = await scrape_indeed_selenium(
            query, location, max_results, job_type, 
            salary_min, salary_max, experience_level, employment_type, days_old
        )
    except CloudflareBlockedError as e:
        # Indeed is blocked - return clear error with solution
        raise HTTPException(
            status_code=503,
            detail=f"Indeed blocked by Cloudflare. {str(e)}. Solutions: 1) Configure PROXY_URL in .env file 2) Use /api/jobs/ziprecruiter-enhanced endpoint 3) Wait and retry"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    await set_cache(cache_key, jobs, settings.CACHE_TTL)
    return jobs


@router.get("/jobs/indeed-self-test")
async def indeed_self_test(q: str = Query("python developer"), l: Optional[str] = Query("remote")):
    """Quickly test Indeed scraping with small limits to verify Cloudflare workarounds."""
    try:
        jobs = await scrape_indeed_selenium(q, l, max_results=5)
        return {
            "ok": True,
            "count": len(jobs),
            "note": "If count is 0 repeatedly, Cloudflare may still be blocking.",
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
    cache_key = f"ziprecruiter:{query}:{location}:{max_results}"
    cached = await get_cache(cache_key)
    if cached:
        return cached

    try:
        jobs = await scrape_ziprecruiter(query, location, max_results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    await set_cache(cache_key, jobs, settings.CACHE_TTL)
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
    cache_key = f"ziprecruiter_enhanced:{query}:{location}:{job_type}:{max_results}"
    cached = await get_cache(cache_key)
    if cached:
        return cached

    try:
        jobs = await scrape_ziprecruiter_enhanced(query, location, max_results, job_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    await set_cache(cache_key, jobs, settings.CACHE_TTL)
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
        "max_results": 20,
        "search_query": "software engineer" (optional)
    }
    
    Features:
    - Automatically follows job board links (Greenhouse, Lever, Dayforce, etc.)
    - Extracts actual job titles from job boards
    - Optional search_query to filter jobs by keyword
    
    Returns:
    - List of Job objects with actual job titles, company, location, description, etc.
    """
    cache_key = f"generic_career:{request.url}:{request.max_results}:{request.search_query}"
    cached = await get_cache(cache_key)
    if cached:
        return cached

    try:
        jobs = await scrape_generic_career_page(request.url, request.max_results, request.search_query)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error scraping {request.url}: {str(e)}")

    await set_cache(cache_key, jobs, settings.CACHE_TTL)
    return jobs


@router.get("/jobs/scrape-url-get", response_model=List[Job])
async def scrape_career_page_url_get(
    url: str = Query(..., description="Career page URL to scrape"),
    max_results: int = Query(20, description="Maximum number of results (default: 20)"),
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
    /api/jobs/scrape-url-get?url=https://www.burton.com/us/en/careers&max_results=20
    /api/jobs/scrape-url-get?url=https://www.burton.com/us/en/careers&search_query=designer
    
    Parameters:
    - url: Career page URL to scrape
    - max_results: Maximum number of results (default: 20)
    - search_query: Optional keyword to filter jobs (searches in title, description, location)
    
    Returns:
    - List of Job objects with actual job titles, company, location, description, etc.
    """
    cache_key = f"generic_career:{url}:{max_results}:{search_query}"
    cached = await get_cache(cache_key)
    if cached:
        return cached

    try:
        jobs = await scrape_generic_career_page(url, max_results, search_query)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error scraping {url}: {str(e)}")

    await set_cache(cache_key, jobs, settings.CACHE_TTL)
    return jobs
