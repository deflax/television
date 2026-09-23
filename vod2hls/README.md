# VOD-to-HLS Service

This directory contains Television's internal live-like HLS channel producer. The Python producer shuffles video files, streams them through FFmpeg, and writes a rolling playlist and segments to a named Docker volume. The `vod-web` NGINX service serves only those HLS assets to other containers on Television's Compose network.

The production deployment is defined by `../docker-compose.yml`; this directory intentionally has no second Compose stack.

## Integrated paths and endpoints

- Host media: `television/data/archive/`
- Producer media mount: `/media` (read-only)
- Shared output volume: `vod-hls`
- Restreamer source URL: `http://vod-web/hls/live.m3u8`
- Internal web health URL: `http://vod-web/health`

Neither `vod-web` nor its raw HLS output has a host port or HAProxy route. Producer status and control files are also blocked by NGINX.

## Development and tests

Run commands below from the `television/` repository root.

Run the producer unit tests in a reproducible container:

```bash
docker build -t television-vod2hls-test ./vod2hls/producer
docker run --rm television-vod2hls-test pytest -q
```

Or, with Python and pytest installed locally:

```bash
python -m pytest -q vod2hls/producer/tests
```

Generate short sample inputs (requires host FFmpeg):

```bash
bash vod2hls/scripts/generate-test-media.sh ./data/archive
```

The script accepts a different output directory as its first argument when fixtures should not be placed in the production archive.

## Runtime checks and control

```bash
# Start or rebuild only this service pair
docker compose --env-file variables.env up -d --build vod-producer vod-web

# Check container health and internal HTTP responses
docker compose --env-file variables.env ps vod-producer vod-web
docker compose --env-file variables.env exec -T vod-web wget -qO- http://localhost/health
docker compose --env-file variables.env exec -T vod-web wget -qO- http://localhost/hls/live.m3u8

# Check the producer's content-aware health and inspect its private status
docker compose --env-file variables.env exec -T vod-producer python -m app.health --check
docker compose --env-file variables.env exec -T vod-producer cat /hls/status.json

# Skip the currently playing file without restarting the supervisor
docker compose --env-file variables.env exec -T vod-producer python -m app.control skip-current

# Logs, restart, and stop
docker compose --env-file variables.env logs -f vod-producer vod-web
docker compose --env-file variables.env restart vod-producer vod-web
docker compose --env-file variables.env stop vod-producer vod-web
```

The producer is intentionally unhealthy while the archive has no supported media. `vod-web` can remain healthy in that state, but the playlist returns 404 until output is produced.

## Producer behavior

Supported inputs are `.mp4`, `.mkv`, `.mov`, `.avi`, `.webm`, and `.m4v` by default. Matching is case-insensitive. The producer rescans between complete shuffled cycles, skips failed inputs, and adds silent AAC audio when an input has no audio stream. Output is fixed-dimension H.264/AAC HLS according to the `VOD_*` settings in `../variables.env.dist`.

`producer/app/` contains the runtime code, `producer/tests/` contains its unit tests, and `nginx/nginx.conf` defines the internal-only HLS server.
