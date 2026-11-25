"""Composer Audio track (A2): drop, waveform, move/trim/fade drags, menus, gain lane, meter and Undo.

The WAV is generated in a temporary folder; no audio fixture is committed. Native or --headless.
"""
import _path  # noqa: F401
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile
from PySide6.QtCore import Qt, QPoint, QPointF, QMimeData, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from phosphor.editor import qt_app as q
from phosphor.editor.composer_view import AUDIO_TRACK, GAIN_LANE, ADD_TRACK_ROW
from phosphor.engine import audio_clips
from phosphor.engine.composition import COMPOSER, export_song, render_song

q.configure_graphics()
app = QApplication([])
RATE = 48000


def drop(view, paths, x, y):
    """Drag enter, move and drop as a file manager would; returns whether the drop was accepted."""
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    action, buttons, modifiers = Qt.DropAction.CopyAction, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    enter = QDragEnterEvent(QPoint(round(x), round(y)), action, mime, buttons, modifiers)
    app.sendEvent(view, enter)
    if not enter.isAccepted(): return False
    move = QDragMoveEvent(QPoint(round(x), round(y)), action, mime, buttons, modifiers)
    app.sendEvent(view, move)
    if not move.isAccepted(): return False
    event = QDropEvent(QPointF(x, y), action, mime, buttons, modifiers)
    app.sendEvent(view, event)
    return event.isAccepted()


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    wav = root / 'beat.wav'
    t = np.arange(RATE) / RATE  # One second: two beats at 120 BPM.
    soundfile.write(wav, np.column_stack((.5*np.sin(2*np.pi*110*t), .5*np.cos(2*np.pi*165*t))), RATE, subtype='FLOAT')
    loud = root / 'loud.flac'
    soundfile.write(loud, .9*np.sin(2*np.pi*55*t[:RATE//2]), RATE)
    text = root / 'notes.txt'; text.write_text('not audio', encoding='utf-8')
    with patch.object(q, 'write_workspace', lambda *args: None), patch.object(q.Window, 'remember_file', lambda *args: None):
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
        try:
            w.resize(1220, 840); w.show(); w.activateWindow(); app.processEvents(); w.timer.stop()
            total = sum(w.vsplit.sizes()); w.vsplit.setSizes([total - 560, 560]); app.processEvents()  # A taller timeline: rows fit.
            w.set_project(q.replace(w.project, bpm=120.)); w.commit_history()
            panel = w.composer_panel; view = panel.timeline
            view.roll_hear_notes = False
            view.set_roll_option('roll_audio_gain_lane', False)
            view.scale, view.scroll = 60., 0.
            row = view.audio_top + AUDIO_TRACK / 2
            assert view.roll_top == view.audio_top + AUDIO_TRACK  # No tracks yet: no Add row.
            assert not view.grab().isNull()  # Empty track with its hint.

            # Drop: only audio files, only on the Audio track; several files go one after another.
            assert not drop(view, [text], view.x_of(4.), row)
            assert not drop(view, [wav], view.x_of(4.), view.roll_top + 40)
            assert not w.project.audio_clips
            assert drop(view, [wav], view.x_of(4.) + 2, row)
            assert len(w.project.audio_clips) == 1 and w.project.audio_clips[0].start == 4., w.project.audio_clips
            assert w.history.undo_label == 'Add audio clip' and view.selected_audio == 0
            assert drop(view, [wav, loud], view.x_of(10.), row)
            assert [c.start for c in w.project.audio_clips] == [4., 10., 12.], [c.start for c in w.project.audio_clips]
            assert w.history.undo_label == 'Add audio clips'
            w.undo(); assert len(w.project.audio_clips) == 1
            w.redo(); assert len(w.project.audio_clips) == 3
            assert not view.grab().isNull()
            assert audio_clips.waveform(w.project.audio_clips[0].path, w.project.audio_clips[0].fingerprint) is not None
            assert view.audio_loud(w.project.audio_clips[2]) is False
            w.undo(); app.processEvents()

            def gesture(a, b, modifier=Qt.KeyboardModifier.NoModifier, label=None):
                view.setFocus(); w.commit_history(); size = w.history._index
                QTest.mousePress(view, Qt.MouseButton.LeftButton, modifier, a)
                QTest.mouseMove(view, b, 30)
                QTest.mouseRelease(view, Qt.MouseButton.LeftButton, modifier, b)
                w.commit_history(); app.processEvents()
                assert w.history._index == size + 1, ('one step per drag', w.history.undo_label, size, [e[0] for e in w.history._entries])
                if label: assert w.history.undo_label == label, w.history.undo_label

            def at(beat, y=row): return QPoint(round(view.x_of(beat)), round(y))
            clip = w.project.audio_clips[0]
            # A click selects without moving anything.
            view.setFocus(); w.commit_history(); size = w.history._index
            QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(5.))
            w.commit_history(); assert w.history._index == size and view.focus_kind == 'audio'
            gesture(at(5.), at(7.), label='Move audio clip')
            assert w.project.audio_clips[0].start == 6., w.project.audio_clips[0]
            right = view.audio_rect(w.project.audio_clips[0]).right()
            gesture(QPoint(round(right) - 2, round(row)), at(7.), label='Trim audio clip')
            assert abs(w.project.audio_clips[0].duration - .5) < 1e-9, w.project.audio_clips[0]
            left = view.audio_rect(w.project.audio_clips[0]).left()
            gesture(QPoint(round(left) + 2, round(row)), at(6.5), label='Trim audio clip')
            trimmed = w.project.audio_clips[0]
            assert (trimmed.start, abs(trimmed.offset - .25) < 1e-9, abs(trimmed.duration - .25) < 1e-9) == (6.5, True, True), trimmed
            left = view.audio_rect(trimmed).left()
            gesture(QPoint(round(left) + 2, round(row)), at(6.), label='Trim audio clip')  # Back out to the file start.
            top = view.audio_rect(w.project.audio_clips[0]).top() + 2
            gesture(QPoint(round(view.x_of(6.)) + 1, round(top)), QPoint(round(view.x_of(6.25)), round(top)), label='Fade audio clip')
            assert abs(w.project.audio_clips[0].fade_in - .125) < 1e-6, w.project.audio_clips[0]
            gesture(at(6.4), at(9.), Qt.KeyboardModifier.AltModifier, label='Duplicate audio clip')
            starts = [c.start for c in w.project.audio_clips]
            assert starts[0] == 6. and abs(starts[1] - 8.6) <= .125 and starts[1] % .0625 == 0, starts
            w.undo(); assert len(w.project.audio_clips) == 1

            # Right-click menu: split, duplicate, routing, delete; each one Undo step.
            view.select_audio(0); w.seek_song(float(w.project.seconds(6.5))); app.processEvents()
            view.set_playhead(6.5)
            menu = view.build_audio_menu(0)
            assert menu.split.isEnabled()
            menu.split.trigger(); w.commit_history()
            assert len(w.project.audio_clips) == 2 and w.history.undo_label == 'Split audio clip'
            assert w.project.audio_clips[1].start == 6.5 and view.selected_audio == 1
            routing = next(a for a in view.build_audio_menu(1).routing.actions() if a.data() == 'x')
            routing.trigger(); w.commit_history()
            assert w.project.audio_clips[1].routing == 'x' and w.history.undo_label == 'Change audio routing'
            view.build_audio_menu(0).actions()[1].trigger(); w.commit_history()  # Duplicate.
            assert w.history.undo_label == 'Duplicate audio clip' and len(w.project.audio_clips) == 3
            view.build_audio_menu(2).delete.trigger(); w.commit_history()
            assert w.history.undo_label == 'Delete audio clip' and len(w.project.audio_clips) == 2
            # Split shortcut and Delete key act on the selected audio clip while it has the focus.
            view.select_audio(1); view.set_playhead(6.75); view.setFocus()
            view.split_action.trigger(); w.commit_history()
            assert len(w.project.audio_clips) == 3 and w.history.undo_label == 'Split audio clip'
            QTest.keyClick(view, Qt.Key.Key_Delete); w.commit_history()
            assert len(w.project.audio_clips) == 2 and w.history.undo_label == 'Delete audio clip'

            # Gain lane: shown under the track; click adds a node, a drag moves it.
            view.audio_gain_button.click(); app.processEvents()
            assert view.roll_audio_gain_lane and w.preferences['roll_audio_gain_lane']
            assert view.roll_top == view.audio_top + AUDIO_TRACK + GAIN_LANE + ADD_TRACK_ROW
            lane = view.gain_lane_rect(); view.gain_strip.set_rect(lane)
            view.select_audio(0)
            point = QPoint(round(view.x_of(6.25)), round(view.gain_strip.y_of(.5)))
            gesture(point, point, label='Edit audio gain lane')
            points = w.project.audio_clips[0].gain_points
            assert len(points) == 3 and abs(points[1][1] - .5) < .02 and points[1][0] == .125, points
            gesture(point, QPoint(point.x(), round(view.gain_strip.y_of(.2))), label='Edit audio gain lane')
            assert abs(w.project.audio_clips[0].gain_points[1][1] - .2) < .02
            view.build_gain_menu(0, 1).actions()[0].trigger(); w.commit_history()  # Delete node.
            assert len(w.project.audio_clips[0].gain_points) == 2
            assert not view.grab().isNull()
            view.audio_gain_button.click(); assert not view.roll_audio_gain_lane

            # Meter: the lamp latches above full scale; a click on it resets.
            assert view.set_meter(1.4) and view.meter_clipped
            assert not view.set_meter(.5) and view.meter_clipped
            QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.clip_lamp_rect().center().toPoint())
            assert not view.meter_clipped
            assert view.audio_tooltip(10, 10)[0].startswith('Output meter')

            # Playback equals export with the edited clips.
            path = root / 'song.wav'
            machine = w.output_settings()
            export_song(path, w.project, machine, format='WAV', depth=32, rate=RATE)
            samples, _ = soundfile.read(path, dtype='float32', always_2d=True)
            from dataclasses import replace
            expected = render_song(np.arange(len(samples)) / RATE, w.project, replace(machine, sample_rate=RATE)).astype('float32')
            assert np.array_equal(samples, expected)

            # Undo everything back to the empty track, then Redo.
            final = w.project
            while w.history.can_undo: w.undo()
            assert not w.project.audio_clips
            while w.history.can_redo: w.redo()
            assert w.project == final

            # The transport row still fits on one line at 1800 px with the Audio track.
            w.resize(1800, 1000); app.processEvents()
            assert not panel.clip_row_wrapped, panel.width()
            assert not view.grab().isNull()
        finally:
            w.history_timer.stop(); w.timer.stop(); w.force_close = True
            w.shutdown_audio(); w.release_views(); w.close()
print('Audio track passed: drop, waveform, move/trim/fade/copy drags, menus, split/delete keys, gain lane, meter, export and Undo.')
