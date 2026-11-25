import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np
from phosphor.engine import automation

from phosphor.storage.project_files import PROJECT_VERSION
from phosphor.engine.automation import gain_at, validate_points, read_points, points_document, MAX_POINTS
from phosphor.engine.transforms import (Param, new_transform, apply_stack, validate_transform,
                                        validate_path, path_window)
from phosphor.engine.function_plot import (Variable, FunctionPlot, CompiledPlot, compiled_plot,
                                          validate_variable, PARAMETRIC_OBJECT)
from phosphor.engine.signal_engine import Shape, Settings, render
from phosphor.engine.composition import Clip, Note, Project, clip_settings, render_song
from phosphor.storage.app_files import scene_document, scene_from_document
from phosphor.storage.project_files import project_document, project_from_document
from phosphor.storage.project_files import PROJECT_VERSION
from phosphor.storage.app_files import SCENE_VERSION


RAMP = ((0., 0., 'linear'), (4., 1., 'linear'))
CLOCK = (.5, 0.)


def rotate(param):
    return new_transform('Rotate').with_param('z', param)


class GainTests(unittest.TestCase):
    def test_lane_arrays_are_reused_read_only_and_bounded(self):
        points = ((0, -1, 'curve', .12, -.7), (2, .5, 'hold'), (3, -.4, 'linear'), (4, 1, 'hold'))
        times = np.linspace(-1., 5., 131)
        with patch.object(automation, '_lane_arrays', automation._build_arrays):
            expected = gain_at(points, CLOCK, times)
        with patch.object(automation, '_build_arrays', wraps=automation._build_arrays) as build:
            for _ in range(2):
                self.assertTrue(np.array_equal(gain_at(points, CLOCK, times), expected))
            build.assert_called_once()
        self.assertTrue(all(not arr.flags.writeable for arr in automation._lane_arrays(points)))
        for i in range(4097): automation._lane_arrays(((i, 0., 'hold'),))
        self.assertEqual(len(automation._lanes), 4096)
        self.assertNotIn(id(points), automation._lanes)

    def test_curve_gain_and_point_documents(self):
        b = .6
        points = ((0., 0., 'curve', b, b), (4., 0., 'hold'))
        self.assertEqual(read_points(points_document(points), 'curve'), points)
        self.assertEqual(read_points(points_document(RAMP), 'linear'), RAMP)
        np.testing.assert_allclose(gain_at(points, (1., 0.), [-1., 0., 2., 4., 5.]),
                                   [0., 0., .75*b, 0., 0.], atol=1e-15)
        for point in ((0., 0., 'curve'), (0., 0., 'linear', 0., 0.),
                      (0., 0., 'curve', 1.01, 0.), (0., 0., 'curve', 0., -1.01),
                      (0., 0., 'curve', float('nan'), 0.), (0., 0., 'curve', True, 0.)):
            with self.subTest(point=point), self.assertRaises(ValueError):
                read_points([list(point)], 'bad curve')

    def test_curve_scene_project_and_clipboard_round_trips(self):
        from phosphor.engine.lane_access import get_points, set_points
        from phosphor.engine.clipboard import copy_nodes, paste_nodes, to_text, from_text
        points = ((0., 0., 'curve', .7, -.3), (4., .2, 'linear'))
        shape = Shape(lanes=(('position_x', points),),
                      transforms=(rotate(Param(10., points=points)),))
        settings = Settings(shapes=(shape,))
        document = scene_document(settings, 'drawing', '')
        self.assertEqual(document['version'], SCENE_VERSION)
        self.assertEqual(scene_from_document(json.loads(json.dumps(document)))[0], settings)
        clip = Clip(0., 8., settings)
        key = ('transform', 0, 0, 'z')
        self.assertEqual(get_points(set_points(clip, key, points), key), points)
        project = Project(clips=(clip,))
        self.assertEqual(project_from_document(json.loads(json.dumps(project_document(project)))), project)
        payload = from_text(to_text(copy_nodes(points, (0, 1))))
        pasted, _ = paste_nodes((), payload, 2., 8.)
        self.assertEqual(pasted, tuple((p[0]+2., *p[1:]) for p in points))
        plain = scene_document(Settings(shapes=(Shape(),)), 'drawing', '')
        from phosphor.storage.app_files import POSE_FIELDS
        from phosphor.engine.transforms import resting_tilt_step
        for version in (11, 12, 13, 14):
            old = copy.deepcopy(dict(plain, version=version))
            if version == 11:
                old['shapes'][0].update({name: 0. for name in POSE_FIELDS})
            self.assertEqual(scene_from_document(old)[0].shapes, (Shape(transforms=(resting_tilt_step(),)),))
        equation = replace(shape, solid=PARAMETRIC_OBJECT,
                           plot=FunctionPlot('a*s', 's', (Variable('a', .3, points=points),)))
        settings = Settings(shapes=(equation,))
        self.assertEqual(scene_from_document(json.loads(json.dumps(scene_document(settings, 'drawing', ''))))[0], settings)

    def test_jump_pairs(self):
        for beat in (0., 2., 4.):
            points = tuple(p for p in ((0., 0., 'linear'),) if beat > 0)
            points += ((beat, 1., 'linear'), (beat, -.5, 'linear'))
            if beat < 4:
                points += ((4., 0., 'hold'),)
            validate_points(points, 'lane')
            with np.errstate(all='raise'):
                self.assertAlmostEqual(gain_at(points, (1., 0.), beat - 1e-6), 1., places=5)
                self.assertEqual(gain_at(points, (1., 0.), beat), -.5)
        hold = ((0., .2, 'hold'), (2., 1., 'linear'), (2., -.5, 'hold'))
        np.testing.assert_array_equal(gain_at(hold, (1., 0.), [1.999999, 2., 3.]), [.2, -.5, -.5])

    def test_paste_jump_pair(self):
        from phosphor.engine.clipboard import copy_nodes, paste_nodes
        points = ((0., -.5, 'linear'), (0., 1., 'hold'), (2., .5, 'linear'))
        pasted, indices = paste_nodes((), copy_nodes(points, range(3)), 1., 4.)
        self.assertEqual(pasted, tuple((b + 1., g, s) for b, g, s in points))
        self.assertEqual(indices, (0, 1, 2))
        validate_points(pasted, 'lane')

    def test_empty_unbaked_and_scalar_vector(self):
        self.assertIsInstance(gain_at((), (), 1.), float)
        self.assertEqual(gain_at((), CLOCK, 1.), 1.)
        np.testing.assert_array_equal(gain_at((), CLOCK, [0, 1, 2]), [1, 1, 1])
        np.testing.assert_array_equal(gain_at(RAMP, (), [-1, 1, 20]), [0, 0, 0])
        self.assertEqual(gain_at(RAMP, (), 20), 0.)

    def test_interpolation_steps_boundaries_tempo_and_offset(self):
        times = np.array([[-1., 0., .5], [1., 2., 3.]])
        expected = [[0, 0, .25], [.5, 1, 1]]
        np.testing.assert_allclose(gain_at(RAMP, CLOCK, times), expected)
        np.testing.assert_allclose(gain_at(RAMP, (.5, 3.), times + 3.), expected)
        self.assertEqual(gain_at(RAMP, CLOCK, 1.), .5)  # Beat 2 at 120 BPM.
        hold = ((1., -1., 'hold'), (3., 2., 'linear'), (5., 0., 'hold'))
        np.testing.assert_allclose(gain_at(hold, (1., 0.), [-2, 1, 2.99, 3, 4, 5, 8]), [-1, -1, -1, 2, 1, 0, 0])
        self.assertEqual(gain_at(((2., -.5, 'linear'),), CLOCK, 99), -.5)

    def test_invalid_points_name_the_lane(self):
        bad = [list(RAMP), ((0., 1.),), ((0., 1., 'linear', 4),),
               ((2., 1., 'linear'), (1., 1., 'hold')), ((1., 1., 'hold'),)*3,
               ((-1., 1., 'linear'),), ((100001., 1., 'linear'),),
               ((0., 1.1, 'linear'),), ((0., -1.1, 'linear'),), ((0., 1., 'ease'),),
               ((float('nan'), 1., 'linear'),), ((0., float('inf'), 'linear'),),
               ((True, 1., 'linear'),), ((0., False, 'hold'),), ((0., '1', 'linear'),),
               tuple((float(i), 1., 'hold') for i in range(MAX_POINTS + 1))]
        for points in bad:
            with self.subTest(points=points), self.assertRaisesRegex(ValueError, 'lane'):
                validate_points(points, 'lane')
        validate_points(((0., -1., 'hold'), (100000., 1., 'linear')), 'lane')
        # Files from before the ±1 range open clamped instead of failing.
        self.assertEqual(read_points([[0., -2., 'hold'], [4., 1.7, 'linear'], [8., .5, 'linear']], 'lane'),
                         ((0., -1., 'hold'), (4., 1., 'linear'), (8., .5, 'linear')))
        self.assertEqual(read_points(points_document(RAMP), 'lane'), RAMP)
        for value in (RAMP, [1], [[0, 1]]):
            with self.assertRaisesRegex(ValueError, 'lane'): read_points(value, 'lane')


