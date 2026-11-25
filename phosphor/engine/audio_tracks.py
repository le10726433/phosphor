"""Composer track edits. Each edit returns a validated project, with clips following their tracks."""
from collections import Counter
from dataclasses import replace

from phosphor.engine.audio_clips import (AudioTrack, GAIN_RANGE, MAX_AUDIO_CLIPS, MAX_AUDIO_TRACKS, TRACK_NAME_LIMIT,
                                         validate_audio_track)
from phosphor.engine.audio_edit import place
from phosphor.engine.composition import validate_project


def _indices(count, tracks):
    picks = tuple(tracks)
    if any(type(i) is not int or not 0 <= i < count for i in picks):
        raise ValueError('That audio track does not exist.')
    return tuple(sorted(set(picks)))


def track_clips(project, t):
    _indices(len(project.audio_tracks), (t,))
    return tuple(i for i, c in enumerate(project.audio_clips) if c.track == t)


def default_name(project):
    names = {track.name.casefold() for track in project.audio_tracks}
    n = 1
    while f'audio {n}' in names: n += 1
    return f'Audio {n}'


def insert_tracks(project, tracks, clips, at=None):
    old = project.audio_tracks
    if at is None: at = len(old)
    _indices(len(old) + 1, (at,))
    if len(old) + len(tracks) > MAX_AUDIO_TRACKS: raise ValueError('Use at most 32 audio tracks.')
    if len(project.audio_clips) + len(clips) > MAX_AUDIO_CLIPS: raise ValueError('Too many audio clips.')
    if any(type(c.track) is not int or not 0 <= c.track < len(tracks) for c in clips):
        raise ValueError('An audio clip is on a track that does not exist.')
    shifted = [replace(c, track=c.track + len(tracks)) if c.track >= at else c for c in project.audio_clips]
    shifted.extend(replace(c, track=c.track + at) for c in clips)
    project = replace(project, audio_tracks=old[:at] + tracks + old[at:])
    return place(project, shifted)[0], tuple(range(at, at + len(tracks)))


def add_track(project, at=None, name=None):
    project, indices = insert_tracks(project, (AudioTrack(name or default_name(project)),), (), at)
    return project, indices[0]


def _ordered(project, order):
    positions = {old: new for new, old in enumerate(order)}
    clips = [replace(c, track=positions[c.track]) for c in project.audio_clips if c.track in positions]
    tracks = tuple(project.audio_tracks[i] for i in order)
    return place(replace(project, audio_tracks=tracks, audio_clips=()), clips)[0]


def reorder(project, order):
    try: order = tuple(order)
    except TypeError as error: raise ValueError('Invalid track order.') from error
    if any(type(i) is not int for i in order) or sorted(order) != list(range(len(project.audio_tracks))):
        raise ValueError('Invalid track order.')
    return _ordered(project, order)


def remove_tracks(project, tracks):
    count = len(project.audio_tracks)
    picks = _indices(count, tracks)
    return _ordered(project, [i for i in range(count) if i not in picks])


def moved_order(count, picks, target):
    picks = _indices(count, picks)
    kept = tuple(i for i in range(count) if i not in picks)
    at = min(len(kept), max(0, target))
    return kept[:at] + picks + kept[at:], tuple(range(at, at + len(picks)))


def duplicate_tracks(project, tracks):
    picks = _indices(len(project.audio_tracks), tracks)
    if not picks: return validate_project(project), ()
    headers = tuple(replace(project.audio_tracks[i], name=copy_name(project.audio_tracks[i].name)) for i in picks)
    clips = tuple(replace(c, track=picks.index(c.track)) for c in project.audio_clips if c.track in picks)
    return insert_tracks(project, headers, clips, picks[-1] + 1)


def copy_name(name):
    """A duplicate's name: the name shortened if needed so ' copy' always fits (describe relies on it)."""
    return name[:TRACK_NAME_LIMIT - 5].rstrip() + ' copy'


def _set(project, tracks, **fields):
    """Header fields of the picked tracks. Only those tracks are validated: notes and clips are
    unchanged, so a gain drag on a 20,000-note song does not revalidate the score per mouse move."""
    picks = _indices(len(project.audio_tracks), tracks)
    headers = tuple(validate_audio_track(replace(t, **fields)) if i in picks else t for i, t in enumerate(project.audio_tracks))
    return project if headers == project.audio_tracks else replace(project, audio_tracks=headers)


