"""
Playwright-based Indeed scraper - COMPLETE FIXED VERSION

This version fixes all the lock errors and resource exhaustion issues that occur
after scraping 40-45 jobs.

Key improvements:
- Proper async lock handling
- Better page lifecycle management  
- Reduced resource consumption
- Proper cleanup strategies
- No more lock errors after 40+ jobs

Author: Fixed version
Date: 2024
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
        chrome_runtime=True,
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


# Global browser resources - FIXED with proper locking
_playwright: Optional["Playwright"] = None
_browser: Optional[Browser] = None
_context: Optional[BrowserContext] = None
_proxy_manager: Optional[ProxyManager] = None
_current_proxy: Optional[str] = None
_last_fetch = 0
_active_pages: Dict[int, Page] = {}  # FIXED: Changed from Set to Dict to track actual page objects
_resource_lock: Optional[asyncio.Lock] = None  # FIXED: Will be initialized in async context
_max_concurrent_pages = 3  # FIXED: Reduced from 5 to prevent resource exhaustion
_browser_creation_count = 0
_last_cleanup_time = 0
_cleanup_interval = 300  # 5 minutes
_scrape_count = 0
_max_scrapes_before_cleanup = 15  # FIXED: Reduced from 20 to cleanup more frequently


def _get_or_create_lock():
    """FIXED: Get or create the resource lock in async context."""
    global _resource_lock
    if _resource_lock is None:
        try:
            loop = asyncio.get_running_loop()
            _resource_lock = asyncio.Lock()
        except RuntimeError:
            # No running loop yet, will be created later
            pass
    return _resource_lock


def _parse_proxy_for_playwright(proxy_url: str) -> Optional[Dict]:
    """Parse a proxy URL into Playwright's proxy format."""
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
    """FIXED: Force cleanup of all Playwright resources with proper error handling."""
    global _playwright, _browser, _context, _active_pages, _browser_creation_count, _scrape_count
    
    print("🧹 Force cleaning up all Playwright resources...")
    
    # Close all active pages first
    pages_to_close = list(_active_pages.values())
    for page in pages_to_close:
        try:
            if not page.is_closed():
                await page.close()
        except Exception as e:
            print(f"  ⚠️  Error closing page: {e}")
    
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
    
    if len(_active_pages) > _max_concurrent_pages:
        should_cleanup = True
        reason = f"too many active pages ({len(_active_pages)})"
    
    if should_cleanup:
        print(f"🔄 Cleanup triggered: {reason}")
        await _force_cleanup_all_resources()
        _last_cleanup_time = now
        _scrape_count = 0


async def get_browser(force_new: bool = False, rotate_proxy: bool = False) -> tuple[Browser, BrowserContext]:
    """
    FIXED: Get or create a Playwright browser instance with proper locking.
    
    Args:
        force_new: If True, create a new browser instance
        rotate_proxy: If True, rotate to the next proxy before creating browser
        
    Returns:
        Tuple of (browser, context)
    """
    global _playwright, _browser, _context, _browser_creation_count, _active_pages
    
    if not PLAYWRIGHT_AVAILABLE:
        raise ImportError("Playwright is not installed. Install with: pip install playwright && python -m playwright install chromium")
    
    # FIXED: Get or create lock in async context
    resource_lock = _get_or_create_lock()
    if resource_lock is None:
        resource_lock = asyncio.Lock()
    
    # FIXED: Use lock to prevent concurrent browser creation
    async with resource_lock:
        # Check if we need to cleanup resources
        await _check_and_cleanup_resources()
        
        # Check if existing browser is still usable
        if _browser and not force_new:
            try:
                # Check if browser is still alive
                if _browser.is_connected():
                    # Check if we should rotate proxy based on time
                    if _proxy_manager and _proxy_manager.should_rotate():
                        print("🔄 Proxy rotation interval reached - recreating context")
                        # Only recreate context, not entire browser
                        if _context:
                            try:
                                await _context.close()
                            except:
                                pass
                            _context = None
                    else:
                        # Browser and context are good
                        if _context:
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
                await _force_cleanup_all_resources()
                raise Exception(f"{error_msg}. Run: python -m playwright install chromium")
        
        # Launch browser with optimized settings for headless mode
        if not _browser:
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
                await _force_cleanup_all_resources()
                raise Exception(f"{error_msg}. Run: python -m playwright install chromium")
        
        # Create context if needed
        if not _context:
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
            if STEALTH_AVAILABLE and _stealth:
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
    resource_lock = _get_or_create_lock()
    if resource_lock:
        async with resource_lock:
            await _force_cleanup_all_resources()
    else:
        await _force_cleanup_all_resources()


