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
import atexit
from typing import Optional, List, Dict, Set
from urllib.parse import quote_plus, urlparse
from bs4 import BeautifulSoup
from app.models.job_model import Job
from app.core.config import settings
from app.core.proxy_manager import ProxyManager

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


# Global browser resources - properly managed to prevent resource leaks
_playwright: Optional["Playwright"] = None
_browser: Optional[Browser] = None
_context: Optional[BrowserContext] = None
_proxy_manager: Optional[ProxyManager] = None
_current_proxy: Optional[str] = None
_last_fetch = 0
_active_pages: Set[int] = set()  # Track active page IDs
_resource_lock = asyncio.Lock() if PLAYWRIGHT_AVAILABLE else None  # Lock for resource management
_max_pages_per_context = 2  # Maximum concurrent pages
_browser_creation_count = 0  # Track how many times browser was created
_last_cleanup_time = 0  # Last time resources were cleaned up
_cleanup_interval = 300  # Cleanup every 5 minutes
_scrape_count = 0  # Count of scrapes since last full cleanup
_max_scrapes_before_cleanup = 20  # Force cleanup after this many scrapes


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


async def _force_cleanup_all_resources():
    """Force cleanup of all Playwright resources to prevent resource leaks."""
    global _playwright, _browser, _context, _active_pages, _browser_creation_count, _scrape_count
    
    print("🧹 Force cleaning up all Playwright resources...")
    
    # Close all tracked pages
    _active_pages.clear()
    
    # Close context
    if _context:
        try:
            await _context.close()
            print("  ✓ Context closed")
        except Exception as e:
            print(f"  ⚠️  Error closing context: {e}")
        _context = None
    
    # Close browser
    if _browser:
        try:
            await _browser.close()
            print("  ✓ Browser closed")
        except Exception as e:
            print(f"  ⚠️  Error closing browser: {e}")
        _browser = None
    
    # Stop playwright
    if _playwright:
        try:
            await _playwright.stop()
            print("  ✓ Playwright stopped")
        except Exception as e:
            print(f"  ⚠️  Error stopping playwright: {e}")
        _playwright = None
    
    _browser_creation_count = 0
    _scrape_count = 0
    print("✓ All Playwright resources cleaned up")


async def _check_and_cleanup_resources():
    """Check if resources need cleanup and perform if necessary."""
    global _last_cleanup_time, _browser_creation_count, _scrape_count
    
    now = time.monotonic()
    
    # Cleanup if:
    # 1. Cleanup interval has passed
    # 2. Too many browser creations (possible leak)
    # 3. Browser is dead but playwright is alive
    # 4. Too many scrapes since last cleanup
    # 5. Too many active pages
    should_cleanup = False
    reason = ""
    
    if now - _last_cleanup_time > _cleanup_interval:
        should_cleanup = True
        reason = f"interval exceeded ({_cleanup_interval}s)"
    
    if _browser_creation_count > 10:
        should_cleanup = True
        reason = f"too many browser creations ({_browser_creation_count})"
    
    if _playwright and not _browser:
        should_cleanup = True
        reason = "orphaned playwright instance"
    
    if _scrape_count >= _max_scrapes_before_cleanup:
        should_cleanup = True
        reason = f"max scrapes reached ({_scrape_count}/{_max_scrapes_before_cleanup})"
    
    if len(_active_pages) > _max_pages_per_context:
        should_cleanup = True
        reason = f"too many active pages ({len(_active_pages)})"
    
    if should_cleanup:
        print(f"🔄 Cleanup triggered: {reason}")
        await _force_cleanup_all_resources()
        _last_cleanup_time = now
        _scrape_count = 0


