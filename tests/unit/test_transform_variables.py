"""Transforms T5 (0.81.0): object variables shared by the stack and by Path Start and Trace."""
from dataclasses import replace
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile

from phosphor.engine import object_replace as r
from phosphor.engine.automation import parked_lane, parked_lanes
from phosphor.engine.composition import Project, Clip, Note, clip_settings, render_song, export_song
from phosphor.engine.function_plot import FunctionPlot, Variable, PARAMETRIC_OBJECT
from phosphor.engine.lane_access import automatable, blocks, get_points, set_points, label, static_value, block_of
from phosphor.engine.recurrence import RecurrencePlot, RECURRENCE_OBJECT
from phosphor.engine.signal_engine import Settings, Shape, render, validate, scene_plan, posed_object
from phosphor.engine.transforms import (Param, new_transform, ROTATE, SCALE, MAP, TRANSLATE, apply_stack, path_window,
                                        variable_values, validate_object_variables, check_variable_name,
                                        renamed_expression, renamed_variable, variables_in_use, new_variable_name,
                                        uses_position, MAX_OBJECT_VARIABLES, TRANSFORM_RESERVED)
from phosphor.storage.app_files import SCENE_VERSION, scene_document, scene_from_document, read_scene, atomic_json
from phosphor.storage.project_files import project_document, project_from_document

RATE = 48000
LANE = ((0., 0., 'linear'), (4., 1., 'linear'))
MACHINE = Settings(sample_rate=RATE)


def rotate_by(expression):
    return new_transform(ROTATE).with_param('z', Param(0., expression))


def shape_with(*variables, expression='a', **values):
    return Shape(transforms=(rotate_by(expression),), transform_variables=tuple(variables), **values)


