"""Composer audio-track pan and phase (AW2): header drags and dialogs, True phase / Time delay by
right-click, the Pan and Phase lanes (add and drag nodes, the node menu), Undo (one step per
gesture), save and reopen, and playback equal to export with pan and phase on.

The WAV is generated in a temporary folder; no audio fixture is committed. Native or --headless.
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
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from phosphor.editor import qt_app as q
from phosphor.engine import audio_clips
from phosphor.engine.audio_clips import AudioTrack
from phosphor.engine.composition import COMPOSER, export_song, render_song
from phosphor.engine.track_chain import ChainState
from phosphor.storage.project_files import read_project, save_project

q.configure_graphics()
app = QApplication([])
RATE = 48000

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    t = np.arange(RATE) / RATE
    path = root / 'tone.wav'
    mono = .3 * np.sin(2 * np.pi * 220 * t)
    soundfile.write(path, np.column_stack((mono, mono)), RATE, subtype='FLOAT')
    with patch.object(q, 'write_workspace', lambda *args: None), patch.object(q.Window, 'remember_file', lambda *args: None):
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
        try:
            w.resize(1220, 840); w.show(); w.activateWindow(); app.processEvents(); w.timer.stop()
            total = sum(w.vsplit.sizes()); w.vsplit.setSizes([total - 560, 560]); app.processEvents()
            clip = audio_clips.audio_clip_from_file(path, 0.)
            w.set_project(replace(w.project, bpm=120., audio_tracks=(AudioTrack('Lead'),), audio_clips=(clip,)))
            w.commit_history()
            panel = w.composer_panel; view = panel.timeline
            view.roll_hear_notes = False
            view.set_roll_option('roll_audio_gain_lane', False)
            view.scale, view.scroll = 40., 0.
            app.processEvents()

            def steps(action, label=None):
                view.setFocus(); w.commit_history(); size = w.history._index
                action(); w.commit_history(); app.processEvents()
                assert w.history._index == size + 1, ('one step', w.history.undo_label)
                if label: assert w.history.undo_label == label, (w.history.undo_label, label)

            def drag(a, b):
                QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
                QTest.mouseMove(view, QPoint((a.x() + b.x()) // 2, (a.y() + b.y()) // 2), 20)
                QTest.mouseMove(view, b, 20)
                QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)

            row = view.track_row(0)
            pan, phase = view.track_pan_rect(row), view.track_phase_rect(row)
            assert pan is not None and phase is not None and not pan.intersects(phase)
            for rect in (view.track_gain_rect(row), view.mute_rect(row), view.solo_rect(row), view.track_meter_rect(row)):
                assert not rect.intersects(pan) and not rect.intersects(phase), rect

            # Pan: drag up 50 px is +0.5 (right), one Undo step; the header reads R50.
            steps(lambda: drag(pan.center().toPoint(), pan.center().toPoint() + QPoint(0, -50)), 'Change audio track pan')
            assert abs(w.project.audio_tracks[0].pan - .5) < 1e-9, w.project.audio_tracks[0]
            assert view.header_tooltip(view.track_row(0), pan.center().x(), pan.center().y()).startswith('Pan R50')

            # Phase: True phase drag (+1 degree per pixel), then Time delay by right-click.
            steps(lambda: drag(phase.center().toPoint(), phase.center().toPoint() + QPoint(0, -90)), 'Change audio track phase')
            assert abs(w.project.audio_tracks[0].phase - 90.) < 1e-9
            menu = view.build_phase_mode_menu(0)
            assert menu.modes['true'].isChecked() and not menu.modes['delay'].isChecked()
            steps(lambda: menu.modes['delay'].trigger(), 'Change audio track phase mode')
            assert w.project.audio_tracks[0].phase_mode == 'delay' and w.project.audio_tracks[0].phase == 90.
            opened = []  # The view's instance hook: record the menu instead of showing it (no modal QMenu.exec).
            view.open_audio_menu = lambda kind, index, extra, position: opened.append((kind, index))
            QTest.mousePress(view, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, phase.center().toPoint())
            QTest.mouseRelease(view, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, phase.center().toPoint())
            app.processEvents()
            assert opened == [('phase-mode', 0)], opened
            steps(lambda: drag(phase.center().toPoint(), phase.center().toPoint() + QPoint(0, -15)), 'Change audio track delay')
            assert abs(w.project.audio_tracks[0].delay_ms - 1.5) < 1e-9, w.project.audio_tracks[0]

            # The header menu has Pan…, Phase and the Lane choices.
            menu = view.build_track_menu(0)
            assert menu.pan.text() == 'Pan…' and menu.phase.modes['delay'].isChecked()
            assert [a.text() for a in menu.lane_menu.actions() if a.text()][1:] == ['Gain (clips)', 'Pan (track)', 'Phase (track)']

            # Pan lane: show it, add a node by click (one step), drag it (one step), node menu, Undo.
            view.set_audio_lane('pan'); app.processEvents()
            assert view.roll_audio_gain_lane and view.roll_audio_lane == 'pan'
            lane = view.gain_lane_rect(0)
            assert lane is not None
            at = QPoint(round(view.x_of(1.)), round(lane.top() + lane.height() * .25))
            steps(lambda: QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at), 'Change audio track pan lane')
            points = w.project.audio_tracks[0].pan_points
            assert len(points) == 1 and abs(points[0][0] - .5) < 1e-6 and .6 < points[0][1] < .9, points
            steps(lambda: drag(at, at + QPoint(round(view.scale), 0)), 'Change audio track pan lane')
            moved = w.project.audio_tracks[0].pan_points
            assert len(moved) == 1 and abs(moved[0][0] - 1.) < 1e-6, moved
            assert view.header_tooltip(view.track_row(0), pan.center().x(), pan.center().y()).endswith('automating it (~).')
            node_menu = view.build_track_lane_menu(0, 0)
            assert node_menu.remove.isEnabled()
            steps(lambda: node_menu.remove.trigger(), 'Change audio track pan lane')
            assert w.project.audio_tracks[0].pan_points == ()
            w.undo(); assert w.project.audio_tracks[0].pan_points == moved

            # Delete on a selected lane node deletes the node, not the track selected before (review).
            view.track_picks = {0}; view.focus_kind = 'tracks'
            node_at = QPoint(round(view.x_of(3.)), round(lane.top() + lane.height() * .25))  # Clear of the node at beat 2.
            QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, node_at)
            QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, node_at)
            w.commit_history(); app.processEvents()
            assert view.focus_kind == 'track-lane' and not view.track_picks
            assert len(w.project.audio_tracks[0].pan_points) == 2, w.project.audio_tracks[0].pan_points
            steps(lambda: view.delete_selection(), 'Change audio track pan lane')
            assert len(w.project.audio_tracks) == 1 and len(w.project.audio_tracks[0].pan_points) == 1
            w.undo(); w.undo()

            # Phase lane (follows the delay mode), one node; the lane paints.
            view.set_audio_lane('phase'); app.processEvents()
            lane = view.gain_lane_rect(0)
            steps(lambda: QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                                           QPoint(round(view.x_of(2.)), round(lane.center().y()))), 'Change audio track phase lane')
            assert len(w.project.audio_tracks[0].phase_points) == 1
            assert not view.grab().isNull()
            view.set_audio_lane('gain')

            # Save and reopen keeps everything; playback blocks with one ChainState equal the export.
            song = root / 'song.project.json'
            save_project(song, w.project)
            reopened = read_project(song)
            assert reopened.audio_tracks[0] == w.project.audio_tracks[0], (reopened.audio_tracks[0], w.project.audio_tracks[0])
            settings = replace(w.output_settings(), sample_rate=RATE)
            seconds = 1.2
            out = root / 'song.wav'
            export_song(out, reopened, settings, 0., float(reopened.beats(seconds)), format='WAV', depth=32)
            exported, _ = soundfile.read(out, dtype='float64')
            state = ChainState(); times = np.arange(round(seconds * RATE)) / RATE
            played = np.concatenate([render_song(times[i:i+480], reopened, settings, chain_state=state) for i in range(0, len(times), 480)])
            assert np.allclose(exported, np.clip(played, -1, 1), atol=1e-6), np.abs(exported - np.clip(played, -1, 1)).max()
            dry = render_song(times, replace(reopened, audio_tracks=(AudioTrack('Lead'),)), settings)
            assert not np.allclose(played, dry), 'pan and phase change the sound'
        finally:
            w.history_timer.stop(); w.timer.stop(); w.force_close = True
            w.shutdown_audio(); w.release_views(); w.close()
print('Track pan and phase passed: header drags, True phase / Time delay, Pan and Phase lanes, node menu, Undo, save/reopen, playback equals export.')
