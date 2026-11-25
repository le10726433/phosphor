import _path  # noqa: F401 (makes the phosphor package importable)
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from phosphor.editor import qt_app as q
from phosphor.engine.composition import COMPOSER, SANDBOX
from phosphor.storage import project_files, recent_files


q.configure_graphics(); app = QApplication([])
with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    with patch.object(project_files, 'WORKSPACE_PATH', root/'workspace.json'), patch.object(q, 'write_workspace', lambda name: project_files.write_workspace(name, root/'workspace.json')):
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
        try:
            assert not w.remember_recent
            w.recent_path = root/'recent-files.json'
            w.remember_recent = True
            w.resize(1220, 840); w.show(); QTest.qWait(150)
            project = root/'Recent.project.json'
            with patch.object(q.QFileDialog, 'getSaveFileName', return_value=(str(project), '')):
                assert w.save_project(True)
            w.recent_menu.aboutToShow.emit()
            first = w.recent_menu.actions()[0]
            assert first.text() == project.name
            assert first.toolTip() == str(project.resolve()) == first.statusTip()
            with patch.object(w, 'confirm_project', return_value=False) as confirm:
                first.trigger()
                confirm.assert_called_once()
            with patch.object(w, 'confirm_project', return_value=True), patch.object(w, 'load_project', wraps=w.load_project) as load:
                first.trigger()
                load.assert_called_once()
            w.workspace = SANDBOX
            w.recent_menu.aboutToShow.emit()
            assert w.recent_menu.actions()[0].text() == 'No recent files'
            assert not w.recent_menu.actions()[0].isEnabled()
            w.workspace = COMPOSER
            project.unlink()
            w.recent_menu.aboutToShow.emit()
            assert w.recent_menu.actions()[0].text() == 'No recent files'
            assert recent_files.read_recent('project', w.recent_path) == []
            project.write_text('{broken', encoding='utf-8')
            recent_files.add_recent('project', project, w.recent_path)
            w.recent_menu.aboutToShow.emit()
            with patch.object(w, 'error') as error:
                w.recent_menu.actions()[0].trigger()
                error.assert_called_once()
            assert recent_files.read_recent('project', w.recent_path) == []
            recent_files.add_recent('project', project, w.recent_path)
            w.recent_menu.aboutToShow.emit()
            w.recent_menu.actions()[-1].trigger()
            assert recent_files.read_recent('project', w.recent_path) == []
        finally:
            w.force_close = True; w.close(); app.processEvents()
print('Recent files passed: save, workspace menus, confirm/open, missing and failed reads, clear.')
