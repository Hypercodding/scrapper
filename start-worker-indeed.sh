#!/bin/sh
# Trace every command — if xvfb-run dies silently on a future image
# variant this shows exactly which line did it.
set -x

export SERVICE_ROLE=worker

# Headful mode via Xvfb. When HEADFUL=true on Railway, wrap celery in
# xvfb-run so the worker has a virtual display and Patchright + real Chrome
# can launch with headless=false (which Cloudflare's bot-management model
# treats far more favorably than headless=true).
RUN_CMD="celery -A app.workers.celery_app worker \
  --loglevel=info \
  --concurrency=1 \
  --max-tasks-per-child=${MAX_TASKS_PER_CHILD:-20} \
  --queues=scrape.indeed,scrape.indeed.retry,scrape.retry"

run_headless() {
  echo "▶️  Launching worker (headless)"
  export HEADFUL=false
  exec sh -c "$RUN_CMD"
}

run_under_xvfb() {
  echo "🖥️  Launching worker under Xvfb"
  # Print versions so we can correlate xvfb-run / Xvfb behavior with image
  # rebuilds. xvfb-run is a shell wrapper; print its sha to spot drift.
  xvfb-run --help 2>&1 | head -3 || true
  Xvfb -version 2>&1 | head -3 || true

  # Run xvfb-run WITHOUT exec so we can fall back if it exits unexpectedly.
  # `-e /dev/stderr` makes Xvfb's own errors visible (default is /dev/null
  # which is exactly the silent-failure mode we hit on Debian trixie).
  # `-l` enables verbose xvfb-run script tracing to stderr.
  xvfb-run \
    -a \
    -e /dev/stderr \
    --server-args="-screen 0 1920x1080x24 -ac +extension RANDR -nolisten tcp" \
    sh -c "$RUN_CMD"
  rc=$?
  echo "⚠️  xvfb-run exited with code $rc — falling back to headless so the worker survives"
  run_headless
}

if [ "${HEADFUL:-false}" = "true" ]; then
  if command -v xvfb-run >/dev/null 2>&1 && command -v xauth >/dev/null 2>&1; then
    run_under_xvfb
  else
    echo "⚠️  HEADFUL=true but xvfb-run or xauth is missing — falling back to headless."
    echo "    Install both in Dockerfile.railway (apt-get install xvfb xauth) and redeploy."
    run_headless
  fi
else
  run_headless
fi
