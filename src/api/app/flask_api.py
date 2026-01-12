import time
import sys
import os
import ast
import logging
import requests
from datetime import datetime
from typing import Dict, Optional, Any, List
from flask import Flask, render_template, jsonify, request, abort
from flask.helpers import send_file
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler
from core_client import Client


# Constants
DEFAULT_REC_PATH = "/recordings"
DEFAULT_ENABLE_DELAY = 24
DEFAULT_CORE_SYNC_PERIOD = 15
STREAM_ACCESS_RETRY_ATTEMPTS = 15
STREAM_ACCESS_RETRY_INTERVAL = 6
FALLBACK_JOB_ID = 'fallback'
CORE_API_SYNC_JOB_ID = 'core_api_sync'

# Supported video file extensions
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi')
THUMBNAIL_EXTENSION = '.png'


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


class StreamManager:
    """Manages stream state, database, and scheduling logic."""
    
    def __init__(self, scheduler: BackgroundScheduler, client: Client, config: Config, logger: logging.Logger):
        self.scheduler = scheduler
        self.client = client
        self.config = config
        self.logger = logger
        self.database: Dict[str, Dict[str, Any]] = {}
        self.playhead: Dict[str, Any] = {}
        self.priority = 0
    
    def get_core_process_details(self, process_id: str) -> Optional[Any]:
        """Get process details from Core API."""
        try:
            return self.client.v3_process_get(id=process_id)
        except Exception as e:
            self.logger.error(f'Error getting process details for {process_id}: {e}')
            return None
    
    def process_running_channel(
        self, 
        stream_id: str, 
        stream_name: str, 
        stream_description: str, 
        stream_hls_url: str
    ) -> None:
        """Process and schedule a running channel."""
        if stream_id in self.database:
            # Skip already learned channels
            return
        
        try:
            # Get the channel settings from the stream description
            api_settings = ast.literal_eval(stream_description)
            stream_start = api_settings.get('start_at')
            stream_prio = api_settings.get('prio', 0)
        except Exception as e:
            # Skip channels without readable meta
            self.logger.debug(f'Failed to parse stream description for {stream_id}: {e}')
            return
        
        self.logger.warning(f'{stream_id} ({stream_name}) found. {api_settings}')
        
        # Check whether we have stream details
        stream_details = api_settings.get('details', "")
        if stream_details:
            self.logger.warning(f'Details found: {stream_details}')
        
        if stream_start == "now":
            if not self._wait_for_stream_access(stream_hls_url, stream_name):
                return
            self.scheduler.add_job(
                func=self.exec_stream, 
                id=stream_id, 
                args=(stream_id, stream_name, stream_prio, stream_hls_url)
            )
        else:
            self.scheduler.add_job(
                func=self.exec_stream, 
                trigger='cron', 
                hour=stream_start, 
                jitter=60,
                id=stream_id, 
                args=(stream_id, stream_name, stream_prio, stream_hls_url)
            )
        
        self.database[stream_id] = {
            'name': stream_name, 
            'start_at': stream_start, 
            'details': stream_details, 
            'src': stream_hls_url
        }
        
        # Bootstrap the playhead if it's still empty
        if not self.playhead:
            try:
                fallback = self.fallback_search()
                self.scheduler.add_job(
                    func=self.exec_stream, 
                    id=FALLBACK_JOB_ID, 
                    args=(fallback['stream_id'], fallback['stream_name'], 0, fallback['stream_hls_url'])
                )
            except ValueError as e:
                self.logger.warning(f'Could not bootstrap playhead: {e}')
    
    def _wait_for_stream_access(self, stream_hls_url: str, stream_name: str) -> bool:
        """Wait for stream to become accessible."""
        req_counter = 0
        while True:
            time.sleep(STREAM_ACCESS_RETRY_INTERVAL)
            req_counter += 1
            try:
                if requests.get(stream_hls_url).status_code == 200:
                    self.logger.warning(
                        f'{stream_hls_url} accessible after {req_counter} attempts.'
                    )
                    self.logger.warning(
                        f'Waiting extra {self.config.enable_delay} seconds before we initiate the stream...'
                    )
                    time.sleep(self.config.enable_delay)
                    return True
            except Exception as e:
                self.logger.debug(f'Stream access check failed: {e}')
            
            if req_counter == STREAM_ACCESS_RETRY_ATTEMPTS:
                self.logger.error(
                    f'Stream {stream_name} cancelled after {req_counter} attempts.'
                )
                return False
    
    def remove_channel_from_database(
        self, 
        stream_id: str, 
        stream_name: str, 
        state: Any
    ) -> None:
        """Remove channel from database and handle cleanup."""
        if stream_id not in self.database:
            return
        
        self.logger.warning(f'{stream_id} ({stream_name}) will be removed. Reason: {state.exec}')
        self.database.pop(stream_id)
        
        try:
            self.scheduler.remove_job(stream_id)
        except Exception as e:
            self.logger.error(f'Error removing job {stream_id}: {e}')
        
        # Handle the situation where we remove a stream that is currently playing
        if stream_id == self.playhead.get('id'):
            self.logger.warning(f'{stream_id} was playing.')
            try:
                fallback = self.fallback_search()
                self.priority = 0
                self.logger.warning('Source priority is reset to 0')
                self.scheduler.add_job(
                    func=self.exec_stream, 
                    id=FALLBACK_JOB_ID, 
                    args=(fallback['stream_id'], fallback['stream_name'], self.priority, fallback['stream_hls_url'])
                )
            except ValueError as e:
                self.logger.error(f'Could not find fallback stream after removing {stream_id}: {e}')
                self.playhead = {}
    
    def fallback_search(self) -> Dict[str, str]:
        """Search for a fallback stream based on current time."""
        self.logger.warning('Searching for a fallback job.')
        current_hour = int(datetime.now().hour)
        scheduled_hours = []
        
        # Collect scheduled hours from database
        for key, value in self.database.items():
            if value['start_at'] in ("now", "never"):
                # Do not use non-time scheduled streams as fallbacks
                continue
            try:
                scheduled_hours.append(int(value['start_at']))
            except (ValueError, TypeError):
                continue
        
        if not scheduled_hours:
            # No scheduled streams available, return first available stream
            if self.database:
                first_key = next(iter(self.database))
                first_value = self.database[first_key]
                return {
                    "stream_id": first_key,
                    "stream_name": first_value['name'],
                    "stream_hls_url": first_value['src']
                }
            # No streams at all
            raise ValueError("No streams available for fallback")
        
        # Convert the scheduled hours to a circular list
        scheduled_hours = scheduled_hours + [h + 24 for h in scheduled_hours]
        
        # Find the closest scheduled hour
        closest_hour = min(scheduled_hours, key=lambda x: abs(x - current_hour))
        target_hour = str(closest_hour % 24)
        
        # Find stream matching the closest hour
        for key, value in self.database.items():
            if value['start_at'] == target_hour:
                return {
                    "stream_id": key,
                    "stream_name": value['name'],
                    "stream_hls_url": value['src']
                }
        
        # Fallback to first available stream if no match found
        if self.database:
            first_key = next(iter(self.database))
            first_value = self.database[first_key]
            return {
                "stream_id": first_key,
                "stream_name": first_value['name'],
                "stream_hls_url": first_value['src']
            }
        
        raise ValueError("No streams available for fallback")
    
    def update_playhead(
        self, 
        stream_id: str, 
        stream_name: str, 
        stream_prio: int, 
        stream_hls_url: str
    ) -> None:
        """Update the playhead with new stream information."""
        self.playhead = {
            "id": stream_id,
            "name": stream_name,
            "prio": stream_prio,
            "head": stream_hls_url
        }
        self.logger.warning(f'Playhead: {str(self.playhead)}')
    
    def exec_stream(
        self, 
        stream_id: str, 
        stream_name: str, 
        stream_prio: int, 
        stream_hls_url: str
    ) -> None:
        """Execute stream based on priority."""
        if stream_prio > self.priority:
            self.priority = stream_prio
            self.logger.warning(f'Source priority is now set to: {self.priority}')
            self.update_playhead(stream_id, stream_name, stream_prio, stream_hls_url)
        elif stream_prio == self.priority:
            self.update_playhead(stream_id, stream_name, stream_prio, stream_hls_url)
        else:
            self.logger.warning(
                f'Source with higher priority ({self.priority}) is blocking. Skipping playhead update.'
            )
    
    def core_api_sync(self) -> None:
        """Synchronize with Datarhei CORE API."""
        new_ids = []
        
        try:
            process_list = self.client.v3_process_get_list()
        except Exception as e:
            self.logger.error(f'Error getting process list: {e}')
            return
        
        for process in process_list:
            try:
                get_process = self.get_core_process_details(process.id)
                if not get_process:
                    continue
                
                stream_id = get_process.reference
                meta = get_process.metadata
                state = get_process.state
            except Exception as e:
                self.logger.debug(f'Error processing stream: {process}, {e}')
                continue
            
            if meta is None or meta.get('restreamer-ui', {}).get('meta') is None:
                # Skip processes without metadata or meta key
                continue
            
            new_ids.append(stream_id)
            stream_name = meta['restreamer-ui']['meta']['name']
            stream_description = meta['restreamer-ui']['meta']['description']
            stream_storage_type = meta['restreamer-ui']['control']['hls']['storage']
            stream_hls_url = f'https://{self.config.core_hostname}/{stream_storage_type}/{stream_id}.m3u8'
            
            if state.exec == "running":
                self.process_running_channel(
                    stream_id, stream_name, stream_description, stream_hls_url
                )
            else:
                self.remove_channel_from_database(stream_id, stream_name, state)
                if stream_id in new_ids:
                    new_ids.remove(stream_id)
        
        # Cleanup orphaned references
        orphan_keys = [key for key in self.database if key not in new_ids]
        for orphan_key in orphan_keys:
            self.logger.warning(f'Key {orphan_key} is an orphan. Removing.')
            self.database.pop(orphan_key)
            try:
                self.scheduler.remove_job(orphan_key)
            except Exception as e:
                self.logger.error(f'Error removing orphan job {orphan_key}: {e}')


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

