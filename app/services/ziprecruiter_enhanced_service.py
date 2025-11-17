import time
import asyncio
import random
import re
import json
import os
from typing import Optional, List, Dict, Any
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from app.models.job_model import Job
from app.core.config import settings

_last_fetch = 0
_request_lock = asyncio.Lock()
_driver = None


def get_chrome_executable_path() -> Optional[str]:
    """Get Chrome executable path based on environment."""
    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin and os.path.exists(chrome_bin):
        return chrome_bin
    
    # Common paths
    paths = [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    
    for path in paths:
        if os.path.exists(path):
            return path
    
    return None


def get_driver():
    """Initialize and return an undetected Chrome WebDriver instance."""
    global _driver
    if _driver is None:
        options = uc.ChromeOptions()
        
        # Add necessary arguments for containerized environments
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument(f"user-agent={settings.USER_AGENT}")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--remote-debugging-port=9222")
        options.add_argument("--remote-debugging-address=0.0.0.0")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-breakpad")
        options.add_argument("--disable-client-side-phishing-detection")
        options.add_argument("--disable-default-apps")
        options.add_argument("--disable-hang-monitor")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-prompt-on-repost")
        options.add_argument("--disable-sync")
        options.add_argument("--metrics-recording-only")
        options.add_argument("--no-first-run")
        options.add_argument("--safebrowsing-disable-auto-update")
        options.add_argument("--password-store=basic")
        
        # Initialize undetected chromedriver
        chrome_path = get_chrome_executable_path()
        _driver = uc.Chrome(
            options=options,
            driver_executable_path=None,
            browser_executable_path=chrome_path,
            use_subprocess=True,
            version_main=None
        )
        
        # Additional stealth measures
        _driver.execute_cdp_cmd('Network.setUserAgentOverride', {
            "userAgent": settings.USER_AGENT
        })
        
        # Random viewport size
        viewport_width = random.randint(1366, 1920)
        viewport_height = random.randint(768, 1080)
        _driver.set_window_size(viewport_width, viewport_height)
    
    return _driver


async def scrape_ziprecruiter_enhanced(query: str, location: Optional[str] = None, max_results: int = 20, job_type: Optional[str] = None) -> List[Job]:
    """
    Enhanced ZipRecruiter scraper that extracts detailed job information.
    
    Args:
        query: Job search query (e.g., "python developer")
        location: Job location (e.g., "remote", "New York, NY")
        max_results: Maximum number of jobs to return
    
    Returns:
        List of Job objects with detailed information
    """
    global _last_fetch
    
    # Rate limiting
    async with _request_lock:
        now = time.monotonic()
        wait = settings.MIN_DELAY - (now - _last_fetch)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_fetch = time.monotonic()
    
    # Run the scraping in a thread pool to avoid blocking
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _scrape_sync_enhanced, query, location, max_results, job_type)


