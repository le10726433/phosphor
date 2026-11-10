import unittest
import numpy as np
from playback_history import PlaybackHistory


class PlaybackHistoryTests(unittest.TestCase):
    def test_burst_writes_advance_smoothly_between_publications(self):
        now = [0.]
        history = PlaybackHistory(clock=lambda:now[0])
        history.reset(48000,.1)
        # CoreAudio accepts a 100 ms batch immediately, then sleeps ~80 ms.
        samples = np.arange(9600, dtype=np.float32).reshape(-1,2)
        for start in range(0,4800,512):
            history.publish(start,samples[start:start+512],0.)
        cursors = []
        for tick in range(1,5):
            now[0] = .1+tick/60
            displayed,cursor = history.snapshot(1920)
            cursors.append(cursor)
            valid = min(cursor,1920)
            np.testing.assert_array_equal(displayed[-valid:],samples[cursor-valid:cursor])
            if cursor < 1920: np.testing.assert_array_equal(displayed[:-valid],0.)
        self.assertEqual(len(set(cursors)),4)
        np.testing.assert_allclose(np.diff(cursors),800,atol=1)

    def test_latency_silence_and_no_unsubmitted_samples(self):
        now=[1.]
        history=PlaybackHistory(clock=lambda:now[0]);history.reset(48000,.1)
        block=np.ones((512,2),dtype=np.float32)
        history.publish(0,block,1.)
        signal,cursor=history.snapshot(1920)
        self.assertEqual(cursor,0);np.testing.assert_array_equal(signal,0.)
        now[0]=2.
        signal,cursor=history.snapshot(1920)
        self.assertEqual(cursor,512)
        np.testing.assert_array_equal(signal[-512:],block)
        np.testing.assert_array_equal(signal[:-512],0.)

    def test_bounded_history_and_reset_after_underflow(self):
        now=[0.]
        history=PlaybackHistory(clock=lambda:now[0]);history.reset(48000,.1)
        block=np.ones((512,2),dtype=np.float32)
        for i in range(1000): history.publish(i*512,block,i*512/48000)
        self.assertEqual(len(history.blocks),64)
        history.publish(512000,block,20.,underflow=True)
        now[0]=20.1
        _,cursor=history.snapshot(1920)
        self.assertAlmostEqual(cursor,512000,delta=1)
        history.reset(48000,.03)
        signal,cursor=history.snapshot(1920)
        self.assertEqual(cursor,0);np.testing.assert_array_equal(signal,0.)
