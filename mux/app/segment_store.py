"""Segment store - tracks HLS segments with metadata.

The segment store is the source of truth for what segments exist and their
relationships. It handles:
- Tracking segments across stream switches
- Marking discontinuity points
- Generating consistent playlists
- Cleaning up old segments
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import HLS_OUTPUT_DIR, HLS_LIST_SIZE, HLS_SEGMENT_TIME, MAX_SEGMENT_AGE, NUM_VARIANTS, parse_bitrate

logger = logging.getLogger(__name__)


@dataclass
class Segment:
    """Represents a single HLS segment."""
    sequence: int
    variant: int
    filename: str
    duration: float
    discontinuity_before: bool = False
    created_at: float = field(default_factory=time.time)
    
    @property
    def path(self) -> Path:
        """Full path to the segment file."""
        if NUM_VARIANTS > 1:
            return Path(HLS_OUTPUT_DIR) / f'stream_{self.variant}' / self.filename
        return Path(HLS_OUTPUT_DIR) / self.filename
    
    @property
    def age(self) -> float:
        """Age of segment in seconds."""
        return time.time() - self.created_at


class SegmentStore:
    """Thread-safe store for HLS segments.
    
    Maintains an ordered list of segments per variant and handles
    discontinuity markers for stream switches.
    """
    
    def __init__(self):
        self._lock = asyncio.Lock()
        # segments[variant_index] = list of Segment objects
        self._segments: dict[int, list[Segment]] = {i: [] for i in range(NUM_VARIANTS)}
        self._next_sequence = 0
        self._pending_discontinuity = False
    
    async def add_segment(
        self,
        variant: int,
        filename: str,
        duration: float,
    ) -> Segment:
        """Add a new segment to the store.
        
        Returns the created Segment object.
        """
        async with self._lock:
            # Extract sequence number from filename (segment_00001.ts -> 1)
            match = re.search(r'segment_(\d+)\.ts$', filename)
            if match:
                seq = int(match.group(1))
            else:
                # Fallback to internal counter
                seq = self._next_sequence
            
            # Update next_sequence to be at least seq + 1
            if seq >= self._next_sequence:
                self._next_sequence = seq + 1
            
            # Determine discontinuity - apply to first segment at this sequence
            discontinuity = False
            if self._pending_discontinuity:
                # Check if we already have a segment at this sequence
                existing_seg = None
                for segs in self._segments.values():
                    for seg in segs:
                        if seg.sequence == seq:
                            existing_seg = seg
                            break
                    if existing_seg:
                        break
                
                if existing_seg is None:
                    # First segment at this sequence gets the discontinuity
                    discontinuity = True
                    logger.info(f'Added discontinuity before segment {seq}')
                    self._pending_discontinuity = False
                else:
                    # Copy discontinuity flag from existing segment at this sequence
                    discontinuity = existing_seg.discontinuity_before
            
            segment = Segment(
                sequence=seq,
                variant=variant,
                filename=filename,
                duration=duration,
                discontinuity_before=discontinuity,
            )
            
            self._segments[variant].append(segment)
            
            logger.debug(f'Added segment: variant={variant} seq={segment.sequence} file={filename}')
            return segment
    
    async def mark_discontinuity(self) -> None:
        """Mark that the next segment should have a discontinuity tag."""
        async with self._lock:
            self._pending_discontinuity = True
            logger.info('Discontinuity marked for next segment')
    
    async def get_segments(self, variant: int, count: Optional[int] = None) -> list[Segment]:
        """Get segments for a variant, optionally limited to most recent count."""
        async with self._lock:
            segments = self._segments.get(variant, [])
            if count is not None:
                return segments[-count:]
            return segments.copy()
    
    async def get_next_sequence(self) -> int:
        """Get the next sequence number that will be assigned."""
        async with self._lock:
            return self._next_sequence
    
    async def cleanup_old_segments(self) -> int:
        """Remove segments older than MAX_SEGMENT_AGE.
        
        Returns the number of segments removed.
        """
        removed = 0
        async with self._lock:
            for variant in self._segments:
                old_segments = []
                new_segments = []
                
                for seg in self._segments[variant]:
                    if seg.age > MAX_SEGMENT_AGE:
                        old_segments.append(seg)
                    else:
                        new_segments.append(seg)
                
                self._segments[variant] = new_segments
                
                # Delete files for old segments
                for seg in old_segments:
                    try:
                        if seg.path.exists():
                            seg.path.unlink()
                            removed += 1
                    except Exception as e:
                        logger.warning(f'Failed to delete segment {seg.path}: {e}')
        
        if removed > 0:
            logger.debug(f'Cleaned up {removed} old segments')
        return removed
    
    async def generate_playlist(self, variant: int) -> str:
        """Generate an HLS playlist for the given variant."""
        async with self._lock:
            return self._generate_playlist_unlocked(variant)
    
    def _generate_playlist_unlocked(self, variant: int) -> str:
        """Generate playlist without acquiring lock (caller must hold lock)."""
        segments = self._segments.get(variant, [])
        
        # Get most recent segments up to list size
        playlist_segments = segments[-HLS_LIST_SIZE:] if segments else []
        
        if not playlist_segments:
            # Return minimal valid playlist
            return (
                '#EXTM3U\n'
                '#EXT-X-VERSION:3\n'
                f'#EXT-X-TARGETDURATION:{HLS_SEGMENT_TIME}\n'
                '#EXT-X-MEDIA-SEQUENCE:0\n'
            )
        
        media_sequence = playlist_segments[0].sequence
        max_duration = max(seg.duration for seg in playlist_segments)
        target_duration = int(max_duration) + 1
        
        lines = [
            '#EXTM3U',
            '#EXT-X-VERSION:3',
            f'#EXT-X-TARGETDURATION:{target_duration}',
            f'#EXT-X-MEDIA-SEQUENCE:{media_sequence}',
        ]
        
        for seg in playlist_segments:
            if seg.discontinuity_before:
                lines.append('#EXT-X-DISCONTINUITY')
            lines.append(f'#EXTINF:{seg.duration:.3f},')
            lines.append(seg.filename)
        
        return '\n'.join(lines) + '\n'
    
    async def generate_master_playlist(self) -> str:
        """Generate the master playlist for ABR mode."""
        from config import ABR_VARIANTS, MUX_MODE
        
        if MUX_MODE != 'abr':
            # In copy mode, generate playlist directly (no master needed)
            # This shouldn't normally be called in copy mode
            async with self._lock:
                return self._generate_playlist_unlocked(0)
        
        lines = [
            '#EXTM3U',
            '#EXT-X-VERSION:3',
        ]
        
        # Stream 0 is source copy (highest quality)
        lines.append('#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080')
        lines.append('stream_0/playlist.m3u8')
        
        # ABR variants
        for i, variant in enumerate(ABR_VARIANTS):
            bandwidth = parse_bitrate(variant['video_bitrate']) * 1000
            bandwidth += parse_bitrate(variant['audio_bitrate']) * 1000
            height = variant['height']
            width = int(height * 16 / 9)  # Assume 16:9
            
            lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={width}x{height}')
            lines.append(f'stream_{i + 1}/playlist.m3u8')
        
        return '\n'.join(lines) + '\n'



def setup_output_dirs() -> None:
    """Create the HLS output directory structure."""
    os.makedirs(HLS_OUTPUT_DIR, exist_ok=True)
    
    if NUM_VARIANTS > 1:
        for i in range(NUM_VARIANTS):
            os.makedirs(f'{HLS_OUTPUT_DIR}/stream_{i}', exist_ok=True)
    
    logger.info(f'Output directory ready: {HLS_OUTPUT_DIR}')


# Global segment store instance
segment_store = SegmentStore()
