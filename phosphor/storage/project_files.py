"""Composer project files: embedded scenes and linked audio media in JSON.

Every clip embeds its whole scene as a scene document (objects, transforms, path
windows, parametric equations, builder drawings including imported SVGs, and OBJ
model geometry), so scenes rebuild without any other file. Audio clips link to
external media by relative/absolute path and fingerprint. A MIDI file named by
a clip's Sandbox source is not needed: in Composer the Notes track drives the pitch.
"""
from dataclasses import replace
import json
import os
from pathlib import Path

from phosphor.engine.composition import Project, Note, Clip, validate_project, MODES
from phosphor.engine.automation import points_document, read_points
from phosphor.engine.track_effects import MAX_EFFECTS, new_effect, with_lane, with_value
from phosphor.engine.audio_clips import (AudioClip, AudioTrack, MAX_AUDIO_CLIPS, MAX_AUDIO_TRACKS,
                                         validate_audio_clip, validate_audio_track, resolve_audio_clip)
from phosphor.storage.app_files import scene_document, scene_from_document, atomic_json

PROJECT_FORMAT = 'phosphor-project'
# 7: clip mute; 8: audio gain lanes and file lengths; 9: audio clip mute.
# 10: audio tracks (clips nested in 'audio_tracks'). 11: track pan, phase, delay and lanes.
# 12: ordered audio track effects, parameter lanes and bypass.
PROJECT_VERSION = 12
TRACK_MIX_FIELDS = ('pan', 'pan_points', 'phase_mode', 'phase', 'delay_ms', 'phase_points')
TRACK_EFFECT_FIELDS = ('effects', 'effects_bypass')
PROJECT_SUFFIX = '.project.json'
WORKSPACE_PATH = None  # Set below; kept separate from appearance preferences (Defaults never resets it).


def audio_clip_document(clip, base):
    relative = None
    if base is not None:
        # clip.path is resolved, so resolve the folder too: on macOS /var is a link to
        # /private/var, and an unresolved folder gives a ../../private/... detour.
        try: relative = Path(os.path.relpath(clip.path, Path(base).resolve())).as_posix()
        except ValueError: pass  # Different Windows drives.
    return dict(name=clip.name, path=relative, absolute=clip.path, size=clip.size,
                fingerprint=clip.fingerprint, start=clip.start, duration=clip.duration,
                offset=clip.offset, gain=clip.gain, fade_in=clip.fade_in,
                fade_out=clip.fade_out, routing=clip.routing,
                **({'gain_points': points_document(clip.gain_points)} if clip.gain_points else {}),
                **({'source_duration': clip.source_duration} if clip.source_duration is not None else {}),
                **({'muted': True} if clip.muted else {}))


def audio_clip_from_document(item, version, path=None, notices=None):
    required_audio = {'name', 'path', 'absolute', 'size', 'fingerprint', 'start', 'duration'}
    defaults = {'offset': 0., 'gain': 1., 'fade_in': 0., 'fade_out': 0., 'routing': 'stereo'}
    later = {'gain_points', 'source_duration'} if version >= 8 else set()
    if version >= 9: later.add('muted')
    if not isinstance(item, dict) or set(item) - set(defaults) - later != required_audio:
        raise ValueError('Invalid audio clip fields.')
    if item['path'] is not None and not isinstance(item['path'], str):
        raise ValueError('Audio clip relative path must be text or null.')
    if type(item.get('muted', False)) is not bool: raise ValueError('Audio clip mute must be true or false.')
    clip = AudioClip(item['start'], item['absolute'], item['name'], item['size'],
                     item['fingerprint'], item['duration'],
                     **{key: item.get(key, value) for key, value in defaults.items()},
                     gain_points=read_points(item['gain_points'], 'Audio clip gain lane') if 'gain_points' in item else (),
                     source_duration=item.get('source_duration'), muted=item.get('muted', False))
    validate_audio_clip(clip)
    if path is not None: clip = resolve_audio_clip(clip, path, item['path'])
    if clip.missing and notices is not None: notices.append(f'Audio file not found: {clip.name}')
    return clip


def _effect_document(effect):
    item = {'kind': effect.kind}
    if effect.values: item['values'] = dict(effect.values)
    if effect.lanes: item['lanes'] = {name: points_document(nodes) for name, nodes in effect.lanes}
    if effect.bypass: item['bypass'] = True
    return item


def _read_effect(item):
    if not isinstance(item, dict) or set(item) - {'values', 'lanes', 'bypass'} != {'kind'}:
        raise ValueError('Invalid audio effect fields.')
    effect = new_effect(item['kind'])
    for key in ('values', 'lanes'):
        entries = item.get(key, {})
        if not isinstance(entries, dict): raise ValueError(f'Effect {key} must be an object.')
        for name, value in entries.items():
            if key == 'values':
                effect = with_value(effect, name, value)
            else:
                if not isinstance(value, list) or any(not isinstance(p, list) for p in value):
                    raise ValueError(f'Invalid {name} lane nodes.')
                effect = with_lane(effect, name, tuple(tuple(p) for p in value))
    return replace(effect, bypass=item.get('bypass', False))


