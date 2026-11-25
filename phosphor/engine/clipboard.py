"""Qt-free, validated Composer clipboard documents and immutable paste helpers."""
from dataclasses import replace
import json
import math

from phosphor.engine.composition import (
    Note, Clip, validate_project, MAX_NOTES, MAX_CLIPS, MAX_BEATS,
    PITCH_RANGE, MODES,
)
from phosphor.engine.automation import (
    validate_points, points_document, read_points, MAX_POINTS,
    CLIP_LANE_FIELDS, validate_lanes,
)
from phosphor.engine.audio_clips import MAX_AUDIO_CLIPS, MAX_AUDIO_TRACKS, resolve_audio_clip
from phosphor.engine.audio_tracks import insert_tracks
from phosphor.storage.app_files import scene_document, scene_from_document
from phosphor.storage.project_files import (PROJECT_VERSION, TRACK_EFFECT_FIELDS, TRACK_MIX_FIELDS, audio_clip_document,
                                            audio_clip_from_document, track_from_document, track_mix_document)

FORMAT = 'phosphor-composer-clipboard'
VERSION = 1
MIME = 'application/x-phosphor-composer'
MAX_TEXT = 64 * 1024 * 1024


def _number(value):
    try:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('Expected a finite number.')
    except OverflowError as error:
        raise ValueError('Expected a finite number.') from error
    return value


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) <= set(required) | set(optional):
        raise ValueError('Missing or unsupported clipboard fields.')


def _scene(value, notices=None):
    try:
        return scene_from_document(value, notices=notices)
    except Exception as error:
        raise ValueError(f'Invalid clipboard scene: {error}') from error


def _lanes(item):
    lanes = item.get('lanes', {})
    if not isinstance(lanes, dict):
        raise ValueError('Invalid clip lanes.')
    result = tuple(sorted((field, read_points(points, 'Clipboard lanes'))
                          for field, points in lanes.items()))
    validate_lanes(result, CLIP_LANE_FIELDS, 'Clipboard lanes')
    return result


def _validate(payload, kind=None, notices=None):
    _keys(payload, ('format', 'version', 'kind'), ('notes', 'clips', 'nodes', 'tracks'))
    selected = payload['kind']
    if (payload['format'] != FORMAT or type(payload['version']) is not int
            or payload['version'] != VERSION or selected not in ('notes', 'clips', 'nodes', 'tracks')
            or (kind is not None and selected != kind)):
        raise ValueError('Unsupported clipboard format, version or kind.')
    _keys(payload, ('format', 'version', 'kind', selected))
    items = payload[selected]
    limit = {'notes': MAX_NOTES, 'clips': MAX_CLIPS, 'nodes': MAX_POINTS, 'tracks': MAX_AUDIO_TRACKS}[selected]
    if not isinstance(items, list) or not 0 < len(items) <= limit:
        raise ValueError('Invalid clipboard item count.')
    if selected == 'nodes':
        read_points(items, 'Clipboard nodes')
        if items[0][0] != 0:
            raise ValueError('Clipboard nodes must begin at zero.')
    elif selected == 'tracks':
        count = 0
        for item in items:
            _keys(item, ('name', 'gain', 'muted', 'collapsed', 'clips'), (*TRACK_MIX_FIELDS, *TRACK_EFFECT_FIELDS))
            track_from_document(item)
            if not isinstance(item['clips'], list): raise ValueError('Audio clips must be a list.')
            count += len(item['clips'])
            if count > MAX_AUDIO_CLIPS: raise ValueError('Too many audio clips.')
            for clip in item['clips']: audio_clip_from_document(clip, PROJECT_VERSION)
    elif selected == 'notes':
        for item in items:
            if not isinstance(item, list) or len(item) not in (4, 5):
                raise ValueError('Invalid clipboard note.')
            start, length, pitch, velocity = item[:4]
            if len(item) == 5 and not 0 <= _number(item[4]) <= _number(length):
                raise ValueError('Invalid clipboard glide.')
            if (not 0 <= _number(start) or not 0 < _number(length)
                    or start + length > MAX_BEATS or type(pitch) is not int
                    or not PITCH_RANGE[0] <= pitch <= PITCH_RANGE[1]
                    or not 0 <= _number(velocity) <= 1):
                raise ValueError('Invalid clipboard note values.')
    else:
        previous = -1
        for item in items:
            _keys(item, ('offset', 'length', 'name', 'mode', 'local_time', 'scene'), ('lanes', 'time_offset', 'color', 'muted'))
            color = item.get('color')
            if type(item.get('muted', False)) is not bool: raise ValueError('Invalid clip mute.')
            if color is not None and (type(color) is not int or not 0 <= color < 8):
                raise ValueError('Invalid clip colour.')
            if abs(_number(item.get('time_offset', 0.))) > MAX_BEATS:
                raise ValueError('Invalid clipboard clip time offset.')
            offset, length = _number(item['offset']), _number(item['length'])
            if (offset < 0 or offset < previous or length <= 0 or offset + length > MAX_BEATS
                    or item['mode'] not in MODES or not isinstance(item['name'], str)
                    or len(item['name']) > 64 or type(item['local_time']) is not bool):
                raise ValueError('Invalid clipboard clip values.')
            previous = offset
            _lanes(item)
            settings, mode, device = _scene(item['scene'], notices)
            if item['scene']['version'] < 14:
                item['scene'] = scene_document(settings, mode, device)
    return payload


