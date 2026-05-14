"""Frame iteration across one or more AVI files in a recording session.

Yields (real_time, frame_index, frame_bgr) for every frame across all
source files, treating them as a single continuous timeline.

Uses PyAV (libav bindings) because OpenCV's MJPEG AVI decoder fails on
the CY50's output.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

import av
import numpy as np


@dataclass
class FrameRef:
    """A single frame in the recording timeline."""

    real_time: datetime         # Wall-clock time of the captured photo
    global_index: int           # Index across all source files combined
    file_index: int             # Index within the current source file
    source_path: Path           # AVI file the frame came from
    frame: np.ndarray           # H x W x 3 BGR image (OpenCV convention)


def _frame_to_bgr(av_frame) -> np.ndarray:
    """Convert a PyAV VideoFrame to a BGR ndarray (matches cv2 convention)."""
    # to_ndarray gives RGB by default; we use bgr24 to match OpenCV.
    return av_frame.to_ndarray(format="bgr24")


class FrameIterator:
    """Iterate every frame across multiple AVI source files.

    The CY50 stores time-lapse output as MJPEG-encoded AVI at the SAME
    framerate as the captured interval (5 fps for our 5-second interval),
    so `frame_index * interval_secs` recovers real-world elapsed time
    from the start of the first file.

    Args:
        source_files: ordered list of AVI file paths (chronological)
        anchor_time: real wall-clock time at which the first frame was captured
        interval_secs: seconds between consecutive captured frames
        clock_offset_secs: camera_time - real_time. Anchor_time is assumed to
            already account for this, but stored on each FrameRef for reference.
    """

    def __init__(self, source_files: list[Path], anchor_time: datetime,
                 interval_secs: float = 5.0, clock_offset_secs: float = 0.0):
        if not source_files:
            raise ValueError("source_files must not be empty")
        self.source_files = [Path(p) for p in source_files]
        for p in self.source_files:
            if not p.exists():
                raise FileNotFoundError(f"AVI file not found: {p}")
        self.anchor_time = anchor_time
        self.interval_secs = interval_secs
        self.clock_offset_secs = clock_offset_secs
        # Lazy cache of frame counts per file
        self._counts: list[int] | None = None

    def _file_counts(self) -> list[int]:
        if self._counts is None:
            counts = []
            for p in self.source_files:
                container = av.open(str(p))
                try:
                    counts.append(container.streams.video[0].frames)
                finally:
                    container.close()
            self._counts = counts
        return self._counts

    def total_frames(self) -> int:
        """Sum of frame counts across every source file."""
        return sum(self._file_counts())

    def time_at(self, global_index: int) -> datetime:
        """Compute wall-clock time for a given global frame index."""
        return self.anchor_time + timedelta(seconds=global_index * self.interval_secs)

    def __iter__(self) -> Iterator[FrameRef]:
        global_idx = 0
        for source_path in self.source_files:
            container = av.open(str(source_path))
            try:
                file_idx = 0
                for av_frame in container.decode(video=0):
                    yield FrameRef(
                        real_time=self.time_at(global_idx),
                        global_index=global_idx,
                        file_index=file_idx,
                        source_path=source_path,
                        frame=_frame_to_bgr(av_frame),
                    )
                    global_idx += 1
                    file_idx += 1
            finally:
                container.close()

    def sample(self, global_index: int) -> FrameRef | None:
        """Read a single frame by global index.

        For MJPEG AVI we can't seek reliably, so we decode-and-discard
        until we reach the target. This is fine for occasional samples
        but should not be used in tight loops — use __iter__ for bulk work.
        """
        counts = self._file_counts()
        cumulative = 0
        for source_path, count in zip(self.source_files, counts):
            if global_index < cumulative + count:
                file_target = global_index - cumulative
                container = av.open(str(source_path))
                try:
                    for i, av_frame in enumerate(container.decode(video=0)):
                        if i == file_target:
                            return FrameRef(
                                real_time=self.time_at(global_index),
                                global_index=global_index,
                                file_index=file_target,
                                source_path=source_path,
                                frame=_frame_to_bgr(av_frame),
                            )
                finally:
                    container.close()
                return None
            cumulative += count
        return None