class EvaluationTests(unittest.TestCase):
    def test_transform_gain_multiplies_static_expression_and_clips(self):
        step = rotate(Param(90., points=RAMP, clock=CLOCK))
        np.testing.assert_allclose(apply_stack([[1., 0., 0.]], (step,), 1.)[0],
                                   [[2**-.5, 2**-.5, 0.]], atol=1e-12)
        move = new_transform('Translate').with_param('x', Param(0., '0.1*t', RAMP, CLOCK))
        self.assertAlmostEqual(apply_stack([[0., 0., 0.]], (move,), 1.)[0][0, 0], .05)
        scale = new_transform('Scale').with_param('x', Param(8., points=((0., 2., 'hold'),)))
        self.assertEqual(apply_stack([[1., 0., 0.]], (scale,), 1.)[0][0, 0], 10.)
        angle = rotate(Param(180., points=((0., 2., 'hold'),)))
        np.testing.assert_allclose(apply_stack([[1., 0., 0.]], (angle,), 1.)[0], [[1., 0., 0.]], atol=1e-12)
        for kind, name in (('Mirror', 'x'),):
            step = new_transform(kind).with_param(name, Param(1., points=RAMP))
            with self.assertRaises(ValueError): validate_transform(step)
        with self.assertRaises(ValueError): validate_transform(rotate(Param(90., points=((1., 3., 'hold'),))))

    def test_path_gain_before_wrap_and_trace_clip(self):
        start = Param(.75, points=((0., 2., 'hold'),))
        trace = Param(.8, points=RAMP, clock=CLOCK)
        self.assertEqual(path_window(start, trace, 1.), (.5, .4))
        self.assertEqual(path_window(Param(0., '0.75*t', ((0., 2., 'hold'),)),
                                     Param(.8, points=((0., 2., 'hold'),)), 1.), (.5, 1.))
        with self.assertRaises(ValueError): validate_path(Param(0.), Param(1., points=((0., 3., 'hold'),)))

    def test_equation_variables_gain_per_sample_before_equations(self):
        times = np.array([0., .5, 1., 2., 3.])
        for expression in ('', '2*t'):
            variable = Variable('a', 2., -3., 3., expression, RAMP, CLOCK)
            plot = FunctionPlot('a', 'a*s', (variable,))
            expected = np.clip((2*times if expression else np.full(5, 2.)) * np.array([0., .25, .5, 1., 1.]), -3., 3.)
            positions = np.linspace(0, 1, 5)
            np.testing.assert_allclose(CompiledPlot(plot)(times, positions), np.column_stack((expected, expected*positions, np.zeros(5))))
            self.assertIs(compiled_plot(plot), compiled_plot(plot))
        constant = CompiledPlot(FunctionPlot('a', 'a', (Variable('a', 2.),)))
        self.assertEqual(constant.namespace['a'], 2.)
        self.assertEqual(constant.varying, ())
        with self.assertRaises(ValueError): validate_variable(Variable('a', points=((0., 4., 'hold'),)))

    def test_clip_clock_baking_and_identity(self):
        plain = Shape()
        animated = Shape(transforms=(rotate(Param(90., points=RAMP)),), path_start=Param(.5, points=RAMP),
                         path_trace=Param(1., points=RAMP))
        equation = Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot(variables=(Variable('a', points=RAMP),)))
        settings = Settings(shapes=(plain, animated, equation))
        for local, offset in ((True, 0.), (False, 3.)):
            baked = clip_settings(Clip(6., 8., settings, local_time=local), Settings(), 120.)
            self.assertIs(baked.shapes[0], plain)
            shape = baked.shapes[1]
            for param in (shape.transforms[0].param('z'), shape.path_start, shape.path_trace,
                          baked.shapes[2].plot.variables[0]):
                self.assertEqual(param.clock, (.5, offset))
            self.assertEqual(shape.transforms[0].param('x').clock, ())
            self.assertEqual(gain_at(shape.path_trace.points, shape.path_trace.clock, offset+1.), .5)
        self.assertEqual(animated.path_trace.clock, ())

    def test_song_held_gain_changes_after_beat_four(self):
        lane = ((0., 1., 'hold'), (4., 0., 'linear'))
        automated = Settings(shapes=(Shape(transforms=(rotate(Param(90., points=lane)),)),))
        plain = replace(automated, shapes=(Shape(transforms=(rotate(Param(90.)),)),))
        zero = replace(automated, shapes=(Shape(transforms=(rotate(Param(0.)),)),))
        for local in (True, False):
            def project(settings):
                return Project(clips=(Clip(4., 8., settings, local_time=local),), notes=(Note(0., 16., 60),))
            for begin, expected in ((2.5, plain), (4.5, zero)):
                times = begin + np.arange(1000)/48000.
                actual = render_song(times, project(automated), Settings())
                reference = render_song(times, project(expected), Settings())
                self.assertGreater(np.max(np.abs(reference)), .01)
                np.testing.assert_allclose(actual, reference, atol=1e-12)

    def test_sandbox_holds_first_gain(self):
        lane = ((0., .5, 'linear'), (4., 1., 'hold'))
        shape = Shape(transforms=(rotate(Param(90., points=lane)),), path_start=Param(.4, points=lane),
                      path_trace=Param(1., points=lane))
        plain = replace(shape, transforms=(rotate(Param(45.)),), path_start=Param(.2), path_trace=Param(.5))
        times = .5 + np.arange(3000)/48000.
        settings = Settings(source='Single tone', shapes=(shape,))
        for actual, expected in zip(render(times, settings), render(times, replace(settings, shapes=(plain,)))):
            np.testing.assert_allclose(actual, expected, atol=1e-12)


