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
    playwright = await async_playwright().start()
    
    # Launch browser with optimized settings for headless mode
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
    
    browser, context = await get_browser()
    page: Page = await context.new_page()
    
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
        
        # Extract job data
        jobs = []
        for card in job_cards[:max_results]:
            try:
                job = _extract_job_from_card(card, query, location)
                if job:
                    # Apply filters
                    if _should_include_job(job, job_type, salary_min, salary_max, experience_level, employment_type, days_old):
                        jobs.append(job)
            except Exception as e:
                print(f"⚠️  Error extracting job from card: {e}")
                continue
        
        print(f"✓ Extracted {len(jobs)} jobs")
        return jobs
        
    except Exception as e:
        print(f"❌ Error during scraping: {e}")
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
    """Extract job data from a job card element."""
    try:
        # Extract job ID
        job_id = card.get('data-jk', '')
        
        # Extract title
        title_elem = card.select_one('a[class*="jcs-JobTitle"], a[class*="jobTitle"], h2 a, h3 a')
        title = title_elem.get_text(strip=True) if title_elem else None
        
        if not title:
            return None
        
        # Extract company
        company_elem = card.select_one('span[class*="company"], div[class*="company"], a[class*="company"]')
        company = company_elem.get_text(strip=True) if company_elem else None
        
        # Extract location
        location_elem = card.select_one('div[data-testid="text-location"], div.companyLocation')
        job_location = location_elem.get_text(strip=True) if location_elem else location
        
        # Extract job URL
        if title_elem and title_elem.get('href'):
            href = title_elem.get('href')
            job_url = f"https://www.indeed.com{href}" if href.startswith('/') else href
        else:
            job_url = f"https://www.indeed.com/viewjob?jk={job_id}" if job_id else None
        
        # Extract salary (if available)
        salary_elem = card.select_one('span[class*="salary"], div[class*="salary"]')
        salary = salary_elem.get_text(strip=True) if salary_elem else None
        
        # Extract description snippet
        desc_elem = card.select_one('div[class*="summary"], div[class*="snippet"]')
        description = desc_elem.get_text(strip=True) if desc_elem else None
        
        return Job(
            title=title,
            company=company or "Unknown",
            location=job_location or location or "Unknown",
            url=job_url or "",
            description=description or "",
            salary=salary,
            source="indeed",
            job_id=job_id
        )
    except Exception as e:
        print(f"⚠️  Error extracting job data: {e}")
        return None


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
    # Add filter logic here (similar to Selenium version)
    # For now, return True for all jobs
    return True

