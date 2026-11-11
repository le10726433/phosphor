"""Native/--headless Circle, motion conversion, rigid assignment and restart."""
import _path  # noqa: F401  (makes the phosphor package importable)
import os,sys,time,tempfile
from dataclasses import replace
if '--headless' in sys.argv:os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import Qt,QPoint
from PySide6.QtGui import QImage,QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from phosphor.editor import qt_app as q
from phosphor.engine.custom_shape import Drawing,Curve
from phosphor.engine.signal_engine import posed_shapes,is_motion_guide,validate
from phosphor.storage.app_files import read_scene
from phosphor.editor.shape_builder import DRAW_WIDTH,BOX_WIDTH
q.configure_graphics();app=QApplication([]);w=q.Window(SimpleNamespace(scene=None,ready_file=None));w.show();QTest.qWait(100)
assert w.scene.shape.solid=='Tilting circle' and not (w.scene.shape.spin_x or w.scene.shape.spin_y or w.scene.shape.spin_z)
w.open_builder();QTest.qWait(100);b=w.builder
def click(point):QTest.mouseClick(b.view,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(*map(round,b.canvas.pixel(point))))
b.set_tool('Circle');click((0,0));click((.7,0))
assert len(b.drawing.curves)==1 and b.drawing.curves[0].kind=='Circle' and b.radius_panel.isVisible()
b.radius.setValue(.4);b.edit_radius()
positions=np.array([n[1:] for n in b.drawing.nodes]);assert abs(np.linalg.norm(positions[1]-positions[0])-.4)<1e-9
for width,height in ((1000,700),(1220,840)):
    w.resize(width,height);QTest.qWait(50);assert (w.width(),w.height())==(width,height),(w.size(),width,height)
assert BOX_WIDTH==DRAW_WIDTH/2
b.canvas.update();QTest.qWait(100)
if '--headless' not in sys.argv:
    image=b.view.grabWindow().convertToFormat(QImage.Format.Format_RGBA8888);assert not image.isNull()
    pixels=np.frombuffer(image.bits(),dtype=np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*4].reshape(image.height(),image.width(),4)
    ratio=np.array([image.width()/b.canvas.width(),image.height()/b.canvas.height()]);point=positions[0]+[0,.4]
    x,y=np.rint(b.canvas.pixel(point)*ratio).astype(int);r=int(np.ceil(3*ratio.max()))
    assert pixels[y-r:y+r+1,x-r:x+r+1,:3].max()>180,'Circle stroke missing'
    image.save(str(q.HERE/'.user/circle-canvas.png'))
# Retain coverage for scenes created with the old separate-guide data format.
b.begin(Drawing('Travel',((0,-.5,0),(1,.5,0)),((0,1),),motion_path=True))
b.apply();assert is_motion_guide(w.scene.shape) and w.scene.shape.uid
guide_id=w.scene.shape.uid;guide_index=w.scene.selected
assert w.leave_builder()
w.scene.add();w.scene.change('position_x',.4);w.sync();w.show_motion();QTest.qWait(30);d=w.motion_dialog
assert not d.follow.isChecked() and d.endpoint.currentText()=='Back and forth'
for i in range(d.objects.count()):d.objects.item(i).setCheckState(Qt.CheckState.Checked)
d.speed.setValue(.5);d.apply()
moving=posed_shapes(w.scene.settings.shapes,1.)
assert len(moving)==2 and abs(moving[1].position_x-moving[0].position_x-.4)<1e-9
assert abs((moving[0].position_x+moving[1].position_x)/2)<1e-9
assert all(s.motion.guide_id==guide_id for s in w.scene.settings.shapes if not is_motion_guide(s))
d.follow.setChecked(True);d.angle.setValue(90.);d.endpoint.setCurrentText('Stop at end');d.apply()
moving=posed_shapes(w.scene.settings.shapes,99)
assert abs(moving[1].position_y-moving[0].position_y-.4)<1e-9
assert abs(moving[0].rotation_z-90)<1e-9
d.reject()
# Stopped preview advances using wall time; Play resets the motion clock.
w.motion_epoch=time.perf_counter()-10;w.tick()
w.overlay(0,10);stopped=list(w.handles)
w.motion_epoch=time.perf_counter();w.tick();w.overlay(0,0)
assert not np.array_equal(stopped[0][1],w.handles[0][1])
with patch.object(w.audio,'start') as start:
    w.play();assert start.call_args.args[0].motion_time_offset==0.
    assert time.perf_counter()-w.motion_epoch<.5
with tempfile.TemporaryDirectory() as folder:
    p=Path(folder)/'motion.scene.json';w.scene.save(p);loaded,_,_=read_scene(p)
    assert loaded.shapes==w.scene.settings.shapes
# Convert guide back to drawable, then back to guide; assignments remain paused.
w.scene.selected=guide_index;w.sync();w.open_builder();QTest.qWait(50)
b.commit(replace(b.drawing,motion_path=False));b.apply();assert len(posed_shapes(w.scene.settings.shapes,1))==3
b.commit(replace(b.drawing,motion_path=True));b.apply();assert len(posed_shapes(w.scene.settings.shapes,1))==2
branch=Drawing('Branch',((0,0,0),(1,1,0),(2,0,1),(3,0,-1)),((0,1),(0,2),(0,3)))
b.begin(branch)
b.selected_segments=frozenset((0,1,2));b.refresh()
with patch.object(q.QMessageBox,'information') as prompt:
    b.motion_role.setChecked(True);assert prompt.called and 'branches' in str(prompt.call_args)
assert not b.motion_role.isChecked() and not b.drawing.motion_path
b.begin(w.scene.shape.custom);w.tick();QTest.qWait(100)
shot=w.grab()
if '--headless' not in sys.argv:
    painter=QPainter(shot);painter.drawImage(b.container.mapTo(w,QPoint(0,0)),b.view.grabWindow())
    painter.drawImage(b.preview.mapTo(w,QPoint(0,0)),b.preview.view.grabWindow());painter.end()
shot.save(str(q.HERE/'.user/motion-builder.png'))
w.timer.stop();w.shutdown_audio();w.force_close=True;w.close();app.processEvents()
print('Circle/motion passed: default, radius, conversion/branch prompt, group assignment, velocity, follow/offset, stop/restart, continuous preview, files and layout.')