class FileTests(unittest.TestCase):
    def rich_settings(self):
        return Settings(shapes=(Shape(transforms=(rotate(Param(90., '30*t', RAMP)),), path_trace=Param(.8, points=RAMP)),
                                Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot('a*s', 'a', (Variable('a', 2., points=RAMP),)))))

    def test_scene_round_trip_and_runtime_clock_omission(self):
        settings = self.rich_settings()
        document = scene_document(settings, 'drawing', '')
        self.assertEqual(document['version'], SCENE_VERSION)
        self.assertEqual(scene_from_document(json.loads(json.dumps(document)))[0], settings)
        baked = clip_settings(Clip(4., 8., settings, local_time=False), Settings(), 120.)
        self.assertNotIn('clock', json.dumps(scene_document(baked, 'drawing', '')))
        self.assertEqual(scene_document(baked, 'drawing', ''), document)
        self.assertNotEqual(baked.shapes, settings.shapes)  # Clock participates in equality/cache keys.
        for shape in baked.shapes: hash(shape)

    def test_old_versions_gate_every_lane_including_empty_points(self):
        plain = scene_document(Settings(shapes=(Shape(),)), 'drawing', '')
        from phosphor.engine.transforms import resting_tilt_step
        self.assertEqual(scene_from_document(dict(plain, version=12))[0].shapes, (Shape(transforms=(resting_tilt_step(),)),))
        rich = scene_document(self.rich_settings(), 'drawing', '')
        for version in (11, 12):
            for field in ('transform', 'path_start', 'path_trace', 'variable'):
                old = copy.deepcopy(plain)
                old['version'] = version
                if field == 'transform':
                    old['shapes'][0]['transforms'] = [rotate(Param(90., points=RAMP)).document()]
                elif field == 'variable': old['shapes'] = [rich['shapes'][1]]
                else: old['shapes'][0][field] = dict(value=.5, points=[])
                with self.subTest(version=version, field=field), self.assertRaises(ValueError): scene_from_document(old)

    def test_unautomated_shapes_keep_the_existing_document_form(self):
        shape = Shape(transforms=(rotate(Param(90., '30*t')),), path_trace=Param(.5))
        value = scene_document(Settings(shapes=(shape,)), 'drawing', '')['shapes'][0]
        self.assertEqual(value, dict(solid='Cube', position_x=0., position_y=0., shape_size=1.,
                                    transforms=[dict(kind='Rotate', params=dict(x=0., y=0., z=dict(value=90., expression='30*t')))],
                                    path_trace=.5))
        self.assertNotIn('points', json.dumps(value))
        self.assertEqual(Param.read(dict(value=2., expression=''), 'param'), Param(2.))
        for invalid in (dict(value=2.), dict(value=2., clock=[.5, 0]), dict(points=[])):
            with self.assertRaises(ValueError): Param.read(invalid, 'param')

    def test_project_versions(self):
        project = Project(clips=(Clip(0., 8., self.rich_settings()),))
        document = project_document(project)
        self.assertEqual(document['version'], PROJECT_VERSION)
        self.assertEqual(project_from_document(json.loads(json.dumps(document))), project)
        old_project = Project(clips=(Clip(0., 8., Settings(shapes=(Shape(),))),))
        old = project_document(old_project)
        old['version'] = 1
        del old['chords']
        del old['velocity']
        old['clips'][0]['scene']['version'] = 12
        from phosphor.engine.transforms import resting_tilt_step
        expected = replace(old_project, clips=(replace(old_project.clips[0],
            settings=Settings(shapes=(Shape(transforms=(resting_tilt_step(),)),))),))
        self.assertEqual(project_from_document(old), expected)


