#!/bin/bash
set -e

# Create HLS output directory
mkdir -p /tmp/hls

# Create mount point directories (needed whether S3 or local)
mkdir -p "${RECORDINGS_DIR:-/recordings}"
mkdir -p "${LIBRARY_DIR:-/library}"

# Execute the main command
exec "$@"
