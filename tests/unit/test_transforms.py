import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from phosphor.engine.function_plot import FunctionPlot, PARAMETRIC_OBJECT
from phosphor.engine.signal_engine import Settings, Shape, render, validate, planned_path, projected_solid, posed_object
from phosphor.engine.transforms import (KINDS, ROTATE, SPIN, TRANSLATE, SCALE, SHEAR, MIRROR, MOTION_PATH, PERSPECTIVE, NOISE,
                                        SCENE_25_TRANSFORMS, Param, Transform, NoiseOptions, new_transform, apply_stack, migrated_pose, route_positions,
                                        validate_stack, resting_tilt_step, stack_varies_with_time, GLITCH, WAVE)
from phosphor.storage.app_files import scene_document, read_scene, atomic_json
from phosphor.storage.app_files import SCENE_VERSION


def stack(*items): return tuple(items)
def static(kind, *values): return Transform(kind, tuple(Param(float(v)) for v in values))


class StackTests(unittest.TestCase):
    def test_outline_clock_for_constant_transforms(self):
        spin = static(SPIN, 0, 0, .5)
        self.assertTrue(stack_varies_with_time((spin,)))
        self.assertFalse(stack_varies_with_time((static(ROTATE, 0, 0, 45),)))
        self.assertFalse(stack_varies_with_time((replace(spin, enabled=False),)))
        for kind, field in ((WAVE, 'speed'), (NOISE, 'speed'), (GLITCH, 'rate')):
            step = new_transform(kind)
            self.assertTrue(stack_varies_with_time((step,)))
            self.assertFalse(stack_varies_with_time((step.with_param(field, Param(0)),)))

    points = np.array([[1., 0., 0.], [0., 1., 0.], [0., 0., 1.], [.3, -.2, .5]])

    def test_every_kind_has_neutral_defaults_except_motion_perspective_and_noise(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                posed, noise = apply_stack(self.points, (new_transform(kind),), 0.)
                if kind in (MOTION_PATH, PERSPECTIVE) or kind in SCENE_25_TRANSFORMS: continue
                np.testing.assert_allclose(posed, self.points, atol=1e-12)
                self.assertEqual(noise is not None, kind == NOISE)

    def test_steps_match_their_definitions_and_run_in_order(self):
        rotate = static(ROTATE, 0, 0, 90)
        np.testing.assert_allclose(apply_stack([[1, 0, 0]], (rotate,), 0)[0], [[0, 1, 0]], atol=1e-12)
        np.testing.assert_allclose(apply_stack([[1, 0, 0]], (static(SPIN, 0, 0, 1),), .25)[0], [[0, 1, 0]], atol=1e-12)
        np.testing.assert_allclose(apply_stack([[1, 2, 3]], (static(TRANSLATE, .5, -1, 2),), 0)[0], [[1.5, 1, 5]])
        np.testing.assert_allclose(apply_stack([[1, 2, 3]], (static(SCALE, 2, -1, .5),), 0)[0], [[2, -2, 1.5]])
        np.testing.assert_allclose(apply_stack([[1, 2, 3]], (static(SHEAR, 1, 0, 0, 0, 0, 0),), 0)[0], [[3, 2, 3]])
        np.testing.assert_allclose(apply_stack([[1, 2, 3]], (static(MIRROR, 1, 0, 1),), 0)[0], [[-1, 2, -3]])
        # A point 1 unit toward a camera 2 units away doubles in X/Y.
        np.testing.assert_allclose(apply_stack([[.5, .5, 1]], (static(PERSPECTIVE, 2),), 0)[0], [[1, 1, 1]])
        move = static(TRANSLATE, 1, 0, 0)
        np.testing.assert_allclose(apply_stack([[1, 0, 0]], (move, rotate), 0)[0], [[0, 2, 0]], atol=1e-12)
        np.testing.assert_allclose(apply_stack([[1, 0, 0]], (rotate, move), 0)[0], [[1, 1, 0]], atol=1e-12)
        np.testing.assert_allclose(apply_stack([[1, 0, 0]], (replace(rotate, enabled=False), move), 0)[0], [[2, 0, 0]])

    def test_time_varying_numbers_see_t_x_y_z_and_s(self):
        twist = Transform(ROTATE, (Param(0), Param(0), Param(0, '90*y')))
        posed = apply_stack([[1, 0, 0], [1, 1, 0]], (twist,), 0)[0]
        np.testing.assert_allclose(posed, [[1, 0, 0], [-1, 1, 0]], atol=1e-12)  # Only the point at y=1 turns.
        moving = Transform(TRANSLATE, (Param(0, 't'), Param(0, 's'), Param(0)))
        np.testing.assert_allclose(apply_stack([[0, 0, 0], [0, 0, 0]], (moving,), .5, np.array([0, .25]))[0], [[.5, 0, 0], [.5, .25, 0]])
        # Results clip to the field's range except angles.
        big = Transform(SCALE, (Param(1, '1e9'), Param(1), Param(1)))
        self.assertEqual(apply_stack([[1, 0, 0]], (big,), 0)[0][0, 0], 10.)
        spin = Transform(ROTATE, (Param(0), Param(0), Param(0, '450')))
        np.testing.assert_allclose(apply_stack([[1, 0, 0]], (spin,), 0)[0], [[0, 1, 0]], atol=1e-12)

    def test_validation_names_the_field_and_rejects_bad_values(self):
        validate_stack((new_transform(MOTION_PATH), new_transform(NOISE)))
        cases = [Transform(ROTATE, (Param(200), Param(0), Param(0))), Transform(ROTATE, (Param(0, 'foo'), Param(0), Param(0))),
                 Transform(ROTATE, (Param(0, 'import os'), Param(0), Param(0))), Transform(MIRROR, (Param(.5), Param(0), Param(0))),
                 Transform(NOISE, (Param(.1), Param(1, 't'))), Transform('Twirl'), (new_transform(ROTATE),)*17]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                validate_stack(case if isinstance(case, tuple) else (case,))
        with self.assertRaisesRegex(ValueError, 'Rotate X'): validate_stack((Transform(ROTATE, (Param(0, 'foo'), Param(0), Param(0))),))

    def test_route_positions_are_deterministic(self):
        np.testing.assert_allclose(route_positions([[0, 0], [1, 0], [1, 1]], [0, 1, 2, 0]), [0, 1/(2+2**.5), 2/(2+2**.5)])


class EngineTests(unittest.TestCase):
    times = np.arange(9600) / 48000

    def render(self, *shapes, **settings):
        return render(self.times, Settings(source='Single tone', shapes=tuple(shapes), **settings))[0]

    def test_no_stack_is_unchanged_and_neutral_stacks_are_identical(self):
        for solid in ('Cube', 'Tilting circle', 'Text'):
            plain = Shape(solid=solid, rotation_y=20)
            neutral = replace(plain, transforms=(static(ROTATE, 0, 0, 0), static(SCALE, 1, 1, 1)))
            np.testing.assert_allclose(self.render(neutral), self.render(plain), atol=1e-12)
        np.testing.assert_array_equal(posed_object(.3, Shape(spin_y=1.), None)[0], projected_solid(.3, Shape(spin_y=1.)))

    def test_old_pose_fields_migrate_exactly(self):
        for pose in (dict(rotation_x=30, rotation_z=-45), dict(spin_y=.5, spin_z=-1), dict(rotation_x=30, rotation_y=-20, rotation_z=10, spin_x=.25, spin_z=.5)):
            with self.subTest(pose=pose):
                np.testing.assert_allclose(self.render(Shape(transforms=migrated_pose(pose))), self.render(Shape(**pose)), atol=1e-12)
        self.assertEqual(migrated_pose({}), ())

    def test_parametric_objects_take_the_stack_per_sample(self):
        circle = Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot())
        plain = self.render(circle, travel_mode='Faster travel', travel_speed=float('inf'))
        moved = self.render(replace(circle, transforms=(static(TRANSLATE, .5, 0, 0),)), travel_mode='Faster travel', travel_speed=float('inf'))
        np.testing.assert_allclose(moved, plain + [.5, 0], atol=1e-9)

    def test_noise_is_per_sample_and_chunk_independent(self):
        noisy = Shape(transforms=(new_transform(NOISE),))
        base, full = self.render(Shape()), self.render(noisy)
        difference = full - base
        self.assertGreater(np.abs(difference).max(), .005)
        self.assertLess(np.abs(difference).max(), .1)  # Gaussian noise has no hard amplitude bound.
        parts = np.concatenate([render(self.times[i:i+700], Settings(source='Single tone', shapes=(noisy,)))[0] for i in range(0, len(self.times), 700)])
        np.testing.assert_array_equal(parts, full)
        pink = Shape(transforms=(replace(new_transform(NOISE), noise=NoiseOptions('Pink')),))
        self.assertFalse(np.allclose(self.render(pink), full))

    def test_time_varying_steps_update_each_pose_frame(self):
        orbit = Shape(transforms=(new_transform(MOTION_PATH),))
        first, later = planned_path(0, 60, (orbit,), 'No changes', 8), planned_path(30, 60, (orbit,), 'No changes', 8)
        self.assertFalse(np.allclose(first[0], later[0]))


