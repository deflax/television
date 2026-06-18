"""Configuration module for mux service."""

import os
import json
import logging
from typing import Callable, TypeVar, TypedDict, cast

logger = logging.getLogger(__name__)

T = TypeVar('T', int, float)


class ABRVariant(TypedDict):
    width: int
    height: int
    video_bitrate: str
    audio_bitrate: str


def _parse_env(
    name: str,
    default: T,
    type_fn: Callable[[str], T],
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


def _parse_optional_env(
    name: str,
    type_fn: Callable[[str], T],
    min_val: T | None = None,
    max_val: T | None = None,
) -> T | None:
    raw = os.environ.get(name, '')
    if not raw:
        return None

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
        logger.warning(f'Invalid {name}={raw!r}: {e}, ignoring')
        return None


def _resolve_segment_retention_seconds() -> int:
    configured = _parse_optional_env(
        'HLS_SEGMENT_RETENTION_SECONDS',
        int,
        min_val=HLS_SEGMENT_TIME,
        max_val=86400,
    )
    if configured is None:
        return DEFAULT_SEGMENT_RETENTION_SECONDS
    if configured < MIN_SEGMENT_RETENTION_SECONDS:
        logger.warning(
            'HLS_SEGMENT_RETENTION_SECONDS=%s is shorter than segment cache lifetime %ss; using %ss',
            configured,
            MIN_SEGMENT_RETENTION_SECONDS,
            MIN_SEGMENT_RETENTION_SECONDS,
        )
    return max(configured, MIN_SEGMENT_RETENTION_SECONDS)


# API connection
API_URL = os.environ.get('API_URL', 'http://api:8080')

# HLS output configuration
HLS_OUTPUT_DIR = '/tmp/hls'
HLS_SEGMENT_TIME = _parse_env('HLS_SEGMENT_TIME', 4, int, min_val=1, max_val=60)
HLS_LIST_SIZE = _parse_env('HLS_LIST_SIZE', 20, int, min_val=3, max_val=100)
HLS_VIEWER_TTL = _parse_env('HLS_VIEWER_TTL', 90.0, float, min_val=30.0, max_val=600.0)
HLS_SEGMENT_CACHE_MAX_AGE = _parse_env('HLS_SEGMENT_CACHE_MAX_AGE', 300, int, min_val=0, max_val=3600)
HLS_SEGMENT_CACHE_STALE_REVALIDATE = _parse_env(
    'HLS_SEGMENT_CACHE_STALE_REVALIDATE',
    60,
    int,
    min_val=0,
    max_val=3600,
)

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
ABR_THREADS = _parse_env('ABR_THREADS', 2, int, min_val=0, max_val=64)

# ABR variants configuration
DEFAULT_ABR_VARIANTS: list[ABRVariant] = [
    {"width": 1280, "height": 720, "video_bitrate": "1500k", "audio_bitrate": "128k"},
]


def parse_abr_variants() -> list[ABRVariant]:
    """Parse ABR_VARIANTS from environment or use defaults."""
    variants_json = os.environ.get('ABR_VARIANTS', '')
    if variants_json:
        try:
            decoded = cast(object, json.loads(variants_json))
            if not isinstance(decoded, list):
                raise ValueError('ABR_VARIANTS must be a non-empty list')

            decoded_items = cast(list[object], decoded)
            if len(decoded_items) == 0:
                raise ValueError('ABR_VARIANTS must be a non-empty list')

            variants: list[ABRVariant] = []
            for i, item in enumerate(decoded_items):
                if not isinstance(item, dict):
                    raise ValueError(f'Variant {i} is not an object')

                item_map = cast(dict[object, object], item)
                width = item_map.get('width')
                height = item_map.get('height')
                video_bitrate = item_map.get('video_bitrate')
                audio_bitrate = item_map.get('audio_bitrate')

                if not isinstance(width, int):
                    raise ValueError(f'Variant {i} width must be an integer')
                if not isinstance(height, int):
                    raise ValueError(f'Variant {i} height must be an integer')
                if not isinstance(video_bitrate, str):
                    raise ValueError(f'Variant {i} video_bitrate must be a string')
                if not isinstance(audio_bitrate, str):
                    raise ValueError(f'Variant {i} audio_bitrate must be a string')

                variants.append({
                    'width': width,
                    'height': height,
                    'video_bitrate': video_bitrate,
                    'audio_bitrate': audio_bitrate,
                })

            logger.info(f"Using custom ABR variants: {variants}")
            return variants
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Invalid ABR_VARIANTS, using defaults: {e}")
    return DEFAULT_ABR_VARIANTS


ABR_VARIANTS = parse_abr_variants()


# Logging
_LOG_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARN': logging.WARNING,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
}
_raw_log_level = os.environ.get('MUX_LOG_LEVEL', 'INFO').upper()
if _raw_log_level not in _LOG_LEVELS:
    logger.warning(f'Invalid MUX_LOG_LEVEL={_raw_log_level!r}, using INFO')
    _raw_log_level = 'INFO'
LOG_LEVEL = _LOG_LEVELS[_raw_log_level]

# Transition settings
TRANSITION_TIMEOUT = _parse_env('TRANSITION_TIMEOUT', 15.0, float, min_val=1.0, max_val=120.0)
SEGMENT_STABILITY_DELAY = 0.1

# Derived values
NUM_VARIANTS = len(ABR_VARIANTS) + 1 if MUX_MODE == 'abr' else 1
DEFAULT_SEGMENT_RETENTION_SECONDS = max(
    HLS_LIST_SIZE * HLS_SEGMENT_TIME * 3,
    HLS_LIST_SIZE * HLS_SEGMENT_TIME + HLS_SEGMENT_CACHE_MAX_AGE + HLS_SEGMENT_CACHE_STALE_REVALIDATE,
)
MIN_SEGMENT_RETENTION_SECONDS = (
    HLS_LIST_SIZE * HLS_SEGMENT_TIME
    + HLS_SEGMENT_CACHE_MAX_AGE
    + HLS_SEGMENT_CACHE_STALE_REVALIDATE
)
MAX_SEGMENT_AGE = _resolve_segment_retention_seconds()
SEGMENT_CACHE_CONTROL = (
    f'public, max-age={HLS_SEGMENT_CACHE_MAX_AGE}, '
    f'stale-while-revalidate={HLS_SEGMENT_CACHE_STALE_REVALIDATE}'
)


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
