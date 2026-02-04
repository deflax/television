"""Shared utility functions for mux service."""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def wait_for_stable_file(
    path: Path,
    check_delay: float = 0.1,
    max_attempts: int = 10,
) -> bool:
    """Wait until file exists and size stops changing.
    
    Args:
        path: Path to the file to check
        check_delay: Delay between size checks in seconds
        max_attempts: Maximum number of stability checks
        
    Returns:
        True if file is stable, False if file doesn't exist or never stabilized
    """
    try:
        for attempt in range(max_attempts):
            if not path.exists():
                logger.debug(f'File does not exist: {path.name}')
                return False
            
            size1 = path.stat().st_size
            await asyncio.sleep(check_delay)
            
            if not path.exists():
                logger.debug(f'File disappeared: {path.name}')
                return False
            
            size2 = path.stat().st_size
            
            if size1 == size2 and size1 > 0:
                return True
            
            # Log progress for debugging slow files
            if attempt > 0 and attempt % 5 == 0:
                logger.debug(f'Waiting for file stability: {path.name} size={size2} attempt={attempt}')
        
        # Proceed anyway after max attempts (file may be slow but valid)
        final_exists = path.exists()
        final_size = path.stat().st_size if final_exists else 0
        if final_exists and final_size > 0:
            logger.debug(f'File not fully stable but proceeding: {path.name} size={final_size}')
            return True
        
        logger.warning(f'File never stabilized: {path.name} exists={final_exists} size={final_size}')
        return False
    except Exception as e:
        logger.error(f'Error checking file stability: {path.name} - {e}')
        return False
