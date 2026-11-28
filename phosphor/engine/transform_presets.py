"""Saved transforms (T6): a step or a whole stack, with the object variables it reads.

A preset keeps every field, expression, enabled flag and automation lane (the steps' own
Param points and On/Off points) and the object variables (T5) its expressions read, with
their own lanes. Inserting copies it into the object, so later edits to the preset never
change a scene. A preset variable named like one the object already has, or like one of the
object's active or parked equation variables, is renamed name_2, name_3… in the inserted
expressions (AST names, as T5 does), with a notice. Qt-free; storage is
storage/transform_presets.py.
"""
from dataclasses import dataclass, replace

from phosphor.engine.transforms import (
    MAX_OBJECT_VARIABLES, MAX_TRANSFORMS, TRANSFORM_RESERVED, Param, Transform,
    ROTATE, SPIN, TRANSLATE, SCALE, PERSPECTIVE, SUBDIVIDE, TWIST, WAVE,
    KALEIDOSCOPE, ECHO, QUANTIZE, GLITCH, POINTIFY,
    compiled_expression, read_object_variable, renamed_expression,
    validate_object_variables, validate_stack, validate_transform, variable_names,
)

STEP, STACK = 'step', 'stack'
KINDS = (STEP, STACK)
APPEND, REPLACE = 'append', 'replace'  # How a stack preset is inserted.


@dataclass(frozen=True)
class TransformPreset:
    kind: str  # STEP (one transform) or STACK (one or more).
    transforms: tuple
    variables: tuple = ()  # function_plot.Variable: the object variables the steps read.

    def document(self):
        return dict(kind=self.kind, transforms=[step.document() for step in self.transforms],
                    variables=[variable.document() for variable in self.variables])

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or set(value) != {'kind', 'transforms', 'variables'}:
            raise ValueError('Saved transform fields are missing or unsupported.')
        if value['kind'] not in KINDS: raise ValueError(f'Unknown saved transform kind {value["kind"]!r}.')
        if not isinstance(value['transforms'], list) or not isinstance(value['variables'], list):
            raise ValueError('Saved transform steps and variables must be lists.')
        if not 1 <= len(value['transforms']) <= MAX_TRANSFORMS or len(value['variables']) > MAX_OBJECT_VARIABLES:
            raise ValueError(f'A saved transform has 1–{MAX_TRANSFORMS} steps and at most {MAX_OBJECT_VARIABLES} variables.')
        variables = tuple(read_object_variable(item) for item in value['variables'])
        names = variable_names(variables)
        preset = cls(value['kind'], tuple(Transform.read(item, names) for item in value['transforms']), variables)
        validate_preset(preset)
        return preset


def validate_preset(preset):
    """Fields, expressions and names only: unlike validate_stack, this never requests Noise
    loops or runs the stack, so reading the library can't touch the realtime noise cache.
    inserted() runs validate_stack on the object's result."""
    if not isinstance(preset, TransformPreset) or preset.kind not in KINDS: raise ValueError('Invalid saved transform.')
    count = len(preset.transforms)
    if not count or (preset.kind == STEP and count != 1) or count > MAX_TRANSFORMS:
        raise ValueError('A saved step has one transform; a saved stack has 1–%d.' % MAX_TRANSFORMS)
    validate_object_variables(preset.variables)
    names = variable_names(preset.variables)
    for step in preset.transforms:
        if not isinstance(step, Transform): raise ValueError('Invalid saved transform step.')
        validate_transform(step, names)
    unused = set(names) - used_names(preset.transforms, names)
    if unused: raise ValueError(f'Saved variables {", ".join(sorted(unused))} are not read by any step.')


def used_names(transforms, names):
    """The names among names that the transforms' expressions read (compiled co_names)."""
    used = set()
    for step in transforms:
        for param in step.params:
            if param.expression:
                used |= set(compiled_expression(param.expression, 'expression', names).co_names) & set(names)
    return used


def _saved(value):
    """value without runtime-only fields (Param.clock, Variable.clock): a document round trip."""
    return TransformPreset.read(value.document())


def make_preset(kind, transforms, object_variables=()):
    """A preset of transforms (one step for STEP) with the object variables they read, in the
    object's order."""
    transforms = tuple(transforms)
    used = used_names(transforms, variable_names(object_variables))
    return _saved(TransformPreset(kind, transforms, tuple(v for v in object_variables if v.name in used)))


def _free_name(name, used):
    suffix = 2
    while f'{name}_{suffix}' in used: suffix += 1
    return f'{name}_{suffix}'