def _scrape_sync_enhanced(query: str, location: Optional[str], max_results: int, job_type: Optional[str] = None) -> List[Job]:
    """Enhanced synchronous scraping function with pagination to get ALL jobs."""
    driver = get_driver()
    jobs = []
    all_jobs_before_filter = []  # Track jobs before filtering
    page = 1
    max_pages = 50  # Reasonable limit to prevent infinite loops
    jobs_per_page = 20  # ZipRecruiter typically shows 20 jobs per page
    
    try:
        while len(jobs) < max_results and page <= max_pages:
            # Build ZipRecruiter search URL with pagination
            if location:
                location_param = _format_location_for_ziprecruiter(location)
                url = f"https://www.ziprecruiter.com/candidate/search?search={query}&location={location_param}&page={page}"
            else:
                url = f"https://www.ziprecruiter.com/candidate/search?search={query}&page={page}"
            
            print(f"Navigating to page {page}: {url}")
            
            # Navigate to the page
            driver.get(url)
            
            # Random delay to appear more human-like
            time.sleep(random.uniform(3, 6))
            
            # Try to wait for job results
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "article.job_result, div.job_content"))
                )
            except:
                pass
            
            # Get page source and parse with BeautifulSoup
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # Check if we've reached the end of results
            if _is_end_of_results(soup):
                print(f"Reached end of results on page {page}")
                break
            
            # Try to extract job data from JSON in the page
            jobs_data = _extract_jobs_from_json(soup)
            
            if jobs_data:
                print(f"Found {len(jobs_data)} jobs from JSON data on page {page}")
                page_jobs_added = 0
                
                for job_data in jobs_data:
                    try:
                        job = _convert_json_to_job(job_data)
                        if job and job.title:
                            all_jobs_before_filter.append(job)
                            
                            # Apply job type and location filtering with more permissive logic
                            location_match = _matches_location_filter(job, location)
                            job_type_match = _matches_job_type_filter(job, job_type)
                            
                            # If no location filter specified, always match
                            if not location:
                                location_match = True
                            
                            # If no job type filter specified, always match
                            if not job_type:
                                job_type_match = True
                            
                            if location_match and job_type_match:
                                jobs.append(job)
                                page_jobs_added += 1
                                print(f"✓ Matched: {job.title} at {job.company or 'Unknown'} in {job.location or 'Unknown'} ({job.remote_type or 'Unknown'})")
                            else:
                                print(f"✗ Filtered: {job.title} (location_match={location_match}, job_type_match={job_type_match}, job_location={job.location})")
                            
                            # Stop if we've reached the max_results limit
                            if len(jobs) >= max_results:
                                break
                    except Exception as e:
                        print(f"Error converting job data: {e}")
                        continue
                
                # If no jobs were added from this page, we might have reached the end
                if page_jobs_added == 0:
                    print(f"No new jobs found on page {page}, stopping pagination")
                    break
                    
                print(f"Page {page} complete: {page_jobs_added} jobs added, {len(jobs)} total so far")
            else:
                print(f"No JSON data found on page {page}, stopping pagination")
                break
            
            page += 1
        
        # Apply max_results limit at the end
        jobs = jobs[:max_results]
        
        # Better debugging output
        print(f"\n=== SCRAPING SUMMARY ===")
        print(f"URL: {url}")
        print(f"Total jobs found: {len(all_jobs_before_filter)}")
        print(f"Jobs after filtering: {len(jobs)}")
        print(f"Search criteria: query='{query}', location='{location}', job_type='{job_type}'")
        print(f"Max results requested: {max_results}")
        
        if all_jobs_before_filter and not jobs:
            print("\n⚠️  WARNING: Jobs were found but all were filtered out!")
            print("Sample locations from found jobs:")
            for job in all_jobs_before_filter[:5]:
                print(f"  - {job.location} (remote_type: {job.remote_type})")
            print("\nThis might mean:")
            print("  1. The location filter is too strict")
            print("  2. The job type filter is too strict")
            print("  3. Try removing filters to see all available jobs")
        
        if not all_jobs_before_filter:
            print(f"\n⚠️  No jobs found on ZipRecruiter for this search.")
            print("This might mean:")
            print("  1. ZipRecruiter doesn't have jobs for this location")
            print("  2. The search query didn't return results")
            print("  3. The page structure has changed")
            
            # Save debug info
            with open('/tmp/ziprecruiter_enhanced_debug.html', 'w', encoding='utf-8') as f:
                f.write(driver.page_source)
            print("Debug HTML saved to /tmp/ziprecruiter_enhanced_debug.html")
        
    except Exception as e:
        raise Exception(f"Failed to scrape ZipRecruiter: {str(e)}")
    
    return jobs

def _extract_detailed_job_info(card) -> Optional[Job]:
    """Extract detailed job information from a job card."""
    try:
        # Extract job title
        title = _extract_title(card)
        if not title or len(title) < 3:
            return None
        
        # Extract company information
        company, company_url = _extract_company_info(card)
        
        # Extract location and remote type
        location, remote_type = _extract_location_info(card)
        
        # Extract salary range
        salary_range = _extract_salary(card)
        
        # Extract job type and employment type
        job_type, employment_type = _extract_job_types(card)
        
        # Extract experience level
        experience_level = _extract_experience_level(card)
        
        # Extract posted date
        posted_date = _extract_posted_date(card)
        
        # Extract job description
        description = _extract_description(card)
        
        # Extract job URL
        job_url = _extract_job_url(card)
        
        # Extract job ID
        job_id = _extract_job_id(card)
        
        # Extract skills and requirements
        skills = _extract_skills(card)
        requirements = _extract_requirements(card)
        benefits = _extract_benefits(card)
        
        # Extract industry and company size
        industry = _extract_industry(card)
        company_size = _extract_company_size(card)
        
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
            industry=industry,
            company_size=company_size,
            job_id=job_id
        )
        
    except Exception as e:
        print(f"Error extracting job info: {e}")
        return None


def _extract_title(card) -> Optional[str]:
    """Extract job title."""
    title_elem = (card.find('h2') or 
                 card.find('a', class_=lambda x: 'job' in x.lower() if x else False) or
                 card.find('span', attrs={'data-job-title': True}))
    
    if title_elem:
        title = title_elem.get_text(strip=True)
        if not title or len(title) < 3:
            title = title_elem.get('aria-label', '') or title_elem.get('title', '')
        return title
    return None


def _extract_company_info(card) -> tuple[Optional[str], Optional[str]]:
    """Extract company name and URL."""
    company = None
    company_url = None
    
    # Try to find company link
    company_link = card.find('a', attrs={'aria-label': lambda x: 'hiring' in x.lower() if x else False})
    if company_link:
        company = company_link.get_text(strip=True)
        href = company_link.get('href', '')
        if href:
            company_url = f"https://www.ziprecruiter.com{href}" if href.startswith('/') else href
    
    # If no company link found, try other methods
    if not company:
        for elem in card.find_all(['a', 'span', 'div']):
            text = elem.get_text(strip=True)
            if text and 3 < len(text) < 50:
                # Check if it looks like a company name
                if not any(keyword in text.lower() for keyword in 
                          ['days ago', 'hours ago', 'just posted', 'salary', '$', 'remote', 'full', 'part', 'time']):
                    company = text
                    # Check if this element has a link
                    if elem.name == 'a':
                        href = elem.get('href', '')
                        if href:
                            company_url = f"https://www.ziprecruiter.com{href}" if href.startswith('/') else href
                    break
    
    return company, company_url


