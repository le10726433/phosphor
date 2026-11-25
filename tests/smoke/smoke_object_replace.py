"""Object Replace (R1): Objects list › Replace from the real context menu, undo, parked data,
the Model and Custom shape flows, and Composer clip scenes with parked automation."""
import _path  # noqa: F401  (makes the phosphor package importable)
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from _screen import exit_cleanly_on_failure
from phosphor.editor import qt_app as q
from phosphor.engine.composition import COMPOSER
from phosphor.engine.custom_shape import Drawing, CUSTOM_OBJECT
from phosphor.engine.function_plot import PARAMETRIC_OBJECT
from phosphor.engine.lane_access import blocks, get_points, label as lane_label
from phosphor.engine.model_object import MODEL_OBJECT
from phosphor.engine.recurrence import RECURRENCE_OBJECT
from phosphor.engine.transforms import ROTATE, SPIN
from phosphor.storage import project_files
from phosphor.storage.project_files import save_project, read_project
from tests.unit.mesh_files import write_cube_files

exit_cleanly_on_failure()
q.configure_graphics(); app = QApplication([])
TRIANGLE = Drawing('Triangle', ((10, -.6, -.4), (20, .6, -.4), (30, 0, .6)), ((10, 20), (20, 30), (30, 10)))


def settle(ms=20): QTest.qWait(ms)


def replace_menu(row):
    menu = w.build_object_menu(row)
    assert 'Replace' in [a.text() for a in menu.actions()]
    swap = menu.findChild(QMenu, 'replace_menu')
    assert swap is not None and swap.title() == 'Replace'
    return menu, swap


def items(menu):
    """Every leaf action of menu by text, submenus flattened. (QAction.menu() of a nested
    submenu can return an expired wrapper in PySide 6.10, so submenus are found as children.)"""
    subs = {child.title(): child for child in menu.findChildren(QMenu) if child.parent() is menu}
    result = {}
    for action in menu.actions():
        if action.text() in subs: result.update(items(subs[action.text()]))
        elif action.text(): result[action.text()] = action
    return result


def choose(row, text):
    menu, swap = replace_menu(row)
    action = items(swap)[text]
    assert action.isEnabled(), text
    action.trigger(); settle(); menu.deleteLater()


def label(): return w.undo_action.text()


