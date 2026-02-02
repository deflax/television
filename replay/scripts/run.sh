#!/bin/bash
set -e

echo "Starting Replay Service..."
echo "Recordings directory: ${RECORDINGS_DIR:-/recordings}"

exec uvicorn main:app \
    --host 0.0.0.0 \
    --port ${REPLAY_PORT:-8090} \
    --workers 1 \
    --timeout-keep-alive 30
