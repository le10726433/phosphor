"""Recurrence parsing, deterministic poses, routing, automation and persistence."""
from dataclasses import replace
import json
import math
import time
import unittest

import numpy as np
from phosphor.engine.recurrence import (RecurrencePlot, RECURRENCE_OBJECT, PRESETS,
    parse_expression, compiled_recurrence, recurrence_points, recurrence_points_at, sequence)
from phosphor.engine.function_plot import Variable, FunctionPlot, PARAMETRIC_OBJECT
from phosphor.engine.signal_engine import (Shape, Settings, render, shape_geometry, shape_connectors)
from phosphor.engine.transforms import Param
from phosphor.engine.composition import Clip, Project, clip_settings
from phosphor.engine import lane_access, clipboard
from phosphor.engine.tempo_map import BeatClock, TempoMap
from phosphor.storage.app_files import scene_document, scene_from_document, SCENE_VERSION
from phosphor.storage.project_files import project_document, project_from_document


def shape(plot): return Shape(solid=RECURRENCE_OBJECT, recurrence=plot)


class RecurrenceTests(unittest.TestCase):
    def test_history_parser_and_shared_refusals(self):
        for text in ('x+y+z', 'x[n-1]', 'y[n-8]+z[n-2]',
                     'math.sin(n)+N+t+p', 'x if n > 3 and not y < 0 else z'):
            parse_expression(text, order=8)
        for text in ('x[n-0]', 'x[n-9]', 'x[n-k]', 'x[n-1.0]', 'x[0]', 't[n-1]',
                     'a[n-1]', 's', '__import__("os")', '(1).__class__', 'np.sin(t)',
                     'open("x")', 'math.__dict__', '[t]', 'lambda: 1', '"text"',
                     'sin(x=t)', 'sin(*t)', 'math', '__builtins__', '_hx1',
                     'x := 1', 't @ t', 't if t is 1 else 0', 'sin', 'globals()',
                     'eval("1")', 'f"{t}"', '1j', 'True', 'sin(t', ''):
            with self.subTest(text=text), self.assertRaises(ValueError): parse_expression(text, order=8)
        for text in ('s', 'n', 'N', 'x', 'x[n-1]'):
            with self.assertRaises(ValueError): parse_expression(text, variable=True)

    def test_henon_exact_loop_and_initial(self):
        plot = PRESETS['Hénon map']; expected = list(plot.initial)
        for n in range(plot.order, plot.steps):
            x, y, z = expected[-1]; expected.append((.5-2.8*x*x+y, .3*x, 0.))
        actual = recurrence_points(plot)
        np.testing.assert_array_equal(actual, expected)
        self.assertFalse(actual.flags.writeable)
        self.assertIsNone(sequence(plot)[1])

    def test_second_order_exact_loop(self):
        plot = PRESETS['Second-order oscillator']; expected = list(plot.initial)
        for n in range(2, plot.steps):
            x, y, z = expected[-1]; old = expected[-2]
            expected.append((2*math.cos(.1)*x-old[0], 2*math.cos(.1)*y-old[1], 0.))
        np.testing.assert_array_equal(recurrence_points(plot), expected)

    def test_all_history_rows_and_simultaneous_xyz(self):
        initial = tuple((float(i), float(i+10), float(i+20)) for i in range(8))
        plot = RecurrencePlot('y[n-8]', 'z[n-3]', 'x', order=8, initial=initial, steps=16)
        expected = list(initial)
        for n in range(8, 16): expected.append((expected[n-8][1], expected[n-3][2], expected[n-1][0]))
        np.testing.assert_array_equal(recurrence_points(plot), expected)

    def test_user_names_do_not_shadow_scalar_step_internals(self):
        plot = RecurrencePlot('order+history+float+step', '0', steps=16,
            variables=tuple(Variable(name, 1.) for name in ('order', 'history', 'float', 'step')))
        self.assertEqual(recurrence_points(plot)[1, 0], 4.)
        for name in ('_step', '_history', '_hx1'):
            with self.assertRaises(ValueError):
                compiled_recurrence(replace(plot, variables=(Variable(name),)))

    def test_scalar_functions_and_python_short_circuit(self):
        plot = RecurrencePlot('math.sin(pi/2)+tri(0.5)+square(0)+saw(0.5)',
                              'clip(min(-2, 1)+max(2, 1), -1, 1)',
                              '0 if n > 0 or 1/0 else 1/0', steps=16)
        np.testing.assert_array_equal(recurrence_points(plot)[1], [3., 0., 0.])
        self.assertEqual(set(compiled_recurrence(plot).plot.variables), set())

    def test_divergence_parks_and_reports_step(self):
        for expression in ('1/0', 'sqrt(-1)', 'exp(1000)', '1e309', '1000001', '(-1)**0.5'):
            with self.subTest(expression=expression):
                plot = RecurrencePlot(expression, '0', initial=((.25, .5, .75),))
                points, stopped = sequence(plot)
                self.assertEqual(stopped, 1)
                np.testing.assert_array_equal(points, np.tile(plot.initial[0], (plot.steps, 1)))
        plot = RecurrencePlot('x*10', '0', initial=((1., 0., 0.),))
        points, stopped = sequence(plot)
        self.assertEqual(stopped, 7)
        np.testing.assert_array_equal(points[7:], np.tile(points[6], (plot.steps-7, 1)))

    def test_path_and_points_position_mapping_and_wrap(self):
        plot = RecurrencePlot('n', '0', steps=16)
        positions = np.array([0., .03125, .0625, .5, 1., 1.25, -.25])
        path, wrapped = recurrence_points_at(plot, 0., positions)
        np.testing.assert_array_equal(wrapped, [0., .03125, .0625, .5, 1., .25, .75])
        np.testing.assert_array_equal(path[:, 0], wrapped*15)
        dots, _ = recurrence_points_at(replace(plot, draw='points'), 0., positions)
        np.testing.assert_array_equal(dots[:, 0], [0., 0., 1., 8., 15., 4., 12.])

    def test_static_cache_and_dynamic_pose_variables(self):
        static = RecurrencePlot('x+0.01', '0')
        self.assertIs(recurrence_points(static, 0., 80.), recurrence_points(static, 3., 200.))
        varying = replace(static, x='a', variables=(Variable('a', 0., -10., 10., 't+p'),))
        self.assertEqual(recurrence_points(varying, .5, 120.)[1, 0], 1.5)
        self.assertEqual(recurrence_points(varying, 1., 120.)[1, 0], 3.)
        self.assertIs(recurrence_points(varying, 1.), recurrence_points(varying, 1.))
        clock = BeatClock(TempoMap(120., ((1., 60.),)), 0.)
        self.assertEqual(recurrence_points(varying, 1., clock)[1, 0], 2.5)

    def test_validation_and_document_round_trips(self):
        for plot in PRESETS.values():
            self.assertEqual(RecurrencePlot.read(json.loads(json.dumps(plot.document()))), plot)
        for changes in (dict(order=0), dict(order=9), dict(order=True), dict(steps=15),
                        dict(steps=4097), dict(steps=16.), dict(draw='line'), dict(initial=()),
                        dict(initial=((0., math.nan, 0.),)), dict(initial=((0., 0.),)),
                        dict(variables=(Variable('n'),)), dict(variables=(Variable('x'),)),
                        dict(variables=(Variable('a', expression='s'),))):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                compiled_recurrence(replace(RecurrencePlot(), **changes))
        with self.assertRaises(ValueError): RecurrencePlot.read(dict(RecurrencePlot().document(), extra=1))

    def test_scene_versions_projects_and_clipboard(self):
        settings = Settings(shapes=(shape(PRESETS['Lorenz attractor']),))
        document = scene_document(settings, 'drawing', '')
        self.assertEqual(document['version'], SCENE_VERSION)
        self.assertEqual(scene_from_document(document)[0], settings)
        with self.assertRaises(ValueError): scene_from_document(dict(document, version=15))
        legacy = Settings(shapes=(Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot()),))
        old = scene_document(legacy, 'drawing', '')
        for version in range(11, 16):
            self.assertEqual(scene_from_document(dict(old, version=version))[0], legacy)
        project = Project(clips=(Clip(0., 4., settings),))
        self.assertEqual(project_from_document(project_document(project)), project)
        payload = clipboard.from_text(clipboard.to_text(clipboard.copy_clips(project, (0,))))
        pasted, selected = clipboard.paste_clips(project, payload, 4.)
        self.assertEqual(pasted.clips[selected[0]].settings, settings)

    def test_variable_lane_addressing_and_baked_clocks(self):
        plot = RecurrencePlot('a', '0', variables=(Variable('a', 1., -2., 2.),))
        clip = Clip(2., 4., Settings(shapes=(shape(plot),)))
        key = ('variable', 0, 'a'); points = ((0., 1., 'linear'), (4., 0., 'linear'))
        self.assertIn(key, lane_access.automatable(clip))
        changed = lane_access.set_points(clip, key, points)
        self.assertEqual(lane_access.get_points(changed, key), points)
        self.assertEqual(lane_access.static_value(changed, key), 1.)
        self.assertTrue(lane_access.blocks(changed))
        baked = clip_settings(changed, Settings(), 120.).shapes[0].recurrence
        self.assertEqual(recurrence_points(baked, 1.)[1, 0], .5)
        self.assertEqual(lane_access.get_points(clip, key), ())

    def test_shared_render_route_path_window_and_depth(self):
        plot = RecurrencePlot('n/16', '0', z='0.5', steps=16)
        obj = shape(plot)
        vertices, edges, route = shape_geometry(obj)
        self.assertEqual(len(vertices), 16); self.assertEqual(route[-1], 0)
        np.testing.assert_array_equal(shape_connectors(obj)[0], [[15, 0]])
        # Instant travel makes phase an exact position along the recurrence.
        settings = Settings(shapes=(obj,), source='Single tone', tone_frequency=1.,
                            travel_mode='Faster travel', travel_speed=math.inf)
        times = np.array([.03125, .25, .5, .75])
        drawing = render(times, settings)[0]
        np.testing.assert_allclose(drawing[:, 0], times*15/16, atol=1e-12)
        dots = render(times, replace(settings, shapes=(shape(replace(plot, draw='points')),)))[0]
        np.testing.assert_allclose(dots[:, 0], [0., 4/16, 8/16, 12/16], atol=1e-12)
        window = replace(obj, path_start=Param(.25), path_trace=Param(.5))
        partial = render(times, replace(settings, shapes=(window,)))[0]
        np.testing.assert_allclose(partial[:, 0], (.25+.5*times)*15/16, atol=1e-12)
        # Step 0 is the initial row; start it at z = 0.5 too so every point has depth.
        rotated = replace(obj, recurrence=replace(plot, initial=((0., 0., .5),)), rotation_y=90.)
        depth = render(times, replace(settings, shapes=(rotated,)))[0]
        self.assertTrue(np.allclose(abs(depth[:, 0]), .5))

    def test_4096_step_clifford_budget(self):
        plot = replace(PRESETS['Clifford attractor'], steps=4096)
        compiled = compiled_recurrence(plot)  # Time evaluation, not parsing or an lru hit.
        start = time.perf_counter(); points, stopped = compiled.evaluate(0., 120.)
        elapsed = time.perf_counter()-start
        self.assertIsNone(stopped); self.assertEqual(points.shape, (4096, 3))
        self.assertLess(elapsed, .05, f'4096 steps took {elapsed*1000:.1f} ms')


if __name__ == '__main__': unittest.main()
