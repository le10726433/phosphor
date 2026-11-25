"""On/Off lanes (M2): row menus › Automate On/Off, painting and flipping blocks, clip mute."""
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
from phosphor.engine import switch_lane as sw
from phosphor.storage import project_files


def move(widget, point):
    # QTest.mouseMove does not carry the held button on every platform.
    app.sendEvent(widget, QMouseEvent(QEvent.Type.MouseMove, QPointF(point), QPointF(widget.mapToGlobal(point)),
                                      Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def action(menu, text):
    found = [a for a in menu.actions() if a.text() == text]
    assert found, (text, [a.text() for a in menu.actions()])
    return found[0]


folder = tempfile.TemporaryDirectory(); root = Path(folder.name)
q.configure_graphics(); app = QApplication([])
with patch.object(project_files, 'WORKSPACE_PATH', root / 'workspace.json'), patch.object(q, 'write_workspace', lambda name: project_files.write_workspace(name, root / 'workspace.json')):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
    w.resize(1220, 840); w.show(); QTest.qWait(150)
    w.add_object('Cube'); w.add_object('Tetrahedron'); w.show_panel(q.PANEL_SCENE); QTest.qWait(30)
    w.set_project(replace(w.project, notes=(Note(0., 16., 57),)))
    view = w.composer_panel.timeline

    # Objects list › right-click › Automate On/Off: a lane that is on across the clip.
    menu = w.build_object_menu(1)
    automate = action(menu, 'Automate On/Off'); assert automate.isEnabled()
    automate.trigger(); QTest.qWait(50)
    key = ('on', 1)
    assert get_points(w.project.clips[0], key) == sw.DEFAULT, get_points(w.project.clips[0], key)
    assert view.roll == cv.TRANSFORM_ROLL
    assert [b.title for b in blocks(w.project.clips[0])] == ['Tetrahedron › On/Off'], blocks(w.project.clips[0])
    row = w.shape_list.itemWidget(w.shape_list.item(1))
    assert row.caption.text().endswith('◆'), row.caption.text()
    assert action(w.build_object_menu(1), 'Remove On/Off automation').isEnabled()

    # Drag across beats 4–8 paints off (the opposite of the state under the press); snapped to 1/16.
    view.scroll, view.scale = 0., 40.; view.lane_scroll = 0; QTest.qWait(30)
    rect = view.switch_rect(view.block_rect(0))
    at = lambda beat: QPoint(round(view.x_of(beat)) + 1, round(rect.center().y()))
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(4.))
    move(view, at(6.)); move(view, at(7.97))
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(7.97))
    points = get_points(w.project.clips[0], key)
    assert points == ((0., 1., 'hold'), (4., 0., 'hold'), (8., 1., 'hold')), points
    view.repaint(); QTest.qWait(20)  # Paints the blocks (paint_switch) without errors.
    assert 'off at beat' in view.lane_tooltip(at(6.).x(), at(6.).y()), view.lane_tooltip(at(6.).x(), at(6.).y())

    # Click a run to flip it: the off run turns on again and merges.
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(6.))
    assert get_points(w.project.clips[0], key) == sw.DEFAULT, get_points(w.project.clips[0], key)
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(10.))
    assert get_points(w.project.clips[0], key) == ((0., 0., 'hold'),)
    # Paint the middle on again from an off press.
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(4.))
    move(view, at(7.97)); QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(7.97))
    points = get_points(w.project.clips[0], key)
    assert points == ((0., 0., 'hold'), (4., 1., 'hold'), (8., 0., 'hold')), points
    switch_menu = view.build_switch_menu(key, 2.)
    assert action(switch_menu, 'Turn on here').isEnabled() and action(switch_menu, 'Turn all on').isEnabled()
    action(switch_menu, 'Turn all on').trigger()
    assert get_points(w.project.clips[0], key) == sw.DEFAULT
    view.set_lane(key, sw.paint(sw.DEFAULT, 4., 8., False))

    # Rendered samples: while the Tetrahedron is off, the song equals the scene without it.
    machine = replace(w.output_settings(), source='Single tone')
    project = w.project
    clip = project.clips[0]
    alone = replace(project, clips=(replace(clip, settings=replace(clip.settings, shapes=clip.settings.shapes[:1])),))
    middle = float(project.seconds(5.)) + np.arange(2000) / 48000.
    assert np.array_equal(render_song(middle, project, machine), render_song(middle, alone, machine))
    early = float(project.seconds(1.)) + np.arange(2000) / 48000.
    assert not np.array_equal(render_song(early, project, machine), render_song(early, alone, machine))

    # Transform list › right-click › Automate On/Off on a step.
    w.shape_list.setCurrentRow(0); w.scene_sections.setCurrentIndex(1); QTest.qWait(30)
    w.transform_panel.add('Rotate'); QTest.qWait(30)
    step_menu = w.transform_panel.build_step_menu(0)
    action(step_menu, 'Automate On/Off').trigger(); QTest.qWait(50)
    assert get_points(w.project.clips[0], ('on', 0, 0)) == sw.DEFAULT
    assert w.transform_panel.list.item(0).text().endswith('◆'), w.transform_panel.list.item(0).text()
    assert [b.key for b in blocks(w.project.clips[0])] == [('on', 0, 0), ('on', 1)]
    action(w.transform_panel.build_step_menu(0), 'Remove On/Off automation').trigger(); QTest.qWait(30)
    assert get_points(w.project.clips[0], ('on', 0, 0)) == ()

    # Clip mute from the clip menu: silent like a gap, and undoable.
    w.commit_history(now=1e6)
    clip_menu, _ = view.build_clip_menu(0)
    assert not clip_menu.mute.isChecked()
    clip_menu.mute.trigger(); QTest.qWait(30)
    assert w.project.clips[0].muted
    assert not np.any(render_song(early, w.project, machine))
    view.repaint(); QTest.qWait(20)
    w.commit_history(now=2e6)
    assert w.undo_action.text() == 'Undo Mute clip', w.undo_action.text()
    w.undo(); assert not w.project.clips[0].muted

    # Files keep the lanes and the mute; Sandbox only offers Automate On/Off in Composer.
    view.set_clip_muted(0, True)
    path = root / 'song.project.json'
    with patch.object(q.QFileDialog, 'getSaveFileName', return_value=(str(path), '')): assert w.save_project()
    saved = project_files.read_project(path)
    assert saved.clips[0].muted and get_points(saved.clips[0], key) == get_points(w.project.clips[0], key)
    w.set_workspace(SANDBOX); QTest.qWait(30)
    w.add_object('Cube'); w.scene_sections.setCurrentIndex(1); w.transform_panel.add('Rotate'); QTest.qWait(30)
    sandbox = action(w.build_object_menu(0), 'Automate On/Off (in Composer)'); assert not sandbox.isEnabled()
    assert not action(w.transform_panel.build_step_menu(0), 'Automate On/Off (in Composer)').isEnabled()
    w.project_dirty = False; w.sandbox_scene.saved = w.sandbox_scene.document()
    w.release_views(); w.close()
print('On/Off passed: row menus, painted and flipped blocks, rendered switching, clip mute, undo, files.')
