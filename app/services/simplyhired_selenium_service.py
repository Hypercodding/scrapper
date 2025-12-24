import time
import random
import threading
import re
from typing import Optional, List
import undetected_chromedriver as uc
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options as ChromeOptions
from urllib.parse import urlparse, quote_plus
import os
import json
import zipfile
import tempfile
import subprocess
import platform
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from selenium_stealth import stealth
from selenium.webdriver import ActionChains
from app.models.job_model import Job
from app.core.config import settings
from app.core.proxy_manager import get_proxy_manager, reset_proxy_manager
import urllib3
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
import requests

# Import shared driver management functions from Indeed service
from app.services.indeed_selenium_service import (
    configure_driver_connection_pool,
    get_chrome_executable_path,
    get_chromedriver_path,
    check_chrome_process_count,
    check_system_resources,
    cleanup_zombie_processes,
    cleanup_global_driver,
    _get_proxy_urls,
    _build_proxy_auth_extension,
    get_driver as get_shared_driver,
    _perform_human_interactions,
    _progressive_scroll
)

_last_fetch = 0
_request_lock = threading.Lock()


class CloudflareBlockedError(Exception):
    """Raised when SimplyHired returns a Cloudflare/turnstile block page."""
    pass


def _format_location_for_simplyhired(location: str) -> str:
    """Format location for SimplyHired search."""
    location = location.strip()
    
    # Handle remote job types
    if location.lower() in ['remote', 'work from home', 'wfh']:
        return 'remote'
    
    # SimplyHired accepts various location formats
    # URL encode spaces and special characters
    return quote_plus(location)


def _get_simplyhired_employment_filter(employment_type: str) -> Optional[str]:
    """Get SimplyHired employment type filter parameter."""
    employment_type = employment_type.lower().strip()
    
    # SimplyHired uses 'jt' parameter for job type
    employment_filters = {
        'full-time': 'jt=fulltime',
        'fulltime': 'jt=fulltime',
        'part-time': 'jt=parttime',
        'parttime': 'jt=parttime',
        'contract': 'jt=contract',
        'temporary': 'jt=temporary',
        'internship': 'jt=internship',
        'intern': 'jt=internship'
    }
    
    return employment_filters.get(employment_type)


def _get_simplyhired_date_filter(days_old: int) -> Optional[str]:
    """Get SimplyHired date filter parameter."""
    # SimplyHired uses 'fdb' parameter for date posted
    # Values: 1 (last 24 hours), 3 (last 3 days), 7 (last 7 days), 14 (last 14 days), 30 (last 30 days)
    if days_old <= 1:
        return 'fdb=1'
    elif days_old <= 3:
        return 'fdb=3'
    elif days_old <= 7:
        return 'fdb=7'
    elif days_old <= 14:
        return 'fdb=14'
    elif days_old <= 30:
        return 'fdb=30'
    else:
        return None  # SimplyHired doesn't support > 30 days filter


def _find_job_cards_simplyhired(soup: BeautifulSoup) -> List:
    """Find job cards using SimplyHired-specific selectors."""
    job_cards = []
    
    # SimplyHired uses different selectors - try multiple patterns
    primary_selectors = [
        'div[data-testid="job-card"]',  # Modern SimplyHired layout
        'div.SerpJob-jobCard',  # SimplyHired job card class
        'div[class*="SerpJob"]',  # Any SerpJob class
        'article[data-testid="job-card"]',  # Article-based layout
        'div[class*="job-card"]',  # Generic job card
        'div[class*="JobCard"]',  # Alternative naming
    ]
    
    # Try primary selectors first
    for selector in primary_selectors:
        cards = soup.select(selector)
        if cards:
            print(f"Found {len(cards)} job cards using selector: {selector}")
            job_cards.extend(cards)
            if len(cards) >= 10:  # Reasonable number for SimplyHired
                break
    
    # Fallback: Look for job title links and find parent containers
    if not job_cards:
        print("DEBUG - No job cards found with primary selectors, trying title links...")
        title_links = soup.find_all('a', href=lambda x: x and '/job/' in x)
        print(f"DEBUG - Found {len(title_links)} job title links")
        job_cards = [link.find_parent(['div', 'article']) for link in title_links if link.find_parent(['div', 'article'])]
        job_cards = [card for card in job_cards if card]
        print(f"DEBUG - Found {len(job_cards)} job cards from title links")
    
    # Remove duplicates
    unique_cards = []
    seen_cards = set()
    for card in job_cards:
        card_id = id(card)
        if card_id not in seen_cards:
            seen_cards.add(card_id)
            unique_cards.append(card)
    
    if len(unique_cards) > 30:
        print(f"WARNING - Found {len(unique_cards)} job cards, limiting to first 20")
        unique_cards = unique_cards[:20]
    
    return unique_cards


