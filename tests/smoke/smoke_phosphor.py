"""Check the native GPU phosphor layer: light appears, decays, and Off restores the line trace."""
import _path  # noqa: F401  (makes the phosphor package importable)
import sys
from types import SimpleNamespace
import numpy as np
from PySide6.QtGui import QImage,QSurfaceFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from phosphor.editor import qt_app as q
from phosphor.display.phosphor import PHOSPHOR_OFF

# Match qt_app.main: the real window is multisampled, and that changes off-screen rendering.
q.configure_graphics();surface=QSurfaceFormat();surface.setSamples(4);QSurfaceFormat.setDefaultFormat(surface);app=QApplication([])
w=q.Window(SimpleNamespace(scene=None,ready_file=None));w.resize(1000,700);w.show();QTest.qWait(300)
layer=w.host.phosphor;scope=w.host.scope
assert layer is not None,'Shaders failed to load'
base=dict(w.preferences,crt_scanlines=False,crt_glow=False,grid_divisions=2,palette='OLED white',glow_decay=0.,trace_interpolation='Off')


def lit():
    view=w.host.view;QTest.qWait(40)
    image=view.grabWindow().convertToFormat(QImage.Format.Format_RGBA8888)
    data=np.frombuffer(image.constBits(),np.uint8).reshape(image.height(),image.bytesPerLine()//4,4)[...,:3]
    return int(np.count_nonzero(data.max(axis=2)>80)),int(data.max())


w.apply_preferences(dict(base,phosphor='P31 green'));w.timer.stop();QTest.qWait(40)
assert w.host.scope.overlays is not None
phase=np.linspace(0,2*np.pi,1920)
circle=.7*np.column_stack((np.cos(phase),np.sin(phase)))
w.tick();scope.signal=circle
scope.overlays=[]
assert not scope.draw_trace and layer.root.isVisible()
for _ in range(12):
    scope.signal=circle
    w.update_phosphor(1920,.016);scope.update();QTest.qWait(20)
bright,peak=lit()
assert bright>800 and peak>150,(bright,peak)
for _ in range(50):
    w.update_phosphor(0,.016);scope.update();QTest.qWait(12)
dark,_=lit()
assert dark<bright*.05,(dark,bright)
w.apply_preferences(dict(base,phosphor='P31 green',beam_profile='Gaussian'));scope.overlays=[]
for _ in range(12):
    scope.signal=circle
    w.update_phosphor(1920,.016);scope.update();QTest.qWait(20)
soft,peak=lit()
assert soft>800 and peak>150,(soft,peak)
for _ in range(50):
    w.update_phosphor(0,.016);scope.update();QTest.qWait(12)
# Glow is bloom of the energy buffer: it lights a wider area, including past sharp corners.
w.apply_preferences(dict(base,phosphor='P31 green',beam_profile='Gaussian',crt_glow=True,glow_size=16.,glow_intensity=1.5));scope.overlays=[]
for _ in range(12):
    scope.signal=circle
    w.update_phosphor(1920,.016);scope.update();QTest.qWait(20)
glowing,peak=lit()
assert layer.root.property('bloomOn') and glowing>soft*1.5 and peak>150,(glowing,soft,peak)
for _ in range(50):
    w.update_phosphor(0,.016);scope.update();QTest.qWait(12)
w.apply_preferences(dict(base,phosphor=PHOSPHOR_OFF));w.tick();QTest.qWait(40)
assert scope.draw_trace and not layer.root.isVisible()
w.timer.start()
w.apply_preferences(dict(base,phosphor='P7 blue / long yellow'));QTest.qWait(300)
assert not scope.draw_trace
w.resize(1220,840);QTest.qWait(120);w.resize(1000,700);QTest.qWait(120)
w.timer.stop();w.shutdown_audio();w.force_close=True;w.close();app.processEvents()
print('Phosphor layer passed: native shaders, accumulation, decay, Gaussian beam, bloom glow, Off restore and resize.')