def track_mix_document(track):
    fields = {}
    for key in TRACK_MIX_FIELDS:
        value = getattr(track, key)
        if value != getattr(AudioTrack, key):
            fields[key] = points_document(value) if key.endswith('_points') else value
    if track.effects: fields['effects'] = [_effect_document(e) for e in track.effects]
    if track.effects_bypass: fields['effects_bypass'] = True
    return fields


def track_from_document(item):
    fields = {key: item[key] for key in ('name', 'gain', 'muted', 'collapsed', *TRACK_MIX_FIELDS)
              if key in item}
    effects = item.get('effects', [])
    if not isinstance(effects, list) or len(effects) > MAX_EFFECTS:
        raise ValueError(f'Expected a list of at most {MAX_EFFECTS} audio effects.')
    fields.update(effects=tuple(_read_effect(e) for e in effects), effects_bypass=item.get('effects_bypass', False))
    for kind in ('pan', 'phase'):
        key = kind + '_points'
        if key in fields:
            raw = fields[key]  # Strict: values outside 0..1 are refused, not clamped (read_points clamps legacy gains).
            if not isinstance(raw, list) or any(not isinstance(p, list) or len(p) != 3 or type(p[1]) not in (int, float)
                                                or not 0 <= p[1] <= 1 for p in raw):
                raise ValueError(f'Audio track {kind} lane: invalid nodes.')
            fields[key] = read_points(raw, f'Audio track {kind} lane')
    return validate_audio_track(AudioTrack(**fields))


def project_document(project, base=None):
    validate_project(project)
    return dict(format=PROJECT_FORMAT, version=PROJECT_VERSION, bpm=project.bpm, chords=project.chords, velocity=project.velocity,
                **({'audio_tracks': [dict(name=t.name, clips=[audio_clip_document(c, base) for c in project.audio_clips if c.track == i],
                                          **({'gain': t.gain} if t.gain != 1 else {}),
                                          **({'muted': True} if t.muted else {}),
                                          **({'collapsed': True} if t.collapsed else {}), **track_mix_document(t))
                                     for i, t in enumerate(project.audio_tracks)]} if project.audio_tracks else {}),
                loop=dict(enabled=project.loop, start=project.loop_start, end=project.loop_end),
                notes=[[n.start, n.length, n.pitch, n.velocity] + ([n.glide] if n.glide else []) for n in project.notes],
                **({'markers': [list(marker) for marker in project.markers]} if project.markers else {}),
                **({key: [list(entry) for entry in getattr(project, key)] for key in ('tempo_changes', 'meter_changes') if getattr(project, key)}),
                clips=[dict(name=c.name, start=c.start, length=c.length, local_time=c.local_time,
                            **({'color': c.color} if c.color is not None else {}),
                            **({'time_offset': c.time_offset} if c.time_offset else {}),
                            **({'muted': True} if c.muted else {}),
                            scene=scene_document(c.settings, c.mode, ''),
                            **({'lanes': {name: points_document(points) for name, points in c.lanes}} if c.lanes else {})) for c in project.clips])


