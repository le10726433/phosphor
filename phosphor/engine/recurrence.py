"""Checked scalar recurrences, recomputed independently at each pose time."""
import ast
from dataclasses import dataclass, replace
from functools import lru_cache
import math
from types import FunctionType

import numpy as np
from phosphor.engine.function_plot import (Variable, FunctionPlot, FUNCTIONS, CONSTANTS,
    MATH_ALIASES, MAX_VARIABLES, checked_expression, validate_variable, _number)
from phosphor.engine.automation import gain_at
from phosphor.engine import recurrence_jit

RECURRENCE_OBJECT = 'Recurrence equation'
NAMES = {'x', 'y', 'z', 'n', 'N', 't', 'p'}


@dataclass(frozen=True)
class RecurrencePlot:
    x: str = 'sin(n*0.1)'
    y: str = 'cos(n*0.1)'
    z: str = '0'
    order: int = 1
    initial: tuple = ((0., 0., 0.),)
    steps: int = 1024
    draw: str = 'path'
    variables: tuple = ()

    def document(self):
        return dict(x=self.x, y=self.y, z=self.z, order=self.order,
                    initial=[list(row) for row in self.initial], steps=self.steps,
                    draw=self.draw, variables=[v.document() for v in self.variables])

    @classmethod
    def read(cls, value):
        fields = {'x', 'y', 'z', 'order', 'initial', 'steps', 'draw', 'variables'}
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError('Recurrence fields are missing or unsupported.')
        # Use the existing strict variable reader, including automation points.
        variables = FunctionPlot.read(dict(x='0', y='0', variables=value['variables'])).variables
        try: initial = tuple(tuple(row) for row in value['initial'])
        except TypeError: raise ValueError('Invalid initial values.') from None
        plot = cls(**dict(value, initial=initial, variables=variables))
        compiled_recurrence(plot)
        return plot

    def with_variable(self, name, value):
        return replace(self, variables=tuple(replace(v, value=value) if v.name == name else v for v in self.variables))


class _History(ast.NodeTransformer):
    def __init__(self, order, label): self.order, self.label = order, label

    def visit_Subscript(self, node):
        index = node.slice
        if not (isinstance(node.value, ast.Name) and node.value.id in ('x', 'y', 'z')
                and isinstance(index, ast.BinOp) and isinstance(index.op, ast.Sub)
                and isinstance(index.left, ast.Name) and index.left.id == 'n'
                and isinstance(index.right, ast.Constant) and type(index.right.value) is int
                and 1 <= index.right.value <= self.order):
            raise ValueError(f'{self.label}: history must be x[n-k], y[n-k] or z[n-k], with literal k in 1–{self.order}.')
        return ast.copy_location(ast.Name(f'_h{node.value.id}{index.right.value}', ast.Load()), node)


class _Scalar(ast.NodeTransformer):
    def visit_Attribute(self, node):
        return ast.copy_location(ast.Name(MATH_ALIASES.get(node.attr, node.attr), ast.Load()), node)

    def visit_Constant(self, node):
        return ast.copy_location(ast.Constant(float(node.value)), node)


def parse_expression(text, variables=(), order=1, label='expression', variable=False):
    names = {'t', 'p'} if variable else NAMES | set(variables)
    history = {f'_h{axis}{k}' for axis in 'xyz' for k in range(1, order+1)} if not variable else set()
    # Generated history identifiers cannot be typed directly by scene authors.
    if isinstance(text, str) and any(name in text for name in history):
        raise ValueError(f'{label}: private history names are unavailable.')
    tree = checked_expression(text, names | history, label,
                              None if variable else _History(order, label).visit)
    return ast.fix_missing_locations(_Scalar().visit(tree))