def renamed_preset(preset, taken, notices=None):
    """preset with each variable whose name is in taken ({name: why it is taken}) renamed
    name_2, name_3… in its steps' expressions; a notice per rename."""
    if not any(v.name in taken for v in preset.variables): return preset
    used = set(variable_names(preset.variables)) | set(taken) | set(TRANSFORM_RESERVED)
    transforms, variables = list(preset.transforms), list(preset.variables)
    for i, variable in enumerate(preset.variables):
        if variable.name not in taken: continue
        old, new = variable.name, _free_name(variable.name, used); used.add(new)
        variables[i] = replace(variable, name=new)
        for j, step in enumerate(transforms):
            params = tuple(replace(param, expression=renamed_expression(param.expression, old, new))
                           if param.expression else param for param in step.params)
            if params != step.params: transforms[j] = replace(step, params=params)
        if notices is not None:
            notices.append(f'Saved variable {old} renamed {new}: {taken[old]}.')
    return replace(preset, transforms=tuple(transforms), variables=tuple(variables))


def inserted(transforms, variables, preset, index=None, mode=APPEND, taken=None, notices=None):
    """(transforms, variables, first, count): the object's stack and variables with preset
    inserted. A step goes at index (after the selection; None: the end); a stack is appended at
    the end or replaces the stack (mode). taken: {name: what uses it} for the object's equation
    variables (active or parked), which object variables can't share. Raises ValueError if the
    result would not validate (too many steps or variables)."""
    transforms, variables = tuple(transforms), tuple(variables)
    clashes = {name: f'this object already has a variable {name}' for name in variable_names(variables)}
    for name, what in (taken or {}).items(): clashes.setdefault(name, f"{name} is a variable of this object's {what}")
    preset = renamed_preset(preset, clashes, notices)
    steps = preset.transforms
    if preset.kind == STACK and mode == REPLACE: stack, first = steps, 0
    else:
        first = len(transforms) if index is None or preset.kind == STACK else min(max(index, 0), len(transforms))
        stack = (*transforms[:first], *steps, *transforms[first:])
    if len(stack) > MAX_TRANSFORMS:
        raise ValueError(f'An object can have at most {MAX_TRANSFORMS} transforms; this would make {len(stack)}.')
    combined = (*variables, *preset.variables)
    if len(combined) > MAX_OBJECT_VARIABLES:
        raise ValueError(f'An object can have at most {MAX_OBJECT_VARIABLES} variables; this would make {len(combined)}.')
    validate_object_variables(combined, taken)
    validate_stack(stack, combined)
    return stack, combined, first, len(steps)


# Built-in starter transform stack recipes shown in Transform › Add › Recipes & Library Browser.
BUILTIN_RECIPES = {
    '3D Turntable': TransformPreset(STACK, (
        Transform(ROTATE, (Param(22.), Param(0.), Param(0.))),
        Transform(SPIN, (Param(0.), Param(0.18), Param(0.))),
        Transform(PERSPECTIVE, (Param(3.2),)),
    )),
    'Subdivided Twist': TransformPreset(STACK, (
        Transform(SUBDIVIDE, (Param(4.),)),
        Transform(TWIST, (Param(0.), Param(140., '140*sin(tau*0.2*t)'), Param(0.))),
    )),
    'Organic Ripple': TransformPreset(STACK, (
        Transform(SUBDIVIDE, (Param(4.),)),
        Transform(WAVE, (Param(0.14), Param(3.), Param(1.2), Param(1.))),
    )),
    'Mandala Kaleidoscope': TransformPreset(STACK, (
        Transform(SCALE, (Param(0.55), Param(0.55), Param(0.55))),
        Transform(TRANSLATE, (Param(0.32), Param(0.), Param(0.))),
        Transform(KALEIDOSCOPE, (Param(6.), Param(0.))),
        Transform(SPIN, (Param(0.), Param(0.), Param(0.08))),
    )),
    'Echo Tunnel': TransformPreset(STACK, (
        Transform(ECHO, (Param(5.), Param(0.82), Param(14.), Param(0.), Param(0.), Param(-0.18))),
        Transform(PERSPECTIVE, (Param(2.8),)),
    )),
    'Bit-Crushed Glitch': TransformPreset(STACK, (
        Transform(QUANTIZE, (Param(0.12), Param(0.12), Param(0.12))),
        Transform(GLITCH, (Param(0.14), Param(8.), Param(10.))),
        Transform(POINTIFY, (Param(96.), Param(0.05))),
    )),
}