def _extract_location_info(card) -> tuple[Optional[str], Optional[str]]:
    """Extract location and remote type."""
    location = None
    remote_type = None
    
    # Look for location patterns
    for elem in card.find_all(['a', 'span', 'div', 'li']):
        text = elem.get_text(strip=True)
        if text and (',' in text or 'remote' in text.lower() or any(state in text for state in ['CA', 'NY', 'TX', 'FL', 'WA'])):
            if len(text) < 100 and text:
                location = text
                # Determine remote type
                if 'remote' in text.lower():
                    if 'hybrid' in text.lower():
                        remote_type = 'Hybrid'
                    else:
                        remote_type = 'Remote'
                else:
                    remote_type = 'On-site'
                break
    
    return location, remote_type


def _extract_salary(card) -> Optional[str]:
    """Extract salary range."""
    # Look for salary patterns
    salary_patterns = [
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        r'\$[\d,]+(?:K|k)?\s*/\s*(?:year|yr|hour|hr)',
        r'[\d,]+(?:K|k)\s*-\s*[\d,]+(?:K|k)',
        r'Salary:\s*\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?'
    ]
    
    text_content = card.get_text()
    for pattern in salary_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            return match.group().strip()
    
    return None


def _extract_job_types(card) -> tuple[Optional[str], Optional[str]]:
    """Extract job type and employment type."""
    job_type = None
    employment_type = None
    
    text_content = card.get_text().lower()
    
    # Employment type patterns
    if 'full-time' in text_content or 'full time' in text_content:
        employment_type = 'Full-time'
    elif 'part-time' in text_content or 'part time' in text_content:
        employment_type = 'Part-time'
    elif 'contract' in text_content:
        employment_type = 'Contract'
    elif 'internship' in text_content:
        employment_type = 'Internship'
    elif 'temporary' in text_content or 'temp' in text_content:
        employment_type = 'Temporary'
    
    # Job type patterns
    if 'permanent' in text_content:
        job_type = 'Permanent'
    elif 'temporary' in text_content:
        job_type = 'Temporary'
    elif 'contract' in text_content:
        job_type = 'Contract'
    
    return job_type, employment_type


def _extract_experience_level(card) -> Optional[str]:
    """Extract experience level."""
    text_content = card.get_text().lower()
    
    if any(level in text_content for level in ['senior', 'sr.', 'lead', 'principal', 'staff']):
        return 'Senior'
    elif any(level in text_content for level in ['mid', 'intermediate', 'experienced']):
        return 'Mid-level'
    elif any(level in text_content for level in ['junior', 'jr.', 'entry', 'associate']):
        return 'Entry-level'
    elif 'executive' in text_content or 'director' in text_content or 'manager' in text_content:
        return 'Executive'
    
    return None


def _extract_posted_date(card) -> Optional[str]:
    """Extract posted date."""
    date_patterns = [
        r'\d+\s+(?:days?|hours?)\s+ago',
        r'Posted\s+\d+\s+(?:days?|hours?)\s+ago',
        r'Just\s+posted',
        r'Today',
        r'Yesterday'
    ]
    
    text_content = card.get_text()
    for pattern in date_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            return match.group().strip()
    
    return None


def _extract_description(card) -> Optional[str]:
    """Extract job description."""
    # Look for description in various elements
    description_elem = (card.find('p') or
                       card.find('div', class_=lambda x: x and any(keyword in x.lower() for keyword in ['snippet', 'description', 'summary']) if x else False))
    
    if description_elem:
        description = description_elem.get_text(strip=True)
        # Clean up and truncate if too long
        if description and len(description) > 1000:
            description = description[:1000] + '...'
        return description
    
    return None


def _extract_job_url(card) -> Optional[str]:
    """Extract job URL."""
    link_elem = card.find('a', href=True)
    if link_elem and 'href' in link_elem.attrs:
        href = link_elem['href']
        if href.startswith('/'):
            return f"https://www.ziprecruiter.com{href}"
        elif href.startswith('http'):
            return href
    return None


def _extract_job_id(card) -> Optional[str]:
    """Extract job ID from URL or data attributes."""
    # Try to get from URL
    link_elem = card.find('a', href=True)
    if link_elem:
        href = link_elem.get('href', '')
        # Extract ID from URL pattern
        id_match = re.search(r'id=([^&]+)', href)
        if id_match:
            return id_match.group(1)
    
    # Try data attributes
    for elem in card.find_all(attrs={'data-job-id': True}):
        return elem.get('data-job-id')
    
    return None


def _extract_skills(card) -> List[str]:
    """Extract skills from job description."""
    skills = []
    text_content = card.get_text().lower()
    
    # Common tech skills
    tech_skills = [
        'python', 'javascript', 'java', 'react', 'angular', 'vue', 'node.js',
        'django', 'flask', 'spring', 'express', 'mongodb', 'postgresql',
        'mysql', 'redis', 'docker', 'kubernetes', 'aws', 'azure', 'gcp',
        'git', 'jenkins', 'ci/cd', 'rest api', 'graphql', 'microservices'
    ]
    
    for skill in tech_skills:
        if skill in text_content:
            skills.append(skill.title())
    
    return skills[:10]  # Limit to 10 skills


