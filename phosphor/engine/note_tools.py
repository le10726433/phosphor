"""Pure, immutable selection tools for the Composer note roll."""
from dataclasses import replace
import math
import random

from phosphor.engine.composition import Note, PITCH_RANGE, MAX_BEATS, MAX_NOTES
from phosphor.engine.note_edit import move_notes
from phosphor.audio.score_source import SCALE_TYPES

MIN_LENGTH = 1 / 64
CHORD_TYPES = dict(major=(0, 4, 7), minor=(0, 3, 7), dim=(0, 3, 6),
                   aug=(0, 4, 8), sus2=(0, 2, 7), sus4=(0, 5, 7),
                   **{'6': (0, 4, 7, 9), '7': (0, 4, 7, 10),
                      'maj7': (0, 4, 7, 11), 'm7': (0, 3, 7, 10),
                      '9': (0, 4, 7, 10, 14)})
ROLL_SCALES = ('Off', *(s for s in SCALE_TYPES if s != 'Chromatic'))


def _selection(notes, indices):
    picked = frozenset(indices)
    if any(type(i) is not int or not 0 <= i < len(notes) for i in picked):
        raise ValueError('Invalid note selection.')
    return tuple(sorted(picked))


def _number(value, low=0., high=MAX_BEATS):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid note tool value.')
    return value


def _clamp(value, low=0., high=MAX_BEATS):
    return min(high, max(low, value))


def _length(note, length, **changes):
    length = _clamp(length, MIN_LENGTH)
    return replace(note, length=length, glide=min(note.glide, length), **changes)


def _groups(notes, picked):
    groups = []
    for i in sorted(picked, key=lambda i: (notes[i].start, i)):
        if not groups or notes[i].start - notes[groups[-1][0]].start > 1e-6:
            groups.append([])
        groups[-1].append(i)
    return groups


def _append(notes, additions):
    additions = tuple(additions)
    if len(notes) + len(additions) > MAX_NOTES:
        raise ValueError(f'Use at most {MAX_NOTES} notes.')
    return (*notes, *additions), tuple(range(len(notes), len(notes) + len(additions)))


def chord_pitches(root, kind, inversion=0):
    if type(root) is not int or kind not in CHORD_TYPES:
        raise ValueError('Invalid chord root or type.')
    intervals = CHORD_TYPES[kind]
    if type(inversion) is not int or not 0 <= inversion < len(intervals):
        raise ValueError('Invalid chord inversion.')
    return tuple(sorted(root + offset + (12 if i < inversion else 0)
                        for i, offset in enumerate(intervals)))


def stamp_chord(notes, start, length, root, kind, inversion=0, velocity=1.0):
    _number(start, -MAX_BEATS, MAX_BEATS * 2); _number(length); _number(velocity, 0, 1)
    return _append(notes, (Note(_clamp(start), max(MIN_LENGTH, length), p, velocity)
                           for p in chord_pitches(root, kind, inversion) if PITCH_RANGE[0] <= p <= PITCH_RANGE[1]))


def set_velocity(notes, indices, value):
    picked = _selection(notes, indices); _number(value, -math.inf, math.inf)
    result = list(notes); value = _clamp(value, 0, 1)
    for i in picked: result[i] = replace(notes[i], velocity=value)
    return tuple(result), picked


def shift_velocity(notes, indices, delta):
    picked = _selection(notes, indices); _number(delta, -math.inf, math.inf)
    result = list(notes)
    for i in picked: result[i] = replace(notes[i], velocity=_clamp(notes[i].velocity + delta, 0, 1))
    return tuple(result), picked


def velocity_line(notes, indices, a, b):
    picked = _selection(notes, indices)
    for beat, value in (a, b):
        _number(beat, -MAX_BEATS, MAX_BEATS * 2); _number(value, -math.inf, math.inf)
    a, b = (a[0], _clamp(a[1], 0, 1)), (b[0], _clamp(b[1], 0, 1))
    low, high = sorted((a[0], b[0]))
    def value(start):
        return b[1] if a[0] == b[0] else a[1] + (b[1] - a[1]) * ((start - a[0]) / (b[0] - a[0]))
    result = list(notes)
    for i in picked:
        if low <= notes[i].start <= high: result[i] = replace(notes[i], velocity=_clamp(value(notes[i].start), 0, 1))
    return tuple(result), picked


def ramp_velocity(notes, indices, first, last):
    picked = _selection(notes, indices)
    _number(first, -math.inf, math.inf); _number(last, -math.inf, math.inf)
    starts = sorted({notes[i].start for i in picked})
    if len(starts) < 2: return set_velocity(notes, picked, first)
    return velocity_line(notes, picked, (starts[0], first), (starts[-1], last))


def strum(notes, indices, amount, direction):
    picked = _selection(notes, indices); _number(amount)
    if direction not in ('up', 'down'): raise ValueError('Invalid strum direction.')
    result = list(notes)
    for group in _groups(notes, picked):
        for k, i in enumerate(sorted(group, key=lambda i: notes[i].pitch, reverse=direction == 'down')):
            n = notes[i]; start = _clamp(min(n.start + k * amount, n.end - MIN_LENGTH))
            result[i] = _length(n, n.end - start, start=start)
    return tuple(result), picked


