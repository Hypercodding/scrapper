import time
import asyncio
import random
import re
from typing import Optional, List
import undetected_chromedriver as uc
from urllib.parse import urlparse
import os
import json
import zipfile
import tempfile
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from selenium_stealth import stealth
from selenium.webdriver import ActionChains
from app.models.job_model import Job
from app.core.config import settings
from app.core.proxy_manager import get_proxy_manager, reset_proxy_manager

_last_fetch = 0
_request_lock = asyncio.Lock()
_driver = None
_driver_created_at = 0  # Track when driver was created for rotation


def get_chrome_executable_path() -> Optional[str]:
    """
    Get Chrome executable path based on environment.
    Checks CHROME_BIN env var first, then common installation paths.
    """
    # Check environment variable first (set in Dockerfile)
    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin and os.path.exists(chrome_bin):
        return chrome_bin
    
    # Common Linux paths
    linux_paths = [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]
    
    # Common macOS paths
    mac_paths = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    
    # Common Windows paths (for completeness)
    windows_paths = [
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    ]
    
    # Check all paths
    all_paths = linux_paths + mac_paths + windows_paths
    for path in all_paths:
        if os.path.exists(path):
            return path
    
    # If not found, return None and let undetected-chromedriver find it
    return None


class CloudflareBlockedError(Exception):
    """Raised when Indeed returns a Cloudflare/turnstile block page."""
    pass


def _get_proxy_urls() -> List[str]:
    """
    Get list of proxy URLs from settings.
    
    Returns:
        List of proxy URLs
    """
    proxy_urls = []
    
    # Check new PROXY_URLS setting (comma-separated)
    if hasattr(settings, "PROXY_URLS") and settings.PROXY_URLS:
        proxy_urls_str = settings.PROXY_URLS.strip()
        if proxy_urls_str:
            # Split by comma and clean up
            proxy_urls = [url.strip() for url in proxy_urls_str.split(",") if url.strip()]
    
    # Fall back to legacy PROXY_URL setting
    if not proxy_urls and hasattr(settings, "PROXY_URL") and settings.PROXY_URL:
        proxy_url = settings.PROXY_URL.strip()
        if proxy_url:
            proxy_urls = [proxy_url]
    
    return proxy_urls


def _build_proxy_auth_extension(proxy_url: str) -> str:
    """Create a minimal Chrome extension that configures a fixed proxy and handles auth.

    Returns path to the zipped extension that can be loaded with --load-extension.
    """
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        raise ValueError("Invalid PROXY_URL; must include host and port")
    username = parsed.username or ""
    password = parsed.password or ""
    host = parsed.hostname
    port = int(parsed.port)

    manifest = {
        "version": "1.0",
        "manifest_version": 2,
        "name": "ProxyAuth",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestBlocking"
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "88.0"
    }

    background_js = f"""
    const config = {{
      mode: "fixed_servers",
      rules: {{
        singleProxy: {{ scheme: "http", host: "{host}", port: {port} }},
        bypassList: ["localhost", "127.0.0.1"]
      }}
    }};
    chrome.proxy.settings.set({{ value: config, scope: "regular" }}, function() {{}});

    function callbackFn(details) {{
      return {{ authCredentials: {{ username: "{username}", password: "{password}" }} }};
    }}
    chrome.webRequest.onAuthRequired.addListener(
      callbackFn,
      {{ urls: ["<all_urls>"] }},
      ["blocking"]
    );
    """

    temp_dir = tempfile.mkdtemp(prefix="proxy_ext_")
    manifest_path = os.path.join(temp_dir, "manifest.json")
    bg_path = os.path.join(temp_dir, "background.js")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    with open(bg_path, "w", encoding="utf-8") as f:
        f.write(background_js)

    zip_path = os.path.join(temp_dir, "proxy_auth_extension.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(manifest_path, arcname="manifest.json")
        zf.write(bg_path, arcname="background.js")
    return zip_path


def get_driver(force_new: bool = False):
    """
    Initialize and return an undetected Chrome WebDriver instance.
    
    Args:
        force_new: Force creation of a new driver (useful for proxy rotation)
    """
    global _driver, _driver_created_at
    
    # Check if we need to rotate proxy
    try:
        proxy_urls = _get_proxy_urls()
        if proxy_urls and len(proxy_urls) > 1:
            proxy_manager = get_proxy_manager(proxy_urls, settings.PROXY_ROTATION_INTERVAL)
            if proxy_manager.should_rotate():
                print("⏰ Proxy rotation interval reached, creating new driver with next proxy...")
                force_new = True
                proxy_manager.rotate_proxy()
    except Exception as e:
        print(f"⚠️  Error checking proxy rotation: {e}")
    
    # Reset if driver is stale or window is closed, or if forced
    try:
        if _driver and not force_new:
            # Check if window is still available
            try:
                _driver.current_url  # This will fail if window is closed
                # Also check if process is still alive
                if _driver.service.process:
                    _driver.service.process.poll()
                    if _driver.service.process.returncode is not None:
                        _driver = None
            except Exception:
                # Window closed or driver dead, reset it
                try:
                    _driver.quit()
                except:
                    pass
                _driver = None
        elif force_new and _driver:
            # Close existing driver to create new one
            try:
                _driver.quit()
            except:
                pass
            _driver = None
    except:
        _driver = None
    if _driver is None:
        # Undetected ChromeDriver automatically handles anti-bot detection
        options = uc.ChromeOptions()
        
        # Add necessary arguments
        # NOTE: Headless mode is detected by Cloudflare - comment out to use visible browser
        # options.add_argument("--headless=new")  # New headless mode (less detectable)
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"user-agent={settings.USER_AGENT}")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--allow-insecure-localhost")
        options.add_argument("--test-type")
        try:
            options.set_capability('acceptInsecureCerts', True)
        except Exception:
            pass
        # Accept-Language and locale hints
        if getattr(settings, "ACCEPT_LANGUAGE", None):
            lang_hint = settings.ACCEPT_LANGUAGE.split(",")[0]
            if lang_hint:
                options.add_argument(f"--lang={lang_hint}")
        # Optional proxy support via extension to avoid MITM fingerprints
        seleniumwire_options = None
        proxy_url = None
        
        # Get proxy URLs and initialize proxy manager
        proxy_urls = _get_proxy_urls()
        if proxy_urls:
            try:
                # Initialize proxy manager with all available proxies
                proxy_manager = get_proxy_manager(proxy_urls, settings.PROXY_ROTATION_INTERVAL)
                
                # Get the current proxy to use
                proxy_raw = proxy_manager.get_current_proxy()
                print(f"🔄 Using proxy: {proxy_manager._mask_proxy(proxy_raw)}")
                
                if proxy_raw:
                    parsed = urlparse(proxy_raw)
                    host_port = f"{parsed.hostname}:{parsed.port}" if parsed.hostname and parsed.port else parsed.netloc
                    if host_port:
                        # Use Chrome extension for proxy auth; no MITM
                        ext_zip = _build_proxy_auth_extension(proxy_raw)
                        options.add_argument(f"--load-extension={os.path.dirname(ext_zip)}")
                        options.add_argument(f"--disable-extensions-except={os.path.dirname(ext_zip)}")
                        # Important: do not set --proxy-server when using extension proxy settings
                        proxy_url = proxy_raw
            except Exception as e:
                print(f"⚠️  Error setting up proxy: {e}")
                # Fall back to first proxy without manager
                if proxy_urls:
                    try:
                        options.add_argument(f"--proxy-server={proxy_urls[0]}")
                    except:
                        pass
        
        # Initialize undetected chromedriver without selenium-wire to avoid MITM
        chrome_path = get_chrome_executable_path()
        _driver = uc.Chrome(
            options=options,
            driver_executable_path=None,
            browser_executable_path=chrome_path,
            use_subprocess=True,
            version_main=141
        )
        
        # Additional stealth measures - hide webdriver property
        _driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        _driver.execute_cdp_cmd('Network.setUserAgentOverride', {
            "userAgent": settings.USER_AGENT,
            "acceptLanguage": getattr(settings, "ACCEPT_LANGUAGE", "en-US,en;q=0.9")
        })
        # Extra headers for realism
        try:
            extra_headers = {"Accept-Language": getattr(settings, "ACCEPT_LANGUAGE", "en-US,en;q=0.9")}
            _driver.execute_cdp_cmd('Network.setExtraHTTPHeaders', {"headers": extra_headers})
        except Exception:
            pass

        # selenium-stealth to reduce detectability
        try:
            lang_list = [l.strip() for l in getattr(settings, "ACCEPT_LANGUAGE", "en-US,en;q=0.9").split(',') if l]
            primary_lang = lang_list[0] if lang_list else "en-US"
            stealth(
                _driver,
                languages=[primary_lang, "en"],
                vendor="Google Inc.",
                platform="MacIntel",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True,
            )
        except Exception:
            pass
        
        # Random viewport  size to appear more human
        viewport_width = random.randint(1366, 1920)
        viewport_height = random.randint(768, 1080)
        _driver.set_window_size(viewport_width, viewport_height)
        
        # Track when driver was created
        _driver_created_at = time.time()
        print(f"✓ WebDriver initialized successfully")
    
    return _driver


async def scrape_indeed_selenium(
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
    Enhanced Indeed scraper using Selenium with comprehensive data extraction and advanced filtering.
    
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
    async with _request_lock:
        now = time.monotonic()
        jitter = random.uniform(0, 0.75)
        wait = settings.MIN_DELAY + jitter - (now - _last_fetch)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_fetch = time.monotonic()
    
    # Run the scraping in a thread pool to avoid blocking
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _scrape_sync_enhanced, query, location, max_results, job_type, salary_min, salary_max, experience_level, employment_type, days_old)


