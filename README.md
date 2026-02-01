# Television

A multi-channel live streaming platform with automated scheduling, Discord integration, and protected video archive. Built on top of [Datarhei Restreamer](https://docs.datarhei.com/restreamer) for video processing.

## Features

- **Multi-Channel Scheduling** - Schedule streams by hour with priority-based automatic switching
- **Automatic Fallback** - Falls back to nearest scheduled stream when current ends
- **Discord Bot Integration** - Live notifications, EPG commands, and remote recording control
- **Protected Video Archive** - HMAC-based timecode authentication for secure access
- **Live Recording** - Record streams with automatic thumbnail generation
- **HLS Adaptive Streaming** - Quality selection via HLS.js with Plyr player
- **Automated SSL** - Let's Encrypt certificates via acme.sh
- **Cloudflare Compatible** - Proper handling of CF-Connecting-IP headers

## Architecture

```
                    ┌─────────────────┐
                    │    Internet     │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │    HAProxy      │  :80, :443
                    │  (SSL, Routing) │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼───────┐ ┌────▼─────┐ ┌──────▼──────┐
     │   Flask API    │ │Restreamer│ │   Icecast   │
     │    (:8080)     │ │ (:8080)  │ │   (:8000)   │
     │                │ │ (:1935)  │ │  (optional) │
     │ - Web UI       │ │ (:6000)  │ └─────────────┘
     │ - Stream Mgmt  │ └──────────┘
     │ - Discord Bot  │
     │ - Archive      │
     └────────────────┘
```

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Python 3.13, Flask 3.1 |
| Frontend | Bootstrap 5, Plyr.js, HLS.js |
| Streaming | Datarhei Restreamer 2.12 |
| Proxy | HAProxy |
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
| `API_LOG_LEVEL_API` | Log level for Hypercorn/API (default: `INFO`) |
| `API_LOG_LEVEL_JOB` | Log level for APScheduler (default: `WARN`) |
| `API_LOG_LEVEL_STREAM` | Log level for stream manager (default: `INFO`) |
| `API_LOG_LEVEL_CONTENT` | Log level for content/routes (default: `INFO`) |
| `API_LOG_LEVEL_DISCORD` | Log level for Discord bot (default: `INFO`) |
| `API_LOG_LEVEL_SSE` | Log level for SSE events (default: `WARN`) |
| `API_VOD_TOKEN` | Bearer token for video upload API |
| `FLASK_SECRET_KEY` | Flask session encryption key |
| `TIMECODE_SECRET_KEY` | HMAC key for archive timecodes |
| `DISCORDBOT_TOKEN` | Discord bot token |
| `DISCORDBOT_GUILD_ID` | Discord server ID |
| `DISCORDBOT_CHANNEL_ID` | Channel for bot messages |

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
| `.rec` | bosmang | Start recording current stream |
| `.recstop` | bosmang | Stop recording |

#### Stream Control Workflow

1. Use `.streams` to list all available Restreamer processes with their names, IDs, and current state (running/stopped)
2. Use `.start <name or id>` to start a specific process (name matching is case-insensitive)
3. Use `.stop <name or id>` to stop it

### API Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/` | GET | Public | Live stream player |
| `/archive` | GET/POST | Timecode | Video archive |
| `/request-timecode` | POST | Public | Request archive access |
| `/playhead` | GET | Public | Current stream info (JSON) |
| `/video` | POST | Bearer | Upload video files |

## Project Structure

```
television/
├── api/
│   ├── app/
│   │   ├── static/              # CSS, JS, images
│   │   ├── templates/           # Jinja2 templates
│   │   ├── flask_api.py         # Application entry point
│   │   ├── frontend.py          # Routes and views
│   │   ├── stream_manager.py    # Stream orchestration
│   │   ├── discord_bot_manager.py
│   │   └── timecode_manager.py
│   ├── Dockerfile
│   └── requirements.txt
├── config/
│   ├── haproxy/
│   │   └── haproxy.cfg
│   └── icecast/
│       └── icecast.xml.template
├── data/                        # Runtime data (gitignored)
│   ├── certificates/
│   ├── restreamer/
│   └── recorder/
├── docker-compose.yml
├── variables.env.dist
└── init.sh
```

## License

[Unlicense](LICENSE) - Public Domain
