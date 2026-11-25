"""Saved transforms (T6) in the editor: the Transform panel saves and inserts through the
window, one Undo step in the Sandbox and in a Composer clip scene; the file is off until a
path is set, and a damaged file is reported without stopping the panel."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from phosphor.engine.function_plot import PARAMETRIC_OBJECT
from phosphor.engine.transforms import Param, Variable, ROTATE, SCALE, FIELDS


class SavedTransformEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from phosphor.editor.qt_app import Window, PANEL_SCENE
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name)/'transform-presets.json'
        self.w = Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
        self.w.timer.stop(); self.w.show_panel(PANEL_SCENE)
        self.w.show(); self.app.processEvents()
        self.warnings = []
        self.panel.warn = lambda title, text: self.warnings.append(text)

    def tearDown(self):
        self.w.force_close = True
        self.w.shutdown_audio()
        self.w.scene.mark_saved(); self.w.project_dirty = False
        self.w.release_views(); self.w.close(); self.w.deleteLater(); self.app.processEvents()
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); self.w = None
        self.folder.cleanup()

    @property
    def panel(self): return self.w.transform_panel

    def build(self, composer=False):
        """An object with variables k (read by a Rotate step, automated) and m (unused)."""
        w = self.w
        if composer: w.set_workspace('Composer')
        w.add_object('Cube')
        lane = ((0., 1., 'linear'), (4., .5, 'linear'))
        w.scene.change('transform_variables', (Variable('k', 2., 0., 5., points=lane), Variable('m', 1.)))
        rotate = self.panel_step(ROTATE).with_param('z', Param(10., 'k*t', lane))
        w.scene.change('transforms', (rotate, self.panel_step(SCALE)))
        w.sync(); self.commit()

    def panel_step(self, kind):
        from phosphor.engine.transforms import new_transform
        return new_transform(kind)

    def commit(self):
        w = self.w
        if w.workspace == 'Composer': w.commit_history(force=True)
        else: w.commit_scene_history(force=True)

    def test_off_until_a_path_is_set(self):
        self.build()
        from PySide6.QtWidgets import QMenu
        menu = self.panel.build_saved_menu(QMenu())
        self.assertEqual([a.text() for a in menu.actions()], ['Saved transforms are off in this session'])
        self.assertFalse(self.panel.save_step(0, 'Never'))
        self.assertFalse(self.path.exists())

    def test_save_insert_undo_in_the_sandbox(self):
        self.build(); w, panel = self.w, self.panel
        panel.set_presets_path(self.path)
        step_menu = [a.text() for a in panel.build_step_menu(0).actions()]
        self.assertIn('Save step…', step_menu); self.assertIn('Save stack…', step_menu)
        self.assertTrue(panel.save_step(0, 'Spin k')); self.assertTrue(panel.save_stack('Both'))
        self.assertEqual(list(panel.saved), ['Spin k', 'Both'])
        self.assertEqual([v.name for v in panel.saved['Spin k'].variables], ['k'])
        before = w.scene.shape
        panel.select(1)
        self.assertTrue(panel.insert_saved('Spin k')); self.commit()
        shape = w.scene.shape
        self.assertEqual([t.kind for t in shape.transforms], [ROTATE, SCALE, ROTATE])
        self.assertEqual(shape.transforms[2].param('z').expression, 'k_2*t')
        self.assertEqual(shape.transforms[2].param('z').points, before.transforms[0].param('z').points)
        self.assertEqual([v.name for v in shape.transform_variables], ['k', 'm', 'k_2'])
        self.assertEqual(shape.transform_variables[2].points, before.transform_variables[0].points)
        self.assertIn('renamed k_2', panel.error.text())
        self.assertEqual(w.undo_action.text(), 'Undo Insert saved step')
        w.undo(); self.assertEqual(w.scene.shape, before)
        w.redo(); self.assertEqual(w.scene.shape, shape)
        # Replace the stack: one step; Undo brings the old stack back.
        self.assertTrue(panel.insert_saved('Both', 'replace')); self.commit()
        self.assertEqual([t.kind for t in w.scene.shape.transforms], [ROTATE, SCALE])
        self.assertEqual(w.undo_action.text(), 'Undo Replace with saved stack')
        w.undo(); self.assertEqual(w.scene.shape, shape)
        # Later edits to the preset do not change the scene.
        panel.confirm = lambda title, text: True
        self.assertTrue(panel.save_step(1, 'Spin k'))  # Now a Scale step under the old name.
        self.assertEqual(w.scene.shape, shape)
        # Rename and delete.
        self.assertTrue(panel.rename_saved('Both', 'Pair')); self.assertEqual(list(panel.saved), ['Spin k', 'Pair'])
        self.assertTrue(panel.delete_saved('Spin k')); self.assertEqual(list(panel.saved), ['Pair'])
        self.assertFalse(self.warnings)

    def test_equation_variable_names_are_avoided(self):
        self.build(); w, panel = self.w, self.panel
        panel.set_presets_path(self.path); panel.save_step(0, 'Spin k')
        w.add_object(PARAMETRIC_OBJECT)
        w.scene.change('plot', replace(w.scene.shape.plot, variables=(Variable('k', 1.),)))
        w.sync(); self.commit()
        self.assertTrue(panel.insert_saved('Spin k')); self.commit()
        self.assertEqual([v.name for v in w.scene.shape.transform_variables], ['k_2'])
        self.assertIn('Parametric equations', panel.error.text())

    def test_insert_into_a_composer_clip_scene(self):
        self.build(composer=True); w, panel = self.w, self.panel
        panel.set_presets_path(self.path)
        self.assertTrue(panel.save_stack('Both'))
        project = w.project
        self.assertTrue(panel.insert_saved('Both')); self.commit()
        clip = w.project.clips[w.clip_index].settings.shapes[0]
        self.assertEqual([t.kind for t in clip.transforms], [ROTATE, SCALE, ROTATE, SCALE])
        self.assertEqual([v.name for v in clip.transform_variables], ['k', 'm', 'k_2'])
        self.assertEqual(clip.transforms[2].param('z').expression, 'k_2*t')
        self.assertTrue(clip.transform_variables[2].points)
        self.assertEqual(w.undo_action.text(), 'Undo Insert saved stack')
        w.undo(); self.assertEqual(w.project, project)
        w.redo(); self.assertEqual(w.project.clips[w.clip_index].settings.shapes[0], clip)

    def test_a_full_stack_can_still_be_replaced(self):
        from phosphor.engine.transforms import MAX_TRANSFORMS
        from PySide6.QtWidgets import QMenu
        self.build(); w, panel = self.w, self.panel
        panel.set_presets_path(self.path); self.assertTrue(panel.save_stack('Both'))
        w.scene.change('transforms', (self.panel_step(SCALE),) * MAX_TRANSFORMS); w.sync(); self.commit()
        self.assertTrue(panel.add_button.isEnabled())
        self.assertFalse(any(a.isEnabled() for a in panel.kind_actions))
        menu = panel.build_saved_menu(QMenu())
        stack = next(a for a in menu.actions() if a.text() == 'Both').menu()
        self.assertEqual([(a.text(), a.isEnabled()) for a in stack.actions()],
                         [('Append to stack', False), ('Replace stack', True)])
        self.assertTrue(panel.insert_saved('Both', 'replace')); self.commit()
        self.assertEqual([t.kind for t in w.scene.shape.transforms], [ROTATE, SCALE])

    def test_a_damaged_file_is_reported_and_kept(self):
        self.build(); panel = self.panel
        self.path.write_text('{ not json', encoding='utf-8')
        panel.set_presets_path(self.path)
        self.assertIn('could not be read', panel.saved_error); self.assertEqual(panel.saved, {})
        from PySide6.QtWidgets import QMenu
        menu = panel.build_saved_menu(QMenu())
        texts = [a.text() for a in menu.actions()]
        self.assertTrue(texts[0].startswith('Saved transforms could not be read'))
        self.assertFalse(panel.save_step(0, 'Spin k')); self.assertTrue(self.warnings)
        self.assertEqual(self.path.read_text(encoding='utf-8'), '{ not json')
        panel.add(ROTATE); self.assertEqual(len(self.w.scene.shape.transforms), 3)  # The panel still works.


if __name__ == '__main__': unittest.main()
