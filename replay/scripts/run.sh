#!/bin/bash
set -e

echo "Starting Replay Service..."
echo "Recordings directory: ${RECORDINGS_DIR:-/recordings}"

# Run with Hypercorn (ASGI server)
# Config disables HTTP/2 to fix VLC stream cancellation errors
exec hypercorn --config /app/hypercorn.toml \
    --bind 0.0.0.0:${REPLAY_PORT:-8090} \
    --workers 1 \
    "main:create_app()"