class PathWindowTests(unittest.TestCase):
    times = np.arange(9600) / 48000
    square = (np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float), np.array([0, 1, 2, 3, 0]))

    def render(self, *shapes, **settings):
        return render(self.times, Settings(source='Single tone', shapes=tuple(shapes), **settings))[0]

    def test_trim_route_cuts_by_arc_length_wraps_and_reverses(self):
        from phosphor.engine.transforms import trim_route
        def piece(start, trace):
            vertices, route, *_ = trim_route(*self.square, (), start, trace)
            return np.round(vertices[route], 9).tolist()
        self.assertEqual(piece(.125, .25), [[.5, 0], [1, 0], [1, .5], [.5, 0]])
        self.assertEqual(piece(.9, .2), [[0, .4], [0, 0], [.4, 0], [0, .4]])  # Wraps past the first point.
        self.assertEqual(piece(.5, -.25), [[1, 1], [1, 0], [1, 1]])  # Backwards from Start.
        _, _, drawn, joins, _ = trim_route(*self.square, (), .125, .25)
        self.assertEqual(joins.tolist(), [[5, 4]])  # The return to the piece's start is travel.

    def test_defaults_are_unchanged_and_windows_change_the_drawing(self):
        from phosphor.engine.transforms import Param as P
        for solid in ('Cube', 'Text', 'Tilting circle'):
            shape = Shape(solid=solid)
            np.testing.assert_array_equal(self.render(replace(shape, path_start=P(0.), path_trace=P(1.))), self.render(shape))
            self.assertFalse(np.allclose(self.render(replace(shape, path_trace=P(.5))), self.render(shape)))
            self.assertFalse(np.allclose(self.render(replace(shape, path_trace=P(-1.))), self.render(shape)))
        circle = Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot())
        half = self.render(replace(circle, path_trace=P(.5)), travel_mode='Faster travel', travel_speed=float('inf'))
        self.assertGreaterEqual(half[:, 0].min(), -1e-9)  # x = sin(tau*s) for s in 0..0.5 only.
        np.testing.assert_allclose(self.render(replace(Shape(), path_trace=P(0.)), circle), self.render(circle))  # Hidden.

    def test_time_varying_windows_are_chunk_independent_and_validated(self):
        from phosphor.engine.transforms import Param as P
        moving = replace(Shape(solid='Text', text='HI'), path_start=P(0., '0.1*t'), path_trace=P(1., 'sin(t)'))
        full = self.render(moving)
        parts = np.concatenate([render(self.times[i:i+777], Settings(source='Single tone', shapes=(moving,)))[0] for i in range(0, len(self.times), 777)])
        np.testing.assert_array_equal(parts, full)
        for bad in (dict(path_trace=P(1.5)), dict(path_start=P(-.1)), dict(path_trace=P(1., 'y')), dict(path_start=P(0., 'bogus'))):
            with self.subTest(bad=bad), self.assertRaises(ValueError): validate(Settings(shapes=(replace(Shape(), **bad),)))


