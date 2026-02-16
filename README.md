# Television

A multi-channel live streaming platform with automated scheduling, Discord integration, and protected video archive. Built on top of [Datarhei Restreamer](https://docs.datarhei.com/restreamer) for video processing.

## Features

- **Multi-Channel Scheduling** - Schedule streams by hour with priority-based automatic switching
- **Automatic Fallback** - Falls back to nearest scheduled stream when current ends
- **Discord Bot Integration** - Live notifications, EPG commands, and stream control
- **Protected Video Archive** - HMAC-based timecode authentication for secure access
- **Replay Service** - Multi-channel endless shuffled HLS playback of recorded videos
- **Mux Service** - Seamless stream multiplexer with adaptive bitrate output and crash recovery
- **HLS Adaptive Streaming** - Quality selection via HLS.js with Plyr player and automatic error recovery
- **Automated SSL** - Let's Encrypt certificates via acme.sh
- **Cloudflare Compatible** - Proper handling of CF-Connecting-IP headers
- **HTTP/2 Support** - ALPN negotiation for modern clients

## Architecture

```
                                    ┌──────────────┐
                                    │   Internet   │
                                    └──────┬───────┘
                                           │
                                    ┌──────▼───────┐
                                    │   HAProxy    │ :80, :443 (HTTP/2)
                                    │ SSL/Routing  │
                                    └──────┬───────┘
              ┌────────────────────────────┼────────────────────────────┐
              │                            │                            │
              │ example.com                │ example.com                │ stream.example.com
              │ /                          │ /live/*                    │
              │                            │                            │
       ┌──────▼──────┐              ┌──────▼──────┐              ┌──────▼──────┐
       │  Quart API  │◄────SSE──────│     Mux     │              │  Restreamer │
       │   :8080     │   /events    │    :8091    │              │   (:8080)   │
       │             │              │             │              │    :1935    │
       │ - Web UI    │              │ - ABR HLS   │◄────HLS──────│    :6000    │
       │ - SSE       │              │ - 1080p/720p│              │             │
       │ - Schedule  │              │ - Playhead  │              │ - Ingest    │
       │ - Discord   │              │   switching │              │ - Transcode │
       │ - Archive   │              │             │              │ - HLS out   │
       └─────────────┘              └─────────────┘              └──────┬──────┘
                                                                        │
                                                                        │ HLS
                                                                        │ (internal)
                                                                        │
                                                                 ┌──────▼──────┐
                                                                 │   Replay    │
                                                                 │    :8090    │
                                                                 │             │
                                                                 │ - Multi-ch  │
                                                                 │ - Auto-disc │
                                                                 │ - Shuffled  │
                                                                 │   playback  │
                                                                 └──────┬──────┘
                                                                        │
                                                           ┌────────────┴────────────┐
                                                           │                         │
                                                     /recordings               /library
                                                    (data/recorder)        (data/library/*)
```

**Data Flow:**
1. **Ingest** → Streamers push to Restreamer via SRT/RTMP
2. **Replay** → Serves recorded/library content as endless shuffled HLS channels
3. **Restreamer** → Ingests live streams + Replay HLS, transcodes, outputs unified HLS
4. **Scheduling** → API tracks schedule, broadcasts playhead via SSE
5. **Muxing** → Mux service follows playhead, switches Restreamer HLS inputs, outputs ABR stream
6. **Delivery** → HAProxy routes requests, terminates SSL, serves to viewers

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Python 3.13, Quart (async Flask) |
| Frontend | Bootstrap 5, Plyr.js, HLS.js |
| Streaming | Datarhei Restreamer 2.12 |
| Replay | FFmpeg HLS, multi-channel |
| Mux | FFmpeg ABR (1080p + 720p) |
| Proxy | HAProxy (HTTP/2, health checks) |
| ASGI Server | Uvicorn |
| Containerization | Docker Compose |
| SSL | acme.sh (Let's Encrypt) |

## Installation

### Prerequisites

- Docker and Docker Compose
- A domain name pointing to your server
- (Optional) Cloudflare for CDN/proxy

### Quick Start

1. **Clone and configure environment**

   ```bash
   git clone <repository-url>
   cd television
   cp variables.env.dist variables.env
   ```

   Edit `variables.env` and set all required variables (see [Configuration](#configuration)).

2. **Initialize data directories**

   ```bash
   ./init.sh
   ```

3. **Start the acme.sh service**

   ```bash
   docker-compose up -d acme-sh
   ```

4. **Register ACME account**

   ```bash
   source variables.env
   docker exec acme.sh --register-account -m $EMAIL
   ```

   Copy the `ACCOUNT_THUMBPRINT` from the output and add it to `variables.env`.

5. **Start the stack**

   ```bash
   docker-compose up -d --build --remove-orphans
   ```

6. **Issue SSL certificates**

   ```bash
   source variables.env
   docker exec acme.sh --issue -d $SERVER_NAME --stateless
   docker exec acme.sh --issue -d $CORE_API_HOSTNAME --stateless
   ```

7. **Install certificates**

   ```bash
   source variables.env
   docker exec acme.sh --install-cert -d $SERVER_NAME \
     --reloadcmd "cat \$CERT_KEY_PATH \$CERT_FULLCHAIN_PATH > /certificates/$SERVER_NAME.pem"
   docker exec acme.sh --install-cert -d $CORE_API_HOSTNAME \
     --reloadcmd "cat \$CERT_KEY_PATH \$CERT_FULLCHAIN_PATH > /certificates/$CORE_API_HOSTNAME.pem"
   ```

8. **Reload HAProxy**

   ```bash
   docker kill -s USR2 haproxy
   ```

9. **Set up automatic certificate renewal**

   Add to crontab:
   ```bash
   0 0 1 * * docker exec acme.sh --cron && docker kill -s USR2 haproxy
   ```

## Configuration

### Environment Variables

| Variable | Description |
|----------|-------------|
| `SERVER_NAME` | Main domain (e.g., `example.com`) |
| `EMAIL` | Admin email for ACME certificates |
| `ACCOUNT_THUMBPRINT` | ACME account thumbprint (from step 4) |
| `CORE_API_HOSTNAME` | Restreamer hostname (e.g., `stream.example.com`) |
| `CORE_API_AUTH_USERNAME` | Restreamer admin username |
| `CORE_API_AUTH_PASSWORD` | Restreamer admin password |
| `API_LOG_LEVEL_API` | Log level for Uvicorn/API (default: `INFO`) |
| `API_LOG_LEVEL_JOB` | Log level for APScheduler (default: `WARN`) |
| `API_LOG_LEVEL_STREAM` | Log level for stream manager (default: `INFO`) |
| `API_LOG_LEVEL_CONTENT` | Log level for content/routes (default: `INFO`) |
| `API_LOG_LEVEL_DISCORD` | Log level for Discord bot (default: `INFO`) |
| `API_LOG_LEVEL_SSE` | Log level for SSE events (default: `WARN`) |
| `API_VOD_TOKEN` | Bearer token for video upload API |
| `FLASK_SECRET_KEY` | Flask session encryption key |
| `TIMECODE_SECRET_KEY` | HMAC key for archive timecodes |
| `DISCORDBOT_ENABLED` | Enable Discord bot (`true`/`false`, default: `false`) |
| `DISCORDBOT_TOKEN` | Discord bot token |
| `DISCORDBOT_LIVE_CHANNEL_ID` | Discord channel ID for live stream announcements |
| `DISCORDBOT_TIMECODE_CHANNEL_ID` | Discord channel ID for timecode messages |
| `DISCORDBOT_LIVE_CHANNEL_UPDATE` | Live announcement interval in minutes (default: `1440`) |
| `FRONTEND_MODE` | `mux` (default) or `legacy` - see [Frontend Modes](#frontend-modes) |

### Replay Service Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RECORDINGS_DIR` | `/recordings` | Path to recorder MP4/MKV files |
| `LIBRARY_DIR` | `/library` | Path to library directories (auto-discovered) |
| `HLS_SEGMENT_TIME` | `4` | HLS segment duration (seconds) |
| `HLS_LIST_SIZE` | `20` | Number of segments in playlist |
| `VIDEO_BITRATE` | `4000k` | Video encoding bitrate (when transcoding) |
| `AUDIO_BITRATE` | `128k` | Audio encoding bitrate (when transcoding) |
| `REPLAY_PORT` | `8090` | HTTP server port |
| `REPLAY_SCAN_INTERVAL` | `60` | Directory scan interval (seconds) |

### Mux Service Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `API_URL` | `http://api:8080` | Internal API URL for SSE connection |
| `RESTREAMER_INTERNAL_URL` | `http://restreamer:8080` | Internal restreamer URL (bypasses public hostname) |
| `CORE_API_HOSTNAME` | - | Public restreamer hostname (for URL rewriting) |
| `MUX_MODE` | `copy` | Mode: `copy` (passthrough) or `abr` (adaptive bitrate) |
| `HLS_SEGMENT_TIME` | `4` | HLS segment duration (seconds) |
| `HLS_LIST_SIZE` | `20` | Number of segments in playlist |
| `ABR_PRESET` | `veryfast` | x264 encoder preset (ABR mode only) |
| `ABR_GOP_SIZE` | `48` | Keyframe interval in frames (ABR mode only) |
| `ABR_VARIANTS` | See below | JSON array of ABR variants (ABR mode only) |

**ABR_VARIANTS format:**
```json
[
  {"height": 1080, "video_bitrate": "5000k", "audio_bitrate": "192k"},
  {"height": 720, "video_bitrate": "2800k", "audio_bitrate": "128k"},
  {"height": 576, "video_bitrate": "1400k", "audio_bitrate": "96k"}
]
```

Each variant specifies a resolution height (source is capped at this), video bitrate, and audio bitrate. Source stream (copy) is always included as stream_0.

### Icecast Audio Streaming Variables

> **Note:** Icecast is disabled by default. The icecast service is commented out in `docker-compose.yml`. To enable it, uncomment the icecast service, set `ICECAST_ENABLED=true` in `variables.env`, and uncomment the icecast backend in `haproxy.cfg`.

| Variable | Default | Description |
|----------|---------|-------------|
| `ICECAST_ENABLED` | `false` | Enable audio-only streaming via Icecast |
| `ICECAST_SOURCE_PASSWORD` | - | Password for FFmpeg to connect to Icecast |
| `ICECAST_ADMIN_PASSWORD` | - | Icecast admin interface password |
| `ICECAST_RELAY_PASSWORD` | - | Icecast relay password |
| `ICECAST_HOST` | `icecast` | Internal Icecast hostname |
| `ICECAST_PORT` | `8000` | Icecast server port |
| `ICECAST_MOUNT` | `/stream.mp3` | Mount point for audio stream |
| `ICECAST_AUDIO_BITRATE` | `128k` | Audio encoding bitrate (`320k` in `variables.env.dist`) |
| `ICECAST_AUDIO_FORMAT` | `mp3` | Audio format: `mp3` or `aac` |

When enabled, the mux service automatically extracts audio from the muxed live stream and sends it to Icecast. The audio stream is available at `https://example.com/live/stream.mp3`.

## Services

### Replay Service

Multi-channel HLS streaming service that serves video files as endless shuffled streams.

**Features:**
- **Multi-channel support** - One channel per directory
- **Auto-discovery** - Automatically detects new directories in `/library`
- **Per-channel config** - Optional `channel.json` for transcoding settings
- **File watching** - Detects added/removed files and updates playlist
- **Copy mode** (default) - No transcoding, minimal CPU usage

**Channels:**
- `/replay/recorder/` - Always present, serves `data/recorder`
- `/replay/<name>/` - Auto-discovered from `data/library/<name>`

**Per-channel configuration** (`channel.json` in the channel directory):
```json
{
    "transcode": false
}
```
- `transcode: false` (default) - Copy mode, passthrough without re-encoding
- `transcode: true` - Full transcoding to configured bitrate

**Copy mode compatibility:**
- Smooth file-to-file transitions in copy mode require compatible source streams (codec, resolution, fps, and audio layout).
- The replay service keeps the largest compatible subset and skips incompatible files (with warnings in logs) to avoid stream breaks.
- For best non-transcoding results, remux files to MPEG-TS (`.ts`) with aligned stream parameters.

**Conflict detection:** If `data/library/recorder` exists, it's skipped to avoid conflict with the reserved `recorder` channel.

### Mux Service

Stream multiplexer that monitors the API's playhead and outputs a continuous stream from Restreamer channels.

**Features:**
- **SSE monitoring** - Listens to `/events` for playhead changes
- **Seamless switching** - Continuous segment numbering with `#EXT-X-DISCONTINUITY` markers across playhead transitions. Players stay connected without reloading.
- **Crash recovery** - Automatic recovery with discontinuity markers on ffmpeg crashes. No full resets that break clients.
- **Internal routing** - Direct container-to-container access bypasses Cloudflare for optimal performance
- **Two modes** - Copy (passthrough) or ABR (adaptive bitrate)
- **No upscaling** - ABR mode caps output at source resolution
- **Stale segment cleanup** - Background cleanup of old `.ts` segments to prevent disk filling
- **Modular architecture** - Separated concerns: config, ffmpeg runner, segment store, stream management, playhead monitoring

**Modes** (set via `MUX_MODE` env var):

| Mode | Description |
|------|-------------|
| `copy` (default) | Passthrough, no transcoding. Single stream output. |
| `abr` | Adaptive bitrate with source passthrough + transcoded variants. |

**Copy mode output:**
- `/live/stream.m3u8` - Single quality stream (passthrough)

**ABR mode output:**
- `/live/stream.m3u8` - Master playlist (ABR)
- `/live/stream_0/` - Source (passthrough, no re-encoding)
- `/live/stream_1/` - 1080p (5000k video, 192k audio) - only if source > 1080p
- `/live/stream_2/` - 720p (2800k video, 128k audio) - only if source > 720p
- `/live/stream_3/` - 576p (1400k video, 96k audio) - only if source > 576p

**Switching behavior:**
- On playhead change: segments continue numbering, `#EXT-X-DISCONTINUITY` tag injected, ffmpeg restarts with new input (~4s gap)
- On crash: seamless recovery with discontinuity marker, segment numbering continues (no viewer disruption)

### Frontend Modes

| Mode | Template | Behavior |
|------|----------|----------|
| `mux` (default) | `index_mux.html` | Static stream from `/live/stream.m3u8`, mux service handles switching |
| `legacy` | `index_legacy.html` | SSE-based client-side stream switching |

Set via `FRONTEND_MODE` environment variable.

**Frontend Features:**
- **Automatic error recovery** - HLS.js detects network/media errors and automatically recovers
- **Global modals** - Weather and Archive modals accessible from all pages
- **SSE real-time updates** - Live visitor count, EPG changes, playhead updates
- **Responsive design** - Mobile-friendly Bootstrap 5 interface

## Usage

### Setting Up Streams

1. Access the Restreamer admin panel at `https://stream.example.com/ui`

2. Create channels and configure them with JSON metadata in the Description field:

   ```json
   { "start_at": "2100", "prio": 0, "details": "Evening show" }
   ```

   | Field | Description |
   |-------|-------------|
   | `start_at` | Military time e.g. `"1745"` (HH:MM), `"now"` for immediate, or `"never"` to disable |
   | `prio` | Priority level (higher takes precedence) |
   | `details` | Optional description for Discord announcements |

### Adding Replay Content

**Recorder channel** (always present):
```bash
# Add files to data/recorder/
cp video.mp4 data/recorder/
```

**Library channels** (auto-discovered):
```bash
# Create a new channel
mkdir -p data/library/mychannel

# Add files
cp video.mp4 data/library/mychannel/

# Optional: configure transcoding
echo '{"transcode": true}' > data/library/mychannel/channel.json
```

### Streaming URLs

**SRT (recommended):**
```
srt://SERVERADDR:6000?mode=caller&transtype=live&pkt_size=1316&streamid=STREAM-UUID.stream,mode:publish,token:CHANGEME
```

**RTMP:**
```
rtmp://SERVERADDR/STREAM-UUID.stream/CHANGEME
```

### Discord Bot Commands

| Command | Role | Description |
|---------|------|-------------|
| `.help` | - | Show all available commands |
| `.hello` | worshipper | Bot status check |
| `.time` | - | Current server time (UTC) |
| `.epg` | - | Show stream schedule |
| `.now` | - | Current stream info |
| `.streams` | bosmang | List all Restreamer processes and their states |
| `.start <name or id>` | bosmang | Start a Restreamer process |
| `.stop <name or id>` | bosmang | Stop a Restreamer process |
| `.rnd` | bosmang | Switch to a random stream from the database |

#### Stream Control Workflow

1. Use `.streams` to list all available Restreamer processes with their names, IDs, and current state (running/stopped)
2. Use `.start <name or id>` to start a specific process (name matching is case-insensitive)
3. Use `.stop <name or id>` to stop it
4. Use `.rnd` to switch to a random stream (excludes the currently playing one)

### API Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/` | GET | Public | Live stream player |
| `/weather` | GET | Public | Weather visualization page |
| `/archive` | GET/POST | Timecode | Video archive |
| `/request-timecode` | POST | Public | Request archive access |
| `/logout` | GET | Public | Clear session and logout |
| `/events` | GET | Public | SSE stream (playhead, visitors, EPG) |
| `/health` | GET | Public | Health check endpoint |
| `/live.m3u8` | GET | Public | IPTV playlist (dynamic) |
| `/epg.xml` | GET | Public | XMLTV EPG (dynamic) |
| `/video` | POST | Bearer | Upload video files |
| `/video/watch/<name>` | GET | Timecode | Video player page |
| `/video/<file>` | GET | Timecode | Stream video file |
| `/video/download/<file>` | GET | Timecode | Download video file |
| `/thumb/<file>` | GET | Timecode | Serve thumbnail image |
| `/replay/<channel>/playlist.m3u8` | GET | Public | HLS replay stream for channel |
| `/live/stream.m3u8` | GET | Public | Mux stream (master playlist) |

## Logging and Monitoring

### Access Log Filtering

To reduce noise in logs, successful (200 OK) responses are filtered from access logs:

**API Service** (`api/main.py`):
- All 200 OK and 404 Not Found responses are hidden from uvicorn/hypercorn access logs
- Only other status codes (3xx, 4xx, 5xx) appear in logs

**Mux Service** (`mux/app/server.py`):
- 200 OK responses for `/health` and `.m3u8` playlist requests are hidden
- Segment requests (`.ts`) and errors still appear in logs

**Visitor Tracking:**
- Private/internal IP addresses (Docker, LAN, loopback) are excluded from visitor counts
- Prevents internal services from appearing as watchers

### Log Levels

Configure granular logging via environment variables:

| Variable | Default | Component |
|----------|---------|-----------|
| `API_LOG_LEVEL_API` | `INFO` | Uvicorn web server |
| `API_LOG_LEVEL_JOB` | `WARN` | APScheduler background jobs |
| `API_LOG_LEVEL_STREAM` | `INFO` | Stream manager operations |
| `API_LOG_LEVEL_CONTENT` | `INFO` | Route handlers and content |
| `API_LOG_LEVEL_DISCORD` | `INFO` | Discord bot |
| `API_LOG_LEVEL_SSE` | `WARN` | Server-sent events |

## Project Structure

```
television/
├── api/
│   ├── static/                  # CSS, JS, images
│   ├── templates/
│   │   ├── base.html            # Base template with modals
│   │   ├── index_mux.html       # Mux mode template
│   │   └── index_legacy.html    # Legacy mode template
│   ├── main.py                  # Application entry point
│   ├── services/                # Core/backend service layer
│   │   ├── core_api.py          # Restreamer Core API client
│   │   └── stream_manager.py    # Stream orchestration
│   ├── integrations/
│   │   └── discord_bot_manager.py
│   ├── web/                     # HTTP route layer
│   │   ├── frontend_routes.py
│   │   ├── api_routes.py
│   │   ├── playlist_routes.py
│   │   ├── sse_routes.py
│   │   └── ingest_routes.py
│   ├── utils/
│   │   └── obfuscation.py       # Hostname obfuscation
│   ├── Dockerfile
│   └── requirements.txt
├── replay/
│   ├── app/
│   │   ├── main.py              # HTTP server & lifecycle
│   │   ├── channel.py           # Channel class & ffmpeg
│   │   └── config.py            # Configuration
│   ├── scripts/
│   │   └── run.sh
│   ├── channel.json.dist        # Example channel config
│   ├── Dockerfile
│   └── requirements.txt
├── mux/
│   ├── app/
│   │   ├── main.py              # Application orchestration
│   │   ├── config.py            # Configuration management
│   │   ├── ffmpeg_runner.py     # FFmpeg process lifecycle
│   │   ├── segment_store.py     # HLS segment & playlist management
│   │   ├── stream_manager.py    # Stream switching logic
│   │   ├── playhead_monitor.py  # SSE monitoring
│   │   ├── hls_viewer_tracker.py # HLS viewer counting
│   │   ├── server.py            # HTTP server for HLS output
│   │   └── utils.py             # Utility functions
│   ├── scripts/
│   │   └── run.sh
│   ├── Dockerfile
│   └── requirements.txt
├── haproxy/
│   ├── haproxy.cfg
│   └── Dockerfile
├── data/                        # Runtime data (gitignored)
│   ├── acme/                    # ACME certificates data
│   ├── certificates/
│   ├── restreamer/
│   ├── recorder/                # Recorder channel files (vod/, thumb/)
│   └── library/                 # Library channels (auto-discovered)
├── docker-compose.yml
├── variables.env.dist
└── init.sh
```

## License

[Unlicense](LICENSE) - Public Domain