def _extract_requirements(card) -> List[str]:
    """Extract job requirements."""
    requirements = []
    text_content = card.get_text()
    
    # Look for requirement patterns
    req_patterns = [
        r'Requirements?:?\s*([^.]+)',
        r'Must have:?\s*([^.]+)',
        r'Required:?\s*([^.]+)',
        r'Qualifications:?\s*([^.]+)'
    ]
    
    for pattern in req_patterns:
        matches = re.findall(pattern, text_content, re.IGNORECASE | re.MULTILINE)
        for match in matches:
            req_text = match.strip()
            if len(req_text) > 10 and len(req_text) < 200:
                requirements.append(req_text)
    
    return requirements[:5]  # Limit to 5 requirements


def _extract_benefits(card) -> List[str]:
    """Extract job benefits."""
    benefits = []
    text_content = card.get_text().lower()
    
    # Common benefits
    benefit_keywords = [
        'health insurance', 'dental', 'vision', '401k', 'retirement',
        'vacation', 'pto', 'paid time off', 'flexible schedule',
        'remote work', 'work from home', 'stock options', 'bonus',
        'professional development', 'training', 'gym membership'
    ]
    
    for benefit in benefit_keywords:
        if benefit in text_content:
            benefits.append(benefit.title())
    
    return benefits[:8]  # Limit to 8 benefits


def _extract_industry(card) -> Optional[str]:
    """Extract industry."""
    text_content = card.get_text().lower()
    
    industries = [
        'technology', 'healthcare', 'finance', 'education', 'retail',
        'manufacturing', 'consulting', 'nonprofit', 'government',
        'media', 'entertainment', 'real estate', 'automotive'
    ]
    
    for industry in industries:
        if industry in text_content:
            return industry.title()
    
    return None


def _extract_company_size(card) -> Optional[str]:
    """Extract company size."""
    text_content = card.get_text()
    
    size_patterns = [
        r'(\d+)\s*-\s*(\d+)\s*employees',
        r'(\d+)\+?\s*employees',
        r'startup', 'small business', 'enterprise', 'fortune 500'
    ]
    
    for pattern in size_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            return match.group().strip()
    
    return None


def _is_end_of_results(soup: BeautifulSoup) -> bool:
    """Check if we've reached the end of available job results."""
    # Look for common "no more results" indicators
    end_indicators = [
        "no more results",
        "no results found",
        "end of results",
        "that's all the jobs",
        "no jobs match",
        "try different search"
    ]
    
    page_text = soup.get_text().lower()
    return any(indicator in page_text for indicator in end_indicators)


