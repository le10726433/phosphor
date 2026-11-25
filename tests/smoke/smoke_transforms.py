"""SCENE › Transform: the per-object stack, time-varying fields, layout and files."""
import _path  # noqa: F401  (makes the phosphor package importable)
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt

from phosphor.editor import qt_app as q
from phosphor.engine.transforms import KINDS, ROTATE, MOTION_PATH, NOISE, TRANSLATE, MAP, Param

q.configure_graphics(); app = QApplication([])
w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox')); w.resize(1000, 700); w.show(); QTest.qWait(100)
w.show_panel(q.PANEL_SCENE); w.add_object('Cube'); w.scene_sections.setCurrentIndex(1); QTest.qWait(50)
panel = w.transform_panel
assert w.scene.shape.transforms == () and panel.empty.isVisible() and not panel.buttons['remove'].isEnabled()

# Every kind fits 1000×700 without sideways scroll while selected, and adds after the selected step.
for kind in KINDS:
    panel.add(kind); QTest.qWait(30)
    assert w.scene.shape.transforms[panel.current].kind == kind and panel.editors[kind][0].isVisible()
    assert w.panels[q.PANEL_SCENE].horizontalScrollBar().maximum() == 0, kind
assert [t.kind for t in w.scene.shape.transforms] == list(KINDS) and panel.list.count() == len(KINDS)
for _ in KINDS: panel.action('remove')
assert w.scene.shape.transforms == ()

# Static by default; Time varying swaps the slider for an expression and keeps the value.
panel.add(ROTATE); field = panel.editors[ROTATE][1][1]
field.slider.entry.setText('30'); QTest.keyClick(field.slider.entry, Qt.Key.Key_Return)
assert w.scene.shape.transforms[0].params[1].value == 30 and not field.varying and field.slider.isVisible()
field.make_varying(); QTest.qWait(20)
assert w.scene.shape.transforms[0].params[1].expression == '30' and field.expression.isVisible() and not field.slider.isVisible()
assert field.caption.text().startswith('ƒ') and panel.list.item(0).text().endswith('ƒ')
field.expression.setText('30 + 90*y*sin(t)'); QTest.keyClick(field.expression, Qt.Key.Key_Return)
assert w.scene.shape.transforms[0].params[1].expression == '30 + 90*y*sin(t)'
good = w.scene.shape
field.expression.setText('30 + bogus'); QTest.keyClick(field.expression, Qt.Key.Key_Return)
assert w.scene.shape == good and 'bogus' in panel.error.text()  # Errors keep the last good stack.
field.changed.emit(Param(30.))  # What right-click › Static sends.
assert w.scene.shape.transforms[0].params[1].expression == '' and field.slider.isVisible()  # Static again.

# Order, on/off and duplicates; Motion path presets.
panel.add(TRANSLATE); panel.action('up')
assert [t.kind for t in w.scene.shape.transforms] == [TRANSLATE, ROTATE] and panel.current == 0
panel.list.itemWidget(panel.list.item(1)).power.click(); assert not w.scene.shape.transforms[1].enabled
panel.list.itemWidget(panel.list.item(1)).power.click(); assert w.scene.shape.transforms[1].enabled
panel.list.setCurrentRow(1); panel.action('duplicate'); assert len(w.scene.shape.transforms) == 3
panel.action('remove'); panel.list.setCurrentRow(1)
panel.add(MOTION_PATH); assert panel.presets.isVisible()
panel.load_preset(panel.presets.findText('Bounce'))
motion = w.scene.shape.transforms[2]
assert motion.kind == MOTION_PATH and motion.params[0].expression == '' and 'abs' in motion.params[1].expression

# The scope moves along the path; noise reaches the output.
times = np.arange(48000) / 48000
moving = q.render(times, w.output_settings())[0]
frames = [moving[i:i+800].mean(axis=0) for i in (0, 24000)]
assert np.abs(frames[0] - frames[1]).max() > .01, frames
panel.add(NOISE); noisy = q.render(times, w.output_settings())[0]
assert 0 < np.abs(noisy - moving).max() < .1
panel.noise_colour.setCurrentText('Pink'); panel.colour_edited(panel.noise_colour.currentIndex())
pink = q.render(times, w.output_settings())[0]
assert np.isfinite(pink).all() and not np.array_equal(noisy, pink)
assert panel.noise_spectrum.isVisible()
panel.noise_rate.setCurrentText('Wobble')
wobble = q.render(times, w.output_settings())[0]
assert np.isfinite(wobble).all() and not np.array_equal(pink, wobble)
assert all(field.isVisible() for field in panel.editors[NOISE][1])
panel.noise_rate.setCurrentText('Sample')
np.testing.assert_array_equal(q.render(times, w.output_settings())[0], pink)
assert all(not field.isVisible() for field in panel.editors[NOISE][1][1:])

