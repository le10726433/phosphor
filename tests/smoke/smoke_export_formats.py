"""Export a short Composer loop as FLAC 24/192; exercise dialog and fallback."""
import _path  # noqa: F401
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtTest import QTest
from phosphor.editor import qt_app as q
from phosphor.editor.export_dialog import ExportDialog
from phosphor.storage import audio_writer, project_files
from phosphor.storage.app_files import DEFAULT_PREFERENCES
from phosphor.storage.app_files import read_preferences
from phosphor.engine.composition import COMPOSER, Note, render_song

q.configure_graphics(); app = QApplication([])
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    with patch.object(q, 'PREFERENCES_PATH', root/'preferences.json'), \
         patch.object(q, 'read_preferences', return_value=dict(DEFAULT_PREFERENCES)), \
         patch.object(project_files, 'WORKSPACE_PATH', root/'workspace.json'), \
         patch.object(q, 'write_workspace', lambda name: project_files.write_workspace(name, root/'workspace.json')):
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
        try:
            w.resize(1220, 840); w.show(); QTest.qWait(100)
            w.add_object('Cube')
            w.set_project(replace(w.project, bpm=120., notes=(Note(0., 2., 57),), loop_start=.25, loop_end=.75))
            dialog = ExportDialog(w, w.preferences, 8., .25)
            before = dialog.size_label.text(); dialog.rate.setCurrentIndex(3)
            assert dialog.size_label.text() != before
            dialog.format.setCurrentText('FLAC'); assert dialog.depth.findData(32) == -1
            dialog.depth.setCurrentIndex(dialog.depth.findData(24)); dialog.range.setCurrentIndex(1)
            dialog.close()
            with patch.object(audio_writer, 'soundfile', None):
                fallback = ExportDialog(w, w.preferences, 8.)
                assert not fallback.format.model().item(1).isEnabled()
                assert fallback.format.model().item(1).toolTip()
                assert fallback.depth.findData(32) == -1
                fallback.close()

            target = root/'loop.flac'
            with patch.object(w, 'error') as error:
                w.export(path=target, format='FLAC', depth=24, rate=192000, range='loop')
                deadline = time.monotonic()+30
                while w.exporting and time.monotonic() < deadline:
                    QTest.qWait(10); time.sleep(.001)
                assert not w.exporting; error.assert_not_called()
            data, rate = soundfile.read(target)
            assert rate == 192000 and data.shape == (48000, 2)
            assert soundfile.info(target).subtype == 'PCM_24'
            settings = replace(w.output_settings(), sample_rate=rate, motion_time_offset=0.)
            expected = render_song(.125+np.arange(48000)/rate, w.project, settings)
            np.testing.assert_allclose(data, np.clip(expected, -1, 1), atol=2/2**23)
            w.save_preferences()
            assert read_preferences(root/'preferences.json')['export_rate'] == 192000
            # The modal options and file chooser can both be patched, as other smokes do.
            # A subclass, not a MagicMock on the Shiboken class: that crashes signal connection.
            class Accepting(ExportDialog):
                def exec(self): return QDialog.DialogCode.Accepted
            from phosphor.editor import export_dialog
            with patch.object(export_dialog, 'ExportDialog', Accepting), \
                 patch.object(q.QFileDialog, 'getSaveFileName', return_value=('', '')) as save:
                w.export(); save.assert_called_once(); assert not w.exporting
            assert w.preferences['export_rate'] == 192000 and w.preferences['export_range'] == 'loop'
        finally:
            w.preferences_save.stop(); w.timer.stop(); w.shutdown_audio()
            w.release_views(); w.force_close = True; w.close(); app.processEvents()
print('Export formats passed: FLAC 24/192 loop, live size, fallback choices and patchable dialogs.')
