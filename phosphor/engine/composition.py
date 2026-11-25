"""Composer projects: a note roll and scene clips on one timeline (no Qt).

The Notes track supports eight-voice chords, or the original monophonic line (the
most recently started sounding note wins) when Project.chords is off. Each voice
restarts its phase unless a legato glide continues its partner's phase.
The Scene track holds clips; each clip carries a whole
scene (objects, transforms, equations, imported drawings and models) and its own mix
and listening mode. A clip's local time t starts at its time_offset unless it follows
the song clock. Rendering is a pure function of song time, like Sandbox scenes, so
seeking, looping and exporting are just a choice of sample times.
"""
from collections import OrderedDict
from dataclasses import dataclass, replace
from functools import lru_cache
import heapq
import math
from threading import Lock
from phosphor.storage.audio_writer import AudioWriter

import numpy as np

from phosphor.engine.signal_engine import Settings, render, shape_parts, select_signal, validate, validate_once
from phosphor.engine.identity_cache import Identity
from phosphor.audio.midi_synth import STEAL_FADE_SECONDS
from phosphor.engine.automation import gain_at, lane_points, validate_lanes, live_lanes, CLIP_LANE_FIELDS
from phosphor.engine.tempo_map import TempoMap, BeatClock, Meter
from phosphor.engine.audio_clips import (AudioClip, AudioTrack, MAX_AUDIO_CLIPS, MAX_AUDIO_TRACKS, validate_audio_clip,
                                         validate_audio_track, mix_audio_clips, audio_clip_end_beats)
from phosphor.engine.track_chain import ChainState
from phosphor.engine.audio_clips import AudioLayout, processing_tail

COMPOSER, SANDBOX = 'Composer', 'Sandbox'
WORKSPACES = (COMPOSER, SANDBOX)
BEATS_PER_BAR = 4
DEFAULT_CLIP_BEATS = 16.  # Four bars.
PITCH_RANGE = (21, 108)  # A0-C8, as the other sources.
BPM_RANGE = (20., 400.)
MAX_NOTES, MAX_CLIPS = 20000, 512
MAX_BEATS = 100000.
ATTACK_SECONDS, RELEASE_SECONDS = .004, .008  # Short ramps at note edges avoid clicks.
MUSIC_LEVEL = .8  # The original sound is a plain sine at the note's pitch, as for Live MIDI.
MODES = ('mixed', 'drawing', 'music')
MAX_CHORD_VOICES = 8
CHORD_CYCLE_MAX = 8
CHORD_CYCLE_TOLERANCE = .05


@dataclass(frozen=True)
class Note:
    start: float  # Beats from the song start.
    length: float  # Beats.
    pitch: int  # MIDI note number.
    velocity: float = 1.0  # Linear loudness and drawing size (MIDI 127).
    glide: float = 0.  # Beats of linear-semitone portamento, for legato notes only.

    @property
    def end(self): return self.start + self.length


@dataclass(frozen=True)
class Clip:
    start: float  # Beats.
    length: float  # Beats; the scene simply runs for as long as the clip lasts.
    settings: Settings  # The clip's whole scene, with its own mix.
    mode: str = 'drawing'  # Listening mode, as in Sandbox (mixed, drawing or music).
    name: str = 'Scene'
    local_time: bool = True  # t starts at time_offset; False follows the song clock.
    lanes: tuple = ()
    time_offset: float = 0.  # Beats of scene time elapsed at the clip start.
    color: int | None = None  # Optional editor palette index; never affects rendering.
    muted: bool = False  # A muted clip renders as a gap (silent, no cycle), like a deactivated clip.

    @property
    def end(self): return self.start + self.length


@dataclass(frozen=True)
class Project:
    bpm: float = 120.
    notes: tuple = ()
    clips: tuple = ()
    loop: bool = False
    loop_start: float = 0.  # Beats.
    loop_end: float = DEFAULT_CLIP_BEATS
    markers: tuple = ()  # (song beat, section name), sorted by beat.
    tempo_changes: tuple = ()
    meter_changes: tuple = ()
    chords: bool = False
    velocity: bool = False
    audio_clips: tuple = ()
    audio_tracks: tuple = ()

    def __post_init__(self):
        if self.audio_clips and self.audio_tracks == ():
            object.__setattr__(self, 'audio_tracks', (AudioTrack(),))

    def seconds(self, beats):
        if not self.tempo_changes: return np.asarray(beats, dtype=float) * 60. / self.bpm
        return TempoMap(self.bpm, self.tempo_changes).seconds(beats)

    def beats(self, seconds):
        if not self.tempo_changes: return np.asarray(seconds, dtype=float) * self.bpm / 60.
        return TempoMap(self.bpm, self.tempo_changes).beats(seconds)

    @property
    def audio_layout(self):
        """Clip time bounds and playable track membership for dry blocks and chain history."""
        value = self.__dict__.get('_audio_layout')
        if value is None:
            value = AudioLayout.from_project(self)
            object.__setattr__(self, '_audio_layout', value)
        return value

    @property
    def end_beats(self):
        """Where the song ends: its last clip or note (at least one bar).

        Computed once per project (P7c): the editor reads it on every playing frame, and the scan
        over every note took 1.1 ms at 20,000 notes. Projects are frozen, so it can't go stale.
        """
        value = self.__dict__.get('_end_beats')
        if value is None:
            ends = [c.end for c in self.clips] + [n.end for n in self.notes]
            ends += [float(audio_clip_end_beats(self, c)) for c in self.audio_clips]
            value = max(ends + [float(BEATS_PER_BAR)])
            object.__setattr__(self, '_end_beats', value)
        return value


def new_project():
    """One empty four-bar clip, so there is always a scene to edit."""
    return Project(clips=(Clip(0., DEFAULT_CLIP_BEATS, empty_settings(), name='Scene 1'),), chords=True, velocity=True)


def empty_settings():
    return replace(Settings(), travel_mode='Faster travel', travel_speed=64.)


def _finite(value, label):
    try:
        finite = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f'{label} must be a finite number.')
    return float(value)


