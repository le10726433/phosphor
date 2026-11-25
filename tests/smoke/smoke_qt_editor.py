"""Native macOS/Windows desktop integration check. Run separately from unit tests."""
import _path  # noqa: F401  (makes the phosphor package importable)
from pathlib import Path
import sys
HERE = Path(__file__).resolve().parents[2]
(HERE/'.user').mkdir(exist_ok=True)
from phosphor.editor import qt_app as q
from types import SimpleNamespace
from PySide6.QtTest import QTest
from PySide6.QtCore import QPoint,Qt,QUrl
from PySide6.QtWidgets import QApplication,QPushButton,QDialogButtonBox,QTabWidget,QLineEdit,QListWidget
from unittest.mock import patch
from phosphor.editor.phosphor_editor import PhosphorEditor
import tempfile,json,time,wave
import numpy as np
q.os.environ['QSG_RENDER_LOOP']='basic'
headless = '--headless' in sys.argv
if headless: q.os.environ['QT_QPA_PLATFORM']='offscreen'
q.configure_graphics()
app=QApplication([]);app.setQuitOnLastWindowClosed(False)
w=q.Window(SimpleNamespace(scene=None,ready_file=None,workspace='Sandbox'));w.show();QTest.qWait(1000)
# Open the manual through the Help menu (its own non-modal window, UI1) and exercise the reader's navigation.
manual_action = next(action for action in w.findChildren(q.QAction) if action.text() == 'Manual')
manual_action.trigger(); QTest.qWait(100)
assert w.manual_window.isVisible() and not w.manual_window.isModal() and w.manual_window.parent() is w
manual = w.manual_window.view
browser = manual.findChild(q.QTextBrowser, 'manual_browser')
assert 'phosphor user manual' in browser.toPlainText()
assert 'images/window-sandbox.png' in browser.toHtml()
image_url = browser.source().resolved(QUrl('images/window-sandbox.png'))
assert not browser.loadResource(browser.document().ResourceType.ImageResource, image_url).isNull()
browser.setSource(browser.source().resolved(QUrl('01-introduction.md#13-running-phosphor')))
assert 'name="13-running-phosphor"' in browser.toHtml()
buttons = {button.text(): button for button in manual.findChildren(QPushButton)}
assert buttons['Back'].isEnabled()
buttons['Back'].click()
assert browser.source().fileName() == 'README.md'
buttons['Forward'].click()
assert browser.source().fileName() == '01-introduction.md'
buttons['Contents'].click()
assert browser.source().fileName() == 'README.md'
search_field = manual.findChild(QLineEdit, 'manual_search')
results = manual.findChild(QListWidget, 'manual_search_results')
search_field.setFocus()
QTest.keyClicks(search_field, 'running phosphor')
assert results.count() > 0
assert 'Running phosphor'.lower() in results.item(0).text().lower()
results.setCurrentRow(0)
QTest.keyClick(results, Qt.Key.Key_Return)
assert browser.source().fileName() == '01-introduction.md'
assert browser.source().fragment() == '13-running-phosphor'
assert browser.extraSelections()
assert all(selection.cursor.selectedText().lower() == 'running phosphor'
           for selection in browser.extraSelections())
