"""Worker and queue configuration."""
import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class WorkerSettings(BaseSettings):
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""
    WORKER_CONCURRENCY: int = 1
    MAX_CONCURRENT_SCRAPES_PER_HOST: int = 2
    JOB_RESULT_TTL_SECONDS: int = 86400
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    CIRCUIT_BREAKER_WINDOW_SECONDS: int = 600
    MAX_WORKER_MEMORY_MB: int = 3500
    SERVICE_ROLE: str = "api"  # api | worker

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def broker_url(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def result_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()
