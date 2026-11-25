import json
import tempfile
import unittest
from dataclasses import fields, replace
import pickle
from unittest.mock import patch
from pathlib import Path

import numpy as np
from phosphor.engine import composition, signal_engine

from phosphor.storage.project_files import PROJECT_VERSION
from phosphor.engine.composition import (Project, Note, Clip, NoteSource, new_project, empty_settings, note_line, render_song,
                                         song_seconds, clip_at, add_clip, fit_clip, move_clips, remove_clip, validate_project, export_song,
                                         COMPOSER, SANDBOX)
from phosphor.engine.signal_engine import Settings, Shape, render, select_signal
from phosphor.engine.function_plot import FunctionPlot, PARAMETRIC_OBJECT
from phosphor.engine.custom_shape import Drawing, CUSTOM_OBJECT
from phosphor.engine.transforms import new_transform, Param
from phosphor.storage.project_files import project_document, project_from_document, save_project, read_project, read_workspace, write_workspace

MACHINE = replace(Settings(), sample_rate=48000)


def song(*clips, notes=(), **kwargs):
    return Project(notes=tuple(notes), clips=tuple(clips), **kwargs)


class NoteTests(unittest.TestCase):
    def test_baked_settings_inputs_and_shape_hash(self):
        shape = Shape(lanes=(('position_x', ((0., 1., 'hold'),)),))
        clip = Clip(0., 4., Settings(shapes=(shape,)))
        project = song(clip)
        bake = composition.clip_settings
        baked = bake(clip, MACHINE, project)
        self.assertIs(bake(clip, MACHINE, project), baked)
        for changed in (replace(project, bpm=80), replace(project, tempo_changes=((2., 75.),))):
            self.assertIsNot(bake(clip, MACHINE, changed), baked)
        for field, value in (('sample_rate', 96000), ('route_mode', 'Shortest path'), ('corner_dwell_us', 1.),
                             ('shape_redraw_count', 2), ('switch_sweep_ms', 5.)):
            self.assertIsNot(bake(clip, replace(MACHINE, **{field: value}), project), baked)
        self.assertEqual(hash(shape), hash(tuple(getattr(shape, f.name) for f in fields(shape))))
        self.assertNotIn('_hash', vars(pickle.loads(pickle.dumps(shape))))
        for invalid in (replace(MACHINE, shape_redraw_count=1.), replace(MACHINE, route_mode=[])):
            with self.assertRaises(ValueError):
                render_song(np.array([.1]), project, invalid)

    def test_render_validates_once_including_muted_edges_and_rejects_each_invalid_snapshot(self):
        clip = Clip(0., 4., Settings())
        machine = replace(MACHINE, switch_sweep_ms=5.)
        project = song(clip, replace(clip, start=4., muted=True))
        with patch.object(signal_engine, 'validate', wraps=signal_engine.validate) as check:
            for _ in range(2): render_song(np.array([.1]), project, machine)
            self.assertEqual(check.call_count, 1)
            self.assertTrue(check.call_args.args[0].switch_fades)
            invalid = replace(check.call_args.args[0], drawing_gain=2.)
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, 'Output level'):
                    render(np.array([.1]), invalid)
            self.assertEqual(check.call_count, 3)

    def test_monophonic_line_uses_last_note_priority_and_resumes_held_notes(self):
        starts, ends, pitches, owners = note_line((Note(0, 8, 48), Note(2, 2, 60), Note(10, 1, 50)))
        np.testing.assert_array_equal(starts, [0, 2, 4, 10]); np.testing.assert_array_equal(ends, [2, 4, 8, 11])
        np.testing.assert_array_equal(pitches, [48, 60, 48, 50])
        np.testing.assert_array_equal(owners, [0, 2, 0, 10])  # The held C3 resumes with its own phase.

    def test_notes_restart_the_phase_and_rests_are_silent(self):
        project = song(notes=(Note(0, 1, 69), Note(2, 1, 81)))  # 120 BPM: a beat is 0.5 s.
        music, phase, envelope = NoteSource(project).at(np.array([.25, .75, 1.0, 1.25]))
        np.testing.assert_allclose(phase, [110., 0., 0., 220.])  # A4 for 0.25 s; rest; A5 from its start.
        np.testing.assert_array_equal(envelope[1:3], 0)
        np.testing.assert_allclose(NoteSource(project).frequency_at(np.array([.25, 1.25])), [440., 880.])

    def test_offsets_shift_song_time_for_clip_local_time(self):
        project = song(notes=(Note(4, 4, 69),))
        np.testing.assert_allclose(NoteSource(project, offset=2.).at(np.array([.5]))[1], NoteSource(project).at(np.array([2.5]))[1])