def _scrape_sync_enhanced(
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
    """Enhanced synchronous scraping function with comprehensive data extraction and advanced filtering."""
    try:
        driver = get_driver()
    except Exception as e:
        raise Exception(f"Failed to initialize browser: {str(e)}")
    
    jobs = []
    all_jobs_before_filter = []  # Track jobs before filtering
    seen_job_ids = set()  # Track job IDs to prevent duplicates
    page = 0
    max_pages = 15  # Indeed typically shows 15 jobs per page, allow more pages for better coverage
    
    try:
        while len(jobs) < max_results and page < max_pages:
            # Build Indeed search URL with pagination and filters
            base_url = "https://www.indeed.com/jobs"
            params = f"?q={query.replace(' ', '+')}"
            if location:
                location_param = _format_location_for_indeed(location)
                params += f"&l={location_param}"
                print(f"DEBUG - Location '{location}' formatted as '{location_param}'")
            
            # Add employment type filter using Indeed's URL parameters
            if employment_type:
                employment_filter = _get_indeed_employment_filter(employment_type)
                if employment_filter:
                    params += f"&{employment_filter}"
                    print(f"DEBUG - Employment type '{employment_type}' mapped to filter '{employment_filter}'")
            
            # Add date posted filter using Indeed's URL parameters
            if days_old:
                date_filter = _get_indeed_date_filter(days_old)
                if date_filter:
                    params += f"&fromage={date_filter}"
                    print(f"DEBUG - Date filter '{days_old} days' mapped to 'fromage={date_filter}'")
            
            # Add experience level filter using Indeed's URL parameters
            if experience_level:
                experience_filter = _get_indeed_experience_filter(experience_level)
                if experience_filter:
                    params += f"&sc={experience_filter}"
                    print(f"DEBUG - Experience level '{experience_level}' mapped to 'sc={experience_filter}'")
            
            if page > 0:
                params += f"&start={page * 15}"  # Indeed uses 15 jobs per page
            
            url = base_url + params
            print(f"Navigating to page {page + 1}: {url}")
            print(f"DEBUG - Full URL with filters: {url}")
            
            # Navigate to the page with soft-retries and backoff when Cloudflare is detected
            retries = 0
            while True:
                driver.get(url)
                page_html = driver.page_source or ""
                
                # More precise Cloudflare detection - only trigger if we see actual block indicators
                # AND we don't see legitimate Indeed job content
                has_cloudflare_indicators = (
                    "Checking your browser" in page_html  # Cloudflare's actual text
                    or "Enable JavaScript and cookies to continue" in page_html  # Cloudflare block message
                    or ("challenge-platform" in page_html and "<title>Just a moment" in page_html)  # Cloudflare interstitial
                )
                has_indeed_content = (
                    'id="mosaic-provider-jobcards"' in page_html  # Indeed's job cards container
                    or 'class="jobsearch-ResultsList"' in page_html  # Indeed's results list
                    or 'data-jk=' in page_html  # Indeed job key attribute
                )
                
                is_actually_blocked = has_cloudflare_indicators and not has_indeed_content
                
                if is_actually_blocked:
                    if retries >= getattr(settings, "MAX_RETRIES", 1):
                        try:
                            with open('/tmp/indeed_debug.html', 'w', encoding='utf-8') as f:
                                f.write(page_html)
                        except Exception:
                            pass
                        raise CloudflareBlockedError("Indeed blocked by Cloudflare (captcha/turnstile). See /tmp/indeed_debug.html")
                    backoff = random.uniform(getattr(settings, "BACKOFF_MIN", 2.0), getattr(settings, "BACKOFF_MAX", 8.0)) * (1 + 0.5 * retries)
                    print(f"Cloudflare detected, retry {retries + 1}/{getattr(settings, 'MAX_RETRIES', 3)}, waiting {backoff:.1f}s...")
                    try:
                        if getattr(settings, "HUMANIZE", True):
                            _perform_human_interactions(driver)
                        time.sleep(random.uniform(0.8, 1.6))
                        driver.delete_all_cookies()
                        # Add more realistic wait
                        time.sleep(random.uniform(3.0, 6.0))
                    except Exception:
                        pass
                    # Close and recreate browser
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    global _driver
                    _driver = None  # Force new driver creation
                    driver = get_driver()
                    print(f"Browser restarted, waiting {backoff:.1f}s before retry...")
                    time.sleep(backoff)
                    retries += 1
                    continue
                break

            # Verify we're on the correct page by checking the URL
            current_url = driver.current_url
            if page > 0 and 'start=' not in current_url:
                print(f"WARNING - Expected start parameter in URL but not found. Current URL: {current_url}")
            elif page > 0:
                expected_start = page * 15
                if f'start={expected_start}' not in current_url:
                    print(f"WARNING - Expected start={expected_start} but URL shows: {current_url}")
            
            # Random delay to appear more human-like
            time.sleep(random.uniform(getattr(settings, "PAGE_DELAY_MIN", 2.0), getattr(settings, "PAGE_DELAY_MAX", 5.8)))
            if getattr(settings, "HUMANIZE", True):
                _perform_human_interactions(driver)
            
            # Try to wait for job results
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-jk], div.job_seen_beacon, td.resultContent"))
                )
            except:
                pass  # Continue even if wait times out
            else:
                # Additional human-like scrolling to trigger lazy content
                if getattr(settings, "HUMANIZE", True):
                    _progressive_scroll(driver)
            
            # Get page source and parse with BeautifulSoup
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # Try multiple selectors for Indeed's different layouts
            job_cards = _find_job_cards_indeed(soup)
            
            print(f"Found {len(job_cards)} potential job cards on page {page + 1}")
            
            # If no job cards found, we've reached the end of results
            if not job_cards:
                print(f"DEBUG - No job cards found on page {page + 1}, reached end of results")
                break
            
            # Check if we've reached the end of results (additional checks)
            if _is_end_of_results_indeed(soup):
                print(f"Reached end of results on page {page + 1}")
                break
            
            # Check if we're getting the same jobs as previous page (pagination issue)
            current_page_job_ids = set()
            for card in job_cards[:5]:  # Check first 5 jobs
                try:
                    temp_job = _extract_detailed_job_info_indeed(card)
                    if temp_job and temp_job.title:
                        temp_job_id = _create_job_id(temp_job)
                        current_page_job_ids.add(temp_job_id)
                except:
                    continue
            
            # If all jobs on this page are duplicates, we've likely reached the end
            if current_page_job_ids and current_page_job_ids.issubset(seen_job_ids):
                print(f"All jobs on page {page + 1} are duplicates, stopping pagination")
                break
            
            # If we have very few unique jobs on this page, check if we should continue
            if len(current_page_job_ids) <= 2 and page > 0:
                print(f"Only {len(current_page_job_ids)} unique jobs found on page {page + 1}, likely end of results")
                break
            
            
            page_jobs_added = 0
            for i, card in enumerate(job_cards):
                try:
                    # Validate that this card looks like a real job listing
                    if not _is_valid_job_card(card):
                        print(f"DEBUG - Skipping card {i+1} - doesn't appear to be a valid job listing")
                        continue
                    
                    # First try to extract from the card as-is
                    job = _extract_detailed_job_info_indeed(card)
                    
                    # Enhanced extraction: Always fetch complete data from individual job pages
                    if job and job.title and job.url:
                        print(f"Fetching complete data from job page: {job.title}")
                        enhanced_job = _extract_complete_job_details_from_url(driver, job)
                        if enhanced_job:
                            job = enhanced_job
                        # Add a delay to be respectful to Indeed's servers
                        time.sleep(2)
                    
                    if job and job.title:
                        # Create a unique identifier for the job to prevent duplicates
                        job_id = _create_job_id(job)
                        
                        # Skip if we've already seen this job
                        if job_id in seen_job_ids:
                            print(f"⚠️  Duplicate job skipped: {job.title} at {job.company}")
                            continue
                        
                        seen_job_ids.add(job_id)
                        all_jobs_before_filter.append(job)
                        
                        # Debug: Show key extracted fields with more detail
                        print(f"DEBUG - Job: {job.title} | Company: {job.company} | Location: {job.location} | Remote: {job.remote_type}")
                        print(f"DEBUG - Salary: {job.salary_range} | Employment: {job.employment_type} | Experience: {job.experience_level} | Posted: {job.posted_date}")
                        print(f"DEBUG - Description: {job.description[:100] if job.description else 'None'}...")
                        
                        # Apply all filters
                        location_match = _matches_location_filter_indeed(job, location)
                        job_type_match = _matches_job_type_filter_indeed(job, job_type)
                        salary_match = _matches_salary_filter_indeed(job, salary_min, salary_max)
                        experience_match = _matches_experience_filter_indeed(job, experience_level)
                        employment_match = _matches_employment_filter_indeed(job, employment_type)
                        date_match = _matches_date_filter_indeed(job, days_old)
                        
                        # Debug employment filtering
                        if employment_type:
                            print(f"DEBUG - Employment Filter: '{employment_type}' | Job Employment: '{job.employment_type}' | Match: {employment_match}")
                        
                        # If no filter specified, always match
                        if not location:
                            location_match = True
                        if not job_type:
                            job_type_match = True
                        if not salary_min and not salary_max:
                            salary_match = True
                        if not experience_level:
                            experience_match = True
                        if not employment_type:
                            employment_match = True
                        if not days_old:
                            date_match = True
                        
                        if location_match and job_type_match and salary_match and experience_match and employment_match and date_match:
                            jobs.append(job)
                            page_jobs_added += 1
                            print(f"✓ Matched: {job.title} at {job.company or 'Unknown'} in {job.location or 'Unknown'} ({job.remote_type or 'Unknown'})")
                        else:
                            print(f"✗ Filtered: {job.title} (location_match={location_match}, job_type_match={job_type_match}, salary_match={salary_match}, experience_match={experience_match}, employment_match={employment_match}, date_match={date_match}, job_location={job.location}, posted_date={job.posted_date})")
                        
                        # Stop if we've reached the max_results limit
                        if len(jobs) >= max_results:
                            break
                except Exception as e:
                    print(f"Error parsing job card: {e}")
                    continue
            
            # If no jobs were added from this page, we've likely reached the end
            if page_jobs_added == 0:
                print(f"No new jobs found on page {page + 1}, stopping pagination")
                break
            
            # Additional check: if we've seen too many duplicates in a row, stop
            if page > 2:  # After page 3, check for too many duplicates
                recent_duplicates = 0
                for i in range(max(0, len(all_jobs_before_filter) - 20), len(all_jobs_before_filter)):
                    if i < len(all_jobs_before_filter):
                        job = all_jobs_before_filter[i]
                        job_id = _create_job_id(job)
                        if job_id in seen_job_ids:
                            recent_duplicates += 1
                
                if recent_duplicates > 15:  # If more than 15 recent jobs are duplicates
                    print(f"Too many duplicates detected ({recent_duplicates}), stopping pagination")
                    break
                
            print(f"Page {page + 1} complete: {page_jobs_added} jobs added, {len(jobs)} total so far")
            print(f"DEBUG - Total unique jobs seen: {len(seen_job_ids)}")
            print(f"DEBUG - Jobs before filtering: {len(all_jobs_before_filter)}")
            
            # If we've found enough jobs, break early
            if len(jobs) >= max_results:
                print(f"Found {len(jobs)} jobs, reached target of {max_results}")
                break
            
            page += 1
        
        # Apply max_results limit at the end
        jobs = jobs[:max_results]
        
        # Better debugging output
        print(f"\n=== INDEED SCRAPING SUMMARY ===")
        print(f"Total jobs found: {len(all_jobs_before_filter)}")
        print(f"Jobs after filtering: {len(jobs)}")
        print(f"Search criteria: query='{query}', location='{location}', job_type='{job_type}'")
        print(f"Salary range: {salary_min or 'None'} - {salary_max or 'None'}")
        print(f"Experience level: {experience_level or 'None'}")
        print(f"Employment type: {employment_type or 'None'}")
        print(f"Date filter: {days_old or 'None'} days old")
        print(f"Max results requested: {max_results}")
        
        # If no jobs found, save HTML for debugging
        if len(jobs) == 0 and len(all_jobs_before_filter) == 0:
            try:
                debug_file = "/tmp/indeed_debug.html"
                with open(debug_file, 'w', encoding='utf-8') as f:
                    f.write(driver.page_source)
                print(f"Debug HTML saved to {debug_file}")
            except Exception as e:
                print(f"Could not save debug HTML: {e}")
        
        if all_jobs_before_filter and not jobs:
            print("\n⚠️  WARNING: Jobs were found but all were filtered out!")
            print("Sample locations from found jobs:")
            for job in all_jobs_before_filter[:5]:
                print(f"  - {job.location} (remote_type: {job.remote_type})")
        
        if not all_jobs_before_filter:
            print(f"\n⚠️  No jobs found on Indeed for this search.")
            # Save debug info
            with open('/tmp/indeed_debug.html', 'w', encoding='utf-8') as f:
                f.write(driver.page_source)
            print("Debug HTML saved to /tmp/indeed_debug.html")
        
    except CloudflareBlockedError:
        # Mark proxy as failed when Cloudflare blocks
        try:
            proxy_urls = _get_proxy_urls()
            if proxy_urls and len(proxy_urls) > 1:
                proxy_manager = get_proxy_manager(proxy_urls, settings.PROXY_ROTATION_INTERVAL)
                proxy_manager.mark_proxy_failure()
                print("⚠️  Cloudflare challenge detected - proxy marked as failed")
        except Exception as e:
            print(f"⚠️  Error marking proxy failure: {e}")
        # Let callers handle the specific block to enable fallback
        raise
    except Exception as e:
        raise Exception(f"Failed to scrape Indeed: {str(e)}")
    
    # Mark proxy as successful if scraping completed
    try:
        proxy_urls = _get_proxy_urls()
        if proxy_urls and len(proxy_urls) > 1:
            proxy_manager = get_proxy_manager(proxy_urls, settings.PROXY_ROTATION_INTERVAL)
            proxy_manager.mark_proxy_success()
    except Exception as e:
        print(f"⚠️  Error marking proxy success: {e}")
    
    return jobs


def _perform_human_interactions(driver):
    """Simulate minor human interactions to reduce bot detection."""
    try:
        actions = ActionChains(driver)
        # Small random mouse moves
        for _ in range(random.randint(2, 4)):
            x_off = random.randint(-30, 30)
            y_off = random.randint(-20, 20)
            actions.move_by_offset(x_off, y_off)
        actions.pause(random.uniform(0.2, 0.6)).perform()
        # Occasional scroll jiggles
        if random.random() < 0.8:
            js = "window.scrollBy(0, arguments[0]);"
            driver.execute_script(js, random.randint(120, 380))
            time.sleep(random.uniform(0.15, 0.4))
            if random.random() < 0.5:
                driver.execute_script(js, -random.randint(40, 120))
    except Exception:
        pass


def _progressive_scroll(driver):
    """Gradually scroll the page to trigger lazy-loaded elements."""
    try:
        total = driver.execute_script("return document.body.scrollHeight") or 2000
        steps = random.randint(4, 7)
        for i in range(steps):
            frac = (i + 1) / steps
            y = int(total * frac)
            driver.execute_script("window.scrollTo(0, arguments[0]);", y)
            time.sleep(random.uniform(0.25, 0.7))
    except Exception:
        pass

