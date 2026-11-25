"""Cycle audio generation without PortAudio or an output device."""
import unittest
from types import SimpleNamespace

import numpy as np

from phosphor.audio.audio_preview import AudioPreview
from phosphor.engine.signal_engine import Settings


class AudioPreviewCycleTests(unittest.TestCase):
    def player(self):
        player = AudioPreview()
        player.parameters = (Settings(), 'drawing')
        player.cycle = True
        player.frames = 1000  # Test loop mechanics independently of the startup ramp.
        return player

    def block(self, player, count):
        result = player._next_block(count)
        player.frames += count
        return result

    def test_cyclic_reads_and_boundary_swap_within_block(self):
        player = self.player()
        first = np.repeat(np.arange(8, dtype='float32')[:, None]/10, 2, axis=1)
        second = np.full((11, 2), -.5, dtype='float32')
        player.set_cycle(first)
        first[:] = .9  # Publication owns its data.
        expected = np.repeat(np.arange(8, dtype='float32')[:, None]/10, 2, axis=1)
        np.testing.assert_array_equal(self.block(player, 19), expected[np.arange(19)%8])
        self.assertFalse(player.cycle_samples.flags.writeable)
        player.set_cycle(second)
        block = self.block(player, 28)
        np.testing.assert_array_equal(block[:5], expected[3:])
        np.testing.assert_array_equal(block[5:], -.5)
        np.testing.assert_array_equal(self.block(player, 25), -.5)

    def test_none_fades_at_boundary_across_blocks_then_new_loop(self):
        player = self.player()
        player.set_cycle(np.ones((8, 2), dtype='float32'))
        self.block(player, 3)
        player.set_cycle(None)
        block = self.block(player, 105)
        np.testing.assert_array_equal(block[:5], 1.)
        tail = self.block(player, 200)
        faded = np.concatenate((block[5:], tail))
        ramp = round(.005*player.sample_rate)
        np.testing.assert_allclose(faded[:ramp, 0], np.arange(ramp-1, -1, -1)/ramp,
                                   atol=1e-7)
        np.testing.assert_array_equal(faded[ramp:], 0.)
        self.assertIsNone(player.cycle_samples)
        self.assertFalse(player.stop_event.is_set())
        player.set_cycle(np.full((9, 2), .25, dtype='float32'))
        np.testing.assert_array_equal(self.block(player, 21), .25)

    def test_publication_owns_samples_and_latest_request_wins(self):
        player = self.player()
        original = np.full((8, 2), .25, dtype='float32')
        player.set_cycle(original)
        original[:] = 1.
        np.testing.assert_array_equal(self.block(player, 3), .25)
        player.set_cycle(np.full((9, 2), .5, dtype='float32'))
        player.set_cycle(np.full((10, 2), .75, dtype='float32'))
        block = self.block(player, 8)
        np.testing.assert_array_equal(block[:5], .25)
        np.testing.assert_array_equal(block[5:], .75)
        self.assertEqual(len(player.cycle_samples), 10)
        self.assertFalse(player.cycle_samples.flags.writeable)

    def test_stop_request_fades_and_startup_ramp_has_no_clicks(self):
        player = self.player()
        player.set_cycle(np.ones((8, 2), dtype='float32'))
        player.metronome = True
        player.count_in_seconds = 2.
        player.frames = 0
        block = self.block(player, 512)
        ramp = round(.005*player.sample_rate)
        np.testing.assert_allclose(block[:ramp, 0], np.linspace(0, 1, ramp), atol=1e-7)
        np.testing.assert_array_equal(block[ramp:], 1.)
        player._cycle_stop.set()
        block = self.block(player, 512)
        self.assertTrue(player.stop_event.is_set())
        self.assertEqual(block[ramp-1, 0], 0.)
        np.testing.assert_array_equal(block[ramp:], 0.)

    def test_prepare_and_stop_reset_cycle_reader_for_reuse(self):
        player = self.player()
        player.set_cycle(np.full((8, 2), .5, dtype='float32'))
        self.block(player, 3)
        player.set_cycle(None)
        self.block(player, 20)
        self.assertGreater(player._cycle_fade, 0)
        player.stop()
        self.assertFalse(player.cycle)
        self.assertIsNone(player.cycle_samples)
        player.cycle = True
        player.set_cycle(np.full((11, 2), .25, dtype='float32'))
        player._prepare(SimpleNamespace(latency=.1), Settings(), 'drawing', 512, '')
        self.assertFalse(player.stop_event.is_set())
        self.assertFalse(player._cycle_stop.is_set())
        self.assertEqual(player._cycle_index, 0)
        player.frames = 1000
        np.testing.assert_array_equal(self.block(player, 14), .25)
        self.assertEqual(player._cycle_index, 3)


if __name__ == '__main__':
    unittest.main()
