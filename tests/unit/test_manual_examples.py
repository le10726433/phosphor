"""Every example in the user manual (docs/manual) runs in the engine.

Examples carry a hidden spec under the visible text: <!-- example: {...} --> (JSON); the
format is in docs/manual/README.md (Examples in this manual). Each
one is built as the manual describes and rendered; it must be valid, finite, and actually
draw something (a scene that is silent or flat would contradict the manual's picture).
"""
from dataclasses import replace
import json
from pathlib import Path
import re
import unittest

import numpy as np

from phosphor.engine.composition import Clip, Note, Project, render_song, validate_project
from phosphor.engine.function_plot import FunctionPlot, Variable, PARAMETRIC_OBJECT, CompiledPlot
from phosphor.engine.lane_access import automatable, set_points
from phosphor.engine.scene_path import INSTANT_TRAVEL
from phosphor.engine.signal_engine import Settings, Shape, render, validate
from phosphor.engine.transforms import FIELDS, KINDS, Param, NoiseOptions, new_transform

MANUAL = Path(__file__).resolve().parents[2]/'docs'/'manual'
SPEC = re.compile(r'<!--\s*example:\s*(\{.*?\})\s*-->')
FENCE = re.compile(r'^```.*?^```', re.S | re.M)
TIMES = 1.25 + np.arange(4800)/48000.  # 0.1 s, away from t = 0.


def manual_examples():
    for chapter in sorted(MANUAL.glob('*.md')):
        text = FENCE.sub('', chapter.read_text(encoding='utf-8'))  # Format samples in code blocks are not examples.
        for match in SPEC.finditer(text):
            yield chapter.name, match.group(1)


def number(value):
    """A spec value: a number, or an expression string (static when it parses as a number)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool): return Param(float(value))
    try: return Param(float(value))
    except ValueError: return Param(0., str(value))


def plot(spec):
    if 'x' not in spec: return FunctionPlot()  # The default equation (a circle).
    variables = tuple(Variable(v['name'], float(v['value']), float(v['low']), float(v['high']), v.get('expression', ''))
                      for v in spec.get('variables', ()))
    return FunctionPlot(spec['x'], spec['y'], variables)


def base(shapes, travel=INSTANT_TRAVEL):
    return Settings(source='Single tone', tone_frequency=110., travel_speed=travel, shapes=tuple(shapes))


def shape_for(spec):
    name = spec.get('object', PARAMETRIC_OBJECT if 'x' in spec else 'Cube')
    # Object variables (T5): "object_variables": [{"name", "value", "low", "high", "expression"?}].
    variables = tuple(Variable(v['name'], float(v['value']), float(v['low']), float(v['high']), v.get('expression', ''))
                      for v in spec.get('object_variables', ()))
    if name == PARAMETRIC_OBJECT: return Shape(solid=PARAMETRIC_OBJECT, plot=plot(spec), transform_variables=variables)
    return Shape(solid=name, transform_variables=variables)


def stack(steps):
    result = []
    for step in steps:
        transform = new_transform(step['transform'])
        if 'noise' in step:
            options = dict(step['noise'])
            if 'curve' in options: options['curve'] = tuple(tuple(node) for node in options['curve'])
            transform = replace(transform, noise=NoiseOptions(**options))
        names = {field.name for field in FIELDS[step['transform']]}
        for field, value in step.get('fields', {}).items():
            if field not in names: raise ValueError(f'{step["transform"]} has no field {field!r} (fields: {sorted(names)})')
            transform = transform.with_param(field, number(value))
        result.append(transform)
    return tuple(result)


def lane_key(text, clip):
    parts = text.split(':')
    if parts[0] == 'mix': return ('mix', parts[1])
    if parts[0] == 'transform': return ('transform', 0, int(parts[1]), parts[2])
    if parts[0] in ('path', 'shape', 'variable', 'transform_variable'): return (parts[0], 0, parts[1])
    raise ValueError(f'Unknown lane key {text!r}')


class ManualExampleTests(unittest.TestCase):
    def assert_draws(self, settings, label):
        validate(settings)
        signal = render(TIMES, settings)[0]
        self.assertTrue(np.isfinite(signal).all(), label)
        self.assertGreater(float(np.ptp(signal[:, 0]) + np.ptp(signal[:, 1])), 1e-3, f'{label}: draws nothing')

    def test_examples_exist(self):
        self.assertGreaterEqual(len(list(manual_examples())), 1, 'the manual has no checked examples')

    def test_every_example(self):
        seen = {}
        for chapter, text in manual_examples():
            with self.subTest(chapter=chapter, spec=text[:100]):
                spec = json.loads(text)
                self.assertRegex(spec['id'], r'^[a-z0-9-]+$')
                self.assertNotIn(spec['id'], seen, f'duplicate example id (also in {seen.get(spec["id"])})')
                seen[spec['id']] = chapter
                kind = spec['kind']
                if kind == 'equation':
                    compiled = CompiledPlot(plot(spec))
                    xy = compiled(TIMES, np.mod(TIMES*110., 1.))
                    self.assertTrue(np.isfinite(xy).all())
                    self.assertLessEqual(float(np.abs(xy).max()), 2.5, 'far outside the screen')
                    self.assert_draws(base([shape_for(spec)]), spec['id'])
                elif kind == 'transform':
                    for step in spec['steps']: self.assertIn(step['transform'], KINDS)
                    shape = replace(shape_for(spec), transforms=stack(spec['steps']))
                    self.assert_draws(base([shape]), spec['id'])
                elif kind == 'path':
                    shape = replace(shape_for(spec), path_start=number(spec['start']), path_trace=number(spec['trace']))
                    self.assert_draws(base([shape]), spec['id'])
                elif kind == 'lane':
                    # A lane spec carries its context: "object", "steps", or "x"/"y"/"variables".
                    shape = replace(shape_for(spec), transforms=stack(spec.get('steps', ())))
                    clip = Clip(0., 16., base([shape]))
                    key = lane_key(spec['key'], clip)
                    self.assertIn(key, automatable(clip), f'{spec["key"]} is not automatable on this clip')
                    points = tuple((float(b), float(g), s) for b, g, s in spec['points'])
                    clip = set_points(clip, key, points)
                    project = validate_project(Project(clips=(clip,), notes=(Note(0., 16., 57),)))
                    song = render_song(project.seconds(np.linspace(.5, 15.5, 4096)), project, Settings())
                    self.assertTrue(np.isfinite(song).all())
                else:
                    self.fail(f'unknown example kind {kind!r}')


if __name__ == '__main__':
    unittest.main()
