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

    def set_result(
        self,
        job_id: str,
        jobs: List[Dict[str, Any]],
        pending_retries: int = 0,
    ) -> bool:
        """Set the primary result list.

        If `pending_retries > 0`, status stays PROCESSING and the parent
        will only flip to COMPLETED when every retry resolves via
        `complete_retry`.
        """
        status = (
            JobStatus.PROCESSING.value
            if pending_retries > 0
            else JobStatus.COMPLETED.value
        )
        return self.update(
            job_id,
            status=status,
            result=jobs,
            pending_retries=pending_retries,
            progress={"jobs_found": len(jobs), "pending_retries": pending_retries},
        )

    # Lua script: atomically append a result and/or decrement pending_retries,
    # transition to COMPLETED when the counter hits zero. Necessary because
    # multiple per-jk retry tasks race on the same parent job key.
    #
    # KEYS[1] = scrape:job:<id>
    # ARGV[1] = job_dict JSON (empty string if this retry has no result to append)
    # ARGV[2] = ttl seconds
    # ARGV[3] = ISO timestamp
    _COMPLETE_RETRY_LUA = """
    local raw = redis.call('GET', KEYS[1])
    if not raw then return 0 end
    local data = cjson.decode(raw)
    if ARGV[1] ~= '' then
        if not data.result or type(data.result) ~= 'table' then
            data.result = {}
        end
        table.insert(data.result, cjson.decode(ARGV[1]))
    end
    local pending = data.pending_retries
    if type(pending) ~= 'number' then pending = 0 end
    pending = pending - 1
    if pending < 0 then pending = 0 end
    data.pending_retries = pending
    if pending == 0 then
        data.status = 'completed'
    end
    local found = 0
    if type(data.result) == 'table' then found = #data.result end
    if type(data.progress) ~= 'table' then data.progress = {} end
    data.progress.jobs_found = found
    data.progress.pending_retries = pending
    data.updated_at = ARGV[3]
    redis.call('SETEX', KEYS[1], tonumber(ARGV[2]), cjson.encode(data))
    return 1
    """

    def complete_retry(
        self,
        job_id: str,
        job_dict: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Atomically: append `job_dict` to result (if given), decrement
        pending_retries, flip status to COMPLETED when counter hits zero.

        Called by `scrape_indeed_single_jk_task` on every terminal outcome
        (success → pass job_dict; final failure → pass None or a stub).
        """
        ttl = get_worker_settings().JOB_RESULT_TTL_SECONDS
        payload = json.dumps(job_dict, default=str) if job_dict else ""
        result = self.client.eval(
            self._COMPLETE_RETRY_LUA,
            1,
            self._key(job_id),
            payload,
            ttl,
            datetime.utcnow().isoformat(),
        )
        return bool(result)

    def set_failed(self, job_id: str, error: str) -> bool:
        return self.update(job_id, status=JobStatus.FAILED.value, error=error)

    def delete(self, job_id: str) -> bool:
        return bool(self.client.delete(self._key(job_id)))


def get_job_store() -> JobStore:
    return JobStore()
