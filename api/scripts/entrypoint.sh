#!/bin/bash

set -e

MODULE_NAME=${MODULE_NAME:-flask_api}
VARIABLE_NAME=${VARIABLE_NAME:-create_app}
export APP_MODULE=${APP_MODULE:-"$MODULE_NAME:$VARIABLE_NAME"}

exec "$@"

