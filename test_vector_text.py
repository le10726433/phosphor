from pathlib import Path
import tempfile
import unittest
import numpy as np
from vector_text import text_geometry,text_route,validate_text,GLYPHS
from signal_engine import Settings,Shape,planned_path,render,validate
from qt_scene import Scene
from app_files import read_scene
from scene_path import TRAVEL_MODES,connector_segments


class VectorTextTests(unittest.TestCase):
    def test_glyph_routes_cover_edges_are_closed_and_normalized(self):
        for char in GLYPHS:
            with self.subTest(char=char):
                vertices,edges,route=text_geometry(char)
                self.assertTrue(np.isfinite(vertices).all())
                self.assertLessEqual(np.linalg.norm(vertices,axis=1).max(),1+1e-12)
                self.assertEqual(route[0],route[-1])
                traversed={tuple(sorted((a,b))) for a,b in zip(route,route[1:])}
                self.assertTrue({tuple(sorted(edge)) for edge in edges} <= traversed)
                np.testing.assert_array_equal(vertices[:,2],0.)
        np.testing.assert_array_equal(text_geometry('Hello')[0],text_geometry('HELLO')[0])

    def test_scene_roundtrip_duplicate_and_legacy_default(self):
        scene=Scene();scene.add(text=True);scene.change('text','Hi, Mike!')
        scene.change('position_x',.3);scene.change('rotation_z',20)
        scene.add(True);scene.change('text','SECOND')
        self.assertEqual(scene.settings.shapes[1].text,'Hi, Mike!')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'text.scene.json';scene.save(path)
            settings,_,_=read_scene(path)
            self.assertEqual(settings.shapes,scene.settings.shapes)
            self.assertNotIn('text',scene.document()['shapes'][0])
            self.assertIn('text',scene.document()['shapes'][1])

    def test_text_audio_is_chunk_independent_and_travel_modes_work(self):
        shapes=(Shape('Text',text='HI',rotation_seconds=None),Shape(position_x=.5,shape_size=.3))
        times=np.arange(4096)/48000
        for mode in TRAVEL_MODES:
            settings=Settings(shapes=shapes,travel_mode=mode)
            validate(settings)
            full=render(times,settings)[2]
            chunks=np.concatenate([render(chunk,settings)[2] for chunk in np.array_split(times,5)])
            np.testing.assert_allclose(full,chunks,atol=1e-14)
        a=render(times,Settings(shapes=(Shape('Text',text='A',rotation_seconds=None),)))[2]
        b=render(times,Settings(shapes=(Shape('Text',text='B',rotation_seconds=None),)))[2]
        self.assertGreater(np.max(abs(a-b)),.01)

    def test_blank_and_invalid_text(self):
        vertices,_,route=text_geometry('   ')
        np.testing.assert_array_equal(vertices,0.)
        self.assertEqual(len(route),2)
        for text in ('A'*65,'hello\nworld','こんにちは',None):
            with self.subTest(text=text),self.assertRaises(ValueError):validate_text(text)
        scene=Scene();scene.add(text=True);before=scene.document()
        with self.assertRaises(ValueError):scene.change('text','❌')
        self.assertEqual(scene.document(),before)


class TextConnectorTests(unittest.TestCase):
    @staticmethod
    def shape(text='HELLO', **options):
        return Shape('Text', text=text, rotation_seconds=None, **options)

    @staticmethod
    def steps(text='HELLO'):
        _, _, route, connectors = text_route(text)
        return np.r_[connector_segments(route, connectors), False]

    def test_connectors_join_only_separate_strokes(self):
        self.assertEqual(len(text_route('L')[3]), 0)
        self.assertEqual(len(text_route('LL')[3]), 1)
        self.assertEqual(len(text_route('   ')[3]), 0)
        self.assertEqual(text_route('HELLO')[3].shape[1], 2)

    def test_faster_travel_speeds_only_connectors(self):
        steps = self.steps()
        plain = np.diff(planned_path(0, 60, (self.shape(),), 'No changes', 8)[2])
        faster = np.diff(planned_path(0, 60, (self.shape(),), 'Faster travel', 8)[2])
        np.testing.assert_allclose(faster[steps], plain[steps]/8)
        np.testing.assert_allclose(faster[~steps], plain[~steps])

    def test_unit_speed_and_single_strokes_keep_timing(self):
        plain = planned_path(0, 60, (self.shape(),), 'No changes', 8)
        np.testing.assert_allclose(planned_path(0, 60, (self.shape(),), 'Faster travel', 1)[2], plain[2])
        one = (self.shape('L'),)
        np.testing.assert_allclose(planned_path(0, 60, one, 'Faster travel', 8)[2], planned_path(0, 60, one, 'No changes', 8)[2])

    def test_geometry_is_unchanged_and_transforms_stay_rigid(self):
        plain = planned_path(0, 60, (self.shape(),), 'No changes', 8)
        faster = planned_path(0, 60, (self.shape(),), 'Faster travel', 8)
        np.testing.assert_array_equal(faster[0], plain[0])
        moved = planned_path(0, 60, (self.shape(shape_size=.5, position_x=.3, position_y=-.2),), 'Faster travel', 8)
        np.testing.assert_allclose(moved[0], faster[0]*.5/self.shape().shape_size)
        np.testing.assert_allclose(moved[1], np.tile([.3, -.2], (len(moved[1]), 1)))
        np.testing.assert_allclose(moved[2]/moved[2][-1], faster[2]/faster[2][-1])

    def test_scenes_and_redraw_keep_shape_travel_and_connectors(self):
        shapes = (self.shape('LL', position_x=-.5), self.shape('T', position_x=.5))
        self.assertGreater(planned_path(0, 60, shapes, 'No changes', 8)[2][-1], planned_path(0, 60, shapes, 'Faster travel', 8)[2][-1])
        for mode in ('Nearest-shape ordering', 'Retrace existing edges'):
            self.assertGreater(planned_path(0, 60, shapes, mode, 8)[2][-1], 0)
        redrawn = planned_path(0, 60, shapes, 'Faster travel', 8, 0., 3)
        self.assertGreater(len(redrawn[0]), len(planned_path(0, 60, shapes, 'Faster travel', 8)[0]))
