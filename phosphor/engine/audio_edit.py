"""Audio-track edits: move, trim, split, duplicate, fades, gain and the gain lane (no Qt).

Each edit takes a Project and an index into project.audio_clips and returns
(new project, index of the edited clip), with the clips sorted stably by start and
track. A clip starts at a beat but plays
in seconds, so trims, fades and gain-lane times are seconds and a trim keeps the
audio where it was in song time.
"""
from dataclasses import replace
import math

from phosphor.engine.audio_clips import (AudioClip, GAIN_RANGE, GAIN_LANE_SHAPES, LANE_CLOCK, MAX_AUDIO_CLIPS,
                                         ROUTINGS, audio_clip_end_beats, source_end)
from phosphor.engine.automation import gain_at

MIN_SECONDS = .01  # Shortest clip a trim or split leaves.


def _start_seconds(project, clip): return float(project.seconds(clip.start))


def place(project, clips, focus=None):
    """Validated project with `clips` in start order, and where clips[focus] went."""
    from phosphor.engine.composition import validate_project
    if len(clips) > MAX_AUDIO_CLIPS: raise ValueError(f'Use at most {MAX_AUDIO_CLIPS} audio clips.')
    order = sorted(range(len(clips)), key=lambda i: (clips[i].start, clips[i].track))
    project = validate_project(replace(project, audio_clips=tuple(clips[i] for i in order)))
    return project, (order.index(focus) if focus is not None else None)


def _edited(project, index, clip):
    clips = list(project.audio_clips); clips[index] = clip
    return place(project, clips, index)


def add(project, clip):
    return place(project, (*project.audio_clips, clip), len(project.audio_clips))


def delete(project, index):
    clips = project.audio_clips[:index] + project.audio_clips[index+1:]
    project, _ = place(project, clips)
    return project, (min(index, len(clips) - 1) if clips else None)


def move(project, index, start, track=None):
    from phosphor.engine.composition import MAX_BEATS
    clip = project.audio_clips[index]
    if track is None: track = clip.track
    if type(track) is not int or not 0 <= track < len(project.audio_tracks):
        raise ValueError('That audio track does not exist.')
    return _edited(project, index, replace(clip, track=track, start=min(MAX_BEATS, max(0., float(start)))))


def _clamped_fades(clip):
    return replace(clip, fade_in=min(clip.fade_in, clip.duration), fade_out=min(clip.fade_out, clip.duration))


def trim_start(project, index, start):
    """Move the clip's start to `start` beats; the audio stays put in song time."""
    clip = project.audio_clips[index]
    s0 = _start_seconds(project, clip)
    target = max(0., float(start))
    delta = float(project.seconds(target)) - s0
    limited = min(max(delta, -clip.offset), clip.duration - MIN_SECONDS)
    if limited != delta: target, delta = float(project.beats(s0 + limited)), limited
    if delta == 0: return project, index
    moved = replace(clip, start=target, offset=max(0., clip.offset + delta), duration=clip.duration - delta,
                    gain_points=shift_points(clip.gain_points, -delta))
    return _edited(project, index, _clamped_fades(moved))


def trim_end(project, index, end):
    """Move the clip's end to `end` beats, at most to the end of its file."""
    clip = project.audio_clips[index]
    duration = float(project.seconds(max(0., float(end)))) - _start_seconds(project, clip)
    duration = min(max(duration, MIN_SECONDS), source_end(clip) - clip.offset)
    if duration == clip.duration: return project, index
    return _edited(project, index, _clamped_fades(replace(clip, duration=duration)))