def _payload(kind, items):
    return _validate(dict(format=FORMAT, version=VERSION, kind=kind, **{kind: items}))


def _selection(items, indices):
    try:
        indices = tuple(indices)
    except TypeError as error:
        raise ValueError('Invalid clipboard selection.') from error
    if not indices or any(type(i) is not int or not 0 <= i < len(items) for i in indices):
        raise ValueError('Invalid clipboard selection.')
    return [items[i] for i in sorted(set(indices))]


def copy_notes(project, indices):
    validate_project(project)
    notes = sorted(_selection(project.notes, indices), key=lambda n: (n.start, n.pitch))
    return _payload('notes', [[n.start - notes[0].start, n.length, n.pitch, n.velocity] +
                             ([n.glide] if n.glide else []) for n in notes])


def copy_clips(project, indices):
    validate_project(project)
    clips = sorted(_selection(project.clips, indices), key=lambda c: c.start)
    return _payload('clips', [dict(offset=c.start - clips[0].start, length=c.length,
        name=c.name, mode=c.mode, local_time=c.local_time,
        **({'color': c.color} if c.color is not None else {}),
        **({'time_offset': c.time_offset} if c.time_offset else {}),
        **({'muted': True} if c.muted else {}),
        **({'lanes': {field: points_document(points) for field, points in c.lanes}} if c.lanes else {}),
        scene=scene_document(c.settings, c.mode, '')) for c in clips])


def copy_tracks(project, tracks):
    validate_project(project)
    items = []
    for i in _selection(range(len(project.audio_tracks)), tracks):
        t = project.audio_tracks[i]
        items.append(dict(name=t.name, gain=t.gain, muted=t.muted, collapsed=t.collapsed, **track_mix_document(t),
                          clips=[audio_clip_document(c, None) for c in project.audio_clips if c.track == i]))
    return _payload('tracks', items)


def paste_tracks(project, payload, at=None):
    """Insert the payload's tracks and clips at `at`. Each distinct file is checked once (a file the
    project already plays is not read again), after the limits are known to allow the paste."""
    items = _validate(payload, 'tracks')['tracks']
    tracks = tuple(track_from_document(item) for item in items)
    clips = [(i, audio_clip_from_document(c, PROJECT_VERSION)) for i, item in enumerate(items) for c in item['clips']]
    if len(project.audio_tracks) + len(tracks) > MAX_AUDIO_TRACKS: raise ValueError(f'Use at most {MAX_AUDIO_TRACKS} audio tracks.')
    if len(project.audio_clips) + len(clips) > MAX_AUDIO_CLIPS: raise ValueError('Too many audio clips.')
    found = {(c.path, c.fingerprint): False for c in project.audio_clips if not c.missing}
    resolved = []
    for i, clip in clips:
        key = (clip.path, clip.fingerprint)
        if key not in found: found[key] = resolve_audio_clip(clip, None, None).missing
        resolved.append(replace(clip, missing=found[key], track=i))
    return insert_tracks(project, tracks, tuple(resolved), at)