async def create_page_with_tracking(context: BrowserContext) -> Page:
    """
    FIXED: Create a new page with resource tracking.
    Ensures we don't exceed max pages per context.
    """
    global _active_pages
    
    # FIXED: Check if we have too many active pages and close oldest
    if len(_active_pages) >= _max_concurrent_pages:
        print(f"⚠️  Maximum pages ({_max_concurrent_pages}) reached, closing oldest page...")
        # Close the oldest page (first in dict)
        oldest_page_id = next(iter(_active_pages))
        oldest_page = _active_pages[oldest_page_id]
        try:
            if not oldest_page.is_closed():
                await oldest_page.close()
        except Exception as e:
            print(f"⚠️  Error closing oldest page: {e}")
        del _active_pages[oldest_page_id]
    
    page = await context.new_page()
    page_id = id(page)
    _active_pages[page_id] = page  # FIXED: Store page object, not just ID
    print(f"📄 Created page (active: {len(_active_pages)})")
    return page


async def close_page_with_tracking(page: Page):
    """FIXED: Close a page and remove from tracking."""
    global _active_pages
    
    if page:
        page_id = id(page)
        try:
            if not page.is_closed():
                await page.close()
        except Exception as e:
            print(f"⚠️  Error closing page: {e}")
        
        # FIXED: Remove from dict instead of set
        if page_id in _active_pages:
            del _active_pages[page_id]
        print(f"📄 Closed page (active: {len(_active_pages)})")


async def close_all_pages():
    """FIXED: Helper function to close all tracked pages."""
    global _active_pages
    
    pages_to_close = list(_active_pages.values())
    for page in pages_to_close:
        try:
            if not page.is_closed():
                await page.close()
        except Exception as e:
            print(f"⚠️  Error closing page: {e}")
    
    _active_pages.clear()
    print(f"📄 Closed all pages")


