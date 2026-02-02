#!/bin/bash
set -e

# Start the multiplexer in the background
python main.py &
MUX_PID=$!

# Start the web server
uvicorn server:app --host 0.0.0.0 --port 8091 &
SERVER_PID=$!

# Wait for either process to exit
wait -n

# Kill both processes on exit
kill $MUX_PID $SERVER_PID 2>/dev/null || true
