"""Native macOS/Windows desktop integration check. Run separately from unit tests."""
from pathlib import Path
import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
(HERE/'.user').mkdir(exist_ok=True)
import qt_app as q
from types import SimpleNamespace
from PySide6.QtTest import QTest
from PySide6.QtCore import QPoint,Qt
from PySide6.QtWidgets import QApplication,QPushButton,QDialogButtonBox
from unittest.mock import patch
import tempfile,json,time,wave
import numpy as np
q.os.environ['QSG_RENDER_LOOP']='basic'
headless = '--headless' in sys.argv
if headless: q.os.environ['QT_QPA_PLATFORM']='offscreen'
q.configure_graphics()
app=QApplication([]);app.setQuitOnLastWindowClosed(False)
w=q.Window(SimpleNamespace(scene=None,ready_file=None));w.show();QTest.qWait(1000)
assert ('Software' if headless else q.graphics_api().name) in w.backend,w.backend
baseline=dict(w.preferences)
scene_before=w.scene.document()
samples_before=q.render(np.arange(256)/48000,w.output_settings())[2]
for palette in q.PALETTES:
    w.apply_preferences(dict(baseline,palette=palette))
    artwork=q.QPixmap(str(q.logo_path(palette)))
    assert not artwork.isNull()
    expected=artwork.scaled(64,64,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
    assert w.brand_logo.pixmap().toImage()==expected.toImage()
    assert app.windowIcon().pixmap(64,64).toImage()==q.QIcon(artwork).pixmap(64,64).toImage()
    assert w.scene.document()==scene_before
    np.testing.assert_array_equal(q.render(np.arange(256)/48000,w.output_settings())[2],samples_before)
w.apply_preferences(baseline)
w.tabs.setCurrentIndex(1)
w.scene_sections.setCurrentIndex(1)
b=next(b for b in w.findChildren(QPushButton) if b.text()=='Duplicate')
QTest.mouseClick(b,Qt.MouseButton.LeftButton);assert len(w.scene.settings.shapes)==2
x=w.parameters['rotation_x'];x.entry.setText('23.7');QTest.keyClick(x.entry,Qt.Key.Key_Return)
assert w.scene.shape.rotation_x==24,w.scene.shape
QTest.mouseDClick(x.slider,Qt.MouseButton.LeftButton);assert w.scene.shape.rotation_x==0
QTest.qWait(100)
center=w.handles[1][1]
QTest.mousePress(w.host.view,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(*map(round,center)))
QTest.mouseMove(w.host.view,QPoint(round(center[0]+90),round(center[1]-30)),100)
QTest.mouseRelease(w.host.view,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(round(center[0]+90),round(center[1]-30)))
assert w.scene.shape.position_x>.2,w.scene.shape
QTest.qWait(50)
corner=w.handles[1][2]
QTest.mousePress(w.host.view,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(*map(round,corner)))
QTest.mouseMove(w.host.view,QPoint(round(corner[0]+100),round(corner[1]-50)),100)
QTest.mouseRelease(w.host.view,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(round(corner[0]+100),round(corner[1]-50)))
assert w.scene.shape.shape_size>1,w.scene.shape
with tempfile.TemporaryDirectory(dir=HERE/'.user') as folder:
    prefs=Path(folder)/'prefs.json'; scene=Path(folder)/'test.scene.json'
    with patch.object(q,'PREFERENCES_PATH',prefs):
        original=w.scene.document();baseline=dict(w.preferences)
        w.show_preferences();d=w.preferences_dialog
        combo=d.findChild(q.QComboBox);combo.setCurrentText('Cyan')
        assert w.preferences['palette']=='Cyan';assert w.scene.document()==original
        buttons=d.findChild(QDialogButtonBox)
        interpolation=d.findChild(q.QComboBox,'trace_interpolation')
        interpolation.setCurrentText('Cubic 4x')
        visual_values=dict(trace_thickness=2.,glow_size=8.,glow_intensity=1.5,glow_decay=.6)
        for key,value in visual_values.items():
            slider=d.findChild(q.SliderInput,key)
            slider.entry.setText(str(value));QTest.keyClick(slider.entry,Qt.Key.Key_Return)
            assert w.preferences[key]==value
        assert w.scene.document()==original
        QTest.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Apply),Qt.MouseButton.LeftButton)
        assert json.loads(prefs.read_text())['palette']=='Cyan'
        assert json.loads(prefs.read_text())['trace_interpolation']=='Cubic 4x'
        for key,value in visual_values.items():assert json.loads(prefs.read_text())[key]==value
        thickness=d.findChild(q.SliderInput,'trace_thickness')
        QTest.mouseDClick(thickness.slider,Qt.MouseButton.LeftButton)
        assert w.preferences['trace_thickness']==1.
        interpolation.setCurrentText('Cubic 8x')
        combo.setCurrentText('Amber');d.reject();assert w.preferences['palette']=='Cyan'
        assert w.preferences['trace_interpolation']=='Cubic 4x'
        assert w.preferences['trace_thickness']==2.
        w.apply_preferences(baseline)
    with patch.object(q.QFileDialog,'getSaveFileName',return_value=(str(scene),'')):
        assert w.save_scene(True)
    assert not w.scene.dirty
    w.scene.change('position_x',5);w.sync()
    before=w.scene.document()
    with patch.object(q.QFileDialog,'getOpenFileName',return_value=(str(scene),'')),patch.object(q.QMessageBox,'question',return_value=q.QMessageBox.StandardButton.Cancel):
        w.open_scene()
    assert w.scene.document()==before
    with patch.object(q.QFileDialog,'getOpenFileName',return_value=(str(scene),'')),patch.object(q.QMessageBox,'question',return_value=q.QMessageBox.StandardButton.Discard):
        w.open_scene()
    assert not w.scene.dirty
    target=Path(folder)/'export.wav'
    with patch.object(q.QFileDialog,'getSaveFileName',return_value=(str(target),'')),patch.object(w,'error') as error:
        w.export()
        deadline=time.monotonic()+10
        while w.exporting and time.monotonic()<deadline:
            QTest.qWait(10)
            # qWait's native wait can starve the Python export worker. Yield the
            # GIL explicitly, retaining the same timeout and completion checks.
            time.sleep(.001)
        assert not w.exporting
        error.assert_not_called()
    with wave.open(str(target)) as wav:
        assert wav.getnchannels()==2 and wav.getframerate()==48000
        assert wav.getnframes()==round(q.source_duration(w.scene.settings)*48000)
        actual=np.frombuffer(wav.readframes(16),dtype='<i2').reshape(-1,2)
    # Export includes the user's hardware calibration, independently of scene data.
    expected=np.rint(np.clip(q.render(np.arange(16)/48000,w.output_settings())[2],-1,1)*32767).astype('<i2')
    np.testing.assert_array_equal(actual,expected)
    assert not list(Path(folder).glob('.export-*'))
