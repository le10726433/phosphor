"""Window recording (mono cut and chords), count-in, loop and one-step undo; no MIDI/audio hardware."""
import _path  # noqa: F401
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from phosphor.editor import qt_app as q
from phosphor.audio.audio_preview import AudioPreview
from phosphor.engine.composition import COMPOSER
from phosphor.storage import project_files


now = [0.]


class SilentAudio(AudioPreview):
    active = False

    @property
    def running(self): return self.active

    def start(self, settings, mode):
        self.active = True
        self.frames = 0
        self.output_latency = .1
        self.playback.reset(self.sample_rate, self.output_latency)
        self.playback.clock = lambda: now[0]
        self.playback.origin = now[0] + self.output_latency

    def stop(self): self.active = False


class Message(SimpleNamespace):
    def copy(self): return Message(**vars(self))


q.configure_graphics()
app = QApplication([])
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    with patch.object(q, 'AudioPreview', SilentAudio), \
         patch.object(q, 'read_preferences', lambda: dict(q.DEFAULT_PREFERENCES)), \
         patch.object(q, 'read_signal_preferences', lambda: dict(q.DEFAULT_SIGNAL_PREFERENCES)), \
         patch.object(project_files, 'WORKSPACE_PATH', root / 'workspace.json'), \
         patch.object(q, 'write_workspace', lambda name: None), \
         patch.object(q, 'settle_garbage', lambda: None):
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
        w.force_close = True
        try:
            panel = w.composer_panel
            panel.record.setChecked(True)
            assert not panel.record.isChecked() and w.midi_recorder is None
            w.audio.midi.port = SimpleNamespace(close=lambda: None)
            # Chords off: the mono cut below must not depend on the new-project default.
            w.set_project(replace(w.project, chords=False)); w.commit_history()
            baseline = w.project
            assert not baseline.chords
            panel.count_in.setCurrentIndex(1)
            panel.record.setChecked(True)
            assert w.audio.count_in_seconds == 2. and panel.record.isChecked()

            def send(seconds, kind='note_on', note=60):
                now[0] = seconds
                w.audio.midi.receive(Message(type=kind, note=note, velocity=100, channel=0))
                w.drain_note_recording()

            send(.5)  # Count-in: ignored.
            send(2.6)  # Compensated song beat 1.
            send(3.1, note=64)  # New note cuts the old one at beat 2.
            assert w.project is baseline  # No edits during a take.
            now[0] = 3.6
            panel.record.setChecked(False)
            assert not w.audio.running
            assert [(round(n.start, 6), round(n.length, 6), n.pitch) for n in w.project.notes] == [(1., 1., 60), (2., 1., 64)]
            w.undo()
            assert w.project == baseline  # One undo removes the entire take.
            w.redo()
            assert len(w.project.notes) == 2
            w.undo()

            # Chords on: every held key is recorded, so the two notes overlap.
            chorded = replace(baseline, chords=True)
            w.set_project(chorded); w.commit_history()
            assert w.project.chords
            now[0] = 0.
            panel.record.setChecked(True)
            assert w.audio.count_in_seconds == 2. and panel.record.isChecked()
            send(.5)  # Count-in: ignored.
            send(2.6)  # Beat 1.
            send(3.1, note=64)  # Beat 2; 60 keeps sounding.
            assert w.project is chorded
            now[0] = 3.6
            panel.record.setChecked(False)
            assert not w.audio.running
            assert [(round(n.start, 6), round(n.length, 6), n.pitch) for n in w.project.notes] == [(1., 2., 60), (2., 1., 64)]
            w.undo()
            assert w.project == chorded  # One undo removes the entire take.
            w.set_project(baseline); w.commit_history()
            assert not w.project.chords

            # Options travel through the real menu actions; stopping Play commits.
            panel.record_replace.setChecked(True)
            panel.record_quantise.setChecked(True)
            panel.count_in.setCurrentIndex(0)
            w.seek_song(0.)
            now[0] = 0.
            panel.record.setChecked(True)
            send(.24)
            send(.49, 'note_off')
            w.stop_song()
            assert [(n.start, n.length) for n in w.project.notes] == [(.25, .5)]
            w.undo(); assert w.project == baseline

            # A take can join playback; loop positions remain in the loop range.
            looped = replace(baseline, loop=True, loop_end=4.)
            w.set_project(looped); w.commit_history()
            panel.record_quantise.setChecked(False)
            panel.record_overdub.setChecked(True)
            now[0] = 0.
            w.start_song(count_in=0)
            panel.record.setChecked(True)
            send(1.6)  # Beat 3 after compensation.
            send(2.35, 'note_off')  # Beat .5 after wrap.
            w.stop_song()
            assert [(round(n.start, 6), round(n.length, 6)) for n in w.project.notes] == [(0., .5), (3., 1.)]
            w.undo(); assert w.project == looped
        finally:
            w.stop_song(); w.close(); app.processEvents()
print('MIDI record smoke passed: count-in, callback, timing, mono cut, chords overlap, options, held stop, single undo.')
