"""Native/--headless group movement, marquee, clipboard and Shift constraints."""
import _path  # noqa: F401  (makes the phosphor package importable)
import os,sys,atexit
if '--headless' in sys.argv:os.environ['QT_QPA_PLATFORM']='offscreen'
from types import SimpleNamespace
import numpy as np
from PySide6.QtWidgets import QApplication,QMenu
from PySide6.QtCore import Qt,QPoint,QTimer,QMimeData
from PySide6.QtGui import QKeySequence,QImage,QPainter
from PySide6.QtTest import QTest
from phosphor.editor import qt_app as q
from phosphor.engine.custom_shape import Drawing,Curve
q.configure_graphics();app=QApplication([]);w=q.Window(SimpleNamespace(scene=None,ready_file=None));w.show();QTest.qWait(100)
old_clipboard=QMimeData();mime=app.clipboard().mimeData()
if mime is not None:
    for format in mime.formats():old_clipboard.setData(format,mime.data(format))
def restore_clipboard():
    # The offscreen plugin has an in-process clipboard and can crash during
    # teardown with a Python-owned MIME payload still installed. Native runs
    # restore the real user's clipboard; offscreen runs simply clear theirs.
    if '--headless' in sys.argv:app.clipboard().clear()
    else:app.clipboard().setMimeData(old_clipboard)
atexit.register(restore_clipboard)
w.open_builder();QTest.qWait(100);b=w.builder
fixture=Drawing('Groups',((0,-.7,.3),(1,0,.3),(2,.5,.3),(3,-.6,-.4),(4,.6,-.4)),((0,1),(1,2)),(Curve('Semicircle',(3,4),-1),))
b.commit(fixture);b.live.setChecked(True);b.set_tool('Select')
def pixel(p):return QPoint(*map(round,b.canvas.pixel(p)))
def click(p,mods=Qt.KeyboardModifier.NoModifier):QTest.mouseClick(b.view,Qt.MouseButton.LeftButton,mods,pixel(p))
def drag(a,c,mods=Qt.KeyboardModifier.NoModifier,before_release=None):
    QTest.mousePress(b.view,Qt.MouseButton.LeftButton,mods,pixel(a))
    QTest.mouseMove(b.view,pixel(c),30)
    # Qt's test mouseMove does not carry modifier flags; deliver the actual
    # modifier-aware position as well, exactly as the native canvas does.
    b.move(b.canvas.pixel(c),mods)
    if before_release:before_release()
    QTest.mouseRelease(b.view,Qt.MouseButton.LeftButton,mods,pixel(c))
frozen=w.audio.parameters[0];old=w.scene.shape.custom
def still_frozen():assert w.scene.shape.custom==old
drag((-.35,.3),(-.15,.45),before_release=still_frozen)
delta=np.array(b.drawing.nodes[0][1:])-fixture.nodes[0][1:]
for a,c in zip(fixture.nodes[:3],b.drawing.nodes[:3]):np.testing.assert_allclose(np.array(c[1:])-a[1:],delta)
assert b.drawing.nodes[3:]==fixture.nodes[3:] and frozen.shapes[-1].custom==fixture
b.undo();assert b.drawing==fixture
drag((-.35,.3),(-.1,.4),Qt.KeyboardModifier.ShiftModifier)
assert all(n[2]==.3 for n in b.drawing.nodes[:3]);b.undo()
# A box crossing a stroke selects its complete connected component.
drag((-.5,.2),(-.2,.4));assert b.selected_segments==frozenset((0,1))
# Shift-box adds the separate curved object.
drag((-.1,-1.1),(.1,-.8),Qt.KeyboardModifier.ShiftModifier)
assert b.selected_segments==frozenset((0,1,2))
original=b.drawing
QTest.keySequence(b.view,QKeySequence(QKeySequence.StandardKey.Copy))
assert b.clipboard_drawing()==original
QTest.keySequence(b.view,QKeySequence(QKeySequence.StandardKey.Paste))
assert len(b.drawing.segments)==6 and len(b.drawing.nodes)==10
assert len(b.selected_segments)==3
QTest.keySequence(b.view,QKeySequence(QKeySequence.StandardKey.Cut))
assert b.drawing==original
b.undo();assert len(b.drawing.segments)==6
b.undo();assert b.drawing==original
b.redo();assert len(b.drawing.segments)==6
b.undo()
# Context menu exposes clipboard actions and preserves an existing selection.
actions=[]
def inspect_menu():
    menu=b.context_popup
    actions.extend(a.text() for a in menu.actions());menu.close()
QTimer.singleShot(0,inspect_menu)
b.context_menu(b.canvas.pixel((-.35,.3)))
assert actions==['Copy','Cut','Paste']
# Node drags constrain independently without moving their neighbors.
drag((.5,.3),(.8,.5),Qt.KeyboardModifier.ShiftModifier)
assert b.drawing.nodes[2][2]==.3 and b.drawing.nodes[:2]==fixture.nodes[:2]
b.undo()
drag((.5,.3),(.6,.7),Qt.KeyboardModifier.ShiftModifier)
assert b.drawing.nodes[2][1]==.5
b.undo()
# Each creation tool uses the previous placed point as its constraint anchor.
for tool,points in (
    ('Line',((-.7,-.7),(.2,-.5))),('Polyline',((-.7,-.7),(.2,-.5),(.4,.3))),
    ('Arc',((-.7,-.7),(.2,-.5),(.4,.3))),('Semicircle',((-.7,-.7),(.2,-.5))),
    ('Spline',((-.7,-.7),(.2,-.5),(.4,.3)))):
    b.begin(Drawing(tool));b.set_tool(tool)
    for index,p in enumerate(points):click(p,Qt.KeyboardModifier.ShiftModifier if index else Qt.KeyboardModifier.NoModifier)
    if b.pending:QTest.keyClick(b.view,Qt.Key.Key_Return)
    assert b.drawing.playable,(tool,b.notice.text())
    nodes=b.drawing.nodes
    assert nodes[1][2]==nodes[0][2],tool
    if len(nodes)>2:assert nodes[2][1]==nodes[1][1],tool
# A nearby off-axis snap target cannot override the Shift constraint.
b.begin(Drawing('Snap',((0,0,0),(1,.8,.04),(2,-.5,-.5)),((0,2),)))
b.set_tool('Select');drag((0,0),(.79,.1),Qt.KeyboardModifier.ShiftModifier)
assert len(b.drawing.nodes)==3 and b.drawing.nodes[0][2]==0
b.begin(fixture);b.set_tool('Select')
b.press(b.canvas.pixel((-.9,-1.1)),Qt.KeyboardModifier.NoModifier)
b.move(b.canvas.pixel((.8,.5)),Qt.KeyboardModifier.NoModifier);b.refresh()
w.tick();QTest.qWait(100)
shot=w.grab()
if '--headless' not in sys.argv:
    image=b.view.grabWindow();assert not image.isNull()
    painter=QPainter(shot);painter.drawImage(b.container.mapTo(w,QPoint(0,0)),image);painter.end()
shot.save(str(q.HERE/'.user/builder-selection.png'))
restore_clipboard();atexit.unregister(restore_clipboard)
w.timer.stop();w.shutdown_audio();w.force_close=True;w.close();app.processEvents()
print('Builder selection passed: connected movement, Shift axes, box/additive selection, clipboard/context menu, Undo/Redo and constraints in every drawing tool.')