def arpeggiate(notes, indices, pattern, step):
    picked = _selection(notes, indices); _number(step, MIN_LENGTH)
    if pattern not in ('up', 'down', 'up-down'): raise ValueError('Invalid arpeggio pattern.')
    removed, additions = set(), []
    for group in _groups(notes, picked):
        ordered = sorted(group, key=lambda i: notes[i].pitch)
        if pattern == 'down': ordered.reverse()
        elif pattern == 'up-down': ordered += ordered[-2:0:-1]
        start = notes[group[0]].start; end = max(notes[i].end for i in group)
        group_step = step
        count = math.ceil((end - start) / group_step - 1e-10)
        while count < len(ordered) and group_step / 2 >= MIN_LENGTH:
            group_step /= 2
            count = math.ceil((end - start) / group_step - 1e-10)
        if len(additions) + count + len(notes) - len(removed) - len(group) > MAX_NOTES:
            raise ValueError(f'Use at most {MAX_NOTES} notes.')
        removed.update(group)
        for k in range(count):
            at = start + k * group_step
            length = min(group_step, end - at)
            if at > MAX_BEATS or length < MIN_LENGTH - 1e-10: continue
            source = notes[ordered[k % len(ordered)]]
            if PITCH_RANGE[0] <= source.pitch <= PITCH_RANGE[1]:
                additions.append(replace(source, start=at, length=max(MIN_LENGTH, length), glide=0.))
    kept = tuple(n for i, n in enumerate(notes) if i not in removed)
    return _append(kept, additions)


def quantize(notes, indices, grid, strength=1., swing=0., ends=False):
    picked = _selection(notes, indices); _number(grid, MIN_LENGTH); _number(strength, 0, 1); _number(swing, 0, 1)
    def target(beat):
        base = math.floor(beat / grid)
        points = [(n + (swing / 2 if n % 2 else 0)) * grid for n in range(max(0, base - 2), base + 3)]
        near = min(points, key=lambda p: (abs(p - beat), p))
        return beat + strength * (near - beat)
    result = list(notes)
    for i in picked:
        n = notes[i]; start = _clamp(target(n.start))
        result[i] = _length(n, target(n.end) - start if ends else n.length, start=start)
    return tuple(result), picked


def humanize(notes, indices, timing, velocity, seed):
    picked = _selection(notes, indices); _number(timing); _number(velocity, 0, 1)
    rng = random.Random(seed); result = list(notes)
    for i in picked:
        n = notes[i]; shift = rng.uniform(-timing, timing); gain = rng.uniform(-velocity, velocity)
        result[i] = replace(n, start=_clamp(n.start + shift), velocity=_clamp(n.velocity + gain, 0, 1))
    return tuple(result), picked


def copy_moved(notes, indices, semitones=0, beats=0.):
    picked = _selection(notes, indices)
    moved, _ = move_notes(notes, picked, semitones, beats)
    return _append(notes, (moved[i] for i in picked))


def duplicate(notes, indices, step):
    picked = _selection(notes, indices); _number(step)
    if not picked: return tuple(notes), ()
    span = max(notes[i].end for i in picked) - min(notes[i].start for i in picked)
    shift = math.ceil(span / step - 1e-10) * step if step else span
    return copy_moved(notes, picked, 0, shift)


def split(notes, indices, beat):
    picked = _selection(notes, indices); _number(beat)
    result, additions, selected = list(notes), [], list(picked)
    for i in picked:
        n = notes[i]
        if beat - n.start > MIN_LENGTH and n.end - beat > MIN_LENGTH:
            result[i] = _length(n, beat - n.start)
            additions.append(replace(n, start=beat, length=n.end - beat, glide=0.))
    result, appended = _append(tuple(result), additions)
    return result, tuple(selected) + appended


def join(notes, indices):
    picked = _selection(notes, indices); result = list(notes); removed = set()
    by_pitch = {}
    for i in sorted(picked, key=lambda i: (notes[i].start, i)):
        n = notes[i]; previous = by_pitch.get(n.pitch)
        if previous is not None and n.start <= result[previous].end + 1e-6:
            result[previous] = _length(result[previous], max(result[previous].end, n.end) - result[previous].start)
            removed.add(i)
        else: by_pitch[n.pitch] = i
    selected = tuple(j for j, i in enumerate(i for i in range(len(notes)) if i not in removed) if i in picked)
    return tuple(n for i, n in enumerate(result) if i not in removed), selected


def legato(notes, indices):
    picked = _selection(notes, indices); groups = _groups(notes, picked); result = list(notes)
    for group, following in zip(groups, groups[1:]):
        for i in group: result[i] = _length(notes[i], notes[following[0]].start - notes[i].start)
    return tuple(result), picked


def in_scale(pitch, scale, root):
    if scale not in ROLL_SCALES or type(root) is not int or not 0 <= root <= 11:
        raise ValueError('Invalid roll scale or root.')
    return scale == 'Off' or (pitch - root) % 12 in SCALE_TYPES[scale]


def snap_pitch(pitch, scale, root):
    ladder = [p for p in range(PITCH_RANGE[0], PITCH_RANGE[1] + 1) if in_scale(p, scale, root)]
    return min(ladder, key=lambda p: (abs(p - pitch), p))


def step_in_scale(pitch, steps, scale, root):
    if type(steps) is not int: raise ValueError('Expected integer scale steps.')
    current = snap_pitch(pitch, scale, root)
    direction = 1 if steps > 0 else -1
    for _ in range(abs(steps)):
        candidates = range(current + direction, PITCH_RANGE[1] + 1, 1) if direction > 0 else range(current - 1, PITCH_RANGE[0] - 1, -1)
        current = next((p for p in candidates if in_scale(p, scale, root)), current)
    return current
