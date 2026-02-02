"""Channel class and ffmpeg management for the replay service."""

import json
import os
import random
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import (
    logger,
    stop_event,
    channels,
    channels_lock,
    HLS_BASE_DIR,
    HLS_SEGMENT_TIME,
    HLS_LIST_SIZE,
    VIDEO_BITRATE,
    AUDIO_BITRATE,
    SCAN_INTERVAL,
    SEGMENT_RETAIN_SECONDS,
    LIBRARY_DIR,
    RESERVED_CHANNEL,
)


@dataclass
class Channel:
    """Represents a single replay channel with its own ffmpeg process.
    
    Per-channel config can be set via a channel.json file in the source directory:
    {
        "transcode": false  // Use copy mode instead of re-encoding (default: false)
    }
    """
    name: str
    source_dir: str
    hls_dir: str
    transcode: bool = False
    ffmpeg_process: Optional[subprocess.Popen] = None
    shuffled_files: list[str] = field(default_factory=list)
    known_files: set[str] = field(default_factory=set)
    restart_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None

    def load_config(self) -> None:
        """Load channel configuration from channel.json if present."""
        config_path = Path(self.source_dir) / 'channel.json'
        if not config_path.exists():
            return
        try:
            with open(config_path) as f:
                config = json.load(f)
            if 'transcode' in config:
                self.transcode = bool(config['transcode'])
                logger.info(f"[{self.name}] Config loaded: transcode={self.transcode}")
        except Exception as e:
            logger.warning(f"[{self.name}] Failed to load channel.json: {e}")

    def get_video_files(self) -> list[str]:
        """Find all video files in this channel's source directory."""
        source_path = Path(self.source_dir)
        if not source_path.exists():
            logger.warning(f"[{self.name}] Source directory does not exist: {self.source_dir}")
            return []

        video_files = [
            f for f in source_path.rglob('*')
            if f.suffix.lower() in ('.mp4', '.mkv')
        ]
        files = sorted([str(f) for f in video_files])
        return files

    def shuffle_playlist(self) -> list[str]:
        """Refresh and shuffle the playlist."""
        files = self.get_video_files()
        if files:
            random.shuffle(files)
            self.shuffled_files = files
            logger.info(f"[{self.name}] Shuffled playlist with {len(files)} files")
        return self.shuffled_files

    def create_concat_file(self) -> str:
        """Create a concat demuxer file for ffmpeg."""
        concat_file = f'/tmp/concat_{self.name}.txt'
        with open(concat_file, 'w') as f:
            for video_file in self.shuffled_files:
                escaped = video_file.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")
        return concat_file


def cleanup_old_segments(channel: Channel):
    """Periodically remove old .ts segments for a channel."""
    hls_dir = Path(channel.hls_dir)
    while not stop_event.is_set():
        try:
            now = time.time()
            for seg in hls_dir.glob('segment_*.ts'):
                try:
                    age = now - seg.stat().st_mtime
                    if age > SEGMENT_RETAIN_SECONDS:
                        seg.unlink()
                except FileNotFoundError:
                    pass
                except Exception as e:
                    logger.warning(f"[{channel.name}] Could not remove old segment {seg}: {e}")
        except Exception as e:
            logger.warning(f"[{channel.name}] Segment cleanup error: {e}")
        stop_event.wait(timeout=HLS_SEGMENT_TIME)


def watch_channel_files(channel: Channel):
    """Watch for file changes in a channel's source directory."""
    while not stop_event.is_set():
        stop_event.wait(timeout=SCAN_INTERVAL)
        if stop_event.is_set():
            break
        current_files = set(channel.get_video_files())
        if current_files == channel.known_files:
            continue
        added = current_files - channel.known_files
        removed = channel.known_files - current_files
        for f in added:
            logger.info(f"[{channel.name}] File added: {Path(f).name}")
        for f in removed:
            logger.info(f"[{channel.name}] File removed: {Path(f).name}")
        channel.known_files = current_files
        channel.restart_event.set()


