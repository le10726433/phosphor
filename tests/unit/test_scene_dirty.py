"""0.55.0-dev: Scene.dirty without serializing models matches comparing scene documents."""
from pathlib import Path
import tempfile
import unittest
from phosphor.editor.qt_scene import Scene
from phosphor.engine.model_object import Model, model_from_obj
from phosphor.engine.transforms import new_transform, SPIN
from phosphor.storage.app_files import scene_document, scene_state
from tests.fixtures.dense_mesh import dense_mesh_obj


class SceneDirtyTests(unittest.TestCase):
    def setUp(self):
        self.model = model_from_obj(dense_mesh_obj(rings=12, segments=12), 'Dense')[0]

    def test_edits_saves_and_loads(self):
        scene = Scene()
        self.assertFalse(scene.dirty)
        scene.add_model(self.model)
        self.assertTrue(scene.dirty)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'model.json'
            scene.save(path)
            self.assertFalse(scene.dirty)
            scene.change('transforms', (new_transform(SPIN),))
            self.assertTrue(scene.dirty)
            scene.change('transforms', ())
            self.assertFalse(scene.dirty)  # Back to what is on disk.
            scene.mode = 'music'
            self.assertTrue(scene.dirty)
            loaded = Scene()
            loaded.load(path)
            self.assertFalse(loaded.dirty)
            self.assertIsNot(loaded.settings.shapes[0].model, self.model)
            loaded.saved = None
            self.assertTrue(loaded.dirty)

    def test_state_equality_matches_document_equality(self):
        scene = Scene()
        scene.add_model(self.model)
        base = scene.settings
        copy = Model(self.model.name, tuple(map(tuple, self.model.vertices)), tuple(self.model.edges))
        renamed = Model('Other', self.model.vertices, self.model.edges)
        moved = Model(self.model.name, ((0., 0., 0.),)+self.model.vertices[1:], self.model.edges)
        for model in (copy, renamed, moved):
            scene.change('model', model)
            with self.subTest(name=model.name):
                self.assertEqual(scene_state(base, 'drawing', '') == scene_state(scene.settings, 'drawing', ''),
                                 scene_document(base, 'drawing', '') == scene_document(scene.settings, 'drawing', ''))
            scene.settings = base


if __name__ == '__main__':
    unittest.main()