def _is_valid_job_card_simplyhired(card) -> bool:
    """Validate that a card element actually contains a job listing."""
    if not card:
        return False
    
    # Check for job title link (most reliable indicator)
    title_link = card.select_one('a[href*="/job/"]')
    if title_link:
        return True
    
    # Check for company name
    company_elements = card.select('[class*="company"], [class*="Company"]')
    if company_elements:
        return True
    
    # Check for location info
    location_elements = card.select('[class*="location"], [class*="Location"]')
    if location_elements:
        return True
    
    return False


def _extract_title_simplyhired(card) -> Optional[str]:
    """Extract job title from SimplyHired job card."""
    title_selectors = [
        'a[href*="/job/"]',
        'h2 a',
        'h3 a',
        '[class*="jobTitle"] a',
        '[class*="JobTitle"] a',
        'a[class*="title"]',
        'h2',
        'h3'
    ]
    
    for selector in title_selectors:
        title_elem = card.select_one(selector)
        if title_elem:
            title = title_elem.get_text(strip=True)
            if title and len(title) > 3:
                # Clean up title
                title = re.sub(r'\b(new|urgent|hiring)\b', '', title, flags=re.IGNORECASE).strip()
                return title
    
    return None


def _extract_company_info_simplyhired(card) -> tuple[Optional[str], Optional[str]]:
    """Extract company name and URL from SimplyHired job card."""
    company = None
    company_url = None
    
    company_selectors = [
        '[class*="companyName"]',
        '[class*="CompanyName"]',
        '[class*="company"]',
        '[class*="Company"]',
        'a[href*="/company/"]',
        '[data-testid="company-name"]'
    ]
    
    for selector in company_selectors:
        company_elem = card.select_one(selector)
        if company_elem:
            company = company_elem.get_text(strip=True)
            if company and len(company) > 1:
                # Check if it's a link
                if company_elem.name == 'a':
                    href = company_elem.get('href', '')
                    if href:
                        company_url = f"https://www.simplyhired.com{href}" if href.startswith('/') else href
                else:
                    # Look for nested link
                    link_elem = company_elem.find('a')
                    if link_elem:
                        href = link_elem.get('href', '')
                        if href:
                            company_url = f"https://www.simplyhired.com{href}" if href.startswith('/') else href
                break
    
    return company, company_url


def _extract_location_info_simplyhired(card) -> tuple[Optional[str], Optional[str]]:
    """Extract location and remote type from SimplyHired job card."""
    location = None
    remote_type = None
    
    location_selectors = [
        '[class*="location"]',
        '[class*="Location"]',
        '[data-testid="job-location"]',
        '[class*="jobLocation"]'
    ]
    
    for selector in location_selectors:
        location_elem = card.select_one(selector)
        if location_elem:
            location = location_elem.get_text(strip=True)
            break
    
    # Determine remote type based on location text
    if location:
        location_lower = location.lower()
        if 'remote' in location_lower:
            if 'hybrid' in location_lower:
                remote_type = 'Hybrid'
            else:
                remote_type = 'Remote'
        else:
            remote_type = 'On-site'
    
    return location, remote_type


