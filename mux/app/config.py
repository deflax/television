"""Configuration module for mux service."""

import os
import json
import logging

logger = logging.getLogger(__name__)

# API connection
API_URL = os.environ.get('API_URL', 'http://api:8080')

# HLS output configuration
HLS_OUTPUT_DIR = '/tmp/hls'
HLS_SEGMENT_TIME = int(os.environ.get('HLS_SEGMENT_TIME', '4'))
HLS_LIST_SIZE = int(os.environ.get('HLS_LIST_SIZE', '20'))

# Server settings
SERVER_PORT = 8091

# Internal restreamer URL rewriting (bypass public hostname/Cloudflare)
RESTREAMER_INTERNAL_URL = os.environ.get('RESTREAMER_INTERNAL_URL', 'http://restreamer:8080')
RESTREAMER_PUBLIC_HOST = os.environ.get('CORE_API_HOSTNAME', '')

# Mux mode: 'copy' (passthrough) or 'abr' (adaptive bitrate with source copy)
MUX_MODE = os.environ.get('MUX_MODE', 'copy').lower()

# ABR encoding settings
ABR_PRESET = os.environ.get('ABR_PRESET', 'veryfast')
ABR_GOP_SIZE = int(os.environ.get('ABR_GOP_SIZE', '48'))

# ABR variants configuration
DEFAULT_ABR_VARIANTS = [
    {"height": 1080, "video_bitrate": "5000k", "audio_bitrate": "192k"},
    {"height": 720, "video_bitrate": "2800k", "audio_bitrate": "128k"},
    {"height": 576, "video_bitrate": "1400k", "audio_bitrate": "96k"},
]


def parse_abr_variants() -> list[dict]:
    """Parse ABR_VARIANTS from environment or use defaults."""
    variants_json = os.environ.get('ABR_VARIANTS', '')
    if variants_json:
        try:
            variants = json.loads(variants_json)
            if isinstance(variants, list) and len(variants) > 0:
                logger.info(f"Using custom ABR variants: {variants}")
                return variants
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid ABR_VARIANTS JSON, using defaults: {e}")
    return DEFAULT_ABR_VARIANTS


ABR_VARIANTS = parse_abr_variants()

# Icecast audio streaming configuration
ICECAST_ENABLED = os.environ.get('ICECAST_ENABLED', 'true').lower() in ('true', '1', 'yes')
ICECAST_HOST = os.environ.get('ICECAST_HOST', 'icecast')
ICECAST_PORT = int(os.environ.get('ICECAST_PORT', '8000'))
ICECAST_SOURCE_PASSWORD = os.environ.get('ICECAST_SOURCE_PASSWORD', 'hackme')
ICECAST_MOUNT = os.environ.get('ICECAST_MOUNT', '/stream.mp3')
ICECAST_AUDIO_BITRATE = os.environ.get('ICECAST_AUDIO_BITRATE', '128k')
ICECAST_AUDIO_FORMAT = os.environ.get('ICECAST_AUDIO_FORMAT', 'mp3')

# Transition settings
TRANSITION_TIMEOUT = float(os.environ.get('TRANSITION_TIMEOUT', '15'))
SEGMENT_STABILITY_DELAY = 0.1

# Derived values
NUM_VARIANTS = len(ABR_VARIANTS) + 1 if MUX_MODE == 'abr' else 1
MAX_SEGMENT_AGE = HLS_LIST_SIZE * HLS_SEGMENT_TIME * 3


def parse_bitrate(bitrate_str: str) -> int:
    """Parse a human-readable bitrate string to integer kbps.
    
    Examples: '5000k' -> 5000, '2.5m' -> 2500, '128' -> 128
    """
    bitrate_str = bitrate_str.lower().strip()
    if bitrate_str.endswith('m'):
        return int(float(bitrate_str[:-1]) * 1000)
    if bitrate_str.endswith('k'):
        return int(float(bitrate_str[:-1]))
    return int(bitrate_str)


def rewrite_stream_url(url: str) -> str:
    """Rewrite public stream URL to use internal restreamer container."""
    if not RESTREAMER_PUBLIC_HOST or not RESTREAMER_INTERNAL_URL:
        return url
    
    public_prefix = f'https://{RESTREAMER_PUBLIC_HOST}/'
    if url.startswith(public_prefix):
        internal_url = RESTREAMER_INTERNAL_URL.rstrip('/') + '/' + url[len(public_prefix):]
        logger.debug(f'Rewrote URL: {url} -> {internal_url}')
        return internal_url
    
    return url
