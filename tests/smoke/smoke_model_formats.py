"""Native/--headless model format import through the Sandbox editor."""
import _path  # noqa: F401
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

if '--headless' in sys.argv:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from phosphor.editor import qt_app as q
from phosphor.engine.model_object import MODEL_OBJECT
from tests.unit.mesh_files import write_cube_files

q.configure_graphics()
app = QApplication([])
w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
w.show()
QTest.qWait(50)
try:
    with tempfile.TemporaryDirectory() as folder:
        with patch.object(q.QMessageBox, 'critical') as critical, patch.object(q.QMessageBox, 'information') as information:
            for path in write_cube_files(folder):
                before = len(w.scene.settings.shapes)
                w.add_model(path)
                assert len(w.scene.settings.shapes) == before+1, path
                shape = w.scene.settings.shapes[-1]
                assert shape.solid == MODEL_OBJECT and len(shape.model.edges) == 12, path
            critical.assert_not_called()
            information.assert_not_called()
            w.add_model(os.path.join(folder, 'cube.fbx'))
            assert 'Unsupported 3D model file type' in str(critical.call_args[0][2])
        with patch.object(q.QFileDialog, 'getOpenFileName', return_value=('', '')) as dialog:
            w.import_model()
            assert dialog.call_args[0][3].startswith('3D models (*.obj *.stl *.gltf *.glb);;'), dialog.call_args
finally:
    w.timer.stop()
    w.shutdown_audio()
    w.release_views()
    w.force_close = True
    w.close()
    app.processEvents()
print('Model formats passed: OBJ, ASCII/binary STL, glTF with external buffer, GLB; 12 cube edges each.')
