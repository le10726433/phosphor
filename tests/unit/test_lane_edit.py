import unittest

import numpy as np

from phosphor.engine.automation import MAX_BEATS, gain_at, validate_points
from phosphor.engine.lane_edit import (
    SNAP_EPSILON, reference_gain, on_reference, snap, move_node,
    drag_segment, snap_delta, tidy, set_reference, shift_lane, split_segment,
    bend_segment, straighten, set_gains,
)


POINTS = ((0., 0., 'linear'), (1., .5, 'linear'),
          (3., 1., 'linear'), (4., 0., 'hold'))


class LaneEditTests(unittest.TestCase):
    def test_set_gains_linear_hold_and_untouched_nodes(self):
        result = self.valid(set_gains(POINTS, {1, 3}, -.123456))
        self.assertEqual(result, (POINTS[0], (1., -.1235, 'linear'),
                                  POINTS[2], (4., -.1235, 'hold')))
        self.assertEqual(set_gains(POINTS, (), .5), POINTS)
        self.assertEqual(set_gains((), (), .5), ())
        self.assertEqual(set_gains(POINTS, {0}, 9.)[0][1], 1.)
        self.assertEqual(set_gains(POINTS, {0}, -9.)[0][1], -1.)
        for index in (-1, len(POINTS)):
            with self.assertRaises(ValueError): set_gains(POINTS, {index}, .5)

    def test_set_gains_curve_offsets_and_bounds(self):
        points = ((0., 0., 'curve', .2, -.1), (2., .3, 'curve', .1, .2),
                  (4., 0., 'hold'), (6., -.5, 'linear'))
        result = self.valid(set_gains(points, {1}, .6))
        self.assertEqual(result[0][:3], points[0][:3])
        self.assertEqual(result[1][:3], (2., .6, 'curve'))
        for i in (0, 1):
            for j in (1, 2):
                old_chord = (1-j/3)*points[i][1] + j/3*points[i+1][1]
                new_chord = (1-j/3)*result[i][1] + j/3*result[i+1][1]
                self.assertAlmostEqual(result[i][j+2]-new_chord, points[i][j+2]-old_chord)
        self.assertEqual(result[2:], points[2:])
        together = self.valid(set_gains(points, {0, 1}, .1))
        self.assertEqual(tuple(p[1] for p in together), (.1, .1, 0., -.5))
        self.assertAlmostEqual(together[0][3], .2)
        self.assertAlmostEqual(together[0][4], -.2)
        bounded = ((0., 0., 'curve', 1., .8), (4., 0., 'hold'))
        self.assertEqual(self.valid(set_gains(bounded, {0}, .5)), bounded)
        self.assertEqual(self.valid(set_gains(bounded, {0, 1}, 9.)), bounded)

    def test_cubic_split_and_trim_exactness(self):
        points = ((0., -.4, 'curve', .9, -.7), (4., .6, 'linear'))
        for u in (.001, .125, .37, .9, .999):
            left, right = split_segment(points, 0, u)
            split = (left, right, points[1])
            grid = np.linspace(0., 4., 1001)
            np.testing.assert_allclose(gain_at(split, (1., 0.), grid),
                                       gain_at(points, (1., 0.), grid), atol=1e-12, rtol=0.)
            trimmed = shift_lane(points, -4*u)
            grid = np.linspace(0., 4*(1-u), 1001)
            np.testing.assert_allclose(gain_at(trimmed, (1., 0.), grid),
                                       gain_at(points, (1., 0.), grid+4*u), atol=1e-12, rtol=0.)

    def test_curve_editing_preserves_bend_offsets(self):
        points = bend_segment(((0., -.2, 'linear'), (4., .1, 'hold')), 0, .15)
        self.assertEqual(points[0][2], 'curve')
        moved = move_node(points, 1, 4., .4, 4.)
        self.assertAlmostEqual(moved[0][3]-points[0][3], .1)
        self.assertAlmostEqual(moved[0][4]-points[0][4], .2)
        dragged, index = drag_segment(points, 0, .1)
        self.assertEqual(index, 0)
        for j in (1, 3, 4): self.assertAlmostEqual(dragged[0][j]-points[0][j], .1)
        self.assertEqual(straighten(points, 0), ((0., -.2, 'linear'), (4., .1, 'hold')))
        self.assertEqual(bend_segment(points, 0, -.15)[0][2], 'linear')
        validate_points(bend_segment(points, 0, 100.), 'bounded')
        bounded = ((0., 0., 'curve', 1., .8), (4., 0., 'linear'))
        self.assertEqual(drag_segment(bounded, 0, .5)[0], bounded)
        self.assertEqual(move_node(bounded, 0, 0., .5, 4.), bounded)
        self.assertEqual(tidy(points), points)
        self.assertEqual(on_reference(points, reference_gain(points)), (0,))
        validate_points(set_reference(points, -.1), 'reference')
        self.assertAlmostEqual(snap_delta(points, 0, .08, -.1, .03), .1)

    def test_shift_preserves_curve(self):
        lanes = (
            ((0., 0., 'curve', .8, -.4), (2., .5, 'curve', -.2, .9), (4., .2, 'hold')),
            ((0., 0., 'linear'), (2., 1., 'linear'), (4., .5, 'hold')),
            ((0., 0., 'hold'), (2., 1., 'hold'), (4., .5, 'linear')),
            ((0., 0., 'linear'), (2., .5, 'linear'),
             (2., 1., 'hold'), (4., .5, 'linear')),
        )
        for points in lanes:
            for offset in (1.25, -.5, -2., -2.5):
                with self.subTest(points=points, offset=offset):
                    result = self.valid(shift_lane(points, offset))
                    grid = np.unique(np.concatenate((np.linspace(0., 8., 2001),
                                                    [p[0] for p in result])))
                    np.testing.assert_allclose(gain_at(result, (1., 0.), grid),
                                               gain_at(points, (1., 0.), grid - offset),
                                               atol=1e-12, rtol=0.)
                    if offset == -.5:
                        self.assertEqual(result[0][2], points[0][2])
                    if offset == -2.:
                        self.assertEqual(result, tuple((p[0] - 2., *p[1:])
                                                     for p in points if p[0] >= 2.))

    def test_shift_boundaries_and_interpolation_precision(self):
        self.assertIs(shift_lane(POINTS, 0.), POINTS)
        self.assertEqual(shift_lane((), -1.), ())
        self.assertEqual(self.valid(shift_lane(POINTS, -5.)), ((0., 0., 'hold'),))
        points = ((0., 0., 'linear'), (3., 1., 'hold'))
        self.assertEqual(self.valid(shift_lane(points, -1.))[0], (0., 1. / 3., 'linear'))
        self.assertEqual(self.valid(shift_lane(POINTS, MAX_BEATS - 1.)),
                         ((MAX_BEATS - 1., 0., 'linear'),
                          (MAX_BEATS, .5, 'linear')))
        self.assertEqual(self.valid(shift_lane(POINTS, MAX_BEATS + 1.)),
                         ((MAX_BEATS, 0., 'linear'),))

    def valid(self, points):
        self.assertIsInstance(points, tuple)
        validate_points(points, 'edited lane')
        return points

    def test_reference_and_snap(self):
        self.assertEqual(SNAP_EPSILON, 1e-6)
        self.assertEqual(reference_gain(()), 1.)
        self.assertEqual(reference_gain(POINTS), 0.)
        self.assertEqual(on_reference(POINTS, 0.), (0, 3))
        self.assertEqual(on_reference(((0., 1. + 5e-7, 'hold'),), 1.), (0,))
        self.assertEqual(snap(.95, 1., .1), 1.)
        self.assertEqual(snap(.8, 1., .1), .8)
        self.assertEqual(snap(.95, 1., 0), .95)

    def test_move_node(self):
        result = self.valid(move_node(POINTS, 1, -10, 9, 4))
        self.assertEqual(result[1], (.001, 1., 'linear'))  # Gain clamps at GAIN_RANGE (±1).
        self.assertEqual(self.valid(move_node(POINTS, 1, 9, .123456, 4))[1],
                         (2.999, .1235, 'linear'))
        self.assertEqual(self.valid(move_node(POINTS, 0, -1, -9, 4))[0][0:2], (0., -1.))
        self.assertEqual(self.valid(move_node(POINTS, 3, 9, 0, 4))[3][0], 4.)
        pair = ((0., 0., 'linear'), (1., .5, 'linear'), (1., 1., 'hold'))
        for i in (1, 2):
            self.assertEqual(self.valid(move_node(pair, i, 3, .123456, 4))[i][0], 1.)
        narrow = ((0., 0., 'linear'), (.0001, 1., 'linear'), (.0002, 0., 'hold'))
        self.assertEqual(self.valid(move_node(narrow, 1, 3, .5, 4))[1][0], .0001)

    def test_middle_drag_preserves_outside_and_structure(self):
        for delta in (-.1, -.3):  # Downward: the segment's right end is already at +1.
            result, index = drag_segment(POINTS, 1, delta)
            self.valid(result)
            self.assertEqual(index, 2)
            self.assertEqual(len(result), 6)
            self.assertEqual(tuple(p[0] for p in result), (0., 1., 1., 3., 3., 4.))
            grid = np.concatenate((np.linspace(-1., 1., 1000, endpoint=False),
                                   np.linspace(3., 5., 1000)))
            np.testing.assert_allclose(gain_at(result, (1., 0.), grid),
                                       gain_at(POINTS, (1., 0.), grid))
            self.assertEqual(result[index][1], round(.5 + delta, 4))
        self.assertEqual(POINTS[1][1], .5)

    def test_existing_pairs_and_boundary_segments(self):
        paired, index = drag_segment(POINTS, 1, .2)
        result, new_index = drag_segment(paired, index, .1)
        self.valid(result)
        self.assertEqual((len(result), new_index), (len(paired), index))
        for i, size, new_index in ((0, 5, 0), (2, 5, 3)):
            result, index = drag_segment(POINTS, i, .1)
            self.valid(result)
            self.assertEqual((len(result), index), (size, new_index))
        two = POINTS[:2]
        result, index = drag_segment(two, 0, .123456)
        self.valid(result)
        self.assertEqual((len(result), index, result[0][1]), (2, 0, .1235))
        for i in (-1, 3, 4):
            with self.assertRaises(ValueError):
                drag_segment(POINTS, i, .1)
        with self.assertRaises(ValueError):
            drag_segment(paired, 1, .1)

    def test_hold_and_delta_clamping(self):
        hold = (POINTS[0], (1., .5, 'hold'), POINTS[2], POINTS[3])
        result, index = drag_segment(hold, 1, 10.)
        self.valid(result)
        self.assertEqual(len(result), 5)
        self.assertEqual(result[index], (1., 1., 'hold'))
        self.assertEqual(result[index + 1], hold[2])
        for delta, expected in ((10., (.5, 1.)), (-10., (-1., -.5))):  # The segment already touches +1.
            result, index = drag_segment(POINTS, 1, delta)
            self.valid(result)
            self.assertEqual(tuple(p[1] for p in result[index:index + 2]), expected)

    def test_snap_delta(self):
        self.assertEqual(snap_delta(POINTS, 1, .46, 1., .1), .5)
        self.assertEqual(snap_delta(POINTS, 1, -.04, 1., .1), 0.)
        self.assertEqual(snap_delta(POINTS, 1, .46, 1., 0), .46)
        self.assertEqual(snap_delta(POINTS, 1, .2, 1., .1), .2)
        hold = ((0., .5, 'hold'), (1., 1., 'linear'))
        self.assertEqual(snap_delta(hold, 0, -.04, 1., .1), -.04)
        close = ((0., .95, 'linear'), (1., .98, 'hold'))
        self.assertAlmostEqual(snap_delta(close, 0, 0., 1., .1), .02)

    def test_tidy_and_set_reference(self):
        pair = ((0., 1., 'linear'), (0., 1., 'hold'), (1., .5, 'linear'))
        self.assertEqual(self.valid(tidy(pair)), pair[1:])
        self.assertEqual(self.valid(tidy(())), ())
        self.assertEqual(self.valid(tidy(POINTS)), POINTS)
        near = POINTS + ((5., 5e-7, 'hold'),)
        result = self.valid(set_reference(near, -.123456))
        self.assertEqual(tuple(p[1] for p in result), (-.1235, .5, 1., -.1235, -.1235))
        self.assertEqual(self.valid(set_reference(POINTS, 9))[0][1], 1.)
        self.assertEqual(self.valid(set_reference(POINTS, -9))[0][1], -1.)
        self.assertEqual(self.valid(set_reference((), .5)), ())


if __name__ == '__main__':
    unittest.main()
