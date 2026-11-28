"""Quarter-note tempo maps and bar-based meters, independent of Qt."""
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
import math
import numpy as np


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def validate_changes(changes, meter=False):
    if not isinstance(changes, tuple) or len(changes) > 256:
        raise ValueError('Use at most 256 tempo or meter changes.')
    previous = -1 if meter else 0
    for entry in changes:
        if not isinstance(entry, tuple) or len(entry) != (3 if meter else 2):
            raise ValueError('Invalid tempo or meter change.')
        position = entry[0]
        if not _number(position) or not previous < position <= 100000:
            raise ValueError('Changes must be strictly ordered within the beat limit.')
        if meter:
            if type(position) is not int or type(entry[1]) is not int or not 1 <= entry[1] <= 16 or type(entry[2]) is not int or entry[2] not in (2, 4, 8, 16):
                raise ValueError('Invalid time signature.')
        elif not _number(entry[1]) or not 20 <= entry[1] <= 400:
            raise ValueError('Tempo must be 20–400 BPM.')
        previous = position


@dataclass(frozen=True)
class TempoMap:
    bpm: float
    changes: tuple = ()

    def __post_init__(self):
        if not _number(self.bpm) or not 20 <= self.bpm <= 400:
            raise ValueError('Tempo must be 20–400 BPM.')
        validate_changes(self.changes)

    def seconds(self, beats):
        if isinstance(beats, (int, float, np.floating, np.integer)):
            b = float(beats)
            if not self.changes:
                return b * 60. / self.bpm
            _, _, _, starts_t, seconds_t, rates_t = _segments(self.bpm, self.changes)
            i = bisect_right(starts_t, b) - 1
            if i < 0: i = 0
            return seconds_t[i] + (b - starts_t[i]) * rates_t[i]
        if not self.changes:
            return np.asarray(beats, dtype=float) * 60. / self.bpm
        starts, seconds, rates = _segments(self.bpm, self.changes)[:3]
        values = np.asarray(beats, dtype=float)
        index = np.maximum(0, np.searchsorted(starts, values, side='right') - 1)
        return seconds[index] + (values - starts[index]) * rates[index]

    def beats(self, seconds):
        if isinstance(seconds, (int, float, np.floating, np.integer)):
            s = float(seconds)
            if not self.changes:
                return s * self.bpm / 60.
            _, _, _, starts_t, boundaries_t, rates_t = _segments(self.bpm, self.changes)
            i = bisect_right(boundaries_t, s) - 1
            if i < 0: i = 0
            return starts_t[i] + (s - boundaries_t[i]) / rates_t[i]
        if not self.changes:
            return np.asarray(seconds, dtype=float) * self.bpm / 60.
        starts, boundaries, rates = _segments(self.bpm, self.changes)[:3]
        values = np.asarray(seconds, dtype=float)
        index = np.maximum(0, np.searchsorted(boundaries, values, side='right') - 1)
        return starts[index] + (values - boundaries[index]) / rates[index]


@lru_cache(maxsize=256)
def _segments(bpm, changes):
    starts = np.array([0., *(b for b, _ in changes)])
    rates = 60. / np.array([bpm, *(v for _, v in changes)])
    seconds = np.r_[0., np.cumsum(np.diff(starts) * rates[:-1])]
    for array in (starts, seconds, rates):
        array.setflags(write=False)
    return starts, seconds, rates, tuple(starts.tolist()), tuple(seconds.tolist()), tuple(rates.tolist())


@dataclass(frozen=True)
class BeatClock:
    tempo_map: TempoMap
    origin_beats: float = 0.
    beat_offset: float = 0.  # Automation lanes may start after the scene's time origin.

    def _origin_seconds(self):
        cached = self.__dict__.get('_origin_sec')
        if cached is None:
            cached = float(self.tempo_map.seconds(self.origin_beats))
            object.__setattr__(self, '_origin_sec', cached)
        return cached

    def beats(self, local_seconds):
        origin_sec = self._origin_seconds()
        if isinstance(local_seconds, (int, float, np.floating, np.integer)):
            return self.tempo_map.beats(origin_sec + float(local_seconds)) - self.origin_beats - self.beat_offset
        return self.tempo_map.beats(origin_sec + np.asarray(local_seconds)) - self.origin_beats - self.beat_offset


class Meter:
    def __init__(self, meter_changes=()):
        validate_changes(meter_changes, meter=True)
        entries = meter_changes if meter_changes and meter_changes[0][0] == 0 else ((0, 4, 4), *meter_changes)
        self.bars = np.array([b for b, _, _ in entries])
        self.lengths = np.array([n * 4. / d for _, n, d in entries])
        self.starts = np.r_[0., np.cumsum(np.diff(self.bars) * self.lengths[:-1])]
        if self.starts[-1] > 100000:
            raise ValueError('Meter change exceeds the beat limit.')

    def bar_start(self, bar):
        values = np.asarray(bar)
        index = np.maximum(0, np.searchsorted(self.bars, values, side='right') - 1)
        return self.starts[index] + (values - self.bars[index]) * self.lengths[index]

    def bar_of(self, beats):
        values = np.asarray(beats, dtype=float)
        index = np.maximum(0, np.searchsorted(self.starts, values, side='right') - 1)
        offset = np.floor((values - self.starts[index]) / self.lengths[index]).astype(np.int64)
        return self.bars[index] + offset, values - self.starts[index] - offset * self.lengths[index]

    def beats_per_bar_at(self, beats):
        index = np.maximum(0, np.searchsorted(self.starts, beats, side='right') - 1)
        return self.lengths[index]

    def bar_range(self, start, end):
        """One-based inclusive bar numbers for a half-open interval of beats."""
        first, _ = self.bar_of(start)
        last, offset = self.bar_of(end)
        return int(first) + 1, max(int(first) + 1, int(last) + int(offset > 0))

    def bar_lines(self, b0, b1):
        first = int(self.bar_of(b0)[0])
        last = int(self.bar_of(b1)[0])
        lines = self.bar_start(np.arange(first, last + 1))
        return lines[(lines >= b0) & (lines <= b1)]