class ShapeLaneTests(unittest.TestCase):
    def test_sandbox_and_baked_size(self):
        from phosphor.engine.signal_engine import posed_shapes
        lane = ((0., .5, 'hold'), (4., 1., 'hold'))
        shape = Shape(position_x=.4, lanes=(('position_x', lane),))
        times = .8 + np.arange(500)/48000.
        settings = Settings(source='Single tone', shapes=(shape,))
        expected = replace(settings, shapes=(Shape(position_x=.2),))
        for a, b in zip(render(times, settings), render(times, expected)):
            np.testing.assert_array_equal(a, b)
        # A held midpoint gives a constant comparison block despite pose quantisation.
        ramp = ((0., .5, 'linear'), (2., .75, 'hold'), (3., .75, 'linear'), (4., 1., 'hold'))
        shape = Shape(lanes=(('shape_size', ramp),))
        self.assertEqual(posed_shapes((replace(shape, lane_clock=CLOCK),), .5)[0].shape_size, .625)
        for local, offset in ((True, 0.), (False, 3.)):
            baked = clip_settings(Clip(6., 8., replace(settings, shapes=(shape,)), local_time=local), Settings(), 120.)
            self.assertEqual(baked.shapes[0].lane_clock, (.5, offset))
            block = offset + 1.1 + np.arange(500)/48000.
            for a, b in zip(render(block, baked), render(block, replace(baked, shapes=(Shape(shape_size=.75),)))):
                np.testing.assert_allclose(a, b, atol=1e-12)
        for gain, expected_size in ((0., .001), (-.0001, -.001), (-1., -1.)):
            zero = Shape(lanes=(('shape_size', ((0., gain, 'hold'),)),))
            self.assertEqual(posed_shapes((zero,), 0.)[0].shape_size, expected_size)
        plain = Shape()
        self.assertIs(posed_shapes((plain,), 1.)[0], plain)
        self.assertIs(clip_settings(Clip(0., 8., Settings(shapes=(plain,))), Settings(), 120.).shapes[0], plain)

    def test_helpers_and_validation(self):
        from phosphor.engine.automation import lane_points, with_lane
        from phosphor.engine.signal_engine import validate
        lanes = with_lane(with_lane((), 'shape_size', RAMP), 'position_y', RAMP)
        self.assertEqual(lane_points(lanes, 'position_y'), RAMP)
        self.assertEqual(lane_points(lanes, 'position_x'), ())
        self.assertEqual(with_lane(lanes, 'shape_size', ()), (('position_y', RAMP),))
        for bad in ((('unknown', RAMP),), (('position_x', RAMP),)*2,
                    (('position_x', ()),), tuple(reversed(lanes)), [], (('position_x',),)):
            with self.subTest(lanes=bad), self.assertRaises(ValueError):
                validate(Settings(shapes=(Shape(lanes=bad),)))
        from phosphor.engine.custom_shape import Drawing, CUSTOM_OBJECT
        guide = Shape(solid=CUSTOM_OBJECT, uid='guide',
                      custom=Drawing('Guide', ((0, 0, 0), (1, 2, 0)), ((0, 1),), motion_path=True),
                      lanes=(('position_x', RAMP),))
        with self.assertRaisesRegex(ValueError, 'Motion guides'):
            validate(Settings(shapes=(guide,)))


