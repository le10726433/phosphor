"""Per-object transform stacks: ordered steps, each number static or time varying.

A stack runs top to bottom in the object's own space, before Size, Position
and any motion-path follow rotation. Every step is
evaluated once per pose frame (Settings.drawing_hz) at t = frame time, so it costs
nothing per sample, except Sample Noise, which displaces every output sample.

A time-varying number is an expression in the parametric-object whitelist
(function_plot.parse_expression) with these names:
- t: pose time in seconds;
- p: beats since that clock's time origin, following runtime tempo changes;
- x, y, z: the point's coordinates in object space just before this step;
- s: the point's 0-1 position along the object's route (its first visit).
Because x, y, z and s differ per point, Rotate Y = 90*y twists and Scale = 1+0.5*z
tapers. Static numbers must lie in their field's range; Scale, Shear, Translate,
Motion path, Perspective and noise results are clipped to it, angles are not.

Map is formula-only: X′, Y′ and Z′ are expressions of the same names giving the new
coordinates. All three read the point from before the step; NaN and infinity become 0,
and results are clipped to Translate's range. A formula that is just its own axis name (the default)
passes that coordinate through untouched, so a new Map step changes nothing.

Object variables (T5, 0.81.0): Shape.transform_variables, function_plot.Variables shared by
the whole stack and by Path Start and Trace. Each is evaluated once per pose frame, before the
stack (variable_values), and every step and path expression reads it by name.
"""
import ast
import unicodedata
from dataclasses import dataclass, replace
from functools import lru_cache
import math

import numpy as np

from phosphor.engine.function_plot import (parse_expression, FUNCTIONS, CONSTANTS, _HELPERS, Variable, MAX_VARIABLES,
                                           validate_variable, validate_variable_name, MAX_EXPRESSION_LENGTH)
from phosphor.engine.automation import gain_at, validate_points, points_document, read_points
from phosphor.engine import noise_loops
from phosphor.engine.switch_lane import validate_switch


ROTATE, SPIN, TRANSLATE, SCALE, SHEAR = 'Rotate', 'Spin', 'Translate', 'Scale', 'Shear'
MIRROR, MOTION_PATH, PERSPECTIVE, NOISE = 'Mirror', 'Motion path', 'Perspective', 'Noise'
MAP = 'Map'
OLD_NOISE = 'White noise'
COLOURS = ('White', 'Pink', 'Brownian', 'Custom')
RATES = ('Sample', 'Wobble')
KINDS = (ROTATE, SPIN, TRANSLATE, SCALE, SHEAR, MIRROR, MOTION_PATH, PERSPECTIVE, MAP, NOISE)
MAX_TRANSFORMS = 16
EXPRESSION_NAMES = ('x', 'y', 'z')  # Plus t, p and s, which parse_expression always allows.


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    default: float
    low: float
    high: float
    clip: bool = True  # Clip time-varying results to the range (angles wrap instead).
    toggle: bool = False  # On/off; never time varying.
    integer: bool = False  # Static whole number; never time varying.
    curve: bool = False  # Slider spacing for wide ranges (SliderInput curve).
    formula: bool = False  # Always an expression (Map); never static or automated.


def _axes(label, default, low, high, clip=True, curve=False):
    return tuple(Field(axis, label.format(axis.upper()), default, low, high, clip, curve=curve) for axis in 'xyz')


