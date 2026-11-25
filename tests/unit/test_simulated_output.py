"""The simulated output device (P7c): real-time cadence, underflows and AudioPreview's path."""
import unittest
from types import SimpleNamespace

import numpy as np

from phosphor.audio.simulated_output import SimulatedDevice, PortAudioError


class FakeClock:
    """A clock that only moves when someone sleeps (or a test advances it)."""
    def __init__(self):
        self.now = 0.
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += max(0., seconds)


def device(rate=1000, **options):
    clock = FakeClock()
    return SimulatedDevice(rate, clock=clock, sleep=clock.sleep, **options), clock


class SimulatedStreamTests(unittest.TestCase):
    def test_blocking_writes_wait_for_room_at_the_rate_cadence(self):
        sd, clock = device(1000)
        stream = sd.OutputStream(samplerate=1000, channels=2, dtype='float32', blocksize=10, latency=.05)
        self.assertEqual((stream.capacity, stream.latency), (50, .05))
        stream.start()
        block = np.zeros((10, 2), dtype='float32')
        for _ in range(100):
            self.assertFalse(stream.write(block))
        # 100 blocks of 10 frames at 1 kHz behind 50 frames of primed silence: one second, less the buffer.
        self.assertAlmostEqual(clock.now, (1000 + 50 - 50) / 1000, places=6)
        self.assertEqual(stream.underflows, 0)

    def test_a_late_write_reports_an_underflow_and_loses_the_time(self):
        sd, clock = device(1000)
        stream = sd.OutputStream(samplerate=1000, blocksize=10, latency=.05)
        stream.start()
        block = np.zeros((10, 2), dtype='float32')
        stream.write(block)
        clock.now += 1.  # Synthesis stalled for a second: the buffer ran dry.
        self.assertTrue(stream.write(block))
        self.assertFalse(stream.write(block))
        self.assertEqual(stream.underflows, 1)
        self.assertEqual(stream.written - stream.played(), 20)  # Silence played meanwhile is not owed.

    def test_low_latency_and_minimum_buffer(self):
        sd, _ = device(96000)
        self.assertEqual(sd.OutputStream(samplerate=96000, blocksize=512, latency='low').capacity, 960)
        self.assertEqual(sd.OutputStream(samplerate=96000, blocksize=2048, latency=.01).capacity, 2048)

    def test_refused_rate_raises_like_portaudio(self):
        sd, _ = device(96000)
        with self.assertRaises(PortAudioError):
            sd.OutputStream(samplerate=192000, blocksize=512, latency=.1)
        sd, _ = device(96000, rates=(48000, 96000))
        sd.OutputStream(samplerate=48000, blocksize=512, latency=.1)

    def test_device_queries_look_like_sounddevice(self):
        from phosphor.audio.audio_preview import device_rate, wasapi_default_output
        sd, _ = device(176400)
        self.assertEqual(device_rate(sd), 176400)
        self.assertEqual(wasapi_default_output(sd), 0)
        self.assertTrue(sd.WasapiSettings(exclusive=True).exclusive)

    def test_callback_stream_requests_blocks_until_stopped(self):
        import time
        sd = SimulatedDevice(48000)
        calls = []
        def callback(output, frames, time_info, status):
            calls.append((frames, status.output_underflow)); output.fill(0)
        stream = sd.OutputStream(samplerate=48000, blocksize=480, latency='low', callback=callback)
        stream.start()
        deadline = time.perf_counter() + 5
        while len(calls) < 3 and time.perf_counter() < deadline: time.sleep(.005)
        stream.stop(); stream.close()
        count = len(calls)
        time.sleep(.03)
        self.assertGreaterEqual(count, 3)
        self.assertEqual(len(calls), count)  # Nothing after stop.
        self.assertEqual({frames for frames, _ in calls}, {480})


class SimulatedPlaybackTests(unittest.TestCase):
    def test_playback_runs_through_the_simulated_device_at_its_rate(self):
        import time
        from phosphor.audio.audio_preview import AudioPreview
        from phosphor.engine.signal_engine import Settings
        player = AudioPreview()
        player.device = SimulatedDevice(192000)
        player.start(Settings(), 'mixed')
        try:
            deadline = time.perf_counter() + 5
            while player.frames < 4 * player.block_frames and time.perf_counter() < deadline: time.sleep(.01)
        finally:
            player.stop()
        stream = player.device.streams[-1]
        self.assertEqual((player.sample_rate, stream.samplerate), (192000, 192000))
        self.assertEqual(stream.blocksize, player.block_frames)
        self.assertGreaterEqual(player.frames, 4 * player.block_frames)
        self.assertTrue(stream.closed)
        self.assertEqual(player.error, '')


if __name__ == '__main__':
    unittest.main()
