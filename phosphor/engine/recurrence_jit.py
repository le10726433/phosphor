"""Optional, lazily compiled scalar recurrence kernels.

Only validated expression trees enter the source generator. Compilation and the
Numba import happen on one background thread; audio never waits for either.
"""
import ast
from collections import OrderedDict
import copy
import ctypes
import gc
import math
import os
import sys
import threading

import numpy as np
from phosphor.engine.function_plot import ARITY, CONSTANTS

ENABLED = os.environ.get('PHOSPHOR_NUMBA', '1') != '0'


class Unsupported(ValueError):
    pass


def _integer(node):
    """Return (int-valued, derives from an unbounded Python int)."""
    if isinstance(node, ast.Call):
        name = node.func.id if isinstance(node.func, ast.Name) else ''
        return name in ('floor', 'ceil', 'trunc', 'round', 'sign'), name in ('floor', 'ceil', 'trunc', 'round')
    if isinstance(node, ast.Compare) or isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return True, False
    if isinstance(node, ast.UnaryOp):
        return _integer(node.operand)
    if isinstance(node, ast.BoolOp):
        parts = [_integer(value) for value in node.values]
        return all(p[0] for p in parts), any(p[1] for p in parts)
    if isinstance(node, ast.BinOp) and not isinstance(node.op, ast.Div):
        a, b = _integer(node.left), _integer(node.right)
        return a[0] and b[0], a[1] or b[1]
    return False, False


def _check(tree):
    parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            a, b = _integer(node.left), _integer(node.right)
            if a[0] and b[0] and (a[1] or b[1]):
                raise Unsupported('Python integer arithmetic')
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Pow):
            continue
        exponent = node.right
        if isinstance(exponent, ast.Constant) and math.isfinite(exponent.value) and float(exponent.value).is_integer():
            continue
        child = node
        while id(child) in parents:
            parent = parents[id(child)]
            if (isinstance(parent, (ast.Compare, ast.BoolOp))
                    or isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not)
                    or isinstance(parent, ast.IfExp) and parent.test is child
                    or isinstance(parent, ast.Call) and parent.func.id == 'abs'):
                raise Unsupported('Complex power may escape into a real result')
            if (isinstance(parent, ast.BinOp)
                    or isinstance(parent, ast.UnaryOp) and isinstance(parent.op, (ast.UAdd, ast.USub))
                    or isinstance(parent, ast.IfExp) and parent.test is not child):
                child = parent
            else:
                break


class _KernelExpression(ast.NodeTransformer):
    def __init__(self, names, order):
        self.names = list(names)
        self.history = {f'_h{axis}{k}' for axis in 'xyz' for k in range(1, order+1)}

    def visit_Name(self, node):
        name = node.id
        if name in self.names:
            return ast.Subscript(ast.Name('_v', ast.Load()), ast.Constant(self.names.index(name)), ast.Load())
        if name in ('x', 'y', 'z', 'n', 'N', 't', 'p'):
            return ast.Name('_' + name, ast.Load())
        if name in self.history:
            return node
        if name in CONSTANTS:
            return ast.Constant(float(CONSTANTS[name]))
        raise Unsupported(f'Unknown name: {name}')

    def visit_Call(self, node):
        if (not isinstance(node.func, ast.Name) or node.func.id not in ARITY
                or node.func.id == 'hypot' or node.keywords
                or len(node.args) != ARITY[node.func.id]):
            raise Unsupported('Unsupported function or argument count')
        return ast.Call(ast.Name('_f_' + node.func.id, ast.Load()), [self.visit(a) for a in node.args], [])

    def visit_BinOp(self, node):
        if isinstance(node.op, ast.Pow):
            return ast.Call(ast.Name('_f_pow', ast.Load()), [self.visit(node.left), self.visit(node.right)], [])
        return self.generic_visit(node)

    def generic_visit(self, node):
        allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp,
                   ast.IfExp, ast.Constant, ast.Load, ast.Add, ast.Sub, ast.Mult,
                   ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.UAdd, ast.USub,
                   ast.Not, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
                   ast.And, ast.Or)
        if not isinstance(node, allowed):
            raise Unsupported(f'Unsupported node: {type(node).__name__}')
        if isinstance(node, ast.Constant) and type(node.value) not in (int, float):
            raise Unsupported('Only numeric constants are supported')
        return super().generic_visit(node)