def _extract_jobs_from_json(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """Extract job data from JSON embedded in the page."""
    jobs_data = []
    
    try:
        # Look for script tags containing job data
        script_tags = soup.find_all('script', type='application/json')
        
        for script in script_tags:
            try:
                data = json.loads(script.string)
                # Look for job cards in the JSON structure
                if 'hydrateJobCardsResponse' in data and 'jobCards' in data['hydrateJobCardsResponse']:
                    jobs_data = data['hydrateJobCardsResponse']['jobCards']
                    break
            except (json.JSONDecodeError, KeyError):
                continue
        
        # If not found in application/json, try to find in regular script tags
        if not jobs_data:
            script_tags = soup.find_all('script')
            for script in script_tags:
                if script.string and 'hydrateJobCardsResponse' in script.string:
                    try:
                        # Extract JSON from script content
                        start = script.string.find('"hydrateJobCardsResponse"')
                        if start != -1:
                            # Find the opening brace
                            brace_start = script.string.find('{', start)
                            if brace_start != -1:
                                # Find matching closing brace
                                brace_count = 0
                                end = brace_start
                                for i, char in enumerate(script.string[brace_start:], brace_start):
                                    if char == '{':
                                        brace_count += 1
                                    elif char == '}':
                                        brace_count -= 1
                                        if brace_count == 0:
                                            end = i + 1
                                            break
                                
                                json_str = script.string[brace_start:end]
                                data = json.loads(json_str)
                                if 'jobCards' in data:
                                    jobs_data = data['jobCards']
                                    break
                    except (json.JSONDecodeError, KeyError, ValueError):
                        continue
    
    except Exception as e:
        print(f"Error extracting JSON data: {e}")
    
    return jobs_data


def _convert_json_to_job(job_data: Dict[str, Any]) -> Optional[Job]:
    """Convert ZipRecruiter JSON job data to Job model."""
    try:
        # Extract basic info
        title = job_data.get('title', '')
        if not title:
            return None
        
        company = job_data.get('company', {}).get('name', '')
        company_url = job_data.get('companyUrl', '')
        if company_url and not company_url.startswith('http'):
            company_url = f"https://www.ziprecruiter.com{company_url}"
        
        # Extract location
        location_info = job_data.get('location', {})
        location = location_info.get('displayName', '')
        
        # Determine remote type
        location_types = job_data.get('locationTypes', [])
        remote_type = None
        if location_types:
            type_mapping = {1: 'On-site', 2: 'Hybrid', 3: 'Remote', 4: 'Remote'}
            remote_type = type_mapping.get(location_types[0].get('name'), 'On-site')
        
        # Extract salary
        salary_range = None
        pay_info = job_data.get('pay', {})
        if pay_info and pay_info.get('min') and pay_info.get('max'):
            min_sal = pay_info.get('min', 0)
            max_sal = pay_info.get('max', 0)
            if min_sal and max_sal:
                salary_range = f"${min_sal:,} - ${max_sal:,}"
        
        # Extract employment type
        employment_type = None
        employment_types = job_data.get('employmentTypes', [])
        if employment_types:
            type_mapping = {1: 'Full-time', 2: 'Part-time', 3: 'Contract', 4: 'Internship', 5: 'Temporary'}
            employment_type = type_mapping.get(employment_types[0].get('name'), 'Full-time')
        
        # Extract job description
        description = job_data.get('shortDescription', '')
        
        # Extract job URL
        job_url = job_data.get('jobRedirectPageUrl', '')
        if job_url and not job_url.startswith('http'):
            job_url = f"https://www.ziprecruiter.com{job_url}"
        
        # Extract job ID
        job_id = job_data.get('listingKey', '')
        
        # Extract experience level from title
        experience_level = None
        title_lower = title.lower()
        if any(level in title_lower for level in ['senior', 'sr.', 'lead', 'principal', 'staff']):
            experience_level = 'Senior'
        elif any(level in title_lower for level in ['mid', 'intermediate', 'experienced']):
            experience_level = 'Mid-level'
        elif any(level in title_lower for level in ['junior', 'jr.', 'entry', 'associate']):
            experience_level = 'Entry-level'
        elif any(level in title_lower for level in ['executive', 'director', 'manager']):
            experience_level = 'Executive'
        
        # Extract skills from description
        skills = []
        if description:
            tech_skills = [
                'python', 'javascript', 'java', 'react', 'angular', 'vue', 'node.js',
                'django', 'flask', 'spring', 'express', 'mongodb', 'postgresql',
                'mysql', 'redis', 'docker', 'kubernetes', 'aws', 'azure', 'gcp',
                'git', 'jenkins', 'ci/cd', 'rest api', 'graphql', 'microservices'
            ]
            desc_lower = description.lower()
            for skill in tech_skills:
                if skill in desc_lower:
                    skills.append(skill.title())
        
        # Extract benefits
        benefits = []
        benefits_data = job_data.get('benefits', [])
        benefit_mapping = {
            1: 'Health Insurance', 2: 'Dental', 3: 'Vision', 4: '401k', 
            5: 'Retirement', 6: 'Vacation', 7: 'PTO', 8: 'Flexible Schedule'
        }
        for benefit in benefits_data:
            benefit_name = benefit_mapping.get(benefit.get('name'), '')
            if benefit_name:
                benefits.append(benefit_name)
        
        # Extract posted date
        posted_date = None
        status = job_data.get('status', {})
        if status.get('postedAtUtc'):
            # Convert UTC timestamp to readable date
            try:
                from datetime import datetime
                posted_utc = status['postedAtUtc']
                posted_date = datetime.fromisoformat(posted_utc.replace('Z', '+00:00')).strftime('%Y-%m-%d')
            except:
                posted_date = status.get('postedAtUtc', '')
        
        return Job(
            title=title,
            company=company,
            company_url=company_url,
            location=location,
            description=description,
            url=job_url,
            salary_range=salary_range,
            job_type=None,  # Not available in JSON
            posted_date=posted_date,
            experience_level=experience_level,
            benefits=benefits[:8] if benefits else None,
            requirements=None,  # Would need to parse from description
            skills=skills[:10] if skills else None,
            remote_type=remote_type,
            employment_type=employment_type,
            industry=None,  # Not available in JSON
            company_size=None,  # Not available in JSON
            job_id=job_id
        )
        
    except Exception as e:
        print(f"Error converting job data: {e}")
        return None


def _matches_location_filter(job: Job, location_filter: Optional[str]) -> bool:
    """Check if job matches the location filter."""
    if not location_filter:
        return True
    
    # If no job location is available, accept it (don't filter out)
    if not job.location:
        return True
    
    location_filter = location_filter.lower().strip()
    job_location = job.location.lower()
    
    # Handle remote jobs - if user asks for remote, only show remote jobs
    if location_filter in ['remote', 'work from home', 'wfh']:
        return job.remote_type and job.remote_type.lower() == 'remote'
    
    # For non-remote searches, allow remote jobs to appear too
    # (remote jobs can be done from anywhere)
    if job.remote_type and job.remote_type.lower() == 'remote':
        return True
    
    # Handle country-level filtering - use partial matching
    if location_filter in ['usa', 'us', 'united states']:
        # Check for US locations (state codes, common cities, or "United States")
        us_indicators = ['united states', ', us', 'usa']
        us_states = ['al', 'ak', 'az', 'ar', 'ca', 'co', 'ct', 'de', 'fl', 'ga', 
                     'hi', 'id', 'il', 'in', 'ia', 'ks', 'ky', 'la', 'me', 'md',
                     'ma', 'mi', 'mn', 'ms', 'mo', 'mt', 'ne', 'nv', 'nh', 'nj',
                     'nm', 'ny', 'nc', 'nd', 'oh', 'ok', 'or', 'pa', 'ri', 'sc',
                     'sd', 'tn', 'tx', 'ut', 'vt', 'va', 'wa', 'wv', 'wi', 'wy']
        
        # Check for US indicators
        if any(indicator in job_location for indicator in us_indicators):
            return True
        
        # Check if location ends with state code (e.g., "New York, NY")
        location_parts = job_location.split(',')
        if len(location_parts) >= 2:
            state_code = location_parts[-1].strip()
            if state_code in us_states:
                return True
        
        return False
    
    # Handle US state filtering with both full names and abbreviations
    us_state_mappings = {
        'california': ['california', 'ca'],
        'ca': ['california', 'ca'],
        'texas': ['texas', 'tx'],
        'tx': ['texas', 'tx'],
        'florida': ['florida', 'fl'],
        'fl': ['florida', 'fl'],
        'new york': ['new york', 'ny'],
        'ny': ['new york', 'ny'],
        'illinois': ['illinois', 'il'],
        'il': ['illinois', 'il'],
        'pennsylvania': ['pennsylvania', 'pa'],
        'pa': ['pennsylvania', 'pa'],
        'ohio': ['ohio', 'oh'],
        'oh': ['ohio', 'oh'],
        'georgia': ['georgia', 'ga'],
        'ga': ['georgia', 'ga'],
        'north carolina': ['north carolina', 'nc'],
        'nc': ['north carolina', 'nc'],
        'michigan': ['michigan', 'mi'],
        'mi': ['michigan', 'mi'],
        'new jersey': ['new jersey', 'nj'],
        'nj': ['new jersey', 'nj'],
        'virginia': ['virginia', 'va'],
        'va': ['virginia', 'va'],
        'washington': ['washington', 'wa'],
        'wa': ['washington', 'wa'],
        'arizona': ['arizona', 'az'],
        'az': ['arizona', 'az'],
        'massachusetts': ['massachusetts', 'ma'],
        'ma': ['massachusetts', 'ma'],
        'tennessee': ['tennessee', 'tn'],
        'tn': ['tennessee', 'tn'],
        'indiana': ['indiana', 'in'],
        'in': ['indiana', 'in'],
        'missouri': ['missouri', 'mo'],
        'mo': ['missouri', 'mo'],
        'maryland': ['maryland', 'md'],
        'md': ['maryland', 'md'],
        'wisconsin': ['wisconsin', 'wi'],
        'wi': ['wisconsin', 'wi'],
        'colorado': ['colorado', 'co'],
        'co': ['colorado', 'co'],
        'minnesota': ['minnesota', 'mn'],
        'mn': ['minnesota', 'mn'],
        'south carolina': ['south carolina', 'sc'],
        'sc': ['south carolina', 'sc'],
        'alabama': ['alabama', 'al'],
        'al': ['alabama', 'al'],
        'louisiana': ['louisiana', 'la'],
        'la': ['louisiana', 'la'],
        'kentucky': ['kentucky', 'ky'],
        'ky': ['kentucky', 'ky'],
        'oregon': ['oregon', 'or'],
        'or': ['oregon', 'or'],
        'oklahoma': ['oklahoma', 'ok'],
        'ok': ['oklahoma', 'ok'],
        'connecticut': ['connecticut', 'ct'],
        'ct': ['connecticut', 'ct'],
        'utah': ['utah', 'ut'],
        'ut': ['utah', 'ut'],
        'iowa': ['iowa', 'ia'],
        'ia': ['iowa', 'ia'],
        'nevada': ['nevada', 'nv'],
        'nv': ['nevada', 'nv'],
        'arkansas': ['arkansas', 'ar'],
        'ar': ['arkansas', 'ar'],
        'mississippi': ['mississippi', 'ms'],
        'ms': ['mississippi', 'ms'],
        'kansas': ['kansas', 'ks'],
        'ks': ['kansas', 'ks'],
        'new mexico': ['new mexico', 'nm'],
        'nm': ['new mexico', 'nm'],
        'nebraska': ['nebraska', 'ne'],
        'ne': ['nebraska', 'ne'],
        'west virginia': ['west virginia', 'wv'],
        'wv': ['west virginia', 'wv'],
        'idaho': ['idaho', 'id'],
        'id': ['idaho', 'id'],
        'hawaii': ['hawaii', 'hi'],
        'hi': ['hawaii', 'hi'],
        'new hampshire': ['new hampshire', 'nh'],
        'nh': ['new hampshire', 'nh'],
        'maine': ['maine', 'me'],
        'me': ['maine', 'me'],
        'montana': ['montana', 'mt'],
        'mt': ['montana', 'mt'],
        'rhode island': ['rhode island', 'ri'],
        'ri': ['rhode island', 'ri'],
        'delaware': ['delaware', 'de'],
        'de': ['delaware', 'de'],
        'south dakota': ['south dakota', 'sd'],
        'sd': ['south dakota', 'sd'],
        'north dakota': ['north dakota', 'nd'],
        'nd': ['north dakota', 'nd'],
        'alaska': ['alaska', 'ak'],
        'ak': ['alaska', 'ak'],
        'vermont': ['vermont', 'vt'],
        'vt': ['vermont', 'vt'],
        'wyoming': ['wyoming', 'wy'],
        'wy': ['wyoming', 'wy']
    }
    
    if location_filter in us_state_mappings:
        state_keywords = us_state_mappings[location_filter]
        return any(keyword in job_location for keyword in state_keywords)
    
    elif location_filter in ['pakistan', 'pk']:
        return 'pakistan' in job_location or ', pk' in job_location
    
    elif location_filter in ['uk', 'united kingdom']:
        return 'uk' in job_location or 'united kingdom' in job_location or 'england' in job_location
    
    elif location_filter in ['canada', 'ca']:
        return 'canada' in job_location or ', ca' in job_location
    
    # Handle city-level filtering with more flexible matching
    city_keywords = {
        'lahore': ['lahore'],
        'karachi': ['karachi'],
        'islamabad': ['islamabad'],
        'new york': ['new york', 'nyc', 'ny'],
        'nyc': ['new york', 'nyc', 'ny'],
        'san francisco': ['san francisco', 'sf'],
        'sf': ['san francisco', 'sf'],
        'los angeles': ['los angeles', 'la'],
        'la': ['los angeles'],
        'chicago': ['chicago'],
        'boston': ['boston'],
        'seattle': ['seattle'],
        'austin': ['austin'],
        'denver': ['denver'],
        'miami': ['miami'],
        'london': ['london'],
        'toronto': ['toronto'],
        'vancouver': ['vancouver'],
        'sydney': ['sydney'],
        'melbourne': ['melbourne'],
        'berlin': ['berlin'],
        'paris': ['paris'],
        'mumbai': ['mumbai'],
        'delhi': ['delhi'],
        'bangalore': ['bangalore', 'bengaluru'],
        'tokyo': ['tokyo'],
        'shanghai': ['shanghai'],
        'beijing': ['beijing']
    }
    
    if location_filter in city_keywords:
        return any(keyword in job_location for keyword in city_keywords[location_filter])
    
    # For other locations, use flexible partial matching
    # This allows for variations in location format
    return location_filter in job_location

def _matches_job_type_filter(job: Job, job_type_filter: Optional[str]) -> bool:
    """Check if job matches the job type filter."""
    if not job_type_filter:
        return True
    
    job_type_filter = job_type_filter.lower().strip()
    job_remote_type = (job.remote_type or '').lower()
    job_title = (job.title or '').lower()
    job_description = (job.description or '').lower()
    
    # Map filter terms to job remote types with comprehensive matching
    if job_type_filter in ['remote', 'work from home', 'wfh', 'work from home', 'telecommute', 'telework']:
        # Check remote type and also look for remote keywords in title/description
        return (job_remote_type in ['remote'] or 
                any(keyword in job_title for keyword in ['remote', 'work from home', 'wfh', 'telecommute']) or
                any(keyword in job_description for keyword in ['remote', 'work from home', 'wfh', 'telecommute']))
    
    elif job_type_filter in ['hybrid', 'partially remote', 'part remote', 'flexible']:
        # Check hybrid type and also look for hybrid keywords
        return (job_remote_type in ['hybrid'] or 
                any(keyword in job_title for keyword in ['hybrid', 'partially remote', 'flexible']) or
                any(keyword in job_description for keyword in ['hybrid', 'partially remote', 'flexible']))
    
    elif job_type_filter in ['onsite', 'on-site', 'on site', 'office', 'in-person', 'in person']:
        # Check onsite type and also look for onsite keywords
        return (job_remote_type in ['on-site', 'onsite'] or 
                any(keyword in job_title for keyword in ['onsite', 'on-site', 'office', 'in-person']) or
                any(keyword in job_description for keyword in ['onsite', 'on-site', 'office', 'in-person']))
    
    # If no specific match, return True (don't filter)
    return True


def _format_location_for_ziprecruiter(location: str) -> str:
    """Format location for ZipRecruiter search."""
    location = location.strip().lower()
    
    # Handle remote job types
    if location in ['remote', 'work from home', 'wfh']:
        return 'remote'
    
    # Handle country-level searches
    country_mappings = {
        'usa': 'United+States',
        'us': 'United+States', 
        'united states': 'United+States',
        'pakistan': 'Pakistan',
        'pk': 'Pakistan',
        'uk': 'United+Kingdom',
        'united kingdom': 'United+Kingdom',
        'canada': 'Canada',
        'australia': 'Australia',
        'germany': 'Germany',
        'france': 'France',
        'india': 'India',
        'china': 'China',
        'japan': 'Japan'
    }
    
    if location in country_mappings:
        return country_mappings[location]
    
    # Handle US state searches
    us_state_url_mappings = {
        'california': 'California',
        'ca': 'California',
        'texas': 'Texas',
        'tx': 'Texas',
        'florida': 'Florida',
        'fl': 'Florida',
        'new york': 'New+York',
        'ny': 'New+York',
        'illinois': 'Illinois',
        'il': 'Illinois',
        'pennsylvania': 'Pennsylvania',
        'pa': 'Pennsylvania',
        'ohio': 'Ohio',
        'oh': 'Ohio',
        'georgia': 'Georgia',
        'ga': 'Georgia',
        'north carolina': 'North+Carolina',
        'nc': 'North+Carolina',
        'michigan': 'Michigan',
        'mi': 'Michigan',
        'new jersey': 'New+Jersey',
        'nj': 'New+Jersey',
        'virginia': 'Virginia',
        'va': 'Virginia',
        'washington': 'Washington',
        'wa': 'Washington',
        'arizona': 'Arizona',
        'az': 'Arizona',
        'massachusetts': 'Massachusetts',
        'ma': 'Massachusetts',
        'tennessee': 'Tennessee',
        'tn': 'Tennessee',
        'indiana': 'Indiana',
        'in': 'Indiana',
        'missouri': 'Missouri',
        'mo': 'Missouri',
        'maryland': 'Maryland',
        'md': 'Maryland',
        'wisconsin': 'Wisconsin',
        'wi': 'Wisconsin',
        'colorado': 'Colorado',
        'co': 'Colorado',
        'minnesota': 'Minnesota',
        'mn': 'Minnesota',
        'south carolina': 'South+Carolina',
        'sc': 'South+Carolina',
        'alabama': 'Alabama',
        'al': 'Alabama',
        'louisiana': 'Louisiana',
        'la': 'Louisiana',
        'kentucky': 'Kentucky',
        'ky': 'Kentucky',
        'oregon': 'Oregon',
        'or': 'Oregon',
        'oklahoma': 'Oklahoma',
        'ok': 'Oklahoma',
        'connecticut': 'Connecticut',
        'ct': 'Connecticut',
        'utah': 'Utah',
        'ut': 'Utah',
        'iowa': 'Iowa',
        'ia': 'Iowa',
        'nevada': 'Nevada',
        'nv': 'Nevada',
        'arkansas': 'Arkansas',
        'ar': 'Arkansas',
        'mississippi': 'Mississippi',
        'ms': 'Mississippi',
        'kansas': 'Kansas',
        'ks': 'Kansas',
        'new mexico': 'New+Mexico',
        'nm': 'New+Mexico',
        'nebraska': 'Nebraska',
        'ne': 'Nebraska',
        'west virginia': 'West+Virginia',
        'wv': 'West+Virginia',
        'idaho': 'Idaho',
        'id': 'Idaho',
        'hawaii': 'Hawaii',
        'hi': 'Hawaii',
        'new hampshire': 'New+Hampshire',
        'nh': 'New+Hampshire',
        'maine': 'Maine',
        'me': 'Maine',
        'montana': 'Montana',
        'mt': 'Montana',
        'rhode island': 'Rhode+Island',
        'ri': 'Rhode+Island',
        'delaware': 'Delaware',
        'de': 'Delaware',
        'south dakota': 'South+Dakota',
        'sd': 'South+Dakota',
        'north dakota': 'North+Dakota',
        'nd': 'North+Dakota',
        'alaska': 'Alaska',
        'ak': 'Alaska',
        'vermont': 'Vermont',
        'vt': 'Vermont',
        'wyoming': 'Wyoming',
        'wy': 'Wyoming'
    }
    
    if location in us_state_url_mappings:
        return us_state_url_mappings[location]
    
    # Handle city-level searches with proper formatting
    city_mappings = {
        'lahore': 'Lahore%2C+Pakistan',
        'karachi': 'Karachi%2C+Pakistan',
        'islamabad': 'Islamabad%2C+Pakistan',
        'new york': 'New+York%2C+NY',
        'nyc': 'New+York%2C+NY',
        'san francisco': 'San+Francisco%2C+CA',
        'sf': 'San+Francisco%2C+CA',
        'los angeles': 'Los+Angeles%2C+CA',
        'la': 'Los+Angeles%2C+CA',
        'chicago': 'Chicago%2C+IL',
        'boston': 'Boston%2C+MA',
        'seattle': 'Seattle%2C+WA',
        'austin': 'Austin%2C+TX',
        'denver': 'Denver%2C+CO',
        'miami': 'Miami%2C+FL',
        'london': 'London%2C+UK',
        'toronto': 'Toronto%2C+Canada',
        'vancouver': 'Vancouver%2C+Canada',
        'sydney': 'Sydney%2C+Australia',
        'melbourne': 'Melbourne%2C+Australia',
        'berlin': 'Berlin%2C+Germany',
        'paris': 'Paris%2C+France',
        'mumbai': 'Mumbai%2C+India',
        'delhi': 'Delhi%2C+India',
        'bangalore': 'Bangalore%2C+India',
        'tokyo': 'Tokyo%2C+Japan',
        'shanghai': 'Shanghai%2C+China',
        'beijing': 'Beijing%2C+China'
    }
    
    if location in city_mappings:
        return city_mappings[location]
    
    # For unmapped locations, URL encode spaces and commas
    return location.replace(' ', '+').replace(',', '%2C')



def close_driver():
    """Close the WebDriver when done."""
    global _driver
    if _driver:
        _driver.quit()
        _driver = None