FIELDS = {
    ROTATE: _axes('{} (°)', 0., -180., 180., clip=False),
    SPIN: _axes('{} (rev/s)', 0., -10., 10., clip=False, curve=True),
    TRANSLATE: _axes('{}', 0., -10., 10., curve=True),
    SCALE: _axes('{} (×)', 1., -10., 10., curve=True),
    SHEAR: tuple(Field(name, label, 0., -5., 5.) for name, label in
                 (('xy', 'X by Y'), ('xz', 'X by Z'), ('yx', 'Y by X'), ('yz', 'Y by Z'), ('zx', 'Z by X'), ('zy', 'Z by Y'))),
    MIRROR: tuple(Field(axis, f'Flip {axis.upper()}', 0., 0., 1., toggle=True) for axis in 'xyz'),
    MOTION_PATH: tuple(Field(axis, f'{axis}(t)', 0., -10., 10., curve=True) for axis in 'xyz'),
    PERSPECTIVE: (Field('distance', 'Camera distance', 3., .5, 100., curve='log'),),
    NOISE: (Field('intensity', 'Intensity', .02, 0., 1., curve=True),
            Field('speed', 'Speed (steps/s)', 4., 0., 200., curve=True),
            Field('detail', 'Detail (steps)', 2., 0., 64., curve=True)),
    # Translate's range; the value is unused (formula only).
    MAP: tuple(Field(axis, f"{axis.upper()}′", 0., -10., 10., formula=True) for axis in 'xyz'),
}
HELP = {
    ROTATE: 'Turns the object about X, then Y, then Z (fixed viewport axes).',
    SPIN: 'Turns the object continuously: revolutions per second about X, then Y, then Z.',
    TRANSLATE: 'Moves the object in its own units (Size scales them).',
    SCALE: 'Stretches the object along each axis; negative values flip it.',
    SHEAR: 'Slants one axis by another: X by Y adds Y × amount to X.',
    MIRROR: 'Flips the object across the chosen axes.',
    MOTION_PATH: 'Moves the object along x(t), y(t), z(t), in its own units. Right-click a field for Static.',
    PERSPECTIVE: 'Shrinks far points: a camera at this distance looks down −Z (z toward the camera grows).',
    MAP: 'Moves every point to new coordinates: X′, Y′, Z′ are formulas of the point before this step (x, y, z), t, p and s.',
    NOISE: 'Adds noise: Sample = every output sample (heard as hiss); Wobble = slow drift of the shape. Colour sets its spectrum.',
}
# Motion path starts time varying, so adding one moves the object at once.
MOTION_PRESETS = {
    'Orbit': ('0.3*cos(tau*0.25*t)', '0.3*sin(tau*0.25*t)', ''),
    'Figure 8': ('0.4*sin(tau*0.2*t)', '0.2*sin(tau*0.4*t)', ''),
    'Bounce': ('', '0.4*abs(sin(tau*0.5*t)) - 0.2', ''),
    'Drift': ('0.3*sin(tau*0.07*t) + 0.1*sin(tau*0.19*t)', '0.3*sin(tau*0.05*t + 1) + 0.1*sin(tau*0.23*t)', ''),
    'Toward and away': ('', '', '0.8*sin(tau*0.25*t)'),
}
# Map starting points (manual §7 explains each). Spherize divides by the radius, so the
# origin itself gives NaN and goes to 0.
MAP_PRESETS = {
    'Spherize': ('x/sqrt(x*x + y*y + z*z)', 'y/sqrt(x*x + y*y + z*z)', 'z/sqrt(x*x + y*y + z*z)'),
    'Partial spherize': ('x + 0.5*(x/sqrt(x*x + y*y + z*z) - x)', 'y + 0.5*(y/sqrt(x*x + y*y + z*z) - y)',
                         'z + 0.5*(z/sqrt(x*x + y*y + z*z) - z)'),
    'Bulge': ('x*(1 + 0.5*exp(-4*(x*x + y*y)))', 'y*(1 + 0.5*exp(-4*(x*x + y*y)))', 'z'),
    'Wave': ('x + 0.2*sin(3*y + tau*t)', 'y', 'z'),
    'Swap X and Y': ('y', 'x', 'z'),
    'Twist': ('x*cos(2*y) - z*sin(2*y)', 'y', 'x*sin(2*y) + z*cos(2*y)'),
}


@dataclass(frozen=True)
class Param:
    value: float
    expression: str = ''  # Empty: static. Otherwise evaluated every pose frame.
    points: tuple = ()
    clock: tuple = ()  # Runtime only, in seconds per beat and offset seconds.

    def document(self):
        if not self.expression and not self.points: return self.value
        value = dict(value=self.value)
        if self.expression: value['expression'] = self.expression
        if self.points: value['points'] = points_document(self.points)
        return value

    @classmethod
    def read(cls, item, label):
        if not isinstance(item, dict): return cls(item)
        if not {'value'} < set(item) <= {'value', 'expression', 'points'}:
            raise ValueError(f'{label}: unsupported fields.')
        return cls(item['value'], item.get('expression', ''),
                   read_points(item['points'], label) if 'points' in item else ())


def validate_curve(curve):
    if not isinstance(curve, tuple) or not 2 <= len(curve) <= 32:
        raise ValueError('Noise curve needs 2–32 nodes.')
    previous = 0.
    for node in curve:
        if not isinstance(node, tuple) or len(node) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in node):
            raise ValueError('Noise curve nodes must be finite frequency/gain pairs.')
        hz, db = node
        if not 20 <= hz <= 20000 or hz <= previous or not -72 <= db <= 0:
            raise ValueError('Noise curve needs increasing 20–20000 Hz and gains −72–0 dB.')
        previous = hz


@dataclass(frozen=True)
class NoiseOptions:
    """Self-contained spectrum and playback rate; preset is display metadata."""
    colour: str = 'White'
    rate: str = 'Sample'
    curve: tuple = ()
    preset: str = ''


def curve_of(options):
    return options.curve if options.colour == 'Custom' else noise_loops.BUILTINS[COLOURS.index(options.colour)]


