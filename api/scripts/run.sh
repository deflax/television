#!/bin/bash

set -e

# If there's an init.sh script in the /api directory, run it before starting
PRE_START_PATH=/api/init.sh
if [ -f $PRE_START_PATH ] ; then
    . "$PRE_START_PATH"
else
    echo "There is no prescript $PRE_START_PATH"
fi

# Start Uvicorn (ASGI server for Quart with SSE support)
echo "uvicorn $APP_MODULE"
pwd
exec uvicorn "$APP_MODULE" \
    --factory \
    --host 0.0.0.0 \
    --port 8080 \
    --workers 1 \
    --timeout-keep-alive 30
