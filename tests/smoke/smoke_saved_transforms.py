"""Saved transforms (T6) from the real menus: right-click a step › Save step… and Save stack…,
insert both from Add › Saved (a variable clash is renamed), undo, rename and delete; then insert
the stack into a Composer clip scene as one Undo step."""
import _path  # noqa: F401  (makes the phosphor package importable)
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from _screen import exit_cleanly_on_failure
from phosphor.editor import qt_app as q
from phosphor.editor.transform_panel import SAVED_OFF
from phosphor.engine.composition import COMPOSER
from phosphor.engine.transforms import ROTATE, SCALE, FIELDS

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


def shape(): return w.scene.shape


def trigger(menu, *path):
    """Trigger the action at path (texts) in menu, opening submenus as Qt would (aboutToShow)."""
    for i, text in enumerate(path):
        action = next((a for a in menu.actions() if a.text() == text), None)
        assert action is not None, (text, [a.text() for a in menu.actions()])
        if i == len(path) - 1:
            assert action.isEnabled(), text
            action.trigger(); settle(); return
        menu = action.menu(); menu.aboutToShow.emit()


def saved_menu():
    menu = panel().saved_menu; menu.aboutToShow.emit(); return menu


def answers(*texts):
    """Answer the next name dialogs with texts, in order."""
    queue = list(texts)
    panel().ask_text = lambda title, text_label, text='': (queue.pop(0), True)


def build_object():
    """A Cube with variable a, read by Rotate Z, and a Scale step."""
    w.add_object('Cube'); settle(); separate()
    w.scene_sections.setCurrentIndex(1); settle()
    panel().add_variable_button.click(); settle(); separate()
    panel().add(ROTATE); settle(); separate()
    z = panel().editors[ROTATE][1][[f.name for f in FIELDS[ROTATE]].index('z')]
    z.make_varying('a*10'); settle(); separate()
    panel().add(SCALE); settle(); separate()
    assert [t.kind for t in shape().transforms] == [ROTATE, SCALE] and [v.name for v in shape().transform_variables] == ['a']


def no_sideways_scroll():
    assert w.panels[q.PANEL_SCENE].horizontalScrollBar().maximum() == 0, 'SCENE scrolls sideways'


errors, warnings = [], []
with tempfile.TemporaryDirectory() as folder, patch.object(q.Window, 'remember_file', lambda *args: None), \
        patch('phosphor.editor.history.monotonic', side_effect=history_clock):
    presets = Path(folder) / 'transform-presets.json'
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
    w.error = lambda error: errors.append(str(error))  # Instance hooks: no modal boxes.
    w.resize(1000, 700); w.show(); w.activateWindow(); settle(100)
    w.show_panel(q.PANEL_SCENE); settle()
    assert [a.text() for a in saved_menu().actions()] == [SAVED_OFF]  # Off until user files are on.
    panel().set_presets_path(presets)
    panel().warn = lambda title, text: warnings.append(text)
    panel().confirm = lambda title, text: True
    build_object()

    # Right-click the Rotate step › Save step…; then Save stack….
    answers('Spin a'); trigger(panel().build_step_menu(0), 'Save step…')
    answers('Spin and scale'); trigger(panel().build_step_menu(1), 'Save stack…')
    saved = json.loads(presets.read_text(encoding='utf-8'))
    assert [p['name'] for p in saved['presets']] == ['Spin a', 'Spin and scale'], saved
    assert [v['name'] for v in saved['presets'][0]['preset']['variables']] == ['a']
    names = [a.text() for a in saved_menu().actions()]
    assert 'Spin a' in names and 'Spin and scale' in names and 'Rename…' in names and 'Delete…' in names, names
    separate()

    # Insert the step below the selection: its a clashes with the object's and becomes a_2.
    before = shape()
    panel().select(0); settle()
    trigger(saved_menu(), 'Spin a')
    assert [t.kind for t in shape().transforms] == [ROTATE, ROTATE, SCALE], shape().transforms
    assert shape().transforms[1].param('z').expression == 'a_2*10' and [v.name for v in shape().transform_variables] == ['a', 'a_2']
    assert 'renamed a_2' in panel().error.text() and label() == 'Undo Insert saved step', (panel().error.text(), label())
    assert len(panel().variable_rows) == 2 and panel().list.count() == 3
    separate()

    # Append the stack, then replace the stack with it; each is one Undo step.
    after_step = shape()
    trigger(saved_menu(), 'Spin and scale', 'Append to stack')
    assert [t.kind for t in shape().transforms] == [ROTATE, ROTATE, SCALE, ROTATE, SCALE]
    assert [v.name for v in shape().transform_variables] == ['a', 'a_2', 'a_3'] and label() == 'Undo Insert saved stack'
    separate()
    trigger(saved_menu(), 'Spin and scale', 'Replace stack')
    assert [t.kind for t in shape().transforms] == [ROTATE, SCALE] and label() == 'Undo Replace with saved stack'
    assert shape().transforms[0].param('z').expression == 'a_4*10', shape().transforms[0]
    no_sideways_scroll()
    w.undo(); settle(); assert len(shape().transforms) == 5
    w.undo(); settle(); assert shape() == after_step
    w.undo(); settle(); assert shape() == before and panel().list.count() == 2 and len(panel().variable_rows) == 1
    w.redo(); settle(); assert shape() == after_step
    separate()

    # Rename and delete from the same submenu.
    panel().ask_item = lambda title, text_label, items, current=0: ('Spin a', True)
    answers('Spin A'); trigger(saved_menu(), 'Rename…')
    assert list(panel().saved) == ['Spin A', 'Spin and scale'], list(panel().saved)
    panel().ask_item = lambda title, text_label, items, current=0: ('Spin and scale', True)
    trigger(saved_menu(), 'Delete…')
    assert list(panel().saved) == ['Spin A'] and shape() == after_step  # Scenes keep their copies.

    # Composer: insert the saved step into a clip scene as one Undo step.
    w.set_workspace(COMPOSER); settle(100)
    build_object()
    project = w.project
    panel().select(1); settle()
    trigger(saved_menu(), 'Spin A')
    clip = w.project.clips[w.clip_index].settings.shapes[0]
    assert [t.kind for t in clip.transforms] == [ROTATE, SCALE, ROTATE] and [v.name for v in clip.transform_variables] == ['a', 'a_2']
    assert label() == 'Undo Insert saved step', label()
    w.undo(); settle(); assert w.project == project
    no_sideways_scroll()
    assert not errors and not warnings, (errors, warnings)
    w.tick(); QTest.qWait(50)
    w.force_close = True; w.close()
print('saved transforms smoke OK')
