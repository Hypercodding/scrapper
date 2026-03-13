"""
Request throttling to prevent too many parallel scraping operations.

This module limits concurrent scraping operations to prevent resource exhaustion
on Railway deployments. It automatically detects your Railway plan and sets
appropriate limits.

Browser semaphore (get_browser_semaphore) is separate from the scraping throttle:
- ScrapingThrottle: limits how many full pipeline requests run concurrently
- _browser_semaphore: guarantees only ONE browser process exists at any time,
  regardless of plan tier or request concurrency. Every scrape_with_selenium
  call must hold this lock for its entire duration.
"""
import asyncio
import os
from typing import Optional


class ScrapingThrottle:
    """
    Limit concurrent scraping operations to prevent resource exhaustion.
    
    Uses asyncio.Semaphore to control how many scraping operations can run
    simultaneously. When the limit is reached, additional requests wait until
    a slot becomes available.
    """
    
    def __init__(self, max_concurrent: int = 2):
        """
        Initialize throttle
        
        Args:
            max_concurrent: Maximum number of simultaneous scraping operations.
                          Recommended values:
                          - Free/Hobby Railway: 1 (sequential only)
                          - Pro Railway: 2-3
                          - Enterprise Railway: 5+
        """
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.max_concurrent = max_concurrent
    
    async def __aenter__(self):
        """Acquire semaphore when entering async context"""
        await self.semaphore.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Release semaphore when exiting async context"""
        self.semaphore.release()
    
    @property
    def available_slots(self) -> int:
        """Get number of available slots for scraping"""
        return self.semaphore._value  # pylint: disable=protected-access
    
    @property
    def active_scrapes(self) -> int:
        """Get number of currently active scraping operations"""
        return self.max_concurrent - self.available_slots


def detect_railway_plan() -> str:
    """
    Detect which Railway plan is being used based on environment variables.
    
    Returns:
        'free', 'hobby', 'pro', 'enterprise', or 'local'
    """
    # Not on Railway
    if not os.environ.get("RAILWAY_ENVIRONMENT"):
        return "local"
    
    # Check memory limit to determine plan
    memory_limit = os.environ.get("RAILWAY_SERVICE_MEMORY_LIMIT_MB")
    if memory_limit:
        memory_mb = int(memory_limit)
        if memory_mb <= 512:
            return "free"
        elif memory_mb <= 1024:
            return "hobby"
        elif memory_mb <= 8192:
            return "pro"
        else:
            return "enterprise"
    
    # Default to free if we can't determine
    return "free"


def get_max_concurrent_from_env() -> int:
    """
    Automatically determine safe concurrent limit based on Railway plan.
    
    Returns:
        Recommended max_concurrent value for detected environment
    """
    plan = detect_railway_plan()
    
    plan_limits = {
        "free": 1,        # 512 MB - sequential only
        "hobby": 1,       # 1 GB - sequential recommended
        "pro": 2,         # 8 GB - can handle 2-3 parallel
        "enterprise": 5,  # 32+ GB - can handle many parallel
        "local": 2        # Local development - moderate
    }
    
    limit = plan_limits.get(plan, 1)
    
    # Allow override via environment variable
    override = os.environ.get("MAX_CONCURRENT_SCRAPES")
    if override:
        try:
            limit = int(override)
            print(f"ℹ️  Using custom max_concurrent: {limit} (from MAX_CONCURRENT_SCRAPES env var)")
        except ValueError:
            print(f"⚠️  Invalid MAX_CONCURRENT_SCRAPES value: {override}, using detected: {limit}")
    else:
        print(f"ℹ️  Detected Railway plan: {plan}, setting max_concurrent={limit}")
    
    return limit


# Global throttle instance
_scraping_throttle: Optional[ScrapingThrottle] = None

# ONE browser at a time — hard limit regardless of plan tier or request concurrency.
# Acquiring this lock before launching Chrome and releasing it only after full cleanup
# prevents two simultaneous Chrome processes from competing for Railway's limited PIDs
# and memory. asyncio.Semaphore(1) is effectively a mutex in an async context.
_browser_semaphore: Optional[asyncio.Semaphore] = None


def get_browser_semaphore() -> asyncio.Semaphore:
    """
    Returns the global browser mutex (Semaphore(1)).

    Every call to scrape_with_selenium must hold this for its full duration:

        async with get_browser_semaphore():
            driver = webdriver.Chrome(...)
            ...  # full scrape
            driver.quit()
    """
    global _browser_semaphore
    if _browser_semaphore is None:
        _browser_semaphore = asyncio.Semaphore(1)
    return _browser_semaphore


def get_scraping_throttle() -> ScrapingThrottle:
    """
    Get or create global scraping throttle.
    
    The throttle is created once and reused for all scraping operations.
    It automatically detects the appropriate limit based on Railway plan.
    
    Returns:
        Global ScrapingThrottle instance
    """
    global _scraping_throttle
    if _scraping_throttle is None:
        max_concurrent = get_max_concurrent_from_env()
        _scraping_throttle = ScrapingThrottle(max_concurrent)
    return _scraping_throttle


def reset_scraping_throttle(max_concurrent: Optional[int] = None):
    """
    Reset throttle with new settings.
    
    Useful for testing or runtime configuration changes.
    
    Args:
        max_concurrent: New max concurrent value. If None, auto-detects from environment.
    """
    global _scraping_throttle
    if max_concurrent is None:
        max_concurrent = get_max_concurrent_from_env()
    _scraping_throttle = ScrapingThrottle(max_concurrent)
    print(f"✓ Scraping throttle reset: max_concurrent={max_concurrent}")


def get_throttle_status() -> dict:
    """
    Get current throttle status information.
    
    Returns:
        Dict with throttle status including:
        - max_concurrent: Maximum allowed parallel scrapes
        - available_slots: How many more scrapes can start
        - active_scrapes: How many scrapes are currently running
        - railway_plan: Detected Railway plan
    """
    throttle = get_scraping_throttle()
    return {
        "max_concurrent": throttle.max_concurrent,
        "available_slots": throttle.available_slots,
        "active_scrapes": throttle.active_scrapes,
        "railway_plan": detect_railway_plan(),
        "message": f"Can run {throttle.available_slots} more concurrent scrape(s)"
    }


# Example usage in scraping functions:
"""
from app.core.throttle import get_scraping_throttle

async def scrape_indeed_selenium(...):
    # Acquire throttle before scraping
    async with get_scraping_throttle():
        # Your scraping code here
        jobs = await _do_scraping(...)
    return jobs
"""