def _is_valid_job_card(card) -> bool:
    """
    Validate that a card element actually contains a job listing.
    This helps filter out false positives from overly broad selectors.
    """
    if not card:
        return False
    
    # Check for Indeed's job ID attribute (most reliable indicator)
    if card.get('data-jk'):
        return True
    
    # Check for job title link (common in job listings)
    title_link = card.select_one('a[class*="jcs-JobTitle"], a[class*="jobTitle"], h2 a, h3 a')
    if title_link:
        return True
    
    # Check for company name (job listings usually have company info)
    company_elements = card.select('span[class*="company"], div[class*="company"], a[class*="company"]')
    if company_elements:
        return True
    
    # Check for location info (job listings usually have location)
    location_elements = card.select('div[class*="location"], span[class*="location"], div[class*="job-location"]')
    if location_elements:
        return True
    
    # Check if the card has job-related classes
    card_classes = ' '.join(card.get('class', []))
    job_indicators = ['job', 'result', 'listing', 'card']
    if any(indicator in card_classes.lower() for indicator in job_indicators):
        return True
    
    return False


def _find_job_cards_indeed(soup: BeautifulSoup) -> List:
    """Find job cards using multiple Indeed-specific selectors."""
    job_cards = []
    
    # Try Indeed's specific job card selectors in order of reliability
    # Indeed typically shows 15 jobs per page, so we should find around that many
    primary_selectors = [
        'div[data-jk]',  # Most reliable - Indeed's job ID attribute
        'div.job_seen_beacon',  # Alternative Indeed layout
        'td.resultContent',  # Table layout
    ]
    
    # Try primary selectors first
    for selector in primary_selectors:
        cards = soup.select(selector)
        if cards:
            print(f"Found {len(cards)} job cards using primary selector: {selector}")
            job_cards.extend(cards)
            # If we found cards with primary selectors, use those
            if len(cards) >= 10:  # Reasonable number for Indeed
                break
    
    # Only try broader selectors if we didn't find enough with primary ones
    if len(job_cards) < 5:
        print("DEBUG - Not enough cards found with primary selectors, trying broader ones...")
        fallback_selectors = [
            'div[class*="jobsearch-SerpJobCard"]',  # Indeed's specific job card class
            'div[class*="jobsearch-"]',  # Any Indeed jobsearch class
            'div[data-testid*="job"]',  # Data testid with job
        ]
        
        for selector in fallback_selectors:
            cards = soup.select(selector)
            if cards:
                print(f"Found {len(cards)} job cards using fallback selector: {selector}")
                job_cards.extend(cards)
                # Stop after finding a reasonable number
                if len(job_cards) >= 15:
                    break
    
    # If still no cards, try finding by job title links
    if not job_cards:
        print("DEBUG - No job cards found with standard selectors, trying job title links...")
        title_links = soup.find_all('a', class_=lambda x: x and 'jcs-JobTitle' in x)
        print(f"DEBUG - Found {len(title_links)} job title links")
        job_cards = [link.find_parent('div') for link in title_links if link.find_parent('div')]
        job_cards = [card for card in job_cards if card]
        print(f"DEBUG - Found {len(job_cards)} job cards from title links")
    
    # Remove duplicates from job cards (same element might be found by multiple selectors)
    unique_cards = []
    seen_cards = set()
    for card in job_cards:
        card_id = id(card)  # Use memory address as unique identifier
        if card_id not in seen_cards:
            seen_cards.add(card_id)
            unique_cards.append(card)
    
    # Validate that we found a reasonable number of job cards
    # Indeed typically shows 15 jobs per page, so we should find around that many
    if len(unique_cards) > 30:
        print(f"WARNING - Found {len(unique_cards)} job cards, which seems too many for one page")
        print("DEBUG - This might indicate false positives in job card detection")
        # Limit to first 20 cards to avoid processing too many false positives
        unique_cards = unique_cards[:20]
        print(f"DEBUG - Limited to first 20 cards for processing")
    elif len(unique_cards) > 15:
        print(f"DEBUG - Found {len(unique_cards)} job cards (more than expected 15, but continuing)")
    else:
        print(f"DEBUG - Found {len(unique_cards)} job cards (reasonable number)")
    
    return unique_cards


def _extract_detailed_job_info_indeed(card) -> Optional[Job]:
    """Extract detailed job information from an Indeed job card with enhanced extraction."""
    try:
        # Extract job title
        title = _extract_title_indeed(card)
        if not title or len(title) < 3:
            return None
        
        # Extract company information
        company, company_url = _extract_company_info_indeed(card)
        
        # Extract location and remote type
        location, remote_type = _extract_location_info_indeed(card)
        
        # Extract salary range
        salary_range = _extract_salary_indeed(card)
        
        # Extract job type and employment type
        job_type, employment_type = _extract_job_types_indeed(card)
        
        # Extract experience level
        experience_level = _extract_experience_level_indeed(card)
        
        # Extract posted date
        posted_date = _extract_posted_date_indeed(card)
        
        # Extract job description
        description = _extract_description_indeed(card)
        
        # Extract job URL
        job_url = _extract_job_url_indeed(card)
        
        # Extract job ID
        job_id = _extract_job_id_indeed(card)
        
        # Extract skills and requirements
        skills = _extract_skills_indeed(card)
        requirements = _extract_requirements_indeed(card)
        benefits = _extract_benefits_indeed(card)
        
        # Extract industry and company size
        industry = _extract_industry_indeed(card)
        company_size = _extract_company_size_indeed(card)
        
        # Enhanced extraction: Try to get more details from the full card text
        full_text = card.get_text()
        
        # If description is still None, try to extract from full text
        if not description:
            description = _extract_description_from_full_text(full_text)
        
        # If employment_type is still None, try to extract from full text
        if not employment_type:
            employment_type = _extract_employment_from_full_text(full_text)
        
        # If experience_level is still None, try to extract from full text
        if not experience_level:
            experience_level = _extract_experience_from_full_text(full_text)
        
        # If posted_date is still None, try to extract from full text
        if not posted_date:
            posted_date = _extract_date_from_full_text(full_text)
        
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


def _extract_title_indeed(card) -> Optional[str]:
    """Extract job title from Indeed job card."""
    title_selectors = [
        'h2.jobTitle a',
        'h2.jobTitle',
        'a[data-jk]',
        'span[title]',
        'h2[class*="jobTitle"]',
        'a[class*="jcs-JobTitle"]'
    ]
    
    for selector in title_selectors:
        title_elem = card.select_one(selector)
        if title_elem:
            title = title_elem.get_text(strip=True)
            if title and len(title) > 3:
                # Clean up title (remove "new" badges etc)
                title = re.sub(r'\b(new|urgent|hiring)\b', '', title, flags=re.IGNORECASE).strip()
                return title
    
    return None


def _extract_company_info_indeed(card) -> tuple[Optional[str], Optional[str]]:
    """Extract company name and URL from Indeed job card with enhanced selectors."""
    company = None
    company_url = None
    
    # Enhanced company selectors for better extraction
    company_selectors = [
        'span[data-testid="company-name"]',
        'span.companyName',
        'span[class*="company"]',
        'a[data-testid="company-name"]',
        'div[class*="companyName"]',
        'span[class*="companyName"]',
        'a[class*="companyName"]',
        'a[class*="company"]',
        'div[class*="company"]',
        # Indeed's specific selectors
        'a[class*="jcs-CompanyLink"]',
        'span[class*="jcs-CompanyLink"]',
        'div[class*="jcs-CompanyLink"]',
        'a[class*="companyLink"]',
        'span[class*="companyLink"]',
        'div[class*="companyLink"]'
    ]
    
    for selector in company_selectors:
        company_elem = card.select_one(selector)
        if company_elem:
            company = company_elem.get_text(strip=True)
            if company and len(company) > 1:
                # Check if it's a link or contains a link
                if company_elem.name == 'a':
                    href = company_elem.get('href', '')
                    if href:
                        company_url = f"https://www.indeed.com{href}" if href.startswith('/') else href
                        print(f"  ✓ Company URL found (direct link): {company_url}")
                else:
                    # Look for nested link
                    link_elem = company_elem.find('a')
                    if link_elem:
                        href = link_elem.get('href', '')
                        if href:
                            company_url = f"https://www.indeed.com{href}" if href.startswith('/') else href
                            print(f"  ✓ Company URL found (nested link): {company_url}")
                break
    
    # Fallback: Look for any link that might be a company URL
    if not company_url:
        all_links = card.find_all('a', href=True)
        for link in all_links:
            href = link.get('href')
            if href and ('company' in href.lower() or 'employer' in href.lower() or 'indeed.com/company' in href):
                company_url = href
                if not company_url.startswith('http'):
                    company_url = 'https://www.indeed.com' + company_url
                print(f"  ✓ Company URL found (fallback): {company_url}")
                break
    
    return company, company_url


