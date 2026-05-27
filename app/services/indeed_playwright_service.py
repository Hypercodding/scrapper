"""
Playwright-based Indeed scraper - Optimized for best performance with proxy rotation.

Benefits:
- No ChromeDriver version issues (bundles its own browser)
- Better resource management
- More stable in headless mode
- Better error handling
- Fast navigation with smart waiting strategies
- Proxy rotation support for avoiding blocks
- Smart proxy rotation per job fetch to avoid detection
"""

import time
import random
import asyncio
import re
from datetime import date, timedelta
from typing import Optional, List, Dict, Tuple
from urllib.parse import quote_plus, urlparse
from bs4 import BeautifulSoup
from app.models.job_model import Job
from app.core import metrics
from app.core.config import settings
from app.core.proxy_manager import ProxyManager
from app.services.indeed_url_builder import build_indeed_search_url


# Module-level list of jks the worker failed to fully enrich during the last
# scrape. Read by app.workers.tasks.scrape_indeed_task to enqueue per-jk
# retries via the scrape.indeed.retry queue (wired in Step 2 of the rollout).
# Reset at the start of every scrape_indeed_playwright() call.
_LAST_FAILED_JKS: List[str] = []


def get_last_failed_jks() -> List[str]:
    """Return (and clear) the failed-jk list from the most recent scrape."""
    global _LAST_FAILED_JKS
    out = list(_LAST_FAILED_JKS)
    _LAST_FAILED_JKS = []
    return out


async def scrape_single_jk_with_fresh_session(
    jk: str,
    query: str = "",
    location: Optional[str] = None,
) -> Optional[Job]:
    """Fetch one Indeed /viewjob page in an isolated browser session.

    Used by the per-jk retry queue (`scrape_indeed_single_jk_task`) when the
    primary scrape couldn't extract a full description for this jk. Forces
    a fresh proxy rotation so the retry doesn't reuse the same egress IP
    Cloudflare likely just blocked.

    Returns a `Job` with `detail_fetch_status="ok"` on success; `None` or a
    Job with non-ok status on failure. Caller decides what to record.
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise ImportError(
            "Playwright is not installed. Install with: "
            "pip install playwright && python -m playwright install chromium"
        )

    print(f"🎯 Single-jk fetch: jk={jk} (fresh session)")
    pw = None
    browser = None
    context = None
    page = None
    try:
        pw = await async_playwright().start()
        # Force-rotate: this retry exists *because* the previous attempt was
        # blocked, so reusing the same proxy is pointless.
        proxy_config = _rotate_proxy_on_error()
        browser, context = await _launch_browser_with_context(pw, proxy_config)
        page = await context.new_page()

        viewjob_url = f"https://www.indeed.com/viewjob?jk={jk}"
        # Stub job — the extractor enriches in place. `url` and `job_key`
        # are what _extract_complete_job_details_from_url_playwright reads.
        job = Job(
            title="(pending)",
            url=viewjob_url,
            job_key=jk,
            job_id=jk,
        )

        enhanced = await _extract_complete_job_details_from_url_playwright(
            page, job,
            original_search_url=f"https://www.indeed.com/jobs?q={query}",
            skip_nav_back=True,
        )

        if enhanced and enhanced.detail_fetch_status == "ok" and enhanced.description:
            metrics.incr("per_jk_retry_success")
            # Title may still be "(pending)" if the extractor didn't refresh
            # it — try to recover from the page H1.
            if enhanced.title == "(pending)":
                try:
                    h1 = await page.locator("h1").first.text_content(timeout=2000)
                    if h1:
                        enhanced.title = h1.strip()
                except Exception:
                    pass
            print(f"  ✓ Single-jk fetch succeeded: {len(enhanced.description)} chars")
            return enhanced

        print(f"  ⨯ Single-jk fetch failed: status={enhanced.detail_fetch_status if enhanced else 'no-job'}")
        return enhanced  # caller checks .detail_fetch_status

    except Exception as exc:
        print(f"  ⨯ Single-jk fetch crashed: {exc}")
        return None
    finally:
        try:
            if page and not page.is_closed():
                await page.close()
        except Exception:
            pass
        await _close_browser(browser, context)
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("⚠️  Playwright not installed. Install with: pip install playwright && python -m playwright install chromium")

try:
    from playwright_stealth import Stealth
    STEALTH_AVAILABLE = True
    # Create a global stealth instance with comprehensive settings
    _stealth = Stealth(
        chrome_app=True,
        chrome_csi=True,
        chrome_load_times=True,
        chrome_runtime=True,  # Enable chrome.runtime
        navigator_webdriver=True,
        navigator_plugins=True,
        navigator_languages=True,
        navigator_platform=True,
        navigator_vendor=True,
        navigator_user_agent=True,
        navigator_hardware_concurrency=True,
        navigator_permissions=True,
        webgl_vendor=True,
        media_codecs=True,
        hairline=True,
        iframe_content_window=True,
    )
    print("✓ playwright-stealth loaded successfully")
except ImportError as ie:
    STEALTH_AVAILABLE = False
    _stealth = None
    print(f"ℹ️  playwright-stealth not installed: {ie}. Install with: pip install playwright-stealth")
except Exception as stealth_error:
    STEALTH_AVAILABLE = False
    _stealth = None
    print(f"⚠️  Error initializing playwright-stealth: {stealth_error}")


def _get_indeed_job_type_filter(job_type: str) -> Optional[str]:
    """Get Indeed job type filter parameter (remote/hybrid/onsite).
    
    Indeed uses location parameter 'l=remote' for remote jobs to search all over United States.
    For hybrid and onsite, we rely on post-scraping filtering as Indeed
    doesn't have explicit URL parameters for these (uses jt parameter for employment type).
    """
    job_type = job_type.lower().strip()
    
    # Indeed uses 'l=remote' for remote job searches
    # This bypasses location filtering and searches all over United States
    if job_type in ['remote', 'work from home', 'wfh', 'telecommute', 'telework']:
        return 'remote'
    
    # For hybrid and onsite, Indeed doesn't have URL-level filters via location
    # We'll rely on post-scraping filtering based on job.remote_type
    # Return None to indicate no location parameter needed
    return None


def _format_location_for_indeed(location: str) -> str:
    """Format location for Indeed search."""
    location = location.strip()
    
    # Handle remote job types
    if location.lower() in ['remote', 'work from home', 'wfh']:
        return 'remote'
    
    # Indeed accepts various location formats
    # URL encode spaces and special characters
    return quote_plus(location)


# Proxy state — shared across calls within this process (proxy manager is lightweight)
_proxy_manager: Optional[ProxyManager] = None
_current_proxy: Optional[str] = None
# In-process rate-limit timestamp (the Redis HostThrottle is the real global limiter)
_last_fetch: float = 0


def _parse_proxy_for_playwright(proxy_url: str) -> Optional[Dict]:
    """
    Parse a proxy URL into Playwright's proxy format.
    
    Args:
        proxy_url: Proxy URL in format http://user:pass@host:port
        
    Returns:
        Dict with 'server', 'username', 'password' for Playwright
    """
    if not proxy_url:
        return None
    
    try:
        parsed = urlparse(proxy_url)
        proxy_config = {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        }
        
        if parsed.username:
            proxy_config["username"] = parsed.username
        if parsed.password:
            proxy_config["password"] = parsed.password
            
        return proxy_config
    except Exception as e:
        print(f"⚠️  Error parsing proxy URL: {e}")
        return None


def _init_proxy_manager() -> Optional[ProxyManager]:
    """Initialize the proxy manager from config settings."""
    global _proxy_manager
    
    if _proxy_manager is not None:
        return _proxy_manager
    
    # Get proxy URLs from config
    proxy_urls_str = getattr(settings, "PROXY_URLS", "")
    
    if not proxy_urls_str:
        print("ℹ️  No proxy URLs configured - using direct connection")
        return None
    
    # Parse comma-separated proxy URLs
    proxy_urls = [url.strip() for url in proxy_urls_str.split(",") if url.strip()]
    
    if not proxy_urls:
        print("ℹ️  No valid proxy URLs found - using direct connection")
        return None
    
    try:
        rotation_interval = getattr(settings, "PROXY_ROTATION_INTERVAL", 240)
        _proxy_manager = ProxyManager(proxy_urls, rotation_interval)
        print(f"✓ Proxy manager initialized with {len(proxy_urls)} proxies (rotation every {rotation_interval}s)")
        return _proxy_manager
    except Exception as e:
        print(f"⚠️  Error initializing proxy manager: {e}")
        return None


def _get_current_proxy_config() -> Optional[Dict]:
    """Get the current proxy configuration for Playwright."""
    global _current_proxy
    
    proxy_manager = _init_proxy_manager()
    if not proxy_manager:
        return None
    
    # Check if we should rotate based on time
    if proxy_manager.should_rotate():
        proxy_manager.rotate_proxy()
    
    _current_proxy = proxy_manager.get_current_proxy()
    return _parse_proxy_for_playwright(_current_proxy)


def _rotate_proxy_on_error() -> Optional[Dict]:
    """Force rotate to next proxy after an error."""
    global _current_proxy
    
    if not _proxy_manager:
        return None
    
    # Mark current proxy as failed
    _proxy_manager.mark_proxy_failure()
    
    # Force rotate to next proxy
    _current_proxy = _proxy_manager.rotate_proxy(force=True)
    return _parse_proxy_for_playwright(_current_proxy)


def _mark_proxy_success():
    """Mark the current proxy as successful."""
    if _proxy_manager and _current_proxy:
        _proxy_manager.mark_proxy_success(_current_proxy)


class CloudflareBlockedError(Exception):
    """Raised when Indeed returns a Cloudflare/turnstile block page."""
    pass


_BROWSER_ARGS = [
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


async def _launch_browser_with_context(pw: "Playwright", proxy_config=None) -> tuple:
    """
    Launch a fresh Chromium browser and context on an existing Playwright instance.
    Caller owns and must close both objects.
    Returns (browser, context).
    """
    try:
        browser = await pw.chromium.launch(headless=True, args=_BROWSER_ARGS)
    except Exception as e:
        raise Exception(
            f"Failed to launch Chromium: {e}. Run: python -m playwright install chromium"
        )

    accept_lang = getattr(settings, "ACCEPT_LANGUAGE", "en-US,en;q=0.9") or "en-US,en;q=0.9"
    locale = accept_lang.split(",")[0].strip() if accept_lang else "en-US"

    context_options: dict = {
        "viewport": {"width": 1280, "height": 720},
        "user_agent": settings.USER_AGENT,
        "locale": locale,
        "timezone_id": "America/New_York",
        "java_script_enabled": True,
        "bypass_csp": True,
        "ignore_https_errors": True,
        "accept_downloads": False,
    }
    if proxy_config:
        context_options["proxy"] = proxy_config

    context = await browser.new_context(**context_options)

    if STEALTH_AVAILABLE and _stealth:
        await _stealth.apply_stealth_async(context)
        print("✓ Playwright-stealth applied")
    else:
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            window.chrome = {runtime: {}, loadTimes: function() {}, csi: function() {}, app: {}};
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
            Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
            Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
        """)

    return browser, context