def kernel_source(trees, variable_names, order):
    expressions = []
    for tree in trees:
        _check(tree)
        transformed = _KernelExpression(variable_names, order).visit(copy.deepcopy(tree))
        expressions.append(ast.unparse(ast.fix_missing_locations(transformed).body))
    # This template contains only internal names and integer indices. All expression
    # source comes from the transformed AST, never from scene text.
    lines = ['def _kernel(_pts, _order, _steps, _N, _t, _p, _v, _progress):',
             '    for _i in range(_order, _steps):', '        _progress[0] = _i']
    for column, axis in enumerate('xyz'):
        lines.append(f'        _{axis} = _pts[_i-1, {column}]')
        for k in range(1, order+1):
            lines.append(f'        _h{axis}{k} = _pts[_i-{k}, {column}]')
    lines.append('        _n = float(_i)')
    for name, expression in zip('abc', expressions):
        lines.append(f'        _{name} = ({expression}) * 1.')
    lines.extend(['        if not (abs(_a) <= 1e6 and abs(_b) <= 1e6 and abs(_c) <= 1e6):',
                  '            return _i'])
    for column, name in enumerate('abc'):
        lines.append(f'        _pts[_i, {column}] = _{name}')
    lines.append('    return -1')
    return '\n'.join(lines) + '\n'


# Keep the library alive for the lifetime of all dispatchers.
_libm = ctypes.CDLL('ucrtbase' if sys.platform == 'win32' else None)
_c_pow = _libm.pow
_c_pow.argtypes = (ctypes.c_double, ctypes.c_double)
_c_pow.restype = ctypes.c_double


def _pow(a, b):
    a, b = (a * 1.), (b * 1.)
    if b == 0.:
        return 1.
    if math.isnan(a):
        return a
    if math.isnan(b):
        return 1. if a == 1. else b
    odd = math.isfinite(b) and abs(b) % 2. == 1.
    if math.isinf(b):
        if abs(a) == 1.: return 1.
        if (abs(a) > 1.) == (b > 0.): return math.inf
        return 0.
    if math.isinf(a):
        result = math.inf if b > 0. else 0.
        return -result if a < 0. and odd else result
    if a == 0.:
        if b < 0.: raise ZeroDivisionError('zero to a negative power')
        return a if odd else 0.
    negative = a < 0.
    if negative:
        if b != np.floor(b): raise ValueError('complex power')
        a = -a
    if a == 1.:
        return -1. if negative and odd else 1.
    result = _c_pow(a, b)
    if math.isinf(result): raise OverflowError('power overflow')
    return -result if negative and odd else result


def _atan2(x, y):
    x, y = (x * 1.), (y * 1.)
    r = math.atan2(x, y)
    if math.isnan(r) and not math.isnan(x) and not math.isnan(y): raise ValueError('math domain error')
    if math.isinf(r) and math.isfinite(x) and math.isfinite(y): raise OverflowError('math range error')
    return r


def _fmod(x, y):
    x, y = (x * 1.), (y * 1.)
    if math.isinf(y) and math.isfinite(x): return x
    r = np.fmod(x, y)
    if math.isnan(r) and not math.isnan(x) and not math.isnan(y): raise ValueError('math domain error')
    return r


def _copysign(x, y): return math.copysign((x * 1.), (y * 1.))
# CPython 3.12 mathmodule.c uses these radToDeg / degToRad constants.
def _degrees(x): return (x * 1.) * (180.0 / math.pi)
def _radians(x): return (x * 1.) * (math.pi / 180.0)
def _abs(x): return abs((x * 1.))
def _sign(x):
    x = (x * 1.)
    return float(int(x > 0) - int(x < 0))
def _min(a, b):
    a, b = (a * 1.), (b * 1.)
    return b if b < a else a
def _max(a, b):
    a, b = (a * 1.), (b * 1.)
    return b if b > a else a
def _clip(x, lo, hi):
    x, lo, hi = (x * 1.), (lo * 1.), (hi * 1.)
    a = lo if lo > x else x
    return hi if hi < a else a
def _square(x): return 1. if (x * 1.) % 1. < .5 else -1.
def _saw(x): return 2.*((x * 1.) % 1.)-1.
def _tri(x): return 1.-4.*abs((x * 1.) % 1.-.5)


def _math_helper(function):
    def helper(x):
        x = (x * 1.)
        r = function(x)
        if r != r and x == x: raise ValueError('math domain error')
        if math.isinf(r) and math.isfinite(x):
            if r < 0.: raise ValueError('math domain error')
            raise OverflowError('math range error')
        return r
    return helper


