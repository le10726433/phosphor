"""Noise spectra, deterministic playback, migration and self-contained presets."""
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import numpy as np
from scipy.signal import welch

from phosphor.engine import noise_loops as loops
from phosphor.engine.transforms import (NOISE, NoiseOptions, Param, Transform, new_transform, curve_of,
                                        validate_transform, apply_stack, noise_curve, has_noise, uses_position)
from phosphor.engine.signal_engine import Settings, Shape
from phosphor.storage.app_files import scene_document, scene_from_document
from phosphor.storage.noise_presets import read_noise_presets, save_noise_preset, delete_noise_preset

CUSTOM = ((20., 0.), (1000., 0.), (4000., -24.), (20000., -24.))


def step(colour='White', rate='Sample', **params):
    result = replace(new_transform(NOISE), noise=NoiseOptions(colour, rate, CUSTOM if colour == 'Custom' else ()))
    for name, value in params.items(): result = result.with_param(name, Param(value))
    return result


class NoiseDocumentTests(unittest.TestCase):
    def document(self, transform):
        return scene_document(Settings(shapes=(Shape(transforms=(transform,)),)), 'drawing', '')

    def test_migration_and_version_gates(self):
        document = self.document(step()); document['version'] = 19
        document['shapes'][0]['transforms'] = [dict(kind='White noise', params=dict(intensity=.03, seed=7))]
        migrated = scene_from_document(document)[0].shapes[0].transforms[0]
        self.assertEqual(migrated, step(intensity=.03))
        document['version'] = 20
        with self.assertRaises(ValueError): scene_from_document(document)
        document = self.document(step()); document['version'] = 19
        with self.assertRaises(ValueError): scene_from_document(document)
        document['version'] = 20; document['shapes'][0]['transforms'][0]['params']['seed'] = 7
        with self.assertRaises(ValueError): scene_from_document(document)

    def test_all_colours_rates_round_trip_without_preset_file(self):
        for colour in ('White', 'Pink', 'Brownian', 'Custom'):
            for rate in ('Sample', 'Wobble'):
                transform = step(colour, rate)
                if colour == 'Custom': transform = replace(transform, noise=replace(transform.noise, preset='Absent preset'))
                with self.subTest(colour=colour, rate=rate):
                    self.assertEqual(Transform.read(transform.document()), transform)
                    with patch('phosphor.storage.noise_presets.read_noise_presets', side_effect=AssertionError('No preset lookup')):
                        self.assertEqual(scene_from_document(self.document(transform))[0].shapes[0].transforms[0], transform)

    def test_invalid_options_and_document_fields(self):
        for options in (NoiseOptions('Blue'), NoiseOptions(rate='Fast'), NoiseOptions(curve=CUSTOM),
                        NoiseOptions(preset='Name'), NoiseOptions('Custom'), NoiseOptions('Custom', curve=((30., 0.), (20., 0.))),
                        NoiseOptions('Custom', curve=((19., 0.), (20000., 0.))),
                        NoiseOptions('Custom', curve=((20., 0.), (20000., -73.))),
                        NoiseOptions('Custom', curve=((20., float('nan')), (20000., 0.))),
                        NoiseOptions('Custom', curve=tuple((20.+i, 0.) for i in range(33)))):
            with self.subTest(options=options), self.assertRaises(ValueError): validate_transform(replace(step(), noise=options))
        with self.assertRaises(ValueError): validate_transform(replace(step(), noise=None))
        with self.assertRaises(ValueError): validate_transform(replace(new_transform('Rotate'), noise=NoiseOptions()))
        for key, value in (('colour', 'Pink'), ('rate', 'Sample'), ('curve', []), ('preset', 'Name')):
            document = new_transform('Rotate').document(); document[key] = value
            with self.assertRaises(ValueError): Transform.read(document)
        for key in ('colour', 'rate'):
            document = step().document(); del document[key]
            with self.assertRaises(ValueError): Transform.read(document)

    def test_options_survive_lanes_clipboard_audition_and_history(self):
        from phosphor.engine.composition import Clip, Project
        from phosphor.engine import clipboard, lane_access
        from phosphor.engine.signal_engine import audition_shapes
        from phosphor.editor.history import History, State
        transform = replace(step('Custom', 'Wobble'), noise=NoiseOptions('Custom', 'Wobble', CUSTOM, 'Saved curve'))
        shape = Shape(uid='noise', transforms=(transform, transform))
        clip = Clip(0., 4., Settings(shapes=(shape,)))
        lane = ((0., 1., 'linear'), (4., 0., 'hold'))
        clip = lane_access.set_points(clip, ('transform', 0, 0, 'speed'), lane)
        self.assertEqual(clip.settings.shapes[0].transforms[0].noise, transform.noise)
        project = Project(clips=(clip,))
        payload = clipboard.from_text(clipboard.to_text(clipboard.copy_clips(project, (0,))))
        pasted, indices = clipboard.paste_clips(project, payload, 4.)
        self.assertEqual(pasted.clips[indices[0]].settings.shapes[0].transforms, clip.settings.shapes[0].transforms)
        solo = audition_shapes((shape,), (), (('noise', 1),))
        self.assertEqual(solo[0].transforms[0].noise, transform.noise)
        bypass = replace(shape, stack_bypassed=True)
        self.assertEqual(bypass.transforms, shape.transforms)
        history = History(); history.reset(State(project)); history.push('Edit scene', State(pasted))
        self.assertEqual(history.undo().project, project); self.assertEqual(history.redo().project, pasted)


