"""M2 On/Off lanes and clip mute: lane editing, evaluation, scene plan, files and exports."""
import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from phosphor.storage.project_files import PROJECT_VERSION
from phosphor.engine import switch_lane as sw
from phosphor.engine import lane_access as lanes
from phosphor.engine.composition import (Clip, Note, Project, clip_at, clip_settings, cycle_span, export_song,
                                         render_song, split_clip, validate_project)
from phosphor.engine.signal_engine import Settings, Shape, posed_shapes, render, scene_plan, validate
from phosphor.engine.transforms import new_transform, Param
from phosphor.editor.history import describe
from phosphor.storage.app_files import scene_document, scene_from_document
from phosphor.storage.project_files import project_document, project_from_document
from phosphor.storage.project_files import PROJECT_VERSION
from phosphor.storage.app_files import SCENE_VERSION

MACHINE = replace(Settings(), sample_rate=48000)
OFF_2_TO_4 = ((0., 1., 'hold'), (2., 0., 'hold'), (4., 1., 'hold'))


class LaneEditTests(unittest.TestCase):
    def test_paint_flip_and_blocks(self):
        lane = sw.paint(sw.DEFAULT, 2, 4, False)
        self.assertEqual(lane, OFF_2_TO_4)
        self.assertEqual(sw.blocks(lane, 8), ((0., 2., True), (2., 4., False), (4., 8., True)))
        self.assertEqual(sw.paint(lane, 3, 6, True), ((0., 1., 'hold'), (2., 0., 'hold'), (3., 1., 'hold')))
        self.assertEqual(sw.paint(lane, 1, 5, False), ((0., 1., 'hold'), (1., 0., 'hold'), (5., 1., 'hold')))
        self.assertEqual(sw.paint(lane, 2, 4, True), sw.DEFAULT)  # Merges back into one run.
        self.assertEqual(sw.paint(lane, 3, 3, True), lane)  # Empty ranges change nothing.
        self.assertEqual(sw.flip(lane, 3, 8), sw.DEFAULT)
        self.assertEqual(sw.flip(lane, 0, 8), ((0., 0., 'hold'), (4., 1., 'hold')))
        # The last run holds past the clip end: flipping it leaves no change back there.
        self.assertEqual(sw.flip(lane, 6, 8), ((0., 1., 'hold'), (2., 0., 'hold')))
        self.assertEqual(sw.paint(sw.DEFAULT, 5, math.inf, False), ((0., 1., 'hold'), (5., 0., 'hold')))
        self.assertEqual(sw.blocks((), 4), ((0., 4., True),))

    def test_state_and_normalized_points(self):
        self.assertTrue(sw.state_at(OFF_2_TO_4, 1.99)); self.assertFalse(sw.state_at(OFF_2_TO_4, 2.))
        self.assertTrue(sw.state_at(OFF_2_TO_4, 4.)); self.assertTrue(sw.state_at((), 9.))
        self.assertFalse(sw.state_at(((3., 0., 'hold'),), 0.))  # The first value holds before it.
        self.assertEqual(sw.normalized([(3., False), (5., False), (5., True)]), ((0., 0., 'hold'), (5., 1., 'hold')))

    def test_validation(self):
        sw.validate_switch(OFF_2_TO_4, 'lane'); sw.validate_switch((), 'lane')
        for bad in (((0., .5, 'hold'),), ((0., 1., 'linear'),), ((0., 1., 'hold'), (0., 0., 'hold')),
                    ((0., True, 'hold'),), ((0., 1., 'curve', 1., 1.),)):
            with self.subTest(bad=bad), self.assertRaises(ValueError): sw.validate_switch(bad, 'lane')

    def test_evaluation_with_a_baked_clock(self):
        clock = (.5, 0.)  # 120 BPM from t = 0.
        self.assertTrue(sw.switch_on(OFF_2_TO_4, clock, .99))
        self.assertFalse(sw.switch_on(OFF_2_TO_4, clock, 1.))
        self.assertTrue(sw.switch_on(OFF_2_TO_4, clock, 2.))
        self.assertTrue(sw.switch_on(OFF_2_TO_4, (), 1.5))  # Unbaked (Sandbox): the first node.
        self.assertTrue(sw.switch_on((), clock, 1.5))


