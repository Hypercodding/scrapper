"""
Scraping Execution Controller

This module enforces strict single-concurrency execution for scraping operations.
Only one scraping request may run at a time, ensuring only one browser instance
exists at any moment.

Key Features:
- Global execution lock (one request at a time)
- Automatic timeout handling (Railway-appropriate timeouts)
- Guaranteed cleanup in all cases
- Resource usage returns to baseline after every request
"""

import asyncio
import time
import logging
from typing import Callable, TypeVar, Awaitable, Any, Optional
from functools import wraps
from contextlib import asynccontextmanager

from app.core.browser_executor import hard_kill_all_browsers, verify_cleanup

logger = logging.getLogger(__name__)

# Railway-appropriate timeouts (in seconds)
SCRAPE_TIMEOUT = 600  # 10 minutes - reasonable for complex scraping
CLEANUP_TIMEOUT = 30  # 30 seconds for cleanup operations
# No timeout for queue wait - requests wait until their turn (no queue timeout)
LOCK_ACQUIRE_TIMEOUT = None  # None = wait indefinitely for lock (no queue timeout)

# Global execution lock - ensures only one scrape runs at a time
_execution_lock = asyncio.Lock()
_current_task: Optional[asyncio.Task] = None
_task_start_time: Optional[float] = None
_queue_count: int = 0  # Track how many requests are waiting
_completed_count: int = 0  # Track completed requests for stats

T = TypeVar('T')


class ScrapeTimeoutError(Exception):
    """Raised when a scraping operation exceeds the timeout"""
    pass


class ScrapeInProgressError(Exception):
    """Raised when attempting to start a scrape while another is in progress"""
    pass


@asynccontextmanager
async def scrape_execution_context():
    """
    Async context manager for scraping execution.
    
    Enforces single-concurrency: only one scrape can run at a time.
    Automatically handles cleanup. Requests queue up and wait for their turn
    (no queue timeout - they wait until the lock is available).
    
    Usage:
        async with scrape_execution_context():
            # Perform scraping here
            result = await scrape_function()
    """
    global _current_task, _task_start_time, _queue_count, _completed_count
    
    # Track queue position
    _queue_count += 1
    queue_position = _queue_count
    wait_start = time.time()
    
    if _execution_lock.locked():
        logger.info(f"📋 Request queued (position: {queue_position}, waiting for lock...)")
    
    # Acquire lock - no timeout: queued requests wait until their turn (no queue timeout)
    await _execution_lock.acquire()
    
    wait_time = time.time() - wait_start
    _task_start_time = time.time()
    
    if wait_time > 1:
        logger.info(f"🔒 Scrape execution lock acquired after {wait_time:.1f}s wait - starting scrape")
    else:
        logger.info("🔒 Scrape execution lock acquired - starting scrape")
    
    try:
        # Verify no browser processes before starting (safety check)
        if not verify_cleanup():
            logger.warning("Browser processes detected before scrape start - cleaning up")
            hard_kill_all_browsers()
            await asyncio.sleep(2.0)  # Wait for cleanup
        
        yield
        
    finally:
        # Always cleanup, regardless of success or failure
        logger.info("🧹 Executing mandatory cleanup after scrape")
        
        try:
            # Hard-kill all browser processes
            killed = hard_kill_all_browsers()
            if killed > 0:
                logger.info(f"Killed {killed} browser process(es) during cleanup")
            
            # Wait for resources to be released
            await asyncio.sleep(2.0)
            
            # Verify cleanup
            if verify_cleanup():
                logger.info("✓ Cleanup verified - no browser processes remaining")
            else:
                logger.warning("⚠️  Warning: Some browser processes may still exist after cleanup")
                
        except Exception as cleanup_error:
            logger.error(f"Error during cleanup: {cleanup_error}")
            # Try one more aggressive cleanup
            try:
                hard_kill_all_browsers()
            except Exception:
                pass
        
        # Release lock and update counters
        _execution_lock.release()
        _queue_count -= 1
        _completed_count += 1
        elapsed = time.time() - (_task_start_time or time.time())
        _task_start_time = None
        
        remaining = _queue_count
        if remaining > 0:
            logger.info(f"🔓 Scrape completed (elapsed: {elapsed:.1f}s) - {remaining} request(s) still waiting")
        else:
            logger.info(f"🔓 Scrape completed (elapsed: {elapsed:.1f}s) - queue empty")
        
        # Small delay to ensure system resources are fully released
        await asyncio.sleep(1.0)


