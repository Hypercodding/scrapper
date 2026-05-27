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
    sort: Optional[str] = None,
    radius: Optional[int] = None,
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

        # Step 12 — Resumability: on a Celery retry, if the previous attempt
        # finished the SERP scan before failing during detail fetches, skip
        # the SERP entirely and re-run only the detail phase via the per-jk
        # retry queue (same machinery as Step 2). This protects long batches
        # from losing the entire SERP cost on a single mid-run failure.
        existing = store.get(job_id) or {}
        prior_progress = existing.get("progress") or {}
        prior_result = existing.get("result") or []
        already_fetched_jks = {
            r.get("job_key") for r in prior_result if r.get("job_key")
        }
        discovered_jks = prior_progress.get("discovered_jks") or []

        if (
            retry_index > 0
            and prior_progress.get("serp_done")
            and discovered_jks
            and fetch_full_details
        ):
            # Resume path: enqueue per-jk retries for everything the previous
            # attempt knew about but didn't deliver. Bounded by the same per-
            # batch cap that the normal Step 2 path uses.
            from app.core.config import settings as scrape_settings
            from app.core import metrics

            remaining = [
                jk for jk in discovered_jks if jk not in already_fetched_jks
            ]
            cap = getattr(scrape_settings, "MAX_PER_JK_RETRIES_PER_BATCH", 25)
            remaining = remaining[:cap]
            logger.info(
                "Indeed job %s resuming from SERP checkpoint: %d/%d jks remain to fetch",
                job_id, len(remaining), len(discovered_jks),
            )
            store.set_result(
                job_id,
                prior_result,
                pending_retries=len(remaining),
            )
            for failed_jk in remaining:
                scrape_indeed_single_jk_task.apply_async(
                    args=(job_id, failed_jk, query, location),
                    queue="scrape.indeed.retry",
                )
                metrics.incr("per_jk_retry_enqueued")
            record_success(INDEED_HOST)
            return {
                "job_id": job_id,
                "resumed": True,
                "jobs_already_delivered": len(prior_result),
                "pending_retries": len(remaining),
            }

        # Fresh-or-non-resumable path: full primary scrape.
        def _progress_cb(**kwargs):
            try:
                store.set_progress(job_id, **kwargs)
            except Exception:
                pass

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
                    sort=sort,
                    radius=radius,
                    progress_callback=_progress_cb,
                )
            )

        result = [j.model_dump() for j in jobs]

        # Drain rows the strict-mode gate dropped during the primary scrape
        # and enqueue per-jk retries with a forced fresh proxy / fingerprint.
        # The parent stays in PROCESSING until every retry resolves via
        # JobStore.complete_retry — this keeps the API client polling instead
        # of seeing a premature COMPLETED with an incomplete result list.
        from app.core.config import settings as scrape_settings
        from app.core import metrics
        from app.services.indeed_playwright_service import get_last_failed_jks

        failed_jks = get_last_failed_jks() if fetch_full_details else []
        max_retries_per_batch = getattr(
            scrape_settings, "MAX_PER_JK_RETRIES_PER_BATCH", 25
        )
        # Dedup + cap so a pathological run can't flood scrape.indeed.retry.
        failed_jks = list(dict.fromkeys(failed_jks))[:max_retries_per_batch]

        store.set_result(job_id, result, pending_retries=len(failed_jks))
        record_success(INDEED_HOST)
        logger.info(
            "Indeed job %s primary done: %d jobs, %d per-jk retries queued",
            job_id, len(result), len(failed_jks),
        )

        if failed_jks:
            for failed_jk in failed_jks:
                scrape_indeed_single_jk_task.apply_async(
                    args=(job_id, failed_jk, query, location),
                    queue="scrape.indeed.retry",
                )
                metrics.incr("per_jk_retry_enqueued")

        return {
            "job_id": job_id,
            "jobs_found": len(result),
            "pending_retries": len(failed_jks),
        }

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


@celery_app.task(
    bind=True,
    name="app.workers.tasks.scrape_indeed_single_jk_task",
    max_retries=3,
    acks_late=True,
    queue="scrape.indeed.retry",
)
def scrape_indeed_single_jk_task(
    self,
    parent_job_id: str,
    jk: str,
    query: str = "",
    location: Optional[str] = None,
):
    """Fetch one Indeed /viewjob page that the primary scrape's strict gate
    dropped. Runs in an isolated browser with a forced fresh proxy.

    On terminal outcome (success or final failure), `JobStore.complete_retry`
    atomically appends the result (if any), decrements `pending_retries`,
    and flips the parent's status to COMPLETED when the counter hits zero.
    """
    from app.core.host_throttle import host_throttle
    from app.core.settings_workers import get_worker_settings
    from app.services.indeed_playwright_service import (
        scrape_single_jk_with_fresh_session,
        CloudflareBlockedError,
    )
    from app.core import metrics

    store = get_job_store()
    settings = get_worker_settings()
    retry_index = self.request.retries

    try:
        with host_throttle(INDEED_HOST, max_concurrent=settings.MAX_CONCURRENT_SCRAPES_PER_HOST):
            job = _run_async(
                scrape_single_jk_with_fresh_session(jk=jk, query=query, location=location)
            )

        if job is not None and job.detail_fetch_status == "ok":
            store.complete_retry(parent_job_id, job.model_dump())
            logger.info("Per-jk retry %s succeeded for parent %s", jk, parent_job_id)
            return {"jk": jk, "status": "ok"}

        # Sub-task technically returned but the result is degraded — retry if budget allows.
        raise CloudflareBlockedError(
            f"per-jk fetch returned degraded result for {jk} "
            f"(status={getattr(job, 'detail_fetch_status', 'no-job')})"
        )

    except (CloudflareBlockedError, Exception) as exc:
        if retry_index >= self.max_retries:
            # Final failure: record a blocked stub so the parent's
            # pending_retries counter still ticks down, otherwise the
            # parent job would hang in PROCESSING forever.
            stub = {
                "job_key": jk,
                "url": f"https://www.indeed.com/viewjob?jk={jk}",
                "title": "(retry exhausted)",
                "detail_fetch_status": "blocked",
            }
            store.complete_retry(parent_job_id, stub)
            logger.warning(
                "Per-jk retry %s exhausted for parent %s: %s",
                jk, parent_job_id, exc,
            )
            return {"jk": jk, "status": "blocked"}

        backoff = _INDEED_RETRY_DELAYS[min(retry_index, len(_INDEED_RETRY_DELAYS) - 1)]
        raise self.retry(exc=exc, countdown=backoff)
