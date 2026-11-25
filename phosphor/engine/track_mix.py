"""Composer stereo balance, right-channel phase rotation and channel delay."""
from dataclasses import dataclass
from functools import lru_cache
from math import ceil, floor, nextafter

import numpy as np
from scipy.signal import fftconvolve

from phosphor.engine.automation import gain_at
from phosphor.engine.track_chain import TrackChain
from phosphor.engine.track_effects import effect_processor

MAX_DELAY_MS = 20.
LANE_CLOCK = (1., 0.)  # One second per unit, measured from the start of the song.
_LIMIT = {'pan': 1., 'phase': 180., 'delay': MAX_DELAY_MS}


def to_lane(kind, value):
    return (value / _LIMIT[kind] + 1.) / 2.


def from_lane(kind, v):
    return (2. * v - 1.) * _LIMIT[kind]


class _Lane:
    @property
    def uses_times(self):
        return bool(self.points)

    def value(self, kind, static, times):
        if self.points:
            return from_lane(kind, gain_at(self.points, LANE_CLOCK, times))
        return static


@dataclass(frozen=True)
class Pan(_Lane):
    pan: float
    points: tuple = ()
    history_seconds = lookahead_seconds = 0.

    def apply(self, window, rate, times=None):
        if not self.points and self.pan == 0:
            return window
        p = self.value('pan', self.pan, times)
        far = np.sin((1. - np.abs(p)) * (np.pi / 2))
        out = window.copy()
        out[:, 0] *= np.where(p > 0, far, 1.)
        out[:, 1] *= np.where(p < 0, far, 1.)
        return out


@lru_cache(maxsize=32)
def _hilbert(taps, rate):
    radius = taps // 2
    k = np.arange(-radius, radius + 1)
    odd = k % 2 != 0
    fir = np.zeros(taps)
    fir[odd] = 2. / (np.pi * k[odd])
    return fir * np.blackman(taps)


@dataclass(frozen=True)
class TruePhase(_Lane):
    """Centred type III Hilbert FIR. FFT roundoff across splits is about 1e-12."""
    degrees: float
    points: tuple = ()
    taps_seconds: float = .04

    def _radius(self, rate):
        # Nearest odd length, choosing the larger odd length at an even tie.
        return max(15, floor(self.taps_seconds * rate / 2))

    def history_seconds(self, rate):
        # Round toward zero so ceil(seconds * rate) cannot add a spurious sample.
        return nextafter(self._radius(rate) / rate, 0.)

    lookahead_seconds = history_seconds

    def apply(self, window, rate, times=None):
        radius = self._radius(rate)
        out = window[radius:-radius].copy()
        phi = np.deg2rad(self.value('phase', self.degrees,
                                    times[radius:-radius] if self.points else None))
        quadrature = fftconvolve(window[:, 1], _hilbert(2 * radius + 1, rate), mode='valid')
        out[:, 1] = out[:, 1] * np.cos(phi) - quadrature * np.sin(phi)
        return out


@dataclass(frozen=True)
class TimeDelay(_Lane):
    ms: float
    points: tuple = ()
    history_seconds = MAX_DELAY_MS / 1000.
    lookahead_seconds = 0.

    def apply(self, window, rate, times=None):
        history = ceil(self.history_seconds * rate)
        rows = np.arange(history, len(window))
        ms = self.value('delay', self.ms, times[history:] if self.points else None)
        distance = np.abs(ms) * rate / 1000.
        whole = np.floor(distance).astype(np.intp)
        fraction = distance - whole
        # Split the displacement before subtracting from row indices. The fraction
        # then has exactly the same bits at every position in a playback block.
        recent = rows - whole
        channel = np.where(ms < 0, 0, 1)
        sample = window[recent, channel]
        earlier = window[np.maximum(recent - 1, 0), channel]
        out = window[history:].copy()
        out[rows - history, channel] = np.where(fraction == 0, sample,
                                                sample + fraction * (earlier - sample))
        return out


def track_chain_for(track):
    stages = []
    if track.pan != 0 or track.pan_points:
        stages.append(Pan(track.pan, track.pan_points))
    if track.phase_mode == 'true':
        if track.phase != 0 or track.phase_points:
            stages.append(TruePhase(track.phase, track.phase_points))
    elif track.delay_ms != 0 or track.phase_points:
        stages.append(TimeDelay(track.delay_ms, track.phase_points))
    causal = () if track.effects_bypass else tuple(effect_processor(e) for e in track.effects if not e.bypass)
    return TrackChain(windowed=tuple(stages), causal=causal)