def _extract_location_info_indeed(card) -> tuple[Optional[str], Optional[str]]:
    """Extract location and remote type from Indeed job card."""
    location = None
    remote_type = None
    
    # Try multiple location selectors
    location_selectors = [
        'div[data-testid="text-location"]',
        'div.companyLocation',
        'div[class*="location"]',
        'span[class*="location"]'
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


def _extract_salary_indeed(card) -> Optional[str]:
    # Extract salary range from Indeed job card with comprehensive patterns targeting main page elements
    # First, try to extract salary near apply button (most reliable on main page)
    salary_near_apply = _extract_salary_near_apply_button(card)
    if salary_near_apply:
        print(f"  ✓ Salary found near apply button: {salary_near_apply}")
        return salary_near_apply

    # Second, try to extract from job details section on main page
    salary_from_details = _extract_salary_from_job_details_main_page(card)
    if salary_from_details:
        print(f"  ✓ Salary found in job details: {salary_from_details}")
        return salary_from_details
    
    # Then, look for salary in specific elements that appear above the apply button
    salary_selectors = [
        # Indeed's specific selectors for salary on main page
        'div[data-testid="attribute_snippet_test_salary"]',
        'span[data-testid="attribute_snippet_test_salary"]',
        'div[class*="attribute_snippet"]',
        'span[class*="attribute_snippet"]',
        'div[class*="salary"]',
        'span[class*="salary"]',
        'div[class*="pay"]',
        'span[class*="pay"]',
        'div[class*="compensation"]',
        'span[class*="compensation"]',
        'div[class*="metadata"]',
        'span[class*="metadata"]',
        'div[class*="job-snippet"]',
        'span[class*="job-snippet"]',
        # Indeed's specific selectors for job details
        'div[class*="jobsearch-JobDescriptionSection"] span',
        'div[class*="jobsearch-JobDescriptionSection"] div',
        'span[class*="icl-u-xs-mr--xs"]',
        'div[class*="icl-u-xs-mr--xs"]',
        # Look for elements near apply button
        'div[class*="result-snippet"]',
        'span[class*="result-snippet"]',
        'div[class*="job-summary"]',
        'span[class*="job-summary"]'
    ]
    
    for selector in salary_selectors:
        salary_elem = card.select_one(selector)
        if salary_elem:
            salary_text = salary_elem.get_text(strip=True)
            if '$' in salary_text or 'salary' in salary_text.lower() or 'pay' in salary_text.lower():
                salary_text = re.sub(r'\s+', ' ', salary_text).strip()
                if len(salary_text) > 5:
                    return salary_text
    
    # Get all text content for pattern matching
    text_content = card.get_text()
    
    # Enhanced salary patterns with more comprehensive matching
    salary_patterns = [
        # Standard salary ranges with currency
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?\s*(?:a\s+year|per\s+year|annually|yearly)',
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        r'\$[\d,]+(?:K|k)?\s*/\s*(?:year|yr|hour|hr)',
        r'\$[\d,]+(?:K|k)?\s*per\s*(?:year|yr|hour|hr)',
        r'\$[\d,]+(?:K|k)?\s*annually',
        r'\$[\d,]+(?:K|k)?\s*hourly',
        
        # Salary ranges without currency symbol
        r'[\d,]+(?:K|k)\s*-\s*[\d,]+(?:K|k)\s*(?:a\s+year|per\s+year|annually|yearly)',
        r'[\d,]+(?:K|k)\s*-\s*[\d,]+(?:K|k)',
        r'[\d,]+(?:K|k)\s*to\s*[\d,]+(?:K|k)',
        
        # Salary with labels
        r'Salary:\s*\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        r'Pay:\s*\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        r'Compensation:\s*\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        r'Rate:\s*\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        
        # Single salary amounts
        r'Up\s+to\s+\$[\d,]+(?:K|k)?',
        r'Starting\s+at\s+\$[\d,]+(?:K|k)?',
        r'From\s+\$[\d,]+(?:K|k)?',
        r'Base\s+salary\s*\$[\d,]+(?:K|k)?',
        
        # Hourly rates
        r'\$[\d,]+(?:\.\d+)?\s*/\s*hour',
        r'\$[\d,]+(?:\.\d+)?\s*per\s*hour',
        r'\$[\d,]+(?:\.\d+)?\s*hourly',
        
        # Annual ranges with different formats
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?\s*(?:per\s*)?(?:year|yr)',
        r'[\d,]+(?:K|k)\s*-\s*[\d,]+(?:K|k)\s*(?:per\s*)?(?:year|yr)',
        
        # Salary with benefits
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?\s*plus\s+benefits',
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?\s*with\s+benefits'
    ]
    
    for pattern in salary_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            salary_text = match.group().strip()
            # Clean up the salary text
            salary_text = re.sub(r'\s+', ' ', salary_text).strip()
            if len(salary_text) > 5:  # Ensure it's meaningful
                return salary_text
    
    return None


def _extract_salary_near_apply_button(card) -> Optional[str]:
    """Extract salary information that appears near the apply button on main page."""
    # Look for salary in elements that typically appear near apply buttons
    apply_button_selectors = [
        'a[class*="apply"]',
        'button[class*="apply"]',
        'div[class*="apply"]',
        'span[class*="apply"]',
        'a[data-testid*="apply"]',
        'button[data-testid*="apply"]'
    ]
    
    for apply_selector in apply_button_selectors:
        apply_elem = card.select_one(apply_selector)
        if apply_elem:
            # Look for salary in the same container or nearby elements
            parent = apply_elem.parent
            if parent:
                # Look for salary in the parent container
                salary_elem = parent.find(['span', 'div'], class_=re.compile(r'salary|pay|compensation|attribute'))
                if salary_elem:
                    salary_text = salary_elem.get_text(strip=True)
                    if '$' in salary_text and len(salary_text) > 5:
                        return salary_text
                
                # Look for salary in siblings
                for sibling in parent.find_all(['span', 'div']):
                    sibling_text = sibling.get_text(strip=True)
                    if '$' in sibling_text and any(keyword in sibling_text.lower() for keyword in ['year', 'hour', 'salary', 'pay']):
                        if len(sibling_text) > 5:
                            return sibling_text
    
    return None


def _extract_salary_from_job_details_main_page(card) -> Optional[str]:
    """Extract salary from job details section that appears on main page."""
    # Look for job details section on main page
    details_selectors = [
        'div[class*="jobDetails"]',
        'div[class*="job-details"]',
        'div[class*="jobsearch-JobDescriptionSection"]',
        'div[class*="result-snippet"]',
        'div[class*="job-snippet"]',
        'div[class*="attribute_snippet"]'
    ]
    
    for selector in details_selectors:
        details_section = card.select_one(selector)
        if details_section:
            # Look for salary in this section
            salary_text = _extract_salary_from_details_section(details_section)
            if salary_text:
                return salary_text
    
    return None


def _extract_salary_from_details_section(details_section) -> Optional[str]:
    """Extract salary from Indeed's job details section."""
    # Look for salary in the details section
    salary_elements = details_section.find_all(['span', 'div'], string=re.compile(r'\$[\d,]+'))
    
    for elem in salary_elements:
        text = elem.get_text(strip=True)
        if '$' in text and any(keyword in text.lower() for keyword in ['year', 'hour', 'salary', 'pay']):
            return text
    
    return None


def _extract_job_types_indeed(card) -> tuple[Optional[str], Optional[str]]:
    """Extract job type and employment type from Indeed job card with enhanced patterns targeting Indeed's UI structure."""
    job_type = None
    employment_type = None
    
    # Look for employment type in Indeed's structured job details section first
    job_details_selectors = [
        'div[data-testid="job-details"]',
        'div[class*="job-details"]',
        'div[class*="jobDetails"]',
        'div[class*="jobDetailsContainer"]',
        'div[class*="jobsearch-JobDescriptionSection"]'
    ]
    
    for selector in job_details_selectors:
        details_section = card.select_one(selector)
        if details_section:
            # Look for employment type within the job details section
            employment_in_details = _extract_employment_from_details_section(details_section)
            if employment_in_details:
                employment_type = employment_in_details
                break
    
    # Look for employment type in specific elements
    employment_selectors = [
        'span[class*="job-snippet"]',
        'div[class*="job-snippet"]',
        'span[class*="attribute_snippet"]',
        'div[class*="attribute_snippet"]',
        'span[class*="metadata"]',
        'div[class*="metadata"]',
        'span[class*="job-type"]',
        'div[class*="job-type"]',
        'span[class*="employment"]',
        'div[class*="employment"]',
        # Indeed's specific selectors
        'div[class*="jobsearch-JobDescriptionSection"] span',
        'div[class*="jobsearch-JobDescriptionSection"] div',
        'span[class*="icl-u-xs-mr--xs"]',
        'div[class*="icl-u-xs-mr--xs"]'
    ]
    
    if not employment_type:
        for selector in employment_selectors:
            elem = card.select_one(selector)
            if elem:
                text = elem.get_text(strip=True).lower()
                if any(term in text for term in ['Full-Time', 'Part-Time', 'Contract', 'Temporary', 'Internship']):
                    employment_type = _extract_employment_from_text(text)
                    if employment_type:
                        break
    
    # Fallback to full text search
    if not employment_type:
        text_content = card.get_text().lower()
        employment_type = _extract_employment_from_text(text_content)
    
    # Job type patterns
    text_content = card.get_text().lower()
    if 'permanent' in text_content:
        job_type = 'Permanent'
    elif 'temporary' in text_content:
        job_type = 'Temporary'
    elif 'contract' in text_content:
        job_type = 'Contract'
    
    return job_type, employment_type


def _extract_employment_from_details_section(details_section) -> Optional[str]:
    """Extract employment type from Indeed's job details section."""
    # Look for employment type in the details section
    text_content = details_section.get_text().lower()
    return _extract_employment_from_text(text_content)


def _extract_employment_from_text(text_content: str) -> Optional[str]:
    """Extract employment type from text content."""
    employment_patterns = {
        'full-time': ['Full-Time', 'full time', 'fulltime', 'permanent', 'regular', 'ft'],
        'part-time': ['Part-Time', 'part time', 'parttime', 'pt'],
        'contract': ['Contract', 'contractor', 'freelance', 'consultant', 'contract work', 'contracting'],
        'internship': ['Internship', 'intern', 'trainee', 'co-op', 'coop', 'student'],
        'temporary': ['Temporary', 'temp', 'seasonal', 'short-term', 'short term']
    }
    
    for emp_type, patterns in employment_patterns.items():
        if any(pattern in text_content for pattern in patterns):
            return emp_type.title()
    
    return None


def _extract_experience_level_indeed(card) -> Optional[str]:
    """Extract experience level from Indeed job card with enhanced patterns."""
    # Since we're using Indeed's URL-based filtering, we should trust Indeed's classification
    # and not override it with our own detection to avoid false positives
    return None


def _extract_posted_date_indeed(card) -> Optional[str]:
    """Extract posted date from Indeed job card with comprehensive patterns."""
    # Get all text content first
    text_content = card.get_text()
    
    # Enhanced date patterns with comprehensive matching
    date_patterns = [
        # Standard time ago formats
        r'\d+\s+(?:days?|hours?|minutes?|mins?)\s+ago',
        r'Posted\s+\d+\s+(?:days?|hours?|minutes?|mins?)\s+ago',
        r'Active\s+\d+\s+(?:days?|hours?|minutes?|mins?)\s+ago',
        r'Updated\s+\d+\s+(?:days?|hours?|minutes?|mins?)\s+ago',
        
        # Specific time references
        r'Just\s+posted',
        r'Just\s+now',
        r'Today',
        r'Yesterday',
        r'Recently\s+posted',
        r'New\s+posting',
        r'Fresh\s+posting',
        
        # Time with specific words
        r'\d+\s+(?:day|hour|minute)s?\s+ago',
        r'Posted\s+\d+\s+(?:day|hour|minute)s?\s+ago',
        r'Active\s+\d+\s+(?:day|hour|minute)s?\s+ago',
        
        # Relative time formats
        r'(\d+)\s+(?:days?|hours?|minutes?|mins?)\s+ago',
        r'Posted\s+(\d+)\s+(?:days?|hours?|minutes?|mins?)\s+ago',
        r'Active\s+(\d+)\s+(?:days?|hours?|minutes?|mins?)\s+ago',
        
        # Time ranges
        r'(\d+)-(\d+)\s+(?:days?|hours?)\s+ago',
        r'Posted\s+(\d+)-(\d+)\s+(?:days?|hours?)\s+ago',
        
        # Specific time periods
        r'Less\s+than\s+\d+\s+(?:days?|hours?)\s+ago',
        r'Within\s+the\s+last\s+\d+\s+(?:days?|hours?)',
        r'In\s+the\s+last\s+\d+\s+(?:days?|hours?)',
        
        # Time with additional context
        r'Posted\s+(\d+)\s+(?:days?|hours?)\s+ago\s+by',
        r'Active\s+(\d+)\s+(?:days?|hours?)\s+ago\s+by',
        r'(\d+)\s+(?:days?|hours?)\s+ago\s+by',
        
        # Time with company info
        r'(\d+)\s+(?:days?|hours?)\s+ago\s+•',
        r'Posted\s+(\d+)\s+(?:days?|hours?)\s+ago\s+•',
        
        # Time with location info
        r'(\d+)\s+(?:days?|hours?)\s+ago\s+in',
        r'Posted\s+(\d+)\s+(?:days?|hours?)\s+ago\s+in'
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            date_text = match.group().strip()
            # Clean up the date text
            date_text = re.sub(r'\s+', ' ', date_text).strip()
            if len(date_text) > 2:  # Ensure it's meaningful
                return date_text
    
    # Look for date in specific elements as fallback
    date_selectors = [
        'span[class*="date"]',
        'div[class*="date"]',
        'span[data-testid="myJobsStateDate"]',
        'div[data-testid="myJobsStateDate"]',
        'span[class*="metadata"]',
        'div[class*="metadata"]',
        'span[class*="attribute_snippet"]',
        'div[class*="attribute_snippet"]',
        'span[class*="job-snippet"]',
        'div[class*="job-snippet"]',
        'span[class*="posted"]',
        'div[class*="posted"]',
        'span[class*="time"]',
        'div[class*="time"]',
        'span[class*="ago"]',
        'div[class*="ago"]'
    ]
    
    for selector in date_selectors:
        date_elem = card.select_one(selector)
        if date_elem:
            date_text = date_elem.get_text(strip=True)
            if date_text and any(keyword in date_text.lower() for keyword in ['ago', 'posted', 'today', 'yesterday', 'active', 'just']):
                date_text = re.sub(r'\s+', ' ', date_text).strip()
                if len(date_text) > 2:
                    return date_text
    
    return None


def _extract_description_indeed(card) -> Optional[str]:
    """Extract job description from Indeed job card with enhanced selectors."""
    # Look for description/snippet elements with comprehensive selectors
    description_selectors = [
        'div.job-snippet',
        'div[data-testid="job-snippet"]',
        'div[class*="snippet"]',
        'div[class*="summary"]',
        'div[class*="description"]',
        'span[class*="job-snippet"]',
        'span[class*="snippet"]',
        'div[class*="job-snippet"]',
        'div[class*="attribute_snippet"]',
        'span[class*="attribute_snippet"]',
        'div[class*="metadata"]',
        'span[class*="metadata"]',
        'div[class*="jobDescription"]',
        'span[class*="jobDescription"]',
        'div[class*="result-snippet"]',
        'span[class*="result-snippet"]',
        'div[class*="job-summary"]',
        'span[class*="job-summary"]',
        'div[class*="job-desc"]',
        'span[class*="job-desc"]',
        'div[class*="preview"]',
        'span[class*="preview"]',
        'div[class*="job-content"]',
        'span[class*="job-content"]'
    ]
    
    for selector in description_selectors:
        desc_elem = card.select_one(selector)
        if desc_elem:
            description = desc_elem.get_text(strip=True)
            if description and len(description) > 15:
                # Clean up description
                description = re.sub(r'\s+', ' ', description)  # Normalize whitespace
                # Remove common prefixes that aren't part of the description
                description = re.sub(r'^(Job|Company|Location|Posted|Salary|Benefits|Requirements):\s*', '', description, flags=re.IGNORECASE)
                # Truncate if too long
                if len(description) > 1000:
                    description = description[:1000] + '...'
                return description
    
    # Enhanced fallback: Look for any meaningful text content
    all_text = card.get_text()
    lines = [line.strip() for line in all_text.split('\n') if line.strip()]
    
    # Look for lines that might be descriptions
    for line in lines:
        if (len(line) > 40 and  # Longer minimum length
            not any(keyword in line.lower() for keyword in [
                'days ago', 'hours ago', 'just posted', 'remote', 'hybrid', 
                'Full-Time', 'Part-Time', 'Contract', 'Temporary', 'Internship',
                'salary', 'benefits', 'requirements', 'qualifications', 'posted',
                'apply', 'company', 'location', 'job', 'title', 'responds within',
                'often responds', 'new', 'urgent', 'hiring', 'immediately'
            ]) and
            not line.isupper() and  # Not all caps (likely titles)
            not re.match(r'^\$[\d,]+', line) and  # Not salary info
            not re.match(r'^\d+\s+(?:days?|hours?)\s+ago', line) and  # Not date info
            not re.match(r'^[A-Z][a-z]+\s+[A-Z][a-z]+$', line) and  # Not company names
            not re.match(r'^[A-Z][a-z]+,\s*[A-Z]{2}\s+\d{5}', line) and  # Not locations
            len(line.split()) > 6):  # Must have multiple words
            # Clean up the line
            line = re.sub(r'\s+', ' ', line).strip()
            if len(line) > 1000:
                line = line[:1000] + '...'
            return line
    
    return None


def _extract_job_url_indeed(card) -> Optional[str]:
    """Extract job URL from Indeed job card."""
    # Look for job title links
    link_selectors = [
        'h2.jobTitle a',
        'a[data-jk]',
        'a[class*="jcs-JobTitle"]',
        'a[href*="/viewjob"]',
        'a[href*="/rc/clk"]'
    ]
    
    for selector in link_selectors:
        link_elem = card.select_one(selector)
        if link_elem:
            href = link_elem.get('href', '')
            if href:
                if href.startswith('/'):
                    return f"https://www.indeed.com{href}"
                elif href.startswith('http'):
                    return href
    
    return None


def _extract_job_id_indeed(card) -> Optional[str]:
    """Extract job ID from Indeed job card."""
    # Look for data-jk attribute
    job_id_elem = card.find(attrs={'data-jk': True})
    if job_id_elem:
        return job_id_elem.get('data-jk')
    
    # Try to extract from URL
    link_elem = card.find('a', href=True)
    if link_elem:
        href = link_elem.get('href', '')
        # Extract job ID from Indeed URL patterns
        id_match = re.search(r'jk=([^&]+)', href)
        if id_match:
            return id_match.group(1)
    
    return None


def _extract_skills_indeed(card) -> List[str]:
    """Extract skills from Indeed job card by analyzing the actual job description."""
    skills = []
    text_content = card.get_text().lower()
    
    # Common tech skills with more comprehensive list
    tech_skills = [
        'python', 'javascript', 'java', 'typescript', 'react', 'angular', 'vue', 'node.js',
        'django', 'flask', 'spring', 'express', 'mongodb', 'postgresql', 'mysql', 'redis',
        'docker', 'kubernetes', 'aws', 'azure', 'gcp', 'git', 'jenkins', 'ci/cd', 
        'rest api', 'graphql', 'microservices', 'php', 'ruby', 'go', 'rust', 'c++', 'c#',
        'swift', 'kotlin', 'scala', 'r', 'sql', 'elasticsearch', 'linux', 'unix',
        'html', 'css', 'sass', 'less', 'webpack', 'babel', 'npm', 'yarn', 'maven', 'gradle',
        'machine learning', 'ai', 'artificial intelligence', 'data science', 'analytics',
        'blockchain', 'cryptocurrency', 'devops', 'agile', 'scrum', 'kanban'
    ]
    
    # First, try to extract skills from the job description/snippet
    description = _extract_description_indeed(card)
    if description:
        desc_lower = description.lower()
        
        # Extract skills that appear in the description
        for skill in tech_skills:
            if skill in desc_lower and skill not in skills:
                skills.append(skill.title())
    
    # If no skills found in description, try the full card text as fallback
    if not skills:
        for skill in tech_skills:
            if skill in text_content and skill not in skills:
                skills.append(skill.title())
    
    return skills[:10]  # Limit to 10 skills


def _extract_requirements_indeed(card) -> List[str]:
    """Extract job requirements from Indeed job card."""
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


def _extract_benefits_indeed(card) -> List[str]:
    """Extract job benefits from Indeed job card."""
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


def _extract_industry_indeed(card) -> Optional[str]:
    """Extract industry from Indeed job card."""
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


def _extract_company_size_indeed(card) -> Optional[str]:
    """Extract company size from Indeed job card."""
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


def _is_end_of_results_indeed(soup: BeautifulSoup) -> bool:
    """Check if we've reached the end of available job results on Indeed."""
    # Look for common "no more results" indicators
    end_indicators = [
        "no more results",
        "no results found",
        "end of results",
        "that's all the jobs",
        "no jobs match",
        "try different search",
        "no jobs found",
        "we couldn't find any jobs",
        "0 jobs found"
    ]
    
    page_text = soup.get_text().lower()
    
    # Check for text indicators
    if any(indicator in page_text for indicator in end_indicators):
        print(f"DEBUG - Found end indicator in text: {[ind for ind in end_indicators if ind in page_text]}")
        return True
    
    # Check for specific Indeed elements that indicate end of results
    # Be more specific with selectors to avoid false positives
    no_results_selectors = [
        'div[class*="no-results"]:not([class*="job"])',  # Exclude job-related elements
        'div[class*="empty-state"]',
        'div[class*="no-jobs"]',
        'div[class*="zero-results"]',
        'div[class*="no-matches"]'
    ]
    
    no_results_elements = []
    for selector in no_results_selectors:
        elements = soup.select(selector)
        if elements:
            # Check if the element actually contains no-results text
            for elem in elements:
                text = elem.get_text().lower().strip()
                if any(indicator in text for indicator in ['no results', 'no jobs found', '0 jobs', 'no matches']):
                    no_results_elements.append(elem)
    
    if no_results_elements:
        print(f"DEBUG - Found actual no-results elements: {len(no_results_elements)}")
        for elem in no_results_elements[:2]:  # Show first 2
            print(f"DEBUG - No-results text: {elem.get_text().strip()[:100]}...")
        return True
    
    # Check if there are very few job cards (less than 3) which might indicate end
    job_cards = _find_job_cards_indeed(soup)
    print(f"DEBUG - Found {len(job_cards)} job cards on page")
    
    # Only consider it end of results if we have 0 job cards
    if len(job_cards) == 0:
        print("DEBUG - No job cards found, considering end of results")
        return True
    
    # If we found job cards, it's definitely not end of results
    if len(job_cards) > 0:
        print(f"DEBUG - Found {len(job_cards)} job cards, not end of results")
        return False
    
    return False


def _matches_location_filter_indeed(job: Job, location_filter: Optional[str]) -> bool:
    """Check if job matches the location filter for Indeed with comprehensive location support like ZipRecruiter.
    
    Since we're using Indeed's URL-based location filtering, be very permissive here.
    Indeed has already filtered the results, so we only filter out obvious mismatches.
    """
    if not location_filter:
        return True
    
    # Since Indeed's URL filtering has already done the work, be very permissive
    # Only filter out jobs if they're obviously wrong
    # If no job location is available, accept it (Indeed returned it for a reason)
    if not job.location:
        return True
    
    location_filter = location_filter.lower().strip()
    job_location = job.location.lower()
    
    # For remote searches, be flexible - remote jobs are remote anywhere
    if location_filter in ['remote', 'work from home', 'wfh']:
        # Accept jobs that are remote or have remote indicators
        if job.remote_type and 'remote' in job.remote_type.lower():
            return True
        # Also accept if location contains remote
        if 'remote' in job_location:
            return True
        # Be permissive - if Indeed returned it for a remote search, trust it
        return True
    
    # For non-remote searches, always allow remote jobs (they can be done from anywhere)
    if job.remote_type and job.remote_type.lower() == 'remote':
        return True
    
    # Since Indeed has already filtered by location in the URL, trust its filtering
    # We'll just do a very permissive check here to catch any obvious issues
    
    # Be very permissive - if the location filter appears anywhere in the job location, it's a match
    # This handles partial matches, variations in formatting, etc.
    if location_filter in job_location:
        return True
    
    # Also check common location variations without strict mapping
    # This catches cases where Indeed might use slightly different formatting
    location_parts = location_filter.split()
    if any(part in job_location for part in location_parts if len(part) > 2):
        return True
    
    # Since Indeed returned this job for the location search, trust it
    # Only filter out if it's completely unrelated (which is rare since Indeed already filtered)
    return True


def _matches_job_type_filter_indeed(job: Job, job_type_filter: Optional[str]) -> bool:
    """Check if job matches the job type filter for Indeed."""
    if not job_type_filter:
        return True
    
    job_type_filter = job_type_filter.lower().strip()
    job_remote_type = (job.remote_type or '').lower()
    job_title = (job.title or '').lower()
    job_description = (job.description or '').lower()
    
    # Map filter terms to job remote types
    if job_type_filter in ['remote', 'work from home', 'wfh', 'telecommute', 'telework']:
        return (job_remote_type in ['remote'] or 
                any(keyword in job_title for keyword in ['remote', 'work from home', 'wfh', 'telecommute']) or
                any(keyword in job_description for keyword in ['remote', 'work from home', 'wfh', 'telecommute']))
    
    elif job_type_filter in ['hybrid', 'partially remote', 'part remote', 'flexible']:
        return (job_remote_type in ['hybrid'] or 
                any(keyword in job_title for keyword in ['hybrid', 'partially remote', 'flexible']) or
                any(keyword in job_description for keyword in ['hybrid', 'partially remote', 'flexible']))
    
    elif job_type_filter in ['onsite', 'on-site', 'on site', 'office', 'in-person', 'in person']:
        return (job_remote_type in ['on-site', 'onsite'] or 
                any(keyword in job_title for keyword in ['onsite', 'on-site', 'office', 'in-person']) or
                any(keyword in job_description for keyword in ['onsite', 'on-site', 'office', 'in-person']))
    
    return True


def _matches_salary_filter_indeed(job: Job, salary_min: Optional[int], salary_max: Optional[int]) -> bool:
    """Check if job matches the salary filter for Indeed."""
    if not salary_min and not salary_max:
        return True
    
    if not job.salary_range:
        return True  # Don't filter out jobs without salary info
    
    # Extract salary numbers from salary range string
    salary_text = job.salary_range.lower()
    
    # Look for salary patterns and extract numbers
    import re
    salary_patterns = [
        r'\$?(\d+(?:,\d{3})*(?:k|k)?)\s*-\s*\$?(\d+(?:,\d{3})*(?:k|k)?)',
        r'\$?(\d+(?:,\d{3})*(?:k|k)?)\s*/\s*(?:year|yr|hour|hr)',
        r'(\d+(?:,\d{3})*(?:k|k))\s*-\s*(\d+(?:,\d{3})*(?:k|k))'
    ]
    
    for pattern in salary_patterns:
        match = re.search(pattern, salary_text)
        if match:
            try:
                # Extract and convert salary numbers
                min_sal_str = match.group(1).replace(',', '').replace('k', '000')
                max_sal_str = match.group(2).replace(',', '').replace('k', '000')
                
                min_sal = int(min_sal_str)
                max_sal = int(max_sal_str)
                
                # Check if salary range overlaps with filter range
                if salary_min and salary_max:
                    return not (max_sal < salary_min or min_sal > salary_max)
                elif salary_min:
                    return max_sal >= salary_min
                elif salary_max:
                    return min_sal <= salary_max
                    
            except (ValueError, IndexError):
                continue
    
    return True  # If we can't parse salary, don't filter out


def _matches_experience_filter_indeed(job: Job, experience_level: Optional[str]) -> bool:
    """Check if job matches the experience level filter for Indeed."""
    if not experience_level:
        return True
    
    # Since we're using Indeed's URL-based filtering with sc parameter,
    # we should trust Indeed's filtering and not apply additional post-search filtering
    # Indeed has already filtered the results correctly based on the URL parameters
    return True


def _matches_employment_filter_indeed(job: Job, employment_type: Optional[str]) -> bool:
    """Check if job matches the employment type filter for Indeed."""
    if not employment_type:
        return True
    
    # Since we're now using Indeed's URL-based filtering, be more permissive
    # Only filter out jobs if they explicitly don't match
    if not job.employment_type:
        return True  # Don't filter out jobs without employment type info
    
    employment_type = employment_type.lower().strip()
    job_employment = job.employment_type.lower()
    
    # Map filter terms to employment types (all lowercase for comparison)
    employment_mappings = {
        'full-time': ['full-time', 'full time', 'fulltime'],
        'part-time': ['part-time', 'part time', 'parttime'],
        'contract': ['contract', 'contractor', 'freelance'],
        'internship': ['internship', 'intern', 'trainee', 'co-op', 'coop', 'student'],
        'temporary': ['temporary', 'temp', 'temporary']
    }
    
    if employment_type in employment_mappings:
        # Check if job matches the employment type
        matches = any(emp_type in job_employment for emp_type in employment_mappings[employment_type])
        if matches:
            return True
        
        # For internship searches, also allow jobs that don't explicitly state employment type
        # since Indeed's URL filtering should handle the main filtering
        if employment_type == 'internship':
            return True  # Be permissive for internships
    
    return True


def _matches_date_filter_indeed(job: Job, days_old: Optional[int]) -> bool:
    """Check if job matches the date filter for Indeed (posted within last N days)."""
    if not days_old:
        return True
    
    # Since we're now using Indeed's URL-based filtering with fromage parameter,
    # be more permissive with post-search filtering
    if not job.posted_date:
        return True  # Don't filter out jobs without date info
    
    from datetime import datetime, timedelta
    import re
    
    try:
        # Parse the posted date from various formats
        posted_date = job.posted_date.lower().strip()
        current_date = datetime.now()
        
        # Handle different date formats from Indeed
        if 'today' in posted_date:
            job_date = current_date
        elif 'yesterday' in posted_date:
            job_date = current_date - timedelta(days=1)
        elif 'just posted' in posted_date or 'just now' in posted_date:
            job_date = current_date
        else:
            # Extract number of days/hours from text like "3 days ago", "2 hours ago"
            days_match = re.search(r'(\d+)\s+(?:days?|hours?)\s+ago', posted_date)
            if days_match:
                time_value = int(days_match.group(1))
                if 'hour' in posted_date:
                    # Convert hours to days (approximate)
                    job_date = current_date - timedelta(hours=time_value)
                else:
                    job_date = current_date - timedelta(days=time_value)
            else:
                # If we can't parse the date, be permissive since Indeed's URL filtering should handle it
                return True
        
        # Check if job is within the specified number of days
        days_difference = (current_date - job_date).days
        return days_difference <= days_old
        
    except Exception as e:
        print(f"Error parsing date '{job.posted_date}': {e}")
        # If there's an error parsing, be permissive since Indeed's URL filtering should handle it
        return True


def _extract_description_from_full_text(full_text: str) -> Optional[str]:
    """Extract description from full card text as fallback."""
    lines = [line.strip() for line in full_text.split('\n') if line.strip()]
    
    for line in lines:
        if (len(line) > 50 and 
            not any(keyword in line.lower() for keyword in [
                'days ago', 'hours ago', 'just posted', 'remote', 'hybrid', 
                'Full-Time', 'Part-Time', 'Contract', 'Temporary', 'Internship',
                'salary', 'benefits', 'requirements', 'qualifications', 'posted',
                'apply', 'company', 'location', 'job', 'title'
            ]) and
            not line.isupper() and
            not re.match(r'^\$[\d,]+', line) and
            not re.match(r'^\d+\s+(?:days?|hours?)\s+ago', line) and
            len(line.split()) > 8):
            if len(line) > 1000:
                line = line[:1000] + '...'
            return line
    
    return None


def _extract_employment_from_full_text(full_text: str) -> Optional[str]:
    """Extract employment type from full card text as fallback."""
    text_lower = full_text.lower()
    
    employment_patterns = {
        'full-time': ['Full-Time', 'full time', 'fulltime', 'permanent', 'regular', 'ft'],
        'part-time': ['Part-Time', 'part time', 'parttime', 'pt'],
        'contract': ['Contract', 'contractor', 'freelance', 'consultant'],
        'internship': ['Internship', 'intern', 'trainee', 'co-op', 'coop'],
        'temporary': ['Temporary', 'temp', 'seasonal']
    }
    
    for emp_type, patterns in employment_patterns.items():
        if any(pattern in text_lower for pattern in patterns):
            return emp_type.title()
    
    return None


def _extract_experience_from_full_text(full_text: str) -> Optional[str]:
    """Extract experience level from full card text as fallback."""
    # Since we're using Indeed's URL-based filtering, we should trust Indeed's classification
    # and not override it with our own detection to avoid false positives
    return None


def _extract_date_from_full_text(full_text: str) -> Optional[str]:
    """Extract posted date from full card text as fallback."""
    date_patterns = [
        r'\d+\s+(?:days?|hours?)\s+ago',
        r'Posted\s+\d+\s+(?:days?|hours?)\s+ago',
        r'Just\s+posted',
        r'Today',
        r'Yesterday',
        r'\d+\s+(?:minutes?|mins?)\s+ago',
        r'Just\s+now'
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            return match.group().strip()
    
    return None


def _extract_complete_job_details_from_url(driver, job) -> Optional[Job]:
    """Extract complete job details by navigating to the individual job page URL."""
    try:
        if not job.url:
            return job
        
        # Store current state
        original_url = driver.current_url
        original_window = driver.current_window_handle
        
        try:
            # Navigate to the individual job page
            print(f"  → Navigating to: {job.url}")
            driver.get(job.url)
            
            # Wait for page to load
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.common.by import By
            
            try:
                # Wait for the job content to load
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "jobsearch-JobComponent"))
                )
            except:
                # If specific element not found, wait for any job content
                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.TAG_NAME, "body"))
                    )
                except:
                    pass
            
            # Parse the full job page
            full_page_soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # Extract complete details from the full page
            enhanced_salary = _extract_salary_from_full_page_improved(full_page_soup)
            enhanced_employment = _extract_employment_from_full_page_improved(full_page_soup)
            enhanced_date = _extract_date_from_full_page_improved(full_page_soup)
            enhanced_description = _extract_description_from_full_page_improved(full_page_soup)
            enhanced_experience = _extract_experience_from_full_page_improved(full_page_soup)
            enhanced_benefits = _extract_benefits_from_full_page(full_page_soup)
            enhanced_requirements = _extract_requirements_from_full_page(full_page_soup)
            enhanced_skills = _extract_skills_from_full_page(full_page_soup)
            enhanced_industry = _extract_industry_from_full_page(full_page_soup)
            enhanced_company_size = _extract_company_size_from_full_page(full_page_soup)
            enhanced_raw_data = _extract_raw_data_from_full_page(full_page_soup)
            
            # Update job with enhanced details (only if not already present or significantly better)
            if enhanced_salary and not job.salary_range:
                job.salary_range = enhanced_salary
                print(f"  ✓ Enhanced salary: {enhanced_salary}")
            
            if enhanced_employment and not job.employment_type:
                job.employment_type = enhanced_employment
                print(f"  ✓ Enhanced employment: {enhanced_employment}")
            
            if enhanced_date and not job.posted_date:
                job.posted_date = enhanced_date
                print(f"  ✓ Enhanced date: {enhanced_date}")
            
            # Always update description with full job description from detail page
            if enhanced_description and len(enhanced_description) > 100:
                job.description = enhanced_description
                print(f"  ✓ Enhanced description: {len(enhanced_description)} characters")
            
            if enhanced_experience and not job.experience_level:
                job.experience_level = enhanced_experience
                print(f"  ✓ Enhanced experience: {enhanced_experience}")
            
            if enhanced_benefits and not job.benefits:
                job.benefits = enhanced_benefits
                print(f"  ✓ Enhanced benefits: {len(enhanced_benefits)} items")
            
            if enhanced_requirements and not job.requirements:
                job.requirements = enhanced_requirements
                print(f"  ✓ Enhanced requirements: {len(enhanced_requirements)} items")
            
            # Only update skills if current ones are empty or very few
            if enhanced_skills and (not job.skills or len(job.skills or []) < 3):
                job.skills = enhanced_skills
                print(f"  ✓ Enhanced skills: {len(enhanced_skills)} items")
            
            if enhanced_industry and not job.industry:
                job.industry = enhanced_industry
                print(f"  ✓ Enhanced industry: {enhanced_industry}")
            
            if enhanced_company_size and not job.company_size:
                job.company_size = enhanced_company_size
                print(f"  ✓ Enhanced company size: {enhanced_company_size}")
            
        finally:
            # Always navigate back to the original page
            try:
                print(f"  ← Navigating back to search results")
                driver.get(original_url)
                # Wait for the page to load back
                time.sleep(2)
            except Exception as e:
                print(f"Warning: Could not navigate back to original page: {e}")
        
        return job
        
    except Exception as e:
        print(f"Error extracting complete job details: {e}")
        # Try to navigate back to original page
        try:
            driver.get(original_url)
        except:
            pass
        return job


