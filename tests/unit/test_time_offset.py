import copy
import json
import unittest
from dataclasses import replace

import numpy as np

from phosphor.engine.clipboard import copy_clips, from_text, paste_clips, to_text
from phosphor.engine.composition import (
    Clip, MAX_BEATS, Note, NoteSource, Project, clip_settings, render_song, validate_project,
)
from phosphor.engine.function_plot import FunctionPlot, PARAMETRIC_OBJECT
from phosphor.engine.lane_edit import shift_lane
from phosphor.engine.signal_engine import Settings, Shape, render, select_signal
from phosphor.engine.transforms import Param, new_transform
from phosphor.storage.project_files import project_document, project_from_document


LANE = ((0., .25, 'linear'), (2., .5, 'linear'), (8., 1., 'hold'))


def scene(points=()):
    rotate = new_transform('Rotate').with_param('z', Param(90., '90*t', points))
    return Settings(source='Single tone', music_gain=.35,
                    shapes=(Shape(solid='Cube', transforms=(rotate,)),))


class TimeOffsetTests(unittest.TestCase):
    def project(self, clip, bpm=120.):
        return Project(bpm=bpm, clips=(clip,), notes=(Note(0., 20., 60),))

    def test_trim_preserves_samples_with_transform_and_mix_automation(self):
        for bpm in (120., 137.):
            for automated in (False, True):
                with self.subTest(bpm=bpm, automated=automated):
                    points = LANE if automated else ()
                    lanes = (('drawing_gain', LANE), ('music_gain', LANE)) if automated else ()
                    original = Clip(4., 8., scene(points), mode='mixed', lanes=lanes)
                    trimmed = replace(original, start=6., length=6., time_offset=2.,
                                      settings=scene(shift_lane(points, -2.)),
                                      lanes=tuple((key, shift_lane(p, -2.)) for key, p in lanes))
                    before, after = self.project(original, bpm), self.project(trimmed, bpm)
                    times = before.seconds(np.linspace(6., 11.99, 4096))
                    expected = render_song(times, before, Settings())
                    self.assertGreater(np.max(np.abs(expected)), .01)
                    actual = render_song(times, after, Settings())
                    # Transforms are posed at 60 Hz: the first pose frame of the trimmed clip may begin
                    # just before its new start, where the cut lane holds its first value. From the
                    # next pose frame on, the samples are exactly the same.
                    settled = times >= float(after.seconds(6.)) + 1. / 60.
                    np.testing.assert_allclose(actual[settled], expected[settled], rtol=0., atol=1e-9)
                    np.testing.assert_allclose(actual, expected, rtol=0., atol=2e-3)

    def test_zero_default_is_bit_identical_and_matches_previous_render_path(self):
        clip = Clip(4., 8., scene(LANE))
        project = self.project(clip)
        times = 2.1 + np.arange(2000) / 48000.
        actual = render_song(times, project, Settings())
        np.testing.assert_array_equal(actual, render_song(times, self.project(replace(clip, time_offset=0.)), Settings()))
        settings = clip_settings(clip, Settings(), project.bpm)
        signals = render(times - 2., settings, NoteSource(project, 2.))
        np.testing.assert_array_equal(actual, select_signal(*signals, settings, clip.mode, clip_output=False))

    def test_song_clock_ignores_offset_including_lane_clocks(self):
        clip = Clip(4., 8., scene(LANE), local_time=False,
                    lanes=(('drawing_gain', LANE),))
        times = np.linspace(2.01, 5.99, 2048)
        expected = render_song(times, self.project(clip), Settings())
        for offset in (-2., 3., MAX_BEATS):
            changed = replace(clip, time_offset=offset)
            self.assertEqual(clip_settings(clip, Settings(), 120.), clip_settings(changed, Settings(), 120.))
            np.testing.assert_array_equal(expected, render_song(times, self.project(changed), Settings()))

    def test_negative_offset_holds_pose_and_preserves_notes_and_mix_clock(self):
        clip = Clip(4., 8., scene(LANE), mode='mixed', time_offset=-2.,
                    lanes=(('drawing_gain', LANE), ('music_gain', LANE)))
        project = self.project(clip)
        times = np.linspace(2.01, 2.99, 2048)  # Scene t is negative until song second 3 (beat 6).
        with np.errstate(invalid='raise', divide='raise', over='raise'):
            actual = render_song(times, project, Settings())
            crossing = render_song(np.linspace(2., 5., 2048), project, Settings())
        self.assertTrue(np.isfinite(actual).all())
        self.assertTrue(np.isfinite(crossing).all())
        # Before scene t=0 all transforms use pose zero; notes still advance.
        held = replace(clip, settings=replace(clip.settings, shapes=(Shape(solid='Cube'),)))
        np.testing.assert_allclose(actual, render_song(times, self.project(held), Settings()), rtol=0., atol=1e-12)
        music = replace(clip, mode='music', lanes=())
        reference = replace(music, time_offset=0.)
        np.testing.assert_allclose(render_song(times, self.project(music), Settings()),
                                   render_song(times, self.project(reference), Settings()), rtol=0., atol=1e-9)
        # Exact per-sample equations must use zero as well, avoiding sqrt(negative).
        equation = Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot('sqrt(t)+cos(2*pi*s)', 'sin(2*pi*s)'))
        parametric = replace(clip, settings=Settings(shapes=(equation,)))
        self.assertTrue(np.isfinite(render_song(times, self.project(parametric), Settings())).all())

    def test_round_trips_and_optional_field(self):
        for local in (True, False):
            for offset in (-2., 0., 3.):
                clip = Clip(4., 8., scene(), local_time=local, time_offset=offset)
                project = self.project(clip)
                document = project_document(project)
                self.assertEqual('time_offset' in document['clips'][0], offset != 0.)
                self.assertEqual(project_from_document(json.loads(json.dumps(document))), project)
                payload = from_text(to_text(copy_clips(project, (0,))))
                self.assertEqual('time_offset' in payload['clips'][0], offset != 0.)
                pasted, indices = paste_clips(Project(), payload, 10.)
                self.assertEqual(pasted.clips[indices[0]], replace(clip, start=10.))
        old = project_document(self.project(Clip(4., 8., scene())))
        old['version'] = 1
        del old['chords']
        del old['velocity']
        self.assertEqual(project_from_document(old).clips[0].time_offset, 0.)
        for offset in (0., -2.):
            old['clips'][0]['time_offset'] = offset
            with self.assertRaises(ValueError):
                project_from_document(old)

    def test_invalid_offsets_rejected(self):
        clip = Clip(4., 8., scene())
        document = project_document(self.project(clip))
        payload = copy_clips(self.project(clip), (0,))
        for offset in (float('nan'), float('inf'), -float('inf'), MAX_BEATS+1,
                       -MAX_BEATS-1, 10**400, True, '2', None):
            with self.subTest(offset=offset):
                with self.assertRaises(ValueError):
                    validate_project(self.project(replace(clip, time_offset=offset)))
                bad = copy.deepcopy(document)
                bad['clips'][0]['time_offset'] = offset
                with self.assertRaises(ValueError):
                    project_from_document(bad)
                bad = copy.deepcopy(payload)
                bad['clips'][0]['time_offset'] = offset
                with self.assertRaises(ValueError):
                    from_text(json.dumps(bad))
        for offset in (-MAX_BEATS, MAX_BEATS):
            validate_project(self.project(replace(clip, time_offset=offset)))


if __name__ == '__main__':
    unittest.main()