# Map: three formula fields (no slider), identity by default, examples, errors keep the last good stack.
before_map = q.render(times, w.output_settings())[0]
panel.add(MAP); QTest.qWait(20); fields = panel.editors[MAP][1]
assert panel.map_presets.isVisible() and not panel.presets.isVisible()
assert all(f.expression.isVisible() and f.slider is None for f in fields) and [f.expression.text() for f in fields] == ['x', 'y', 'z']
assert panel.list.item(panel.current).text().endswith('Map')  # Identity formulas: no ƒ mark.
np.testing.assert_array_equal(q.render(times, w.output_settings())[0], before_map)
fields[0].expression.setText('x + 0.2*sin(3*y + tau*t)'); QTest.keyClick(fields[0].expression, Qt.Key.Key_Return)
step = w.scene.shape.transforms[panel.current]
assert step.kind == MAP and step.params[0].expression == 'x + 0.2*sin(3*y + tau*t)' and panel.list.item(panel.current).text().endswith('ƒ')
assert not np.allclose(q.render(times, w.output_settings())[0], before_map)
good = w.scene.shape
fields[1].expression.setText('y + nope'); QTest.keyClick(fields[1].expression, Qt.Key.Key_Return)
assert w.scene.shape == good and 'nope' in panel.error.text()
fields[1].expression.setText(''); QTest.keyClick(fields[1].expression, Qt.Key.Key_Return)  # Clearing restores y.
assert fields[1].expression.text() == 'y' and w.scene.shape.transforms[panel.current].params[1].expression == 'y'
panel.load_map_preset(panel.map_presets.findText('Spherize')); QTest.qWait(20)
assert 'sqrt' in w.scene.shape.transforms[panel.current].params[2].expression and panel.map_presets.currentIndex() == 0
assert np.isfinite(q.render(times, w.output_settings())[0]).all()
assert w.panels[q.PANEL_SCENE].horizontalScrollBar().maximum() == 0

# Another object keeps its own stack.
w.add_object('Tilting circle'); assert w.scene.shape.transforms == () and panel.list.count() == 0
w.select_shape(0); assert panel.list.count() == 5

# Path window: Start and Trace in SCENE › Objects, static or time varying.
w.select_shape(0); w.scene_sections.setCurrentIndex(0); QTest.qWait(20)
trace = w.path_fields['path_trace']; assert w.path_panel.isVisible() and trace.slider.isVisible()
before = q.render(times, w.output_settings())[0]
trace.slider.entry.setText('-0.5'); QTest.keyClick(trace.slider.entry, Qt.Key.Key_Return)
assert w.scene.shape.path_trace == Param(-.5) and not np.allclose(q.render(times, w.output_settings())[0], before)
trace.make_varying(); QTest.qWait(20); trace = w.path_fields['path_trace']
trace.expression.setText('0.5 + 0.5*sin(t)'); QTest.keyClick(trace.expression, Qt.Key.Key_Return)
assert w.scene.shape.path_trace.expression == '0.5 + 0.5*sin(t)' and trace.caption.text().startswith('ƒ')
trace.expression.setText('y'); QTest.keyClick(trace.expression, Qt.Key.Key_Return)
assert w.scene.shape.path_trace.expression == '0.5 + 0.5*sin(t)' and w.path_error.isVisibleTo(w.sidebar)
w.path_fields['path_start'].slider.commit(.25); assert w.scene.shape.path_start == Param(.25)
assert w.panels[q.PANEL_SCENE].horizontalScrollBar().maximum() == 0

with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / 'transforms.scene.json'
    w.scene.save(path); stacks = [s.transforms for s in w.scene.settings.shapes]
    w.scene.load(path); w.sync()
    assert [s.transforms for s in w.scene.settings.shapes] == stacks and not w.scene.dirty
# p = beats at the Composer tempo, in the Sandbox too. (This edits the scene after it was saved,
# so the window is force-closed below: a normal close would wait on the unsaved-changes dialog.)
trace.expression.setText('0.5 + 0.25*sin(tau*p)'); QTest.keyClick(trace.expression, Qt.Key.Key_Return)
assert w.scene.shape.path_trace.expression == '0.5 + 0.25*sin(tau*p)', w.scene.shape.path_trace
for bpm in (120., 137.):
    w.project = replace(w.project, bpm=bpm); settings = w.output_settings()
    assert settings.tempo == bpm
    beats = replace(settings, shapes=tuple(replace(s, path_trace=Param(1., '0.5 + 0.25*sin(tau*p)')) for s in settings.shapes))
    # Grouped as p is computed (t*tempo/60, then tau*): tau*t*bpm/60 rounds differently in the
    # last bit, and Path Trace turns that into a jump where the trim lands on a vertex (macOS).
    seconds = replace(settings, shapes=tuple(replace(s, path_trace=Param(1., f'0.5 + 0.25*sin(tau*(t*{bpm}/60))')) for s in settings.shapes))
    np.testing.assert_allclose(q.render(times, beats)[0], q.render(times, seconds)[0], rtol=0, atol=1e-12)
w.tick(); QTest.qWait(50)
w.force_close = True; w.close()  # Releases the views without asking about the unsaved p edit.
print('Transform stack editor passed (including the tempo parameter p).')
