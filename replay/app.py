"""
Replay Service - Plays recordings in rotation as a continuous HLS live stream.

Scans the recordings directory for video files, builds a concat playlist,
and uses FFmpeg to produce a looping HLS stream served over HTTP.
No transcoding — video and audio are copied as-is to preserve original quality.
"""

import glob
import logging
import os
import signal
import subprocess
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

LOG_LEVEL = os.environ.get("REPLAY_LOG_LEVEL", "info").upper()
RECORDINGS_DIR = os.environ.get("REPLAY_RECORDINGS_DIR", "/recordings/vod")
HLS_DIR = "/tmp/hls"
HTTP_PORT = int(os.environ.get("REPLAY_HTTP_PORT", "8090"))
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi")
SCAN_INTERVAL = int(os.environ.get("REPLAY_SCAN_INTERVAL", "60"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("replay")

ffmpeg_process: subprocess.Popen | None = None
shutdown_event = threading.Event()


def discover_videos() -> list[str]:
    """Return a sorted list of video file paths found in the recordings dir."""
    videos = []
    for ext in VIDEO_EXTENSIONS:
        videos.extend(glob.glob(os.path.join(RECORDINGS_DIR, f"*{ext}")))
    videos.sort()
    return videos


def write_concat_playlist(videos: list[str], path: str) -> None:
    """Write an FFmpeg concat demuxer playlist file."""
    with open(path, "w") as f:
        for v in videos:
            escaped = v.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    log.info("Wrote concat playlist with %d videos to %s", len(videos), path)


def start_ffmpeg(playlist_path: str) -> subprocess.Popen | None:
    """Start FFmpeg to remux the concat playlist into an HLS live stream (no transcoding)."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-stream_loop", "-1",
        "-f", "concat",
        "-safe", "0",
        "-re",
        "-i", playlist_path,
        # Copy codecs — no transcoding
        "-c:v", "copy",
        "-c:a", "copy",
        # HLS output
        "-f", "hls",
        "-hls_time", "4",
        "-hls_list_size", "10",
        "-hls_flags", "delete_segments+append_list+omit_endlist",
        "-hls_segment_filename", os.path.join(HLS_DIR, "seg_%05d.ts"),
        os.path.join(HLS_DIR, "stream.m3u8"),
    ]

    log.info("Starting FFmpeg: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return proc
    except FileNotFoundError:
        log.error("FFmpeg binary not found — is it installed?")
        return None


def ffmpeg_stderr_logger(proc: subprocess.Popen) -> None:
    """Read FFmpeg stderr in a thread and log it."""
    assert proc.stderr is not None
    for line in iter(proc.stderr.readline, b""):
        decoded = line.decode("utf-8", errors="replace").rstrip()
        if decoded:
            log.warning("ffmpeg: %s", decoded)


class CORSRequestHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves HLS files with CORS headers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HLS_DIR, **kwargs)

    def handle(self):
        """Suppress broken-pipe / connection-reset errors from disconnecting clients."""
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            log.debug("Client disconnected during response")

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        log.debug("HTTP: %s", format % args)


def run_http_server() -> HTTPServer:
    """Start the HTTP server in a background thread."""
    server = HTTPServer(("0.0.0.0", HTTP_PORT), CORSRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("HTTP server listening on port %d", HTTP_PORT)
    return server


def ffmpeg_loop():
    """Main loop: discover videos, start ffmpeg, restart if it dies or videos change."""
    global ffmpeg_process
    playlist_path = os.path.join(HLS_DIR, "playlist.txt")
    current_videos: list[str] = []

    while not shutdown_event.is_set():
        videos = discover_videos()

        if not videos:
            log.warning("No video files found in %s — waiting...", RECORDINGS_DIR)
            shutdown_event.wait(SCAN_INTERVAL)
            continue

        # If the video list changed, restart FFmpeg with the new playlist
        if videos != current_videos:
            if ffmpeg_process and ffmpeg_process.poll() is None:
                log.info("Video list changed, restarting FFmpeg...")
                ffmpeg_process.terminate()
                try:
                    ffmpeg_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    ffmpeg_process.kill()
                ffmpeg_process = None

            write_concat_playlist(videos, playlist_path)
            current_videos = videos
            ffmpeg_process = start_ffmpeg(playlist_path)

            if ffmpeg_process:
                threading.Thread(
                    target=ffmpeg_stderr_logger,
                    args=(ffmpeg_process,),
                    daemon=True,
                ).start()

        # Check if FFmpeg is still running
        if ffmpeg_process and ffmpeg_process.poll() is not None:
            log.warning("FFmpeg exited with code %d — restarting...", ffmpeg_process.returncode)
            ffmpeg_process = None
            current_videos = []  # Force re-discovery
            continue

        shutdown_event.wait(SCAN_INTERVAL)


def handle_signal(signum, frame):
    log.info("Received signal %d, shutting down...", signum)
    shutdown_event.set()


def main():
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    os.makedirs(HLS_DIR, exist_ok=True)

    log.info("Replay service starting")
    log.info("Recordings dir: %s", RECORDINGS_DIR)
    log.info("HLS output dir: %s", HLS_DIR)
    log.info("HTTP port: %d", HTTP_PORT)

    http_server = run_http_server()

    try:
        ffmpeg_loop()
    finally:
        log.info("Shutting down...")
        if ffmpeg_process and ffmpeg_process.poll() is None:
            ffmpeg_process.terminate()
            try:
                ffmpeg_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                ffmpeg_process.kill()
        http_server.shutdown()
        log.info("Replay service stopped")


if __name__ == "__main__":
    main()