def validate_project(project):
    if not isinstance(project.audio_tracks, tuple) or len(project.audio_tracks) > MAX_AUDIO_TRACKS:
        raise ValueError(f'Use at most {MAX_AUDIO_TRACKS} audio tracks.')
    for track in project.audio_tracks: validate_audio_track(track)
    if not isinstance(project.audio_clips, tuple) or len(project.audio_clips) > MAX_AUDIO_CLIPS:
        raise ValueError(f'Use at most {MAX_AUDIO_CLIPS} audio clips.')
    for clip in project.audio_clips:
        if not isinstance(clip, AudioClip): raise ValueError('Invalid audio clip.')
        validate_audio_clip(clip)
        if clip.track >= len(project.audio_tracks): raise ValueError('An audio clip is on a track that does not exist.')
    if type(project.chords) is not bool: raise ValueError('Chords must be on or off.')
    if type(project.velocity) is not bool: raise ValueError('Velocity must be on or off.')
    TempoMap(project.bpm, project.tempo_changes)
    Meter(project.meter_changes)
    if not isinstance(project.markers, tuple) or len(project.markers) > 256:
        raise ValueError('Use at most 256 markers.')
    previous_marker = 0.
    for marker in project.markers:
        if not isinstance(marker, tuple) or len(marker) != 2:
            raise ValueError('Invalid marker.')
        beat, name = marker
        beat = _finite(beat, 'Marker beat')
        if not previous_marker <= beat <= MAX_BEATS:
            raise ValueError('Markers must be in beat order within the beat limit.')
        if not isinstance(name, str) or len(name) > 64:
            raise ValueError('Marker names must be at most 64 characters.')
        previous_marker = beat
    bpm = _finite(project.bpm, 'Tempo')
    if not BPM_RANGE[0] <= bpm <= BPM_RANGE[1]: raise ValueError(f'Tempo must be {BPM_RANGE[0]:g}–{BPM_RANGE[1]:g} BPM.')
    if not isinstance(project.notes, tuple) or len(project.notes) > MAX_NOTES: raise ValueError(f'Use at most {MAX_NOTES} notes.')
    if not isinstance(project.clips, tuple) or len(project.clips) > MAX_CLIPS: raise ValueError(f'Use at most {MAX_CLIPS} clips.')
    for note in project.notes:
        if not isinstance(note, Note): raise ValueError('Invalid note.')
        start, length = _finite(note.start, 'Note start'), _finite(note.length, 'Note length')
        if not 0 <= start <= MAX_BEATS or not 0 < length <= MAX_BEATS: raise ValueError('Notes need a start ≥ 0 and a positive length.')
        if type(note.pitch) is not int or not PITCH_RANGE[0] <= note.pitch <= PITCH_RANGE[1]:
            raise ValueError(f'Note pitches must be MIDI notes {PITCH_RANGE[0]}–{PITCH_RANGE[1]}.')
        if not 0 <= _finite(note.velocity, 'Note velocity') <= 1: raise ValueError('Note velocity must be 0–1.')
        if not 0 <= _finite(note.glide, 'Note glide') <= length: raise ValueError('Note glide must be between zero and its length.')
    previous = 0.
    for clip in project.clips:
        if not isinstance(clip, Clip) or not isinstance(clip.settings, Settings): raise ValueError('Invalid clip.')
        start, length = _finite(clip.start, 'Clip start'), _finite(clip.length, 'Clip length')
        if abs(_finite(clip.time_offset, 'Clip time offset')) > MAX_BEATS:
            raise ValueError('Clip time offset exceeds the beat limit.')
        if clip.color is not None and (type(clip.color) is not int or not 0 <= clip.color < 8):
            raise ValueError('Clip colour must be a palette index 0–7 or None.')
        if not 0 <= start <= MAX_BEATS or not 0 < length <= MAX_BEATS: raise ValueError('Clips need a start ≥ 0 and a positive length.')
        if start < previous - 1e-9: raise ValueError('Clips must be in time order and must not overlap.')
        previous = start + length
        if clip.mode not in MODES or not isinstance(clip.name, str) or len(clip.name) > 64 or type(clip.local_time) is not bool:
            raise ValueError('Invalid clip name, listening mode or time setting.')
        if type(clip.muted) is not bool: raise ValueError('Clip mute must be on or off.')
        validate(clip.settings)
        validate_lanes(clip.lanes, CLIP_LANE_FIELDS, 'Clip lanes')
    if type(project.loop) is not bool: raise ValueError('Loop must be on or off.')
    if not 0 <= _finite(project.loop_start, 'Loop start') < _finite(project.loop_end, 'Loop end') <= MAX_BEATS:
        raise ValueError('The loop needs a start before its end.')
    return project


def add_marker(project, beat, name='Section'):
    return validate_project(replace(project, markers=tuple(sorted(
        (*project.markers, (beat, name)), key=lambda item: item[0]))))


def move_marker(project, index, beat):
    markers = list(project.markers)
    markers[index] = (beat, markers[index][1])
    return validate_project(replace(project, markers=tuple(sorted(markers, key=lambda item: item[0]))))


def rename_marker(project, index, name):
    markers = list(project.markers)
    markers[index] = (markers[index][0], name)
    return validate_project(replace(project, markers=tuple(markers)))


def delete_marker(project, index):
    markers = list(project.markers)
    del markers[index]
    return validate_project(replace(project, markers=tuple(markers)))


# Notes ------------------------------------------------------------------------
@lru_cache(maxsize=64)
def note_line(notes):
    """The monophonic line as arrays (segment start, end, pitch, owning note's start), in beats.

    Last-note priority: the latest-started sounding note plays; when it ends, an earlier,
    longer note that still sounds resumes with its own phase (no restart).
    """
    ordered = sorted(notes, key=lambda n: (n.start, n.pitch))
    bounds = sorted({n.start for n in ordered} | {n.end for n in ordered})
    heap, pointer, segments = [], 0, []
    for index, (a, b) in enumerate(zip(bounds, bounds[1:])):
        while pointer < len(ordered) and ordered[pointer].start <= a:
            note = ordered[pointer]; heapq.heappush(heap, (-note.start, -pointer, note.end, note.pitch)); pointer += 1
        while heap and heap[0][2] <= a: heapq.heappop(heap)
        if not heap: continue
        start, _, _, pitch = heap[0]
        if segments and segments[-1][1] == a and segments[-1][2] == pitch and segments[-1][3] == -start:
            segments[-1][1] = b  # Same note continues.
        else: segments.append([a, b, pitch, -start])
    arrays = tuple(np.array([s[i] for s in segments], dtype=float) for i in range(4))
    for array in arrays: array.setflags(write=False)
    return arrays


