"""Chord label ranking and the roll's audible-voice lookup."""
import unittest

from phosphor.engine.chord_names import chord_name, sounding_pitches
from phosphor.engine.composition import Note


class ChordNameTests(unittest.TestCase):
    def test_names(self):
        cases = (
            ((60, 64, 67), 'C'), ((57, 60, 64, 67), 'Am7'),
            ((60, 64, 67, 69), 'C6'), ((52, 55, 60), 'C/E'),
            ((55, 60, 62), 'Gsus4'), ((60, 62, 64, 67), 'Cadd9'),
            ((60, 64, 67, 74, 78), 'Cadd9(#11)'),
            ((60, 67), 'C5'), ((60, 64), 'C'), ((48, 60), ''), ((), ''),
            ((60, 64, 67, 71), 'Cmaj7'), ((59, 62, 65, 69), 'Bm7b5'),
            ((60, 64, 67, 72, 76, 79), 'C'),
            ((60, 61), ''),
        )
        for pitches, expected in cases:
            with self.subTest(pitches=pitches):
                self.assertEqual(chord_name(pitches), expected)
                self.assertEqual(chord_name(list(reversed(pitches))), expected)

    def test_extras(self):
        self.assertEqual(chord_name((60, 61, 64, 67, 70)), 'C7(b9)')
        self.assertEqual(chord_name((60, 61, 64, 67)), 'C(addb9)')

    def test_stolen_voice(self):
        notes = tuple(Note(0., 4., pitch) for pitch in range(60, 69))
        self.assertEqual(sounding_pitches(notes, -1), ())
        self.assertEqual(sounding_pitches(notes, 0), tuple(range(61, 69)))
        self.assertEqual(sounding_pitches(notes, 4), ())

    def test_glide_partner(self):
        notes = (Note(0., 4., 60), Note(1., 1., 62, glide=.5), Note(0., 4., 67))
        self.assertEqual(sounding_pitches(notes, .5), (60, 67))
        self.assertEqual(sounding_pitches(notes, 1), (62, 67))
        self.assertEqual(sounding_pitches(notes, 2), (60, 67))


    def test_every_pitch_set_has_a_name_or_none_without_raising(self):
        # 0.65.1: a perfect fifth beside an altered-fifth shape raised KeyError(7) in the
        # roll's paintEvent, which crashed playback on macOS.
        import itertools
        for size in range(2, 13):
            for combo in itertools.combinations(range(12), size):
                for low in (0, 1):  # Root position and one note lifted an octave.
                    pitches = [60 + c + (12 if i < low else 0) for i, c in enumerate(combo)]
                    self.assertIsInstance(chord_name(pitches), str)

    def test_perfect_fifth_beside_an_altered_fifth(self):
        self.assertEqual(chord_name((60, 63, 66, 67)), 'Cm(add#11)')
        self.assertEqual(chord_name((60, 64, 67, 68)), 'C(addb13)')
        self.assertEqual(chord_name((48, 51, 54, 55, 58)), 'Cm7(#11)')


if __name__ == '__main__': unittest.main()
