"""Pure editing helpers for immutable automation lanes, independent of Qt."""
from phosphor.engine.automation import GAIN_RANGE, MAX_BEATS, validate_points

SNAP_EPSILON = 1e-6


def shift_lane(points, offset):
    """Move the beat origin, preserving the curve after a left-edge trim."""
    if not points or offset == 0:
        return points
    cut = [point for point in points if point[0] + offset < 0.]
    remaining = [(p[0] + offset, *p[1:]) for p in points if p[0] + offset >= 0.]
    if not remaining:
        return _lane(((0., *points[-1][1:]),))
    if cut and remaining[0][0] > 0.:
        index = len(cut) - 1
        u = (-offset - points[index][0]) / (points[index+1][0] - points[index][0])
        _, right = split_segment(points, index, u)
        remaining.insert(0, (0., *right[1:]))
    result = [point for point in remaining if point[0] <= MAX_BEATS]
    if not result:
        result = [(MAX_BEATS, *remaining[0][1:])]
    return _lane(result)


def reference_gain(points):
    return points[0][1] if points else 1.0


def on_reference(points, reference):
    return tuple(i for i, point in enumerate(points)
                 if abs(point[1] - reference) <= SNAP_EPSILON)


def snap(gain, reference, tolerance):
    return reference if tolerance > 0 and abs(gain - reference) <= tolerance else gain


def _gain(gain):
    return round(max(GAIN_RANGE[0], min(gain, GAIN_RANGE[1])), 4)


def _lane(points):
    result = tuple(points)
    validate_points(result, 'Automation lane')
    return result


def _index(points, index, segment=False):
    validate_points(points, 'Automation lane')
    if not 0 <= index < len(points) - int(segment):
        raise ValueError('Invalid automation node or segment index.')


def move_node(points, index, beat, gain, length):
    _index(points, index)
    old_beat = points[index][0]
    paired = ((index > 0 and points[index - 1][0] == old_beat)
              or (index + 1 < len(points) and points[index + 1][0] == old_beat))
    if paired:
        beat = old_beat
    else:
        lower = points[index - 1][0] + 1e-3 if index else 0.
        upper = min(length, MAX_BEATS)
        if index + 1 < len(points):
            upper = min(upper, points[index + 1][0] - 1e-3)
        beat = max(lower, min(beat, upper)) if lower <= upper else old_beat
    result = list(points)
    result[index] = (beat, _gain(gain), *points[index][2:])
    return _lane(_preserve_bends(points, result))


def set_gains(points, indices, gain):
    """Set node gains without changing beats, preserving bounded curve bends."""
    validate_points(points, 'Automation lane')
    indices = set(indices)
    for index in indices:
        _index(points, index)
    result = [(p[0], _gain(gain), *p[2:]) if i in indices else p
              for i, p in enumerate(points)]
    return _lane(_preserve_bends(points, result))


def _moved(points, index):
    _index(points, index, segment=True)
    return (index, index + 1) if points[index][2] != 'hold' else (index,)


def drag_segment(points, index, delta):
    """Apply total drag displacement to the original mouse-press lane."""
    moved = _moved(points, index)
    if points[index][0] == points[index + 1][0]:
        raise ValueError('A jump pair has no segment duration.')
    gains = [points[i][1] for i in moved]
    if points[index][2] == 'curve': gains.extend(points[index][3:])
    delta = max(GAIN_RANGE[0] - min(gains), min(delta, GAIN_RANGE[1] - max(gains)))
    result = list(points)
    for i in moved:
        point = points[i]
        gain = point[1] + delta if points[index][2] == 'curve' else _gain(point[1] + delta)
        result[i] = (point[0], gain, *point[2:])
    if points[index][2] == 'curve':
        # Use the actual rounded endpoint displacement for the handle translation.
        actual = result[index][1] - points[index][1]
        handles = tuple(max(GAIN_RANGE[0], min(GAIN_RANGE[1], h + actual)) for h in points[index][3:])
        result[index] = (*result[index][:3], *handles)
    if len(moved) == 2 and index + 2 < len(points) and points[index + 2][0] != points[index + 1][0]:
        result.insert(index + 2, points[index + 1])
    new_index = index
    if index > 0 and points[index - 1][0] != points[index][0]:
        result[index - 1] = points[index - 1]
        beat, gain = points[index][:2]
        result.insert(index, (beat, gain, 'linear'))
        new_index += 1
    return _lane(result), new_index


