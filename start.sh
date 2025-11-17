#!/bin/bash
set -e

# Debug: Print environment variables
echo "PORT environment variable: ${PORT:-not set}"
echo "Defaulting to PORT: ${PORT:-8000}"

# Use PORT environment variable if set, otherwise default to 8000
PORT=${PORT:-8000}

# Ensure PORT is a number
if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
    echo "Error: PORT must be a number, got: $PORT"
    PORT=8000
fi

echo "Starting uvicorn on port: $PORT"

# Start uvicorn
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"

