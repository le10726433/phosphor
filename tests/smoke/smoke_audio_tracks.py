"""Composer audio tracks (AW1): add, drop onto tracks and the Add row, move clips across tracks,
header mute/solo/gain, collapse, Shift and Ctrl/Cmd selection, reorder, copy/paste/duplicate,
Undo (one step per edit and per drag), save and reopen, playback equals export.

The WAVs are generated in a temporary folder; no audio fixture is committed. Native or --headless.
"""
import _path  # noqa: F401
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile
from PySide6.QtCore import Qt, QPoint, QPointF, QMimeData, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from phosphor.editor import qt_app as q
from phosphor.editor.composer_view import AUDIO_TRACK, COLLAPSED_TRACK, ADD_TRACK_ROW
from phosphor.engine import audio_clips
from phosphor.engine.composition import COMPOSER, export_song, render_song
from phosphor.storage.project_files import read_project, save_project

q.configure_graphics()
app = QApplication([])
RATE = 48000
CTRL = Qt.KeyboardModifier.MetaModifier if sys.platform == 'darwin' else Qt.KeyboardModifier.ControlModifier


def drop(view, paths, x, y):
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    action, buttons, modifiers = Qt.DropAction.CopyAction, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    for kind in (QDragEnterEvent, QDragMoveEvent):
        event = kind(QPoint(round(x), round(y)), action, mime, buttons, modifiers); app.sendEvent(view, event)
        if not event.isAccepted(): return False
    event = QDropEvent(QPoint(round(x), round(y)), action, mime, buttons, modifiers); app.sendEvent(view, event)
    return event.isAccepted()


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    t = np.arange(RATE) / RATE  # One second: two beats at 120 BPM.
    files = []
    for k, (f, g) in enumerate(((110, 165), (220, 330), (55, 82.5))):
        path = root / f'tone{k}.wav'
        soundfile.write(path, np.column_stack((.3*np.sin(2*np.pi*f*t), .3*np.sin(2*np.pi*g*t))), RATE, subtype='FLOAT')
        files.append(path)
    with patch.object(q, 'write_workspace', lambda *args: None), patch.object(q.Window, 'remember_file', lambda *args: None):
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
        try:
            w.resize(1220, 840); w.show(); w.activateWindow(); app.processEvents(); w.timer.stop()
            total = sum(w.vsplit.sizes()); w.vsplit.setSizes([total - 560, 560]); app.processEvents()  # A taller timeline: rows fit.
            w.set_project(replace(w.project, bpm=120.)); w.commit_history()
            panel = w.composer_panel; view = panel.timeline
            view.roll_hear_notes = False
            view.set_roll_option('roll_audio_gain_lane', False)
            view.scale, view.scroll = 40., 0.
            assert not w.project.audio_tracks and len(view.track_rows()) == 1
            questions = []
            w.ask = lambda title, text: questions.append(text) or True

            def steps(action, label=None):
                """Run action as one gesture; assert it is exactly one Undo step (named label)."""
                view.setFocus(); w.commit_history(); size = w.history._index
                action(); w.commit_history(); app.processEvents()
                assert w.history._index == size + 1, ('one step', w.history.undo_label, [e[0] for e in w.history._entries][-4:])
                if label: assert w.history.undo_label == label, (w.history.undo_label, label)

            def row_y(track): row = view.track_row(track); return row.top + row.height / 2

            def drag(a, b, modifier=Qt.KeyboardModifier.NoModifier):
                QTest.mousePress(view, Qt.MouseButton.LeftButton, modifier, a)
                QTest.mouseMove(view, QPoint((a.x() + b.x()) // 2, (a.y() + b.y()) // 2), 20)
                QTest.mouseMove(view, b, 20)
                QTest.mouseRelease(view, Qt.MouseButton.LeftButton, modifier, b)

            # The first drop makes 'Audio 1'; the Add row makes tracks (by click and by drop).
            steps(lambda: drop(view, [files[0]], view.x_of(0.) + 1, row_y(0)), 'Add audio clip')
            assert [x.name for x in w.project.audio_tracks] == ['Audio 1'] and w.project.audio_clips[0].track == 0
            add = view.add_row_rect()
            steps(lambda: QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                                           QPoint(round(view.x_of(4.)), round(add.center().y()))), 'Add audio track')
            assert [x.name for x in w.project.audio_tracks] == ['Audio 1', 'Audio 2'] and view.track_picks == {1}
            steps(lambda: drop(view, [files[1]], view.x_of(4.) + 1, row_y(1)), 'Add audio clip')
            steps(lambda: drop(view, [files[2]], view.x_of(8.) + 1, view.add_row_rect().center().y()))
            tracks = w.project.audio_tracks
            assert len(tracks) == 3 and [c.track for c in w.project.audio_clips] == [0, 1, 2], w.project.audio_clips
            assert view.roll_top == view.audio_top + 3 * AUDIO_TRACK + ADD_TRACK_ROW
            assert not view.grab().isNull()

            # Drag a clip from track 0 onto track 1 (one step), then Undo.
            clip0 = w.project.audio_clips[0]
            steps(lambda: drag(QPoint(round(view.x_of(.5)), round(row_y(0))), QPoint(round(view.x_of(.5)), round(row_y(1)))), 'Move audio clip')
            moved = [c for c in w.project.audio_clips if c.path == clip0.path]
            assert len(moved) == 1 and moved[0].track == 1 and moved[0].start == 0., moved
            w.undo(); assert [c.track for c in w.project.audio_clips] == [0, 1, 2]

            # Header: mute, solo (runtime), gain drag (one step), double-click gain and rename via the menu.
            row = view.track_row(0)
            steps(lambda: QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                                           view.mute_rect(row).center().toPoint()), 'Mute audio track')
            assert w.project.audio_tracks[0].muted and view.audio_silenced(0)
            w.undo(); assert not w.project.audio_tracks[0].muted
            QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.solo_rect(row).center().toPoint())
            assert w.track_solos == {0} and view.audio_track_solos == {0}
            soloed = w.output_project()
            assert all(c.muted for c in soloed.clips) and [c.muted for c in soloed.audio_clips] == [False, True, True]
            assert not any(c.muted for c in w.project.audio_clips)  # Solo is never saved.
            QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, view.solo_rect(row).center().toPoint())
            assert not w.track_solos and w.output_project() is w.project
            gain = view.track_gain_rect(row).center().toPoint()
            steps(lambda: drag(gain, gain + QPoint(0, 30)), 'Change audio track gain')
            assert abs(w.project.audio_tracks[0].gain - .7) < 1e-9, w.project.audio_tracks[0]
            view.ask_text = lambda title, label, text: ('  Drums  ', True)  # The view's instance hook for the dialog.
            steps(lambda: view.build_track_menu(0).rename.trigger(), 'Rename audio track')
            assert w.project.audio_tracks[0].name == 'Drums'

            # Collapse by right-click menu (a thin row; clips stay hit-testable), Expand back.
            steps(lambda: view.build_track_menu(1).collapse.trigger(), 'Collapse audio track')
            assert w.project.audio_tracks[1].collapsed and view.track_row(1).height == COLLAPSED_TRACK
            menu = view.build_audio_track_menu(6., 1)
            assert menu.collapse.objectName() == 'expand_audio_track'
            assert view.audio_hit(view.x_of(4.5), row_y(1))[0] is not None
            assert not view.grab().isNull()
            steps(lambda: menu.collapse.trigger(), 'Expand audio track')
            assert not w.project.audio_tracks[1].collapsed

            # Selection: click, Shift+click (range), Ctrl/Cmd+click (toggle).
            def header(track, modifier=Qt.KeyboardModifier.NoModifier):
                r = view.track_row(track); QTest.mouseClick(view, Qt.MouseButton.LeftButton, modifier, view.name_rect(r).center().toPoint())
            header(0); assert view.track_picks == {0} and view.focus_kind == 'tracks'
            header(2, Qt.KeyboardModifier.ShiftModifier); assert view.track_picks == {0, 1, 2}
            header(1, CTRL); assert view.track_picks == {0, 2}
            header(1, CTRL); header(0, CTRL); assert view.track_picks == {1, 2}

            # Reorder: drag tracks 1 and 2 above track 0 (one step), then Move down from the menu.
            names = [x.name for x in w.project.audio_tracks]
            steps(lambda: drag(view.name_rect(view.track_row(1)).center().toPoint(), QPoint(20, round(view.audio_top + 4))), 'Move audio tracks')
            assert [x.name for x in w.project.audio_tracks] == [names[1], names[2], names[0]], w.project.audio_tracks
            assert view.track_picks == {0, 1}
            assert [w.project.audio_tracks[c.track].name for c in w.project.audio_clips if c.path == str(files[0].resolve())] == ['Drums']
            w.undo(); assert [x.name for x in w.project.audio_tracks] == names
            view.track_picks = {0}
            steps(lambda: view.build_track_menu(0).down.trigger(), 'Move audio track')
            assert [x.name for x in w.project.audio_tracks] == [names[1], names[0], names[2]]
            w.undo()

            # Copy and paste (the selected tracks with their clips and gain lanes), duplicate, delete.
            index = next(i for i, c in enumerate(w.project.audio_clips) if c.track == 1)
            from phosphor.engine import audio_edit
            w.set_project(audio_edit.set_gain_points(w.project, index, ((0., 1., 'linear'), (.5, .25, 'linear')))[0]); w.commit_history()
            header(0); header(1, Qt.KeyboardModifier.ShiftModifier); assert view.track_picks == {0, 1}
            view.setFocus(); QTest.keyClick(view, Qt.Key.Key_C, CTRL)
            header(2)
            steps(lambda: QTest.keyClick(view, Qt.Key.Key_V, CTRL), 'Add audio tracks')
            pasted = w.project.audio_tracks
            assert [x.name for x in pasted] == ['Drums', 'Audio 2', 'Audio 3', 'Drums', 'Audio 2'], pasted
            assert view.track_picks == {3, 4}
            copies = [c for c in w.project.audio_clips if c.track == 4]
            assert len(copies) == 1 and copies[0].gain_points == ((0., 1., 'linear'), (.5, .25, 'linear'))
            assert pasted[3].gain == pasted[0].gain == .7
            steps(lambda: view.build_track_menu(4).duplicate.trigger(), 'Duplicate audio tracks')
            assert [x.name for x in w.project.audio_tracks][5:] == ['Drums copy', 'Audio 2 copy'], w.project.audio_tracks
            steps(lambda: QTest.keyClick(view, Qt.Key.Key_Delete), 'Delete audio tracks')
            assert len(w.project.audio_tracks) == 5 and len(w.project.audio_clips) == 5
            view.track_picks = {3, 4}
            steps(lambda: view.build_track_menu(3).delete.trigger(), 'Delete audio tracks')
            assert [x.name for x in w.project.audio_tracks] == ['Drums', 'Audio 2', 'Audio 3'] and len(w.project.audio_clips) == 3

            # Undo all the way back to the empty project, then Redo.
            final = w.project
            while w.history.can_undo: w.undo()
            assert not w.project.audio_tracks and not w.project.audio_clips
            while w.history.can_redo: w.redo()
            assert w.project == final

            # Save and reopen: tracks, gains, names and clips survive (project version 10).
            w.set_project(audio_edit.move(w.project, 0, 2.)[0]); w.commit_history()
            steps(lambda: view.collapse_tracks([2], True), 'Collapse audio track')
            path = root / 'tracks.project.json'
            save_project(path, w.project)
            reopened = read_project(path)
            assert reopened == w.project, (reopened.audio_tracks, w.project.audio_tracks)
            w.load_project(reopened, path); app.processEvents()
            assert w.project.audio_tracks[2].collapsed and view.track_row(2).height == COLLAPSED_TRACK

            # A short timeline: the tracks scroll under a wheel over them; the roll keeps its room.
            total = sum(w.vsplit.sizes()); w.vsplit.setSizes([total - 300, 300]); app.processEvents()
            assert view.track_extent[0] > view.track_extent[1] and view.note_bottom() - view.roll_top >= 48, (view.track_extent, view.roll_top)
            y = view.audio_top + 10
            wheel = QWheelEvent(QPointF(view.x_of(1.), y), view.mapToGlobal(QPointF(view.x_of(1.), y)), QPoint(), QPoint(0, -120),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            app.sendEvent(view, wheel)
            assert view.track_scroll == AUDIO_TRACK / 2 and view.track_row(0).top < view.audio_top, (view.track_scroll, view.track_row(0))
            app.sendEvent(view, wheel); app.sendEvent(view, wheel)
            assert view.track_at(y)[0].index >= 1, (view.track_scroll, view.track_at(y))
            assert not view.grab().isNull()
            w.vsplit.setSizes([total - 560, 560]); app.processEvents()
            assert view.track_scroll == 0 or view.track_extent[0] > view.track_extent[1]

            # Meters: per-track levels and lamps; playback equals export across tracks.
            view.set_track_meters([.5, 1.5, 0.]); assert view.track_clipped == {1} and view.track_meters[0] == .5
            lamp = view.track_lamp_rect(view.track_row(1)).center().toPoint()
            QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, lamp); assert not view.track_clipped
            for clip in w.project.audio_clips: audio_clips.decoded(clip.path, clip.fingerprint, RATE)
            machine = w.output_settings()
            wav = root / 'song.wav'
            export_song(wav, w.project, machine, format='WAV', depth=32, rate=RATE, end_beats=12.)
            samples, _ = soundfile.read(wav, dtype='float32', always_2d=True)
            expected = render_song(np.arange(len(samples)) / RATE, w.project, replace(machine, sample_rate=RATE)).astype('float32')
            assert np.array_equal(samples, expected) and np.abs(samples).max() > .1
            peaks = [0.] * 3
            render_song(np.arange(len(samples)) / RATE, w.project, replace(machine, sample_rate=RATE), track_peaks=peaks)
            assert peaks[0] > .1 and peaks[1] > .1 and peaks[2] > .1, peaks
            # The playback block path (device-free): same samples as export, and per-track peaks that the
            # GUI drains; count-in blocks are not metered (Codex review).
            preview, settings = w.audio, replace(machine, sample_rate=RATE)
            preview.count_in_seconds = 0.; preview.take_track_peaks()
            block = 1. + np.arange(480) / RATE
            signal, _, _ = preview._song_block(block, settings, (w.project, 0.), meter=True)
            assert np.array_equal(signal, render_song(block, w.project, settings).astype('float32'))
            expected = [0.] * 3; render_song(block, w.project, settings, track_peaks=expected)
            assert preview.take_track_peaks() == expected and preview.take_track_peaks() == []
            preview.count_in_seconds = 1.
            preview._song_block(np.arange(480) / RATE, settings, (w.project, 0.), meter=True)
            assert preview.take_track_peaks() == []
            preview.count_in_seconds = 0.
            assert not view.grab().isNull()
        finally:
            w.history_timer.stop(); w.timer.stop(); w.force_close = True
            w.shutdown_audio(); w.release_views(); w.close()
print('Audio tracks passed: add, drop, move across tracks, mute/solo/gain, collapse, selection, reorder, copy/paste/duplicate/delete, Undo, save/reopen, meters, export.')
