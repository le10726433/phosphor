"""Portamento shares an exact integrated phase between music and drawing."""
import unittest
from dataclasses import replace
import tempfile
from pathlib import Path

import numpy as np

from phosphor.engine.composition import (
    Note, Project, NoteSource, note_line, validate_project,
    ATTACK_SECONDS, RELEASE_SECONDS, MUSIC_LEVEL,
)
from phosphor.engine.signal_engine import Settings, Shape, render
from phosphor.storage.project_files import (
    project_document, project_from_document, save_project, read_project,
)
from phosphor.engine import clipboard


class GlideTests(unittest.TestCase):
    def project(self, gap=0., glide=1.):
        return Project(notes=(Note(0., 2., 69), Note(2. + gap, 3., 81, glide=glide)))

    def test_phase_integral_and_derivative_at_both_boundaries(self):
        source = NoteSource(self.project())
        duration = .5
        rate = np.log(2.) / duration
        times = np.array([1., 1.1, 1.25, 1.5, 1.75])
        elapsed = times - 1.
        expected = 440. + 440. * np.expm1(rate * np.minimum(elapsed, duration)) / rate
        expected += 880. * np.maximum(elapsed - duration, 0.)
        np.testing.assert_allclose(source.at(times)[1], expected, rtol=1e-13)
        h = 1e-7
        derivative = (source.at(times + h)[1] - source.at(times - h)[1]) / (2*h)
        np.testing.assert_allclose(derivative, source.frequency_at(times), rtol=2e-7)
        # One-sided limits agree at both start and end (unwrapped cycles).
        for boundary in (1., 1.5):
            phases = source.at(np.array([boundary-h, boundary, boundary+h]))[1]
            self.assertLess(np.max(np.abs(np.diff(phases))), .0001)

    def test_midpoint_is_geometric_mean(self):
        source = NoteSource(self.project())
        self.assertAlmostEqual(source.frequency_at(np.array([1.25]))[0], np.sqrt(440.*880.))

    def test_non_legato_ignores_glide(self):
        gliding = self.project(gap=.5)
        plain = replace(gliding, notes=tuple(replace(n, glide=0.) for n in gliding.notes))
        times = np.linspace(0., 3., 1001)
        for actual, expected in zip(NoteSource(gliding).at(times), NoteSource(plain).at(times)):
            np.testing.assert_allclose(actual, expected, atol=1e-12)

    def test_chained_glide_and_resumed_owner(self):
        project = Project(notes=(Note(0., 8., 69), Note(2., 2., 81, glide=2.),
                                 Note(3., .5, 76, glide=.5)))
        source = NoteSource(project)
        # The second glide starts at the interrupted slide's instantaneous pitch.
        h = 1e-8
        frequencies = source.frequency_at(np.array([1.5-h, 1.5, 1.5+h]))
        np.testing.assert_allclose(frequencies, np.sqrt(440.*880.), rtol=1e-7)
        phases = source.at(np.array([1.5-h, 1.5, 1.5+h]))[1]
        self.assertLess(np.max(np.abs(np.diff(phases))), .00001)
        # When the short note ends, the earlier owner resumes its own trajectory.
        without_short = NoteSource(replace(project, notes=project.notes[:2]))
        np.testing.assert_allclose(source.at(np.array([1.8]))[1], without_short.at(np.array([1.8]))[1])

    def test_zero_glide_is_bit_identical_to_original_algorithm(self):
        project = Project(bpm=137., notes=(Note(0., 4., 57), Note(1., 1., 69), Note(5., 1., 60)))
        times = np.linspace(-.1, 3., 10001)
        starts, ends, pitches, owners = note_line(project.notes)
        beats = project.beats(times)
        index = np.clip(np.searchsorted(starts, beats, side='right') - 1, 0, len(starts)-1)
        sounding = (beats >= starts[index]) & (beats < ends[index])
        frequency = np.where(sounding, 440. * 2. ** ((pitches[index]-69) / 12), 0.)
        seconds_per_beat = 60. / project.bpm
        phase = frequency * (beats - owners[index]) * seconds_per_beat
        into = (beats - starts[index]) * seconds_per_beat
        left = (ends[index] - beats) * seconds_per_beat
        envelope = np.where(sounding, np.clip(np.minimum(into / ATTACK_SECONDS, left / RELEASE_SECONDS), 0., 1.), 0.)
        mono = MUSIC_LEVEL * envelope * np.sin(2 * np.pi * phase)
        source = NoteSource(project)
        self.assertIsNone(source.glides)
        for actual, expected in zip(source.at(times), (np.column_stack((mono, mono)), phase, envelope)):
            self.assertTrue(np.array_equal(actual, expected))
        self.assertTrue(np.array_equal(source.frequency_at(times), frequency))

    def test_glide_reaches_drawing_and_blocks_offsets_agree(self):
        project = self.project()
        times = np.linspace(1.01, 1.49, 1000)
        settings = replace(Settings(), shapes=(Shape(),))
        drawing = render(times, settings, NoteSource(project))[0]
        plain = self.project(glide=0.)
        self.assertFalse(np.array_equal(drawing, render(times, settings, NoteSource(plain))[0]))
        blocks = np.concatenate([render(t, settings, NoteSource(project))[0] for t in np.array_split(times, 7)])
        np.testing.assert_allclose(blocks, drawing, atol=1e-12)
        shifted = NoteSource(project, offset=.75)
        np.testing.assert_allclose(shifted.at(times-.75)[1], NoteSource(project).at(times)[1])

    def test_round_trip_and_older_files(self):
        project = self.project()
        document = project_document(project)
        self.assertEqual(len(document['notes'][0]), 4)
        self.assertEqual(document['notes'][1][-1], 1.)
        self.assertEqual(project_from_document(document), project)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'glide.project.json'
            save_project(path, project)
            self.assertEqual(read_project(path), project)
        for version in (2, 3):
            old = dict(document, version=version, notes=[n[:4] for n in document['notes']])
            del old['chords']
            del old['velocity']
            self.assertTrue(all(n.glide == 0. for n in project_from_document(old).notes))

    def test_invalid_glide_is_rejected(self):
        for value in (-1., 4., float('nan'), float('inf'), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_project(replace(self.project(), notes=(Note(0., 3., 69, glide=value),)))

    def test_clipboard_preserves_optional_glide(self):
        project = self.project()
        payload = clipboard.from_text(clipboard.to_text(clipboard.copy_notes(project, (0, 1))))
        pasted, indices = clipboard.paste_notes(Project(), payload, 4.)
        self.assertEqual(tuple(pasted.notes[i].glide for i in indices), (0., 1.))
        self.assertEqual(len(payload['notes'][0]), 4)


if __name__ == '__main__':
    unittest.main()
