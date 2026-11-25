"""Audio format contracts, legacy PCM conversion and time-based rendering."""
import tempfile
import unittest
import wave
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from phosphor.storage import audio_writer as aw
from phosphor.storage.app_files import DEFAULT_PREFERENCES, validated_preferences
from phosphor.engine.composition import Project, Clip, Note, render_song, export_song
from phosphor.engine.signal_engine import Settings, Shape, export_wav, render


class AudioWriterTests(unittest.TestCase):
    def test_all_formats_depths_rates_roundtrip(self):
        if aw.soundfile is None: self.skipTest('soundfile required for round-trip reads')
        with tempfile.TemporaryDirectory() as folder:
            for format in ('WAV', 'FLAC'):
                for depth in ((16, 24, 32) if format == 'WAV' else (16, 24)):
                    for rate in aw.RATES:
                        with self.subTest(format=format, depth=depth, rate=rate):
                            count = round(1.25 * rate)
                            samples = np.column_stack((np.sin(np.arange(count)*.13)*1.2,
                                                       np.cos(np.arange(count)*.19)*.7))
                            path = Path(folder) / ('test.' + format.lower())
                            with aw.open(path, rate, format, depth) as writer:
                                for start in range(0, count, rate): writer.write(samples[start:start+rate])
                            data, actual_rate = aw.soundfile.read(path, dtype='float64')
                            self.assertEqual(actual_rate, rate); self.assertEqual(data.shape, (count, 2))
                            self.assertEqual(aw.soundfile.info(path).subtype, 'FLOAT' if depth == 32 else f'PCM_{depth}')
                            expected = samples if depth == 32 else aw.integer_samples(samples, depth) / 2**(depth-1)
                            np.testing.assert_allclose(data, expected, atol=6e-8)

    def test_fallback_integer_wav_matches_native_and_legacy(self):
        with tempfile.TemporaryDirectory() as folder:
            samples = np.array([[-2., 2.], [-1., 1.], [-.12345, .54321], [0., 0.]])
            for depth in (16, 24):
                path = Path(folder) / f'fallback{depth}.wav'
                with patch.object(aw, 'soundfile', None):
                    with aw.open(path, 48000, 'WAV', depth) as writer: writer.write(samples)
                with wave.open(str(path)) as reader:
                    raw = reader.readframes(len(samples))
                    self.assertEqual(reader.getsampwidth(), depth//8)
                if depth == 16:
                    self.assertEqual(raw, np.rint(np.clip(samples, -1, 1)*32767).astype('<i2').tobytes())
                if aw.soundfile is not None:
                    native = Path(folder) / f'native{depth}.wav'
                    with aw.open(native, 48000, 'WAV', depth) as writer: writer.write(samples)
                    with wave.open(str(native)) as reader: self.assertEqual(raw, reader.readframes(len(samples)))
                    read, _ = aw.soundfile.read(path)
                    np.testing.assert_array_equal(read, aw.integer_samples(samples, depth)/2**(depth-1))

    def test_invalid_and_unavailable_choices(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'test.wav'
            for options in (('FLAC', 32, 48000), ('MP3', 16, 48000), ('WAV', 16, 12345)):
                with self.assertRaises(ValueError): aw.open(path, options[2], options[0], options[1])
            with patch.object(aw, 'soundfile', None):
                for format, depth in (('FLAC', 16), ('WAV', 32)):
                    with self.assertRaises(ValueError): aw.open(path, 48000, format, depth)
            self.assertFalse(path.exists())

    def test_old_preferences_and_export_validation(self):
        old = {key: value for key, value in DEFAULT_PREFERENCES.items() if not key.startswith('export_')}
        self.assertEqual(validated_preferences(old), DEFAULT_PREFERENCES)
        for key, value in (('export_rate', 12), ('export_range', 'selection'), ('export_depth', 8)):
            with self.assertRaises(ValueError): validated_preferences(dict(DEFAULT_PREFERENCES, **{key: value}))

    def test_render_song_shared_sample_times(self):
        settings = replace(Settings(), shapes=(Shape(),), corner_dwell_us=120., drawing_hz=73.)
        project = Project(notes=(Note(0., 2., 57), Note(2.1, 1., 64)),
                          clips=(Clip(0., 4., settings),))
        low = render_song(np.arange(96000)/48000, project, settings)
        high = render_song(np.arange(192000)/96000, project, replace(settings, sample_rate=96000))
        np.testing.assert_allclose(low, high[::2], atol=1e-12)
        # Noise reads the same 48 kHz table at shared sample times too.
        from phosphor.engine.transforms import new_transform, NOISE
        settings = replace(settings, shapes=(Shape(transforms=(new_transform(NOISE),)),))
        project = replace(project, clips=(Clip(0., 4., settings),))
        low = render_song(np.arange(9600)/48000, project, settings)
        high = render_song(np.arange(19200)/96000, project, replace(settings, sample_rate=96000))
        np.testing.assert_allclose(low, high[::2], atol=1e-12)

    def test_exporters_rate_span_source_and_legacy_pcm(self):
        settings = replace(Settings(), shapes=(Shape(),))
        project = Project(notes=(Note(0., 4., 57),), clips=(Clip(0., 4., settings),))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'song.wav'
            export_song(path, project, settings, start_beats=1., end_beats=1.25, rate=96000)
            with wave.open(str(path)) as reader:
                self.assertEqual(reader.getframerate(), 96000); self.assertEqual(reader.getnframes(), 12000)
            for source_only in (False, True):
                export_wav(path, settings, .025, source_only=source_only)
                times = np.arange(1200)/48000
                from phosphor.engine.signal_engine import source_components
                expected = source_components(times, settings)[0] if source_only else render(times, settings)[2]
                with wave.open(str(path)) as reader:
                    self.assertEqual(reader.readframes(1200), np.rint(np.clip(expected, -1, 1)*32767).astype('<i2').tobytes())

    def test_exporters_each_format_depth_rate(self):
        if aw.soundfile is None: self.skipTest('soundfile required for round-trip reads')
        settings = replace(Settings(), shapes=(Shape(),))
        project = Project(notes=(Note(0., 4., 57),), clips=(Clip(0., 4., settings),))
        with tempfile.TemporaryDirectory() as folder:
            for format in ('WAV', 'FLAC'):
                for depth in ((16, 24, 32) if format == 'WAV' else (16, 24)):
                    for rate in aw.RATES:
                        for source_only in (False, True):
                            with self.subTest(format=format, depth=depth, rate=rate, source=source_only):
                                path = Path(folder)/('export.'+format.lower())
                                export_wav(path, settings, .02, source_only=source_only,
                                           format=format, depth=depth, rate=rate)
                                info = aw.soundfile.info(path)
                                self.assertEqual(info.frames, round(.02*rate)); self.assertEqual(info.samplerate, rate)
                                export_song(path, project, settings, end_beats=.04, source_only=source_only,
                                            format=format, depth=depth, rate=rate)
                                info = aw.soundfile.info(path)
                                self.assertEqual(info.frames, round(.02*rate)); self.assertEqual(info.samplerate, rate)


if __name__ == '__main__': unittest.main()