def validate_noise(options):
    if not isinstance(options, NoiseOptions) or options.colour not in COLOURS or options.rate not in RATES:
        raise ValueError('Invalid Noise colour or rate.')
    if not isinstance(options.preset, str): raise ValueError('Invalid Noise preset name.')
    if options.colour == 'Custom': validate_curve(options.curve)
    elif options.curve != () or options.preset: raise ValueError('Only Custom Noise has a curve or preset.')


@dataclass(frozen=True)
class Transform:
    kind: str
    params: tuple = ()
    enabled: bool = True
    noise: NoiseOptions | None = None
    # On/Off lane (engine/switch_lane.py) in points; the value stays 1. Off at a pose frame
    # drops the step like enabled=False (signal_engine.posed_shapes). Saved only when automated.
    on: Param = Param(1.)

    def param(self, name):
        return self.params[[field.name for field in FIELDS[self.kind]].index(name)]

    def with_param(self, name, param):
        names = [field.name for field in FIELDS[self.kind]]
        params = list(self.params); params[names.index(name)] = param
        return replace(self, params=tuple(params))

    def document(self):
        value = dict(kind=self.kind, params={field.name: param.document() for field, param in zip(FIELDS[self.kind], self.params)})
        if not self.enabled: value['enabled'] = False
        if self.on.points: value['on'] = points_document(self.on.points)
        if self.kind == NOISE:
            value.update(colour=self.noise.colour, rate=self.noise.rate)
            if self.noise.colour == 'Custom': value['curve'] = [list(node) for node in self.noise.curve]
            if self.noise.preset: value['preset'] = self.noise.preset
        return value

    @classmethod
    def read(cls, value, variables=()):
        """variables: the object's variable names (T5), which expressions may read."""
        if not isinstance(value, dict) or not {'kind', 'params'} <= set(value):
            raise ValueError('Transform fields are missing or unsupported.')
        kind = value['kind']
        if kind not in FIELDS: raise ValueError(f'Unknown transform {kind!r}.')
        allowed = {'kind', 'params', 'enabled', 'on'}
        options = None
        if kind == NOISE:
            allowed |= {'colour', 'rate', 'curve', 'preset'}
            if not {'colour', 'rate'} <= set(value): raise ValueError('Noise colour and rate are required.')
            curve = value.get('curve', [])
            if not isinstance(curve, list) or any(not isinstance(node, list) or len(node) != 2 for node in curve):
                raise ValueError('Invalid Noise curve.')
            options = NoiseOptions(value['colour'], value['rate'], tuple(tuple(node) for node in curve), value.get('preset', ''))
            if options.colour != 'Custom' and ('curve' in value or 'preset' in value):
                raise ValueError('Only Custom Noise has a curve or preset.')
        if not set(value) <= allowed: raise ValueError('Transform fields are unsupported.')
        params = value['params']
        names = [field.name for field in FIELDS[kind]]
        if not isinstance(params, dict) or set(params) != set(names):
            raise ValueError(f'{kind} transform values are missing or unsupported.')
        read = []
        for name in names:
            item = params[name]
            read.append(Param.read(item, f'{kind} {name}'))
        enabled = value.get('enabled', True)
        if type(enabled) is not bool: raise ValueError('Transform enabled must be true or false.')
        on = Param(1., '', read_points(value['on'], f'{kind} On/Off')) if 'on' in value else Param(1.)
        transform = cls(kind, tuple(read), enabled, options, on)
        validate_transform(transform, tuple(variables))
        return transform


def new_transform(kind):
    params = tuple(Param(field.default, field.name if field.formula else '') for field in FIELDS[kind])
    transform = Transform(kind, params, noise=NoiseOptions() if kind == NOISE else None)
    if kind == MOTION_PATH: transform = with_motion_preset(transform, 'Orbit')
    return transform


RESTING_TILT_DEGREES = math.degrees(.40)


def resting_tilt_step():
    """Explicit equivalent of the resting pitch used before scene version 17."""
    return Transform(ROTATE, (Param(RESTING_TILT_DEGREES), Param(0.), Param(0.)))


def used_resting_tilt(solid):
    from phosphor.engine.solids import PLATONIC_SOLIDS
    from phosphor.engine.model_object import MODEL_OBJECT
    return solid in (*PLATONIC_SOLIDS, 'Octant sphere', MODEL_OBJECT)


def with_map_preset(transform, name):
    return replace(transform, params=tuple(Param(0., text) for text in MAP_PRESETS[name]))


def is_identity(field, param):
    """A Map formula that is just its own axis name: the coordinate passes through."""
    return field.formula and param.expression.strip() == field.name and not param.points


