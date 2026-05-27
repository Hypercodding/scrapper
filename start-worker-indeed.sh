#!/bin/sh
set -e
export SERVICE_ROLE=worker

# Headful mode via Xvfb. When HEADFUL=true on Railway, wrap celery in
# xvfb-run so the worker has a virtual display and Patchright + real Chrome
# can launch with headless=false (which Cloudflare's bot-management model
# treats far more favorably than headless=true). Default behavior unchanged.
RUN_CMD="celery -A app.workers.celery_app worker \
  --loglevel=info \
  --concurrency=1 \
  --max-tasks-per-child=${MAX_TASKS_PER_CHILD:-20} \
  --queues=scrape.indeed,scrape.indeed.retry,scrape.retry"

if [ "${HEADFUL:-false}" = "true" ]; then
  echo "🖥️  Launching worker under Xvfb (HEADFUL=true)"
  # -a = auto-select unused server number, avoids races with stray :99 leftovers.
  # 1920x1080x24 matches the most-common real-desktop resolution.
  exec xvfb-run -a --server-args="-screen 0 1920x1080x24 -ac +extension RANDR" \
    sh -c "$RUN_CMD"
else
  exec sh -c "$RUN_CMD"
fi
