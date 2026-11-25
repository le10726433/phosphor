import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from phosphor.storage.function_presets import read_function_presets
from phosphor.storage.app_files import SCENE_VERSION

from phosphor.engine.function_plot import (CompiledPlot, FunctionPlot, PRESETS, Variable, parse_expression,
                                           compiled_plot, validate_plot, PARAMETRIC_OBJECT)


class FunctionPlotTests(unittest.TestCase):
    def test_python_maths_syntax_evaluates_per_sample(self):
        times = np.arange(480)/48000
        plot = FunctionPlot('math.sin(2*math.pi*100*t)', 'cos(tau*s)**2 - 0.5')
        signal = CompiledPlot(plot)(times, (times*50) % 1)
        np.testing.assert_allclose(signal[:, 0], np.sin(2*np.pi*100*times))
        np.testing.assert_allclose(signal[:, 1], np.cos(2*np.pi*((times*50) % 1))**2-.5)

    def test_s_and_t_are_separate_inputs(self):
        times = np.array([0., .25, .5, 1.25])
        signal = CompiledPlot(FunctionPlot('s', 't'))(times, [0., .25, .5, .25])
        np.testing.assert_allclose(signal[:, 0], [0., .25, .5, .25])
        np.testing.assert_allclose(signal[:, 1], times)

    def test_constants_broadcast_and_conditionals_vectorize(self):
        times = np.linspace(0, 1, 9, endpoint=False)
        plot = FunctionPlot('0.5', '1 if s < 0.5 and not s > 0.9 else -1')
        signal = CompiledPlot(plot)(times, times)
        np.testing.assert_allclose(signal[:, 0], .5)
        np.testing.assert_allclose(signal[:, 1], np.where(times < .5, 1., -1.))
        chained = CompiledPlot(FunctionPlot('0.25 <= s < 0.75', '0'))(times, times)
        np.testing.assert_allclose(chained[:, 0], ((times >= .25) & (times < .75)).astype(float))

    def test_variables_are_bound_and_validated(self):
        plot = FunctionPlot('a*sin(tau*s)', 'b', (Variable('a', .5, 0., 1.), Variable('b', -.25, -1., 1.)))
        signal = CompiledPlot(plot.with_variable('a', 1.))(np.array([.025]), np.array([.25]))
        np.testing.assert_allclose(signal[0], [1., -.25, 0.])
        for bad in (Variable('t'), Variable('sin'), Variable('pi'), Variable('if'), Variable('2x'),
                    Variable('a', 5., 0., 1.), Variable('a', 0., 1., 1.), Variable('a', math.nan)):
            with self.assertRaises(ValueError, msg=bad): validate_plot(FunctionPlot(variables=(bad,)))
        with self.assertRaises(ValueError): validate_plot(FunctionPlot(variables=(Variable('a'), Variable('a'))))
        with self.assertRaisesRegex(ValueError, 'unknown name'): CompiledPlot(FunctionPlot('a', '0'))

    def test_variables_can_be_driven_by_t_and_s(self):
        times = np.arange(480)/48000; positions = (times*100) % 1
        driven = FunctionPlot('a*sin(tau*s)', 'b', (Variable('a', 1., 0., 2., '1 + sin(tau*5*t)'), Variable('b', .5, 0., 1., 's*3')))
        signal = CompiledPlot(driven)(times, positions)
        np.testing.assert_allclose(signal[:, 0], (1+np.sin(2*np.pi*5*times))*np.sin(2*np.pi*positions))
        np.testing.assert_allclose(signal[:, 1], np.clip(positions*3, 0, 1))  # Clipped to min–max.
        static = FunctionPlot('a*sin(tau*s)', 'b', (Variable('a', 1., 0., 2.), Variable('b', .5, 0., 1.)))
        self.assertEqual(static.variables[0].document(), dict(name='a', value=1., low=0., high=2.))
        self.assertEqual(FunctionPlot.read(driven.document()), driven)
        for bad in ('a', 'x', 'import os', ''):
            plot = FunctionPlot('a', '0', (Variable('a', 1., 0., 2., bad),))
            if bad == '': validate_plot(plot); continue
            with self.subTest(bad=bad), self.assertRaises(ValueError): validate_plot(plot)

    def test_unsafe_python_is_rejected(self):
        for text in ('__import__("os")', '(1).__class__', 'np.sin(t)', 'open("x")', 'math.__dict__',
                     '[t]', 't[0]', 'lambda: 1', '"text"', 'sin(x=t)', 'sin(*t)', 'math',
                     '__builtins__', '_where(1, 2, 3)', 'x := 1', 't @ t', 't if t is 1 else 0',
                     'sin', 'globals()', 'eval("1")', 'f"{t}"', '1j', 'True', 'sin(t' , ''):
            with self.assertRaises(ValueError, msg=text): parse_expression(text, ())

    def test_runtime_problems_are_contained(self):
        with self.assertRaises(ValueError): CompiledPlot(FunctionPlot('1/0', '0'))
        with self.assertRaises(ValueError): CompiledPlot(FunctionPlot('10.0**10**10', '0'))
        positions = np.linspace(0, 1, 5, endpoint=False)
        signal = CompiledPlot(FunctionPlot('log(s - 1)', '1/(s*0)'))(positions, positions)
        self.assertTrue(np.isfinite(signal).all())
        np.testing.assert_array_equal(signal[:, 0], 0.)

    def test_numbers_too_large_for_a_float_are_value_errors(self):
        # OverflowError used to escape the ValueError handlers: a presets file holding one
        # stopped the app from starting.
        for text in ('9'*330, 'sin(t) + ' + '9'*320):
            with self.assertRaisesRegex(ValueError, 'too large', msg=text[:12]): parse_expression(text, ())
        with self.assertRaises(ValueError): validate_plot(FunctionPlot(variables=(Variable('a', 10**400, 0., 1.),)))
        with self.assertRaises(ValueError):
            FunctionPlot.read(dict(x='t', y='t', frequency=10**400, variables=[]))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'function-presets.json'
            path.write_text(json.dumps(dict(format='phosphor-function-presets', version=1, presets=[
                dict(name='Big', function=dict(x='9'*330, y='t', frequency=110., variables=[]))])))
            with self.assertRaises(ValueError): read_function_presets(path)

    def test_functions_take_exact_argument_counts(self):
        # A NumPy ufunc's extra positional argument is its output array: sin(t, t) overwrote t.
        for text in ('sin(t, t)', 'max(t, 2, t)', 'clip(t, 0, 1, t)', 'round(t, 2)', 'atan2(t)',
                     'math.atan2(t)', 'sin()', 'square(s, s)'):
            with self.assertRaisesRegex(ValueError, 'argument', msg=text): parse_expression(text, ())
        times = np.arange(8)/48000.; before = times.copy()
        plot = CompiledPlot(FunctionPlot('atan2(t, 1) + hypot(s, 1) + fmod(t, 1) + copysign(1, t)',
                                         'min(t, 1) + max(t, 0) + clip(t, 0, 1) + round(t) + math.hypot(t, s)'))
        plot(times, times)
        np.testing.assert_array_equal(times, before)
        for preset in PRESETS.values(): CompiledPlot(preset)

    def test_compiled_plots_are_shared_and_unclipped(self):
        plot = FunctionPlot('2', '-0.5')
        self.assertIs(compiled_plot(plot), compiled_plot(FunctionPlot('2', '-0.5')))
        np.testing.assert_allclose(compiled_plot(plot)(np.zeros(3), np.zeros(3)), [[2., -.5, 0.]]*3)

    def test_document_round_trip_and_presets(self):
        for name, plot in PRESETS.items():
            self.assertEqual(FunctionPlot.read(plot.document()), plot)
            times = np.arange(4800)/48000
            signal = CompiledPlot(plot)(times, (times*110.) % 1)
            self.assertLessEqual(abs(signal).max(), 1., name)
        with self.assertRaises(ValueError): FunctionPlot.read(dict(PRESETS['Circle'].document(), extra=1))
        with self.assertRaises(ValueError): FunctionPlot.read(dict(PRESETS['Circle'].document(), frequency=0))

    def test_scene_files_store_parametric_objects(self):
        from phosphor.engine.signal_engine import Settings, Shape
        from phosphor.storage.app_files import atomic_json, read_scene, scene_document
        plot = PRESETS['Trefoil knot']
        settings = Settings(shapes=(Shape(solid=PARAMETRIC_OBJECT, plot=plot, position_x=.2), Shape()))
        document = scene_document(settings, 'drawing', '')
        self.assertEqual(document['version'], SCENE_VERSION)
        self.assertEqual(document['shapes'][0]['plot'], plot.document())
        self.assertNotIn('plot', document['shapes'][1])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'plot.scene.json'
            atomic_json(path, document)
            self.assertEqual(read_scene(path)[0].shapes, settings.shapes)
            unsafe = dict(document['shapes'][0], plot=dict(plot.document(), x='__import__("os")'))
            for bad in (dict(document, shapes=[unsafe]), dict(document, version=10), dict(document, function=plot.document())):
                atomic_json(path, bad)
                with self.assertRaises(ValueError): read_scene(path)

    def test_parametric_objects_play_the_old_function_exactly_on_a_single_tone(self):
        from phosphor.engine.signal_engine import Settings, Shape, render
        from phosphor.engine.scene_path import INSTANT_TRAVEL
        times = np.arange(9600)/48000
        for name, plot in PRESETS.items():
            settings = Settings(source='Single tone', tone_frequency=90., travel_mode='Faster travel', travel_speed=INSTANT_TRAVEL,
                                shapes=(Shape(solid=PARAMETRIC_OBJECT, plot=plot),))
            drawing = render(times, settings)[0]
            np.testing.assert_allclose(drawing, CompiledPlot(plot)(times, (times*90.) % 1)[:, :2], atol=1e-12, err_msg=name)
            pieces = np.concatenate([render(times[:3333], settings)[0], render(times[3333:], settings)[0]])
            np.testing.assert_array_equal(drawing, pieces)

    def test_parametric_objects_take_transforms_and_share_the_note_cycle(self):
        from phosphor.engine.signal_engine import Settings, Shape, render
        times = np.arange(4800)/48000
        circle = Shape(solid=PARAMETRIC_OBJECT, plot=PRESETS['Circle'])
        moved = Shape(solid=PARAMETRIC_OBJECT, plot=PRESETS['Circle'], position_x=.3, shape_size=.5, rotation_z=90)
        base = render(times, Settings(source='Single tone', shapes=(circle,)))[0]
        shifted = render(times, Settings(source='Single tone', shapes=(moved,)))[0]
        np.testing.assert_allclose(shifted, base @ np.array([[0, 1], [-1, 0]]) * .5 + [.3, 0], atol=1e-12)
        scene = render(times, Settings(shapes=(circle, Shape(position_x=.5))))[0]
        self.assertTrue(np.isfinite(scene).all())
        self.assertGreater(np.ptp(scene[:, 0]), 1.)

    def test_saved_presets_round_trip_and_reject_bad_names(self):
        import tempfile
        from pathlib import Path
        from phosphor.storage.function_presets import (read_function_presets, save_function_preset,
                                                       delete_function_preset)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'function-presets.json'
            self.assertEqual(read_function_presets(path), {})
            mine = PRESETS['Spiral'].with_variable('turns', 9.)
            save_function_preset('  My spiral ', mine, path)
            save_function_preset('Wave', FunctionPlot('saw(s)', 'square(s)'), path)
            presets = read_function_presets(path)
            self.assertEqual(list(presets), ['My spiral', 'Wave'])
            self.assertEqual(presets['My spiral'], mine)
            save_function_preset('My spiral', PRESETS['Circle'], path)  # Replaces in place.
            self.assertEqual(list(read_function_presets(path)), ['My spiral', 'Wave'])
            self.assertEqual(read_function_presets(path)['My spiral'], PRESETS['Circle'])
            for bad in ('', '   ', 'Circle', 'x'*41):
                with self.assertRaises(ValueError, msg=bad): save_function_preset(bad, mine, path)
            self.assertEqual(list(delete_function_preset('Wave', path)), ['My spiral'])
            path.write_text('{"format": "phosphor-function-presets", "presets": [{"name": "a", "function": {}}]}')
            with self.assertRaises(ValueError): read_function_presets(path)
            # Version 1 presets kept an s frequency; it is dropped when they are read.
            path.write_text(json.dumps(dict(format='phosphor-function-presets', version=1, presets=[
                dict(name='Old', function=dict(PRESETS['Heart'].document(), frequency=90.))])))
            self.assertEqual(read_function_presets(path), {'Old': PRESETS['Heart']})

    def test_zero_z_preserves_legacy_vertices_exactly(self):
        from phosphor.engine.signal_engine import parametric_points, PARAMETRIC_STEPS
        positions = np.arange(PARAMETRIC_STEPS+1)/PARAMETRIC_STEPS
        expected = np.column_stack((np.sin(math.tau*positions), np.cos(math.tau*positions),
                                    np.zeros(len(positions))))
        np.testing.assert_array_equal(parametric_points(FunctionPlot(), .75), expected)
        np.testing.assert_array_equal(parametric_points(FunctionPlot(z='0'), .75), expected)
        self.assertFalse(parametric_points(FunctionPlot(), .75).flags.writeable)

    def test_z_uses_sample_names_variables_and_wrapped_positions(self):
        from phosphor.engine.signal_engine import parametric_points, parametric_points_at, PARAMETRIC_STEPS
        plot = FunctionPlot('s', 't', (Variable('a', .25),), z='a + s + t + p')
        times = np.array([.1, .2, .3]); positions = np.array([0., .5, 1.])
        raw = CompiledPlot(plot)(times, positions, 90.)
        self.assertEqual(raw.shape, (3, 3))
        np.testing.assert_allclose(raw[:, 2], .25 + positions + times + times*90/60)
        vertices = parametric_points(plot, .5, 90.)
        np.testing.assert_allclose(vertices[:, 2], .25 + np.arange(PARAMETRIC_STEPS+1)/PARAMETRIC_STEPS + 1.25)
        points, wrapped = parametric_points_at(plot, .5, np.array([-.25, .5, 1.25]), 90.)
        np.testing.assert_array_equal(wrapped, [.75, .5, .25])
        np.testing.assert_allclose(points[:, 2], .25 + wrapped + 1.25)

    def test_z_documents_errors_migration_and_saved_presets(self):
        from phosphor.engine.function_plot import migrate_tempo_variable
        from phosphor.storage.function_presets import save_function_preset
        legacy = dict(x='s', y='t', variables=[])
        self.assertEqual(FunctionPlot.read(legacy).document(), legacy)
        self.assertNotIn('z', FunctionPlot.read(dict(legacy, z='0')).document())
        for name in ('Helix', 'Trefoil knot', 'Toroidal spiral'):
            plot = PRESETS[name]
            self.assertNotEqual(plot.z, '0')
            self.assertEqual(FunctionPlot.read(plot.document()), plot)
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder)/'presets.json'
                save_function_preset('Spatial curve', plot, path)
                self.assertEqual(read_function_presets(path), {'Spatial curve': plot})
        for bad in ('sin(', '__import__("os")', 's[0]', 'unknown', '1/0', None):
            with self.subTest(z=bad), self.assertRaisesRegex(ValueError, '^z:'):
                CompiledPlot(FunctionPlot(z=bad))
        old = dict(x='p*s', y='p', z='p + s', variables=[Variable('p').document()])
        migrated = FunctionPlot.read(migrate_tempo_variable(old))
        self.assertEqual(migrated.z, 'p_ + s')
        np.testing.assert_allclose(CompiledPlot(migrated)(np.zeros(2), [0., 1.])[:, 2], [1., 2.])

    def test_z_reaches_rotation_and_trimmed_audio(self):
        from phosphor.engine.signal_engine import Settings, Shape, render
        from phosphor.engine.transforms import Param
        from phosphor.engine.scene_path import INSTANT_TRAVEL
        times = np.arange(4800)/48000
        # A Y rotation brings a depth-only line into the output X channel.
        shape = Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot('0', '0', z='s'), rotation_y=90)
        settings = Settings(source='Single tone', tone_frequency=90., travel_mode='Faster travel',
                            travel_speed=INSTANT_TRAVEL, shapes=(shape,))
        signal = render(times, settings)[0]
        self.assertGreater(np.ptp(signal[:, 0]), .8)
        np.testing.assert_allclose(signal[:, 1], 0., atol=1e-12)
        from dataclasses import replace
        trimmed = replace(shape, path_start=Param(.25), path_trace=Param(.5))
        cut = render(times, replace(settings, shapes=(trimmed,)))[0]
        self.assertGreater(np.ptp(cut[:, 0]), .4)
        pieces = np.concatenate([render(times[:1234], settings)[0], render(times[1234:], settings)[0]])
        np.testing.assert_array_equal(signal, pieces)


if __name__ == '__main__':
    unittest.main()