@lru_cache(maxsize=64)
def _velocity_line(notes):
    """Last-note-priority gains, without changing note_line's envelope boundaries.

    Equal-start, equal-pitch notes can exchange ownership invisibly in note_line;
    retain those boundaries here so a longer duplicate resumes its own velocity.
    """
    ordered = sorted(notes, key=lambda n: (n.start, n.pitch))
    bounds = sorted({n.start for n in ordered} | {n.end for n in ordered})
    heap, pointer, starts, gains = [], 0, [], []
    for a, b in zip(bounds, bounds[1:]):
        while pointer < len(ordered) and ordered[pointer].start <= a:
            n = ordered[pointer]; heapq.heappush(heap, (-n.start, -pointer, n.end, n.velocity)); pointer += 1
        while heap and heap[0][2] <= a: heapq.heappop(heap)
        if not heap: continue
        starts.append(a); gains.append(heap[0][3])
    arrays = np.array(starts, dtype=float), np.array(gains, dtype=float)
    for array in arrays: array.setflags(write=False)
    return arrays


def note_velocities(notes): return _velocity_line(notes)[1]


# Prepared scores (audit P2) ----------------------------------------------------
# Every audio block builds a NoteSource or ChordSource per clip. Their whole-score data
# (lines, velocities, glide paths, chord lanes) depends only on the notes tuple, the tempo
# and the velocity flag, so it is prepared once per score revision, not per block. The key
# is the notes tuple's identity (projects are immutable and replaced, never mutated): an
# lru_cache keyed by value re-hashed every Note on each block (3 ms at 20,000 notes). Each
# entry holds the tuple itself, so its id can't be reused while the entry lives.
PREPARED_SCORES = 8
_prepared = OrderedDict()
_prepared_lock = Lock()
prepared_builds = 0  # Scores built (tests count them).


def _prepared_score(kind, project, build):
    global prepared_builds
    notes = project.notes
    key = (kind, id(notes), project.bpm, project.tempo_changes, project.velocity)
    with _prepared_lock:
        entry = _prepared.get(key)
        if entry is not None and entry[0] is notes:
            _prepared.move_to_end(key)
            return entry[1]
    score = build(project)  # Outside the lock: a duplicate build is harmless.
    with _prepared_lock:
        prepared_builds += 1
        _prepared[key] = (notes, score)
        _prepared.move_to_end(key)
        while len(_prepared) > PREPARED_SCORES: _prepared.popitem(last=False)
    return score


def clear_prepared():
    with _prepared_lock: _prepared.clear()


def _note_score(project):
    """(line, (velocity starts, velocities) or (None, None), glide paths or None) for NoteSource."""
    def build(project):
        line = note_line(project.notes)
        velocity = _velocity_line(project.notes) if project.velocity else (None, None)
        glides = None
        if any(n.glide > 0 for n in project.notes):
            # Each owner keeps its trajectory when an overlapping note ends.
            paths = {}
            previous = None
            for note in sorted(project.notes, key=lambda n: (n.start, n.pitch)):
                target = 440. * 2. ** ((note.pitch - 69) / 12)
                duration, initial, phase = 0., target, 0.
                if previous is not None and note.start <= previous.end and note.glide > 0:
                    old = paths[(previous.start, previous.pitch)]
                    if project.tempo_changes:
                        initial, phase = NoteSource._trajectory(old, float(project.seconds(note.start) - project.seconds(previous.start)))
                        duration = float(project.seconds(note.start + note.glide) - project.seconds(note.start))
                    else:
                        initial, phase = NoteSource._trajectory(old, float(project.seconds(note.start - previous.start)))
                        duration = float(project.seconds(note.glide))
                paths[(note.start, note.pitch)] = (initial, target, duration, phase)
                previous = note
            glides = np.array([paths[(owner, pitch)] for pitch, owner in zip(line[2], line[3])]).reshape(-1, 4)
            glides.setflags(write=False)
        return line, velocity, glides
    return _prepared_score('notes', project, build)


def _chord_score(project):
    """(allocation, lanes) for ChordSource: each lane's line, natural ends, steals, glides and velocities."""
    def build(project):
        allocation, ends, paths, lanes = _chord_layout(project.notes, project.bpm, project.tempo_changes)
        velocity = np.array([n.velocity for n in project.notes]) if project.velocity else None
        prepared = []
        for indices in lanes:
            notes = allocation.note[indices]
            arrays = (allocation.start[indices], ends[indices], allocation.pitch[indices], allocation.owner[indices],
                      allocation.end[indices], allocation.steal[indices],
                      None if paths is None else paths[notes], None if velocity is None else velocity[notes])
            for array in arrays:
                if array is not None: array.setflags(write=False)
            prepared.append(arrays)
        return allocation, tuple(prepared)
    return _prepared_score('chords', project, build)