async def _close_browser(browser, context) -> None:
    """Close browser context and browser instance, ignoring errors."""
    for obj, method_name in [(context, "close"), (browser, "close")]:
        if obj:
            try:
                await getattr(obj, method_name)()
            except Exception:
                pass


def get_proxy_stats() -> Optional[Dict]:
    """Return proxy statistics for monitoring, or None if no proxy manager."""
    if not _proxy_manager:
        return None
    return _proxy_manager.get_proxy_stats()


def reset_proxy_manager_state():
    """Reset the proxy manager state."""
    global _proxy_manager, _current_proxy
    if _proxy_manager:
        _proxy_manager.reset_failures()
    _current_proxy = None


async def scrape_indeed_playwright(
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
    force_rotate_proxy: bool = False,
    sort: Optional[str] = None,
    radius: Optional[int] = None,
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

    global _last_fetch, _LAST_FAILED_JKS

    # Reset the per-scrape failed-jk collector so the next call (or a Celery
    # retry on the same worker process) doesn't leak ghosts from the previous run.
    _LAST_FAILED_JKS = []

    # In-process rate limiting (Redis HostThrottle handles global cross-replica limiting)
    now = time.monotonic()
    jitter = random.uniform(0, 0.75)
    wait = settings.MIN_DELAY + jitter - (now - _last_fetch)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_fetch = time.monotonic()

    print(f"🔍 Starting Indeed scrape: query='{query}', location='{location}'")

    # Each call owns its own Playwright + browser + context lifecycle.
    # concurrency=1 per worker process means no shared state is needed.
    pw = None
    browser = None
    context = None
    page = None

    try:
        pw = await async_playwright().start()
        proxy_config = _rotate_proxy_on_error() if force_rotate_proxy else _get_current_proxy_config()
        browser, context = await _launch_browser_with_context(pw, proxy_config)
        page = await context.new_page()

    except Exception as browser_error:
        import traceback
        error_msg = str(browser_error)
        print(f"❌ Failed to launch browser: {error_msg}")
        print(f"   Traceback: {traceback.format_exc()}")
        # Ensure partial resources are closed
        if pw:
            try:
                await _close_browser(browser, context)
                await pw.stop()
            except Exception:
                pass
        raise Exception(
            f"Failed to launch browser: {error_msg}. "
            "Ensure Playwright browsers are installed: python -m playwright install chromium"
        )

    try:
        # Build Indeed SERP URL via the shared builder. All filters (job_type,
        # employment_type, experience_level, days_old, salary_min, sort, radius)
        # now reach Indeed in its native encoding instead of being silently dropped.
        url = build_indeed_search_url(
            query=query,
            location=location,
            job_type=job_type,
            employment_type=employment_type,
            experience_level=experience_level,
            salary_min=salary_min,
            days_old=days_old,
            sort=sort,
            radius=radius,
            start=0,
        )
        print(f"🌐 Navigating to: {url}")
        metrics.incr("serp_attempts")

        # Save the original search URL (without any job view parameters)
        original_search_url = url
        
        # Navigate with retry logic for Cloudflare
        max_retries = getattr(settings, "MAX_RETRIES", 3)
        cloudflare_retries = 0
        direct_connection_tried = False  # fallback flag for proxy failures

        while True:
            try:
                # Optimized navigation strategy for Playwright
                navigation_timeout = 30000  # 30 seconds - optimized for direct connection
                navigation_success = False
                
                # Strategy 1: domcontentloaded (fastest, best for Playwright)
                try:
                    print(f"   Navigating (domcontentloaded, {navigation_timeout/1000}s timeout)...")
                    await page.goto(url, wait_until="domcontentloaded", timeout=navigation_timeout)
                    print("✓ Navigation completed")
                    navigation_success = True
                except Exception as nav_error1:
                    error_type = type(nav_error1).__name__
                    error_msg = str(nav_error1)
                    print(f"⚠️  domcontentloaded failed: {error_msg[:100]}...")
                    
                    # Strategy 2: commit + manual waiting (most reliable fallback)
                    try:
                        print(f"   Trying commit strategy...")
                        await page.goto(url, wait_until="commit", timeout=15000)
                        print("✓ Navigation started (commit)")
                        
                        # Wait for body element
                        await page.wait_for_selector('body', timeout=20000, state='attached')
                        print("✓ Body loaded")
                        
                        # Wait for job content
                        try:
                            await page.wait_for_selector(
                                'div[data-jk], div.job_seen_beacon, div[class*="job"]',
                                timeout=15000,
                                state='attached'
                            )
                            print("✓ Job content detected")
                        except:
                            # Give page time to load
                            await page.wait_for_timeout(3000)
                            body_text = await page.evaluate("document.body ? document.body.innerText.length : 0")
                            if body_text > 100:
                                print(f"✓ Page has content ({body_text} chars)")
                        
                        navigation_success = True
                    except Exception as commit_error:
                        print(f"⚠️  All navigation strategies failed: {str(commit_error)[:100]}")
                        navigation_success = False
                
                # Wait for page content
                if navigation_success:
                    await page.wait_for_timeout(2000)
                else:
                    # Try scrolling to trigger lazy loading
                    await page.wait_for_timeout(3000)
                    try:
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                        await page.wait_for_timeout(1500)
                    except:
                        pass
                
                # Wait for job listings
                try:
                    await page.wait_for_selector(
                        'div[data-jk], div.job_seen_beacon, #challenge-form',
                        timeout=15000,
                        state='attached'
                    )
                except:
                    await page.wait_for_timeout(2000)
                
                # Get page content
                page_html = await page.content()
                body_length = 0  # default; updated below

                # Check page state
                try:
                    page_title = await page.title()
                    current_url = page.url
                    body_exists = await page.evaluate("document.body !== null")
                    body_length = await page.evaluate("document.body ? document.body.innerText.length : 0")
                    
                    print(f"🔍 Page: '{page_title[:50]}', {len(page_html)} chars, body: {body_length} chars")
                    
                    # Wait for content if needed
                    if len(page_html) < 500 or body_length < 100:
                        print(f"⚠️  Waiting for content...")
                        for attempt in range(3):
                            await page.wait_for_timeout(3000)
                            page_html = await page.content()
                            body_length = await page.evaluate("document.body ? document.body.innerText.length : 0")
                            if len(page_html) > 1000 and body_length > 100:
                                print(f"✓ Content loaded")
                                break
                    
                    # Check for Chrome error pages
                    if 'chrome-error://' in current_url or 'chromewebdata' in current_url:
                        raise Exception("Network error - Chrome error page detected")
                    
                except Exception as check_err:
                    if "Network error" in str(check_err) or "Chrome error" in str(check_err):
                        raise
                    pass
                
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
                    metrics.incr("serp_success")

                # Detect proxy/network failure: completely empty page, no Cloudflare indicators.
                # An empty response (body=0 chars) is NOT a Cloudflare block — it means the proxy
                # accepted the TCP connection but returned no HTTP data.  Retry with direct connection.
                if not is_actually_blocked and len(page_html) < 200 and body_length == 0:
                    if proxy_config and not direct_connection_tried:
                        print("⚠️  Empty page detected — proxy non-functional, retrying with direct connection...")
                        try:
                            if page and not page.is_closed():
                                await page.close()
                        except Exception:
                            pass
                        await _close_browser(browser, context)
                        browser, context = await _launch_browser_with_context(pw, None)
                        page = await context.new_page()
                        proxy_config = None
                        direct_connection_tried = True
                        cloudflare_retries += 1
                        await asyncio.sleep(2.0)
                        continue
                    else:
                        raise Exception(
                            "Indeed returned an empty page on "
                            f"{'direct connection' if direct_connection_tried else 'proxy and direct connection'}. "
                            "Check network connectivity from the worker host."
                        )

                if not is_actually_blocked:
                    # Success - no Cloudflare block, mark proxy as successful
                    _mark_proxy_success()
                    break
                
                # Cloudflare detected - retry logic
                if cloudflare_retries >= max_retries:
                    raise CloudflareBlockedError(
                        f"Indeed blocked by Cloudflare. Tried {cloudflare_retries + 1} times. "
                        f"Try again later or use a different IP address."
                    )
                
                # Retry with backoff
                backoff = random.uniform(3.0, 6.0) * (1 + 0.5 * cloudflare_retries)
                print(f"⚠️  Cloudflare detected, retry {cloudflare_retries + 1}/{max_retries}, waiting {backoff:.1f}s...")
                
                # Perform human-like interactions
                if getattr(settings, "HUMANIZE", True):
                    await _perform_human_interactions_playwright(page)
                
                # Clear cookies and wait
                await context.clear_cookies()
                await asyncio.sleep(backoff)
                
                # Recreate browser with a rotated proxy for fresh start
                try:
                    if page and not page.is_closed():
                        await page.close()
                except Exception:
                    pass
                await _close_browser(browser, context)
                proxy_config = _rotate_proxy_on_error()
                browser, context = await _launch_browser_with_context(pw, proxy_config)
                page = await context.new_page()
                cloudflare_retries += 1
                
            except CloudflareBlockedError:
                raise
            except Exception as nav_error:
                error_str = str(nav_error).lower()
                
                # Check if we got content despite error
                if "timeout" in error_str:
                    try:
                        page_html = await page.content()
                        if 'data-jk=' in page_html or 'job_seen_beacon' in page_html:
                            print("✓ Got content despite timeout")
                            break
                    except:
                        pass
                
                # Retry on error
                if cloudflare_retries >= max_retries:
                    raise Exception(f"Navigation failed after {max_retries} retries: {nav_error}")
                
                print(f"⚠️  Navigation error, retry {cloudflare_retries + 1}/{max_retries}...")
                cloudflare_retries += 1
                await asyncio.sleep(random.uniform(2.0, 4.0))

                # Recreate browser with rotated proxy on navigation error
                try:
                    if page and not page.is_closed():
                        await page.close()
                except Exception:
                    pass
                await _close_browser(browser, context)
                proxy_config = _rotate_proxy_on_error()
                browser, context = await _launch_browser_with_context(pw, proxy_config)
                page = await context.new_page()
        
        # Pagination over Indeed SERP pages (start=0, 10, 20, …). Indeed shows
        # ~10 results per page; walk pages until max_results, no new cards, or
        # MAX_SERP_PAGES is hit. Page 1 is already loaded above.
        max_serp_pages = getattr(settings, "MAX_SERP_PAGES", 20)
        page_size = 10

        jobs: List[Job] = []
        seen_job_keys: set = set()
        browser_alive = True

        if not fetch_full_details:
            print("ℹ️  Fast mode: skipping job detail pages (using search results data only)")

        # Detail-fetch rotation settings (apply per detail fetch, not per SERP page)
        job_fetch_count = 0
        cloudflare_block_count = 0
        max_cloudflare_blocks = getattr(settings, "MAX_JOB_PAGE_CLOUDFLARE_BLOCKS", 3)
        use_auto_rotating_proxy = getattr(settings, "USE_AUTO_ROTATING_PROXY", True)
        min_delay_between_fetches = getattr(settings, "JOB_DETAIL_MIN_DELAY", 4.0)
        max_delay_between_fetches = getattr(settings, "JOB_DETAIL_MAX_DELAY", 8.0)

        for serp_page_idx in range(max_serp_pages):
            # Navigate to subsequent SERP pages. Page 0 is already loaded.
            if serp_page_idx > 0:
                if not browser_alive or not page or page.is_closed():
                    print(f"⚠️  Browser unusable before page {serp_page_idx + 1} — stopping pagination")
                    break

                next_url = build_indeed_search_url(
                    query=query, location=location, job_type=job_type,
                    employment_type=employment_type, experience_level=experience_level,
                    salary_min=salary_min, days_old=days_old, sort=sort, radius=radius,
                    start=serp_page_idx * page_size,
                )
                inter_page_delay = random.uniform(3.0, 6.0)
                print(f"📄 SERP page {serp_page_idx + 1} after {inter_page_delay:.1f}s delay: {next_url}")
                await asyncio.sleep(inter_page_delay)
                try:
                    await page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(2000)
                except Exception as page_nav_err:
                    print(f"⚠️  Failed to navigate to SERP page {serp_page_idx + 1}: {page_nav_err}")
                    break

            # Progressive scroll to surface lazy-loaded cards
            await _progressive_scroll_playwright(page)

            try:
                content = await page.content()
            except Exception as e:
                print(f"⚠️  Failed to read page content on SERP page {serp_page_idx + 1}: {e}")
                break

            soup = BeautifulSoup(content, "html.parser")
            job_cards = _find_job_cards_indeed(soup)
            print(f"📋 SERP page {serp_page_idx + 1}: found {len(job_cards)} job cards")

            if not job_cards:
                if serp_page_idx == 0:
                    print("ℹ️  No cards on first page — search returned no results")
                else:
                    print("ℹ️  No more results — stopping pagination")
                break

            new_on_this_page = 0
            for card in job_cards:
                try:
                    job = _extract_job_from_card(card, query, location)
                    if not (job and job.title and job.url):
                        continue

                    # Cross-page dedup: Indeed repeats sponsored cards on every page
                    dedup_key = job.job_key or job.url
                    if dedup_key in seen_job_keys:
                        continue
                    seen_job_keys.add(dedup_key)
                    new_on_this_page += 1

                    # Too many CF blocks: stop the SERP loop entirely instead
                    # of silently flipping to fast-mode (which used to ship
                    # card-snippet "descriptions" as if they were real). The
                    # remaining jks become Step-2 retry candidates.
                    if cloudflare_block_count >= max_cloudflare_blocks:
                        print(
                            f"  ⚠️  Too many Cloudflare blocks ({cloudflare_block_count}/"
                            f"{max_cloudflare_blocks}) — aborting SERP loop, "
                            f"remaining jks will be retried per-jk"
                        )
                        if job.job_key:
                            _LAST_FAILED_JKS.append(job.job_key)
                        browser_alive = False  # break outer pagination loop too
                        break

                    if fetch_full_details and browser_alive:
                        print(f"  → Fetching complete data from job page: {job.title}")
                        metrics.incr("detail_attempts")

                        delay = random.uniform(min_delay_between_fetches, max_delay_between_fetches)
                        print(f"    ⏳ Waiting {delay:.1f}s before fetch...")
                        await asyncio.sleep(delay)

                        if getattr(settings, "HUMANIZE", True):
                            try:
                                await page.evaluate("window.scrollBy(0, Math.random() * 300)")
                                await page.wait_for_timeout(random.uniform(500, 1000))
                                await page.mouse.move(
                                    random.randint(100, 800),
                                    random.randint(100, 500)
                                )
                                await page.wait_for_timeout(random.uniform(200, 500))
                            except Exception:
                                pass

                        rotate_browser_per_job = getattr(settings, "ROTATE_BROWSER_PER_JOB", True)

                        if use_auto_rotating_proxy and rotate_browser_per_job:
                            try:
                                print(f"    🔄 Recreating browser to force fresh proxy connection...")
                                try:
                                    if page and not page.is_closed():
                                        await page.close()
                                except Exception:
                                    pass
                                await _close_browser(browser, context)
                                proxy_config = _get_current_proxy_config()
                                browser, context = await _launch_browser_with_context(pw, proxy_config)
                                page = await context.new_page()
                                print(f"    ✓ Browser recreated with fresh proxy connection")
                                await page.wait_for_timeout(random.uniform(1000, 2000))
                            except Exception as browser_error:
                                print(f"    ⚠️  Error recreating browser: {browser_error}")
                                browser_alive = False
                                # Browser dead before we could fetch this job's
                                # detail page — flag the row and add the jk to
                                # the per-jk retry list (handled in Step 2).
                                job.detail_fetch_status = "parse_failed"
                                if job.job_key:
                                    _LAST_FAILED_JKS.append(job.job_key)
                                # In STRICT mode, drop the row entirely.
                                if getattr(settings, "STRICT_DESCRIPTION_MODE", True):
                                    continue
                                if _should_include_job(job, job_type, salary_min, salary_max, experience_level, employment_type, days_old):
                                    jobs.append(job)
                                if len(jobs) >= max_results:
                                    break
                                continue

                        try:
                            if page.is_closed():
                                print("  ⚠️  Page was closed - skipping job detail extraction for remaining jobs")
                                browser_alive = False
                            else:
                                enhanced_job = await _extract_complete_job_details_from_url_playwright(
                                    page, job, original_search_url,
                                    skip_nav_back=use_auto_rotating_proxy and rotate_browser_per_job
                                )
                                if enhanced_job:
                                    if enhanced_job.requirements and any('Ray ID' in str(req) for req in enhanced_job.requirements):
                                        cloudflare_block_count += 1
                                        metrics.incr("cf_blocks")
                                        print(f"    ⚠️  Cloudflare block detected (total: {cloudflare_block_count}/{max_cloudflare_blocks})")
                                        # Keep `job` as the SERP-card version but
                                        # mark blocked so STRICT mode drops it
                                        # and the per-jk retry queue picks it up.
                                        job.detail_fetch_status = "blocked"
                                    else:
                                        job = enhanced_job
                                        job_fetch_count += 1

                                post_fetch_delay = random.uniform(1.5, 3.0)
                                await asyncio.sleep(post_fetch_delay)
                        except Exception as enhance_error:
                            error_msg = str(enhance_error).lower()
                            job.detail_fetch_status = "parse_failed"
                            if "closed" in error_msg or "target" in error_msg:
                                print(f"  ⚠️  Browser closed during job detail extraction - flagging parse_failed for remaining jobs")
                                browser_alive = False
                            else:
                                print(f"  ⚠️  Error enhancing job details: {enhance_error}")
                    elif not fetch_full_details:
                        # Caller opted into fast mode — card-snippet description
                        # is by design, but mark the row so clients can tell.
                        job.detail_fetch_status = "skipped"

                    # Drop degraded rows in STRICT mode, but only when the
                    # client asked for full details. Fast-mode (skipped) rows
                    # are intentional and always pass through.
                    strict = getattr(settings, "STRICT_DESCRIPTION_MODE", True)
                    if (
                        strict
                        and fetch_full_details
                        and job.detail_fetch_status != "ok"
                    ):
                        if job.job_key:
                            _LAST_FAILED_JKS.append(job.job_key)
                        print(
                            f"  ⨯ Dropping degraded row (status={job.detail_fetch_status}, "
                            f"jk={job.job_key}) — queued for per-jk retry"
                        )
                        continue

                    # Apply post-scrape filters
                    if _should_include_job(job, job_type, salary_min, salary_max, experience_level, employment_type, days_old):
                        jobs.append(job)

                        if len(jobs) >= max_results:
                            break
                except Exception as e:
                    print(f"⚠️  Error extracting job from card: {e}")
                    continue

            # Outer-loop stop conditions
            if len(jobs) >= max_results:
                print(f"✓ Reached max_results={max_results}")
                break
            if new_on_this_page == 0:
                print(f"ℹ️  No new jobs on page {serp_page_idx + 1} (all duplicates) — stopping pagination")
                break
            if fetch_full_details and not browser_alive:
                print("ℹ️  Browser dead after detail fetches — cannot paginate further")
                break

        print(f"✓ Extracted {len(jobs)} jobs across {serp_page_idx + 1} SERP page(s); fetched details from {job_fetch_count} job pages")
        return jobs


    except Exception as e:
        error_msg = str(e)
        print(f"❌ Error during scraping: {error_msg}")
        import traceback
        print(f"   Full traceback:\n{traceback.format_exc()}")
        if "pthread_create" in error_msg or "Resource temporarily unavailable" in error_msg:
            raise Exception(f"System resource exhaustion — please retry. Original: {error_msg}")
        if "browser" in error_msg.lower() or "playwright" in error_msg.lower():
            raise Exception(
                f"Playwright browser error: {error_msg}. "
                "Ensure browsers are installed: python -m playwright install chromium"
            )
        raise
    finally:
        # Guaranteed cleanup: close page → context+browser → Playwright → hard-kill any stragglers
        try:
            if page and not page.is_closed():
                await page.close()
        except Exception:
            pass
        await _close_browser(browser, context)
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass
        # Belt-and-braces OS-level kill to prevent zombie Chrome on Railway
        try:
            from app.core.browser_executor import hard_kill_all_browsers
            hard_kill_all_browsers()
        except Exception:
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
    
    # Debug: Check if soup has any content
    if not soup or not soup.find('body'):
        print("⚠️  Warning: No body element found in page HTML")
        return cards
    
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
            print(f"  ✓ Found {len(found)} cards using selector: {selector}")
            break
    
    # If no cards found, debug what's actually on the page
    if not cards:
        # Check for common error/block pages
        page_text = soup.get_text().lower()
        if 'no jobs found' in page_text or 'try different keywords' in page_text:
            print("  ℹ️  Page indicates no jobs found for this search")
        elif 'cloudflare' in page_text or 'checking your browser' in page_text:
            print("  ⚠️  Page appears to be a Cloudflare challenge")
        elif 'indeed' not in page_text:
            print("  ⚠️  Page doesn't appear to be an Indeed page")
        else:
            # Try to find any divs that might be job cards
            all_divs = soup.find_all('div', limit=50)
            print(f"  🔍 Debug: Found {len(all_divs)} div elements on page")
            # Check for any divs with job-related classes
            job_related = [d for d in all_divs if any(keyword in str(d.get('class', [])).lower() 
                          for keyword in ['job', 'result', 'listing', 'card', 'serp'])]
            if job_related:
                print(f"  🔍 Found {len(job_related)} divs with job-related classes")
    
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


_SALARY_NUMBER_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d+)?)\s*([Kk])?")
_SALARY_CURRENCY_RE = re.compile(r"([€£¥$]|USD|EUR|GBP|CAD|AUD)", re.IGNORECASE)
_SALARY_PERIOD_RE = re.compile(
    r"(per\s+|/\s*|a\s+)(hour|hr|year|yr|annum|month|mo|week|wk|day)",
    re.IGNORECASE,
)
_SALARY_FROM_RE = re.compile(r"\bfrom\s+\$?\s*([\d,]+(?:\.\d+)?)\s*([Kk])?", re.IGNORECASE)
_SALARY_UPTO_RE = re.compile(r"\bup\s+to\s+\$?\s*([\d,]+(?:\.\d+)?)\s*([Kk])?", re.IGNORECASE)

