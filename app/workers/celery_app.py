"""Celery application configuration."""
from celery import Celery

from app.core.settings_workers import get_worker_settings

settings = get_worker_settings()

celery_app = Celery(
    "scrapper",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=2000,
    task_time_limit=3000,
    task_default_retry_delay=60,
    task_max_retries=3,
    task_routes={
        "app.workers.tasks.scrape_career_page_task": {"queue": "scrape.default"},
    },
    task_queues={
        "scrape.default": {"exchange": "scrape.default", "routing_key": "scrape.default"},
        "scrape.retry": {"exchange": "scrape.retry", "routing_key": "scrape.retry"},
        "scrape.dlq": {"exchange": "scrape.dlq", "routing_key": "scrape.dlq"},
    },
)
