#!/bin/bash

source certbot.env

CB=`docker ps | grep certbot | cut -d ' ' -f 1`

echo $BASE_URL
echo $EMAIL

docker exec $CB certbot certonly --non-interactive --standalone --http-01-address 0.0.0.0 --email $EMAIL --agree-tos --keep --preferred-challenges http --cert-name stream.$BASE_URL \
	-d tv.$BASE_URL -d stream.$BASE_URL -d vod.$BASE_URL

cat "./data/certbot/etc/live/stream.$BASE_URL/privkey.pem" "./data/certbot/etc/live/stream.$BASE_URL/fullchain.pem" > "./data/certificates/stream.$BASE_URL.pem"
docker kill -s USR2 television_haproxy_1
