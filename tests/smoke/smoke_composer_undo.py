"""Composer edit gestures round-trip through the window's snapshot history."""
import _path  # noqa: F401
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint, QPointF, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from phosphor.editor import qt_app as q
from phosphor.editor import composer_view as cv
from phosphor.engine.composition import COMPOSER, SANDBOX
from phosphor.engine.lane_access import get_points
from phosphor.storage import project_files


q.configure_graphics()
app = QApplication([])
clock = [0.]


def move(widget, point):
    app.sendEvent(widget, QMouseEvent(QEvent.Type.MouseMove, QPointF(point),
                  QPointF(widget.mapToGlobal(point)), Qt.MouseButton.NoButton,
                  Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def drag(widget, start, end):
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    move(widget, QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2))
    # Exercise the held-mouse re-arm without waiting for the timer.
    w.commit_history()
    assert w.history_timer.isActive()
    move(widget, end)
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)


def flush():
    w.commit_history(now=clock[0])
    app.processEvents()


def round_trip(label, edit):
    clock[0] += 1.  # Deliberately outside the coalescing window.
    view.setFocus(); app.processEvents()
    before = w.project
    edit(); flush()
    after = w.project
    assert before != after, label
    assert w.undo_action.text() == 'Undo ' + label, w.undo_action.text()
    view.setFocus(); w.undo_action.trigger()
    assert w.project == before, label
    assert w.redo_action.text() == 'Redo ' + label
    w.redo_action.trigger()
    assert w.project == after, label


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    with patch.object(project_files, 'WORKSPACE_PATH', root / 'workspace.json'), \
         patch.object(q, 'write_workspace', lambda name: None), \
         patch('phosphor.editor.history.monotonic', side_effect=lambda: clock[0]), \
         patch.object(q.Window, 'remember_file', lambda *args: None):
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
        try:
            w.resize(1220, 840); w.show(); w.activateWindow(); app.processEvents()
            w.timer.stop()  # No playback/render timing is needed by these assertions.
            view = w.composer_panel.timeline
            view.scroll, view.scale, view.top_pitch = 0., 40., 68  # Rows 68–58 stay above the velocity lane.
            w.add_object('Cube'); flush()
            w.project_path = root / 'undo.project.json'
            assert w.save_project()
            saved = w.project
            assert not w.project_dirty

            round_trip('Move clip', lambda: drag(view,
                QPoint(round(view.x_of(2)), cv.RULER + 10),
                QPoint(round(view.x_of(4)), cv.RULER + 10)))
            point = QPoint(round(view.x_of(4) + 2), round(view.y_of(60) + view.row / 2))
            assert view.roll_top <= point.y() < view.note_bottom(), 'note-roll click outside the visible roll'
            round_trip('Edit notes', lambda: QTest.mouseClick(view, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, point))
            def delete_note():
                menu = view.build_note_menu(0)
                next(a for a in menu.actions() if a.text() == 'Delete notes').trigger()
                menu.deleteLater()
            round_trip('Edit notes', delete_note)

            # Set up a lane, then separately verify a node drag as one history entry.
            key = ('mix', 'drawing_gain')
            w.automate(key, True); flush(); app.processEvents()
            rect = view.block_rect(0)
            start = w.project.clips[0].start
            at = lambda beat, gain: QPoint(round(view.x_of(start + beat)), round(view.gain_y(rect, gain)))
            QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(8, .5))
            flush()
            points = get_points(w.project.clips[0], key)
            assert len(points) == 3, points
            round_trip('Edit automation', lambda: drag(view, at(8, points[1][1]), at(9, .2)))

            w.show_panel(q.PANEL_MIX); app.processEvents()
            slider = w.parameters['drawing_gain'].slider
            round_trip('Edit scene', lambda: slider.setValue(slider.value() - 1000))
            round_trip('Change tempo', lambda: w.composer_panel.bpm.setValue(135))

            # The actual window shortcut also restores a project, without moving time.
            view.setFocus(); w.playhead = 1.25
            after = w.project
            QTest.keyClick(view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            assert w.project != after and w.playhead == 1.25
            QTest.keyClick(view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
            assert w.project == after and w.playhead == 1.25

            # A focused text editor accepts its own Undo shortcut through ShortcutOverride.
            field = QLineEdit(w.centralWidget()); field.show(); field.setFocus(); app.processEvents()
            QTest.keyClicks(field, 'abc')
            before = w.project
            QTest.keyClick(field, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            assert w.project is before and field.text() != 'abc'
            field.hide(); field.deleteLater(); view.setFocus(); app.processEvents()

            while w.history.can_undo: w.undo()
            # The initial empty project precedes the save; redo back to the saved entry.
            while w.project != saved: w.redo()
            assert not w.project_dirty and not w.history.dirty
            w.redo(); assert w.project_dirty
            w.undo(); assert w.project == saved and not w.project_dirty
            w.set_workspace(SANDBOX)
            assert not w.undo_action.isEnabled() and not w.redo_action.isEnabled()
            w.set_workspace(COMPOSER)
            w.load_project(saved, None)
            assert not w.history.can_undo and not w.history.can_redo and not w.project_dirty
        finally:
            w.history_timer.stop(); w.timer.stop()
            w.force_close = True
            w.shutdown_audio(); w.release_views(); w.close()
print('Composer undo passed: clip/note/node/scene/tempo edits, labels, text focus, save and reset.')
