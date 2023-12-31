#!/bin/bash

mkdir -p data/certificates
cd data/certificates
openssl genrsa -out default.key 2048
openssl req -new -key default.key -out default.csr
openssl x509 -req -days 3650 -in default.csr -signkey default.key -out default.crt
cat default.key default.crt >> default.pem
rm default.key
rm default.csr
rm default.crt
