import os
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from flask import Flask, render_template, jsonify, request, abort, session, redirect, url_for
from flask.helpers import send_file
from werkzeug.utils import secure_filename
from functools import wraps

from timecode_manager import TimecodeManager


# Constants
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi')
THUMBNAIL_EXTENSION = '.png'
DEFAULT_REC_PATH = "/recordings"


# Flask route helpers
def get_client_address(req) -> str:
    """Get client IP address, handling proxy headers."""
    if req.environ.get('HTTP_X_FORWARDED_FOR') is None:
        return req.environ['REMOTE_ADDR']
    return req.environ['HTTP_X_FORWARDED_FOR']


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
    def decorated_function(*args, **kwargs):
        # Check if user is authenticated
        if not session.get('authenticated', False):
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
        
        return f(*args, **kwargs)
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


def register_routes(app: Flask, stream_manager, config, loggers, discord_bot_manager=None) -> None:
    """Register all Flask routes for the frontend."""

    # Initialize timecode manager
    timecode_manager = TimecodeManager()
    
    @app.route('/', methods=['GET'])
    def root_route():
        """Frontend index page - public live stream."""
        client_ip = get_client_address(request)
        loggers.content.warning(f'[{client_ip}] index /')
        return render_template(
            'index.html',
            now=datetime.now(timezone.utc)
        )
    
    @app.route('/archive', methods=['GET', 'POST'])
    def archive_route():
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
            submitted_timecode = request.form.get('timecode', '').strip()

            if timecode_manager.validate_timecode(identifier, submitted_timecode):
                # Valid timecode - create session
                session.permanent = True
                session['authenticated'] = True
                session['hostname'] = client_hostname
                session['identifier'] = identifier  # Store identifier for validation
                session['created_at'] = datetime.now(timezone.utc).isoformat()
                loggers.content.warning(f'[{client_ip}] authenticated with identifier {identifier}')
                return redirect(url_for('archive_route'))
            else:
                # Invalid timecode
                loggers.content.warning(f'[{client_ip}] invalid timecode attempt from {identifier}')
                return render_template(
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
                            loggers.content.warning(f'[{client_ip}] archive (authenticated)')
                            return render_template(
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
        loggers.content.warning(f'[{client_ip}] archive (not authenticated)')
        return render_template(
            'archive.html',
            now=datetime.now(timezone.utc),
            video_files=[],
            thumbnails=[],
            authenticated=False
        )

    @app.route('/request-timecode', methods=['POST'])
    def request_timecode_route():
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

        loggers.content.warning(f'[{client_ip}] timecode requested for {obfuscated_hostname}')

        if sent_to_discord:
            message = 'Timecode has been sent to Discord channel'
        else:
            message = 'Timecode generated (Discord bot not available)'

        return jsonify({
            'success': True,
            'message': message,
            'obfuscated_hostname': obfuscated_hostname
        })
    
    @app.route('/playhead', methods=['GET'])
    def playhead_route():
        """Get current playhead information."""
        if stream_manager is None:
            return jsonify({}), 503
        return jsonify(stream_manager.playhead)

    @app.route("/thumb/<thumb_file>", methods=['GET'])
    @requires_auth
    def thumb_route(thumb_file: str):
        """Serve thumbnail images."""
        thumb_path = os.path.join(config.rec_path, 'thumb', thumb_file)
        if not os.path.exists(thumb_path):
            abort(404)
        
        client_ip = get_client_address(request)
        loggers.content.warning(f'[{client_ip}] thumb {thumb_path}')
        return send_file(thumb_path, mimetype='image/png')
    
    @app.route('/video', methods=['POST'])
    def video_upload():
        """Handle video file uploads."""
        token = request.headers.get("Authorization")
        if token != f"Bearer {config.vod_token}":
            return "Unauthorized", 401
        
        upload_path = os.path.join(config.rec_path, 'vod')
        if not os.path.exists(upload_path):
            abort(404)
        
        if 'file' not in request.files:
            return 'No file provided', 400
        
        file = request.files['file']
        if file.filename == '':
            return 'No file selected', 400
        
        filename = secure_filename(file.filename)
        file.save(os.path.join(upload_path, filename))
        return "File uploaded successfully", 200
    
    @app.route("/video/<video_file>", methods=['GET'])
    @requires_auth
    def video_route(video_file: str):
        """Stream video files."""
        video_path = os.path.join(config.rec_path, 'vod', video_file)
        if not os.path.exists(video_path):
            abort(404)
        
        client_ip = get_client_address(request)
        loggers.content.warning(f'[{client_ip}] stream {video_path}')
        return send_file(video_path, mimetype='video/mp4')
    
    @app.route("/video/download/<video_file>", methods=['GET'])
    @requires_auth
    def video_download_route(video_file: str):
        """Download video files."""
        video_path = os.path.join(config.rec_path, 'vod', video_file)
        if not os.path.exists(video_path):
            abort(404)
        
        client_ip = get_client_address(request)
        loggers.content.warning(f'[{client_ip}] download {video_path}')
        return send_file(
            video_path,
            as_attachment=True,
            download_name=video_file
        )
    
    @app.route("/video/watch/<video_file_no_extension>", methods=['GET'])
    @requires_auth
    def video_watch_route(video_file_no_extension: str):
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
        loggers.content.warning(f'[{client_ip}] player {video_path}')
        return render_template(
            'watch.html',
            now=datetime.now(timezone.utc),
            video_file=video_file,
            thumb_file=thumb_file
        )

    @app.route('/logout', methods=['GET'])
    def logout_route():
        """Clear session and logout user."""
        client_ip = get_client_address(request)
        loggers.content.warning(f'[{client_ip}] logout')
        session.clear()
        return redirect(url_for('root_route'))
