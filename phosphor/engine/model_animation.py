"""Audio-clock clip timing and portable, bounded baked model positions."""
import base64
from dataclasses import dataclass
from functools import cached_property
import math
import zlib

import numpy as np

MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_FRAMES = 18001
MODES = ('Loop', 'Ping-pong', 'Hold')


def clip_time(t, duration, speed=1., beats=0., tempo=120., trim_in=0., trim_out=None, mode='Loop'):
    values = (t, duration, speed, beats, tempo, trim_in)
    if not all(math.isfinite(v) for v in values) or duration < 0 or beats < 0 or tempo <= 0:
        raise ValueError('Invalid animation timing.')
    if trim_out is not None and not math.isfinite(trim_out):
        raise ValueError('Invalid animation trim.')
    if mode not in MODES:
        raise ValueError('Invalid animation mode.')
    end = duration if trim_out is None else min(duration, max(0., trim_out))
    start = min(end, max(0., trim_in))
    span = end - start
    if span <= 0: return start
    period = 2 * span if mode == 'Ping-pong' else span
    elapsed = t * (period * tempo / (60. * beats) if beats > 0 else speed)
    if mode == 'Hold': return start + min(span, max(0., elapsed))
    if mode == 'Ping-pong':
        elapsed %= 2 * span
        return start + (elapsed if elapsed <= span else 2 * span - elapsed)
    return start + elapsed % span


@dataclass(frozen=True)
class Playback:
    speed: float = 1.
    beats: float = 0.
    mode: str = 'Loop'
    trim_in: float = 0.
    trim_out: float | None = None

    def validate(self):
        clip_time(0., 1., **self.__dict__)
        if self.trim_out is not None and not math.isfinite(self.trim_out):
            raise ValueError('Invalid animation trim.')


@dataclass(frozen=True)
class ModelAnimation:
    fps: float
    duration: float
    count: int
    vertex_count: int
    box: tuple
    data: bytes

    def __reduce__(self):
        return type(self), (self.fps, self.duration, self.count, self.vertex_count, self.box, self.data)

    @classmethod
    def from_frames(cls, frames, fps, duration):
        frames = np.asarray(frames, dtype=float)
        if frames.ndim != 3 or frames.shape[2] != 3 or not np.isfinite(frames).all():
            raise ValueError('Invalid animation positions.')
        if frames.size*2 > MAX_FRAME_BYTES or not 2 <= len(frames) <= MAX_FRAMES:
            raise ValueError('Embedded animation exceeds the 16 MB or frame-count limit.')
        low, high = frames.min(axis=(0, 1)), frames.max(axis=(0, 1))
        step = np.where(high > low, (high-low)/65535., 1.)
        q = np.rint((frames-low)/step).astype('<u2')
        delta = q.copy()
        delta[1:] = q[1:] - q[:-1]  # Unsigned modular deltas, explicitly little endian.
        result = cls(float(fps), float(duration), len(frames), frames.shape[1],
                     (tuple(low), tuple(high)), zlib.compress(delta.tobytes(), 9))
        result.validate()
        return result

    def validate(self):
        size = self.count * self.vertex_count * 3 * 2
        if (type(self.count) is not int or type(self.vertex_count) is not int
                or not 2 <= self.count <= MAX_FRAMES or self.vertex_count < 2
                or size > MAX_FRAME_BYTES or len(self.data) > MAX_FRAME_BYTES):
            raise ValueError('Embedded animation exceeds the 16 MB or frame-count limit.')
        if not math.isfinite(self.fps) or self.fps <= 0 or not math.isfinite(self.duration) or self.duration <= 0:
            raise ValueError('Invalid animation duration or frame rate.')
        if self.count != int(np.ceil(self.duration*self.fps)) + 1:
            raise ValueError('Animation frame count does not match its duration.')
        box = np.asarray(self.box)
        if box.shape != (2, 3) or not np.isfinite(box).all() or np.any(box[1] < box[0]) or np.max(np.abs(box)) > 1.001:
            raise ValueError('Invalid normalized animation bounds.')
        self.frames

    @cached_property
    def frames(self):
        size = self.count * self.vertex_count * 6
        if size > MAX_FRAME_BYTES or size < 0:
            raise ValueError('Embedded animation exceeds the 16 MB limit.')
        try:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(self.data, size + 1)
            if len(raw) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError('Invalid compressed animation size.')
            delta = np.frombuffer(raw, dtype='<u2').reshape(self.count, self.vertex_count, 3)
            q = np.cumsum(delta, axis=0, dtype=np.uint64) & 65535
            low, high = np.asarray(self.box)
            frames = low + q * ((high-low)/65535.)
            # Backed by immutable bytes: callers cannot re-enable writes.
            return np.frombuffer(frames.astype('<f8').tobytes(), dtype='<f8').reshape(frames.shape)
        except zlib.error as error:
            raise ValueError('Invalid compressed animation.') from error

    def at(self, t, playback=Playback(), tempo=120.):
        if hasattr(tempo, 'beats'):
            if playback.beats > 0: t = float(tempo.beats(t))
            tempo = 60.  # Unlocked playback stays in seconds through tempo changes.
        seconds = clip_time(t, self.duration, playback.speed, playback.beats, tempo,
                            playback.trim_in, playback.trim_out, playback.mode)
        index = min(seconds * self.fps, self.count - 1.)
        last_start = (self.count-2)/self.fps
        if seconds > last_start and self.duration > last_start:
            index = self.count-2 + (seconds-last_start)/(self.duration-last_start)
        i = int(index)
        a = index - i
        return (1-a)*self.frames[i] + a*self.frames[min(i+1, self.count-1)]

    def document(self):
        return dict(fps=self.fps, duration=self.duration, count=self.count,
                    vertex_count=self.vertex_count, box=[list(v) for v in self.box],
                    data=base64.b64encode(self.data).decode('ascii'))

    @classmethod
    def read(cls, value):
        try:
            if len(value['data']) > (MAX_FRAME_BYTES * 4 // 3 + 4):
                raise ValueError('Embedded animation exceeds the 16 MB limit.')
            result = cls(value['fps'], value['duration'], value['count'], value['vertex_count'],
                         tuple(tuple(v) for v in value['box']), base64.b64decode(value['data'], validate=True))
            result.validate()
            return result
        except (KeyError, TypeError) as error:
            raise ValueError('Invalid embedded animation.') from error
