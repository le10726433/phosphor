"""Synthetic messages exercise take timing without ports or audio devices."""
from types import SimpleNamespace
import unittest

from phosphor.engine.composition import Project, Note, MAX_NOTES, MAX_BEATS, validate_project
from phosphor.engine.midi_recorder import MidiRecorder


def message(kind='note_on', note=60, velocity=100, channel=0):
    return SimpleNamespace(type=kind, note=note, velocity=velocity, channel=channel)


class MidiRecorderTests(unittest.TestCase):
    def test_beats_velocity_and_latency(self):
        take = MidiRecorder(Project(bpm=120), latency=.1)
        take.feed(message(), .6)
        take.feed(message('note_off'), 1.1)
        note, = take.stop(2.).notes
        self.assertAlmostEqual(note.start, 1.)
        self.assertAlmostEqual(note.length, 1.)
        self.assertAlmostEqual(note.velocity, 100 / 127)

    def test_quantise_and_zero_velocity_off(self):
        take = MidiRecorder(Project(), snap=.25)
        take.feed(message(), .14)
        take.feed(message(velocity=0), .39)
        self.assertEqual(take.stop(1.).notes, (Note(.25, .5, 60, 100 / 127),))

    def test_replace_overdub_and_monophonic_cut(self):
        original = Project(notes=(Note(0., 4., 50), Note(6., 1., 51)))
        for mode in ('replace', 'overdub'):
            take = MidiRecorder(original, mode)
            take.feed(message(), .5)
            result = take.stop(1.)
            self.assertEqual(result.notes[-1], original.notes[-1])
            self.assertEqual(len(result.notes), 2 if mode == 'replace' else 3)
            if mode == 'overdub': self.assertEqual(result.notes[0].length, 1.)
        self.assertEqual(original.notes[0].length, 4.)

    def test_new_note_cuts_held_and_old_release_is_ignored(self):
        take = MidiRecorder(Project())
        take.feed(message(), 0.)
        take.feed(message(note=64), .5)
        take.feed(message('note_off'), .6)
        result = take.stop(1.)
        self.assertEqual([(n.start, n.length, n.pitch) for n in result.notes], [(0., 1., 60), (1., 1., 64)])
        self.assertIs(take.stop(99.), result)

    def test_loop_wrap_and_held_split(self):
        take = MidiRecorder(Project(loop=True, loop_start=2., loop_end=4.))
        take.feed(message(), 1.5)  # Beat 3.
        take.feed(message('note_off'), 2.25)  # Beat 2.5 after wrap.
        take.feed(message(note=65), 2.5)  # Beat 3 after wrap.
        notes = take.stop(2.75).notes
        self.assertEqual([(n.start, n.length, n.pitch) for n in notes],
                         [(2., .5, 60), (3., .5, 65)])

    def test_wrapped_input_positions(self):
        take = MidiRecorder(Project(loop=True, loop_end=4.))
        take.feed(message(), 1.5)
        take.feed(message('note_off'), .25)
        self.assertEqual([(n.start, n.length) for n in take.stop(.5).notes], [(0., .5), (3., 1.)])

    def test_clamps_empty_and_limit(self):
        take = MidiRecorder(Project(), latency=.1)
        take.feed(message(note=0), 0.)
        take.feed(message(note=127), .5)
        result = take.stop(1.)
        self.assertEqual([n.pitch for n in result.notes], [21, 108])
        self.assertEqual(result.notes[0].start, 0.)
        take = MidiRecorder(Project())
        for i in range(MAX_NOTES + 2): take.feed(message(), i / 2)
        result = take.stop(MAX_NOTES)
        self.assertEqual(len(result.notes), MAX_NOTES)
        validate_project(result)
        original = Project(notes=(Note(0., 1., 60),))
        self.assertIs(MidiRecorder(original, 'replace').stop(3.), original)
        take = MidiRecorder(Project())
        take.feed(message(), MAX_BEATS * 2)
        self.assertFalse(take.stop(MAX_BEATS * 3).notes)

    def test_invalid_time(self):
        with self.assertRaises(ValueError): MidiRecorder(Project(), latency=-1.)
        with self.assertRaises(ValueError): MidiRecorder(Project()).feed(message(), float('nan'))

    def test_unrelated_overlaps_survive_overdub(self):
        original = Project(notes=(Note(0., 4., 50), Note(1., 1., 51)))
        take = MidiRecorder(original)
        take.feed(message(), 4.)
        self.assertEqual(take.stop(5.).notes[:2], original.notes)

    def test_full_roll_does_not_evict_existing_notes(self):
        original = Project(notes=tuple(Note(i * 2., 1., 50) for i in range(MAX_NOTES)))
        take = MidiRecorder(original)
        take.feed(message(), .5)
        self.assertIs(take.stop(1.), original)


if __name__ == '__main__': unittest.main()
