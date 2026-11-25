"""Monitoring-only beat clicks and a clock-injectable tap tempo estimator."""
from collections import deque
import time
import numpy as np
from phosphor.engine.composition import BEATS_PER_BAR


def click_times(song_seconds_block, bpm, sample_rate):
    """Sample ages and beat numbers, with boundaries rounded to the output grid.

    Wrapped song positions make seeks and loop wraps independent of block size.
    """
    samples = np.rint(np.asarray(song_seconds_block) * sample_rate).astype(np.int64)
    period = 60. * sample_rate / bpm
    beat = np.floor(samples / period).astype(np.int64)
    beat += np.rint((beat + 1) * period).astype(np.int64) <= samples
    boundary = np.rint(beat * period).astype(np.int64)
    return samples - boundary, beat


def mix_clicks(signal, song_seconds_block, bpm, sample_rate, level=.3):
    """Add a 15 ms decaying sine equally to X and Y; return a new array."""
    age, beat = click_times(song_seconds_block, bpm, sample_rate)
    return _mix(signal, age, beat, sample_rate, level)


def click_times_beats(song_beats, sample_rate, tempo_map):
    """Sample ages from mapped quarter-note boundaries (also valid across seeks)."""
    values = np.asarray(song_beats, dtype=float)
    samples = np.rint(tempo_map.seconds(values) * sample_rate).astype(np.int64)
    beat = np.floor(values).astype(np.int64)
    beat += np.rint(tempo_map.seconds(beat + 1) * sample_rate).astype(np.int64) <= samples
    boundary = np.rint(tempo_map.seconds(beat) * sample_rate).astype(np.int64)
    return samples - boundary, beat


def mix_clicks_beats(signal, song_beats, sample_rate, tempo_map, level=.3, meter=None):
    if meter is not None:
        values = np.asarray(song_beats, dtype=float)
        # Include fractional bar starts (e.g. 3/8), alongside quarter-note clicks.
        bars, _ = meter.bar_of(values)
        boundary = np.maximum(np.floor(values), meter.bar_start(bars))
        following = np.minimum(np.floor(values) + 1, meter.bar_start(bars + 1))
        samples = np.rint(tempo_map.seconds(values) * sample_rate).astype(np.int64)
        boundary = np.where(np.rint(tempo_map.seconds(following) * sample_rate) <= samples, following, boundary)
        age = samples - np.rint(tempo_map.seconds(boundary) * sample_rate).astype(np.int64)
        _, offset = meter.bar_of(boundary)
        return _mix(signal, age, boundary, sample_rate, level, np.abs(offset) < 1e-9)
    age, beat = click_times_beats(song_beats, sample_rate, tempo_map)
    return _mix(signal, age, beat, sample_rate, level)


def _mix(signal, age, beat, sample_rate, level, accent=None):
    t = age / float(sample_rate)
    if accent is None: accent = beat % BEATS_PER_BAR == 0
    frequency = np.where(accent, 1800., 1200.)
    amplitude = np.where(accent, 1., .65) * min(1., max(0., level))
    envelope = np.exp(-t.clip(0) / .003) * ((age >= 0) & (t < .015))
    click = amplitude * envelope * np.sin(2 * np.pi * frequency * t)
    return signal + click[:, None]


class TapTempo:
    """Average intervals between the last four taps; the first returns None."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.taps = deque(maxlen=4)

    def tap(self):
        now = self.clock()
        if self.taps and (now - self.taps[-1] > 2. or now <= self.taps[-1]):
            self.taps.clear()
        self.taps.append(now)
        if len(self.taps) < 2:
            return None
        return min(400., max(20., 60. * (len(self.taps) - 1) / (self.taps[-1] - self.taps[0])))
