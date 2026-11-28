"""Pure addressing and display groups for a clip's gain lanes.

Keys: ('shape', i, field), ('path', i, field), ('transform', i, j, field), ('variable', i,
name), ('mix', field), and the On/Off lanes ('on', i) for object i and ('on', i, j) for its
transform j (engine/switch_lane.py). Every On/Off lane is its own block in the roll.
('parked', i, name) is a parked variable lane (object Replace, engine/object_replace.py): kept
in Shape.lanes, editable and removable, but without effect until an equation declaring the
variable is active again. Only existing parked lanes have keys; there is no new parked lane.
('transform_variable', i, name) is object i's own variable (T5, Shape.transform_variables): its
lane is that Variable's points, never a Shape.lanes entry, so it meets neither an equation
variable's key nor a parked lane, and it stays when the object is replaced.
"""
from collections import Counter
from dataclasses import dataclass, replace

from phosphor.engine import automation, transforms, switch_lane
from phosphor.engine.lane_edit import shift_lane
from phosphor.engine.object_replace import declaring_type
from phosphor.engine.signal_engine import PARAMETRIC_OBJECT, is_motion_guide
from phosphor.engine.recurrence import RECURRENCE_OBJECT
from phosphor.engine.vector_text import TEXT_OBJECT
from phosphor.engine.matrix_rain import MATRIX_OBJECT, MATRIX_FIELDS


def automatable(clip):
    keys = []
    for i, shape in enumerate(clip.settings.shapes):
        guide = is_motion_guide(shape)
        if not guide:
            keys.append(('on', i))
            keys.extend(('shape', i, name) for name in automation.SHAPE_LANE_FIELDS)
        keys.extend(('path', i, name) for name in ('path_start', 'path_trace'))
        if shape.solid == MATRIX_OBJECT and shape.matrix is not None:
            keys.extend(('matrix', i, field.name) for field in MATRIX_FIELDS)
        for j, step in enumerate(shape.transforms):
            keys.extend(('transform', i, j, field.name) for field in transforms.FIELDS[step.kind]
                        if not field.toggle and not field.integer and not field.formula)
            if not guide: keys.append(('on', i, j))
        keys.extend(('transform_variable', i, variable.name) for variable in shape.transform_variables)
        if shape.solid in (PARAMETRIC_OBJECT, RECURRENCE_OBJECT):
            keys.extend(('variable', i, variable.name) for variable in equation(shape).variables)
        keys.extend(('parked', i, name) for name in automation.parked_lanes(shape.lanes))
    keys.extend(('mix', name) for name in automation.CLIP_LANE_FIELDS)
    return keys


def is_parked(key):
    """Whether key names a parked variable lane (no effect; shown dimmed)."""
    return isinstance(key, tuple) and bool(key) and key[0] == 'parked'


def is_switch(key):
    """Whether key names an On/Off lane (two values, drawn as blocks)."""
    return isinstance(key, tuple) and bool(key) and key[0] == 'on'


def _check(clip, key):
    if (not isinstance(key, tuple) or not key
            or (key[0] != 'mix' and (len(key) < 2 or type(key[1]) is not int))
            or (key[0] == 'transform' and (len(key) < 3 or type(key[2]) is not int))
            or key not in automatable(clip)):
        raise ValueError(f'Invalid automation key: {key!r}')


def equation(shape):
    return shape.recurrence if shape.solid == RECURRENCE_OBJECT else shape.plot


def _number(clip, key):
    kind, *parts = key
    if kind == 'mix': return getattr(clip.settings, parts[0])
    shape = clip.settings.shapes[parts[0]]
    if kind == 'matrix': return getattr(shape.matrix, parts[1])
    if kind == 'transform': return shape.transforms[parts[1]].param(parts[2])
    if kind == 'variable': return next(v for v in equation(shape).variables if v.name == parts[1])
    if kind == 'transform_variable': return next(v for v in shape.transform_variables if v.name == parts[1])
    return getattr(shape, parts[1])


def get_points(clip, key):
    _check(clip, key)
    if key[0] == 'mix': return automation.lane_points(clip.lanes, key[1])
    if key[0] == 'shape':
        return automation.lane_points(clip.settings.shapes[key[1]].lanes, key[2])
    if key[0] == 'parked':
        return automation.lane_points(clip.settings.shapes[key[1]].lanes, automation.parked_lane(key[2]))
    if key[0] == 'on':
        shape = clip.settings.shapes[key[1]]
        return automation.lane_points(shape.lanes, switch_lane.ON) if len(key) == 2 else shape.transforms[key[2]].on.points
    return _number(clip, key).points


