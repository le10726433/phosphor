"""Sandbox undo (U1): real shortcuts and controls round-trip through the scene history."""
import _path  # noqa: F401  (makes the phosphor package importable)
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from _screen import exit_cleanly_on_failure
from phosphor.editor import qt_app as q
from phosphor.engine.transforms import ROTATE, SPIN, MAP, NOISE

exit_cleanly_on_failure()
q.configure_graphics(); app = QApplication([])
CTRL = Qt.KeyboardModifier.ControlModifier  # Qt maps Ctrl to Command on macOS.
SHIFT = Qt.KeyboardModifier.ShiftModifier


# The history's merge window is 0.4 s of its clock. A controlled clock keeps a slow first
# frame from splitting a gesture: each read moves 10 ms, separate() moves a whole second.
clock = [0.]


def history_clock():
    clock[0] += .01
    return clock[0]


def settle(ms=20):
    """Let the 0 ms history timer commit."""
    QTest.qWait(ms)


def separate(): clock[0] += 1.; settle()


def keys(widget, redo=False):
    QTest.keyClick(widget, Qt.Key.Key_Z, CTRL | (SHIFT if redo else Qt.KeyboardModifier.NoModifier)); settle()


def label(): return w.undo_action.text()


with tempfile.TemporaryDirectory() as folder, patch.object(q.Window, 'remember_file', lambda *args: None), \
        patch('phosphor.editor.history.monotonic', side_effect=history_clock):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
    w.resize(1000, 700); w.show(); w.activateWindow(); QTest.qWait(100)
    w.show_panel(q.PANEL_SCENE); settle()
    assert not w.undo_action.isEnabled() and label() == 'Undo'

    # Add from the Add object menu, then undo and redo with the real keys.
    menu = w.add_button.menu()
    next(a for a in menu.actions() if a.text() == 'Text').trigger(); settle(); separate()
    next(a for a in menu.actions() if a.text() == 'Parametric equation').trigger(); settle()
    assert len(w.scene.settings.shapes) == 2 and label() == 'Undo Add object', label()
    w.shape_list.setFocus(); settle()
    keys(w.shape_list); assert len(w.scene.settings.shapes) == 1 and w.redo_action.text() == 'Redo Add object'
    keys(w.shape_list, redo=True); assert len(w.scene.settings.shapes) == 2
    QTest.keyClick(w.shape_list, Qt.Key.Key_Y, CTRL); settle()  # Windows' second Redo key: nothing left.
    assert len(w.scene.settings.shapes) == 2
    separate()

    # Delete the first object; Undo brings it back selected.
    w.select_shape(0); settle(); w.remove_button.click(); settle()
    assert len(w.scene.settings.shapes) == 1 and label() == 'Undo Delete object'
    keys(w.shape_list); assert len(w.scene.settings.shapes) == 2 and w.scene.selected == 0 and w.shape_list.currentRow() == 0
    separate()

    # Wheel turns on one spin box are one step, even with the field focused.
    box = w.parameters['shape_size']; box.setFocus(); settle()
    start = w.scene.shape.shape_size
    for _ in range(5):
        centre = QPointF(box.rect().center())
        app.sendEvent(box, QWheelEvent(centre, QPointF(box.mapToGlobal(centre.toPoint())), QPoint(0, 0), QPoint(0, 120),
                                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))
        QTest.qWait(30)
    settle()
    assert w.scene.shape.shape_size > start and label() == 'Undo Resize object', (w.scene.shape.shape_size, label())
    keys(box)  # The focused spin box has nothing of its own to undo: the scene steps back.
    assert w.scene.shape.shape_size == start and label() == 'Undo Add object', label()
    keys(box, redo=True); assert w.scene.shape.shape_size > start
    separate()

    # A slider drag is one step.
    gain = w.parameters['drawing_gain']; gain.slider.setFocus(); settle()
    level = w.scene.settings.drawing_gain
    gain.slider.setSliderDown(True)
    for position in (2000, 3000, 4000, 5000): gain.slider.setValue(position); QTest.qWait(25)
    gain.slider.setSliderDown(False); settle()
    assert w.scene.settings.drawing_gain != level and label() == 'Undo Change level', label()
    w.undo_action.trigger(); settle(); assert w.scene.settings.drawing_gain == level
    w.redo_action.trigger(); settle()
    separate()

    # A viewport drag of the selected object (its centre handle) is one step.
    w.tick(); settle()
    index, centre, _ = next(h for h in w.handles if h[0] == w.scene.selected)
    before = (w.scene.shape.position_x, w.scene.shape.position_y)
    point = QPointF(*centre)
    assert w.pointer('press', point)
    for step in range(1, 6): w.pointer('move', point + QPointF(8*step, 4*step)); QTest.qWait(20)
    w.pointer('release', point + QPointF(40, 20)); settle()
    assert (w.scene.shape.position_x, w.scene.shape.position_y) != before and label() == 'Undo Move object', label()
    w.undo(); assert (w.scene.shape.position_x, w.scene.shape.position_y) == before
    w.redo(); separate()

    # Transform stack through the panel: add (Map and Noise too), reorder and bypass.
    w.scene_sections.setCurrentIndex(1); settle()
    panel = w.transform_panel
    for kind in (ROTATE, SPIN, MAP, NOISE): panel.add(kind); settle(); separate()
    assert [t.kind for t in w.scene.shape.transforms] == [ROTATE, SPIN, MAP, NOISE] and label() == 'Undo Add transform'
    panel.list.setCurrentRow(1); panel.action('up'); settle()
    assert [t.kind for t in w.scene.shape.transforms][:2] == [SPIN, ROTATE] and label() == 'Undo Reorder transforms', label()
    separate()
    panel.list.itemWidget(panel.list.item(0)).power.click(); settle()
    assert not w.scene.shape.transforms[0].enabled and label() == 'Undo Bypass transform', label()
    w.undo(); assert w.scene.shape.transforms[0].enabled
    w.undo(); assert [t.kind for t in w.scene.shape.transforms][:2] == [ROTATE, SPIN]
    w.redo(); w.redo(); assert not w.scene.shape.transforms[0].enabled
    separate()
    stack = w.scene.shape.transforms
    w.undo(); w.undo(); w.undo()  # Bypass, reorder, then Add Noise.
    assert [t.kind for t in w.scene.shape.transforms] == [ROTATE, SPIN, MAP]
    while w.scene.shape.transforms != stack: w.redo()
    assert not w.redo_action.isEnabled()

    # Scene settings and the listening mode.
    separate(); w.edit('bpm', 97.); settle()
    assert label() == 'Undo Change tempo'
    separate(); w.edit('mode', 'Original audio'); settle()
    assert w.scene.mode == 'music' and label() == 'Undo Change listening mode'
    w.undo(); assert w.scene.mode == 'drawing'
    w.undo(); assert w.scene.settings.bpm != 97.
    w.redo(); w.redo(); assert w.scene.mode == 'music'

    # Saving marks the step: Undo away and back toggles the title's unsaved mark.
    w.sandbox_scene.path = Path(folder)/'undo.scene.json'
    assert w.save_scene() and not w.scene.dirty and not w.windowTitle().startswith('*')
    w.undo(); assert w.scene.dirty and w.windowTitle().startswith('*')
    w.redo(); assert not w.scene.dirty and not w.windowTitle().startswith('*')

    # Solo is window-only: not a step.
    separate(); steps = len(w.scene_history)
    w.toggle_objects('solo', 0); settle(); assert w.solo_ids and len(w.scene_history) == steps
    w.toggle_objects('solo', 0); settle()

    # The Composer keeps its own history; the Sandbox's is back on return.
    w.set_workspace('Composer'); settle()
    assert w.undo_action.text() != 'Undo Change listening mode'
    w.set_workspace('Sandbox'); settle()
    assert label() == 'Undo Change listening mode', label()

    # Opening a scene starts a fresh history.
    w.open_scene_path(str(w.sandbox_scene.path)); settle()
    assert not w.undo_action.isEnabled() and len(w.scene.settings.shapes) == 2

    w.tick(); QTest.qWait(50)
    w.force_close = True; w.close()
print('Sandbox undo passed: menus, keys, wheel, slider and viewport drags, transforms, scene settings, save marks, workspaces.')