errors = []
with tempfile.TemporaryDirectory() as folder, patch.object(q.Window, 'remember_file', lambda *args: None), \
        patch.object(project_files, 'WORKSPACE_PATH', Path(folder) / 'workspace.json'), \
        patch.object(q, 'write_workspace', lambda name: project_files.write_workspace(name, Path(folder) / 'workspace.json')):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
    w.error = lambda error: errors.append(str(error))  # Instance hook: no modal box.
    w.resize(1000, 700); w.show(); w.activateWindow(); settle(100)
    w.show_panel(q.PANEL_SCENE); settle()

    # The Replace submenu has Add object's categories and items; the current type is checked and disabled.
    add = items(w.add_button.menu())
    w.add_object('Cube'); settle()
    w.scene_sections.setCurrentIndex(1); w.transform_panel.add(ROTATE); w.transform_panel.add(SPIN); settle()
    w.scene_sections.setCurrentIndex(0)
    w.scene.change('position_x', .4); w.scene.change('muted', True); w.sync(); settle()
    menu, swap = replace_menu(0)
    assert [a.text() for a in swap.actions() if a.text()] == [a.text() for a in w.add_button.menu().actions() if a.text()]
    offered = items(swap)
    assert list(offered) == list(add), (list(offered), list(add))
    assert offered['Cube'].isChecked() and not offered['Cube'].isEnabled()
    assert all(a.isEnabled() and not a.isChecked() for t, a in offered.items() if t != 'Cube')
    menu.deleteLater()

    # Cube › Parametric: placement, mute and the transform stack stay; one Undo step.
    before = w.scene.shape
    choose(0, 'Parametric equation')
    shape = w.scene.shape
    assert shape.solid == PARAMETRIC_OBJECT and shape.plot is not None and label() == 'Undo Replace object', label()
    assert (shape.position_x, shape.muted, shape.transforms) == (before.position_x, before.muted, before.transforms)
    assert w.function_panel.isVisible() and w.function_panel.plot == shape.plot
    w.undo(); settle()
    assert w.scene.shape == before and w.redo_action.text() == 'Redo Replace object'
    w.redo(); settle(); assert w.scene.shape.solid == PARAMETRIC_OBJECT
    _, swap = replace_menu(0)
    assert items(swap)['Parametric equation'].isChecked() and not items(swap)['Parametric equation'].isEnabled()

    # A variable, then Text and back: the equations and the variable come back (parked meanwhile).
    w.function_panel.add_variable(); settle()
    w.function_panel.variable_value_edited(0, 4.); settle()
    plot = w.scene.shape.plot
    assert [v.name for v in plot.variables] == ['a'] and plot.variables[0].value == 4.
    choose(0, 'Text')
    assert w.scene.shape.solid == q.TEXT_OBJECT and w.scene.shape.text == 'HELLO' and w.scene.shape.plot == plot
    w.text_entry.setText('PARKED'); w.text_entry.editingFinished.emit(); settle()
    assert w.scene.shape.text == 'PARKED'
    choose(0, 'Parametric equation')
    assert w.scene.shape.plot == plot and w.scene.shape.text == 'PARKED'
    choose(0, 'Text'); assert w.scene.shape.text == 'PARKED'
    choose(0, RECURRENCE_OBJECT); assert w.scene.shape.recurrence is not None and w.recurrence_panel.isVisible()
    choose(0, 'Parametric equation'); assert w.scene.shape.plot == plot

    # Several objects selected: every one is replaced, as one Undo step.
    w.add_object('Octahedron'); settle()
    for i in range(2): w.shape_list.item(i).setSelected(True)
    settle()
    _, swap = replace_menu(1)
    assert not any(a.isChecked() for a in items(swap).values())  # Mixed types: nothing checked.
    shapes = w.scene.settings.shapes
    choose(1, 'Tetrahedron')
    assert [s.solid for s in w.scene.settings.shapes] == ['Tetrahedron', 'Tetrahedron'] and label() == 'Undo Replace object'
    assert w.scene.settings.shapes[0].plot == plot and w.scene.settings.shapes[0].transforms == shapes[0].transforms
    w.undo(); settle(); assert w.scene.settings.shapes == shapes
    w.shape_list.clearSelection(); w.select_shape(0); settle()

    # 3D model…: cancelling the picker leaves the object unchanged; a picked file replaces it.
    steps = label()
    with patch.object(w, 'choose_model', return_value=None):
        choose(0, '3D model…')
    assert w.scene.settings.shapes == shapes and label() == steps
    obj = str(next(path for path in write_cube_files(folder) if str(path).endswith('.obj')))
    with patch.object(w, 'choose_model', return_value=(obj, None, None)), patch.object(w, 'model_notes', lambda report: None):
        choose(0, '3D model…')
    assert w.scene.shape.solid == MODEL_OBJECT and w.scene.shape.model is not None and not errors, errors
    model = w.scene.shape.model
    choose(0, 'Cube'); assert w.scene.shape.model == model
    with patch.object(w, 'choose_model', side_effect=AssertionError('a parked model needs no picker')):
        choose(0, '3D model…')
    assert w.scene.shape.model == model

    # Custom shape…: the Builder opens on an empty drawing; leaving it unapplied changes nothing.
    steps, shapes = label(), w.scene.settings.shapes
    choose(0, 'Custom shape…')
    assert w.building and w.builder.drawing == Drawing() and w.builder_replace == [0]
    assert w.leave_builder() and w.scene.settings.shapes == shapes and label() == steps and w.builder_replace is None
    choose(0, 'Custom shape…'); w.builder.begin(TRIANGLE); w.builder.apply(); settle()
    assert w.scene.shape.solid == CUSTOM_OBJECT and w.scene.shape.custom == TRIANGLE and w.scene.shape.model == model
    assert w.builder_index == 0 and w.leave_builder()
    assert label() == 'Undo Replace object', label()  # The Builder's own steps are gone with it.
    choose(0, 'Parametric equation'); assert w.scene.shape.plot == plot and w.scene.shape.custom == TRIANGLE
    with patch.object(w, 'open_builder', side_effect=AssertionError('a parked drawing needs no Builder')):
        choose(0, 'Custom shape…')
    assert w.scene.shape.custom == TRIANGLE
    choose(0, 'Parametric equation')

    # Saved and reopened, the parked data is still there.
    scene_path = Path(folder) / 'replace.scene.json'
    w.sandbox_scene.save(scene_path)
    document = w.sandbox_scene.document()['shapes'][0]
    assert set(document['parked']) == {'text', 'custom', 'model', 'recurrence'}, document['parked']
    notices = []
    w.sandbox_scene.load(scene_path, notices); w.reset_scene_history(); w.sync(); settle()
    assert not notices and w.scene.shape.custom == TRIANGLE and w.scene.shape.text == 'PARKED' and w.scene.shape.plot == plot
    assert not errors, errors

    # Composer clip scenes: an automated variable is parked (dimmed and labelled) and comes back.
    w.set_workspace(COMPOSER); settle(100)
    w.add_object(PARAMETRIC_OBJECT); settle()
    w.function_panel.add_variable(); settle()
    w.function_panel.automate.emit('a', True); settle(50)
    key = ('variable', 0, 'a')
    clip = w.project.clips[0]
    points = get_points(clip, key)
    assert points and w.workspace == COMPOSER
    choose(0, 'Cube')
    clip = w.project.clips[0]
    assert clip.settings.shapes[0].solid == 'Cube' and label() == 'Undo Replace object', label()
    parked = ('parked', 0, 'a')
    assert get_points(clip, parked) == points, (clip.settings.shapes[0].lanes, points)
    assert lane_label(clip, parked) == 'Variable a (parked: Parametric)', lane_label(clip, parked)
    assert [b.title for b in blocks(clip)] == ['Cube › Parked variables'], [b.title for b in blocks(clip)]
    view = w.composer_panel.timeline
    assert [b.key for b in view.lane_blocks()] == [('parked', 0)]
    view.repaint(); settle()
    centre = view.block_rect(0).center()
    tip = view.lane_tooltip(centre.x(), centre.y())
    assert 'Variable a (parked: Parametric)' in tip and 'no effect' in tip, tip
    # Editable: a lane edit on the parked key changes only the parked lane.
    edited = ((0., .5, 'linear'), (clip.length, 1., 'linear'))
    view.set_lane(parked, edited); settle()
    assert get_points(w.project.clips[0], parked) == edited and w.scene.shape.solid == 'Cube'
    # Saved in the project and read back.
    project_path = Path(folder) / 'replace.project.json'
    save_project(project_path, w.project)
    notices = []; reopened = read_project(project_path, notices)
    assert not [n for n in notices if 'Parked' in n] and get_points(reopened.clips[0], parked) == edited
    w.undo(); settle(); assert get_points(w.project.clips[0], parked) == points
    w.undo(); settle()
    clip = w.project.clips[0]
    assert clip.settings.shapes[0].solid == PARAMETRIC_OBJECT and get_points(clip, key) == points
    w.redo(); w.redo(); settle()
    choose(0, 'Parametric equation')
    clip = w.project.clips[0]
    assert get_points(clip, key) == edited and [b.key for b in blocks(clip)] == [('variable', 0)]
    # Deleting a parked lane: Replace › Cube again, then remove it.
    choose(0, 'Cube')
    w.composer_panel.timeline.set_lane(parked, ()); settle()
    assert not blocks(w.project.clips[0])
    assert not errors, errors
    w.tick(); QTest.qWait(50)
    w.force_close = True; w.close()
print('object replace smoke OK')
