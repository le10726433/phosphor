"""Builder Text tool, bold outline and Z extrusion through the real UI. Native or --headless."""
import _path  # noqa: F401  (makes the phosphor package importable)
import os
import sys
import tempfile
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QLineEdit
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from phosphor.editor import qt_app as q
from phosphor.engine.custom_shape import CUSTOM_OBJECT, compiled, text_drawing
from phosphor.engine.signal_engine import projected_solid
from phosphor.storage.app_files import read_scene
q.os.environ['QSG_RENDER_LOOP'] = 'basic'
q.configure_graphics(); app = QApplication([])
w = q.Window(SimpleNamespace(scene=None, ready_file=None)); w.resize(1220, 840); w.show(); QTest.qWait(100)
w.save_brightness = lambda: None
w.open_builder(); QTest.qWait(100); b = w.builder


def click(point):
    p = b.canvas.pixel(point)
    QTest.mouseClick(b.view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(*map(round, p)))


# Text tool: the entry row appears only for Text; a click places centred strokes.
assert not b.text_panel.isVisible()
b.tools['Text'].click(); assert b.tool == 'Text' and b.text_panel.isVisible()
click((0., 0.)); assert 'Type the text' in b.notice.text() and not b.drawing.playable
b.findChild(QLineEdit, 'builder_text').setText('HI'); b.findChild(QDoubleSpinBox, 'builder_text_size').setValue(.5)
click((.2, .1)); QTest.qWait(30)
points = np.array([n[1:] for n in b.drawing.nodes])
count = len(text_drawing('HI').segments)
assert len(b.drawing.segments) == count and abs(np.ptp(points[:, 1])-.5) < 1e-9, (len(b.drawing.segments), np.ptp(points[:, 1]))
assert np.allclose((points.min(axis=0)+points.max(axis=0))/2, (.2, .1), atol=.01)  # Click rounding.
assert b.selected_segments == frozenset(range(count)) and 'Placed text' in b.notice.text()
# At 1000x700 the style rows replace the node controls and nothing in the panel is squeezed.
w.resize(1000, 700); QTest.qWait(150)
assert b.style_panel.isVisible() and not b.node_panel.isVisible()
assert b.info.height() >= b.info.sizeHint().height(), (b.info.height(), b.info.sizeHint().height())
assert all(s.height() >= s.minimumSizeHint().height() for s in b.style_spins.values())
w.resize(1220, 840); QTest.qWait(100)
# Style controls follow the selection; bold traces the outline and keeps the centre line faint.
bold, depth = b.style_spins['bold'], b.style_spins['depth']
assert bold.isEnabled() and bold.value() == 0 and bold.specialValueText() == 'Off'
bold.setValue(.06); bold.editingFinished.emit()
style = b.drawing.styles[0]
assert style.bold == .06 and style.depth == 0 and b.selected_segments == frozenset(range(count))
vertices, _, _, travel = compiled(b.drawing)
assert len(travel) == 1 and len(vertices) > 40  # Two outlined letters, one travel link.
depth.setValue(.4); depth.editingFinished.emit()
assert b.drawing.styles[0].depth == .4 and abs(np.ptp(compiled(b.drawing)[0][:, 2])-.4) < 1e-9
assert len(b.past) >= 3
b.undo(); assert b.drawing.styles[0].depth == 0
b.redo(); assert b.drawing.styles[0].depth == .4
# Deselect: the controls disable; a plain stroke added later stays unstyled.
b.set_tool('Line'); click((-1., -.8)); click((-.6, -.8)); QTest.qWait(30)
line_nodes = set(b.drawing.segments[-1])
assert len(b.drawing.segments) == count+1 and len(b.drawing.styles) == 1 and not line_nodes & set(b.drawing.styles[0].nodes)
b.selected_segments = frozenset(); b.refresh(); assert not bold.isEnabled()
# Apply: the scene plays the extruded outline, and turning it reveals the depth.
b.apply(); QTest.qWait(50)
shape = w.scene.shape
assert shape.solid == CUSTOM_OBJECT and shape.custom.styles == b.drawing.styles
w.scene.change('rotation_y', 60); w.sync()
posed = projected_solid(0., w.scene.shape)
face_on = projected_solid(0., w.scene.shape.__class__(**{**vars(w.scene.shape), 'rotation_y': 0}))
assert np.ptp(posed[:, 0]) != np.ptp(face_on[:, 0])
w.tick()
# Shape files and scene files keep the styles.
with tempfile.TemporaryDirectory() as folder:
    shape_path = Path(folder)/'hi.shape.json'
    with patch('phosphor.editor.shape_builder.QFileDialog.getSaveFileName', return_value=(str(shape_path), '')):
        b.save_shape()
    import json
    saved = json.loads(shape_path.read_text())
    assert saved['version'] == 5 and saved['drawing']['styles'][0]['bold'] == .06
    scene_path = Path(folder)/'styled.scene.json'; w.scene.save(scene_path)
    assert read_scene(scene_path)[0].shapes[-1].custom.styles == b.drawing.styles
(q.HERE/'.user').mkdir(exist_ok=True)
b.fit(); QTest.qWait(100); b.view.grabWindow().save(str(q.HERE/'.user/builder-text-style.png'))
assert w.leave_builder()
w.timer.stop(); w.shutdown_audio(); w.release_views(); w.force_close = True; w.close(); app.processEvents()
print('Builder text tool, bold outline, Z extrusion, undo/redo, apply and files passed.')
