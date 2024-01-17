# Television
Multi channel stream setup with Flask REST API for scheduling channels.

### Install
1. `cp variables.env.dist variables.env` and set the required variables
2. run `./init.sh` to prepare data directories and generate self signed certs to bootstrap the load balancer
3. run the docker-compose stack using `docker-compose up -d --build --remove-orphans`
4. run `./renew-certificates.sh` periodically to generate/update the certificates

### Usage
1. Access the admin panel at `https://stream.example.com/ui`
2. Access the recordings gallery at `https://vod.example.com/`

### EPG stream priorities
prio = 0 - scheduled
prio = 1 - live
prio = 2 - live and vod recording

### Purge vod database
`docker exec -ti television_archive_1 /app/gallery.js storage --storage /data/storage --database /data/config/database.db -l debug purge`