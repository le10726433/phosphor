"""Audio import, history, display signal and linked-media resolution in Composer."""
import _path  # noqa: F401
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from phosphor.editor import qt_app as q
import soundfile
from phosphor.engine.composition import COMPOSER, SANDBOX, render_song
from phosphor.storage.project_files import save_project


q.configure_graphics()
app = QApplication([])
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    wav = root / 'tone.wav'
    t = np.arange(48000) / 48000
    soundfile.write(wav, .25*np.cos(2*np.pi*440*t), 48000, subtype='FLOAT')
    with patch.object(q, 'write_workspace', lambda *args: None), \
         patch.object(q.Window, 'remember_file', lambda *args: None):
        window = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
        try:
            window.resize(1220, 840); window.show(); app.processEvents()
            window.timer.stop()
            window.set_workspace(SANDBOX)
            assert not window.add_audio_action.isEnabled()
            window.set_workspace(COMPOSER)
            assert window.add_audio_action.isEnabled()
            # A preceding edit with the same focus must remain a separate Undo step.
            window.composer_panel.bpm.setFocus(); app.processEvents()
            window.composer_panel.bpm.setValue(123)
            window.commit_history()
            assert window.project.bpm == 123, window.project.bpm
            window.playhead = float(window.project.seconds(2.))
            window.add_audio_clip_file(str(wav))
            assert len(window.project.audio_clips) == 1
            assert abs(window.project.audio_clips[0].start - 2.) < 1e-12
            assert window.undo_action.text() == 'Undo Add audio clip'
            imported = window.project
            window.composer_panel.timeline.setFocus(); app.processEvents()
            window.undo()
            assert not window.project.audio_clips and window.project.bpm == 123, (window.project.bpm, window.project.audio_clips, window.history.undo_label)
            window.redo(); assert window.project == imported
            times = window.playhead + np.arange(128)/window.audio.sample_rate
            machine = window.output_settings()
            assert np.any(render_song(times, window.project, machine))
            assert not np.any(render_song(times, replace(window.project, audio_clips=()), machine))
            project_path = root / 'song.project.json'
            save_project(project_path, window.project)
            window.history.mark_saved(); window.project_dirty = False
            media = root / 'song media'; media.mkdir()
            moved = media / wav.name; wav.rename(moved)
            window.open_project_path(str(project_path))
            assert not window.project.audio_clips[0].missing
            assert window.project.audio_clips[0].path == str(moved.resolve())
            # The decode worker may still be reading the moved file (opening primes it): wait, or Windows refuses the delete.
            from phosphor.engine import audio_clips as clips_engine; assert clips_engine.wait_idle(10)
            moved.unlink()
            # A3: a missing file opens the Missing audio files dialog (here: Skip) instead of a notice.
            shown = []
            window.run_dialog = lambda dialog: (shown.append(dialog.keys()), dialog.reject())
            window.open_project_path(str(project_path))
            assert window.project.audio_clips[0].missing
            assert len(shown) == 1 and len(shown[0]) == 1, shown
        finally:
            window.history_timer.stop(); window.timer.stop()
            window.force_close = True
            window.shutdown_audio(); window.release_views(); window.close()
print('Audio clips passed: import, undo/redo, signal, moved media and the missing-file dialog.')
