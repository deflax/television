import asyncio
import copy
import json
import time

from quart import request

from web.helpers import get_client_address
from web.state import WebRouteState


SSE_TO_HLS_GRACE_SECONDS = 45.0


def register_sse_routes(app, stream_manager, loggers, discord_bot_manager, state: WebRouteState) -> None:
    """Register SSE and realtime state endpoints."""

    def _prune_recent_sse_disconnects(now: float) -> None:
        cutoff = now - SSE_TO_HLS_GRACE_SECONDS
        for ip, ts in list(state.recent_sse_disconnects.items()):
            if ts < cutoff:
                del state.recent_sse_disconnects[ip]

    async def _broadcast_visitors() -> None:
        total = state.visitor_tracker.count + state.hls_viewer_count
        event = {
            'type': 'visitors',
            'data': json.dumps({'visitors': total}),
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

        reported_ips = set(data.get('viewers', {}).keys())
        sse_ips = set(state.visitor_tracker.visitors.keys())
        grace_ips = {
            ip for ip, ts in state.recent_sse_disconnects.items() if ts >= now - SSE_TO_HLS_GRACE_SECONDS
        }
        hls_only_ips = reported_ips - sse_ips - grace_ips

        old_count = state.hls_viewer_count
        state.hls_viewer_count = len(hls_only_ips)

        if state.hls_viewer_count != old_count:
            await _broadcast_visitors()

        if discord_bot_manager is not None:
            total = state.visitor_tracker.count + state.hls_viewer_count
            discord_bot_manager.update_hls_viewers(hls_only_ips, total)

        return 'OK', 200

    @app.route('/events', methods=['GET'])
    async def sse_stream():
        """Server-Sent Events endpoint for real-time playhead and visitor updates."""
        client_ip = get_client_address(request)
        loggers.sse.info(f'[{client_ip}] SSE client connected')

        queue: asyncio.Queue = asyncio.Queue()
        state.sse_clients.add(queue)
        state.recent_sse_disconnects.pop(client_ip, None)
        state.visitor_tracker.connect(client_ip)
        await _broadcast_visitors()

        async def send_events():
            try:
                if stream_manager is not None:
                    initial_data = json.dumps(stream_manager.playhead)
                    yield f'event: playhead\ndata: {initial_data}\n\n'

                total = state.visitor_tracker.count + state.hls_viewer_count
                yield f"event: visitors\ndata: {json.dumps({'visitors': total})}\n\n"

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
