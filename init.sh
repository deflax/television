#!/bin/bash

echo "creating data dir structure"

# acme.sh
mkdir -v -p data/acme
mkdir -v -p data/certificates

# restreamer
mkdir -v -p data/restreamer/config
mkdir -v -p data/restreamer/data

# scheduler
mkdir -v -p data/recorder/vod
mkdir -v -p data/recorder/live
mkdir -v -p data/recorder/thumb

# icecast
mkdir -v -p logs/icecast
touch logs/icecast/access.log
touch logs/icecast/error.log
chown 1000:1000 logs/icecast/access.log
chown 1000:1000 logs/icecast/error.log
