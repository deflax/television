"""Configuration module for mux service."""

import os
import json
import logging
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T', int, float)


def _parse_env(
    name: str,
    default: T,
    type_fn: type,
    min_val: T | None = None,
    max_val: T | None = None,
) -> T:
    """Parse and validate an environment variable.
    
    Args:
        name: Environment variable name
        default: Default value if not set or invalid
        type_fn: Type conversion function (int, float)
        min_val: Minimum allowed value (inclusive)
        max_val: Maximum allowed value (inclusive)
    
    Returns:
        Parsed and validated value, or default on error
    """
    raw = os.environ.get(name, '')
    if not raw:
        return default
    
    try:
        val = type_fn(raw)
        if min_val is not None and val < min_val:
            logger.warning(f'{name}={val} below minimum {min_val}, using {min_val}')
            return min_val
        if max_val is not None and val > max_val:
            logger.warning(f'{name}={val} above maximum {max_val}, using {max_val}')
            return max_val
        return val
    except (ValueError, TypeError) as e:
        logger.warning(f'Invalid {name}={raw!r}: {e}, using default {default}')
        return default


# API connection
API_URL = os.environ.get('API_URL', 'http://api:8080')

# HLS output configuration
HLS_OUTPUT_DIR = '/tmp/hls'
HLS_SEGMENT_TIME = _parse_env('HLS_SEGMENT_TIME', 4, int, min_val=1, max_val=60)
HLS_LIST_SIZE = _parse_env('HLS_LIST_SIZE', 20, int, min_val=3, max_val=100)

# Server settings
SERVER_PORT = 8091

# Internal restreamer URL rewriting (bypass public hostname/Cloudflare)
RESTREAMER_INTERNAL_URL = os.environ.get('RESTREAMER_INTERNAL_URL', 'http://restreamer:8080')
RESTREAMER_PUBLIC_HOST = os.environ.get('CORE_API_HOSTNAME', '')

# Mux mode: 'copy' (passthrough) or 'abr' (adaptive bitrate with source copy)
MUX_MODE = os.environ.get('MUX_MODE', 'copy').lower()

# ABR encoding settings
ABR_PRESET = os.environ.get('ABR_PRESET', 'veryfast')
ABR_GOP_SIZE = _parse_env('ABR_GOP_SIZE', 48, int, min_val=1, max_val=300)

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
            if not isinstance(variants, list) or len(variants) == 0:
                raise ValueError('ABR_VARIANTS must be a non-empty list')
            
            # Validate required keys in each variant
            required_keys = {'height', 'video_bitrate', 'audio_bitrate'}
            for i, v in enumerate(variants):
                if not isinstance(v, dict):
                    raise ValueError(f'Variant {i} is not an object')
                missing = required_keys - v.keys()
                if missing:
                    raise ValueError(f'Variant {i} missing keys: {missing}')
            
            logger.info(f"Using custom ABR variants: {variants}")
            return variants
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Invalid ABR_VARIANTS, using defaults: {e}")
    return DEFAULT_ABR_VARIANTS


ABR_VARIANTS = parse_abr_variants()

# Icecast audio streaming configuration
ICECAST_ENABLED = os.environ.get('ICECAST_ENABLED', 'true').lower() in ('true', '1', 'yes')
ICECAST_HOST = os.environ.get('ICECAST_HOST', 'icecast')
ICECAST_PORT = _parse_env('ICECAST_PORT', 8000, int, min_val=1, max_val=65535)
ICECAST_SOURCE_PASSWORD = os.environ.get('ICECAST_SOURCE_PASSWORD', 'hackme')
ICECAST_MOUNT = os.environ.get('ICECAST_MOUNT', '/stream.mp3')
ICECAST_AUDIO_BITRATE = os.environ.get('ICECAST_AUDIO_BITRATE', '128k')
ICECAST_AUDIO_FORMAT = os.environ.get('ICECAST_AUDIO_FORMAT', 'mp3')

# Transition settings
TRANSITION_TIMEOUT = _parse_env('TRANSITION_TIMEOUT', 15.0, float, min_val=1.0, max_val=120.0)
SEGMENT_STABILITY_DELAY = 0.1

# Derived values
NUM_VARIANTS = len(ABR_VARIANTS) + 1 if MUX_MODE == 'abr' else 1
MAX_SEGMENT_AGE = HLS_LIST_SIZE * HLS_SEGMENT_TIME * 3


def parse_bitrate(bitrate_str: str, default: int = 1000) -> int:
    """Parse a human-readable bitrate string to integer kbps.
    
    Examples: '5000k' -> 5000, '2.5m' -> 2500, '128' -> 128
    
    Returns default value if parsing fails.
    """
    try:
        bitrate_str = bitrate_str.lower().strip()
        if bitrate_str.endswith('m'):
            return int(float(bitrate_str[:-1]) * 1000)
        if bitrate_str.endswith('k'):
            return int(float(bitrate_str[:-1]))
        return int(bitrate_str)
    except (ValueError, AttributeError) as e:
        logger.warning(f'Invalid bitrate "{bitrate_str}", using default {default}k: {e}')
        return default


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