def run_channel_ffmpeg(channel: Channel):
    """Run ffmpeg for a single channel."""
    os.makedirs(channel.hls_dir, exist_ok=True)

    # Clean up old segments from previous runs
    for f in Path(channel.hls_dir).glob('*'):
        try:
            f.unlink()
        except Exception as e:
            logger.warning(f"[{channel.name}] Could not remove {f}: {e}")

    # Start cleanup thread for this channel
    cleanup_thread = threading.Thread(
        target=cleanup_old_segments, args=(channel,), daemon=True
    )
    cleanup_thread.start()

    # Start file watcher thread for this channel
    watcher_thread = threading.Thread(
        target=watch_channel_files, args=(channel,), daemon=True
    )
    watcher_thread.start()

    logger.info(f"[{channel.name}] Started (source: {channel.source_dir})")

    while not stop_event.is_set():
        channel.shuffle_playlist()
        channel.known_files = set(channel.shuffled_files)
        channel.restart_event.clear()

        if not channel.shuffled_files:
            logger.warning(f"[{channel.name}] No video files found. Waiting 30 seconds...")
            time.sleep(30)
            continue

        concat_file = channel.create_concat_file()

        # Build ffmpeg command - use copy mode or transcode based on channel config
        cmd = [
            'ffmpeg',
            '-re',
            '-f', 'concat',
            '-safe', '0',
            '-stream_loop', '-1',
            '-i', concat_file,
        ]

        if channel.transcode:
            # Full transcoding
            cmd.extend([
                '-c:v', 'libx264',
                '-preset', 'veryfast',
                '-tune', 'zerolatency',
                '-b:v', VIDEO_BITRATE,
                '-maxrate', VIDEO_BITRATE,
                '-bufsize', str(int(VIDEO_BITRATE.replace('k', '000')) * 2),
                '-g', '48',
                '-sc_threshold', '0',
                '-keyint_min', '48',
                '-c:a', 'aac',
                '-b:a', AUDIO_BITRATE,
                '-ac', '2',
                '-ar', '44100',
            ])
        else:
            # Copy mode - no re-encoding (requires compatible source files)
            cmd.extend([
                '-c:v', 'copy',
                '-c:a', 'copy',
            ])

        cmd.extend([
            '-f', 'hls',
            '-hls_time', str(HLS_SEGMENT_TIME),
            '-hls_list_size', str(HLS_LIST_SIZE),
            '-hls_flags', 'append_list+omit_endlist+temp_file',
            '-hls_segment_type', 'mpegts',
            '-hls_segment_filename', f'{channel.hls_dir}/segment_%05d.ts',
            f'{channel.hls_dir}/playlist.m3u8'
        ])

        mode = "transcode" if channel.transcode else "copy"
        logger.info(f"[{channel.name}] Starting ffmpeg ({mode} mode)")

        try:
            channel.ffmpeg_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )

            stderr_lines: list[str] = []

            def _drain_stderr():
                assert channel.ffmpeg_process is not None
                assert channel.ffmpeg_process.stderr is not None
                for raw_line in channel.ffmpeg_process.stderr:
                    line = raw_line.decode(errors='replace').rstrip()
                    if line:
                        stderr_lines.append(line)
                        if len(stderr_lines) > 200:
                            del stderr_lines[:100]
                        if line.startswith('[concat @') and "Opening '" in line:
                            track = line.split("Opening '", 1)[1].rstrip("'")
                            logger.info(f"[{channel.name}] Now playing: {Path(track).name}")

            drain_thread = threading.Thread(target=_drain_stderr, daemon=True)
            drain_thread.start()

            last_segment_time = time.time()
            while channel.ffmpeg_process.poll() is None and not stop_event.is_set():
                time.sleep(2)

                if channel.restart_event.is_set():
                    logger.info(f"[{channel.name}] Files changed, restarting ffmpeg...")
                    channel.ffmpeg_process.terminate()
                    try:
                        channel.ffmpeg_process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        channel.ffmpeg_process.kill()
                    break

                hls_path = Path(channel.hls_dir)
                segments = sorted(hls_path.glob('segment_*.ts'))
                if segments:
                    newest_mtime = segments[-1].stat().st_mtime
                    if newest_mtime > last_segment_time:
                        last_segment_time = newest_mtime
                    elif time.time() - last_segment_time > HLS_SEGMENT_TIME * 10:
                        logger.error(f"[{channel.name}] ffmpeg stalled, killing...")
                        channel.ffmpeg_process.kill()
                        break

            if stop_event.is_set():
                break

            drain_thread.join(timeout=5)

            if channel.restart_event.is_set():
                continue

            logger.warning(f"[{channel.name}] ffmpeg ended (code: {channel.ffmpeg_process.returncode})")
            if stderr_lines:
                logger.warning(f"[{channel.name}] ffmpeg stderr:\n" + '\n'.join(stderr_lines[-20:]))

            logger.info(f"[{channel.name}] Restarting ffmpeg in 5 seconds...")
            time.sleep(5)

        except Exception as e:
            logger.error(f"[{channel.name}] Error running ffmpeg: {e}")
            time.sleep(5)


