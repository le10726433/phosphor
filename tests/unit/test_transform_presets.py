import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from phosphor.engine.transform_presets import (APPEND, REPLACE, STACK, STEP, TransformPreset,
                                                inserted, make_preset, validate_preset)
from phosphor.engine.transforms import (MAP, MAX_OBJECT_VARIABLES, MAX_TRANSFORMS, NOISE, ROTATE,
                                        SCALE, Param, Variable, apply_stack, new_transform,
                                        variable_values)
from phosphor.storage.transform_presets import (FORMAT, VERSION, delete_transform_preset,
                                               read_transform_presets, rename_transform_preset,
                                               save_transform_preset)


LANE = ((0., 1., 'linear'), (4., .5, 'hold'))


def rotate_by(expression):
    return new_transform(ROTATE).with_param('z', Param(0., expression))


def example_stack():
    scale = new_transform(SCALE).with_param('x', Param(1., 'a*2'))
    mapped = new_transform(MAP).with_param('x', Param(0., 'y + b'))
    mapped = mapped.with_param('y', Param(0., 'x*b'))
    noise = new_transform(NOISE)
    noise = replace(noise, noise=replace(noise.noise, colour='Pink'))
    variables = (Variable('a', .75, 0., 2.), Variable('b', 1., -2., 2., 'sin(t)'))
    return (scale, mapped, noise), variables


class TransformPresetTests(unittest.TestCase):
    def test_runtime_clocks_are_dropped(self):
        angle = Param(0., 'k*t', LANE, clock=(.5, 0.))
        step = new_transform(ROTATE).with_param('z', angle)
        k = Variable('k', 2., 0., 5., points=LANE, clock=(.5, 0.))
        preset = make_preset(STEP, (step,), (k,))
        self.assertEqual(preset.transforms[0].param('z'), replace(angle, clock=()))
        self.assertEqual(preset.variables, (replace(k, clock=()),))

    def test_step_insert_position(self):
        before, after = new_transform(SCALE), new_transform(MAP)
        step = new_transform(ROTATE)
        preset = make_preset(STEP, (step,))
        for index, first, expected in ((1, 1, (before, step, after)),
                                       (None, 2, (before, after, step)),
                                       (-3, 0, (step, before, after)),
                                       (10, 2, (before, after, step))):
            with self.subTest(index=index):
                self.assertEqual(inserted((before, after), (), preset, index=index),
                                 (expected, (), first, 1))

    def test_inserted_stack_evaluates_like_the_source(self):
        source, variables = example_stack()
        preset = make_preset(STACK, source, variables)
        stack, copied, _, _ = inserted((), (), preset)
        points = np.array([[.25, -.5, 1.], [1., .75, -1.]])
        for t in (0., .5, 2.):
            with self.subTest(t=t):
                posed, noise = apply_stack(points, source, t, values=variable_values(variables, t))
                actual, actual_noise = apply_stack(points, stack, t, values=variable_values(copied, t))
                self.assertTrue(np.array_equal(actual, posed))
                self.assertTrue(np.array_equal(actual_noise, noise))

    def test_name_clashes_rewrite_only_inserted_steps(self):
        old_step = rotate_by('2*k')
        old_k = Variable('k', 3., 0., 4.)
        incoming = Variable('k', .25, 0., 1., points=LANE)
        kk = Variable('kk')
        step = rotate_by('k * sin( t ) + kk')
        preset = make_preset(STEP, (step,), (incoming, kk))
        for existing, new_name in (((old_k,), 'k_2'), ((old_k, Variable('k_2')), 'k_3')):
            with self.subTest(new_name=new_name):
                notices = []
                stack, variables, _, _ = inserted((old_step,), existing, preset, notices=notices)
                self.assertEqual(stack, (old_step, rotate_by(f'{new_name} * sin( t ) + kk')))
                self.assertEqual(variables, (*existing, replace(incoming, name=new_name), kk))
                self.assertEqual(len(notices), 1)
                self.assertRegex(notices[0], r'\bk\b')
                self.assertIn(new_name, notices[0])

    def test_equation_variable_reserves_its_name(self):
        k = Variable('k')
        preset = make_preset(STEP, (rotate_by('k*t'),), (k,))
        notices = []
        stack, variables, _, _ = inserted((), (), preset, taken={'k': 'Parametric equations'},
                                          notices=notices)
        self.assertEqual(stack, (rotate_by('k_2*t'),))
        self.assertEqual(variables, (replace(k, name='k_2'),))
        self.assertEqual(len(notices), 1)
        self.assertIn('Parametric equations', notices[0])

    def test_insertion_limits(self):
        preset = make_preset(STEP, (rotate_by('k'),), (Variable('k'),))
        with self.assertRaises(ValueError):
            inserted((new_transform(SCALE),) * MAX_TRANSFORMS, (), preset)
        variables = tuple(Variable(f'v{i}') for i in range(MAX_OBJECT_VARIABLES))
        with self.assertRaises(ValueError):
            inserted((), variables, preset)

    def test_validate_step_and_stack_lengths(self):
        for preset in (TransformPreset(STEP, (new_transform(SCALE), new_transform(ROTATE))),
                       TransformPreset(STACK, ())):
            with self.subTest(kind=preset.kind), self.assertRaises(ValueError):
                validate_preset(preset)


class TransformPresetFileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'transform-presets.json'

    def test_step_round_trip_with_variables_and_lanes(self):
        step = new_transform(ROTATE).with_param('z', Param(0., 'k*t', LANE))
        step = replace(step, enabled=False, on=Param(1., points=((0., 0., 'hold'), (1., 1., 'hold'))))
        k = Variable('k', 3., 0., 10., points=((0., .75, 'hold'),))
        preset = make_preset(STEP, (step,), (Variable('m'), k))
        self.assertEqual(preset, TransformPreset(STEP, (step,), (k,)))
        save_transform_preset('Turn', preset, path=self.path)
        self.assertEqual(read_transform_presets(path=self.path), {'Turn': preset})
        document = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(document['format'], FORMAT)
        self.assertEqual(document['version'], VERSION)
        self.assertEqual(document['presets'][0]['preset'], preset.document())

    def test_stack_round_trip_append_and_replace(self):
        steps, variables = example_stack()
        preset = make_preset(STACK, steps, variables)
        save_transform_preset('Stack', preset, path=self.path)
        saved = read_transform_presets(path=self.path)['Stack']
        self.assertEqual(saved, TransformPreset(STACK, steps, variables))
        old = new_transform(ROTATE)
        self.assertEqual(inserted((old,), (), saved, mode=APPEND), ((old, *steps), variables, 1, 3))
        self.assertEqual(inserted((old,), (), saved, mode=REPLACE), (steps, variables, 0, 3))

    def test_overwriting_saved_step_preserves_earlier_insertion(self):
        angle = Param(30., points=LANE)
        step = new_transform(ROTATE).with_param('z', angle)
        save_transform_preset('Turn', make_preset(STEP, (step,)), path=self.path)
        stack, _, _, _ = inserted((), (), read_transform_presets(path=self.path)['Turn'])
        changed = step.with_param('z', replace(angle, value=45.))
        save_transform_preset('Turn', make_preset(STEP, (changed,)), path=self.path)
        self.assertEqual(read_transform_presets(path=self.path)['Turn'].transforms, (changed,))
        self.assertEqual(stack, (step,))

    def test_missing_file_is_empty(self):
        self.assertEqual(read_transform_presets(path=self.path), {})

    def test_damaged_files_refuse_read_and_save(self):
        preset = make_preset(STEP, (new_transform(ROTATE),))
        entry = dict(name='Turn', preset=preset.document())
        document = dict(format=FORMAT, version=VERSION, presets=[entry])
        unknown = dict(preset.document(), transforms=[dict(preset.transforms[0].document(), kind='Unknown')])
        undeclared = TransformPreset(STEP, (rotate_by('missing*t'),)).document()
        cases = {
            'not JSON': '{',
            'list': '[]',
            'format': json.dumps(dict(document, format='other')),
            'zero version': json.dumps(dict(document, version=0)),
            'string version': json.dumps(dict(document, version=str(VERSION))),
            'boolean version': json.dumps(dict(document, version=True)),
            'extra entry key': json.dumps(dict(document, presets=[dict(entry, extra=0)])),
            'unknown transform': json.dumps(dict(document, presets=[dict(entry, preset=unknown)])),
            'undeclared variable': json.dumps(dict(document, presets=[dict(entry, preset=undeclared)])),
            'duplicate names': json.dumps(dict(document, presets=[entry, entry])),
        }
        for label, content in cases.items():
            original = content.encode('utf-8')
            self.path.write_bytes(original)
            with self.subTest(case=label, operation='read'), self.assertRaises(ValueError):
                read_transform_presets(path=self.path)
            with self.subTest(case=label, operation='save'), self.assertRaises(ValueError):
                save_transform_preset('Other', preset, path=self.path)
            with self.subTest(case=label, operation='preserve file'):
                self.assertEqual(self.path.read_bytes(), original)

    def test_newer_file_refuses_edits_and_preserves_bytes(self):
        preset = make_preset(STEP, (new_transform(ROTATE),))
        document = dict(format=FORMAT, version=VERSION + 1,
                        presets=[dict(name='Turn', preset=preset.document())])
        original = json.dumps(document, indent=4).encode('utf-8')
        for operation, args in ((read_transform_presets, ()), (save_transform_preset, ('Other', preset)),
                                (rename_transform_preset, ('Turn', 'Other')), (delete_transform_preset, ('Turn',))):
            with self.subTest(operation=operation.__name__):
                self.path.write_bytes(original)
                with self.assertRaisesRegex(ValueError, 'newer'):
                    operation(*args, path=self.path)
                self.assertEqual(self.path.read_bytes(), original)

    def test_rename_keeps_order_and_delete_leaves_valid_file(self):
        turn = make_preset(STEP, (new_transform(ROTATE),))
        scale = make_preset(STEP, (new_transform(SCALE),))
        save_transform_preset('Turn', turn, path=self.path)
        save_transform_preset('Scale', scale, path=self.path)
        rename_transform_preset('Turn', 'Spin', path=self.path)
        for old, new in (('Spin', 'Scale'), ('Missing', 'New'), ('Spin', ''), ('Spin', 'x'*41)):
            with self.subTest(old=old, new=new), self.assertRaises(ValueError):
                rename_transform_preset(old, new, path=self.path)
        for name in ('', 'x'*41):
            with self.subTest(name=name), self.assertRaises(ValueError):
                save_transform_preset(name, turn, path=self.path)
        self.assertEqual(list(read_transform_presets(path=self.path).items()), [('Spin', turn), ('Scale', scale)])
        delete_transform_preset('Spin', path=self.path)
        self.assertEqual(read_transform_presets(path=self.path), {'Scale': scale})
        delete_transform_preset('Scale', path=self.path)
        self.assertTrue(self.path.is_file())
        self.assertEqual(read_transform_presets(path=self.path), {})



