"""Parametric equation objects: a restricted Python-maths evaluator.

Expressions use Python expression syntax with maths names (sin, pi, math.sin...).
They are parsed and checked against a whitelist before compiling, so a scene file
cannot run arbitrary code: no imports, attribute access other than ``math.<name>``,
subscripts, lambdas, comprehensions or keyword arguments. Evaluation is vectorized
with numpy over a whole audio block. ``a if cond else b`` becomes numpy.where, and
``and``/``or``/``not`` become element-wise logic, so they also work on arrays.

Names available in expressions:
- ``t``: time in seconds since playback started, exact for every sample.
- ``p``: beats since the time origin, following the runtime tempo map.
- ``s``: 0-1 position along the object's share of each note cycle. With the Single
  tone source and one object at instant travel, ``s`` is ``(t*frequency) % 1``.
- user variables, and the maths functions and constants in FUNCTIONS/CONSTANTS.
"""
import ast
from dataclasses import dataclass, replace
from functools import lru_cache
import keyword
import math
import re

import numpy as np
from phosphor.engine.automation import gain_at, validate_points, points_document, read_points

MAX_EXPRESSION_LENGTH = 400
MAX_NODES = 300
MAX_VARIABLES = 8
PARAMETRIC_OBJECT = 'Parametric equation'
VARIABLE_LIMIT = 1e6
# _where, _and, _or, _not: the helpers if-else and and/or/not compile to (_HELPERS below).
RESERVED = {'t', 's', 'p', 'math', 'np', '_where', '_and', '_or', '_not'}


def _square(x): return np.where(np.mod(x, 1.) < .5, 1., -1.)
def _saw(x): return 2.*np.mod(x, 1.)-1.
def _tri(x): return 1.-4.*np.abs(np.mod(x, 1.)-.5)


# Periodic helpers take a cycle position (period 1), like s.
FUNCTIONS = dict(
    sin=np.sin, cos=np.cos, tan=np.tan, asin=np.arcsin, acos=np.arccos, atan=np.arctan,
    atan2=np.arctan2, sinh=np.sinh, cosh=np.cosh, tanh=np.tanh, asinh=np.arcsinh,
    acosh=np.arccosh, atanh=np.arctanh, exp=np.exp, log=np.log, log2=np.log2,
    log10=np.log10, sqrt=np.sqrt, abs=np.abs, fabs=np.abs, floor=np.floor, ceil=np.ceil,
    trunc=np.trunc, round=np.round, sign=np.sign, hypot=np.hypot, fmod=np.fmod,
    copysign=np.copysign, degrees=np.degrees, radians=np.radians, min=np.minimum,
    max=np.maximum, clip=np.clip, square=_square, saw=_saw, tri=_tri)
# Exact positional argument counts. NumPy ufuncs take an extra positional "out" array,
# so sin(t, t) would overwrite t for the rest of the block.
ARITY = {name: 2 if name in ('atan2', 'hypot', 'fmod', 'copysign', 'min', 'max') else 3 if name == 'clip' else 1
         for name in FUNCTIONS}
CONSTANTS = dict(pi=math.pi, tau=math.tau, e=math.e)
# math.<name> spellings that differ from the bare names above.
MATH_ALIASES = dict(fabs='abs')
BUILTIN_NAMES = set(FUNCTIONS) | set(CONSTANTS)

_BINARY = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_UNARY = (ast.UAdd, ast.USub, ast.Not)
_COMPARE = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')


@dataclass(frozen=True)
class Variable:
    name: str
    value: float = 1.
    low: float = 0.
    high: float = 10.
    # Empty: the slider value. Otherwise an expression of t, p and s, evaluated with the
    # equations (per sample) and clipped to low..high; value stays as the static fallback.
    expression: str = ''
    points: tuple = ()
    clock: tuple = ()  # Runtime only.

    def document(self):
        value = dict(name=self.name, value=self.value, low=self.low, high=self.high)
        if self.expression: value['expression'] = self.expression
        if self.points: value['points'] = points_document(self.points)
        return value


def migrate_tempo_variable(value, notices=None):
    """Copy an old plot document, renaming only AST Name references to legacy p."""
    if not isinstance(value, dict) or not isinstance(value.get('variables'), list): return value
    variables = value['variables']
    if not any(isinstance(v, dict) and v.get('name') == 'p' for v in variables): return value
    names = {v.get('name') for v in variables if isinstance(v, dict)}
    name, suffix = 'p_', 2
    while name in names:
        name = f'p_{suffix}'; suffix += 1

    def rewrite(text):
        if not text: return text
        try: tree = ast.parse(text.strip(), mode='eval')
        except (SyntaxError, AttributeError): raise ValueError('Cannot migrate invalid variable p expression.') from None
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == 'p': node.id = name
        result = ast.unparse(tree)
        if len(result) > MAX_EXPRESSION_LENGTH:
            raise ValueError(f'Variable p migration exceeds {MAX_EXPRESSION_LENGTH} expression characters.')
        return result

    result = dict(value, x=rewrite(value['x']), y=rewrite(value['y']))
    if 'z' in value: result['z'] = rewrite(value['z'])
    result['variables'] = [dict(v, name=name if v['name'] == 'p' else v['name'],
                              **({'expression': rewrite(v['expression'])} if 'expression' in v else {})) for v in variables]
    if notices is not None:
        notices.append(f'Variable p renamed to {name} (p is now the tempo parameter).')
    return result


