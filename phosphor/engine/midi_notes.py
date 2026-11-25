"""MIDI melody or all-note import and note export, independent of the editor."""
from dataclasses import dataclass, replace
import math

from phosphor.audio.live_midi import mido_module
from phosphor.audio.score_source import midi_notes, melody_line
from phosphor.engine.composition import Note, PITCH_RANGE, MAX_NOTES, validate_project
from phosphor.engine.tempo_map import Meter


@dataclass(frozen=True)
class MidiNotes:
    notes: tuple
    bpm: float
    dropped: int = 0
    tempo_changes: tuple = ()
    meter_changes: tuple = ()


def read_midi(path, chords=False):
    mido = mido_module()
    file = mido.MidiFile(str(path))
    records = midi_notes(path, with_ticks=True)
    source = [record[:3] for record in records]
    # melody_line can shorten an end, but keeps the selected onset and pitch.
    owners = {}
    for record in records: owners.setdefault((record[0], record[2]), record)
    boundaries = {record[0]: record[3] for record in records}
    notes, dropped = [], 0
    selected = sorted(records, key=lambda r: (r[0], r[2])) if chords else melody_line(source)
    for item in selected:
        start, end, pitch = item[:3]
        record = item if chords else owners[start, pitch]
        first, last, velocity = record[3:]
        if end != record[1]: last = boundaries[end]
        if not PITCH_RANGE[0] <= pitch <= PITCH_RANGE[1] or last <= first:
            dropped += 1
            continue
        if len(notes) == MAX_NOTES:
            dropped += 1
            continue
        notes.append(Note(first / file.ticks_per_beat, (last-first) / file.ticks_per_beat,
                          pitch, velocity / 127.))
    tick, bpm = 0, 120.
    tempos, meters = {}, {}
    for message in mido.merge_tracks(file.tracks):
        tick += message.time
        beat = tick / file.ticks_per_beat
        if message.type == 'set_tempo':
            value = round(float(mido.tempo2bpm(message.tempo)), 3)
            if tick == 0: bpm = value
            else: tempos[beat] = value
        elif message.type == 'time_signature':
            meter = Meter(tuple((b, *v) for b, v in sorted(meters.items())))
            bar, offset = meter.bar_of(beat)
            if abs(float(offset)) > 1e-7:
                raise ValueError('MIDI time signatures must fall on bar starts.')
            meters[int(bar)] = (message.numerator, message.denominator)
    return MidiNotes(tuple(notes), bpm, dropped, tuple(sorted(tempos.items())),
                     tuple((b, *v) for b, v in sorted(meters.items())))


def import_notes(project, midi, mode='replace', playhead=0., snap=0., set_tempo=False):
    if mode not in ('replace', 'insert'): raise ValueError('Choose replace or insert.')
    if not math.isfinite(playhead) or not math.isfinite(snap) or snap < 0:
        raise ValueError('Invalid insertion position or Snap.')
    offset = max(0., round(playhead / snap) * snap if snap else playhead) if mode == 'insert' else 0.
    incoming = tuple(replace(note, start=note.start + offset) for note in midi.notes)
    notes = project.notes + incoming if mode == 'insert' else incoming
    if len(notes) > MAX_NOTES: raise ValueError(f'Use at most {MAX_NOTES} notes.')
    return validate_project(replace(project, notes=tuple(sorted(notes, key=lambda n: (n.start, n.pitch))),
                                    bpm=midi.bpm if set_tempo else project.bpm,
                                    tempo_changes=midi.tempo_changes if set_tempo else project.tempo_changes,
                                    meter_changes=midi.meter_changes if set_tempo else project.meter_changes))


def write_midi(path, project):
    validate_project(project)
    mido = mido_module()
    file = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack(); file.tracks.append(track)
    events = [(0, -2, 0, mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(project.bpm)))]
    for beat, bpm in project.tempo_changes:
        events.append((round(beat * 480), -2, 0, mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(bpm))))
    meter = Meter(project.meter_changes)
    for bar, count, unit in project.meter_changes:
        events.append((round(float(meter.bar_start(bar)) * 480), -1, 0,
                       mido.MetaMessage('time_signature', numerator=count, denominator=unit)))
    for note in project.notes:
        first = round(note.start * 480)
        last = max(first + 1, round(note.end * 480))
        events.append((first, 1, note.pitch, max(1, round(note.velocity * 127))))
        events.append((last, 0, note.pitch, 0))
    previous = 0
    for tick, on, pitch, velocity in sorted(events, key=lambda e: e[:3]):
        message = velocity.copy(time=tick-previous) if on < 0 else mido.Message(
            'note_on' if on else 'note_off', note=pitch, velocity=velocity, channel=0, time=tick-previous)
        track.append(message)
        previous = tick
    track.append(mido.MetaMessage('end_of_track'))
    file.save(str(path))