_CURRENCY_SYMBOL_TO_CODE = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
}

_PERIOD_NORMALIZED = {
    "hour": "hour", "hr": "hour",
    "year": "year", "yr": "year", "annum": "year",
    "month": "month", "mo": "month",
    "week": "week", "wk": "week",
    "day": "day",
}


def _parse_salary_number(raw_num: str, k_suffix: Optional[str]) -> float:
    n = float(raw_num.replace(",", ""))
    if k_suffix:  # "$80K" → 80,000
        n *= 1000
    return n


def _parse_salary(raw: Optional[str]) -> Dict[str, Optional[object]]:
    """Parse Indeed salary strings into structured fields.

    Handles: "$50K-$80K a year", "$50,000 - $70,000 a year", "$30/hour",
    "From $80,000 a year", "Up to $100,000". Returns a dict with min, max,
    currency, period — any field may be None if not recoverable.
    """
    result: Dict[str, Optional[object]] = {
        "min": None, "max": None, "currency": None, "period": None,
    }
    if not raw:
        return result

    text = raw.strip()

    # Currency: explicit symbol/code; default USD when a dollar sign is present.
    cur_match = _SALARY_CURRENCY_RE.search(text)
    if cur_match:
        sym = cur_match.group(1).upper()
        result["currency"] = _CURRENCY_SYMBOL_TO_CODE.get(sym, sym)

    # Period
    period_match = _SALARY_PERIOD_RE.search(text)
    if period_match:
        result["period"] = _PERIOD_NORMALIZED.get(period_match.group(2).lower())
    elif re.search(r"/\s*hr\b", text, re.IGNORECASE):
        result["period"] = "hour"

    # "From X" — only a minimum
    from_match = _SALARY_FROM_RE.search(text)
    upto_match = _SALARY_UPTO_RE.search(text)

    if from_match and not upto_match:
        result["min"] = _parse_salary_number(from_match.group(1), from_match.group(2))
        return result
    if upto_match and not from_match:
        result["max"] = _parse_salary_number(upto_match.group(1), upto_match.group(2))
        return result

    # Range: "$50K - $80K", "$50,000 to $70,000"
    range_match = re.search(
        r"\$?\s*([\d,]+(?:\.\d+)?)\s*([Kk])?\s*(?:-|–|to)\s*\$?\s*([\d,]+(?:\.\d+)?)\s*([Kk])?",
        text,
    )
    if range_match:
        result["min"] = _parse_salary_number(range_match.group(1), range_match.group(2))
        result["max"] = _parse_salary_number(range_match.group(3), range_match.group(4))
        return result

    # Single value
    single_match = _SALARY_NUMBER_RE.search(text)
    if single_match:
        val = _parse_salary_number(single_match.group(1), single_match.group(2))
        result["min"] = val
        result["max"] = val

    return result


