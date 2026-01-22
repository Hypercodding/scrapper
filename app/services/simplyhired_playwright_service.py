"""
Playwright-based SimplyHired scraper with stealth - Optimized to avoid Cloudflare detection.

Benefits:
- Better Cloudflare bypass with playwright-stealth
- No ChromeDriver version issues (bundles its own browser)
- Better resource management
- More stable in headless mode
- Better error handling
- Fast navigation with smart waiting strategies
- Proxy rotation support for avoiding blocks
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
    print("✓ playwright-stealth loaded successfully for SimplyHired")
except ImportError as ie:
    STEALTH_AVAILABLE = False
    _stealth = None
    print(f"ℹ️  playwright-stealth not installed: {ie}. Install with: pip install playwright-stealth")
except Exception as stealth_error:
    STEALTH_AVAILABLE = False
    _stealth = None
    print(f"⚠️  Error initializing playwright-stealth: {stealth_error}")

# Import extraction functions from Selenium service
from app.services.simplyhired_selenium_service import (
    _format_location_for_simplyhired,
    _get_simplyhired_employment_filter,
    _get_simplyhired_date_filter,
    _get_simplyhired_job_type_filter,
    _find_job_cards_simplyhired,
    _is_valid_job_card_simplyhired,
    _extract_title_simplyhired,
    _extract_company_info_simplyhired,
    _extract_location_info_simplyhired,
    _extract_salary_simplyhired,
    _extract_job_types_simplyhired,
    _extract_experience_level_simplyhired,
    _extract_posted_date_simplyhired,
    _extract_description_simplyhired,
    _extract_job_url_simplyhired,
    _extract_job_id_simplyhired,
    _extract_skills_simplyhired,
    _extract_requirements_simplyhired,
    _extract_benefits_simplyhired,
    _extract_industry_simplyhired,
    _extract_company_size_simplyhired,
    _extract_detailed_job_info_simplyhired,
    _is_end_of_results_simplyhired,
    _create_job_id,
    _extract_salary_from_full_page_simplyhired,
    _extract_employment_from_full_page_simplyhired,
    _extract_date_from_full_page_simplyhired,
    _extract_description_from_full_page_simplyhired,
    _extract_experience_from_full_page_simplyhired,
    _extract_benefits_from_full_page_simplyhired,
    _extract_requirements_from_full_page_simplyhired,
    _extract_skills_from_full_page_simplyhired,
    _extract_industry_from_full_page_simplyhired,
    _extract_company_size_from_full_page_simplyhired,
    _extract_job_id_from_full_page_simplyhired,
    _extract_company_from_full_page_simplyhired,
    _extract_location_from_full_page_simplyhired,
    _validate_description,
    _clean_description_text,
)

# Global browser resources - properly managed to prevent resource leaks
_playwright: Optional["Playwright"] = None
_browser: Optional[Browser] = None
_context: Optional[BrowserContext] = None
_proxy_manager: Optional[ProxyManager] = None
_current_proxy: Optional[str] = None
_last_fetch = 0
_active_pages: Set[int] = set()  # Track active page IDs
_resource_lock = asyncio.Lock() if PLAYWRIGHT_AVAILABLE else None  # Lock for resource management
_max_pages_per_context = 5  # Maximum concurrent pages
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
    """Raised when SimplyHired returns a Cloudflare/turnstile block page."""
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


async def get_browser(force_new: bool = False, rotate_proxy: bool = False) -> tuple[Browser, BrowserContext]:
    """
    Get or create a Playwright browser instance with stealth and proxy support.
    
    Args:
        force_new: If True, create a new browser instance
        rotate_proxy: If True, rotate to the next proxy before creating browser
        
    Returns:
        Tuple of (browser, context)
    """
    global _playwright, _browser, _context, _browser_creation_count, _active_pages
    
    if not PLAYWRIGHT_AVAILABLE:
        raise ImportError("Playwright is not installed. Install with: pip install playwright && python -m playwright install chromium")
    
    # Check if we need to cleanup resources
    await _check_and_cleanup_resources()
    
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


async def _extract_complete_job_details_from_url_playwright(page: Page, job: Job, original_search_url: str) -> Optional[Job]:
    """
    Extract complete job details by navigating to the individual job page URL.
    This fetches the full job description, requirements, benefits, skills, etc.
    """
    if not job.url:
        return job
    
    try:
        # Navigate to the individual job page
        print(f"    → Navigating to job page: {job.url}")
        
        job_page_timeout = getattr(settings, "JOB_PAGE_TIMEOUT", 60000)  # 60 seconds default
        await page.goto(job.url, wait_until="domcontentloaded", timeout=job_page_timeout)
        
        # Wait for page to load
        await asyncio.sleep(random.uniform(2.0, 3.5))
        
        # Try to wait for job content
        try:
            await page.wait_for_selector(
                "div[class*='description'], div[class*='Description'], article, main",
                timeout=15000
            )
        except:
            pass  # Continue even if selector not found
        
        # Try to extract description using Playwright's page evaluation first (more reliable)
        enhanced_description = None
        try:
            # Look for description using JavaScript - more reliable than parsing HTML
            description_text = await page.evaluate("""
                () => {
                    // Try multiple selectors
                    const selectors = [
                        'div[data-testid="viewjob-jobDescription"]',
                        'div[data-testid="jobDescription"]',
                        'div[class*="JobDescription"]',
                        'div[class*="jobDescription"]',
                        'div[class*="job-description"]',
                        'div[class*="fullDescription"]',
                    ];
                    
                    for (const selector of selectors) {
                        const elem = document.querySelector(selector);
                        if (elem) {
                            // Remove similar jobs sections
                            const similar = elem.querySelector('[class*="similar"], [class*="related"]');
                            if (similar) similar.remove();
                            
                            let text = elem.innerText || elem.textContent || '';
                            text = text.trim();
                            
                            // Check if it's substantial and doesn't start with nav text
                            if (text.length > 200 && 
                                !text.toLowerCase().startsWith('skip to content') &&
                                !text.toLowerCase().startsWith('job title')) {
                                return text;
                            }
                        }
                    }
                    
                    // Fallback: Look for heading "Full Job Description" and get content after it
                    const headings = document.querySelectorAll('h2, h3, h4');
                    for (const heading of headings) {
                        const headingText = (heading.innerText || heading.textContent || '').toLowerCase();
                        if (headingText.includes('full job description') || headingText.includes('job description')) {
                            // Get parent container
                            let parent = heading.parentElement;
                            if (parent) {
                                // Get all text from parent, but skip the heading
                                let text = parent.innerText || parent.textContent || '';
                                // Remove the heading text from the start
                                const headingTextFull = heading.innerText || heading.textContent || '';
                                if (text.startsWith(headingTextFull)) {
                                    text = text.substring(headingTextFull.length).trim();
                                }
                                
                                // Remove similar jobs if present
                                const similar = parent.querySelector('[class*="similar"], [class*="related"]');
                                if (similar) {
                                    const similarText = similar.innerText || similar.textContent || '';
                                    text = text.replace(similarText, '').trim();
                                }
                                
                                if (text.length > 200) {
                                    return text;
                                }
                            }
                        }
                    }
                    
                    return null;
                }
            """)
            
            if description_text and isinstance(description_text, str) and len(description_text) > 200:
                # Clean the description
                enhanced_description = _clean_description_text(description_text)
                if enhanced_description and len(enhanced_description) > 200:
                    print(f"    ✓ Extracted description via Playwright: {len(enhanced_description)} characters")
        except Exception as e:
            print(f"    ⚠️  Error extracting description via Playwright: {e}")
        
        # Get full page content for other extractions
        full_page_content = await page.content()
        full_page_soup = BeautifulSoup(full_page_content, 'html.parser')
        
        # Extract job ID from full page if not already present
        if not job.job_id:
            enhanced_job_id = _extract_job_id_from_full_page_simplyhired(full_page_soup, page.url)
            if enhanced_job_id:
                job.job_id = enhanced_job_id
                print(f"    ✓ Enhanced job ID: {enhanced_job_id}")
        
        # Extract enhanced details from the full page (if description not found via Playwright)
        enhanced_company, enhanced_company_url = _extract_company_from_full_page_simplyhired(full_page_soup)
        enhanced_location = _extract_location_from_full_page_simplyhired(full_page_soup)
        enhanced_salary = _extract_salary_from_full_page_simplyhired(full_page_soup)
        enhanced_employment = _extract_employment_from_full_page_simplyhired(full_page_soup)
        enhanced_date = _extract_date_from_full_page_simplyhired(full_page_soup)
        
        # Only extract description from soup if we didn't get it via Playwright
        if not enhanced_description:
            enhanced_description = _extract_description_from_full_page_simplyhired(full_page_soup)
        enhanced_experience = _extract_experience_from_full_page_simplyhired(full_page_soup)
        enhanced_benefits = _extract_benefits_from_full_page_simplyhired(full_page_soup)
        enhanced_requirements = _extract_requirements_from_full_page_simplyhired(full_page_soup)
        enhanced_skills = _extract_skills_from_full_page_simplyhired(full_page_soup)
        enhanced_industry = _extract_industry_from_full_page_simplyhired(full_page_soup)
        enhanced_company_size = _extract_company_size_from_full_page_simplyhired(full_page_soup)
        
        # Update job with enhanced details (prioritize full page data)
        if enhanced_company and not job.company:
            job.company = enhanced_company
            print(f"    ✓ Enhanced company: {enhanced_company}")
        
        if enhanced_company_url and not job.company_url:
            job.company_url = enhanced_company_url
            print(f"    ✓ Enhanced company URL: {enhanced_company_url}")
        
        # Update location with validation - especially for remote jobs
        if enhanced_location:
            # Validate location - reject invalid patterns
            invalid_patterns = ['job title', 'skills', 'company', 'city, state', 'zip', 'search']
            location_lower = enhanced_location.lower()
            is_invalid = any(pattern in location_lower for pattern in invalid_patterns)
            
            # For remote jobs, ensure location is set to "Remote" if invalid or missing
            if job.remote_type == 'Remote':
                if is_invalid or not enhanced_location:
                    job.location = 'Remote'
                    print(f"    ✓ Set location to 'Remote' (invalid location detected)")
                elif not job.location:
                    job.location = enhanced_location
                    print(f"    ✓ Enhanced location: {enhanced_location}")
            elif not is_invalid and not job.location:
                job.location = enhanced_location
                print(f"    ✓ Enhanced location: {enhanced_location}")
            elif is_invalid and not job.location:
                # Invalid location and no existing location - try to infer from remote_type
                if job.remote_type == 'Hybrid':
                    job.location = 'Hybrid'
                elif job.remote_type == 'Remote':
                    job.location = 'Remote'
        elif not job.location:
            # No enhanced location found - set based on remote_type
            if job.remote_type == 'Remote':
                job.location = 'Remote'
                print(f"    ✓ Set location to 'Remote' (no location found)")
            elif job.remote_type == 'Hybrid':
                job.location = 'Hybrid'
                print(f"    ✓ Set location to 'Hybrid' (no location found)")
        
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
        # Prioritize full page description as it's more complete
        # But validate it first to ensure it's not navigation/header text
        if enhanced_description:
            # Clean the description
            cleaned_description = _clean_description_text(enhanced_description)
            
            # Be more lenient - if it's substantial and doesn't start with nav text, use it
            if cleaned_description and len(cleaned_description) > 50:
                # Check if it starts with navigation text
                first_200 = cleaned_description.lower()[:200]
                starts_with_nav = any(nav in first_200 for nav in ['skip to content', 'job title, skills', 'search jobs', 'sign in'])
                
                if _validate_description(cleaned_description):
                    # Valid description
                    if len(cleaned_description) > 100:
                        job.description = cleaned_description
                        print(f"    ✓ Enhanced description: {len(cleaned_description)} characters")
                    elif not job.description or len(job.description) < 100:
                        job.description = cleaned_description
                        print(f"    ✓ Updated description: {len(cleaned_description)} characters")
                elif not starts_with_nav and len(cleaned_description) > 300:
                    # Not validated but substantial and doesn't start with nav - likely valid
                    job.description = cleaned_description
                    print(f"    ✓ Enhanced description (lenient): {len(cleaned_description)} characters")
                else:
                    # Invalid description (likely navigation text) - keep existing or skip
                    if not job.description or len(job.description) < 100:
                        print(f"    ⚠️  Enhanced description appears to be navigation text - skipping (length: {len(cleaned_description)})")
                    else:
                        print(f"    ⚠️  Enhanced description invalid - keeping existing description")
        elif not job.description:
            print(f"    ⚠️  No description found on full page")
        
        if enhanced_experience and not job.experience_level:
            job.experience_level = enhanced_experience
            print(f"    ✓ Enhanced experience: {enhanced_experience}")
        
        if enhanced_benefits and (not job.benefits or len(job.benefits or []) < len(enhanced_benefits)):
            job.benefits = enhanced_benefits
            print(f"    ✓ Enhanced benefits: {len(enhanced_benefits)} items")
        
        if enhanced_requirements and (not job.requirements or len(job.requirements or []) < len(enhanced_requirements)):
            job.requirements = enhanced_requirements
            print(f"    ✓ Enhanced requirements: {len(enhanced_requirements)} items")
        
        # Benefits - Always update if found (SimplyHired shows these as tags)
        if enhanced_benefits:
            job.benefits = enhanced_benefits
            print(f"    ✓ Enhanced benefits: {len(enhanced_benefits)} items")
        
        # Requirements - Extract from job description if not in separate section
        if enhanced_requirements and (not job.requirements or len(job.requirements or []) < len(enhanced_requirements)):
            job.requirements = enhanced_requirements
            print(f"    ✓ Enhanced requirements: {len(enhanced_requirements)} items")
        
        # Skills - Always update if found (SimplyHired shows these as qualification tags)
        if enhanced_skills:
            job.skills = enhanced_skills
            print(f"    ✓ Enhanced skills: {len(enhanced_skills)} items")
        
        # Industry and company_size - Only set if clearly available (not typically on SimplyHired)
        if enhanced_industry:
            job.industry = enhanced_industry
            print(f"    ✓ Enhanced industry: {enhanced_industry}")
        
        if enhanced_company_size:
            job.company_size = enhanced_company_size
            print(f"    ✓ Enhanced company size: {enhanced_company_size}")
        
    except Exception as e:
        print(f"    ⚠️  Error extracting enhanced details: {e}")
        # Continue with basic job data if enhancement fails
    finally:
        # Navigate back to the original search results page
        try:
            print(f"    ← Navigating back to search results")
            back_nav_timeout = getattr(settings, "BACK_NAV_TIMEOUT", 30000)  # 30 seconds default
            await page.goto(original_search_url, wait_until="domcontentloaded", timeout=back_nav_timeout)
            await asyncio.sleep(2.0)  # Wait for page to load
        except Exception as nav_error:
            print(f"    ⚠️  Warning: Could not navigate back: {nav_error}")
            # If navigation back fails, it's not critical - we can continue
    
    return job


async def scrape_simplyhired_playwright(
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
    Scrape SimplyHired jobs using Playwright with stealth.
    
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
        fetch_full_details: If True, visit each job page to extract complete details
        
    Returns:
        List of Job objects
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise ImportError("Playwright is not installed. Install with: pip install playwright && python -m playwright install chromium")
    
    global _last_fetch, _scrape_count
    
    # Increment scrape counter for resource management
    _scrape_count += 1
    print(f"🔍 Starting SimplyHired scrape #{_scrape_count} (cleanup threshold: {_max_scrapes_before_cleanup})")
    
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
    except Exception as page_error:
        error_msg = f"Failed to create page: {str(page_error)}"
        print(f"❌ {error_msg}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        # Force cleanup on page creation failure
        await _force_cleanup_all_resources()
        raise Exception(f"{error_msg}. This may indicate Playwright browsers are not installed. Run: python -m playwright install chromium")
    
    try:
        # Build SimplyHired URL
        base_url = "https://www.simplyhired.com/search"
        params = f"?q={quote_plus(query)}"
        
        # Handle job_type filter - if remote, bypass location and search all over United States
        # Similar to how Indeed handles remote jobs
        if job_type and job_type.lower() in ['remote', 'work from home', 'wfh', 'telecommute', 'telework']:
            # For remote jobs, use location=remote to search all over United States
            # This bypasses any specific location filter
            job_type_filter = _get_simplyhired_job_type_filter(job_type)
            if job_type_filter:
                params += f"&{job_type_filter}"
                print(f"DEBUG - Job type '{job_type}' mapped to filter '{job_type_filter}' (bypassing location, searching all US)")
        elif location:
            # Only add location if job_type is not remote
            location_param = _format_location_for_simplyhired(location)
            params += f"&l={location_param}"
            print(f"DEBUG - Location '{location}' formatted as '{location_param}'")
        
        # Add job type filter for hybrid/onsite (if not already handled above)
        if job_type and job_type.lower() not in ['remote', 'work from home', 'wfh', 'telecommute', 'telework']:
            # For hybrid/onsite, SimplyHired doesn't have URL-level filters
            # We'll rely on post-scraping filtering
            print(f"DEBUG - Job type '{job_type}' will be filtered post-scraping (no URL parameter available)")
        
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
        
        url = base_url + params
        print(f"🌐 Navigating to: {url}")
        
        # Save the original search URL (without any job view parameters)
        original_search_url = url
        
        # Navigate with retry logic for Cloudflare
        max_retries = getattr(settings, "MAX_RETRIES", 3)
        cloudflare_retries = 0
        
        while True:
            try:
                # Optimized navigation strategy for Playwright
                navigation_timeout = 60000  # 60 seconds - give Cloudflare time to resolve
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
                        await page.goto(url, wait_until="commit", timeout=30000)
                        print("✓ Navigation started (commit)")
                        
                        # Wait for body element
                        await page.wait_for_selector('body', timeout=30000, state='attached')
                        print("✓ Body loaded")
                        
                        # Wait for job content
                        try:
                            await page.wait_for_selector(
                                'li h2 a[href*="/job/"], a[href*="/job/"], ul li h2',
                                timeout=20000,
                                state='attached'
                            )
                            print("✓ Job content detected")
                        except:
                            # Give page time to load
                            await page.wait_for_timeout(5000)
                            body_text = await page.evaluate("document.body ? document.body.innerText.length : 0")
                            if body_text > 100:
                                print(f"✓ Page has content ({body_text} chars)")
                        
                        navigation_success = True
                    except Exception as commit_error:
                        print(f"⚠️  All navigation strategies failed: {str(commit_error)[:100]}")
                        navigation_success = False
                
                # Wait for page content
                if navigation_success:
                    await page.wait_for_timeout(3000)  # Wait for Cloudflare to resolve
                else:
                    # Try scrolling to trigger lazy loading
                    await page.wait_for_timeout(5000)
                    try:
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                        await page.wait_for_timeout(2000)
                    except:
                        pass
                
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
                    if len(page_html) < 1000 or body_length < 100:
                        print(f"⚠️  Waiting for content...")
                        for attempt in range(5):
                            await page.wait_for_timeout(3000)
                            page_html = await page.content()
                            body_length = await page.evaluate("document.body ? document.body.innerText.length : 0")
                            if len(page_html) > 10000 and body_length > 500:
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
                    or len(page_html) < 1000  # Very short page = likely challenge
                )
                
                # Check for SimplyHired content (more comprehensive)
                has_simplyhired_content = (
                    '/job/' in page_html
                    or 'SimplyHired' in page_html
                    or 'job' in page_html.lower() and 'search' in page_html.lower()
                    or 'python job' in page_html.lower()
                    or len(page_html) > 10000  # Reasonable page size
                )
                
                # Also check page title
                try:
                    page_title = await page.title()
                    if "Just a moment" in page_title or "Checking" in page_title:
                        has_cloudflare_indicators = True
                    if "SimplyHired" in page_title or "job" in page_title.lower():
                        has_simplyhired_content = True
                except Exception:
                    pass
                
                is_actually_blocked = has_cloudflare_indicators and not has_simplyhired_content
                
                # If we have SimplyHired content, we're good (even if there are some Cloudflare indicators)
                if has_simplyhired_content:
                    is_actually_blocked = False
                    print("✓ SimplyHired content detected - proceeding with scraping")
                
                if not is_actually_blocked:
                    # Success - no Cloudflare block, mark proxy as successful
                    _mark_proxy_success()
                    break
                
                # Cloudflare detected - retry logic
                if cloudflare_retries >= max_retries:
                    raise CloudflareBlockedError(
                        f"SimplyHired blocked by Cloudflare. Tried {cloudflare_retries + 1} times. "
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
                        if '/job/' in page_html or 'SimplyHired' in page_html:
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
        job_cards = _find_job_cards_simplyhired(soup)
        
        print(f"📋 Found {len(job_cards)} job cards")
        
        # Extract job data from listing page
        jobs = []
        browser_alive = True  # Track if browser is still usable
        
        if not fetch_full_details:
            print("ℹ️  Fast mode: skipping job detail pages (using search results data only)")
        
        for card in job_cards[:max_results * 2]:  # Get more cards to account for filtering
            try:
                job = _extract_detailed_job_info_simplyhired(card)
                if job and job.title and job.url:
                    # Enhanced extraction: Visit individual job page for complete data
                    # Only attempt if browser is still alive AND fetch_full_details is True
                    if fetch_full_details and browser_alive:
                        print(f"  → Fetching complete data from job page: {job.title}")
                        try:
                            # Check if page is still connected before navigating
                            if page.is_closed():
                                print("  ⚠️  Page was closed - skipping job detail extraction for remaining jobs")
                                browser_alive = False
                            else:
                                enhanced_job = await _extract_complete_job_details_from_url_playwright(page, job, original_search_url)
                                if enhanced_job:
                                    job = enhanced_job
                                # Add delay to be respectful to SimplyHired's servers
                                await asyncio.sleep(random.uniform(1.5, 3.0))
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

