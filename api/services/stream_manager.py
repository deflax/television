import json
import os
import random
import logging
import threading
import time
from pathlib import Path
from urllib.parse import urljoin
import requests
from datetime import date, datetime, timezone
from typing import Dict, Optional, Any
from apscheduler.schedulers.background import BackgroundScheduler
from services.core_api import CoreAPIClient
from services.playhead_metadata import PlayheadMetadataPoller
from services.schedule import parse_channel_settings, programmes_for_days



# Constants
STREAM_ACCESS_TIMEOUT = 5
DEFAULT_METADATA_TIMEOUT = 3.5
DISABLED_STREAM_START = 'never'


class StreamManager:
    """Manages stream state, database, and scheduling logic."""
    
    def __init__(self, scheduler: BackgroundScheduler, client: CoreAPIClient, config, logger: logging.Logger):
        self.scheduler = scheduler
        self.client = client
        self.config = config
        self.logger = logger
        self.database: Dict[str, Dict[str, Any]] = {}
        self.playhead: Dict[str, Any] = {}
        self.priority = 0
        self.metadata_poller = PlayheadMetadataPoller(client, logger)
        self.catalogue: Dict[str, Dict[str, Any]] = {}
        self.schedule_day: str | None = None
        self._state_path = Path(getattr(config, 'schedule_state_path', '/state/schedule.json'))
        self._state_lock = threading.Lock()
        try:
            self._daily_state = json.loads(self._state_path.read_text())
            if not isinstance(self._daily_state, dict):
                raise ValueError('schedule state must be an object')
        except FileNotFoundError:
            self._daily_state = {}
        self._readiness: dict[str, tuple[str, float, bool]] = {}
        self._executed_programmes: set[tuple[str, str]] = set()

        # Discord-initiated command tracking: stream_id -> {action, actor_name}
        # When a Discord user runs .start/.stop, the stream reference is recorded here.
        # When core_api_sync later adds/removes the channel from the database,
        # the on_db_change callback fires only for these tracked entries.
        self._pending_discord_actions: Dict[str, Dict[str, str]] = {}
        self._on_db_change_callback = None  # callable(action, stream_name, stream_id, actor_name)

    
    def get_core_process_list(self) -> list:
        """Get all processes from Core API with their names and states."""
        processes = []
        try:
            process_list = self.client.v3_process_get_list()
        except Exception as e:
            self.logger.error(f'Error getting process list: {e}')
            return processes

        for process in process_list:
            try:
                details = self.get_core_process_details(process['id'])
                if not details:
                    continue
                meta = details.get('metadata')
                if meta is None or meta.get('restreamer-ui', {}).get('meta') is None:
                    continue
                stream_name = meta['restreamer-ui']['meta']['name']
                state = details.get('state')
                state_exec = state['exec'] if state else 'unknown'
                processes.append({
                    'id': process['id'],
                    'reference': details['reference'],
                    'name': stream_name,
                    'state': state_exec,
                })
            except Exception as e:
                self.logger.debug(f'Error processing stream for list: {process.get("id")}, {e}')
                continue
        return processes

    def process_command(self, process_id: str, command: str) -> dict:
        """Send a command (start/stop/restart/reload) to a Restreamer process.

        Args:
            process_id: The process ID to send the command to.
            command: One of 'start', 'stop', 'restart', 'reload'.

        Returns:
            A dict with 'success' (bool) and 'message' (str).
        """
        valid_commands = ('start', 'stop', 'restart', 'reload')
        if command not in valid_commands:
            return {'success': False, 'message': f'Invalid command. Must be one of: {", ".join(valid_commands)}'}

        try:
            self.client.v3_process_put_command(id=process_id, command=command)
            self.logger.info(f'Sent "{command}" command to process {process_id}')
            return {'success': True, 'message': f'Process {process_id} command "{command}" sent successfully.'}
        except Exception as e:
            self.logger.error(f'Failed to send "{command}" to process {process_id}: {e}')
            return {'success': False, 'message': f'Failed to send command: {e}'}

    def get_core_process_details(self, process_id: str) -> Optional[dict]:
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
        stream_hls_url: str,
    ) -> None:
        """Learn a running channel only after its HLS output makes progress."""
        try:
            settings = parse_channel_settings(stream_description)
        except (ValueError, TypeError, SyntaxError) as e:
            self.logger.warning(f'{stream_id} ({stream_name}) skipped: {e}')
            return
        if settings['start_at'] == DISABLED_STREAM_START:
            return
        if not self._stream_is_playable(stream_id, stream_hls_url):
            self.remove_channel_from_database(stream_id, stream_name, {'exec': 'unavailable'})
            return
        self.database[stream_id] = {
            **settings, 'name': stream_name, 'src': stream_hls_url,
            'details': settings.get('details', ''),
        }
        pending = self._pending_discord_actions.pop(stream_id, None)
        if pending and self._on_db_change_callback:
            self._on_db_change_callback(
                'added', stream_name, stream_id, pending.get('actor_name', 'Unknown')
            )

    def _stream_is_playable(self, stream_id: str, url: str) -> bool:
        """Require an advancing media playlist and an accessible latest segment."""
        try:
            response = requests.get(url, timeout=STREAM_ACCESS_TIMEOUT)
            response.raise_for_status()
            playlist = response.text
            if '#EXT-X-STREAM-INF:' in playlist:
                variants = [line.strip() for line in playlist.splitlines()
                            if line.strip() and not line.startswith('#')]
                if not variants:
                    return False
                url = urljoin(url, variants[0])
                response = requests.get(url, timeout=STREAM_ACCESS_TIMEOUT)
                response.raise_for_status()
                playlist = response.text
            if not playlist.startswith('#EXTM3U') or '#EXTINF:' not in playlist:
                return False
            segments = [line.strip() for line in playlist.splitlines()
                        if line.strip() and not line.startswith('#')]
            if not segments or '#EXT-X-ENDLIST' in playlist:
                return False
            sequence = next((line for line in playlist.splitlines()
                             if line.startswith('#EXT-X-MEDIA-SEQUENCE:')), '')
            signature = sequence + ':' + segments[-1]
            now = time.monotonic()
            previous = self._readiness.get(stream_id)
            changed = previous is not None and previous[0] != signature
            updated = now if previous is None or changed else previous[1]
            advancing = changed or (previous is not None and previous[2])
            self._readiness[stream_id] = (signature, updated, advancing)
            if not advancing or now - updated > 60:
                return False
            segment_url = urljoin(url, segments[-1])
            with requests.get(segment_url, timeout=STREAM_ACCESS_TIMEOUT,
                              headers={'Range': 'bytes=0-0'}, stream=True) as segment:
                segment.raise_for_status()
                return bool(next(segment.iter_content(chunk_size=1), b''))
        except (requests.RequestException, ValueError) as e:
            self.logger.debug(f'HLS readiness check failed for {stream_id}: {e}')
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

        if isinstance(state, dict):
            reason = state.get('exec', 'unknown')
        else:
            reason = 'unknown'

        self.logger.warning(f'{stream_id} ({stream_name}) will be removed. Reason: {reason}')
        self.database.pop(stream_id)

        # Fire callback if this was a Discord-initiated stop
        pending = self._pending_discord_actions.pop(stream_id, None)
        if pending and self._on_db_change_callback:
            self._on_db_change_callback(
                'removed', stream_name, stream_id, pending.get('actor_name', 'Unknown')
            )

        self._executed_programmes = {
            key for key in self._executed_programmes if key[0] != stream_id
        }
        if stream_id == self.playhead.get('id'):
            self.playhead = {}
            self.priority = 0
    
    def get_next_stream(self) -> Optional[Dict[str, Any]]:
        """Pick a random stream from the database, excluding the currently playing one.
        
        Returns None if the database is empty or contains only the current stream.
        """
        current_id = self.playhead.get('id')
        
        # Collect all streams except the currently playing one
        candidates = []
        for key, value in self.database.items():
            if key != current_id:
                candidates.append({
                    'id': key,
                    'name': value['name'],
                    'src': value['src'],
                    'prio': value['prio'],
                })
        
        if not candidates:
            return None
        
        # Pick a random stream
        s = random.choice(candidates)
        return {'stream_id': s['id'], 'stream_name': s['name'], 
                'stream_hls_url': s['src'], 'stream_prio': s['prio']}
    
    def fallback_search(self) -> Dict[str, str]:
        """Choose a playable fallback, preferring the most recent due schedule."""
        if not self.database:
            raise ValueError('No streams available for fallback')
        now = datetime.now(timezone.utc)
        due = [item for item in programmes_for_days(self.catalogue, now.date(), 1)
               if item['start'] <= now and item['id'] in self.database]
        stream_id = max(due, key=lambda item: item['start'])['id'] if due else next(iter(self.database))
        entry = self.database[stream_id]
        return {'stream_id': stream_id, 'stream_name': entry['name'], 'stream_hls_url': entry['src']}

    def poll_current_metadata(self) -> None:
        self.playhead = self.metadata_poller.poll(self.playhead, self._metadata_timeout())

    def _metadata_timeout(self) -> float:
        timeout = getattr(self.config, 'metadata_timeout', None)
        if timeout is None:
            timeout = getattr(self.config, 'icy_timeout', DEFAULT_METADATA_TIMEOUT)
        return float(timeout)

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
        self.logger.info(f'Playhead: {str(self.playhead)}')
    
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
            self.logger.info(f'Source priority is now set to: {self.priority}')
            self.update_playhead(stream_id, stream_name, stream_prio, stream_hls_url)
        elif stream_prio == self.priority:
            self.update_playhead(stream_id, stream_name, stream_prio, stream_hls_url)
        else:
            self.logger.warning(
                f'Source with higher priority ({self.priority}) is blocking. Skipping playhead update.'
            )
    
    def _save_daily_state(self, state: dict) -> None:
        """Atomically persist decisions before sending commands."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix('.tmp')
        with temporary.open('w') as handle:
            json.dump(state, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self._state_path)
        self._daily_state = state

    def daily_announcement_date(self) -> str | None:
        with self._state_lock:
            return self._daily_state.get('announced')

    def mark_daily_announced(self, day: str) -> None:
        with self._state_lock:
            self._save_daily_state({**self._daily_state, 'announced': day})

    def _apply_daily_control(self, stream_id: str, entry: dict, state: dict, today: date) -> bool:
        if ('days' not in entry or entry['start_at'] == DISABLED_STREAM_START
                or entry['process_id'] != f'restreamer-ui:ingest:{stream_id}'):
            return False
        day = today.isoformat()
        with self._state_lock:
            decisions = self._daily_state.get('decisions', {})
            if decisions.get(stream_id) == day:
                return False
            self._save_daily_state({
                **self._daily_state,
                'decisions': {**decisions, stream_id: day},
            })
        allowed = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')[today.weekday()] in entry['days']
        command = 'start' if allowed else 'stop'
        # Core's desired order distinguishes reconnecting inputs from stopped ones.
        order = state.get('order')
        already_set = order == command if order else (
            state.get('exec') == 'running' if allowed else state.get('exec') == 'finished'
        )
        if not already_set:
            self.process_command(entry['process_id'], command)
            return not allowed
        return False

    def _select_due_programme(self, now: datetime) -> None:
        day = now.date()
        self._executed_programmes = {
            key for key in self._executed_programmes if key[1].startswith(day.isoformat())
        }
        due = [programme for programme in programmes_for_days(self.catalogue, day, 1)
               if programme['start'] <= now < programme['stop']
               and programme['id'] in self.database]
        for stream_id, entry in self.database.items():
            if entry['start_at'] == 'now':
                due.append({'id': stream_id, 'start': now.replace(hour=0, minute=0, second=0, microsecond=0),
                            'prio': entry.get('prio', 0)})
        due.sort(key=lambda item: (item.get('prio', 0), item['start'], item['id']), reverse=True)
        for programme in due:
            stream_id = programme['id']
            key = (stream_id, programme['start'].isoformat())
            if key in self._executed_programmes:
                break
            entry = self.database[stream_id]
            self.exec_stream(stream_id, entry['name'], entry.get('prio', 0), entry['src'])
            if self.playhead.get('id') == stream_id:
                self._executed_programmes.add(key)
                break
        if not self.playhead and self.database:
            fallback = self.fallback_search()
            entry = self.database[fallback['stream_id']]
            self.exec_stream(fallback['stream_id'], entry['name'], entry.get('prio', 0), entry['src'])

    def core_api_sync(self) -> None:
        """Discover all schedules; reconcile inputs daily and playback each sync."""
        now = datetime.now(timezone.utc)
        try:
            process_list = self.client.v3_process_get_list()
        except Exception as e:
            self.logger.error(f'Error getting process list: {e}')
            return
        catalogue = {}
        runtime_details = {}
        complete = True
        for process in process_list:
            details = self.get_core_process_details(process['id'])
            if not details:
                complete = False
                continue
            meta = (details.get('metadata') or {}).get('restreamer-ui', {})
            if not meta.get('meta'):
                continue
            try:
                stream_id = details['reference']
                channel = meta['meta']
                settings = parse_channel_settings(channel['description'])
                storage = meta['control']['hls']['storage']
                url = f'https://{self.config.core_hostname}/{storage}/{stream_id}.m3u8'
                entry = {**settings, 'name': channel['name'], 'src': url,
                         'details': settings.get('details', ''), 'process_id': process['id']}
            except (KeyError, ValueError, TypeError, SyntaxError) as e:
                self.logger.warning(f'Invalid schedule for {process["id"]}: {e}')
                continue
            # Ingest owns control; publication processes must not duplicate decisions.
            if stream_id in catalogue and not process['id'].startswith('restreamer-ui:ingest:'):
                continue
            catalogue[stream_id] = entry
            runtime_details[stream_id] = details
        for stream_id, entry in catalogue.items():
            details = runtime_details[stream_id]
            state = details.get('state') or {}
            stopped = self._apply_daily_control(stream_id, entry, state, now.date())
            if entry['start_at'] != DISABLED_STREAM_START and state.get('exec') == 'running' and not stopped:
                self.process_running_channel(
                    stream_id, entry['name'],
                    details['metadata']['restreamer-ui']['meta']['description'], entry['src'],
                )
            else:
                self._readiness.pop(stream_id, None)
                self.remove_channel_from_database(stream_id, entry['name'], state)
        if complete:
            for stream_id in list(self.database):
                if stream_id not in catalogue:
                    self.remove_channel_from_database(stream_id, self.database[stream_id]['name'], {'exec': 'removed'})
            self.catalogue = catalogue
            self.schedule_day = now.date().isoformat()
        else:
            self.catalogue = {**self.catalogue, **catalogue}
        self._select_due_programme(now)

    def register_db_change_callback(self, callback) -> None:
        """Register a callback for Discord-initiated database changes.

        The callback is called as: callback(action, stream_name, stream_id, actor_name)
        where action is 'added' or 'removed'.
        """
        self._on_db_change_callback = callback

    def track_discord_command(self, stream_id: str, action: str, actor_name: str) -> None:
        """Record that a Discord user initiated a start/stop for a stream.

        The stream_id is the reference ID used as the database key.
        When core_api_sync detects the resulting state change, the announcement fires.
        """
        self._pending_discord_actions[stream_id] = {
            'action': action,
            'actor_name': actor_name,
        }
