"""On/Off lanes: two-value automation stored in the gain-lane point format.

A switch lane is a tuple of hold points (beat from the clip start, 0. or 1., 'hold') with
strictly increasing beats. Before its first node the first value holds, so a lane is
"on" or "off" everywhere. Objects keep theirs as the 'on' entry of Shape.lanes; each
transform as Transform.on (a Param whose value stays 1). Off removes the object from the
beam path (scene_plan skips it) or the step from its stack, per pose frame; with no lane
nothing changes. The editor paints ranges and flips blocks; nothing here imports Qt.
"""
import math

import numpy as np

from phosphor.engine.automation import gain_at, validate_points

ON = 'on'  # Lane name in Shape.lanes and the last part of every On/Off lane key.
DEFAULT = ((0., 1., 'hold'),)  # A new lane: on across the clip.


def validate_switch(points, label):
    validate_points(points, label)
    previous = None
    for beat, value, *rest in points:
        if rest != ['hold'] or value not in (0, 1) or isinstance(value, bool):
            raise ValueError(f'{label}: On/Off nodes hold 0 or 1.')
        if previous is not None and beat <= previous:
            raise ValueError(f'{label}: On/Off nodes need increasing beats.')
        previous = beat


def switch_on(points, clock, seconds):
    """Whether the lane is on at `seconds` (no points: on; unbaked: its first node)."""
    return not points or gain_at(points, clock, seconds) >= .5


def lanes_off(shapes, seconds):
    """Indices of shapes whose On/Off lane is off at `seconds` (as posed_shapes decides).

    shapes carry their baked lane_clock (Composer's clip_settings) or none (their first node).
    """
    return frozenset(index for index, shape in enumerate(shapes)
                     if (points := next((p for name, p in shape.lanes if name == ON), None))
                     and not switch_on(points, shape.lane_clock, seconds))


def state_at(points, beat):
    """The lane's value (True for on) at a beat from the clip start."""
    if not points: return True
    index = max(0, int(np.searchsorted([p[0] for p in points], beat, side='right')) - 1)
    return points[index][1] >= .5


def normalized(changes):
    """Minimal hold points from (beat, on) changes: the first at beat 0, values alternating."""
    changes = sorted(changes, key=lambda change: change[0])
    if not changes: return DEFAULT
    result = [(0., 1. if changes[0][1] else 0., 'hold')]
    for beat, on in changes[1:]:
        value = 1. if on else 0.
        if beat <= 0.: result[0] = (0., value, 'hold'); continue
        if value == result[-1][1]: continue
        if beat == result[-1][0]: result[-1] = (float(beat), value, 'hold')
        else: result.append((float(beat), value, 'hold'))
    # A change at the same beat as the one before may make two equal neighbours.
    tidy = [result[0]]
    for point in result[1:]:
        if point[1] != tidy[-1][1]: tidy.append(point)
    return tuple(tidy)


def paint(points, start, end, on):
    """Set the lane on (True) or off over beats [start, end); the rest keeps its state.

    end may be math.inf: the new state then holds to the end of the lane.
    """
    start, end = max(0., float(start)), float(end)
    if not end > start: return tuple(points) if points else DEFAULT
    points = tuple(points) if points else DEFAULT
    changes = [(p[0], p[1] >= .5) for p in points if p[0] < start or p[0] > end]
    if not changes or changes[0][0] > 0. and start > 0.:
        changes.insert(0, (0., state_at(points, 0.)))
    changes.append((start, on))
    if math.isfinite(end): changes.append((end, state_at(points, end)))
    return normalized(changes)


def blocks(points, length):
    """(start, end, on) runs covering beats [0, length], merged; whole lane when empty."""
    length = float(length)
    points = tuple(points) if points else DEFAULT
    edges = [p[0] for p in points[1:] if 0. < p[0] < length]
    result = []
    for a, b in zip([0., *edges], [*edges, length]):
        on = state_at(points, a)
        if result and result[-1][2] == on: result[-1] = (result[-1][0], b, on)
        else: result.append((a, b, on))
    return tuple(result)


def flip(points, beat, length):
    """Turn the run under `beat` to the other state (it merges with its neighbours).

    The last run holds past the clip end, so flipping it leaves no change back there.
    """
    runs = blocks(points, length)
    a, b, on = next((run for run in reversed(runs) if run[0] <= beat), runs[0])
    return paint(points, a, b if b < float(length) else math.inf, not on)


def all_on(points):
    return all(p[1] >= .5 for p in points)