_RELATIVE_DATE_RE = re.compile(
    r"(\d+)\s*\+?\s*(minute|min|hour|hr|day|week|month)s?\s+ago",
    re.IGNORECASE,
)


def _relative_date_to_iso(raw: Optional[str], today: Optional[date] = None) -> Optional[str]:
    """Convert Indeed's relative date strings to an ISO date (YYYY-MM-DD).

    Handles "Just posted", "Today", "Yesterday", "3 days ago", "30+ days ago",
    "2 weeks ago", "1 month ago". Returns None if the input is unrecognizable.
    """
    if not raw:
        return None
    base = today or date.today()
    text = raw.strip().lower()

    if "just posted" in text or "today" in text or "posted today" in text:
        return base.isoformat()
    if "yesterday" in text:
        return (base - timedelta(days=1)).isoformat()

    match = _RELATIVE_DATE_RE.search(text)
    if not match:
        return None

    n = int(match.group(1))
    unit = match.group(2).lower()
    if unit in ("minute", "min", "hour", "hr"):
        return base.isoformat()
    if unit == "day":
        return (base - timedelta(days=n)).isoformat()
    if unit == "week":
        return (base - timedelta(weeks=n)).isoformat()
    if unit == "month":
        return (base - timedelta(days=n * 30)).isoformat()
    return None


