"""Clocked preview of submitted audio, independent of bursty device writes.

The estimated presentation clock uses reported output latency, not calibrated
acoustic latency. Samples are copied verbatim; no synthesis or interpolation.
"""
from collections import deque
import math
import time
import numpy as np


class PlaybackHistory:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.reset(48000, .1)

    def reset(self, sample_rate, latency):
        self.sample_rate = sample_rate
        self.latency = max(0., float(latency))
        self.origin = None
        # Retain latency plus ample margin beyond the 40 ms visible trace.
        self.blocks = deque(maxlen=max(64, math.ceil((self.latency+.25)*sample_rate/512)))

    def publish(self, start_frame, samples, submitted_at, underflow=False):
        if self.origin is None or underflow:
            self.origin = submitted_at+self.latency-start_frame/self.sample_rate
        self.blocks.append((start_frame, samples))

    def snapshot(self, count):
        blocks = tuple(self.blocks)
        result = np.zeros((count, 2), dtype=np.float32)
        if not blocks or self.origin is None:
            return result, 0
        submitted_end = blocks[-1][0]+len(blocks[-1][1])
        cursor = min(submitted_end, max(0, int((self.clock()-self.origin)*self.sample_rate)))
        begin = cursor-count
        for start, samples in reversed(blocks):
            end = start+len(samples)
            if end <= begin: break
            low, high = max(start,begin), min(end,cursor)
            if high > low:
                result[low-begin:high-begin] = samples[low-start:high-start]
        return result, cursor