def validate_plot(plot):
    if type(plot.order) is not int or not 1 <= plot.order <= 8:
        raise ValueError('Order must be 1–8.')
    if type(plot.steps) is not int or not 16 <= plot.steps <= 4096:
        raise ValueError('Steps must be 16–4096.')
    if plot.draw not in ('path', 'points'): raise ValueError('Draw must be path or points.')
    if not isinstance(plot.initial, tuple) or len(plot.initial) != plot.order:
        raise ValueError('Initial values need exactly Order rows.')
    for row in plot.initial:
        if not isinstance(row, tuple) or len(row) != 3:
            raise ValueError('Initial values need three finite numbers per row.')
        for value in row: _number(value, 'Initial value')
    if not isinstance(plot.variables, tuple) or len(plot.variables) > MAX_VARIABLES:
        raise ValueError(f'Use at most {MAX_VARIABLES} variables.')
    names = [v.name for v in plot.variables]
    if len(set(names)) != len(names): raise ValueError('Variable names must be unique.')
    for v in plot.variables:
        validate_variable(v)
        if v.name in NAMES or v.name in ('_step', '_history') or v.name.startswith(('_hx', '_hy', '_hz')):
            raise ValueError(f'{v.name!r} is reserved.')
        if v.expression: parse_expression(v.expression, label=f'Variable {v.name}', variable=True)
    for axis in 'xyz': parse_expression(getattr(plot, axis), names, plot.order, axis)


SCALARS = {name: getattr(math, name) for name in FUNCTIONS if hasattr(math, name)}
SCALARS.update(abs=abs, round=round, min=min, max=max,
    clip=lambda x, low, high: min(max(x, low), high),
    sign=lambda x: (x > 0) - (x < 0), square=lambda x: 1. if x % 1. < .5 else -1.,
    saw=lambda x: 2.*(x % 1.)-1., tri=lambda x: 1.-4.*abs(x % 1.-.5))


class CompiledRecurrence:
    def __init__(self, plot):
        validate_plot(plot)
        self.plot = plot
        names = [v.name for v in plot.variables]
        trees = [parse_expression(getattr(plot, axis), names, plot.order, axis) for axis in 'xyz']
        # One compiled scalar function avoids three eval calls per step. History uses a ring.
        bindings = [f'x, y, z = _history[(n-1) % {plot.order}]']
        for k in range(1, plot.order+1):
            bindings.append(f'_hx{k}, _hy{k}, _hz{k} = _history[(n-{k}) % {plot.order}]')
        source = 'def _step(_history, n):\n    ' + '\n    '.join(bindings)
        source += '\n    n = n*1.0\n    return (' + ', '.join(ast.unparse(t.body) for t in trees) + ')\n'
        namespace = {'__builtins__': {}}
        exec(compile(source, '<recurrence step>', 'exec'), namespace)
        self.step_code = namespace.pop('_step').__code__
        self.varying = [(v, compile(parse_expression(v.expression, variable=True), '<variable>', 'eval') if v.expression else None)
                        for v in plot.variables]
        self.dynamic = any(v.expression or v.points for v in plot.variables) or any(
            isinstance(node, ast.Name) and node.id in ('t', 'p') for tree in trees for node in ast.walk(tree))
        self.kernel_source = None
        if self.dynamic:
            try: self.kernel_source = recurrence_jit.kernel_source(trees, names, plot.order)
            except recurrence_jit.Unsupported: pass

    def evaluate(self, seconds, tempo, jit=True):
        plot = self.plot
        namespace = dict(SCALARS, **CONSTANTS, __builtins__={},
                         N=float(plot.steps), t=float(seconds),
                         p=float(tempo.beats(seconds) if hasattr(tempo, 'beats') else seconds*tempo/60.))
        for v, code in self.varying:
            try:
                value = float(eval(code, namespace)) if code is not None else v.value
                value *= float(gain_at(v.points, v.clock, seconds))
                if math.isnan(value): value = v.low
                namespace[v.name] = float(min(max(value, v.low), v.high))
            except (ArithmeticError, ValueError, TypeError): namespace[v.name] = float(v.low)
        kernel = (recurrence_jit.request(self.kernel_source)
                  if jit and self.kernel_source is not None and recurrence_jit.ENABLED else None)
        if kernel is None:
            return self._interpret(namespace)
        points = np.empty((plot.steps, 3)); points[:plot.order] = plot.initial
        progress = np.zeros(1, np.int64)
        values = np.array([namespace[v.name] for v in plot.variables], dtype=float)
        try:
            result = kernel(points, plot.order, plot.steps, namespace['N'], namespace['t'], namespace['p'], values, progress)
            stopped = int(result) if result >= 0 else None
        except (ArithmeticError, ValueError, TypeError):
            stopped = int(progress[0])
        if stopped is not None: points[stopped:] = points[stopped-1]
        points.setflags(write=False)
        return points, stopped

    def _interpret(self, namespace):
        plot = self.plot
        step = FunctionType(self.step_code, namespace)
        history = [tuple(map(float, row)) for row in plot.initial]
        points = np.empty((plot.steps, 3)); points[:plot.order] = plot.initial
        stopped = None
        for n in range(plot.order, plot.steps):
            try:
                row = tuple(float(v) for v in step(history, n))
                if any(not math.isfinite(v) or abs(v) > 1e6 for v in row): raise ValueError('diverged')
            except (ArithmeticError, ValueError, TypeError):
                points[n:] = points[n-1]; stopped = n; break
            points[n] = row; history[n % plot.order] = row
        points.setflags(write=False)
        return points, stopped


