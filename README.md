# Television
Multi channel stream setup with Flask REST API for scheduling channels.

### Install
1. `cp variables.env.dist variables.env` and set the required variables

2. Start the acme-sh service:
`docker-compose up -d acme-sh`

3. Register acme account:
`source variables.env; docker exec acme.sh --register-account -m $EMAIL`

4. Set the `ACCOUNT_THUMBPRINT` variable

5. Run the stack using `docker-compose up -d --build --remove-orphans`

6. Issue a certificate:
`source variables.env; docker exec acme.sh --issue -d $BASE_URL -d $CORE_API_HOSTNAME --stateless`

7. Install the certificate:
`source variables.env; docker exec acme.sh --install-cert -d $BASE_URL --reloadcmd "cat \$CERT_KEY_PATH \$CERT_FULLCHAIN_PATH > /certificates/$BASE_URL.pem"`

8. Reastart haproxy container:
`docker kill -s USR2 haproxy`

9. Set crontab:
`0 0 1 * * docker exec acme.sh --cron && docker kill -s USR2 haproxy`

### Usage
1. Access the admin panel at `https://stream.example.com/ui`
2. Access the recordings gallery at `https://tv.example.com/gallery`

### EPG stream priorities
- prio = 0 - scheduled
- prio = 1 - live
- prio = 2 - live and vod recording
