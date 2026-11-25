"""0.55.0-dev: the faster part-joining in model_route must give the identical route."""
from unittest import mock
import unittest
import numpy as np
from phosphor.engine import model_object
from phosphor.engine.model_object import Model, model_from_obj, model_route
from phosphor.engine.scene_path import connector_segments
from tests.fixtures.dense_mesh import dense_mesh_obj


def reference_connectors(points, edges):
    """The 0.54.0 implementation, kept verbatim as the oracle."""
    parent = list(range(len(points)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for a, b in edges:
        parent[root(a)] = root(b)
    connectors = []
    while True:
        roots = np.array([root(i) for i in range(len(points))])
        connected, remaining = np.flatnonzero(roots == roots[0]), np.flatnonzero(roots != roots[0])
        if not len(remaining):
            return connectors
        distances = np.linalg.norm(points[connected, None]-points[None, remaining], axis=2)
        i, j = np.unravel_index(int(np.argmin(distances)), distances.shape)
        a, b = int(connected[i]), int(remaining[j])
        connectors.append((min(a, b), max(a, b)))
        parent[root(b)] = root(a)


def grid_model(count):
    """Separate unit segments on an integer grid: many exactly equal distances (ties)."""
    vertices, edges = [], []
    for k in range(count):
        x, y = k % 5, k // 5
        vertices += [(x*.1, y*.1, 0.), (x*.1, y*.1, .05)]
        edges.append((2*k, 2*k+1))
    return Model('Grid', tuple(vertices), tuple(edges))


class ModelConnectorTests(unittest.TestCase):
    def assert_same(self, model):
        points = np.array(model.vertices, dtype=float)
        expected = reference_connectors(points, list(model.edges))
        self.assertEqual(model_object._connect_components(points, list(model.edges)), expected)
        return expected

    def test_dense_meshes_match_the_reference(self):
        for options in (dict(), dict(rings=24, segments=30), dict(rings=16, segments=20, jitter=.2), dict(rings=30, segments=12, facets=3)):
            with self.subTest(**options):
                model = model_from_obj(dense_mesh_obj(**options), 'Dense')[0]
                self.assertGreater(len(self.assert_same(model)), 2)

    def test_equal_distances_break_ties_like_the_reference(self):
        for count in (2, 7, 25):
            with self.subTest(count=count):
                self.assert_same(grid_model(count))
        # Reversed vertex order: later-joined parts hold lower indices.
        model = grid_model(25)
        order = list(range(len(model.vertices)))[::-1]
        back = {old: new for new, old in enumerate(order)}
        reversed_model = Model('Grid', tuple(model.vertices[i] for i in order),
                               tuple(sorted(tuple(sorted((back[a], back[b]))) for a, b in model.edges)))
        self.assert_same(reversed_model)

    def test_connected_model_needs_no_connectors(self):
        model = Model('Line', ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.)), ((0, 1), (1, 2)))
        self.assertEqual(self.assert_same(model), [])

    def test_model_route_is_identical(self):
        model = model_from_obj(dense_mesh_obj(rings=24, segments=30), 'Dense')[0]
        model_route.cache_clear()
        fast = model_route(model)
        model_route.cache_clear()
        with mock.patch.object(model_object, '_connect_components', reference_connectors):
            slow = model_route(model)
        model_route.cache_clear()
        np.testing.assert_array_equal(fast[0], slow[0])
        self.assertEqual(fast[1], slow[1])
        np.testing.assert_array_equal(fast[2], slow[2])
        np.testing.assert_array_equal(fast[3], slow[3])


class ConnectorSegmentTests(unittest.TestCase):
    def test_matches_isin(self):
        rng = np.random.default_rng(7)
        for _ in range(300):
            count = int(rng.integers(2, 60))
            route = rng.integers(0, count, int(rng.integers(2, 200)))
            connectors = rng.integers(0, count, (int(rng.integers(1, 20)), 2))
            span = int(max(route.max(), connectors.max()))+1
            key = lambda a, b: np.minimum(a, b)*span+np.maximum(a, b)
            expected = np.isin(key(route[:-1], route[1:]), key(connectors[:, 0], connectors[:, 1]))
            np.testing.assert_array_equal(connector_segments(route, connectors), expected)


class ModelCacheTests(unittest.TestCase):
    def test_hash_is_cached_and_equality_unchanged(self):
        vertices = ((0., 0., 0.), (1., 0., 0.))
        a, b = Model('A', vertices, ((0, 1),)), Model('A', tuple(map(tuple, vertices)), ((0, 1),))
        self.assertEqual(hash(a), hash(('A', a.vertices, a.edges)))
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))
        self.assertNotEqual(a, Model('B', vertices, ((0, 1),)))
        self.assertEqual({a: 1}[b], 1)

    def test_validate_still_rejects_bad_data(self):
        with self.assertRaises(ValueError):
            Model('Bad', ((0., 0., 2.), (1., 0., 0.)), ((0, 1),)).validate()
        bad = Model('Bad', ((0., 0., 0.), (1., 0., 0.)), ((1, 0),))
        for _ in range(2):  # Failures are never cached as valid.
            with self.assertRaises(ValueError):
                bad.validate()

    def test_pickle_drops_the_caches(self):
        import pickle
        model = Model('A', ((0., 0., 0.), (1., 0., 0.)), ((0, 1),))
        hash(model); model.validate()
        copy = pickle.loads(pickle.dumps(model))
        self.assertEqual(copy, model)
        self.assertNotIn('_hash', copy.__dict__)
        self.assertNotIn('_valid', copy.__dict__)


if __name__ == '__main__':
    unittest.main()