class ClipLaneTests(unittest.TestCase):
    def test_output_gain_and_ranges(self):
        settings = Settings(shapes=(Shape(),))
        lane = ((0., 1., 'hold'), (4., .5, 'hold'))
        for local in (True, False):
            clip = Clip(4., 8., settings, local_time=local)
            for begin, factor in ((2.5, 1.), (4.5, .5)):
                times = begin + np.arange(500)/48000.
                def song(c): return render_song(times, Project(clips=(c,), notes=(Note(0., 16., 60),)), Settings())
                reference = song(clip)
                self.assertGreater(np.max(np.abs(reference)), .01)
                np.testing.assert_allclose(song(replace(clip, lanes=(('drawing_gain', lane),))), reference*factor, atol=1e-12)
                capped = replace(clip, lanes=(('drawing_gain', ((0., 2., 'hold'),)),))
                np.testing.assert_allclose(song(capped), reference*(.95/.55), atol=1e-12)

    def test_blend_matches_raw_render(self):
        from phosphor.engine.composition import NoteSource, validate_project
        settings = Settings(shapes=(Shape(),), music_gain=.4)
        for mode in ('mixed', 'drawing', 'music'):
            clip = Clip(4., 8., settings, mode=mode, local_time=False, lanes=(('music_gain', ((0., 0., 'linear'), (4., 2., 'hold'))),))
            project = Project(clips=(clip,), notes=(Note(0., 16., 60),))
            times = 2.5 + np.arange(500)/48000.
            drawing, music, _ = render(times, clip_settings(clip, Settings(), 120.), NoteSource(project))
            mg = np.clip(.4*(times-2.), 0., 1.)[:, None]
            expected = .55*((1-mg)*drawing+mg*music) if mode == 'mixed' else .55*(drawing if mode == 'drawing' else music)
            np.testing.assert_allclose(render_song(times, project, Settings()), expected, atol=1e-12)
        for dg_gain, mg_gain, expected_dg, expected_mg in ((-2., -2., 0., 0.), (2., 2., .95, .8)):
            capped = replace(clip, mode='mixed', lanes=(('drawing_gain', ((0., dg_gain, 'hold'),)),
                                                       ('music_gain', ((0., mg_gain, 'hold'),))))
            np.testing.assert_allclose(render_song(times, replace(project, clips=(capped,)), Settings()),
                                       expected_dg*((1-expected_mg)*drawing+expected_mg*music), atol=1e-12)
        for bad in ((('shape_size', RAMP),), (('drawing_gain', ()),), (('music_gain', RAMP),)*2):
            with self.assertRaises(ValueError): validate_project(Project(clips=(replace(clip, lanes=bad),)))