class SongTests(unittest.TestCase):
    times = np.arange(48000 * 4) / 48000

    def test_each_clip_plays_exactly_like_its_sandbox_scene(self):
        circle = replace(empty_settings(), shapes=(Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot()),), drawing_gain=.5)
        cube = replace(empty_settings(), shapes=(Shape(transforms=(new_transform('Spin'),)),))
        project = song(Clip(0, 4, circle, 'drawing'), Clip(4, 4, cube, 'mixed', local_time=True), notes=(Note(0, 8, 57),))
        output = render_song(self.times, project, MACHINE)
        first = self.times < 2
        source = NoteSource(project)
        expected = select_signal(*render(self.times[first], replace(circle, sample_rate=48000), source), replace(circle, sample_rate=48000), 'drawing', clip_output=False)
        np.testing.assert_allclose(output[first], expected, atol=1e-12)
        local = self.times[~first] - 2.
        expected = select_signal(*render(local, replace(cube, sample_rate=48000), NoteSource(project, 2.)), replace(cube, sample_rate=48000), 'mixed', clip_output=False)
        np.testing.assert_allclose(output[~first], expected, atol=1e-12)

    def test_gaps_are_silent_and_blocks_are_independent(self):
        settings = replace(empty_settings(), shapes=(Shape(),))
        project = song(Clip(0, 2, settings), Clip(4, 2, settings), notes=(Note(0, 8, 60),))
        output = render_song(self.times, project, MACHINE)
        np.testing.assert_array_equal(output[(self.times >= 1) & (self.times < 2)], 0)
        parts = np.concatenate([render_song(self.times[i:i+777], project, MACHINE) for i in range(0, len(self.times), 777)])
        np.testing.assert_array_equal(parts, output)

    def test_loops_wrap_and_clips_are_found_by_beat(self):
        project = song(Clip(0, 4, empty_settings()), loop=True, loop_start=2., loop_end=4.)  # 1 s to 2 s.
        np.testing.assert_allclose(song_seconds(project, 0., np.array([.5, 1.99, 2., 2.25, 3.])), [.5, 1.99, 1., 1.25, 1.])
        np.testing.assert_allclose(song_seconds(replace(project, loop=False), 0., np.array([3.])), [3.])
        np.testing.assert_array_equal(clip_at(project, np.array([0, 3.99, 4, 9])), [0, 0, -1, -1])

    def test_editing_keeps_clips_ordered_without_overlaps(self):
        project = new_project()
        project, index = add_clip(project, 32.)
        self.assertEqual((index, [c.start for c in project.clips]), (1, [0., 32.]))
        project, index = add_clip(project, 24.)  # Shortened to fit before the next clip.
        self.assertEqual(project.clips[index].length, 8.)
        with self.assertRaises(ValueError): add_clip(project, 2.)
        moved = fit_clip(project, 1, 0., 20.)  # Clamped between its neighbours.
        self.assertEqual((moved.clips[1].start, moved.clips[1].length), (16., 16.))  # All the room between 16 and 32.
        self.assertEqual(len(remove_clip(project, 0).clips), 2)
        validate_project(project)
        for bad in (replace(project, bpm=1.), replace(project, notes=(Note(0, 0, 60),)), replace(project, notes=(Note(0, 1, 200),)),
                    replace(project, clips=project.clips[::-1]), replace(project, loop_start=8., loop_end=4.)):
            with self.subTest(bad=bad), self.assertRaises(ValueError): validate_project(bad)

    def test_song_export(self):
        project = song(Clip(0, 2, replace(empty_settings(), shapes=(Shape(),))), notes=(Note(0, 2, 60),))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'song.wav'; export_song(path, project, MACHINE)
            self.assertEqual(path.stat().st_size, 44 + 48000 * 2 * 4)  # A song lasts at least a bar: 2 s at 120 BPM, 16-bit stereo.


