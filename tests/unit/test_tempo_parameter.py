import copy
import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace

import numpy as np

from phosphor.engine.clipboard import copy_clips, from_text, paste_clips
from phosphor.engine.composition import Clip, Note, Project, clip_settings, render_song
from phosphor.engine.function_plot import (
    CompiledPlot, FunctionPlot, Variable, PARAMETRIC_OBJECT, MAX_EXPRESSION_LENGTH,
    migrate_tempo_variable,
)
from phosphor.engine.scene_path import INSTANT_TRAVEL
from phosphor.engine.signal_engine import Settings, Shape, render, validate
from phosphor.engine.transforms import Param, new_transform, path_window
from phosphor.storage.app_files import scene_document, scene_from_document, read_scene, atomic_json
from phosphor.storage.function_presets import read_function_presets, write_function_presets
from phosphor.storage.project_files import project_document, project_from_document, read_project


def scene(plot=None, expression=None):
    shape = Shape(solid=PARAMETRIC_OBJECT, plot=plot) if plot else Shape(solid='Cube')
    if expression:
        shape = replace(shape, transforms=(new_transform('Rotate').with_param('z', Param(0., expression)),))
    return Settings(source='Single tone', travel_speed=INSTANT_TRAVEL, shapes=(shape,))


def legacy_scene(taken=False):
    document = scene_document(scene(FunctionPlot('a+s', 'a-s', (Variable('a', .3),))), 'drawing', '')
    document['version'] = 13
    document['shapes'][0]['plot'] = dict(
        x='p*cos(tau*s)+q', y='p*sin(tau*s)-q',
        variables=[dict(name='p', value=.3, low=0., high=1.),
                   # Variable expressions see t and s only (never other variables), so q cannot use p.
                   dict(name='q', value=.1, low=0., high=1., expression='0.15+0*t')]
        + ([dict(name='p_', value=.1, low=0., high=1.)] if taken else []))
    return document


