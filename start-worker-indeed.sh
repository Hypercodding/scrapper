#!/bin/sh
set -e
export SERVICE_ROLE=worker
exec celery -A app.workers.celery_app worker \
  --loglevel=info \
  --concurrency=1 \
  --max-tasks-per-child="${MAX_TASKS_PER_CHILD:-20}" \
  --queues=scrape.indeed,scrape.retry