def _extract_benefits_from_full_page(soup) -> Optional[List[str]]:
    """Extract benefits from Indeed's full job page."""
    benefits = []
    
    # Look for benefits in various sections
    benefit_selectors = [
        'div[class*="benefits"]',
        'div[class*="perks"]',
        'div[class*="compensation"]',
        'div[class*="jobsearch-JobDescriptionSection"]',
        'ul[class*="benefits"]',
        'ul[class*="perks"]'
    ]
    
    for selector in benefit_selectors:
        elements = soup.select(selector)
        for elem in elements:
            # Look for list items or text that might contain benefits
            list_items = elem.find_all(['li', 'span', 'div'])
            for item in list_items:
                text = item.get_text(strip=True)
                if text and any(keyword in text.lower() for keyword in [
                    'health', 'dental', 'vision', 'insurance', 'pto', 'vacation', 'sick', 'leave',
                    'retirement', '401k', 'pension', 'bonus', 'stock', 'equity', 'remote', 'flexible',
                    'gym', 'fitness', 'lunch', 'snacks', 'coffee', 'parking', 'transit', 'tuition',
                    'education', 'training', 'development', 'conference', 'travel', 'relocation'
                ]):
                    if len(text) > 5 and len(text) < 100:  # Reasonable length for benefits
                        benefits.append(text)
    
    # Remove duplicates and return
    return list(set(benefits)) if benefits else None