def discover_library_channels() -> list[str]:
    """Discover channel directories in the library."""
    library_path = Path(LIBRARY_DIR)
    if not library_path.exists():
        return []
    
    channel_names = []
    for entry in library_path.iterdir():
        if entry.is_dir():
            name = entry.name
            if name == RESERVED_CHANNEL:
                logger.error(f"CONFLICT: '{LIBRARY_DIR}/{name}' conflicts with reserved channel 'recorder'. Skipping.")
                continue
            channel_names.append(name)
    return sorted(channel_names)


def start_channel(name: str, source_dir: str):
    """Start a new channel."""
    with channels_lock:
        if name in channels:
            logger.warning(f"Channel '{name}' already exists")
            return

        hls_dir = f'{HLS_BASE_DIR}/{name}'
        channel = Channel(name=name, source_dir=source_dir, hls_dir=hls_dir)
        channel.load_config()
        channel.thread = threading.Thread(
            target=run_channel_ffmpeg, args=(channel,), daemon=True
        )
        channel.thread.start()
        channels[name] = channel
        logger.info(f"Channel '{name}' started (transcode={channel.transcode})")


def stop_channel(name: str):
    """Stop a channel."""
    with channels_lock:
        if name not in channels:
            return
        channel = channels[name]
        if channel.ffmpeg_process:
            channel.ffmpeg_process.terminate()
            try:
                channel.ffmpeg_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                channel.ffmpeg_process.kill()
        del channels[name]
        logger.info(f"Channel '{name}' stopped")


def watch_library():
    """Watch for new/removed directories in the library."""
    known_library_channels: set[str] = set()

    while not stop_event.is_set():
        current_channels = set(discover_library_channels())

        added = current_channels - known_library_channels
        removed = known_library_channels - current_channels

        for name in added:
            source_dir = f'{LIBRARY_DIR}/{name}'
            logger.info(f"New library channel discovered: {name}")
            start_channel(name, source_dir)

        for name in removed:
            logger.info(f"Library channel removed: {name}")
            stop_channel(name)

        known_library_channels = current_channels
        stop_event.wait(timeout=SCAN_INTERVAL)


def stop_all_channels():
    """Stop all channels gracefully."""
    stop_event.set()
    with channels_lock:
        for name, channel in list(channels.items()):
            if channel.ffmpeg_process:
                logger.info(f"Stopping channel '{name}'...")
                channel.ffmpeg_process.terminate()
                try:
                    channel.ffmpeg_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    channel.ffmpeg_process.kill()
        channels.clear()
