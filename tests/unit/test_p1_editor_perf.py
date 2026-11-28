"""Unit tests for P1 chord lookup, voice cache, and static outline helpers."""
import unittest
import numpy as np
from phosphor.engine.composition import Note, chord_voices
from phosphor.engine.chord_names import sounding_pitches


class SoundingPitchesBinarySearchTests(unittest.TestCase):
    def _reference_sounding(self, voices, beat):
        mask = (voices.start <= beat) & (beat < voices.end) & (beat < voices.steal)
        return tuple(sorted(int(p) for p in voices.pitch[mask]))

    def test_matches_full_scan_with_chords_steals_and_glides(self):
        rng = np.random.default_rng(2026)
        notes = []
        # Dense overlapping notes (>8 simultaneous to force voice stealing) + glides
        for i in range(400):
            start = float(rng.integers(0, 60)) * 0.25
            length = float(rng.integers(1, 20)) * 0.25
            pitch = int(rng.integers(36, 84))
            glide = 0.25 if (i % 7 == 0 and length >= 0.5) else 0.0
            notes.append(Note(start=start, length=length, pitch=pitch, glide=glide))
        notes = tuple(notes)
        voices = chord_voices(notes)

        self.assertIsNotNone(voices.max_end)
        self.assertFalse(voices.max_end.flags.writeable)
        if len(voices.max_end) > 1:
            self.assertTrue(np.all(np.diff(voices.max_end) >= 0))

        # Test boundaries (exact starts, ends, steals) and random beats
        probe_beats = list(np.linspace(-1.0, 25.0, 200))
        probe_beats.extend(float(x) for x in voices.start[:40])
        probe_beats.extend(float(x) for x in voices.end[:40])
        finite_steals = voices.steal[np.isfinite(voices.steal)]
        probe_beats.extend(float(x) for x in finite_steals[:40])

        for beat in probe_beats:
            got = sounding_pitches(notes, beat, voices)
            expected = self._reference_sounding(voices, beat)
            self.assertEqual(got, expected, f'Mismatch at beat={beat}')

    def test_empty_notes(self):
        voices = chord_voices(())
        self.assertEqual(sounding_pitches((), 0.0, voices), ())
        self.assertEqual(sounding_pitches((), 5.0, voices), ())

    def test_chord_voices_cache_bounded(self):
        self.assertEqual(chord_voices.cache_info().maxsize, 16)


if __name__ == '__main__':
    unittest.main()
