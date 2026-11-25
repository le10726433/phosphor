import unittest

from phosphor.editor.zoom_fit import zoom_fit


class ZoomFitTests(unittest.TestCase):
    def test_offset_range_and_pixel_margins(self):
        offset, scale = zoom_fit((20., 28.), 912., 64., 24., 2., 400.)
        self.assertEqual(scale, 100.)
        self.assertAlmostEqual(64 + (20 - offset) * scale, 88.)
        self.assertAlmostEqual(64 + (28 - offset) * scale, 888.)

    def test_song_starts_at_zero(self):
        offset, scale = zoom_fit((0., 32.), 1000.)
        self.assertEqual(offset, 0.)
        self.assertGreater(scale * 32, 800.)
        self.assertLessEqual(64 + scale * 32, 976.)

    def test_scale_limits(self):
        self.assertEqual(zoom_fit((0., 10000.), 1000.)[1], 2.)
        self.assertEqual(zoom_fit((10., 10.001), 1000.)[1], 400.)
        self.assertEqual(zoom_fit((0., 100.), 1000., min_scale=10., max_scale=20.)[1], 10.)

    def test_degenerate_and_reversed_ranges(self):
        self.assertEqual(zoom_fit((0., 0.), 1000.), (0., 400.))
        self.assertEqual(zoom_fit((28., 20.), 912.), zoom_fit((20., 28.), 912.))
        self.assertEqual(zoom_fit((-4., -1.), 1000.), (0., 400.))

    def test_tiny_view_and_invalid_limits(self):
        self.assertEqual(zoom_fit((0., 4.), 20.), (0., 2.))
        for kwargs in ({'min_scale': 0}, {'min_scale': 10, 'max_scale': 2}, {'margin': -1}):
            with self.assertRaises(ValueError): zoom_fit((0., 4.), 1000., **kwargs)


if __name__ == '__main__':
    unittest.main()