def set_points(clip, key, points):
    _check(clip, key)
    (switch_lane.validate_switch if is_switch(key) else automation.validate_points)(points, label(clip, key))
    kind = key[0]
    if kind == 'mix':
        return replace(clip, lanes=automation.with_lane(clip.lanes, key[1], points))
    i = key[1]
    shape = clip.settings.shapes[i]
    if kind == 'shape':
        shape = replace(shape, lanes=automation.with_lane(shape.lanes, key[2], points))
    elif kind == 'parked':
        shape = replace(shape, lanes=automation.with_lane(shape.lanes, automation.parked_lane(key[2]), points))
    elif kind == 'on' and len(key) == 2:
        shape = replace(shape, lanes=automation.with_lane(shape.lanes, switch_lane.ON, points))
    elif kind == 'on':
        steps = list(shape.transforms)
        steps[key[2]] = replace(steps[key[2]], on=replace(steps[key[2]].on, points=points))
        shape = replace(shape, transforms=tuple(steps))
    else:
        number = replace(_number(clip, key), points=points)
        if kind == 'path':
            shape = replace(shape, **{key[2]: number})
        elif kind == 'matrix':
            shape = replace(shape, matrix=replace(shape.matrix, **{key[2]: number}))
        elif kind == 'transform':
            steps = list(shape.transforms)
            steps[key[2]] = steps[key[2]].with_param(key[3], number)
            shape = replace(shape, transforms=tuple(steps))
        elif kind == 'transform_variable':
            shape = replace(shape, transform_variables=tuple(number if v.name == key[2] else v for v in shape.transform_variables))
        else:
            variables = tuple(number if v.name == key[2] else v for v in equation(shape).variables)
            field = 'recurrence' if shape.solid == RECURRENCE_OBJECT else 'plot'
            shape = replace(shape, **{field: replace(equation(shape), variables=variables)})
    shapes = list(clip.settings.shapes)
    shapes[i] = shape
    return replace(clip, settings=replace(clip.settings, shapes=tuple(shapes)))


def shift_clip_lanes(clip, offset):
    """Shift every populated lane without changing the clip's static values."""
    if offset == 0:
        return clip
    for key in automatable(clip):
        points = get_points(clip, key)
        if points:
            clip = set_points(clip, key, shift_lane(points, offset))
    return clip


def label(clip, key):
    _check(clip, key)
    kind = key[0]
    if kind == 'on': return 'On/Off'
    if kind == 'transform':
        step = clip.settings.shapes[key[1]].transforms[key[2]]
        field = next(f for f in transforms.FIELDS[step.kind] if f.name == key[3])
        return transforms.field_label(step.kind, field)
    if kind == 'variable': return f'Variable {key[2]}'
    if kind == 'transform_variable': return f'Object variable {key[2]}'
    if kind == 'parked':
        solid = declaring_type(clip.settings.shapes[key[1]], key[2])
        names = {PARAMETRIC_OBJECT: 'Parametric', RECURRENCE_OBJECT: 'Recurrence'}
        return f'Variable {key[2]} (parked: {names[solid]})' if solid else f'Variable {key[2]} (parked)'
    return {'position_x': 'Position X', 'position_y': 'Position Y', 'shape_size': 'Size',
            'path_start': 'Path Start', 'path_trace': 'Path Trace',
            'text_speed': 'Matrix Swap speed', 'fall_speed': 'Matrix Fall speed',
            'drawing_gain': 'Output level', 'music_gain': 'Original blend'}[key[-1]]


def static_value(clip, key):
    _check(clip, key)
    if is_switch(key): return 1.
    if is_parked(key):  # The parked variable's own value, else the gain alone.
        shape = clip.settings.shapes[key[1]]
        solid = declaring_type(shape, key[2])
        if solid is None: return 1.
        plot = shape.recurrence if solid == RECURRENCE_OBJECT else shape.plot
        return next(v.value for v in plot.variables if v.name == key[2])
    number = _number(clip, key)
    return number.value if key[0] in ('transform', 'path', 'matrix', 'variable', 'transform_variable') else number


@dataclass(frozen=True)
class Block:
    key: tuple
    title: str
    keys: tuple


def block_of(key):
    if not isinstance(key, tuple) or not key:
        raise ValueError(f'Invalid automation key: {key!r}')
    lengths = {'mix': (2,), 'shape': (3,), 'path': (3,), 'matrix': (3,), 'variable': (3,), 'parked': (3,), 'transform_variable': (3,),
               'transform': (4,), 'on': (2, 3)}
    if key[0] not in lengths or len(key) not in lengths[key[0]]:
        raise ValueError(f'Invalid automation key: {key!r}')
    return key if key[0] == 'on' else key[:-1]


def blocks(clip, include=()):
    names = [f'"{s.text}"' if s.solid == TEXT_OBJECT else s.solid for s in clip.settings.shapes]
    counts = Counter(names)
    names = [f'{name} #{i+1}' if counts[name] > 1 else name for i, name in enumerate(names)]
    groups = {}
    for key in automatable(clip):
        groups.setdefault(block_of(key), []).append(key)
    result = []
    for group, keys in groups.items():
        if group not in include and not any(get_points(clip, key) for key in keys): continue
        kind = group[0]
        if kind == 'mix':
            title = 'Mix'
        elif kind == 'on':
            step = '' if len(group) == 2 else f'{group[2]+1}. {clip.settings.shapes[group[1]].transforms[group[2]].kind} '
            title = f'{names[group[1]]} › {step}On/Off'
        else:
            if kind == 'transform':
                j = group[2]
                suffix = f'{j+1}. {clip.settings.shapes[group[1]].transforms[j].kind}'
            else:
                suffix = {'shape': 'Placement', 'path': 'Path', 'matrix': 'Matrix', 'variable': 'Equation', 'parked': 'Parked variables',
                          'transform_variable': 'Variables'}[kind]
            title = f'{names[group[1]]} › {suffix}'
        result.append(Block(group, title, tuple(keys)))
    return tuple(result)


def default_points(clip, key=None):
    """A new lane: gain 1 across the clip, or on across the clip for an On/Off key."""
    if is_switch(key): return switch_lane.DEFAULT
    return ((0., 1., 'linear'), (float(clip.length), 1., 'linear'))


def without_switches(clip):
    """The clip with every On/Off lane removed (history labels compare the rest)."""
    for key in automatable(clip):
        if is_switch(key) and get_points(clip, key): clip = set_points(clip, key, ())
    return clip