def _extract_salary_simplyhired(card) -> Optional[str]:
    """Extract salary range from SimplyHired job card."""
    salary_selectors = [
        '[class*="salary"]',
        '[class*="Salary"]',
        '[class*="pay"]',
        '[class*="Pay"]',
        '[class*="compensation"]',
        '[data-testid="salary"]'
    ]
    
    for selector in salary_selectors:
        salary_elem = card.select_one(selector)
        if salary_elem:
            salary_text = salary_elem.get_text(strip=True)
            if '$' in salary_text or 'salary' in salary_text.lower() or 'pay' in salary_text.lower():
                salary_text = re.sub(r'\s+', ' ', salary_text).strip()
                if len(salary_text) > 5:
                    return salary_text
    
    # Pattern matching in text content
    text_content = card.get_text()
    salary_patterns = [
        r'\$[\d,]+(?:\.\d+)?\s*-\s*\$[\d,]+(?:\.\d+)?',
        r'\$[\d,]+(?:\.\d+)?\s*/\s*(?:year|month|hour|yr|mo|hr)',
        r'(?:salary|pay|compensation):\s*\$[\d,]+(?:\.\d+)?(?:\s*-\s*\$[\d,]+(?:\.\d+)?)?'
    ]
    
    for pattern in salary_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            return match.group(0).strip()
    
    return None


def _extract_job_types_simplyhired(card) -> tuple[Optional[str], Optional[str]]:
    """Extract job type and employment type from SimplyHired job card."""
    job_type = None
    employment_type = None
    
    text_content = card.get_text().lower()
    
    # Check for remote type
    if 'remote' in text_content:
        if 'hybrid' in text_content:
            job_type = 'Hybrid'
        else:
            job_type = 'Remote'
    else:
        job_type = 'On-site'
    
    # Check for employment type
    if 'full-time' in text_content or 'fulltime' in text_content:
        employment_type = 'Full-Time'
    elif 'part-time' in text_content or 'parttime' in text_content:
        employment_type = 'Part-Time'
    elif 'contract' in text_content:
        employment_type = 'Contract'
    elif 'internship' in text_content or 'intern' in text_content:
        employment_type = 'Internship'
    
    return job_type, employment_type


def _extract_experience_level_simplyhired(card) -> Optional[str]:
    """Extract experience level from SimplyHired job card."""
    text_content = card.get_text().lower()
    
    if 'executive' in text_content or 'c-level' in text_content:
        return 'Executive'
    elif 'director' in text_content or 'manager' in text_content:
        return 'Director'
    elif 'senior' in text_content:
        return 'Senior'
    elif 'mid' in text_content or 'mid-level' in text_content:
        return 'Mid'
    elif 'junior' in text_content or 'entry' in text_content or 'entry-level' in text_content:
        return 'Entry'
    elif 'assistant' in text_content:
        return 'Assistant'
    elif 'intern' in text_content or 'internship' in text_content:
        return 'Intern'
    
    return None