@lru_cache(maxsize=64)
def _compiled_recurrence(plot): return CompiledRecurrence(plot)


def compiled_recurrence(plot):
    # The cache compares plots with ==, where True == 1 and 16. == 16: a cached valid plot must not
    # answer for one whose order or steps is not an int (0.80.0; an Objects-list Replace compiles
    # the default recurrence early). CompiledRecurrence raises for those.
    if type(plot.order) is not int or type(plot.steps) is not int: return CompiledRecurrence(plot)
    return _compiled_recurrence(plot)


compiled_recurrence.cache_clear = _compiled_recurrence.cache_clear
compiled_recurrence.cache_info = _compiled_recurrence.cache_info


@lru_cache(maxsize=256)
def _sequence(plot, seconds, tempo): return compiled_recurrence(plot).evaluate(seconds, tempo)


@lru_cache(maxsize=64)
def _static_sequence(plot): return compiled_recurrence(plot).evaluate(0., 120.)


def sequence(plot, seconds=0., tempo=120.):
    compiled = compiled_recurrence(plot)
    return _sequence(plot, float(seconds), tempo) if compiled.dynamic else _static_sequence(plot)


def recurrence_points(plot, seconds=0., tempo=120.):
    return sequence(plot, seconds, tempo)[0]


def recurrence_points_at(plot, seconds, positions, tempo=120.):
    positions = np.asarray(positions, dtype=float)
    wrapped = np.where(positions > 1, positions-1, np.where(positions < 0, positions+1, positions))
    s = np.clip(wrapped, 0., 1.)
    points = recurrence_points(plot, seconds, tempo)
    if plot.draw == 'points': return points[np.minimum((s*plot.steps).astype(int), plot.steps-1)], wrapped
    u = s*(plot.steps-1); i = u.astype(int); j = np.minimum(i+1, plot.steps-1)
    return points[i] + (u-i)[:, None]*(points[j]-points[i]), wrapped


PRESETS = {
    'Hénon map': RecurrencePlot('0.5 - 2.8*x*x + y', '0.3*x', draw='points'),
    'Clifford attractor': RecurrencePlot('(sin(-1.4*y*3)+1.6*cos(-1.4*x*3))/3',
        '(sin(1.6*x*3)+0.7*cos(1.6*y*3))/3', initial=((.1, .1, 0.),), draw='points'),
    'Peter de Jong attractor': RecurrencePlot('(sin(1.4*y*2)-cos(-2.3*x*2))/2',
        '(sin(2.4*x*2)-cos(-2.1*y*2))/2', draw='points'),
    'Lorenz attractor': RecurrencePlot('x+0.01*10*(y-x)',
        'y+0.01*(x*(28-(z*30+25))-y)', 'z+0.01*(x*y*30-(8/3)*(z+25/30))',
        initial=((.01, .01, -.8),), steps=4096),
    'Second-order oscillator': RecurrencePlot('2*cos(w)*x-x[n-2]', '2*cos(w)*y-y[n-2]',
        order=2, initial=((1., 0., 0.), (math.cos(.1), .5*math.sin(.1), 0.)),
        variables=(Variable('w', .1, .001, 1.),)),
    'Sunflower': RecurrencePlot('sqrt(n/N)*cos(n*pi*(3-sqrt(5)))',
        'sqrt(n/N)*sin(n*pi*(3-sqrt(5)))', draw='points'),
}
