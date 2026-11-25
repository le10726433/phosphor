"""P7c: the output-rate signal preference, its Settings control, and playback = export at every rate."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from phosphor.storage.signal_preferences import (DEFAULT_SIGNAL_PREFERENCES, FOLLOW_DEVICE, OUTPUT_RATES,
                                                 validated_signal_preferences)
from phosphor.storage import audio_writer


class OutputRatePreferenceTests(unittest.TestCase):
    def test_choices_follow_device_by_default_and_cap_at_192k(self):
        self.assertEqual(DEFAULT_SIGNAL_PREFERENCES['output_rate'], FOLLOW_DEVICE)
        self.assertEqual(OUTPUT_RATES, (44100, 48000, 88200, 96000, 176400, 192000))
        self.assertEqual(max(OUTPUT_RATES), 192000)
        for rate in (FOLLOW_DEVICE, *OUTPUT_RATES):
            self.assertEqual(validated_signal_preferences(dict(DEFAULT_SIGNAL_PREFERENCES, output_rate=rate))['output_rate'], rate)
        for bad in (384000, 352800, 22050, 48000.0, '48000', True, None, -1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validated_signal_preferences(dict(DEFAULT_SIGNAL_PREFERENCES, output_rate=bad))

    def test_files_saved_before_0_77_follow_the_device(self):
        older = {key: value for key, value in DEFAULT_SIGNAL_PREFERENCES.items() if key != 'output_rate'}
        self.assertEqual(validated_signal_preferences(older)['output_rate'], FOLLOW_DEVICE)
        self.assertEqual(validated_signal_preferences(dict(corner_dwell_us=5.))['output_rate'], FOLLOW_DEVICE)

    def test_export_offers_the_same_rates(self):
        self.assertEqual(audio_writer.RATES, OUTPUT_RATES)
        for rate in OUTPUT_RATES: audio_writer.validate_options('WAV', 24, rate)
        with self.assertRaises(ValueError): audio_writer.validate_options('WAV', 24, 384000)

    def test_settings_control_lists_the_rates_and_round_trips(self):
        from PySide6.QtWidgets import QApplication
        from phosphor.editor.preference_panels import SignalOutputEditor
        from phosphor.editor.qt_app import SliderInput
        app = QApplication.instance() or QApplication([])
        editor = SignalOutputEditor(SliderInput, DEFAULT_SIGNAL_PREFERENCES)
        self.assertEqual([editor.rate.itemText(i) for i in range(editor.rate.count())],
                         ['Follow device', '44.1 kHz', '48 kHz', '88.2 kHz', '96 kHz', '176.4 kHz', '192 kHz'])
        self.assertEqual(editor.values()['output_rate'], FOLLOW_DEVICE)
        seen = []
        editor.changed.connect(seen.append)
        editor.rate.setCurrentIndex(editor.rate.findData(176400))
        self.assertEqual(seen[-1]['output_rate'], 176400)
        self.assertEqual(validated_signal_preferences(editor.values())['output_rate'], 176400)
        editor.load(dict(DEFAULT_SIGNAL_PREFERENCES, output_rate=88200))
        self.assertEqual((editor.values()['output_rate'], len(seen)), (88200, 1))  # load() does not echo.
        editor.deleteLater(); app.processEvents()


class PlaybackMatchesExportTests(unittest.TestCase):
    def test_playback_blocks_equal_the_export_at_each_rate(self):
        if audio_writer.soundfile is None: self.skipTest('soundfile required to read the export back')
        from phosphor.audio.audio_preview import AudioPreview, playback_block_frames
        from phosphor.engine.composition import Project, Note, Clip, export_song
        from phosphor.engine.signal_engine import Settings, Shape
        settings = replace(Settings(), shapes=(Shape(),))
        project = Project(notes=(Note(0., 2., 57), Note(.5, 1., 64)), clips=(Clip(0., 4., settings),))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'song.wav'
            for rate in (44100, 88200, 176400, 192000):
                with self.subTest(rate=rate):
                    blocks = 6
                    frames = playback_block_frames(rate)
                    export_song(path, project, settings, end_beats=blocks * frames / rate * 2, format='WAV', depth=32, rate=rate)
                    exported = audio_writer.soundfile.read(str(path), dtype='float32')[0]
                    player = AudioPreview(); player.sample_rate = rate
                    player.composer = (project, 0.)
                    player.parameters = (settings, 'mixed')
                    played = []
                    for _ in range(blocks):
                        block = player._next_block(frames); player._publish_block(block, 0., False); played.append(block)
                    played = np.concatenate(played)
                    self.assertGreater(np.abs(played).max(), .05)
                    skip = round(.005 * rate)  # Playback's start ramp.
                    np.testing.assert_allclose(played[skip:], exported[skip:len(played)], atol=1e-6)


if __name__ == '__main__':
    unittest.main()
