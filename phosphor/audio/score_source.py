"""Generated sources: a scale, a single tone, or the melody of a MIDI file.

One phase drives the beam around the scene, so a chord would distort the figure:
the app plays a MIDI file's top melodic line. No music is bundled with phosphor: the
files come from the user's own disk.
"""
from functools import lru_cache
from pathlib import Path
import numpy as np
from phosphor.storage.app_paths import ASSETS_DIR

SCALE_NAME = 'Scale'
MIDI_FILE_SOURCE = 'MIDI file'
TONE_SOURCE = 'Single tone'
SOURCES = (SCALE_NAME, MIDI_FILE_SOURCE, TONE_SOURCE)
SCORE_FOLDER = ASSETS_DIR/'scores'
# Semitones above the root within one octave.
SCALE_TYPES = {
    'Major': (0, 2, 4, 5, 7, 9, 11),
    'Natural minor': (0, 2, 3, 5, 7, 8, 10),
    'Harmonic minor': (0, 2, 3, 5, 7, 8, 11),
    'Major pentatonic': (0, 2, 4, 7, 9),
    'Minor pentatonic': (0, 3, 5, 7, 10),
    'Blues': (0, 3, 5, 6, 7, 10),
    'Chromatic': tuple(range(12)),
}
NOTE_RANGE = (21, 108)  # A0 to C8 as MIDI note numbers.
NOTE_NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')
TONE_RANGE = (.01, 20000.)  # Hz, the old function plot's s frequency range.
TONE_SECONDS = 10.  # Length of a WAV export of the endless single tone.


def note_name(midi):
    """MIDI 60 is C4."""
    return f'{NOTE_NAMES[midi % 12]}{midi//12-1}'


def scale_ladder(scale_type, root, end):
    """Notes from the root (the fundamental) up to end inclusive."""
    steps = SCALE_TYPES[scale_type]
    return tuple(note for note in range(root, end+1) if (note-root) % 12 in steps)


def validate_scale(scale_type, root, end):
    if scale_type not in SCALE_TYPES:
        raise ValueError('Select a scale type.')
    if any(type(value) is not int for value in (root, end)) or not NOTE_RANGE[0] <= root < end <= NOTE_RANGE[1]:
        raise ValueError(f'The scale needs a start note and a higher end note from {note_name(NOTE_RANGE[0])} to {note_name(NOTE_RANGE[1])}.')
    if (end-root) % 12 not in SCALE_TYPES[scale_type]:
        raise ValueError(f'{note_name(end)} is not in the {scale_type.lower()} scale on {note_name(root)}.')


@lru_cache(maxsize=32)
def scale_notes(scale_type, root, end):
    """Up from the root to the end note and back down, one note per beat."""
    ladder = np.array(scale_ladder(scale_type, root, end))
    notes = np.concatenate((ladder, ladder[-2:0:-1]))
    notes.setflags(write=False)
    return notes
CHORD_WINDOW = .015  # Seconds; onsets this close count as one chord.
REST_SECONDS = .03  # Shorter gaps between melody notes are played legato.
DEFAULT_TEMPO = 120.


def midi_notes(path, with_ticks=False):
    """(start s, end s, pitch), applying the tempo map and ignoring drum channel 10.

    with_ticks appends (start tick, end tick, velocity) for note-roll conversion.
    """
    from phosphor.audio.live_midi import mido_module
    mido = mido_module()
    file = mido.MidiFile(str(path))
    if file.type == 2 or file.ticks_per_beat <= 0:
        raise ValueError('Use a synchronous MIDI file with ticks per beat.')
    notes, held, clock, ticks, tempo = [], {}, 0., 0, 500000
    for message in mido.merge_tracks(file.tracks):
        clock += mido.tick2second(message.time, file.ticks_per_beat, tempo)
        ticks += message.time
        if message.type == 'set_tempo': tempo = message.tempo
        if getattr(message, 'channel', None) == 9: continue
        key = (getattr(message, 'channel', None), getattr(message, 'note', None))
        if message.type == 'note_on' and message.velocity > 0 and message.channel != 9:
            held.setdefault(key, []).append((clock, ticks, message.velocity))
        elif message.type in ('note_on', 'note_off') and held.get(key):
            start, first, velocity = held[key].pop(0)
            note = (start, clock, message.note)
            notes.append(note + (first, ticks, velocity) if with_ticks else note)
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
    from phosphor.audio.live_midi import mido_module
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
