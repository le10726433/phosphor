"""Triggered X(t)/Y(t) sweep: trigger, free-run, chunking and screen mapping."""
import unittest
import numpy as np
from phosphor.display.time_sweep import AUTO_SECONDS, EDGE_FALLING, EDGE_RISING, SCOPE_SCALE, TRIGGER_BOTH, TRIGGER_X, TRIGGER_Y, DualSweep, TimeSweep


def tone(seconds, rate=48000, hz=100., phase=1.):
    t = np.arange(round(seconds*rate))/rate
    return np.column_stack((np.sin(2*np.pi*hz*t+phase), .5*np.cos(2*np.pi*hz*t))).astype(np.float32)


class TimeSweepTests(unittest.TestCase):
    def test_sweeps_start_on_rising_zero_crossings(self):
        sweep = TimeSweep(); sweep.configure(15, 48000)
        signal = tone(.5)
        pieces = sweep.feed(signal)
        starts = [values[0] for positions, values in pieces if positions[0] == 0]
        self.assertGreater(len(starts), 10)
        for first in starts:
            self.assertGreaterEqual(first[0], 0.)
            self.assertLess(first[0], .02)  # One 100 Hz sample step is about 0.013.
        # Each sweep covers the whole window once, then waits for the next trigger.
        drawn = sum(len(p) for p, _ in pieces)
        self.assertLessEqual(drawn, len(signal))
        self.assertEqual(max(p[-1] for p, _ in pieces), sweep.length-1)

    def test_chunking_does_not_change_the_sweeps(self):
        signal = tone(.4, hz=73.)
        whole = TimeSweep(); whole.configure(9, 48000)
        whole_pieces = whole.feed(signal)
        split = TimeSweep(); split.configure(9, 48000)
        pieces = []
        for chunk in np.array_split(signal, 61):
            pieces += split.feed(chunk)
        # Pieces are contiguous runs, and chunk joins repeat the previous sample.
        self.assertTrue(all(np.all(np.diff(p) == 1) for p, _ in pieces))
        starts = lambda items: [v[0].tolist() for p, v in items if p[0] == 0]
        self.assertEqual(starts(pieces), starts(whole_pieces))
        drawn = lambda items: sum(len(p)-(p[0] != 0) for p, _ in items)
        self.assertEqual(drawn(pieces), drawn(whole_pieces))
        np.testing.assert_array_equal(whole.buffer, split.buffer)

    def test_silence_free_runs_after_the_auto_timeout(self):
        sweep = TimeSweep(); sweep.configure(5, 48000)
        self.assertEqual(sweep.feed(np.zeros((round(AUTO_SECONDS*48000)-1, 2))), [])
        pieces = sweep.feed(np.zeros((10, 2)))
        self.assertEqual(pieces[0][0][0], 0)

    def test_long_windows_wait_one_window_before_free_running(self):
        sweep = TimeSweep(); sweep.configure(2000, 96000)
        self.assertEqual(sweep.length, 192000)
        self.assertEqual(sweep.feed(np.zeros((150000, 2))), [])
        self.assertTrue(sweep.feed(np.zeros((50000, 2))))

    def test_window_and_rate_changes_restart_and_clamp(self):
        sweep = TimeSweep(); sweep.configure(20, 48000)
        sweep.feed(tone(.1)); self.assertGreater(sweep.filled, 0)
        sweep.configure(.01, 96000)
        self.assertEqual((sweep.window_ms, sweep.length, sweep.filled), (.1, 10, 0))
        sweep.configure(5000, 96000)
        self.assertEqual(sweep.window_ms, 2000.)

    def test_screen_points_share_time_axis_and_separate(self):
        sweep = TimeSweep(); sweep.configure(1, 48000)
        positions = np.array([0, sweep.length-1]); values = np.array([[.5, .5], [0., 0.]], dtype=np.float32)
        x, y = sweep.screen_points(positions, values, 800, 600, 1., 0.)
        np.testing.assert_allclose(x, y)
        np.testing.assert_allclose(x[:, 0], (0, 800))
        np.testing.assert_allclose(x[0, 1], 300-.5*SCOPE_SCALE*600)
        x, y = sweep.screen_points(positions, values, 800, 600, 2., .5)
        np.testing.assert_allclose(x[1, 1], 300-.5*SCOPE_SCALE*600)  # X trace raised.
        np.testing.assert_allclose(y[1, 1], 300+.5*SCOPE_SCALE*600)  # Y trace lowered.
        np.testing.assert_allclose(x[0, 1], 300-1.5*SCOPE_SCALE*600)  # Gain applies before offset.

    def test_line_view_reduces_long_windows_to_pixel_columns(self):
        sweep = TimeSweep(); sweep.configure(2000, 96000)
        sweep.feed(tone(2.2, rate=96000, hz=440.))
        polylines = sweep.display_polylines(700, 500, 1., .3)
        self.assertEqual(len(polylines), 2)
        self.assertEqual(len(polylines[0]), 1400)
        # Envelope keeps the waveform's extremes.
        scale = SCOPE_SCALE*500
        self.assertAlmostEqual(float(polylines[0][:, 1].min()), 250-(1+.3)*scale, delta=1)
        short = TimeSweep(); short.configure(1, 48000); short.feed(tone(.2))
        self.assertEqual(len(short.display_polylines(700, 500, 1., 0.)[0]), short.filled)