def _extract_requirements_from_full_page(soup) -> Optional[List[str]]:
    """Extract requirements from Indeed's full job page."""
    requirements = []
    
    # Look for requirements in job description sections
    req_selectors = [
        'div[class*="jobsearch-JobDescriptionSection"]',
        'div[class*="description"]',
        'div[class*="requirements"]',
        'div[class*="qualifications"]',
        'ul[class*="requirements"]',
        'ul[class*="qualifications"]'
    ]
    
    for selector in req_selectors:
        elements = soup.select(selector)
        for elem in elements:
            # Look for list items or paragraphs that might contain requirements
            list_items = elem.find_all(['li', 'p', 'div'])
            for item in list_items:
                text = item.get_text(strip=True)
                if text and any(keyword in text.lower() for keyword in [
                    'required', 'must have', 'minimum', 'years of experience', 'degree', 'bachelor',
                    'master', 'phd', 'certification', 'license', 'skills', 'proficiency', 'knowledge',
                    'experience with', 'familiar with', 'understanding of', 'ability to', 'strong'
                ]):
                    if len(text) > 10 and len(text) < 200:  # Reasonable length for requirements
                        requirements.append(text)
    
    # Remove duplicates and return
    return list(set(requirements)) if requirements else None


def _extract_skills_from_full_page(soup) -> Optional[List[str]]:
    """Extract skills from Indeed's full job page."""
    skills = []
    
    # Common programming languages and technologies
    skill_keywords = [
        'python', 'java', 'javascript', 'typescript', 'react', 'angular', 'vue', 'node.js',
        'php', 'ruby', 'go', 'rust', 'c++', 'c#', 'swift', 'kotlin', 'scala', 'r',
        'sql', 'mysql', 'postgresql', 'mongodb', 'redis', 'elasticsearch', 'aws', 'azure',
        'docker', 'kubernetes', 'jenkins', 'git', 'linux', 'unix', 'html', 'css',
        'sass', 'less', 'webpack', 'babel', 'npm', 'yarn', 'maven', 'gradle'
    ]
    
    # Look for skills in job description
    desc_selectors = [
        'div[class*="jobsearch-JobDescriptionSection"]',
        'div[class*="description"]',
        'div[class*="requirements"]'
    ]
    
    for selector in desc_selectors:
        elements = soup.select(selector)
        for elem in elements:
            text = elem.get_text().lower()
            for skill in skill_keywords:
                if skill in text and skill not in skills:
                    skills.append(skill.title())
    
    return skills if skills else None


def _extract_industry_from_full_page(soup) -> Optional[str]:
    """Extract industry from Indeed's full job page."""
    # Look for industry information
    industry_selectors = [
        'div[class*="company-info"]',
        'div[class*="jobsearch-CompanyInfoContainer"]',
        'div[class*="jobsearch-CompanyDescription"]',
        'span[class*="industry"]',
        'div[class*="industry"]'
    ]
    
    for selector in industry_selectors:
        elem = soup.select_one(selector)
        if elem:
            text = elem.get_text(strip=True)
            if text and len(text) > 3 and len(text) < 100:
                return text
    
    return None


