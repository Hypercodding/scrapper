"""
Playwright-based Indeed scraper - More stable alternative to Selenium.

Benefits:
- No ChromeDriver version issues (bundles its own browser)
- Better resource management
- More stable in headless mode
- Better error handling
- Full proxy support and Cloudflare bypass
"""

import time
import random
import asyncio
import re
from typing import Optional, List
from urllib.parse import quote_plus, urlparse
from bs4 import BeautifulSoup
from app.models.job_model import Job
from app.core.config import settings
from app.core.proxy_manager import get_proxy_manager, reset_proxy_manager

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("⚠️  Playwright not installed. Install with: pip install playwright && python -m playwright install chromium")


_browser: Optional[Browser] = None
_context: Optional[BrowserContext] = None
_last_fetch = 0


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
            proxy_urls = [url.strip() for url in proxy_urls_str.split(",") if url.strip()]
    
    # Fall back to legacy PROXY_URL setting
    if not proxy_urls and hasattr(settings, "PROXY_URL") and settings.PROXY_URL:
        proxy_url = settings.PROXY_URL.strip()
        if proxy_url:
            proxy_urls = [proxy_url]
    
    return proxy_urls


async def get_browser(force_new: bool = False) -> tuple[Browser, BrowserContext]:
    """
    Get or create a Playwright browser instance with proxy and stealth support.
    
    Args:
        force_new: If True, create a new browser instance
        
    Returns:
        Tuple of (browser, context)
    """
    global _browser, _context
    
    if not PLAYWRIGHT_AVAILABLE:
        raise ImportError("Playwright is not installed. Install with: pip install playwright && python -m playwright install chromium")
    
    if _browser and not force_new:
        try:
            # Check if browser is still alive
            if _browser.is_connected():
                return _browser, _context
        except:
            # Browser is dead, create new one
            _browser = None
            _context = None
    
    # Get proxy configuration
    proxy_config = None
    proxy_urls = _get_proxy_urls()
    if proxy_urls:
        try:
            # Initialize proxy manager with all available proxies
            proxy_manager = get_proxy_manager(proxy_urls, getattr(settings, "PROXY_ROTATION_INTERVAL", 240))
            
            # Get the current proxy to use
            proxy_raw = proxy_manager.get_current_proxy()
            print(f"🔄 Using proxy: {proxy_manager._mask_proxy(proxy_raw)}")
            
            if proxy_raw:
                parsed = urlparse(proxy_raw)
                if parsed.hostname and parsed.port:
                    proxy_config = {
                        "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
                    }
                    # Add authentication if provided
                    if parsed.username and parsed.password:
                        proxy_config["username"] = parsed.username
                        proxy_config["password"] = parsed.password
        except Exception as e:
            print(f"⚠️  Error setting up proxy: {e}")
            # Continue without proxy
    
    # Create new browser
    try:
        playwright = await async_playwright().start()
    except Exception as playwright_error:
        error_msg = f"Failed to start Playwright: {str(playwright_error)}"
        print(f"❌ {error_msg}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        raise Exception(f"{error_msg}. This may indicate Playwright browsers are not installed. Run: python -m playwright install chromium")
    
    # Launch browser with optimized settings for headless mode
    try:
        _browser = await playwright.chromium.launch(
            headless=True,
            args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-software-rasterizer",
            "--single-process",
            "--no-zygote",
            "--window-size=1280,720",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-client-side-phishing-detection",
            "--disable-default-apps",
            "--disable-sync",
            "--metrics-recording-only",
            "--mute-audio",
            "--disable-blink-features=AutomationControlled",
        ]
        )
    except Exception as launch_error:
        error_msg = f"Failed to launch Chromium browser: {str(launch_error)}"
        print(f"❌ {error_msg}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        raise Exception(f"{error_msg}. This usually means Playwright browsers are not installed. Run: python -m playwright install chromium")
    
    # Get accept language with proper fallback
    accept_lang = getattr(settings, "ACCEPT_LANGUAGE", "en-US,en;q=0.9") or "en-US,en;q=0.9"
    locale = accept_lang.split(",")[0].strip() if accept_lang else "en-US"
    
    # Create context with realistic settings and proxy
    context_options = {
        "viewport": {"width": 1280, "height": 720},
        "user_agent": settings.USER_AGENT,
        "locale": locale,
        "timezone_id": "America/New_York",
        "java_script_enabled": True,
        "bypass_csp": True,
        "ignore_https_errors": True,
        "accept_downloads": False,
    }
    
    # Add proxy if configured
    if proxy_config:
        context_options["proxy"] = proxy_config
    
    _context = await _browser.new_context(**context_options)
    
    # Enhanced stealth script to hide automation (similar to selenium-stealth)
    await _context.add_init_script("""
        // Hide webdriver property
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        
        // Add plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        
        // Set languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        
        // Add chrome runtime
        window.chrome = {
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };
        
        // Override permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        
        // Mock platform
        Object.defineProperty(navigator, 'platform', {
            get: () => 'Win32'
        });
        
        // Mock hardware concurrency
        Object.defineProperty(navigator, 'hardwareConcurrency', {
            get: () => 8
        });
        
        // Mock device memory
        Object.defineProperty(navigator, 'deviceMemory', {
            get: () => 8
        });
    """)
    
    return _browser, _context


async def close_browser():
    """Close the browser and clean up resources."""
    global _browser, _context
    
    if _context:
        try:
            await _context.close()
        except:
            pass
        _context = None
    
    if _browser:
        try:
            await _browser.close()
        except:
            pass
        _browser = None


async def scrape_indeed_playwright(
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
    Scrape Indeed jobs using Playwright.
    
    Args:
        query: Job search query
        location: Job location
        max_results: Maximum number of jobs to return
        job_type: Job type filter
        salary_min: Minimum salary
        salary_max: Maximum salary
        experience_level: Experience level filter
        employment_type: Employment type filter
        days_old: Filter jobs posted within last N days
        
    Returns:
        List of Job objects
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise ImportError("Playwright is not installed. Install with: pip install playwright && python -m playwright install chromium")
    
    global _last_fetch
    
    # Rate limiting
    now = time.monotonic()
    jitter = random.uniform(0, 0.75)
    wait = settings.MIN_DELAY + jitter - (now - _last_fetch)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_fetch = time.monotonic()
    
    try:
        browser, context = await get_browser()
    except Exception as browser_error:
        error_msg = f"Failed to get browser: {str(browser_error)}"
        print(f"❌ {error_msg}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        raise Exception(f"{error_msg}. This may indicate Playwright browsers are not installed. Run: python -m playwright install chromium")
    
    try:
        page: Page = await context.new_page()
    except Exception as page_error:
        error_msg = f"Failed to create page: {str(page_error)}"
        print(f"❌ {error_msg}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        raise Exception(f"{error_msg}. This may indicate Playwright browsers are not installed. Run: python -m playwright install chromium")
    
    try:
        # Build Indeed URL
        base_url = "https://www.indeed.com/jobs"
        params = {"q": query}
        if location:
            params["l"] = location
        if job_type:
            params["jt"] = job_type.lower()
        if salary_min:
            params["salary"] = f"{salary_min}-{salary_max or ''}"
        
        url = f"{base_url}?q={quote_plus(query)}"
        if location:
            url += f"&l={quote_plus(location)}"
        
        print(f"🌐 Navigating to: {url}")
        
        # Navigate with timeout and retry logic for Cloudflare
        max_retries = getattr(settings, "MAX_RETRIES", 3)
        cloudflare_retries = 0
        
        while True:
            try:
                # Navigate with more lenient wait strategy
                # Try multiple wait strategies in order of preference
                navigation_timeout = 30000  # 30 seconds
                navigation_success = False
                
                # Strategy 1: Try domcontentloaded (fastest, most reliable)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=navigation_timeout)
                    print("✓ Navigation completed (domcontentloaded)")
                    navigation_success = True
                except Exception as nav_error1:
                    print(f"⚠️  domcontentloaded timeout, trying 'load' strategy...")
                    
                    # Strategy 2: Try 'load' (waits for all resources)
                    try:
                        await page.goto(url, wait_until="load", timeout=navigation_timeout)
                        print("✓ Navigation completed (load)")
                        navigation_success = True
                    except Exception as nav_error2:
                        print(f"⚠️  load timeout, trying 'commit' strategy...")
                        
                        # Strategy 3: Try 'commit' (just waits for navigation to start)
                        try:
                            await page.goto(url, wait_until="commit", timeout=10000)
                            print("✓ Navigation started (commit)")
                            # Give it a bit more time to load
                            await page.wait_for_timeout(5000)
                            navigation_success = True
                        except Exception as nav_error3:
                            # All strategies failed, but check if we got any content
                            print(f"⚠️  All navigation strategies failed, checking if we got content anyway...")
                            navigation_success = False
                
                # Wait for page to potentially load more content
                await page.wait_for_timeout(random.uniform(3000, 5000))
                
                # Try to wait for job listings if they exist (with timeout)
                try:
                    # Wait for either job cards or Cloudflare challenge
                    await page.wait_for_selector(
                        'div[data-jk], div.job_seen_beacon, #challenge-form, .cf-browser-verification',
                        timeout=5000,
                        state='attached'
                    )
                except Exception:
                    # Selector not found - might be Cloudflare or page still loading
                    pass
                
                # Get page content to check for Cloudflare
                page_html = await page.content()
                
                # Check for Cloudflare blocking (more comprehensive detection)
                has_cloudflare_indicators = (
                    "Checking your browser" in page_html
                    or "Enable JavaScript and cookies to continue" in page_html
                    or "challenge-platform" in page_html
                    or "cf-browser-verification" in page_html
                    or "Just a moment" in page_html
                    or "Ray ID" in page_html  # Cloudflare Ray ID
                    or "cf-challenge" in page_html.lower()
                    or "challenge-form" in page_html.lower()
                )
                
                # Check for Indeed content (more comprehensive)
                has_indeed_content = (
                    'id="mosaic-provider-jobcards"' in page_html
                    or 'class="jobsearch-ResultsList"' in page_html
                    or 'data-jk=' in page_html
                    or 'class="job_seen_beacon"' in page_html
                    or 'indeed.com/jobs' in page_html
                    or 'jobTitle' in page_html
                )
                
                # Also check page title
                try:
                    page_title = await page.title()
                    if "Just a moment" in page_title or "Checking" in page_title:
                        has_cloudflare_indicators = True
                except Exception:
                    pass
                
                is_actually_blocked = has_cloudflare_indicators and not has_indeed_content
                
                # If we have Indeed content, we're good (even if there are some Cloudflare indicators)
                if has_indeed_content:
                    is_actually_blocked = False
                    print("✓ Indeed content detected - proceeding with scraping")
                
                if not is_actually_blocked:
                    # Success - no Cloudflare block
                    break
                
                # Cloudflare detected - retry logic
                if cloudflare_retries >= max_retries:
                    try:
                        # Save debug HTML
                        import os
                        debug_path = '/tmp/indeed_playwright_debug.html'
                        with open(debug_path, 'w', encoding='utf-8') as f:
                            f.write(page_html)
                        print(f"⚠️  Cloudflare block detected. Debug HTML saved to {debug_path}")
                    except Exception:
                        pass
                    raise CloudflareBlockedError(
                        f"Indeed blocked by Cloudflare (captcha/turnstile). "
                        f"Tried {cloudflare_retries + 1} times. "
                        f"Solutions: 1) Configure PROXY_URL in .env 2) Wait and retry 3) Use residential proxy"
                    )
                
                # Retry with backoff
                backoff = random.uniform(
                    getattr(settings, "BACKOFF_MIN", 2.0),
                    getattr(settings, "BACKOFF_MAX", 8.0)
                ) * (1 + 0.5 * cloudflare_retries)
                
                print(f"⚠️  [PLAYWRIGHT] Cloudflare detected, retry {cloudflare_retries + 1}/{max_retries}, waiting {backoff:.1f}s...")
                
                # Perform human-like interactions
                if getattr(settings, "HUMANIZE", True):
                    await _perform_human_interactions_playwright(page)
                
                # Clear cookies and wait
                await context.clear_cookies()
                await asyncio.sleep(backoff)
                
                # Close and recreate browser context for fresh start
                try:
                    await page.close()
                    await context.close()
                    await browser.close()
                    _browser = None
                    _context = None
                    print("✓ [PLAYWRIGHT] Old browser cleaned up, creating new one...")
                except Exception as cleanup_err:
                    print(f"⚠️  [PLAYWRIGHT] Error during cleanup: {cleanup_err}")
                
                # Create new browser for retry
                browser, context = await get_browser(force_new=True)
                page = await context.new_page()
                
                cloudflare_retries += 1
                
            except CloudflareBlockedError:
                raise
            except Exception as nav_error:
                error_str = str(nav_error).lower()
                
                # Check if it's a timeout error
                if "timeout" in error_str:
                    # Even on timeout, check if we got any content
                    try:
                        page_html = await page.content()
                        # Quick check for Cloudflare
                        if "challenge-platform" in page_html or "Just a moment" in page_html:
                            # It's Cloudflare, treat as block
                            is_actually_blocked = True
                            print("⚠️  [PLAYWRIGHT] Timeout likely due to Cloudflare challenge")
                            # Continue to Cloudflare retry logic below
                        elif 'data-jk=' in page_html or 'job_seen_beacon' in page_html:
                            # We got content despite timeout, proceed
                            print("✓ Got content despite timeout, proceeding...")
                            is_actually_blocked = False
                            break
                        else:
                            # Unknown timeout - retry
                            if cloudflare_retries >= max_retries:
                                raise Exception(f"Navigation timeout after {max_retries} retries. This may indicate: 1) Slow network 2) Cloudflare blocking 3) Proxy issues. Error: {nav_error}")
                            print(f"⚠️  [PLAYWRIGHT] Navigation timeout, retry {cloudflare_retries + 1}/{max_retries}...")
                            cloudflare_retries += 1
                            await asyncio.sleep(random.uniform(3.0, 6.0))
                            # Recreate browser for retry
                            try:
                                await page.close()
                                await context.close()
                                await browser.close()
                                _browser = None
                                _context = None
                                browser, context = await get_browser(force_new=True)
                                page = await context.new_page()
                            except Exception:
                                pass
                            continue
                    except Exception as content_check_error:
                        print(f"⚠️  [PLAYWRIGHT] Error checking page content: {content_check_error}")
                
                # For other errors, retry
                if cloudflare_retries >= max_retries:
                    raise Exception(f"Navigation failed after {max_retries} retries: {nav_error}")
                print(f"⚠️  [PLAYWRIGHT] Navigation error: {nav_error}, retrying...")
                cloudflare_retries += 1
                await asyncio.sleep(random.uniform(2.0, 5.0))
                
                # Recreate browser for retry
                try:
                    await page.close()
                    await context.close()
                    await browser.close()
                    _browser = None
                    _context = None
                    browser, context = await get_browser(force_new=True)
                    page = await context.new_page()
                except Exception:
                    pass
        
        # Progressive scroll to load more jobs
        await _progressive_scroll_playwright(page)
        
        # Get page content
        content = await page.content()
        
        # Parse with BeautifulSoup
        soup = BeautifulSoup(content, "html.parser")
        
        # Extract job cards (reuse existing extraction logic)
        job_cards = _find_job_cards_indeed(soup)
        
        print(f"📋 Found {len(job_cards)} job cards")
        
        # Extract job data from listing page
        jobs = []
        for card in job_cards[:max_results * 2]:  # Get more cards to account for filtering
            try:
                job = _extract_job_from_card(card, query, location)
                if job and job.title and job.url:
                    # Enhanced extraction: Visit individual job page for complete data
                    print(f"  → Fetching complete data from job page: {job.title}")
                    try:
                        enhanced_job = await _extract_complete_job_details_from_url_playwright(page, job)
                        if enhanced_job:
                            job = enhanced_job
                        # Add delay to be respectful to Indeed's servers
                        await asyncio.sleep(random.uniform(1.5, 3.0))
                    except Exception as enhance_error:
                        print(f"⚠️  Error enhancing job details: {enhance_error}")
                        # Continue with basic job data if enhancement fails
                    
                    # Apply filters
                    if _should_include_job(job, job_type, salary_min, salary_max, experience_level, employment_type, days_old):
                        jobs.append(job)
                        
                        # Stop if we've reached max_results
                        if len(jobs) >= max_results:
                            break
            except Exception as e:
                print(f"⚠️  Error extracting job from card: {e}")
                continue
        
        print(f"✓ Extracted {len(jobs)} jobs")
        return jobs
        
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Error during scraping: {error_msg}")
        import traceback
        print(f"   Full traceback:\n{traceback.format_exc()}")
        # Re-raise with more context if it's a browser-related error
        if "browser" in error_msg.lower() or "playwright" in error_msg.lower():
            raise Exception(f"Playwright browser error: {error_msg}. Ensure Playwright browsers are installed: python -m playwright install chromium")
        raise
    finally:
        try:
            await page.close()
        except:
            pass


async def _progressive_scroll_playwright(page: Page):
    """Gradually scroll the page to trigger lazy-loaded elements."""
    try:
        total = await page.evaluate("document.body.scrollHeight") or 2000
        steps = random.randint(4, 7)
        for i in range(steps):
            frac = (i + 1) / steps
            y = int(total * frac)
            await page.evaluate(f"window.scrollTo(0, {y})")
            await asyncio.sleep(random.uniform(0.25, 0.7))
    except Exception:
        pass


async def _perform_human_interactions_playwright(page: Page):
    """Perform human-like interactions to avoid bot detection."""
    try:
        # Random mouse movements
        for _ in range(random.randint(2, 4)):
            x = random.randint(100, 1200)
            y = random.randint(100, 600)
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.1, 0.3))
        
        # Random scrolls
        scroll_amount = random.randint(200, 800)
        await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
        await asyncio.sleep(random.uniform(0.5, 1.5))
        
        # Scroll back a bit
        await page.evaluate(f"window.scrollBy(0, -{scroll_amount // 2})")
        await asyncio.sleep(random.uniform(0.3, 0.8))
        
    except Exception as e:
        print(f"⚠️  Error during human interactions: {e}")


def _find_job_cards_indeed(soup: BeautifulSoup) -> List:
    """Find job cards using multiple Indeed-specific selectors."""
    cards = []
    
    # Try multiple selectors
    selectors = [
        'div[data-jk]',  # Most reliable - Indeed's job ID attribute
        'div.job_seen_beacon',
        'div[class*="job_seen_beacon"]',
        'div[class*="jobCard"]',
        'div[class*="result"]',
        'div[data-testid="job-card"]',
    ]
    
    for selector in selectors:
        found = soup.select(selector)
        if found:
            cards.extend(found)
            break
    
    # Remove duplicates based on data-jk attribute
    seen = set()
    unique_cards = []
    for card in cards:
        jk = card.get('data-jk')
        if jk and jk not in seen:
            seen.add(jk)
            unique_cards.append(card)
        elif not jk:
            # If no data-jk, use a hash of the card content
            card_hash = hash(str(card))
            if card_hash not in seen:
                seen.add(card_hash)
                unique_cards.append(card)
    
    return unique_cards


def _extract_job_from_card(card, query: str, location: Optional[str]) -> Optional[Job]:
    """Extract comprehensive job data from a job card element (matches Selenium version)."""
    try:
        # Extract job ID
        job_id = card.get('data-jk', '')
        
        # Extract title
        title = _extract_title_indeed(card)
        if not title or len(title) < 3:
            return None
        
        # Extract company information (name and URL)
        company, company_url = _extract_company_info_indeed(card)
        
        # Extract location and remote type
        job_location, remote_type = _extract_location_info_indeed(card)
        
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
        job_url = _extract_job_url_indeed(card, job_id)
        
        # Extract skills, requirements, and benefits
        skills = _extract_skills_indeed(card)
        requirements = _extract_requirements_indeed(card)
        benefits = _extract_benefits_indeed(card)
        
        # Extract industry and company size
        industry = _extract_industry_indeed(card)
        company_size = _extract_company_size_indeed(card)
        
        return Job(
            title=title,
            company=company or "Unknown",
            company_url=company_url,
            location=job_location or location or "Unknown",
            description=description or "",
            url=job_url or "",
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
        print(f"⚠️  Error extracting job data: {e}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
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
            if title and len(title) > 2:
                return title
    
    return None


def _extract_company_info_indeed(card) -> tuple[Optional[str], Optional[str]]:
    """Extract company name and URL from Indeed job card."""
    company = None
    company_url = None
    
    company_selectors = [
        'span[data-testid="company-name"]',
        'span.companyName',
        'span[class*="company"]',
        'a[data-testid="company-name"]',
        'div[class*="companyName"]',
        'a[class*="companyName"]',
        'a[class*="company"]',
        'a[class*="jcs-CompanyLink"]',
    ]
    
    for selector in company_selectors:
        company_elem = card.select_one(selector)
        if company_elem:
            company = company_elem.get_text(strip=True)
            if company and len(company) > 1:
                if company_elem.name == 'a':
                    href = company_elem.get('href', '')
                    if href:
                        company_url = f"https://www.indeed.com{href}" if href.startswith('/') else href
                else:
                    link_elem = company_elem.find('a')
                    if link_elem:
                        href = link_elem.get('href', '')
                        if href:
                            company_url = f"https://www.indeed.com{href}" if href.startswith('/') else href
                break
    
    return company, company_url


def _extract_location_info_indeed(card) -> tuple[Optional[str], Optional[str]]:
    """Extract location and remote type from Indeed job card."""
    location = None
    remote_type = None
    
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
    """Extract salary range from Indeed job card."""
    
    salary_selectors = [
        'div[data-testid="attribute_snippet_test_salary"]',
        'span[data-testid="attribute_snippet_test_salary"]',
        'div[class*="attribute_snippet"]',
        'span[class*="attribute_snippet"]',
        'div[class*="salary"]',
        'span[class*="salary"]',
        'div[class*="pay"]',
        'span[class*="pay"]',
    ]
    
    for selector in salary_selectors:
        salary_elem = card.select_one(selector)
        if salary_elem:
            salary_text = salary_elem.get_text(strip=True)
            if '$' in salary_text or 'salary' in salary_text.lower():
                return salary_text
    
    # Pattern matching fallback
    text_content = card.get_text()
    salary_patterns = [
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        r'\$[\d,]+(?:K|k)?\s*/\s*(?:year|hour)',
        r'\$[\d,]+(?:K|k)?\s*per\s*(?:year|hour)',
    ]
    
    for pattern in salary_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            return match.group().strip()
    
    return None


def _extract_job_types_indeed(card) -> tuple[Optional[str], Optional[str]]:
    """Extract job type and employment type from Indeed job card."""
    job_type = None
    employment_type = None
    
    text_content = card.get_text().lower()
    
    # Employment type patterns
    if 'full-time' in text_content or 'fulltime' in text_content:
        employment_type = 'Full-Time'
    elif 'part-time' in text_content or 'parttime' in text_content:
        employment_type = 'Part-Time'
    elif 'contract' in text_content:
        employment_type = 'Contract'
    elif 'internship' in text_content or 'intern' in text_content:
        employment_type = 'Internship'
    
    # Job type patterns
    if 'permanent' in text_content:
        job_type = 'Permanent'
    elif 'temporary' in text_content:
        job_type = 'Temporary'
    elif 'contract' in text_content:
        job_type = 'Contract'
    
    return job_type, employment_type


def _extract_experience_level_indeed(card) -> Optional[str]:
    """Extract experience level from Indeed job card."""
    # Indeed uses URL-based filtering, so we trust their classification
    return None


def _extract_posted_date_indeed(card) -> Optional[str]:
    """Extract posted date from Indeed job card."""
    
    text_content = card.get_text()
    
    date_patterns = [
        r'\d+\s+(?:days?|hours?|minutes?)\s+ago',
        r'Posted\s+\d+\s+(?:days?|hours?)\s+ago',
        r'Just\s+posted',
        r'Today',
        r'Yesterday',
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            return match.group().strip()
    
    return None


def _extract_description_indeed(card) -> Optional[str]:
    """Extract job description from Indeed job card."""
    description_selectors = [
        'div[class*="summary"]',
        'div[class*="snippet"]',
        'span[class*="summary"]',
        'span[class*="snippet"]',
        'div[class*="job-snippet"]',
        'span[class*="job-snippet"]',
    ]
    
    for selector in description_selectors:
        desc_elem = card.select_one(selector)
        if desc_elem:
            description = desc_elem.get_text(strip=True)
            if description and len(description) > 10:
                return description
    
    return None


def _extract_job_url_indeed(card, job_id: str) -> Optional[str]:
    """Extract job URL from Indeed job card."""
    title_elem = card.select_one('a[class*="jcs-JobTitle"], a[class*="jobTitle"], h2 a, h3 a')
    if title_elem and title_elem.get('href'):
        href = title_elem.get('href')
        return f"https://www.indeed.com{href}" if href.startswith('/') else href
    
    if job_id:
        return f"https://www.indeed.com/viewjob?jk={job_id}"
    
    return None


def _extract_skills_indeed(card) -> List[str]:
    """Extract skills from Indeed job card."""
    # Skills are typically not in the card preview, would need full job page
    return []


def _extract_requirements_indeed(card) -> List[str]:
    """Extract requirements from Indeed job card."""
    # Requirements are typically not in the card preview, would need full job page
    return []


def _extract_benefits_indeed(card) -> List[str]:
    """Extract benefits from Indeed job card."""
    # Benefits are typically not in the card preview, would need full job page
    return []


def _extract_industry_indeed(card) -> Optional[str]:
    """Extract industry from Indeed job card."""
    # Industry is typically not in the card preview
    return None


def _extract_company_size_indeed(card) -> Optional[str]:
    """Extract company size from Indeed job card."""
    # Company size is typically not in the card preview
    return None


# Full page extraction functions (for enhanced details from individual job pages)
def _extract_salary_from_full_page_improved(soup) -> Optional[str]:
    """Extract salary from Indeed's full job page."""
    text_content = soup.get_text()
    
    salary_patterns = [
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?\s*(?:a\s+year|per\s+year|annually|yearly)',
        r'\$[\d,]+(?:K|k)?\s*-\s*\$[\d,]+(?:K|k)?',
        r'\$[\d,]+(?:K|k)?\s*/\s*(?:year|hour)',
        r'\$[\d,]+(?:K|k)?\s*per\s*(?:year|hour)',
    ]
    
    for pattern in salary_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            return match.group().strip()
    
    return None


def _extract_employment_from_full_page_improved(soup) -> Optional[str]:
    """Extract employment type from Indeed's full job page."""
    text_content = soup.get_text().lower()
    
    employment_patterns = {
        'full-time': ['full-time', 'full time', 'fulltime', 'permanent', 'regular', 'ft'],
        'part-time': ['part-time', 'part time', 'parttime', 'pt'],
        'contract': ['contract', 'contractor', 'freelance', 'consultant'],
        'internship': ['internship', 'intern', 'trainee', 'co-op'],
        'temporary': ['temporary', 'temp', 'seasonal']
    }
    
    for emp_type, patterns in employment_patterns.items():
        if any(pattern in text_content for pattern in patterns):
            return emp_type.title()
    
    return None


def _extract_date_from_full_page_improved(soup) -> Optional[str]:
    """Extract posted date from Indeed's full job page."""
    text_content = soup.get_text()
    
    date_patterns = [
        r'\d+\s+(?:days?|hours?|minutes?)\s+ago',
        r'Posted\s+\d+\s+(?:days?|hours?)\s+ago',
        r'Just\s+posted',
        r'Today',
        r'Yesterday',
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            return match.group().strip()
    
    return None


def _extract_description_from_full_page_improved(soup) -> Optional[str]:
    """Extract full job description from Indeed's full job page."""
    desc_selectors = [
        'div[class*="jobsearch-jobDescriptionText"]',
        'div[class*="jobDescriptionText"]',
        'div[class*="jobDescription"]',
        'div[class*="description"]',
        'div[id*="jobDescriptionText"]',
    ]
    
    for selector in desc_selectors:
        desc_elem = soup.select_one(selector)
        if desc_elem:
            description = desc_elem.get_text(strip=True)
            if description and len(description) > 100:
                description = re.sub(r'\s+', ' ', description).strip()
                return description
    
    return None


def _extract_experience_from_full_page_improved(soup) -> Optional[str]:
    """Extract experience level from Indeed's full job page."""
    text_content = soup.get_text().lower()
    
    experience_patterns = {
        'entry': ['entry level', 'junior', 'assistant', 'intern'],
        'mid': ['mid-level', 'mid level', 'intermediate'],
        'senior': ['senior', 'lead', 'principal', 'architect'],
        'executive': ['executive', 'director', 'vp', 'vice president', 'c-level']
    }
    
    for exp_level, patterns in experience_patterns.items():
        if any(pattern in text_content for pattern in patterns):
            return exp_level.title()
    
    return None


def _extract_benefits_from_full_page(soup) -> Optional[List[str]]:
    """Extract benefits from Indeed's full job page."""
    benefits = []
    text_content = soup.get_text().lower()
    
    benefit_keywords = [
        'health insurance', 'dental', 'vision', '401k', 'retirement',
        'vacation', 'pto', 'paid time off', 'flexible schedule',
        'remote work', 'work from home', 'stock options', 'bonus',
        'professional development', 'training', 'gym membership'
    ]
    
    for benefit in benefit_keywords:
        if benefit in text_content:
            benefits.append(benefit.title())
    
    return benefits[:10] if benefits else None


def _extract_requirements_from_full_page(soup) -> Optional[List[str]]:
    """Extract requirements from Indeed's full job page."""
    requirements = []
    text_content = soup.get_text()
    
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
    
    return requirements[:5] if requirements else None


def _extract_skills_from_full_page(soup) -> Optional[List[str]]:
    """Extract skills from Indeed's full job page."""
    skills = []
    text_content = soup.get_text().lower()
    
    skill_keywords = [
        'python', 'java', 'javascript', 'typescript', 'react', 'angular', 'vue', 'node.js',
        'php', 'ruby', 'go', 'rust', 'c++', 'c#', 'swift', 'kotlin', 'scala', 'r',
        'sql', 'mysql', 'postgresql', 'mongodb', 'redis', 'elasticsearch', 'aws', 'azure',
        'docker', 'kubernetes', 'jenkins', 'git', 'linux', 'unix', 'html', 'css',
    ]
    
    for skill in skill_keywords:
        if skill in text_content and skill.title() not in skills:
            skills.append(skill.title())
    
    return skills[:10] if skills else None


def _extract_industry_from_full_page(soup) -> Optional[str]:
    """Extract industry from Indeed's full job page."""
    text_content = soup.get_text().lower()
    
    industries = [
        'technology', 'healthcare', 'finance', 'education', 'retail',
        'manufacturing', 'consulting', 'nonprofit', 'government',
        'media', 'entertainment', 'real estate', 'automotive'
    ]
    
    for industry in industries:
        if industry in text_content:
            return industry.title()
    
    return None


def _extract_company_size_from_full_page(soup) -> Optional[str]:
    """Extract company size from Indeed's full job page."""
    text_content = soup.get_text()
    
    size_patterns = [
        r'(\d+)\s*-\s*(\d+)\s*employees',
        r'(\d+)\+?\s*employees',
    ]
    
    for pattern in size_patterns:
        match = re.search(pattern, text_content, re.IGNORECASE)
        if match:
            return match.group().strip()
    
    text_lower = text_content.lower()
    if 'startup' in text_lower:
        return 'Startup'
    elif 'small business' in text_lower:
        return 'Small Business'
    elif 'enterprise' in text_lower or 'fortune 500' in text_lower:
        return 'Enterprise'
    
    return None


async def _extract_complete_job_details_from_url_playwright(page: Page, job: Job) -> Optional[Job]:
    """Extract complete job details by navigating to the individual job page URL (synchronous, one at a time)."""
    if not job.url:
        return job
    
    original_url = page.url
    
    try:
        # Navigate to the individual job page
        print(f"    → Navigating to: {job.url}")
        await page.goto(job.url, wait_until="domcontentloaded", timeout=30000)
        
        # Wait for page to load
        await page.wait_for_timeout(random.uniform(2000, 3000))
        
        # Wait for job content to load
        try:
            await page.wait_for_selector('div[class*="jobsearch-JobComponent"], body', timeout=10000)
        except:
            pass  # Continue even if selector not found
        
        # Get full page content
        full_page_content = await page.content()
        full_page_soup = BeautifulSoup(full_page_content, 'html.parser')
        
        # Extract enhanced details from the full page
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
        
        # Update job with enhanced details (only if not already present or significantly better)
        if enhanced_salary and (not job.salary_range or len(enhanced_salary) > len(job.salary_range or "")):
            job.salary_range = enhanced_salary
            print(f"    ✓ Enhanced salary: {enhanced_salary}")
        
        if enhanced_employment and not job.employment_type:
            job.employment_type = enhanced_employment
            print(f"    ✓ Enhanced employment: {enhanced_employment}")
        
        if enhanced_date and not job.posted_date:
            job.posted_date = enhanced_date
            print(f"    ✓ Enhanced date: {enhanced_date}")
        
        # Always update description with full job description from detail page
        if enhanced_description and len(enhanced_description) > 100:
            job.description = enhanced_description
            print(f"    ✓ Enhanced description: {len(enhanced_description)} characters")
        
        if enhanced_experience and not job.experience_level:
            job.experience_level = enhanced_experience
            print(f"    ✓ Enhanced experience: {enhanced_experience}")
        
        if enhanced_benefits and (not job.benefits or len(job.benefits or []) < len(enhanced_benefits)):
            job.benefits = enhanced_benefits
            print(f"    ✓ Enhanced benefits: {len(enhanced_benefits)} items")
        
        if enhanced_requirements and (not job.requirements or len(job.requirements or []) < len(enhanced_requirements)):
            job.requirements = enhanced_requirements
            print(f"    ✓ Enhanced requirements: {len(enhanced_requirements)} items")
        
        if enhanced_skills and (not job.skills or len(job.skills or []) < 3):
            job.skills = enhanced_skills
            print(f"    ✓ Enhanced skills: {len(enhanced_skills)} items")
        
        if enhanced_industry and not job.industry:
            job.industry = enhanced_industry
            print(f"    ✓ Enhanced industry: {enhanced_industry}")
        
        if enhanced_company_size and not job.company_size:
            job.company_size = enhanced_company_size
            print(f"    ✓ Enhanced company size: {enhanced_company_size}")
        
    except Exception as e:
        print(f"    ⚠️  Error extracting enhanced details: {e}")
        # Continue with basic job data if enhancement fails
    finally:
        # Navigate back to the original search results page
        try:
            print(f"    ← Navigating back to search results")
            await page.goto(original_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1000)  # Brief wait for page to load
        except Exception as nav_error:
            print(f"    ⚠️  Warning: Could not navigate back: {nav_error}")
    
    return job


def _should_include_job(
    job: Job,
    job_type: Optional[str],
    salary_min: Optional[int],
    salary_max: Optional[int],
    experience_level: Optional[str],
    employment_type: Optional[str],
    days_old: Optional[int]
) -> bool:
    """Check if job should be included based on filters."""
    # Job type filter
    if job_type:
        job_type_lower = job_type.lower()
        if job_type_lower in ['remote']:
            if job.remote_type and job.remote_type.lower() != 'remote':
                return False
        elif job_type_lower in ['hybrid']:
            if job.remote_type and job.remote_type.lower() != 'hybrid':
                return False
        elif job_type_lower in ['onsite', 'on-site']:
            if job.remote_type and job.remote_type.lower() not in ['on-site', 'onsite']:
                return False
    
    # Employment type filter
    if employment_type and job.employment_type:
        if employment_type.lower() not in job.employment_type.lower():
            return False
    
    # Experience level filter (if provided)
    if experience_level and job.experience_level:
        if experience_level.lower() not in job.experience_level.lower():
            return False
    
    # Salary filter (basic - would need parsing)
    # Note: Full salary parsing would require parsing the salary_range string
    
    # Days old filter (basic - would need date parsing)
    # Note: Full date parsing would require parsing the posted_date string
    
    return True

