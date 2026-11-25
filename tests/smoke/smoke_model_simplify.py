"""Native/--headless simplification preview, import wiring and a single Undo."""
import _path  # noqa: F401
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

if '--headless' in sys.argv:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from phosphor.editor import qt_app as q
from phosphor.editor import model_import_dialog as mid
from phosphor.editor.model_import_dialog import ModelImportDialog
from phosphor.engine.mesh_formats import read_mesh, source_triangles, SUGGESTED_TRIANGLES
from tests.fixtures.dense_mesh import dense_mesh_obj


def wait_for(dialog, predicate):
    if predicate():
        return
    loop = QEventLoop()
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)

    def done():
        if predicate():
            loop.quit()

    dialog.preview_done.connect(done)
    timeout.start(60000)
    try:
        loop.exec()
    finally:
        timeout.stop()
        dialog.preview_done.disconnect(done)
    assert predicate(), 'Preview failed or timed out: '+dialog.preview.text()


q.configure_graphics()
app = QApplication([])
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder)/'dense.obj'
    path.write_text(dense_mesh_obj(), encoding='utf-8')
    with patch.object(q, 'read_preferences', lambda *args: dict(q.DEFAULT_PREFERENCES)), \
         patch.object(q, 'read_signal_preferences', lambda *args: dict(q.DEFAULT_SIGNAL_PREFERENCES)):
        # Undo is a Composer action; import edits the selected clip's ordinary scene.
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Composer'))
    w.show()
    dialog = None
    information, critical = Mock(), Mock()
    try:
        with patch.object(q, 'QMessageBox', SimpleNamespace(information=information, critical=critical)):
            mesh = read_mesh(path)
            n = source_triangles(mesh)
            dialog = ModelImportDialog(w, mesh, n)
            dialog.show()
            wait_for(dialog, lambda: dialog.ready_result() is not None)
            assert dialog.target() is None
            assert f'Result: {n:,} triangles' in dialog.preview.text()
            dialog.checkbox.setChecked(True)
            dialog.spin.setValue(n//5)
            wait_for(dialog, lambda: dialog.ready_result() is not None and dialog.target() == n//5)
            preview_model, report = dialog.ready_result()
            assert report.simplified and n//5-1 <= report.result_triangles <= n//5
            # Acceptance preserves the cached preview for the editor to reuse.
            dialog.accept()
            assert dialog.ready_result()[0] == preview_model
            w.activateWindow()
            w.composer_panel.timeline.setFocus()
            app.processEvents()
            w.commit_history()
            before = len(w.scene.settings.shapes)
            w.add_model(path, dialog.target(), dialog.ready_result())
            assert len(w.scene.settings.shapes) == before+1
            assert w.scene.settings.shapes[-1].model == preview_model
            information.assert_called_once()
            assert 'Simplified from' in information.call_args[0][2]
            assert information.call_args[0][1] == 'Model imported'
            critical.assert_not_called()
            w.commit_history()
            w.composer_panel.timeline.setFocus()
            w.undo_action.trigger()
            assert len(w.scene.settings.shapes) == before

            fake = SimpleNamespace(exec=lambda: QDialog.DialogCode.Accepted,
                                   target=lambda: n//4, ready_result=lambda: None)
            factory = Mock(return_value=fake)
            with patch.object(w, 'model_import_dialog', factory), \
                 patch.object(q, 'QFileDialog', SimpleNamespace(getOpenFileName=lambda *args: (str(path), ''))):
                w.import_model()
            factory.assert_called_once()
            assert len(w.scene.settings.shapes) == before+1
            assert information.call_count == 2
            imported = w.scene.settings.shapes[-1].model
            assert 'Simplified from' in information.call_args[0][2] and imported != preview_model
            critical.assert_not_called()

            # 0.59.0-dev: over SUGGESTED_TRIANGLES the dialog opens with Simplify ticked at
            # that target, shows progress, enables Import only once the preview is ready,
            # and asks before importing the full model.
            large = Path(folder)/'large.obj'
            large.write_text(dense_mesh_obj(rings=80, segments=80), encoding='utf-8')
            mesh = mid.run_with_progress(w, 'Reading…', lambda: read_mesh(large))
            n = source_triangles(mesh)
            assert n > SUGGESTED_TRIANGLES, n
            dialog.close()
            dialog = ModelImportDialog(w, mesh, n)
            dialog.show()
            app.processEvents()
            assert dialog.checkbox.isChecked() and dialog.target() == SUGGESTED_TRIANGLES
            assert not dialog.import_button.isEnabled() and dialog.progress.isVisible()
            wait_for(dialog, lambda: dialog.ready_result() is not None)
            assert dialog.import_button.isEnabled() and not dialog.progress.isVisible()
            assert f'Result: {SUGGESTED_TRIANGLES:,} triangles' in dialog.preview.text() or \
                f'Result: {SUGGESTED_TRIANGLES-1:,} triangles' in dialog.preview.text(), dialog.preview.text()
            answers = []
            def ask(answer):
                def question(*args):
                    answers.append(args[1]); return answer
                return question
            with patch.object(mid.QMessageBox, 'question', ask(QMessageBox.StandardButton.No)):
                dialog.checkbox.setChecked(False)
            assert answers == ['Import full model'] and dialog.checkbox.isChecked()
            assert dialog.ready_result() is not None  # Declining keeps the finished preview.
            with patch.object(mid.QMessageBox, 'question', ask(QMessageBox.StandardButton.Yes)):
                dialog.checkbox.setChecked(False)
            assert dialog.target() is None and not dialog.import_button.isEnabled()
            wait_for(dialog, lambda: dialog.ready_result() is not None)
            assert f'Result: {n:,} triangles' in dialog.preview.text(), dialog.preview.text()
    finally:
        if dialog is not None:
            dialog.close()
        w.timer.stop()
        w.shutdown_audio()
        w.release_views()
        w.force_close = True
        w.close()
        app.processEvents()
print('Model simplify passed: asynchronous previews, cached import, one Undo, import dialog wiring, '
      'large-model default, progress and the full-model confirmation.')
