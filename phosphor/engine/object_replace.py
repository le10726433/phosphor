"""Object Replace (R1, 0.80.0): change what an object is, keeping its place and automation.

Pure functions on frozen Shapes; the editor's Objects-list menu (qt_app.replace_objects) calls
them inside one Undo step.

- Carried over unchanged: uid, list position, placement (position, size, rotation, spin),
  Path Start/Trace, motion binding, mute, Bypass all, the transform stack, and the placement,
  path, transform and On/Off lanes, and the object's variables (T5, Shape.transform_variables,
  with their lanes: they belong to the object, not to its equation, so they are never parked).
  replace() keeps every field but the type's.
- Parked: the type fields the new type does not use (text, custom, model, plot, recurrence)
  stay on the Shape; scene files keep them under 'parked' (storage/app_files.py). Switching
  back restores them; a type the object has never been gets its Add-object defaults.
- Variables: the active equation's variables live in its Variable points. Equation to
  equation (Replace between Recurrence and Parametric), a variable of the same name keeps its
  value (clamped to the new range) and its lane. (Loading a preset in the equation panel is not
  a Replace: it gives the preset's values, as before 0.80.0.) The lane of every other
  outgoing variable is parked in Shape.lanes as 'variable:<name>' (automation.PARKED_LANE),
  and a parked lane comes back when an active equation declares its variable again. Parked
  lanes are never evaluated or baked; lane_access lists them as ('parked', i, name) keys.
- Name clashes (T5): an object variable cannot share a name with a variable of the object's
  equations, active or parked (equation_variables). An equation that arrives with such a name
  (a Replace to a type whose defaults declare it, or an equation-panel preset) has its variable
  renamed name_2, name_3… in its formulas (renamed_clashes), with a notice: the object's
  variables and the steps that read them stay as they were, as T6 keeps an existing variable.
"""
from dataclasses import replace

from phosphor.engine.automation import PARKED_LANE, parked_lane, parked_lanes, with_lane
from phosphor.engine.custom_shape import CUSTOM_OBJECT
from phosphor.engine.function_plot import PARAMETRIC_OBJECT, FunctionPlot
from phosphor.engine.model_object import MODEL_OBJECT
from phosphor.engine.recurrence import RECURRENCE_OBJECT, RecurrencePlot
from phosphor.engine.vector_text import TEXT_OBJECT, DEFAULT_TEXT
from phosphor.engine.matrix_rain import MATRIX_OBJECT, MatrixConfig
from phosphor.engine.solids import PARAMETRIC_SOLIDS

ADD_TEXT = 'HELLO'  # Text of a new Text object (Add object › Text).
EQUATIONS = {PARAMETRIC_OBJECT: 'plot', RECURRENCE_OBJECT: 'recurrence'}
TYPE_FIELDS = ('text', 'custom', 'model', 'plot', 'recurrence', 'matrix', 'shape_params')
FIELD_TYPES = {'text': TEXT_OBJECT, 'custom': CUSTOM_OBJECT, 'model': MODEL_OBJECT,
               'plot': PARAMETRIC_OBJECT, 'recurrence': RECURRENCE_OBJECT, 'matrix': MATRIX_OBJECT}


def new_shape(solid):
    """A new object of a built-in kind, as Add object makes it (not Custom shape or 3D model)."""
    from phosphor.engine.signal_engine import Shape
    if solid == TEXT_OBJECT: return Shape(solid=TEXT_OBJECT, text=ADD_TEXT)
    if solid == MATRIX_OBJECT: return Shape(solid=MATRIX_OBJECT, matrix=MatrixConfig())
    if solid == PARAMETRIC_OBJECT: return Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot())
    if solid == RECURRENCE_OBJECT: return Shape(solid=RECURRENCE_OBJECT, recurrence=RecurrencePlot())
    return Shape(solid=solid)


def type_field(solid):
    """The Shape field that holds solid's own data, or None for the built-in solids."""
    if solid in PARAMETRIC_SOLIDS: return 'shape_params'
    return {TEXT_OBJECT: 'text', CUSTOM_OBJECT: 'custom', MODEL_OBJECT: 'model', MATRIX_OBJECT: 'matrix', **EQUATIONS}.get(solid)


def parked_fields(shape):
    """{field: value} of the type data shape keeps aside for other types (what scenes save
    under 'parked'). Text counts once it differs from the Shape default."""
    active = type_field(shape.solid)
    result = {}
    for field in TYPE_FIELDS:
        if field == active: continue
        value = getattr(shape, field)
        if field == 'text' and value != DEFAULT_TEXT or field != 'text' and value is not None: result[field] = value
    return result


def active_equation(shape):
    field = EQUATIONS.get(shape.solid)
    return getattr(shape, field) if field is not None else None


def parked_equations(shape):
    """(type, equation) of the equations shape keeps aside, Parametric first."""
    return tuple((solid, getattr(shape, field)) for solid, field in EQUATIONS.items()
                 if solid != shape.solid and getattr(shape, field) is not None)


def declaring_type(shape, name):
    """The parked equation type that declares variable name, or None."""
    return next((solid for solid, equation in parked_equations(shape)
                 if any(v.name == name for v in equation.variables)), None)


