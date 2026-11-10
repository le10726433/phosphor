"""Check the buffer contract and pathological trajectories without needing Qt/GPU."""
import unittest
import numpy as np
from qt_geometry import BEAM_FLOOR, BEAM_VERTEX, beam_energy, beam_triangles, stroke_triangles, grid_lines


class BeamTests(unittest.TestCase):
    def test_fast_segments_are_dimmer_and_floored(self):
        energy = beam_energy([1, 1, 1, 10, 1000])
        np.testing.assert_array_equal(energy[:3], [1, 1, 1])
        self.assertLess(energy[3], 1)
        self.assertEqual(float(energy[4]), np.float32(BEAM_FLOOR))
        self.assertEqual(len(beam_energy([])), 0)

    def test_scale_does_not_change_brightness(self):
        lengths = np.array([1., 2., 3., 40., 90.])
        np.testing.assert_allclose(beam_energy(lengths), beam_energy(lengths*7.5), atol=1e-6)

    def test_uniform_circle_is_even(self):
        phase = np.linspace(0, 2*np.pi, 500)
        vertices = beam_triangles(np.column_stack((np.cos(phase), np.sin(phase)))*200, 1, (255, 200, 100), .95, 1.)
        self.assertEqual(set(vertices['a']), {242})

    def test_vertex_layout_is_premultiplied_and_matches_stroke(self):
        self.assertEqual(BEAM_VERTEX.itemsize, 12)
        points = [(0, 0), (1, 0), (2, 0), (3, 0), (203, 0)]
        vertices = beam_triangles(points, 2, (200, 100, 0), 1., 1.)
        stroke = stroke_triangles(points, 2)
        np.testing.assert_array_equal(np.column_stack((vertices['x'], vertices['y'])), stroke)
        self.assertTrue(vertices.flags.c_contiguous)
        np.testing.assert_allclose(vertices['r'], vertices['a'].astype(float)*200/255, atol=1)
        self.assertLess(vertices['a'][-1], vertices['a'][0])

    def test_strength_zero_is_even_and_silence_is_empty(self):
        vertices = beam_triangles([(0, 0), (1, 0), (500, 0)], 1, (255, 255, 255), .5, 0.)
        self.assertEqual(set(vertices['a']), {128})
        self.assertEqual(len(beam_triangles(np.zeros((1920, 2)), 1, (255, 255, 255), .95, 1.)), 0)
        points = [(0, 0), (0, 0), (3, 4), (3, 4), (6, 8)]
        self.assertEqual(len(beam_triangles(points, 1, (255, 255, 255), .95, 1.)), 12)


class StrokeTests(unittest.TestCase):
    def test_immersive_grid_extends_with_square_cells(self):
        for width,height in ((1920,1080),(1080,1920),(1000,1000)):
            for divisions in (7,8,64):
                lines=grid_lines(width,height,divisions)
                xs=sorted(line[0][0] for line in lines if line[0][0]==line[1][0])
                ys=sorted(line[0][1] for line in lines if line[0][1]==line[1][1])
                step=min(width,height)/divisions
                np.testing.assert_allclose(np.diff(xs),step)
                np.testing.assert_allclose(np.diff(ys),step)
                self.assertLessEqual(xs[0],step+1e-7)
                self.assertLessEqual(width-xs[-1],step+1e-7)
                self.assertLessEqual(ys[0],step+1e-7)
                self.assertLessEqual(height-ys[-1],step+1e-7)
                if width==height:
                    np.testing.assert_allclose(xs,np.arange(1,divisions)*step)

    def test_width_endpoints_and_buffer_contract(self):
        vertices = stroke_triangles([(2, 3), (12, 3)], 4)
        self.assertEqual(vertices.dtype, np.float32)
        self.assertTrue(vertices.flags.c_contiguous)
        self.assertEqual(vertices.strides, (8, 4))
        np.testing.assert_array_equal(vertices.min(axis=0), [2, 1])
        np.testing.assert_array_equal(vertices.max(axis=0), [12, 5])

    def test_repeated_silent_samples_are_finite(self):
        self.assertEqual(stroke_triangles(np.zeros((1920, 2)), 1).shape, (0, 2))
        vertices = stroke_triangles([(0, 0), (0, 0), (3, 4), (3, 4)], 2)
        self.assertEqual(len(vertices), 6)
        self.assertTrue(np.isfinite(vertices).all())

    def test_offscreen_trajectory_is_not_clamped_or_changed(self):
        points = np.array([[-5., -3.], [7., 3.], [1., 2.]])
        original = points.copy()
        vertices = stroke_triangles(points, 1)
        np.testing.assert_array_equal(points, original)
        self.assertLess(vertices[:, 0].min(), -1)
        self.assertGreater(vertices[:, 0].max(), 1)


if __name__ == '__main__':
    unittest.main()