def _extract_job_id_indeed(card) -> Optional[str]:
    """Extract job ID from Indeed job card with comprehensive fallbacks."""
    # Look for data-jk attribute on the card itself
    job_id = card.get('data-jk')
    if job_id:
        return job_id
    
    # Look for data-jk attribute on any child element
    job_id_elem = card.find(attrs={'data-jk': True})
    if job_id_elem:
        return job_id_elem.get('data-jk')
    
    # Try to extract from URL in the job title link
    link_elem = card.find('a', href=True)
    if link_elem:
        href = link_elem.get('href', '')
        # Extract job ID from Indeed URL patterns
        id_match = re.search(r'jk=([^&]+)', href)
        if id_match:
            return id_match.group(1)
        # Also try to extract from viewjob URLs
        id_match = re.search(r'/viewjob\?jk=([^&]+)', href)
        if id_match:
            return id_match.group(1)
    
    return None


def _extract_job_from_card(card, query: str, location: Optional[str]) -> Optional[Job]:
    """Extract comprehensive job data from a job card element (matches Selenium version)."""
    try:
        # Extract job ID with comprehensive fallbacks
        job_id = _extract_job_id_indeed(card) or ''
        
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

        # Indeed-specific high-value fields
        apply_url = _extract_apply_url_indeed(card, job_id)
        easy_apply = _extract_easy_apply_indeed(card)
        sponsored = _extract_sponsored_indeed(card)
        company_rating, review_count = _extract_company_rating_indeed(card)
        posted_date_iso = _relative_date_to_iso(posted_date)
        salary_parts = _parse_salary(salary_range)

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
            job_id=job_id,
            job_key=job_id or None,
            apply_url=apply_url,
            easy_apply=easy_apply,
            sponsored=sponsored,
            company_rating=company_rating,
            review_count=review_count,
            posted_date_iso=posted_date_iso,
            salary_min=salary_parts["min"],
            salary_max=salary_parts["max"],
            salary_currency=salary_parts["currency"],
            salary_period=salary_parts["period"],
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
    """Extract job description from Indeed job card with enhanced selectors and fallback strategies."""
    
    # Strategy 1: Try specific Indeed data attributes first
    data_attribute_selectors = [
        '[data-testid*="job-snippet"]',
        '[data-testid*="jobCard-snippet"]',
        '[data-testid*="description"]',
        '[data-mobtk*="description"]',
    ]
    
    for selector in data_attribute_selectors:
        desc_elem = card.select_one(selector)
        if desc_elem:
            description = desc_elem.get_text(strip=True)
            if description and len(description) > 20:
                return _clean_description(description)
    
    # Strategy 2: Try common class-based selectors
    class_selectors = [
        'div.job-snippet',
        'div.job-snippet-container',
        'ul.job-snippet',
        'div[class*="jobCardShelfContainer"]',
        'div[class*="job-snippet"]',
        'div[class*="snippet"]',
        'div[class*="job_summary"]',
        'div[class*="summary"]',
        'td.resultContent',  # Common Indeed structure
        'div.resultContent',
        'div.slider_container',
        'div.job-description',
        'span.job-snippet',
    ]
    
    for selector in class_selectors:
        desc_elem = card.select_one(selector)
        if desc_elem:
            # Try to get text from ul/li structure if present
            if desc_elem.find('ul'):
                items = desc_elem.select('li')
                if items:
                    description = ' '.join([item.get_text(strip=True) for item in items])
                    if len(description) > 20:
                        return _clean_description(description)
            
            # Otherwise get all text
            description = desc_elem.get_text(strip=True)
            if description and len(description) > 20:
                return _clean_description(description)
    
    # Strategy 3: Look for table-based structure (older Indeed layout)
    table_cell = card.select_one('td.resultContent')
    if table_cell:
        # Skip title and company info, look for actual description
        divs = table_cell.find_all('div', recursive=False)
        for div in divs[2:]:  # Skip first 2 divs (usually title/company)
            text = div.get_text(strip=True)
            if len(text) > 40:
                return _clean_description(text)
    
    # Strategy 4: Extract from structured text blocks
    description = _extract_from_structured_content(card)
    if description:
        return description
    
    # Strategy 5: Intelligent text extraction (improved fallback)
    return _intelligent_text_extraction(card)


def _clean_description(description: str) -> str:
    """Clean and normalize job description text."""
    # Remove excessive whitespace
    description = re.sub(r'\s+', ' ', description).strip()
    
    # Remove common non-description prefixes
    prefixes = [
        r'^(Job|Company|Location|Posted|Salary|Benefits?|Requirements?|Qualifications?|Description|Summary):\s*',
        r'^(Urgently hiring|New|Posted today|Posted \d+ days? ago)\s*[-•]?\s*',
    ]
    for prefix in prefixes:
        description = re.sub(prefix, '', description, flags=re.IGNORECASE)
    
    # Remove "Posted X days ago" patterns
    description = re.sub(r'\b(?:Posted|Hiring)?\s*\d+\s+(?:days?|hours?|weeks?)\s+ago\b', '', description, flags=re.IGNORECASE)
    
    # Remove "Employer responds within X days"
    description = re.sub(r'Employer (?:actively reviewing|responds within).*?(?:\.|$)', '', description, flags=re.IGNORECASE)
    
    # Hard ceiling kept only to avoid pathological 10MB payloads on a
    # broken page. Real Indeed descriptions sit comfortably under 50k.
    # The old 1000-char cap was the reason fast-mode/snippet rows looked
    # truncated to clients even when the source HTML was longer.
    if len(description) > 50000:
        description = description[:50000].rsplit(' ', 1)[0] + '...'

    return description.strip()