def _extract_company_size_from_full_page(soup) -> Optional[str]:
    """Extract company size from Indeed's full job page."""
    # Look for company size information
    size_selectors = [
        'div[class*="company-info"]',
        'div[class*="jobsearch-CompanyInfoContainer"]',
        'div[class*="jobsearch-CompanyDescription"]',
        'span[class*="size"]',
        'div[class*="size"]'
    ]
    
    for selector in size_selectors:
        elem = soup.select_one(selector)
        if elem:
            text = elem.get_text(strip=True)
            if text and any(keyword in text.lower() for keyword in [
                'employees', 'staff', 'team', 'company size', '1-10', '11-50', '51-200',
                '201-500', '501-1000', '1001-5000', '5001+', 'startup', 'enterprise'
            ]):
                return text
    
    return None


def _extract_raw_data_from_full_page(soup) -> Optional[str]:
    """Extract raw HTML and text data from the full job page."""
    try:
        # Get the raw HTML of the full page
        raw_html = str(soup)
        
        # Also get the clean text content
        raw_text = soup.get_text(separator='\n', strip=True)
        
        # Combine both for comprehensive raw data
        raw_data = f"FULL_PAGE_HTML:\n{raw_html}\n\nFULL_PAGE_TEXT:\n{raw_text}"
        
        return raw_data
    except Exception as e:
        print(f"Error extracting raw data from full page: {e}")
        return None


def _extract_enhanced_job_details_indeed_improved(driver, card, job) -> Optional[Job]:
    """Enhanced job details extraction with improved session management."""
    try:
        # Find the job title link within the card
        title_link = card.select_one('h2.jobTitle a, a[data-jk], a[class*="jcs-JobTitle"]')
        if not title_link:
            return job
        
        # Get the href attribute
        job_url = title_link.get('href')
        if not job_url:
            return job
        
        # Make sure it's a full URL
        if job_url.startswith('/'):
            job_url = 'https://www.indeed.com' + job_url
        
        # Store current state
        original_url = driver.current_url
        original_window = driver.current_window_handle
        
        try:
            # Navigate directly to the job page (more reliable than new tabs)
            driver.get(job_url)
            
            # Wait for page to load with better error handling
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.common.by import By
            
            try:
                # Wait for the page to load
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
            except:
                # If wait fails, just proceed with a short sleep
                time.sleep(3)
            
            # Parse the full job page
            full_page_soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # Extract enhanced details from the full page
            enhanced_salary = _extract_salary_from_full_page_improved(full_page_soup)
            enhanced_employment = _extract_employment_from_full_page_improved(full_page_soup)
            enhanced_date = _extract_date_from_full_page_improved(full_page_soup)
            enhanced_description = _extract_description_from_full_page_improved(full_page_soup)
            enhanced_experience = _extract_experience_from_full_page_improved(full_page_soup)
            
            # Update job with enhanced details (only if not already present)
            if enhanced_salary and not job.salary_range:
                job.salary_range = enhanced_salary
                print(f"  ✓ Enhanced salary: {enhanced_salary}")
            if enhanced_employment and not job.employment_type:
                job.employment_type = enhanced_employment
                print(f"  ✓ Enhanced employment: {enhanced_employment}")
            if enhanced_date and not job.posted_date:
                job.posted_date = enhanced_date
                print(f"  ✓ Enhanced date: {enhanced_date}")
            # Always update description with full job description from detail page
            if enhanced_description and len(enhanced_description) > 100:
                job.description = enhanced_description
                print(f"  ✓ Enhanced description: {len(enhanced_description)} characters")
            if enhanced_experience and not job.experience_level:
                job.experience_level = enhanced_experience
                print(f"  ✓ Enhanced experience: {enhanced_experience}")
            
        finally:
            # Always navigate back to the original page
            try:
                driver.get(original_url)
                # Wait for the page to load back
                time.sleep(2)
            except Exception as e:
                print(f"Warning: Could not navigate back to original page: {e}")
        
        return job
        
    except Exception as e:
        print(f"Error extracting enhanced job details: {e}")
        # Try to navigate back to original page
        try:
            driver.get(original_url)
        except:
            pass
        return job


def _extract_salary_from_full_page_improved(soup) -> Optional[str]:
    """Extract salary from Indeed's full job page with comprehensive patterns."""
    # Get all text content first
    text_content = soup.get_text()
    
    # Enhanced salary patterns for full page
    salary_patterns = [
        # Standard salary ranges with currency
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?\s*(?:a\s+year|per\s+year|annually|yearly)',
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        r'\$[\d,]+(?:K|k)?\s*/\s*(?:year|yr|hour|hr)',
        r'\$[\d,]+(?:K|k)?\s*per\s*(?:year|yr|hour|hr)',
        r'\$[\d,]+(?:K|k)?\s*annually',
        r'\$[\d,]+(?:K|k)?\s*hourly',
        
        # Salary ranges without currency symbol
        r'[\d,]+(?:K|k)\s*-\s*[\d,]+(?:K|k)\s*(?:a\s+year|per\s+year|annually|yearly)',
        r'[\d,]+(?:K|k)\s*-\s*[\d,]+(?:K|k)',
        r'[\d,]+(?:K|k)\s*to\s*[\d,]+(?:K|k)',
        
        # Salary with labels
        r'Salary:\s*\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        r'Pay:\s*\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        r'Compensation:\s*\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        r'Rate:\s*\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        
        # Single salary amounts
        r'Up\s+to\s+\$[\d,]+(?:K|k)?',
        r'Starting\s+at\s+\$[\d,]+(?:K|k)?',
        r'From\s+\$[\d,]+(?:K|k)?',
        r'Base\s+salary\s*\$[\d,]+(?:K|k)?',
        
        # Hourly rates
        r'\$[\d,]+(?:\.\d+)?\s*/\s*hour',
        r'\$[\d,]+(?:\.\d+)?\s*per\s*hour',
        r'\$[\d,]+(?:\.\d+)?\s*hourly'
    ]
    
    for pattern in salary_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            salary_text = match.group().strip()
            salary_text = re.sub(r'\s+', ' ', salary_text).strip()
            if len(salary_text) > 5:
                return salary_text
    
    # Look for salary in specific elements
    salary_selectors = [
        'div[data-testid="job-details"] span',
        'div[class*="job-details"] span',
        'div[class*="jobDetails"] span',
        'span[class*="icl-u-xs-mr--xs"]',
        'div[class*="icl-u-xs-mr--xs"]',
        'div[class*="jobsearch-JobDescriptionSection"] span',
        'div[class*="jobsearch-JobDescriptionSection"] div',
        'span[class*="salary"]',
        'div[class*="salary"]',
        'span[class*="pay"]',
        'div[class*="pay"]'
    ]
    
    for selector in salary_selectors:
        elements = soup.select(selector)
        for elem in elements:
            text = elem.get_text(strip=True)
            if '$' in text and any(keyword in text.lower() for keyword in ['year', 'hour', 'salary', 'pay', 'compensation']):
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 5:
                    return text
    
    return None


def _extract_employment_from_full_page_improved(soup) -> Optional[str]:
    """Extract employment type from Indeed's full job page with comprehensive patterns."""
    # Get all text content first
    text_content = soup.get_text().lower()
    
    # Enhanced employment type patterns
    employment_patterns = {
        'full-time': ['Full-Time', 'full time', 'fulltime', 'permanent', 'regular', 'ft', 'fulltime position'],
        'part-time': ['Part-Time', 'part time', 'parttime', 'pt', 'part time position'],
        'contract': ['Contract', 'contractor', 'freelance', 'consultant', 'contract work', 'contracting', 'contract position'],
        'internship': ['Internship', 'intern', 'trainee', 'co-op', 'coop', 'student', 'intern position'],
        'temporary': ['Temporary', 'temp', 'seasonal', 'short-term', 'short term', 'temp position']
    }
    
    for emp_type, patterns in employment_patterns.items():
        if any(pattern in text_content for pattern in patterns):
            return emp_type.title()
    
    # Look for employment type in specific elements
    employment_selectors = [
        'div[data-testid="job-details"] span',
        'div[class*="job-details"] span',
        'div[class*="jobDetails"] span',
        'span[class*="icl-u-xs-mr--xs"]',
        'div[class*="icl-u-xs-mr--xs"]',
        'div[class*="jobsearch-JobDescriptionSection"] span',
        'div[class*="jobsearch-JobDescriptionSection"] div',
        'span[class*="employment"]',
        'div[class*="employment"]',
        'span[class*="job-type"]',
        'div[class*="job-type"]'
    ]
    
    for selector in employment_selectors:
        elements = soup.select(selector)
        for elem in elements:
            text = elem.get_text(strip=True).lower()
            if any(term in text for term in ['Full-Time', 'Part-Time', 'Contract', 'Temporary', 'Internship']):
                return _extract_employment_from_text(text)
    
    return None


def _extract_date_from_full_page_improved(soup) -> Optional[str]:
    """Extract posted date from Indeed's full job page with comprehensive patterns."""
    # Get all text content first
    text_content = soup.get_text()
    
    # Enhanced date patterns for full page
    date_patterns = [
        # Standard time ago formats
        r'\d+\s+(?:days?|hours?|minutes?|mins?)\s+ago',
        r'Posted\s+\d+\s+(?:days?|hours?|minutes?|mins?)\s+ago',
        r'Active\s+\d+\s+(?:days?|hours?|minutes?|mins?)\s+ago',
        r'Updated\s+\d+\s+(?:days?|hours?|minutes?|mins?)\s+ago',
        
        # Specific time references
        r'Just\s+posted',
        r'Just\s+now',
        r'Today',
        r'Yesterday',
        r'Recently\s+posted',
        r'New\s+posting',
        r'Fresh\s+posting',
        
        # Time with specific words
        r'\d+\s+(?:day|hour|minute)s?\s+ago',
        r'Posted\s+\d+\s+(?:day|hour|minute)s?\s+ago',
        r'Active\s+\d+\s+(?:day|hour|minute)s?\s+ago',
        
        # Time ranges
        r'(\d+)-(\d+)\s+(?:days?|hours?)\s+ago',
        r'Posted\s+(\d+)-(\d+)\s+(?:days?|hours?)\s+ago',
        
        # Specific time periods
        r'Less\s+than\s+\d+\s+(?:days?|hours?)\s+ago',
        r'Within\s+the\s+last\s+\d+\s+(?:days?|hours?)',
        r'In\s+the\s+last\s+\d+\s+(?:days?|hours?)'
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            date_text = match.group().strip()
            date_text = re.sub(r'\s+', ' ', date_text).strip()
            if len(date_text) > 2:
                return date_text
    
    # Look for date in specific elements
    date_selectors = [
        'span[class*="date"]',
        'div[class*="date"]',
        'span[data-testid="myJobsStateDate"]',
        'div[data-testid="myJobsStateDate"]',
        'span[class*="metadata"]',
        'div[class*="metadata"]',
        'span[class*="job-snippet"]',
        'div[class*="job-snippet"]',
        'span[class*="posted"]',
        'div[class*="posted"]',
        'span[class*="time"]',
        'div[class*="time"]',
        'span[class*="ago"]',
        'div[class*="ago"]'
    ]
    
    for selector in date_selectors:
        elem = soup.select_one(selector)
        if elem:
            text = elem.get_text(strip=True)
            if any(keyword in text.lower() for keyword in ['ago', 'posted', 'today', 'yesterday', 'active', 'just']):
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 2:
                    return text
    
    return None


def _clean_and_format_description(text: str) -> str:
    """Clean and format job description text for better readability."""
    import html
    
    # Decode HTML entities first
    text = html.unescape(text)
    
    # Remove common prefixes that aren't part of the description
    text = re.sub(r'^(Job|Company|Location|Posted|Salary|Benefits|Requirements|Description):\s*', '', text, flags=re.IGNORECASE)
    
    # Remove Indeed-specific text that might appear
    text = re.sub(r'^(Indeed|Job Search|Search Jobs|Create Account|Sign In).*?$', '', text, flags=re.IGNORECASE | re.MULTILINE)
    
    # Remove profile insights and skills sections that often appear
    text = re.sub(r'Profile insights.*?Skills.*?Do you have experience in.*?YesNo', '', text, flags=re.IGNORECASE | re.DOTALL)
    
    # Clean up job details section
    text = re.sub(r'Job details.*?Job type.*?Full-time', '', text, flags=re.IGNORECASE | re.DOTALL)
    
    # Remove specific unwanted patterns
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'Find out how your skills align with the job description', '', text, flags=re.IGNORECASE)
    
    # Clean up spacing around punctuation and common patterns
    text = re.sub(r'([.!?])([A-Z])', r'\1 \2', text)  # Space after sentences
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)  # Space between camelCase words
    
    # Add proper line breaks before section headers
    text = re.sub(r'([a-z])([A-Z][a-z]+:)', r'\1\n\n\2', text)  # New line before section headers
    text = re.sub(r'([a-z])([A-Z][A-Z\s]+:)', r'\1\n\n\2', text)  # New line before ALL CAPS headers
    
    # Clean up specific job description patterns
    text = re.sub(r'Position Title:', '\nPosition Title:', text)
    text = re.sub(r'Location:', '\nLocation:', text)
    text = re.sub(r'Reports To:', '\nReports To:', text)
    text = re.sub(r'Position Type:', '\nPosition Type:', text)
    text = re.sub(r'ABOUT US', '\n\nABOUT US', text)
    text = re.sub(r'POSITION OVERVIEW', '\n\nPOSITION OVERVIEW', text)
    text = re.sub(r'KEY RESPONSIBILITIES', '\n\nKEY RESPONSIBILITIES', text)
    text = re.sub(r'QUALIFICATIONS', '\n\nQUALIFICATIONS', text)
    
    # Clean up multiple spaces and newlines
    text = re.sub(r'[ \t]+', ' ', text)  # Multiple spaces to single space
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)  # Multiple newlines to double newlines
    text = re.sub(r'[ \t]+', ' ', text)  # Clean up spaces again
    
    # Remove leading/trailing whitespace
    text = text.strip()
    
    return text