def with_motion_preset(transform, name):
    for field, expression in zip(FIELDS[MOTION_PATH], MOTION_PRESETS[name]):
        transform = transform.with_param(field.name, replace(transform.param(field.name), expression=expression))  # Keeps a lane.
    return transform


def field_label(kind, field):
    return f'{kind} {field.label}'


@lru_cache(maxsize=512)
def compiled_expression(text, label='expression', names=()):
    """names: the object's variables (T5), readable next to x, y, z, t, p and s."""
    return parse_expression(text, EXPRESSION_NAMES + tuple(names), label)


def validate_transform(transform, names=()):
    if not isinstance(transform, Transform) or transform.kind not in FIELDS:
        raise ValueError('Unknown transform.')
    fields = FIELDS[transform.kind]
    if transform.kind == NOISE: validate_noise(transform.noise)
    elif transform.noise is not None: raise ValueError('Noise options require a Noise transform.')
    if not isinstance(transform.params, tuple) or len(transform.params) != len(fields) or type(transform.enabled) is not bool:
        raise ValueError(f'{transform.kind} transform values are missing.')
    if not isinstance(transform.on, Param) or transform.on.value != 1 or transform.on.expression:
        raise ValueError(f'{transform.kind} On/Off must be a lane only.')
    validate_switch(transform.on.points, f'{transform.kind} On/Off')
    for field, param in zip(fields, transform.params):
        label = field_label(transform.kind, field)
        if not isinstance(param, Param) or isinstance(param.value, bool) or not isinstance(param.value, (int, float)) or not math.isfinite(param.value):
            raise ValueError(f'{label} must be a finite number.')
        if not field.low <= param.value <= field.high:
            raise ValueError(f'{label} must be between {field.low:g} and {field.high:g}.')
        if not isinstance(param.expression, str): raise ValueError(f'{label}: invalid expression.')
        validate_points(param.points, label)
        if param.points and (field.toggle or field.integer or field.formula): raise ValueError(f'{label} cannot be automated.')
        if field.formula and not param.expression.strip(): raise ValueError(f'{label} needs a formula.')
        if param.expression:
            if field.toggle or field.integer: raise ValueError(f'{label} cannot be time varying.')
            compiled_expression(param.expression, label, names)
        if field.toggle and param.value not in (0, 1): raise ValueError(f'{label} must be on or off.')
        if field.integer and param.value != int(param.value): raise ValueError(f'{label} must be a whole number.')


@lru_cache(maxsize=256)
def validate_stack(stack, variables=()):
    """variables: the object's (already validated) transform_variables, which expressions may read."""
    # A migrated full pre-17 stack needs one extra slot for its explicit pitch.
    limit = MAX_TRANSFORMS + (1 if isinstance(stack, tuple) and stack and stack[0] == resting_tilt_step() else 0)
    if not isinstance(stack, tuple) or len(stack) > limit:
        raise ValueError(f'Use at most {MAX_TRANSFORMS} transforms per object.')
    names = variable_names(variables)
    for transform in stack:
        validate_transform(transform, names)
        if transform.kind == NOISE: noise_loops.request(curve_of(transform.noise))
    # Report runtime errors (1/0 constants) before playback, as parametric objects do.
    probe = np.array([[0., 0., 0.], [.5, -.25, .1], [-1., 1., -.5]])
    apply_stack(probe, stack, 0.25, np.array([0., .5, 1.]), probe=True, values=variable_values(variables, 0.25))


def active(stack):
    return tuple(transform for transform in stack if transform.enabled)


def has_noise(stack):
    return any(transform.kind == NOISE and transform.noise.rate == 'Sample' for transform in active(stack))


def uses_position(stack, names=()):
    """Whether any time-varying number reads s (route positions are then worth computing).
    names: the object's variable names (T5).

    Decided from the compiled expression's names, not the text: sin, cos and sqrt contain
    the letter s but do not read s."""
    return any(transform.kind == NOISE and transform.noise.rate == 'Wobble' or
               any(param.expression and 's' in compiled_expression(param.expression, 'expression', tuple(names)).co_names
                   for param in transform.params) for transform in active(stack))


