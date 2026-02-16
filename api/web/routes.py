from web.api_routes import register_api_routes
from web.frontend_routes import register_frontend_routes
from web.state import WebRouteState
from web.timecode_manager import TimecodeManager
from web.visitor_tracker import VisitorTracker


def register_routes(app, stream_manager, config, loggers, discord_bot_manager=None) -> None:
    """Register all web routes by concern area."""

    def on_visitor_connect(ip: str, count: int) -> None:
        if discord_bot_manager is not None:
            discord_bot_manager.log_visitor_change()

    def on_visitor_disconnect(ip: str, count: int) -> None:
        if discord_bot_manager is not None:
            discord_bot_manager.log_visitor_change()

    state = WebRouteState(
        timecode_manager=TimecodeManager(),
        visitor_tracker=VisitorTracker(
            on_connect=on_visitor_connect,
            on_disconnect=on_visitor_disconnect,
        ),
    )

    if discord_bot_manager is not None:
        discord_bot_manager.visitor_tracker = state.visitor_tracker

    register_frontend_routes(app, config, loggers, state)
    register_api_routes(app, stream_manager, config, loggers, discord_bot_manager, state)