class EvaluationTests(unittest.TestCase):
    def test_a_step_follows_its_variable_slider(self):
        times = np.arange(4800) / RATE
        for value in (0., 30., 75.):
            with self.subTest(value=value):
                shape = shape_with(Variable('a', value, -180., 180.))
                reference = Shape(transforms=(new_transform(ROTATE).with_param('z', Param(value)),))
                np.testing.assert_allclose(render(times, Settings(shapes=(shape,)))[0],
                                           render(times, Settings(shapes=(reference,)))[0], atol=1e-12)
        moved = render(times, Settings(shapes=(shape_with(Variable('a', 30., -180., 180.)),)))[0]
        self.assertFalse(np.allclose(moved, render(times, Settings(shapes=(Shape(),)))[0]))

    def test_values_are_evaluated_once_per_pose_frame_and_clipped(self):
        variables = (Variable('a', 2., 0., 5., '1 + t'), Variable('b', 9., 0., 10., '100*t'),
                     Variable('c', 1., -1., 1., 'log(t - 1)'), Variable('d', 4., 0., 10.))
        self.assertEqual(variable_values(variables, .5), dict(a=1.5, b=10., c=-1., d=4.))  # NaN gives min.
        self.assertEqual(variable_values(variables, 0.), dict(a=1., b=0., c=-1., d=4.))
        # p follows the tempo; s reads 0 (a whole-object number).
        self.assertEqual(variable_values((Variable('a', 0., 0., 100., 'p + s'),), 1., tempo=90.), dict(a=1.5))
        self.assertEqual(variable_values((), 1.), {})

    def test_variable_values_are_scalars_and_errors_name_the_field(self):
        with self.assertRaisesRegex(ValueError, 'Variable a: '):
            variable_values((Variable('a', 0., 0., 1., '1/0'),), 0.)
        stack = (rotate_by('a + q'),)
        with self.assertRaisesRegex(ValueError, r"Rotate Z \(°\): unknown name 'q'"):
            validate(Settings(shapes=(Shape(transforms=stack, transform_variables=(Variable('a'),)),)))

    def test_a_step_follows_its_variable_automation(self):
        """In Composer the lane multiplies the variable, which the step then reads."""
        automated = Settings(shapes=(shape_with(Variable('a', 90., -180., 180., points=LANE)),))
        for local in (True, False):
            def project(settings):
                return Project(clips=(Clip(4., 8., settings, local_time=local),), notes=(Note(0., 16., 60),))
            for beat, gain in ((4.5, 1/8), (6., .5), (7.5, 7/8)):
                with self.subTest(local=local, beat=beat):
                    times = beat * .5 + np.arange(400) / RATE  # 120 BPM: a beat is half a second.
                    static = Settings(shapes=(shape_with(Variable('a', 90.*gain, -180., 180.)),))
                    expected = render_song(times[:1], Project(clips=(Clip(4., 8., static, local_time=local),),
                                                              notes=(Note(0., 16., 60),)), MACHINE)
                    np.testing.assert_allclose(render_song(times[:1], project(automated), MACHINE), expected, atol=1e-9)
        baked = clip_settings(Clip(4., 8., automated), Settings(), 120.).shapes[0]
        self.assertEqual(baked.transform_variables[0].clock, (.5, 0.))  # Clip-local time (the default).
        self.assertEqual(variable_values(baked.transform_variables, 1.), dict(a=45.))
        # The Sandbox holds the lane's first gain, as every lane there does.
        self.assertEqual(variable_values(automated.shapes[0].transform_variables, 3.), dict(a=0.))

    def test_playback_blocks_equal_one_render_and_export(self):
        variable = Variable('a', 40., -180., 180., '40*sin(tau*0.5*t)', points=((0., 1., 'linear'), (8., -1., 'linear')))
        shape = replace(shape_with(variable, expression='a + 10*y'),
                        path_start=Param(0., '0.1*a/40'), path_trace=Param(1., '0.75 + 0.2*a/40'))
        settings = Settings(shapes=(shape,))
        times = np.arange(9600) / RATE
        whole = render(times, settings)[0]
        blocks_ = np.concatenate([render(times[i:i+480], settings)[0] for i in range(0, len(times), 480)])
        np.testing.assert_array_equal(blocks_, whole)
        project = Project(clips=(Clip(0., 4., settings),), notes=(Note(0., 4., 57),))
        song = render_song(times, project, MACHINE)
        parts = np.concatenate([render_song(times[i:i+480], project, MACHINE) for i in range(0, len(times), 480)])
        np.testing.assert_array_equal(parts, song)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'export.wav'
            export_song(path, project, MACHINE, end_beats=.4, format='WAV', depth=32)
            samples, rate = soundfile.read(path, dtype='float32', always_2d=True)
        self.assertEqual(rate, RATE)
        np.testing.assert_array_equal(samples, song[:len(samples)].astype('float32'))
        self.assertGreater(np.ptp(song), .1)

    def test_path_start_and_trace_see_the_variables(self):
        variables = (Variable('k', .25, 0., 1.), Variable('w', .5, -1., 1.))
        values = variable_values(variables, 0.)
        self.assertEqual(path_window(Param(0., 'k'), Param(1., 'w'), 0., values=values), (.25, .5))
        shape = Shape(transform_variables=variables, path_start=Param(0., 'k'), path_trace=Param(1., 'w'))
        reference = Shape(path_start=Param(.25), path_trace=Param(.5))
        times = np.arange(2400) / RATE
        validate(Settings(shapes=(shape,)))
        np.testing.assert_allclose(render(times, Settings(shapes=(shape,)))[0],
                                   render(times, Settings(shapes=(reference,)))[0], atol=1e-12)
        with self.assertRaisesRegex(ValueError, "Path Start: unknown name 'k'"):
            validate(Settings(shapes=(replace(shape, transform_variables=()),)))

    def test_every_step_kind_and_builder_paths_see_the_variables(self):
        values = dict(a=.5)
        points = np.array([[1., 0., 0.], [0., 1., 0.]])
        moved, _ = apply_stack(points, (new_transform(TRANSLATE).with_param('x', Param(0., 'a')),), 0., values=values)
        np.testing.assert_allclose(moved, points + [.5, 0., 0.])
        mapped = replace(new_transform(MAP), params=(Param(0., 'x*a'), Param(0., 'y'), Param(0., 'z')))
        np.testing.assert_allclose(apply_stack(points, (mapped,), 0., values=values)[0], points * [.5, 1., 1.])
        shape = Shape(transforms=(new_transform(SCALE).with_param('x', Param(1., 'a*2')),), transform_variables=(Variable('a', .5, 0., 1.),))
        np.testing.assert_allclose(posed_object(0., shape)[0], posed_object(0., replace(shape, transforms=(
            new_transform(SCALE).with_param('x', Param(1.)),), transform_variables=()))[0])

    def test_without_variables_nothing_changes(self):
        """No variables: the exact old path (golden audio stays byte-identical)."""
        stack = (rotate_by('30*sin(t)'),)
        times = np.arange(4800) / RATE
        plain = Settings(shapes=(Shape(transforms=stack),))
        unused = Settings(shapes=(Shape(transforms=stack, transform_variables=(Variable('a', 3., 0., 5.),)),))
        np.testing.assert_array_equal(render(times, unused)[0], render(times, plain)[0])
        self.assertEqual(scene_document(plain, 'drawing', '')['shapes'][0].keys() & {'transform_variables'}, set())

    def test_route_positions_only_when_a_step_reads_s(self):
        self.assertFalse(uses_position((rotate_by('a'),), ('a',)))
        self.assertTrue(uses_position((rotate_by('a*s'),), ('a',)))
        plan = scene_plan(3, 60., (shape_with(Variable('a', 10., -180., 180.), expression='a*s'),), 'No changes', 8.)
        self.assertIsNotNone(plan)