def enforce_single_concurrency(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """
    Decorator to enforce single-concurrency execution for async scraping functions.
    
    This decorator:
    1. Ensures only one scrape runs at a time (global lock)
    2. Enforces timeout (Railway-appropriate)
    3. Guarantees cleanup in all cases
    4. Prevents parallel execution
    
    Usage:
        @enforce_single_concurrency
        async def my_scrape_function(...):
            # Scraping code here
            return results
    """
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        async with scrape_execution_context():
            try:
                # Execute with timeout
                result = await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=SCRAPE_TIMEOUT
                )
                return result
            except asyncio.TimeoutError:
                logger.error(f"Scrape operation timed out after {SCRAPE_TIMEOUT}s")
                raise ScrapeTimeoutError(
                    f"Scraping operation exceeded timeout of {SCRAPE_TIMEOUT}s. "
                    "This may indicate the target site is slow or unresponsive."
                )
            except Exception as e:
                logger.error(f"Scrape operation failed: {e}")
                raise
            # Cleanup is handled by the context manager
    return wrapper


async def execute_scrape_with_cleanup(
    scrape_func: Callable[..., Awaitable[T]],
    *args: Any,
    **kwargs: Any
) -> T:
    """
    Execute a scraping function with guaranteed cleanup.
    
    This is a lower-level function for cases where you need more control.
    For most cases, use the @enforce_single_concurrency decorator.
    
    Args:
        scrape_func: The async scraping function to execute
        *args, **kwargs: Arguments to pass to scrape_func
    
    Returns:
        Result from scrape_func
    
    Raises:
        ScrapeInProgressError: If another scrape is already running
        ScrapeTimeoutError: If the scrape exceeds the timeout
    """
    async with scrape_execution_context():
        try:
            result = await asyncio.wait_for(
                scrape_func(*args, **kwargs),
                timeout=SCRAPE_TIMEOUT
            )
            return result
        except asyncio.TimeoutError:
            raise ScrapeTimeoutError(f"Scrape timed out after {SCRAPE_TIMEOUT}s")
        except Exception as e:
            logger.error(f"Scrape failed: {e}")
            raise


def get_execution_status() -> dict:
    """
    Get the current execution status.
    
    Returns:
        Dictionary with status information:
        - is_locked: Whether a scrape is currently running
        - elapsed_time: How long the current scrape has been running (if any)
        - queue_count: Number of requests waiting in queue
        - completed_count: Total completed requests
    """
    global _task_start_time, _queue_count, _completed_count
    
    is_locked = _execution_lock.locked()
    elapsed = None
    
    if is_locked and _task_start_time:
        elapsed = time.time() - _task_start_time
    
    return {
        "is_locked": is_locked,
        "scrape_in_progress": is_locked,
        "elapsed_seconds": elapsed,
        "timeout_seconds": SCRAPE_TIMEOUT,
        "queue_count": _queue_count,
        "waiting_requests": max(0, _queue_count - (1 if is_locked else 0)),
        "completed_total": _completed_count,
        "lock_acquire_timeout": LOCK_ACQUIRE_TIMEOUT
    }


async def wait_for_scrape_completion(timeout: float = 300) -> bool:
    """
    Wait for any in-progress scrape to complete.
    
    Args:
        timeout: Maximum time to wait (seconds)
    
    Returns:
        True if scrape completed, False if timeout
    """
    try:
        # Try to acquire the lock (will wait until released)
        acquired = await asyncio.wait_for(
            _execution_lock.acquire(),
            timeout=timeout
        )
        if acquired:
            # Immediately release since we were just checking
            _execution_lock.release()
            return True
        return False
    except asyncio.TimeoutError:
        return False

