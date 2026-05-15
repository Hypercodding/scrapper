#!/bin/sh
set -e
export SERVICE_ROLE=api
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