def split(project, index, beat):
    """Two clips meeting at `beat`; returns the right one's index. The cut has no fades."""
    clip = project.audio_clips[index]
    if len(project.audio_clips) >= MAX_AUDIO_CLIPS: raise ValueError('Too many audio clips.')
    local = float(project.seconds(float(beat))) - _start_seconds(project, clip)
    if not MIN_SECONDS <= local <= clip.duration - MIN_SECONDS:
        raise ValueError('The playhead must be inside the audio clip.')
    points = clip.gain_points
    left = replace(clip, duration=local, fade_in=min(clip.fade_in, local), fade_out=0.,
                   gain_points=cut_points(points, 0., local))
    rest = clip.duration - local
    right = replace(clip, start=float(beat), offset=clip.offset + local, duration=rest, fade_in=0.,
                    fade_out=min(clip.fade_out, rest), gain_points=shift_points(cut_points(points, local, None), -local))
    clips = list(project.audio_clips); clips[index:index+1] = [left, right]
    return place(project, clips, index + 1)


def duplicate(project, index, start=None):
    """A copy starting at `start` beats (default: where the clip ends)."""
    clip = project.audio_clips[index]
    if len(project.audio_clips) >= MAX_AUDIO_CLIPS: raise ValueError('Too many audio clips.')
    from phosphor.engine.composition import MAX_BEATS
    start = float(audio_clip_end_beats(project, clip)) if start is None else float(start)
    return add(project, replace(clip, start=min(MAX_BEATS, max(0., start))))


def set_fades(project, index, fade_in=None, fade_out=None):
    clip = project.audio_clips[index]
    def length(value, old): return old if value is None else min(max(0., float(value)), clip.duration)
    return _edited(project, index, replace(clip, fade_in=length(fade_in, clip.fade_in), fade_out=length(fade_out, clip.fade_out)))


def set_gain(project, index, gain):
    return _edited(project, index, replace(project.audio_clips[index], gain=min(GAIN_RANGE[1], max(GAIN_RANGE[0], float(gain)))))


def set_routing(project, index, routing):
    if routing not in ROUTINGS: raise ValueError('Invalid audio clip routing.')
    return _edited(project, index, replace(project.audio_clips[index], routing=routing))


def set_muted(project, index, muted):
    return _edited(project, index, replace(project.audio_clips[index], muted=bool(muted)))


def soloed(project, indices, tracks=frozenset()):
    """A clip is heard when it is not muted, its track is not muted, and — while any audio clip or
    audio track is soloed — the clip or its track is soloed. Mute wins over solo. Any audio solo
    also silences every scene clip (A3)."""
    clips = tuple(c if i in indices or c.track in tracks or c.muted else replace(c, muted=True)
                  for i, c in enumerate(project.audio_clips))
    scenes = tuple(c if c.muted else replace(c, muted=True) for c in project.clips)
    return replace(project, clips=scenes, audio_clips=clips)


def set_gain_points(project, index, points):
    return _edited(project, index, replace(project.audio_clips[index], gain_points=tuple(points)))


# Gain-lane points: (seconds from the clip start, gain 0..1, 'linear' | 'hold') -------------
def lane_value(points, seconds):
    """The lane's gain at clip-local seconds (1 without points)."""
    return float(gain_at(points, LANE_CLOCK, float(seconds))) if points else 1.


def _segment_shape(points, seconds):
    shape = points[0][2]
    for point in points:
        if point[0] <= seconds: shape = point[2]
    return shape


def shift_points(points, delta):
    """Points moved by delta seconds; any before 0 are replaced by one node at 0 on the old curve."""
    if not points or delta == 0: return points
    shifted = [(t + delta, g, s) for t, g, s in points]
    kept = [p for p in shifted if p[0] >= 0]
    if len(kept) < len(shifted) and (not kept or kept[0][0] > 0):
        kept.insert(0, (0., lane_value(points, -delta), _segment_shape(points, -delta)))
    return tuple(kept)


def cut_points(points, low, high):
    """Points inside [low, high] (high None: to the end), with nodes at the cuts on the curve."""
    if not points: return points
    inside = [p for p in points if p[0] >= low and (high is None or p[0] <= high)]
    if not inside or inside[0][0] > low:
        inside.insert(0, (low, lane_value(points, low), _segment_shape(points, low)))
    if high is not None and inside[-1][0] < high:
        inside.append((high, lane_value(points, high), 'linear'))
    return tuple(inside)


