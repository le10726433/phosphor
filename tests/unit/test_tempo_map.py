"""R16 engine timing and the frozen constant-tempo sample contract."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np

from phosphor.engine.tempo_map import TempoMap, Meter, BeatClock
from phosphor.engine.composition import (
    Project, Clip, Note, NoteSource, clip_settings, render_song, song_seconds,
    split_clip, validate_project, export_song,
)
from phosphor.engine.signal_engine import Settings, Shape, render, validate
from phosphor.engine.automation import gain_at
from phosphor.engine.function_plot import CompiledPlot, FunctionPlot, Variable, PARAMETRIC_OBJECT
from phosphor.engine.transforms import Param, new_transform, path_window
from phosphor.engine.scene_path import INSTANT_TRAVEL
from phosphor.engine.metronome import click_times_beats, mix_clicks, mix_clicks_beats
from phosphor.engine.midi_recorder import MidiRecorder
from phosphor.storage.project_files import project_document, project_from_document, save_project, read_project


class TempoMapTests(unittest.TestCase):
    def test_scene_bar_range_respects_meter_and_exclusive_clip_end(self):
        meter = Meter(((0, 3, 4), (3, 5, 4)))
        self.assertEqual(meter.bar_range(3, 9), (2, 3))
        self.assertEqual(meter.bar_range(8.5, 14), (3, 4))
        self.assertEqual(meter.bar_range(9, 14.25), (4, 5))

    def test_meter_metronome_accents_and_constant_regression(self):
        rate = 48000
        seconds = np.arange(rate * 4) / rate
        silent = np.zeros((len(seconds), 2))
        # Empty maps keep the old path exactly, including fractional sample periods.
        for bpm in (120., 137.):
            clock = TempoMap(bpm)
            np.testing.assert_array_equal(mix_clicks(silent, seconds, bpm, rate),
                mix_clicks_beats(silent, clock.beats(seconds), rate, clock))
            np.testing.assert_array_equal(mix_clicks(silent, seconds, bpm, rate),
                mix_clicks_beats(silent, clock.beats(seconds), rate, clock, meter=Meter(((0, 4, 4),))))
        for changes, boundary in ((((0, 3, 4), (2, 6, 8)), 9.), (((0, 3, 8),), 1.5)):
            clock = TempoMap(120., ((1., 60.),))
            beats = clock.beats(clock.seconds(boundary) + np.arange(100) / rate)
            result = mix_clicks_beats(np.zeros((100, 2)), beats, rate, clock, meter=Meter(changes))
            t = np.arange(100) / rate
            expected = .3 * np.exp(-t / .003) * np.sin(2 * np.pi * 1800 * t)
            np.testing.assert_allclose(result[:, 0], expected, atol=1e-12)

    def test_round_trip_monotonicity_and_boundaries(self):
        clock = TempoMap(120., ((4., 60.), (8., 180.)))
        beats = np.unique(np.r_[np.linspace(-10., 100., 10001), 0., 4., 8.])
        seconds = clock.seconds(beats)
        self.assertTrue(np.all(np.diff(seconds) > 0))
        np.testing.assert_allclose(clock.beats(seconds), beats, atol=1e-12)
        np.testing.assert_allclose(clock.seconds(clock.beats(seconds)), seconds, atol=1e-12)
        np.testing.assert_array_equal(clock.seconds([0., 4., 8., 11.]), [0., 2., 6., 7.])
        self.assertEqual(float(clock.beats(6.)), 8.)

    def test_meter(self):
        meter = Meter(((0, 3, 4), (2, 6, 8), (4, 4, 4)))
        np.testing.assert_array_equal(meter.bar_start(np.arange(7)), [0, 3, 6, 9, 12, 16, 20])
        bars, within = meter.bar_of([0, 2.5, 3, 8, 12, 17])
        np.testing.assert_array_equal(bars, [0, 0, 1, 2, 4, 5])
        np.testing.assert_array_equal(within, [0, 2.5, 0, 2, 0, 1])
        np.testing.assert_array_equal(meter.bar_lines(2, 16), [3, 6, 9, 12, 16])
        np.testing.assert_array_equal(meter.beats_per_bar_at([0, 6, 12]), [3, 3, 4])
        self.assertEqual(float(Meter(((2, 3, 4),)).bar_start(3)), 11.)
        self.assertEqual(float(Meter().bar_start(3)), 12.)

    def test_validation(self):
        for changes in ([], ((0., 90.),), ((2., 90.), (2., 100.)), ((-1., 90.),),
                        ((100001., 90.),), ((2., 19.),), ((2., float('nan')),),
                        ((True, 90.),), tuple((float(i+1), 90.) for i in range(257))):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_project(Project(tempo_changes=changes))
        for changes in ([], ((0, 0, 4),), ((0, 17, 4),), ((0, 3, 3),), ((0., 3, 4),),
                        ((-1, 3, 4),), ((0, 3, 4), (0, 4, 4)), ((100000, 16, 2),),
                        tuple((i, 3, 4) for i in range(257))):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_project(Project(meter_changes=changes))

    def test_file_round_trip_and_optional_fields(self):
        project = Project(tempo_changes=((4., 60.),), meter_changes=((0, 3, 4),))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'tempo.project.json'
            save_project(path, project)
            self.assertEqual(read_project(path), project)
        old = project_document(Project())
        self.assertNotIn('tempo_changes', old)
        self.assertNotIn('meter_changes', old)
        for version in (1, 2, 3):
            legacy = dict(old, version=version); del legacy['chords']; del legacy['velocity']
            self.assertEqual(project_from_document(legacy), Project())
        for key, value in (('tempo_changes', [[4, 90]]), ('meter_changes', [[0, 3, 4]])):
            with self.assertRaises(ValueError):
                project_from_document(dict(old, version=2, **{key: value}))
            with self.assertRaises(ValueError):
                project_from_document(dict(old, **{key: 'invalid'}))

    def test_constant_formula_samples_are_bit_identical(self):
        # Stored pre-R16 expectation: the original constant-formula path, independent
        # of Project.seconds/beats and NoteSource. Keep operation ordering unchanged.
        times = np.arange(48000) / 48000.
        bpm = 137.
        beats = np.asarray(times, dtype=float) * bpm / 60.
        sounding = (beats >= 0.) & (beats < 4.)
        frequency = np.where(sounding, 440. * 2. ** ((69. - 69) / 12), 0.)
        phase = frequency * (beats - 0.) * (60. / bpm)
        into = (beats - 0.) * (60. / bpm)
        left = (4. - beats) * (60. / bpm)
        envelope = np.where(sounding, np.clip(np.minimum(into / .004, left / .008), 0., 1.), 0.)
        mono = .8 * envelope * np.sin(2 * np.pi * phase)
        stored_expectation = np.column_stack((mono, mono)) * .55
        project = Project(bpm=bpm, notes=(Note(0., 4., 69),),
                          clips=(Clip(0., 4., Settings(), mode='music'),))
        explicit = replace(project, tempo_changes=(), meter_changes=())
        self.assertTrue(np.array_equal(render_song(times, project, Settings()), stored_expectation))
        self.assertTrue(np.array_equal(render_song(times, explicit, Settings()), stored_expectation))
        # Reading a document without either new field also preserves the path.
        restored = project_from_document(project_document(project))
        np.testing.assert_array_equal(render_song(times, restored, Settings()), stored_expectation)

    def test_note_after_change_and_envelope(self):
        project = Project(notes=(Note(6., 1., 69),), tempo_changes=((4., 60.),),
                          clips=(Clip(0., 8., Settings(), mode='music'),))
        times = np.array([3.99, 4., 4.001, 4.25, 4.999, 5.])
        music, phase, envelope = NoteSource(project).at(times)
        np.testing.assert_allclose(envelope, [0., 0., .25, 1., .125, 0.], atol=1e-12)
        np.testing.assert_allclose(phase[2:5], 440. * (times[2:5] - 4.), atol=1e-10)
        np.testing.assert_allclose(render_song(times, project, Settings()), music * .55)

    def test_automation_and_expression_clocks(self):
        points = ((0., 0., 'linear'), (4., 1., 'linear'))
        plot = FunctionPlot('p', 'v', (Variable('v', 1., 0., 1., points=points),))
        shape = Shape(solid=PARAMETRIC_OBJECT, plot=plot, path_trace=Param(1., points=points))
        for local in (True, False):
            clip = Clip(2., 8., Settings(shapes=(shape,)), local_time=local, time_offset=1.)
            project = Project(clips=(clip,), tempo_changes=((4., 60.),))
            baked = clip_settings(clip, Settings(), project)
            validate(baked)
            origin = 1. if local else 0.
            times = project.seconds(np.array([3.999, 4., 4.001, 5.])) - project.seconds(origin)
            xy = CompiledPlot(baked.shapes[0].plot)(times, np.zeros(4), baked.tempo)
            np.testing.assert_allclose(xy[:, 0], [3.999-origin, 4.-origin, 4.001-origin, 5.-origin])
            np.testing.assert_allclose(xy[:, 1], np.array([1.999, 2., 2.001, 3.]) / 4.)
            self.assertAlmostEqual(gain_at(points, baked.shapes[0].path_trace.clock, times[-1]), .75)
            self.assertAlmostEqual(path_window(Param(0.), baked.shapes[0].path_trace, times[-1], baked.tempo)[1], .75)

    def test_glide_phase_across_change(self):
        project = Project(notes=(Note(0., 8., 69), Note(3., 5., 81, glide=3.)),
                          tempo_changes=((4., 60.),))
        # Glide starts at 1.5 s, lasts 2.5 s, with phase inherited from A4.
        times = np.array([1.5, 1.999999, 2., 2.000001, 3., 4., 4.1])
        _, phase, _ = NoteSource(project).at(times)
        elapsed = times - 1.5
        rate = np.log(2.) / 2.5
        expected = 660. + 440. * np.expm1(rate * np.clip(elapsed, 0., 2.5)) / rate + 880. * np.maximum(elapsed - 2.5, 0.)
        np.testing.assert_allclose(phase, expected, atol=1e-9)
        self.assertLess(phase[3] - phase[1], .002)
        # A third legato note inherits the already mapped trajectory.
        third = replace(project, notes=(*project.notes, Note(5., 2., 76, glide=1.)))
        np.testing.assert_allclose(NoteSource(third).at(np.array([3.]))[1], phase[4:5])

    def test_rendered_equations_transforms_and_path_clock(self):
        expression = '(min(t,2)*2+max(t-2,0))'
        plot = FunctionPlot('sin(p)/3+s/10', 'cos(p)/3')
        stack = (new_transform('Rotate').with_param('z', Param(0., '15*p')),)
        shape = Shape(solid=PARAMETRIC_OBJECT, plot=plot, transforms=stack,
                      path_start=Param(0., 'p/20'), path_trace=Param(1., '.8'))
        scene = Settings(shapes=(shape,), travel_speed=INSTANT_TRAVEL)
        project = Project(notes=(Note(0., 12., 57),), clips=(Clip(0., 12., scene),),
                          tempo_changes=((4., 60.),))
        literal_shape = replace(shape,
            plot=FunctionPlot(f'sin({expression})/3+s/10', f'cos({expression})/3'),
            transforms=(new_transform('Rotate').with_param('z', Param(0., f'15*{expression}')),),
            path_start=Param(0., f'{expression}/20'))
        literal = replace(scene, shapes=(literal_shape,))
        times = np.r_[np.linspace(1.9, 2.1, 400), np.linspace(3., 3.1, 200)]
        baked = clip_settings(project.clips[0], Settings(), project)
        np.testing.assert_allclose(render(times, baked, NoteSource(project))[0],
                                   render(times, literal, NoteSource(project))[0], atol=1e-10)

    def test_range_export_and_recorder_use_mapped_seconds(self):
        project = Project(notes=(Note(0., 12., 69),), tempo_changes=((4., 60.),))
        blocks = []
        class Writer:
            def __init__(self, *args): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def write(self, block): blocks.append(block)
        with patch('phosphor.engine.composition.AudioWriter', Writer):
            export_song('unused.wav', project, Settings(), start_beats=5., end_beats=7., source_only=True)
        # Beat 5..7 spans song seconds 3..5 after the change.
        self.assertEqual(sum(len(b) for b in blocks), 96000)
        np.testing.assert_array_equal(np.concatenate(blocks),
                                      render_song(3. + np.arange(96000)/48000., project, Settings(), True))
        recorder = MidiRecorder(project)
        recorder.feed(SimpleNamespace(type='note_on', note=72, velocity=100), 3.)
        recorder.feed(SimpleNamespace(type='note_off', note=72), 4.)
        take = recorder.stop(4.)
        recorded = next(n for n in take.notes if n.pitch == 72)
        self.assertEqual((recorded.start, recorded.length), (5., 1.))

    def test_metronome_sample_boundaries(self):
        clock = TempoMap(137., ((4.25, 71.),))
        rate = 48000
        for beat in range(5, 9):
            sample = round(float(clock.seconds(beat)) * rate)
            seconds = np.arange(sample-2, sample+3) / rate
            age, numbers = click_times_beats(clock.beats(seconds), rate, clock)
            np.testing.assert_array_equal(age[2:], [0, 1, 2])
            np.testing.assert_array_equal(numbers, [beat-1, beat-1, beat, beat, beat])

    def test_split_and_loop_keep_song_time(self):
        project = Project(notes=(Note(0., 20., 69),), tempo_changes=((4., 60.),),
                          clips=(Clip(1., 10., Settings(), mode='music', time_offset=1.),),
                          loop=True, loop_start=3., loop_end=7.)
        times = np.linspace(.51, 8.9, 4000)
        np.testing.assert_array_equal(render_song(times, project, Settings()),
                                      render_song(times, split_clip(project, 0, 6.), Settings()))
        np.testing.assert_allclose(song_seconds(project, 4.9, [0., .2, 1.]), [4.9, 1.6, 2.4])


if __name__ == '__main__':
    unittest.main()