class TempoParameterTests(unittest.TestCase):
    def setUp(self):
        self.times = .213 + np.arange(2400)/48000.

    def test_transform_exact_samples(self):
        for bpm in (120., 137.):
            a = replace(scene(expression='360*p'), tempo=bpm)
            b = replace(scene(expression=f'360*(t*{bpm}/60)'), tempo=bpm)
            np.testing.assert_array_equal(render(self.times, a)[0], render(self.times, b)[0])

    def test_equations_and_variable_per_sample_and_pose(self):
        for bpm in (120., 137.):
            a = FunctionPlot('sin(p)+v+s', 'cos(p)-v', (Variable('v', 0., -10., 10., 'p/4'),))
            b = FunctionPlot(f'sin(t*{bpm}/60)+v+s', f'cos(t*{bpm}/60)-v',
                             (Variable('v', 0., -10., 10., f'(t*{bpm}/60)/4'),))
            positions = np.mod(self.times*110., 1.)
            np.testing.assert_array_equal(CompiledPlot(a)(self.times, positions, bpm),
                                          CompiledPlot(b)(self.times, positions, bpm))
            for expression in (None, '30*s+p'):
                left = replace(scene(a, expression), tempo=bpm)
                right = replace(scene(b, expression.replace('p', f'(t*{bpm}/60)') if expression else None), tempo=bpm)
                np.testing.assert_array_equal(render(self.times, left)[0], render(self.times, right)[0])

    def test_path_window(self):
        for bpm in (120., 137.):
            a = scene(expression='20*p')
            shape = replace(a.shapes[0], path_start=Param(0., 'p%1'), path_trace=Param(1., 'cos(p)'))
            a = replace(a, shapes=(shape,), tempo=bpm)
            b = replace(a, shapes=(replace(shape, path_start=Param(0., f'(t*{bpm}/60)%1'),
                                          path_trace=Param(1., f'cos(t*{bpm}/60)')),))
            np.testing.assert_array_equal(render(self.times, a)[0], render(self.times, b)[0])
            self.assertEqual(path_window(shape.path_start, shape.path_trace, .3, bpm),
                             path_window(b.shapes[0].path_start, b.shapes[0].path_trace, .3, bpm))

    def test_composer_clocks_and_negative_hold(self):
        for bpm in (120., 137.):
            for local in (True, False):
                for offset in (-2., 1.25):
                    a = Clip(4., 8., scene(FunctionPlot('p/10+s/10', 'p/20'), '360*p'),
                             local_time=local, time_offset=offset)
                    b = replace(a, settings=scene(FunctionPlot(f'(t*{bpm}/60)/10+s/10', f'(t*{bpm}/60)/20'),
                                                   f'360*(t*{bpm}/60)'))
                    project = Project(bpm=bpm, clips=(a,), notes=(Note(0., 20., 60),))
                    times = project.seconds(np.linspace(4.01, 7.99, 2048))
                    np.testing.assert_array_equal(render_song(times, project, Settings()),
                                                  render_song(times, replace(project, clips=(b,)), Settings()))
                    self.assertEqual(clip_settings(a, Settings(), bpm).tempo, bpm)
        held = Clip(4., 8., scene(FunctionPlot('p', 'p'), '360*p'), time_offset=-2.)
        project = Project(clips=(held,), notes=(Note(0., 20., 60),))
        np.testing.assert_array_equal(render_song(np.linspace(2.01, 2.99, 100), project, Settings()), np.zeros((100, 2)))

    def test_runtime_only_and_unused_bit_identity(self):
        original = scene(FunctionPlot())
        document = scene_document(original, 'drawing', '')
        self.assertNotIn('tempo', document['settings'])
        for bpm in (20., 120., 137., 400.):
            changed = replace(original, tempo=bpm)
            self.assertEqual(scene_document(changed, 'drawing', ''), document)
            self.assertEqual(scene_from_document(document)[0].tempo, 120.)
            np.testing.assert_array_equal(render(self.times, original)[0], render(self.times, changed)[0])
        for bpm in (False, float('nan'), float('inf'), 19., 401.):
            with self.assertRaises(ValueError): validate(replace(original, tempo=bpm))

    def test_scene_migration_and_collision(self):
        for taken, name in ((False, 'p_'), (True, 'p_2')):
            document = legacy_scene(taken)
            before = copy.deepcopy(document)
            notices = []
            settings = scene_from_document(document, notices=notices)[0]
            self.assertEqual(document, before)
            self.assertEqual(settings.shapes[0].plot.variables[0].name, name)
            self.assertEqual(notices, [f'Variable p renamed to {name} (p is now the tempo parameter).'])
            # The old p was a static .3, and q evaluated to .15.
            expected = scene(FunctionPlot('.3*cos(tau*s)+.15', '.3*sin(tau*s)-.15'))
            np.testing.assert_array_equal(render(self.times, settings)[0], render(self.times, expected)[0])
        with self.assertRaisesRegex(ValueError, 'reserved'):
            scene_from_document(dict(legacy_scene(), version=14))
        long = legacy_scene()['shapes'][0]['plot']
        long['x'] = '+'.join(['p']*200)
        self.assertLessEqual(len(long['x']), MAX_EXPRESSION_LENGTH)
        with self.assertRaisesRegex(ValueError, 'migration exceeds'):
            migrate_tempo_variable(long)

    def test_all_readers_migrate(self):
        old = legacy_scene()
        project = project_document(Project(clips=(Clip(0., 8., scene()),)))
        project['clips'][0]['scene'] = old
        payload = copy_clips(Project(clips=(Clip(0., 8., scene()),)), (0,))
        payload['clips'][0]['scene'] = old
        notices = []
        migrated = from_text(json.dumps(payload), notices)
        self.assertEqual(len(notices), 1)
        pasted, _ = paste_clips(Project(), migrated, 0.)
        self.assertEqual(pasted.clips[0].settings.shapes[0].plot.variables[0].name, 'p_')
        notices = []
        self.assertEqual(project_from_document(project, notices).clips[0].settings.shapes[0].plot.variables[0].name, 'p_')
        self.assertEqual(len(notices), 1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'data.json'
            for value, reader in ((old, read_scene), (project, read_project)):
                atomic_json(path, value)
                notices = []
                reader(path, notices=notices)
                self.assertEqual(len(notices), 1)
            for version in (1, 2):
                atomic_json(path, dict(format='phosphor-function-presets', version=version,
                                       presets=[dict(name='Legacy', function=old['shapes'][0]['plot'])]))
                notices = []
                presets = read_function_presets(path, notices)
                self.assertEqual(presets['Legacy'].variables[0].name, 'p_')
                self.assertEqual(len(notices), 1)
                write_function_presets(presets, path)
                self.assertEqual(read_function_presets(path), presets)


if __name__ == '__main__':
    unittest.main()