class FileTests(unittest.TestCase):
    def test_projects_embed_every_scene_and_round_trip(self):
        drawing = Drawing('Imported', ((0, 0., 0.), (1, 1., 0.), (2, 1., 1.)), ((0, 1), (1, 2)))
        rich = replace(empty_settings(), shapes=(
            Shape(solid=CUSTOM_OBJECT, custom=drawing, path_trace=Param(.5, 'sin(t)'), transforms=(new_transform('Shear'),)),
            Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot('sin(3*tau*s)', 'cos(tau*s)', ())),))
        project = song(Clip(0, 4, rich, 'mixed', 'Verse', False), Clip(8, 4, empty_settings()), notes=(Note(0, 1, 60, .5),),
                       bpm=97., loop=True, loop_start=0., loop_end=8.)
        document = project_document(project)
        self.assertEqual(document['clips'][0]['scene']['shapes'][0]['custom']['nodes'][1], [1, 1.0, 0.0])  # Geometry is inside.
        self.assertEqual(project_from_document(json.loads(json.dumps(document))), project)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'a.project.json'; save_project(path, project)
            self.assertEqual(read_project(path), project)
            for bad in (dict(document, version=PROJECT_VERSION + 1), dict(document, extra=1), dict(document, notes=[[0, 1, 60]])):
                with self.subTest(bad=list(bad)[-1]), self.assertRaises(ValueError): project_from_document(bad)

    def test_workspace_choice_is_remembered_and_defaults_to_composer(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'workspace.json'
            self.assertEqual(read_workspace(path), COMPOSER)
            write_workspace(SANDBOX, path); self.assertEqual(read_workspace(path), SANDBOX)
            path.write_text('{"workspace": "Elsewhere"}'); self.assertEqual(read_workspace(path), COMPOSER)


class MoveClipsTests(unittest.TestCase):
    def project(self, *intervals, **kwargs):
        settings = empty_settings()
        return song(*(Clip(start, end-start, settings, name=chr(65+i))
                      for i, (start, end) in enumerate(intervals)), **kwargs)

    def move(self, project, indices, start):
        result, picked = move_clips(project, indices, start)
        validate_project(result)
        return result, picked

    def check_move(self, project, indices, start, expected, picked):
        result, selection = self.move(project, indices, start)
        self.assertEqual([(c.name, c.start, c.end) for c in result.clips], expected)
        self.assertEqual(selection, picked)
        return result

    def test_swaps_past_midpoint_in_either_direction(self):
        p = self.project((0, 4), (6, 14))
        swapped = [('B', 0, 8), ('A', 10, 14)]
        self.check_move(p, [0], 7., swapped, (1,))
        self.check_move(p, [1], -1., swapped, (0,))
        for start in (5., 6.):  # Exactly at the midpoint still clamps.
            self.check_move(p, [0], start, [('A', 2, 6), ('B', 6, 14)], (0,))

    def test_gap_drop_and_gap_too_short(self):
        p = self.project((0, 4), (4, 8), (20, 24))
        result = self.check_move(p, [0], 10., [('B', 4, 8), ('A', 10, 14), ('C', 20, 24)], (1,))
        self.assertIs(result.clips[0], p.clips[1])
        self.assertIs(result.clips[2], p.clips[2])
        p = self.project((0, 4), (4, 8), (10, 18))
        self.check_move(p, [0], 8., [('B', 0, 4), ('A', 4, 8), ('C', 10, 18)], (1,))

    def test_small_move_and_song_boundaries(self):
        from phosphor.engine.composition import MAX_BEATS
        p = self.project((0, 4), (10, 14))
        self.check_move(p, [0], 3., [('A', 3, 7), ('B', 10, 14)], (0,))
        result, _ = self.move(p, [0], -1.)
        self.assertIs(result, p)
        result, _ = self.move(p, [1], MAX_BEATS)
        self.assertEqual(result.clips[1].end, MAX_BEATS)

    def test_contiguous_block_moves_and_swaps(self):
        p = self.project((0, 4), (4, 8), (10, 18))
        self.check_move(p, [0, 1], 1., [('A', 1, 5), ('B', 5, 9), ('C', 10, 18)], (0, 1))
        self.check_move(p, [1, 0], 7., [('C', 0, 8), ('A', 10, 14), ('B', 14, 18)], (1, 2))

    def test_swap_past_several_clips_keeps_positional_gaps(self):
        p = self.project((0, 4), (6, 14), (20, 22), (30.1, 34.1))
        result = self.check_move(p, [0], 27., [('B', 0, 8), ('C', 10, 12), ('A', 18, 22),
                                               ('D', 30.1, 34.1)], (2,))
        self.assertIs(result.clips[-1], p.clips[-1])
        self.check_move(result, [2], -1., [(c.name, c.start, c.end) for c in p.clips], (0,))

    def test_noncontiguous_selection_straddles_a_clip_or_clamps(self):
        p = self.project((1, 3), (6, 8), (10, 12))
        self.check_move(p, [0, 2], 3., [('A', 3, 5), ('B', 6, 8), ('C', 12, 14)], (0, 2))
        self.check_move(p, [0, 2], 7., [('A', 4, 6), ('B', 6, 8), ('C', 13, 15)], (0, 2))
        self.check_move(p, [0, 2], 9., [('B', 6, 8), ('A', 9, 11), ('C', 18, 20)], (1, 2))

    def test_clip_content_and_song_tracks_keep_identity(self):
        from phosphor.engine.audio_clips import AudioClip
        a = Clip(0., 4., empty_settings(), 'mixed', 'Verse', False,
                 (('music_gain', ((0., .5, 'hold'),)),), 1.25, 3, True)
        b = replace(a, start=6., length=8., name='Chorus')
        p = song(a, b, notes=(Note(0, 1, 60),), markers=((2., 'Intro'),), tempo_changes=((4., 95.),),
                 meter_changes=((0, 3, 4),), audio_clips=(AudioClip(2., 'a.wav', 'Audio', 0, '', 2.),),
                 loop=True, loop_start=1., loop_end=8.)
        result, _ = self.move(p, [0], 7.)
        for old, new in zip((b, a), result.clips):
            for field in ('settings', 'lanes', 'time_offset', 'length', 'name', 'mode', 'local_time', 'color', 'muted'):
                self.assertIs(getattr(new, field), getattr(old, field))
        for field in ('notes', 'markers', 'tempo_changes', 'meter_changes', 'audio_clips', 'audio_tracks', 'loop', 'loop_start', 'loop_end'):
            self.assertIs(getattr(result, field), getattr(p, field))

    def test_random_moves_are_rigid_and_repeatable(self):
        import random
        rng = random.Random(7)
        for case in range(300):
            intervals = []
            end = 0.
            for _ in range(rng.randint(1, 6)):
                beat = end + rng.randint(0, 16) / 4
                end = beat + rng.randint(1, 32) / 4
                intervals.append((beat, end))
            p = self.project(*intervals)
            indices = sorted(rng.sample(range(len(intervals)), rng.randint(1, len(intervals))))
            start = rng.randint(-40, 240) / 4
            with self.subTest(case=case, indices=indices, start=start):
                result, picked = self.move(p, indices, start)
                self.assertEqual((result, picked), self.move(p, indices, start))
                self.assertCountEqual([(c.name, c.length) for c in result.clips], [(c.name, c.length) for c in p.clips])
                self.assertEqual([result.clips[i].name for i in picked], [p.clips[i].name for i in indices])
                self.assertEqual([result.clips[i].start - result.clips[picked[0]].start for i in picked],
                                 [p.clips[i].start - p.clips[indices[0]].start for i in indices])

    def test_return_to_press_snapshot_and_invalid_inputs(self):
        p = self.project((1, 3), (6, 8), (10, 12))
        self.move(p, [0, 2], 9.)
        result, picked = self.move(p, iter((2, 0, 2)), p.clips[0].start)
        self.assertIs(result, p)
        self.assertEqual(picked, (0, 2))
        with self.assertRaisesRegex(ValueError, 'Choose a clip to move'):
            move_clips(p, [], 0.)
        with self.assertRaisesRegex(ValueError, 'Clip start must be a finite number'):
            move_clips(p, [0], float('nan'))
        for index in (-1, len(p.clips)):
            with self.assertRaises(IndexError): move_clips(p, [index], 0.)

    def test_swap_never_starts_past_the_beat_limit(self):
        """A clip may legally end past MAX_BEATS; swapping past it would start beyond the limit, so the move clamps
        (Codex review of UI1)."""
        from phosphor.engine.composition import MAX_BEATS
        p = self.project((0, 4), (MAX_BEATS - 2, MAX_BEATS + 10))
        result, picked = self.move(p, [0], MAX_BEATS + 1)
        self.assertEqual([(c.name, c.start) for c in result.clips], [('A', MAX_BEATS - 6), ('B', MAX_BEATS - 2)])
        self.assertEqual(picked, (0,))


if __name__ == '__main__':
    unittest.main()
