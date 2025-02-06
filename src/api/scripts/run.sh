#!/bin/bash

set -e

# If there's a prestart.sh script in the /app directory, run it before starting
PRE_START_PATH=/app/prestart.sh
if [ -f $PRE_START_PATH ] ; then
    . "$PRE_START_PATH"
else
    echo "There is no prescript $PRE_START_PATH"
fi

# Start Waitress
echo "waitress-serve $APP_MODULE"
pwd
waitress-serve --port=8080 --threads=16 \
--trusted-proxy='*' --log-untrusted-proxy-headers \
--trusted-proxy-headers='x-forwarded-for'
--call $APP_MODULE