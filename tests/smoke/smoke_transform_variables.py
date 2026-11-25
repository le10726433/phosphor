"""Variables in transforms (T5): Transform › Variables from the real controls. Add a variable,
use it in a step and in Path Start, drag its slider, rename it, refuse clashing names, undo;
then the same in a Composer clip scene with an automated variable that survives Replace."""
import _path  # noqa: F401  (makes the phosphor package importable)
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from _screen import exit_cleanly_on_failure
from phosphor.editor import qt_app as q
from phosphor.engine.composition import COMPOSER
from phosphor.engine.function_plot import PARAMETRIC_OBJECT
from phosphor.engine.lane_access import blocks, get_points
from phosphor.engine.signal_engine import posed_object
from phosphor.engine.transforms import ROTATE, FIELDS

exit_cleanly_on_failure()
q.configure_graphics(); app = QApplication([])
clock = [0.]


def history_clock():
    clock[0] += .01
    return clock[0]


def settle(ms=20): QTest.qWait(ms)


def separate(): clock[0] += 1.; settle()


def label(): return w.undo_action.text()


def panel(): return w.transform_panel


def rows(): return panel().variable_rows


def variables(): return w.scene.shape.transform_variables


def type_into(field, text):
    field.setFocus(); field.setText(text); field.editingFinished.emit(); settle()


def z_field(): return panel().editors[ROTATE][1][[f.name for f in FIELDS[ROTATE]].index('z')]


def drag(slider, positions):
    """A slider drag (setSliderDown), committed once per frame and as one Undo step."""
    slider.slider.setFocus(); settle()
    slider.slider.setSliderDown(True)
    for position in positions: slider.slider.setValue(position); QTest.qWait(25)
    slider.slider.setSliderDown(False); settle(50)


def no_sideways_scroll():
    assert w.panels[q.PANEL_SCENE].horizontalScrollBar().maximum() == 0, 'SCENE scrolls sideways'


def build_object(workspace):
    """An object with variable a used by Rotate Z and Path Start (steps shared by both workspaces)."""
    w.add_object('Cube'); settle(); separate()
    w.scene_sections.setCurrentIndex(1); settle()
    assert panel().add_variable_button.isVisible() and panel().add_variable_button.text() == '+ Variable'
    panel().add_variable_button.click(); settle()
    assert [v.name for v in variables()] == ['a'] and len(rows()) == 1
    if workspace == 'Sandbox': assert label() == 'Undo Add variable', label()
    separate()
    panel().add(ROTATE); settle(); separate()
    z_field().make_varying('a*10'); settle()
    assert w.scene.shape.transforms[0].param('z').expression == 'a*10', w.scene.shape.transforms[0]
    assert not panel().error.text(), panel().error.text()
    separate()
    w.scene_sections.setCurrentIndex(0); settle()
    w.path_fields['path_start'].make_varying('a/40'); settle()
    assert w.scene.shape.path_start.expression == 'a/40' and w.path_error.isHidden(), w.path_error.text()
    w.scene_sections.setCurrentIndex(1); settle(); separate()


