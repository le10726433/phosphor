"""Native macOS/Windows desktop integration check. Run separately from unit tests."""
import _path  # noqa: F401  (makes the phosphor package importable)
from pathlib import Path
import sys
HERE = Path(__file__).resolve().parents[2]
(HERE/'.user').mkdir(exist_ok=True)
from phosphor.editor import qt_app as q
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
        # Start from grid lines on, whatever the user's saved preferences say.
        w.apply_preferences(dict(w.preferences,show_grid=True))
        original=w.scene.document();baseline=dict(w.preferences)
        w.show_preferences();d=w.preferences_dialog
        combo=d.findChild(q.QComboBox);combo.setCurrentText('Cyan')
        assert w.preferences['palette']=='Cyan';assert w.scene.document()==original
        buttons=d.findChild(QDialogButtonBox)
        interpolation=d.findChild(q.QComboBox,'trace_interpolation')
        interpolation.setCurrentText('Cubic 4x')
        # Grid lines can be removed entirely, which greys out the division count.
        grid=d.findChild(q.QCheckBox,'show_grid');density=d.findChild(q.QSpinBox,'grid_divisions')
        assert grid.isChecked() and density.isEnabled()
        grid.setChecked(False);assert w.preferences['show_grid'] is False and not density.isEnabled()
        w.tick();assert w.host.scope.show_grid is False
        grid.setChecked(True);assert density.isEnabled()
        # Preferences > Phosphor intensity and the brightness slider under the scope are one value.
        intensity=d.findChild(q.SliderInput,'beam_intensity');intensity.commit(12.)
        assert w.preferences['beam_intensity']==12. and w.brightness_control.value==12.
        # Phosphor decay editor: presets grey out the line-trace glow decay, and an edit
        # copies the preset into Custom, which is applied live and saved with the rest.
        phosphor=d.findChild(q.QComboBox,'phosphor');phosphor.setCurrentText('P7 blue / long yellow')
        editor=d.findChild(q.PhosphorEditor,'phosphor_editor');decay=d.findChild(q.SliderInput,'glow_decay')
        assert editor.isEnabled() and not decay.isEnabled()
        afterglow=editor.fields['afterglow_time'][0];afterglow.setValue(1500.)
        assert phosphor.currentText()=='Custom' and w.preferences['phosphor']=='Custom'
        assert w.preferences['phosphor_custom']['afterglow_time']==1.5 and w.preferences['phosphor_custom']['afterglow_color']=='#d9d933'
        editor.set_color('flash_color','#ff0000');assert w.preferences['phosphor_custom']['flash_color']=='#ff0000'
        plot=editor.plot;handle=plot.handles()['flash'];start=handle.toPoint()
        QTest.mousePress(plot,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
        QTest.mouseMove(plot,start+QPoint(60,0));QTest.mouseRelease(plot,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start+QPoint(60,0))
        assert w.preferences['phosphor_custom']['flash_time']>.02,w.preferences['phosphor_custom']
        phosphor.setCurrentText('P31 green');assert editor.fields['afterglow_time'][0].value()==50.
        phosphor.setCurrentText('Custom');assert editor.fields['afterglow_time'][0].value()==1500.
        phosphor.setCurrentText(q.PHOSPHOR_OFF);assert decay.isEnabled() and not editor.isEnabled()
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
        assert json.loads(prefs.read_text())['phosphor_custom']['afterglow_time']==1.5
        thickness=d.findChild(q.SliderInput,'trace_thickness')
        QTest.mouseDClick(thickness.slider,Qt.MouseButton.LeftButton)
        assert w.preferences['trace_thickness']==1.
        interpolation.setCurrentText('Cubic 8x')
        combo.setCurrentText('Amber');d.reject();assert w.preferences['palette']=='Cyan'
        assert w.preferences['trace_interpolation']=='Cubic 4x'
        assert w.preferences['trace_thickness']==2.
        w.apply_preferences(baseline)
        # The brightness slider applies live and saves only its own value straight away.
        saved=json.loads(prefs.read_text())
        w.brightness_control.commit(40.);assert w.preferences['beam_intensity']==40.
        w.save_brightness();assert json.loads(prefs.read_text())==dict(saved,beam_intensity=40.)
        assert w.brightness_panel.isVisible() and w.brightness_control.isEnabled()
        w.apply_preferences(dict(w.preferences,phosphor=q.PHOSPHOR_OFF));assert not w.brightness_control.isEnabled()
        w.apply_preferences(baseline);assert w.brightness_control.value==baseline['beam_intensity']
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
    # Playback follows a 96 kHz output device; the preview still spans 40 ms and
    # exported files keep the scene's 48 kHz.
    playback_rate=w.audio.sample_rate;w.audio.sample_rate=96000
    if not w.audio.running:
        w.tick();assert w.output_settings().sample_rate==96000
        assert len(w.time_trace.signal)==3840 or not w.time_trace.isVisible()
        assert w.status.text().count('96 kHz stereo XY')==1,w.status.text()
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
    w.audio.sample_rate=playback_rate
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
