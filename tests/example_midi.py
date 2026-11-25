"""A small generated MIDI file for tests, smokes and tools: no music is bundled with phosphor."""
import atexit
from pathlib import Path
import shutil
import tempfile

from phosphor.audio.live_midi import mido_module

_folder = None
MELODY = ((72, 1), (75, 1), (77, 2), (75, 1), (77, 1), (82, 1), (80, 1), (79, 2), (77, 2))


def example_midi(name='example_melody.mid', bpm=96):
    """Write (once per process) a melody with a held chord tone under each note, so imports
    see overlapping notes and the top line differs from the full chord view."""
    global _folder
    if _folder is None:
        _folder = Path(tempfile.mkdtemp(prefix='phosphor-midi-'))
        atexit.register(shutil.rmtree, _folder, True)
    path = _folder/name
    if not path.exists():
        mido = mido_module()
        track = mido.MidiTrack()
        track.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(bpm)))
        for pitch, beats in MELODY:
            track.append(mido.Message('note_on', note=pitch, velocity=90))
            track.append(mido.Message('note_on', note=pitch - 12, velocity=60))
            track.append(mido.Message('note_off', note=pitch, time=480*beats))
            track.append(mido.Message('note_off', note=pitch - 12))
        file = mido.MidiFile(ticks_per_beat=480)
        file.tracks.append(track)
        file.save(str(path))
    return path