def _extract_from_structured_content(card) -> Optional[str]:
    """Extract description from structured content like lists or specific containers."""
    # Look for ul elements that might contain job details
    uls = card.find_all('ul')
    for ul in uls:
        # Skip navigation or metadata lists
        if any(cls in str(ul.get('class', [])) for cls in ['nav', 'menu', 'toolbar', 'metadata']):
            continue
        
        items = ul.find_all('li')
        if 2 <= len(items) <= 10:  # Reasonable number of bullet points
            text = ' '.join([item.get_text(strip=True) for item in items])
            if len(text) > 50:
                return _clean_description(text)
    
    return None


def _intelligent_text_extraction(card) -> Optional[str]:
    """Intelligent fallback extraction with improved filtering."""
    all_text = card.get_text()
    lines = [line.strip() for line in all_text.split('\n') if line.strip()]
    
    # Exclusion patterns for metadata
    exclude_patterns = [
        r'^\$[\d,]+',  # Salary
        r'\d+\s+(?:days?|hours?|weeks?)\s+ago',  # Post date
        r'^(?:Full-Time|Part-Time|Contract|Temporary|Internship|Remote|Hybrid)',  # Job type
        r'^(?:Posted|Hiring|New|Urgent|Apply)',  # Action words
        r'^[A-Z][a-z]+,\s*[A-Z]{2}',  # Location format
        r'Employer (?:actively|responds)',  # Employer activity
        r'^\d+\s+(?:review|job)',  # Review/job counts
        r'^Save job',  # UI elements
        r'responds within',
        r'often responds',
    ]
    
    exclude_keywords = {
        'days ago', 'hours ago', 'just posted', 'posted today', 
        'save', 'apply', 'sponsored', 'indeed', 'company reviews',
        'similar jobs', 'view all', 'see more', 'show more',
        'salary estimate', 'benefits', 'show less'
    }
    
    candidates = []
    
    for line in lines:
        # Skip short lines
        if len(line) < 50:
            continue
        
        # Skip if matches exclusion patterns
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in exclude_patterns):
            continue
        
        # Skip if contains exclusion keywords
        if any(keyword in line.lower() for keyword in exclude_keywords):
            continue
        
        # Skip all-caps lines (likely titles)
        if line.isupper() and len(line) < 100:
            continue
        
        # Must have multiple words (at least 8)
        if len(line.split()) < 8:
            continue
        
        # Must contain some lowercase letters (not all caps/numbers)
        if not re.search(r'[a-z]', line):
            continue
        
        # Score the line based on description-like characteristics
        score = 0
        if len(line) > 100:
            score += 2
        if any(word in line.lower() for word in ['responsible', 'experience', 'skill', 'work', 'team', 'develop', 'manage', 'support']):
            score += 2
        if re.search(r'\b(?:will|must|should|required|preferred)\b', line, re.IGNORECASE):
            score += 1
        if line.count(',') >= 2:
            score += 1
        
        candidates.append((score, line))
    
    # Return the highest scoring candidate
    if candidates:
        candidates.sort(reverse=True, key=lambda x: x[0])
        return _clean_description(candidates[0][1])
    
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


def _extract_apply_url_indeed(card, job_id: str) -> Optional[str]:
    """Best-effort direct apply URL from the SERP card.

    Indeed only exposes a direct apply link on Easy Apply cards; otherwise it
    routes through /rc/clk redirect. Fall back to the canonical viewjob URL —
    consumers can resolve it server-side if they want the final destination.
    """
    apply_elem = card.select_one(
        'a[class*="indeedApply"], a[data-testid*="apply"], a[href*="indeedApply"]'
    )
    if apply_elem and apply_elem.get('href'):
        href = apply_elem['href']
        return f"https://www.indeed.com{href}" if href.startswith('/') else href
    if job_id:
        return f"https://www.indeed.com/viewjob?jk={job_id}"
    return None


def _extract_easy_apply_indeed(card) -> Optional[bool]:
    """True if the card advertises Indeed Easy Apply."""
    if card.select_one(
        'button[id*="indeedApplyButton"], span[class*="indeedApply"], '
        '[data-testid*="indeed-apply"], a[class*="indeedApply"]'
    ):
        return True
    text = card.get_text(" ", strip=True).lower()
    if "easily apply" in text or "easy apply" in text:
        return True
    return False


def _extract_sponsored_indeed(card) -> Optional[bool]:
    """True if the card is a paid/sponsored placement."""
    if card.select_one(
        'span.sponsoredJob, [data-testid*="sponsored"], '
        'span[class*="sponsored" i]'
    ):
        return True
    # Indeed sometimes renders the word "Sponsored" inside a small <span>.
    sponsored_span = card.find(string=re.compile(r"^\s*sponsored\s*$", re.IGNORECASE))
    return True if sponsored_span else False


def _extract_company_rating_indeed(card) -> Tuple[Optional[float], Optional[int]]:
    """Extract numeric company rating (1.0–5.0) and review count."""
    rating: Optional[float] = None
    review_count: Optional[int] = None

    rating_elem = card.select_one(
        'span[aria-label*="out of 5 stars"], span[aria-label*="of 5"]'
    )
    if rating_elem:
        label = rating_elem.get("aria-label", "")
        m = re.search(r"([0-9]\.[0-9])", label)
        if m:
            try:
                rating = float(m.group(1))
            except ValueError:
                pass

    if rating is None:
        # Fallback: a free-standing "4.2" near the rating star icon.
        rating_text = card.select_one('span[class*="ratingNumber"], span[data-testid*="rating"]')
        if rating_text:
            m = re.search(r"([0-5]\.[0-9])", rating_text.get_text(strip=True))
            if m:
                try:
                    rating = float(m.group(1))
                except ValueError:
                    pass

    reviews_elem = card.select_one(
        'span[data-testid="reviewCount"], a[class*="reviewCount"], '
        'span[class*="reviewCount"]'
    )
    if reviews_elem:
        m = re.search(r"([\d,]+)", reviews_elem.get_text(strip=True))
        if m:
            try:
                review_count = int(m.group(1).replace(",", ""))
            except ValueError:
                pass

    return rating, review_count


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

def _clean_and_format_description(text: str) -> str:
    """Clean and format job description text."""
    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove multiple newlines (keep max 2)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    # Trim
    text = text.strip()
    return text