def project_from_document(data, notices=None, path=None):
    if not isinstance(data, dict) or data.get('format') != PROJECT_FORMAT or type(data.get('version')) is not int or data.get('version') not in range(1, PROJECT_VERSION + 1):
        raise ValueError('Unsupported project file format/version.')
    required = {'format', 'version', 'bpm', 'loop', 'notes', 'clips'}
    if data['version'] >= 4: required.add('chords')
    if data['version'] >= 6: required.add('velocity')
    optional = {'markers', 'tempo_changes', 'meter_changes'}
    version = data['version']
    if version >= 10:
        if 'audio_clips' in data: raise ValueError('Audio clips are stored in audio tracks from project version 10.')
        optional.add('audio_tracks')
    else:
        if 'audio_tracks' in data: raise ValueError('Audio tracks require project version 10.')
        if version >= 5: optional.add('audio_clips')
    if set(data) - optional != required:
        raise ValueError('Project fields are missing or unsupported.')
    maps = {}
    for key, size in (('tempo_changes', 2), ('meter_changes', 3)):
        entries = data.get(key, [])
        if key in data and data['version'] < 3:
            raise ValueError('Tempo and meter changes require project version 3.')
        if not isinstance(entries, list) or any(not isinstance(entry, list) or len(entry) != size for entry in entries):
            raise ValueError('Invalid tempo or meter changes.')
        maps[key] = tuple(tuple(entry) for entry in entries)
    markers = data.get('markers', [])
    if 'markers' in data and data['version'] < 3:
        raise ValueError('Markers require project version 3.')
    if not isinstance(markers, list) or any(not isinstance(m, list) or len(m) != 2 for m in markers):
        raise ValueError('Invalid project markers.')
    loop = data['loop']
    if not isinstance(loop, dict) or set(loop) != {'enabled', 'start', 'end'}: raise ValueError('Invalid project loop.')
    if not isinstance(data['notes'], list) or not isinstance(data['clips'], list): raise ValueError('Project notes and clips must be lists.')
    notes = []
    for item in data['notes']:
        if not isinstance(item, list) or len(item) not in (4, 5): raise ValueError('Invalid note.')
        if len(item) == 5 and data['version'] < 3: raise ValueError('Glide requires project version 3.')
        notes.append(Note(*item))
    clips = []
    for item in data['clips']:
        if not isinstance(item, dict) or set(item) - {'lanes', 'time_offset', 'color', 'muted'} != {'name', 'start', 'length', 'local_time', 'scene'}: raise ValueError('Invalid clip.')
        if 'color' in item and data['version'] < 3:
            raise ValueError('Clip colour requires project version 3.')
        if 'muted' in item and (data['version'] < 7 or type(item['muted']) is not bool):
            raise ValueError('Clip mute requires project version 7 and true or false.')
        if 'time_offset' in item and data['version'] < 2:
            raise ValueError('Clip time offset requires project version 2.')
        lanes = ()
        if 'lanes' in item:
            if data['version'] < 2: raise ValueError('Clip lanes require project version 2.')
            if not isinstance(item['lanes'], dict): raise ValueError('Invalid clip lanes.')
            lanes = tuple(sorted((name, read_points(points, 'Clip lanes')) for name, points in item['lanes'].items()))
        settings, mode, _ = scene_from_document(item['scene'], notices=notices)
        if mode not in MODES: raise ValueError('Invalid clip listening mode.')
        clips.append(Clip(item['start'], item['length'], settings, mode, item['name'], item['local_time'], lanes,
                          time_offset=item.get('time_offset', 0.), color=item.get('color'), muted=item.get('muted', False)))
    tracks = []
    items = data.get('audio_clips', [])
    if not isinstance(items, list): raise ValueError('Audio clips must be a list.')
    audio_clips = [audio_clip_from_document(item, version, path, notices) for item in items]
    if version >= 10:
        items = data.get('audio_tracks', [])
        if not isinstance(items, list): raise ValueError('Invalid audio track fields.')
        if len(items) > MAX_AUDIO_TRACKS: raise ValueError(f'Use at most {MAX_AUDIO_TRACKS} audio tracks.')
        for i, item in enumerate(items):
            if version < 12 and isinstance(item, dict) and set(item).intersection(TRACK_EFFECT_FIELDS):
                raise ValueError('Audio track effects require project version 12.')
            if version < 11 and isinstance(item, dict) and set(item).intersection(TRACK_MIX_FIELDS):
                raise ValueError('Audio track pan and phase fields require project version 11.')
            if (not isinstance(item, dict) or set(item) - {'gain', 'muted', 'collapsed', *TRACK_MIX_FIELDS, *TRACK_EFFECT_FIELDS} != {'name', 'clips'}
                    or not isinstance(item['clips'], list)):
                raise ValueError('Invalid audio track fields.')
            if len(audio_clips) + len(item['clips']) > MAX_AUDIO_CLIPS:
                raise ValueError(f'Use at most {MAX_AUDIO_CLIPS} audio clips.')
            tracks.append(track_from_document(item))
            audio_clips.extend(replace(audio_clip_from_document(c, version, path, notices), track=i) for c in item['clips'])
    # Canonical order for every version (Codex review): the app always wrote clips in start order, so
    # only a hand-edited older file changes its summing order here, once, on open.
    audio_clips.sort(key=lambda c: (c.start, c.track))
    try:
        project = Project(data['bpm'], tuple(notes), tuple(clips), loop['enabled'], loop['start'], loop['end'],
                          markers=tuple(tuple(m) for m in markers), chords=data.get('chords', False),
                          velocity=data.get('velocity', False),
                          audio_clips=tuple(audio_clips), audio_tracks=tuple(tracks), **maps)
        return validate_project(project)
    except (TypeError, KeyError, OverflowError) as error:
        raise ValueError(f'Invalid project: {error}') from error


def save_project(path, project):
    document = project_document(project, Path(path).parent)
    atomic_json(path, document)
    return document


def read_project(path, notices=None):
    return project_from_document(json.loads(Path(path).read_text(encoding='utf-8')), notices, path)


def read_workspace(path=None):
    """The last workspace (Composer or Sandbox); Composer when never chosen or unreadable."""
    from phosphor.engine.composition import WORKSPACES, COMPOSER
    path = Path(path or WORKSPACE_PATH)
    try:
        value = json.loads(path.read_text(encoding='utf-8')).get('workspace')
        return value if value in WORKSPACES else COMPOSER
    except (OSError, ValueError, AttributeError):
        return COMPOSER


def write_workspace(name, path=None):
    atomic_json(Path(path or WORKSPACE_PATH), dict(workspace=name))


from phosphor.storage.app_paths import USER_DIR  # noqa: E402
WORKSPACE_PATH = USER_DIR / 'workspace.json'
