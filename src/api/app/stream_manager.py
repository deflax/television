import time
import ast
import logging
import requests
from datetime import datetime
from typing import Dict, Optional, Any
from apscheduler.schedulers.background import BackgroundScheduler
from core_client import Client


# Constants
STREAM_ACCESS_RETRY_ATTEMPTS = 15
STREAM_ACCESS_RETRY_INTERVAL = 6
FALLBACK_JOB_ID = 'fallback'


class StreamManager:
    """Manages stream state, database, and scheduling logic."""
    
    def __init__(self, scheduler: BackgroundScheduler, client: Client, config, logger: logging.Logger):
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