class LaneFileTests(unittest.TestCase):
    def test_scene_and_project_round_trips_and_gates(self):
        settings = Settings(shapes=(Shape(lanes=(('position_x', RAMP), ('shape_size', RAMP))),))
        document = scene_document(settings, 'drawing', '')
        self.assertEqual(document['version'], SCENE_VERSION)
        self.assertEqual(scene_from_document(json.loads(json.dumps(document)))[0], settings)
        baked = clip_settings(Clip(0., 8., settings), Settings(), 120.)
        self.assertEqual(scene_document(baked, 'drawing', ''), document)
        self.assertNotIn('lane_clock', json.dumps(document))
        for version in (11, 12):
            with self.assertRaises(ValueError): scene_from_document(dict(document, version=version))
        project = Project(clips=(Clip(0., 8., settings, lanes=(('drawing_gain', RAMP),)),))
        doc = project_document(project)
        self.assertEqual(doc['version'], PROJECT_VERSION)
        self.assertEqual(project_from_document(json.loads(json.dumps(doc))), project)
        legacy = dict(doc, version=1); del legacy['chords']; del legacy['velocity']
        with self.assertRaises(ValueError): project_from_document(legacy)
        plain = project_document(Project(clips=(Clip(0., 8., Settings(shapes=(Shape(),))),)))
        self.assertNotIn('lanes', json.dumps(plain))


if __name__ == '__main__':
    unittest.main()
