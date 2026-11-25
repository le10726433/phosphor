"""Animated import, playback controls, undo and responsive layout (--headless)."""
import _path  # noqa: F401
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from phosphor.editor import qt_app as q
from phosphor.editor.model_import_dialog import ModelImportDialog
from phosphor.engine.mesh_formats import read_mesh, source_triangles

q.configure_graphics()
app = QApplication([])
w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Composer'))
w.show()
dialog = None
try:
    mesh = read_mesh(Path(__file__).parents[1] / 'fixtures/low_poly_character_walk_cycle/scene.gltf')
    dialog = ModelImportDialog(w, mesh, source_triangles(mesh))
    assert dialog.animation.count() == 3
    assert dialog.animation.currentData() == 4  # Longest action, EmptyAction.
    assert dialog.in_place.isChecked()
    dialog.animation.setCurrentIndex(dialog.animation.findData(0))
    dialog.checkbox.setChecked(True); dialog.spin.setValue(300)
    dialog.show()
    deadline = time.monotonic()+60
    while dialog.ready_result() is None and time.monotonic() < deadline:
        QTest.qWait(20)
    result = dialog.ready_result()
    assert result is not None, dialog.preview.text()
    model, report = result
    assert model.animation.count == 481 and report.result_triangles == 300
    assert 'Embedded animation' in dialog.preview.text()
    dialog.close()
    w.scene.add_model(model); w.sync(); w.commit_history()
    w.show_panel(q.PANEL_SCENE); w.scene_sections.setCurrentIndex(1)
    w.resize(1000, 700); QTest.qWait(50)
    assert w.transform_panel.animation_box.isVisible()
    assert w.panels[q.PANEL_SCENE].horizontalScrollBar().maximum() == 0
    w.transform_panel.animation_fields['speed'].setValue(2.)
    w.commit_history()
    assert w.scene.shape.animation.speed == 2.
    w.undo()
    assert w.scene.shape.animation.speed == 1.
    w.redo()
    assert w.scene.shape.animation.speed == 2.
finally:
    if dialog is not None: dialog.close()
    w.timer.stop(); w.shutdown_audio(); w.release_views()
    w.force_close = True; w.close(); app.processEvents()
print('Animated model import, controls, undo and 1000x700 layout passed.')
