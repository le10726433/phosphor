import _path  # noqa: F401
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from phosphor.editor import qt_app as q
from phosphor.audio.live_midi import mido_module
from phosphor.audio.score_source import midi_notes, melody_line
from phosphor.engine.composition import COMPOSER, SANDBOX, PITCH_RANGE
from phosphor.engine.midi_notes import read_midi
from tests.example_midi import example_midi


def seconds_to_ticks(path, seconds):
    mido = mido_module(); file = mido.MidiFile(str(path))
    elapsed, ticks, tempo = 0., 0, 500000
    for message in mido.merge_tracks(file.tracks):
        duration = mido.tick2second(message.time, file.ticks_per_beat, tempo)
        if seconds <= elapsed + duration + 1e-9:
            return ticks + mido.second2tick(max(0., seconds-elapsed), file.ticks_per_beat, tempo)
        elapsed += duration; ticks += message.time
        if message.type == 'set_tempo': tempo = message.tempo
    return ticks


q.configure_graphics()
app = QApplication([])
with tempfile.TemporaryDirectory() as folder, patch.object(q, 'write_workspace', lambda *args: None):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
    try:
        w.show(); app.processEvents(); w.timer.stop()
        score = example_midi()
        expected = [note for note in melody_line(midi_notes(score)) if PITCH_RANGE[0] <= note[2] <= PITCH_RANGE[1]]
        before = w.project
        assert w.import_midi_notes(score, 'replace', True, chords=False)
        w.commit_history(); after = w.project
        assert [note.pitch for note in after.notes] == [note[2] for note in expected]
        tpb = mido_module().MidiFile(str(score)).ticks_per_beat
        for actual, source in zip(after.notes, expected):
            assert abs(actual.start - seconds_to_ticks(score, source[0])/tpb) < 1e-8
        w.composer_panel.timeline.setFocus(); w.undo(); assert w.project == before
        w.redo(); assert w.project == after
        path = Path(folder)/'export.mid'
        assert w.export_midi_notes(path)
        exported = mido_module().MidiFile(str(path))
        assert exported.type == 0 and read_midi(path).bpm == round(after.bpm, 3)
        assert sum(msg.type == 'note_on' and msg.velocity > 0 for track in exported.tracks for msg in track) == len(after.notes)
        w.seek_song(float(w.project.seconds(8.13)))
        assert w.import_midi_notes(score, 'insert', False, chords=False)
        assert len(w.project.notes) == 2*len(after.notes)
        offset = w.composer_panel.timeline.snapped(8.13)
        for note in after.notes:
            assert any(candidate.pitch == note.pitch and abs(candidate.start-note.start-offset) < 1e-8
                       for candidate in w.project.notes)
        assert w.project.bpm == after.bpm and w.project.clips == after.clips
        w.commit_history(); w.undo(); assert w.project == after
        w.set_workspace(SANDBOX)
        assert not w.import_midi_action.isVisible() and not w.export_midi_action.isVisible()
    finally:
        w.history_timer.stop(); w.timer.stop(); w.force_close = True
        w.shutdown_audio(); w.release_views(); w.close()
print('MIDI notes passed: bundled melody/tick onsets, one-step undo/redo, insertion and type 0 export.')
