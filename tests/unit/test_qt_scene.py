"""Scene-edit invariants: selected edits, failure safety and portable files."""
from pathlib import Path
import tempfile
import unittest
from phosphor.editor.qt_scene import Scene


class QtSceneTests(unittest.TestCase):
    def test_selected_edits_preserve_other_shapes_and_duplicate_is_independent(self):
        scene = Scene()
        scene.change('position_x',1.7)
        scene.add(True)
        scene.change('solid','Octant sphere')
        scene.change('rotation_z',47)
        self.assertEqual(scene.settings.shapes[0].solid,'Tilting circle')
        self.assertEqual((scene.settings.shapes[0].spin_x,scene.settings.shapes[0].spin_y,scene.settings.shapes[0].spin_z),(0.,0.,0.))
        self.assertEqual(scene.settings.shapes[0].rotation_z,0)
        self.assertEqual(scene.shape.position_x,1.7)
        scene.remove(); scene.remove()
        self.assertEqual(len(scene.settings.shapes),1)

    def test_invalid_edit_does_not_mutate_scene(self):
        scene = Scene(); before = scene.document()
        with self.assertRaises(ValueError): scene.change('shape_size',0)
        self.assertEqual(scene.document(),before)
        self.assertFalse(scene.dirty)

    def test_roundtrip_and_failed_load_preserve_existing_state(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            path = Path(folder)/'scene.json'
            scene = Scene(); scene.add(); scene.change('position_y',-2.5)
            scene.change('spin_z',.5); scene.change('bpm',97)
            scene.save(path)
            self.assertFalse(scene.dirty)
            restored = Scene(); restored.load(path)
            self.assertEqual(scene.document(),restored.document())
            self.assertFalse(restored.dirty)
            self.assertNotIn('preferences',restored.document())
            before = restored.document(); path.write_text('{broken')
            with self.assertRaises(ValueError): restored.load(path)
            self.assertEqual(restored.document(),before)


if __name__ == '__main__': unittest.main()