class LaneAccessTests(unittest.TestCase):
    def setUp(self):
        self.clip = Clip(0., 8., Settings(shapes=(
            Shape(uid='a', transforms=(new_transform('Rotate'), new_transform('Mirror'))), Shape(uid='b'))))

    def test_keys_blocks_labels_and_defaults(self):
        keys = lanes.automatable(self.clip)
        for key in (('on', 0), ('on', 0, 0), ('on', 0, 1), ('on', 1)): self.assertIn(key, keys)
        self.assertEqual(lanes.default_points(self.clip, ('on', 0)), sw.DEFAULT)
        self.assertEqual(lanes.default_points(self.clip)[0][2], 'linear')
        clip = lanes.set_points(self.clip, ('on', 0, 1), OFF_2_TO_4)
        clip = lanes.set_points(clip, ('on', 1), OFF_2_TO_4)
        self.assertEqual(clip.settings.shapes[0].transforms[1].on.points, OFF_2_TO_4)
        self.assertEqual(clip.settings.shapes[1].lanes, (('on', OFF_2_TO_4),))
        self.assertEqual([(b.key, b.title, b.keys) for b in lanes.blocks(clip)], [
            (('on', 0, 1), 'Cube #1 › 2. Mirror On/Off', (('on', 0, 1),)),
            (('on', 1), 'Cube #2 › On/Off', (('on', 1),))])
        self.assertEqual(lanes.label(clip, ('on', 1)), 'On/Off')
        self.assertEqual(lanes.static_value(clip, ('on', 1)), 1.)
        self.assertEqual(lanes.without_switches(clip), self.clip)
        with self.assertRaises(ValueError): lanes.set_points(self.clip, ('on', 0), ((0., -1., 'hold'), (4., 1., 'linear')))
        with self.assertRaises(ValueError): lanes.get_points(self.clip, ('on', 0, 2))

    def test_split_and_shift_keep_switch_lanes(self):
        clip = lanes.set_points(self.clip, ('on', 0), OFF_2_TO_4)
        project = split_clip(Project(clips=(clip,)), 0, 3.)
        left, right = project.clips
        self.assertEqual(lanes.get_points(left, ('on', 0)), OFF_2_TO_4)
        right_lane = lanes.get_points(right, ('on', 0))
        sw.validate_switch(right_lane, 'right')
        self.assertEqual(sw.blocks(right_lane, right.length), ((0., 1., False), (1., 5., True)))

    def test_history_labels(self):
        before = Project(clips=(self.clip,))
        after = Project(clips=(lanes.set_points(self.clip, ('on', 0), OFF_2_TO_4),))
        self.assertEqual(describe(before, after), 'Edit On/Off')
        self.assertEqual(describe(before, Project(clips=(replace(self.clip, muted=True),))), 'Mute clip')
        self.assertEqual(describe(Project(clips=(replace(self.clip, muted=True),)), before), 'Unmute clip')
        moved = Project(clips=(replace(self.clip, settings=replace(self.clip.settings, drawing_gain=.3)),))
        self.assertEqual(describe(before, moved), 'Edit scene')