async def get_browser(force_new: bool = False, rotate_proxy: bool = False, new_context_only: bool = False) -> tuple[Browser, BrowserContext]:
    """
    Get or create a Playwright browser instance with stealth and proxy support.
    
    Args:
        force_new: If True, create a new browser instance
        rotate_proxy: If True, rotate to the next proxy before creating browser
        new_context_only: If True, only create new context (for auto-rotating proxies)
        
    Returns:
        Tuple of (browser, context)
    """
    global _playwright, _browser, _context, _browser_creation_count, _active_pages
    
    if not PLAYWRIGHT_AVAILABLE:
        raise ImportError("Playwright is not installed. Install with: pip install playwright && python -m playwright install chromium")
    
    # Check if we need to cleanup resources
    await _check_and_cleanup_resources()
    
    # If new_context_only is True and browser exists, just create new context
    if new_context_only and _browser and _browser.is_connected():
        # Close existing context
        if _context:
            try:
                await _context.close()
                print("  ✓ Old context closed for rotation")
            except Exception as e:
                print(f"  ⚠️  Error closing old context: {e}")
        
        # Create new context with same proxy (proxy rotates per request)
        proxy_config = _get_current_proxy_config()
        
        accept_lang = getattr(settings, "ACCEPT_LANGUAGE", "en-US,en;q=0.9") or "en-US,en;q=0.9"
        locale = accept_lang.split(",")[0].strip() if accept_lang else "en-US"
        
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
        
        if proxy_config:
            context_options["proxy"] = proxy_config
        
        _context = await _browser.new_context(**context_options)
        
        # Apply stealth to new context
        if STEALTH_AVAILABLE and _stealth:
            await _stealth.apply_stealth_async(_context)
        else:
            await _context.add_init_script("""
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
        
        print("  ✓ New context created with proxy rotation")
        return _browser, _context
    
    if _browser and not force_new:
        try:
            # Check if browser is still alive
            if _browser.is_connected():
                # Check if we should rotate proxy based on time
                if _proxy_manager and _proxy_manager.should_rotate():
                    print("🔄 Proxy rotation interval reached - recreating browser with new proxy")
                    force_new = True
                else:
                    return _browser, _context
        except:
            # Browser is dead, cleanup and create new one
            print("⚠️  Browser connection lost, cleaning up...")
            await _force_cleanup_all_resources()
    
    # If force_new, cleanup existing resources first
    if force_new:
        await _force_cleanup_all_resources()
    
    # Clear active pages tracking for new browser
    _active_pages.clear()
    
    # Get proxy configuration
    if rotate_proxy:
        proxy_config = _rotate_proxy_on_error()
    else:
        proxy_config = _get_current_proxy_config()
    
    if proxy_config:
        print(f"🌐 Creating browser instance with proxy: {proxy_config['server']}")
    else:
        print("🌐 Creating browser instance (no proxy - direct connection)")
    
    # Create new playwright instance if needed
    if not _playwright:
        try:
            _playwright = await async_playwright().start()
            _browser_creation_count += 1
            print(f"✓ Playwright started (creation #{_browser_creation_count})")
        except Exception as playwright_error:
            error_msg = f"Failed to start Playwright: {str(playwright_error)}"
            print(f"❌ {error_msg}")
            import traceback
            print(f"   Traceback: {traceback.format_exc()}")
            raise Exception(f"{error_msg}. This may indicate Playwright browsers are not installed. Run: python -m playwright install chromium")
    
    # Launch browser with optimized settings for headless mode
    try:
        _browser = await _playwright.chromium.launch(
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
        # Cleanup on failure
        await _force_cleanup_all_resources()
        raise Exception(f"{error_msg}. This usually means Playwright browsers are not installed. Run: python -m playwright install chromium")
    
    # Get accept language with proper fallback
    accept_lang = getattr(settings, "ACCEPT_LANGUAGE", "en-US,en;q=0.9") or "en-US,en;q=0.9"
    locale = accept_lang.split(",")[0].strip() if accept_lang else "en-US"
    
    # Create context with realistic settings
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
    
    # Add proxy configuration if available
    if proxy_config:
        context_options["proxy"] = proxy_config
        print(f"✓ Proxy configured for browser context")
    
    _context = await _browser.new_context(**context_options)
    
    # Apply comprehensive stealth using playwright-stealth library
    # This handles: webdriver, plugins, languages, chrome runtime, permissions,
    # WebGL vendor/renderer, canvas fingerprint, and many more detection vectors
    if STEALTH_AVAILABLE and _stealth:
        # Apply stealth to the context - all pages will inherit stealth settings
        await _stealth.apply_stealth_async(_context)
        print("✓ Playwright-stealth applied (comprehensive anti-detection)")
    else:
        # Fallback to basic stealth script if playwright-stealth not available
        print("ℹ️  Using basic stealth (install playwright-stealth for better detection avoidance)")
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
    """Close the browser and clean up ALL resources including Playwright instance."""
    await _force_cleanup_all_resources()


async def create_page_with_tracking(context: BrowserContext) -> Page:
    """
    Create a new page with resource tracking.
    Ensures we don't exceed max pages per context.
    """
    global _active_pages
    
    # Check if we have too many active pages
    if len(_active_pages) >= _max_pages_per_context:
        print(f"⚠️  Maximum pages ({_max_pages_per_context}) reached, cleaning up old pages...")
        # Force cleanup and reset
        await _force_cleanup_all_resources()
        # Get new browser/context
        _, context = await get_browser(force_new=True)
    
    page = await context.new_page()
    page_id = id(page)
    _active_pages.add(page_id)
    print(f"📄 Created page (active: {len(_active_pages)})")
    return page


async def close_page_with_tracking(page: Page):
    """Close a page and remove from tracking."""
    global _active_pages
    
    if page:
        page_id = id(page)
        try:
            if not page.is_closed():
                await page.close()
        except Exception as e:
            print(f"⚠️  Error closing page: {e}")
        
        _active_pages.discard(page_id)
        print(f"📄 Closed page (active: {len(_active_pages)})")


def get_proxy_stats() -> Optional[Dict]:
    """
    Get current proxy statistics for monitoring/debugging.
    
    Returns:
        Dict with proxy stats or None if no proxy manager
    """
    if not _proxy_manager:
        return None
    return _proxy_manager.get_proxy_stats()


def reset_proxy_manager_state():
    """Reset the proxy manager state (useful for testing or forced reset)."""
    global _proxy_manager, _current_proxy
    
    if _proxy_manager:
        _proxy_manager.reset_failures()
    _current_proxy = None


def get_browser_resource_stats() -> Dict:
    """
    Get current browser resource statistics for monitoring.
    Useful for debugging resource leaks.
    """
    return {
        "playwright_active": _playwright is not None,
        "browser_connected": _browser.is_connected() if _browser else False,
        "context_active": _context is not None,
        "active_pages": len(_active_pages),
        "max_pages_per_context": _max_pages_per_context,
        "browser_creation_count": _browser_creation_count,
        "scrape_count": _scrape_count,
        "max_scrapes_before_cleanup": _max_scrapes_before_cleanup,
        "last_cleanup_time": _last_cleanup_time,
        "cleanup_interval": _cleanup_interval,
    }


def _sync_cleanup_atexit():
    """Synchronous cleanup for atexit handler."""
    global _playwright, _browser, _context
    
    print("🧹 Performing atexit cleanup of Playwright resources...")
    
    # We can't use async in atexit, so we need to do sync cleanup
    # This is a best-effort cleanup
    if _context:
        try:
            # Try to get event loop if it exists
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, schedule cleanup
                loop.create_task(_force_cleanup_all_resources())
            else:
                # If loop is not running, run cleanup
                loop.run_until_complete(_force_cleanup_all_resources())
        except Exception as e:
            print(f"⚠️  Atexit cleanup error (expected if loop is closed): {e}")
    
    _context = None
    _browser = None
    _playwright = None
    print("✓ Atexit cleanup completed")


# Register cleanup on exit
atexit.register(_sync_cleanup_atexit)


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
    fetch_full_details: bool = True  # Set to False for faster scraping (skip job detail pages)
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
    
    global _last_fetch, _scrape_count
    
    # Increment scrape counter for resource management
    _scrape_count += 1
    print(f"🔍 Starting scrape #{_scrape_count} (cleanup threshold: {_max_scrapes_before_cleanup})")
    
    # Rate limiting
    now = time.monotonic()
    jitter = random.uniform(0, 0.75)
    wait = settings.MIN_DELAY + jitter - (now - _last_fetch)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_fetch = time.monotonic()
    
    page = None  # Initialize page variable for cleanup in finally block
    
    try:
        browser, context = await get_browser()
    except Exception as browser_error:
        error_msg = f"Failed to get browser: {str(browser_error)}"
        print(f"❌ {error_msg}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        # Force cleanup on browser creation failure
        await _force_cleanup_all_resources()
        raise Exception(f"{error_msg}. This may indicate Playwright browsers are not installed. Run: python -m playwright install chromium")
    
    try:
        page = await create_page_with_tracking(context)
        # Stealth is already applied to context, no need to apply to individual pages
    except Exception as page_error:
        error_msg = f"Failed to create page: {str(page_error)}"
        print(f"❌ {error_msg}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        # Force cleanup on page creation failure
        await _force_cleanup_all_resources()
        raise Exception(f"{error_msg}. This may indicate Playwright browsers are not installed. Run: python -m playwright install chromium")
    
    try:
        # Build Indeed URL
        base_url = "https://www.indeed.com/jobs"
        url = f"{base_url}?q={quote_plus(query)}"
        
        # Handle job_type filter - if remote, bypass location and search all over United States
        # Similar to how SimplyHired handles remote jobs
        if job_type and job_type.lower() in ['remote', 'work from home', 'wfh', 'telecommute', 'telework']:
            # For remote jobs, use location=remote to search all over United States
            # This bypasses any specific location filter
            job_type_location = _get_indeed_job_type_filter(job_type)
            if job_type_location:
                url += f"&l={quote_plus(job_type_location)}"
                print(f"DEBUG - Job type '{job_type}' mapped to location '{job_type_location}' (bypassing location, searching all US)")
        elif location:
            # Only add location if job_type is not remote
            location_param = _format_location_for_indeed(location)
            url += f"&l={quote_plus(location_param)}"
            print(f"DEBUG - Location '{location}' formatted as '{location_param}'")
        
        # Add job type filter for hybrid/onsite (if not already handled above)
        if job_type and job_type.lower() not in ['remote', 'work from home', 'wfh', 'telecommute', 'telework']:
            # For hybrid/onsite, Indeed doesn't have URL-level filters via location
            # We'll rely on post-scraping filtering
            print(f"DEBUG - Job type '{job_type}' will be filtered post-scraping (no URL location parameter available)")
        
        # Add salary filter if provided
        if salary_min:
            salary_param = f"{salary_min}-{salary_max or ''}"
            url += f"&salary={quote_plus(salary_param)}"
            print(f"DEBUG - Salary filter: {salary_param}")
        
        print(f"🌐 Navigating to: {url}")
        
        # Save the original search URL (without any job view parameters)
        original_search_url = url
        
        # Navigate with retry logic for Cloudflare
        max_retries = getattr(settings, "MAX_RETRIES", 3)
        cloudflare_retries = 0
        
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
                
                # Recreate browser for fresh start with rotated proxy
                # Properly close existing page and cleanup resources
                if page:
                    await close_page_with_tracking(page)
                
                # Rotate proxy on Cloudflare block - force_new will cleanup old resources
                browser, context = await get_browser(force_new=True, rotate_proxy=True)
                page = await create_page_with_tracking(context)
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
                
                # Recreate browser with rotated proxy
                # Properly close existing page and cleanup resources
                if page:
                    await close_page_with_tracking(page)
                
                # Rotate proxy on navigation error - force_new will cleanup old resources
                browser, context = await get_browser(force_new=True, rotate_proxy=True)
                page = await create_page_with_tracking(context)
        
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
        browser_alive = True  # Track if browser is still usable
        
        if not fetch_full_details:
            print("ℹ️  Fast mode: skipping job detail pages (using search results data only)")
        
        # NEW: Track job fetches and implement proxy rotation strategy
        job_fetch_count = 0  # Track number of jobs fetched for proxy rotation
        cloudflare_block_count = 0  # Track Cloudflare blocks on job pages
        max_cloudflare_blocks = getattr(settings, "MAX_JOB_PAGE_CLOUDFLARE_BLOCKS", 3)  # Stop fetching details after N blocks
        
        # For rotating residential proxies (like Webshare), recreate browser per job
        # This forces a fresh TCP connection and new IP from the proxy pool
        use_auto_rotating_proxy = getattr(settings, "USE_AUTO_ROTATING_PROXY", True)
        
        # Delays configuration
        min_delay_between_fetches = getattr(settings, "JOB_DETAIL_MIN_DELAY", 4.0)  # Longer delays for residential
        max_delay_between_fetches = getattr(settings, "JOB_DETAIL_MAX_DELAY", 8.0)  # More variation
        
        for card in job_cards[:max_results * 2]:  # Get more cards to account for filtering
            try:
                job = _extract_job_from_card(card, query, location)
                if job and job.title and job.url:
                    # Check if we should stop fetching details due to too many Cloudflare blocks
                    if cloudflare_block_count >= max_cloudflare_blocks:
                        print(f"  ⚠️  Too many Cloudflare blocks ({cloudflare_block_count}) - switching to fast mode (no job detail pages)")
                        fetch_full_details = False  # Disable detailed fetching
                    
                    # Enhanced extraction: Visit individual job page for complete data
                    # Only attempt if browser is still alive AND fetch_full_details is True
                    if fetch_full_details and browser_alive:
                        print(f"  → Fetching complete data from job page: {job.title}")
                        
                        # NEW: Add delay before fetching (randomized for more human-like behavior)
                        delay = random.uniform(min_delay_between_fetches, max_delay_between_fetches)
                        print(f"    ⏳ Waiting {delay:.1f}s before fetch...")
                        await asyncio.sleep(delay)
                        
                        # NEW: Perform human-like interactions BEFORE navigating to job page
                        if getattr(settings, "HUMANIZE", True):
                            try:
                                # Scroll a bit on search results page
                                await page.evaluate("window.scrollBy(0, Math.random() * 300)")
                                await page.wait_for_timeout(random.uniform(500, 1000))
                                # Move mouse randomly
                                await page.mouse.move(
                                    random.randint(100, 800),
                                    random.randint(100, 500)
                                )
                                await page.wait_for_timeout(random.uniform(200, 500))
                            except Exception:
                                pass
                        
                        # SIMPLE APPROACH: Recreate browser to get fresh proxy connection
                        # This forces Webshare to assign a new IP from the pool
                        rotate_browser_per_job = getattr(settings, "ROTATE_BROWSER_PER_JOB", True)
                        
                        if use_auto_rotating_proxy and rotate_browser_per_job:
                            try:
                                print(f"    🔄 Recreating browser to force fresh proxy connection...")
                                # Close current page
                                await close_page_with_tracking(page)
                                # Force new browser instance (closes all connections, gets new IP)
                                browser, context = await get_browser(force_new=True, rotate_proxy=False)
                                page = await create_page_with_tracking(context)
                                print(f"    ✓ Browser recreated with fresh proxy connection")
                                # Small delay after recreation
                                await page.wait_for_timeout(random.uniform(1000, 2000))
                            except Exception as browser_error:
                                print(f"    ⚠️  Error recreating browser: {browser_error}")
                                browser_alive = False
                                continue
                        
                        try:
                            # Check if page is still connected before navigating
                            if page.is_closed():
                                print("  ⚠️  Page was closed - skipping job detail extraction for remaining jobs")
                                browser_alive = False
                            else:
                                enhanced_job = await _extract_complete_job_details_from_url_playwright(
                                    page, job, original_search_url, 
                                    skip_nav_back=use_auto_rotating_proxy and rotate_browser_per_job
                                )
                                if enhanced_job:
                                    # Check if we got blocked (job would have Ray ID in requirements)
                                    if enhanced_job.requirements and any('Ray ID' in str(req) for req in enhanced_job.requirements):
                                        cloudflare_block_count += 1
                                        print(f"    ⚠️  Cloudflare block detected (total: {cloudflare_block_count}/{max_cloudflare_blocks})")
                                    else:
                                        job = enhanced_job
                                        job_fetch_count += 1
                                
                                # NEW: Add delay after fetching (slightly shorter than pre-fetch delay)
                                post_fetch_delay = random.uniform(1.5, 3.0)
                                await asyncio.sleep(post_fetch_delay)
                        except Exception as enhance_error:
                            error_msg = str(enhance_error).lower()
                            if "closed" in error_msg or "target" in error_msg:
                                # Browser/page was closed - stop trying to navigate
                                print(f"  ⚠️  Browser closed during job detail extraction - using basic data for remaining jobs")
                                browser_alive = False
                            else:
                                print(f"  ⚠️  Error enhancing job details: {enhance_error}")
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
        
        print(f"✓ Extracted {len(jobs)} jobs (fetched details from {job_fetch_count} job pages)")
        return jobs
        
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Error during scraping: {error_msg}")
        import traceback
        print(f"   Full traceback:\n{traceback.format_exc()}")
        
        # Check for resource exhaustion errors
        if "pthread_create" in error_msg or "Resource temporarily unavailable" in error_msg:
            print("🚨 Resource exhaustion detected - forcing full cleanup")
            await _force_cleanup_all_resources()
            raise Exception(f"System resource exhaustion - browser resources have been cleaned up. Please retry the request.")
        
        # Re-raise with more context if it's a browser-related error
        if "browser" in error_msg.lower() or "playwright" in error_msg.lower():
            await _force_cleanup_all_resources()
            raise Exception(f"Playwright browser error: {error_msg}. Ensure Playwright browsers are installed: python -m playwright install chromium")
        raise
    finally:
        # Always close the page properly
        if page:
            await close_page_with_tracking(page)


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
    
    # Truncate if too long
    if len(description) > 1000:
        description = description[:1000].rsplit(' ', 1)[0] + '...'
    
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
    Extract description from Indeed's full job page with comprehensive patterns.
    Indeed typically structures descriptions under headings like:
    - "Full job description"
    - "Company Description"
    - Direct description containers
    """
    
    # Strategy 1: Look for text following "Full job description" heading
    full_job_desc_heading = soup.find(string=re.compile(r'Full job description', re.IGNORECASE))
    if full_job_desc_heading:
        print(f"  ✓ Found 'Full job description' heading")
        # Get the parent and find the next sibling or descendants with text
        parent = full_job_desc_heading.find_parent()
        if parent:
            # Try to find the next div/section after the heading
            next_section = parent.find_next_sibling()
            if next_section:
                text = next_section.get_text(separator='\n', strip=True)
                if len(text) > 100:
                    text = _clean_and_format_description(text)
                    print(f"  ✓ Extracted {len(text)} chars from section after 'Full job description'")
                    return text
            
            # Alternative: get all text from parent onwards
            remaining_text = []
            current = parent.find_next_sibling()
            while current:
                text_content = current.get_text(separator='\n', strip=True)
                if text_content and len(text_content) > 20:
                    remaining_text.append(text_content)
                current = current.find_next_sibling()
                # Limit to prevent getting too much
                if len(remaining_text) > 10:
                    break
            
            if remaining_text:
                combined = '\n\n'.join(remaining_text)
                combined = _clean_and_format_description(combined)
                if len(combined) > 100:
                    print(f"  ✓ Extracted {len(combined)} chars from siblings after 'Full job description'")
                    return combined
    
    # Strategy 2: Look for "Company Description" heading
    company_desc_heading = soup.find(string=re.compile(r'Company Description', re.IGNORECASE))
    if company_desc_heading:
        print(f"  ✓ Found 'Company Description' heading")
        parent = company_desc_heading.find_parent()
        if parent:
            # Get text from parent and its siblings
            text_parts = [parent.get_text(separator='\n', strip=True)]
            next_elem = parent.find_next_sibling()
            count = 0
            while next_elem and count < 5:
                text_content = next_elem.get_text(separator='\n', strip=True)
                if text_content:
                    text_parts.append(text_content)
                next_elem = next_elem.find_next_sibling()
                count += 1
            
            combined = '\n\n'.join(text_parts)
            combined = _clean_and_format_description(combined)
            if len(combined) > 100:
                print(f"  ✓ Extracted {len(combined)} chars from 'Company Description' section")
                return combined
    
    # Strategy 3: Primary Indeed selectors for job description container
    desc_selectors = [
        # Most common Indeed job description selectors
        'div.jobsearch-jobDescriptionText',
        'div[class*="jobsearch-jobDescriptionText"]',
        'div#jobDescriptionText',
        'div[id*="jobDescriptionText"]',
        
        # Data attribute selectors
        'div[data-testid="job-description"]',
        'div[data-testid="jobsearch-JobComponent-description"]',
        
        # Class-based selectors
        'div[class*="jobDescriptionText"]',
        'div[class*="job-description"]',
        'div[class*="jobDescription"]',
        
        # Nested selectors
        'div[class*="jobsearch-JobComponent"] div[class*="jobDescriptionText"]',
        'article div[class*="jobDescriptionText"]',
        
        # Alternative structures
        'div[class*="jobsearch-JobComponent-description"]',
        'section[class*="jobDescription"]',
    ]
    
    for selector in desc_selectors:
        elements = soup.select(selector)
        for elem in elements:
            text = elem.get_text(separator='\n', strip=True)
            if len(text) > 100:
                text = _clean_and_format_description(text)
                if len(text) > 100:
                    print(f"  ✓ Found description using selector '{selector}': {len(text)} characters")
                    return text
    
    # Strategy 4: Look for large text blocks in divs with specific patterns
    all_divs = soup.find_all('div')
    for div in all_divs:
        # Skip divs with too many child divs (likely containers)
        if len(div.find_all('div', recursive=False)) > 5:
            continue
        
        text = div.get_text(separator='\n', strip=True)
        
        # Check if this looks like a job description
        if (len(text) > 300 and  # Substantial length
            len(text.split()) > 50 and  # Multiple words
            not text.startswith('Apply') and
            not text.startswith('Sign in') and
            'job description' in text.lower()[:200]):  # Contains job description in first part
            
            text = _clean_and_format_description(text)
            print(f"  ✓ Found description in generic div: {len(text)} characters")
            return text
    
    # Strategy 5: Fallback - look for the main content area
    main_selectors = [
        'main',
        'article',
        'div[role="main"]',
        'div[class*="mainContent"]',
        'div[class*="main-content"]'
    ]
    
    for selector in main_selectors:
        elem = soup.select_one(selector)
        if elem:
            # Find the largest text block within main
            text_blocks = []
            for child in elem.find_all(['div', 'section', 'article']):
                text = child.get_text(separator='\n', strip=True)
                if len(text) > 300 and len(text.split()) > 50:
                    text_blocks.append((len(text), text))
            
            if text_blocks:
                # Get the longest block
                text_blocks.sort(reverse=True)
                text = _clean_and_format_description(text_blocks[0][1])
                print(f"  ✓ Found description in main content area: {len(text)} characters")
                return text
    
    # Strategy 6: Last resort - look for any substantial paragraph clusters
    paragraphs = soup.find_all('p')
    if len(paragraphs) > 3:
        # Combine consecutive paragraphs
        combined_text = '\n\n'.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50])
        combined_text = _clean_and_format_description(combined_text)
        if len(combined_text) > 300:
            print(f"  ✓ Found description from paragraph clustering: {len(combined_text)} characters")
            return combined_text
    
    print(f"  ⚠ No description found using any method")
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
                if job_retry_count >= max_job_retries:
                    print(f"    ❌ Job page blocked by Cloudflare after {job_retry_count + 1} attempts - skipping enhanced details")
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
            
            # Break out of retry loop on success
            break
            
        except Exception as e:
            if job_retry_count >= max_job_retries:
                print(f"    ⚠️  Error extracting enhanced details after {job_retry_count + 1} attempts: {e}")
                return job  # Continue with basic job data if enhancement fails
            
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