class ReviewFindingTests(unittest.TestCase):
    """Cases from Codex's review of T6 (November 21, 2026)."""
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.path = Path(self.folder.name)/'transform-presets.json'

    def tearDown(self): self.folder.cleanup()

    def entry(self, preset):
        return dict(format=FORMAT, version=VERSION, presets=[dict(name='Bad', preset=preset)])

    def test_odd_types_are_reported_as_damage(self):
        step = make_preset(STEP, (new_transform(ROTATE),)).document()
        huge = json.loads(json.dumps(step)); huge['transforms'][0]['params']['z'] = 10**400
        kind = json.loads(json.dumps(step)); kind['transforms'][0]['kind'] = []
        for text in (json.dumps(self.entry(kind)), json.dumps(self.entry(huge)),
                     '[' * 100000 + ']' * 100000, json.dumps(dict(format=FORMAT, version=True, presets=[])),
                     json.dumps(dict(format=FORMAT, version=VERSION, presets=[dict(name=3, preset=step)]))):
            with self.subTest(text=text[:60]):
                self.path.write_text(text, encoding='utf-8')
                with self.assertRaises(ValueError): read_transform_presets(self.path)

    def test_long_lists_are_refused_before_reading_them(self):
        step = new_transform(ROTATE).document()
        many = dict(kind=STACK, transforms=[step] * 100000, variables=[])
        self.path.write_text(json.dumps(self.entry(many)), encoding='utf-8')
        with self.assertRaises(ValueError): read_transform_presets(self.path)

    def test_variables_no_step_reads_are_refused(self):
        spare = make_preset(STEP, (new_transform(ROTATE),)).document()
        spare['variables'] = [Variable('spare', 1.).document()]
        self.path.write_text(json.dumps(self.entry(spare)), encoding='utf-8')
        with self.assertRaises(ValueError): read_transform_presets(self.path)

    def test_reading_never_requests_noise_loops(self):
        from unittest import mock
        stack, variables = example_stack()
        noise = replace(stack[2], noise=replace(stack[2].noise, colour='Custom', curve=((20., 0.), (20000., -40.))))
        preset = make_preset(STACK, (noise,), variables)
        save_transform_preset('Custom noise', preset, path=self.path)
        with mock.patch('phosphor.engine.noise_loops.request') as request, mock.patch('phosphor.engine.noise_loops.get') as get:
            self.assertEqual(read_transform_presets(self.path), {'Custom noise': preset})
        request.assert_not_called(); get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