class NameTests(unittest.TestCase):
    def test_name_rules(self):
        for name in ('a', 'speed', 'v_2', '_k', 'gxx', 'n'):
            check_variable_name(name)
        for name, message in (('2a', 'letters, digits'), ('a-b', 'letters, digits'), ('', 'letters, digits'),
                              ('t', 'reserved'), ('p', 'reserved'), ('s', 'reserved'), ('sin', 'reserved'),
                              ('pi', 'reserved'), ('tau', 'reserved'), ('math', 'reserved'), ('if', 'reserved'),
                              ('x', 'point coordinate'), ('y', 'point coordinate'), ('z', 'point coordinate'),
                              ('gx', 'copy index'), ('gy', 'copy index'), ('gz', 'copy index'),
                              ('_where', 'reserved'), ('_and', 'reserved'), ('_or', 'reserved'), ('_not', 'reserved')):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, message): check_variable_name(name)
        with self.assertRaisesRegex(ValueError, 'already has a variable a'):
            validate_object_variables((Variable('a'), Variable('a')))
        with self.assertRaisesRegex(ValueError, f'at most {MAX_OBJECT_VARIABLES}'):
            validate_object_variables(tuple(Variable(f'v{i}') for i in range(MAX_OBJECT_VARIABLES + 1)))
        with self.assertRaisesRegex(ValueError, 'min < max'):
            validate_object_variables((Variable('a', 1., 2., 1.),))
        with self.assertRaisesRegex(ValueError, "Variable a: unknown name 'x'"):
            validate_object_variables((Variable('a', 1., 0., 2., 'x + t'),))  # Whole-object: no point names.
        self.assertNotIn(new_variable_name({'a', 'b'}), {'a', 'b', 'e', 'p', 's', 't', *TRANSFORM_RESERVED})

    def test_names_of_equation_variables_are_refused(self):
        """User decision (November 21, 2026): active and parked equation variables keep their names."""
        parametric = Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot('k*sin(tau*s)', 'cos(tau*s)', (Variable('k', 1., 0., 2.),)))
        with self.assertRaisesRegex(ValueError, "k is a variable of this object's Parametric equations"):
            validate(Settings(shapes=(replace(parametric, transform_variables=(Variable('k'),)),)))
        parked = r.replaced(replace(parametric, plot=replace(parametric.plot, variables=(Variable('k', points=LANE),))), 'Cube')
        self.assertEqual(r.equation_variables(parked), dict(k='parked Parametric equations'))
        with self.assertRaisesRegex(ValueError, "parked Parametric equations"):
            validate(Settings(shapes=(replace(parked, transform_variables=(Variable('k'),)),)))
        recurrence = r.new_shape(RECURRENCE_OBJECT)
        recurrence = replace(recurrence, recurrence=replace(recurrence.recurrence, x='sin(n*0.1*q)', variables=(Variable('q'),)))
        with self.assertRaisesRegex(ValueError, 'Recurrence equations'):
            validate(Settings(shapes=(replace(recurrence, transform_variables=(Variable('q'),)),)))
        validate(Settings(shapes=(replace(parametric, transform_variables=(Variable('a'),)),)))

    def test_an_incoming_equation_variable_with_a_taken_name_is_renamed(self):
        """Least surprise, as T6's presets: the object's variable stays, the equation's is renamed."""
        plot = FunctionPlot('k*sin(tau*s) + kk', 'cos(k*tau*s)', (Variable('k', 1., 0., 2.), Variable('kk', 0., 0., 1.)))
        notices = []
        renamed = r.renamed_clashes(plot, {'k'}, notices)
        self.assertEqual((renamed.x, renamed.y), ('k_2*sin(tau*s) + kk', 'cos(k_2*tau*s)'))
        self.assertEqual([v.name for v in renamed.variables], ['k_2', 'kk'])
        self.assertEqual(notices, ['Equation variable k renamed k_2: the object has a variable k.'])
        self.assertIs(r.renamed_clashes(plot, {'q'}), plot)
        # Replace: the incoming equation is renamed, the object variable and its lane stay.
        cube = Shape(transform_variables=(Variable('k', 2., 0., 5., points=LANE),), transforms=(rotate_by('k'),))
        incoming = replace(cube, plot=plot)
        result = r.replaced(incoming, PARAMETRIC_OBJECT)
        self.assertEqual([v.name for v in result.plot.variables], ['k_2', 'kk'])
        self.assertEqual(result.transform_variables, cube.transform_variables)
        validate(Settings(shapes=(result,)))


