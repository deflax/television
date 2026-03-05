"""S3/MinIO FUSE mount management for the replay service.

When S3_ENABLED=true, mounts a MinIO bucket to the library directory using s3fs-fuse.
The bucket root is the library itself — each subdirectory in the bucket is a channel.
This makes S3 objects appear as regular files, so ffmpeg and ffprobe work unchanged.
"""

import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from config import (
    logger,
    S3_ENABLED,
    S3_ENDPOINT,
    S3_ACCESS_KEY,
    S3_SECRET_KEY,
    S3_BUCKET,
    S3_MOUNT_OPTIONS,
    LIBRARY_DIR,
)

# s3fs password file location
_PASSWD_FILE = '/tmp/.s3fs-passwd'


def _write_credentials() -> None:
    """Write s3fs credentials file."""
    with open(_PASSWD_FILE, 'w') as f:
        f.write(f'{S3_ACCESS_KEY}:{S3_SECRET_KEY}')
    os.chmod(_PASSWD_FILE, 0o600)


def _parse_endpoint() -> tuple[str, bool]:
    """Parse S3 endpoint into URL and whether to use path-style access.

    Returns:
        (endpoint_url, use_path_style): endpoint for s3fs and whether to use
        path-style requests (always True for MinIO).
    """
    parsed = urlparse(S3_ENDPOINT)
    use_ssl = parsed.scheme == 'https'
    return S3_ENDPOINT, not use_ssl


def _mount_bucket(bucket: str, mount_point: str) -> bool:
    """Mount an S3 bucket to a local directory using s3fs.

    Returns True on success, False on failure.
    """
    mount_path = Path(mount_point)
    mount_path.mkdir(parents=True, exist_ok=True)

    # Check if already mounted
    try:
        result = subprocess.run(
            ['mountpoint', '-q', mount_point],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            logger.info(f"S3 bucket '{bucket}' already mounted at {mount_point}")
            return True
    except FileNotFoundError:
        pass

    # Build s3fs mount command
    endpoint_url, no_ssl = _parse_endpoint()

    cmd = [
        's3fs',
        bucket,
        mount_point,
        '-o', f'passwd_file={_PASSWD_FILE}',
        '-o', f'url={endpoint_url}',
        '-o', 'use_path_request_style',
        '-o', 'allow_other',
        '-o', 'ro',
        '-o', 'umask=0222',
    ]

    if no_ssl:
        cmd.extend(['-o', 'no_check_certificate'])
        cmd.extend(['-o', 'use_cache=/tmp/s3fs_cache'])

    # Add any extra user-specified mount options
    if S3_MOUNT_OPTIONS:
        for opt in S3_MOUNT_OPTIONS.split(','):
            opt = opt.strip()
            if opt:
                cmd.extend(['-o', opt])

    logger.info(f"Mounting S3 bucket '{bucket}' at {mount_point}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            logger.error(f"Failed to mount S3 bucket '{bucket}': {stderr}")
            return False

        # Verify mount is accessible
        time.sleep(1)
        if mount_path.is_dir():
            try:
                list(mount_path.iterdir())
                logger.info(f"S3 bucket '{bucket}' mounted successfully at {mount_point}")
                return True
            except OSError as e:
                logger.error(f"S3 mount at {mount_point} is not accessible: {e}")
                return False

        logger.error(f"S3 mount at {mount_point} failed — directory not accessible")
        return False

    except subprocess.TimeoutExpired:
        logger.error(f"S3 mount of bucket '{bucket}' timed out")
        return False
    except Exception as e:
        logger.error(f"S3 mount error for bucket '{bucket}': {e}")
        return False


def _unmount(mount_point: str) -> None:
    """Unmount an s3fs mount point."""
    try:
        subprocess.run(
            ['fusermount', '-uz', mount_point],
            capture_output=True,
            timeout=10,
            check=False,
        )
        logger.info(f"Unmounted {mount_point}")
    except Exception as e:
        logger.warning(f"Failed to unmount {mount_point}: {e}")


def mount_s3_buckets() -> bool:
    """Mount the S3 library bucket. Returns True on success."""
    if not S3_ENABLED:
        logger.info("S3 storage disabled, using local filesystem")
        return True

    # Validate configuration
    missing = []
    if not S3_ENDPOINT:
        missing.append('S3_ENDPOINT')
    if not S3_ACCESS_KEY:
        missing.append('S3_ACCESS_KEY')
    if not S3_SECRET_KEY:
        missing.append('S3_SECRET_KEY')

    if missing:
        logger.error(f"S3 enabled but missing required config: {', '.join(missing)}")
        return False

    # Check s3fs is available
    try:
        subprocess.run(['s3fs', '--version'], capture_output=True, check=True, timeout=5)
    except (FileNotFoundError, subprocess.CalledProcessError):
        logger.error("s3fs-fuse is not installed \u2014 cannot mount S3 bucket")
        return False

    _write_credentials()

    # Mount the library bucket (root = library, subdirs = channels)
    if not _mount_bucket(S3_BUCKET, LIBRARY_DIR):
        logger.error(f"Failed to mount library bucket '{S3_BUCKET}' at {LIBRARY_DIR}")
        return False

    return True


def unmount_s3_buckets() -> None:
    """Unmount S3 bucket on shutdown."""
    if not S3_ENABLED:
        return

    _unmount(LIBRARY_DIR)

    # Clean up credentials
    try:
        os.unlink(_PASSWD_FILE)
    except OSError:
        pass