class NoteSource:
    """The note roll as a render() source: at(times) -> (music, phase, envelope).

    times are the clip's local seconds; offset turns them into song seconds.
    """
    def __init__(self, project, offset=0.):
        self.project, self.offset = project, float(offset)
        self.line, (self.velocity_starts, self.velocities), self.glides = _note_score(project)

    @staticmethod
    def _trajectory(path, elapsed):
        initial, target, duration, phase = path
        if duration == 0: return target, phase + target * elapsed
        rate = math.log(target / initial) / duration
        sliding = np.clip(elapsed, 0., duration)
        integral = initial * sliding if rate == 0 else initial * np.expm1(rate * sliding) / rate
        return initial * np.exp(rate * sliding), phase + integral + target * np.maximum(elapsed - duration, 0.)

    def _glide_values(self, beats, index, sounding):
        elapsed = (beats - self.line[3][index]) * (60. / self.project.bpm)
        if self.project.tempo_changes:
            elapsed = self.project.seconds(beats) - self.project.seconds(self.line[3][index])
        initial, target, duration, base = self.glides[index].T
        rate = np.divide(np.log(target / initial), duration, out=np.zeros_like(duration), where=duration > 0)
        sliding = np.clip(elapsed, 0., duration)
        integral = initial * sliding
        np.divide(initial * np.expm1(rate * sliding), rate, out=integral, where=rate != 0)
        frequency = np.where(duration > 0, initial * np.exp(rate * sliding), target)
        phase = base + integral + target * np.maximum(elapsed - duration, 0.)
        return np.where(sounding, frequency, 0.), np.where(sounding, phase, 0.)

    def _lookup(self, times):
        starts, ends, pitches, owners = self.line
        beats = self.project.beats(np.asarray(times, dtype=float) + self.offset)
        if not len(starts): return beats, None, np.zeros(len(beats), dtype=bool)
        index = np.clip(np.searchsorted(starts, beats, side='right') - 1, 0, len(starts) - 1)
        sounding = (beats >= starts[index]) & (beats < ends[index])
        return beats, index, sounding

    def frequency_at(self, times):
        beats, index, sounding = self._lookup(times)
        if index is None: return np.zeros(len(sounding))
        if self.glides is not None: return self._glide_values(beats, index, sounding)[0]
        return np.where(sounding, 440. * 2. ** ((self.line[2][index] - 69) / 12), 0.)

    def at(self, times):
        beats, index, sounding = self._lookup(times)
        count = len(beats)
        if index is None:
            silence = np.zeros(count)
            return np.zeros((count, 2)), silence, silence
        starts, ends, pitches, owners = self.line
        frequency = np.where(sounding, 440. * 2. ** ((pitches[index] - 69) / 12), 0.)
        seconds_per_beat = 60. / self.project.bpm
        phase = frequency * (beats - owners[index]) * seconds_per_beat
        if self.project.tempo_changes:
            phase = frequency * (self.project.seconds(beats) - self.project.seconds(owners[index]))
        if self.glides is not None:
            frequency, phase = self._glide_values(beats, index, sounding)
        into = (beats - starts[index]) * seconds_per_beat; left = (ends[index] - beats) * seconds_per_beat
        if self.project.tempo_changes:
            into = self.project.seconds(beats) - self.project.seconds(starts[index])
            left = self.project.seconds(ends[index]) - self.project.seconds(beats)
        envelope = np.where(sounding, np.clip(np.minimum(into / ATTACK_SECONDS, left / RELEASE_SECONDS), 0., 1.), 0.)
        if self.project.velocity:
            gain_index = np.clip(np.searchsorted(self.velocity_starts, beats, side='right') - 1, 0, len(self.velocities) - 1)
            envelope = envelope * self.velocities[gain_index]
        mono = MUSIC_LEVEL * envelope * np.sin(2 * np.pi * phase)
        return np.column_stack((mono, mono)), phase, envelope


class _HeldPoseSource(NoteSource):
    """A NoteSource that ignores the clamped times render() passes and uses the true ones."""
    def __init__(self, project, offset, true_times):
        super().__init__(project, offset); self.true_times = true_times

    def at(self, times): return super().at(self.true_times)

    def frequency_at(self, times): return super().frequency_at(self.true_times)


@dataclass(frozen=True)
class ChordVoices:
    start: np.ndarray
    end: np.ndarray
    pitch: np.ndarray
    owner: np.ndarray
    note: np.ndarray
    steal: np.ndarray
    partner: np.ndarray
    dropped: tuple


@lru_cache(maxsize=64)
def chord_voices(notes):
    """Allocate beat-domain segments; muted partners retain their own trajectories."""
    events = {}
    for i, n in enumerate(notes):
        events.setdefault(n.start, []).append(i)
        events.setdefault(n.end, [])
    active, muted, stolen, partners, rows, dropped = {}, {}, set(), {}, [], []
    for beat in sorted(events):
        # Touching notes remain pairing candidates even though their segments close.
        candidates = {i: row for i, row in active.items()
                      if row[0] < beat and notes[i].end >= beat}
        for i in list(active):
            if active[i][1] <= beat: del active[i]
        incoming = events[beat]
        gliders = {i for i in incoming if notes[i].glide > 0}
        claimed = set()
        while gliders and candidates:
            new, old = min(((i, j) for i in gliders for j in candidates),
                           key=lambda p: (abs(notes[p[0]].pitch-notes[p[1]].pitch),
                                          notes[p[0]].pitch, notes[p[1]].pitch, p[1]))
            gliders.remove(new); candidates.pop(old); claimed.add(old)
            partners[new] = old
            row = active.pop(old, None)
            if row is not None: row[1] = beat
            if notes[old].end > notes[new].end:
                muted[old] = notes[new].end
        resumes = [i for i, until in muted.items() if until <= beat and i not in claimed]
        for i in resumes: del muted[i]
        for i in sorted((*incoming, *resumes), key=lambda i: (notes[i].pitch, i)):
            if i in stolen or notes[i].end <= beat: continue
            if len(active) >= MAX_CHORD_VOICES:
                old = min(active, key=lambda j: (notes[j].start, j))
                active.pop(old)[5] = beat
                stolen.add(old); muted.pop(old, None); dropped.append((old, beat))
            n = notes[i]
            row = [beat, n.end, n.pitch, n.start, i, np.inf, partners.get(i, -1)]
            rows.append(row); active[i] = row
    rows = sorted((r for r in rows if r[1] > r[0]), key=lambda r: (r[0], r[2], r[4]))
    arrays = tuple(np.array([r[k] for r in rows], dtype=int if k in (4, 6) else float)
                   for k in range(7))
    for a in arrays: a.setflags(write=False)
    return ChordVoices(*arrays, tuple(dropped))


class _ChordLane(NoteSource):
    def __init__(self, project, offset, prepared):
        """prepared: one lane of _chord_score (start, end, pitch, owner, natural end, steal, glides, velocities)."""
        self.project, self.offset = project, float(offset)
        start, end, pitch, owner, self.natural_end, self.steal, self.glides, self.velocities = prepared
        self.line = (start, end, pitch, owner)
        self.velocity_starts = self.line[0]

    def values(self, times):
        beats, index, sounding = self._lookup(times)
        starts, _, pitches, owners = self.line
        frequency = np.where(sounding, 440. * 2. ** ((pitches[index] - 69) / 12), 0.)
        seconds_per_beat = 60. / self.project.bpm
        phase = frequency * (beats - owners[index]) * seconds_per_beat
        if self.project.tempo_changes:
            phase = frequency * (self.project.seconds(beats) - self.project.seconds(owners[index]))
        if self.glides is not None:
            frequency, phase = self._glide_values(beats, index, sounding)
        into = (beats - starts[index]) * seconds_per_beat
        left = (self.natural_end[index] - beats) * seconds_per_beat
        if self.project.tempo_changes:
            into = self.project.seconds(beats) - self.project.seconds(starts[index])
            left = self.project.seconds(self.natural_end[index]) - self.project.seconds(beats)
        envelope = np.where(sounding, np.clip(np.minimum(into / ATTACK_SECONDS, left / RELEASE_SECONDS), 0., 1.), 0.)
        fading = np.isfinite(self.steal[index])
        if np.any(fading):
            envelope[fading] *= np.clip(1 - (self.project.seconds(beats[fading]) -
                self.project.seconds(self.steal[index[fading]])) / STEAL_FADE_SECONDS, 0., 1.)
        return sounding, frequency, phase, envelope, self.velocities[index] if self.project.velocity else None


