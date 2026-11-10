"""Sample-clock synthesis of a melody read from a user-chosen MIDI file.

One phase drives the beam around the scene, so a chord would distort the figure:
the app plays the file's top melodic line. No music is bundled with phosphor.
"""
from functools import lru_cache
from pathlib import Path
import numpy as np

SCALE_NAME = 'C-major scale'
MIDI_FILE_SOURCE = 'MIDI file'
SOURCES = (SCALE_NAME, MIDI_FILE_SOURCE)
CHORD_WINDOW = .015  # Seconds; onsets this close count as one chord.
REST_SECONDS = .03  # Shorter gaps between melody notes are played legato.
DEFAULT_TEMPO = 120.


def midi_notes(path):
    """(start s, end s, pitch) for every pitched note, with the file's tempo map applied."""
    from live_midi import mido_module
    notes, held, clock = [], {}, 0.
    for message in mido_module().MidiFile(str(path)):
        clock += message.time
        if message.type == 'note_on' and message.velocity > 0 and message.channel != 9:
            held.setdefault(message.note, []).append(clock)
        elif message.type in ('note_on', 'note_off') and held.get(message.note):
            notes.append((held[message.note].pop(0), clock, message.note))
    if not notes:
        raise ValueError(f'No notes found in {Path(path).name}.')
    return sorted(notes)


def melody_line(notes):
    """Top line: the highest note of each chord, skipping notes under a held melody note."""
    starts = np.array([note[0] for note in notes])
    groups = np.split(np.arange(len(notes)), np.flatnonzero(np.diff(starts) > CHORD_WINDOW)+1)
    melody = []
    for group in groups:
        start, end, pitch = notes[max(group, key=lambda index: notes[index][2])]
        if melody and start < melody[-1][1]-.01 and pitch < melody[-1][2]:
            continue
        if melody and melody[-1][1] > start:
            melody[-1] = (melody[-1][0], start, melody[-1][2])
        melody.append((start, end, pitch))
    return melody


def melody_arrays(melody, reference_bpm):
    """Score arrays in beats at the reference tempo; rests fill gaps longer than REST_SECONDS."""
    beat = reference_bpm/60
    starts, durations, pitches, gates = [], [], [], []

    def add(start, duration, pitch, gate):
        starts.append(start*beat); durations.append(duration*beat); pitches.append(pitch); gates.append(gate)
    if melody[0][0] > REST_SECONDS:
        add(0., melody[0][0], -1, 1.)
    for index, (start, end, pitch) in enumerate(melody):
        following = melody[index+1][0] if index+1 < len(melody) else end
        if following-end > REST_SECONDS:
            add(start, end-start, pitch, 1.)
            add(end, following-end, -1, 1.)
        else:
            add(start, following-start, pitch, (end-start)/(following-start))
    return np.array(starts), np.array(durations), np.array(pitches), np.array(gates), melody[-1][1]*beat


def file_tempo(path):
    """The file's first tempo in quarter notes per minute; Tempo at this value plays at original speed."""
    from live_midi import mido_module
    mido = mido_module()
    for track in mido.MidiFile(str(path)).tracks:
        for message in track:
            if message.type == 'set_tempo':
                return round(mido.tempo2bpm(message.tempo), 3)
    return DEFAULT_TEMPO


@lru_cache(maxsize=8)
def _file_arrays(path, stamp):
    return melody_arrays(melody_line(midi_notes(path)), file_tempo(path))


def score_arrays(path):
    """Melody arrays in beats at the file's own tempo; re-read when the file changes."""
    if not path:
        raise ValueError('Choose a MIDI file for the MIDI file source.')
    path = Path(path)
    try:
        status = path.stat()
    except OSError as error:
        raise ValueError(f'MIDI file not found: {path}') from error
    try:
        return _file_arrays(str(path), (status.st_mtime_ns, status.st_size))
    except (OSError, EOFError, KeyError, IndexError) as error:
        raise ValueError(f'Could not read MIDI file {path.name}: {error}') from error


def score_components(times, bpm, path):
    starts, durations, pitches, gates, beats = score_arrays(path)
    position = np.mod(times * bpm / 60, beats)
    indices = np.minimum(np.searchsorted(starts, position, side='right') - 1, len(starts) - 1)
    local = (position - starts[indices]) * 60 / bpm
    sounding_seconds = durations[indices] * gates[indices] * 60 / bpm
    midi = pitches[indices]
    frequency = np.where(midi >= 0, 440 * 2.0 ** ((midi - 69) / 12), 0)
    # Subtle 5 Hz vibrato: integrated frequency modulation keeps phase continuous.
    phase = frequency * (local + .003 * (1 - np.cos(2 * np.pi * 5 * local)) / (2 * np.pi * 5))
    attack = np.minimum(.018, sounding_seconds / 4)
    release = np.minimum(.045, sounding_seconds / 4)
    envelope = np.clip(np.minimum(local / attack, (sounding_seconds - local) / release), 0, 1)
    envelope = .8 * np.sin(envelope * np.pi / 2) ** 2 * (midi >= 0)
    # Deterministic, flute-like additive synthesis.
    angle = 2 * np.pi * phase
    tone = (np.sin(angle) + .18 * np.sin(2 * angle) + .055 * np.sin(3 * angle)
            + .015 * np.sin(4 * angle)) / 1.25
    mono = envelope * tone
    return np.column_stack((mono, mono)), phase, envelope


def score_frequency(times,bpm,path):
    starts,durations,pitches,gates,beats=score_arrays(path)
    position=np.mod(times*bpm/60,beats)
    indices=np.minimum(np.searchsorted(starts,position,side='right')-1,len(starts)-1)
    local=(position-starts[indices])*60/bpm
    midi=pitches[indices]
    frequency=np.where(midi>=0,440*2.**((midi-69)/12),0.)
    return frequency*(1+.003*np.sin(2*np.pi*5*local))


def source_duration(bpm, path):
    return score_arrays(path)[4] * 60 / bpm