def snap_delta(points, index, delta, reference, tolerance):
    moved = _moved(points, index)
    if tolerance <= 0:
        return delta
    nearest = min(moved, key=lambda i: abs(points[i][1] + delta - reference))
    if abs(points[nearest][1] + delta - reference) <= tolerance:
        return reference - points[nearest][1]
    return delta


def tidy(points):
    result = tuple(point for i, point in enumerate(points)
                   if not (i + 1 < len(points) and point[:2] == points[i + 1][:2]))
    return _lane(result if result else points)


def set_reference(points, gain):
    indices = on_reference(points, reference_gain(points))
    result = [(p[0], _gain(gain), *p[2:]) if i in indices else p for i, p in enumerate(points)]
    return _lane(_preserve_bends(points, result))


def _preserve_bends(original, result):
    """Keep handle offsets from the endpoint chord, bounded by the gain range."""
    scale = 1.
    for i, p in enumerate(original[:-1]):
        if p[2] != 'curve': continue
        for j in (1, 2):
            delta = (1-j/3)*(result[i][1]-p[1]) + (j/3)*(result[i+1][1]-original[i+1][1])
            if delta > 0: scale = min(scale, (GAIN_RANGE[1]-p[j+2])/delta)
            elif delta < 0: scale = min(scale, (GAIN_RANGE[0]-p[j+2])/delta)
    if scale < 1.:
        result = [(p[0], old[1] + scale*(p[1]-old[1]), *p[2:]) for old, p in zip(original, result)]
    for i, p in enumerate(original[:-1]):
        if p[2] != 'curve': continue
        a, b = result[i][1], result[i+1][1]
        old_a, old_b = p[1], original[i+1][1]
        handles = [p[j+2] + (1-j/3)*(a-old_a) + (j/3)*(b-old_b) for j in (1, 2)]
        handles = [max(GAIN_RANGE[0], min(GAIN_RANGE[1], h)) for h in handles]
        result[i] = (*result[i][:3], *handles)
    return result


def split_segment(points, index, u):
    """Return the left and inserted right nodes of an exact split at fraction u."""
    _index(points, index, segment=True)
    if not 0 <= u <= 1: raise ValueError('Split fraction must be in 0..1.')
    p, q = points[index:index+2]
    beat = p[0] + u * (q[0] - p[0])
    if p[2] != 'curve':
        gain = p[1] if p[2] == 'hold' else p[1] + u*(q[1]-p[1])
        return p, (beat, gain, p[2])
    lerp = lambda a, b: a + u*(b-a)
    a, b, c = lerp(p[1], p[3]), lerp(p[3], p[4]), lerp(p[4], q[1])
    d, e = lerp(a, b), lerp(b, c)
    return (p[0], p[1], 'curve', a, d), (beat, lerp(d, e), 'curve', e, c)


def bend_segment(points, index, delta):
    """Move the midpoint by delta relative to the original bend."""
    _index(points, index, segment=True)
    p, q = points[index:index+2]
    if p[0] == q[0]: raise ValueError('A jump pair has no segment duration.')
    h1 = p[1] + (q[1]-p[1])/3
    h2 = p[1] + 2*(q[1]-p[1])/3
    old = ((p[3]-h1) + (p[4]-h2))/2 if p[2] == 'curve' else 0.
    b = max(GAIN_RANGE[0]-min(h1, h2), min(old + delta/.75, GAIN_RANGE[1]-max(h1, h2)))
    result = list(points)
    result[index] = (p[0], p[1], 'linear') if abs(b) < 1e-4 else (p[0], p[1], 'curve', h1+b, h2+b)
    return _lane(result)


def straighten(points, index):
    _index(points, index)
    result = list(points)
    result[index] = (*points[index][:2], 'linear')
    return _lane(result)
