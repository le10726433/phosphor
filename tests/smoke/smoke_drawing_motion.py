"""Native/--headless component roles and offset-preserving in-builder assignment."""
import _path  # noqa: F401  (makes the phosphor package importable)
import os,sys,tempfile,json
if '--headless' in sys.argv:os.environ['QT_QPA_PLATFORM']='offscreen'
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import Qt,QPoint
from PySide6.QtGui import QPainter,QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from phosphor.editor import qt_app as q
from phosphor.engine.custom_shape import Drawing,Curve
from phosphor.engine.signal_engine import builder_motion_parts
from phosphor.storage.app_files import read_scene
q.configure_graphics();app=QApplication([]);w=q.Window(SimpleNamespace(scene=None,ready_file=None));w.show();QTest.qWait(100)
w.open_builder();QTest.qWait(100);b=w.builder
fixture=Drawing('Motion assembly',((0,1,1),(1,3,1),(2,0,2),(3,.4,2),(4,.5,3),(5,.7,3)),((0,1),(2,3)),(Curve('Circle',(4,5)),))
b.commit(fixture);b.fit();b.set_tool('Select')
def click(point,mods=Qt.KeyboardModifier.NoModifier):QTest.mouseClick(b.view,Qt.MouseButton.LeftButton,mods,QPoint(*map(round,b.canvas.pixel(point))))
click((2,1));assert b.selected_segments==frozenset((0,)) and b.motion_role.isEnabled()
b.motion_role.setChecked(True)
assert len(b.drawing.guides)==1 and not b.drawing.motion_path
assert b.drawing.guides[0].nodes==(0,1) and len(b.drawing.segments)==3
click((.2,2));click((.5,3.2),Qt.KeyboardModifier.ShiftModifier)
assert b.selected_segments==frozenset((1,2)) and b.assign_button.isEnabled()
b.live.setChecked(True);frozen=w.audio.parameters[0]
QTest.mouseClick(b.assign_button,Qt.MouseButton.LeftButton);QTest.qWait(50);dialog=b.motion_dialog
assert dialog is not None and not dialog.follow.isChecked()
dialog.speed.setValue(1.);dialog.apply();dialog.reject()
assert b.drawing.nodes==fixture.nodes and len(b.drawing.attachments)==1
assert frozen.shapes[-1].custom.attachments==()
assert len(w.scene.settings.shapes)==2 # one assembly, no separate scene guide
shape=w.scene.shape;assert shape.motion is None and shape.uid==''
def points(t):return [p+c for p,c,r,e in builder_motion_parts(w.scene.shape,0,t)]
base=points(0);moved=points(1)
for a,c in zip(base,moved):np.testing.assert_allclose(c,a+[1,0],atol=1e-12)
snapshot=b.drawing;b.undo();assert not b.drawing.attachments
b.redo();assert b.drawing==snapshot
b.selected_segments=frozenset((1,2));b.refresh();b.show_motion();QTest.qWait(30);dialog=b.motion_dialog
dialog.follow.setChecked(True);dialog.angle.setValue(0);dialog.apply();dialog.reject()
for a,c in zip(base,points(0)):np.testing.assert_allclose(a,c,atol=1e-12)
assert b.drawing.attachments[0].follow
with tempfile.TemporaryDirectory() as folder:
    asset=Path(folder)/'assembly.shape.json';scene=Path(folder)/'assembly.scene.json'
    with patch.object(q.QFileDialog,'getSaveFileName',return_value=(str(asset),'')):b.save_shape()
    assert json.loads(asset.read_text())['version']==4
    w.scene.save(scene);loaded,_,_=read_scene(scene);assert loaded.shapes==w.scene.settings.shapes
    before=b.drawing
    with patch.object(q.QFileDialog,'getOpenFileName',return_value=(str(asset),'')):b.load_shape()
    assert b.drawing==before
# Per-path unmark/Undo does not erase the other geometry or lose the assignment.
b.selected=None;b.selected_edge=0;b.selected_segments=frozenset((0,));b.refresh()
b.motion_role.setChecked(False);assert not b.drawing.guides[0].enabled and b.drawing.attachments
assert b.drawing.nodes==fixture.nodes
b.undo();assert len(b.drawing.guides)==1 and len(b.drawing.attachments)==1
for width,height in ((1000,700),(1220,840)):
    w.resize(width,height);QTest.qWait(50);assert (w.width(),w.height())==(width,height)
    assert b.canvas.width()>400 and b.canvas.height()>250
b.fit();w.tick();b.canvas.update();QTest.qWait(150)
shot=w.grab()
if '--headless' not in sys.argv:
    image=b.view.grabWindow().convertToFormat(QImage.Format.Format_RGBA8888);assert not image.isNull()
    pixels=np.frombuffer(image.bits(),dtype=np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*4].reshape(image.height(),image.width(),4)
    assert np.count_nonzero(pixels[:,:,:3].max(axis=2)>180)>100
    image.save(str(q.HERE/'.user/drawing-motion-canvas.png'))
    painter=QPainter(shot);painter.drawImage(b.container.mapTo(w,QPoint(0,0)),image)
    painter.drawImage(b.preview.mapTo(w,QPoint(0,0)),b.preview.view.grabWindow());painter.end()
shot.save(str(q.HERE/'.user/drawing-motion.png'))
w.timer.stop();w.shutdown_audio();w.force_close=True;w.close();app.processEvents()
print('Builder motion passed: selected-path role, in-builder rigid group assignment, preserved offsets/no jump, live snapshots, follow, Undo/Redo, files and layout.')