if not q.SYSTEM_AUDIO_AVAILABLE:
    assert not w.monitor.model().item(1).isEnabled()
    w.monitor.setCurrentIndex(1);w.monitor_changed(1)
    assert not w.system_monitor and w.monitor.currentIndex()==0
    assert q.SYSTEM_AUDIO_NOTICE in w.status.text()
# Remaining checks inject capture data; they never open a platform capture stream.
q.SYSTEM_AUDIO_AVAILABLE=True
before=w.scene.document()
original_preferences=dict(w.preferences)
w.apply_preferences(dict(w.preferences,trace_interpolation='Off'))
with patch.object(w,'refresh_capture_devices'):
    w.monitor.setCurrentIndex(1);w.monitor_changed(1)
assert w.system_monitor and not w.scene.dirty
assert w.scene.document()==before
assert not w.generated_panel.isEnabled() and not w.generated_panel.isVisible()
assert not w.tabs.isTabEnabled(1) and not w.tabs.isTabEnabled(2)
raw=np.column_stack((.3*np.sin(np.arange(1920)*.07),.25*np.cos(np.arange(1920)*.071))).astype(np.float32)
with patch.object(w.capture,'samples',return_value=raw),patch.object(q,'render',side_effect=AssertionError('System monitor must bypass synthesis')),patch.object(w.audio,'start') as output:
    w.tick()
    np.testing.assert_array_equal(w.host.scope.signal,raw)
    original = raw.copy()
    for gain in (0.,.5,4.,32.):
        w.visualization_gain_control.entry.setText(str(gain))
        QTest.keyClick(w.visualization_gain_control.entry,Qt.Key.Key_Return)
        w.tick()
        np.testing.assert_array_equal(w.host.scope.signal,original*gain)
        np.testing.assert_array_equal(raw,original)
        assert w.scene.document()==before
    QTest.mouseDClick(w.visualization_gain_control.slider,Qt.MouseButton.LeftButton)
    assert w.visualization_gain==1.
    w.tick();np.testing.assert_array_equal(w.host.scope.signal,raw)
    for factor in (4,8):
        w.apply_preferences(dict(w.preferences,trace_interpolation=f'Cubic {factor}x'))
        w.tick();QTest.qWait(30)
        assert len(w.host.scope.signal)==(len(raw)-1)*factor+1
        np.testing.assert_array_equal(w.host.scope.signal[::factor],raw)
        np.testing.assert_array_equal(raw,original)
        assert w.scene.document()==before
    assert w.host.scope.overlays==[] and w.handles==[]
    output.assert_not_called()
with patch.object(q.QMessageBox,'information'),patch.object(q.QFileDialog,'getSaveFileName') as save:
    w.record();w.export();save.assert_not_called()
w.monitor.setCurrentIndex(0);w.monitor_changed(0)
assert not w.system_monitor and w.tabs.isTabEnabled(1) and w.generated_panel.isEnabled()
assert w.scene.document()==before
w.apply_preferences(original_preferences)
w.timer.stop();w.shutdown_audio();w.force_close=True;w.close();app.processEvents()
print(('Offscreen Qt logic checks passed (GPU not verified):' if headless else 'Native Qt editor checks passed: GPU,')+' buttons, integer slider/reset, mouse move/resize, preferences Apply/Cancel, scene save/open/cancel, asynchronous WAV export, input-only system monitor bypass/restore.')
