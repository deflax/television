#!/bin/bash

echo "creating data dir structure"
mkdir -v -p data/restreamer/config
mkdir -v -p data/restreamer/data

mkdir -v -p data/recorder/vod
mkdir -v -p data/recorder/live
mkdir -v -p data/recorder/thumb

mkdir -v -p data/archive

mkdir -v -p data/certbot/etc
mkdir -v -p data/certbot/var
mkdir -v -p logs/certbot

mkdir -v -p data/certificates

echo "generating self signed certificates for haproxy bootstrap"
cd data/certificates
openssl genrsa -out default.key 2048
openssl req -new -key default.key -out default.csr
openssl x509 -req -days 3650 -in default.csr -signkey default.key -out default.crt
cat default.key default.crt >> default.pem
rm default.key
rm default.csr
rm default.crt
