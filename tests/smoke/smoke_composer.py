"""Composer: workspace switch, clip scenes, timeline mouse editing, playback blocks and project files."""
import _path  # noqa: F401  (makes the phosphor package importable)
import tempfile
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, PropertyMock

import numpy as np
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QWidget, QScrollArea, QLineEdit, QMenu
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent

from phosphor.editor import qt_app as q
from phosphor.editor import composer_view as cv
from phosphor.engine.composition import Note, COMPOSER, SANDBOX, render_song
from phosphor.storage import project_files

folder = tempfile.TemporaryDirectory(); root = Path(folder.name)
q.configure_graphics(); app = QApplication([])
with patch.object(project_files, 'WORKSPACE_PATH', root / 'workspace.json'), \
     patch.object(q, 'read_preferences', lambda *args: dict(q.DEFAULT_PREFERENCES)), \
     patch.object(q, 'read_signal_preferences', lambda *args: dict(q.DEFAULT_SIGNAL_PREFERENCES)), \
     patch.object(q, 'write_workspace', lambda name: project_files.write_workspace(name, root / 'workspace.json')):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=None))  # No saved choice: Composer.
    w.force_close = True
    w.resize(1220, 840); w.show(); QTest.qWait(150)
    assert w.workspace == COMPOSER and w.composer_panel.isVisible() and not w.bar_buttons[q.PANEL_INPUT].isEnabled()
    assert w.workspace_buttons[COMPOSER].isChecked() and w.open_action.text() == 'Open project…'
    assert w.scene is w.clip_scene and len(w.project.clips) == 1

    # The selected clip's scene is edited through the usual panels.
    w.add_object('Cube'); assert len(w.project.clips[0].settings.shapes) == 1 and w.project_dirty
    w.edit('drawing_gain', .4); assert w.project.clips[0].settings.drawing_gain == .4

    # Timeline mouse: draw a note, drag its right edge, delete with right-click; add and resize clips.
    view = w.composer_panel.timeline; view.scroll, view.scale, view.top_pitch = 0., 40., 68  # Rows 68–58 stay above the velocity lane.
    # Hear notes would open a real output stream on each drawn note, which the direct
    # _next_block checks below must not share (smoke_note_tools covers audition).
    # Keep these fixed note coordinates in the full roll; smoke_velocity covers
    # the lane and its reduced note area.
    w.apply_preferences(dict(w.preferences, roll_hear_notes=False, roll_velocity_lane=False)); assert not view.roll_hear_notes
    QTest.qWait(50)
    # R11: real keyboard/mouse events and real menu actions, each undoable.
    w.activateWindow(); view.setFocus(); app.processEvents(); w.commit_history()
    marker_baseline = w.project
    w.seek_song(float(w.project.seconds(2.)))
    view.scroll = 12.
    w.tempo_changed(90.)
    assert view.playhead == 2. and view.scroll == 12.
    w.tempo_changed(marker_baseline.bpm)
    w.seek_song(0.)
    assert view.scroll == 0.
    w.seek_song(float(w.project.seconds(2.)))
    QTest.keyClick(view, Qt.Key.Key_M); app.processEvents(); w.commit_history()
    assert w.project.markers == ((2., 'Section'),)
    w.undo(); assert w.project == marker_baseline
    QTest.keyClick(view, Qt.Key.Key_M); app.processEvents(); w.commit_history()
    one_marker = w.project
    # The real press handler only queues the menu; it opens after release.
    menu_requests = []
    with patch.object(view, 'marker_menu', lambda *args: menu_requests.append(args)):
        point = QPoint(round(view.x_of(2.)) + 2, cv.RULER // 2)
        QTest.mousePress(view, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)
        app.processEvents(); assert not menu_requests
        QTest.mouseRelease(view, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)
        app.processEvents(); assert len(menu_requests) == 1 and menu_requests[0][0] == 0
    w.seek_song(0.)
    point = QPoint(round(view.x_of(2.)) + 2, cv.RULER // 2)
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    assert view.playhead == 2. and w.project == one_marker
    end = QPoint(point.x() + round(4. * view.scale), point.y())
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    QTest.mouseMove(view, QPoint(point.x() + round(2. * view.scale), point.y()), 20)
    QTest.mouseMove(view, end, 20)
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    app.processEvents(); w.commit_history()
    assert w.project.markers == ((6., 'Section'),)
    w.undo(); assert w.project == one_marker
    # Patch only a module-level dialog name, never a Qt class method or QMenu.
    menu = view.build_marker_menu(0)
    with patch.object(cv, 'QInputDialog', SimpleNamespace(getText=lambda *a, **kw: ('Intro', True))):
        next(a for a in menu.actions() if a.text() == 'Rename…').trigger()
    menu.deleteLater(); app.processEvents(); w.commit_history()
    assert w.project.markers == ((2., 'Intro'),)
    w.undo(); assert w.project == one_marker
    menu = view.build_marker_menu(0)
    assert not next(a for a in menu.actions() if a.text() == 'Loop between here and next').isEnabled()
    next(a for a in menu.actions() if a.text() == 'Delete').trigger()
    menu.deleteLater(); app.processEvents(); w.commit_history()
    assert w.project.markers == ()
    w.undo(); assert w.project == one_marker
    menu = view.build_marker_menu(beat=8.)
    next(a for a in menu.actions() if a.text() == 'Add marker').trigger()
    menu.deleteLater(); app.processEvents(); w.commit_history()
    two_markers = w.project
    assert two_markers.markers == ((2., 'Section'), (8., 'Section'))
    # Moving past a neighbour keeps dragging the original marker after reordering.
    point = QPoint(round(view.x_of(2.)) + 2, cv.RULER // 2)
    end = QPoint(point.x() + round(8. * view.scale), point.y())
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    QTest.mouseMove(view, QPoint(point.x() + round(7. * view.scale), point.y()), 20)
    QTest.mouseMove(view, end, 20)
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    app.processEvents(); w.commit_history()
    assert w.project.markers == ((8., 'Section'), (10., 'Section'))
    w.undo(); assert w.project == two_markers
    menu = view.build_marker_menu(0)
    next(a for a in menu.actions() if a.text() == 'Loop between here and next').trigger()
    menu.deleteLater(); app.processEvents(); w.commit_history()
    assert w.project.loop and (w.project.loop_start, w.project.loop_end) == (2., 8.)
    w.undo(); assert w.project == two_markers
    w.undo(); assert w.project == one_marker
    w.undo(); assert w.project == marker_baseline
    w.seek_song(0.)
    # R10: each operation reaches Window.set_project and is restored by one undo.
    w.activateWindow(); view.setFocus(); app.processEvents(); w.commit_history()
    before = w.project
    w.seek_song(float(w.project.seconds(6.)))
    QTest.keyClick(view, Qt.Key.Key_S); app.processEvents(); w.commit_history()
    assert len(w.project.clips) == 2 and w.project.clips[0].end == 6.
    assert w.project.clips[1].start == 6. and w.project.clips[1].time_offset == 6.
    assert w.clip_index == 1
    w.undo(); assert w.project == before
    # Boundaries and gaps cannot split; the original selected clip need not be under the playhead.
    w.seek_song(0.); QTest.keyClick(view, Qt.Key.Key_S); assert w.project == before
    y = cv.RULER + cv.TRACK // 2
    press, end = QPoint(round(view.x_of(2.)), y), QPoint(round(view.x_of(22.)), y)
    QTest.keyPress(view, Qt.Key.Key_Alt)
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, press)
    QTest.mouseMove(view, QPoint(round(view.x_of(18.)), y), 20)
    QTest.mouseMove(view, end, 20)
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, end)
    QTest.keyRelease(view, Qt.Key.Key_Alt); app.processEvents(); w.commit_history()
    assert len(w.project.clips) == 2
    assert w.project.clips[0] == before.clips[0] and w.project.clips[1].start == 20.
    assert w.project.clips[1].settings == before.clips[0].settings
    if sys.platform != 'darwin': assert w.menuBar().activeAction() is None
    w.undo(); assert w.project == before
    # Inspect and trigger the real clip menu without exec (no patched Qt classes).
    menu, _ = view.build_clip_menu(0)
    submenu = next(a.menu() for a in menu.actions() if a.text() == 'Colour')
    assert len(submenu.actions()) == 9
    action = next(a for a in submenu.actions() if a.data() == 5)
    assert not action.icon().isNull()
    action.trigger(); menu.deleteLater(); app.processEvents()
    w.commit_history(); assert w.project.clips[0].color == 5
    w.undo(); assert w.project == before and w.project.clips[0].color is None
    from phosphor.display.crt_theme import PALETTES
    from PySide6.QtGui import QColor
    original_colors = view.colors
    for palette in PALETTES.values():
        view.colors = dict(original_colors, **palette)
        background = QColor(palette['background'])
        for color in range(8):
            tint = view.clip_color(color)
            # The swatches stay visibly brighter than every CRT timeline background.
            assert tint.lightness() - background.lightness() > 80
    view.colors = original_colors
    w.seek_song(0.); view.setFocus()
    # Change real preferences in memory only; Shift now zooms instead of scrolling.
    from phosphor.editor import wheel_mapping as wm
    from phosphor.editor.preference_panels import ControlsPreferences
    baseline_preferences = dict(w.preferences)
    controls = ControlsPreferences(baseline_preferences)
    controls.boxes['wheel_zoom'].setCurrentIndex(controls.boxes['wheel_zoom'].findData('shift'))
    wheel_values = {k: v for k, v in controls.values().items() if k in wm.ACTIONS}
    assert wheel_values == wm.validated_mapping(baseline_preferences)
    assert 'different modifiers' in controls.message.text()
    changed = dict(baseline_preferences, wheel_zoom='shift', wheel_scroll='control')
    controls.load(changed)
    assert controls.values() == dict(wm.validated_mapping(changed), metronome_level=changed['metronome_level'],
                                     wheel_sensitivity=changed['wheel_sensitivity'], trackpad_sensitivity=changed['trackpad_sensitivity'])
    w.apply_preferences(changed)
    pos = QPoint(cv.GUTTER + 100, view.roll_top + 40)
    scale, scroll, row = view.scale, view.scroll, view.row
    app.sendEvent(view, QWheelEvent(QPointF(pos), QPointF(view.mapToGlobal(pos)), QPoint(), QPoint(0, 120),
                                   Qt.MouseButton.NoButton, Qt.KeyboardModifier.ShiftModifier,
                                   Qt.ScrollPhase.NoScrollPhase, False))
    assert view.scale > scale and view.row == row
    w.show_shortcuts()
    assert any(w.shortcuts_window.table.item(r, 1).text() == ('⇧wheel' if sys.platform == 'darwin' else 'Shift+wheel')
               and w.shortcuts_window.table.item(r, 0).text() == 'Zoom time around pointer'
               for r in range(w.shortcuts_window.table.rowCount()))
    w.shortcuts_window.close(); controls.deleteLater()
    w.apply_preferences(baseline_preferences)
    view.scale, view.scroll = scale, scroll
    # The actual Controls page previews edits and Cancel restores the baseline.
    w.show_preferences()
    dialog = w.preferences_dialog
    page = dialog.findChild(ControlsPreferences)
    box = page.boxes['wheel_zoom']; box.setCurrentIndex(box.findData('none'))
    assert w.preferences['wheel_zoom'] == 'none' and view.wheel_mapping['wheel_zoom'] == 'none'
    dialog.reject(); app.processEvents()
    assert w.preferences == baseline_preferences
    # Real window shortcuts, with audio-device start/stop replaced by an in-memory state.
    playing = [False]
    def start_audio(*args): playing[0] = True
    def stop_audio(): playing[0] = False
    with patch.object(type(w.audio), 'running', new_callable=PropertyMock, side_effect=lambda:playing[0]), \
         patch.object(w.audio, 'start', side_effect=start_audio) as start, \
         patch.object(w.audio, 'stop', side_effect=stop_audio), \
         patch.object(q, 'settle_garbage'):
        w.activateWindow(); view.setFocus(); app.processEvents()
        QTest.keyClick(view, Qt.Key.Key_Space); assert playing[0] and start.call_count == 1
        QTest.keyClick(view, Qt.Key.Key_Space); assert not playing[0]
        # A focused button must not also receive Space and click itself.
        button = w.composer_panel.play; button.setFocus()
        QTest.keyClick(button, Qt.Key.Key_Space); assert playing[0] and start.call_count == 2
        QTest.keyClick(button, Qt.Key.Key_Space); assert not playing[0]
        bpm = w.composer_panel.bpm; before = bpm.value(); bpm.setFocus()
        QTest.keyClick(bpm, Qt.Key.Key_Space)
        assert not playing[0] and bpm.value() == before and start.call_count == 2
        field = QLineEdit(w.centralWidget()); field.show(); field.setFocus()
        QTest.keyClicks(field, 'a b'); assert field.text() == 'a b' and not playing[0]
        # Home/End stay with the field, not the transport. (Only Windows moves the cursor
        # with them: macOS line edits use Command-Left/Right.)
        w.seek_song(float(w.project.seconds(3.))); held = w.playhead_beats
        QTest.keyClick(field, Qt.Key.Key_Home); assert w.playhead_beats == held
        assert sys.platform == 'darwin' or field.cursorPosition() == 0
        QTest.keyClick(field, Qt.Key.Key_End); assert w.playhead_beats == held
        assert sys.platform == 'darwin' or field.cursorPosition() == 3
        field.hide(); field.deleteLater(); view.setFocus()
        original = w.project
        w.set_project(replace(original, loop=True, loop_start=2., loop_end=8.))
        w.seek_song(float(w.project.seconds(5.)))
        QTest.keyClick(view, Qt.Key.Key_Home); assert abs(w.playhead_beats - 2.) < 1e-9
        QTest.keyClick(view, Qt.Key.Key_End); assert abs(w.playhead_beats - w.project.end_beats) < 1e-9
        w.set_project(replace(original, loop=False))
        QTest.keyClick(view, Qt.Key.Key_Home); assert w.playhead_beats == 0.
        QTest.keyClick(view, Qt.Key.Key_Space); assert playing[0]
        QTest.keyClick(view, Qt.Key.Key_End)
        assert not playing[0] and abs(w.playhead_beats - w.project.end_beats) < 1e-9
        with patch.object(w, 'building', True):
            QTest.keyClick(view, Qt.Key.Key_Space); assert not playing[0]
            QTest.keyClick(view, Qt.Key.Key_Home)
            assert abs(w.playhead_beats - w.project.end_beats) < 1e-9
        w.set_workspace(SANDBOX); w.composer_panel.hide(); w.bar_buttons[q.PANEL_SCENE].setFocus()
        QTest.keyClick(app.focusWidget(), Qt.Key.Key_Space); assert playing[0]
        calls = start.call_count
        QTest.keyClick(app.focusWidget(), Qt.Key.Key_Home)
        assert playing[0] and start.call_count == calls + 1
        QTest.keyClick(app.focusWidget(), Qt.Key.Key_End)
        assert playing[0] and start.call_count == calls + 1
        QTest.keyClick(app.focusWidget(), Qt.Key.Key_Space); assert not playing[0]
        w.set_workspace(COMPOSER); w.set_project(original); w.seek_song(0.); view.setFocus()
    if sys.platform != 'darwin':
        # Height gestures must not hand subsequent editing keys to the menu bar.
        original_row, original_block, original_pitch = view.row, view.block, view.top_pitch
        for roll, attribute in ((cv.NOTE_ROLL, 'row'), (cv.TRANSFORM_ROLL, 'block')):
            view.set_roll(roll); view.setFocus()
            before = getattr(view, attribute)
            pos = QPoint(cv.GUTTER + 100, view.roll_top + cv.LANES_TOP + 40)
            QTest.keyPress(w, Qt.Key.Key_Alt)
            # Windows maps Alt + vertical wheel to angleDelta().x().
            for delta in (QPoint(120, 0), QPoint(0, 120)):
                app.sendEvent(view, QWheelEvent(QPointF(pos), QPointF(view.mapToGlobal(pos)),
                                               QPoint(), delta, Qt.MouseButton.NoButton,
                                               Qt.KeyboardModifier.AltModifier, Qt.ScrollPhase.NoScrollPhase, False))
            QTest.keyRelease(w, Qt.Key.Key_Alt); app.processEvents()
            assert getattr(view, attribute) > before, (roll, before, getattr(view, attribute))
            assert w.menuBar().activeAction() is None
            assert app.focusWidget() is not w.menuBar()

            # The consumed release must not disable the next plain Alt gesture.
            QTest.keyPress(w, Qt.Key.Key_Alt); QTest.keyRelease(w, Qt.Key.Key_Alt)
            app.processEvents()
            assert w.menuBar().activeAction() is not None
            QTest.keyClick(w.menuBar(), Qt.Key.Key_Escape); app.processEvents()
            assert w.menuBar().activeAction() is None
        view.row, view.block, view.top_pitch = original_row, original_block, original_pitch
        # Alt must suppress menu activation when assigned zoom or time scroll too.
        for action, mapping in (
                ('zoom', dict(baseline_preferences, wheel_zoom='alt', wheel_height='control')),
                ('scroll', dict(baseline_preferences, wheel_scroll='alt', wheel_height='shift'))):
            w.apply_preferences(mapping)
            view.scroll = 10.; before_scale, before_scroll = view.scale, view.scroll
            pos = QPoint(cv.GUTTER + 100, 8)
            QTest.keyPress(w, Qt.Key.Key_Alt)
            app.sendEvent(view, QWheelEvent(QPointF(pos), QPointF(view.mapToGlobal(pos)), QPoint(), QPoint(120, 0),
                                           Qt.MouseButton.NoButton, Qt.KeyboardModifier.AltModifier,
                                           Qt.ScrollPhase.NoScrollPhase, False))
            QTest.keyRelease(w, Qt.Key.Key_Alt); app.processEvents()
            assert w.menuBar().activeAction() is None
            assert view.scale > before_scale if action == 'zoom' else view.scroll < before_scroll
        w.apply_preferences(baseline_preferences)
        view.scale, view.scroll = scale, scroll
        view.set_roll(cv.NOTE_ROLL); view.setFocus()
    def point(beats, pitch):
        y = round(view.y_of(pitch) + view.row / 2); assert view.roll_top <= y < view.note_bottom(), 'note-roll click outside the visible roll'
        return QPoint(round(view.x_of(beats) + 2), y)
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(1, 60))
    assert w.project.notes == (Note(1., .25, 60),), w.project.notes
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(2, 64))
    QTest.mouseMove(view, point(3.6, 64)); QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(3.6, 64))
    assert w.project.notes[1].start == 2 and w.project.notes[1].length == 1.75, w.project.notes
    menu = view.build_note_menu(0)
    next(a for a in menu.actions() if a.text() == 'Delete notes').trigger()
    menu.deleteLater()
    assert [n.pitch for n in w.project.notes] == [64]
    QTest.mouseDClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(view.x_of(17.)), cv.RULER + 10))
    assert [c.start for c in w.project.clips] == [0., 16.] and w.clip_index == 1 and w.scene.settings.shapes == ()
    edge = QPoint(round(view.x_of(32.) - 2), cv.RULER + 10)
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, edge)
    QTest.mouseMove(view, QPoint(round(view.x_of(24.)), cv.RULER + 10)); QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(view.x_of(24.)), cv.RULER + 10))
    assert w.project.clips[1].length == 8., w.project.clips[1]
    w.add_object('Tilting circle'); w.select_clip(0); assert w.scene.settings.shapes[0].solid == 'Cube'
    w.select_clip(1); assert w.scene.settings.shapes[0].solid == 'Tilting circle'
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(view.x_of(2.)), 8))
    assert abs(w.playhead_beats - 2.) < .05

    # View actions frame song/notes/clip without editing the document or moving the playhead.
    original = w.project
    w.set_project(replace(original, notes=(Note(6., 1., 60), Note(9., 2., 64), Note(80., 4., 67))))
    before, playhead = w.project, w.playhead_beats
    view.scroll, view.scale = 50., 400.
    w.activateWindow(); view.setFocus(); app.processEvents()
    QTest.keyClick(view, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
    assert view.scroll == 0. and cv.GUTTER <= view.x_of(0.)
    assert view.width() - 60 <= view.x_of(w.project.end_beats) <= view.width() - 20
    song_scale = view.scale
    view.note_selection = {0, 1}
    w.zoom_selection_action.trigger()
    assert view.scale > song_scale and view.scroll > 0.
    assert cv.GUTTER + 20 <= view.x_of(6.) <= cv.GUTTER + 28
    assert view.width() - 28 <= view.x_of(11.) <= view.width() - 20
    assert view.x_of(80.) > view.width()  # Unselected notes do not expand the fit.
    view.note_selection = set()
    w.zoom_selection_action.trigger()
    assert cv.GUTTER <= view.x_of(view.clip.start) <= cv.GUTTER + 28
    assert view.x_of(view.clip.end) <= view.width() - 20
    assert w.project is before and w.playhead_beats == playhead
    w.set_project(original); view.scroll, view.scale = 0., 40.

    # Playback blocks come from the song: the audio engine renders the note under each clip.
    w.set_project(replace(w.project, notes=(Note(0., 8., 57), Note(16., 8., 45))))
    settings = replace(w.output_settings(), source='Single tone')
    w.audio.parameters = (settings, 'drawing'); w.audio.composer = (w.project, 0.); w.audio.frames = 0
    block = w.audio._next_block(4800)
    expected = np.clip(render_song(np.arange(4800) / settings.sample_rate, w.project, settings), -1, 1)
    np.testing.assert_allclose(block[240:], expected[240:], atol=1e-6)  # After the 5 ms start ramp.
    assert np.abs(block).max() > .05
    # Monitoring clicks use the real playback block generator, without a device.
    from phosphor.engine.metronome import TapTempo, mix_clicks
    from phosphor.engine.composition import song_seconds
    saved_project = w.project
    quiet = replace(w.project, notes=(), loop=True, loop_start=4., loop_end=8.)
    w.audio.composer = (quiet, 3.99); w.audio.frames = 0
    w.composer_panel.metronome.setChecked(False)
    np.testing.assert_array_equal(w.audio._next_block(2400), 0)
    w.composer_panel.metronome.setChecked(True)
    click_block = w.audio._next_block(2400)
    positions = song_seconds(quiet, 3.99, np.arange(2400) / settings.sample_rate)
    expected_clicks = mix_clicks(np.zeros((2400, 2)), positions, quiet.bpm,
                                settings.sample_rate, w.audio.metronome_level)
    np.testing.assert_allclose(click_block, expected_clicks, atol=1e-7)
    assert np.max(abs(click_block)) > .05
    w.composer_panel.metronome.setChecked(False)
    # Count-in API: fake only the Python audio start, never Qt class methods.
    w.seek_song(.5)
    w.composer_panel.count_in.setCurrentIndex(1)
    with patch.object(w.audio, 'start'):
        w.start_song()
    assert w.play_start == .5 and w.audio.count_in_seconds == float(w.project.seconds(4.))
    w.audio.frames = 0
    pre_roll = w.audio._next_block(1000)
    np.testing.assert_allclose(pre_roll, mix_clicks(np.zeros_like(pre_roll),
        np.arange(1000) / settings.sample_rate - w.audio.count_in_seconds,
        w.project.bpm, settings.sample_rate, w.audio.metronome_level), atol=1e-7)
    with patch.object(w.audio, 'start'):
        w.start_song(count_in=2)
    assert w.audio.count_in_seconds == float(w.project.seconds(8.))
    w.audio.count_in_seconds = 0.; w.composer_panel.count_in.setCurrentIndex(0)
    w.audio.composer = None
    # Synthetic tap times drive the real button and existing tempo edit path.
    taps = iter((0., .6, 1.2))
    w.tap_clock = TapTempo(lambda: next(taps))
    w.composer_panel.tap.click(); assert w.project.bpm == saved_project.bpm
    w.composer_panel.tap.click(); assert abs(w.project.bpm - 100.) < 1e-8
    assert w.composer_panel.bpm.value() == 100.
    w.composer_panel.tap.click(); assert abs(w.project.bpm - 100.) < 1e-8
    w.set_project(saved_project)
    w.audio.composer = None
    w.tick(); QTest.qWait(30)
    assert w.host.scope.signal is not None and np.ptp(w.host.scope.signal, axis=0).max() > .01  # Stopped preview at the playhead.

    # Files: the project embeds every clip's scene; the Sandbox scene is separate.
    path = root / 'song.project.json'
    with patch.object(q.QFileDialog, 'getSaveFileName', return_value=(str(path), '')): assert w.save_project()
    assert not w.project_dirty and w.windowTitle().startswith('song.project.json')
    saved = w.project
    w.set_workspace(SANDBOX)
    assert w.scene is w.sandbox_scene and w.scene.settings.shapes == () and not w.composer_panel.isVisible()
    assert w.open_action.text() == 'Open scene…' and w.bar_buttons[q.PANEL_INPUT].isEnabled()
    assert project_files.read_workspace(root / 'workspace.json') == SANDBOX
    w.add_object('Octahedron'); w.send_to_composer()
    assert w.project.clips[-1].settings.shapes[0].solid == 'Octahedron' and w.project.clips[-1].start == 24.
    w.set_workspace(COMPOSER); assert w.scene is w.clip_scene and w.composer_panel.isVisible()
    with patch.object(q.QFileDialog, 'getOpenFileName', return_value=(str(path), '')), \
         patch.object(q.QMessageBox, 'question', return_value=q.QMessageBox.StandardButton.Discard):
        w.open_project()
    assert w.project == saved and not w.project_dirty and w.clip_index == 0
    assert w.sandbox_scene.settings.shapes[0].solid == 'Octahedron'  # Opening a project leaves the Sandbox scene alone.

    # Trace only hides the timeline; leaving it brings it back.
    w.set_immersive(True); assert not w.composer_panel.isVisible() and not w.top_row.isVisible()
    w.set_immersive(False); QTest.qWait(50); assert w.composer_panel.isVisible() and w.top_row.isVisible()
    # Transport and panels fit at supported native window sizes.
    # One transport row needs about 1610 px since R12's metronome controls and about
    # 1680 px since 0.53.0's Cycle toggle (panel 36 px narrower than the window) and about
    # 1785 px since 0.62.0's Notes menu button; 1800 checks it.
    for width, height in ((1000, 700), (1220, 840), (1800, 1000)):
        w.resize(width, height); QTest.qWait(100); app.processEvents()
        panel = w.composer_panel
        if width in (1000, 1800):
            assert panel.clip_row_wrapped == (width == 1000), (width, panel.width())
        for widget in panel.findChildren(QWidget):
            if not widget.isVisible() or widget is panel.timeline or panel.timeline.isAncestorOf(widget):
                continue
            if isinstance(widget.parent(), QAbstractSpinBox):
                continue  # Qt's own parts; their generic hints are not what they show (wider on macOS).
            assert widget.width() >= widget.sizeHint().width() - 1, (width, widget, widget.width(), widget.sizeHint())
            if isinstance(widget, QAbstractSpinBox):  # The widest value, suffix included, must not be clipped.
                metrics = widget.lineEdit().fontMetrics()
                widest = max(metrics.horizontalAdvance(widget.textFromValue(v)+widget.suffix()) for v in (widget.minimum(), widget.maximum()))
                assert widget.lineEdit().width() >= widest, (width, widget.suffix(), widget.lineEdit().width(), widest)
            rect = widget.rect().translated(widget.mapTo(panel, QPoint(0, 0)))
            assert panel.rect().contains(rect), (width, widget, rect, panel.rect())
        # The Snap box shows its widest entry whole (macOS cut "1/16" to "1/1…").
        snap = panel.snap; widest = max(snap.fontMetrics().horizontalAdvance(snap.itemText(i)) for i in range(snap.count()))
        assert snap.width() >= widest + cv.COMBO_CHROME, (width, snap.width(), widest)
        assert w.status.height() <= w.status.fontMetrics().height() * 1.5
        assert w.status.text() and w.status.text() in w.status.toolTip()
        for name in q.PANELS:
            if not w.bar_buttons[name].isEnabled():
                continue
            w.show_panel(name); QTest.qWait(50); app.processEvents()
            for scroll in w.sidebar.findChildren(QScrollArea):
                if scroll.isVisible():
                    assert scroll.horizontalScrollBar().maximum() == 0, (width, name)

    w.project_dirty = False; w.sandbox_scene.saved = w.sandbox_scene.document()
    w.release_views(); w.close()
print('Composer passed: workspace switch, clip scenes, timeline editing, playback blocks, project files.')
