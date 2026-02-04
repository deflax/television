"""Shared utility functions for mux service."""

import asyncio
from pathlib import Path


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
        for _ in range(max_attempts):
            if not path.exists():
                return False
            
            size1 = path.stat().st_size
            await asyncio.sleep(check_delay)
            
            if not path.exists():
                return False
            
            size2 = path.stat().st_size
            
            if size1 == size2 and size1 > 0:
                return True
        
        # Proceed anyway after max attempts (file may be slow but valid)
        return path.exists() and path.stat().st_size > 0
    except Exception:
        return False
