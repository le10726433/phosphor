"""M1 hard switches: exact audio, persisted flags and window-only audition."""
import json
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from phosphor.engine.signal_engine import Settings, Shape, render, audition_shapes, object_id
from phosphor.engine.transforms import new_transform, Param
from phosphor.storage.app_files import scene_document, scene_from_document
from phosphor.storage.app_files import SCENE_VERSION


class SwitchAudioTests(unittest.TestCase):
    def setUp(self):
        self.a = Shape(uid='a', position_x=-.3, shape_size=.4)
        self.b = Shape(uid='b', solid='Tetrahedron', position_x=.4, shape_size=.3)
        self.settings = Settings(source='Single tone', shapes=(self.a, self.b))
        self.times = np.arange(4800)/48000

    def audio(self, shapes):
        return render(self.times, replace(self.settings, shapes=shapes))[2]

    def test_mute_removes_cycle_share_and_unmute_restores_exact_samples(self):
        original = self.audio((self.a, self.b))
        muted = replace(self.b, muted=True)
        self.assertTrue(np.array_equal(self.audio((self.a, muted)), self.audio((self.a,))))
        self.assertTrue(np.array_equal(self.audio((self.a, replace(muted, muted=False))), original))

    def test_empty_muted_scene_and_route_modes(self):
        from phosphor.engine.route_plan import ROUTE_MODES
        for mode in ROUTE_MODES:
            self.settings = replace(self.settings, route_mode=mode, shape_redraw_count=3)
            self.assertTrue(np.array_equal(self.audio((replace(self.a, muted=True),)), self.audio(())))
            self.assertTrue(np.array_equal(self.audio((self.a, replace(self.b, muted=True))), self.audio((self.a,))))

    def test_solo_and_default_identity(self):
        shapes = self.settings.shapes
        self.assertIs(audition_shapes(shapes), shapes)
        solo = audition_shapes(shapes, {'a'})
        self.assertTrue(np.array_equal(self.audio(solo), self.audio((self.a,))))
        self.assertFalse(self.b.muted)

    def test_bypass_preserves_flags_and_equals_empty_stack(self):
        stack = (new_transform('Rotate').with_param('z', Param(47.)),
                 replace(new_transform('Noise'), enabled=False))
        shape = replace(self.a, transforms=stack, stack_bypassed=True)
        self.assertTrue(np.array_equal(self.audio((shape,)), self.audio((self.a,))))
        self.assertEqual(shape.transforms, stack)
        self.assertFalse(np.array_equal(self.audio((replace(shape, stack_bypassed=False),)), self.audio((self.a,))))

    def test_transform_solo_does_not_change_original_enabled_flags(self):
        stack = (new_transform('Rotate').with_param('z', Param(40.)),
                 replace(new_transform('Translate').with_param('x', Param(.4)), enabled=False))
        shape = replace(self.a, transforms=stack)
        solo = audition_shapes((shape,), (), (('a', 1),))
        expected = replace(shape, transforms=(replace(stack[1], enabled=True),))
        self.assertTrue(np.array_equal(self.audio(solo), self.audio((expected,))))
        self.assertEqual(shape.transforms, stack)

    def test_scene_19_round_trip_and_false_omission(self):
        settings = replace(self.settings, shapes=(replace(self.a, muted=True, stack_bypassed=True), self.b))
        doc = scene_document(settings, 'drawing', '')
        self.assertEqual(doc['version'], SCENE_VERSION)
        self.assertEqual(scene_from_document(doc)[0], settings)
        self.assertNotIn('muted', doc['shapes'][1]); self.assertNotIn('stack_bypassed', doc['shapes'][1])
        self.assertNotIn('solo', json.dumps(doc))

    def test_version_18_defaults_and_rejects_new_flags(self):
        doc = scene_document(self.settings, 'drawing', ''); doc['version'] = 18
        self.assertEqual(scene_from_document(doc)[0], self.settings)
        doc['shapes'][0]['muted'] = True
        with self.assertRaises(ValueError): scene_from_document(doc)


class WindowSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from phosphor.editor.qt_app import Window, PANEL_SCENE
        self.w = Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
        self.w.timer.stop(); self.w.show_panel(PANEL_SCENE)
        self.w.add_object('Cube'); self.w.add_object('Tetrahedron')
        self.w.show(); self.app.processEvents()

    def tearDown(self):
        # QTest's modifier clicks leave Qt reporting Alt as held (no release follows), and the
        # window reads QApplication.keyboardModifiers(): release it so later tests start clean.
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        QTest.keyRelease(self.w, Qt.Key.Key_Alt); self.app.processEvents()
        self.w.force_close = True
        self.w.shutdown_audio()
        self.w.scene.mark_saved(); self.w.project_dirty = False
        self.w.release_views(); self.w.close(); self.w.deleteLater(); self.app.processEvents()
        # Delete the window now, not at interpreter exit after QApplication (exit 139).
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); self.w = None

    def test_focus_scoped_object_keys_and_composer_keys(self):
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        w = self.w
        w.shape_list.setFocus(); self.app.processEvents()
        QTest.keyClick(w.shape_list, Qt.Key.Key_M)
        self.assertTrue(w.scene.shape.muted)
        QTest.keyClick(w.shape_list, Qt.Key.Key_S)
        self.assertEqual(w.solo_ids, {object_id(w.scene.shape, 1)})
        QTest.keyClick(w.shape_list, Qt.Key.Key_M, Qt.KeyboardModifier.AltModifier)
        self.assertFalse(any(s.muted for s in w.scene.settings.shapes))
        w.set_workspace('Composer'); w.show_panel('Scene')
        w.add_object('Cube'); w.add_object('Tetrahedron')
        view = w.composer_panel.timeline; view.setFocus(); self.app.processEvents()
        view.playhead = 1.
        QTest.keyClick(view, Qt.Key.Key_M)
        self.assertEqual(len(w.project.markers), 1)
        w.seek_song(.5)
        QTest.keyClick(view, Qt.Key.Key_S)
        self.assertEqual(len(w.project.clips), 2)
        self.assertFalse(w.solo_ids)
        self.assertFalse(any(s.muted for c in w.project.clips for s in c.settings.shapes))

    def test_sandbox_mute_undo_redo_and_multiple_selection(self):
        w = self.w
        w.shape_list.item(0).setSelected(True)
        w.toggle_objects('mute')
        self.assertTrue(all(s.muted for s in w.scene.settings.shapes))
        w.undo(); self.assertFalse(any(s.muted for s in w.scene.settings.shapes))
        w.redo(); self.assertTrue(all(s.muted for s in w.scene.settings.shapes))

    def test_composer_solo_is_runtime_only_and_clears_on_clip_change(self):
        from phosphor.engine.composition import render_song, with_clip
        from phosphor.storage.project_files import project_document
        w = self.w; w.set_workspace('Composer')
        w.add_object('Cube'); w.add_object('Tetrahedron'); w.store_clip_scene()
        before = project_document(w.project)
        w.toggle_objects('solo', 0)
        runtime = w.output_project()
        self.assertIs(runtime, w.output_project())
        clip = w.project.clips[w.clip_index]
        expected = with_clip(w.project, w.clip_index,
            replace(clip, settings=replace(clip.settings, shapes=(clip.settings.shapes[0],))))
        times = np.arange(2400)/48000
        self.assertTrue(np.array_equal(render_song(times, runtime, w.output_settings()),
                                       render_song(times, expected, w.output_settings())))
        self.assertEqual(project_document(w.project), before)
        with patch('phosphor.editor.qt_app.QMessageBox.warning') as warning:
            w.warn_solo_export(); warning.assert_called_once()
        w.append_clip(); self.assertFalse(w.solo_ids)

    def test_transform_power_solo_and_bypass_controls(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        w = self.w; panel = w.transform_panel
        panel.add('Rotate'); panel.add('Translate')
        original = w.scene.shape.transforms
        power = panel.list.itemWidget(panel.list.item(0)).power
        QTest.mouseClick(power, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier)
        self.assertTrue(w.transform_solos)
        self.assertEqual(w.scene.shape.transforms, original)
        QTest.mouseClick(power, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier)
        self.assertFalse(w.transform_solos)
        panel.bypass_button.click(); self.assertTrue(w.scene.shape.stack_bypassed)
        self.assertEqual(w.scene.shape.transforms, original)


    def test_muted_editor_outline_keeps_all_orientation_axes(self):
        w = self.w
        w.toggle_objects('mute', 0)
        w.overlay(.25)
        layers = w.host.scope.overlays
        self.assertEqual(len(layers), 6)  # Guides, outline, three axes, handles (UI1).
        self.assertLess(layers[1][1].alphaF(), .25)
        self.assertTrue(all(len(points) for points, _ in layers[1:5]))  # Outline and axes (guides and hidden handles may be empty).


if __name__ == '__main__': unittest.main()
