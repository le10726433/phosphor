"""Native/--headless curve tools, selection, gestures, files and output snapshots."""
import _path  # noqa: F401  (makes the phosphor package importable)
import os,sys,tempfile,json
if '--headless' in sys.argv:os.environ['QT_QPA_PLATFORM']='offscreen'
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
from PySide6.QtGui import QImage,QPainter
from phosphor.editor import qt_app as q
from phosphor.engine.custom_shape import paths
q.configure_graphics();app=QApplication([])
w=q.Window(SimpleNamespace(scene=None,ready_file=None));w.show();QTest.qWait(100);w.open_builder();QTest.qWait(100)
b=w.builder
for width,height in ((1000,700),(1220,840)):
    w.resize(width,height);QTest.qWait(50)
    assert (w.width(),w.height())==(width,height),(w.size(),width,height)
    for button in b.tools.values():assert button.isVisible() and button.width()>=button.sizeHint().width()
    assert b.canvas.height()>250 and b.canvas.width()>400
def click(point):
    p=b.canvas.pixel(point);QTest.mouseClick(b.view,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(*map(round,p)))
before=w.scene.document();b.live.setChecked(True);b.set_tool('Arc')
click((-.8,0));click((0,.7));assert len(b.pending)==2 and w.scene.document()==before
click((.8,0));assert not b.pending and b.drawing.curves[0].kind=='Arc'
assert w.scene.shape.custom==b.drawing
arc=b.drawing;frozen=w.audio.parameters[0]
b.set_tool('Semicircle');click((.8,0));click((-.8,0))
assert len(b.drawing.nodes)==3 # endpoints share the original arc nodes
assert b.flip_button.isEnabled()
semi=b.drawing;b.flip();assert b.drawing.curves[-1].side==-1 and w.scene.shape.custom==b.drawing
b.undo();assert b.drawing==semi
b.redo();assert b.drawing.curves[-1].side==-1
b.undo()
b.set_tool('Spline')
for point in ((-.8,0),(-.5,-.5),(0,-.3),(.5,-.7),(.8,0)):click(point)
assert len(b.pending)==5 and b.drawing==semi
QTest.keyClick(b.view,Qt.Key.Key_Return)
assert len(b.drawing.curves)==3 and b.drawing.curves[-1].kind=='Spline'
assert len(b.drawing.nodes)==6 and w.scene.shape.custom==b.drawing
saved=b.drawing
b.undo();assert b.drawing==semi
b.redo();assert b.drawing==saved
assert frozen.shapes[-1].custom==arc
# Picking follows the curved stroke rather than the straight control polygon.
b.set_tool('Select');path=paths(b.drawing)[-1]
nodes=np.array([n[1:] for n in b.drawing.nodes])
distances=np.linalg.norm((path[:,None]-nodes)*b.canvas.scale,axis=2).min(axis=1)
click(path[int(np.argmax(distances))]);assert b.selected_edge==2
b.delete_selection();assert not b.drawing.curves # selected connected shape is removed together
b.undo();assert b.drawing==saved
# Drag the arc's through point; held snapshots stay unchanged until release.
b.set_tool('Select');position=b.drawing.nodes[1][1:];p=b.canvas.pixel(position);end=b.canvas.pixel((0,.55))
original=w.scene.shape.custom
QTest.mousePress(b.view,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(*map(round,p)))
QTest.mouseMove(b.view,QPoint(*map(round,end)),30);assert w.scene.shape.custom==original
QTest.mouseRelease(b.view,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(*map(round,end)))
assert w.scene.shape.custom!=original and b.drawing.curves[0].kind=='Arc'
b.undo();assert b.drawing==saved
# A collinear arc is rejected, preserving the last completed scene and retry path.
b.set_tool('Arc');click((-.5,.9));click((0,.9));click((.5,.9))
assert len(b.pending)==2 and b.drawing==saved and 'non-collinear' in b.notice.text()
QTest.keyClick(b.view,Qt.Key.Key_Escape);assert not b.pending
with tempfile.TemporaryDirectory() as folder:
    asset=Path(folder)/'curves.shape.json'
    with patch.object(q.QFileDialog,'getSaveFileName',return_value=(str(asset),'')):b.save_shape()
    value=json.loads(asset.read_text());assert value['version']==2
    assert len(value['drawing']['nodes'])==6 and len(value['drawing']['curves'])==3
    b.undo()
    with patch.object(q.QFileDialog,'getOpenFileName',return_value=(str(asset),'')):b.load_shape()
    assert b.drawing==saved
for palette in q.PALETTES:w.apply_preferences(dict(w.preferences,palette=palette));w.tick()
b.fit();b.canvas.update();QTest.qWait(150)
if '--headless' not in sys.argv:
    image=b.view.grabWindow().convertToFormat(QImage.Format.Format_RGBA8888)
    assert not image.isNull()
    pixels=np.frombuffer(image.bits(),dtype=np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*4].reshape(image.height(),image.width(),4)
    assert np.count_nonzero(pixels[:,:,:3].max(axis=2)>150)>100
    # Check interiors away from node markers; a grid/node-only image must fail.
    ratio=np.array([image.width()/b.canvas.width(),image.height()/b.canvas.height()])
    for path in paths(b.drawing):
        checked=0
        for index in np.linspace(1,len(path)-2,12,dtype=int):
            point=path[index]
            if np.min(np.linalg.norm((point-nodes)*b.canvas.scale,axis=1))<12:continue
            x,y=np.rint(b.canvas.pixel(point)*ratio).astype(int);r=max(2,int(np.ceil(3*ratio.max())))
            region=pixels[max(0,y-r):y+r+1,max(0,x-r):x+r+1,:3]
            assert region.size and region.max()>180,'Native curve stroke missing'
            checked+=1
        assert checked>=3,'Not enough interior curve samples checked'
    image.save(str(q.HERE/'.user/curve-builder-canvas.png'))
    shot=w.grab();painter=QPainter(shot);painter.drawImage(b.container.mapTo(w,QPoint(0,0)),image)
    painter.drawImage(b.preview.mapTo(w,QPoint(0,0)),b.preview.view.grabWindow());painter.end()
else:shot=w.grab()
shot.save(str(q.HERE/'.user/curve-builder.png'))
assert w.leave_builder()
w.timer.stop();w.shutdown_audio();w.force_close=True;w.close();app.processEvents()
print('Curve builder passed: arc/semicircle/spline, snap/join, flip, curved picking, drag snapshots, Undo/Redo, invalid arc retry, files, palettes and layout.')
