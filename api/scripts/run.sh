#!/bin/bash

set -e

# If there's a init.sh script in the /app directory, run it before starting
PRE_START_PATH=/app/init.sh
if [ -f $PRE_START_PATH ] ; then
    . "$PRE_START_PATH"
else
    echo "There is no prescript $PRE_START_PATH"
fi

# Start Uvicorn (ASGI server for Quart with SSE support)
echo "uvicorn $APP_MODULE"
pwd
exec uvicorn "${MODULE_NAME}:${VARIABLE_NAME}" \
    --factory \
    --host 0.0.0.0 \
    --port 8080 \
    --workers 1 \
    --timeout-keep-alive 30

