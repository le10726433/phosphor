"""Click-free switching (mute/solo M3): sweeps around On/Off switches. No Qt.

M2 switches an object or transform step per pose frame: the first pose frame at or after
its lane's change has it on or off. With a sweep length L > 0 an object that goes off has
its Path Trace swept from 1 to 0 over the L seconds before that frame boundary (and back up
over the L seconds after it returns), and its share of the beam cycle shrinks with it, so
the others take its share gradually instead of all at once. A transform step's effect is
crossfaded over L instead (the shape before and after the switch).

The sweep is evaluated at key times: SWEEP_STEPS + 1 trace levels per object sweep, the two
ends of a step crossfade. A sample between two keys blends the beam positions of the two
key plans linearly in time, so the waveform stays continuous: a sweep that ends with no
object left reaches the empty plan (the beam at rest in the centre) without a jump.

Everything is a function of sample times, so playback blocks and exports agree sample for
sample. With no lane and no fade nothing here runs (signal_engine keeps its old path).
"""
import math

import numpy as np

from phosphor.engine.automation import gain_at, lane_points
from phosphor.engine.switch_lane import ON

MAX_SWEEP_MS = 50.  # Sweep-length preference range: 0 (hard switch, M2) to this.
DEFAULT_SWEEP_MS = 5.  # The preference's default (Settings keeps 0: exact-sample tests).
SWEEP_STEPS = 4  # Trace levels per object sweep: keys at 0, 1/4, 1/2, 3/4 and 1.
ALL = ('all',)  # Event target: every object (a muted clip's boundary).


def signature(shapes):
    """The static on/off state of a scene's objects and steps (Mute, Solo copies, bypass, power)."""
    return tuple((shape.uid, shape.muted, shape.stack_bypassed, tuple(step.enabled for step in shape.transforms))
                 for shape in shapes)


def project_signature(project):
    """signature() for every clip of a Composer project, with clip mute and audio clip mute."""
    return (tuple((clip.muted, signature(clip.settings.shapes)) for clip in project.clips),
            tuple(clip.muted for clip in project.audio_clips), tuple(t.muted for t in project.audio_tracks))


def switching(shapes):
    """Whether any of these (non-guide) shapes has an object or step On/Off lane."""
    return any((shape.lanes and lane_points(shape.lanes, ON)) or any(step.on.points for step in shape.transforms)
               for shape in shapes)


def _changes(points, clock, frames, hz, offset):
    """Frame boundaries in `frames` (after the first) where the lane's per-frame state changes."""
    seconds = np.maximum(0., frames / hz + offset)
    states = gain_at(points, clock, seconds) >= .5
    change = np.flatnonzero(states[1:] != states[:-1]) + 1
    return [(float(frames[k]) / hz, bool(states[k])) for k in change]


def frame_events(shapes, frame, hz, offset, length, fades=()):
    """Switch events whose sweep window overlaps pose frame `frame`: (target, time, rising).

    target is ('object', i) or ('step', i, j) over the non-guide shapes, or ALL for `fades`
    ((time, rising) pairs, such as a muted clip's edges). time is where the switch happens
    (a frame boundary for lanes): an off sweep ends there, an on sweep starts there.
    """
    reach = math.ceil(length * hz) + 1
    frames = np.arange(frame - reach, frame + reach + 2, dtype=float)
    span = (frame / hz, (frame + 1) / hz)
    events = []
    for i, shape in enumerate(shapes):
        if shape.muted: continue  # Static mute never switches inside a render.
        points = lane_points(shape.lanes, ON) if shape.lanes else ()
        if points:
            events += [(('object', i), time, rising) for time, rising in _changes(points, shape.lane_clock, frames, hz, offset)]
        if shape.stack_bypassed: continue
        for j, step in enumerate(shape.transforms):
            if step.enabled and step.on.points:
                events += [(('step', i, j), time, rising) for time, rising in _changes(step.on.points, step.on.clock, frames, hz, offset)]
    events += [(ALL, float(time), bool(rising)) for time, rising in fades]
    return [event for event in events if _window(event, length)[0] < span[1] and _window(event, length)[1] > span[0]]


def _window(event, length):
    _, time, rising = event
    return (time, time + length) if rising else (time - length, time)


def segments(events, length, start, end):
    """Consecutive key times (a, b) inside a sweep window that overlap [start, end)."""
    keys, windows = set(), []
    for event in events:
        w0, w1 = _window(event, length)
        windows.append((w0, w1))
        steps = 1 if event[0][0] == 'step' else SWEEP_STEPS
        keys.update((w0, w1, *(w0 + k * (w1 - w0) / steps for k in range(1, steps))))
    keys = sorted(keys)
    return [(a, b) for a, b in zip(keys[:-1], keys[1:])
            if b > start and a < end and any(w0 <= (a + b) / 2 <= w1 for w0, w1 in windows)]


def _ramp(event, length, time):
    """1 where the target is fully on, 0 where it is off, linear across its window."""
    _, at, rising = event
    value = (time - at) / length if rising else (at - time) / length
    if value >= 1 - 1e-9: return 1.
    if value <= 1e-9: return 0.
    return value


def key_state(shapes, events, length, time):
    """scene_plan's `switches` override at a key time, or None when it changes nothing.

    One (trace gain, steps forced off) pair per non-guide shape: gain 0 drops the object,
    a gain below 1 sweeps its trace and its share of the cycle; a step is off in the second
    half of its crossfade's key pair (keys sit at the crossfade's ends).
    """
    gains = [1.] * len(shapes)
    off = [None] * len(shapes)
    for event in events:
        ramp = _ramp(event, length, time)
        target = event[0]
        if target == ALL:
            gains = [min(gain, ramp) for gain in gains]
        elif target[0] == 'object':
            gains[target[1]] = min(gains[target[1]], ramp)
        elif ramp < .5:
            i, j = target[1], target[2]
            flags = list(off[i] or (False,) * len(shapes[i].transforms)); flags[j] = True
            off[i] = tuple(flags)
    if all(gain == 1. for gain in gains) and not any(off): return None
    return tuple(zip(gains, off))
