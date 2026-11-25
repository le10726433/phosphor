import _path  # noqa: F401 (makes the phosphor package importable)
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from phosphor.editor import qt_app as q
from phosphor.engine.composition import COMPOSER, SANDBOX
from phosphor.storage import autosave as a
from phosphor.storage import app_files
from phosphor.storage.project_files import read_project, save_project
from phosphor.storage.app_files import read_scene


q.configure_graphics(); app = QApplication([])
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER, skip_recovery=True))
    try:
        assert not w.autosave_enabled
        w.autosave_dir = root/'autosave'; w.autosave_now()
        assert not w.autosave_dir.exists()
        w.autosave_enabled = True
        w.resize(1220, 840); w.show(); QTest.qWait(150)
        assert w.autosave_timer.interval() == 120000
        w.set_project(replace(w.project, bpm=137.))
        w.sandbox_scene.add('Cube')
        w.autosave_now()
        assert w.recovery_writer.flush(10)  # 0.76.0: recovery files are written on a worker.
        project_copy = a.recovery_path(w.autosave_dir, os.getpid(), COMPOSER)
        scene_copy = a.recovery_path(w.autosave_dir, os.getpid(), SANDBOX)
        assert read_project(project_copy) == w.project
        assert read_scene(scene_copy)[0] == w.sandbox_scene.settings

        # A stalled writer: GUI events keep running, ticks coalesce, the latest edit survives.
        gate, started, written, write = threading.Event(), threading.Event(), [], a.write_recovery
        def stalled(directory, workspace, writer, original=None, pid=None):
            started.set(); gate.wait(10)
            written.append(workspace); return write(directory, workspace, writer, original, pid)
        with patch.object(a, 'write_recovery', side_effect=stalled):
            w.autosave_now(); assert not w.recovery_writer.busy()  # Unchanged: nothing to write.
            w.set_project(replace(w.project, bpm=138.)); w.autosave_now()
            assert started.wait(5)
            ticks = []; timer = q.QTimer(); timer.timeout.connect(lambda: ticks.append(1)); timer.start(10)
            QTest.qWait(200); timer.stop()
            assert len(ticks) >= 5, ticks  # The GUI thread is not blocked by the write.
            for bpm in (139., 140., 141.): w.set_project(replace(w.project, bpm=bpm)); w.autosave_now()
            gate.set(); assert w.recovery_writer.flush(10)
        assert written == [COMPOSER, COMPOSER], written
        assert read_project(project_copy).bpm == 141. == w.project.bpm

        # Worker errors reach the status bar through the queued signal.
        replace_file = app_files.os.replace
        def fail_document(source, target):
            if Path(target) == project_copy: raise OSError('disk failure')
            return replace_file(source, target)
        before = project_copy.read_bytes()
        with patch.object(app_files.os, 'replace', side_effect=fail_document):
            w.set_project(replace(w.project, bpm=142.)); w.autosave_now(); assert w.recovery_writer.flush(10)
        QTest.qWait(50)
        assert w.notice and w.notice[0] == 'Could not autosave: disk failure', w.notice
        assert project_copy.read_bytes() == before
        w.autosave_now(); assert w.recovery_writer.flush(10)  # The failed revision is retried.
        assert read_project(project_copy).bpm == 142.
        target = root/'Saved.project.json'
        with patch.object(q.QFileDialog, 'getSaveFileName', return_value=(str(target), '')):
            assert w.save_project(True)
        assert not project_copy.exists() and not a.meta_path(project_copy).exists()
        assert scene_copy.exists()  # Saving one document leaves the other protected.
        scene_target = root/'Saved.scene.json'
        with patch.object(q.QFileDialog, 'getSaveFileName', return_value=(str(scene_target), '')):
            assert w.save_scene(True)
        assert not scene_copy.exists()

        recovered = replace(w.project, bpm=149.)
        stale = 987654321
        a.write_recovery(w.autosave_dir, COMPOSER, lambda path: save_project(path, recovered), target, stale)
        a.write_recovery(w.autosave_dir, SANDBOX,
            lambda path: q.atomic_json(path, w.sandbox_scene.document()), scene_target, stale)
        with patch.object(a, 'process_alive', side_effect=lambda pid: pid == os.getpid()):
            found = a.find_stale(w.autosave_dir)
        for item in found: w.restore_autosave(item)
        assert w.project == recovered and w.project_dirty
        assert w.project_path == target
        assert not w.history.can_undo and not w.history.can_redo and w.history.dirty
        w.commit_history(); assert w.project_dirty
        assert w.sandbox_scene.dirty and w.sandbox_scene.path == scene_target
        assert not (w.autosave_dir/f'{stale}.lock').exists()
        assert read_project(project_copy) == recovered
        # A rejected close preserves recovery; an accepted Don't save close removes it.
        with patch.object(w, 'confirm_all', return_value=False):
            assert not w.close()
        assert project_copy.exists()
        with patch.object(w, 'confirm_all', return_value=True):
            assert w.close()
        assert list(w.autosave_dir.iterdir()) == []
    finally:
        w.force_close = True; w.close(); app.processEvents()
print('Autosave passed: opt-in, both documents, save cleanup, restore dirty/history/paths, close cleanup.')
