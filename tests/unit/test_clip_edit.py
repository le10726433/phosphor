import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from phosphor.storage.project_files import PROJECT_VERSION
from phosphor.engine.composition import (Clip, Note, Project, MAX_CLIPS, empty_settings,
    split_clip, duplicate_clip, duplicated_clip_named, render_song, validate_project)
from phosphor.engine.lane_access import automatable, get_points, set_points
from phosphor.engine.lane_edit import shift_lane
from phosphor.engine.signal_engine import Settings, Shape
from phosphor.engine.transforms import new_transform
from phosphor.engine import clipboard
from phosphor.storage.app_files import scene_document
from phosphor.storage.project_files import (project_document, project_from_document,
    read_project, save_project)
from phosphor.storage.project_files import PROJECT_VERSION


POINTS = ((0., .2, 'linear'), (3., .9, 'hold'), (5., -.3, 'linear'), (8., .7, 'linear'))


class ClipEditTests(unittest.TestCase):
    def clip(self, local_time=True):
        spin = new_transform('Spin').with_param('x', replace(new_transform('Spin').param('x'), value=.25))
        settings = replace(empty_settings(), shapes=(Shape(position_x=.2, transforms=(spin,)),))
        clip = Clip(2., 8., settings, 'mixed', local_time=local_time, time_offset=.5, color=3)
        for key in (('mix', 'drawing_gain'), ('shape', 0, 'position_x'),
                    ('transform', 0, 0, 'z')):
            # Select the transform's registered numeric field rather than depending on its label.
            if key[0] == 'transform':
                key = next(k for k in automatable(clip) if k[0] == 'transform')
            clip = set_points(clip, key, POINTS)
        return clip

    def test_split_render_identity_with_lanes_transforms_and_both_clocks(self):
        times = np.unique(np.r_[np.arange(0., 6., 1 / 48000), 2.0625,
                                  np.nextafter(2.0625, 0.), np.nextafter(2.0625, 3.)])
        for local in (True, False):
            with self.subTest(local_time=local):
                project = Project(clips=(self.clip(local),), notes=(Note(0., 12., 57), Note(4., 2., 64)))
                split = split_clip(project, 0, 4.125)
                # Equal to rounding (the right piece's lanes restart from an interpolated node) except during the first pose frame after the split (1/60 s):
                # that frame began before the right piece's automation origin, so its
                # automated numbers hold their first node there (a documented limit).
                expected = render_song(times, project, Settings())
                frame = (times >= 2.0625) & (times < 2.0625 + 1 / 60)
                np.testing.assert_allclose(render_song(times, split, Settings())[~frame], expected[~frame], rtol=0, atol=1e-12)
                self.assertEqual(split.notes, project.notes)
                self.assertEqual(split.clips[1].time_offset, 2.625 if local else .5)
                self.assertEqual(split.clips[0].end, split.clips[1].start)
                # Repeated splits and a file round-trip retain the same signal too.
                again = project_from_document(project_document(split_clip(split, 1, 7.25)))
                frames = frame | ((times >= 3.625) & (times < 3.625 + 1 / 60))
                np.testing.assert_allclose(render_song(times, again, Settings())[~frames], expected[~frames], rtol=0, atol=1e-12)

    def test_split_shifts_every_lane_using_shift_lane(self):
        clip = self.clip()
        result = split_clip(Project(clips=(clip,)), 0, 4.125)
        self.assertEqual(result.clips[0], replace(clip, length=2.125))
        for key in automatable(clip):
            self.assertEqual(get_points(result.clips[1], key), shift_lane(get_points(clip, key), -2.125))
        self.assertEqual(result.clips[1].color, 3)
        for beat in (clip.start, clip.end, -1., float('nan')):
            with self.subTest(beat=beat), self.assertRaises(ValueError):
                split_clip(Project(clips=(clip,)), 0, beat)

    def test_duplicate_preserves_content_and_resolves_occupied_space(self):
        original = self.clip()
        next_clip = Clip(12., 4., empty_settings())
        project = Project(clips=(original, next_clip))
        result, index = duplicate_clip(project, 0, 4.)
        self.assertEqual(index, 1)
        self.assertEqual(result.clips[index], replace(original, start=10., length=2.))
        self.assertIs(result.clips[0], original)
        validate_project(result)
        result, index = duplicate_clip(project, 0, 20.)
        self.assertEqual(result.clips[index], replace(original, start=20.))
        with self.assertRaises(ValueError): duplicate_clip(project, 0, 100000.)
        full = Project(clips=tuple(Clip(i, 1., empty_settings()) for i in range(MAX_CLIPS)))
        with self.assertRaises(ValueError): duplicate_clip(full, 0, 600.)
        with self.assertRaises(ValueError): split_clip(full, 0, .5)

    def test_duplicate_template_keeps_every_clip_setting_and_names_the_copy(self):
        """'Duplicate selected clip' (clip templates): lanes, length, local time, offset, colour
        and mute are kept, as Alt+drag keeps them; only the name gains ' copy'."""
        original = replace(self.clip(local_time=False), name='Verse', muted=True)
        self.assertTrue(original.lanes)
        project = Project(clips=(original,))
        result, index = duplicated_clip_named(project, 0, 16.)
        self.assertEqual(result.clips[index], replace(original, start=16., name='Verse copy'))
        self.assertIs(result.clips[0], original)
        validate_project(result)
        self.assertEqual(duplicated_clip_named(project, 0, 4.)[0].clips[1].start, original.end)  # Past occupied space.
        long_name = replace(original, name='n' * 48)
        self.assertEqual(duplicated_clip_named(Project(clips=(long_name,)), 0, 20.)[0].clips[1].name, 'n' * 48)
        full = Project(clips=tuple(Clip(i, 1., empty_settings()) for i in range(MAX_CLIPS)))
        with self.assertRaises(ValueError): duplicated_clip_named(full, 0, 600.)

    def test_colour_round_trip_validation_and_clipboard(self):
        for color in (None, *range(8)):
            project = Project(clips=(replace(self.clip(), color=color),))
            self.assertEqual(project_from_document(project_document(project)), project)
            payload = clipboard.from_text(clipboard.to_text(clipboard.copy_clips(project, [0])))
            pasted, index = clipboard.paste_clips(project, payload, 20.)
            self.assertEqual(pasted.clips[index[0]].color, color)
        for color in (-1, 8, True, 1.5, 'red'):
            with self.subTest(color=color), self.assertRaises(ValueError):
                project_document(Project(clips=(replace(self.clip(), color=color),)))

    def test_inline_version_two_fixture_reads_and_saves_as_five(self):
        fixture = {
            'format': 'phosphor-project', 'version': 2, 'bpm': 96.,
            'loop': {'enabled': True, 'start': 2., 'end': 10.},
            'notes': [[2., 3., 60, .75], [6., 2., 67, .8]],
            'clips': [{'name': 'Legacy', 'start': 2., 'length': 8., 'local_time': True,
                       'time_offset': .5, 'lanes': {'drawing_gain': [list(p) for p in POINTS]},
                       'scene': scene_document(replace(empty_settings(), shapes=(Shape(),)), 'mixed', '')}],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'legacy.project.json'
            path.write_text(json.dumps(fixture), encoding='utf-8')
            project = read_project(path)
            self.assertEqual(project.notes, (Note(2., 3., 60, .75), Note(6., 2., 67, .8)))
            self.assertEqual(len(project.clips), 1)
            clip = project.clips[0]
            self.assertEqual((clip.start, clip.length, clip.name, clip.color), (2., 8., 'Legacy', None))
            self.assertEqual(clip.lanes, (('drawing_gain', POINTS),))
            self.assertEqual(len(clip.settings.shapes), 1)
            self.assertEqual(save_project(path, project)['version'], PROJECT_VERSION)
            self.assertEqual(read_project(path), project)

    def test_version_one_still_opens(self):
        document = project_document(Project(clips=(Clip(0., 4., empty_settings()),)))
        document['version'] = 1
        del document['chords']
        del document['velocity']
        self.assertIsNone(project_from_document(document).clips[0].color)


if __name__ == '__main__':
    unittest.main()
