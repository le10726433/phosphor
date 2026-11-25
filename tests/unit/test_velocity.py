"""Velocity contracts: legacy identity, voice gains and project migration."""
from dataclasses import replace
import unittest
import numpy as np
from phosphor.storage.project_files import PROJECT_VERSION
from phosphor.engine.composition import (Project, Note, Clip, NoteSource, ChordSource,
    new_project, validate_project, render_song, render_chords, note_velocities)
from phosphor.engine.signal_engine import Settings, Shape, render
from phosphor.engine.midi_recorder import MidiRecorder
from phosphor.storage.project_files import project_document, project_from_document
from phosphor.storage.project_files import PROJECT_VERSION
from phosphor.editor.history import describe
from tests.unit.golden_scenes import scene, WINDOW_STARTS, RATE
from tests.unit.test_midi_recorder import message


class VelocityTests(unittest.TestCase):
    def test_defaults_validation_and_labels(self):
        self.assertFalse(Project().velocity)
        self.assertTrue(new_project().velocity)
        self.assertEqual(Note(0, 1, 60).velocity, 1.)
        for v in (0, 1, None, 'on'):
            with self.assertRaisesRegex(ValueError, 'Velocity must be on or off'):
                validate_project(replace(Project(), velocity=v))
        p = Project(notes=(Note(0, 1, 60),))
        self.assertEqual(describe(p, replace(p, velocity=True)), 'Velocity on')
        self.assertEqual(describe(replace(p, velocity=True), p), 'Velocity off')
        self.assertEqual(describe(p, replace(p, notes=(replace(p.notes[0], velocity=.5),))), 'Edit velocity')
        self.assertEqual(describe(p, replace(p, notes=(replace(p.notes[0], pitch=62, velocity=.5),))), 'Edit notes')

    def test_file_round_trip_and_migration(self):
        for enabled in (False, True):
            p = replace(new_project(), velocity=enabled)
            d = project_document(p)
            self.assertEqual(d['version'], PROJECT_VERSION)
            self.assertEqual(project_from_document(d), p)
            del d['velocity']
            with self.assertRaises(ValueError): project_from_document(d)
            d['version'] = 5
            self.assertFalse(project_from_document(d).velocity)
            d['velocity'] = False
            with self.assertRaises(ValueError): project_from_document(d)

    def test_full_velocity_is_exact_legacy(self):
        for name in ('composer', 'chord_triad', 'chord_glide', 'chord_steal'):
            original = scene(name)
            p = replace(original, notes=tuple(replace(n, velocity=1.) for n in original.notes))
            for start in WINDOW_STARTS:
                t = start + np.arange(128) / RATE
                np.testing.assert_array_equal(render_song(t, p, Settings()), render_song(t, replace(p, velocity=True), Settings()))

    def test_varied_velocity_mono_chord_identity(self):
        for tempo in ((), ((2.5, 95.),)):
            p = Project(notes=(Note(0., 2., 60, .25), Note(2., 2., 64, .7, glide=.5), Note(5., 2., 67, .9)),
                        clips=(Clip(0., 8., scene('travel'), 'mixed'),), tempo_changes=tempo, velocity=True)
            t = np.arange(0., 4., 1/4800)
            for mode in ('drawing', 'mixed', 'music'):
                a = replace(p, clips=(replace(p.clips[0], mode=mode),))
                np.testing.assert_array_equal(render_song(t, a, Settings()), render_song(t, replace(a, chords=True), Settings()))
            np.testing.assert_array_equal(NoteSource(p).at(t)[0], ChordSource(replace(p, chords=True)).at(t)[0])

    def test_half_gain_music_and_size_about_center(self):
        t = np.arange(.1, .2, 1/48000)
        settings = Settings(shapes=(Shape(position_x=.3, position_y=-.2),))
        for chords, pitches in ((False, (60,)), (True, (60, 64, 67))):
            p = Project(notes=tuple(Note(0, 2, pitch) for pitch in pitches), velocity=True, chords=chords)
            def signals(project):
                return render_chords(t, settings, ChordSource(project), t) if chords else render(t, settings, NoteSource(project))
            full, music, _ = signals(p)
            half, quiet, _ = signals(replace(p, notes=tuple(replace(n, velocity=.5) for n in p.notes)))
            center, _, _ = signals(replace(p, notes=tuple(replace(n, velocity=0.) for n in p.notes)))
            np.testing.assert_allclose(quiet, .5 * music, rtol=1e-12, atol=1e-15)
            np.testing.assert_allclose(half - center, .5 * (full - center), rtol=1e-12, atol=1e-15)

    def test_mixed_chord_gains(self):
        t = np.arange(.01, 1.9, 1/48000)
        p = Project(notes=tuple(Note(0., 4., pitch, v) for pitch, v in zip((60, 64, 67), (.3, .6, 1.))), chords=True, velocity=True)
        def audio(notes): return ChordSource(replace(p, notes=notes)).at(t)[0]
        actual = audio(p.notes)
        self.assertFalse(np.array_equal(actual, audio(tuple(replace(n, velocity=.6) for n in p.notes))))
        self.assertLessEqual(np.max(np.abs(actual)), np.max(np.abs(audio(tuple(replace(n, velocity=1.) for n in p.notes)))) + 1e-12)
        for lane in ChordSource(p).lanes:
            values = lane.values(t)
            self.assertEqual(len(values), 5)
            self.assertTrue(np.all(values[3] == 1.))

    def test_last_note_priority_and_cached_velocity(self):
        notes = (Note(0., 4., 60, .2), Note(1., 1., 67, .8))
        np.testing.assert_array_equal(note_velocities(notes), [.2, .8, .2])
        self.assertIs(note_velocities(notes), note_velocities(notes))
        self.assertFalse(note_velocities(notes).flags.writeable)

    def test_recorded_velocity_64(self):
        take = MidiRecorder(new_project())
        take.feed(message(velocity=64), .5)
        take.feed(message('note_off'), 1.)
        self.assertEqual(take.stop(2.).notes[0].velocity, 64/127)

    def test_duplicate_owner_resumes_its_velocity(self):
        notes = (Note(0., 4., 60, .2), Note(0., 2., 60, .8))
        p = Project(notes=notes, velocity=True)
        times = np.array([.1, 1.1])
        legacy = NoteSource(replace(p, velocity=False)).at(times)
        weighted = NoteSource(p).at(times)
        np.testing.assert_allclose(weighted[2], [.8, .2], rtol=0, atol=0)
        np.testing.assert_array_equal(weighted[1], legacy[1])
        np.testing.assert_array_equal(weighted[0], legacy[0] * np.array([.8, .2])[:, None])
