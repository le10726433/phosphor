"""P7c hot spots H2 (Model identity key), H3 (cached outline edges) and the song end cache."""
import copy
import pickle
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from phosphor.engine.model_object import Model


def cube_model(name='Cube'):
    vertices = tuple((x, y, z) for x in (-1., 1.) for y in (-1., 1.) for z in (-1., 1.))
    edges = ((0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3), (2, 6), (3, 7), (4, 5), (4, 6), (5, 7), (6, 7))
    return Model(name, vertices, edges)


class CountingTuple(tuple):
    """A tuple that counts element-wise equality comparisons made against it."""
    compares = 0

    def __eq__(self, other):
        type(self).compares += 1
        return tuple.__eq__(self, other)

    __hash__ = tuple.__hash__


class ModelIdentityTests(unittest.TestCase):
    def test_equality_semantics_are_unchanged(self):
        a, b = cube_model(), cube_model()
        self.assertIsNot(a, b)
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))
        self.assertNotEqual(a, cube_model('Other'))
        self.assertNotEqual(a, replace(a, vertices=a.vertices[:-1] + ((1., 1., 2.),)))
        self.assertNotEqual(a, 'Cube')
        self.assertEqual({a: 1}[b], 1)
        self.assertEqual(a, a)

    def test_equal_models_compare_fields_once_then_by_identity_key(self):
        edges = CountingTuple(cube_model().edges)
        a = Model('Cube', cube_model().vertices, edges)
        b = Model('Cube', cube_model().vertices, tuple(edges))
        CountingTuple.compares = 0
        for _ in range(50): self.assertEqual(a, b)
        self.assertEqual(CountingTuple.compares, 1)
        # Transitively: a third equal model joins the same key after one compare.
        c = Model('Cube', cube_model().vertices, tuple(edges))
        for _ in range(50): self.assertTrue(c == a and b == c)
        self.assertLessEqual(CountingTuple.compares, 2)

    def test_lru_lookup_with_an_equal_model_is_identity_fast_after_the_first(self):
        from functools import lru_cache
        calls = []
        @lru_cache(maxsize=4)
        def plan(model): calls.append(model); return len(model.edges)
        edges = CountingTuple(cube_model().edges)
        cached = Model('Cube', cube_model().vertices, edges)
        plan(cached)
        reopened = Model('Cube', cube_model().vertices, tuple(edges))  # Same model from another load.
        CountingTuple.compares = 0
        for _ in range(100): plan(reopened)
        self.assertEqual(len(calls), 1)
        self.assertEqual(CountingTuple.compares, 1)

    def test_hash_collisions_still_compare_fields(self):
        a, b = cube_model(), cube_model('Other')
        object.__setattr__(b, '_hash', hash(a))  # Force a collision.
        self.assertNotEqual(a, b)
        self.assertNotEqual(a.__dict__.get('_same'), b.__dict__.get('_same') or object())

    def test_pickling_and_copies_drop_the_identity_key(self):
        a, b = cube_model(), cube_model()
        self.assertEqual(a, b)
        self.assertIsNotNone(a.__dict__.get('_same'))
        for clone in (pickle.loads(pickle.dumps(a)), copy.copy(a), copy.deepcopy(a)):
            self.assertNotIn('_same', clone.__dict__)
            self.assertEqual(clone, a)


class OutlineEdgeCacheTests(unittest.TestCase):
    def test_stroke_edges_reuses_the_edge_array_for_the_same_edges_object(self):
        from phosphor.display import qt_geometry
        edges = [(0, 1), (1, 2), (2, 3)]
        points = np.array([[0., 0.], [10., 0.], [10., 10.], [0., 10.]])
        first = qt_geometry.stroke_edges(points, edges, 1.)
        with patch.object(qt_geometry.np, 'asarray', wraps=np.asarray) as asarray:
            second = qt_geometry.stroke_edges(points * 2, edges, 1.)
        converted = [c for c in asarray.call_args_list if c.args and c.args[0] is edges]
        self.assertEqual(converted, [])
        np.testing.assert_array_equal(first, qt_geometry.stroke_edges(points, list(edges), 1.))
        self.assertEqual(second.shape, first.shape)

    def test_cached_edges_match_the_uncached_result(self):
        from phosphor.display.qt_geometry import stroke_edges, stroke_triangles
        rng = np.random.default_rng(3)
        points = rng.normal(size=(40, 2)) * 50
        edges = tuple((int(a), int(b)) for a, b in rng.integers(0, 40, size=(60, 2)))
        expected = [stroke_triangles(points[[a, b]], .7) for a, b in edges
                    if np.linalg.norm(points[b] - points[a]) > 1e-6]
        for _ in range(2):
            np.testing.assert_array_equal(stroke_edges(points, edges, .7), np.concatenate(expected))

    def test_cache_is_bounded(self):
        from phosphor.display import qt_geometry
        points = np.zeros((3, 2)); points[1, 0] = 1
        for _ in range(3 * qt_geometry.EDGE_ARRAYS_MAX):
            qt_geometry.stroke_edges(points, [(0, 1)], 1.)
        self.assertLessEqual(len(qt_geometry._edge_arrays), qt_geometry.EDGE_ARRAYS_MAX)


class SongEndCacheTests(unittest.TestCase):
    def test_end_beats_is_computed_once_per_project(self):
        from phosphor.engine.composition import Project, Note
        project = Project(notes=tuple(Note(k * .5, .75, 60) for k in range(100)))
        self.assertEqual(project.end_beats, 49.5 + .75)
        self.assertEqual(project.__dict__['_end_beats'], 50.25)
        object.__setattr__(project, '_end_beats', -1.)  # Only to prove the next read is the cached value.
        self.assertEqual(project.end_beats, -1.)
        object.__setattr__(project, '_end_beats', 50.25)
        edited = replace(project, notes=project.notes + (Note(80., 1., 60),))
        self.assertEqual(edited.end_beats, 81.)
        self.assertEqual(project, pickle.loads(pickle.dumps(project)))


if __name__ == '__main__':
    unittest.main()
