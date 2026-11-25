"""R16: Window project, tempo-map playback blocks, clocks and file round trip."""
import _path  # noqa: F401
import os
import sys
import math
from dataclasses import replace
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import numpy as np
from PySide6.QtWidgets import QApplication, QDialog, QDoubleSpinBox, QSpinBox, QComboBox
from PySide6.QtCore import Qt, QPoint, QPointF, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest

from phosphor.editor import qt_app as q
from phosphor.editor import composer_view as cv
from phosphor.engine.composition import COMPOSER, Project, Clip, Note, clip_settings, render_song, song_seconds
from phosphor.engine.tempo_map import Meter, TempoMap
from phosphor.engine.function_plot import CompiledPlot, FunctionPlot, PARAMETRIC_OBJECT
from phosphor.engine.signal_engine import Settings, Shape
from phosphor.engine.automation import gain_at
from phosphor.engine.metronome import mix_clicks_beats
from phosphor.storage import project_files


q.configure_graphics()
app = QApplication([])


class AcceptMapDialog(QDialog):
    """Exercise the real controls without a blocking modal or Qt method patches."""
    def exec(self):
        if self.windowTitle() == 'Tempo change here':
            self.findChild(QDoubleSpinBox).setValue(90.)
        else:
            self.findChild(QSpinBox).setValue(3)
            self.findChild(QComboBox).setCurrentText('4')
        return QDialog.DialogCode.Accepted


def trigger(menu, text):
    next(action for action in menu.actions() if action.text() == text).trigger()
    menu.deleteLater()


