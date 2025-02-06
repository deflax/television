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
# exec waitress-serve --listen=*:8080 --trusted-proxy='*' \
# --trusted-proxy-headers="x-forwarded-for","x-forwarded-host","x-forwarded-proto","x-forwarded-port" \
# --log-untrusted-proxy-headers --threads=16 --call app.api:create_app
pwd
waitress-serve --port=8080 --threads=16 --call $APP_MODULE