def equation_variables(shape):
    """{name: what declares it} for the variables of shape's equations, active and parked; an
    object variable (T5) cannot take these names (user decision, November 21, 2026)."""
    labels = {PARAMETRIC_OBJECT: 'Parametric equations', RECURRENCE_OBJECT: 'Recurrence equations'}
    result = {}
    for solid, field in EQUATIONS.items():
        equation = getattr(shape, field)
        if equation is None: continue
        label = labels[solid] if solid == shape.solid else 'parked ' + labels[solid]
        for variable in equation.variables: result.setdefault(variable.name, label)
    return result


def renamed_clashes(equation, taken, notices=None):
    """equation with each variable named in taken (the object's variables) renamed name_2,
    name_3… in its formulas (AST names); a notice per rename."""
    from phosphor.engine.transforms import renamed_expression
    if equation is None or not any(v.name in taken for v in equation.variables): return equation
    used = {v.name for v in equation.variables} | set(taken)
    for variable in equation.variables:
        if variable.name not in taken: continue
        suffix = 2
        while f'{variable.name}_{suffix}' in used: suffix += 1
        new = f'{variable.name}_{suffix}'; used.add(new)
        formulas = {axis: renamed_expression(getattr(equation, axis), variable.name, new) for axis in ('x', 'y', 'z')}
        equation = replace(equation, variables=tuple(replace(v, name=new) if v.name == variable.name else v
                                                     for v in equation.variables), **formulas)
        if notices is not None:
            notices.append(f'Equation variable {variable.name} renamed {new}: the object has a variable {variable.name}.')
    return equation


def needs_input(shape, solid):
    """Whether replacing shape with solid needs a new drawing or model (none is parked)."""
    return (solid == CUSTOM_OBJECT and shape.custom is None) or (solid == MODEL_OBJECT and shape.model is None)


def _reclaim(lanes, equation):
    """(lanes, equation) with each parked lane whose variable equation declares (and has no
    lane of its own) moved back into that variable."""
    parked = parked_lanes(lanes)
    if not parked or not any(v.name in parked and not v.points for v in equation.variables): return lanes, equation
    variables = []
    for variable in equation.variables:
        if not variable.points and variable.name in parked:
            variable = replace(variable, points=parked[variable.name])
            lanes = with_lane(lanes, parked_lane(variable.name), ())
        variables.append(variable)
    return lanes, replace(equation, variables=tuple(variables))


def carried(outgoing, incoming):
    """incoming whose variables named like one of outgoing's take its value (clamped to
    incoming's range) and its lane (Replace, equation to equation)."""
    old = {v.name: v for v in outgoing.variables} if outgoing is not None else {}
    if not any(v.name in old for v in incoming.variables): return incoming
    return replace(incoming, variables=tuple(
        replace(v, value=min(max(old[v.name].value, v.low), v.high), points=old[v.name].points) if v.name in old else v
        for v in incoming.variables))


def _swap_variables(lanes, outgoing, incoming):
    """(lanes, incoming) with outgoing's variables carried into incoming by name. outgoing
    gives up its lanes: same-named ones move to incoming, the rest are parked in lanes."""
    names = {v.name for v in incoming.variables} if incoming is not None else set()
    for variable in (outgoing.variables if outgoing is not None else ()):
        if variable.points and variable.name not in names: lanes = with_lane(lanes, parked_lane(variable.name), variable.points)
    if incoming is None: return lanes, None
    return _reclaim(lanes, carried(outgoing, incoming))


def _without_points(equation):
    if equation is None or not any(v.points for v in equation.variables): return equation
    return replace(equation, variables=tuple(replace(v, points=()) for v in equation.variables))


def replaced(shape, solid, custom=None, model=None, notices=None):
    """shape as a solid object. A parked drawing or model comes back before custom or model
    (the new one the editor asked for); ValueError if neither exists (needs_input). An
    incoming equation variable named like an object variable is renamed (renamed_clashes)."""
    if solid == shape.solid: return shape
    values = dict(solid=solid)
    outgoing = active_equation(shape)
    field = EQUATIONS.get(shape.solid)
    if field is not None: values[field] = _without_points(outgoing)  # Its lanes move below.
    incoming = None
    if solid in EQUATIONS:
        incoming = getattr(shape, EQUATIONS[solid]) or getattr(new_shape(solid), EQUATIONS[solid])
        incoming = renamed_clashes(incoming, {v.name for v in shape.transform_variables}, notices)
    lanes, incoming = _swap_variables(shape.lanes, outgoing, incoming)
    if incoming is not None: values[EQUATIONS[solid]] = incoming
    else:
        if solid == TEXT_OBJECT and shape.text == DEFAULT_TEXT: values['text'] = ADD_TEXT
        elif solid == MATRIX_OBJECT and shape.matrix is None: values['matrix'] = MatrixConfig()
        elif solid == CUSTOM_OBJECT and shape.custom is None:
            if custom is None: raise ValueError('Draw the custom shape first.')
            values['custom'] = custom
        elif solid == MODEL_OBJECT and shape.model is None:
            if model is None: raise ValueError('Choose a 3D model first.')
            values['model'] = model
    return replace(shape, lanes=lanes, **values)


def kept_parked_lanes(shape, notices=None):
    """shape without the parked variable lanes no parked equation declares (a scene being
    read); each dropped lane adds a notice."""
    names = [name for name in parked_lanes(shape.lanes) if declaring_type(shape, name) is None]
    if not names: return shape
    lanes = shape.lanes
    for name in names:
        lanes = with_lane(lanes, PARKED_LANE + name, ())
        if notices is not None: notices.append(f'Parked automation of variable {name} dropped: no parked equation declares it.')
    return replace(shape, lanes=lanes)
