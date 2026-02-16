import os
from datetime import datetime, timedelta, timezone
from typing import List

from quart import abort, redirect, render_template, request, session, url_for
from quart.helpers import send_file

from web.helpers import get_client_address, get_client_hostname, requires_auth
from web.state import WebRouteState


VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi')
THUMBNAIL_EXTENSION = '.png'


def get_video_files(rec_path: str) -> List[str]:
    """Get list of video files from recordings directory."""
    vod_path = os.path.join(rec_path, 'vod')
    if not os.path.exists(vod_path):
        return []

    return [file for file in os.listdir(vod_path) if file.endswith(VIDEO_EXTENSIONS)]


def get_sorted_thumbnails(rec_path: str) -> List[str]:
    """Get sorted list of thumbnail files by modification time."""
    thumbnails_path = os.path.join(rec_path, 'thumb')
    if not os.path.exists(thumbnails_path):
        return []

    thumbnails = [file for file in os.listdir(thumbnails_path) if file.endswith(THUMBNAIL_EXTENSION)]
    thumbnail_paths = [os.path.join(thumbnails_path, file) for file in thumbnails]
    sorted_thumbnails_paths = sorted(thumbnail_paths, key=os.path.getmtime, reverse=True)
    return [os.path.basename(file) for file in sorted_thumbnails_paths]


def register_frontend_routes(app, config, loggers, state: WebRouteState) -> None:
    """Register human-facing HTML routes and protected media routes."""

    @app.context_processor
    def inject_site_vars():
        return {
            'server_name': config.server_name or 'localhost',
            'core_hostname': config.core_hostname or 'localhost',
        }

    @app.route('/privacy-policy', methods=['GET'])
    @app.route('/privacy', methods=['GET'])
    async def privacy_policy_route():
        """Privacy policy page - public."""
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] privacy policy /privacy-policy')
        return await render_template('privacy_policy.html', now=datetime.now(timezone.utc))

    @app.route('/', methods=['GET'])
    async def root_route():
        """Frontend index page - public live stream."""
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] index /')

        template = 'index_mux.html' if config.frontend_mode == 'mux' else 'index_legacy.html'
        return await render_template(template, now=datetime.now(timezone.utc))

    @app.route('/archive', methods=['GET', 'POST'])
    async def archive_route():
        """Archive page with timecode authentication."""
        client_ip = get_client_address(request)
        client_hostname = get_client_hostname(request)

        identifier = client_hostname
        if client_hostname in ('unknown', 'localhost', '127.0.0.1') or '.' not in client_hostname:
            identifier = client_ip

        if request.method == 'POST':
            form = await request.form
            submitted_timecode = form.get('timecode', '').strip()

            if state.timecode_manager.validate_timecode(identifier, submitted_timecode):
                session.permanent = True
                session['authenticated'] = True
                session['hostname'] = client_hostname
                session['identifier'] = identifier
                session['created_at'] = datetime.now(timezone.utc).isoformat()
                loggers.content.info(f'[{client_ip}] authenticated with identifier {identifier}')

                next_url = session.pop('next', None)
                if next_url:
                    return redirect(next_url)
                return redirect(url_for('archive_route'))

            loggers.content.warning(f'[{client_ip}] invalid timecode attempt from {identifier}')
            return await render_template(
                'archive.html',
                now=datetime.now(timezone.utc),
                video_files=[],
                thumbnails=[],
                authenticated=False,
                error='Invalid timecode. Please request a new one.',
            )

        if session.get('authenticated', False):
            session_identifier = session.get('identifier', session.get('hostname'))
            if session_identifier == identifier:
                session_created = session.get('created_at')
                if session_created:
                    try:
                        created_dt = datetime.fromisoformat(session_created)
                        if datetime.now(timezone.utc) - created_dt <= timedelta(hours=24):
                            video_files = get_video_files(config.rec_path)
                            sorted_thumbnails = get_sorted_thumbnails(config.rec_path)
                            loggers.content.info(f'[{client_ip}] archive (authenticated)')
                            return await render_template(
                                'archive.html',
                                now=datetime.now(timezone.utc),
                                video_files=video_files,
                                thumbnails=sorted_thumbnails,
                                authenticated=True,
                            )
                    except (ValueError, TypeError):
                        pass

            session.clear()

        loggers.content.info(f'[{client_ip}] archive (not authenticated)')
        return await render_template(
            'archive.html',
            now=datetime.now(timezone.utc),
            video_files=[],
            thumbnails=[],
            authenticated=False,
        )

    @app.route('/thumb/<thumb_file>', methods=['GET'])
    @requires_auth
    async def thumb_route(thumb_file: str):
        """Serve thumbnail images."""
        thumb_path = os.path.join(config.rec_path, 'thumb', thumb_file)
        if not os.path.exists(thumb_path):
            abort(404)

        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] thumb {thumb_path}')
        return await send_file(thumb_path, mimetype='image/png')

    @app.route('/video/<video_file>', methods=['GET'])
    @requires_auth
    async def video_route(video_file: str):
        """Stream video files."""
        video_path = os.path.join(config.rec_path, 'vod', video_file)
        if not os.path.exists(video_path):
            abort(404)

        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] stream {video_path}')
        return await send_file(video_path, mimetype='video/mp4')

    @app.route('/video/download/<video_file>', methods=['GET'])
    @requires_auth
    async def video_download_route(video_file: str):
        """Download video files."""
        video_path = os.path.join(config.rec_path, 'vod', video_file)
        if not os.path.exists(video_path):
            abort(404)

        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] download {video_path}')
        return await send_file(video_path, as_attachment=True, download_name=video_file)

    @app.route('/video/watch/<video_file_no_extension>', methods=['GET'])
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
            thumb_file = ''

        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] player {video_path}')
        return await render_template(
            'watch.html',
            now=datetime.now(timezone.utc),
            video_file=video_file,
            thumb_file=thumb_file,
        )

    @app.route('/weather', methods=['GET'])
    async def weather_route():
        """Weather visualization page - public."""
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] weather /weather')
        return await render_template('weather.html', now=datetime.now(timezone.utc))

    @app.route('/logout', methods=['GET'])
    async def logout_route():
        """Clear session and logout user."""
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] logout')
        session.clear()
        return redirect(url_for('root_route'))
