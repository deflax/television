#!/bin/bash

echo "creating data dir structure"

# acme.sh
mkdir -v -p data/acme
mkdir -v -p data/certificates

# restreamer
mkdir -v -p data/restreamer/config
mkdir -v -p data/restreamer/data

# scheduler
mkdir -v -p data/recorder/live
mkdir -v -p data/recorder/vod
mkdir -v -p data/recorder/thumb

# replay library
mkdir -v -p data/library

