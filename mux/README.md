# Mux Service

HLS stream multiplexer that monitors an API playhead via SSE and switches between input streams to produce a continuous HLS output stream.

## Features

- **Clean stream transitions** - Switches happen at segment boundaries, no playback glitches
- **ABR support** - Adaptive bitrate with source passthrough + transcoded variants
- **Copy mode** - Simple passthrough for single-quality output
- **Dynamic playlists** - Generated on-demand from segment store for consistency
- **Crash recovery** - Auto-restarts FFmpeg on failures with discontinuity markers

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         main.py                             │
│  - Coordinates all components                               │
│  - Signal handling (SIGTERM/SIGINT)                         │
└─────────────────┬───────────────────────────────────────────┘
                  │
    ┌─────────────┼─────────────┬─────────────────┐
    ▼             ▼             ▼                 ▼
┌────────┐  ┌──────────┐  ┌──────────┐    ┌────────────┐
│Playhead│  │ Stream   │  │ HTTP     │    │ Cleanup    │
│Monitor │  │ Manager  │  │ Server   │    │ Loop       │
└───┬────┘  └────┬─────┘  └────┬─────┘    └─────┬──────┘
    │            │             │                │
    │ on_change  │             │                │
    └───────────►│             │                │
                 │             │                │
           ┌─────▼─────┐       │                │
           │  FFmpeg   │       │                │
           │  Runner   │       │                │
           └─────┬─────┘       │                │
                 │             │                │
                 ▼             ▼                ▼
           ┌─────────────────────────────────────────┐
           │            Segment Store                │
           │  - Tracks all segments with metadata    │
           │  - Generates playlists dynamically      │
           │  - Handles discontinuity markers        │
           │  - Cleans up old segments               │
           └─────────────────────────────────────────┘
```

### Components

| File | Description |
|------|-------------|
| `main.py` | Entry point, starts all async tasks |
| `config.py` | Environment configuration |
| `utils.py` | Shared utilities (file stability checks) |
| `segment_store.py` | Central store for segments, generates playlists |
| `ffmpeg_runner.py` | FFmpeg process wrapper with segment detection |
| `stream_manager.py` | Handles stream lifecycle and transitions |
| `playhead_monitor.py` | SSE client watching API for URL changes |
| `server.py` | HTTP server for HLS output |

## How Stream Switching Works

The key improvement over typical implementations is that transitions happen at **clean segment boundaries**:

1. **Playhead change detected** via SSE from API
2. **Stop current FFmpeg** gracefully (lets current segment finish writing)
3. **Mark discontinuity** in segment store
4. **Start new FFmpeg** with next sequence number
5. **Wait for first segment** from new stream
6. **Resume normal operation**

This eliminates the "back and forth" playback issue caused by overlapping FFmpeg instances or segment number collisions.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `API_URL` | `http://api:8080` | API endpoint for SSE playhead events |
| `MUX_MODE` | `copy` | `copy` (passthrough) or `abr` (adaptive bitrate) |
| `HLS_SEGMENT_TIME` | `4` | Segment duration in seconds |
| `HLS_LIST_SIZE` | `20` | Max segments in playlist |
| `TRANSITION_TIMEOUT` | `15` | Max seconds to wait for new stream segment |

### ABR Mode Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `ABR_PRESET` | `veryfast` | x264 encoding preset |
| `ABR_GOP_SIZE` | `48` | Keyframe interval |
| `ABR_VARIANTS` | (see below) | JSON array of variant definitions |

Default ABR variants:
```json
[
  {"height": 720, "video_bitrate": "2800k", "audio_bitrate": "128k"},
  {"height": 576, "video_bitrate": "1400k", "audio_bitrate": "96k"}
]
```

### URL Rewriting

| Variable | Default | Description |
|----------|---------|-------------|
| `RESTREAMER_INTERNAL_URL` | `http://restreamer:8080` | Internal restreamer URL |
| `CORE_API_HOSTNAME` | (empty) | Public hostname to rewrite |

When set, URLs starting with `https://{CORE_API_HOSTNAME}/` are rewritten to use the internal URL.

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check, returns `{"status": "ok", "stream_ready": bool}` |
| `GET /live/stream.m3u8` | Master playlist (ABR) or media playlist (copy mode) |
| `GET /live/stream_N/playlist.m3u8` | Variant playlist (ABR mode only) |
| `GET /live/*.ts` | Segment files |

## Running

### Docker

```bash
docker build -t mux .
docker run -p 8091:8091 \
  -e API_URL=http://api:8080 \
  -e MUX_MODE=copy \
  mux
```

### Local Development

```bash
pip install -r requirements.txt
cd app && python main.py
```

## Dependencies

- Python 3.10+ (uses `X | None` union syntax)
- FFmpeg with libx264 and libmp3lame
- httpx (async HTTP client)
- quart (async web framework)
- uvicorn (ASGI server)

## Expected API Format

The service expects the API to provide an SSE endpoint at `/events` that emits events in this format:

```
event: playhead
data: {"head": "https://example.com/stream.m3u8", "name": "Stream Name"}
```

The `head` field contains the HLS stream URL to switch to. The `name` field is used for logging.

## File Structure

```
/workspace
├── app/
│   ├── config.py           # Environment configuration
│   ├── utils.py            # Shared utilities
│   ├── segment_store.py    # Segment tracking and playlist generation
│   ├── ffmpeg_runner.py    # FFmpeg process management
│   ├── stream_manager.py   # Stream lifecycle and transitions
│   ├── playhead_monitor.py # SSE client for API events
│   ├── server.py           # HTTP server (Quart/uvicorn)
│   └── main.py             # Entry point
├── scripts/
│   └── run.sh              # Docker entrypoint
├── Dockerfile
├── requirements.txt
└── README.md
```

## Troubleshooting

### Stream not starting
- Check that the API is reachable at `API_URL`
- Verify the API returns valid SSE events with `head` field
- Check FFmpeg logs (set log level to DEBUG)

### Playback issues after switch
- This should not happen with the new architecture
- If it does, check that `#EXT-X-DISCONTINUITY` tags appear in playlists
- Verify segment sequence numbers are monotonically increasing

### High CPU usage
- In ABR mode, transcoding requires significant CPU
- Consider using `ABR_PRESET=ultrafast` for lower CPU at cost of quality
- In copy mode, CPU usage should be minimal

### Segments not cleaning up
- Cleanup runs every 30 seconds
- Segments older than `HLS_LIST_SIZE * HLS_SEGMENT_TIME * 3` seconds are removed
- Check logs for cleanup errors