class RenameTests(unittest.TestCase):
    def test_rename_follows_ast_names_and_keeps_the_text(self):
        self.assertEqual(renamed_expression('a + 2*a_b + sin(a)*abs(a)', 'a', 'w'), 'w + 2*a_b + sin(w)*abs(w)')
        self.assertEqual(renamed_expression('  a*tau  ', 'a', 'beta'), '  beta*tau  ')
        # Python reads a full-width ｋ as k, so the rename must find it (overseer, November 21, 2026).
        self.assertEqual(renamed_expression('ｋ*s + 1', 'k', 'k_2'), 'k_2*s + 1')
        self.assertEqual(renamed_expression('ｋ + k', 'k', 'k_2'), 'k_2 + k_2')
        self.assertEqual(renamed_expression('math.pi * a', 'a', 'b'), 'math.pi * b')
        self.assertEqual(renamed_expression('tau*t', 'a', 'b'), 'tau*t')  # 'a' only in tau: untouched.
        self.assertEqual(renamed_expression('1 +', 'a', 'b'), '1 +')
        self.assertEqual(renamed_expression('b if a > 0 else -a', 'a', 'c'), 'b if c > 0 else -c')

    def test_rename_updates_every_expression_that_uses_it(self):
        motion = new_transform('Motion path')
        steps = (rotate_by('a*90'), motion, new_transform(SCALE).with_param('y', Param(1., '1 + 0.1*a', LANE)))
        shape = Shape(transforms=steps, transform_variables=(Variable('a', 1., 0., 2.), Variable('b')),
                      path_start=Param(0., 'a/4'), path_trace=Param(1., 'b/10'))
        validate(Settings(shapes=(shape,)))
        self.assertEqual(variables_in_use(shape), {'a', 'b'})
        renamed = renamed_variable(shape, 'a', 'speed')
        self.assertEqual([v.name for v in renamed.transform_variables], ['speed', 'b'])
        self.assertEqual(renamed.transforms[0].param('z').expression, 'speed*90')
        self.assertIs(renamed.transforms[1], motion)  # Untouched steps stay the same object.
        self.assertEqual(renamed.transforms[2].param('y'), Param(1., '1 + 0.1*speed', LANE))
        self.assertEqual((renamed.path_start.expression, renamed.path_trace), ('speed/4', shape.path_trace))
        validate(Settings(shapes=(renamed,)))
        times = np.arange(2400) / RATE
        np.testing.assert_array_equal(render(times, Settings(shapes=(renamed,)))[0], render(times, Settings(shapes=(shape,)))[0])