def _integer_helper(function):
    def helper(x):
        x = (x * 1.)
        if math.isnan(x): raise ValueError('cannot convert NaN to integer')
        if math.isinf(x): raise OverflowError('cannot convert infinity to integer')
        return function(x) + 0.0
    return helper


_HELPERS = dict(pow=_pow, atan2=_atan2, fmod=_fmod, copysign=_copysign,
                degrees=_degrees, radians=_radians, abs=_abs, fabs=_abs,
                sign=_sign, min=_min, max=_max, clip=_clip,
                square=_square, saw=_saw, tri=_tri)
for _name in ('sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'sinh', 'cosh',
              'tanh', 'asinh', 'acosh', 'atanh', 'exp', 'log', 'log2', 'log10', 'sqrt'):
    _HELPERS[_name] = _math_helper(getattr(math, _name))
for _name, _function in (('floor', np.floor), ('ceil', np.ceil), ('trunc', np.trunc), ('round', np.rint)):
    _HELPERS[_name] = _integer_helper(_function)

_condition = threading.Condition()
_cache = OrderedDict()
_pending = OrderedDict()
_reasons = {}
_UNSUPPORTED = object()
_numba = None  # False means unavailable permanently; also injectable in tests.
_worker_thread = None
_compiling = None


def _remember(source, value, message=None):
    _cache[source] = value
    _cache.move_to_end(source)
    if message is not None: _reasons[source] = message
    # Failure reasons survive eviction so failed sources are never retried.
    while len(_cache) > 64:
        _cache.popitem(last=False)
    _condition.notify_all()


def _worker():
    global _numba, _compiling
    try:
        if _numba is False: raise ImportError('Numba unavailable')
        import numba
    except Exception as error:
        with _condition:
            _numba = False
            for source in _pending:
                _remember(source, _UNSUPPORTED, f'Numba unavailable: {type(error).__name__}: {error}')
            _pending.clear()
            _condition.notify_all()
        return
    with _condition:
        _numba = numba
    dispatchers = None
    while True:
        with _condition:
            while not _pending: _condition.wait()
            source, _ = _pending.popitem(last=True)
            _compiling = source
        # A compile allocates enough to trigger a full collection, which holds the GIL
        # for ~15 ms (measured 21 ms vs 8 ms worst hold); the audio thread needs it.
        collecting = gc.isenabled()
        gc.disable()
        try:
            if dispatchers is None:
                dispatchers = {'_f_' + name: numba.njit(nogil=True)(helper) for name, helper in _HELPERS.items()}
            namespace = dict(dispatchers, __builtins__={}, float=float, abs=abs, range=range)
            exec(compile(source, '<recurrence kernel>', 'exec'), namespace)
            kernel = numba.njit(nogil=True)(namespace['_kernel'])
            f, i = numba.float64, numba.int64
            kernel.compile(i(f[:, ::1], i, i, f, f, f, f[::1], i[::1]))
            message = None
        except Exception as error:
            kernel, message = _UNSUPPORTED, f'{type(error).__name__}: {error}'
        finally:
            if collecting: gc.enable()
        with _condition:
            _remember(source, kernel, message)
            _compiling = None


def request(source):
    global _worker_thread
    if not ENABLED or source is None: return None
    with _condition:
        if _numba is False: return None
        if source in _cache:
            value = _cache[source]
            _cache.move_to_end(source)
            return None if value is _UNSUPPORTED else value
        if source in _reasons: return None
        if source != _compiling:
            _pending[source] = None
            _pending.move_to_end(source)
            while len(_pending) > 8:  # Dropped sources are queued again when next used.
                _pending.popitem(last=False)
        if _worker_thread is None:
            _worker_thread = threading.Thread(target=_worker, name='recurrence-jit', daemon=True)
            _worker_thread.start()
        _condition.notify_all()
    return None


def wait(source, timeout=None):
    kernel = request(source)
    if kernel is not None or not ENABLED or source is None: return kernel
    with _condition:
        _condition.wait_for(lambda: source in _cache or source in _reasons or _numba is False, timeout)
        value = _cache.get(source)
        return None if value is _UNSUPPORTED else value


def reason(source):
    with _condition:
        return 'Numba unavailable' if _numba is False else _reasons.get(source)


def status():
    with _condition:
        return dict(numba=getattr(_numba, '__version__', None),
                    ready=sum(v is not _UNSUPPORTED for v in _cache.values()),
                    pending=len(_pending) + int(_compiling is not None))
