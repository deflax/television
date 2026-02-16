from quart import jsonify, request

from web.helpers import (
    get_client_address,
    get_client_hostname,
    get_request_base_url,
    send_timecode_to_discord,
)
from web.state import WebRouteState


def register_public_api_routes(app, loggers, discord_bot_manager, state: WebRouteState) -> None:
    """Register public API endpoints used by external systems and clients."""

    @app.route('/health', methods=['GET'])
    async def health_route():
        """Lightweight health check endpoint for HAProxy."""
        return 'OK', 200

    @app.route('/privacy-policy-url', methods=['GET'])
    async def privacy_policy_url_route():
        """Public privacy policy URL for app-store metadata."""
        client_ip = get_client_address(request)
        loggers.content.info(f'[{client_ip}] privacy policy url /privacy-policy-url')
        return jsonify({'privacy_policy_url': f'{get_request_base_url(request)}/privacy-policy'})

    @app.route('/request-timecode', methods=['POST'])
    async def request_timecode_route():
        """Request a timecode for the current hostname."""
        client_ip = get_client_address(request)
        client_hostname = get_client_hostname(request)

        identifier = client_hostname
        if client_hostname in ('unknown', 'localhost', '127.0.0.1') or '.' not in client_hostname:
            identifier = client_ip

        timecode = state.timecode_manager.generate_timecode(identifier)
        obfuscated_hostname = state.timecode_manager.obfuscate_hostname(client_hostname, client_ip)
        sent_to_discord = send_timecode_to_discord(discord_bot_manager, obfuscated_hostname, timecode)

        loggers.content.info(f'[{client_ip}] timecode requested for {obfuscated_hostname}')
        message = 'Timecode has been sent to Discord channel' if sent_to_discord else 'Timecode generated (Discord bot not available)'

        return jsonify({
            'success': True,
            'message': message,
            'obfuscated_hostname': obfuscated_hostname,
        })
