"""Stage C: timing, glTF evaluation, bounded storage and shared playback."""
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
import copy
import unittest

import numpy as np

from phosphor.engine.mesh_formats import read_mesh, sample_channel, skin_positions, up_axis_vertices, AnimationSource
from phosphor.engine.model_animation import clip_time, Playback, ModelAnimation
from phosphor.engine.model_object import Model, MODEL_OBJECT, model_route
from phosphor.engine.signal_engine import Shape, Settings, posed_object, render, scene_plan
from phosphor.engine.transforms import Param, new_transform, SPIN
from phosphor.engine.composition import Clip, Project, Note, NoteSource, clip_settings, render_song
from phosphor.engine.tempo_map import BeatClock, TempoMap
from phosphor.storage.app_files import scene_document, scene_from_document
from phosphor.storage.app_files import SCENE_VERSION

FIXTURE = Path(__file__).parents[1] / 'fixtures/low_poly_character_walk_cycle/scene.gltf'


@lru_cache(maxsize=1)
def source():
    return read_mesh(FIXTURE).animation_source


@lru_cache(maxsize=1)
def model():
    return source().model(0, 300)[0]


class TimingTests(unittest.TestCase):
    def test_table(self):
        cases = [({}, 0., 0.), ({}, 2.5, .5), ({}, -0.5, 1.5),
                 ({'mode': 'Ping-pong'}, 2.5, 1.5), ({'mode': 'Ping-pong'}, -.5, .5),
                 ({'mode': 'Hold'}, 3., 2.), ({'mode': 'Hold'}, -1., 0.),
                 ({'speed': 2.}, .75, 1.5), ({'beats': 4., 'speed': 9., 'tempo': 60.}, 1., .5),
                 ({'trim_in': .5, 'trim_out': 1.5}, 1.25, .75),
                 ({'trim_in': .5, 'trim_out': 1.5, 'mode': 'Hold'}, 3., 1.5),
                 ({'mode': 'Ping-pong', 'beats': 4., 'tempo': 60.}, 2., 2.),
                 ({'mode': 'Ping-pong', 'beats': 4., 'tempo': 60.}, 4., 0.),
                 ({'trim_in': 1., 'trim_out': 1.}, 9., 1.)]
        for options, t, expected in cases:
            with self.subTest(options=options, t=t): self.assertAlmostEqual(clip_time(t, 2., **options), expected)

    def test_invalid(self):
        for kwargs in ({'mode': 'bad'}, {'tempo': 0}, {'speed': float('nan')}, {'beats': -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): clip_time(0., 2., **kwargs)


class InterpolationTests(unittest.TestCase):
    def test_linear_step_and_cubic(self):
        times = np.array([0., 2.])
        values = np.array([[0., 0., 0.], [2., 4., 6.]])
        np.testing.assert_array_equal(sample_channel(times, values, 1.), [1., 2., 3.])
        np.testing.assert_array_equal(sample_channel(times, values, 1., 'STEP'), values[0])
        cubic = np.array([[0., 0., 0.], values[0], [1., 0., 0.], [0., 0., 0.], values[1], [0., 0., 0.]])
        np.testing.assert_array_equal(sample_channel(times, cubic, 1., 'CUBICSPLINE'), [1.25, 2., 3.])
        np.testing.assert_array_equal(sample_channel(times, cubic, 2., 'CUBICSPLINE'), values[1])

    def test_slerp_and_cubic_quaternion_normalization(self):
        q = np.array([[0., 0., 0., 1.], [0., 1., 0., 0.]])
        np.testing.assert_allclose(sample_channel([0., 1.], q, .5, rotation=True), [0., 2**-.5, 0., 2**-.5])
        np.testing.assert_allclose(sample_channel([0., 1.], -q, .5, rotation=True), -sample_channel([0., 1.], q, .5, rotation=True))
        cubic = np.array([[0., 0., 0., 0.], q[0], [0., 0., 0., 0.], [0., 0., 0., 0.], q[1], [0., 0., 0., 0.]])
        self.assertAlmostEqual(np.linalg.norm(sample_channel([0., 1.], cubic, .5, 'CUBICSPLINE', True)), 1.)

    def test_bind_pose(self):
        # Fixture inverse bind matrices define the actual bind pose, which differs
        # from the export's default node TRS. Every bind joint cancels its inverse.
        for _, points, skin in source().primitives:
            joints, weights, _, inverse = skin
            result = skin_positions(points, joints, weights, np.linalg.inv(inverse), inverse)
            np.testing.assert_allclose(result, points, rtol=0, atol=1e-9)

    def test_rigid_hierarchy(self):
        # Reuse the supplied hierarchy and vertices, evaluating the mesh rigidly.
        rigid = copy.copy(source())
        node, points, _ = rigid.primitives[0]
        rigid.primitives = [(node, points, None)]
        world = rigid.worlds(0, .7)[node]
        np.testing.assert_allclose(rigid.positions(0, .7), points @ world[:3, :3].T + world[:3, 3], atol=1e-12)

    def test_morph_channels_are_ignored(self):
        document = copy.deepcopy(source().gltf)
        action = document['animations'][0]
        action['channels'].append({'sampler': len(action['samplers']), 'target': {'node': 10, 'path': 'weights'}})
        # Morph outputs have a different layout; stage C must not read them as TRS.
        action['samplers'].append({'input': -1000, 'output': -1000})
        ignored = AnimationSource(document, source().buffers, source().faces, source().lines, 'test', 'glTF')
        self.assertEqual(ignored.animations, source().animations)


class FixtureTests(unittest.TestCase):
    def test_picker(self):
        self.assertEqual(len(source().animations), 8)
        self.assertEqual(source().picker, (0, 4))
        self.assertEqual(source().animations[0].duration, 16.)
        self.assertEqual(source().animations[0].key_count, 769)

    def test_bake_seam_and_axes(self):
        frames = source().bake(0)
        self.assertEqual(frames.shape, (481, 1657, 3))
        # Identical endpoint keys; 1e-9 admits matrix/BLAS rounding, not a visible seam.
        np.testing.assert_allclose(frames[-1], frames[0], rtol=0, atol=1e-9)
        travel = source().worlds(0, .4)[11, :3, 3] - source().worlds(0, 0)[11, :3, 3]
        expected = up_axis_vertices(source().positions(0, .4), 'Z') - up_axis_vertices([travel], 'Z')[0]*[1, 0, 1]
        np.testing.assert_allclose(source().positions(0, .4, True, 'Z'), expected, atol=1e-12)

    def test_root_motion_and_empty_action(self):
        # EmptyAction targets Armature.001 itself with constant translation; it is
        # not a second action on its parent. Walk root_01 (11) carries hip travel.
        empty = [c for c in source().channels[4] if c[1] == 'translation' and c[0] < 12]
        self.assertEqual([c[0] for c in empty], [6])
        self.assertEqual(float(np.ptp(empty[0][3], axis=0).max()), 0.)
        t = .5
        raw = source().positions(0, t)
        inplace = source().positions(0, t, True)
        delta = source().worlds(0, t)[11, :3, 3] - source().worlds(0, 0)[11, :3, 3]
        np.testing.assert_allclose(raw-inplace, np.tile(delta*[1, 0, 1], (len(raw), 1)), atol=1e-12)

    def test_all_frame_normalization_and_selection(self):
        m = model()
        self.assertLessEqual(len(m.vertices), 1657)
        self.assertEqual(m.animation.count, 481)
        frames = m.animation.frames
        self.assertLess(abs(np.linalg.norm(frames, axis=2).max()-1.), 5e-5)
        np.testing.assert_array_equal(m.vertices, frames[0])
        np.testing.assert_array_equal(frames[-1], frames[0])
        # One box and scale for the whole motion, not separately unit-radius frames.
        radii = np.linalg.norm(frames, axis=2).max(axis=1)
        self.assertGreater(np.ptp(radii), .001)
        self.assertFalse(frames.flags.writeable)
        with self.assertRaises(ValueError): frames.setflags(write=True)


class StorageTests(unittest.TestCase):
    def test_quantized_roundtrip(self):
        frames = source().bake(0)[:4, :20]
        frames = frames / max(1., abs(frames).max())
        animation = ModelAnimation.from_frames(frames, 30., .1)
        loaded = ModelAnimation.read(animation.document())
        step = (frames.max(axis=(0, 1))-frames.min(axis=(0, 1)))/65535.
        self.assertLessEqual(float(np.max(abs(loaded.frames-frames))), float(step.max()/2+1e-15))
        self.assertEqual(loaded.document(), animation.document())
        self.assertEqual(animation, loaded)

    def test_model_and_scene_roundtrip(self):
        m = model()
        self.assertEqual(Model.read(m.document()), m)
        settings = Settings(shapes=(Shape(solid=MODEL_OBJECT, model=m, animation=Playback(2., 4., 'Hold', .2, 2.)),))
        document = scene_document(settings, 'drawing', '')
        self.assertEqual(document['version'], SCENE_VERSION)
        loaded = scene_from_document(document)[0]
        self.assertEqual(scene_document(loaded, 'drawing', ''), document)

    def test_scene17_unchanged(self):
        from tests.unit.test_model_object import CUBE
        from phosphor.engine.model_object import model_from_obj
        settings = Settings(shapes=(Shape(solid=MODEL_OBJECT, model=model_from_obj(CUBE)[0]),))
        doc = scene_document(settings, 'drawing', ''); doc['version'] = 17
        self.assertEqual(scene_from_document(doc)[0], settings)
        self.assertNotIn('animation', doc['shapes'][0])
        self.assertNotIn('animation', doc['shapes'][0]['model'])

    def test_limits_and_damaged_stream(self):
        value = model().animation.document()
        for changes in ({'count': 1000000}, {'data': 'eA=='}, {'vertex_count': 1000000}, {'fps': -1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ModelAnimation.read(dict(value, **changes))


class PlaybackTests(unittest.TestCase):
    def test_pose_stack_and_route_cache(self):
        m = model(); shape = Shape(solid=MODEL_OBJECT, model=m)
        route = model_route(m)
        np.testing.assert_array_equal(posed_object(.2, shape, include_depth=True)[0], m.animation.at(.2))
        self.assertIs(model_route(m), route)
        spin = new_transform(SPIN).with_param('z', Param(.25))
        self.assertFalse(np.array_equal(posed_object(.2, replace(shape, transforms=(spin,)))[0], posed_object(.2, shape)[0]))
        args = (12, 60., (shape,), 'No changes', 8.)
        self.assertIs(scene_plan(*args), scene_plan(*args))

    def test_tempo_map(self):
        animation = model().animation; playback = Playback(beats=4.)
        clock = BeatClock(TempoMap(120., ((2., 60.),)))
        np.testing.assert_array_equal(animation.at(2., playback, clock), animation.at(1.5, playback, 120.))
        np.testing.assert_array_equal(animation.at(2., Playback(), clock), animation.at(2.))

    def test_block_agreement_and_composer_extension(self):
        shape = Shape(solid=MODEL_OBJECT, model=model(), animation=Playback(trim_out=2.))
        settings = Settings(shapes=(shape,), source='Single tone')
        times = np.arange(960)/48000 + 2.25
        expected = render(times, settings)[0]
        blocks = np.concatenate([render(t, settings)[0] for t in np.array_split(times, 3)])
        np.testing.assert_allclose(blocks, expected, rtol=0, atol=1e-12)
        # Local animation time continues through a Composer clip longer than its trim.
        project = Project(notes=(Note(0., 16., 57),), clips=(Clip(0., 16., settings, 'drawing', local_time=True),))
        actual = render_song(times, project, Settings())
        expected = render(times, clip_settings(project.clips[0], Settings(), project.bpm), NoteSource(project))[2]
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)
        hold = replace(shape, animation=Playback(mode='Hold', trim_out=2.))
        np.testing.assert_array_equal(posed_object(20., hold)[0], posed_object(2., hold)[0])
