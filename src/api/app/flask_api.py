import time
import sys
import os
import logging
from datetime import datetime
from typing import Optional
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from core_client import Client

from stream_manager import StreamManager
from frontend import register_routes


# Constants
DEFAULT_REC_PATH = "/recordings"
DEFAULT_ENABLE_DELAY = 24
DEFAULT_CORE_SYNC_PERIOD = 15
CORE_API_SYNC_JOB_ID = 'core_api_sync'


class Config:
    """Application configuration loaded from environment variables."""
    
    def __init__(self):
        self.log_level = os.environ.get('FLASKAPI_LOG_LEVEL', 'INFO').upper()
        self.vod_token = os.environ.get('FLASKAPI_VOD_TOKEN')
        self.core_hostname = os.environ.get('CORE_API_HOSTNAME', 'stream.example.com')
        self.core_username = os.environ.get('CORE_API_AUTH_USERNAME', 'admin')
        self.core_password = os.environ.get('CORE_API_AUTH_PASSWORD', 'pass')
        self.core_sync_period = int(os.environ.get('CORE_SYNC_PERIOD', DEFAULT_CORE_SYNC_PERIOD))
        self.rec_path = DEFAULT_REC_PATH
        self.enable_delay = DEFAULT_ENABLE_DELAY
        self.server_name = os.environ.get('SERVER_NAME')
        self.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(32).hex())
        self.discord_webhook_url = os.environ.get('DISCORD_WEBHOOK_URL')
        self.discord_timecode_channel_id = os.environ.get('DISCORDBOT_TIMECODE_CHANNEL_ID')


class LoggerManager:
    """Manages application loggers."""
    
    def __init__(self, log_level: str):
        self.log_level = log_level
        self._setup_loggers()
    
    def _setup_loggers(self) -> None:
        """Initialize and configure loggers."""
        self.api = logging.getLogger('waitress')
        self.job = logging.getLogger('apscheduler')
        self.content = logging.getLogger('content')
        
        self.api.setLevel(self.log_level)
        self.job.setLevel(self.log_level)
        self.content.setLevel(self.log_level)


# Global instances (will be initialized in create_app)
config = Config()
loggers = LoggerManager(config.log_level)
scheduler = BackgroundScheduler()
stream_manager: Optional[StreamManager] = None
client: Optional[Client] = None
app = Flask(__name__)


def _initialize_core_client(config: Config, logger: logging.Logger) -> Client:
    """Initialize and authenticate Core API client."""
    try:
        client = Client(
            base_url=f'https://{config.core_hostname}',
            username=config.core_username,
            password=config.core_password
        )
        logger.warning(f'Logging in to Datarhei Core API {config.core_username}@{config.core_hostname}')
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


def create_app() -> Flask:
    """Create and configure Flask application."""
    global stream_manager, client
    
    # Configure Flask app
    app.config['SERVER_NAME'] = config.server_name
    app.config['PREFERRED_URL_SCHEME'] = 'https'
    app.config['SECRET_KEY'] = config.secret_key
    app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24 hours in seconds
    
    # Initialize Core API client
    client = _initialize_core_client(config, loggers.api)
    
    # Initialize stream manager
    stream_manager = StreamManager(scheduler, client, config, loggers.job)
    
    # Setup scheduler
    _setup_scheduler(stream_manager, config)
    
    # Register frontend routes
    register_routes(app, stream_manager, config, loggers)
    
    return app