def drag(view, start, end):
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for point in (QPoint((start.x() + end.x()) // 2, start.y()), end):
        app.sendEvent(view, QMouseEvent(QEvent.Type.MouseMove, QPointF(point),
            QPointF(view.mapToGlobal(point)), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    with patch.object(project_files, 'WORKSPACE_PATH', root / 'workspace.json'), \
         patch.object(q, 'read_preferences', lambda *args: dict(q.DEFAULT_PREFERENCES)), \
         patch.object(q, 'read_signal_preferences', lambda *args: dict(q.DEFAULT_SIGNAL_PREFERENCES)), \
         patch.object(q, 'write_workspace', lambda name: project_files.write_workspace(name, root / 'workspace.json')):
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
        w.force_close = True
        try:
            points = ((0., .2, 'linear'), (8., 1., 'linear'))
            shape = Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot('sin(p)', 'cos(p)'))
            clip = Clip(0., 12., Settings(shapes=(shape,)), mode='music',
                        lanes=(('drawing_gain', points),))
            project = Project(notes=(Note(0., 4., 69), Note(4., 8., 76, glide=2.)),
                              clips=(clip,), tempo_changes=((4., 60.),), meter_changes=((0, 3, 4),))
            w.set_project(project)
            settings = w.output_settings()
            w.audio.parameters = (settings, 'drawing')
            w.audio.metronome = False
            w.audio.count_in_seconds = 0.
            # Same device-free generator used by smoke_composer's playback blocks.
            for start in (1.95, 2.95, 5.95):
                w.audio.composer = (w.project, start)
                w.audio.frames = 0
                for index in range(3):
                    frames = w.audio.frames
                    times = start + (frames + np.arange(4800)) / settings.sample_rate
                    block = w.audio._next_block(4800)
                    expected = np.clip(render_song(times, w.project, settings), -1., 1.)
                    ramp = round(.005 * settings.sample_rate)
                    np.testing.assert_allclose(block[ramp:], expected[ramp:], atol=1e-6)
                    np.testing.assert_allclose(w.project.beats(times),
                                               np.where(times < 2., times*2., 4.+times-2.))
                    w.audio.frames += len(block)
            assert float(w.project.seconds(6.)) == 4.
            np.testing.assert_array_equal(Meter(w.project.meter_changes).bar_lines(0., 9.), [0., 3., 6., 9.])
            baked = clip_settings(clip, settings, project)
            times = np.array([1.999999, 2., 2.000001, 4.])
            xy = CompiledPlot(FunctionPlot('p', 'p'))(times, np.zeros(4), baked.tempo)
            np.testing.assert_allclose(xy[:, 0], [3.999998, 4., 4.000001, 6.])
            assert abs(gain_at(points, baked.tempo, 4.) - .8) < 1e-12
            # Callback metronome integration, after the tempo change and a seek.
            w.audio.composer = (project, 3.99 - 4800 / settings.sample_rate)
            w.audio.frames = 4800
            w.audio.metronome = True
            block = w.audio._next_block(2400)
            positions = song_seconds(project, 3.99 - 4800 / settings.sample_rate,
                                     (4800 + np.arange(2400)) / settings.sample_rate)
            dry = np.clip(render_song(positions, project, settings).astype('float32'), -1., 1.)
            expected = mix_clicks_beats(dry, project.beats(positions), settings.sample_rate,
                                       TempoMap(project.bpm, project.tempo_changes), w.audio.metronome_level,
                                       Meter(project.meter_changes))
            np.testing.assert_allclose(block, np.clip(expected, -1., 1.).astype('float32'), atol=1e-7)
            path = root / 'tempo.project.json'
            w.project_path = path
            assert w.save_project()
            reopened = project_files.read_project(path)
            assert reopened == w.project
            w.open_project_path(path)
            assert w.project == reopened
            assert w.project.tempo_changes == ((4., 60.),)
            assert w.project.meter_changes == ((0, 3, 4),)
            # Ruler actions, flags and all edits use the Window history path.
            w.resize(1700, 900); w.show(); w.activateWindow(); app.processEvents()
            w.timer.stop(); w.audio.composer = None
            view = w.composer_panel.timeline
            view.scroll, view.scale = 0., 40.
            view.setFocus(); w.commit_history()
            baseline = w.project
            # The press queues a menu, which is opened only after release.
            requests = []
            point = QPoint(round(view.x_of(8.)), 5)
            with patch.object(view, 'ruler_menu', lambda *args: requests.append(args)):
                QTest.mousePress(view, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)
                app.processEvents(); assert not requests
                QTest.mouseRelease(view, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)
                app.processEvents(); assert len(requests) == 1
            with patch.object(cv, 'QDialog', AcceptMapDialog):
                trigger(view.build_ruler_menu(8.), 'Tempo change here…')
            view.setFocus(); w.commit_history()
            assert w.project.tempo_changes == ((4., 60.), (8., 90.))
            w.undo(); assert w.project == baseline
            w.redo(); tempo_project = w.project
            with patch.object(cv, 'QDialog', AcceptMapDialog):
                trigger(view.build_ruler_menu(6.), 'Time signature here…')
            view.setFocus(); w.commit_history()
            assert w.project.meter_changes == ((0, 3, 4), (2, 3, 4))
            w.undo(); assert w.project == tempo_project
            w.redo(); with_meter = w.project
            assert cv.position_text(w.project, 7.).startswith('3.2.00')
            assert view.ruler_bars()[:5] == [(0., 1), (3., 2), (6., 3), (9., 4), (12., 5)]
            np.testing.assert_array_equal(Meter(w.project.meter_changes).bar_lines(0., 12.), [0, 3, 6, 9, 12])
            # Paint the actual ruler and bar labels, including a 6/8 section.
            view.change_map('meter', 3, (6, 8)); w.commit_history()
            assert cv.position_text(w.project, 10.).startswith('4.2.00')
            assert not view.grab().isNull()
            w.undo(); assert w.project == with_meter
            view.snap = cv.SNAPS['Bar']
            assert view.snapped(7.9) == 9. and view.snapped(7.9, math.floor) == 6.
            assert view.snap_step(7.9) == 3.
            assert view.snapped(2.9, origin=5.) == 4.
            view.snap = .25
            for kind, index, start, end, y in (('tempo', 1, 8., 10., 5), ('meter', 1, 6., 9., 15)):
                before = w.project
                drag(view, QPoint(round(view.x_of(start)) + 1, y), QPoint(round(view.x_of(end)) + 1, y))
                w.commit_history()
                changes = getattr(w.project, kind + '_changes')
                assert changes[index][0] == (end if kind == 'tempo' else 3), changes
                w.undo(); assert w.project == before
            # A marker can share a beat with both types of flag, with disjoint hits.
            view.change_map('tempo', 6., (100.,)); w.commit_history()
            shared_flags = w.project
            view.add_marker_at(6.); w.commit_history()
            x = view.x_of(6.) + 2
            assert view.marker_hit(x, 5) is None
            assert view.marker_hit(x, 15) is None
            assert view.flag_hit(x, 5) == ('tempo', 1)
            assert view.flag_hit(x, 15) == ('meter', 1)
            assert view.flag_hit(x, cv.RULER // 2) is None
            assert view.marker_hit(x, cv.RULER // 2) == 0
            w.undo(); assert w.project == shared_flags
            w.undo(); assert w.project == with_meter
            trigger(view.build_ruler_menu(8., ('tempo', 1)), 'Remove'); w.commit_history()
            assert w.project.tempo_changes == ((4., 60.),)
            w.undo(); assert w.project == with_meter
            trigger(view.build_ruler_menu(6., ('meter', 1)), 'Remove'); w.commit_history()
            assert w.project.meter_changes == ((0, 3, 4),)
            w.undo(); assert w.project == with_meter
            w.clip_length_changed(2.); w.commit_history()
            assert w.project.clips[0].length == 6.
            w.undo(); assert w.project == with_meter
            # Appending and Sandbox transfer both round an off-bar clip end up.
            shorter = replace(with_meter, clips=(replace(with_meter.clips[0], length=10.),))
            w.set_project(shorter); w.commit_history()
            for append in (w.append_clip, w.send_to_composer):
                append(); w.commit_history()
                assert w.project.clips[-1].start == 12.
                w.undo(); assert w.project == shorter
            w.undo(); assert w.project == with_meter
            with patch.object(cv, 'QDialog', AcceptMapDialog):
                trigger(view.build_ruler_menu(0.), 'Tempo change here…')
            view.setFocus(); w.commit_history()
            assert w.project.bpm == 90. and w.project.tempo_changes == with_meter.tempo_changes
            w.undo(); assert w.project == with_meter
        finally:
            w.audio.composer = None
            w.release_views()
            w.close()

print('Tempo map passed: playback, clocks, MIDI-independent maps, ruler actions, flags, marker hits and undo.')
