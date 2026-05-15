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
