from web.ingest_routes import register_ingest_routes
from web.playlist_routes import register_playlist_routes
from web.public_api_routes import register_public_api_routes
from web.sse_routes import register_sse_routes
from web.state import WebRouteState


def register_api_routes(app, stream_manager, config, loggers, discord_bot_manager, state: WebRouteState) -> None:
    """Register API-oriented and machine-facing routes."""
    register_public_api_routes(app, loggers, discord_bot_manager, state)
    register_playlist_routes(app, stream_manager, loggers)
    register_sse_routes(app, stream_manager, loggers, discord_bot_manager, state)
    register_ingest_routes(app, config)