class ChordSource:
    """Independent mono lanes with the mono source's floating-point arithmetic."""
    def __init__(self, project, offset=0.):
        self.project, self.offset = project, float(offset)
        _, lanes = _chord_score(project)
        self.lanes = [_ChordLane(project, offset, lane) for lane in lanes]

    def voices(self, times): return [lane.values(times) for lane in self.lanes]

    def at(self, times):
        count = np.zeros(len(times), dtype=int); env = np.zeros(len(times)); mono = np.zeros(len(times))
        for mask, frequency, phase, envelope, gain in self.voices(times):
            count[mask] += 1; env[mask] += envelope[mask]
            weighted = envelope if gain is None else envelope * gain
            mono[mask] += MUSIC_LEVEL * weighted[mask] * np.sin(2 * np.pi * phase[mask])
        multi = count > 1
        mono[multi] /= np.maximum(1., env[multi])
        return np.column_stack((mono, mono)), None, None


@lru_cache(maxsize=16)
def _chord_layout(notes, bpm, tempo_changes):
    """Tempo-dependent voice layout, cached: ChordSource is rebuilt for every audio block."""
    project = Project(bpm=bpm, notes=notes, tempo_changes=tempo_changes)
    allocation = chord_voices(project.notes)
    paths = None
    if any(n.glide > 0 for n in project.notes):
        paths = np.zeros((len(project.notes), 4))
        partners = dict(zip(allocation.note, allocation.partner))
        for i in sorted(range(len(project.notes)), key=lambda i: (project.notes[i].start, project.notes[i].pitch, i)):
            n = project.notes[i]; target = 440. * 2. ** ((n.pitch - 69) / 12)
            initial, duration, phase = target, 0., 0.
            old = int(partners.get(i, -1))
            if old >= 0:
                previous = project.notes[old]
                if project.tempo_changes:
                    elapsed = float(project.seconds(n.start) - project.seconds(previous.start))
                    duration = float(project.seconds(n.start+n.glide) - project.seconds(n.start))
                else:
                    elapsed = float(project.seconds(n.start-previous.start))
                    duration = float(project.seconds(n.glide))
                initial, phase = NoteSource._trajectory(paths[old], elapsed)
            paths[i] = initial, target, duration, phase
    ends = allocation.end.copy()
    finite = np.isfinite(allocation.steal)
    ends[finite] = np.minimum(ends[finite], project.beats(
        project.seconds(allocation.steal[finite]) + STEAL_FADE_SECONDS))
    lanes = []
    for i, start in enumerate(allocation.start):
        lane = next((lane for lane in lanes if ends[lane[-1]] <= start), None)
        if lane is None: lane = []; lanes.append(lane)
        lane.append(i)
    lanes = tuple(np.array(lane, dtype=int) for lane in lanes)
    for array in (ends, *lanes, *(() if paths is None else (paths,))): array.setflags(write=False)
    return allocation, ends, paths, lanes


def render_chords(pose_times, settings, source, local_times):
    validate_once(settings)
    count = np.zeros(len(pose_times), dtype=int)
    local = np.zeros((len(pose_times), 2)); center = local.copy(); weighted = local.copy()
    envelopes = np.zeros(len(pose_times)); mono = envelopes.copy()
    for mask, frequency, phase, envelope, gain in source.voices(local_times):
        if not mask.any(): continue
        voice_envelope = envelope if gain is None else envelope * gain
        scaled, centers = shape_parts(pose_times[mask], phase[mask], voice_envelope[mask], settings,
                                     frequency[mask] if settings.corner_dwell_us > 0 else None)
        count[mask] += 1; local[mask] += scaled; center[mask] += centers
        weighted[mask] += envelope[mask, None] * centers
        envelopes[mask] += envelope[mask]
        mono[mask] += MUSIC_LEVEL * voice_envelope[mask] * np.sin(2 * np.pi * phase[mask])
    drawing = local + center
    silent = count == 0
    if silent.any():
        zeros = np.zeros(np.count_nonzero(silent))
        scaled, centers = shape_parts(pose_times[silent], zeros, zeros, settings, zeros)
        drawing[silent] = scaled + centers
    multi = count > 1
    if multi.any():
        total = envelopes[multi]; divisor = np.maximum(1., total)
        centers = center[multi] / count[multi, None]
        np.divide(weighted[multi], total[:, None], out=centers, where=total[:, None] != 0)
        drawing[multi] = local[multi] / divisor[:, None] + centers
        mono[multi] /= divisor
    music = np.column_stack((mono, mono))
    output = settings.drawing_gain * ((1-settings.music_gain)*drawing + settings.music_gain*music)
    return drawing, music, output


# Timeline ---------------------------------------------------------------------
def clip_at(project, beats):
    """Index of the clip sounding at each beat position, or -1 in a gap or a muted clip."""
    beats = np.asarray(beats, dtype=float)
    if not project.clips: return np.full(beats.shape, -1)
    starts = np.array([c.start for c in project.clips]); ends = np.array([c.end for c in project.clips])
    index = np.clip(np.searchsorted(starts, beats, side='right') - 1, 0, len(starts) - 1)
    inside = (beats >= starts[index]) & (beats < ends[index])
    if any(c.muted for c in project.clips): inside &= ~np.array([c.muted for c in project.clips])[index]
    return np.where(inside, index, -1)


def song_seconds(project, start, elapsed):
    """Song positions for playback that began at song second start: loops wrap, otherwise linear."""
    position = float(start) + np.asarray(elapsed, dtype=float)
    if project.loop:
        a, b = float(project.seconds(project.loop_start)), float(project.seconds(project.loop_end))
        if start < b:
            over = position >= b
            position = np.where(over, a + np.mod(position - b, b - a), position)
    return position


