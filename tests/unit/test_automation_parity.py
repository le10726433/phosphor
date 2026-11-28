"""gain_at's scalar fast path (Q1 bundle E) is bit-identical to its array path, which keeps the
pre-bundle-E formula: curved automation must not change audio samples (golden invariant)."""
import unittest

import numpy as np

from phosphor.engine.automation import gain_at
from phosphor.engine.tempo_map import TempoMap, BeatClock


def random_lane(rng, kinds):
    count = int(rng.integers(1, 9))
    beats = np.sort(rng.uniform(0., 16., count))
    if count > 2 and rng.random() < .3: beats[1] = beats[0]  # A step: two points at one beat.
    points = []
    for beat in beats:
        kind = kinds[int(rng.integers(len(kinds)))]
        gain = float(rng.uniform(-1., 1.))
        if kind == 'curve': points.append((float(beat), gain, 'curve', float(rng.uniform(-1., 1.)), float(rng.uniform(-1., 1.))))
        else: points.append((float(beat), gain, kind))
    return tuple(points)


class GainAtParityTests(unittest.TestCase):
    def assert_scalar_equals_array(self, kinds, seed):
        rng = np.random.default_rng(seed)
        tempo = BeatClock(TempoMap(97., ((3., 140.), (6.5, 80.))), 1.25, .5)
        clocks = ((.5, 0.), (60. / 97., .3), tempo)
        compared = 0
        for _ in range(150):
            lane = random_lane(rng, kinds)
            for clock in clocks:
                times = np.r_[rng.uniform(-1., 12., 40), 0., -.5, 30.]
                array = gain_at(lane, clock, times)
                scalar = np.array([gain_at(lane, clock, float(t)) for t in times])
                self.assertTrue(np.array_equal(scalar, array), (lane, clock))
                compared += len(times)
        self.assertGreater(compared, 15000)

    def test_curve_lanes_scalar_equals_array_exactly(self):
        self.assert_scalar_equals_array(('curve',), 1)

    def test_linear_and_hold_lanes_scalar_equals_array_exactly(self):
        self.assert_scalar_equals_array(('linear',), 2)
        self.assert_scalar_equals_array(('hold',), 3)

    def test_mixed_lanes_scalar_equals_array_exactly(self):
        self.assert_scalar_equals_array(('curve', 'linear', 'hold'), 4)

    def test_curve_keeps_the_pre_bundle_e_formula(self):
        """The array path equals the original Bezier expression term for term (9d86c30)."""
        rng = np.random.default_rng(5)
        for _ in range(200):
            g0, h1, h2, g1 = (float(v) for v in rng.uniform(-1., 1., 4))
            length = float(rng.uniform(.25, 8.))
            lane = ((0., g0, 'curve', h1, h2), (length, g1, 'linear'))
            beats = rng.uniform(0., length, 50)
            u = np.clip(beats / length, 0., 1.)
            expected = (1-u)**3*g0 + 3*(1-u)**2*u*h1 + 3*(1-u)*u*u*h2 + u**3*g1
            self.assertTrue(np.array_equal(gain_at(lane, (1., 0.), beats), expected))
            self.assertTrue(np.array_equal([gain_at(lane, (1., 0.), float(b)) for b in beats], expected))


if __name__ == '__main__':
    unittest.main()