class NoiseLoopTests(unittest.TestCase):
    def tearDown(self): loops.set_realtime(False)

    def test_rebuild_identical_rms_and_wrap(self):
        for curve in loops.BUILTINS:
            loops.clear(); first = loops.get(curve)
            loops.clear(); second = loops.get(curve)
            np.testing.assert_array_equal(first, second)
            self.assertEqual((first.shape, first.dtype), ((loops.N,), np.dtype('float32')))
            self.assertAlmostEqual(float(np.sqrt(np.mean(first.astype(float)**2))), loops.TARGET_RMS, delta=.001*loops.TARGET_RMS)
            self.assertLessEqual(abs(float(first[0])-float(first[-1])), 6*np.std(np.diff(first)))

    def test_measured_slopes_and_custom_curve(self):
        for curve, expected in zip(loops.BUILTINS, (0., -3., -6.)):
            hz, power = welch(loops.get(curve), loops.RATE, nperseg=16384)
            selected = (hz >= 100) & (hz <= 10000)
            slope = np.polyfit(np.log2(hz[selected]), 10*np.log10(power[selected]), 1)[0]
            self.assertAlmostEqual(slope, expected, delta=.5)
        hz, power = welch(loops.get(CUSTOM), loops.RATE, nperseg=16384)
        db = 10*np.log10(power)
        def band(f): return np.mean(db[(hz > f*.95) & (hz < f*1.05)])
        for frequency in (400., 800., 2000., 6000., 12000.):
            expected = np.interp(np.log10(frequency), np.log10(np.asarray(CUSTOM)[:, 0]), np.asarray(CUSTOM)[:, 1])
            self.assertAlmostEqual(band(frequency)-band(200), expected, delta=2.)
        measured = loops.spectrum(CUSTOM)
        self.assertEqual(len(measured[0]), 64); self.assertEqual(measured[1].max(), 0.)

    def test_device_rates_and_period(self):
        curve = loops.BUILTINS[0]
        low = loops.sample_noise(curve, np.array([0, 441, 44100, 441000]), 44100, True)
        high = loops.sample_noise(curve, np.array([0, 480, 48000, 480000]), 48000, True)
        np.testing.assert_array_equal(low, high)
        np.testing.assert_array_equal(high, loops.sample_noise(curve, np.array([0, 480, 48000, 480000])+loops.N, 48000, True))

    def test_realtime_requests_without_waiting(self):
        loops.clear(); entered, release = threading.Event(), threading.Event()
        original = loops.build
        def build(curve): entered.set(); release.wait(5); return original(curve)
        result = []
        def playback():
            loops.set_realtime(True)
            result.append((loops.realtime(), loops.sample_noise(CUSTOM, [0], 48000, wait=False)))
        with patch.object(loops, 'build', build):
            thread = threading.Thread(target=playback); thread.start(); thread.join(2)
            try:
                self.assertFalse(thread.is_alive()); self.assertEqual(result, [(True, None)])
                self.assertTrue(entered.wait(2)); self.assertFalse(loops.realtime())
            finally: release.set()
            self.assertIsNotNone(loops.get(CUSTOM))

    def test_custom_lru_and_builtin_retention(self):
        loops.clear(); builtin = loops.get(loops.BUILTINS[0])
        curves = [((20., 0.), (20000., float(-i-1))) for i in range(6)]
        for curve in curves[:4]: loops.get(curve)
        retained = loops.get(curves[0])
        for curve in curves[4:]: loops.get(curve)
        self.assertIs(loops.get(curves[0]), retained)
        self.assertIs(loops.get(loops.BUILTINS[0]), builtin)
        self.assertLessEqual(len([c for c in loops._cache if c not in loops.BUILTINS]), 4)