errors = []
with tempfile.TemporaryDirectory() as folder, patch.object(q.Window, 'remember_file', lambda *args: None), \
        patch('phosphor.editor.history.monotonic', side_effect=history_clock):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
    w.error = lambda error: errors.append(str(error))  # Instance hook: no modal box.
    w.resize(1000, 700); w.show(); w.activateWindow(); settle(100)
    w.show_panel(q.PANEL_SCENE); settle()
    build_object('Sandbox')
    no_sideways_scroll()

    # Drag the slider: the step follows, and the drag is one Undo step.
    value = variables()[0].value
    posed = posed_object(0., w.scene.shape)[0]
    drag(rows()[0]['value'].slider, (2000, 4000, 6000, 8000))
    moved = variables()[0].value
    assert moved != value and label() == 'Undo Edit variable', (moved, label())
    assert w.output_settings().shapes[0].transform_variables[0].value == moved
    assert not np.allclose(posed_object(0., w.scene.shape)[0], posed), 'the step does not follow its variable'
    w.undo(); settle()
    assert variables()[0].value == value and rows()[0]['value'].slider.value == value
    w.redo(); settle(); assert variables()[0].value == moved
    separate()

    # Time varying (an expression of t) and back to Static.
    rows()[0]['value'].make_varying('2 + sin(t)'); settle()
    assert variables()[0].expression == '2 + sin(t)' and not rows()[0]['value'].slider.isVisible()
    type_into(rows()[0]['value'].expression, '')  # Clearing it means Static.
    assert variables()[0].expression == ''
    separate()

    # Rename: every expression that reads it follows, as one Undo step.
    type_into(rows()[0]['name'], 'speed')
    shape = w.scene.shape
    assert [v.name for v in shape.transform_variables] == ['speed'] and label() == 'Undo Rename variable', label()
    assert (shape.transforms[0].param('z').expression, shape.path_start.expression) == ('speed*10', 'speed/40')
    w.undo(); settle()
    assert w.scene.shape.transforms[0].param('z').expression == 'a*10' and rows()[0]['name'].text() == 'a'
    w.redo(); settle(); separate()

    # Bad names are refused and the panel says why.
    for name, why in (('x', 'point coordinate'), ('2b', 'letters, digits'), ('sin', 'reserved')):
        type_into(rows()[0]['name'], name)
        assert [v.name for v in variables()] == ['speed'] and why in panel().variable_error.text(), panel().variable_error.text()
        assert rows()[0]['name'].text() == 'speed'
    # A used variable cannot be removed.
    rows()[0]['name'].parentWidget().findChild(q.QPushButton, 'remove_transform_variable').click(); settle()
    assert variables() and 'speed is still used' in panel().variable_error.text(), panel().variable_error.text()
    separate()

    # Equation variables keep their names (user decision, November 21, 2026), both ways.
    w.add_object(PARAMETRIC_OBJECT); settle(); separate()
    w.function_panel.add_variable(); settle(); separate()
    assert [v.name for v in w.scene.shape.plot.variables] == ['a']
    w.scene_sections.setCurrentIndex(1); settle()
    panel().add_variable_button.click(); settle()
    assert [v.name for v in variables()] == ['b'], variables()  # + Variable skips the equation's a.
    type_into(rows()[0]['name'], 'a')
    assert [v.name for v in variables()] == ['b'] and 'Parametric equations' in panel().variable_error.text()
    w.scene_sections.setCurrentIndex(0); settle()
    type_into(w.function_panel.variable_rows[0]['name'], 'b')
    assert [v.name for v in w.scene.shape.plot.variables] == ['a'] and 'Transform › Variables' in w.function_panel.error.text()
    w.function_panel.add_variable(); settle()
    assert [v.name for v in w.scene.shape.plot.variables] == ['a', 'c']  # Skips the object's b.
    # A preset whose variable has an object variable's name: the preset's variable is renamed.
    w.scene_sections.setCurrentIndex(1); settle()
    type_into(rows()[0]['name'], 'k'); assert [v.name for v in variables()] == ['k']
    w.scene_sections.setCurrentIndex(0); settle()
    presets = w.function_panel.presets
    w.function_panel.load_preset(next(i for i in range(presets.count()) if presets.itemData(i) == ('builtin', 'Rose (k petals)'))); settle()
    plot = w.scene.shape.plot
    assert [v.name for v in plot.variables] == ['k_2'] and 'k_2' in plot.x, plot
    assert 'renamed k_2' in w.function_panel.error.text(), w.function_panel.error.text()
    assert [v.name for v in variables()] == ['k']
    no_sideways_scroll()
    separate()

    # Saved and reopened.
    scene_path = Path(folder) / 'variables.scene.json'
    w.sandbox_scene.save(scene_path)
    saved = w.sandbox_scene.settings.shapes
    notices = []
    w.sandbox_scene.load(scene_path, notices); w.reset_scene_history(); w.sync(); settle()
    assert w.sandbox_scene.settings.shapes == saved and not notices and not errors, (notices, errors)

    # Composer: the same in a clip scene, plus automation that survives Replace.
    w.set_workspace(COMPOSER); settle(100)
    build_object(COMPOSER)
    key = ('transform_variable', 0, 'a')
    field = rows()[0]['value']
    assert field.automation_allowed
    field.automate.emit(True); settle(50)  # Right-click › Automate.
    clip = w.project.clips[w.clip_index]
    points = get_points(clip, key)
    assert points and [b.title for b in blocks(clip)] == ['Cube › Variables'], [b.title for b in blocks(clip)]
    assert [b.key for b in w.composer_panel.timeline.lane_blocks()] == [('transform_variable', 0)]
    assert rows()[0]['value'].caption.text().startswith(q.AUTOMATED), rows()[0]['value'].caption.text()
    assert not clip.settings.shapes[0].lanes  # Never a Shape.lanes (parked) entry.
    separate()
    value = variables()[0].value
    drag(rows()[0]['value'].slider, (1000, 3000, 7000))
    assert variables()[0].value != value and get_points(w.project.clips[w.clip_index], key) == points
    w.undo(); settle(); assert variables()[0].value == value
    w.undo(); settle(); assert not get_points(w.project.clips[w.clip_index], key)  # Before Automate.
    w.redo(); settle(); assert get_points(w.project.clips[w.clip_index], key) == points
    separate()
    w.replace_objects([0], 'Tetrahedron'); settle()
    clip = w.project.clips[w.clip_index]
    shape = clip.settings.shapes[0]
    assert shape.solid == 'Tetrahedron' and [v.name for v in shape.transform_variables] == ['a'], shape
    assert get_points(clip, key) == points and label() == 'Undo Replace object', label()
    assert [b.title for b in blocks(clip)] == ['Tetrahedron › Variables'], [b.title for b in blocks(clip)]
    w.undo(); settle(); assert w.project.clips[w.clip_index].settings.shapes[0].solid == 'Cube'
    no_sideways_scroll()
    assert not errors, errors
    w.tick(); QTest.qWait(50)
    w.force_close = True; w.close()
print('transform variables smoke OK')
