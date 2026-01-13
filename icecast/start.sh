#!/bin/sh

env

set -x

set_val() {
    if [ -n "$2" ]; then
        sed -i "s/<$2>[^<]*<\/$2>/<$2>$1<\/$2>/g" /etc/icecast2/icecast.xml
    else
        echo "Setting for '$1' is missing, skipping." >&2
    fi
}

cp -v /etc/icecast2/icecast.xml.template /etc/icecast2/icecast.xml

set_val $ICECAST_SOURCE_PASSWORD source-password
set_val $ICECAST_RELAY_PASSWORD  relay-password
set_val $ICECAST_ADMIN_PASSWORD  admin-password
set_val $CORE_API_HOSTNAME       hostname
set_val $CORE_API_HOSTNAME       stream-url

set -e

icecast2 -n -c /etc/icecast2/icecast.xml
