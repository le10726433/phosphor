"""Immutable roll tools: boundaries, stable selection and musical timing."""
from dataclasses import replace
import random
import unittest

from phosphor.engine.composition import Note, Project, MAX_BEATS, MAX_NOTES, validate_project
from phosphor.engine import note_tools as t


class NoteToolsTests(unittest.TestCase):
    def test_velocity_set_shift_and_validation(self):
        notes = (Note(0, 1, 60, .2), Note(1, 1, 62, .8), Note(2, 1, 64, .6))
        result, selected = t.set_velocity(notes, (0, 2), 2.)
        self.assertEqual([n.velocity for n in result], [1., .8, 1.]); self.assertEqual(selected, (0, 2))
        result, _ = t.shift_velocity(notes, (0, 1), -.5)
        self.assertEqual(result[0].velocity, 0.); self.assertAlmostEqual(result[1].velocity, .3)
        self.assertEqual(result[2], notes[2])
        for bad in (True, None, float('nan'), float('inf')):
            with self.assertRaises(ValueError): t.set_velocity(notes, (0,), bad)
            with self.assertRaises(ValueError): t.shift_velocity(notes, (0,), bad)
            with self.assertRaises(ValueError): t.ramp_velocity(notes, (0,), 0, bad)
        with self.assertRaises(ValueError): t.set_velocity(notes, (3,), .5)

    def test_velocity_line_and_ramp(self):
        notes = tuple(Note(beat, 1, 60) for beat in (0., 1., 1., 2., 3.))
        result, selected = t.velocity_line(notes, range(5), (2., .8), (0., .2))
        self.assertEqual(selected, tuple(range(5)))
        for actual, expected in zip(result, (.2, .5, .5, .8, 1.)):
            self.assertAlmostEqual(actual.velocity, expected)
        result, _ = t.velocity_line(notes, range(5), (1., .2), (1., .7))
        self.assertEqual([n.velocity for n in result], [1., .7, .7, 1., 1.])
        result, _ = t.ramp_velocity(notes, (1, 2), .3, .9)
        self.assertEqual([n.velocity for n in result], [1., .3, .3, 1., 1.])
        result, _ = t.ramp_velocity(notes, (0, 1, 2, 3), -1., 2.)
        self.assertEqual([n.velocity for n in result], [0., .5, .5, 1., 1.])
        self.assertEqual(t.ramp_velocity(notes, (), .2, .8), (notes, ()))

    def setUp(self):
        self.notes = (Note(1., 1., 60, .3, .5), Note(1., 1., 64, .6), Note(1., 1., 67, .9))

    def test_chords(self):
        self.assertEqual(len(t.CHORD_TYPES), 11)
        self.assertEqual(t.chord_pitches(60, 'major', 1), (64, 67, 72))
        self.assertEqual(t.chord_pitches(60, 'major', 2), (67, 72, 76))
        self.assertEqual(t.chord_pitches(60, '9', 4), (72, 74, 76, 79, 82))
        for kind, offsets in t.CHORD_TYPES.items():
            self.assertEqual(t.chord_pitches(60, kind), tuple(60 + i for i in offsets))
        for args in ((60, 'bad', 0), (60, 'major', 3), (60, 'major', -1)):
            with self.assertRaises(ValueError): t.chord_pitches(*args)

    def test_stamp_drops_and_appends(self):
        result, picked = t.stamp_chord(self.notes, -1., 0., 105, 'major')
        self.assertEqual(picked, (3,)); self.assertEqual(result[:3], self.notes)
        self.assertEqual(result[3], Note(0., t.MIN_LENGTH, 105))
        self.assertEqual(t.stamp_chord((), 0., 1., 10, 'major'), ((), ()))
        with self.assertRaises(ValueError): t.stamp_chord((self.notes[0],) * MAX_NOTES, 0, 1, 60, 'major')

    def test_strum(self):
        result, picked = t.strum(self.notes, (2, 0, 1), .125, 'up')
        self.assertEqual(picked, (0, 1, 2))
        self.assertEqual([n.start for n in result], [1., 1.125, 1.25])
        self.assertEqual([n.end for n in result], [2.] * 3)
        result, _ = t.strum(self.notes, (0, 1, 2), 4, 'down')
        self.assertEqual(result[2], self.notes[2]); self.assertEqual(result[0].length, t.MIN_LENGTH)
        self.assertEqual(result[0].glide, t.MIN_LENGTH)

    def test_group_tolerance(self):
        notes = (Note(0., 1., 60), Note(.0000005, 1., 64), Note(.1, 1., 67))
        result, _ = t.strum(notes, range(3), .1, 'up')
        self.assertAlmostEqual(result[1].start, .1000005)
        self.assertEqual(result[2], notes[2])

    def test_arpeggio_patterns(self):
        for count, expected in ((1, [60]*6), (2, [60, 64]*3), (3, [60, 64, 67, 64, 60, 64])):
            notes = tuple(replace(n, length=1.5) for n in self.notes[:count])
            result, picked = t.arpeggiate(notes, range(count), 'up-down', .25)
            self.assertEqual([n.pitch for n in result], expected)
            self.assertEqual(picked, tuple(range(6)))
            self.assertTrue(all(n.glide == 0 for n in result))
        for pattern, expected in (('up', [60, 64, 67, 60]), ('down', [67, 64, 60, 67])):
            result, _ = t.arpeggiate(self.notes, range(3), pattern, .25)
            self.assertEqual([n.pitch for n in result], expected)
            self.assertEqual(result[1].velocity, .6)

    def test_arpeggio_remainder_and_rest(self):
        notes = (Note(3., .6, 70), *self.notes)
        result, picked = t.arpeggiate(notes, (1, 2, 3), 'up', .3)
        self.assertIs(result[0], notes[0]); self.assertEqual(picked, (1, 2, 3, 4))
        self.assertAlmostEqual(result[-1].length, .1)
        with self.assertRaises(ValueError): t.arpeggiate(self.notes, range(3), 'up', 0.)

    def test_arpeggio_short_chord_fits_each_pattern(self):
        notes, _ = t.stamp_chord((), 0., 1., 60, 'major')
        for pattern, expected in (('up', [60, 64, 67, 60]),
                                  ('down', [67, 64, 60, 67]),
                                  ('up-down', [60, 64, 67, 64])):
            with self.subTest(pattern=pattern):
                result, picked = t.arpeggiate(notes, range(3), pattern, 1.)
                self.assertEqual([n.pitch for n in result], expected)
                self.assertEqual([n.start for n in result], [0., .25, .5, .75])
                self.assertEqual([n.length for n in result], [.25] * 4)
                self.assertEqual(picked, (0, 1, 2, 3))

    def test_arpeggio_step_is_per_group_and_respects_minimum(self):
        short = tuple(replace(n, start=0.) for n in self.notes)
        long = tuple(replace(n, start=2., length=4.) for n in self.notes)
        result, _ = t.arpeggiate((*short, *long), range(6), 'up', 1.)
        self.assertEqual([n.start for n in result], [0., .25, .5, .75, 2., 3., 4., 5.])
        tiny = tuple(replace(n, start=0., length=t.MIN_LENGTH) for n in self.notes)
        result, _ = t.arpeggiate(tiny, range(3), 'up', 1.)
        self.assertEqual(result, (replace(tiny[0], glide=0.),))

    def test_quantize_strength(self):
        notes = (Note(.37, .48, 60, glide=.4),)
        self.assertEqual(t.quantize(notes, (0,), .25, 0)[0], notes)
        result, _ = t.quantize(notes, (0,), .25, .5)
        self.assertAlmostEqual(result[0].start, .31); self.assertEqual(result[0].length, .48)
        result, _ = t.quantize(notes, (0,), .25, 1., ends=True)
        self.assertAlmostEqual(result[0].start, .25); self.assertAlmostEqual(result[0].end, .75)

    def test_quantize_swing_and_minimum(self):
        result, _ = t.quantize((Note(.26, .02, 60, glide=.02),), (0,), .25, swing=1., ends=True)
        self.assertEqual(result[0].start, .375)
        self.assertEqual(result[0].length, t.MIN_LENGTH); self.assertEqual(result[0].glide, t.MIN_LENGTH)
        # The next even grid point can be nearer than the delayed odd one.
        self.assertEqual(t.quantize((Note(.48, 1., 60),), (0,), .25, swing=1.)[0][0].start, .5)

    def test_humanize_seed_and_draw_order(self):
        a = t.humanize(self.notes, (2, 0), .02, .05, 1)
        self.assertEqual(a, t.humanize(self.notes, (0, 2), .02, .05, 1))
        self.assertNotEqual(a, t.humanize(self.notes, (0, 2), .02, .05, 2))
        rng = random.Random(1)
        self.assertEqual(a[0][0].start, 1. + rng.uniform(-.02, .02))
        self.assertEqual(a[0][0].velocity, .3 + rng.uniform(-.05, .05))
        self.assertIs(a[0][1], self.notes[1])
        result, _ = t.humanize((Note(0, 1, 60, 0), Note(MAX_BEATS, 1, 60, 1)), (0, 1), 2., 1., 1)
        validate_project(Project(notes=result))

    def test_duplicate_and_copy(self):
        notes = (Note(.1, .3, 60), Note(.5, .2, 64))
        result, picked = t.duplicate(notes, (0, 1), .25)
        self.assertEqual(picked, (2, 3)); self.assertEqual(result[:2], notes)
        self.assertAlmostEqual(result[2].start, .85)
        self.assertAlmostEqual(t.duplicate(notes, (0, 1), 0)[0][2].start, .7)
        result, picked = t.copy_moved(notes, (0,), -100, -5.)
        self.assertEqual(result[2], Note(0., .3, 21)); self.assertEqual(picked, (2,))
        self.assertEqual(t.copy_moved((Note(MAX_BEATS, 1, 108),), (0,), 12, 3)[0][-1], Note(MAX_BEATS, 1, 108))

    def test_split(self):
        result, picked = t.split(self.notes, (0, 2), 1.5)
        self.assertEqual(picked, (0, 2, 3, 4)); self.assertEqual(result[1], self.notes[1])
        self.assertEqual(result[0], Note(1., .5, 60, .3, .5))
        self.assertEqual(result[3], Note(1.5, .5, 60, .3, 0.))
        for beat in (1., 2., 1. + t.MIN_LENGTH):
            self.assertEqual(t.split(self.notes, (0,), beat)[0], self.notes)

    def test_join(self):
        notes = (Note(1, 1, 60, .3, .4), Note(0, 1, 60, .7, .2), Note(3, 1, 60), Note(0, 5, 64))
        result, picked = t.join(notes, range(4))
        self.assertEqual(result, (Note(0, 2, 60, .7, .2), notes[2], notes[3]))
        self.assertEqual(picked, (0, 1, 2))
        self.assertEqual(t.join((notes[0], notes[2]), (0, 1))[0], (notes[0], notes[2]))

    def test_legato(self):
        notes = (*self.notes, Note(4., 1., 72))
        result, picked = t.legato(notes, range(4))
        self.assertEqual([n.end for n in result], [4., 4., 4., 5.])
        self.assertEqual(picked, (0, 1, 2, 3))

    def test_scales(self):
        self.assertNotIn('Chromatic', t.ROLL_SCALES)
        self.assertTrue(t.in_scale(61, 'Off', 0)); self.assertFalse(t.in_scale(61, 'Major', 0))
        self.assertTrue(t.in_scale(61, 'Major', 1))
        self.assertEqual(t.snap_pitch(61, 'Major', 0), 60)
        self.assertEqual(t.snap_pitch(500, 'Major', 0), 108)
        self.assertEqual(t.step_in_scale(64, 1, 'Major', 0), 65)
        self.assertEqual(t.step_in_scale(60, -2, 'Major', 0), 57)
        self.assertEqual(t.step_in_scale(108, 2, 'Major', 0), 108)
        self.assertEqual(t.step_in_scale(60, 2, 'Off', 0), 62)
        with self.assertRaises(ValueError): t.in_scale(60, 'bad', 0)
        with self.assertRaises(ValueError): t.in_scale(60, 'Major', 12)

    def test_empty_and_bad_selections(self):
        for tool, args in ((t.strum, (.1, 'up')), (t.arpeggiate, ('up', .25)),
                           (t.quantize, (.25,)), (t.humanize, (.02, .05, 1)),
                           (t.duplicate, (.25,)), (t.copy_moved, (1, .25)),
                           (t.split, (1.5,)), (t.join, ()), (t.legato, ())):
            with self.subTest(tool=tool.__name__):
                self.assertEqual(tool(self.notes, (), *args), (self.notes, ()))
                for indices in ((-1,), (3,), (True,)):
                    with self.assertRaises(ValueError): tool(self.notes, indices, *args)

    def test_invalid_values(self):
        for tool, args in ((t.strum, (-1, 'up')), (t.strum, (.1, 'bad')),
                           (t.quantize, (0,)), (t.quantize, (.25, 2)),
                           (t.humanize, (float('nan'), .1, 1)), (t.duplicate, (-1,)),
                           (t.copy_moved, (.5, 0)), (t.split, (float('inf'),))):
            with self.subTest(tool=tool.__name__, args=args), self.assertRaises(ValueError): tool(self.notes, (0,), *args)

    def test_preferences_defaults_and_invalid(self):
        from phosphor.storage.app_files import DEFAULT_PREFERENCES, validated_preferences
        result = validated_preferences(dict(DEFAULT_PREFERENCES, roll_scale='bad', roll_scale_root=True,
                                            roll_hear_notes='yes', roll_scale_snap=1))
        for key in ('roll_scale', 'roll_scale_root', 'roll_scale_snap', 'roll_hear_notes'):
            self.assertEqual(result[key], DEFAULT_PREFERENCES[key])


if __name__ == '__main__': unittest.main()
