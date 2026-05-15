"""Redis-backed scrape job state."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.settings_workers import get_worker_settings

logger = logging.getLogger(__name__)

JOB_KEY_PREFIX = "scrape:job:"


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _redis_client():
    import redis
    settings = get_worker_settings()
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


class JobStore:
    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = _redis_client()
        return self._client

    def _key(self, job_id: str) -> str:
        return f"{JOB_KEY_PREFIX}{job_id}"

    def create(
        self,
        url: str,
        max_results: Optional[int] = None,
        search_query: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> str:
        job_id = job_id or str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        data = {
            "job_id": job_id,
            "status": JobStatus.PENDING.value,
            "created_at": now,
            "updated_at": now,
            "url": url,
            "max_results": max_results,
            "search_query": search_query,
            "result": None,
            "error": None,
            "progress": None,
            "celery_task_id": None,
        }
        ttl = get_worker_settings().JOB_RESULT_TTL_SECONDS
        self.client.setex(self._key(job_id), ttl, json.dumps(data))
        return job_id

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        raw = self.client.get(self._key(job_id))
        if not raw:
            return None
        return json.loads(raw)

    def update(self, job_id: str, **fields: Any) -> bool:
        data = self.get(job_id)
        if not data:
            return False
        data.update(fields)
        data["updated_at"] = datetime.utcnow().isoformat()
        ttl = get_worker_settings().JOB_RESULT_TTL_SECONDS
        self.client.setex(self._key(job_id), ttl, json.dumps(data, default=str))
        return True

    def set_status(self, job_id: str, status: JobStatus, **extra: Any) -> bool:
        return self.update(job_id, status=status.value, **extra)

    def set_progress(self, job_id: str, **progress: Any) -> bool:
        data = self.get(job_id) or {}
        current = data.get("progress") or {}
        current.update(progress)
        return self.update(job_id, progress=current)

    def set_result(self, job_id: str, jobs: List[Dict[str, Any]]) -> bool:
        return self.update(
            job_id,
            status=JobStatus.COMPLETED.value,
            result=jobs,
            progress={"jobs_found": len(jobs)},
        )

    def set_failed(self, job_id: str, error: str) -> bool:
        return self.update(job_id, status=JobStatus.FAILED.value, error=error)

    def delete(self, job_id: str) -> bool:
        return bool(self.client.delete(self._key(job_id)))


def get_job_store() -> JobStore:
    return JobStore()
