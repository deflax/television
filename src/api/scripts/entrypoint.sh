#!/bin/bash

set -e

if [ -f /app/app.py ]; then
    DEFAULT_MODULE_NAME=api
fi
MODULE_NAME=${MODULE_NAME:-$DEFAULT_MODULE_NAME}
VARIABLE_NAME=${VARIABLE_NAME:-api}
export APP_MODULE=${APP_MODULE:-"$MODULE_NAME:$VARIABLE_NAME"}

exec "$@"