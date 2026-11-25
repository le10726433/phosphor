"""Scene-edit invariants: selected edits, empty scenes, failure safety and portable files."""
from pathlib import Path
import tempfile
import unittest
from phosphor.editor.qt_scene import Scene
from phosphor.engine.function_plot import PARAMETRIC_OBJECT, FunctionPlot


class QtSceneTests(unittest.TestCase):
    def test_new_scene_is_empty_and_the_last_object_can_be_removed(self):
        scene = Scene()
        self.assertEqual((scene.settings.shapes, scene.selected, scene.shape), ((), -1, None))
        with self.assertRaises(ValueError): scene.change('position_x', 1.)
        scene.remove(); scene.duplicate()  # Nothing to remove or copy.
        self.assertEqual(scene.settings.shapes, ())
        scene.add('Cube'); scene.add(PARAMETRIC_OBJECT)
        self.assertEqual(scene.shape.plot, FunctionPlot())
        scene.remove(); scene.remove()
        self.assertEqual((scene.settings.shapes, scene.selected), ((), -1))

    def test_selected_edits_preserve_other_shapes_and_duplicate_is_independent(self):
        scene = Scene()
        scene.add('Tilting circle')
        scene.change('position_x',1.7)
        scene.duplicate()
        scene.change('solid','Octant sphere')
        scene.change('rotation_z',47)
        self.assertEqual(scene.settings.shapes[0].solid,'Tilting circle')
        self.assertEqual((scene.settings.shapes[0].spin_x,scene.settings.shapes[0].spin_y,scene.settings.shapes[0].spin_z),(0.,0.,0.))
        self.assertEqual(scene.settings.shapes[0].rotation_z,0)
        self.assertEqual(scene.shape.position_x,1.7)
        scene.remove()
        self.assertEqual((len(scene.settings.shapes),scene.selected),(1,0))

    def test_invalid_edit_does_not_mutate_scene(self):
        scene = Scene(); scene.add(); scene.saved = scene.document(); before = scene.document()
        with self.assertRaises(ValueError): scene.change('shape_size',0)
        self.assertEqual(scene.document(),before)
        self.assertFalse(scene.dirty)

    def test_roundtrip_and_failed_load_preserve_existing_state(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            path = Path(folder)/'scene.json'
            scene = Scene(); scene.add(); scene.change('position_y',-2.5)
            scene.change('spin_z',.5); scene.change('bpm',97)
            scene.add(PARAMETRIC_OBJECT); scene.change('travel_speed',float('inf'))
            scene.save(path)
            self.assertFalse(scene.dirty)
            self.assertEqual(scene.document()['settings']['travel_speed'],'instant')
            restored = Scene(); restored.load(path)
            self.assertEqual(scene.document(),restored.document())
            self.assertEqual(restored.settings.shapes,scene.settings.shapes)
            self.assertFalse(restored.dirty)
            self.assertNotIn('preferences',restored.document())
            before = restored.document(); path.write_text('{broken')
            with self.assertRaises(ValueError): restored.load(path)
            self.assertEqual(restored.document(),before)
            empty = Scene(); empty.save(path); restored.load(path)
            self.assertEqual((restored.settings.shapes,restored.selected),((),-1))


if __name__ == '__main__': unittest.main()
