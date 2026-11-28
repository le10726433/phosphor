"""Immutable beat-based gain lanes, shared by transforms and equation variables."""
from bisect import bisect_right
from collections import OrderedDict
import math
from threading import Lock

import numpy as np

# Narrowed from ±2 on November 13, 2026 at the user's request. Provisional: validate by
# hand whether ±1 gives enough range (e.g. inverting or doubling a value) before relying
# on it. Files saved with larger gains open clamped (read_points).
GAIN_RANGE = (-1., 1.)
MAX_POINTS = 256
SHAPES = ('linear', 'hold', 'curve')
SHAPE_LANE_FIELDS = ('position_x', 'position_y', 'shape_size')
CLIP_LANE_FIELDS = ('drawing_gain', 'music_gain')
# composition imports the renderer, which imports this module.
MAX_BEATS = 100000.
# Object Replace (R1, 0.80.0): a variable lane whose variable the active equation does not
# declare is parked in Shape.lanes as 'variable:<name>'. Parked lanes sort after every live
# field ('on', 'position_*', 'shape_size'), are never evaluated or baked, and come back into
# the variable's points when an equation declaring it is active again (engine/object_replace.py).
PARKED_LANE = 'variable:'


def parked_lane(name):
    return PARKED_LANE + name


def is_parked_lane(field):
    return field.startswith(PARKED_LANE)


def live_lanes(lanes):
    """lanes without parked variable lanes (those sort last, so this is usually lanes itself)."""
    if not lanes or not is_parked_lane(lanes[-1][0]): return lanes
    return tuple(lane for lane in lanes if not is_parked_lane(lane[0]))


def parked_lanes(lanes):
    """{variable name: points} of the parked variable lanes."""
    return {field[len(PARKED_LANE):]: points for field, points in lanes if is_parked_lane(field)}


def lane_points(lanes, name):
    return next((points for field, points in lanes if field == name), ())


def with_lane(lanes, name, points):
    values = [(field, value) for field, value in lanes if field != name]
    if points: values.append((name, points))
    return tuple(sorted(values))


def validate_lanes(lanes, allowed_fields, label):
    if not isinstance(lanes, tuple):
        raise ValueError(f'{label}: lanes must be a tuple.')
    previous = ''
    for lane in lanes:
        if not isinstance(lane, tuple) or len(lane) != 2:
            raise ValueError(f'{label}: each lane needs a field and points.')
        name, points = lane
        if name not in allowed_fields or name <= previous:
            raise ValueError(f'{label}: lane fields must be allowed, unique and sorted.')
        validate_points(points, label)
        if not points: raise ValueError(f'{label}: lanes need non-empty points.')
        previous = name


_validated_points = OrderedDict()
_validated_lock = Lock()


def validate_points(points, label):
    if not isinstance(points, tuple) or len(points) > MAX_POINTS:
        raise ValueError(f'{label}: use a tuple of at most {MAX_POINTS} gain points.')
    if not points:
        return
    key = id(points)
    with _validated_lock:
        if _validated_points.get(key) is points:
            return
    previous = -1.
    same_beat = 0
    for point in points:
        if not isinstance(point, tuple) or len(point) not in (3, 5):
            raise ValueError(f'{label}: each gain point needs beat, gain and shape.')
        beat, gain, shape = point[:3]
        if len(point) != (5 if shape == 'curve' else 3):
            raise ValueError(f'{label}: curves need two handle gains.')
        for number in (beat, gain, *point[3:]):
            try:
                valid = type(number) in (int, float) and math.isfinite(number)
            except OverflowError:
                valid = False
            if not valid: raise ValueError(f'{label}: gain points need finite numbers.')
        same_beat = same_beat + 1 if beat == previous else 1
        if not 0 <= beat <= MAX_BEATS or beat < previous or same_beat > 2:
            raise ValueError(f'{label}: beats must not decrease within 0..{MAX_BEATS:g}; at most two nodes per beat.')
        if not GAIN_RANGE[0] <= gain <= GAIN_RANGE[1] or shape not in SHAPES:
            raise ValueError(f'{label}: invalid gain or segment shape.')
        if any(not GAIN_RANGE[0] <= h <= GAIN_RANGE[1] for h in point[3:]):
            raise ValueError(f'{label}: handle gains must be in range.')
        previous = beat
    with _validated_lock:
        _validated_points[key] = points
        _validated_points.move_to_end(key)
        if len(_validated_points) > 4096: _validated_points.popitem(last=False)


_lanes = OrderedDict()
_lanes_lock = Lock()


