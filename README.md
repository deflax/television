# Television
Multi channel stream setup with Flask REST API for scheduling channels.

### Install
1. `cp variables.env.dist variables.env` and set the required variables

2. Start the acme-sh service:
`docker-compose up -d acme-sh`

3. Register acme account:
`source variables.env; docker exec acme.sh --register-account -m $EMAIL`

4. Set the `ACCOUNT_THUMBPRINT` variable

5. Run the stack using:
<pre>
docker-compose up -d --build --remove-orphans
</pre>

6. Issue a certificate:
<pre>
source variables.env; \
docker exec acme.sh --issue -d $SERVER_NAME --stateless; \
docker exec acme.sh --issue -d $CORE_API_HOSTNAME --stateless
</pre>

7. Install the certificate:
<pre>
source variables.env; \
docker exec acme.sh --install-cert -d $SERVER_NAME --reloadcmd "cat \$CERT_KEY_PATH \$CERT_FULLCHAIN_PATH > /certificates/$SERVER_NAME.pem"; \
docker exec acme.sh --install-cert -d $CORE_API_HOSTNAME --reloadcmd "cat \$CERT_KEY_PATH \$CERT_FULLCHAIN_PATH > /certificates/$CORE_API_HOSTNAME.pem"
</pre>

8. Restart haproxy container:
<pre>
docker kill -s USR2 haproxy
</pre>

9. Set crontab:
<pre>
0 0 1 * * docker exec acme.sh --cron && docker kill -s USR2 haproxy
</pre>

### Usage
1. Access the admin panel at `https://stream.example.com/ui` to setup the channels that we want to detect
2. Control the api from the admin panel using json in the Description Metadata of the channel:
<pre>
{ "start_at": "21", "prio": 0 }
</pre>