def _extract_description_from_full_page_improved(soup: BeautifulSoup) -> Optional[str]:
    """
    Extract description from Indeed's full job page with comprehensive patterns and debugging.
    Indeed typically structures descriptions under headings like:
    - "Full job description"
    - "Company Description"
    - Direct description containers
    """
    
    print(f"  🔍 DEBUG: Starting description extraction...")
    
    # Strategy 1: Look for text following "Full job description" heading
    full_job_desc_heading = soup.find(string=re.compile(r'Full job description', re.IGNORECASE))
    if full_job_desc_heading:
        print(f"  ✓ Found 'Full job description' heading")
        parent = full_job_desc_heading.find_parent()
        if parent:
            print(f"    Parent tag: {parent.name}, classes: {parent.get('class', [])}")
            
            # Strategy 1a: Get ALL text from parent's parent (go up one level)
            grandparent = parent.find_parent()
            if grandparent:
                # Get all text after the heading element
                all_text_parts = []
                for elem in grandparent.descendants:
                    if elem == full_job_desc_heading:
                        # Start collecting from here
                        continue
                    if isinstance(elem, str) and elem.strip():
                        all_text_parts.append(elem.strip())
                
                if all_text_parts:
                    combined = ' '.join(all_text_parts)
                    combined = _clean_and_format_description(combined)
                    if len(combined) > 200:
                        print(f"  ✓ Extracted {len(combined)} chars from grandparent after heading")
                        return combined
            
            # Strategy 1b: Get all following siblings
            remaining_text = []
            current = parent.find_next_sibling()
            while current and len(remaining_text) < 20:
                text_content = current.get_text(separator='\n', strip=True)
                if text_content and len(text_content) > 10:
                    remaining_text.append(text_content)
                    print(f"    Found sibling with {len(text_content)} chars")
                current = current.find_next_sibling()
            
            if remaining_text:
                combined = '\n\n'.join(remaining_text)
                combined = _clean_and_format_description(combined)
                if len(combined) > 100:
                    print(f"  ✓ Extracted {len(combined)} chars from siblings after 'Full job description'")
                    return combined
    
    # Strategy 2: Primary Indeed selectors for job description container (IMPROVED)
    desc_selectors = [
        # Most common Indeed job description selectors (2024)
        'div.jobsearch-jobDescriptionText',
        'div#jobDescriptionText',
        
        # Class-based variations
        'div[class*="jobsearch-JobDescriptionText"]',
        'div[class*="jobDescriptionText"]',
        'div[class*="job-description"]',
        'div[class*="jobDescription"]',
        
        # ID-based variations  
        'div[id*="jobDescriptionText"]',
        'div[id*="job-description"]',
        
        # Data attribute selectors
        'div[data-testid="jobsearch-JobComponent-description"]',
        'div[data-testid="job-description"]',
        
        # Nested selectors (look deeper)
        'div[class*="jobsearch-JobComponent"]',
        'div[id*="jobsearch"]',
        
        # Alternative structures
        'section[class*="jobDescription"]',
        'article[class*="jobDescription"]',
    ]
    
    for selector in desc_selectors:
        elements = soup.select(selector)
        print(f"  Trying selector '{selector}': found {len(elements)} elements")
        
        for elem in elements:
            # Get all text including nested elements
            text = elem.get_text(separator='\n', strip=True)
            print(f"    Element has {len(text)} chars, classes: {elem.get('class', [])}")
            
            if len(text) > 100:
                text = _clean_and_format_description(text)
                if len(text) > 100:
                    print(f"  ✓ Found description using selector '{selector}': {len(text)} characters")
                    return text
    
    # Strategy 3: Look for divs with ID containing "job" 
    job_divs = soup.find_all('div', id=re.compile(r'job', re.I))
    print(f"  Found {len(job_divs)} divs with 'job' in ID")
    for div in job_divs:
        text = div.get_text(separator='\n', strip=True)
        print(f"    Div ID='{div.get('id')}': {len(text)} chars")
        if len(text) > 300:
            text = _clean_and_format_description(text)
            print(f"  ✓ Found description in div with job ID: {len(text)} characters")
            return text
    
    # Strategy 4: Look for largest text blocks in ANY div
    print(f"  Analyzing all divs for large text blocks...")
    all_divs = soup.find_all('div', limit=200)  # Limit to first 200 divs
    text_blocks = []
    
    for div in all_divs:
        # Skip divs with too many child divs (likely containers)
        child_divs = div.find_all('div', recursive=False)
        if len(child_divs) > 8:
            continue
        
        text = div.get_text(separator='\n', strip=True)
        
        # Score based on length and word count
        if len(text) > 200 and len(text.split()) > 40:
            # Avoid navigation/header elements
            classes = ' '.join(div.get('class', [])).lower()
            if any(skip in classes for skip in ['nav', 'header', 'footer', 'menu', 'sidebar']):
                continue
            
            # Avoid elements that start with common UI text
            if text.startswith(('Apply', 'Sign in', 'Save job', 'Report', 'Share')):
                continue
            
            text_blocks.append((len(text), text, div.get('class', []), div.get('id', '')))
    
    if text_blocks:
        # Sort by length and show top candidates
        text_blocks.sort(reverse=True)
        print(f"  Found {len(text_blocks)} candidate text blocks")
        for i, (length, text, classes, div_id) in enumerate(text_blocks[:5]):
            print(f"    #{i+1}: {length} chars, classes={classes}, id={div_id}")
            # Also print first 100 chars to help debug
            print(f"        Preview: {text[:100]}...")
        
        # Return the longest one
        text = _clean_and_format_description(text_blocks[0][1])
        print(f"  ✓ Using longest text block: {len(text)} characters")
        return text
    
    # Strategy 5: Fallback - look for the main content area
    main_selectors = [
        'main',
        'article', 
        'div[role="main"]',
        'div[class*="mainContent"]',
        'div[class*="main-content"]',
        'div[class*="content"]'
    ]
    
    for selector in main_selectors:
        elem = soup.select_one(selector)
        if elem:
            print(f"  Found main content area: {selector}")
            text = elem.get_text(separator='\n', strip=True)
            if len(text) > 500:
                text = _clean_and_format_description(text)
                print(f"  ✓ Found description in main content area: {len(text)} characters")
                return text
    
    # Strategy 6: Last resort - combine all paragraphs
    paragraphs = soup.find_all('p')
    if len(paragraphs) > 3:
        print(f"  Trying paragraph combination ({len(paragraphs)} paragraphs)")
        combined_text = '\n\n'.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 40])
        combined_text = _clean_and_format_description(combined_text)
        if len(combined_text) > 200:
            print(f"  ✓ Found description from paragraph clustering: {len(combined_text)} characters")
            return combined_text
    
    print(f"  ❌ No description found using any method")
    print(f"  DEBUG: Page has {len(soup.find_all('div'))} divs, {len(soup.find_all('p'))} paragraphs")
    print(f"  DEBUG: Total page text length: {len(soup.get_text())} chars")
    
    # FINAL DEBUG: Save HTML to file for inspection
    try:
        with open('/tmp/debug_indeed_page.html', 'w', encoding='utf-8') as f:
            f.write(str(soup.prettify()))
        print(f"  DEBUG: Saved full HTML to /tmp/debug_indeed_page.html for inspection")
    except:
        pass
    
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


def _extract_job_id_from_full_page(soup, url: str) -> Optional[str]:
    """Extract job ID from Indeed's full job page."""
    # Try to extract from URL first (most reliable)
    id_match = re.search(r'jk=([^&]+)', url)
    if id_match:
        return id_match.group(1)
    
    # Try to extract from viewjob URL pattern
    id_match = re.search(r'/viewjob\?jk=([^&]+)', url)
    if id_match:
        return id_match.group(1)
    
    # Look for data-jk attribute in the page
    job_id_elem = soup.find(attrs={'data-jk': True})
    if job_id_elem:
        return job_id_elem.get('data-jk')
    
    # Look for job ID in meta tags or script tags
    meta_elem = soup.find('meta', attrs={'property': re.compile(r'job.*id', re.I)})
    if meta_elem and meta_elem.get('content'):
        return meta_elem.get('content')
    
    return None


def _extract_company_url_from_full_page(soup, page: Page = None) -> Optional[str]:
    """
    Extract company URL from Indeed's full job page.
    Indeed displays company info with links to company pages.
    """
    # Strategy 1: Look for company name link with data-testid
    company_selectors = [
        'a[data-testid="companyName"]',
        'a[data-testid="employer-link"]',
        'a[data-testid="inlineHeader-companyName"]',
        'div[data-testid="inlineHeader-companyName"] a',
    ]
    
    for selector in company_selectors:
        company_elem = soup.select_one(selector)
        if company_elem and company_elem.get('href'):
            href = company_elem.get('href')
            # Convert relative to absolute URL
            if href.startswith('/'):
                return f"https://www.indeed.com{href}"
            elif href.startswith('http'):
                return href
    
    # Strategy 2: Look for company header section
    company_header = soup.find('div', attrs={'data-testid': re.compile(r'.*company.*', re.I)})
    if company_header:
        company_link = company_header.find('a', href=True)
        if company_link:
            href = company_link.get('href')
            if href and ('/cmp/' in href or '/company/' in href):
                if href.startswith('/'):
                    return f"https://www.indeed.com{href}"
                return href
    
    # Strategy 3: Look for links containing /cmp/ (Indeed's company page pattern)
    all_links = soup.find_all('a', href=True, limit=50)  # Limit to first 50 links
    for link in all_links:
        href = link.get('href', '')
        # Indeed company pages use /cmp/ pattern
        if '/cmp/' in href or '/company/' in href:
            # Make sure it's not a review or jobs link
            if '/reviews' not in href and '/jobs' not in href:
                # Check if link text looks like a company name (not too long)
                link_text = link.get_text(strip=True)
                if link_text and 3 < len(link_text) < 100:
                    if href.startswith('/'):
                        return f"https://www.indeed.com{href}"
                    return href
    
    # Strategy 4: Look for company name in header and find associated link
    company_name_elem = soup.find(['span', 'div', 'h2'], attrs={'data-testid': re.compile(r'.*company.*', re.I)})
    if company_name_elem:
        company_name = company_name_elem.get_text(strip=True)
        if company_name:
            # Find link with this company name nearby
            parent = company_name_elem.find_parent()
            if parent:
                nearby_link = parent.find('a', href=True)
                if nearby_link:
                    href = nearby_link.get('href')
                    if href and ('/cmp/' in href or '/company/' in href):
                        if href.startswith('/'):
                            return f"https://www.indeed.com{href}"
                        return href
    
    # Strategy 5: Look for employer/company section by class
    employer_selectors = [
        'div[class*="employer"]',
        'div[class*="company"]',
        'div[class*="CompanyInfo"]',
        'div[id*="company"]',
    ]
    
    for selector in employer_selectors:
        employer_div = soup.select_one(selector)
        if employer_div:
            employer_link = employer_div.find('a', href=True)
            if employer_link:
                href = employer_link.get('href')
                if href and ('/cmp/' in href or '/company/' in href):
                    if href.startswith('/'):
                        return f"https://www.indeed.com{href}"
                    return href
    
    return None


