"""Native check of the X(t)/Y(t) time view: system audio fed with synthetic capture samples,
then the generated Sandbox signal, the stopped Composer song, and video (no time view).

No capture helper runs: samples go straight into SystemAudio.accept_samples. The view
controls live in Appearance > Monitor.
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
w=q.Window(SimpleNamespace(scene=None,ready_file=None,workspace='Sandbox'));w.resize(1000,700);w.show();QTest.qWait(300)
w.apply_preferences(dict(w.preferences,phosphor='P31 green',crt_scanlines=False,grid_divisions=2,palette='OLED white'))
w.monitor.setCurrentIndex(1);w.monitor_changed(1)
# The view controls moved from Input to Appearance > Monitor.
assert not w.capture_panel.isAncestorOf(w.sweep_display) and w.appearance_editor.tabs.indexOf(w.monitor_page)>=0
w.show_panel(q.PANEL_APPEARANCE);w.appearance_editor.tabs.setCurrentWidget(w.monitor_page);QTest.qWait(50)
assert not w.sweep_panel.isVisible() and w.viewer_heading.text()=='X–Y SIGNAL MONITOR'
w.sweep_display.setCurrentIndex(1);QTest.qWait(50)
assert w.sweep_mode and w.sweep_panel.isVisible() and w.viewer_heading.text()=='X(t), Y(t) TIME VIEW'
assert not any(a.horizontalScrollBar().maximum()>0 for a in w.findChildren(QScrollArea) if a.isVisible()),'Monitor tab scrolls sideways at 1000x700'
# Trigger: X/Y/Both and edge. Both explains that the traces stop sharing a time base.
assert w.sweep_trigger_control.currentText()=='X' and w.sweep_edge_control.currentText()=='Rising' and not w.sweep_both_note.isVisible()
w.sweep_trigger_control.setCurrentText('Both');QTest.qWait(50)
assert w.sweep_trigger=='Both' and w.sweep_both_note.isVisible()
assert not any(a.horizontalScrollBar().maximum()>0 for a in w.findChildren(QScrollArea) if a.isVisible()),'Monitor tab scrolls sideways with the Both note'
w.sweep_edge_control.setCurrentText('Falling');assert w.sweep_edge=='Falling'
w.sweep_trigger_control.setCurrentText('X');w.sweep_edge_control.setCurrentText('Rising')
w.sweep_window_control.commit(10.);w.sweep_separation_control.commit(.5)
c=w.capture;c.sample_rate=96000;c.device_name='synthetic';c.ready=True;w.timer.stop()
import time
state=dict(t=0,clock=None)
def feed(frames=20):
    # Feed what real capture would deliver since the last tick: display gains (0.52.0) use
    # wall-clock frame time, so a fixed 1600 per slower hand tick would dim the trace.
    for _ in range(frames):
        now=time.perf_counter();count=1600 if state['clock'] is None else max(1,round((now-state['clock'])*96000))
        state['clock']=now
        t=(state['t']+np.arange(count))/96000;state['t']+=count
        c.accept_samples(np.column_stack((.3*np.sin(2*np.pi*200*t),.3*np.sin(2*np.pi*300*t))).astype(np.float32))
        # The tick itself takes about 15 ms here; a short wait keeps frames near 1/60 s.
        w.tick();QTest.qWait(2)
def lit_rows():
    image=w.host.view.grabWindow().convertToFormat(QImage.Format.Format_RGBA8888)
    data=np.frombuffer(image.constBits(),np.uint8).reshape(image.height(),image.bytesPerLine()//4,4)[...,:3]
    rows=np.flatnonzero((data.max(axis=2)>120).sum(axis=1)>20)
    return rows,image.height()
feed()
assert w.sweep.length==960 and w.host.scope.polylines is None
# With capture diagnostics showing and the Both note, the Input tab still fits.
w.sweep_trigger_control.setCurrentText('Both');feed(2);QTest.qWait(50)
assert w.capture_status.text() and not any(a.horizontalScrollBar().maximum()>0 for a in w.findChildren(QScrollArea) if a.isVisible()),'Monitor tab scrolls sideways while capturing'
w.show_panel(q.PANEL_INPUT);QTest.qWait(50)
assert not any(a.horizontalScrollBar().maximum()>0 for a in w.findChildren(QScrollArea) if a.isVisible()),'Input tab scrolls sideways while capturing'
w.show_panel(q.PANEL_APPEARANCE)
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
# Generated input (stopped Sandbox): the time view gets a continuous rendered signal.
w.monitor.setCurrentIndex(0);w.monitor_changed(0);w.add_object('Cube');w.sweep_display.setCurrentIndex(1)
w.sweep_window_control.commit(10.);w.sweep_trigger_control.setCurrentText('X')
def ticks(n=12):
    for _ in range(n): w.tick();QTest.qWait(16)
ticks()
polylines=w.host.scope.polylines;rate=w.output_settings().sample_rate
assert polylines is not None and len(polylines)==2 and w.sweep.length==round(rate*.01),(w.sweep.length,rate)
assert w.sweep.sweeps[0][0].filled>0 and np.ptp(np.concatenate([line[:,1] for line in polylines]))>1,'generated time view is flat'
assert isinstance(w.sweep_feed,float) and w.viewer_heading.text()=='X(t), Y(t) TIME VIEW'
# Signal gain is display only and now applies to the generated signal too.
w.visualization_gain_control.commit(2.);ticks(2);gained=w.host.scope.signal.copy()
w.visualization_gain_control.commit(1.);ticks(1)
assert np.allclose(np.max(np.abs(gained)),2*np.max(np.abs(w.host.scope.signal)),rtol=.05)
# Stopped Composer: the window at the playhead repeats.
w.set_workspace(q.COMPOSER);w.add_object('Cube');ticks(4)
assert isinstance(w.sweep_feed,int) and w.host.scope.polylines is not None and w.sweep.sweeps[0][0].filled>0
w.set_workspace(q.SANDBOX)
# Video input: no time view (the raster has no beam samples).
w.monitor.setCurrentIndex(2);w.monitor_changed(2);ticks(2)
assert not w.sweep_display.isEnabled() and w.host.scope.polylines is None and w.viewer_heading.text()!='X(t), Y(t) TIME VIEW'
w.monitor.setCurrentIndex(0);w.monitor_changed(0)
assert w.sweep_display.isEnabled()
w.shutdown_audio();w.force_close=True;w.close();app.processEvents()
print('Time view passed: Monitor tab controls/layout, X/Y/Both trigger and edge, triggered phosphor traces with separation, line-trace buffer and decimation, X–Y restore, generated and Composer input, display gain, no video time view.')
