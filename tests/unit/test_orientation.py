"""Scene-17 orientation, import axes and the main-window build indicator."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import numpy as np

from phosphor.engine import mesh_formats as meshes
from phosphor.engine.signal_engine import (Shape, Settings, posed_object, projected_solid,
    shape_geometry, posed_shapes, world_motion_route, world_motion_tangents,
    builder_motion_parts)
from phosphor.engine.transforms import resting_tilt_step, used_resting_tilt, new_transform, SPIN, MAX_TRANSFORMS
from phosphor.engine.model_object import MODEL_OBJECT, model_from_obj
from phosphor.engine.composition import Project, Clip
from phosphor.engine.custom_shape import CUSTOM_OBJECT, Drawing
from phosphor.engine.motion_path import Motion
from phosphor.storage.app_files import scene_document, scene_from_document
from phosphor.storage.project_files import project_document, project_from_document
from tests.unit.test_model_object import CUBE
from tests.unit.mesh_files import stl_bytes, gltf_mesh, write_gltf
from phosphor.storage.app_files import SCENE_VERSION


def old_pitch(points):
    c, s = np.cos(.40), np.sin(.40)
    return points @ np.array([[1., 0., 0.], [0., c, -s], [0., s, c]]).T


class OrientationTests(unittest.TestCase):
    def shapes(self):
        return (Shape(), Shape(solid=MODEL_OBJECT, model=model_from_obj(CUBE)[0]),
                Shape(solid='Text', text='HI'))

    def test_migration_preserves_old_pose_and_save_is_idempotent(self):
        shapes = self.shapes()
        doc = scene_document(Settings(shapes=shapes), 'drawing', '')
        doc['version'] = 16
        loaded = scene_from_document(doc)[0]
        for before, after in zip(shapes, loaded.shapes):
            self.assertEqual(after.transforms, (resting_tilt_step(),) if used_resting_tilt(before.solid) else ())
            raw = shape_geometry(before)[0]
            expected = old_pitch(raw) if used_resting_tilt(before.solid) else raw
            np.testing.assert_allclose(posed_object(0., after, include_depth=True)[0], expected, rtol=0, atol=1e-12)
        saved = scene_document(loaded, 'drawing', '')
        self.assertEqual(saved['version'], SCENE_VERSION)
        self.assertEqual(scene_from_document(saved)[0], loaded)

    def test_new_objects_have_no_tilt(self):
        for shape in self.shapes() + (Shape(solid='Octant sphere'),):
            raw = shape_geometry(shape)[0]
            np.testing.assert_array_equal(posed_object(0, shape, include_depth=True)[0], raw)
            np.testing.assert_array_equal(projected_solid(0, shape, include_depth=True), raw)

    def test_project_embedded_scene_migrates(self):
        doc = project_document(Project(clips=(Clip(0, 4, Settings(shapes=self.shapes())),)))
        doc['clips'][0]['scene']['version'] = 16
        loaded = project_from_document(doc)
        from phosphor.storage.project_files import PROJECT_VERSION
        self.assertEqual(doc['version'], PROJECT_VERSION)  # The scene migrates; the project version is unchanged by it.
        self.assertEqual(loaded.clips[0].settings.shapes[0].transforms, (resting_tilt_step(),))

    def test_migration_of_a_full_transform_stack(self):
        shape = Shape(transforms=(new_transform(SPIN),) * MAX_TRANSFORMS)
        doc = scene_document(Settings(shapes=(shape,)), 'drawing', '')
        doc['version'] = 16
        loaded = scene_from_document(doc)[0]
        self.assertEqual(loaded.shapes[0].transforms, (resting_tilt_step(),) + shape.transforms)
        self.assertEqual(scene_from_document(scene_document(loaded, 'drawing', ''))[0], loaded)

    def test_z_up_rotation_and_y_identity(self):
        basis = np.eye(3)
        rotated = meshes.up_axis_vertices(basis, 'Z')
        np.testing.assert_array_equal(rotated[2], [0, 1, 0])
        self.assertEqual(np.linalg.det(rotated), 1.)
        self.assertIs(meshes.up_axis_vertices(basis, 'Y'), basis)
        with self.assertRaises(ValueError): meshes.up_axis_vertices(basis, 'X')

    def test_format_defaults_and_y_path_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            obj = Path(folder)/'cube.obj'; obj.write_text(CUBE)
            stl = Path(folder)/'cube.stl'; stl.write_bytes(stl_bytes(True))
            doc, data = gltf_mesh()
            paths = [obj, stl, write_gltf(folder, 'data', doc, data), write_gltf(folder, 'glb', doc, data)]
            for path in paths:
                self.assertEqual(meshes.read_mesh(path).up_axis, 'Z' if path.suffix == '.stl' else 'Y')
                if path.suffix != '.stl':
                    self.assertEqual(meshes.load_model(path), meshes.load_model(path, up_axis='Y'))
                    model = meshes.load_model(path)[0]
                    shape = Shape(solid=MODEL_OBJECT, model=model)
                    np.testing.assert_array_equal(posed_object(0., shape, include_depth=True)[0], shape_geometry(shape)[0])
            self.assertEqual(replace(meshes.load_model(obj)[0], name='Model'), model_from_obj(CUBE)[0])

    def test_motion_follow_retains_tilt_before_final_heading(self):
        guide = Shape(solid=CUSTOM_OBJECT, uid='g', custom=Drawing(
            'Guide', ((0, 0., 0.), (1, 1., 1.)), ((0, 1),), motion_path=True))
        follower = Shape(motion=Motion('g', follow=True))
        doc = scene_document(Settings(shapes=(follower, guide)), 'drawing', '')
        doc['version'] = 16
        migrated = scene_from_document(doc)[0].shapes
        # Guide routes/tangents and builder internals use projected_solid only for
        # custom objects, which never had a resting tilt or receive a migration step.
        np.testing.assert_array_equal(world_motion_route(migrated[1])[0], world_motion_route(guide)[0])
        np.testing.assert_array_equal(world_motion_tangents(migrated[1]), world_motion_tangents(guide))
        old_parts = builder_motion_parts(guide, 0., 0.)
        new_parts = builder_motion_parts(migrated[1], 0., 0.)
        for a, b in zip(old_parts, new_parts):
            for x, y in zip(a, b): np.testing.assert_array_equal(x, y)
        shape = posed_shapes(migrated, .4)[0]
        raw = shape_geometry(follower)[0]
        # projected_solid's final fields rotate the already pitched geometry; the
        # follower's migrated stack supplies that pitch before motion-follow Z.
        expected = projected_solid(.4, shape, vertices=old_pitch(raw), include_depth=True)
        np.testing.assert_allclose(posed_object(.4, shape, include_depth=True)[0], expected, rtol=0, atol=1e-12)


class VersionLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        # Window construction warms the default recurrence. Keep these GUI tests
        # from changing the cold-cache assumptions of the recurrence unit tests.
        from phosphor.engine.recurrence import compiled_recurrence
        compiled_recurrence.cache_clear()

    def test_motion_assignment_uses_the_migrated_pose(self):
        from PySide6.QtWidgets import QWidget
        from phosphor.editor.motion_dialog import MotionDialog
        guide = Shape(solid=CUSTOM_OBJECT, uid='g', custom=Drawing(
            'Guide', ((0, 0., 0.), (1, 1., 1.)), ((0, 1),), motion_path=True))
        model = model_from_obj('v 0 0 0\nv 2 0 0\nv 0 1 3\nf 1 2 3\n')[0]
        shape = Shape(solid=MODEL_OBJECT, model=model, transforms=(resting_tilt_step(),))
        editor = QWidget()
        editor.scene = SimpleNamespace(shape=shape, selected=0, settings=Settings(shapes=(shape, guide)))
        editor.restart_motion = editor.sync = lambda: None
        dialog = MotionDialog(editor)
        try:
            dialog.apply()
            points = old_pitch(shape_geometry(shape)[0])[:, :2]
            expected = (points.min(axis=0) + points.max(axis=0)) / 2
            np.testing.assert_allclose(editor.scene.settings.shapes[0].motion.anchor, expected, rtol=0, atol=1e-12)
        finally:
            dialog.close()
            editor.close()

    def test_import_axis_invalidates_and_recomputes_preview(self):
        from PySide6.QtCore import QEventLoop, QTimer
        from phosphor.editor.model_import_dialog import ModelImportDialog
        mesh = meshes.MeshSource([[0, 0, 0], [2, 0, 0], [0, 1, 3]], [[0, 1, 2]], [], 'Triangle', 'STL', 'Z')
        dialog = ModelImportDialog(None, mesh, 1)
        def wait():
            loop = QEventLoop()
            timer = QTimer(); timer.setSingleShot(True); timer.timeout.connect(loop.quit)
            dialog.preview_done.connect(loop.quit)
            timer.start(5000)
            loop.exec()
            dialog.preview_done.disconnect(loop.quit)
            self.assertIsNotNone(dialog.ready_result(), dialog.preview.text())
        try:
            self.assertEqual(dialog.up_axis.currentText(), 'Z')
            wait()
            first = dialog.ready_result()
            self.assertEqual(first, meshes.model_from_source(mesh))
            dialog.up_axis.setCurrentText('Y')
            self.assertIsNone(dialog.ready_result())
            self.assertFalse(dialog.import_button.isEnabled())
            wait()
            self.assertEqual(dialog.ready_result(), meshes.model_from_source(mesh, up_axis='Y'))
            self.assertNotEqual(dialog.ready_result()[0], first[0])
        finally:
            dialog.reject()

    def test_label_clipboard_and_view_modes(self):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        from phosphor.editor.qt_app import Window
        from phosphor.display.branding import APP_NAME
        from phosphor.storage.build_info import VERSION, BUILD_DATE
        app = QApplication.instance() or QApplication([])
        window = Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
        try:
            self.assertEqual(window.version_label.text(), f'{APP_NAME} {VERSION}')
            self.assertEqual(window.version_label.toolTip(), BUILD_DATE)
            QTest.mouseClick(window.version_label, Qt.MouseButton.LeftButton)
            self.assertEqual(app.clipboard().text(), VERSION)
            window.set_fullscreen(True)
            self.assertTrue(window.statusBar().isHidden())
            window.exit_view_modes()
            self.assertFalse(window.statusBar().isHidden())
            window.set_immersive(True)
            self.assertTrue(window.statusBar().isHidden())
            window.exit_view_modes()
            self.assertFalse(window.statusBar().isHidden())
        finally:
            window.force_close = True
            window.close()
            window.release_views()


if __name__ == '__main__':
    unittest.main()
