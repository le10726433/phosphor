"""Automation: right-click › Automate, the transform roll, mouse-drawn nodes and rendered samples."""
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
from phosphor.engine.composition import Note, COMPOSER, SANDBOX, render_song
from phosphor.engine.lane_access import get_points, blocks
from phosphor.engine.signal_engine import Settings
from phosphor.storage import project_files

folder = tempfile.TemporaryDirectory(); root = Path(folder.name)
q.configure_graphics(); app = QApplication([])
with patch.object(project_files, 'WORKSPACE_PATH', root / 'workspace.json'), patch.object(q, 'write_workspace', lambda name: project_files.write_workspace(name, root / 'workspace.json')):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
    w.resize(1220, 840); w.show(); QTest.qWait(150)
    w.add_object('Cube'); w.show_panel(q.PANEL_SCENE); w.scene_sections.setCurrentIndex(1)
    w.transform_panel.add('Rotate'); QTest.qWait(30)
    w.set_project(replace(w.project, notes=(Note(0., 16., 57),)))
    assert w.transform_panel.editors['Rotate'][1][2].automation_allowed  # Composer offers Automate.

    # Automate Rotate Z: a flat lane at gain 1 across the clip, shown in the transform roll.
    w.transform_panel.param_edited('z', replace(w.scene.shape.transforms[0].param('z'), value=90.))
    key = ('transform', 0, 0, 'z')
    w.transform_panel.automate.emit(0, 'z', True); QTest.qWait(50)
    clip = w.project.clips[0]
    assert get_points(clip, key) == ((0., 1., 'linear'), (16., 1., 'linear')), get_points(clip, key)
    view = w.composer_panel.timeline
    assert view.roll == cv.TRANSFORM_ROLL and view.roll_buttons[cv.TRANSFORM_ROLL].isChecked() and view.roll_buttons[cv.TRANSFORM_ROLL].isVisible()
    assert [b.key for b in blocks(clip)] == [('transform', 0, 0)] and view.active[('transform', 0, 0)] == key
    field = w.transform_panel.editors['Rotate'][1][2]
    assert field.caption.text().startswith(q.AUTOMATED), field.caption.text()
    # A slider edit keeps the lane.
    field.slider.edited.emit(80.); QTest.qWait(20)
    assert w.scene.shape.transforms[0].param('z').value == 80. and get_points(w.project.clips[0], key)

    # Mouse: click inside the block at beat 8 near gain 0 adds a node; drag it to gain −1.
    view.scroll, view.scale = 0., 40.; view.lane_scroll = 0; QTest.qWait(30)
    rect = view.block_rect(0)
    at = lambda beat, gain: QPoint(round(view.x_of(beat)), round(view.gain_y(rect, gain)))
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(8., 0.))
    QTest.mouseMove(view, at(8., -1.)); QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(8., -1.))
    points = get_points(w.project.clips[0], key)
    assert len(points) == 3 and points[1][0] == 8. and abs(points[1][1] + 1.) < .08, points
    # Shift-drag changes only the gain; the beat stays.
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, at(8., points[1][1]))
    target = at(10., .5)  # QTest.mouseMove carries no modifiers; send the move with Shift held.
    app.sendEvent(view, QMouseEvent(QEvent.Type.MouseMove, QPointF(target), QPointF(view.mapToGlobal(target)), Qt.MouseButton.NoButton,
                                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier))
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, at(10., .5))
    points = get_points(w.project.clips[0], key)
    assert points[1][0] == 8. and abs(points[1][1] - .5) < .08, points
    assert w.scene.selected == 0 and w.scene.shape.transforms[0].param('z').points == points  # The clip scene follows.

    # Rendered samples follow the lane: at beat 8 Rotate Z is 80 × gain.
    machine = replace(w.output_settings(), source='Single tone')
    project = w.project; plain = replace(project, clips=(replace(project.clips[0], settings=replace(project.clips[0].settings,
        shapes=(replace(project.clips[0].settings.shapes[0], transforms=(project.clips[0].settings.shapes[0].transforms[0].with_param('z',
            replace(project.clips[0].settings.shapes[0].transforms[0].param('z'), value=80. * points[1][1], points=())),)),))),))
    times = float(project.seconds(8.)) + np.arange(40) / 48000.
    np.testing.assert_allclose(render_song(times, project, machine), render_song(times, plain, machine), atol=1e-9)
    early = float(project.seconds(.5)) + np.arange(400) / 48000.
    assert np.abs(render_song(early, project, machine) - render_song(early, plain, machine)).max() > 1e-3

    # Right-click › Automate on Position X and Output level; markers show.
    w.automate(('shape', 0, 'position_x'), True); w.automate(('mix', 'drawing_gain'), True); QTest.qWait(30)
    assert w.parameters['position_x'].prefix() == q.AUTOMATED and w.slider_captions['drawing_gain'][0].text().startswith(q.AUTOMATED)
    assert [b.title for b in blocks(w.project.clips[0])] == ['Cube › Placement', 'Cube › 1. Rotate', 'Mix']
    w.automate(('shape', 0, 'position_x'), False); assert w.parameters['position_x'].prefix() == ''

    # The roll fits 1000×700: buttons and the first block are inside the timeline.
    w.resize(1000, 700); QTest.qWait(150)
    assert view.rect().contains(view.roll_buttons[cv.NOTE_ROLL].geometry()) and view.rect().contains(view.roll_buttons[cv.TRANSFORM_ROLL].geometry())
    assert view.block_rect(0).bottom() <= view.height(), (view.block_rect(0), view.height())
    view.roll_buttons[cv.NOTE_ROLL].click(); assert view.roll == cv.NOTE_ROLL
    (q.HERE / '.user').mkdir(exist_ok=True); view.set_roll(cv.TRANSFORM_ROLL); QTest.qWait(50); w.grab().save(str(q.HERE / '.user/automation-roll.png'))

    # Files keep the lanes; Sandbox does not offer Automate.
    path = root / 'song.project.json'
    with patch.object(q.QFileDialog, 'getSaveFileName', return_value=(str(path), '')): assert w.save_project()
    assert project_files.read_project(path) == w.project
    w.set_workspace(SANDBOX); assert not w.transform_panel.editors['Rotate'][1][2].automation_allowed
    w.project_dirty = False; w.sandbox_scene.saved = w.sandbox_scene.document()
    w.release_views(); w.close()
print('Automation passed: Automate, transform roll mouse nodes, rendered gain, markers, 1000x700, files.')
