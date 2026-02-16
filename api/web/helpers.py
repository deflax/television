from datetime import datetime, timedelta, timezone
from functools import wraps

from quart import redirect, request, session, url_for


def get_client_address(req) -> str:
    """Get client IP address, handling proxy headers."""
    forwarded_for = req.headers.get('X-Forwarded-For')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return req.remote_addr or '0.0.0.0'


def get_client_hostname(req) -> str:
    """Get client hostname from request headers."""
    return 'unknown'


def get_request_base_url(req) -> str:
    """Build external base URL from current request and proxy headers."""
    host = req.headers.get('Host') or req.host
    scheme = req.headers.get('X-Forwarded-Proto', req.scheme)
    return f'{scheme}://{host}'


def send_timecode_to_discord(discord_bot_manager, obfuscated_hostname: str, timecode: str) -> bool:
    """Send timecode request to Discord via bot."""
    if discord_bot_manager is None:
        return False

    try:
        return discord_bot_manager.send_timecode_message(obfuscated_hostname, timecode)
    except Exception as exc:
        print(f'Failed to send timecode to Discord: {exc}')
        return False


def requires_auth(func):
    """Decorator to require timecode authentication."""

    @wraps(func)
    async def decorated_function(*args, **kwargs):
        if not session.get('authenticated', False):
            session['next'] = request.url
            return redirect(url_for('archive_route'))

        client_ip = get_client_address(request)
        current_hostname = get_client_hostname(request)

        identifier = current_hostname
        if current_hostname in ('unknown', 'localhost', '127.0.0.1') or '.' not in current_hostname:
            identifier = client_ip

        session_identifier = session.get('identifier', session.get('hostname'))
        if session_identifier != identifier:
            session.clear()
            return redirect(url_for('root_route'))

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

        return await func(*args, **kwargs)

    return decorated_function
