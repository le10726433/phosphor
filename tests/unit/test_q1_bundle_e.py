"""Unit tests for Q1 Bundle E per-frame scene plan vectorization & scalar fast paths."""
import unittest
import numpy as np

from phosphor.storage.build_info import VERSION
from phosphor.engine.tempo_map import TempoMap, BeatClock
from phosphor.engine.automation import gain_at, validate_points
from phosphor.engine.transforms import (
    Param, Transform, ROTATE, TRANSLATE, SCALE, MAP, MOTION_PATH,
    apply_stack, path_window, trim_route, connector_steps, variable_values,
)
from phosphor.engine.function_plot import Variable, FunctionPlot, PARAMETRIC_OBJECT
from phosphor.engine.signal_engine import Shape, Settings, render, scene_plan


class Q1BundleETests(unittest.TestCase):
    def test_build_version_bumped(self):
        self.assertRegex(VERSION, r'^0\.9[2-9]\.\d+(-dev)?$')

    def test_tempo_map_and_beat_clock_scalar_array_parity(self):
        tmap = TempoMap(120., ((4., 140.), (8., 90.), (12., 160.)))
        clock = BeatClock(tmap, origin_beats=2.5)
        for sec in (0.0, 0.5, 2.0, 4.123, 7.999, 10.5, 25.0):
            s_beat = tmap.beats(sec)
            a_beat = float(tmap.beats(np.array([sec]))[0])
            self.assertIsInstance(s_beat, float)
            self.assertEqual(s_beat, a_beat)  # Same operations: bit-identical.
            self.assertAlmostEqual(tmap.seconds(s_beat), sec, places=12)
            self.assertEqual(tmap.seconds(s_beat), float(tmap.seconds(np.array([s_beat]))[0]))
            self.assertIsInstance(clock.beats(sec), float)
            self.assertEqual(clock.beats(sec), float(clock.beats(np.array([sec]))[0]))

    def test_automation_gain_at_scalar_array_parity_and_validation_cache(self):
        pts = ((0., 0.2, 'linear'), (1.5, 0.8, 'curve', 0.9, -0.2), (3.0, -0.5, 'hold'), (5.0, 1.0, 'linear'))
        validate_points(pts, 'Test')
        validate_points(pts, 'Test')  # identity cache hit
        tmap = TempoMap(120., ((2., 150.),))
        clock = BeatClock(tmap, origin_beats=0.5)
        for sec in (-0.5, 0.0, 0.4, 1.0, 1.5, 2.25, 3.0, 4.5, 10.0):
            g_scalar = gain_at(pts, clock, sec)
            g_array = float(gain_at(pts, clock, np.array([sec]))[0])
            self.assertEqual(g_scalar, g_array)  # Bit-identical, curves included (audio invariant).
        # Step / hold points at identical beats
        step_pts = ((0., 0.0, 'linear'), (2., 0.0, 'linear'), (2., 1.0, 'linear'), (4., 1.0, 'linear'))
        for sec in (0.5, 1.0, 1.0001, 1.5):
            self.assertEqual(gain_at(step_pts, (0.5, 0.0), sec),
                             float(gain_at(step_pts, (0.5, 0.0), np.array([sec]))[0]))

    def test_connector_steps_and_trim_route_vectorized_parity(self):
        route = np.array([0, 1, 2, 3, 4, 0], dtype=int)
        connectors = np.array([[2, 3], [4, 0]], dtype=int)
        steps = connector_steps(route, connectors)
        self.assertEqual(steps.dtype, np.dtype(bool))
        self.assertEqual(steps.tolist(), [False, False, True, False, True])
        # Reversed vertex order in connector still matches undirected edge
        self.assertEqual(connector_steps(route, np.array([[3, 2], [0, 4]])).tolist(), [False, False, True, False, True])
        self.assertEqual(connector_steps(route, ()).tolist(), [False] * 5)

        verts = np.array([[0., 0.], [1., 0.], [1., 1.], [0., 1.], [-0.5, 0.5]], dtype=float)
        for start, trace in ((0.1, 0.6), (0.7, 0.5), (0.25, -0.4)):
            res = trim_route(verts, route, connectors, start, trace)
            self.assertIsNotNone(res)
            pts, piece, edges, joins, weights = res
            self.assertEqual(len(pts), len(verts) + 2)
            self.assertEqual(piece[0], piece[-1])
            self.assertEqual(edges.shape[1], 2)
            self.assertEqual(joins.shape[1], 2)
            self.assertEqual(len(weights), 2)

    def test_apply_stack_and_variable_values_fast_paths(self):
        pts = np.array([[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]], dtype=float)
        lane = ((0., 0.5, 'linear'), (4., 1.0, 'linear'))
        vars_ = (
            Variable('k', 2.0, 0.0, 5.0, '', lane),
            Variable('w', 1.0, 0.0, 5.0, '1 + 0.5*sin(t)'),
        )
        vals = variable_values(vars_, 0.5, 120.)
        self.assertIn('k', vals)
        self.assertIn('w', vals)
        stack = (
            Transform(SCALE, (Param(1.5, '', lane), Param(2.0), Param(1.0))),
            Transform(TRANSLATE, (Param(0.25, '', lane), Param(-0.1), Param(0.0))),
            Transform(ROTATE, (Param(30., '', lane), Param(0.), Param(45.))),
            Transform(MOTION_PATH, (Param(0., '0.2*cos(t)*k'), Param(0., '0.2*sin(t)*w'), Param(0.))),
            Transform(MAP, (Param(0., 'x'), Param(0., 'y + 0.1*z'), Param(0., 'z'))),
        )
        posed, noise = apply_stack(pts, stack, 0.5, s=np.array([0., 0.5, 1.]), tempo=120., values=vals)
        self.assertEqual(posed.shape, (3, 3))
        self.assertIsNone(noise)
        self.assertTrue(np.isfinite(posed).all())

        w_start, w_trace = path_window(Param(0.2, '', lane), Param(0.8, '', lane), 0.5, 120., vals)
        self.assertGreaterEqual(w_start, 0.)
        self.assertLessEqual(w_start, 1.)
        self.assertGreaterEqual(w_trace, -1.)
        self.assertLessEqual(w_trace, 1.)

    def test_exact_parametric_with_transform_variables_and_stack(self):
        plot = FunctionPlot(x='cos(tau*s)', y='sin(tau*s)', z='0')
        vars_ = (Variable('a', 0.5, 0.1, 2.0, '', ((0., 0.5, 'linear'), (4., 1.0, 'linear'))),)
        stack = (Transform(SCALE, (Param(1.0, 'a'), Param(1.0, 'a'), Param(1.0))),)
        shape = Shape(solid=PARAMETRIC_OBJECT, plot=plot, transforms=stack, transform_variables=vars_)
        settings = Settings(shapes=(shape,))
        scene_plan.cache_clear()
        times = np.arange(0, 240) / 48000.0
        drawing, _, output = render(times, settings)
        self.assertEqual(drawing.shape, (240, 2))
        self.assertTrue(np.isfinite(output).all())


if __name__ == '__main__':
    unittest.main()
