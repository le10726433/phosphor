"""Group movement preserves the single-note edit and playback rules."""
import unittest

from phosphor.engine.composition import Note, MAX_BEATS, note_line
from phosphor.engine.note_edit import move_notes


class NoteEditTests(unittest.TestCase):
    def test_subset_and_stable_indices(self):
        notes = (Note(2., 1., 60, .3), Note(4., 2., 64), Note(2., 1., 60, .3))
        moved, indices = move_notes(notes, {2, 0}, 12, .25)
        self.assertEqual(indices, (0, 2))
        self.assertEqual(moved[0], Note(2.25, 1., 72, .3))
        self.assertEqual(moved[2], moved[0])
        self.assertIs(moved[1], notes[1])
        self.assertEqual(notes[0], Note(2., 1., 60, .3))

    def test_clamps_and_empty_selection(self):
        notes = (Note(.25, 1., 22), Note(MAX_BEATS, 1., 107))
        moved, _ = move_notes(notes, {0, 1}, -12, -1.)
        self.assertEqual((moved[0].start, moved[0].pitch), (0., 21))
        moved, _ = move_notes(notes, {0, 1}, 12, 1.)
        self.assertEqual((moved[1].start, moved[1].pitch), (MAX_BEATS, 108))
        self.assertEqual(move_notes(notes, ()), (notes, ()))

    def test_overlap_uses_existing_last_note_priority(self):
        notes = (Note(0., 4., 60), Note(4., 1., 64))
        moved, indices = move_notes(notes, {1}, 1, -3.)
        self.assertEqual(indices, (1,))
        self.assertEqual(moved, (notes[0], Note(1., 1., 65)))
        starts, ends, pitches, owners = note_line(moved)
        self.assertEqual(list(zip(starts, ends, pitches, owners)),
                         [(0., 1., 60., 0.), (1., 2., 65., 1.), (2., 4., 60., 0.)])

    def test_invalid_arguments(self):
        notes = (Note(0., 1., 60),)
        for indices, semitones, beats in [({-1}, 0, 0), ({1}, 0, 0), ({True}, 0, 0),
                                         ({0}, .5, 0), ({0}, 0, float('nan'))]:
            with self.assertRaises(ValueError):
                move_notes(notes, indices, semitones, beats)
