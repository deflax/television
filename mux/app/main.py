"""Mux Service - HLS stream multiplexer with clean transitions.

Monitors the API playhead via SSE and switches between input streams to
produce a continuous HLS output stream at /live/stream.m3u8.

The key improvement is that transitions happen at clean segment boundaries:
1. Stop FFmpeg (allows current segment to finish)
2. Mark discontinuity in segment store
3. Start new FFmpeg with next sequence number
4. Playlists are generated dynamically from segment store
"""

import asyncio
import logging
import signal

from config import MUX_MODE, HLS_SEGMENT_TIME, HLS_LIST_SIZE, ABR_VARIANTS, SERVER_PORT, API_URL, LOG_LEVEL
from hls_viewer_tracker import cleanup_loop as hls_cleanup_loop, report_loop as hls_report_loop
from playhead_monitor import PlayheadMonitor
from stream_manager import stream_manager
from segment_store import segment_store

# Configure logging
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

# Shutdown event
shutdown_event = asyncio.Event()

# Playhead monitor (set in main())
_monitor: PlayheadMonitor | None = None


async def on_playhead_change(new_url: str, stream_name: str) -> None:
    """Handle playhead change from API.
    
    Always calls switch() which handles both IDLE state (starts fresh)
    and RUNNING state (performs transition).
    """
    logger.info(f'Switching to stream: {stream_name}')
    
    success = await stream_manager.switch(new_url)
    
    if not success:
        logger.error(f'Failed to switch to {stream_name}')


async def run_server() -> None:
    """Run the HTTP server."""
    from server import app
    import uvicorn
    
    logger.info(f'Starting HTTP server on port {SERVER_PORT}')
    
    config = uvicorn.Config(
        app,
        host='0.0.0.0',
        port=SERVER_PORT,
        log_level='warning',
    )
    server = uvicorn.Server(config)
    await server.serve()


async def cleanup_loop() -> None:
    """Periodically clean up old segments."""
    while not shutdown_event.is_set():
        try:
            await asyncio.sleep(30)
            await segment_store.cleanup_old_segments()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f'Cleanup error: {e}', exc_info=True)


async def shutdown() -> None:
    """Graceful shutdown."""
    logger.info('Shutting down...')
    shutdown_event.set()
    if _monitor:
        _monitor.stop()
    await stream_manager.stop()
    logger.info('Shutdown complete')


def handle_signal() -> None:
    """Signal handler."""
    asyncio.create_task(shutdown())


async def main() -> None:
    """Main entry point."""
    # Log startup info
    logger.info('Mux service starting...')
    logger.info(
        f'Mode: {MUX_MODE} | Segment time: {HLS_SEGMENT_TIME}s | '
        f'Playlist size: {HLS_LIST_SIZE}'
    )
    if MUX_MODE == 'abr':
        variant_desc = ', '.join(
            f"{v['height']}p@{v['video_bitrate']}" for v in ABR_VARIANTS
        )
        logger.info(f'ABR variants: source (copy) + {variant_desc}')
    
    # Setup signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_signal)
    
    # Create playhead monitor
    global _monitor
    _monitor = PlayheadMonitor(on_change=on_playhead_change)
    
    # Start background tasks
    manager_task = asyncio.create_task(stream_manager.run_loop())
    cleanup_task = asyncio.create_task(cleanup_loop())
    hls_cleanup_task = asyncio.create_task(hls_cleanup_loop())
    hls_report_task = asyncio.create_task(hls_report_loop(API_URL))
    server_task = asyncio.create_task(run_server())
    
    # Run playhead monitor (blocks until stopped)
    try:
        await _monitor.run()
    except asyncio.CancelledError:
        pass
    
    # Cleanup (only if not already shutting down)
    if not shutdown_event.is_set():
        await shutdown()
    
    for task in [manager_task, cleanup_task, hls_cleanup_task, hls_report_task, server_task]:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


if __name__ == '__main__':
    asyncio.run(main())
