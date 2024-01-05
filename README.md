# Television

## Install
Multi channel stream setup with Flask REST API for scheduling channels.

1. run `./init.sh` to prepare data directories and generate self signed certs to bootstrap the load balancer
2. `cp variables.env.dist variables.env` and set the required variables
3. run `./renew-certificates.sh` periodically to generate/update the certificates
4. run the docker-compose stack using `docker-compose up -d --build --remove-orphans`
5. access the admin panel at `https://stream.example.com/ui`


## Recorder

1. Add Publication - Protocols/RTSP:
- Service Name: Recorder
- Protocol: rtsp://
- Address: recorder.local:8554/live