def copy_nodes(points, indices):
    validate_points(points, 'Clipboard nodes')
    nodes = sorted(_selection(points, indices))
    return _payload('nodes', [[p[0] - nodes[0][0], *p[1:]] for p in nodes])


def to_text(payload):
    _validate(payload)
    try:
        text = json.dumps(payload, allow_nan=False, separators=(',', ':'))
    except (TypeError, OverflowError, RecursionError) as error:
        raise ValueError(f'Invalid clipboard document: {error}') from error
    if len(text) > MAX_TEXT:
        raise ValueError('Clipboard text is too long.')
    return text


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate clipboard field.')
        result[key] = value
    return result


def from_text(text, notices=None):
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise ValueError('Invalid clipboard text or size.')
    try:
        return _validate(json.loads(text, object_pairs_hook=_object,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON number.'))), notices=notices)
    except Exception as error:
        raise ValueError('Invalid clipboard document.') from error


def paste_notes(project, payload, beat):
    validate_project(project)
    _validate(payload, 'notes')
    beat = _number(beat)
    if len(project.notes) + len(payload['notes']) > MAX_NOTES:
        raise ValueError('Too many notes.')
    notes = tuple(Note(max(0, min(beat + item[0], MAX_BEATS - item[1])), *item[1:])
                  for item in payload['notes'])
    result = validate_project(replace(project, notes=project.notes + notes))
    return result, tuple(range(len(project.notes), len(result.notes)))


def paste_clips(project, payload, beat):
    validate_project(project)
    _validate(payload, 'clips')
    beat = _number(beat)
    clips, pasted = list(project.clips), []
    previous = 0
    for item in payload['clips']:
        start = max(0, beat + item['offset'], previous)
        for clip in sorted(clips, key=lambda c: c.start):
            if clip.start <= start < clip.end:
                start = clip.end
        following = [c.start for c in clips if c.start > start]
        length = min(item['length'], min(following, default=MAX_BEATS) - start, MAX_BEATS - start)
        if length <= 1e-6:
            continue
        settings, _, _ = _scene(item['scene'])
        clip = Clip(start, length, settings, item['mode'], item['name'], item['local_time'], _lanes(item),
                    time_offset=item.get('time_offset', 0.), color=item.get('color'), muted=item.get('muted', False))
        clips.append(clip)
        pasted.append(clip)
        previous = clip.end
    if not pasted:
        raise ValueError('There is no room for the clips there.')
    if len(clips) > MAX_CLIPS:
        raise ValueError('Too many clips.')
    clips.sort(key=lambda c: c.start)
    result = validate_project(replace(project, clips=tuple(clips)))
    identities = {id(c) for c in pasted}
    return result, tuple(i for i, c in enumerate(clips) if id(c) in identities)


def paste_nodes(points, payload, beat, length):
    validate_points(points, 'Automation nodes')
    _validate(payload, 'nodes')
    beat, length = _number(beat), _number(length)
    if not 0 <= length <= MAX_BEATS:
        raise ValueError('Invalid lane length.')
    pasted = tuple((beat + p[0], *p[1:]) for p in payload['nodes'] if 0 <= beat + p[0] <= length)
    if not pasted:
        raise ValueError('There is no room for the automation nodes there.')
    first, last = pasted[0][0], pasted[-1][0]
    kept = tuple(p for p in points if not first - 1e-9 <= p[0] <= last + 1e-9)
    result = tuple(sorted(kept + pasted, key=lambda point: point[0]))  # Stable: keeps jump pairs in order.
    validate_points(result, 'Automation nodes')
    beats = {p[0] for p in pasted}
    return result, tuple(i for i, p in enumerate(result) if p[0] in beats)


def describe(payload):
    _validate(payload)
    kind = payload['kind']
    count = len(payload[kind])
    noun = {'notes': 'note', 'clips': 'clip', 'nodes': 'automation node', 'tracks': 'audio track'}[kind]
    return f'{count} {noun}{"s" if count != 1 else ""}'
