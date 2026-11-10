import unittest
from dataclasses import replace
import numpy as np
from scene_path import TRAVEL_MODES, plan_scene, travel_length
from signal_engine import Settings, Shape, planned_path, projected_solid, render, shape_audio, validate
from solids import geometry, route_for


class TravelTests(unittest.TestCase):
    def setUp(self):
        self.shapes = (Shape('Square (2D)', -2, 0, .4),
                       Shape('Triangle (2D)', 2, 0, .4),
                       Shape('Pentagon (2D)', 0, 2, .4))
        self.parts = [(projected_solid(0, shape) * shape.shape_size,
                       np.array([shape.position_x, shape.position_y]),
                       route_for(shape.solid), geometry(shape.solid)[1]) for shape in self.shapes]

    def test_no_changes_retains_old_route_and_timing(self):
        settings = Settings(shapes=self.shapes)
        local, centers, travel = plan_scene(self.parts, 'No changes')
        cumulative = np.r_[0, np.cumsum(np.linalg.norm(np.diff(local + centers, axis=0), axis=1))]
        phase = np.linspace(0, 1, 10000, endpoint=False)
        distance = phase * cumulative[-1]
        index = np.minimum(np.searchsorted(cumulative, distance, side='right') - 1, len(cumulative) - 2)
        fraction = (distance-cumulative[index]) / np.maximum(np.diff(cumulative)[index], 1e-15)
        expected = local[index] + centers[index] + fraction[:, None] * np.diff(local + centers, axis=0)[index]
        actual = shape_audio(np.zeros(len(phase)), phase, np.ones(len(phase)), settings)
        np.testing.assert_allclose(actual, expected, atol=1e-12)

    def test_faster_travel_changes_only_travel_weights(self):
        local, centers, travel = plan_scene(self.parts, 'No changes')
        a, b, baseline = planned_path(0, 60, self.shapes, 'No changes', 8)
        c, d, faster = planned_path(0, 60, self.shapes, 'Faster travel', 8)
        np.testing.assert_array_equal(a, c)
        np.testing.assert_array_equal(b, d)
        np.testing.assert_allclose(np.diff(faster), np.diff(baseline) / np.where(travel, 8, 1), atol=1e-12)
        self.assertLess(faster[-1], baseline[-1])

    def test_optimized_connections_and_retrace_stay_on_existing_edges(self):
        baseline = plan_scene(self.parts, 'No changes')
        for mode in ('Nearest-shape ordering', 'Retrace existing edges'):
            local, centers, travel = plan_scene(self.parts, mode)
            self.assertLessEqual(travel_length((local, centers, travel)), travel_length(baseline) + 1e-12)
            covered = [set() for _ in self.parts]
            for index in np.flatnonzero(~travel):
                if np.linalg.norm(local[index+1]-local[index]) < 1e-12:
                    continue
                owner = next(i for i, part in enumerate(self.parts) if np.allclose(part[1], centers[index]))
                vertices = self.parts[owner][0]
                a = int(np.argmin(np.linalg.norm(vertices-local[index], axis=1)))
                b = int(np.argmin(np.linalg.norm(vertices-local[index+1], axis=1)))
                edge = tuple(sorted((a, b)))
                self.assertIn(edge, self.parts[owner][3])
                covered[owner].add(edge)
            for owner, part in enumerate(self.parts):
                self.assertEqual(covered[owner], set(part[3]))
        self.assertGreater(len(plan_scene(self.parts, 'Retrace existing edges')[0]), len(baseline[0]))

    def test_modes_are_chunk_independent_and_original_audio_is_unchanged(self):
        times = np.arange(5000) / 48000
        reference = render(times, Settings(shapes=self.shapes))[1]
        for mode in TRAVEL_MODES:
            settings = Settings(shapes=self.shapes, travel_mode=mode)
            whole = render(times, settings)
            chunks = np.concatenate([render(times[:1523], settings)[2], render(times[1523:], settings)[2]])
            np.testing.assert_array_equal(whole[2], chunks)
            np.testing.assert_array_equal(whole[1], reference)
            single = render(times, Settings())[2]
            np.testing.assert_array_equal(render(times, Settings(travel_mode=mode))[2], single)
        with self.assertRaises(ValueError):
            validate(Settings(travel_speed=0))


if __name__ == '__main__':
    unittest.main()


class NewSceneDefaultTests(unittest.TestCase):
    def test_new_scene_uses_fast_travel_but_engine_default_is_unchanged(self):
        from qt_scene import Scene
        settings = Scene().settings
        self.assertEqual((settings.travel_mode, settings.travel_speed), ('Faster travel', 64.))
        self.assertEqual((Settings().travel_mode, Settings().travel_speed), ('No changes', 8.))