def clip_settings(clip, machine, bpm):
    """Bake scene clocks and machine preferences; bpm accepts a Project or scalar.

    A mapped scene's t=0 is at song beat start-time_offset. Lanes remain
    relative to the clip start, even when its scene time origin is elsewhere.
    """
    project = bpm if isinstance(bpm, Project) else None
    bpm = project.bpm if project is not None else bpm
    changes = project.tempo_changes if project is not None else ()
    # Let normal baking/validation diagnose malformed machine fields, including unhashable inputs.
    values = (machine.sample_rate, machine.route_mode, machine.corner_dwell_us,
              machine.shape_redraw_count, machine.switch_sweep_ms)
    cacheable = (type(bpm) in (int, float) and isinstance(changes, tuple)
                 and all(type(v) in (int, float, str) for v in values))
    bake = _baked_settings if cacheable else _baked_settings.__wrapped__
    return bake(Identity(clip), bpm, Identity(changes), *values)


@lru_cache(maxsize=32, typed=True)
def _baked_settings(key, bpm, tempo_key, sample_rate, route_mode, corner_dwell_us, shape_redraw_count, switch_sweep_ms):
    clip = key.value
    changes = tempo_key.value
    clock = (60. / bpm, (clip.time_offset if clip.local_time else clip.start) * 60. / bpm)
    tempo = bpm
    if changes:
        origin = clip.start - clip.time_offset if clip.local_time else 0.
        tempo = BeatClock(TempoMap(bpm, changes), origin)
        clock = replace(tempo, beat_offset=clip.start - origin)

    def bake(param):
        return replace(param, clock=clock) if param.points else param

    def bake_shape(shape):
        lanes = live_lanes(shape.lanes)  # Parked variable lanes (object Replace) are never baked.
        automated = (lanes or shape.path_start.points or shape.path_trace.points or
                     any(p.points for step in shape.transforms for p in (*step.params, step.on)) or
                     any(plot is not None and any(v.points for v in plot.variables)
                         for plot in (shape.plot, shape.recurrence)) or
                     any(v.points for v in shape.transform_variables))
        if not automated: return shape
        transforms = tuple(replace(step, params=tuple(bake(p) for p in step.params), on=bake(step.on))
                           if any(p.points for p in (*step.params, step.on)) else step for step in shape.transforms)
        plot = shape.plot
        if plot is not None and any(v.points for v in plot.variables):
            plot = replace(plot, variables=tuple(bake(v) for v in plot.variables))
        recurrence = shape.recurrence
        if recurrence is not None and any(v.points for v in recurrence.variables):
            recurrence = replace(recurrence, variables=tuple(bake(v) for v in recurrence.variables))
        variables = (tuple(bake(v) for v in shape.transform_variables)  # Object variables (T5).
                     if any(v.points for v in shape.transform_variables) else shape.transform_variables)
        return replace(shape, transforms=transforms, path_start=bake(shape.path_start),
                       path_trace=bake(shape.path_trace), plot=plot, recurrence=recurrence,
                       transform_variables=variables, lane_clock=clock if lanes else shape.lane_clock)

    return replace(clip.settings, shapes=tuple(bake_shape(s) for s in clip.settings.shapes),
                   sample_rate=sample_rate, route_mode=route_mode, corner_dwell_us=corner_dwell_us,
                   shape_redraw_count=shape_redraw_count, switch_sweep_ms=switch_sweep_ms,
                   motion_time_offset=0., tempo=tempo)


@lru_cache(maxsize=32)
def _faded_settings(key, fades):
    return replace(key.value, switch_fades=fades)


def muted_edges(project, index, offset):
    """(pose time, rising) where clip `index` meets a muted clip: its objects sweep out
    before a muted clip that starts at its end and in after one that ends at its start."""
    clips, clip = project.clips, project.clips[index]
    edges = []
    if index > 0 and clips[index - 1].muted and abs(clips[index - 1].end - clip.start) < 1e-9:
        edges.append((max(0., float(project.seconds(clip.start)) - offset), True))
    if index + 1 < len(clips) and clips[index + 1].muted and abs(clips[index + 1].start - clip.end) < 1e-9:
        edges.append((max(0., float(project.seconds(clip.end)) - offset), False))
    return tuple(edges)


def render_song(times, project, machine, source_only=False, audio=True, wait_media=True, track_peaks=None,
                chains=None, chain_state=None):
    """Stereo output at song seconds `times` (any order), as Sandbox would play each clip.

    machine is a Settings carrying the output rate and the machine signal preferences.
    Gaps between clips and muted clips are silent; the note roll is heard in a clip through its mix.
    wait_media=False (playback and the stopped display) leaves out audio clips whose media
    isn't decoded yet and queues their decode; export and everything else waits for it.
    chains and chain_state: audio-track processing and its memory across calls (AW1b,
    audio_clips.mix_audio_clips). Playback and export pass one ChainState for the whole run.
    """
    times = np.asarray(times, dtype=float)
    if source_only: return (ChordSource(project) if project.chords else NoteSource(project)).at(times)[0]
    output = np.zeros((len(times), 2))
    owner = clip_at(project, project.beats(times))
    for index in np.unique(owner[owner >= 0]):
        clip = project.clips[int(index)]; mask = owner == index
        offset = float(project.seconds(clip.start - clip.time_offset)) if clip.local_time else 0.
        settings = clip_settings(clip, machine, project)
        if settings.switch_sweep_ms > 0 and any(c.muted for c in project.clips):
            fades = muted_edges(project, int(index), offset)
            if fades: settings = _faded_settings(Identity(settings), fades)
        source = ChordSource(project, offset) if project.chords else NoteSource(project, offset)
        local_times = times[mask] - offset
        pose_times = np.maximum(local_times, 0.)
        if not project.chords and np.any(local_times < 0.):
            # Before t = 0 (a start extended earlier than the scene began) the scene holds its
            # starting pose, while notes keep their true song time and phase.
            source = _HeldPoseSource(project, offset, local_times)
        def render_clip():
            return render_chords(pose_times, settings, source, local_times) if project.chords else render(pose_times, settings, source)
        try: drawing, music, mixed = render_clip()
        except ValueError:
            if settings.shape_redraw_count == 1: raise
            # Redraw beyond the path detail limit: play this clip without it, as Sandbox does.
            settings = replace(settings, shape_redraw_count=1)
            drawing, music, mixed = render_clip()
        if clip.lanes:
            clock = (60. / project.bpm, float(project.seconds(clip.time_offset if clip.local_time else clip.start)))
            if project.tempo_changes:
                clock = replace(settings.tempo, beat_offset=clip.start - settings.tempo.origin_beats)
            dg = np.clip(settings.drawing_gain * gain_at(lane_points(clip.lanes, 'drawing_gain'), clock, local_times), 0., .95)[:, None]
            mg = np.clip(settings.music_gain * gain_at(lane_points(clip.lanes, 'music_gain'), clock, local_times), 0., 1.)[:, None]
            output[mask] = dg * ((1-mg)*drawing + mg*music) if clip.mode == 'mixed' else dg * (drawing if clip.mode == 'drawing' else music)
        else:
            output[mask] = select_signal(drawing, music, mixed, settings, clip.mode, clip_output=False)
    if audio and (project.audio_clips or chain_state is not None):
        mix_audio_clips(output, times, project, machine.sample_rate, machine.switch_sweep_ms, wait_media, peaks=track_peaks,
                        chains=chains, chain_state=chain_state)
    return output


