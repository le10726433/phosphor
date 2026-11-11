"""Native or --headless fullscreen, signal calibration and time-view integration."""
import _path  # noqa: F401  (makes the phosphor package importable)
import os,sys,time,tempfile
if '--headless' in sys.argv:os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from PySide6.QtWidgets import QApplication,QDialogButtonBox
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from phosphor.editor import qt_app as q
q.configure_graphics();app=QApplication([])
def settle_view_mode():
    # Cocoa fullscreen transitions animate asynchronously; a second synthetic
    # Escape during that animation can be consumed by the native transition.
    QTest.qWait(1000 if sys.platform=='darwin' and '--headless' not in sys.argv else 30)

w=q.Window(SimpleNamespace(scene=None,ready_file=None));w.show();QTest.qWait(100)
w.apply_signal_preferences(dict(corner_dwell_us=0.))
before=w.scene.document()
w.set_fullscreen(True);settle_view_mode()
assert w.isFullScreen() and w.sidebar.isVisible()
w.set_immersive(True);settle_view_mode();w.tick()
assert w.isFullScreen() and w.immersive and not w.sidebar.isVisible()
assert not w.status.isVisible() and not w.viewer_heading.isVisible()
assert not w.host.scope.overlays and not w.handles
assert w.stage.fill_screen and w.host.geometry()==w.stage.rect()
assert w.stage.width()==w.centralWidget().width()
assert w.stage.height()==w.centralWidget().height()
# Read back the actual native GPU output: black OLED pixels and round white trace.
if '--headless' not in sys.argv:
    baseline=dict(w.preferences)
    # The line trace draws the white trace directly; a saved phosphor would tint or accumulate it.
    w.apply_preferences(dict(baseline,palette='OLED white',crt_scanlines=False,crt_glow=False,phosphor='Off (line trace)'))
    w.timer.stop();w.tick()
    phase=np.linspace(0,2*np.pi,1025)
    w.host.scope.signal=.6*np.column_stack((np.cos(phase),np.sin(phase)))
    w.host.scope.update();QTest.qWait(100)
    image=w.host.view.grabWindow().convertToFormat(QImage.Format.Format_RGBA8888)
    assert not image.isNull()
    pixels=np.frombuffer(image.bits(),dtype=np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*4].reshape(image.height(),image.width(),4)
    np.testing.assert_array_equal(pixels[0,0,:3],[0,0,0])
    rows,cols=np.where(np.min(pixels[:,:,:3],axis=2)>180)
    assert len(rows)>100
    assert abs(np.ptp(rows)-np.ptp(cols))<=2, 'Immersive circle stretched'
    assert cols.min()>image.width()*.05 and rows.min()>image.height()*.05
    image.save(str(q.HERE/'.user/oled-immersive.png'))
    w.apply_preferences(baseline);w.timer.start()
QTest.keyClick(w.host.view,Qt.Key.Key_Escape);settle_view_mode()
assert not w.immersive and w.isFullScreen() and w.sidebar.isVisible()
assert not w.stage.fill_screen and w.host.width()==w.host.height()
QTest.keyClick(w.host.view,Qt.Key.Key_Escape);settle_view_mode()
assert not w.isFullScreen()
w.set_immersive(True);settle_view_mode();w.exit_view_modes();settle_view_mode()
assert not w.isFullScreen() and not w.immersive
assert w.scene.document()==before
with tempfile.TemporaryDirectory(dir=q.HERE/'.user') as folder:
    prefs=Path(folder)/'signal.json'
    with patch.object(q,'SIGNAL_PREFERENCES_PATH',prefs):
        w.show_signal_preferences();d=w.signal_dialog
        slider=d.findChild(q.SliderInput,'corner_dwell_us');slider.commit(80.)
        assert w.output_settings().corner_dwell_us==80 and not w.scene.dirty
        buttons=d.findChild(QDialogButtonBox)
        QTest.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Apply),Qt.MouseButton.LeftButton)
        assert q.read_signal_preferences(prefs)['corner_dwell_us']==80
        slider.commit(140.);d.reject()
        assert w.output_settings().corner_dwell_us==80
        assert w.scene.document()==before
    seen=[]
    def export(path,settings,duration,source_only=False):
        seen.append(settings.corner_dwell_us);Path(path).write_bytes(b'export probe')
    target=Path(folder)/'test.wav'
    with patch.object(q,'export_wav',side_effect=export),patch.object(q.QFileDialog,'getSaveFileName',return_value=(str(target),'')):
        w.export();deadline=time.monotonic()+2
        while w.exporting and time.monotonic()<deadline:QTest.qWait(10);time.sleep(.001)
        assert not w.exporting and seen==[80.] and target.exists()
w.resize(1000,700);w.splitter.setSizes([340,640]);w.tabs.setCurrentIndex(3);QTest.qWait(30);w.tick()
assert w.time_trace.height()>=260 and w.time_trace.width()>200
# The time plot stays raw; the viewport may add points but keeps every original sample.
display_factor=q.INTERPOLATION_FACTORS[w.preferences['trace_interpolation']]
assert len(w.host.scope.signal)==(len(w.time_trace.signal)-1)*display_factor+1 or display_factor==1
np.testing.assert_allclose(w.time_trace.signal,w.host.scope.signal[::display_factor],atol=1e-6)
w.monitor.setCurrentIndex(1);w.monitor_changed(1);w.tabs.setCurrentIndex(3)
assert w.tabs.isTabEnabled(3)
raw=np.column_stack((.6*np.sin(np.arange(1920)*.04),.3*np.cos(np.arange(1920)*.07))).astype(np.float32)
w.visualization_gain=4.
with patch.object(w.capture,'samples',return_value=raw),patch.object(q,'render',side_effect=AssertionError('Capture must bypass shaping')):
    w.tick()
    np.testing.assert_array_equal(w.time_trace.signal,raw)
    factor=q.INTERPOLATION_FACTORS[w.preferences['trace_interpolation']]
    np.testing.assert_array_equal(w.host.scope.signal[::factor],raw*4)
w.time_trace.frozen=True
with patch.object(w.capture,'samples',return_value=raw*.5):w.tick()
np.testing.assert_array_equal(w.time_trace.signal,raw)
w.time_trace.frozen=False
w.capture.sample_rate=44100
with patch.object(w.capture,'samples',return_value=raw[:1764]):w.tick()
assert w.time_trace.sample_rate==44100 and len(w.time_trace.signal)==1764
w.capture.sample_rate=48000
with patch.object(w.capture,'samples',return_value=raw):
    w.resize(1220,840);QTest.qWait(30);w.grab().save(str(q.HERE/'.user/time-traces.png'))
w.timer.stop();w.shutdown_audio();w.force_close=True;w.close();app.processEvents()
print('Fullscreen/Escape, immersive restoration, signal preferences/export and raw time traces passed.')