# Flask route helpers
def get_client_address(req) -> str:
    """Get client IP address, handling proxy headers."""
    if req.environ.get('HTTP_X_FORWARDED_FOR') is None:
        return req.environ['REMOTE_ADDR']
    return req.environ['HTTP_X_FORWARDED_FOR']


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


# Flask Routes
@app.route('/', methods=['GET'])
def root_route():
    """Frontend index page."""
    video_files = get_video_files(config.rec_path)
    sorted_thumbnails = get_sorted_thumbnails(config.rec_path)
    client_ip = get_client_address(request)
    loggers.content.warning(f'[{client_ip}] index /')
    return render_template(
        'index.html',
        now=datetime.utcnow(),
        video_files=video_files,
        thumbnails=sorted_thumbnails
    )


@app.route('/playhead', methods=['GET'])
def playhead_route():
    """Get current playhead information."""
    if stream_manager is None:
        return jsonify({}), 503
    return jsonify(stream_manager.playhead)


@app.route('/database', methods=['GET'])
def database_route():
    """Get stream database information."""
    if stream_manager is None:
        return jsonify({}), 503
    return jsonify(stream_manager.database)


@app.route("/thumb/<thumb_file>", methods=['GET'])
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
def video_route(video_file: str):
    """Stream video files."""
    video_path = os.path.join(config.rec_path, 'vod', video_file)
    if not os.path.exists(video_path):
        abort(404)
    
    client_ip = get_client_address(request)
    loggers.content.warning(f'[{client_ip}] stream {video_path}')
    return send_file(video_path, mimetype='video/mp4')


@app.route("/video/download/<video_file>", methods=['GET'])
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
        now=datetime.utcnow(),
        video_file=video_file,
        thumb_file=thumb_file
    )

def create_app() -> Flask:
    """Create and configure Flask application."""
    global stream_manager, client
    
    # Configure Flask app
    app.config['SERVER_NAME'] = config.server_name
    app.config['PREFERRED_URL_SCHEME'] = 'https'
    
    # Initialize Core API client
    client = _initialize_core_client(config, loggers.api)
    
    # Initialize stream manager
    stream_manager = StreamManager(scheduler, client, config, loggers.job)
    
    # Setup scheduler
    _setup_scheduler(stream_manager, config)
    
    return app
