import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
import numpy as np
from phosphor.engine.composition import BEATS_PER_BAR, Note, new_project, render_song, song_seconds, export_song
from phosphor.engine.metronome import TapTempo, click_times, mix_clicks
from phosphor.engine.signal_engine import Settings
from phosphor.audio.audio_preview import AudioPreview
from phosphor.storage.app_files import DEFAULT_PREFERENCES, validated_preferences


class MetronomeTests(unittest.TestCase):
    def test_beat_positions_at_multiple_rates_and_tempos(self):
        for rate in (44100, 48000, 96000):
            for bpm in (120., 137., 400.):
                samples = np.arange(round(60 * rate / bpm * 9))
                age, beat = click_times(samples / rate, bpm, rate)
                expected = np.rint(np.arange(9) * (60 * rate / bpm)).astype(int)
                np.testing.assert_array_equal(np.flatnonzero(age == 0), expected)
                np.testing.assert_array_equal(beat[expected], np.arange(9))

    def test_wrap_seek_and_block_splits_follow_song_position(self):
        project = replace(new_project(), loop=True, loop_start=4., loop_end=8.)
        rate = 48000
        times = song_seconds(project, 3.99, np.arange(2400) / rate)
        age, beat = click_times(times, project.bpm, rate)
        self.assertEqual((age[480], beat[480]), (0, 4))
        signal = np.zeros((len(times), 2))
        whole = mix_clicks(signal, times, project.bpm, rate)
        split = np.concatenate([mix_clicks(s, t, project.bpm, rate)
                                for s, t in zip(np.split(signal, [487]), np.split(times, [487]))])
        np.testing.assert_array_equal(whole, split)
        np.testing.assert_array_equal(whole[:, 0], whole[:, 1])
        self.assertGreater(abs(whole[481, 0]), 0)
        # Seeking into a click reconstructs its tail without callback history.
        np.testing.assert_array_equal(mix_clicks(signal[500:], times[500:], project.bpm, rate), whole[500:])

    def test_bar_accent_pitch_level_and_duration(self):
        rate = 48000
        times = np.arange(1000) / rate
        accent = mix_clicks(np.zeros((1000, 2)), times, 120, rate)
        plain = mix_clicks(np.zeros((1000, 2)), times + .5, 120, rate)
        self.assertGreater(np.max(abs(accent)), np.max(abs(plain)))
        self.assertGreater(np.count_nonzero(np.diff(np.sign(accent[:600, 0]))),
                           np.count_nonzero(np.diff(np.sign(plain[:600, 0]))))
        np.testing.assert_array_equal(accent[720:], 0)
        np.testing.assert_array_equal(mix_clicks(accent, times, 120, rate, 0), accent)

    def test_count_in_silences_song_and_starts_at_playhead(self):
        player = AudioPreview()
        project = replace(new_project(), notes=(Note(0., 16., 57),))
        project = replace(project, clips=(replace(project.clips[0], mode='music'),))
        settings = Settings()
        player.parameters = (settings, 'drawing'); player.composer = (project, .5)
        player.count_in_seconds = float(project.seconds(BEATS_PER_BAR))
        rate = settings.sample_rate
        player.frames = round(player.count_in_seconds * rate) - 1000
        block = player._next_block(2000)
        np.testing.assert_array_equal(block[:1000], 0)  # Between pre-roll clicks.
        expected = np.clip(render_song(.5 + np.arange(1000) / rate, project, settings), -1, 1)
        self.assertGreater(np.max(abs(expected)), .05)
        np.testing.assert_allclose(block[1000:], expected, atol=1e-6)
        player.frames = 0
        self.assertGreater(np.max(abs(player._next_block(1000))), 0)  # Clicks even with toggle off.

    def test_exports_are_identical_with_monitoring_on_or_off(self):
        player = AudioPreview()
        project = replace(new_project(), notes=(Note(0., 1., 57),))
        project = replace(project, clips=(replace(project.clips[0], mode='music'),))
        settings = Settings()
        player.parameters = (settings, 'music'); player.composer = (project, 0.)
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / name for name in ('off.wav', 'on.wav')]
            monitoring = []
            expected = render_song(np.arange(1000) / settings.sample_rate, project, settings)
            for enabled, path in zip((False, True), paths):
                player.metronome = enabled
                player.count_in_seconds = 2. if enabled else 0.
                monitoring.append(player._next_block(1000))
                export_song(path, project, settings, end_beats=1.)
                np.testing.assert_array_equal(render_song(np.arange(1000) / settings.sample_rate, project, settings), expected)
            self.assertEqual(paths[0].read_bytes(), paths[1].read_bytes())
            self.assertFalse(np.array_equal(*monitoring))

    def test_preference_migration_and_bounds(self):
        old = dict(DEFAULT_PREFERENCES); del old['metronome_level']
        self.assertEqual(validated_preferences(old)['metronome_level'], .3)
        for value in (-.1, 1.1, float('nan'), True):
            with self.assertRaises(ValueError):
                validated_preferences(dict(DEFAULT_PREFERENCES, metronome_level=value))


class TapTempoTests(unittest.TestCase):
    def test_average_last_four_taps_and_idle_reset(self):
        clock = iter((0., .5, 1., 1.75, 2.5, 5., 5.5))
        tap = TapTempo(lambda: next(clock))
        self.assertIsNone(tap.tap())
        self.assertEqual(tap.tap(), 120.)
        self.assertEqual(tap.tap(), 120.)
        self.assertAlmostEqual(tap.tap(), 180 / 1.75)
        self.assertEqual(tap.tap(), 90.)  # Only .5, 1, 1.75, 2.5 remain.
        self.assertIsNone(tap.tap())
        self.assertEqual(tap.tap(), 120.)

    def test_clamp_and_nonincreasing_clock(self):
        clock = iter((0., .01, .01, -1.))
        tap = TapTempo(lambda: next(clock))
        self.assertIsNone(tap.tap())
        self.assertEqual(tap.tap(), 400.)
        self.assertIsNone(tap.tap())
        self.assertIsNone(tap.tap())
        # Idle reset makes intervals over 2 s start a fresh estimate (minimum 30 BPM).
        clock = iter((0., 2., 4.0001))
        tap = TapTempo(lambda: next(clock))
        tap.tap(); self.assertEqual(tap.tap(), 30.); self.assertIsNone(tap.tap())
