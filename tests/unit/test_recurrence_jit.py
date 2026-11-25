"""Exact recurrence parity and optional compiler fallback contracts."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import unittest
from unittest import mock

import numpy as np
from phosphor.engine import recurrence_jit as jit
from phosphor.engine.function_plot import FUNCTIONS, ARITY, Variable
from phosphor.engine.recurrence import CompiledRecurrence, RecurrencePlot, PRESETS, parse_expression
from tests.unit.golden_scenes import dynamic_lorenz

try:
    import numba
except ImportError:
    numba = None


def dynamic(plot):
    return replace(plot, x=f'({plot.x})+0*t')


def bench_cases():
    return (PRESETS['Lorenz attractor'], dynamic_lorenz(),
            replace(PRESETS['Clifford attractor'], steps=4096,
                    x='(sin(a*y*3)+1.6*cos(a*x*3))/3',
                    variables=(Variable('a', -1.4, -3., 3., expression='-1.4+0.2*sin(t)'),)),
            RecurrencePlot(
                'clip(0.5*x[n-1]+0.3*sin(3*x[n-4])-0.2*x[n-8]+0.1*cos(n*0.01+t)*y[n-2], -1, 1)',
                'clip(0.5*y[n-1]+0.3*cos(3*y[n-3])-0.2*y[n-7]+0.1*sin(n*0.013+t)*x[n-5], -1, 1)',
                '0', order=8, initial=tuple((.1*k, .05*k, 0.) for k in range(1, 9)), steps=4096))


class FallbackTests(unittest.TestCase):
    def test_unsupported(self):
        for text in ('hypot(x,y)', 'abs((-x)**0.5)', 'floor(x)*floor(y)',
                     '((-x)**y)==0', 'not ((-x)**y)', '1 if (-x)**y else 0',
                     '((-x)**y) or 0', '(floor(x)+sign(y))*round(z)'):
            with self.subTest(text=text):
                compiled = CompiledRecurrence(dynamic(RecurrencePlot(text, '0', steps=16)))
                self.assertIsNone(compiled.kernel_source)
                a, stopped = compiled.evaluate(.37, 120.)
                b, expected = compiled.evaluate(.37, 120., jit=False)
                np.testing.assert_array_equal(a, b)
                self.assertEqual(stopped, expected)

    def test_disabled_does_not_request(self):
        compiled = CompiledRecurrence(dynamic(PRESETS['Lorenz attractor']))
        expected = compiled.evaluate(.37, 120., jit=False)
        with mock.patch.object(jit, 'ENABLED', False), mock.patch.object(jit, 'request') as request:
            actual = compiled.evaluate(.37, 120.)
            request.assert_not_called()
        np.testing.assert_array_equal(actual[0], expected[0])
        self.assertEqual(actual[1], expected[1])

    def test_missing_numba(self):
        compiled = CompiledRecurrence(dynamic(PRESETS['Lorenz attractor']))
        expected = compiled.evaluate(.37, 120., jit=False)
        with mock.patch.object(jit, '_numba', False):
            self.assertIsNone(jit.request(compiled.kernel_source))
            self.assertIsNone(jit.wait(compiled.kernel_source))
            actual = compiled.evaluate(.37, 120.)
        np.testing.assert_array_equal(actual[0], expected[0])
        self.assertEqual(actual[1], expected[1])

    def test_ast_input_is_not_mutated_and_names_are_bound(self):
        trees = [parse_expression(text, ['a'], 8) for text in ('a+x[n-8]', 'pi+t+p+N', 'n')]
        import ast
        before = [ast.dump(tree) for tree in trees]
        source = jit.kernel_source(trees, ['a'], 8)
        self.assertIn('_v[0]', source)
        self.assertIn('_hx8', source)
        self.assertEqual(before, [ast.dump(tree) for tree in trees])


@unittest.skipUnless(numba is not None, 'Numba is optional')
class KernelTests(unittest.TestCase):
    def ready(self, plot):
        compiled = CompiledRecurrence(plot)
        self.assertIsNotNone(compiled.kernel_source)
        self.assertIsNotNone(jit.wait(compiled.kernel_source), jit.reason(compiled.kernel_source))
        return compiled

    def compare(self, compiled, seconds=.37):
        actual, stopped = compiled.evaluate(seconds, 120.)
        expected, expected_stop = compiled.evaluate(seconds, 120., jit=False)
        np.testing.assert_array_equal(actual, expected)
        # Compare storage as well: assert_array_equal treats signed zeros as equal.
        np.testing.assert_array_equal(actual.view(np.uint64), expected.view(np.uint64))
        self.assertEqual(stopped, expected_stop)
        self.assertFalse(actual.flags.writeable)

    def test_presets_and_benchmarks(self):
        for plot in (*PRESETS.values(), *bench_cases()):
            compiled = self.ready(dynamic(replace(plot, steps=4096)))
            for seconds in (0., .37, 2.5):
                with self.subTest(plot=plot, seconds=seconds): self.compare(compiled, seconds)

    def test_divergence(self):
        for expression in ('1/0', 'sqrt(-1)', 'exp(1000)', '1e309', '1000001', '(-1)**0.5',
                           'x*10', 'x**400', 'log(x-1)', 'acos(x+2)', 'fmod(x,0)',
                           '0**-1', 'floor(1e309)', 'round(x/0.)'):
            with self.subTest(expression=expression):
                self.compare(self.ready(dynamic(RecurrencePlot(expression, '0', initial=((.25, .5, .75),)))))

    def test_helpers(self):
        values = (0., -0., 1., -1., .5, -.5, 2.5, -2.5, 1e300, -1e300)
        for name in FUNCTIONS:
            if name == 'hypot': continue
            args = ['x*1.7-0.3', 'y', 'z'][:ARITY[name]]
            plot = dynamic(RecurrencePlot(f'{name}({",".join(args)})', 'y', 'z', steps=16))
            compiled = self.ready(plot)
            for x in values:
                with self.subTest(name=name, x=x):
                    # Initial rows do not affect the kernel shape.
                    compiled.plot = replace(plot, initial=((x, -.5, .5),))
                    self.compare(compiled)
        # Exercise NaN and infinity *inside* the recurrence, not invalid initial rows.
        for name in FUNCTIONS:
            if name == 'hypot': continue
            for special in ('(1e309-1e309)', '1e309', '-1e309'):
                args = [special, '0', '1'][:ARITY[name]]
                with self.subTest(name=name, special=special):
                    self.compare(self.ready(dynamic(RecurrencePlot(f'{name}({",".join(args)})', '0', steps=16))))

    def test_powers(self):
        for expression in ('x**2', 'x**0.5', 'x**-1', '(-x)**3', 'x**y', '2**x'):
            compiled = self.ready(dynamic(RecurrencePlot(expression, 'y', steps=16, initial=((.7, 1.3, 0.),))))
            self.compare(compiled)
        for a in ('0', '-0.', '1', '-1', '2', '-2', '1e309', '-1e309', '(1e309-1e309)'):
            for b in ('0', '-1', '0.5', '3', '1e300', '1e309', '-1e309', '(1e309-1e309)'):
                with self.subTest(a=a, b=b):
                    self.compare(self.ready(dynamic(RecurrencePlot(f'({a})**({b})', '0', steps=16))))

    def test_bools_short_circuit_and_mixed_types(self):
        for expression in ('math.sin(pi/2)+tri(0.5)+square(0)+saw(0.5)',
                           'clip(min(-2, 1)+max(2, 1), -1, 1)', '0 if n > 0 or 1/0 else 1/0',
                           '(x > 0)+(y < 0)*2-0.5', 'round(x*3)/3+sign(y)*0.1',
                           'x*0.9 if x > 0.5 and y else -y*0.9', 'not x', '-(x > y)',
                           'min(x, (x > 0.2))', '0.5 < x < 0.8', 'x // 0.3 + x % 0.3'):
            with self.subTest(expression=expression):
                compiled = self.ready(dynamic(RecurrencePlot(expression, 'y*0.97+0.01', steps=64,
                                                             initial=((.6, .3, 0.),))))
                for seconds in (0., .37): self.compare(compiled, seconds)

    def test_history_variables_json_and_threads(self):
        plot = RecurrencePlot('0.3*x[n-8]+a', 'y[n-3]', 'z[n-1]', order=8,
            initial=tuple((i, i+1, i+2) for i in range(8)), steps=4096,
            variables=(Variable('a', .2, -1., 1., expression='0.2*sin(t)',
                                points=((0., 1., 'linear'), (4., 0., 'linear'))),))
        plot = RecurrencePlot.read(json.loads(json.dumps(plot.document())))
        compiled = self.ready(plot)
        for seconds in (0., .37, 2.5): self.compare(compiled, seconds)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: compiled.evaluate(.37, 120.), range(2)))
        expected = compiled.evaluate(.37, 120., jit=False)
        for points, stopped in results:
            np.testing.assert_array_equal(points, expected[0])
            self.assertEqual(stopped, expected[1])


if __name__ == '__main__': unittest.main()