def _value(field, param, t, points, s, label, tempo=120., values=None):
    """values: {name: value} of the object's variables at t (variable_values), or None."""
    if not param.expression and not param.points: return float(param.value)
    count = len(points)
    namespace = {'__builtins__': {}, **FUNCTIONS, **CONSTANTS, **_HELPERS, **(values or {}),
                 't': np.full(count, float(t)), 'p': np.full(count, tempo.beats(float(t)) if hasattr(tempo, 'beats') else float(t) * tempo / 60.), 's': np.zeros(count) if s is None else s,
                 'x': points[:, 0], 'y': points[:, 1], 'z': points[:, 2]}
    names = tuple(values) if values else ()
    with np.errstate(all='ignore'):
        try: value = eval(compiled_expression(param.expression, label, names), namespace) if param.expression else param.value
        except (ArithmeticError, ValueError, TypeError) as error: raise ValueError(f'{label}: {error}') from None
        value = np.asarray(value, dtype=float) * gain_at(param.points, param.clock, t)
    if value.ndim > 1 or (value.ndim == 1 and len(value) != count):
        raise ValueError(f'{label}: expression must give one value per point.')
    # Map sends every non-finite result to 0 (1/x at x = 0 must not pin the beam to an edge).
    value = np.nan_to_num(np.broadcast_to(value, (count,)).astype(float), nan=0.,
                          posinf=0. if field.formula else field.high, neginf=0. if field.formula else field.low)
    return np.clip(value, field.low, field.high) if field.clip else value


def _rotate(points, axis, degrees):
    if np.ndim(degrees) == 0 and degrees == 0: return points
    a = np.deg2rad(degrees); c, s = np.cos(a), np.sin(a)
    x, y, z = points[:, 0], points[:, 1], points[:, 2]
    if axis == 0: return np.column_stack((x, c*y - s*z, s*y + c*z))
    if axis == 1: return np.column_stack((c*x + s*z, y, -s*x + c*z))
    return np.column_stack((c*x - s*y, s*x + c*y, z))


