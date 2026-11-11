"""Native check of the system-audio X(t)/Y(t) sweep, fed with synthetic capture samples.

No capture helper runs: samples go straight into SystemAudio.accept_samples.
"""
import _path  # noqa: F401  (makes the phosphor package importable)
import sys
from types import SimpleNamespace
import numpy as np
from PySide6.QtGui import QImage, QSurfaceFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea
from phosphor.editor import qt_app as q

q.configure_graphics();surface=QSurfaceFormat();surface.setSamples(4);QSurfaceFormat.setDefaultFormat(surface);app=QApplication([])
w=q.Window(SimpleNamespace(scene=None,ready_file=None));w.resize(1000,700);w.show();QTest.qWait(300)
w.apply_preferences(dict(w.preferences,phosphor='P31 green',crt_scanlines=False,grid_divisions=2,palette='OLED white'))
w.monitor.setCurrentIndex(1);w.monitor_changed(1)
assert not w.sweep_panel.isVisible() and w.viewer_heading.text()=='X–Y SIGNAL MONITOR'
w.sweep_display.setCurrentIndex(1);QTest.qWait(50)
assert w.sweep_mode and w.sweep_panel.isVisible() and w.viewer_heading.text()=='X(t), Y(t) SWEEP'
assert not any(a.verticalScrollBar().isVisible() for a in w.findChildren(QScrollArea) if a.isVisible()),'Input tab scrolls at 1000x700'
# Trigger: X/Y/Both and edge. Both explains that the traces stop sharing a time base.
assert w.sweep_trigger_control.currentText()=='X' and w.sweep_edge_control.currentText()=='Rising' and not w.sweep_both_note.isVisible()
w.sweep_trigger_control.setCurrentText('Both');QTest.qWait(50)
assert w.sweep_trigger=='Both' and w.sweep_both_note.isVisible()
assert not any(a.verticalScrollBar().isVisible() for a in w.findChildren(QScrollArea) if a.isVisible()),'Input tab scrolls with the Both note'
w.sweep_edge_control.setCurrentText('Falling');assert w.sweep_edge=='Falling'
w.sweep_trigger_control.setCurrentText('X');w.sweep_edge_control.setCurrentText('Rising')
w.sweep_window_control.commit(10.);w.sweep_separation_control.commit(.5)
c=w.capture;c.sample_rate=96000;c.device_name='synthetic';c.ready=True;w.timer.stop()
state=dict(t=0)
def feed(frames=20):
    for _ in range(frames):
        t=(state['t']+np.arange(1600))/96000;state['t']+=1600
        c.accept_samples(np.column_stack((.3*np.sin(2*np.pi*200*t),.3*np.sin(2*np.pi*300*t))).astype(np.float32))
        w.tick();QTest.qWait(16)
def lit_rows():
    image=w.host.view.grabWindow().convertToFormat(QImage.Format.Format_RGBA8888)
    data=np.frombuffer(image.constBits(),np.uint8).reshape(image.height(),image.bytesPerLine()//4,4)[...,:3]
    rows=np.flatnonzero((data.max(axis=2)>120).sum(axis=1)>20)
    return rows,image.height()
feed()
assert w.sweep.length==960 and w.host.scope.polylines is None
# With capture diagnostics showing and the Both note, the Input tab still fits.
w.sweep_trigger_control.setCurrentText('Both');feed(2);QTest.qWait(50)
assert w.capture_status.text() and not any(a.verticalScrollBar().isVisible() for a in w.findChildren(QScrollArea) if a.isVisible()),'Input tab scrolls while capturing'
w.sweep_trigger_control.setCurrentText('X');feed(20)
rows,height=lit_rows()
# Separation 0.5 puts X around 0.5 above centre and Y 0.5 below: two bands, none at centre.
# Amplitude 0.3 makes each band span separation +/- 0.3. Lit rows bunch at a sine's peaks
# and troughs, so a median would flip between the two clusters; compare the band edges.
scale=.45*min(w.host.scope.width(),w.host.scope.height())*height/w.host.scope.height()
upper,lower=rows[rows<height/2],rows[rows>height/2]
assert len(upper) and len(lower),rows
edges=[(band.min(),height/2+top*scale,band.max(),height/2+bottom*scale) for band,top,bottom in ((upper,-.8,-.2),(lower,.2,.8))]
assert all(abs(low-low_expected)<.15*scale and abs(high-high_expected)<.15*scale for low,low_expected,high,high_expected in edges),edges
assert not np.any(abs(rows-height/2)<.1*scale),'traces should leave the centre line'
# Line trace: the whole sweep buffer is drawn as two polylines.
w.apply_preferences(dict(w.preferences,phosphor=q.PHOSPHOR_OFF));feed(6)
polylines=w.host.scope.polylines
assert polylines is not None and len(polylines)==2 and len(polylines[0])==960
w.sweep_window_control.commit(2000.);feed(3)
assert w.sweep.length==192000 and all(len(p)<=2*int(w.host.scope.width()) for p in w.host.scope.polylines)
w.sweep_window_control.commit(10.);w.sweep_trigger_control.setCurrentText('Both');feed(4)
assert len(w.sweep.sweeps)==2 and len(w.host.scope.polylines)==2
w.sweep_display.setCurrentIndex(0);feed(2)
assert w.host.scope.polylines is None and w.viewer_heading.text()=='X–Y SIGNAL MONITOR'
w.shutdown_audio();w.force_close=True;w.close();app.processEvents()
print('Time sweep passed: controls/layout, X/Y/Both trigger and edge, triggered phosphor traces with separation, line-trace buffer and decimation, X–Y restore.')