def rename_track(project, t, name):
    if not isinstance(name, str) or not name.strip(): raise ValueError('An audio track needs a name.')
    return _set(project, (t,), name=name.strip()[:TRACK_NAME_LIMIT])


def set_gain(project, tracks, gain):
    return _set(project, tracks, gain=min(GAIN_RANGE[1], max(GAIN_RANGE[0], float(gain))))


def set_muted(project, tracks, muted):
    return _set(project, tracks, muted=bool(muted))


def set_pan(project, tracks, pan):
    return _set(project, tracks, pan=pan)


def set_phase_mode(project, tracks, mode):
    return _set(project, tracks, phase_mode=mode)


def set_phase(project, tracks, degrees):
    return _set(project, tracks, phase=degrees)


def set_delay(project, tracks, ms):
    return _set(project, tracks, delay_ms=ms)


def set_lane(project, t, kind, points):
    if kind not in ('pan', 'phase'):
        raise ValueError('Audio track lane must be pan or phase.')
    return _set(project, (t,), **{kind + '_points': points})


def set_effects(project, t, effects, bypass=False):
    """Track t's effect chain (AW3): an ordered tuple of AudioEffect and the chain bypass."""
    if not isinstance(effects, tuple): raise ValueError('Audio track effects must be a tuple.')
    return _set(project, (t,), effects=effects, effects_bypass=bool(bypass))


def set_collapsed(project, tracks, collapsed):
    return _set(project, tracks, collapsed=bool(collapsed))


def describe(before, after):
    """Undo label inferred from two projects. Ordinary clip edits fall through to audio_edit."""
    old, new = before.audio_tracks, after.audio_tracks
    count = len(new) - len(old)
    if count > 0:
        added = (Counter(new) - Counter(old)).elements()
        names = {t.name for t in old}
        copied = all(t.name.endswith(' copy') and any(n.startswith(t.name[:-5]) for n in names) for t in added)
        return ('Duplicate' if copied else 'Add') + (' audio track' if count == 1 else ' audio tracks')
    if count < 0: return 'Delete audio track' if count == -1 else 'Delete audio tracks'
    if old == new:
        if len(before.audio_clips) != len(after.audio_clips): return None
        if before.audio_clips == after.audio_clips: return None
        original, edited = Counter(before.audio_clips), Counter(after.audio_clips)
        # First cancel unchanged copies, then horizontal drags (which can reorder flat indices).
        removed = Counter(replace(c, start=0.) for c in (original - edited).elements())
        added = Counter(replace(c, start=0.) for c in (edited - original).elements())
        departures = Counter(replace(c, track=0) for c in (removed - added).elements())
        arrivals = Counter(replace(c, track=0) for c in (added - removed).elements())
        moved = (departures & arrivals).total()
        if moved: return 'Move audio clip' if moved == 1 else 'Move audio clips'
        return None
    changed = [(a, b) for a, b in zip(old, new) if a != b]
    suffix = ' audio track' if len(changed) == 1 else ' audio tracks'
    if Counter(old) == Counter(new):
        single = any(old[:i] + old[i+1:] == new[:j] + new[j+1:]
                     for i, a in enumerate(old) for j, b in enumerate(new) if a == b)
        return 'Move audio track' if single else 'Move audio tracks'
    fields = {key for a, b in changed for key in AudioTrack.__dataclass_fields__ if getattr(a, key) != getattr(b, key)}
    if fields == {'name'}: return 'Rename audio track'
    if fields.intersection(('effects', 'effects_bypass')): return 'Change audio track effects'
    for field, label in (('gain', 'gain'), ('pan', 'pan'), ('phase_mode', 'phase mode'),
                         ('phase', 'phase'), ('delay_ms', 'delay'), ('pan_points', 'pan lane'),
                         ('phase_points', 'phase lane')):
        if fields == {field}: return 'Change audio track ' + label
    for key, on, off in (('muted', 'Mute', 'Unmute'), ('collapsed', 'Collapse', 'Expand')):
        if fields == {key}:
            values = {getattr(b, key) for _, b in changed}
            if len(values) == 1: return (on if values.pop() else off) + suffix
    return 'Edit audio tracks'