NO_CYCLE = 'No note at the playhead'


def song_cycle(project, seconds, machine):
    """One whole trace cycle of the note at `seconds`, to loop while the song is stopped.

    Returns (samples (N, 2) float32, cycle seconds, '') or (None, None, reason); the
    span is cycle_span's.
    """
    span = cycle_span(project, seconds)
    if span is None: return None, None, NO_CYCLE
    start, end = span
    count = max(8, round((end - start) * machine.sample_rate))
    samples = render_song(start + np.arange(count) / machine.sample_rate, project, machine, audio=False)
    return np.clip(samples, -1., 1.).astype('float32'), end - start, ''


def cycle_span(project, seconds):
    """(start, end) song seconds of one whole trace cycle near `seconds`, or None.

    The cycle runs from one whole note phase to the next, so the scene is traced once
    from its path start. It is taken from the note's steady part (after its attack,
    before its release): a cycle containing a note edge's fade cannot loop smoothly.
    During glide the cycle ends where the phase completes, not after 1/f.
    """
    times = np.array([float(seconds)])
    if project.chords: return _chord_cycle_span(project, seconds)
    source = NoteSource(project)
    return _voice_cycle_span(project, seconds, source)


def _voice_cycle_span(project, seconds, source):
    times = np.array([float(seconds)])
    frequency = float(source.frequency_at(times)[0])
    if frequency <= 0 or clip_at(project, project.beats(times))[0] < 0:
        return None
    _, index, _ = source._lookup(times)
    starts, ends, _, owners = source.line
    segment_start = float(project.seconds(starts[index[0]]))
    segment_end = float(project.seconds(ends[index[0]]))
    low_bound = segment_start + ATTACK_SECONDS
    high_bound = max(low_bound, segment_end - RELEASE_SECONDS)

    def phase_at(t): return float(source.at(np.array([t]))[1][0])

    if source.glides is None:
        # Constant pitch: phase = f × (song seconds since the owning note began).
        owner = float(project.seconds(owners[index[0]]))
        def time_of(k): return owner + k / frequency
    else:
        # Glide: the phase is monotonic inside the segment; bisect for whole cycles.
        def time_of(k):
            low, high = segment_start, segment_end
            for _ in range(64):
                middle = (low + high) / 2
                if phase_at(middle) < k: low = middle
                else: high = middle
            return high
    reference = min(max(float(seconds), low_bound), high_bound)
    k = math.floor(phase_at(reference))
    start = time_of(k)
    if start < low_bound: k += 1; start = time_of(k)
    end = time_of(k + 1)
    if end > high_bound and time_of(k - 1) >= low_bound:
        k -= 1; start, end = time_of(k), start
    return start, end


def _chord_cycle_span(project, seconds):
    times = np.array([float(seconds)])
    if clip_at(project, project.beats(times))[0] < 0: return None
    source = ChordSource(project)
    sounding = [(i, lane, lane.values(times)) for i, lane in enumerate(source.lanes)
                if lane._lookup(times)[2][0]]
    if not sounding: return None
    if len(sounding) == 1: return _voice_cycle_span(project, seconds, sounding[0][1])
    _, lowest, _ = min(sounding, key=lambda row: (row[2][1][0], row[0]))
    bounds = []
    for _, lane, _ in sounding:
        _, index, _ = lane._lookup(times); j = index[0]
        bounds.append((float(project.seconds(lane.line[0][j])) + ATTACK_SECONDS,
                       float(project.seconds(lane.line[1][j])) - RELEASE_SECONDS))
    low = max(b[0] for b in bounds); high = min(b[1] for b in bounds)
    if high <= low: return _voice_cycle_span(project, seconds, lowest)
    def phase(lane, t): return float(lane.at(np.array([t]))[1][0])
    _, index, _ = lowest._lookup(times)
    segment_start = float(project.seconds(lowest.line[0][index[0]]))
    segment_end = float(project.seconds(lowest.line[1][index[0]]))
    frequency = float(lowest.frequency_at(times)[0])
    owner = float(project.seconds(lowest.line[3][index[0]]))
    def time_of(k):
        if lowest.glides is None: return owner + k / frequency
        a, b = segment_start, segment_end
        for _ in range(64):
            mid = (a+b)/2
            if phase(lowest, mid) < k: a = mid
            else: b = mid
        return b
    k = math.floor(phase(lowest, min(max(float(seconds), low), high)))
    start = time_of(k)
    if start < low: k += 1; start = time_of(k)
    for cycles in range(1, CHORD_CYCLE_MAX+1):
        end = time_of(k+cycles)
        if end > high: break
        advances = [phase(lane, end)-phase(lane, start) for _, lane, _ in sounding]
        if all(abs(a-round(a)) <= CHORD_CYCLE_TOLERANCE for a in advances): return start, end
    end = time_of(k+1)
    if end > high and time_of(k-1) >= low: start, end = time_of(k-1), start
    return start, end


def export_song(path, project, machine, start_beats=0., end_beats=None, source_only=False,
                format='WAV', depth=16, rate=None):
    """Write a song span in one-second blocks at the requested sample rate."""
    machine = replace(machine, sample_rate=machine.sample_rate if rate is None else rate)
    validate_project(project)
    first = float(project.seconds(start_beats))
    if end_beats is None:  # The whole song, plus any track delay's ring-out (AW2); a chosen range is kept.
        last = float(project.seconds(project.end_beats)) + processing_tail(project)
    else: last = float(project.seconds(end_beats))
    rate = machine.sample_rate; count = max(0, round((last - first) * rate))
    state = ChainState()  # Audio-track memory runs on across the blocks, as in playback (AW1b).
    with AudioWriter(path, rate, format, depth) as output:
        for begin in range(0, count, rate):
            times = first + np.arange(begin, min(begin + rate, count)) / rate
            signal = render_song(times, project, machine, source_only, chain_state=state)
            output.write(signal)


