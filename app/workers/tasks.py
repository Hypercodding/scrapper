"""Celery scrape tasks."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse

from celery.exceptions import Reject

from app.core.circuit_breaker import check_or_raise, record_failure, record_success
from app.core.job_store import JobStatus, get_job_store
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

INDEED_HOST = "indeed.com"
# Backoff delays (seconds) per retry attempt for Cloudflare blocks
_INDEED_RETRY_DELAYS = [30, 120, 600]


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        pass
    return asyncio.run(coro)


@celery_app.task(
    bind=True,
    name="app.workers.tasks.scrape_career_page_task",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def scrape_career_page_task(
    self,
    job_id: str,
    url: str,
    max_results: Optional[int] = None,
    search_query: Optional[str] = None,
):
    store = get_job_store()
    host = urlparse(url).netloc

    try:
        check_or_raise(host)
        store.set_status(job_id, JobStatus.PROCESSING)
        store.set_progress(job_id, stage="starting")

        from app.services.generic_career_scraper import scrape_generic_career_page

        def progress_callback(**kwargs):
            store.set_progress(job_id, **kwargs)

        jobs = _run_async(
            scrape_generic_career_page(
                url,
                max_results=max_results,
                search_query=search_query,
                progress_callback=progress_callback,
            )
        )

        result = [j.model_dump() for j in jobs]
        store.set_result(job_id, result)
        record_success(host)
        logger.info("Job %s completed: %s jobs", job_id, len(result))
        return {"job_id": job_id, "jobs_found": len(result)}

    except Exception as exc:
        logger.exception("Job %s failed: %s", job_id, exc)
        record_failure(host)
        store.set_failed(job_id, str(exc))

        if self.request.retries >= self.max_retries:
            logger.error("Job %s moved to DLQ after max retries", job_id)
            raise Reject(str(exc), requeue=False)

        raise self.retry(exc=exc)


@celery_app.task(
    bind=True,
    name="app.workers.tasks.scrape_indeed_task",
    max_retries=3,
    acks_late=True,
    queue="scrape.indeed",
)
def scrape_indeed_task(
    self,
    job_id: str,
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
):
    """
    Scrape Indeed jobs via Playwright, enforcing a global Redis-backed concurrency cap
    across all worker replicas. Retries with backoff on Cloudflare blocks.
    """
    from app.core.host_throttle import host_throttle
    from app.core.settings_workers import get_worker_settings
    from app.services.indeed_playwright_service import (
        scrape_indeed_playwright,
        CloudflareBlockedError,
    )

    store = get_job_store()
    settings = get_worker_settings()
    retry_index = self.request.retries  # 0-based

    try:
        check_or_raise(INDEED_HOST)
        store.set_status(job_id, JobStatus.PROCESSING)
        store.set_progress(job_id, stage="queued", retry=retry_index)

        # Force proxy rotation on retries so each attempt uses a different IP.
        force_rotate = retry_index > 0

        with host_throttle(INDEED_HOST, max_concurrent=settings.MAX_CONCURRENT_SCRAPES_PER_HOST):
            store.set_progress(job_id, stage="navigating")
            jobs = _run_async(
                scrape_indeed_playwright(
                    query=query,
                    location=location,
                    max_results=max_results,
                    job_type=job_type,
                    salary_min=salary_min,
                    salary_max=salary_max,
                    experience_level=experience_level,
                    employment_type=employment_type,
                    days_old=days_old,
                    fetch_full_details=fetch_full_details,
                    force_rotate_proxy=force_rotate,
                )
            )

        result = [j.model_dump() for j in jobs]
        store.set_result(job_id, result)
        record_success(INDEED_HOST)
        logger.info("Indeed job %s completed: %d jobs found", job_id, len(result))
        return {"job_id": job_id, "jobs_found": len(result)}

    except CloudflareBlockedError as exc:
        record_failure(INDEED_HOST)
        logger.warning("Indeed job %s blocked by Cloudflare (attempt %d/3): %s", job_id, retry_index + 1, exc)

        if retry_index >= self.max_retries:
            store.set_failed(job_id, f"Blocked by Cloudflare after {retry_index + 1} attempts: {exc}")
            logger.error("Indeed job %s moved to DLQ — max retries exhausted", job_id)
            raise Reject(str(exc), requeue=False)

        backoff = _INDEED_RETRY_DELAYS[min(retry_index, len(_INDEED_RETRY_DELAYS) - 1)]
        store.set_progress(job_id, stage="retrying", retry=retry_index + 1, backoff_seconds=backoff)
        raise self.retry(exc=exc, countdown=backoff)

    except RuntimeError as exc:
        # host_throttle raises RuntimeError when slot cannot be acquired
        if "scrape slot" in str(exc):
            logger.warning("Indeed job %s could not acquire throttle slot — requeueing", job_id)
            backoff = _INDEED_RETRY_DELAYS[min(retry_index, len(_INDEED_RETRY_DELAYS) - 1)]
            raise self.retry(exc=exc, countdown=backoff)
        raise

    except Exception as exc:
        logger.exception("Indeed job %s failed: %s", job_id, exc)
        record_failure(INDEED_HOST)
        store.set_failed(job_id, str(exc))

        if retry_index >= self.max_retries:
            logger.error("Indeed job %s moved to DLQ after max retries", job_id)
            raise Reject(str(exc), requeue=False)

        backoff = _INDEED_RETRY_DELAYS[min(retry_index, len(_INDEED_RETRY_DELAYS) - 1)]
        raise self.retry(exc=exc, countdown=backoff)
