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
  # Guard against missing image dependencies. `xvfb-run` is a shell wrapper
  # that requires `xauth` to set up its MIT-MAGIC-COOKIE-1 authority file;
  # if either is missing the previous build of this image crash-looped
  # every replica with "xvfb-run: error: xauth command not found" instead
  # of falling back to headless. Fail open: log loudly, run headless.
  if command -v xvfb-run >/dev/null 2>&1 && command -v xauth >/dev/null 2>&1; then
    echo "🖥️  Launching worker under Xvfb (HEADFUL=true)"
    # -a = auto-select unused server number, avoids races with stray :99 leftovers.
    # 1920x1080x24 matches the most-common real-desktop resolution.
    exec xvfb-run -a --server-args="-screen 0 1920x1080x24 -ac +extension RANDR" \
      sh -c "$RUN_CMD"
  else
    echo "⚠️  HEADFUL=true but xvfb-run or xauth is missing — falling back to headless."
    echo "    Install both in Dockerfile.railway (apt-get install xvfb xauth) and redeploy."
    # Override HEADFUL so the Python layer also picks headless mode.
    export HEADFUL=false
    exec sh -c "$RUN_CMD"
  fi
else
  exec sh -c "$RUN_CMD"
fi
