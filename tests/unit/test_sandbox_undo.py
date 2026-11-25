"""Sandbox undo (U1): every edit kind round-trips through the window's scene history."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from phosphor.engine.signal_engine import object_id
from phosphor.engine.transforms import new_transform, NoiseOptions, Param, MAP, NOISE, ROTATE, SPIN


class SandboxUndoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from phosphor.editor.qt_app import Window, PANEL_SCENE
        self.w = Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
        self.w.timer.stop(); self.w.show_panel(PANEL_SCENE)
        self.w.show(); self.app.processEvents()
        self.clock = 0.

    def tearDown(self):
        self.w.force_close = True
        self.w.shutdown_audio()
        self.w.scene.mark_saved(); self.w.project_dirty = False
        self.w.release_views(); self.w.close(); self.w.deleteLater(); self.app.processEvents()
        # Delete the window now, not at interpreter exit after QApplication (exit 139).
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); self.w = None

    def commit(self, gap=1.):
        """Commit pending edits; a gap over 0.4 s never merges with the previous step."""
        self.clock += gap; self.w.commit_scene_history(now=self.clock)

    def round_trip(self, label, edit):
        w = self.w; self.commit()
        before = (w.scene.settings, w.scene.mode)
        edit(); self.commit()
        after = (w.scene.settings, w.scene.mode)
        self.assertNotEqual(before, after, label)
        self.assertEqual(w.undo_action.text(), 'Undo ' + label)
        self.assertTrue(w.undo_action.isEnabled())
        w.undo(); self.assertEqual((w.scene.settings, w.scene.mode), before, label)
        self.assertEqual(w.redo_action.text(), 'Redo ' + label)
        w.redo(); self.assertEqual((w.scene.settings, w.scene.mode), after, label)
        self.assertIs(w.scene, w.sandbox_scene)

    def test_object_add_delete_duplicate_and_selection(self):
        w = self.w
        self.assertFalse(w.undo_action.isEnabled()); self.assertEqual(w.undo_action.text(), 'Undo')
        self.round_trip('Add object', lambda: w.add_object('Cube'))
        self.round_trip('Add object', lambda: w.add_object('Tetrahedron'))
        self.round_trip('Add object', lambda: w.shape_action('duplicate'))
        w.select_shape(0)
        self.round_trip('Delete object', lambda: w.shape_action('remove'))
        w.undo()
        self.assertEqual(len(w.scene.settings.shapes), 3)
        self.assertEqual(w.scene.selected, 0)  # The deleted object is selected again.
        self.assertEqual(w.shape_list.currentRow(), 0)
        while w.scene_history.can_undo: w.undo()
        self.assertEqual(w.scene.settings.shapes, ()); self.assertEqual(w.scene.selected, -1)
        self.assertFalse(w.scene.dirty)

    def test_object_settings(self):
        from phosphor.editor.qt_app import TEXT_OBJECT, PARAMETRIC_OBJECT
        from phosphor.engine.function_plot import FunctionPlot
        w = self.w; w.add_object('Cube'); w.add_object(TEXT_OBJECT); w.add_object(PARAMETRIC_OBJECT)
        w.select_shape(0)
        self.round_trip('Change shape type', lambda: w.edit('solid', 'Octahedron'))
        self.round_trip('Move object', lambda: w.edit('position_x', .25))
        self.round_trip('Resize object', lambda: w.edit('shape_size', .5))
        self.round_trip('Edit path', lambda: w.path_changed('path_trace', Param(.5)))
        self.round_trip('Mute object', lambda: w.toggle_objects('mute', 0))
        w.select_shape(1)
        self.round_trip('Edit text', lambda: w.edit('text', 'UNDO'))
        w.select_shape(2)
        plot = w.scene.shape.plot
        self.round_trip('Edit equations', lambda: w.plot_changed(replace(plot, x='sin(2*pi*s)*0.5')))

    def test_applied_drawings(self):
        from phosphor.engine.custom_shape import Drawing
        w = self.w
        line = Drawing(nodes=((0, -.5, 0.), (1, .5, 0.)), edges=((0, 1),))
        w.builder_index = None
        self.round_trip('Add object', lambda: w.apply_custom_drawing(line))
        self.round_trip('Edit drawing', lambda: w.apply_custom_drawing(replace(line, nodes=((0, -.5, 0.), (1, .5, .25)))))

    def test_transform_stack(self):
        w = self.w; w.add_object('Cube')
        rotate, spin, noise, map_step = (new_transform(k) for k in (ROTATE, SPIN, NOISE, MAP))
        self.round_trip('Add transform', lambda: w.transforms_changed((rotate,)))
        self.round_trip('Add transform', lambda: w.transforms_changed((rotate, spin, noise, map_step)))
        self.round_trip('Reorder transforms', lambda: w.transforms_changed((spin, rotate, noise, map_step)))
        stack = w.scene.shape.transforms
        self.round_trip('Edit rotate', lambda: w.transforms_changed(
            tuple(replace(t, params=(replace(t.params[0], value=40.), *t.params[1:])) if t.kind == ROTATE else t for t in stack)))
        stack = w.scene.shape.transforms
        self.round_trip('Bypass transform', lambda: w.transforms_changed(
            tuple(replace(t, enabled=False) if t.kind == SPIN else t for t in stack)))
        stack = w.scene.shape.transforms
        self.round_trip('Edit Noise', lambda: w.transforms_changed(
            tuple(replace(t, noise=NoiseOptions('Pink', t.noise.rate)) if t.kind == NOISE else t for t in stack)))
        stack = w.scene.shape.transforms
        self.round_trip('Edit Map', lambda: w.transforms_changed(
            tuple(replace(t, params=(replace(t.params[0], expression='x*0.5'), *t.params[1:])) if t.kind == MAP else t for t in stack)))
        self.round_trip('Bypass stack', lambda: w.edit('stack_bypassed', True))
        self.round_trip('Remove transform', lambda: w.transforms_changed(w.scene.shape.transforms[1:]))

    def test_scene_settings(self):
        w = self.w; w.add_object('Cube')
        self.round_trip('Change tempo', lambda: w.edit('bpm', 96.))
        self.round_trip('Change level', lambda: w.edit('drawing_gain', .4))
        self.round_trip('Change listening mode', lambda: w.edit('mode', 'Original audio'))
        self.round_trip('Edit scene settings', lambda: w.edit('travel_mode', 'No changes'))

    def test_continuous_edits_merge(self):
        w = self.w; w.add_object('Cube'); self.commit()
        field = w.parameters['shape_size']; field.setFocus(); self.app.processEvents()
        for i in range(6):  # Wheel or arrow steps 0.1 s apart on one control.
            w.edit('shape_size', 1. + i/10); self.commit(.1)
        self.assertEqual(w.undo_action.text(), 'Undo Resize object')
        w.undo(); self.assertEqual(w.scene.shape.shape_size, 1.)
        self.assertEqual(w.undo_action.text(), 'Undo Add object')
        w.redo(); self.assertAlmostEqual(w.scene.shape.shape_size, 1.5)
        # A held mouse button (slider or viewport drag) postpones the step until release.
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        slider = w.parameters['drawing_gain'].slider
        QTest.mousePress(slider, Qt.MouseButton.LeftButton)
        w.edit('drawing_gain', .3); w.commit_scene_history()
        self.assertTrue(w.scene_history_timer.isActive())
        w.edit('drawing_gain', .2); w.commit_scene_history()
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton); self.commit()
        self.assertEqual(w.undo_action.text(), 'Undo Change level')
        w.undo(); self.assertAlmostEqual(w.scene.shape.shape_size, 1.5)
        self.assertNotEqual(w.scene.settings.drawing_gain, .2)

    def test_live_only_state_is_not_recorded(self):
        w = self.w; w.add_object('Cube'); w.add_object('Octahedron'); self.commit()
        steps = len(w.scene_history)
        w.toggle_objects('solo', 0); self.commit()  # Solo is window-only.
        self.assertEqual(w.solo_ids, {object_id(w.scene.settings.shapes[0], 0)})
        w.select_shape(1); self.commit()  # Selection alone is not a step.
        w.scene.settings = replace(w.scene.settings, motion_time_offset=2.5); w.sync(); self.commit()
        w.playhead = 3.
        self.assertEqual(len(w.scene_history), steps)
        self.assertEqual(w.undo_action.text(), 'Undo Add object')
        w.edit('bpm', 77.); self.commit()
        w.undo()
        self.assertEqual(w.scene.settings.motion_time_offset, 2.5)  # Runtime fields are kept.
        self.assertEqual(w.scene.selected, 1); self.assertEqual(w.playhead, 3.)
        self.assertTrue(w.solo_ids)  # Same objects: the audition stays.
        w.undo(); self.assertFalse(w.solo_ids)  # An object went away: audition clears.

    def test_undo_step_and_saved_state(self):
        w = self.w; w.add_object('Cube'); self.commit()
        with w.undo_step('Replace object'):
            w.edit('solid', 'Octahedron'); w.edit('position_x', .4); w.add_object('Cube')
        self.assertEqual(w.undo_action.text(), 'Undo Replace object')
        w.undo(); self.assertEqual(len(w.scene.settings.shapes), 1); self.assertEqual(w.scene.shape.solid, 'Cube')
        w.redo(); self.assertEqual(len(w.scene.settings.shapes), 2)
        with tempfile.TemporaryDirectory() as folder:
            w.sandbox_scene.path = Path(folder)/'undo.scene.json'
            from unittest.mock import patch
            with patch.object(type(w), 'remember_file', lambda *args: None):
                self.assertTrue(w.save_scene())
            self.assertFalse(w.scene.dirty)
            w.edit('bpm', 88.); self.commit()
            self.assertTrue(w.scene.dirty)
            w.undo(); self.assertFalse(w.scene.dirty)
            w.undo(); self.assertTrue(w.scene.dirty)
            w.redo(); self.assertFalse(w.scene.dirty)
            # Opening a scene starts a fresh history.
            w.open_scene_path(str(w.sandbox_scene.path))
            self.assertFalse(w.scene_history.can_undo); self.assertFalse(w.undo_action.isEnabled())

    def test_undo_step_in_composer(self):
        w = self.w; w.set_workspace('Composer'); w.add_object('Cube'); w.commit_history(now=1.)
        w.edit('bpm', 101.)  # Pending: committed first as its own, described step.
        before = w.project
        with w.undo_step('Replace object'):
            w.edit('solid', 'Octahedron'); w.add_object('Cube')
        self.assertEqual(w.undo_action.text(), 'Undo Replace object')
        w.undo(); self.assertEqual(w.project, before)
        self.assertEqual(w.undo_action.text(), 'Undo Edit scene')

    def test_text_fields_and_workspaces(self):
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        w = self.w; w.add_object('Cube'); self.commit()
        w.edit('bpm', 99.); self.commit()
        # A focused spin box with nothing of its own to undo passes the shortcut to the window.
        box = w.parameters['position_x']; box.setFocus(); self.app.processEvents()
        QTest.keyClick(box, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertNotEqual(w.scene.settings.bpm, 99.)
        QTest.keyClick(box, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(w.scene.settings.bpm, 99.)
        # Typing keeps its own undo first.
        from PySide6.QtWidgets import QLineEdit
        field = QLineEdit(w.centralWidget()); field.show(); field.setFocus(); self.app.processEvents()
        QTest.keyClicks(field, 'abc')
        QTest.keyClick(field, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(w.scene.settings.bpm, 99.); self.assertNotEqual(field.text(), 'abc')
        field.hide(); field.deleteLater(); w.shape_list.setFocus(); self.app.processEvents()
        # The Composer keeps its own history; the Sandbox's waits for its return.
        w.set_workspace('Composer'); self.assertFalse(w.undo_action.text().endswith('Change tempo'))
        w.set_workspace('Sandbox'); self.assertEqual(w.undo_action.text(), 'Undo Change tempo')


if __name__ == '__main__': unittest.main()