async def _extract_complete_job_details_from_url_playwright(
    page: Page, 
    job: Job, 
    original_search_url: Optional[str] = None,
    skip_nav_back: bool = False
) -> Optional[Job]:
    """Extract complete job details by navigating to the individual job page URL with Cloudflare bypass."""
    if not job.url:
        return job
    
    # Use original search URL if provided
    if original_search_url:
        original_url = original_search_url
    else:
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        current_url = page.url
        parsed = urlparse(current_url)
        query_params = parse_qs(parsed.query)
        if 'vjk' in query_params:
            del query_params['vjk']
        clean_query = urlencode(query_params, doseq=True)
        original_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_query, parsed.fragment))
    
    max_job_retries = getattr(settings, "MAX_JOB_PAGE_RETRIES", 2)
    job_retry_count = 0
    
    while job_retry_count <= max_job_retries:
        try:
            print(f"    → Navigating to: {job.url}")
            
            # CRITICAL: Set proper headers before navigation
            # Include Referer to make it look like we came from search results
            referer = original_search_url if original_search_url else "https://www.indeed.com/"
            await page.set_extra_http_headers({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'same-origin',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
                'Referer': referer,  # Critical: Makes it look like we came from search
            })
            
            # Navigate with shorter timeout, we'll handle waiting manually
            job_page_timeout = getattr(settings, "JOB_PAGE_TIMEOUT", 15000)  # Reduced to 15s
            
            try:
                response = await page.goto(job.url, wait_until="commit", timeout=job_page_timeout)
                status_code = response.status if response else 0
                print(f"    ✓ Navigation response: {status_code}")
            except Exception as nav_err:
                print(f"    ⚠️  Navigation timeout, continuing anyway...")
            
            # CRITICAL: Wait for Cloudflare challenge to complete
            print(f"    ⏳ Waiting for page to load (checking for Cloudflare)...")
            
            # Strategy 1: Wait for body to appear
            try:
                await page.wait_for_selector('body', timeout=10000, state='attached')
            except:
                pass
            
            # Strategy 2: Check for Cloudflare challenge and wait for it to complete
            cloudflare_detected = False
            for attempt in range(10):  # Check for up to 10 seconds
                page_content = await page.content()
                page_title = await page.title()
                
                # Check for Cloudflare indicators
                is_challenge = (
                    'Checking your browser' in page_content or
                    'Just a moment' in page_title or
                    'challenge-platform' in page_content or
                    'cf-challenge' in page_content.lower() or
                    'Ray ID' in page_content
                )
                
                if is_challenge:
                    if attempt == 0:
                        cloudflare_detected = True
                        print(f"    🔐 Cloudflare challenge detected - waiting for completion...")
                    
                    # Wait and check again
                    await asyncio.sleep(1)
                    continue
                else:
                    # No challenge or challenge completed
                    if cloudflare_detected:
                        print(f"    ✓ Cloudflare challenge completed after {attempt}s")
                    break
            
            # Wait a bit more for page to stabilize
            await page.wait_for_timeout(random.uniform(2000, 3000))
            
            # Try to wait for job description content
            try:
                await page.wait_for_selector(
                    'div[class*="jobsearch"], div[class*="jobDescription"], body', 
                    timeout=8000,
                    state='attached'
                )
            except:
                print(f"    ℹ️  Timeout waiting for job content selector")
            
            # Get final page content
            full_page_content = await page.content()
            current_title = await page.title()
            
            # Final check for Cloudflare blocking
            has_cloudflare_indicators = (
                "Checking your browser" in full_page_content
                or "Enable JavaScript and cookies to continue" in full_page_content
                or "challenge-platform" in full_page_content
                or "cf-browser-verification" in full_page_content
                or "Just a moment" in current_title
                or ("Ray ID" in full_page_content and len(full_page_content) < 5000)
            )
            
            # Check for actual job content
            has_job_content = (
                'jobsearch-JobComponent' in full_page_content
                or 'jobDescriptionText' in full_page_content
                or 'job-description' in full_page_content.lower()
                or (len(full_page_content) > 10000 and 'indeed.com' in full_page_content)
            )
            
            # Determine if actually blocked
            is_actually_blocked = has_cloudflare_indicators and not has_job_content
            
            if is_actually_blocked:
                metrics.incr("cf_blocks")
                if job_retry_count >= max_job_retries:
                    print(f"    ❌ Job page blocked by Cloudflare after {job_retry_count + 1} attempts - marking blocked")
                    job.detail_fetch_status = "blocked"
                    return job

                # Retry with longer wait
                backoff = random.uniform(5.0, 8.0) * (1 + job_retry_count)
                print(f"    ⚠️  Still blocked, retry {job_retry_count + 1}/{max_job_retries}, waiting {backoff:.1f}s...")
                
                # Perform human interactions before retry
                if getattr(settings, "HUMANIZE", True):
                    await _perform_human_interactions_playwright(page)
                
                await asyncio.sleep(backoff)
                job_retry_count += 1
                continue
            
            # Successfully got job page content
            print(f"    ✓ Job page loaded ({len(full_page_content)} chars)")
            full_page_soup = BeautifulSoup(full_page_content, 'html.parser')
            
            # Extract job ID from full page if not already present
            if not job.job_id:
                enhanced_job_id = _extract_job_id_from_full_page(full_page_soup, page.url)
                if enhanced_job_id:
                    job.job_id = enhanced_job_id
                    print(f"    ✓ Enhanced job ID: {enhanced_job_id}")
            
            # Extract company URL from full page if not already present
            if not job.company_url:
                enhanced_company_url = _extract_company_url_from_full_page(full_page_soup, page)
                if enhanced_company_url:
                    job.company_url = enhanced_company_url
                    print(f"    ✓ Enhanced company URL: {enhanced_company_url}")
            
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
            
            # Always update description with full job description from detail page.
            # Floor (MIN_DESCRIPTION_LEN, default 500) separates a real
            # job-description block from card-snippet leftovers and CF
            # "Ray ID" stubs that snuck past block-detection.
            min_desc = getattr(settings, "MIN_DESCRIPTION_LEN", 500)
            if enhanced_description and len(enhanced_description) >= min_desc:
                job.description = enhanced_description
                job.detail_fetch_status = "ok"
                metrics.incr("desc_ok")
                metrics.incr("detail_success")
                print(f"    ✓ Enhanced description: {len(enhanced_description)} characters")
            else:
                # Detail page rendered without our expected description block,
                # or block was below floor. Either way: do NOT keep the card
                # snippet silently — flag the row so STRICT mode can drop it
                # and the per-jk retry queue can recover it (Step 2).
                got = len(enhanced_description) if enhanced_description else 0
                print(f"    ⚠️  Description below floor ({got} < {min_desc}) — marking parse_failed")
                job.detail_fetch_status = "parse_failed"
                metrics.incr("selector_drift")
            
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
            
            # Break out of retry loop on success
            break
            
        except Exception as e:
            if job_retry_count >= max_job_retries:
                print(f"    ⚠️  Error extracting enhanced details after {job_retry_count + 1} attempts: {e}")
                job.detail_fetch_status = "parse_failed"
                return job  # caller decides whether to drop / retry / keep

            print(f"    ⚠️  Error on attempt {job_retry_count + 1}, retrying: {e}")
            job_retry_count += 1
            await asyncio.sleep(random.uniform(2.0, 4.0))
            continue
    
    # Navigate back to search results (outside retry loop)
    if not skip_nav_back:
        try:
            print(f"    ← Navigating back to search results")
            back_nav_timeout = getattr(settings, "BACK_NAV_TIMEOUT", 30000)  # 30 seconds default
            await page.goto(original_url, wait_until="domcontentloaded", timeout=back_nav_timeout)
            await page.wait_for_timeout(2000)  # Wait for page to load
        except Exception as nav_error:
            print(f"    ⚠️  Warning: Could not navigate back: {nav_error}")
            # If navigation back fails, it's not critical - we can continue
    else:
        print(f"    ↻ Skipping navigation back (using context rotation)")
    
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