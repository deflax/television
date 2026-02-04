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

# Maximum segments to keep in memory per variant (prevents unbounded growth)
MAX_SEGMENTS_IN_MEMORY = HLS_LIST_SIZE * 3


@dataclass
class Segment:
    """Represents a single HLS segment."""
    sequence: int
    variant: int
    filename: str
    duration: float
    discontinuity_before: bool = False
    # Discontinuity sequence number at this segment (for EXT-X-DISCONTINUITY-SEQUENCE)
    discontinuity_sequence: int = 0
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
        # Playlist sequence - continuous counter for EXT-X-MEDIA-SEQUENCE
        self._next_sequence = 0
        # FFmpeg file number - tracks highest segment number on disk to avoid collisions
        self._next_file_number = 0
        self._pending_discontinuity = False
        # Count of discontinuities for EXT-X-DISCONTINUITY-SEQUENCE
        self._discontinuity_count = 0
        # Source stream properties (detected via ffprobe)
        self._source_width: int = 1920
        self._source_height: int = 1080
        self._source_bitrate: int = 8000000
    
    async def set_source_info(self, width: int, height: int, bitrate: int) -> None:
        """Update the source stream properties for master playlist generation."""
        async with self._lock:
            self._source_width = width
            self._source_height = height
            self._source_bitrate = bitrate
            logger.info(f'Source stream info updated: {width}x{height} @ {bitrate // 1000}kbps')
    
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
            # FFmpeg's start_number determines the filename sequence, and we use
            # that same sequence for the playlist's MEDIA-SEQUENCE.
            match = re.search(r'segment_(\d+)\.ts$', filename)
            if match:
                seq = int(match.group(1))
            else:
                # Fallback to internal counter
                seq = self._next_sequence
            
            # Track for next FFmpeg start_number (continue from highest seen + 1)
            if seq >= self._next_sequence:
                self._next_sequence = seq + 1
            
            # Also track highest file number separately for collision avoidance
            if seq >= self._next_file_number:
                self._next_file_number = seq + 1
            
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
                discontinuity_sequence=self._discontinuity_count,
            )
            
            self._segments[variant].append(segment)
            
            # Prevent unbounded memory growth
            if len(self._segments[variant]) > MAX_SEGMENTS_IN_MEMORY:
                excess = self._segments[variant][:-MAX_SEGMENTS_IN_MEMORY]
                self._segments[variant] = self._segments[variant][-MAX_SEGMENTS_IN_MEMORY:]
                # Clean up files for excess segments
                for old_seg in excess:
                    self._delete_segment_file(old_seg)
                logger.debug(f'Trimmed {len(excess)} excess segments from variant {variant}')
            
            logger.debug(f'Added segment: variant={variant} seq={segment.sequence} file={filename}')
            return segment
    
    async def mark_discontinuity(self) -> None:
        """Mark that the next segment should have a discontinuity tag."""
        async with self._lock:
            self._pending_discontinuity = True
            self._discontinuity_count += 1
            logger.info(f'Discontinuity marked for next segment (count: {self._discontinuity_count})')
    
    async def get_segments(self, variant: int, count: Optional[int] = None) -> list[Segment]:
        """Get segments for a variant, optionally limited to most recent count."""
        async with self._lock:
            segments = self._segments.get(variant, [])
            if count is not None:
                return segments[-count:]
            return segments.copy()
    
    async def get_next_sequence(self) -> int:
        """Get the next sequence number for FFmpeg's start_number parameter.
        
        This returns the highest segment sequence seen + 1, ensuring
        continuous numbering across stream switches.
        """
        async with self._lock:
            return self._next_sequence
    
    def _delete_segment_file(self, seg: Segment) -> bool:
        """Delete a segment's file from disk. Returns True if deleted."""
        try:
            seg.path.unlink(missing_ok=True)
            return True
        except Exception as e:
            logger.warning(f'Failed to delete segment {seg.path}: {e}')
            return False
    
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
                    if self._delete_segment_file(seg):
                        removed += 1
        
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
        
        # Get discontinuity sequence from first segment
        # This represents how many discontinuities occurred before this playlist window
        first_seg = playlist_segments[0]
        disc_seq = first_seg.discontinuity_sequence
        if first_seg.discontinuity_before:
            # If first segment has a discontinuity, the sequence should be one less
            # because the discontinuity is "at" this segment, not before it
            disc_seq = max(0, disc_seq - 1)
        
        lines = [
            '#EXTM3U',
            '#EXT-X-VERSION:3',
            f'#EXT-X-TARGETDURATION:{target_duration}',
            f'#EXT-X-MEDIA-SEQUENCE:{media_sequence}',
            f'#EXT-X-DISCONTINUITY-SEQUENCE:{disc_seq}',
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
        
        async with self._lock:
            source_width = self._source_width
            source_height = self._source_height
            source_bitrate = self._source_bitrate
        
        lines = [
            '#EXTM3U',
            '#EXT-X-VERSION:3',
        ]
        
        # Stream 0 is source copy (highest quality) - use detected values
        lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH={source_bitrate},RESOLUTION={source_width}x{source_height}')
        lines.append('stream_0/playlist.m3u8')
        
        # ABR variants
        for i, variant in enumerate(ABR_VARIANTS):
            bandwidth = parse_bitrate(variant['video_bitrate']) * 1000
            bandwidth += parse_bitrate(variant['audio_bitrate']) * 1000
            height = variant['height']
            # Calculate width maintaining source aspect ratio
            aspect_ratio = source_width / source_height if source_height > 0 else 16 / 9
            width = int(height * aspect_ratio)
            # Ensure width is even (required for most codecs)
            width = width - (width % 2)
            
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
