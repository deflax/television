#!/bin/bash
set -e

echo "Starting Replay Service..."
echo "Recordings directory: ${RECORDINGS_DIR:-/recordings}"

# Run the Python application
exec python3 /app/main.py
