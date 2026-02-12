import os
import copy
import json
import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Set
from quart import Quart, render_template, jsonify, request, abort, session, redirect, url_for
from quart.helpers import send_file
from werkzeug.utils import secure_filename
from functools import wraps

from timecode_manager import TimecodeManager
from visitor_tracker import VisitorTracker


# Constants
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi')
THUMBNAIL_EXTENSION = '.png'
DEFAULT_REC_PATH = "/recordings"


# Route helpers
def get_client_address(req) -> str:
    """Get client IP address, handling proxy headers."""
    forwarded_for = req.headers.get('X-Forwarded-For')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return req.remote_addr or '0.0.0.0'


def get_client_hostname(req) -> str:
    """Get client hostname from request headers."""
    # # Try to get hostname from various headers
    # hostname = (
    #     req.headers.get('X-Forwarded-Host') or
    #     req.headers.get('Host') or
    #     req.environ.get('HTTP_HOST') or
    #     req.environ.get('SERVER_NAME') or
    #     'unknown'
    # )
    # # Remove port if present
    # if ':' in hostname:
    #     hostname = hostname.split(':')[0]
    # return hostname.lower()
    return 'unknown'


def send_timecode_to_discord(discord_bot_manager, obfuscated_hostname: str, timecode: str) -> bool:
    """Send timecode request to Discord via bot.

    Returns True if message was sent successfully, False otherwise.
    """
    if discord_bot_manager is None:
        return False

    try:
        return discord_bot_manager.send_timecode_message(obfuscated_hostname, timecode)
    except Exception as e:
        # Log error but don't fail the request
        print(f"Failed to send timecode to Discord: {e}")
        return False


def requires_auth(f):
    """Decorator to require timecode authentication."""
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        # Check if user is authenticated
        if not session.get('authenticated', False):
            # Store the URL user was trying to access
            session['next'] = request.url
            return redirect(url_for('archive_route'))
        
        # Validate identifier matches session
        client_ip = get_client_address(request)
        current_hostname = get_client_hostname(request)
        
        # Determine current identifier (same logic as root_route)
        identifier = current_hostname
        if current_hostname in ('unknown', 'localhost', '127.0.0.1') or not '.' in current_hostname:
            identifier = client_ip
        
        session_identifier = session.get('identifier', session.get('hostname'))
        
        if session_identifier != identifier:
            # Identifier mismatch - invalidate session
            session.clear()
            return redirect(url_for('root_route'))
        
        # Check session expiration (24 hours)
        session_created = session.get('created_at')
        if session_created:
            try:
                created_dt = datetime.fromisoformat(session_created)
                if datetime.now(timezone.utc) - created_dt > timedelta(hours=24):
                    session.clear()
                    return redirect(url_for('root_route'))
            except (ValueError, TypeError):
                session.clear()
                return redirect(url_for('root_route'))
        
        return await f(*args, **kwargs)
    return decorated_function


def get_video_files(rec_path: str) -> List[str]:
    """Get list of video files from recordings directory."""
    vod_path = os.path.join(rec_path, 'vod')
    if not os.path.exists(vod_path):
        return []
    return [
        file for file in os.listdir(vod_path)
        if file.endswith(VIDEO_EXTENSIONS)
    ]


def get_sorted_thumbnails(rec_path: str) -> List[str]:
    """Get sorted list of thumbnail files by modification time."""
    thumbnails_path = os.path.join(rec_path, 'thumb')
    if not os.path.exists(thumbnails_path):
        return []
    
    thumbnails = [
        file for file in os.listdir(thumbnails_path)
        if file.endswith(THUMBNAIL_EXTENSION)
    ]
    
    # Get full paths and sort by modification time
    thumbnail_paths = [os.path.join(thumbnails_path, file) for file in thumbnails]
    sorted_thumbnails_paths = sorted(
        thumbnail_paths,
        key=lambda x: os.path.getmtime(x),
        reverse=True
    )
    
    # Extract file names from sorted paths
    return [os.path.basename(file) for file in sorted_thumbnails_paths]