def apply_stack(points, stack, t, s=None, tempo=120., probe=False, values=None):
    """(N, 3) object-space points -> (posed (N, 3) points, noise intensity (N,) or None).
    values: the object's variables at t (variable_values), evaluated once before the stack."""
    points = np.array(points, dtype=float).reshape(-1, 3)
    noise = None
    variables = values
    for transform in active(stack):
        fields = FIELDS[transform.kind]
        values = [None if is_identity(field, param) else _value(field, param, t, points, s, field_label(transform.kind, field), tempo, variables)
                  for field, param in zip(fields, transform.params)]
        kind = transform.kind
        if kind == MAP:
            # Every axis reads the points from before the step; identity axes stay untouched.
            if any(value is not None for value in values):
                points = np.column_stack([points[:, axis] if value is None else value for axis, value in enumerate(values)])
        elif kind == ROTATE:
            for axis, degrees in enumerate(values): points = _rotate(points, axis, degrees)
        elif kind == SPIN:
            for axis, spin in enumerate(values): points = _rotate(points, axis, 360.*spin*t)
        elif kind in (TRANSLATE, MOTION_PATH):
            points = points + np.column_stack([np.broadcast_to(v, (len(points),)) for v in values])
        elif kind == SCALE:
            points = points * np.column_stack([np.broadcast_to(v, (len(points),)) for v in values])
        elif kind == SHEAR:
            xy, xz, yx, yz, zx, zy = values
            x, y, z = points[:, 0], points[:, 1], points[:, 2]
            points = np.column_stack((x + xy*y + xz*z, y + yx*x + yz*z, z + zx*x + zy*y))
        elif kind == MIRROR:
            points = points * np.array([-1. if flip else 1. for flip in values])
        elif kind == PERSPECTIVE:
            distance = values[0]
            factor = distance / np.maximum(distance - points[:, 2], .05*distance)
            points = np.column_stack((points[:, 0]*factor, points[:, 1]*factor, points[:, 2]))
        elif kind == NOISE:
            intensity = np.broadcast_to(values[0], (len(points),)).astype(float)
            if transform.noise.rate == 'Sample':
                noise = intensity if noise is None else noise + intensity
            else:
                # Export and the editor wait for the loop. The realtime audio thread never blocks
                # (audit P10): it draws this step unwobbled until the worker has built the loop,
                # and noise_loops.missed() keeps scene_plan from caching that fallback pose.
                realtime = noise_loops.realtime()
                table = noise_loops.get(curve_of(transform.noise), wait=not probe and not realtime)
                if table is None:
                    if realtime and not probe: noise_loops.missed()
                    continue
                position = np.zeros(len(points)) if s is None else np.asarray(s)
                base, detail = t*values[1], values[2]
                shift = np.array([0, noise_loops.N//2, noise_loops.N//4])[:, None]  # x, y, z
                first, second = _loop_value(table, np.stack(np.broadcast_arrays(
                    base+shift+position*detail, base+shift+(position-1)*detail)))
                points = points + (intensity*(first + position*(second-first))).T
    return points, noise


def noise_curve(stack):
    """First active Sample colour; further Sample steps add intensity only."""
    return next((curve_of(t.noise) for t in active(stack) if t.kind == NOISE and t.noise.rate == 'Sample'), None)


def _loop_value(table, position):
    """Catmull–Rom interpolation with periodic neighbours."""
    floor = np.floor(position).astype(np.int64); f = position-floor
    a, b, c, d = (table[(floor+offset) % len(table)].astype(float) for offset in (-1, 0, 1, 2))
    return b + .5*f*(c-a + f*(2*a-5*b+4*c-d + f*(3*(b-c)+d-a)))


def route_positions(vertices, route):
    """Each vertex's 0-1 position along a route at its first visit (unvisited: 0)."""
    vertices = np.asarray(vertices, dtype=float); route = np.asarray(route, dtype=int)
    positions = np.zeros(len(vertices))
    if len(route) < 2: return positions
    lengths = np.linalg.norm(np.diff(vertices[route], axis=0), axis=1)
    cumulative = np.concatenate(([0.], np.cumsum(lengths)))
    total = cumulative[-1] if cumulative[-1] > 1e-12 else 1.
    first, index = np.unique(route, return_index=True)  # Each vertex's first visit.
    positions[first] = cumulative[index] / total
    return positions


def migrated_pose(shape_values):
    """Version 11 rotation/spin fields -> an equivalent stack (they compose X, then Y, then Z)."""
    rotation = [float(shape_values.get(f'rotation_{axis}', 0.)) for axis in 'xyz']
    spin = [float(shape_values.get(f'spin_{axis}', 0.)) for axis in 'xyz']
    if not any(spin):
        return (Transform(ROTATE, tuple(Param(v) for v in rotation)),) if any(rotation) else ()
    if not any(rotation):
        return (Transform(SPIN, tuple(Param(v) for v in spin)),)
    # Both on: one Rotate whose spinning axes are time varying keeps the exact old pose.
    params = tuple(Param(r, f'{r:g} + {360*w:g}*t' if w else '') for r, w in zip(rotation, spin))
    return (Transform(ROTATE, params),)


# Path window: where along its route each object starts drawing, and how much it draws.
# Both are per-object numbers, static or time varying in t (evaluated per pose frame).
START_FIELD = Field('path_start', 'Start', 0., 0., 1., clip=False)  # Results wrap into 0-1.
TRACE_FIELD = Field('path_trace', 'Trace', 1., -1., 1.)  # Negative draws backwards from Start.
PATH_HELP = {
    'path_start': 'Where the object starts drawing: 0–1 along its route (0 = its first point). Right-click for Time varying….',
    'path_trace': 'How much of the route is drawn from Start: 1 = all of it, 0.25 = a quarter, negative = backwards, '
                  '0 = hidden. The object keeps its share of each cycle. Right-click for Time varying….',
}
PATH_EXPRESSION_HELP = ("Time varying: an expression of t (seconds), p (beats) and the object's Variables (Transform tab), "
                        'evaluated once per pose frame. '
                        'Press Enter to apply; right-click › Static to go back.')


def validate_path(start, trace, variables=()):
    """variables: the object's transform_variables, which Start and Trace may read (T5)."""
    names = variable_names(variables)
    for field, param in ((START_FIELD, start), (TRACE_FIELD, trace)):
        if not isinstance(param, Param) or isinstance(param.value, bool) or not isinstance(param.value, (int, float)) or not math.isfinite(param.value):
            raise ValueError(f'Path {field.label} must be a finite number.')
        if not field.low <= param.value <= field.high:
            raise ValueError(f'Path {field.label} must be between {field.low:g} and {field.high:g}.')
        if not isinstance(param.expression, str): raise ValueError(f'Path {field.label}: invalid expression.')
        validate_points(param.points, f'Path {field.label}')
        if param.expression: path_expression(param.expression, f'Path {field.label}', names)
    path_window(start, trace, 0., values=variable_values(variables, 0.))


@lru_cache(maxsize=256)
def path_expression(text, label='expression', names=()):
    """Whole-object numbers have no point coordinates: only t (and s, always 0 here), and the
    object's variables (names, T5)."""
    return parse_expression(text, tuple(names), label)


def path_window(start, trace, t, tempo=120., values=None):
    """(start 0-1, trace -1..1) at pose time t; values: the object's variables at t."""
    probe = np.zeros((1, 3))
    window = [float(np.asarray(_value(field, param, t, probe, None, f'Path {field.label}', tempo, values)).flat[0])
              for field, param in ((START_FIELD, start), (TRACE_FIELD, trace))]
    return window[0] % 1. if window[0] != 1. else 1., window[1]


def trim_route(vertices, route, connectors, start, trace):
    """Cut a closed route to the piece from start, trace of its length long (both by arc length).

    Returns (vertices with the two cut points appended, closed route piece→back to its first
    point, drawn edges, connector edges incl. the closing step, (i, j, f) for the appended
    points so per-vertex data can be interpolated), or None when the route has no length.
    """
    vertices = np.asarray(vertices, dtype=float); route = np.asarray(route, dtype=int)
    closed = route if route[0] == route[-1] else np.r_[route, route[0]]
    lengths = np.linalg.norm(np.diff(vertices[closed], axis=0), axis=1)
    total = lengths.sum()
    if total < 1e-12 or len(closed) < 2: return None
    body = closed[:-1]
    extended = np.r_[np.tile(body, 3), body[0]]
    cumulative = np.concatenate(([0.], np.cumsum(np.tile(lengths, 3))))
    jumps = connector_steps(extended, connectors)
    u0 = (start + 1.) * total; u1 = u0 + trace * total
    lo, hi = min(u0, u1), max(u0, u1)

    def cut(u):
        k = int(np.clip(np.searchsorted(cumulative, u, side='right') - 1, 0, len(extended) - 2))
        span = cumulative[k+1] - cumulative[k]
        return k, (u - cumulative[k]) / span if span > 0 else 0.

    (ka, fa), (kb, fb) = cut(lo), cut(hi)
    n = len(vertices)
    a = vertices[extended[ka]] + fa * (vertices[extended[ka+1]] - vertices[extended[ka]])
    b = vertices[extended[kb]] + fb * (vertices[extended[kb+1]] - vertices[extended[kb]])
    inner = [k for k in range(ka + 1, kb + 1) if lo < cumulative[k] < hi]
    path = [n, *(int(extended[k]) for k in inner), n + 1]
    steps = [ka, *inner]  # Original step each piece segment lies on.
    if trace < 0: path, steps = path[::-1], steps[::-1]
    piece = np.array(path + [path[0]])
    drawn, joins = [], [(path[-1], path[0])]
    for (p, q), step in zip(zip(path[:-1], path[1:]), steps):
        (joins if jumps[step] else drawn).append((p, q))
    weights = ((int(extended[ka]), int(extended[ka+1]), fa), (int(extended[kb]), int(extended[kb+1]), fb))
    edges = np.array(drawn + joins, dtype=int).reshape(-1, 2)
    return np.vstack((vertices, a, b)), piece, edges, np.array(joins, dtype=int).reshape(-1, 2), weights


def connector_steps(route, connectors):
    connectors = np.asarray(connectors, dtype=int).reshape(-1, 2)
    if not len(connectors): return np.zeros(len(route) - 1, dtype=bool)
    pairs = {tuple(sorted(map(int, c))) for c in connectors}
    return np.array([tuple(sorted((int(a), int(b)))) in pairs for a, b in zip(route[:-1], route[1:])])


# Object variables (T5, 0.81.0) -------------------------------------------------------------
# Shape.transform_variables: up to MAX_OBJECT_VARIABLES function_plot.Variables that belong to
# the object (not to its equation), shared by every step and by Path Start and Trace. Each is
# evaluated once per pose frame, before the stack: its expression of t and p (s reads 0, as in
# Path Start), or its slider value, times its automation gain; NaN gives min, and the result is
# clipped to min..max (as equation variables are). A variable's expression never reads other
# variables. Automation is the Variable's own points, addressed by lane_access as
# ('transform_variable', i, name): never a Shape.lanes entry, so R1's parked variable lanes
# ('variable:<name>') and an equation variable's ('variable', i, name) never meet it.
MAX_OBJECT_VARIABLES = MAX_VARIABLES
# x, y, z: point coordinates; gx, gy, gz: Gridify's copy index (T2). t, p, s, functions and
# constants are refused by function_plot.validate_variable_name.
TRANSFORM_RESERVED = ('x', 'y', 'z', 'gx', 'gy', 'gz')
NEW_VARIABLE_NAMES = (*'abcdfghjkmnqruvw', *(f'v{i}' for i in range(1, 40)))  # + Variable takes the first free one.
VARIABLE_EXPRESSION_HELP = ('Time varying: an expression of t (seconds) and p (beats), evaluated once per pose frame '
                            'and clipped to min–max (s reads 0). Press Enter to apply; right-click › Static for the slider.')


def variable_names(variables):
    return tuple(variable.name for variable in variables)


def check_variable_name(name, others=(), taken=None):
    """Refuse a name an object variable cannot have (ValueError says why). others: the object's
    other variable names; taken: {name: what uses it} for the object's equation variables."""
    validate_variable_name(name)
    if name in TRANSFORM_RESERVED:
        what = 'a point coordinate' if len(name) == 1 else "Gridify's copy index"
        raise ValueError(f'{name} is reserved in transforms ({what}); choose another name.')
    if name in others: raise ValueError(f'This object already has a variable {name}; choose another name.')
    if taken and name in taken:
        raise ValueError(f"{name} is a variable of this object's {taken[name]}; choose another name.")


def validate_object_variables(variables, taken=None):
    """Shape.transform_variables. taken: {name: what uses it} (the object's equation variables,
    active or parked), refused as names (user decision, November 21, 2026)."""
    if not isinstance(variables, tuple) or len(variables) > MAX_OBJECT_VARIABLES:
        raise ValueError(f'Use at most {MAX_OBJECT_VARIABLES} variables per object.')
    names = []
    for variable in variables:
        if not isinstance(variable, Variable): raise ValueError('Invalid object variable.')
        check_variable_name(variable.name, names, taken)
        validate_variable(variable)
        names.append(variable.name)


def new_variable_name(used):
    return next(name for name in NEW_VARIABLE_NAMES if name not in used)


def variable_values(variables, t, tempo=120.):
    """{name: value} of an object's variables at pose time t, in their order (one float each)."""
    if not variables: return {}
    t = float(t)
    namespace = {'__builtins__': {}, **FUNCTIONS, **CONSTANTS, **_HELPERS, 't': np.full(1, t),
                 'p': np.full(1, tempo.beats(t) if hasattr(tempo, 'beats') else t * tempo / 60.), 's': np.zeros(1)}
    values = {}
    with np.errstate(all='ignore'):
        for variable in variables:
            label = f'Variable {variable.name}'
            if variable.expression:
                try: value = eval(parse_expression(variable.expression, (), label), namespace)
                except (ArithmeticError, ValueError, TypeError) as error: raise ValueError(f'{label}: {error}') from None
                value = np.asarray(value, dtype=float)
                if value.size != 1: raise ValueError(f'{label}: expression must give one value.')
                value = float(value.reshape(-1)[0])
            else: value = float(variable.value)
            value *= float(gain_at(variable.points, variable.clock, t))
            values[variable.name] = variable.low if math.isnan(value) else min(max(value, variable.low), variable.high)
    return values


def read_object_variable(item):
    """A Variable from its scene document (Variable.document)."""
    if not isinstance(item, dict) or not {'name', 'value', 'low', 'high'} <= set(item) <= {'name', 'value', 'low', 'high', 'expression', 'points'}:
        raise ValueError('Object variable fields are missing or unsupported.')
    item = dict(item)
    if 'points' in item: item['points'] = read_points(item['points'], f'Variable {item["name"]}')
    return Variable(**item)


def renamed_expression(text, old, new):
    """text with each reference to the name old (an AST Name) spelled new; everything else is
    kept as typed. Text that does not parse is returned unchanged."""
    # Python reads names in NFKC form, so a full-width ｋ is the name k.
    if not text or old not in unicodedata.normalize('NFKC', text): return text
    body = text.strip()
    try: tree = ast.parse(body, mode='eval')
    except SyntaxError: return text
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == old]
    if not nodes: return text
    if any(node.lineno != 1 or node.end_lineno != 1 for node in nodes):
        for node in nodes: node.id = new
        result = ast.unparse(tree)
    else:
        encoded = body.encode('utf-8')  # Column offsets count UTF-8 bytes.
        for node in sorted(nodes, key=lambda node: node.col_offset, reverse=True):
            encoded = encoded[:node.col_offset] + new.encode('utf-8') + encoded[node.end_col_offset:]
        result = text[:len(text) - len(text.lstrip())] + encoded.decode('utf-8') + text[len(text.rstrip()):]
    if len(result) > MAX_EXPRESSION_LENGTH:
        raise ValueError(f'Renaming {old} to {new} makes an expression longer than {MAX_EXPRESSION_LENGTH} characters.')
    return result


def _renamed_param(param, old, new):
    if not param.expression: return param
    text = renamed_expression(param.expression, old, new)
    return param if text == param.expression else replace(param, expression=text)


def renamed_variable(shape, old, new):
    """shape with its object variable old renamed new, and every step, Start and Trace
    expression that reads it following the rename (by AST names, as migrate_tempo_variable)."""
    steps = []
    for step in shape.transforms:
        params = tuple(_renamed_param(param, old, new) for param in step.params)
        steps.append(step if params == step.params else replace(step, params=params))
    return replace(shape, transform_variables=tuple(replace(v, name=new) if v.name == old else v for v in shape.transform_variables),
                   transforms=tuple(steps), path_start=_renamed_param(shape.path_start, old, new),
                   path_trace=_renamed_param(shape.path_trace, old, new))


def variables_in_use(shape):
    """Names of the object's variables that a step, Start or Trace expression reads."""
    names = variable_names(shape.transform_variables)
    if not names: return set()
    used = set()
    params = [param for step in shape.transforms for param in step.params]
    for param in (*params, shape.path_start, shape.path_trace):
        if param.expression:
            try: used |= set(compiled_expression(param.expression, 'expression', names).co_names) & set(names)
            except ValueError: continue
    return used
