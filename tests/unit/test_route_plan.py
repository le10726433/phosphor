import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from phosphor.engine.custom_shape import CUSTOM_OBJECT, Drawing, text_drawing
from phosphor.engine.route_plan import ROUTE_MODES, ROUTE_OFF, SHORTEST_JUMPS, SHORTEST_PATH, plan_route, route_length
from phosphor.engine.signal_engine import Settings, Shape, planned_route, render, shape_connectors, shape_geometry
from phosphor.engine.solids import SOLIDS
from phosphor.engine.stroke_style import set_style
from phosphor.engine.vector_text import TEXT_OBJECT

HELLO = Drawing().paste(text_drawing('HELLO', .3), (0., 0.))[0]
SHAPES = [Shape(solid=name) for name in SOLIDS]+[
    Shape(solid=TEXT_OBJECT, text='HELLO WORLD'), Shape(solid=CUSTOM_OBJECT, custom=HELLO),
    Shape(solid=CUSTOM_OBJECT, custom=set_style(set_style(HELLO, set(range(len(HELLO.segments))), bold=.04), {0}, depth=.2))]


def covered(route, edges):
    steps = {tuple(sorted(map(int, pair))) for pair in zip(route[:-1], route[1:])}
    return all(tuple(sorted(map(int, e))) in steps for e in edges)


class PlanTests(unittest.TestCase):
    def test_every_shape_every_mode_covers_all_lines_and_is_never_longer(self):
        for shape in SHAPES:
            vertices, edges, route = shape_geometry(shape)
            original = route_length(vertices, route)
            joins = {tuple(map(int, c)) for c in (shape_connectors(shape) or (np.empty((0, 2)),))[0]}
            drawn = [tuple(map(int, e)) for e in edges if tuple(map(int, e)) not in joins]
            for mode in ROUTE_MODES:
                v, e, r = shape_geometry(shape, mode)
                with self.subTest(shape=shape.solid, mode=mode):
                    self.assertEqual(r[0], r[-1])
                    self.assertTrue(covered(r, drawn))
                    self.assertLessEqual(route_length(v, r), original+1e-9)
                    jumps = shape_connectors(shape, mode)[0] if mode != ROUTE_OFF else ()
                    for a, b in jumps: self.assertIn((a, b), {tuple(x) for x in e})

    def test_off_is_exactly_the_original(self):
        for shape in SHAPES:
            for original, planned in zip(shape_geometry(shape), shape_geometry(shape, ROUTE_OFF)):
                np.testing.assert_array_equal(np.asarray(original), np.asarray(planned))

    def test_known_savings(self):
        lengths = {mode: route_length(*[shape_geometry(Shape(solid=CUSTOM_OBJECT, custom=HELLO), mode)[i] for i in (0, 2)])
                   for mode in ROUTE_MODES}
        self.assertLess(lengths[SHORTEST_JUMPS], lengths[ROUTE_OFF]*.7)
        self.assertLess(lengths[SHORTEST_PATH], lengths[SHORTEST_JUMPS])
        # A tetrahedron has four odd corners: the optimum adds the two shortest pairings.
        vertices, _, route = shape_geometry(Shape(solid='Tetrahedron'), SHORTEST_PATH)
        edge = np.linalg.norm(vertices[0]-vertices[1])
        self.assertAlmostEqual(route_length(vertices, route), 8*edge, 6)

    def test_open_line_is_walked_there_and_back_and_a_triangle_once(self):
        points = np.array([[0., 0, 0], [1, 0, 0], [1, 1, 0]])
        route, jumps = plan_route(points, [(0, 1), (1, 2), (2, 0)], np.array([0, 1, 2, 1, 0, 2, 0]), SHORTEST_PATH)
        self.assertAlmostEqual(route_length(points, route), 2+2**.5); self.assertEqual(len(jumps), 0)
        route, jumps = plan_route(points, [(0, 1), (1, 2)], np.array([0, 1, 2, 1, 0]), SHORTEST_PATH)
        self.assertAlmostEqual(route_length(points, route), 2+2**.5)  # Straight jump back beats retracing.
        self.assertEqual([sorted(j) for j in jumps.tolist()], [[0, 2]])

    def test_unknown_mode_and_degenerate_input(self):
        with self.assertRaises(ValueError): plan_route(np.zeros((2, 3)), [(0, 1)], np.array([0, 1, 0]), 'Fastest')
        route, jumps = plan_route(np.zeros((1, 3)), [(0, 0)], np.array([0, 0]), SHORTEST_PATH)
        np.testing.assert_array_equal(route, [0, 0])


class AudioTests(unittest.TestCase):
    def test_route_mode_changes_audio_only_when_set_and_stays_chunk_independent(self):
        times = np.arange(4800)/48000
        base = Settings(shapes=(Shape(solid=TEXT_OBJECT, text='HELLO WORLD'),))
        original = render(times, base)[0]
        planned = render(times, replace(base, route_mode=SHORTEST_PATH))[0]
        self.assertFalse(np.allclose(original, planned))
        chunks = np.vstack([render(times[i:i+1000], replace(base, route_mode=SHORTEST_PATH))[0] for i in range(0, 4800, 1000)])
        np.testing.assert_allclose(chunks, planned)
        with self.assertRaises(ValueError): render(times, replace(base, route_mode='Fastest'))

    def test_plans_are_cached(self):
        shape = Shape(solid=CUSTOM_OBJECT, custom=HELLO)
        self.assertIs(planned_route(CUSTOM_OBJECT, HELLO, SHORTEST_PATH), planned_route(CUSTOM_OBJECT, shape.custom, SHORTEST_PATH))


class PreferenceTests(unittest.TestCase):
    def test_default_on_and_old_files_migrate(self):
        from phosphor.storage.signal_preferences import DEFAULT_SIGNAL_PREFERENCES, read_signal_preferences, validated_signal_preferences
        self.assertEqual(DEFAULT_SIGNAL_PREFERENCES['route_mode'], SHORTEST_PATH)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'signal-preferences.json'
            path.write_text(json.dumps(dict(corner_dwell_us=5., shape_redraw=False, shape_redraw_count=1)))
            self.assertEqual(read_signal_preferences(path)['route_mode'], SHORTEST_PATH)
        self.assertEqual(validated_signal_preferences(dict(DEFAULT_SIGNAL_PREFERENCES, route_mode=ROUTE_OFF))['route_mode'], ROUTE_OFF)
        with self.assertRaises(ValueError): validated_signal_preferences(dict(DEFAULT_SIGNAL_PREFERENCES, route_mode='x'))


if __name__ == '__main__':
    unittest.main()
