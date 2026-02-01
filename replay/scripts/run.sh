#!/bin/bash
set -e

echo "Starting Replay Service..."
echo "Recordings directory: ${RECORDINGS_DIR:-/recordings}"

# Run with Hypercorn (ASGI server)
exec hypercorn --bind 0.0.0.0:${REPLAY_PORT:-8090} \
    --workers 1 \
    "main:create_app()"