# A common query provides multiple matches for forward/backward wrapping.
search_field.clear()
QTest.keyClicks(search_field, 'phosphor')
assert len(browser.extraSelections()) > 1
QTest.keyClick(search_field, Qt.Key.Key_Return)
first_position = browser.textCursor().selectionStart()
QTest.keyClick(search_field, Qt.Key.Key_Return)
assert browser.textCursor().selectionStart() != first_position
QTest.keyClick(search_field, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
assert browser.textCursor().selectionStart() == first_position
search_field.clear()
assert not browser.extraSelections() and results.isHidden()
w.manual_window.close()
# The application bar opens one panel at a time; choosing the open panel again hides the side panel.
assert w.scene.settings.shapes==() and w.current_panel==q.PANEL_INPUT and w.sidebar.isVisible()
QTest.mouseClick(w.bar_buttons[q.PANEL_MIX],Qt.MouseButton.LeftButton)
assert w.current_panel==q.PANEL_MIX and w.bar_buttons[q.PANEL_MIX].isChecked() and not w.bar_buttons[q.PANEL_INPUT].isChecked()
QTest.mouseClick(w.bar_buttons[q.PANEL_MIX],Qt.MouseButton.LeftButton)
assert not w.sidebar.isVisible() and not any(w.bar_buttons[name].isChecked() for name in q.PANELS)
QTest.mouseClick(w.bar_buttons[q.PANEL_SCENE],Qt.MouseButton.LeftButton);assert w.sidebar.isVisible()
assert not w.scene_sections.isTabEnabled(1)  # Transform needs a selected object.
w.add_object('Tilting circle');assert w.scene_sections.isTabEnabled(1)
assert ('Software' if headless else q.graphics_api().name) in w.backend,w.backend
baseline=dict(w.preferences)
scene_before=w.scene.document()
samples_before=q.render(np.arange(256)/48000,w.output_settings())[2]
for palette in q.PALETTES:
    w.apply_preferences(dict(baseline,palette=palette))
    artwork=q.QPixmap(str(q.logo_path(palette)))
    assert not artwork.isNull()
    assert app.windowIcon().pixmap(64,64).toImage()==q.QIcon(artwork).pixmap(64,64).toImage()
    assert w.scene.document()==scene_before
    np.testing.assert_array_equal(q.render(np.arange(256)/48000,w.output_settings())[2],samples_before)
w.apply_preferences(baseline)
w.show_panel(q.PANEL_SCENE)
w.scene_sections.setCurrentIndex(1)
assert w.transform_target.currentIndex()==0 and w.transform_target.count()==1
b=next(b for b in w.findChildren(QPushButton) if b.text()=='Duplicate')
QTest.mouseClick(b,Qt.MouseButton.LeftButton);assert len(w.scene.settings.shapes)==2
assert w.transform_target.count()==2 and w.transform_target.currentIndex()==1
w.transform_target.activated.emit(0);assert w.scene.selected==0 and w.shape_list.currentRow()==0
w.transform_target.activated.emit(1);assert w.scene.selected==1
# The Transform tab is a stack: add a Rotate step and type an angle, then reset it by double-click.
w.transform_panel.add('Rotate');assert [t.kind for t in w.scene.shape.transforms]==['Rotate']
x=w.transform_panel.editors['Rotate'][1][0].slider;x.entry.setText('23.7');QTest.keyClick(x.entry,Qt.Key.Key_Return)
assert w.scene.shape.transforms[0].params[0].value==23.7,w.scene.shape
QTest.mouseDClick(x.slider,Qt.MouseButton.LeftButton);assert w.scene.shape.transforms[0].params[0].value==0
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
        combo=d.findChild(q.QComboBox,'palette');combo.setCurrentText('Cyan')
        assert w.appearance_editor.palette.currentText()=='Cyan'  # The side panel follows the preview.
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
        editor=d.findChild(PhosphorEditor,'phosphor_editor');decay=d.findChild(q.SliderInput,'glow_decay')
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
        # The Appearance panel applies and saves as you edit; Revert restores the values from when it opened.
        w.show_panel(q.PANEL_APPEARANCE);panel=w.appearance_editor
        assert panel.palette.currentText()==baseline['palette']
        panel.palette.setCurrentText('macOS Graphite');assert w.preferences['palette']=='macOS Graphite'
        panel.sliders['trace_thickness'].commit(3.);assert w.preferences['trace_thickness']==3.
        w.save_preferences();assert json.loads(prefs.read_text())['palette']=='macOS Graphite'
        assert w.scene.document()==original
        w.appearance_revert();w.save_preferences()
        assert w.preferences==baseline and panel.palette.currentText()==baseline['palette']
        assert json.loads(prefs.read_text())==baseline
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
    # exported files use the independently selected 48 kHz.
    playback_rate=w.audio.sample_rate;w.audio.sample_rate=96000
    if not w.audio.running:
        w.tick();assert w.output_settings().sample_rate==96000
        assert len(w.host.scope.signal)>=3840
        assert w.status.text().count('96 kHz stereo XY')==1,w.status.text()
    with patch.object(q.QFileDialog,'getSaveFileName',return_value=(str(target),'')),patch.object(w,'error') as error:
        w.export(format='WAV',depth=16,rate=48000,range='whole')
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
# Live MIDI: choosing the source lists devices at once, choosing a device connects, None disconnects,
# and coming back to Live MIDI reconnects to the scene's device. The scene shapes the sound: no synth preset.
assert 'midi_preset' not in w.parameters and w.parameters['note_decay'].currentText()=='Sustained'
def wait_for_ports(count):
    deadline=time.monotonic()+3
    while w.devices.count()!=count and time.monotonic()<deadline:QTest.qWait(20);time.sleep(.001);w.tick()
    assert w.devices.count()==count,[w.devices.itemText(i) for i in range(w.devices.count())]
with patch.object(w.audio.midi,'names',return_value=['Keys A','Keys B']),patch.object(w.audio.midi,'connect') as connect,patch.object(w.audio.midi,'disconnect') as disconnect:
    w.edit('source',q.MIDI_SOURCE);assert w.live_panel.isVisibleTo(w.sidebar) or w.current_panel!=q.PANEL_INPUT
    wait_for_ports(3);assert w.devices.itemText(0)==q.NO_MIDI_DEVICE and not connect.called
    w.devices.setCurrentIndex(2);w.devices.activated.emit(2)
    connect.assert_called_once_with('Keys B');assert w.scene.device=='Keys B'
    w.devices.setCurrentIndex(0);w.devices.activated.emit(0)
    assert disconnect.called and w.scene.device==''
    w.scene.device='Keys A';connect.reset_mock()
    w.edit('source',q.SCALE_NAME);w.devices.clear();w.edit('source',q.MIDI_SOURCE);wait_for_ports(3)
    connect.assert_called_once_with('Keys A');assert w.devices.currentText()=='Keys A'
    w.edit('note_decay','Plucked');assert w.output_settings().note_decay=='Plucked'
    w.edit('note_decay','Sustained');w.edit('source',q.SCALE_NAME);w.scene.device=''
# Scale: the end note list holds only notes of the scale above the start note, which is the fundamental.
w.show_panel(q.PANEL_INPUT);w.edit('scale_type','Major pentatonic')
assert w.scene.settings.scale_end==84 and all((w.scale_end_box.itemData(i)-48)%12 in (0,2,4,7,9) for i in range(w.scale_end_box.count()))
w.edit('scale_root',w.scale_root_box.itemData(w.scale_root_box.findText('A2')))
assert w.scene.settings.scale_root==45 and w.scene.settings.scale_end==81 and w.scale_end_box.currentText()=='A5'
w.edit('scale_type','Major');w.edit('scale_root',48);w.edit('scale_end',84)
# Travel speed: up to 128x; anything above, or typing "instant", is instant travel.
speed=w.parameters['travel_speed']
for typed,expected in (('128','128.000'),('500','Instant'),('64','64.000'),('inst','Instant')):
    speed.lineEdit().selectAll();QTest.keyClicks(speed.lineEdit(),typed);QTest.keyClick(speed.lineEdit(),Qt.Key.Key_Return)
    assert speed.text()==expected,(typed,speed.text())
assert w.scene.settings.travel_speed==q.INSTANT_TRAVEL;w.edit('travel_speed',64.)
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
assert not w.bar_buttons[q.PANEL_SCENE].isEnabled() and not w.bar_buttons[q.PANEL_MIX].isEnabled()
assert w.current_panel not in (q.PANEL_SCENE,q.PANEL_MIX)
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
assert not w.system_monitor and w.bar_buttons[q.PANEL_SCENE].isEnabled() and w.generated_panel.isEnabled()
assert w.scene.document()==before
w.apply_preferences(original_preferences)
w.timer.stop();w.shutdown_audio();w.force_close=True;w.close();app.processEvents()
print(('Offscreen Qt logic checks passed (GPU not verified):' if headless else 'Native Qt editor checks passed: GPU,')+' buttons, integer slider/reset, mouse move/resize, preferences Apply/Cancel, scene save/open/cancel, asynchronous WAV export, input-only system monitor bypass/restore.')