class UndoLabelTests(unittest.TestCase):
    def test_sandbox_labels(self):
        from phosphor.editor.history import SceneState, describe_scene, STRUCTURAL

        def state(shape): return SceneState(Settings(shapes=(shape,)), 'drawing', 0)
        plain = shape_with(Variable('a', 1., 0., 90.))
        added = replace(plain, transform_variables=(*plain.transform_variables, Variable('b')))
        cases = ((plain, added, 'Add variable'), (added, plain, 'Remove variable'),
                 (plain, renamed_variable(plain, 'a', 'w'), 'Rename variable'),
                 (plain, replace(plain, transform_variables=(Variable('a', 5., 0., 90.),)), 'Edit variable'))
        for before, after, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(describe_scene(state(before), state(after)), expected)
        self.assertLessEqual({'Add variable', 'Remove variable', 'Rename variable'}, STRUCTURAL)


class LaneTests(unittest.TestCase):
    def clip(self):
        equation = Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot('a*sin(tau*s)', 'cos(tau*s)', (Variable('a', 1., 0., 2.),)),
                         transform_variables=(Variable('w', 30., -180., 180.),), transforms=(rotate_by('w'),))
        return Clip(0., 4., Settings(shapes=(equation,)))

    def test_own_key_distinct_from_equation_and_parked_keys(self):
        clip = self.clip()
        keys = automatable(clip)
        self.assertIn(('transform_variable', 0, 'w'), keys)
        self.assertIn(('variable', 0, 'a'), keys)
        self.assertNotIn(('transform_variable', 0, 'a'), keys)
        self.assertEqual(block_of(('transform_variable', 0, 'w')), ('transform_variable', 0))
        clip = set_points(clip, ('transform_variable', 0, 'w'), LANE)
        shape = clip.settings.shapes[0]
        self.assertEqual(shape.transform_variables[0].points, LANE)
        self.assertEqual((shape.lanes, shape.plot.variables[0].points), ((), ()))  # Never a Shape.lanes entry.
        self.assertEqual(get_points(clip, ('transform_variable', 0, 'w')), LANE)
        self.assertEqual(get_points(clip, ('variable', 0, 'a')), ())
        self.assertEqual(label(clip, ('transform_variable', 0, 'w')), 'Object variable w')
        self.assertEqual(static_value(clip, ('transform_variable', 0, 'w')), 30.)
        self.assertEqual([b.title for b in blocks(clip)], ['Parametric equation › Variables'])
        clip = set_points(clip, ('variable', 0, 'a'), LANE)
        self.assertEqual(clip.settings.shapes[0].transform_variables[0].points, LANE)
        self.assertEqual(set_points(clip, ('transform_variable', 0, 'w'), ()).settings.shapes[0].transform_variables[0].points, ())

    def test_variables_and_their_lanes_survive_replace(self):
        clip = set_points(self.clip(), ('transform_variable', 0, 'w'), LANE)
        clip = set_points(clip, ('variable', 0, 'a'), LANE)
        shape = clip.settings.shapes[0]
        cube = r.replaced(shape, 'Cube')
        self.assertEqual(cube.transform_variables, shape.transform_variables)  # Not parked: the object's own.
        self.assertEqual(parked_lanes(cube.lanes), {'a': LANE})  # Only the equation variable is parked.
        self.assertNotIn(parked_lane('w'), dict(cube.lanes))
        keys = automatable(replace(clip, settings=replace(clip.settings, shapes=(cube,))))
        self.assertIn(('transform_variable', 0, 'w'), keys)
        self.assertIn(('parked', 0, 'a'), keys)
        back = r.replaced(cube, PARAMETRIC_OBJECT)
        self.assertEqual(back.transform_variables, shape.transform_variables)
        self.assertEqual(back.plot.variables[0].points, LANE)
        recurrence = r.replaced(cube, RECURRENCE_OBJECT)
        self.assertEqual(recurrence.transform_variables, shape.transform_variables)
        validate(Settings(shapes=(cube, back, recurrence)))
        times = np.arange(1200) / RATE
        np.testing.assert_array_equal(render(times, Settings(shapes=(cube,)))[0],
                                      render(times, Settings(shapes=(replace(cube, lanes=()),)))[0])

    def test_older_data_with_a_same_named_equation_lane_keeps_its_key(self):
        """Older scenes (version 23) store an equation variable's lane in its points and a parked
        one as 'variable:<name>' in Shape.lanes; neither becomes an object-variable key."""
        equation = Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot('w*sin(tau*s)', 'cos(tau*s)', (Variable('w', 1., 0., 2., points=LANE),)))
        parked = r.replaced(equation, 'Cube')
        self.assertEqual(parked_lanes(parked.lanes), {'w': LANE})
        document = scene_document(Settings(shapes=(equation, parked)), 'drawing', '')
        document = json.loads(json.dumps(dict(document, version=23)))
        settings, _, _ = scene_from_document(document)
        keys = automatable(Clip(0., 4., settings))
        self.assertIn(('variable', 0, 'w'), keys)
        self.assertIn(('parked', 1, 'w'), keys)
        self.assertFalse([key for key in keys if key[0] == 'transform_variable'])
        for index in (0, 1):
            with self.assertRaisesRegex(ValueError, 'w is a variable'):
                validate(replace(settings, shapes=tuple(replace(s, transform_variables=(Variable('w'),)) if i == index else s
                                                        for i, s in enumerate(settings.shapes))))


