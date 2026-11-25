"""Velocity action, real lane gestures and history, native or --headless."""
import _path  # noqa: F401
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from phosphor.editor import qt_app as q
from phosphor.editor.composer_view import NOTE_ROLL, TRANSFORM_ROLL
from phosphor.engine.composition import COMPOSER, SANDBOX, Note

q.configure_graphics(); app = QApplication([])
with patch.object(q, 'write_workspace', lambda *args: None):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
    try:
        w.resize(1220, 840); w.show(); w.activateWindow(); app.processEvents(); w.timer.stop()
        view = w.composer_panel.timeline
        view.roll_hear_notes = False
        view.setFocus()
        assert w.project.velocity and w.velocity_action.isChecked()
        assert w.velocity_action.isVisible() and w.velocity_action.isEnabled()
        before = w.project
        w.velocity_action.trigger()
        assert not w.project.velocity and w.history.undo_label == 'Velocity off'
        w.undo(); assert w.project == before and w.velocity_action.isChecked()
        w.redo(); assert not w.project.velocity
        w.undo()
        assert view.velocity_lane_rect() is not None
        assert view.roll_velocity.isChecked()
        view.roll_velocity.click()
        assert not w.project.velocity and not w.velocity_action.isChecked() and not view.roll_velocity.isChecked()
        w.undo(); assert view.roll_velocity.isChecked()
        view.set_roll(TRANSFORM_ROLL)
        assert view.velocity_lane_rect() is None and view.roll_velocity.isHidden()
        assert not view.grab().isNull()
        view.set_roll(NOTE_ROLL)
        view.set_roll_option('roll_velocity_lane', False)
        assert view.velocity_lane_rect() is None and view.roll_velocity.isHidden()
        view.set_roll_option('roll_velocity_lane', True)
        view.scale, view.scroll = 80., 0.

        def fixture():
            view.setFocus(); w.commit_history()
            notes = tuple(Note(beat, .75, pitch, v/127) for beat, pitch, v in ((1., 60, 32), (2., 64, 64), (3., 67, 96)))
            w.set_project(replace(w.project, notes=notes)); w.commit_history()
            view.focus_kind = 'notes'; view.note_selection = set(); view.selected_note = None
            view.velocity_strip.set_rect(view.velocity_lane_rect())
            return notes

        def point(beat, value):
            return QPoint(round(view.x_of(beat)), round(view.velocity_strip.y_of(value)))

        def drag(a, b, shift=False):
            view.setFocus(); w.commit_history()
            size = len(w.history._entries)
            modifier = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
            QTest.mousePress(view, Qt.MouseButton.LeftButton, modifier, a)
            QTest.mouseMove(view, b, 30)
            QTest.mouseRelease(view, Qt.MouseButton.LeftButton, modifier, b)
            w.commit_history(); app.processEvents()
            assert len(w.history._entries) == size + 1, 'one step per drag'
            assert w.history.undo_label == 'Edit velocity'

        notes = fixture()
        drag(point(1., notes[0].velocity), point(1., .6))
        assert w.project.notes[0].velocity > .5 and w.project.notes[1:] == notes[1:]
        assert view.note_selection == {0}
        w.undo(); assert w.project.notes == notes
        w.redo(); assert w.project.notes[0].velocity > .5

        notes = fixture(); view.note_selection = {0, 1}
        drag(point(1., notes[0].velocity), point(1., .5))
        delta = w.project.notes[0].velocity - notes[0].velocity
        assert abs(w.project.notes[1].velocity - notes[1].velocity - delta) < 1e-12
        assert w.project.notes[2] == notes[2]

        notes = fixture()
        drag(point(.6, .2), point(3.3, .2))
        assert len({n.velocity for n in w.project.notes}) == 1
        assert all(n.velocity < .3 for n in w.project.notes)

        notes = fixture(); view.note_selection = {0, 1, 2}
        drag(point(.8, .15), point(3.2, .9), shift=True)
        assert w.project.notes[0].velocity < w.project.notes[1].velocity < w.project.notes[2].velocity

        w.commit_history(); view.set_selection_velocity(.4); w.commit_history()
        assert all(n.velocity == .4 for n in w.project.notes)
        assert w.history.undo_label == 'Edit velocity'
        view.ramp_selection_velocity(.2, .8); w.commit_history()
        assert [round(n.velocity, 8) for n in w.project.notes] == [.2, .5, .8]
        for on in (False, True):
            w.set_project(replace(w.project, velocity=on))
            assert view.roll_velocity.isChecked() == on
            for roll in (NOTE_ROLL, TRANSFORM_ROLL):
                view.set_roll(roll); assert not view.grab().isNull()
        w.set_workspace(SANDBOX)
        assert not w.velocity_action.isVisible() and not w.velocity_action.isEnabled()
    finally:
        w.history_timer.stop(); w.timer.stop(); w.force_close = True
        w.shutdown_audio(); w.release_views(); w.close()
print('Velocity passed: defaults, switches, Undo/Redo, lane visibility, bar/selection/paint/ramp gestures, tools and both rolls.')
