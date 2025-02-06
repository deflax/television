#!/bin/sh

waitress-serve --port=8080 --trusted-proxy='*' \
--trusted-proxy-headers="x-forwarded-for","x-forwarded-host","x-forwarded-proto","x-forwarded-port" \
--log-untrusted-proxy-headers --threads=16 --call api:create_app