"""Arithmetic checks for scroll and pinch events without a GUI."""
import math
import unittest

from phosphor.editor import scroll_mapping as scroll


class ScrollMappingTests(unittest.TestCase):
    def test_sensitivity(self):
        for value in (.25, 1, 2.5, 4):
            with self.subTest(value=value):
                result = scroll.validated_sensitivity(value, .75)
                self.assertIs(type(result), float)
                self.assertEqual(result, value)
        for value in (0, .24, 4.01, 10**500, True, '1', None, math.nan, math.inf):
            with self.subTest(value=value):
                self.assertEqual(scroll.validated_sensitivity(value, .75), .75)

    def test_trackpad_detection(self):
        self.assertFalse(scroll.is_trackpad(0, 0))
        self.assertTrue(scroll.is_trackpad(0, 0, touchpad_device=True))
        self.assertTrue(scroll.is_trackpad(0, 0, scroll_phase=True))
        self.assertTrue(scroll.is_trackpad(-1, 0))
        self.assertTrue(scroll.is_trackpad(0, .5))

    def test_wheel_and_pixel_deltas(self):
        self.assertEqual(scroll.wheel_notches(120, 1), 1.)
        self.assertEqual(scroll.wheel_notches(-240, .5), -1.)
        self.assertEqual(scroll.wheel_notches(15, 1), .125)
        self.assertEqual(scroll.trackpad_pixels(-3, .5), -1.5)
        self.assertEqual(scroll.trackpad_pixels(-6, .5), -3.)
        self.assertEqual(scroll.trackpad_pixels(-6, 2), -12.)

    def test_pinch(self):
        self.assertAlmostEqual(scroll.pinch_factor(.1, 1), 1.1)
        self.assertAlmostEqual(scroll.pinch_factor(-.1, 2), .8)
        self.assertEqual(scroll.pinch_factor(2, 4), 2.)
        self.assertEqual(scroll.pinch_factor(-2, 4), .5)
        for value in (math.inf, -math.inf, math.nan):
            self.assertEqual(scroll.pinch_factor(value, 2), 1.)

    def test_zoom_anchor(self):
        offset, anchor = 1.75, 137
        for old, new in ((12, 35), (35, 12)):
            new_offset = scroll.zoom_about(anchor, offset, old, new)
            self.assertAlmostEqual(new_offset + anchor/new, offset + anchor/old)
            self.assertEqual(scroll.zoom_about(0, offset, old, new), offset)

    def test_accumulated_steps(self):
        for direction in (1, -1):
            steps = scroll.Accumulator()
            self.assertEqual([steps.add(direction * .4) for _ in range(3)], [0, 0, direction])
            self.assertAlmostEqual(steps.remainder, direction * .2)
            for amount in (math.nan, math.inf, 0):
                self.assertEqual(steps.add(amount), 0)
                self.assertAlmostEqual(steps.remainder, direction * .2)
            self.assertEqual(steps.add(-direction * 2.125), -2 * direction)
            self.assertAlmostEqual(steps.remainder, -direction * .125)
            steps.reset()
            self.assertEqual(steps.remainder, 0.)
