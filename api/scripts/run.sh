#!/bin/bash

set -e

# If there's a init.sh script in the /app directory, run it before starting
PRE_START_PATH=/app/init.sh
if [ -f $PRE_START_PATH ] ; then
    . "$PRE_START_PATH"
else
    echo "There is no prescript $PRE_START_PATH"
fi

# Start Hypercorn (ASGI server for Quart with SSE support)
echo "hypercorn $APP_MODULE"
pwd
hypercorn --bind 0.0.0.0:8080 \
--workers 1 \
--access-log - \
"$APP_MODULE()"

