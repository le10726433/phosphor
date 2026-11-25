"""Transform roll editing: reference line snapping, segment drags, reference gain dialog, clip end handles."""
import _path  # noqa: F401  (makes the phosphor package importable)
import sys
import os
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent
from PySide6.QtGui import QMouseEvent

from phosphor.editor import qt_app as q
from phosphor.editor import composer_view as cv
from phosphor.engine.automation import gain_at
from phosphor.engine.composition import COMPOSER, Note, render_song
from phosphor.engine.transforms import Param
from phosphor.engine.lane_access import get_points, set_points
from phosphor.engine import lane_edit
from phosphor.storage import project_files


def move(view, target, modifiers=Qt.KeyboardModifier.NoModifier):
    """Mouse move with the left button held and the given modifiers (QTest.mouseMove has none)."""
    app.sendEvent(view, QMouseEvent(QEvent.Type.MouseMove, QPointF(target), QPointF(view.mapToGlobal(target)),
                                    Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, modifiers))


def drag(view, start, end, modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    middle = QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2)
    move(view, middle, modifiers); move(view, end, modifiers)
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end); QTest.qWait(20)


folder = tempfile.TemporaryDirectory(); root = Path(folder.name)
q.configure_graphics(); app = QApplication([])
with patch.object(project_files, 'WORKSPACE_PATH', root / 'workspace.json'), patch.object(q, 'write_workspace', lambda name: project_files.write_workspace(name, root / 'workspace.json')):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
    w.resize(1220, 840); w.show(); QTest.qWait(150)
    w.add_object('Cube'); w.transform_panel.add('Rotate'); QTest.qWait(30)
    key = ('transform', 0, 0, 'z')
    w.automate(key, True); QTest.qWait(30)
    view = w.composer_panel.timeline; view.scroll, view.scale, view.lane_scroll = 0., 40., 0; QTest.qWait(30)
    rect = view.block_rect(0)
    at = lambda beat, gain: QPoint(round(view.x_of(beat)), round(view.gain_y(rect, gain)))
    lane = lambda: get_points(w.project.clips[0], key)

    # A node dragged near the reference (gain 1) snaps exactly onto it; with Ctrl it does not.
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(8., .5)); QTest.qWait(20)
    assert [b for b, _, _ in lane()] == [0., 8., 16.] and abs(lane()[1][1] - .5) < .03, lane()
    near = QPoint(at(8., 1.).x(), at(8., 1.).y() + 3)
    drag(view, at(8., lane()[1][1]), near)
    assert lane()[1][1] == 1., lane()
    drag(view, at(8., 1.), near, Qt.KeyboardModifier.ControlModifier)
    assert lane()[1][1] != 1. and abs(lane()[1][1] - 1.) < .3, lane()  # 3 px is about 0.23 gain in a block.

    # Segment drag: a four-node lane; drag the middle segment down by about 0.25.
    clip = set_points(w.project.clips[0], key, ((0., 1., 'linear'), (4., 1., 'linear'), (8., .5, 'linear'), (16., 1., 'linear')))
    w.set_project(replace(w.project, clips=(clip,))); QTest.qWait(30)
    before = lane(); grid = np.linspace(0., 16., 641)
    drag(view, at(6., .75), at(6., .5), Qt.KeyboardModifier.ControlModifier)  # Ctrl: no snapping, an exact delta.
    after = lane(); delta = gain_at(after, (.5, 0.), 3.) - gain_at(before, (.5, 0.), 3.)
    beats = [b for b, _, _ in after]
    assert beats.count(4.) == 2 and beats.count(8.) == 2, after  # Step nodes under both ends.
    old, new = gain_at(before, (1., 0.), grid), gain_at(after, (1., 0.), grid)
    outside = (grid < 4.) | (grid > 8.)
    np.testing.assert_allclose(new[outside], old[outside], atol=1e-9)  # The rest of the lane did not move.
    inside = (grid >= 4.) & (grid < 8.)
    shift = new[inside] - old[inside]
    assert abs(shift.mean() + .25) < .04 and np.ptp(shift) < 1e-6, (shift.mean(), np.ptp(shift))  # Moved evenly.
    # Dragging the same segment again reuses the step nodes.
    drag(view, at(6., gain_at(lane(), (1., 0.), 6.)), at(6., gain_at(lane(), (1., 0.), 6.) - .2), Qt.KeyboardModifier.ControlModifier)
    assert [b for b, _, _ in lane()].count(4.) == 2 and len(lane()) == 6, lane()

    # Right-click the reference line: nodes on it move to the typed gain; the others stay.
    placed = [p for p in lane() if abs(p[1] - 1.) > 1e-6]
    # The dialog must not open inside the press: on macOS a modal dialog there swallows the
    # release and the window behaves as if the button were still held (even Play misbehaves).
    calls = []
    def typed(*args): calls.append(QApplication.mouseButtons()); return .8, True
    with patch.object(cv, 'QInputDialog', SimpleNamespace(getDouble=typed)):
        QTest.mousePress(view, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, at(12., 1.)); QTest.qWait(20)
        assert calls == [], 'Reference dialog opened during the press'
        QTest.mouseRelease(view, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, at(12., 1.)); QTest.qWait(20)
    assert calls == [Qt.MouseButton.NoButton], calls
    assert lane()[0][1] == .8 and lane()[-1][1] == .8, lane()
    assert [p for p in lane() if abs(p[1] - .8) > 1e-6] == placed, (lane(), placed)

    # Node entry: reproduce Qt's press, release, double-click, release sequence.
    original = ((0., 0., 'linear'), (4., .25, 'hold'), (8., -.5, 'linear'), (16., 0., 'linear'))
    def node_entry(index, selected, accepted=True, modifiers=Qt.KeyboardModifier.NoModifier):
        # The first click selects an unselected node, as any click does; on a selected
        # node it keeps the selection, so the dialog then edits every selected node.
        after = set(selected) if index in selected else {index}
        clip = set_points(w.project.clips[0], key, original)
        w.set_project(replace(w.project, clips=(clip,))); QTest.qWait(20)
        view.node_selection = (key, set(selected)); view.setFocus(); w.commit_history()
        calls = []
        def typed_node(*args):
            calls.append(args)
            assert QApplication.mouseButtons() == Qt.MouseButton.NoButton
            assert lane() == original and view.node_selection == (key, after)
            return .625, accepted
        position = at(*original[index][:2])
        with patch.object(cv, 'QInputDialog', SimpleNamespace(getDouble=typed_node)):
            QTest.mousePress(view, Qt.MouseButton.LeftButton, modifiers, position)
            move(view, position + QPoint(1, 0), modifiers)  # Click jitter must not move a node.
            QTest.mouseRelease(view, Qt.MouseButton.LeftButton, modifiers, position)
            assert lane() == original and view.node_selection == (key, after)
            QTest.mouseDClick(view, Qt.MouseButton.LeftButton, modifiers, position)
            assert not calls and lane() == original
            QTest.mouseRelease(view, Qt.MouseButton.LeftButton, modifiers, position)
            QTest.qWait(QApplication.doubleClickInterval() + 20)
        assert len(calls) == 1 and calls[0][1] == 'Node gain', calls
        assert calls[0][3:] == (original[index][1], *cv.GAIN_RANGE, 3), calls
        assert view.node_selection == (key, after)
        expected = lane_edit.set_gains(original, selected if index in selected else {index}, .625) if accepted else original
        assert lane() == expected, lane()
        assert view.drag is None
        if accepted:
            w.commit_history()
            QTest.keyClick(view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier); QTest.qWait(30)
            assert lane() == original, lane()
    node_entry(1, {0, 2})  # Unselected node: the click selects it; only it changes.
    node_entry(2, {1, 2})  # Selected node: all selected gains, one undo step.
    node_entry(1, {0, 2}, accepted=False)

    # Off-node double-click keeps the existing first-click node insertion behavior.
    clip = set_points(w.project.clips[0], key, original)
    w.set_project(replace(w.project, clips=(clip,))); QTest.qWait(20)
    def unexpected_dialog(*args): raise AssertionError('Off-node double-click opened a dialog')
    position = at(12., -.75)
    with patch.object(cv, 'QInputDialog', SimpleNamespace(getDouble=unexpected_dialog)):
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, position)
        after_click = lane()
        QTest.mouseDClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, position)
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, position)
        QTest.qWait(20)
    assert lane() == after_click and len(after_click) == len(original) + 1

    # Clip end handles: the left end resizes from the start and keeps the end.
    view.set_roll(cv.NOTE_ROLL); QTest.qWait(20)
    # A time-varying Rotate Y (90°/s) so the scene clock t matters, then render a stretch of the song.
    first_clip = w.project.clips[0]; step = first_clip.settings.shapes[0].transforms[0]
    shape = replace(first_clip.settings.shapes[0], transforms=(step.with_param('y', Param(0., '90*t')),))
    w.set_project(replace(w.project, notes=(Note(0., 16., 57),), clips=(replace(first_clip, settings=replace(first_clip.settings, shapes=(shape,))),)))
    QTest.qWait(30)
    machine = replace(w.output_settings(), source='Single tone')
    times = float(w.project.seconds(6.)) + np.arange(2400) / 48000.
    before_samples = render_song(times, w.project, machine)
    end = w.project.clips[0].end; y = cv.RULER + 10
    song = np.linspace(2., 16., 561); song_gain = gain_at(lane(), (1., 0.), song - w.project.clips[0].start)
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(view.x_of(0.)) + 2, y))
    QTest.mouseMove(view, QPoint(round(view.x_of(2.)) + 2, y))
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(view.x_of(2.)) + 2, y))
    assert w.project.clips[0].start == 2. and w.project.clips[0].end == end, w.project.clips[0]
    # The automation stayed put in song time: same gain at every song beat inside the clip.
    np.testing.assert_allclose(gain_at(lane(), (1., 0.), song - 2.), song_gain, atol=1e-3)
    # The scene clock continued too (Clip.time_offset): the same samples at the same song time.
    assert w.project.clips[0].time_offset == 2., w.project.clips[0]
    np.testing.assert_allclose(render_song(times, w.project, machine), before_samples, atol=1e-6)
    view.set_roll(cv.TRANSFORM_ROLL); QTest.qWait(50)
    w.composer_panel.grab().save(str(root / 'lane-editing.png'))

    # Clicking within SEGMENT_PIXELS of a line adds a node exactly on it; dragging still moves it.
    clip = set_points(w.project.clips[0], key, ((0., 0., 'linear'), (16., 0., 'linear')))
    w.set_project(replace(w.project, clips=(clip,), notes=())); view.selected_clip = 0; view.scroll = view.project.clips[0].start
    view.lane_scroll = 0; QTest.qWait(30); rect = view.block_rect(0); start = view.project.clips[0].start
    on_line = lambda beat, dy: QPoint(round(view.x_of(start + beat)), round(view.gain_y(rect, 0.)) + dy)
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, on_line(8., cv.SEGMENT_PIXELS - 2)); QTest.qWait(20)
    assert lane() == ((0., 0., 'linear'), (8., 0., 'linear'), (16., 0., 'linear')), lane()
    drag(view, on_line(4., 3), on_line(4., 30))
    assert all(abs(b - 4.) > 1e-6 for b, _, _ in lane()) and gain_at(lane(), (1., 0.), [4.])[0] < -.2, lane()
    # Alt/Option bends the middle in one undo step, without focusing Windows menus.
    clip = set_points(w.project.clips[0], key, ((0., 0., 'linear'), (16., 0., 'linear')))
    w.set_project(replace(w.project, clips=(clip,))); view.setFocus(); w.commit_history()
    original = lane(); rect = view.block_rect(0)
    a = on_line(8., 0); b = a + QPoint(0, -16)
    QTest.keyPress(view, Qt.Key.Key_Alt)
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, a)
    move(view, b, Qt.KeyboardModifier.AltModifier)
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, b)
    QTest.keyRelease(view, Qt.Key.Key_Alt); QTest.qWait(30); w.commit_history()
    curved = lane()
    assert curved[0][2] == 'curve' and gain_at(curved, (1., 0.), 8.) > .2, curved
    if sys.platform == 'win32': assert w.menuBar().activeAction() is None
    # Hit-testing follows the exact chords used to paint the 24-sample polyline.
    polyline = view.segment_polyline(rect, curved, 0)
    assert len(polyline) == 24
    for left, right in zip(polyline, polyline[1:]):
        mid = (left + right) / 2
        assert view.segment_at(rect, curved, mid.x(), mid.y()) == 0
    midpoint = polyline[11]
    assert abs(midpoint.y() - view.gain_y(rect, 0.)) > cv.SEGMENT_PIXELS
    assert view.segment_at(rect, curved, midpoint.x(), view.gain_y(rect, 0.)) is None
    w.undo(); assert lane() == original, lane()
    w.redo(); assert lane() == curved, lane()
    menu = view.build_segment_menu(key, curved, 0)
    next(action for action in menu.actions() if action.text() == 'Straighten').trigger()
    w.commit_history(); assert lane() == original, lane()
    w.undo(); assert lane() == curved, lane()
    w.redo(); assert lane() == original, lane()
    menu.deleteLater()

    # Cmd/Ctrl + wheel: over the roll it sets block (or note row) height; over the ruler it zooms time.
    from PySide6.QtGui import QWheelEvent
    # macOS: Command arrives as Control and the Control key as Meta; elsewhere Ctrl zooms, Alt sizes.
    HEIGHT, ZOOM = ((Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.MetaModifier) if sys.platform == 'darwin'
                    else (Qt.KeyboardModifier.AltModifier, Qt.KeyboardModifier.ControlModifier))
    def wheel(y, up=True, modifiers=HEIGHT):
        point = QPointF(view.width() / 2, y)
        QApplication.sendEvent(view, QWheelEvent(point, view.mapToGlobal(point), QPoint(), QPoint(0, 120 if up else -120),
                                                 Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False))
    scale, block = view.scale, view.block
    wheel(view.roll_top + 60); assert view.block == block + cv.BLOCK_STEP and view.scale == scale, (view.block, view.scale)
    wheel(view.roll_top + 60, up=False); assert view.block == block
    for _ in range(40): wheel(view.roll_top + 60, up=False)
    assert view.block == cv.BLOCK_RANGE[0]
    wheel(cv.RULER / 2, modifiers=ZOOM); assert view.scale > scale and view.block == cv.BLOCK_RANGE[0]
    scale = view.scale; wheel(view.roll_top + 60, modifiers=ZOOM)  # Control zooms over the cells too.
    assert view.scale > scale and view.block == cv.BLOCK_RANGE[0], (view.scale, view.block)
    shift_from = view.scroll; wheel(view.roll_top + 60, up=False, modifiers=Qt.KeyboardModifier.ShiftModifier)
    assert view.scroll > shift_from  # Shift moves in time.
    view.set_roll(cv.NOTE_ROLL); row = view.row
    wheel(view.roll_top + 40); assert view.row == row + 1
    for _ in range(40): wheel(view.roll_top + 40)
    assert view.row == cv.ROW_RANGE[1]
    # The Composer panel's resize edge: a taller grab zone, hidden with the panel in Sandbox.
    assert w.vsplit.objectName() == 'composer_split' and w.vsplit.handleWidth() == q.COMPOSER_HANDLE
    handle = w.vsplit.handle(1); assert isinstance(handle, q.LineHandle) and handle.height() == q.COMPOSER_HANDLE
    # The line is centred, so a press on it or just below it still grabs the edge.
    image = handle.grab().toImage(); ratio = image.devicePixelRatio(); column = int(handle.width() / 3 * ratio)
    lit = [y for y in range(image.height()) if image.pixelColor(column, y) == handle.color]
    assert lit and abs(min(lit) / ratio - handle.height() // 2) < 1, (lit, handle.height())
    for offset in (handle.height() // 2, handle.height() // 2 + 3):  # On the line, and 3 px below it.
        sizes = w.vsplit.sizes(); start = QPoint(handle.width() // 3, offset)
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(handle, start + QPoint(0, -40))
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(0, -40)); QTest.qWait(30)
        assert w.vsplit.sizes()[1] > sizes[1], (offset, sizes, w.vsplit.sizes())
        w.vsplit.setSizes(sizes); QTest.qWait(20)

    w.project_dirty = False; w.sandbox_scene.saved = w.sandbox_scene.document()
    w.force_close = True; w.release_views(); w.close()
print('Lane editing passed: reference snapping and override, segment drags with step nodes, reference dialog, clip ends, click on line, Cmd+wheel heights, resize edge.')