class FileTests(unittest.TestCase):
    def shape(self):
        return Shape(transforms=(rotate_by('a*20 + b'),), path_trace=Param(1., '0.5 + 0.4*a'),
                     transform_variables=(Variable('a', 1., 0., 2., points=LANE), Variable('b', 0., -5., 5., '2*sin(t)')))

    def test_round_trip(self):
        settings = Settings(shapes=(self.shape(),))
        document = scene_document(settings, 'drawing', '')
        self.assertEqual(document['version'], SCENE_VERSION)
        self.assertEqual(document['shapes'][0]['transform_variables'],
                         [dict(name='a', value=1., low=0., high=2., points=[list(p) for p in LANE]),
                          dict(name='b', value=0., low=-5., high=5., expression='2*sin(t)')])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'variables.scene.json'; atomic_json(path, document)
            self.assertEqual(read_scene(path)[0].shapes, settings.shapes)
        project = Project(clips=(Clip(0., 4., settings),))
        self.assertEqual(project_from_document(json.loads(json.dumps(project_document(project)))).clips[0].settings.shapes,
                         settings.shapes)

    def test_version_gate_and_invalid_documents(self):
        document = json.loads(json.dumps(scene_document(Settings(shapes=(self.shape(),)), 'drawing', '')))
        with self.assertRaisesRegex(ValueError, 'scene version 24'):
            scene_from_document(dict(document, version=23))
        item = document['shapes'][0]
        for bad in ([], 'a', [dict(name='a')], [dict(name='a', value=1., low=0., high=2., colour=1)],
                    [dict(name='x', value=1., low=0., high=2.)]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                scene_from_document(dict(document, shapes=[dict(item, transform_variables=bad)]))

    def test_older_scenes_open_unchanged(self):
        older = Settings(shapes=(Shape(transforms=(rotate_by('30*sin(t)'),), path_start=Param(0., '0.1*t')),
                                 Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot(variables=(Variable('a', points=LANE),)))))
        document = json.loads(json.dumps(scene_document(older, 'drawing', '')))
        for version in (21, 22, 23):
            with self.subTest(version=version):
                settings = scene_from_document(dict(document, version=version))[0]
                self.assertEqual(settings.shapes, older.shapes)
                self.assertTrue(all(s.transform_variables == () for s in settings.shapes))
                self.assertEqual(scene_document(settings, 'drawing', ''), dict(document, version=SCENE_VERSION))


if __name__ == '__main__':
    unittest.main()