class WobbleTests(unittest.TestCase):
    def test_geometry_and_sample_selection(self):
        points, s = np.zeros((5, 3)), np.array([0., .2, .5, .8, 1.])
        def pose(transform, t=1.): return apply_stack(points, (transform,), t, s)[0]
        wobble = step('Pink', 'Wobble', intensity=.1)
        rigid = pose(wobble.with_param('detail', Param(0.)))
        np.testing.assert_array_equal(rigid, np.tile(rigid[0], (5, 1)))
        frozen = wobble.with_param('speed', Param(0.))
        np.testing.assert_array_equal(pose(frozen, 1.), pose(frozen, 9.))
        bent = pose(wobble)
        self.assertFalse(np.array_equal(bent[0], bent[2])); np.testing.assert_array_equal(bent[0], bent[-1])
        np.testing.assert_array_equal(pose(wobble.with_param('intensity', Param(0.))), points)
        self.assertFalse(np.array_equal(bent, pose(step('White', 'Wobble', intensity=.1))))
        sample = step('Brownian', intensity=.03)
        stack = (wobble, replace(step('White'), enabled=False), sample, step('Pink', intensity=.04))
        np.testing.assert_allclose(apply_stack(points, stack, 1., s)[1], .07)
        self.assertEqual(noise_curve(stack), curve_of(sample.noise)); self.assertTrue(has_noise(stack))
        self.assertFalse(has_noise((wobble,))); self.assertTrue(uses_position((wobble,)))
        self.assertIsNone(noise_curve((wobble,))); self.assertFalse(uses_position((replace(wobble, enabled=False),)))


class NoisePresetTests(unittest.TestCase):
    def test_save_replace_delete_and_damage(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'presets.json'
            self.assertEqual(read_noise_presets(path), {})
            save_noise_preset('  My curve  ', CUSTOM, path)
            self.assertEqual(read_noise_presets(path), {'My curve': CUSTOM})
            save_noise_preset('My curve', loops.BUILTINS[1], path)
            self.assertEqual(read_noise_presets(path)['My curve'], loops.BUILTINS[1])
            for name in ('', 'White', 'Pink', 'Brownian', 'Custom', 'a'*41):
                with self.subTest(name=name), self.assertRaises(ValueError): save_noise_preset(name, CUSTOM, path)
            self.assertEqual(delete_noise_preset('My curve', path), {})
            with patch.object(Path, 'read_text', return_value='broken'):
                with self.assertRaises(ValueError): read_noise_presets(path)


if __name__ == '__main__': unittest.main()
