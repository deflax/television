#!/bin/bash

parent_path=$( cd "$(dirname "${BASH_SOURCE[0]}")" ; pwd -P )
cd $parent_path

source variables.env

CB=`docker ps | grep certbot | cut -d ' ' -f 1`

#echo $BASE_URL
#echo $EMAIL

docker exec $CB certbot certonly --non-interactive --standalone --http-01-address 0.0.0.0 --email $EMAIL --agree-tos --keep --preferred-challenges http --cert-name $BASE_URL \
	-d $BASE_URL -d api.$BASE_URL -d stream.$BASE_URL

cat "./data/certbot/etc/live/$BASE_URL/privkey.pem" "./data/certbot/etc/live/$BASE_URL/fullchain.pem" > "./data/certificates/$BASE_URL.pem"
docker kill -s USR2 television_haproxy_1
