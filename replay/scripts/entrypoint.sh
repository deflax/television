#!/bin/bash
set -e

# Create HLS output directory
mkdir -p /tmp/hls

# Execute the main command
exec "$@"