def _extract_description_from_full_page_improved(soup) -> Optional[str]:
    """Extract description from Indeed's full job page with comprehensive patterns."""
    # Look for job description in various selectors - prioritize the main job description area
    desc_selectors = [
        # Primary selectors for Indeed's job description
        'div.jobsearch-jobDescriptionText',
        'div[class*="jobsearch-jobDescriptionText"]',
        'div[data-testid="job-description"]',
        'div[class*="jobDescriptionText"]',
        'div[class*="job-description"]',
        'div[class*="jobDescription"]',
        'div[class*="description"]',
        'div[class*="jobsearch-jobDescription"]',
        'div[class*="jobsearch-jobDescriptionSection"]',
        'div[class*="jobsearch-JobComponent"]',
        # Look for the main content area
        'div[class*="jobsearch-JobComponent"] div[class*="jobsearch-jobDescriptionText"]',
        'div[class*="jobsearch-JobComponent"] div[class*="jobDescriptionText"]',
        # Fallback selectors
        'div[class*="jobsearch-jobDescriptionText"] p',
        'div[class*="jobsearch-jobDescriptionText"] div',
        'div[class*="jobsearch-jobDescriptionText"] span',
        'div[class*="jobDescriptionText"] p',
        'div[class*="jobDescriptionText"] div',
        'div[class*="jobDescriptionText"] span'
    ]
    
    for selector in desc_selectors:
        elem = soup.select_one(selector)
        if elem:
            # Use separator to preserve some structure
            text = elem.get_text(separator='\n', strip=True)
            if len(text) > 100:
                # Clean up the text with better formatting
                text = _clean_and_format_description(text)
                if len(text) > 100:
                    print(f"  ✓ Found description using selector '{selector}': {len(text)} characters")
                    # Don't truncate - return the full description
                    return text
    
    # Fallback: Look for any large text blocks that might be descriptions
    all_text = soup.get_text()
    lines = [line.strip() for line in all_text.split('\n') if line.strip()]
    
    # Look for the longest meaningful text block that could be a job description
    best_description = None
    max_length = 0
    
    for line in lines:
        if (len(line) > 200 and  # Longer minimum length for full page
            not any(keyword in line.lower() for keyword in [
                'days ago', 'hours ago', 'just posted', 'remote', 'hybrid', 
                'Full-Time', 'Part-Time', 'Contract', 'Temporary', 'Internship',
                'salary', 'benefits', 'requirements', 'qualifications', 'posted',
                'apply', 'company', 'location', 'job', 'title', 'responds within',
                'often responds', 'new', 'urgent', 'hiring', 'immediately',
                'indeed', 'job search', 'search jobs', 'create account', 'sign in',
                'apply now', 'easy apply', 'view job', 'save job', 'share job'
            ]) and
            not line.isupper() and  # Not all caps
            not re.match(r'^\$[\d,]+', line) and  # Not salary info
            not re.match(r'^\d+\s+(?:days?|hours?)\s+ago', line) and  # Not date info
            not re.match(r'^[A-Z][a-z]+\s+[A-Z][a-z]+$', line) and  # Not company names
            not re.match(r'^[A-Z][a-z]+,\s*[A-Z]{2}\s+\d{5}', line) and  # Not locations
            len(line.split()) > 15):  # Must have multiple words
            # Clean up the line with better formatting
            cleaned_line = _clean_and_format_description(line)
            if len(cleaned_line) > max_length:
                max_length = len(cleaned_line)
                best_description = cleaned_line
    
    if best_description:
        print(f"  ✓ Found description using fallback method: {len(best_description)} characters")
    else:
        print(f"  ⚠ No description found using any method")
    
    return best_description


def _extract_experience_from_full_page_improved(soup) -> Optional[str]:
    """Extract experience level from Indeed's full job page with comprehensive patterns."""
    # Since we're using Indeed's URL-based filtering, we should trust Indeed's classification
    # and not override it with our own detection to avoid false positives
    return None


def _create_job_id(job: Job) -> str:
    """
    Create a unique identifier for a job to prevent duplicates.
    Uses a combination of title, company, and location for uniqueness.
    """
    # Create a unique ID based on job attributes
    title = (job.title or '').lower().strip()
    company = (job.company or '').lower().strip()
    location = (job.location or '').lower().strip()
    job_url = (job.url or '').lower().strip()
    
    # Use job URL if available (most reliable)
    if job_url and 'indeed.com' in job_url:
        # Extract the job ID from Indeed URL if possible
        if 'jk=' in job_url:
            try:
                job_id = job_url.split('jk=')[1].split('&')[0]
                return f"indeed_{job_id}"
            except:
                pass
        return job_url
    
    # Fallback to combination of title, company, and location
    # Normalize the strings to avoid minor differences
    title_norm = ' '.join(title.split())
    company_norm = ' '.join(company.split())
    location_norm = ' '.join(location.split())
    
    return f"{title_norm}|{company_norm}|{location_norm}"


def _get_indeed_experience_filter(experience_level: str) -> Optional[str]:
    """
    Get Indeed's experience level filter parameter.
    Indeed uses the 'sc' parameter with encoded attributes for experience level filtering.
    """
    experience_level = experience_level.lower().strip()
    
    # Indeed's experience level filter mappings using the 'sc' parameter
    # Format: 0kf%3Aexplvl%28{LEVEL}%29%3B
    experience_filters = {
        'entry': '0kf%3Aexplvl%28ENTRY_LEVEL%29%3B',
        'entry-level': '0kf%3Aexplvl%28ENTRY_LEVEL%29%3B',
        'entry level': '0kf%3Aexplvl%28ENTRY_LEVEL%29%3B',
        'junior': '0kf%3Aexplvl%28ENTRY_LEVEL%29%3B',
        'jr': '0kf%3Aexplvl%28ENTRY_LEVEL%29%3B',
        'intern': '0kf%3Aexplvl%28ENTRY_LEVEL%29%3B',
        'internship': '0kf%3Aexplvl%28ENTRY_LEVEL%29%3B',
        'assistant': '0kf%3Aexplvl%28ENTRY_LEVEL%29%3B',
        'mid': '0kf%3Aexplvl%28MID_LEVEL%29%3B',
        'mid-level': '0kf%3Aexplvl%28MID_LEVEL%29%3B',
        'mid level': '0kf%3Aexplvl%28MID_LEVEL%29%3B',
        'intermediate': '0kf%3Aexplvl%28MID_LEVEL%29%3B',
        'mid-senior': '0kf%3Aexplvl%28MID_LEVEL%29%3B',
        'mid senior': '0kf%3Aexplvl%28MID_LEVEL%29%3B',
        'senior': '0kf%3Aexplvl%28SENIOR_LEVEL%29%3B',
        'senior-level': '0kf%3Aexplvl%28SENIOR_LEVEL%29%3B',
        'senior level': '0kf%3Aexplvl%28SENIOR_LEVEL%29%3B',
        'sr': '0kf%3Aexplvl%28SENIOR_LEVEL%29%3B',
        'lead': '0kf%3Aexplvl%28SENIOR_LEVEL%29%3B',
        'principal': '0kf%3Aexplvl%28SENIOR_LEVEL%29%3B',
        'executive': '0kf%3Aexplvl%28EXECUTIVE_LEVEL%29%3B',
        'executive-level': '0kf%3Aexplvl%28EXECUTIVE_LEVEL%29%3B',
        'executive level': '0kf%3Aexplvl%28EXECUTIVE_LEVEL%29%3B',
        'director': '0kf%3Aexplvl%28EXECUTIVE_LEVEL%29%3B',
        'manager': '0kf%3Aexplvl%28EXECUTIVE_LEVEL%29%3B',
        'vp': '0kf%3Aexplvl%28EXECUTIVE_LEVEL%29%3B'
    }
    
    return experience_filters.get(experience_level)


def _get_indeed_date_filter(days_old: int) -> int:
    """
    Get Indeed's date filter parameter.
    Indeed uses the 'fromage' parameter where the value is the number of days.
    """
    # Indeed's fromage parameter accepts the number of days directly
    # Common values: 1, 3, 7, 14, 30
    return days_old


def _get_indeed_employment_filter(employment_type: str) -> Optional[str]:
    """
    Get Indeed's URL parameter for employment type filtering.
    Indeed uses specific encoded parameters for job type filtering.
    """
    employment_type = employment_type.lower().strip()
    
    # Indeed's employment type filter mappings
    # These parameters are used in Indeed's search URLs
    employment_filters = {
        'internship': '0kf%3Aattr%28DSQF7%29',  # Internship filter
        'full-time': '0kf%3Aattr%28HOVV8%29',   # Full-time filter  
        'part-time': '0kf%3Aattr%28HOVV8%29',   # Part-time filter
        'contract': '0kf%3Aattr%28HOVV8%29',    # Contract filter
        'temporary': '0kf%3Aattr%28HOVV8%29',  # Temporary filter
        'fresher': '0kf%3Aattr%28DSQF7%29'     # Fresher filter
    }
    
    # Alternative approach: Use Indeed's job type filter
    # This might be more reliable than the encoded parameters
    job_type_filters = {
        'internship': 'jt=internship',
        'full-time': 'jt=fulltime', 
        'part-time': 'jt=parttime',
        'contract': 'jt=contract',
        'temporary': 'jt=temporary'
    }
    
    # Return the job type filter instead of the encoded parameter
    return job_type_filters.get(employment_type)


def _format_location_for_indeed(location: str) -> str:
    """Format location for Indeed search with comprehensive location support like ZipRecruiter."""
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
    us_state_mappings = {
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
    
    if location in us_state_mappings:
        return us_state_mappings[location]
    
    # Handle city-level searches - try different formats for Indeed
    city_mappings = {
        'lahore': 'Lahore,+Pakistan',  # Try without URL encoding first
        'karachi': 'Karachi,+Pakistan',
        'islamabad': 'Islamabad,+Pakistan',
        'new york': 'New+York,+NY',
        'nyc': 'New+York,+NY',
        'san francisco': 'San+Francisco,+CA',
        'sf': 'San+Francisco,+CA',
        'los angeles': 'Los+Angeles,+CA',
        'la': 'Los+Angeles,+CA',
        'chicago': 'Chicago,+IL',
        'boston': 'Boston,+MA',
        'seattle': 'Seattle,+WA',
        'austin': 'Austin,+TX',
        'denver': 'Denver,+CO',
        'miami': 'Miami,+FL',
        'london': 'London,+UK',
        'toronto': 'Toronto,+Canada',
        'vancouver': 'Vancouver,+Canada',
        'sydney': 'Sydney,+Australia',
        'melbourne': 'Melbourne,+Australia',
        'berlin': 'Berlin,+Germany',
        'paris': 'Paris,+France',
        'mumbai': 'Mumbai,+India',
        'delhi': 'Delhi,+India',
        'bangalore': 'Bangalore,+India',
        'tokyo': 'Tokyo,+Japan',
        'shanghai': 'Shanghai,+China',
        'beijing': 'Beijing,+China'
    }
    
    if location in city_mappings:
        return city_mappings[location]
    
    # For unmapped locations, URL encode spaces and commas
    return location.replace(' ', '+').replace(',', '%2C')


def close_driver():
    """Close the WebDriver when done."""
    global _driver, _driver_created_at
    if _driver:
        _driver.quit()
        _driver = None
        _driver_created_at = 0
    
    # Optionally reset proxy manager (uncomment if you want fresh start each time)
    # reset_proxy_manager()

