import unittest
import numpy as np
from phosphor.engine.signal_engine import Settings, Shape, shape_audio, render, projected_solid, validate
from phosphor.engine.solids import route_for


class SceneTests(unittest.TestCase):
    def test_trace_visits_each_shape_and_connects_them_without_summing(self):
        shapes = (Shape(position_x=-.7, shape_size=.35),
                  Shape(solid='Octahedron', position_x=.7, shape_size=.3, spin_y=.125))
        settings = Settings(shapes=shapes)
        parts = [projected_solid(0, shape)[route_for(shape.solid)] * shape.shape_size
                 + [shape.position_x, shape.position_y] for shape in shapes]
        points = np.concatenate(parts + [parts[0][:1]])
        lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
        cumulative = np.concatenate(([0], np.cumsum(lengths)))
        visible = lengths > 1e-10
        phase = ((cumulative[:-1] + lengths / 2) / cumulative[-1])[visible]
        traced = shape_audio(np.zeros(len(phase)), phase, np.ones(len(phase)), settings)
        np.testing.assert_allclose(traced, ((points[:-1] + points[1:]) / 2)[visible], atol=1e-12)
        self.assertLess(traced[:, 0].min(), -.7)
        self.assertGreater(traced[:, 0].max(), .7)

    def test_identical_shapes_are_supported_and_scene_is_chunk_independent(self):
        settings = Settings(shapes=(Shape(), Shape(position_x=.25, shape_size=.5)))
        times = np.arange(12000) / 48000
        whole = render(times, settings)[2]
        split = np.concatenate([render(times[:3431], settings)[2], render(times[3431:], settings)[2]])
        np.testing.assert_array_equal(whole, split)
        self.assertLessEqual(abs(whole).max(), .95)
        single = render(times, Settings())[2]
        self.assertFalse(np.allclose(whole, single))

    def test_every_shape_is_checked_for_headroom_and_rotation(self):
        validate(Settings(shapes=(Shape(position_x=20, shape_size=30),)))
        for bad in (Shape(shape_size=0), Shape(spin_x=11),
                    Shape(position_y=float('nan'))):
            with self.assertRaises(ValueError):
                validate(Settings(shapes=(Shape(), bad)))

    def test_legacy_single_shape_and_scene_agree(self):
        times = np.arange(4000) / 48000
        legacy = Settings(solid='Octahedron', position_x=.3, shape_size=.6, rotation_y=9)
        scene = Settings(shapes=(Shape('Octahedron', .3, 0, .6, rotation_y=9),))
        np.testing.assert_array_equal(render(times, legacy)[2], render(times, scene)[2])


if __name__ == '__main__':
    unittest.main()
