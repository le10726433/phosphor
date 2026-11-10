"""Native/--headless constructor interactions and frozen audio snapshot checks."""
import os,sys,tempfile
if '--headless' in sys.argv:os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
import qt_app as q
from custom_shape import Drawing,CUSTOM_OBJECT
from app_files import read_scene
q.configure_graphics();app=QApplication([])
w=q.Window(SimpleNamespace(scene=None,ready_file=None));w.show();QTest.qWait(100)
before=w.scene.document();w.open_builder();QTest.qWait(100);b=w.builder
assert w.building and not w.splitter.isVisible()
for width,height in ((1000,700),(1220,840)):
    w.resize(width,height);QTest.qWait(50)
    assert w.width()==width and w.height()==height,(w.size(),width,height)
    assert b.canvas.width()>400 and b.canvas.height()>250
def click(point):
    p=b.canvas.pixel(point);QTest.mouseClick(b.view,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(*map(round,p)))
b.live.setChecked(True)
click((-.6,-.5));click((.6,-.5));click((0,.6))
assert len(b.pending)==3 and w.scene.document()==before # unfinished gestures never enter audio
click((-.6,-.5));QTest.qWait(30)
assert not b.pending and len(b.drawing.nodes)==3 and len(b.drawing.edges)==3
assert w.scene.shape.solid==CUSTOM_OBJECT and len(w.scene.settings.shapes)==2
assert w.scene.shape.custom==b.drawing
frozen=w.audio.parameters[0];original=frozen.shapes[-1].custom
b.set_tool('Select');node=b.drawing.nodes[0];p=b.canvas.pixel(node[1:]);end=p+np.array([35.,-15.])
QTest.mousePress(b.view,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(*map(round,p)))
QTest.mouseMove(b.view,QPoint(*map(round,end)),30)
assert w.scene.shape.custom==original
QTest.mouseRelease(b.view,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(*map(round,end)))
assert w.scene.shape.custom!=original and frozen.shapes[-1].custom==original
b.undo();assert b.drawing==original and w.scene.shape.custom==original
b.redo();assert b.drawing!=original
b.live.setChecked(False);applied=w.scene.shape.custom
b.selected=b.drawing.nodes[0][0];b.refresh()
b.coordinates[0].setValue(-.3);b.numeric_move()
assert w.scene.shape.custom==applied and b.unapplied
b.apply();assert w.scene.shape.custom==b.drawing
# Cancel a provisional stroke without leaving a phantom node or audio mutation.
b.set_tool('Polyline');click((.8,.6));click((.8,-.6));snap=b.drawing
QTest.keyClick(b.view,Qt.Key.Key_Escape);assert b.drawing==snap and not b.pending
# Pixel-radius snapping is independent of zoom; Alt temporarily bypasses it.
point=b.drawing.nodes[0]
for zoom in (.5,3):
    b.canvas.zoom=zoom;p=b.canvas.pixel(point[1:])+[6,0]
    assert b.nearest(p,Qt.KeyboardModifier.NoModifier)==point[0]
    assert b.nearest(p,Qt.KeyboardModifier.AltModifier) is None
b.fit()
# An open stroke finishes with Enter and is one undoable gesture.
click((.8,.6));click((.8,-.6));QTest.keyClick(b.view,Qt.Key.Key_Return)
assert not b.pending and len(b.drawing.edges)==len(snap.edges)+1
b.undo();assert b.drawing==snap
with tempfile.TemporaryDirectory() as folder:
    asset=Path(folder)/'shape.json';scene=Path(folder)/'scene.json'
    with patch.object(q.QFileDialog,'getSaveFileName',return_value=(str(asset),'')):b.save_shape()
    saved=b.drawing;b.commit(Drawing('Empty'))
    with patch.object(q.QFileDialog,'getOpenFileName',return_value=(str(asset),'')):b.load_shape()
    assert b.drawing==saved
    b.apply();w.scene.save(scene);loaded,_,_=read_scene(scene)
    assert loaded.shapes==w.scene.settings.shapes
# Native renderer must actually draw construction edges/nodes.
if '--headless' not in sys.argv:
    from PySide6.QtGui import QImage,QPainter
    b.canvas.update();QTest.qWait(150)
    image=b.view.grabWindow().convertToFormat(QImage.Format.Format_RGBA8888)
    assert not image.isNull()
    pixels=np.frombuffer(image.bits(),dtype=np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*4].reshape(image.height(),image.width(),4)
    assert np.count_nonzero(pixels[:,:,:3].max(axis=2)>150)>100
    image.save(str(q.HERE/'.user/shape-builder-canvas.png'))
    w.tick();QTest.qWait(100)
    screenshot=w.grab();painter=QPainter(screenshot)
    painter.drawImage(b.container.mapTo(w,QPoint(0,0)),image)
    painter.drawImage(b.preview.mapTo(w,QPoint(0,0)),b.preview.view.grabWindow())
    painter.end();screenshot.save(str(q.HERE/'.user/shape-builder.png'))
else:
    w.tick();w.grab().save(str(q.HERE/'.user/shape-builder.png'))
assert w.leave_builder() and not w.building and w.splitter.isVisible()
w.open_builder();QTest.qWait(30);assert w.builder.drawing==saved
w.builder.commit(Drawing('Unapplied'))
with patch.object(q.QMessageBox,'question',return_value=QMessageBox.StandardButton.Cancel):assert not w.leave_builder()
with patch.object(q.QMessageBox,'question',return_value=QMessageBox.StandardButton.Discard):assert w.leave_builder()
w.timer.stop();w.shutdown_audio();w.force_close=True;w.close();app.processEvents()
print('Shape builder: draw/close, joined nodes, completed-edit audition, frozen snapshots, drag/undo/redo, files and Back restoration passed.')
