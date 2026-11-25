"""M3 click-free switching: sweep timing, continuity, playback = export and 0 = M2."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from phosphor.engine import switch_lane as sw
from phosphor.engine import switch_sweep
from phosphor.engine.composition import Clip, Note, Project, export_song, muted_edges, render_song
from phosphor.engine.signal_engine import Settings, Shape, render, validate
from phosphor.engine.transforms import new_transform, Param

RATE = 48000
OFF_2_TO_4 = ((0., 1., 'hold'), (2., 0., 'hold'), (4., 1., 'hold'))  # Off from 1 s to 2 s at 120 BPM.
TIMES = np.arange(int(2.5 * RATE)) / RATE


def machine(ms=0.):
    return replace(Settings(), sample_rate=RATE, switch_sweep_ms=ms)


def project(lane=OFF_2_TO_4, note=58, step_lane=None):
    """Two objects; the second carries the lane. Note 58 switches mid-cycle (a whole cycle would hide the step)."""
    a = Shape(position_x=-.4, shape_size=.4)
    if step_lane is not None:
        rotate = new_transform('Rotate').with_param('z', Param(60.))
        a = replace(a, transforms=(replace(rotate, on=Param(1., '', step_lane)),))
    b = Shape(solid='Text', text='A', position_x=.4, shape_size=.4, lanes=(('on', lane),) if lane else ())
    return Project(notes=(Note(0., 8., note),), clips=(Clip(0., 8., Settings(shapes=(a, b)), 'drawing'),))


def largest_step(signal, start, end):
    return float(np.abs(np.diff(signal[start:end], axis=0)).max())


class SweepTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.on = render_song(TIMES, project(()), machine())
        cls.off = render_song(TIMES, project(((0., 0., 'hold'),)), machine())

    def test_zero_is_the_m2_hard_switch(self):
        hard = render_song(TIMES, project(), machine())
        self.assertTrue(np.array_equal(hard, render_song(TIMES, project(), machine(0.))))
        np.testing.assert_allclose(hard[:RATE], self.on[:RATE], rtol=0, atol=1e-12)
        np.testing.assert_allclose(hard[RATE:2 * RATE], self.off[RATE:2 * RATE], rtol=0, atol=1e-12)

    def test_sweep_runs_before_off_and_after_on(self):
        for ms in (2., 5., 20.):
            with self.subTest(ms=ms):
                swept = render_song(TIMES, project(), machine(ms))
                n = round(ms * RATE / 1000)
                # Untouched before the off sweep, off between the switches, back on after the on sweep.
                np.testing.assert_allclose(swept[:RATE - n], self.on[:RATE - n], rtol=0, atol=1e-12)
                np.testing.assert_allclose(swept[RATE:2 * RATE], self.off[RATE:2 * RATE], rtol=0, atol=1e-12)
                np.testing.assert_allclose(swept[2 * RATE + n:], self.on[2 * RATE + n:], rtol=0, atol=1e-12)
                # Inside the sweeps it differs from both.
                for sweep in (slice(RATE - n, RATE), slice(2 * RATE, 2 * RATE + n)):
                    self.assertGreater(np.abs(swept[sweep] - self.on[sweep]).max(), 1e-3)
                    self.assertGreater(np.abs(swept[sweep] - self.off[sweep]).max(), 1e-3)

    def test_sweep_removes_the_step(self):
        steady = max(largest_step(self.on, 0, 2 * RATE), largest_step(self.off, 0, 2 * RATE))
        hard = render_song(TIMES, project(), machine())
        swept = render_song(TIMES, project(), machine(5.))
        for switch in (RATE, 2 * RATE):
            around = (switch - 2000, switch + 2000)
            self.assertGreater(largest_step(hard, *around), 1.5 * steady)
            self.assertLessEqual(largest_step(swept, *around), steady * 1.05)

    def test_no_lanes_keep_the_exact_path(self):
        plain = project(())
        self.assertTrue(np.array_equal(render_song(TIMES[:9600], plain, machine(20.)),
                                       render_song(TIMES[:9600], plain, machine())))
        settings = Settings(shapes=(Shape(),), switch_sweep_ms=20.)
        self.assertTrue(np.array_equal(render(TIMES[:4800], settings)[2],
                                       render(TIMES[:4800], replace(settings, switch_sweep_ms=0.))[2]))

    def test_transform_crossfade(self):
        steady_project = project((), step_lane=((0., 1., 'hold'),))
        steady = largest_step(render_song(TIMES, steady_project, machine()), 0, 2 * RATE)
        stepped = project((), step_lane=OFF_2_TO_4)
        hard = render_song(TIMES, stepped, machine())
        swept = render_song(TIMES, stepped, machine(5.))
        self.assertGreater(largest_step(hard, RATE - 2000, RATE + 2000), 1.5 * steady)
        self.assertLessEqual(largest_step(swept, RATE - 2000, RATE + 2000), steady * 1.05)
        np.testing.assert_allclose(swept[RATE:2 * RATE], hard[RATE:2 * RATE], rtol=0, atol=1e-12)


class ComposerSweepTests(unittest.TestCase):
    def test_playback_blocks_equal_export(self):
        song = project()
        count = int(2.5 * RATE)
        times = np.arange(count) / RATE
        playback = np.concatenate([render_song(times[i:i + 512], song, machine(5.)).astype('float32')
                                   for i in range(0, count, 512)])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'song.wav'
            export_song(path, song, machine(5.), 0., 5., depth=32)
            import soundfile
            exported, rate = soundfile.read(path, dtype='float32')
        self.assertEqual(rate, RATE)
        self.assertTrue(np.array_equal(playback, exported))

    def test_muted_clip_edges_fade_to_silence(self):
        song = project(())
        first = replace(song.clips[0], length=2.)
        song = replace(song, clips=(first, Clip(2., 2., first.settings, 'drawing', muted=True),
                                    Clip(4., 4., first.settings, 'drawing')))
        self.assertEqual(muted_edges(song, 0, 0.), ((1., False),))
        self.assertEqual(muted_edges(song, 2, 0.), ((2., True),))
        times = np.arange(int(2.5 * RATE)) / RATE
        hard = render_song(times, song, machine())
        swept = render_song(times, song, machine(5.))
        steady = largest_step(hard, 0, RATE - 2000)
        self.assertGreater(largest_step(hard, RATE - 100, RATE + 100), 1.5 * steady)
        for edge in (RATE, 2 * RATE):
            # Fading to the centre adds |position| / sweep length to the figure's own slope.
            self.assertLessEqual(largest_step(swept, edge - 2000, edge + 2000), steady * 1.25)
        self.assertTrue(np.all(swept[RATE:2 * RATE] == 0))
        # An unmuted gap is unchanged (only muted clips sweep).
        gap = replace(song, clips=(first, song.clips[2]))
        self.assertTrue(np.array_equal(render_song(times, gap, machine(5.)), render_song(times, gap, machine())))


class LiveToggleTests(unittest.TestCase):
    """Mute, Solo or bypass toggled while playing crossfades from the old render (playback only)."""
    def blocks(self, toggled, ms, composer=False):
        from unittest import mock
        from phosphor.audio.audio_preview import AudioPreview
        song = project((), note=58)
        scene = replace(song.clips[0].settings, sample_rate=RATE, switch_sweep_ms=ms)
        muted = replace(scene, shapes=(scene.shapes[0], replace(scene.shapes[1], muted=True)))
        with mock.patch.object(AudioPreview, 'running', new_callable=mock.PropertyMock, return_value=True):
            player = AudioPreview(); player.sample_rate = RATE
            player.parameters = (scene, 'drawing')
            if composer: player.composer = (song, 0.)
            player.frames = 3000  # Mid-cycle and after the start-up ramp.
            first = player._render_block(512); player.frames += 512
            if toggled:
                player.parameters = (muted, 'drawing')
                if composer: player.composer = (replace(song, clips=(replace(song.clips[0], settings=muted),)), 0.)
            second = player._render_block(512); player.frames += 512
            third = player._render_block(512)
        return np.concatenate((first, second, third)), muted

    def test_crossfade_after_a_live_mute(self):
        for composer in (False, True):
            with self.subTest(composer=composer):
                steady = largest_step(self.blocks(False, 5., composer)[0], 0, 1536)
                hard, muted = self.blocks(True, 0., composer)
                swept, _ = self.blocks(True, 5., composer)
                self.assertGreater(largest_step(hard, 500, 530), 1.5 * steady)
                self.assertLessEqual(largest_step(swept, 0, 1536), steady * 1.25)
                n = round(.005 * RATE)
                np.testing.assert_array_equal(swept[:512], hard[:512])
                np.testing.assert_array_equal(swept[512 + n:], hard[512 + n:])


class SweepModuleTests(unittest.TestCase):
    def test_segments_and_key_states(self):
        shapes = (Shape(), Shape(lanes=(('on', OFF_2_TO_4),)))
        events = [(('object', 1), 1., False)]
        keys = switch_sweep.segments(events, .004, .99, 1.)
        self.assertEqual(len(keys), switch_sweep.SWEEP_STEPS)
        self.assertAlmostEqual(keys[0][0], .996); self.assertEqual(keys[-1][1], 1.)
        self.assertIsNone(switch_sweep.key_state(shapes, events, .004, .996))
        self.assertEqual(switch_sweep.key_state(shapes, events, .004, 1.), ((1., None), (0., None)))
        self.assertAlmostEqual(switch_sweep.key_state(shapes, events, .004, .998)[1][0], .5)
        self.assertFalse(switch_sweep.switching(shapes[:1]))
        self.assertTrue(switch_sweep.switching(shapes))

    def test_validation(self):
        validate(Settings(switch_sweep_ms=switch_sweep.MAX_SWEEP_MS))
        for bad in (-1., switch_sweep.MAX_SWEEP_MS + 1, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError): validate(Settings(switch_sweep_ms=bad))
        self.assertEqual(sw.DEFAULT, ((0., 1., 'hold'),))

    def test_signal_preference(self):
        from phosphor.storage.signal_preferences import DEFAULT_SIGNAL_PREFERENCES, validated_signal_preferences
        from phosphor.engine.composition import clip_settings
        from phosphor.engine.route_plan import ROUTE_OFF
        self.assertEqual(DEFAULT_SIGNAL_PREFERENCES['switch_sweep_ms'], switch_sweep.DEFAULT_SWEEP_MS)
        older = dict(corner_dwell_us=0., shape_redraw=False, shape_redraw_count=1, route_mode=ROUTE_OFF)
        self.assertEqual(validated_signal_preferences(older)['switch_sweep_ms'], switch_sweep.DEFAULT_SWEEP_MS)
        self.assertEqual(validated_signal_preferences(dict(corner_dwell_us=5.))['switch_sweep_ms'], switch_sweep.DEFAULT_SWEEP_MS)
        self.assertEqual(validated_signal_preferences(dict(older, switch_sweep_ms=0))['switch_sweep_ms'], 0.)
        for bad in (-1., 51., True, 'x'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validated_signal_preferences(dict(older, switch_sweep_ms=bad))
        # The machine preference reaches every Composer clip, never the scene file.
        song = project()
        self.assertEqual(clip_settings(song.clips[0], machine(7.), song).switch_sweep_ms, 7.)
        from phosphor.storage.app_files import scene_document
        self.assertNotIn('switch_sweep_ms', scene_document(Settings(switch_sweep_ms=7.), 'drawing', '')['settings'])


if __name__ == '__main__':
    unittest.main()
