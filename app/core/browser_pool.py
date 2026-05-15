"""Per-worker browser lifecycle management for Celery tasks."""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Generator, Optional

from app.core.browser_executor import hard_kill_all_browsers, verify_cleanup

logger = logging.getLogger(__name__)


def _check_memory() -> None:
    try:
        import psutil
        settings_mb = int(os.environ.get("MAX_WORKER_MEMORY_MB", "3500"))
        proc = psutil.Process()
        rss_mb = proc.memory_info().rss / (1024 * 1024)
        if rss_mb > settings_mb:
            raise MemoryError(
                f"Worker memory {rss_mb:.0f}MB exceeds limit {settings_mb}MB"
            )
    except ImportError:
        pass


@contextmanager
def browser_scrape_context(
    create_driver: callable,
) -> Generator[Any, None, None]:
    """
    Acquire an isolated browser for one scrape task.
    create_driver: callable that returns a new WebDriver instance.
    """
    _check_memory()
    if not verify_cleanup():
        hard_kill_all_browsers()

    driver = None
    try:
        driver = create_driver()
        yield driver
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception as e:
                logger.warning("driver.quit() failed: %s", e)
        killed = hard_kill_all_browsers()
        if killed:
            logger.info("Cleaned up %s browser process(es)", killed)
