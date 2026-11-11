"""Exponential and logarithmic Qt slider curves: bounds, zero, and round trips."""
import math
import unittest
from phosphor.editor.qt_app import curve_position, curve_value


class SliderCurveTests(unittest.TestCase):
    def test_bounds_and_zero_are_exact(self):
        self.assertEqual((curve_position(-10, -10, 10), curve_position(0, -10, 10), curve_position(10, -10, 10)), (0., .5, 1.))
        self.assertEqual((curve_value(0, -10, 10), curve_value(.5, -10, 10), curve_value(1, -10, 10)), (-10., 0., 10.))
        self.assertEqual((curve_position(0, 0, 32), curve_position(32, 0, 32)), (0., 1.))
        self.assertAlmostEqual(curve_value(1, 0, 32), 32.)

    def test_round_trip_and_sign_symmetry(self):
        for low, high, values in ((-10, 10, (-7.5, -1, -.01, .01, 1, 7.5)), (0, 32, (.001, 1, 4, 31))):
            for value in values:
                self.assertAlmostEqual(curve_value(curve_position(value, low, high), low, high), value, places=9)
        self.assertAlmostEqual(curve_position(-1, -10, 10), 1-curve_position(1, -10, 10))

    def test_finer_near_zero_and_coarser_near_the_ends(self):
        step = .01
        near = curve_value(.5+step, -10, 10)-curve_value(.5, -10, 10)
        far = curve_value(1, -10, 10)-curve_value(1-step, -10, 10)
        self.assertGreater(far/near, 40)
        # Unity gain sits about a quarter of the way along the 0–32× slider.
        self.assertTrue(.2 < curve_position(1, 0, 32) < .3)
        self.assertTrue(math.isclose(curve_position(4, 0, 32), .51, abs_tol=.01))


if __name__ == '__main__':
    unittest.main()