@dataclass(frozen=True)
class FunctionPlot:
    x: str = 'sin(tau*s)'
    y: str = 'cos(tau*s)'
    variables: tuple = ()
    z: str = '0'

    def document(self):
        value = dict(x=self.x, y=self.y, variables=[variable.document() for variable in self.variables])
        if self.z != '0': value['z'] = self.z
        return value

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or not {'x', 'y', 'variables'} <= set(value) <= {'x', 'y', 'z', 'variables'}:
            raise ValueError('Function plot fields are missing or unsupported.')
        if not isinstance(value['variables'], list):
            raise ValueError('Function plot variables must be a list.')
        variables = []
        for item in value['variables']:
            if not isinstance(item, dict) or not {'name', 'value', 'low', 'high'} <= set(item) <= {'name', 'value', 'low', 'high', 'expression', 'points'}:
                raise ValueError('Function plot variable fields are missing or unsupported.')
            item = dict(item)
            if 'points' in item: item['points'] = read_points(item['points'], f'Variable {item["name"]}')
            variables.append(Variable(**item))
        plot = cls(value['x'], value['y'], tuple(variables), z=value.get('z', '0'))
        validate_plot(plot)
        return plot

    def with_variable(self, name, value):
        return replace(self, variables=tuple(replace(v, value=value) if v.name == name else v for v in self.variables))


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} must be a finite number.')
    try: number = float(value)  # JSON integers can be too large for a float.
    except OverflowError: raise ValueError(f'{label} must be a finite number.') from None
    if not math.isfinite(number): raise ValueError(f'{label} must be a finite number.')
    return number


def validate_variable_name(name):
    if not isinstance(name, str) or not _NAME.match(name):
        raise ValueError(f'Variable name {name!r} must be letters, digits or _ and not start with a digit.')
    if keyword.iskeyword(name) or name in RESERVED or name in BUILTIN_NAMES:
        raise ValueError(f'{name!r} is reserved; choose another variable name.')


def validate_variable(variable):
    validate_points(variable.points, f'Variable {variable.name}')
    validate_variable_name(variable.name)
    low, high, value = (_number(getattr(variable, key), f'Variable {variable.name} {key}') for key in ('low', 'high', 'value'))
    if not -VARIABLE_LIMIT <= low < high <= VARIABLE_LIMIT:
        raise ValueError(f'Variable {variable.name} needs min < max within ±{VARIABLE_LIMIT:g}.')
    if not low <= value <= high:
        raise ValueError(f'Variable {variable.name} value must be between its min and max.')
    if not isinstance(variable.expression, str): raise ValueError(f'Variable {variable.name}: invalid expression.')
    # Variable expressions see t, p and s only, never other variables.
    if variable.expression: parse_expression(variable.expression, (), f'Variable {variable.name}')


def validate_plot(plot):
    if not isinstance(plot.variables, tuple) or len(plot.variables) > MAX_VARIABLES:
        raise ValueError(f'Use at most {MAX_VARIABLES} variables.')
    for variable in plot.variables: validate_variable(variable)
    names = [variable.name for variable in plot.variables]
    if len(set(names)) != len(names): raise ValueError('Variable names must be unique.')
    for axis in ('x', 'y', 'z'): parse_expression(getattr(plot, axis), names, axis)


class _Vectorize(ast.NodeTransformer):
    """Rewrite Python truth logic and math.<name> into numpy-safe calls."""
    def visit_IfExp(self, node):
        self.generic_visit(node)
        return ast.Call(ast.Name('_where', ast.Load()), [node.test, node.body, node.orelse], [])

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        name = '_and' if isinstance(node.op, ast.And) else '_or'
        result = node.values[0]
        for value in node.values[1:]: result = ast.Call(ast.Name(name, ast.Load()), [result, value], [])
        return result

    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Not): return ast.Call(ast.Name('_not', ast.Load()), [node.operand], [])
        return node

    def visit_Compare(self, node):
        self.generic_visit(node)
        if len(node.ops) == 1: return node
        # a < b < c becomes (a < b) and (b < c), element-wise.
        parts, left = [], node.left
        for op, right in zip(node.ops, node.comparators):
            parts.append(ast.Compare(left, [op], [right])); left = right
        result = parts[0]
        for part in parts[1:]: result = ast.Call(ast.Name('_and', ast.Load()), [result, part], [])
        return result

    def visit_Attribute(self, node):
        return ast.Name(MATH_ALIASES.get(node.attr, node.attr), ast.Load())

    def visit_Constant(self, node):
        # Floats keep huge constant powers from becoming unbounded integers.
        return ast.Constant(float(node.value))


