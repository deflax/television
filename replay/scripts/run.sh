#!/bin/bash
set -e

echo "Starting Replay Service..."
echo "Library directory: ${LIBRARY_DIR:-/library}"

# Create directories
mkdir -p /tmp/hls
mkdir -p "${LIBRARY_DIR:-/library}"

exec uvicorn main:app \
    --host 0.0.0.0 \
    --port ${REPLAY_PORT:-8090} \
    --workers 1 \
    --timeout-keep-alive 30
