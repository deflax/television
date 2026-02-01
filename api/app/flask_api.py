import time
import sys
import os
import logging
import asyncio
import threading
from datetime import datetime
from typing import Optional
from quart import Quart
from apscheduler.schedulers.background import BackgroundScheduler
from core_api import CoreAPIClient

from stream_manager import StreamManager
from frontend import register_routes
from discord_bot_manager import DiscordBotManager


# Constants
DEFAULT_REC_PATH = "/recordings"
DEFAULT_ENABLE_DELAY = 24
DEFAULT_CORE_SYNC_PERIOD = 15
CORE_API_SYNC_JOB_ID = 'core_api_sync'


class Config:
    """Application configuration loaded from environment variables."""

    def __init__(self):
        # Log levels for each component
        self.log_level_api = os.environ.get('API_LOG_LEVEL_API', 'INFO').upper()
        self.log_level_job = os.environ.get('API_LOG_LEVEL_JOB', 'WARN').upper()
        self.log_level_stream = os.environ.get('API_LOG_LEVEL_STREAM', 'INFO').upper()
        self.log_level_content = os.environ.get('API_LOG_LEVEL_CONTENT', 'INFO').upper()
        self.log_level_discord = os.environ.get('API_LOG_LEVEL_DISCORD', 'INFO').upper()
        self.log_level_sse = os.environ.get('API_LOG_LEVEL_SSE', 'WARN').upper()

        self.vod_token = os.environ.get('API_VOD_TOKEN')
        self.core_hostname = os.environ.get('CORE_API_HOSTNAME', 'stream.example.com')
        self.core_username = os.environ.get('CORE_API_AUTH_USERNAME', 'admin')
        self.core_password = os.environ.get('CORE_API_AUTH_PASSWORD', 'pass')
        self.core_sync_period = int(os.environ.get('CORE_SYNC_PERIOD', DEFAULT_CORE_SYNC_PERIOD))
        self.rec_path = DEFAULT_REC_PATH
        self.enable_delay = DEFAULT_ENABLE_DELAY
        self.server_name = os.environ.get('SERVER_NAME')
        self.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(32).hex())
        self.discord_bot_enabled = os.environ.get('DISCORDBOT_ENABLED', 'false').lower() == 'true'


class LoggerManager:
    """Manages application loggers."""

    def __init__(self, config: Config):
        self.config = config
        self._setup_loggers()

    def _setup_loggers(self) -> None:
        """Initialize and configure loggers."""
        # Create a shared handler and formatter
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s'))
        handler.setLevel(logging.DEBUG)

        # Set root logger to WARNING to suppress noisy third-party logs (httpcore, etc.)
        logging.basicConfig(level=logging.WARNING)

        self.api = logging.getLogger('hypercorn')
        self.job = logging.getLogger('apscheduler')
        self.stream = logging.getLogger('stream')
        self.content = logging.getLogger('content')
        self.discord = logging.getLogger('discord')
        self.sse = logging.getLogger('sse')

        # Add handler directly to each logger and disable propagation
        # so they're not filtered by root logger level
        for logger, level in [
            (self.api, self.config.log_level_api),
            (self.job, self.config.log_level_job),
            (self.stream, self.config.log_level_stream),
            (self.content, self.config.log_level_content),
            (self.discord, self.config.log_level_discord),
            (self.sse, self.config.log_level_sse),
        ]:
            logger.addHandler(handler)
            logger.setLevel(level)
            logger.propagate = False


# Global instances (will be initialized in create_app)
config = Config()
loggers = LoggerManager(config)
scheduler = BackgroundScheduler()
stream_manager: Optional[StreamManager] = None
client: Optional[CoreAPIClient] = None
discord_bot_manager: Optional[DiscordBotManager] = None
app = Quart(__name__)


def _initialize_core_client(config: Config, logger: logging.Logger) -> CoreAPIClient:
    """Initialize and authenticate Core API client."""
    try:
        client = CoreAPIClient(
            base_url=f'https://{config.core_hostname}',
            username=config.core_username,
            password=config.core_password,
            logger=logger,
        )
        logger.info(f'Logging in to Datarhei Core API {config.core_username}@{config.core_hostname}')
        client.login()
        return client
    except Exception as e:
        logger.error('Client login error')
        logger.error(e)
        time.sleep(10)
        logger.error('Restarting...')
        sys.exit(1)


def _setup_scheduler(stream_manager: StreamManager, config: Config) -> None:
    """Setup and start the scheduler with sync job."""
    scheduler.add_job(
        func=stream_manager.core_api_sync,
        trigger='interval',
        seconds=config.core_sync_period,
        id=CORE_API_SYNC_JOB_ID
    )
    scheduler.get_job(CORE_API_SYNC_JOB_ID).modify(next_run_time=datetime.now())
    scheduler.start()


def _run_discord_bot(bot_manager: DiscordBotManager) -> None:
    """Run Discord bot in a separate thread with its own event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(bot_manager.start())
    except Exception as e:
        loggers.discord.error(f'Discord bot error: {e}')
    finally:
        loop.close()


def _initialize_discord_bot(config: Config, logger: logging.Logger, stream_manager: StreamManager) -> Optional[DiscordBotManager]:
    """Initialize and start Discord bot in a separate thread."""
    if not config.discord_bot_enabled:
        logger.info('Discord bot is disabled')
        return None

    try:
        bot_manager = DiscordBotManager(config, logger, stream_manager)
        bot_thread = threading.Thread(target=_run_discord_bot, args=(bot_manager,), daemon=True)
        bot_thread.start()
        logger.info('Discord bot started in background thread')
        return bot_manager
    except Exception as e:
        logger.error(f'Failed to initialize Discord bot: {e}')
        return None


def create_app() -> Quart:
    """Create and configure Quart application."""
    global stream_manager, client, discord_bot_manager

    # Configure Quart app
    app.config['SERVER_NAME'] = config.server_name
    app.config['PREFERRED_URL_SCHEME'] = 'https'
    app.config['SECRET_KEY'] = config.secret_key
    app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24 hours in seconds

    # Initialize Core API client
    client = _initialize_core_client(config, loggers.api)

    # Initialize stream manager
    stream_manager = StreamManager(scheduler, client, config, loggers.stream)

    # Setup scheduler
    _setup_scheduler(stream_manager, config)

    # Initialize Discord bot (if enabled) - must be after stream_manager
    discord_bot_manager = _initialize_discord_bot(config, loggers.discord, stream_manager)

    # Register frontend routes
    register_routes(app, stream_manager, config, loggers, discord_bot_manager)

    return app