def register_routes(app: Quart, stream_manager, config, loggers, discord_bot_manager=None) -> None:
    """Register all Quart routes for the frontend."""

    # Initialize timecode manager
    timecode_manager = TimecodeManager()

    # Create visitor event callbacks for Discord logging
    def on_visitor_connect(ip: str, count: int) -> None:
        if discord_bot_manager is not None:
            discord_bot_manager.log_visitor_change()

    def on_visitor_disconnect(ip: str, count: int) -> None:
        if discord_bot_manager is not None:
            discord_bot_manager.log_visitor_change()

    # Initialize visitor tracker with SSE connection-based tracking
    visitor_tracker = VisitorTracker(
        on_connect=on_visitor_connect,
        on_disconnect=on_visitor_disconnect
    )

    # Share visitor tracker with Discord bot manager
    if discord_bot_manager is not None:
        discord_bot_manager.visitor_tracker = visitor_tracker

    # Set of active SSE client queues for broadcasting updates
    sse_clients: Set[asyncio.Queue] = set()

    # HLS viewer count reported by mux service (viewers not using SSE)
    hls_viewer_count: int = 0
    
    @app.route('/health', methods=['GET'])
    async def health_route():
        """Lightweight health check endpoint for HAProxy."""
        return 'OK', 200

    @app.route('/hls-viewers', methods=['POST'])
    async def hls_viewers_route():
        """Receive HLS viewer count from the mux service.

        The mux service periodically POSTs its count of unique IPs
        that are actively fetching HLS playlists. We subtract any IPs
        that are already counted via SSE to avoid double-counting.
        """
        nonlocal hls_viewer_count

        data = await request.get_json()
        if not data or 'count' not in data:
            return 'Bad request', 400

        reported_ips = set(data.get('viewers', {}).keys())
        # IPs already tracked via SSE connections
        sse_ips = set(visitor_tracker.visitors.keys())
        # Only count HLS viewers that are NOT also connected via SSE
        hls_only_ips = reported_ips - sse_ips

        old_count = hls_viewer_count
        hls_viewer_count = len(hls_only_ips)

        if hls_viewer_count != old_count:
            await _broadcast_visitors()

        # Update Discord bot: diffs IPs and logs connect/disconnect events
        if discord_bot_manager is not None:
            total = visitor_tracker.count + hls_viewer_count
            discord_bot_manager.update_hls_viewers(hls_only_ips, total)

        return 'OK', 200

    @app.route('/live.m3u8', methods=['GET'])
    async def live_m3u8_route():
        """Serve dynamically generated live.m3u8 playlist file."""
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] live.m3u8')
        
        # Get the host from the request
        host = request.headers.get('Host') or request.host
        scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
        
        # Extract domain name for tvg-id (remove port if present)
        domain = host.split(':')[0] if ':' in host else host
        
        # Generate the playlist content dynamically
        epg_url = f'{scheme}://{host}/epg.xml'
        channel_id = domain.lower()
        playlist_content = f'#EXTM3U url-tvg="{epg_url}" x-tvg-url="{epg_url}" url-tvg-refresh="1"\n'
        playlist_content += f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{domain}" tvg-logo="{scheme}://{host}/static/images/logo.png" group-title="Relax",{domain}\n'
        playlist_content += f'{scheme}://{host}/live/stream.m3u8\n'
        
        response = await app.make_response(playlist_content)
        response.headers['Content-Type'] = 'application/vnd.apple.mpegurl'
        response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.route('/epg.xml', methods=['GET'])
    async def epg_xml_route():
        """Serve dynamically generated XMLTV EPG from the stream database."""
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] epg.xml')

        host = request.headers.get('Host') or request.host
        scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
        domain = host.split(':')[0] if ':' in host else host
        channel_id = domain.lower()

        # Build XMLTV document
        tv = ET.Element('tv', attrib={
            'generator-info-name': 'television-epg',
            'generator-info-url': f'{scheme}://{host}',
        })

        # Single channel entry matching the tvg-id in live.m3u8
        ch = ET.SubElement(tv, 'channel', id=channel_id)
        ET.SubElement(ch, 'display-name').text = domain
        ET.SubElement(ch, 'icon', src=f'{scheme}://{host}/static/images/logo.png')

        # Build programme entries from the stream database
        if stream_manager is not None and stream_manager.database:
            now = datetime.now(timezone.utc)
            live_programmes = []
            scheduled_programmes = []

            for stream_id, entry in stream_manager.database.items():
                start_at = entry.get('start_at', '')
                name = entry.get('name', 'Unknown')
                details = entry.get('details', '')

                if start_at == 'never':
                    continue

                if start_at == 'now':
                    # Currently live -- keep separate, uses its own 3h window
                    live_programmes.append({
                        'start': now,
                        'stop': now + timedelta(hours=3),
                        'title': name,
                        'desc': details,
                        'live': True,
                    })
                else:
                    # Parse military time (e.g. '2100' or '14')
                    time_str = str(start_at).strip()
                    if len(time_str) <= 2:
                        hour, minute = int(time_str), 0
                    else:
                        hour, minute = int(time_str[:-2]), int(time_str[-2:])
                    prog_start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    # If the time already passed today, show it for tomorrow
                    if prog_start < now - timedelta(hours=1):
                        prog_start += timedelta(days=1)
                    scheduled_programmes.append({
                        'start': prog_start,
                        'title': name,
                        'desc': details,
                        'live': False,
                    })

            # Sort scheduled programmes by start time
            scheduled_programmes.sort(key=lambda p: p['start'])

            # Each scheduled programme ends when the next one begins;
            # the last one wraps around to the first one's start (+24h if needed)
            for i, prog in enumerate(scheduled_programmes):
                if i + 1 < len(scheduled_programmes):
                    prog['stop'] = scheduled_programmes[i + 1]['start']
                elif len(scheduled_programmes) > 1:
                    # Last entry wraps to the first entry's start next day
                    prog['stop'] = scheduled_programmes[0]['start'] + timedelta(days=1)
                else:
                    # Only one scheduled programme -- give it a 24h window
                    prog['stop'] = prog['start'] + timedelta(hours=24)

            # Combine: live entries first, then scheduled
            programmes = live_programmes + scheduled_programmes

            for prog in programmes:
                fmt = '%Y%m%d%H%M%S +0000'
                prog_el = ET.SubElement(tv, 'programme', attrib={
                    'start': prog['start'].strftime(fmt),
                    'stop': prog['stop'].strftime(fmt),
                    'channel': channel_id,
                })
                ET.SubElement(prog_el, 'title', lang='en').text = prog['title']
                if prog.get('desc'):
                    ET.SubElement(prog_el, 'desc', lang='en').text = prog['desc']

        xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
        xml_doctype = '<!DOCTYPE tv SYSTEM "xmltv.dtd">\n'
        ET.indent(tv, space='  ')
        xml_body = ET.tostring(tv, encoding='unicode', xml_declaration=False)
        xml_content = xml_declaration + xml_doctype + xml_body + '\n'

        response = await app.make_response(xml_content)
        response.headers['Content-Type'] = 'application/xml; charset=utf-8'
        response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.route('/', methods=['GET'])
    async def root_route():
        """Frontend index page - public live stream."""
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] index /')
        
        # Choose template based on frontend mode
        template = 'index_mux.html' if config.frontend_mode == 'mux' else 'index_legacy.html'
        
        return await render_template(
            template,
            now=datetime.now(timezone.utc)
        )
    
    @app.route('/archive', methods=['GET', 'POST'])
    async def archive_route():
        """Archive page with timecode authentication."""
        client_ip = get_client_address(request)
        client_hostname = get_client_hostname(request)

        # Use IP address for timecode generation if hostname is not a proper domain
        identifier = client_hostname
        if client_hostname in ('unknown', 'localhost', '127.0.0.1') or not '.' in client_hostname:
            # Use IP address as identifier for timecode generation
            identifier = client_ip

        # Handle POST (timecode submission)
        if request.method == 'POST':
            form = await request.form
            submitted_timecode = form.get('timecode', '').strip()

            if timecode_manager.validate_timecode(identifier, submitted_timecode):
                # Valid timecode - create session
                session.permanent = True
                session['authenticated'] = True
                session['hostname'] = client_hostname
                session['identifier'] = identifier  # Store identifier for validation
                session['created_at'] = datetime.now(timezone.utc).isoformat()
                loggers.content.info(f'[{client_ip}] authenticated with identifier {identifier}')

                # Redirect to original page if available
                next_url = session.pop('next', None)
                if next_url:
                    return redirect(next_url)
                return redirect(url_for('archive_route'))
            else:
                # Invalid timecode
                loggers.content.warning(f'[{client_ip}] invalid timecode attempt from {identifier}')
                return await render_template(
                    'archive.html',
                    now=datetime.now(timezone.utc),
                    video_files=[],
                    thumbnails=[],
                    authenticated=False,
                    error='Invalid timecode. Please request a new one.'
                )

        # Handle GET
        # Check if already authenticated
        if session.get('authenticated', False):
            # Validate identifier matches (hostname or IP)
            session_identifier = session.get('identifier', session.get('hostname'))
            if session_identifier == identifier:
                # Check session expiration
                session_created = session.get('created_at')
                if session_created:
                    try:
                        created_dt = datetime.fromisoformat(session_created)
                        if datetime.now(timezone.utc) - created_dt <= timedelta(hours=24):
                            # Valid session - show content
                            video_files = get_video_files(config.rec_path)
                            sorted_thumbnails = get_sorted_thumbnails(config.rec_path)
                            loggers.content.info(f'[{client_ip}] archive (authenticated)')
                            return await render_template(
                                'archive.html',
                                now=datetime.now(timezone.utc),
                                video_files=video_files,
                                thumbnails=sorted_thumbnails,
                                authenticated=True
                            )
                    except (ValueError, TypeError):
                        pass

            # Session invalid - clear it
            session.clear()

        # Not authenticated - show timecode form
        loggers.content.info(f'[{client_ip}] archive (not authenticated)')
        return await render_template(
            'archive.html',
            now=datetime.now(timezone.utc),
            video_files=[],
            thumbnails=[],
            authenticated=False
        )

    @app.route('/request-timecode', methods=['POST'])
    async def request_timecode_route():
        """Request a timecode for the current hostname."""
        client_ip = get_client_address(request)
        client_hostname = get_client_hostname(request)
        
        # Use IP address for timecode generation if hostname is not a proper domain
        # This ensures IP-based visitors get consistent timecodes
        identifier = client_hostname
        if client_hostname in ('unknown', 'localhost', '127.0.0.1') or not '.' in client_hostname:
            # Use IP address as identifier for timecode generation
            identifier = client_ip
        
        # Generate timecode using the identifier
        timecode = timecode_manager.generate_timecode(identifier)
        # Obfuscate for display, passing IP for reverse DNS lookup
        obfuscated_hostname = timecode_manager.obfuscate_hostname(client_hostname, client_ip)

        # Send to Discord via bot
        sent_to_discord = send_timecode_to_discord(discord_bot_manager, obfuscated_hostname, timecode)

        loggers.content.info(f'[{client_ip}] timecode requested for {obfuscated_hostname}')

        if sent_to_discord:
            message = 'Timecode has been sent to Discord channel'
        else:
            message = 'Timecode generated (Discord bot not available)'

        return jsonify({
            'success': True,
            'message': message,
            'obfuscated_hostname': obfuscated_hostname
        })
    
    @app.route('/events', methods=['GET'])
    async def sse_stream():
        """Server-Sent Events endpoint for real-time playhead and visitor updates.

        Replaces the polling-based /playhead and /visitors endpoints.
        Each connected client is an active visitor - no heartbeat polling needed.
        """
        client_ip = get_client_address(request)
        loggers.sse.info(f'[{client_ip}] SSE client connected')

        queue: asyncio.Queue = asyncio.Queue()
        sse_clients.add(queue)
        visitor_tracker.connect(client_ip)

        # Notify all clients about the updated visitor count
        await _broadcast_visitors()

        async def send_events():
            try:
                # Send initial playhead state immediately
                if stream_manager is not None:
                    initial_data = json.dumps(stream_manager.playhead)
                    yield f"event: playhead\ndata: {initial_data}\n\n"

                # Send initial visitor count (SSE + HLS-only viewers)
                total = visitor_tracker.count + hls_viewer_count
                yield f"event: visitors\ndata: {json.dumps({'visitors': total})}\n\n"

                # Send initial EPG state
                if stream_manager is not None:
                    yield f"event: epg\ndata: {json.dumps(stream_manager.database)}\n\n"

                while True:
                    try:
                        # Wait for new events with a 15s timeout for keepalive
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield f"event: {event['type']}\ndata: {event['data']}\n\n"
                    except asyncio.TimeoutError:
                        # Send keepalive comment to prevent connection timeout
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                sse_clients.discard(queue)
                visitor_tracker.disconnect(client_ip)
                loggers.sse.info(f'[{client_ip}] SSE client disconnected')
                # Notify remaining clients about updated visitor count
                await _broadcast_visitors()

        response = await app.make_response(send_events())
        response.headers['Content-Type'] = 'text/event-stream'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        response.headers['X-Accel-Buffering'] = 'no'
        response.timeout = None  # Disable response timeout for SSE
        return response

    async def _broadcast_visitors():
        """Broadcast combined visitor count (SSE + HLS-only) to all SSE clients."""
        total = visitor_tracker.count + hls_viewer_count
        event = {
            'type': 'visitors',
            'data': json.dumps({'visitors': total})
        }
        for q in list(sse_clients):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def _broadcast_playhead():
        """Broadcast current playhead state to all SSE clients."""
        if stream_manager is None:
            return
        event = {
            'type': 'playhead',
            'data': json.dumps(stream_manager.playhead)
        }
        for q in list(sse_clients):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def _broadcast_epg():
        """Broadcast current EPG (stream database) to all SSE clients."""
        if stream_manager is None:
            return
        event = {
            'type': 'epg',
            'data': json.dumps(stream_manager.database)
        }
        for q in list(sse_clients):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # Background task: monitor playhead and EPG changes and broadcast to SSE clients
    @app.before_serving
    async def start_playhead_monitor():
        """Start background tasks that monitor playhead and EPG changes."""
        async def monitor_playhead():
            last_playhead = None
            while True:
                await asyncio.sleep(1)  # Check every second
                if stream_manager is not None:
                    current = stream_manager.playhead
                    if current != last_playhead:
                        last_playhead = current.copy() if current else None
                        await _broadcast_playhead()

        async def monitor_epg():
            last_database = None
            while True:
                await asyncio.sleep(5)  # Check every 5 seconds
                if stream_manager is not None:
                    current_db = copy.deepcopy(stream_manager.database)
                    if current_db != last_database:
                        last_database = current_db
                        await _broadcast_epg()

        app.add_background_task(monitor_playhead)
        app.add_background_task(monitor_epg)

    @app.route("/thumb/<thumb_file>", methods=['GET'])
    @requires_auth
    async def thumb_route(thumb_file: str):
        """Serve thumbnail images."""
        thumb_path = os.path.join(config.rec_path, 'thumb', thumb_file)
        if not os.path.exists(thumb_path):
            abort(404)
        
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] thumb {thumb_path}')
        return await send_file(thumb_path, mimetype='image/png')
    
    @app.route('/video', methods=['POST'])
    async def video_upload():
        """Handle video file uploads."""
        token = request.headers.get("Authorization")
        if token != f"Bearer {config.vod_token}":
            return "Unauthorized", 401
        
        upload_path = os.path.join(config.rec_path, 'vod')
        if not os.path.exists(upload_path):
            abort(404)
        
        files = await request.files
        if 'file' not in files:
            return 'No file provided', 400
        
        file = files['file']
        if file.filename == '':
            return 'No file selected', 400
        
        filename = secure_filename(file.filename)
        await file.save(os.path.join(upload_path, filename))
        return "File uploaded successfully", 200
    
    @app.route("/video/<video_file>", methods=['GET'])
    @requires_auth
    async def video_route(video_file: str):
        """Stream video files."""
        video_path = os.path.join(config.rec_path, 'vod', video_file)
        if not os.path.exists(video_path):
            abort(404)
        
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] stream {video_path}')
        return await send_file(video_path, mimetype='video/mp4')
    
    @app.route("/video/download/<video_file>", methods=['GET'])
    @requires_auth
    async def video_download_route(video_file: str):
        """Download video files."""
        video_path = os.path.join(config.rec_path, 'vod', video_file)
        if not os.path.exists(video_path):
            abort(404)
        
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] download {video_path}')
        return await send_file(
            video_path,
            as_attachment=True,
            download_name=video_file
        )
    
    @app.route("/video/watch/<video_file_no_extension>", methods=['GET'])
    @requires_auth
    async def video_watch_route(video_file_no_extension: str):
        """Video player page."""
        video_file = f'{video_file_no_extension}.mp4'
        thumb_file = f'{video_file_no_extension}.png'
        video_path = os.path.join(config.rec_path, 'vod', video_file)
        thumb_path = os.path.join(config.rec_path, 'thumb', thumb_file)

        if not os.path.exists(video_path):
            abort(404)

        if not os.path.exists(thumb_path):
            thumb_file = ""

        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] player {video_path}')
        return await render_template(
            'watch.html',
            now=datetime.now(timezone.utc),
            video_file=video_file,
            thumb_file=thumb_file
        )

    @app.route('/weather', methods=['GET'])
    async def weather_route():
        """Weather visualization page - public."""
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] weather /weather')
        return await render_template(
            'weather.html',
            now=datetime.now(timezone.utc)
        )

    @app.route('/logout', methods=['GET'])
    async def logout_route():
        """Clear session and logout user."""
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] logout')
        session.clear()
        return redirect(url_for('root_route'))