def _extract_posted_date_simplyhired(card) -> Optional[str]:
    """Extract posted date from SimplyHired job card."""
    date_selectors = [
        '[class*="date"]',
        '[class*="Date"]',
        '[class*="posted"]',
        '[data-testid="posted-date"]'
    ]
    
    for selector in date_selectors:
        date_elem = card.select_one(selector)
        if date_elem:
            date_text = date_elem.get_text(strip=True)
            if date_text:
                return date_text
    
    # Pattern matching
    text_content = card.get_text()
    date_patterns = [
        r'(?:posted|posted on|published):\s*([^\n]+)',
        r'(\d+\s+(?:day|days|hour|hours|week|weeks|month|months)\s+ago)',
        r'((?:today|yesterday))'
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    
    return None


def _extract_description_simplyhired(card) -> Optional[str]:
    """Extract job description from SimplyHired job card."""
    description_selectors = [
        '[class*="description"]',
        '[class*="Description"]',
        '[class*="summary"]',
        '[class*="Summary"]',
        '[class*="snippet"]',
        '[data-testid="job-description"]'
    ]
    
    for selector in description_selectors:
        desc_elem = card.select_one(selector)
        if desc_elem:
            description = desc_elem.get_text(strip=True)
            if description and len(description) > 20:
                return description
    
    return None


def _extract_job_url_simplyhired(card) -> Optional[str]:
    """Extract job URL from SimplyHired job card."""
    # SimplyHired job URLs typically contain /job/
    job_link = card.select_one('a[href*="/job/"]')
    if job_link:
        href = job_link.get('href', '')
        if href:
            if href.startswith('http'):
                return href
            else:
                return f"https://www.simplyhired.com{href}"
    
    return None


def _extract_job_id_simplyhired(card) -> Optional[str]:
    """Extract job ID from SimplyHired job card."""
    # Try to extract from URL
    job_url = _extract_job_url_simplyhired(card)
    if job_url:
        # SimplyHired job IDs are typically in the URL path
        match = re.search(r'/job/([^/?]+)', job_url)
        if match:
            return match.group(1)
    
    # Try data attributes
    job_id = card.get('data-job-id') or card.get('data-id')
    if job_id:
        return str(job_id)
    
    return None


def _extract_skills_simplyhired(card) -> List[str]:
    """Extract skills from SimplyHired job card."""
    skills = []
    
    # Look for skills section
    skills_elem = card.select_one('[class*="skill"], [class*="Skill"]')
    if skills_elem:
        skill_text = skills_elem.get_text()
        # Split by common delimiters
        skills = [s.strip() for s in re.split(r'[,;|]', skill_text) if s.strip()]
    
    return skills[:10]  # Limit to 10 skills


def _extract_requirements_simplyhired(card) -> List[str]:
    """Extract requirements from SimplyHired job card."""
    requirements = []
    
    # Look for requirements section
    req_elem = card.select_one('[class*="requirement"], [class*="Requirement"]')
    if req_elem:
        req_text = req_elem.get_text()
        # Split by line breaks or bullets
        requirements = [r.strip() for r in re.split(r'[\n•\-\*]', req_text) if r.strip() and len(r.strip()) > 10]
    
    return requirements[:10]  # Limit to 10 requirements


def _extract_benefits_simplyhired(card) -> List[str]:
    """Extract benefits from SimplyHired job card."""
    benefits = []
    
    # Look for benefits section
    benefits_elem = card.select_one('[class*="benefit"], [class*="Benefit"]')
    if benefits_elem:
        benefits_text = benefits_elem.get_text()
        # Split by common delimiters
        benefits = [b.strip() for b in re.split(r'[,;|•\-\*]', benefits_text) if b.strip()]
    
    return benefits[:10]  # Limit to 10 benefits


def _extract_detailed_job_info_simplyhired(card) -> Optional[Job]:
    """Extract detailed job information from a SimplyHired job card."""
    try:
        # Extract job title
        title = _extract_title_simplyhired(card)
        if not title or len(title) < 3:
            return None
        
        # Extract company information
        company, company_url = _extract_company_info_simplyhired(card)
        
        # Extract location and remote type
        location, remote_type = _extract_location_info_simplyhired(card)
        
        # Extract salary range
        salary_range = _extract_salary_simplyhired(card)
        
        # Extract job type and employment type
        job_type, employment_type = _extract_job_types_simplyhired(card)
        
        # Extract experience level
        experience_level = _extract_experience_level_simplyhired(card)
        
        # Extract posted date
        posted_date = _extract_posted_date_simplyhired(card)
        
        # Extract job description
        description = _extract_description_simplyhired(card)
        
        # Extract job URL
        job_url = _extract_job_url_simplyhired(card)
        
        # Extract job ID
        job_id = _extract_job_id_simplyhired(card)
        
        # Extract skills, requirements, and benefits
        skills = _extract_skills_simplyhired(card)
        requirements = _extract_requirements_simplyhired(card)
        benefits = _extract_benefits_simplyhired(card)
        
        return Job(
            title=title,
            company=company,
            company_url=company_url,
            location=location,
            description=description,
            url=job_url,
            salary_range=salary_range,
            job_type=job_type,
            posted_date=posted_date,
            experience_level=experience_level,
            benefits=benefits,
            requirements=requirements,
            skills=skills,
            remote_type=remote_type,
            employment_type=employment_type,
            job_id=job_id
        )
        
    except Exception as e:
        print(f"Error extracting job info: {e}")
        return None


def _is_end_of_results_simplyhired(soup: BeautifulSoup) -> bool:
    """Check if we've reached the end of SimplyHired results."""
    # Look for "no more results" indicators
    no_results_indicators = [
        'no results found',
        'no jobs found',
        'try different keywords',
        'no matching jobs'
    ]
    
    page_text = soup.get_text().lower()
    for indicator in no_results_indicators:
        if indicator in page_text:
            return True
    
    return False


def _create_job_id(job: Job) -> str:
    """Create a unique job ID from job data."""
    # Use title + company + location as unique identifier
    parts = [job.title or '', job.company or '', job.location or '']
    return '|'.join(parts).lower().strip()


def scrape_simplyhired_selenium(
    query: str,
    location: Optional[str] = None,
    max_results: int = 20,
    job_type: Optional[str] = None,
    salary_min: Optional[int] = None,
    salary_max: Optional[int] = None,
    experience_level: Optional[str] = None,
    employment_type: Optional[str] = None,
    days_old: Optional[int] = None
) -> List[Job]:
    """
    Enhanced SimplyHired scraper using Selenium with comprehensive data extraction and advanced filtering.
    
    Args:
        query: Job search query (e.g., "python developer")
        location: Job location (e.g., "remote", "New York, NY", "USA", "California")
        max_results: Maximum number of jobs to return
        job_type: Job type filter ('remote', 'hybrid', 'On-site')
        salary_min: Minimum salary filter (e.g., 50000)
        salary_max: Maximum salary filter (e.g., 100000)
        experience_level: Experience level filter ('intern', 'assistant', 'entry', 'junior', 'mid', 'mid-senior', 'senior', 'director', 'executive')
        employment_type: Employment type filter ('Full-Time', 'Part-Time', 'Contract', 'Internship')
        days_old: Filter jobs posted within last N days (e.g., 30 for last 30 days)
    
    Returns:
        List of Job objects with detailed information
    """
    global _last_fetch
    
    # Rate limiting
    with _request_lock:
        now = time.monotonic()
        jitter = random.uniform(0, 0.75)
        wait = settings.MIN_DELAY + jitter - (now - _last_fetch)
        if wait > 0:
            time.sleep(wait)
        _last_fetch = time.monotonic()
    
    return _scrape_sync_simplyhired(query, location, max_results, job_type, salary_min, salary_max, experience_level, employment_type, days_old)


def _scrape_sync_simplyhired(
    query: str,
    location: Optional[str],
    max_results: int,
    job_type: Optional[str] = None,
    salary_min: Optional[int] = None,
    salary_max: Optional[int] = None,
    experience_level: Optional[str] = None,
    employment_type: Optional[str] = None,
    days_old: Optional[int] = None
) -> List[Job]:
    """Enhanced synchronous scraping function for SimplyHired."""
    driver = None
    
    # Check for too many Chrome processes before starting
    process_count = check_chrome_process_count()
    if process_count > 15:
        print(f"⚠️  WARNING: {process_count} Chrome processes detected before scraping!")
        cleanup_zombie_processes(aggressive=True)
        time.sleep(2.0)
    
    try:
        print(f"🔍 [SIMPLYHIRED] Starting scrape: query='{query}', location='{location}', max_results={max_results}")
        print("🔄 [SIMPLYHIRED] Getting driver instance...")
        driver = get_shared_driver(force_new=True)
        print("✓ [SIMPLYHIRED] Driver obtained successfully")
        
        jobs = []
        seen_job_ids = set()
        page = 0
        max_pages = 15  # SimplyHired typically shows multiple jobs per page
        
        while len(jobs) < max_results and page < max_pages:
            # Build SimplyHired search URL
            base_url = "https://www.simplyhired.com/search"
            params = f"?q={quote_plus(query)}"
            
            if location:
                location_param = _format_location_for_simplyhired(location)
                params += f"&l={location_param}"
                print(f"DEBUG - Location '{location}' formatted as '{location_param}'")
            
            # Add employment type filter
            if employment_type:
                employment_filter = _get_simplyhired_employment_filter(employment_type)
                if employment_filter:
                    params += f"&{employment_filter}"
                    print(f"DEBUG - Employment type '{employment_type}' mapped to filter '{employment_filter}'")
            
            # Add date posted filter
            if days_old:
                date_filter = _get_simplyhired_date_filter(days_old)
                if date_filter:
                    params += f"&{date_filter}"
                    print(f"DEBUG - Date filter '{days_old} days' mapped to '{date_filter}'")
            
            # Add pagination
            if page > 0:
                params += f"&pn={page + 1}"  # SimplyHired uses pn parameter for page number
            
            url = base_url + params
            print(f"Navigating to page {page + 1}: {url}")
            
            # Navigate to the page
            retries = 0
            max_navigation_retries = getattr(settings, "MAX_RETRIES", 3)
            
            while True:
                try:
                    print(f"   [SIMPLYHIRED] Attempting navigation to: {url}")
                    driver.get(url)
                    print(f"✓ [SIMPLYHIRED] Navigation successful")
                    
                    time.sleep(0.5)
                    page_html = driver.page_source or ""
                    if not page_html:
                        raise Exception("Page source is empty")
                    print(f"✓ [SIMPLYHIRED] Page source retrieved: {len(page_html)} characters")
                    break
                    
                except Exception as nav_error:
                    error_msg = str(nav_error)
                    print(f"❌ [SIMPLYHIRED] Navigation failed: {error_msg}")
                    
                    if retries < max_navigation_retries:
                        retries += 1
                        print(f"⚠️  Retrying navigation ({retries}/{max_navigation_retries})...")
                        time.sleep(2.0)
                        continue
                    else:
                        raise
            
            # Check for Cloudflare blocking
            has_cloudflare_indicators = (
                "Checking your browser" in page_html
                or "Enable JavaScript and cookies to continue" in page_html
                or ("challenge-platform" in page_html and "<title>Just a moment" in page_html)
            )
            has_simplyhired_content = (
                'simplyhired.com' in page_html
                or 'job-card' in page_html.lower()
                or '/job/' in page_html
            )
            
            is_actually_blocked = has_cloudflare_indicators and not has_simplyhired_content
            
            if is_actually_blocked:
                raise CloudflareBlockedError("SimplyHired blocked by Cloudflare (captcha/turnstile)")
            
            # Random delay to appear more human-like
            time.sleep(random.uniform(getattr(settings, "PAGE_DELAY_MIN", 2.0), getattr(settings, "PAGE_DELAY_MAX", 5.8)))
            if getattr(settings, "HUMANIZE", True):
                _perform_human_interactions(driver)
            
            # Wait for job results
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div[class*='job-card'], div[class*='SerpJob'], a[href*='/job/']"))
                )
            except:
                pass  # Continue even if wait times out
            
            # Additional scrolling
            if getattr(settings, "HUMANIZE", True):
                _progressive_scroll(driver)
            
            # Get page source and parse with BeautifulSoup
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # Find job cards
            job_cards = _find_job_cards_simplyhired(soup)
            
            print(f"Found {len(job_cards)} potential job cards on page {page + 1}")
            
            # If no job cards found, we've reached the end
            if not job_cards:
                print(f"DEBUG - No job cards found on page {page + 1}, reached end of results")
                break
            
            # Check if we've reached the end
            if _is_end_of_results_simplyhired(soup):
                print(f"Reached end of results on page {page + 1}")
                break
            
            # Process job cards
            page_jobs_added = 0
            for i, card in enumerate(job_cards):
                try:
                    # Validate card
                    if not _is_valid_job_card_simplyhired(card):
                        print(f"DEBUG - Skipping card {i+1} - doesn't appear to be a valid job listing")
                        continue
                    
                    # Extract job info
                    job = _extract_detailed_job_info_simplyhired(card)
                    
                    if not job or not job.title:
                        continue
                    
                    # Create unique job ID
                    job_id = _create_job_id(job)
                    
                    # Skip duplicates
                    if job_id in seen_job_ids:
                        continue
                    seen_job_ids.add(job_id)
                    
                    # Apply filters
                    if job_type:
                        job_type_lower = job_type.lower()
                        if job_type_lower == 'remote' and job.remote_type != 'Remote':
                            continue
                        elif job_type_lower == 'hybrid' and job.remote_type != 'Hybrid':
                            continue
                        elif job_type_lower in ['onsite', 'on-site'] and job.remote_type != 'On-site':
                            continue
                    
                    # Salary filtering
                    if salary_min or salary_max:
                        if job.salary_range:
                            # Extract salary numbers from salary_range string
                            salary_numbers = re.findall(r'\$?([\d,]+)', job.salary_range.replace(',', ''))
                            if salary_numbers:
                                try:
                                    # Use average if range, or single value
                                    salaries = [int(s) for s in salary_numbers]
                                    avg_salary = sum(salaries) / len(salaries)
                                    
                                    if salary_min and avg_salary < salary_min:
                                        continue
                                    if salary_max and avg_salary > salary_max:
                                        continue
                                except:
                                    pass  # If parsing fails, include the job
                    
                    # Experience level filtering
                    if experience_level and job.experience_level:
                        exp_levels = {
                            'intern': ['intern'],
                            'assistant': ['assistant'],
                            'entry': ['entry', 'junior'],
                            'junior': ['entry', 'junior'],
                            'mid': ['mid'],
                            'mid-senior': ['mid', 'senior'],
                            'senior': ['senior'],
                            'director': ['director'],
                            'executive': ['executive']
                        }
                        exp_lower = experience_level.lower()
                        if exp_lower in exp_levels:
                            if job.experience_level.lower() not in exp_levels[exp_lower]:
                                continue
                    
                    # Employment type filtering
                    if employment_type and job.employment_type:
                        if employment_type.lower() != job.employment_type.lower():
                            continue
                    
                    jobs.append(job)
                    page_jobs_added += 1
                    
                    if len(jobs) >= max_results:
                        break
                        
                except Exception as e:
                    print(f"Error processing job card {i+1}: {e}")
                    continue
            
            print(f"Added {page_jobs_added} jobs from page {page + 1} (total: {len(jobs)})")
            
            if len(jobs) >= max_results:
                break
            
            if page_jobs_added == 0:
                print("No new jobs added on this page, stopping pagination")
                break
            
            page += 1
        
        print(f"✓ [SIMPLYHIRED] Scraping complete: {len(jobs)} jobs found")
        return jobs[:max_results]
        
    except CloudflareBlockedError:
        raise
    except Exception as e:
        error_msg = str(e)
        print(f"❌ [SIMPLYHIRED] Error during scraping: {error_msg}")
        import traceback
        print(f"   Full traceback:\n{traceback.format_exc()}")
        raise Exception(f"Failed to scrape SimplyHired: {error_msg}")
    finally:
        # Cleanup is handled by the shared driver management
        pass


def close_driver():
    """Close the WebDriver when done."""
    # Use shared cleanup function
    cleanup_global_driver()


def force_cleanup_all():
    """Force cleanup of all Chrome/ChromeDriver resources."""
    # Use shared cleanup function
    from app.services.indeed_selenium_service import force_cleanup_all as shared_force_cleanup
    return shared_force_cleanup()