class ScenePlanTests(unittest.TestCase):
    """Off removes the object (its share goes to the others) or drops the step, per pose frame."""
    def setUp(self):
        self.a = Shape(uid='a', position_x=-.3, shape_size=.4)
        self.b = Shape(uid='b', solid='Tetrahedron', position_x=.4, shape_size=.3)
        self.clock = (.5, 0.)  # Baked: 120 BPM, t = 0 at the clip start.

    def plan(self, frame, *shapes):
        return scene_plan(frame, 60., tuple(shapes), 'No changes', 8.)

    def assertSamePlan(self, left, right):
        for x, y in zip(left[:3], right[:3]): self.assertTrue(np.array_equal(x, y))

    def test_object_lane_off_equals_scene_without_it(self):
        b = replace(self.b, lanes=(('on', OFF_2_TO_4),), lane_clock=self.clock)
        self.assertSamePlan(self.plan(30, self.a, b), self.plan(30, self.a, self.b))  # Beat 1: on.
        self.assertSamePlan(self.plan(60, self.a, b), self.plan(60, self.a))  # Beat 2: off.
        self.assertSamePlan(self.plan(119, self.a, b), self.plan(119, self.a))
        self.assertSamePlan(self.plan(120, self.a, b), self.plan(120, self.a, self.b))  # Beat 4: on.
        self.assertTrue(posed_shapes((b,), 1.5)[0].muted)
        self.assertFalse(b.muted)

    def test_lane_combines_with_static_mute_and_other_lanes(self):
        b = replace(self.b, muted=True, lanes=(('on', sw.DEFAULT),), lane_clock=self.clock)
        self.assertSamePlan(self.plan(30, self.a, b), self.plan(30, self.a))
        sized = (('on', OFF_2_TO_4), ('shape_size', ((0., .5, 'hold'),)))
        b = replace(self.b, lanes=sized, lane_clock=self.clock)
        self.assertSamePlan(self.plan(30, self.a, b), self.plan(30, self.a, replace(self.b, shape_size=.15)))
        validate(Settings(shapes=(b,)))

    def test_transform_lane_off_equals_disabled_step(self):
        rotate = new_transform('Rotate').with_param('z', Param(40.))
        lane = replace(rotate, on=Param(1., '', OFF_2_TO_4, self.clock))
        a = replace(self.a, transforms=(lane,))
        self.assertSamePlan(self.plan(30, a), self.plan(30, replace(self.a, transforms=(rotate,))))
        self.assertSamePlan(self.plan(60, a), self.plan(60, replace(self.a, transforms=(replace(rotate, enabled=False),))))
        self.assertSamePlan(self.plan(60, replace(a, transforms=(replace(lane, enabled=False),))), self.plan(60, self.a))

    def test_no_lanes_keep_the_exact_default_path(self):
        shapes = (self.a, replace(self.b, transforms=(new_transform('Rotate'),)))
        self.assertIs(posed_shapes(shapes, 1.)[1], shapes[1])
        settings = Settings(source='Single tone', shapes=shapes)
        times = np.arange(2400) / 48000
        on = replace(settings, shapes=(replace(self.a, lanes=(('on', sw.DEFAULT),), lane_clock=self.clock), shapes[1]))
        self.assertTrue(np.array_equal(render(times, settings)[2], render(times, on)[2]))


class ComposerTests(unittest.TestCase):
    def setUp(self):
        a = Shape(uid='a', position_x=-.3, shape_size=.4, transforms=(new_transform('Rotate').with_param('z', Param(30.)),))
        b = Shape(uid='b', solid='Tetrahedron', position_x=.4, shape_size=.3)
        clip = Clip(0., 4., Settings(shapes=(a, b)))
        clip = lanes.set_points(clip, ('on', 1), sw.paint(sw.DEFAULT, 1.25, 2.5, False))
        clip = lanes.set_points(clip, ('on', 0, 0), sw.paint(sw.DEFAULT, 1.75, 3., False))
        self.project = Project(notes=(Note(0., 4., 57),), clips=(clip, Clip(4., 4., Settings(shapes=(a,)))))
        self.plain = Project(notes=self.project.notes, clips=(
            Clip(0., 4., Settings(shapes=(a, b))), self.project.clips[1]))

    def test_playback_blocks_equal_export(self):
        count = int(2 * 48000)  # Beats 0–4 at 120 BPM: every switch.
        times = np.arange(count) / 48000
        playback = np.concatenate([render_song(times[i:i + 512], self.project, MACHINE).astype('float32')
                                   for i in range(0, count, 512)])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'song.wav'
            export_song(path, self.project, MACHINE, 0., 4., depth=32)
            import soundfile
            exported, rate = soundfile.read(path, dtype='float32')
        self.assertEqual(rate, 48000)
        self.assertTrue(np.array_equal(playback, exported))
        plain = render_song(times, self.plain, MACHINE)
        whole = render_song(times, self.project, MACHINE)
        frame = 48000 // 60
        # Before the first switch (beat 1.25 = 0.625 s) nothing differs; while b is off it does.
        self.assertTrue(np.array_equal(whole[:30 * frame], plain[:30 * frame]))
        self.assertFalse(np.array_equal(whole[38 * frame:75 * frame], plain[38 * frame:75 * frame]))

    def test_baked_clock_follows_the_clip(self):
        settings = clip_settings(self.project.clips[0], MACHINE, self.project)
        self.assertTrue(settings.shapes[1].lane_clock)
        self.assertTrue(settings.shapes[0].transforms[0].on.clock)
        self.assertEqual(settings.shapes[0].transforms[0].params[0].clock, ())

    def test_clip_mute_is_a_gap(self):
        muted = replace(self.project, clips=(replace(self.project.clips[0], muted=True), self.project.clips[1]))
        validate_project(muted)
        times = np.arange(0, 4 * 48000, 97) / 48000
        song = render_song(times, muted, MACHINE)
        self.assertTrue(np.all(song[times < 2.] == 0))  # Beats 0–4 at 120 BPM.
        self.assertTrue(np.array_equal(song[times >= 2.], render_song(times[times >= 2.], self.project, MACHINE)))
        self.assertEqual(list(clip_at(muted, [1., 5.])), [-1, 1])
        self.assertIsNone(cycle_span(muted, .5))
        with self.assertRaises(ValueError): validate_project(replace(muted, clips=(replace(muted.clips[0], muted=1),)))


