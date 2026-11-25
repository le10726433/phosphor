"""Topology and import checks, deliberately excluding expensive dense routing."""
from collections import Counter
from pathlib import Path
import tempfile
import unittest

from tests.fixtures.dense_mesh import dense_mesh_obj, write_dense_mesh
from phosphor.engine.model_object import model_from_obj


class DenseMeshTests(unittest.TestCase):
    def test_deterministic_text(self):
        self.assertEqual(dense_mesh_obj(), dense_mesh_obj())

    def test_triangle_count_and_closed_edges(self):
        for rings, segments in ((3, 3), (7, 11), (50, 50)):
            with self.subTest(rings=rings, segments=segments):
                lines = dense_mesh_obj(rings=rings, segments=segments).splitlines()
                faces = [tuple(map(int, line.split()[1:])) for line in lines if line.startswith('f ')]
                self.assertEqual(len(faces), 2 * rings * segments)
                self.assertEqual(sum(line.startswith('v ') for line in lines), rings * segments)
                edges = Counter()
                for face in faces:
                    self.assertEqual(len(set(face)), 3)
                    self.assertTrue(all(1 <= v <= rings * segments for v in face))
                    edges.update(tuple(sorted((a, b))) for a, b in zip(face, face[1:] + face[:1]))
                self.assertEqual(set(edges.values()), {2})

    def test_invalid_arguments(self):
        for kwargs in ({'rings': 2}, {'rings': 3.5}, {'segments': 0}, {'segments': '4'},
                       {'facets': 0}, {'facets': 1.5}, {'relief': -.1}, {'relief': 1.1},
                       {'jitter': -.1}, {'jitter': .51}, {'relief': float('nan')},
                       {'jitter': float('inf')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                dense_mesh_obj(**kwargs)

    def test_default_import_keeps_detail(self):
        model, _ = model_from_obj(dense_mesh_obj())
        self.assertGreater(len(model.edges), 1500)

    def test_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'mesh.obj'
            self.assertEqual(write_dense_mesh(path, rings=7, segments=9), 126)
            self.assertEqual(path.read_text(encoding='utf-8'), dense_mesh_obj(rings=7, segments=9))