def get_proxy_stats() -> Optional[Dict]:
    """Get current proxy statistics for monitoring/debugging."""
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
    """Get current browser resource statistics for monitoring."""
    return {
        "playwright_active": _playwright is not None,
        "browser_connected": _browser.is_connected() if _browser else False,
        "context_active": _context is not None,
        "active_pages": len(_active_pages),
        "max_concurrent_pages": _max_concurrent_pages,
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


def _extract_job_from_card(card, query: str, location: Optional[str]) -> Optional[Job]:
    """Extract comprehensive job data from a job card element."""
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
        return None


# Full page extraction functions
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
    """Extract description from Indeed's full job page with comprehensive patterns."""
    
    # Strategy 1: Look for text following "Full job description" heading
    full_job_desc_heading = soup.find(string=re.compile(r'Full job description', re.IGNORECASE))
    if full_job_desc_heading:
        print(f"  ✓ Found 'Full job description' heading")
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
        'div.jobsearch-jobDescriptionText',
        'div[class*="jobsearch-jobDescriptionText"]',
        'div#jobDescriptionText',
        'div[id*="jobDescriptionText"]',
        'div[data-testid="job-description"]',
        'div[data-testid="jobsearch-JobComponent-description"]',
        'div[class*="jobDescriptionText"]',
        'div[class*="job-description"]',
        'div[class*="jobDescription"]',
        'div[class*="jobsearch-JobComponent"] div[class*="jobDescriptionText"]',
        'article div[class*="jobDescriptionText"]',
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
    
    # Strategy 4: Look for large text blocks in divs
    all_divs = soup.find_all('div')
    for div in all_divs:
        if len(div.find_all('div', recursive=False)) > 5:
            continue
        
        text = div.get_text(separator='\n', strip=True)
        
        if (len(text) > 300 and
            len(text.split()) > 50 and
            not text.startswith('Apply') and
            not text.startswith('Sign in') and
            'job description' in text.lower()[:200]):
            
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
            text_blocks = []
            for child in elem.find_all(['div', 'section', 'article']):
                text = child.get_text(separator='\n', strip=True)
                if len(text) > 300 and len(text.split()) > 50:
                    text_blocks.append((len(text), text))
            
            if text_blocks:
                text_blocks.sort(reverse=True)
                text = _clean_and_format_description(text_blocks[0][1])
                print(f"  ✓ Found description in main content area: {len(text)} characters")
                return text
    
    # Strategy 6: Last resort - paragraph clustering
    paragraphs = soup.find_all('p')
    if len(paragraphs) > 3:
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
    """Extract company URL from Indeed's full job page."""
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
    
    # Strategy 3: Look for links containing /cmp/
    all_links = soup.find_all('a', href=True, limit=50)
    for link in all_links:
        href = link.get('href', '')
        if '/cmp/' in href or '/company/' in href:
            if '/reviews' not in href and '/jobs' not in href:
                link_text = link.get_text(strip=True)
                if link_text and 3 < len(link_text) < 100:
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
    """Extract complete job details by navigating to the individual job page URL."""
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
            
            # Set proper headers before navigation
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
                'Referer': referer,
            })
            
            # Navigate with shorter timeout
            job_page_timeout = getattr(settings, "JOB_PAGE_TIMEOUT", 15000)
            
            try:
                response = await page.goto(job.url, wait_until="commit", timeout=job_page_timeout)
                status_code = response.status if response else 0
                print(f"    ✓ Navigation response: {status_code}")
            except Exception as nav_err:
                print(f"    ⚠️  Navigation timeout, continuing anyway...")
            
            # Wait for page to load
            print(f"    ⏳ Waiting for page to load...")
            
            try:
                await page.wait_for_selector('body', timeout=10000, state='attached')
            except:
                pass
            
            # Check for Cloudflare challenge
            cloudflare_detected = False
            for attempt in range(10):
                page_content = await page.content()
                page_title = await page.title()
                
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
                        print(f"    🔐 Cloudflare challenge detected - waiting...")
                    await asyncio.sleep(1)
                    continue
                else:
                    if cloudflare_detected:
                        print(f"    ✓ Cloudflare challenge completed after {attempt}s")
                    break
            
            # Wait for page to stabilize
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
                or "Just a moment" in current_title
                or ("Ray ID" in full_page_content and len(full_page_content) < 5000)
            )
            
            has_job_content = (
                'jobsearch-JobComponent' in full_page_content
                or 'jobDescriptionText' in full_page_content
                or 'job-description' in full_page_content.lower()
                or (len(full_page_content) > 10000 and 'indeed.com' in full_page_content)
            )
            
            is_actually_blocked = has_cloudflare_indicators and not has_job_content
            
            if is_actually_blocked:
                if job_retry_count >= max_job_retries:
                    print(f"    ❌ Job page blocked by Cloudflare after {job_retry_count + 1} attempts")
                    return job
                
                backoff = random.uniform(5.0, 8.0) * (1 + job_retry_count)
                print(f"    ⚠️  Still blocked, retry {job_retry_count + 1}/{max_job_retries}, waiting {backoff:.1f}s...")
                
                if getattr(settings, "HUMANIZE", True):
                    await _perform_human_interactions_playwright(page)
                
                await asyncio.sleep(backoff)
                job_retry_count += 1
                continue
            
            # Successfully got job page content
            print(f"    ✓ Job page loaded ({len(full_page_content)} chars)")
            full_page_soup = BeautifulSoup(full_page_content, 'html.parser')
            
            # Extract enhanced details from the full page
            if not job.job_id:
                enhanced_job_id = _extract_job_id_from_full_page(full_page_soup, page.url)
                if enhanced_job_id:
                    job.job_id = enhanced_job_id
                    print(f"    ✓ Enhanced job ID: {enhanced_job_id}")
            
            if not job.company_url:
                enhanced_company_url = _extract_company_url_from_full_page(full_page_soup, page)
                if enhanced_company_url:
                    job.company_url = enhanced_company_url
                    print(f"    ✓ Enhanced company URL: {enhanced_company_url}")
            
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
            
            # Update job with enhanced details
            if enhanced_salary and (not job.salary_range or len(enhanced_salary) > len(job.salary_range or "")):
                job.salary_range = enhanced_salary
                print(f"    ✓ Enhanced salary: {enhanced_salary}")
            
            if enhanced_employment and not job.employment_type:
                job.employment_type = enhanced_employment
                print(f"    ✓ Enhanced employment: {enhanced_employment}")
            
            if enhanced_date and not job.posted_date:
                job.posted_date = enhanced_date
                print(f"    ✓ Enhanced date: {enhanced_date}")
            
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
            
            break
            
        except Exception as e:
            if job_retry_count >= max_job_retries:
                print(f"    ⚠️  Error extracting enhanced details: {e}")
                return job
            
            print(f"    ⚠️  Error on attempt {job_retry_count + 1}, retrying: {e}")
            job_retry_count += 1
            await asyncio.sleep(random.uniform(2.0, 4.0))
            continue
    
    # Navigate back to search results
    if not skip_nav_back:
        try:
            print(f"    ← Navigating back to search results")
            back_nav_timeout = getattr(settings, "BACK_NAV_TIMEOUT", 30000)
            await page.goto(original_url, wait_until="domcontentloaded", timeout=back_nav_timeout)
            await page.wait_for_timeout(2000)
        except Exception as nav_error:
            print(f"    ⚠️  Warning: Could not navigate back: {nav_error}")
    else:
        print(f"    ↻ Skipping navigation back")
    
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
    
    # Experience level filter
    if experience_level and job.experience_level:
        if experience_level.lower() not in job.experience_level.lower():
            return False
    
    return True


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
    fetch_full_details: bool = True
) -> List[Job]:
    """
    FIXED: Scrape Indeed jobs using Playwright with proper resource management.
    
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
        fetch_full_details: Set to False for faster scraping
        
    Returns:
        List of Job objects
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise ImportError("Playwright is not installed")
    
    global _last_fetch, _scrape_count
    
    # Increment scrape counter
    _scrape_count += 1
    print(f"🔍 Starting scrape #{_scrape_count} (cleanup threshold: {_max_scrapes_before_cleanup})")
    
    # Rate limiting
    now = time.monotonic()
    jitter = random.uniform(0, 0.75)
    wait = settings.MIN_DELAY + jitter - (now - _last_fetch)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_fetch = time.monotonic()
    
    page = None  # FIXED: Initialize outside try block
    
    try:
        browser, context = await get_browser()
        page = await create_page_with_tracking(context)
        
        # Build Indeed URL
        base_url = "https://www.indeed.com/jobs"
        url = f"{base_url}?q={quote_plus(query)}"
        if location:
            url += f"&l={quote_plus(location)}"
        
        print(f"🌐 Navigating to: {url}")
        original_search_url = url
        
        # Navigate with retry logic for Cloudflare
        max_retries = getattr(settings, "MAX_RETRIES", 3)
        cloudflare_retries = 0
        
        while True:
            try:
                navigation_timeout = 30000
                navigation_success = False
                
                # Try domcontentloaded first
                try:
                    print(f"   Navigating (domcontentloaded)...")
                    await page.goto(url, wait_until="domcontentloaded", timeout=navigation_timeout)
                    print("✓ Navigation completed")
                    navigation_success = True
                except Exception as nav_error1:
                    print(f"⚠️  domcontentloaded failed, trying commit...")
                    
                    try:
                        await page.goto(url, wait_until="commit", timeout=15000)
                        print("✓ Navigation started (commit)")
                        await page.wait_for_selector('body', timeout=20000, state='attached')
                        print("✓ Body loaded")
                        
                        try:
                            await page.wait_for_selector(
                                'div[data-jk], div.job_seen_beacon, div[class*="job"]',
                                timeout=15000,
                                state='attached'
                            )
                            print("✓ Job content detected")
                        except:
                            await page.wait_for_timeout(3000)
                            body_text = await page.evaluate("document.body ? document.body.innerText.length : 0")
                            if body_text > 100:
                                print(f"✓ Page has content ({body_text} chars)")
                        
                        navigation_success = True
                    except Exception as commit_error:
                        print(f"⚠️  All navigation strategies failed")
                        navigation_success = False
                
                # Wait for page content
                if navigation_success:
                    await page.wait_for_timeout(2000)
                else:
                    await page.wait_for_timeout(3000)
                    try:
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                        await page.wait_for_timeout(1500)
                    except:
                        pass
                
                # Try to wait for job listings
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
                
                # Check for Cloudflare blocking
                has_cloudflare_indicators = (
                    "Checking your browser" in page_html
                    or "Enable JavaScript and cookies to continue" in page_html
                    or "challenge-platform" in page_html
                    or "cf-browser-verification" in page_html
                    or "Just a moment" in page_html
                    or "Ray ID" in page_html
                )
                
                has_indeed_content = (
                    'id="mosaic-provider-jobcards"' in page_html
                    or 'class="jobsearch-ResultsList"' in page_html
                    or 'data-jk=' in page_html
                    or 'class="job_seen_beacon"' in page_html
                )
                
                is_actually_blocked = has_cloudflare_indicators and not has_indeed_content
                
                if has_indeed_content:
                    is_actually_blocked = False
                    print("✓ Indeed content detected")
                
                if not is_actually_blocked:
                    _mark_proxy_success()
                    break
                
                # Cloudflare detected - retry
                if cloudflare_retries >= max_retries:
                    raise CloudflareBlockedError(
                        f"Indeed blocked by Cloudflare after {cloudflare_retries + 1} attempts"
                    )
                
                backoff = random.uniform(3.0, 6.0) * (1 + 0.5 * cloudflare_retries)
                print(f"⚠️  Cloudflare detected, retry {cloudflare_retries + 1}/{max_retries}")
                
                if getattr(settings, "HUMANIZE", True):
                    await _perform_human_interactions_playwright(page)
                
                await context.clear_cookies()
                await asyncio.sleep(backoff)
                
                # FIXED: Properly close page before getting new browser
                await close_page_with_tracking(page)
                
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
                
                if cloudflare_retries >= max_retries:
                    raise Exception(f"Navigation failed after {max_retries} retries: {nav_error}")
                
                print(f"⚠️  Navigation error, retry {cloudflare_retries + 1}/{max_retries}")
                cloudflare_retries += 1
                await asyncio.sleep(random.uniform(2.0, 4.0))
                
                # FIXED: Properly close page before getting new browser
                await close_page_with_tracking(page)
                
                browser, context = await get_browser(force_new=True, rotate_proxy=True)
                page = await create_page_with_tracking(context)
        
        # Progressive scroll to load more jobs
        await _progressive_scroll_playwright(page)
        
        # Get page content
        content = await page.content()
        
        # Parse with BeautifulSoup
        soup = BeautifulSoup(content, "html.parser")
        
        # Extract job cards
        job_cards = _find_job_cards_indeed(soup)
        
        print(f"📋 Found {len(job_cards)} job cards")
        
        # Extract job data
        jobs = []
        browser_alive = True
        
        if not fetch_full_details:
            print("ℹ️  Fast mode: skipping job detail pages")
        
        # Track job fetches
        job_fetch_count = 0
        cloudflare_block_count = 0
        max_cloudflare_blocks = getattr(settings, "MAX_JOB_PAGE_CLOUDFLARE_BLOCKS", 3)
        
        # FIXED: Disabled aggressive browser rotation per job
        # Instead, reuse browser/context and only rotate on errors
        min_delay_between_fetches = getattr(settings, "JOB_DETAIL_MIN_DELAY", 2.0)  # Reduced delay
        max_delay_between_fetches = getattr(settings, "JOB_DETAIL_MAX_DELAY", 4.0)
        
        for card in job_cards[:max_results * 2]:
            try:
                job = _extract_job_from_card(card, query, location)
                if job and job.title and job.url:
                    # Check if too many Cloudflare blocks
                    if cloudflare_block_count >= max_cloudflare_blocks:
                        print(f"  ⚠️  Too many Cloudflare blocks - switching to fast mode")
                        fetch_full_details = False
                    
                    # Enhanced extraction
                    if fetch_full_details and browser_alive:
                        print(f"  → Fetching details: {job.title}")
                        
                        # Add delay
                        delay = random.uniform(min_delay_between_fetches, max_delay_between_fetches)
                        print(f"    ⏳ Waiting {delay:.1f}s...")
                        await asyncio.sleep(delay)
                        
                        # Human-like interactions
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
                        
                        try:
                            # Check if page is still connected
                            if page.is_closed():
                                print("  ⚠️  Page closed - skipping remaining job details")
                                browser_alive = False
                            else:
                                enhanced_job = await _extract_complete_job_details_from_url_playwright(
                                    page, job, original_search_url, skip_nav_back=False
                                )
                                if enhanced_job:
                                    # Check for Cloudflare block
                                    if enhanced_job.requirements and any('Ray ID' in str(req) for req in enhanced_job.requirements):
                                        cloudflare_block_count += 1
                                        print(f"    ⚠️  Cloudflare block detected ({cloudflare_block_count}/{max_cloudflare_blocks})")
                                    else:
                                        job = enhanced_job
                                        job_fetch_count += 1
                                
                                # Post-fetch delay
                                await asyncio.sleep(random.uniform(1.5, 3.0))
                        except Exception as enhance_error:
                            error_msg = str(enhance_error).lower()
                            if "closed" in error_msg or "target" in error_msg:
                                print(f"  ⚠️  Browser closed - using basic data")
                                browser_alive = False
                            else:
                                print(f"  ⚠️  Error enhancing: {enhance_error}")
                    
                    # Apply filters
                    if _should_include_job(job, job_type, salary_min, salary_max, experience_level, employment_type, days_old):
                        jobs.append(job)
                        
                        if len(jobs) >= max_results:
                            break
            except Exception as e:
                print(f"⚠️  Error extracting job: {e}")
                continue
        
        print(f"✓ Extracted {len(jobs)} jobs (fetched details from {job_fetch_count} pages)")
        return jobs
        
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Error during scraping: {error_msg}")
        
        # Check for resource exhaustion
        if "pthread_create" in error_msg or "Resource temporarily unavailable" in error_msg:
            print("🚨 Resource exhaustion detected - forcing cleanup")
            await _force_cleanup_all_resources()
            raise Exception("System resource exhaustion - resources cleaned up. Please retry.")
        
        raise
        
    finally:
        # FIXED: Always close page in finally block
        if page:
            await close_page_with_tracking(page)
        
        # FIXED: Periodic resource check
        if _scrape_count % 5 == 0:
            print(f"🔍 Periodic resource check (scrape #{_scrape_count})")
            stats = get_browser_resource_stats()
            print(f"   Stats: {stats}")
            
            if stats['active_pages'] > 0:
                print(f"   ⚠️  {stats['active_pages']} pages still open, cleaning...")
                await close_all_pages()