class TriggerTests(unittest.TestCase):
    def starts(self, sweep, signal, channel):
        return [values[0] for positions, values in sweep.feed(signal) if positions[0] == 0]

    def test_y_trigger_and_falling_edge(self):
        signal = tone(.5)  # Y is a cosine at half amplitude.
        sweep = TimeSweep(1); sweep.configure(15, 48000)
        firsts = self.starts(sweep, signal, 1)
        self.assertGreater(len(firsts), 10)
        self.assertTrue(all(0 <= first[1] < .02 for first in firsts))
        falling = TimeSweep(0); falling.configure(15, 48000, True)
        firsts = self.starts(falling, signal, 0)
        self.assertGreater(len(firsts), 10)
        self.assertTrue(all(-.02 < first[0] <= 0 for first in firsts))
        # After a rising trigger the next sample is higher.
        pieces = [v for p, v in TimeSweep(0).feed(signal) if p[0] == 0]
        self.assertTrue(all(v[1, 0] > v[0, 0] for v in pieces))

    def test_both_gives_each_trace_its_own_sweep(self):
        t = np.arange(48000//2)/48000
        # Unrelated frequencies: one shared sweep cannot hold both still.
        signal = np.column_stack((np.sin(2*np.pi*100*t+1), np.sin(2*np.pi*137*t+2))).astype(np.float32)
        dual = DualSweep(); dual.configure(TRIGGER_BOTH, EDGE_RISING, 12, 48000)
        self.assertEqual([channels for _, channels in dual.sweeps], [(0,), (1,)])
        traces = dual.traces(signal, 800, 600, 1., .3)
        self.assertEqual(len(traces), 2)
        for (sweep, (channel,)), (points, drawn) in zip(dual.sweeps, traces):
            self.assertEqual(sweep.channel, channel)
            self.assertEqual(len(drawn), len(points)-1)
            # Every sweep of each trace starts on that trace's own rising crossing.
            starts = points[np.r_[True, ~drawn]]
            centre = 300-(.3 if channel == 0 else -.3)*SCOPE_SCALE*600
            self.assertTrue(np.all(abs(starts[:, 1]-centre) < .02*SCOPE_SCALE*600))
            self.assertTrue(np.all(starts[:, 0] == 0))
        self.assertEqual(len(dual.display_polylines(800, 600, 1., .3)), 2)
        dual.configure(TRIGGER_Y, EDGE_FALLING, 12, 48000)
        self.assertEqual(len(dual.sweeps), 1)
        sweep, channels = dual.sweeps[0]
        self.assertEqual((sweep.channel, sweep.falling, channels), (1, True, (0, 1)))
        dual.configure(TRIGGER_X, EDGE_RISING, 12, 48000)
        self.assertEqual((dual.sweeps[0][0].channel, dual.sweeps[0][0].falling), (0, False))


if __name__ == '__main__':
    unittest.main()