def add_gain_node(points, duration, seconds, gain):
    """Points with a node at (seconds, gain), and its index. A first node also adds level ends at 1."""
    seconds, gain = min(max(0., float(seconds)), duration), min(1., max(0., float(gain)))
    nodes = list(points) or [(0., 1., 'linear'), (duration, 1., 'linear')]
    same = next((i for i, p in enumerate(nodes) if abs(p[0] - seconds) < 1e-6), None)
    if same is not None:
        nodes[same] = (nodes[same][0], gain, nodes[same][2]); return tuple(nodes), same
    shape = _segment_shape(nodes, seconds) if nodes[0][0] <= seconds else 'linear'
    nodes.append((seconds, gain, shape)); nodes.sort(key=lambda p: p[0])
    return tuple(nodes), next(i for i, p in enumerate(nodes) if p[0] == seconds and p[1] == gain)


def move_gain_node(points, index, seconds, gain):
    """The node moved, kept between its neighbours in time and within 0..1."""
    low = points[index - 1][0] if index > 0 else 0.
    high = points[index + 1][0] if index + 1 < len(points) else math.inf
    node = (min(max(float(seconds), low), high), min(1., max(0., float(gain))), points[index][2])
    return points[:index] + (node,) + points[index+1:]


def delete_gain_node(points, index):
    nodes = points[:index] + points[index+1:]
    return nodes if len(nodes) > 1 or not nodes else ()  # A single node left would set the whole clip.


def set_gain_shape(points, index, shape):
    if shape not in GAIN_LANE_SHAPES: raise ValueError('Invalid gain lane segment.')
    return points[:index] + ((*points[index][:2], shape),) + points[index+1:]


def describe(before, after):
    """Undo label for a change of audio_clips (tuples of AudioClip)."""
    removed = [c for c in before if c not in after]
    added = [c for c in after if c not in before]
    relink = {'path', 'missing', 'name', 'size', 'fingerprint', 'source_duration', 'duration', 'fade_in', 'fade_out'}
    if len(before) == len(after) and removed and all(
            {f for f in AudioClip.__dataclass_fields__ if getattr(a, f) != getattr(b, f)} <= relink
            for a, b in zip(before, after)) and any(a.path != b.path for a, b in zip(before, after)):
        return 'Relink audio file' if len({b.path for a, b in zip(before, after) if a != b}) == 1 else 'Relink audio files'
    if len(after) > len(before):
        if len(added) == 2 and len(removed) == 1 and all(c.path == removed[0].path for c in added) \
                and abs(sum(c.duration for c in added) - removed[0].duration) < 1e-6:
            return 'Split audio clip'
        if len(added) == 1 and not removed and any(replace(added[0], start=c.start, track=c.track) == c for c in before):
            return 'Duplicate audio clip'
        return 'Add audio clip' if len(after) == len(before) + 1 else 'Add audio clips'
    if len(after) < len(before): return 'Delete audio clip'
    if len(added) != 1 or len(removed) != 1: return 'Edit audio clips'
    a, b = removed[0], added[0]
    changed = {field for field in AudioClip.__dataclass_fields__ if getattr(a, field) != getattr(b, field)}
    if changed == {'muted'}: return 'Mute audio clip' if b.muted else 'Unmute audio clip'
    for fields, label in (({'start', 'track'}, 'Move audio clip'), ({'fade_in', 'fade_out'}, 'Fade audio clip'),
                          ({'gain_points'}, 'Edit audio gain lane'), ({'gain'}, 'Change audio clip gain'),
                          ({'routing'}, 'Change audio routing')):
        if changed and changed <= fields: return label
    if changed and changed <= {'start', 'offset', 'duration', 'fade_in', 'fade_out', 'gain_points'}: return 'Trim audio clip'
    return 'Edit audio clip'