# Editing helpers (pure: each returns a new Project) ----------------------------
def split_clip(project, index, beat):
    """Split strictly inside a clip, continuing its scene clock and every lane."""
    from phosphor.engine.lane_access import shift_clip_lanes
    clip = project.clips[index]
    beat = _finite(beat, 'Split position')
    if not clip.start < beat < clip.end:
        raise ValueError('The playhead must be inside a clip.')
    if len(project.clips) >= MAX_CLIPS:
        raise ValueError('Too many clips.')
    elapsed = beat - clip.start
    right = replace(shift_clip_lanes(clip, -elapsed), start=beat, length=clip.end - beat,
                    time_offset=clip.time_offset + elapsed if clip.local_time else clip.time_offset)
    clips = (*project.clips[:index], replace(clip, length=elapsed), right, *project.clips[index+1:])
    return validate_project(replace(project, clips=clips))


def duplicate_clip(project, index, start):
    """Copy into free space, advancing past occupied clips and shortening at the next."""
    if len(project.clips) >= MAX_CLIPS:
        raise ValueError('Too many clips.')
    start = max(0., _finite(start, 'Clip start'))
    for clip in project.clips:
        if clip.start <= start < clip.end:
            start = clip.end
    high = next((c.start for c in project.clips if c.start > start), MAX_BEATS)
    length = min(project.clips[index].length, high - start, MAX_BEATS - start)
    if length <= 1e-6:
        raise ValueError('There is no room for a clip there.')
    copy = replace(project.clips[index], start=start, length=length)
    clips = tuple(sorted((*project.clips, copy), key=lambda c: c.start))
    result = validate_project(replace(project, clips=clips))
    return result, next(i for i, c in enumerate(clips) if c is copy)


def with_clip(project, index, clip):
    clips = list(project.clips); clips[index] = clip
    return replace(project, clips=tuple(clips))


def add_clip(project, start, settings=None, length=DEFAULT_CLIP_BEATS, name=None, mode='drawing'):
    """Insert a clip at start (beats), shortened to fit before the next clip. Returns (project, index)."""
    for clip in project.clips:
        if clip.start - 1e-9 <= start < clip.end - 1e-9: raise ValueError('A clip already plays there.')
    following = [c.start for c in project.clips if c.start > start]
    if following: length = min(length, min(following) - start)
    if length <= 0: raise ValueError('There is no room for a clip there.')
    clip = Clip(float(start), float(length), settings or empty_settings(), mode, name or f'Scene {len(project.clips)+1}')
    clips = sorted((*project.clips, clip), key=lambda c: c.start)
    return replace(project, clips=tuple(clips)), clips.index(clip)


def remove_clip(project, index):
    clips = list(project.clips); clips.pop(index)
    return replace(project, clips=tuple(clips))


def fit_clip(project, index, start, length):
    """Move/resize clip index within its neighbours (no overlaps); returns the new project."""
    clips = project.clips
    low = clips[index-1].end if index > 0 else 0.
    high = clips[index+1].start if index + 1 < len(clips) else MAX_BEATS
    start = min(max(float(start), low), high - 1e-6)
    length = min(max(float(length), 1e-3), high - start)
    return with_clip(project, index, replace(clips[index], start=start, length=length))


def move_clips(project, indices, start):
    """Move selected clips rigidly from the press snapshot and return (project, sorted indices).

    A free drop takes priority. Otherwise a contiguous block swaps past crossed midpoints,
    keeping gaps by position, or the move stops at the nearest neighbour or song boundary.
    """
    clips = project.clips
    picked = set(indices)
    if not picked: raise ValueError('Choose a clip to move.')
    if min(picked) < 0 or max(picked) >= len(clips): raise IndexError('Clip index out of range.')
    selected = tuple(i for i in range(len(clips)) if i in picked)
    others = [i for i in range(len(clips)) if i not in picked]
    first, last = selected[0], selected[-1]
    origin = float(clips[first].start)
    start = _finite(start, 'Clip start')
    if start == origin: return project, selected
    span = clips[last].end - origin
    starts = [c.start for c in clips]

    # Both sets remain ordered, so each stationary clip is passed at most once.
    free = start >= 0 and start + span <= MAX_BEATS
    cursor = 0
    if free:
        for i in selected:
            beat = start + (clips[i].start - origin)
            while cursor < len(others) and beat >= clips[others[cursor]].end - 1e-9:
                cursor += 1
            if cursor < len(others) and clips[others[cursor]].start < beat + clips[i].length - 1e-9:
                free = False
                break

    if not free:
        slot = first
        if last - first + 1 == len(selected):
            slot -= sum(start < clips[i].start + clips[i].length / 2 for i in others[:first])
            slot += sum(start + span > clips[i].start + clips[i].length / 2 for i in others[first:])
        if slot > first and clips[others[slot-1]].end - span > MAX_BEATS:
            slot = first  # The swapped block would start past the beat limit: clamp instead (Codex review).
        if slot < first:
            start = float(clips[slot].start)
            for i in range(slot, first):
                gap = clips[i+1].start - clips[i].end
                starts[i] = clips[i].start + span + gap
        elif slot > first:
            crossed = others[first:slot]
            start = clips[crossed[-1]].end - span
            for i in crossed:
                gap = clips[i].start - clips[i-1].end
                starts[i] = clips[i].start - (span + gap)
            starts[crossed[0]] = float(origin)
        else:
            low, high = -origin, MAX_BEATS - clips[last].end
            for i in selected:
                if i and i-1 not in picked: low = max(low, clips[i-1].end - clips[i].start)
                if i+1 < len(clips) and i+1 not in picked: high = min(high, clips[i+1].start - clips[i].end)
            delta = min(max(start - origin, min(0., low)), max(0., high))
            if delta == 0: return project, selected
            start = origin + delta

    for i in selected:
        starts[i] = start + (clips[i].start - origin)
    if all(beat == c.start for beat, c in zip(starts, clips)): return project, selected
    # Merge the two ordered runs; snapshot indices break equal-start ties stably.
    order = tuple(heapq.merge(selected, others, key=lambda i: (starts[i], i)))
    moved = tuple(clips[i] if starts[i] == clips[i].start else replace(clips[i], start=starts[i]) for i in order)
    return replace(project, clips=moved), tuple(j for j, i in enumerate(order) if i in picked)