class FileTests(unittest.TestCase):
    def test_version_12_round_trip_and_version_11_migration(self):
        shape = Shape(solid='Cube', transforms=(Transform(ROTATE, (Param(10), Param(0, '30*sin(t)'), Param(0)), False), new_transform(NOISE)))
        settings = Settings(shapes=(shape, Shape()))
        document = scene_document(settings, 'drawing', '')
        self.assertEqual(document['version'], SCENE_VERSION)
        self.assertNotIn('transforms', document['shapes'][1]); self.assertNotIn('rotation_x', document['shapes'][1])
        self.assertEqual(document['shapes'][0]['transforms'][0], dict(kind='Rotate', enabled=False, params=dict(x=10, y=dict(value=0, expression='30*sin(t)'), z=0)))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'scene.json'; atomic_json(path, document)
            self.assertEqual(read_scene(path)[0].shapes, settings.shapes)
            old = dict(document, version=11); old['shapes'] = [dict(solid='Cube', position_x=0., position_y=0., shape_size=1., rotation_x=20., rotation_y=0., rotation_z=5., spin_z=.5, text='', uid='')]
            for item in old['shapes']: item.pop('text'); item.pop('uid')
            path.write_text(json.dumps(old))
            read = read_scene(path)[0].shapes[0]
            self.assertEqual((read.rotation_x, read.spin_z), (0., 0.))
            self.assertEqual([t.kind for t in read.transforms], [ROTATE, ROTATE])
            self.assertEqual(read.transforms[0], resting_tilt_step())
            self.assertEqual(read.transforms[1].params[2].expression, '5 + 180*t')
            windowed = Settings(shapes=(Shape(path_start=Param(.25), path_trace=Param(-.5, '0.5*sin(t)')),))
            document = scene_document(windowed, 'drawing', '')
            self.assertEqual((document['shapes'][0]['path_start'], document['shapes'][0]['path_trace']), (.25, dict(value=-.5, expression='0.5*sin(t)')))
            atomic_json(path, document); self.assertEqual(read_scene(path)[0].shapes, windowed.shapes)
            old['shapes'][0]['transforms'] = []; path.write_text(json.dumps(old))
            with self.assertRaises(ValueError): read_scene(path)


if __name__ == '__main__':
    unittest.main()
