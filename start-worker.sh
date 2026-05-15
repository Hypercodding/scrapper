#!/bin/sh
set -e
export SERVICE_ROLE=worker
exec celery -A app.workers.celery_app worker \
  --loglevel=info \
  --concurrency="${WORKER_CONCURRENCY:-1}" \
  --queues=scrape.default,scrape.retry
