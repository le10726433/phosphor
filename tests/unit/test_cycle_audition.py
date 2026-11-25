"""Stopped song cycles: whole note phases, steady part of the note, exact song samples."""
import unittest
from dataclasses import replace

import numpy as np

from phosphor.engine.composition import (ATTACK_SECONDS, NO_CYCLE, RELEASE_SECONDS, Clip, Note,
    NoteSource, Project, cycle_span, render_song, song_cycle)
from phosphor.engine.function_plot import FunctionPlot, PARAMETRIC_OBJECT
from phosphor.engine.signal_engine import Settings, Shape


def circle_project(glide=0.):
    shape = Shape(solid=PARAMETRIC_OBJECT,
                  plot=FunctionPlot('cos(tau*s)', 'sin(tau*s)'))
    scene = Settings(shapes=(shape,), travel_speed=float('inf'))
    return Project(notes=(Note(0., 4., 57), Note(4., 4., 64, glide=glide),
                          Note(10., 2., 57)), clips=(Clip(0., 9., scene),))


class CycleAuditionTests(unittest.TestCase):
    def check(self, project, seconds, machine, segment, frequency=None):
        """segment: (start, end) song seconds of the sounding note."""
        source = NoteSource(project)
        samples, period, reason = song_cycle(project, seconds, machine)
        start, end = cycle_span(project, seconds)
        self.assertEqual(reason, '')
        self.assertEqual(samples.dtype, np.dtype('float32'))
        self.assertAlmostEqual(period, end-start, delta=1e-12)
        if frequency is not None: self.assertAlmostEqual(period, 1/frequency, delta=1e-12)
        self.assertEqual(len(samples), max(8, round((end-start)*machine.sample_rate)))
        # Whole phases at both ends: the scene is traced once from its path start.
        phase = lambda t: float(source.at(np.array([t]))[1][0])
        self.assertAlmostEqual(phase(start), round(phase(start)), delta=1e-6)
        self.assertAlmostEqual(phase(end) - phase(start), 1., delta=1e-6)
        # Inside the steady part of the note, and the nearest such cycle to the playhead.
        self.assertGreaterEqual(start, segment[0] + ATTACK_SECONDS - 1e-9)
        self.assertLessEqual(end, segment[1] - RELEASE_SECONDS + 1e-9)
        if segment[0] + ATTACK_SECONDS + period <= seconds <= segment[1] - RELEASE_SECONDS - period:
            self.assertLessEqual(start, seconds + 1e-9); self.assertLess(seconds, end)
        expected = np.clip(render_song(start + np.arange(len(samples))/machine.sample_rate,
                                       project, machine), -1, 1).astype('float32')
        np.testing.assert_array_equal(samples, expected)
        # Seamless loop: the wrap is no bigger than the steps inside the cycle.
        largest = np.max(np.linalg.norm(np.diff(samples, axis=0), axis=1))
        self.assertLessEqual(np.linalg.norm(samples[-1]-samples[0]), largest*1.5)

    def test_constant_pitch_cycles(self):
        project = circle_project()
        for rate in (44100, 48000, 96000):
            for seconds, pitch, segment in ((.731, 57, (0., 2.)), (2.731, 64, (2., 4.)),
                                            (0., 57, (0., 2.)), (1.999, 57, (0., 2.)), (2., 64, (2., 4.))):
                with self.subTest(rate=rate, seconds=seconds):
                    self.check(project, seconds, Settings(sample_rate=rate), segment,
                               440. * 2. ** ((pitch - 69) / 12))

    def test_glide_cycle_completes_its_phase(self):
        project = circle_project(glide=2.)
        for seconds in (.731, 2., 2.231, 2.731, 3.231, 3.995):
            with self.subTest(seconds=seconds):
                segment = (0., 2.) if seconds < 2. else (2., 4.)
                self.check(project, seconds, Settings(), segment)

    def test_mix_and_automation_are_song_samples(self):
        project = circle_project()
        clip = replace(project.clips[0], mode='mixed',
                       settings=replace(project.clips[0].settings, music_gain=.4),
                       lanes=(('drawing_gain', ((0., .3, 'linear'), (8., .8, 'linear'))),))
        self.check(replace(project, clips=(clip,)), .731, Settings(), (0., 2.), 220.)

    def test_tempo_changes(self):
        project = replace(circle_project(glide=2.), bpm=137., tempo_changes=((2., 90.), (5., 110.)))
        seconds = float(project.seconds(4.))
        self.check(project, seconds + .1, Settings(), (seconds, float(project.seconds(8.))))

    def test_rest_gap_end_and_minimum_length(self):
        project = circle_project()
        for seconds in (-.1, 4.1, 5.1, 7.):
            self.assertEqual(song_cycle(project, seconds, Settings()), (None, None, NO_CYCLE))
        fast = replace(project, notes=(Note(0., 4., 127),))
        samples, _, _ = song_cycle(fast, .5, Settings(sample_rate=8000))
        self.assertEqual(len(samples), 8)


if __name__ == '__main__':
    unittest.main()
