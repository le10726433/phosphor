"""Chord allocation, mono identity and device-free integration contracts."""
from dataclasses import replace
import unittest

import numpy as np

from phosphor.storage.project_files import PROJECT_VERSION
from phosphor.engine.composition import (Project, Note, Clip, NoteSource, ChordSource,
    chord_voices, note_line, render_song, render_chords, cycle_span, song_cycle,
    new_project, validate_project, STEAL_FADE_SECONDS, CHORD_CYCLE_TOLERANCE)
from phosphor.engine.signal_engine import Settings
from phosphor.engine.midi_recorder import MidiRecorder
from phosphor.engine.midi_notes import read_midi
from tests.example_midi import example_midi
from phosphor.storage.project_files import project_document, project_from_document
from phosphor.storage.project_files import PROJECT_VERSION
from phosphor.editor.history import describe
from tests.unit.golden_scenes import scene, WINDOW_STARTS, RATE
from tests.unit.test_midi_recorder import message


def project(notes, **kwargs):
    return Project(notes=tuple(notes), clips=(Clip(0., 16., scene('travel'), 'mixed'),),
                   chords=True, **kwargs)


class ChordTests(unittest.TestCase):
    def test_defaults_validation_history(self):
        self.assertFalse(Project().chords)
        self.assertTrue(new_project().chords)
        for value in (0, 1, 'on', None):
            with self.assertRaisesRegex(ValueError, 'Chords must be on or off'):
                validate_project(replace(Project(), chords=value))
        self.assertEqual(describe(Project(), replace(Project(), chords=True)), 'Chords on')
        self.assertEqual(describe(replace(Project(), chords=True), Project()), 'Chords off')

    def test_project_version_six(self):
        for on in (False, True):
            p = replace(new_project(), chords=on)
            d = project_document(p)
            self.assertEqual(d['version'], PROJECT_VERSION)
            self.assertIs(d['chords'], on)
            self.assertEqual(project_from_document(d), p)
            del d['chords']
            with self.assertRaises(ValueError): project_from_document(d)
            d['version'] = 3
            del d['velocity']
            self.assertFalse(project_from_document(d).chords)
            d['chords'] = False
            with self.assertRaises(ValueError): project_from_document(d)
        for version in (1, 2):
            d = project_document(Project()); d['version'] = version; del d['chords']; del d['velocity']
            self.assertFalse(project_from_document(d).chords)
            d['chords'] = False
            with self.assertRaises(ValueError): project_from_document(d)

    def assert_mono_identity(self, p):
        mono = replace(p, chords=False); poly = replace(p, chords=True)
        voices = chord_voices(p.notes)
        for actual, expected in zip((voices.start, voices.end, voices.pitch, voices.owner), note_line(p.notes)):
            np.testing.assert_array_equal(actual, expected)
            self.assertFalse(actual.flags.writeable)
        self.assertEqual(len(ChordSource(poly).lanes), 1)
        times = np.unique(np.r_[np.arange(0., float(p.seconds(p.end_beats)), 1/12000),
                                   p.seconds([n.start for n in p.notes])])
        for mode in ('drawing', 'mixed', 'music'):
            a = replace(mono, clips=tuple(replace(c, mode=mode) for c in mono.clips))
            b = replace(poly, clips=tuple(replace(c, mode=mode) for c in poly.clips))
            np.testing.assert_array_equal(render_song(times, a, scene('travel')),
                                          render_song(times, b, scene('travel')))
        np.testing.assert_array_equal(render_song(times, mono, Settings(), True),
                                      render_song(times, poly, Settings(), True))
        for t in (.1, .75, 1.8, 2.2, 3.1):
            self.assertEqual(cycle_span(mono, t), cycle_span(poly, t))

    def test_golden_composer_identity(self):
        p = scene('composer')
        for start in WINDOW_STARTS:
            times = start + np.arange(128)/RATE
            for drawing in (False, True):
                a = replace(p, clips=tuple(replace(c, mode='drawing') if drawing else c for c in p.clips))
                np.testing.assert_array_equal(render_song(times, a, Settings()),
                    render_song(times, replace(a, chords=True), Settings()))
        self.assert_mono_identity(p)

    def test_nonoverlap_glide_tempo_travel_identity(self):
        self.assert_mono_identity(project((Note(0., 2., 57), Note(2., 2., 64, glide=.5),
                                          Note(5., 2., 60)), tempo_changes=((3., 100.),)))

    def test_legato_overlap_and_resume_identity(self):
        self.assert_mono_identity(project((Note(0., 2., 57), Note(1.5, 1.5, 64, glide=.5))))
        self.assert_mono_identity(project((Note(0., 6., 57), Note(1.5, 1.5, 64, glide=.5))))

    def test_mix_centers_and_zero_attack(self):
        p = project((Note(0., 8., 60), Note(1., 6., 67)))
        times = np.array([.7, .8, 1., 1.2])
        settings = scene('travel')
        drawing, music, _ = render_chords(times, settings, ChordSource(p), times)
        singles = [render_chords(times, settings, ChordSource(replace(p, notes=(n,))), times)
                   for n in p.notes]
        np.testing.assert_allclose(drawing, (singles[0][0]+singles[1][0])/2, atol=1e-12, rtol=0)
        np.testing.assert_allclose(music, (singles[0][1]+singles[1][1])/2, atol=1e-12, rtol=0)
        t = np.array([.5])  # The second note's envelope is exactly zero at its onset.
        np.testing.assert_allclose(render_song(t, p, settings),
            render_song(t, replace(p, notes=p.notes[:1]), settings), atol=1e-12, rtol=0)

    def test_voice_limit_fade_and_no_resume(self):
        notes = tuple(Note(0., 8., pitch) for pitch in (48, 52, 55, 60, 64, 67, 72, 76)) + (Note(2., 4., 79),)
        p = project(notes)
        a = chord_voices(notes)
        self.assertEqual(a.dropped, ((0, 2.),))
        self.assertEqual(a.steal[0], 2.)
        times = 1. + np.array([0., .001, .0025, .004, .005, .006])
        source = ChordSource(p)
        mask, _, _, env, gain = source.voices(times)[0]
        self.assertIsNone(gain)
        np.testing.assert_allclose(env[mask], [1., .8, .5, .2], atol=1e-12)
        self.assertFalse(mask[-1])
        self.assertLessEqual(sum(v[0][-1] for v in source.voices(times)), 8)
        # A muted partner may resume, but a stolen owner may never do so.
        p = project((Note(0., 10., 48), Note(1., 2., 60, glide=.5),
                     *(Note(2., 8., pitch) for pitch in range(65, 74))))
        a = chord_voices(p.notes)
        for note, beat in a.dropped:
            self.assertFalse(np.any((a.note == note) & (a.start > beat)))

    def test_oldest_owner_not_lowest_pitch_is_stolen(self):
        p = project((Note(0., 10., 76), *(Note(1., 8., pitch) for pitch in range(50, 57)), Note(2., 4., 80)))
        self.assertEqual(chord_voices(p.notes).dropped, ((0, 2.),))

    def test_glide_pairing_and_fresh_phase(self):
        p = project(tuple(Note(0., 4., pitch) for pitch in (60, 64, 67)) +
                    tuple(Note(4., 4., pitch, glide=1.) for pitch in (57, 60, 64)) + (Note(4., 4., 69),))
        a = chord_voices(p.notes)
        partners = dict(zip(a.note, a.partner))
        self.assertEqual(partners, {0: -1, 1: -1, 2: -1, 3: 2, 4: 0, 5: 1, 6: -1})
        source = ChordSource(p)
        for lane in source.lanes:
            for j, start in enumerate(lane.line[0]):
                if start != 4.: continue
                pitch = lane.line[2][j]; owner = next(i for i, n in enumerate(p.notes) if n.start == 4 and n.pitch == pitch)
                f = lane.frequency_at(np.array([2., 2.5]))
                target = 440.*2.**((pitch-69)/12)
                self.assertAlmostEqual(f[1], target)
                if partners[owner] >= 0:
                    self.assertAlmostEqual(f[0], 440.*2.**((p.notes[partners[owner]].pitch-69)/12))
                else: self.assertEqual(lane.at(np.array([2.]))[1][0], 0.)

    def test_cycle_common_period_and_fallback(self):
        for pitches in ((60, 64, 67), (60, 61)):
            p = project(Note(0., 8., pitch) for pitch in pitches)
            start, end = cycle_span(p, .75)
            f = 440.*2.**((60-69)/12)
            k = round((end-start)*f)
            self.assertAlmostEqual((end-start)*f, k)
            if len(pitches) == 3:
                self.assertGreater(k, 1); self.assertLessEqual(k, 8)
                for pitch in pitches:
                    advance = (end-start)*440.*2.**((pitch-69)/12)
                    self.assertLessEqual(abs(advance-round(advance)), CHORD_CYCLE_TOLERANCE)
            else: self.assertEqual(k, 1)
            samples, period, reason = song_cycle(p, .75, Settings())
            self.assertEqual(reason, ''); self.assertGreater(len(samples), 0)

    def test_record_overdub_replace_and_override(self):
        original = project((Note(0., 8., 50), Note(10., 1., 51)))
        for mode in ('overdub', 'replace'):
            take = MidiRecorder(original, mode)
            take.feed(message(note=60), .5); take.feed(message(note=64), .75)
            take.feed(message('note_off', note=60), 1.)
            take.feed(message('note_off', note=64), 1.25)
            result = take.stop(2.)
            recorded = [n for n in result.notes if n.pitch in (60, 64)]
            self.assertEqual([(n.start, n.length) for n in recorded], [(1., 1.), (1.5, 1.)])
            self.assertEqual(original.notes[0] in result.notes, mode == 'overdub')
            self.assertIn(original.notes[1], result.notes)
        take = MidiRecorder(original, chords=False)
        take.feed(message(), .5); take.feed(message(note=64), .75)
        self.assertEqual(take.stop(1.).notes[0].length, 1.)

    def test_record_loop_retrigger_and_reservation(self):
        p = project((), loop=True, loop_start=2., loop_end=4.)
        take = MidiRecorder(p)
        take.feed(message(), 1.5); take.feed(message(note=64), 1.6)
        take.feed(message('note_off'), 2.25)
        notes = take.stop(2.5).notes
        np.testing.assert_allclose([(n.start, n.length, n.pitch) for n in notes],
                                   [(2., .5, 60), (2., 1., 64), (3., 1., 60), (3.2, .8, 64)])
        take = MidiRecorder(project(()))
        take.feed(message(), 0.); take.feed(message(), .5)
        self.assertEqual(len(take.stop(1.).notes), 2)

    def test_midi_import_keeps_overlaps(self):
        score = example_midi()
        chords = read_midi(score, chords=True); melody = read_midi(score)
        self.assertGreaterEqual(len(chords.notes), len(melody.notes))
        self.assertTrue(any(b.start < a.end for a, b in zip(chords.notes, chords.notes[1:])))
        self.assertEqual(chords.notes, tuple(sorted(chords.notes, key=lambda n: (n.start, n.pitch))))

    def test_held_pose_and_redraw_fallback_identity(self):
        p = project((Note(0., 8., 60),))
        p = replace(p, clips=(replace(p.clips[0], time_offset=-2.),))
        times = np.arange(0., 2., 1/4800)
        np.testing.assert_array_equal(render_song(times, p, scene('travel')),
                                      render_song(times, replace(p, chords=False), scene('travel')))
        from unittest.mock import patch
        from phosphor.engine import composition as c
        original = c.shape_parts
        seen = []
        def limited(times, phase, envelope, settings, frequency=None):
            seen.append(settings.shape_redraw_count)
            if settings.shape_redraw_count > 1: raise ValueError('Path detail limit')
            return original(times, phase, envelope, settings, frequency)
        with patch.object(c, 'shape_parts', limited):
            result = render_song(times, p, scene('travel'))
        self.assertIn(2, seen); self.assertIn(1, seen)
        np.testing.assert_array_equal(result, render_song(times, p, replace(scene('travel'), shape_redraw_count=1)))

    def test_deterministic_pairing_ties_and_one_claim(self):
        p = project((Note(0., 4., 60), Note(0., 4., 64),
                     Note(4., 2., 62, glide=.5), Note(4., 2., 65, glide=.5),
                     Note(4., 2., 67, glide=.5)))
        a = chord_voices(p.notes)
        self.assertEqual(dict(zip(a.note, a.partner)), {0: -1, 1: -1, 2: 0, 3: 1, 4: -1})
        self.assertIs(chord_voices(p.notes), a)
        for field in ('start', 'end', 'pitch', 'owner', 'note', 'steal', 'partner'):
            self.assertFalse(getattr(a, field).flags.writeable)

    def test_stolen_glider_partner_resumes_at_natural_end(self):
        p = project((Note(0., 10., 48), Note(1., 3., 60, glide=.5),
                     *(Note(2., 8., pitch) for pitch in range(65, 73))))
        a = chord_voices(p.notes)
        self.assertIn((1, 2.), a.dropped)
        np.testing.assert_array_equal(a.start[a.note == 0], [0., 4.])
        np.testing.assert_array_equal(a.owner[a.note == 0], [0., 0.])
        self.assertEqual(sum(a.note == 1), 1)

    def test_empty_block_and_silence(self):
        p = project(())
        self.assertEqual(ChordSource(p).voices(np.array([])), [])
        self.assertEqual(render_song(np.array([]), p, Settings()).shape, (0, 2))
        times = np.array([0., .5, 1.])
        np.testing.assert_array_equal(render_song(times, p, Settings()),
                                      render_song(times, replace(p, chords=False), Settings()))

    def test_chord_cycle_glide_and_tempo_window(self):
        p = project(tuple(Note(0., 4., pitch) for pitch in (60, 64, 67)) +
                    tuple(Note(4., 4., pitch, glide=1.) for pitch in (62, 66, 69)),
                    tempo_changes=((4.5, 90.),))
        for t in (2.01, 2.2, 2.4, float(p.seconds(8.))-.001):
            start, end = cycle_span(p, t)
            self.assertGreaterEqual(start, float(p.seconds(4.))+.004)
            self.assertLessEqual(end, float(p.seconds(8.))-.008)
            source = ChordSource(p)
            low = min((lane for lane in source.lanes if lane._lookup(np.array([t]))[2][0]),
                      key=lambda lane: lane.frequency_at(np.array([t]))[0])
            advance = np.diff(low.at(np.array([start, end]))[1])[0]
            self.assertAlmostEqual(advance, round(advance), places=8)


if __name__ == '__main__': unittest.main()