def _build_arrays(points):
    arrays = (np.array([p[0] for p in points], dtype=float),
              np.array([p[1] for p in points], dtype=float),
              np.array([p[2] == 'linear' for p in points]),
              np.array([p[2] == 'curve' for p in points]),
              np.array([p[3] if p[2] == 'curve' else p[1] for p in points], dtype=float),
              np.array([p[4] if p[2] == 'curve' else p[1] for p in points], dtype=float))
    for array in arrays: array.setflags(write=False)
    return arrays


def _lane_entry(points):
    if not isinstance(points, tuple):
        arrays = _build_arrays(points)
        return arrays, tuple(tuple(a.tolist()) for a in arrays)
    key = id(points)
    with _lanes_lock:
        entry = _lanes.get(key)
        if entry is not None and entry[0] is points:
            _lanes.move_to_end(key)
            return entry[1]
    arrays = _build_arrays(points)
    built = (arrays, tuple(tuple(a.tolist()) for a in arrays))
    with _lanes_lock:
        _lanes[key] = (points, built)
        _lanes.move_to_end(key)
        if len(_lanes) > 4096: _lanes.popitem(last=False)
    return built


def _lane_arrays(points):
    return _lane_entry(points)[0]


def gain_at(points, clock, t):
    if isinstance(t, (int, float, np.floating, np.integer)):
        if not points or not clock:
            return float(points[0][1]) if points else 1.
        tf = float(t)
        beat = float(clock.beats(tf)) if hasattr(clock, 'beats') else (tf - clock[1]) / clock[0]
        nodes_t, gains_t, linear_t, curved_t, h1_t, h2_t = _lane_entry(points)[1]
        last = len(nodes_t) - 1
        idx = bisect_right(nodes_t, beat) - 1
        if idx < 0: idx = 0
        elif idx > last: idx = last
        right = idx + 1 if idx < last else last
        span = nodes_t[right] - nodes_t[idx]
        g0 = gains_t[idx]
        if span <= 0.:
            return g0
        u = (beat - nodes_t[idx]) / span
        if u < 0.: u = 0.
        elif u > 1.: u = 1.
        if curved_t[idx]:
            # Bit-identical to the array path below: NumPy computes (1-u)**3 and u**3 with C pow
            # (as Python's float ** does) but (1-u)**2 as a square (om*om; Python's ** 2 calls pow,
            # which can differ by an ulp). Keep the operation order (test_automation_parity).
            om = 1 - u
            return om**3*g0 + 3*(om*om)*u*h1_t[idx] + 3*om*u*u*h2_t[idx] + u**3*gains_t[right]
        if linear_t[idx]:
            return g0 + u * (gains_t[right] - g0)
        return g0
    times = np.asarray(t, dtype=float)
    if not points or not clock:
        result = np.full(times.shape, points[0][1] if points else 1., dtype=float)
    else:
        beats = clock.beats(times) if hasattr(clock, 'beats') else (times - clock[1]) / clock[0]
        nodes, gains, linear, curved, h1, h2 = _lane_arrays(points)
        index = np.clip(np.searchsorted(nodes, beats, side='right') - 1, 0, len(points)-1)
        right = np.minimum(index + 1, len(points)-1)
        span = nodes[right] - nodes[index]
        fraction = np.clip((beats - nodes[index]) / np.where(span > 0, span, 1.), 0., 1.)
        result = gains[index] + np.where(linear[index], fraction, 0.) * (gains[right] - gains[index])
        curved, h1, h2 = curved[index], h1[index], h2[index]
        u = fraction
        cubic = (1-u)**3*gains[index] + 3*(1-u)**2*u*h1 + 3*(1-u)*u*u*h2 + u**3*gains[right]
        result = np.where(curved & (span > 0), cubic, result)
    return float(result) if times.ndim == 0 else result


def points_document(points):
    return [list(point) for point in points]


def read_points(value, label):
    if not isinstance(value, list) or any(not isinstance(p, list) for p in value):
        raise ValueError(f'{label}: gain points must be a list of lists.')
    points = tuple(tuple(p) for p in value)
    points = tuple(_clamped(p) for p in points)  # Older files allowed ±2.
    validate_points(points, label)
    return points


def _clamped(point):
    if len(point) == 3 and isinstance(point[1], (int, float)) and not isinstance(point[1], bool) and math.isfinite(point[1]):
        return (point[0], min(GAIN_RANGE[1], max(GAIN_RANGE[0], point[1])), point[2])
    return point
