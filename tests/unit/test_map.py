"""Transforms T4: the Map step (X′, Y′, Z′ formulas of the point before the step)."""
import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from phosphor.engine import lane_access
from phosphor.engine.composition import Clip
from phosphor.engine.model_object import MODEL_OBJECT, model_from_obj
from phosphor.engine.signal_engine import Settings, Shape, render, posed_object
from phosphor.engine.transforms import (MAP, MAP_PRESETS, ROTATE, MOTION_PATH, FIELDS, Param, Transform, new_transform,
                                        apply_stack, validate_stack, with_map_preset, is_identity, uses_position,
                                        route_positions)
from phosphor.storage.app_files import scene_document, read_scene, atomic_json
from tests.fixtures.dense_mesh import dense_mesh_obj
from phosphor.storage.app_files import SCENE_VERSION


def mapped(x='x', y='y', z='z'):
    return Transform(MAP, (Param(0., x), Param(0., y), Param(0., z)))


def run(points, *steps, t=0.):
    return apply_stack(points, tuple(steps), t)[0]


class MapStepTests(unittest.TestCase):
    rng = np.random.default_rng(22)
    points = rng.normal(size=(200, 3))

    def test_new_map_is_three_identity_formulas(self):
        step = new_transform(MAP)
        self.assertEqual([p.expression for p in step.params], ['x', 'y', 'z'])
        self.assertTrue(all(is_identity(f, p) for f, p in zip(FIELDS[MAP], step.params)))
        self.assertEqual([f.label for f in FIELDS[MAP]], ['X′', 'Y′', 'Z′'])
        validate_stack((step,))

    def test_default_map_is_bit_identical_to_no_map(self):
        # Even outside Translate's range: identity axes pass through untouched (no clip).
        wide = np.vstack((self.points, [[25., -40., 1e6]]))
        np.testing.assert_array_equal(run(wide, new_transform(MAP)), wide)
        np.testing.assert_array_equal(run(wide, mapped(' x ', 'y', 'z')), wide)
        tilt = Transform(ROTATE, (Param(20.), Param(0., '30*sin(t)'), Param(0.)))
        np.testing.assert_array_equal(run(wide, tilt, new_transform(MAP), t=.3), run(wide, tilt, t=.3))
        times = np.arange(4800) / 48000
        for solid in ('Cube', 'Tilting circle', 'Text'):
            with self.subTest(solid=solid):
                plain = Shape(solid=solid, transforms=(tilt,))
                with_map = replace(plain, transforms=(tilt, new_transform(MAP)))
                np.testing.assert_array_equal(render(times, Settings(shapes=(with_map,)))[0],
                                              render(times, Settings(shapes=(plain,)))[0])

    def test_one_identity_axis_still_passes_through_when_others_change(self):
        wide = np.array([[20., .5, -30.]])
        np.testing.assert_array_equal(run(wide, mapped(y='2*y')), [[20., 1., -30.]])

    def test_every_formula_reads_the_point_from_before_the_step(self):
        p = self.points
        swapped = run(p, mapped('y', 'x'))
        np.testing.assert_array_equal(swapped, p[:, [1, 0, 2]])
        # Y′ = x sees the old x, not X′'s result.
        chained = run(p, mapped('x + 1', 'x', 'x + y'))
        np.testing.assert_allclose(chained, np.column_stack((p[:, 0] + 1, p[:, 0], p[:, 0] + p[:, 1])), rtol=0, atol=0)

    def test_nan_and_infinity_become_zero_and_results_clip_to_translate_range(self):
        p = np.array([[0., -4., 1.], [2., 4., -1.], [-.5, 0., .2]])
        with np.errstate(all='ignore'):
            out = run(p, mapped('1/x', 'sqrt(y)', 'log(0*z)'))
        np.testing.assert_array_equal(out[:, 0], [0., .5, -2.])  # 1/0 = inf -> 0
        np.testing.assert_array_equal(out[:, 1], [0., 2., 0.])  # sqrt(-4) = NaN -> 0
        np.testing.assert_array_equal(out[:, 2], [0., 0., 0.])  # log(0) = -inf -> 0
        clipped = run(p, mapped('100*x', '-100*y', '3'))
        np.testing.assert_array_equal(clipped, [[0., 10., 3.], [10., -10., 3.], [-10., 0., 3.]])
        self.assertTrue(np.isfinite(run(p, mapped('1/x', '1/y', '1/z'))).all())

    def test_formulas_see_t_p_and_s(self):
        p = self.points[:5]; s = np.linspace(0, 1, 5)
        out = apply_stack(p, (mapped('t', 'p', 's'),), .5, s, tempo=90.)[0]
        np.testing.assert_allclose(out, np.column_stack((np.full(5, .5), np.full(5, .75), s)), rtol=0, atol=1e-15)

    def test_wave_and_twist_examples(self):
        p = self.points * .5; t = .3
        wave = run(p, with_map_preset(new_transform(MAP), 'Wave'), t=t)
        expected = p.copy(); expected[:, 0] = p[:, 0] + .2*np.sin(3*p[:, 1] + math.tau*t)
        np.testing.assert_allclose(wave, expected, rtol=0, atol=1e-15)
        twist = run(p, with_map_preset(new_transform(MAP), 'Twist'))
        np.testing.assert_allclose(np.hypot(twist[:, 0], twist[:, 2]), np.hypot(p[:, 0], p[:, 2]), rtol=1e-12)
        np.testing.assert_array_equal(twist[:, 1], p[:, 1])
        angle = np.arctan2(twist[:, 2], twist[:, 0]) - np.arctan2(p[:, 2], p[:, 0])
        np.testing.assert_allclose(np.angle(np.exp(1j*(angle - 2*p[:, 1]))), 0, atol=1e-9)

    def test_spherize_puts_every_dense_model_point_on_the_unit_sphere(self):
        model, _ = model_from_obj(dense_mesh_obj())
        vertices = np.array(model.vertices)
        sphere = run(vertices, with_map_preset(new_transform(MAP), 'Spherize'))
        radius = np.linalg.norm(vertices, axis=1)
        np.testing.assert_allclose(np.linalg.norm(sphere[radius > 1e-9], axis=1), 1., rtol=1e-12)
        partial = run(vertices, with_map_preset(new_transform(MAP), 'Partial spherize'))
        np.testing.assert_allclose(np.linalg.norm(partial[radius > 1e-9], axis=1), (radius[radius > 1e-9] + 1) / 2, rtol=1e-12)
        # Through posed_object (resting pose, then the stack) as the scene uses it.
        posed = posed_object(0., Shape(solid=MODEL_OBJECT, model=model, transforms=(with_map_preset(new_transform(MAP), 'Spherize'),)),
                             include_depth=True)[0]
        self.assertTrue(np.isfinite(posed).all())

    def test_every_example_validates_and_bulge_keeps_the_rim(self):
        for name in MAP_PRESETS:
            with self.subTest(name=name): validate_stack((with_map_preset(new_transform(MAP), name),))
        far = np.array([[3., 0., 0.]]); centre = np.array([[.1, 0., 0.]])
        bulge = with_map_preset(new_transform(MAP), 'Bulge')
        np.testing.assert_allclose(run(far, bulge), far, atol=1e-14)
        self.assertGreater(run(centre, bulge)[0, 0], .14)

    def test_validation(self):
        for bad in (mapped(''), mapped('x', '   '), mapped('bogus'), mapped('x', 'y', 'import os'),
                    Transform(MAP, (Param(0., 'x', ((0., .5, 'linear'),)), Param(0., 'y'), Param(0., 'z')))):
            with self.subTest(bad=bad), self.assertRaises(ValueError): validate_stack((bad,))
        with self.assertRaises(ValueError) as caught: validate_stack((mapped('x', 'y', 'q'),))
        self.assertIn('Map Z′', str(caught.exception))

    def test_map_formulas_are_not_automatable(self):
        clip = Clip(0., 4., Settings(shapes=(Shape(transforms=(new_transform(MAP),)),)))
        self.assertFalse([key for key in lane_access.automatable(clip) if key[0] == 'transform'])
        self.assertIn(('on', 0, 0), lane_access.automatable(clip))  # The step's On/Off lane still exists.

    def test_only_formulas_naming_s_need_route_positions(self):
        # sin, cos and sqrt contain the letter s but do not read s (route_positions per frame is costly).
        for name in MAP_PRESETS:
            with self.subTest(name=name): self.assertFalse(uses_position((with_map_preset(new_transform(MAP), name),)))
        self.assertTrue(uses_position((mapped('x + 0.1*sin(tau*s)'),)))
        self.assertTrue(uses_position((mapped(z='z if s > 0.5 else 0'),)))
        self.assertFalse(uses_position((new_transform(MOTION_PATH),)))  # The default Orbit.

    def test_route_positions_take_each_vertex_first_visit(self):
        vertices = np.array([[0., 0., 0.], [1., 0., 0.], [1., 1., 0.], [5., 5., 5.]])
        np.testing.assert_array_equal(route_positions(vertices, [0, 1, 2, 1, 0]), [0., .25, .5, 0.])

    def test_playback_blocks_equal_one_render(self):
        times = np.arange(9600) / 48000
        settings = Settings(shapes=(Shape(transforms=(with_map_preset(new_transform(MAP), 'Wave'),)),))
        full = render(times, settings)[0]
        parts = np.concatenate([render(times[i:i+700], settings)[0] for i in range(0, len(times), 700)])
        np.testing.assert_array_equal(parts, full)
        self.assertFalse(np.allclose(full, render(times, Settings(shapes=(Shape(),)))[0]))


class MapFileTests(unittest.TestCase):
    def test_round_trip_and_version_gate(self):
        shape = Shape(solid='Cube', transforms=(with_map_preset(new_transform(MAP), 'Wave'), new_transform(MAP)))
        document = scene_document(Settings(shapes=(shape,)), 'drawing', '')
        self.assertEqual(document['version'], SCENE_VERSION)
        self.assertEqual(document['shapes'][0]['transforms'][1],
                         dict(kind='Map', params=dict(x=dict(value=0., expression='x'), y=dict(value=0., expression='y'),
                                                      z=dict(value=0., expression='z'))))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'map.scene.json'; atomic_json(path, document)
            self.assertEqual(read_scene(path)[0].shapes, (shape,))
            path.write_text(json.dumps(dict(document, version=21)), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'scene version 22'): read_scene(path)


if __name__ == '__main__':
    unittest.main()