class FileTests(unittest.TestCase):
    def test_scene_round_trip_and_version_gates(self):
        step = replace(new_transform('Mirror'), on=Param(1., '', OFF_2_TO_4))
        settings = Settings(shapes=(Shape(uid='a', lanes=(('on', OFF_2_TO_4),), transforms=(step,)), Shape(uid='b')))
        document = json.loads(json.dumps(scene_document(settings, 'drawing', '')))
        self.assertEqual(document['version'], SCENE_VERSION)
        self.assertEqual(document['shapes'][0]['lanes'], {'on': [[0., 1., 'hold'], [2., 0., 'hold'], [4., 1., 'hold']]})
        self.assertIn('on', document['shapes'][0]['transforms'][0])
        self.assertNotIn('on', scene_document(Settings(shapes=(Shape(transforms=(new_transform('Mirror'),)),)), 'drawing', '')['shapes'][0]['transforms'][0])
        self.assertEqual(scene_from_document(document)[0].shapes, settings.shapes)
        for older in (dict(document, version=20),):
            with self.assertRaises(ValueError): scene_from_document(older)
        bad = json.loads(json.dumps(document)); bad['shapes'][0]['lanes']['on'][1][1] = .5
        with self.assertRaises(ValueError): scene_from_document(bad)

    def test_older_scenes_open_with_everything_on(self):
        settings = Settings(shapes=(Shape(uid='a', transforms=(new_transform('Mirror'),)),))
        document = json.loads(json.dumps(scene_document(settings, 'drawing', ''))); document['version'] = 20
        shapes = scene_from_document(document)[0].shapes
        self.assertEqual(shapes[0].lanes, ()); self.assertEqual(shapes[0].transforms[0].on, Param(1.))

    def test_project_clip_mute(self):
        project = Project(clips=(Clip(0., 4., Settings(), muted=True), Clip(4., 4., Settings())))
        document = json.loads(json.dumps(project_document(project)))
        self.assertEqual(document['version'], PROJECT_VERSION)
        self.assertEqual([c.get('muted') for c in document['clips']], [True, None])
        self.assertEqual(project_from_document(document), project)
        with self.assertRaises(ValueError): project_from_document(dict(document, version=6))
        older = json.loads(json.dumps(project_document(replace(project, clips=(replace(project.clips[0], muted=False), project.clips[1])))))
        older['version'] = 6
        self.assertFalse(any(c.muted for c in project_from_document(older).clips))

    def test_clipboard_keeps_clip_mute(self):
        from phosphor.engine import clipboard
        project = Project(clips=(Clip(0., 4., Settings(), muted=True), Clip(4., 4., Settings())))
        payload = clipboard.copy_clips(project, [0, 1])
        pasted, _ = clipboard.paste_clips(Project(clips=(Clip(0., 4., Settings()),)), json.loads(json.dumps(payload)), 8.)
        self.assertEqual([c.muted for c in pasted.clips], [False, True, False])


if __name__ == '__main__':
    unittest.main()
