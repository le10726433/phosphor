"""OBJ import, feature-edge reduction, routing and scene storage for 3D models."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import numpy as np
from app_files import read_scene, scene_document, atomic_json
from model_object import MAX_MODEL_EDGES, MODEL_OBJECT, Model, load_obj, model_from_obj, model_route, parse_obj
from qt_scene import Scene
from scene_path import connector_segments
from signal_engine import Settings, Shape, planned_path, validate

CORNERS = ''.join(f'v {x} {y} {z}\n' for x in (-1, 1) for y in (-1, 1) for z in (-1, 1))
QUADS = ((1, 2, 4, 3), (5, 7, 8, 6), (1, 5, 6, 2), (3, 4, 8, 7), (1, 3, 7, 5), (2, 6, 8, 4))
CUBE = CORNERS+''.join('f '+' '.join(map(str, q))+'\n' for q in QUADS)
TRIANGLES = CORNERS+''.join(f'f {q[0]} {q[1]} {q[2]}\nf {q[0]} {q[2]} {q[3]}\n' for q in QUADS)


def two_cubes():
    moved = ''.join(f'v {x+5} {y} {z}\n' for x in (-1, 1) for y in (-1, 1) for z in (-1, 1))
    faces = ''.join('f '+' '.join(str(i+8) for i in q)+'\n' for q in QUADS)
    return CUBE+moved+faces


class ParseTests(unittest.TestCase):
    def test_slashes_negative_indices_comments_and_lines(self):
        vertices, faces, lines = parse_obj('# c\nv 0 0 0\nv 1 0 0\nv 1 1 0 # end\nvn 0 0 1\nf -3/1/1 -2//1 -1/2\nl 1 3\n')
        self.assertEqual((len(vertices), faces, lines), (3, [[0, 1, 2]], [[0, 2]]))

    def test_invalid_files_explain_themselves(self):
        for text in ('', 'v 0 0 0\n', 'v 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 9\n', 'v 0 nan 0\nf 1 1 1\n', 'v a b c\n', 'v 1 1 1\nv 1 1 1\nl 1 2\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                model_from_obj(text)

    def test_load_uses_the_file_name(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'crate.obj'
            path.write_text(CUBE, encoding='utf-8')
            self.assertEqual(load_obj(path)[0].name, 'crate')


class ReductionTests(unittest.TestCase):
    def test_coplanar_diagonals_are_removed(self):
        for text in (CUBE, TRIANGLES):
            model, report = model_from_obj(text)
            self.assertEqual((len(model.edges), len(model.vertices)), (12, 8))
            self.assertFalse(report.limited or report.smooth)
        self.assertEqual(model_from_obj(TRIANGLES)[1].source_edges, 18)

    def test_duplicate_seam_vertices_weld(self):
        seam = (CORNERS+CUBE).replace('f 1 2 4 3\n', 'f 9 10 12 11\n')
        self.assertEqual(len(model_from_obj(seam)[0].edges), 12)

    def test_open_edges_and_explicit_lines_are_kept(self):
        self.assertEqual(len(model_from_obj('v 0 0 0\nv 1 0 0\nv 1 1 0\nf 1 2 3\n')[0].edges), 3)
        self.assertEqual(len(model_from_obj('v 0 0 0\nv 1 0 0\nl 1 2\n')[0].edges), 1)

    def test_dense_models_stay_within_budget(self):
        rows = []
        for i in range(120):
            base = 4*i
            height = i % 2
            rows.append(f'v {i} {height} 0\nv {i+1} {height} 0\nv {i+1} {height} 1\nv {i} {height} 1\nf {base+1} {base+2} {base+3} {base+4}\n')
        model, report = model_from_obj(''.join(rows))
        self.assertEqual(len(model.edges), MAX_MODEL_EDGES)
        self.assertTrue(report.limited)

    def test_model_is_centred_and_unit_radius(self):
        points = np.array(model_from_obj(two_cubes())[0].vertices)
        np.testing.assert_allclose((points.min(axis=0)+points.max(axis=0))/2, 0, atol=1e-12)
        self.assertAlmostEqual(float(np.linalg.norm(points, axis=1).max()), 1.)


class RouteTests(unittest.TestCase):
    def test_route_is_closed_and_covers_every_edge(self):
        for text in (CUBE, two_cubes(), 'v 0 0 0\nv 1 0 0\nl 1 2\n'):
            _, edges, route, _ = model_route(model_from_obj(text)[0])
            self.assertEqual(route[0], route[-1])
            walked = {tuple(sorted(int(v) for v in pair)) for pair in zip(route[:-1], route[1:])}
            self.assertEqual(walked, {tuple(sorted(edge)) for edge in edges})

    def test_separate_parts_join_with_fast_connectors(self):
        model = model_from_obj(two_cubes())[0]
        _, _, route, connectors = model_route(model)
        self.assertEqual(len(connectors), 1)
        steps = np.r_[connector_segments(route, connectors), False]
        shapes = (Shape(solid=MODEL_OBJECT, model=model, rotation_seconds=None),)
        plain = np.diff(planned_path(0, 60, shapes, 'No changes', 8)[2])
        faster = np.diff(planned_path(0, 60, shapes, 'Faster travel', 8)[2])
        self.assertTrue(steps.any())
        np.testing.assert_allclose(faster[steps], plain[steps]/8)
        np.testing.assert_allclose(faster[~steps], plain[~steps])

    def test_size_and_position_move_the_model_as_one_object(self):
        model = model_from_obj(CUBE)[0]
        base = planned_path(0, 60, (Shape(solid=MODEL_OBJECT, model=model, rotation_seconds=None),), 'No changes', 8)
        moved = planned_path(0, 60, (Shape(solid=MODEL_OBJECT, model=model, rotation_seconds=None, shape_size=.5, position_x=.3),), 'No changes', 8)
        np.testing.assert_allclose(moved[0], base[0]*.5)
        np.testing.assert_allclose(moved[1], np.tile([.3, 0.], (len(moved[1]), 1)))


class SceneTests(unittest.TestCase):
    def test_scene_round_trip_and_version(self):
        scene = Scene()
        scene.add_model(model_from_obj(CUBE, 'crate')[0])
        document = scene_document(scene.settings, 'mixed', '')
        self.assertEqual(document['version'], 6)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'scene.json'
            atomic_json(path, document)
            self.assertEqual(read_scene(path)[0].shapes, scene.settings.shapes)

    def test_scenes_without_models_keep_their_format(self):
        document = scene_document(Settings(), 'mixed', '')
        self.assertEqual(document['version'], 1)
        self.assertNotIn('model', document['shapes'][0])

    def test_import_adds_or_replaces(self):
        scene = Scene()
        model = model_from_obj(CUBE)[0]
        scene.add_model(model, True)
        self.assertEqual((len(scene.settings.shapes), scene.shape.solid), (1, MODEL_OBJECT))
        scene.add_model(model)
        self.assertEqual((len(scene.settings.shapes), scene.selected), (2, 1))

    def test_invalid_model_data_is_rejected(self):
        model = model_from_obj(CUBE)[0]
        for broken in (replace(model, edges=()), replace(model, edges=((0, 99),)), replace(model, edges=((1, 0),)),
                       replace(model, vertices=((5., 0., 0.),)+model.vertices[1:]), replace(model, name=' ')):
            with self.subTest(broken=broken.name), self.assertRaises(ValueError):
                validate(Settings(shapes=(Shape(solid=MODEL_OBJECT, model=broken),)))
        with self.assertRaises(ValueError):
            validate(Settings(shapes=(Shape(solid=MODEL_OBJECT),)))
        with self.assertRaises(ValueError):
            Model.read(dict(name='x'))


if __name__ == '__main__':
    unittest.main()