def checked_expression(text, names, label='expression', prepare=None):
    """Shared maths whitelist; an optional preparation checks domain-specific syntax."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f'{label}: enter an expression.')
    if len(text) > MAX_EXPRESSION_LENGTH:
        raise ValueError(f'{label}: keep expressions under {MAX_EXPRESSION_LENGTH} characters.')
    try: tree = ast.parse(text.strip(), mode='eval')
    except SyntaxError as error:
        raise ValueError(f'{label}: syntax error at column {error.offset}: {error.msg}.') from None
    if prepare is not None: tree = prepare(tree)
    names = {*names, *CONSTANTS}
    count = 0
    for node in ast.walk(tree):
        count += 1
        if count > MAX_NODES: raise ValueError(f'{label}: expression is too long.')
        if isinstance(node, (ast.Expression, ast.Load)) or isinstance(node, _BINARY + _UNARY + _COMPARE + (ast.And, ast.Or)):
            continue
        if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp, ast.IfExp)):
            continue
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError(f'{label}: only numbers are allowed as constants.')
            try: float(node.value)  # Constants become floats; a 309-digit integer cannot.
            except OverflowError: raise ValueError(f'{label}: {str(node.value)[:12]}… is too large; numbers must be below 1e308.') from None
            continue
        if isinstance(node, ast.Name):
            continue  # Checked below with its context (call target or value).
        if isinstance(node, ast.Attribute):
            if not (isinstance(node.value, ast.Name) and node.value.id == 'math'):
                raise ValueError(f'{label}: only math.<name> attributes are allowed.')
            attribute = MATH_ALIASES.get(node.attr, node.attr)
            if attribute not in FUNCTIONS and attribute not in CONSTANTS:
                raise ValueError(f'{label}: math.{node.attr} is not available.')
            continue
        if isinstance(node, ast.Call):
            if node.keywords: raise ValueError(f'{label}: keyword arguments are not supported.')
            target = node.func
            name = target.id if isinstance(target, ast.Name) else MATH_ALIASES.get(target.attr, target.attr) if isinstance(target, ast.Attribute) else None
            if name not in FUNCTIONS: raise ValueError(f'{label}: {ast.unparse(target)}() is not an available function.')
            if any(isinstance(arg, ast.Starred) for arg in node.args):
                raise ValueError(f'{label}: * arguments are not supported.')
            if len(node.args) != ARITY[name]:
                count = ARITY[name]
                raise ValueError(f'{label}: {name}() takes {count} argument{"s" if count > 1 else ""}, not {len(node.args)}.')
            continue
        raise ValueError(f'{label}: {type(node).__name__} is not allowed; use a maths expression.')
    calls = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    attributes = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and id(node) not in calls and id(node) not in attributes and node.id not in names:
            known = ', '.join(sorted(names-set(CONSTANTS)))
            if node.id in FUNCTIONS: raise ValueError(f'{label}: {node.id} is a function; call it, e.g. {node.id}(t).')
            raise ValueError(f'{label}: unknown name {node.id!r}. Available: {known}, pi, tau, e.')
    return tree


def parse_expression(text, variables=(), label='expression'):
    """Check text against the whitelist; return the vectorized code object.

    Cached: a slider drag rebuilds the plot on every move, but its text stays the same.
    Code objects are immutable, and errors are raised again each time (not cached).
    """
    if not isinstance(text, str): return _parse_expression(text, variables, label)
    return _cached_expression(text, tuple(variables), label)


@lru_cache(maxsize=512)
def _cached_expression(text, variables, label):
    return _parse_expression(text, variables, label)


def _parse_expression(text, variables, label):
    tree = checked_expression(text, {'t', 's', 'p', *variables}, label)
    tree = ast.fix_missing_locations(_Vectorize().visit(tree))
    return compile(tree, f'<{label}>', 'eval')


_HELPERS = dict(_where=np.where, _and=np.logical_and, _or=np.logical_or, _not=np.logical_not)


class CompiledPlot:
    """Evaluate a validated FunctionPlot at sample times and curve positions s; returns raw (N, 3) X/Y/Z."""
    def __init__(self, plot):
        validate_plot(plot)
        names = [variable.name for variable in plot.variables]
        self.plot = plot
        self.code = tuple(parse_expression(getattr(plot, axis), names, axis) for axis in ('x', 'y', 'z'))
        self.namespace = {'__builtins__': {}, **FUNCTIONS, **CONSTANTS, **_HELPERS,
                          **{variable.name: variable.value for variable in plot.variables}}
        # Time-varying variables: evaluated per call from t and s, before the equations.
        self.varying = tuple((v, parse_expression(v.expression, (), f'Variable {v.name}') if v.expression else None)
                             for v in plot.variables if v.expression or v.points)
        self(np.linspace(0., .05, 64), np.linspace(0., 1., 64))  # Report runtime errors (1/0 constants) before playback.

    def __call__(self, times, positions, tempo=120.):
        times = np.asarray(times, dtype=float)
        namespace = dict(self.namespace, t=times, p=tempo.beats(times) if hasattr(tempo, 'beats') else times * tempo / 60., s=np.asarray(positions, dtype=float))
        result = np.empty((len(times), 3))
        with np.errstate(all='ignore'):
            for variable, code in self.varying:
                name, low, high = variable.name, variable.low, variable.high
                try:
                    value = np.asarray(eval(code, dict(namespace)) if code is not None else variable.value, dtype=float)
                    value = value * gain_at(variable.points, variable.clock, times)
                except (ArithmeticError, ValueError, TypeError) as error:
                    raise ValueError(f'Variable {name}: {error}') from None
                if value.ndim > 1 or (value.ndim == 1 and len(value) != len(times)):
                    raise ValueError(f'Variable {name}: expression must give one value per sample.')
                value = np.nan_to_num(np.broadcast_to(value, (len(times),)).astype(float), nan=low, posinf=high, neginf=low)
                namespace[name] = np.clip(value, low, high)
            for column, code in enumerate(self.code):
                try: value = eval(code, namespace)
                except (ArithmeticError, ValueError, TypeError) as error:
                    raise ValueError(f'{"xyz"[column]}: {error}') from None
                value = np.asarray(value, dtype=float)
                if value.ndim > 1 or (value.ndim == 1 and len(value) != len(times)):
                    raise ValueError(f'{"xyz"[column]}: expression must give one value per sample.')
                result[:, column] = value
        # NaN (log of a negative, 0/0) is silent; infinities clip at the output.
        return np.nan_to_num(result, nan=0., posinf=2., neginf=-2.)


@lru_cache(maxsize=64)
def compiled_plot(plot):
    """Shared compiled form of an immutable plot (scene objects are re-validated often)."""
    return CompiledPlot(plot)


PRESETS = {
    'Helix': FunctionPlot('0.6*cos(3*tau*s)', '0.6*sin(3*tau*s)', z='1.6*s - 0.8'),
    'Trefoil knot': FunctionPlot('0.27*(sin(tau*s)+2*sin(2*tau*s))',
                                 '0.27*(cos(tau*s)-2*cos(2*tau*s))', z='-0.27*sin(3*tau*s)'),
    'Toroidal spiral': FunctionPlot('(0.6+0.2*cos(8*tau*s))*cos(tau*s)',
                                    '(0.6+0.2*cos(8*tau*s))*sin(tau*s)', z='0.2*sin(8*tau*s)'),
    'Circle': FunctionPlot('sin(tau*s)', 'cos(tau*s)'),
    'Lissajous 3:2': FunctionPlot('sin(3*tau*s + phase)', 'sin(2*tau*s)',
                                  (Variable('phase', math.pi/2, 0., math.tau),)),
    'Rose (k petals)': FunctionPlot('cos(k*tau*s)*cos(tau*s)', 'cos(k*tau*s)*sin(tau*s)',
                                    (Variable('k', 4., 1., 12.),)),
    'Spiral': FunctionPlot('s*cos(turns*tau*s)', 's*sin(turns*tau*s)',
                           (Variable('turns', 6., 1., 20.),)),
    'Diamond': FunctionPlot('tri(s)', 'tri(s + 0.25)'),
    'Heart': FunctionPlot('0.055*16*sin(tau*s)**3',
                          '0.055*(13*cos(tau*s) - 5*cos(2*tau*s) - 2*cos(3*tau*s) - cos(4*tau*s))'),
    'Rotating ellipse': FunctionPlot('cos(tau*s)*cos(rate*tau*t) - 0.4*sin(tau*s)*sin(rate*tau*t)',
                                     'cos(tau*s)*sin(rate*tau*t) + 0.4*sin(tau*s)*cos(rate*tau*t)',
                                     (Variable('rate', .25, -2., 2.),)),
    'Stereo tones (t in seconds)': FunctionPlot('0.8*sin(tau*220*t)', '0.8*sin(tau*330*t)'),
}
