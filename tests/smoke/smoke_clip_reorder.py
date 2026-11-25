"""UI1: reorder scene clips by mouse (swap, gap drop, block move; one Undo step each) and
collapse the velocity lane, native or --headless."""
import _path  # noqa: F401
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint, QPointF, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from _screen import exit_cleanly_on_failure
from phosphor.editor import qt_app as q
from phosphor.editor import composer_view as cv
from phosphor.editor.lane_strip import LANE_HEIGHT, COLLAPSED_HEIGHT
from phosphor.engine.composition import COMPOSER, Clip, empty_settings, validate_project
from phosphor.engine.signal_engine import Shape

exit_cleanly_on_failure()
q.configure_graphics()
app = QApplication([])
clock = [0.]


def move(widget, point, modifiers=Qt.KeyboardModifier.NoModifier):
    app.sendEvent(widget, QMouseEvent(QEvent.Type.MouseMove, QPointF(point), QPointF(widget.mapToGlobal(point)),
                                      Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, modifiers))


def clip_point(beat):
    return QPoint(round(view.x_of(beat)), cv.RULER + cv.TRACK // 2)


def drag_clip(beat, beats):
    """Press a clip body at `beat`, drag it `beats` along in four steps, release: one Undo step."""
    clock[0] += 10.; view.setFocus(); w.commit_history()
    start = clip_point(beat); QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for k in range(1, 5):
        move(view, clip_point(beat + beats * k / 4))
        w.commit_history(); assert w.history_timer.isActive()  # Held: no step yet.
    end = clip_point(beat + beats)
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    w.commit_history(now=clock[0]); app.processEvents()
    # One step per drag: the callers check that a single Undo restores the project.
    assert w.undo_action.text() == 'Undo Move clip', w.undo_action.text()
    validate_project(w.project)


def layout():
    return [(c.name, c.start) for c in w.project.clips]


with patch.object(q, 'write_workspace', lambda *args: None), \
     patch('phosphor.editor.history.monotonic', side_effect=lambda: clock[0]):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
    try:
        w.resize(1220, 840); w.show(); w.activateWindow(); app.processEvents(); w.timer.stop()
        view = w.composer_panel.timeline
        view.scroll, view.scale = 0., 24.
        view.snap = cv.SNAPS['1/4']  # Quarter beats: the drags below land on whole beats.

        # A [0, 4], B [6, 14], C [24, 28]: A has an object, so the editor's clip can be told apart.
        a_settings = replace(empty_settings(), shapes=(Shape(),))
        clips = (Clip(0., 4., a_settings, name='A'), Clip(6., 8., empty_settings(), name='B'),
                 Clip(24., 4., empty_settings(), name='C'))
        w.set_project(validate_project(replace(w.project, clips=clips))); w.select_clip(0)
        clock[0] += 10.; w.commit_history(now=clock[0])
        original = w.project
        assert w.clip_index == 0 and w.clip_scene.settings is a_settings

        # Swap: A's end passes B's middle (10): B takes A's old start, the gap of 2 stays, A ends where B ended.
        drag_clip(2., 8.)
        assert layout() == [('B', 0.), ('A', 10.), ('C', 24.)], layout()
        assert w.clip_index == 1 and w.clip_scene.settings is a_settings  # The editor follows A.
        assert view.selected_clip == 1 and w.project.clips[1].settings is a_settings  # Its scene travelled with it.
        view.setFocus(); w.undo_action.trigger()
        assert w.project == original and w.clip_index == 0, layout()

        # Gap drop: A into the free gap before C (touching C is allowed); B and C stay put.
        drag_clip(2., 20.)
        assert layout() == [('B', 6.), ('A', 20.), ('C', 24.)], layout()
        view.setFocus(); w.undo_action.trigger(); assert w.project == original

        # Before a neighbour's middle the clip stops flush against it, as before UI1.
        drag_clip(2., 4.)
        assert layout() == [('A', 2.), ('B', 6.), ('C', 24.)], layout()
        view.setFocus(); w.undo_action.trigger(); assert w.project == original

        # Block: Shift+click B adds it; dragging A past C's middle moves A and B together.
        w.select_clip(0); view.selected_clip = 0; view.clip_picks = set()
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, clip_point(10.))
        assert view.picked_clips() == [0, 1], view.picked_clips()
        drag_clip(2., 16.)
        assert layout() == [('C', 0.), ('A', 14.), ('B', 20.)], layout()
        assert view.picked_clips() == [1, 2] and w.clip_index == 1 and w.clip_scene.settings is a_settings
        view.setFocus(); w.undo_action.trigger(); assert w.project == original
        w.redo_action.trigger(); assert layout() == [('C', 0.), ('A', 14.), ('B', 20.)]
        w.undo_action.trigger(); assert w.project == original

        # Undo after selecting another clip reopens that clip (the Undo entry follows the selection).
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, clip_point(10.))
        assert w.clip_index == 1 and view.picked_clips() == [1]
        b_settings = w.project.clips[1].settings
        drag_clip(10., -10.)  # B's start passes A's middle: B then A.
        assert layout() == [('B', 0.), ('A', 10.), ('C', 24.)] and w.clip_scene.settings is b_settings, layout()
        view.setFocus(); w.undo_action.trigger()
        assert w.project == original and w.clip_index == 1 and w.clip_scene.settings is b_settings
        w.select_clip(0); view.selected_clip = 0; clock[0] += 10.; w.commit_history(now=clock[0])

        # Equal lengths: a swap leaves every positional start the same and is still "Move clip".
        twins = (Clip(0., 4., a_settings, name='A'), Clip(4., 4., empty_settings(), name='B'))
        w.set_project(validate_project(replace(w.project, clips=twins))); w.select_clip(0)
        clock[0] += 10.; w.commit_history(now=clock[0])
        drag_clip(2., 4.)
        assert layout() == [('B', 0.), ('A', 4.)], layout()
        view.setFocus(); w.undo_action.trigger(); assert layout() == [('A', 0.), ('B', 4.)]
        w.set_project(original); w.select_clip(0); view.selected_clip = 0; clock[0] += 10.; w.commit_history(now=clock[0])

        # A blocked block drag is a drag, not a click: the block stays picked.
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, clip_point(10.))
        assert view.picked_clips() == [0, 1]
        start = clip_point(2.); QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        move(view, clip_point(0.)); QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, clip_point(0.))
        assert w.project is original and view.picked_clips() == [0, 1], view.picked_clips()
        # Another edit mid-drag ends the drag: the press snapshot never comes back over it.
        start = clip_point(2.); QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        edited = replace(w.project, bpm=w.project.bpm + 1.); w.set_project(edited)
        move(view, clip_point(20.)); QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, clip_point(20.))
        assert w.project is edited and view.drag is None, layout()
        w.set_project(original); w.commit_history()
        assert not view.grab().isNull()

        # Velocity lane › Collapse: a thin strip, saved with the roll view settings; › Expand restores it.
        view.set_roll(cv.NOTE_ROLL); view.set_roll_option('roll_velocity_lane', True)
        rect = view.velocity_lane_rect(); assert rect.height() == LANE_HEIGHT
        bottom = view.note_bottom()
        menu = view.build_velocity_menu()
        collapse = next(a for a in menu.actions() if a.objectName() == 'collapse_velocity_lane')
        assert collapse.text() == 'Collapse velocity lane'
        collapse.trigger(); menu.deleteLater()
        assert view.velocity_lane_rect().height() == COLLAPSED_HEIGHT and view.note_bottom() == bottom + LANE_HEIGHT - COLLAPSED_HEIGHT
        assert w.preferences['roll_velocity_collapsed'] and view.roll_velocity.isHidden()
        assert not view.grab().isNull()
        menu = view.build_velocity_menu()
        assert [a.objectName() for a in menu.actions()] == ['expand_velocity_lane']  # Nothing else acts on a collapsed lane.
        menu.deleteLater()
        # The Velocity lane toggle (show/hide) is unchanged and keeps the collapse.
        view.set_roll_option('roll_velocity_lane', False); assert view.velocity_lane_rect() is None
        view.set_roll_option('roll_velocity_lane', True); assert view.velocity_lane_rect().height() == COLLAPSED_HEIGHT
        # The saved setting comes back through the preferences.
        w.apply_preferences(dict(w.preferences, roll_velocity_collapsed=False)); assert view.velocity_lane_rect().height() == LANE_HEIGHT
        w.apply_preferences(dict(w.preferences, roll_velocity_collapsed=True)); assert view.roll_velocity_collapsed
        menu = view.build_velocity_menu()
        next(a for a in menu.actions() if a.objectName() == 'expand_velocity_lane').trigger(); menu.deleteLater()
        assert view.velocity_lane_rect().height() == LANE_HEIGHT and not w.preferences['roll_velocity_collapsed']
        assert view.roll_velocity.isVisible()
    finally:
        w.timer.stop(); w.shutdown_audio(); w.force_close = True; w.close(); app.processEvents()
print('Clip reorder (swap, gap drop, block, one Undo step each) and velocity lane collapse passed.')
