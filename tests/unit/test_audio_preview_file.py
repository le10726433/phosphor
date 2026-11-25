"""One-shot browser playback using the real stream setup and a simulated device."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import phosphor
import numpy as np

from phosphor.audio.audio_preview import AudioPreview
from phosphor.audio.midi_synth import MIDI_SOURCE
from phosphor.audio.simulated_output import SimulatedDevice
from phosphor.engine.signal_engine import Settings


class FilePreviewTests(unittest.TestCase):
    def setUp(self):
        self.player = AudioPreview()
        self.player.device = SimulatedDevice(48000)
        self.addCleanup(self.player.stop)
        self.settings = replace(Settings(), source=MIDI_SOURCE)

    def test_play_once_at_level_and_end_without_gui_stop(self):
        player = self.player
        player.cycle = True
        player.composer = (object(), 0)
        player.metronome = True
        player.count_in_seconds = 1.
        data = np.ones((650, 2), dtype='float32')
        with patch('phosphor.audio.audio_preview.render', side_effect=AssertionError):
            player.start_preview(data, 48000, self.settings, 'mixed')
            player.thread.join(2)
        self.assertEqual(player.error, '')
        self.assertFalse(player.running)
        self.assertFalse(player.live)
        self.assertFalse(player.cycle)
        self.assertIsNone(player.composer)
        self.assertEqual(player.frames, 960)
        self.assertEqual(player.preview_seconds, 650 / 48000)
        samples = player.samples(960)
        np.testing.assert_allclose(samples[:240, 0], .7 * np.linspace(0, 1, 240), atol=1e-7)
        np.testing.assert_allclose(samples[240:650], .7)
        np.testing.assert_array_equal(samples[650:], 0)

    def test_level_change_and_stop_fade_across_short_blocks(self):
        player = self.player
        tail = []

        def render_on_stop(stream):
            if player._cycle_stop.wait(3):
                while not player.stop_event.is_set():
                    tail.append(player._render_block(64))
            stream.close()

        data = np.ones((48000, 2), dtype='float32')
        with patch.object(player, '_run', side_effect=render_on_stop):
            player.start_preview(data, 48000, self.settings, 'mixed')
            self.assertTrue(player.preview_running)
            self.assertIsNone(player.buffered_seconds())
            with self.assertRaises(RuntimeError):
                player.start_preview(data, 48000, self.settings, 'mixed')
            player.frames = 480  # Past the common startup ramp.
            player.preview_level = .25
            block = player._render_block(64)
            np.testing.assert_allclose(block[:, 0], np.linspace(.7, .25, 64))
            player.stop()
        samples = np.concatenate(tail)
        np.testing.assert_allclose(samples[:240, 0], .25 * np.arange(239, -1, -1) / 240)
        np.testing.assert_array_equal(samples[240:], 0)
        self.assertFalse(player.preview)
        self.assertIsNone(player._preview_samples)

    def test_rate_fallback_ends_silent_then_retries(self):
        player = self.player
        player.requested_rate = 96000
        data = np.ones((480, 2), dtype='float32')
        player.start_preview(data, 96000, self.settings, 'mixed')
        player.thread.join(2)
        self.assertEqual(player.error, '')
        self.assertEqual(player.preview_retry_rate, 48000)
        self.assertEqual(player.preview_seconds, 0)
        np.testing.assert_array_equal(player.samples(480), 0)
        player.start_preview(data, player.preview_retry_rate, self.settings, 'mixed')
        player.thread.join(2)
        self.assertIsNone(player.preview_retry_rate)
        self.assertEqual(player.preview_seconds, .01)
        self.assertGreater(player.take_output_peak(), 0)
