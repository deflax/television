# pyright: reportMissingImports=false, reportImplicitRelativeImport=false

import asyncio
import copy
import json
import time

from quart import request

from web.helpers import get_client_address
from web.state import WebRouteState, apply_hls_viewer_display_grace


SSE_TO_HLS_GRACE_SECONDS = 45.0


def register_sse_routes(app, stream_manager, loggers, discord_bot_manager, state: WebRouteState) -> None:
    """Register SSE and realtime state endpoints."""

    def _prune_recent_sse_disconnects(now: float) -> None:
        cutoff = now - SSE_TO_HLS_GRACE_SECONDS
        for ip, ts in list(state.recent_sse_disconnects.items()):
            if ts < cutoff:
                del state.recent_sse_disconnects[ip]

    async def _broadcast_visitors() -> None:
        event = {
            'type': 'visitors',
            'data': json.dumps({'visitors': state.hls_viewer_count}),
        }
        for queue in list(state.sse_clients):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def _broadcast_playhead() -> None:
        if stream_manager is None:
            return

        event = {
            'type': 'playhead',
            'data': json.dumps(stream_manager.playhead),
        }
        for queue in list(state.sse_clients):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def _broadcast_epg() -> None:
        if stream_manager is None:
            return

        event = {
            'type': 'epg',
            'data': json.dumps(stream_manager.database),
        }
        for queue in list(state.sse_clients):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    @app.route('/hls-viewers', methods=['POST'])
    async def hls_viewers_route():
        """Receive HLS viewer count from the mux service."""
        data = await request.get_json()
        if not data or 'count' not in data:
            return 'Bad request', 400

        now = time.monotonic()
        _prune_recent_sse_disconnects(now)

        reported_ips = {str(ip) for ip in data.get('viewers', {}).keys()}
        displayed_ips = apply_hls_viewer_display_grace(state, reported_ips, now)
        sse_ips = set(state.visitor_tracker.visitors.keys())
        grace_ips = {
            ip for ip, ts in state.recent_sse_disconnects.items() if ts >= now - SSE_TO_HLS_GRACE_SECONDS
        }
        held_hls_ips = displayed_ips - reported_ips
        old_count = state.hls_viewer_count
        old_ips = state.hls_viewer_ips
        state.hls_viewer_ips = displayed_ips
        state.hls_viewer_count = len(displayed_ips)

        if state.hls_viewer_count != old_count or state.hls_viewer_ips != old_ips:
            message = ' '.join([
                f'HLS viewers updated: hls={state.hls_viewer_count}',
                f'raw_hls={len(reported_ips)}',
                f'held_hls={len(held_hls_ips)}',
                f'sse={state.visitor_tracker.count}',
                f'grace={len(grace_ips)}',
                f'hls_ips={sorted(displayed_ips)}',
                f'raw_hls_ips={sorted(reported_ips)}',
                f'sse_ips={sorted(sse_ips)}',
            ])
            loggers.sse.info(message)

        if state.hls_viewer_count != old_count:
            await _broadcast_visitors()

        if discord_bot_manager is not None:
            discord_bot_manager.update_hls_viewers(displayed_ips, state.hls_viewer_count)

        return 'OK', 200

    @app.route('/events', methods=['GET'])
    async def sse_stream():
        """Server-Sent Events endpoint for real-time playhead and visitor updates."""
        client_ip = get_client_address(request)
        loggers.sse.info(f'[{client_ip}] SSE client connected')

        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        state.sse_clients.add(queue)
        state.recent_sse_disconnects.pop(client_ip, None)
        state.visitor_tracker.connect(client_ip)
        await _broadcast_visitors()

        async def send_events():
            try:
                if stream_manager is not None:
                    initial_data = json.dumps(stream_manager.playhead)
                    yield f'event: playhead\ndata: {initial_data}\n\n'

                yield f"event: visitors\ndata: {json.dumps({'visitors': state.hls_viewer_count})}\n\n"

                if stream_manager is not None:
                    yield f'event: epg\ndata: {json.dumps(stream_manager.database)}\n\n'

                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield f"event: {event['type']}\ndata: {event['data']}\n\n"
                    except asyncio.TimeoutError:
                        yield ': keepalive\n\n'
            except asyncio.CancelledError:
                pass
            finally:
                state.sse_clients.discard(queue)
                state.visitor_tracker.disconnect(client_ip)
                now = time.monotonic()
                state.recent_sse_disconnects[client_ip] = now
                _prune_recent_sse_disconnects(now)
                loggers.sse.info(f'[{client_ip}] SSE client disconnected')
                await _broadcast_visitors()

        response = await app.make_response(send_events())
        response.headers['Content-Type'] = 'text/event-stream'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        response.headers['X-Accel-Buffering'] = 'no'
        response.timeout = None
        return response

    @app.before_serving
    async def start_playhead_monitor():
        """Start background tasks that monitor playhead and EPG changes."""

        async def monitor_playhead():
            last_playhead = None
            while True:
                await asyncio.sleep(1)
                if stream_manager is not None:
                    current = stream_manager.playhead
                    if current != last_playhead:
                        last_playhead = current.copy() if current else None
                        await _broadcast_playhead()

        async def monitor_epg():
            last_database = None
            while True:
                await asyncio.sleep(5)
                if stream_manager is not None:
                    current_db = copy.deepcopy(stream_manager.database)
                    if current_db != last_database:
                        last_database = current_db
                        await _broadcast_epg()

        app.add_background_task(monitor_playhead)
        app.add_background_task(monitor_epg)
