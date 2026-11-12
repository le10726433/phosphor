import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from phosphor.storage.function_presets import read_function_presets

from phosphor.engine.function_plot import (CompiledPlot, FunctionPlot, PRESETS, Variable, parse_expression,
                                           plot_signal, validate_plot)


class FunctionPlotTests(unittest.TestCase):
    def test_python_maths_syntax_evaluates_per_sample(self):
        times = np.arange(480)/48000
        plot = FunctionPlot('math.sin(2*math.pi*100*t)', 'cos(tau*s)**2 - 0.5', 50.)
        signal = CompiledPlot(plot)(times)
        np.testing.assert_allclose(signal[:, 0], np.sin(2*np.pi*100*times))
        np.testing.assert_allclose(signal[:, 1], np.cos(2*np.pi*((times*50) % 1))**2-.5)

    def test_s_is_cycle_position_at_frequency(self):
        times = np.array([0., .25, .5, 1.25])
        signal = CompiledPlot(FunctionPlot('s', 't', 1.))(times)
        np.testing.assert_allclose(signal[:, 0], [0., .25, .5, .25])
        np.testing.assert_allclose(signal[:, 1], times)

    def test_constants_broadcast_and_conditionals_vectorize(self):
        times = np.linspace(0, 1, 9, endpoint=False)
        plot = FunctionPlot('0.5', '1 if s < 0.5 and not s > 0.9 else -1', 1.)
        signal = CompiledPlot(plot)(times)
        np.testing.assert_allclose(signal[:, 0], .5)
        np.testing.assert_allclose(signal[:, 1], np.where(times < .5, 1., -1.))
        chained = CompiledPlot(FunctionPlot('0.25 <= s < 0.75', '0', 1.))(times)
        np.testing.assert_allclose(chained[:, 0], ((times >= .25) & (times < .75)).astype(float))

    def test_variables_are_bound_and_validated(self):
        plot = FunctionPlot('a*sin(tau*s)', 'b', 10., (Variable('a', .5, 0., 1.), Variable('b', -.25, -1., 1.)))
        signal = CompiledPlot(plot.with_variable('a', 1.))(np.array([.025]))
        np.testing.assert_allclose(signal[0], [1., -.25])
        for bad in (Variable('t'), Variable('sin'), Variable('pi'), Variable('if'), Variable('2x'),
                    Variable('a', 5., 0., 1.), Variable('a', 0., 1., 1.), Variable('a', math.nan)):
            with self.assertRaises(ValueError, msg=bad): validate_plot(FunctionPlot(variables=(bad,)))
        with self.assertRaises(ValueError): validate_plot(FunctionPlot(variables=(Variable('a'), Variable('a'))))
        with self.assertRaisesRegex(ValueError, 'unknown name'): CompiledPlot(FunctionPlot('a', '0'))

    def test_unsafe_python_is_rejected(self):
        for text in ('__import__("os")', '(1).__class__', 'np.sin(t)', 'open("x")', 'math.__dict__',
                     '[t]', 't[0]', 'lambda: 1', '"text"', 'sin(x=t)', 'sin(*t)', 'math',
                     '__builtins__', '_where(1, 2, 3)', 'x := 1', 't @ t', 't if t is 1 else 0',
                     'sin', 'globals()', 'eval("1")', 'f"{t}"', '1j', 'True', 'sin(t' , ''):
            with self.assertRaises(ValueError, msg=text): parse_expression(text, ())

    def test_runtime_problems_are_contained(self):
        with self.assertRaises(ValueError): CompiledPlot(FunctionPlot('1/0', '0'))
        with self.assertRaises(ValueError): CompiledPlot(FunctionPlot('10.0**10**10', '0'))
        signal = CompiledPlot(FunctionPlot('log(s - 1)', '1/(s*0)', 1.))(np.linspace(0, 1, 5, endpoint=False))
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
        plot(times)
        np.testing.assert_array_equal(times, before)
        for preset in PRESETS.values(): CompiledPlot(preset)

    def test_gain_scales_without_clipping(self):
        signal = plot_signal(CompiledPlot(FunctionPlot('2', '-0.5')), np.zeros(3), .75)
        np.testing.assert_allclose(signal, [[1.5, -.375]]*3)

    def test_document_round_trip_and_presets(self):
        for name, plot in PRESETS.items():
            self.assertEqual(FunctionPlot.read(plot.document()), plot)
            signal = CompiledPlot(plot)(np.arange(4800)/48000)
            self.assertLessEqual(abs(signal).max(), 1., name)
        with self.assertRaises(ValueError): FunctionPlot.read(dict(PRESETS['Circle'].document(), extra=1))
        with self.assertRaises(ValueError): FunctionPlot.read(dict(PRESETS['Circle'].document(), frequency=0))

    def test_scene_files_store_the_plot_and_open_version_8(self):
        import tempfile
        from pathlib import Path
        from phosphor.engine.signal_engine import Settings
        from phosphor.storage.app_files import atomic_json, read_scene, scene_document
        plot = PRESETS['Rose (k petals)'].with_variable('k', 7.)
        document = scene_document(Settings(), 'drawing', '', plot)
        self.assertEqual(document['version'], 10)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'plot.scene.json'
            atomic_json(path, document)
            self.assertEqual(read_scene(path, with_function=True)[3], plot)
            self.assertEqual(len(read_scene(path)), 3)
            old = dict(document, version=8); old.pop('function')
            atomic_json(path, old)
            self.assertEqual(read_scene(path, with_function=True)[3], FunctionPlot())
            for bad in (dict(document, function=dict(document['function'], x='__import__("os")')),
                        {key: value for key, value in document.items() if key != 'function'}):
                atomic_json(path, bad)
                with self.assertRaises(ValueError): read_scene(path, with_function=True)


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


if __name__ == '__main__':
    unittest.main()
