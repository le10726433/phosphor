"""Phosphor presets, energy deposits and preference handling, without Qt or a GPU."""
import math
import unittest
from pathlib import Path
from phosphor.storage.app_paths import ASSETS_DIR
import numpy as np
from phosphor.storage.app_files import DEFAULT_PREFERENCES, validated_preferences
from phosphor.display.phosphor import (ENERGY_SCALE, BEAM_GAUSSIAN, BEAM_HARD, MAX_SIGMA, MIN_SIGMA, PHOSPHORS, PHOSPHOR_OFF, PHOSPHOR_PALETTE, beam_uniforms,
                      SPOT_TRIANGLES, BLOOM_PEAK, bloom_uniforms, PHOSPHOR_CUSTOM, DEFAULT_CUSTOM_PROFILE, afterglow_level_at,
                      decay_preview, phosphor_profile, preset_profile, validated_profile, deposit_vertices, gaussian_beam, new_sample_count, phosphor_uniforms, stroke_deposit)


class DepositTests(unittest.TestCase):
    def test_only_new_samples_are_deposited(self):
        self.assertEqual(new_sample_count(0, 1, 1920), 0)
        self.assertEqual(new_sample_count(-5, 1, 1920), 0)
        self.assertEqual(new_sample_count(768, 1, 1920), 768)
        self.assertEqual(new_sample_count(768, 4, 7677), 3072)
        self.assertEqual(new_sample_count(10**7, 1, 1920), 1919)
        self.assertEqual(new_sample_count(10, 1, 1), 0)

    def test_deposit_is_additive_and_fast_beam_gets_less_energy(self):
        vertices = stroke_deposit([(0, 0), (1, 0), (2, 0), (102, 0)], 1, 1., 1.)
        self.assertEqual(set(vertices['a']), {0})
        self.assertEqual(set(vertices['b']), {0})
        np.testing.assert_array_equal(vertices['r'], vertices['g'])
        self.assertGreater(vertices['r'][0], vertices['r'][-1])

    def test_strength_zero_is_even(self):
        vertices = stroke_deposit([(0, 0), (1, 0), (200, 0)], 1, 1., 0.)
        self.assertEqual(len(set(vertices['r'])), 1)

    def test_stationary_beam_deposits_one_centred_round_spot(self):
        vertices = deposit_vertices(np.full((100, 2), 7.), 2, 1.)
        self.assertEqual(len(vertices), len(SPOT_TRIANGLES))
        self.assertEqual(set(vertices['r']), {255})
        self.assertAlmostEqual(float(vertices['x'].max()), 8.)
        self.assertAlmostEqual(float(vertices['y'].min()), 6.)

    def test_held_samples_add_energy_until_the_deposit_saturates(self):
        def level(held):
            path = np.concatenate((np.full((held+1, 2), 0.), [(40., 0.)]))
            return int(stroke_deposit(path, 4, 1., 1.)['r'][-1])
        self.assertEqual(level(1), 38)
        self.assertEqual(level(2), 75)
        self.assertEqual(level(50), 255)

    def test_corner_dwell_adds_a_spot_without_losing_the_lines(self):
        path = [(0., 0.), (10., 0.), (10., 0.), (10., 0.), (10., 10.)]
        vertices = stroke_deposit(path, 1, 1., 1.)
        self.assertEqual(len(vertices), 2*6+len(SPOT_TRIANGLES))

    def test_slow_drift_makes_separate_spots_and_a_separate_spot_width(self):
        path = np.column_stack((np.arange(400)*.04, np.zeros(400)))
        self.assertGreater(len(stroke_deposit(path, 2, 1., 1.)), 5*len(SPOT_TRIANGLES))
        spot = deposit_vertices(np.zeros((10, 2)), 1, 1., (), 6.)
        self.assertAlmostEqual(float(spot['x'].max()), 3.)

    def test_glow_bands_add_wider_low_energy_strokes(self):
        points = [(0, 0), (2, 0), (4, 0)]
        core = deposit_vertices(points, 1, 1.)
        both = deposit_vertices(points, 1, 1., ((7, .2), (3, .35)))
        self.assertEqual(len(both), len(core)*3)
        self.assertLess(both['r'][0], core['r'][0])


class PresetTests(unittest.TestCase):
    def test_decay_is_exponential_and_frame_rate_independent(self):
        profile = preset_profile('P7 blue / long yellow', '#00ff00')
        single = phosphor_uniforms(profile, .032, 1.)
        double = phosphor_uniforms(profile, .016, 1.)
        self.assertAlmostEqual(single['slowKeep'], double['slowKeep']**2)
        self.assertEqual(phosphor_uniforms(profile, 0., 1.)['fastKeep'], 1.)
        self.assertLess(single['fastKeep'], single['slowKeep'])

    def test_presets_keep_their_display_uniforms(self):
        # Before profiles, uniforms came straight from the preset tables.
        for name, preset in PHOSPHORS.items():
            if not preset:
                continue
            u = phosphor_uniforms(phosphor_profile(name, '#ff8000', DEFAULT_CUSTOM_PROFILE), .016, 2.)
            self.assertAlmostEqual(u['slowKeep'], math.exp(-.016/preset['slow']))
            self.assertAlmostEqual(u['slowGain'], preset['slow_gain']*min(1., .05/preset['slow']))
            np.testing.assert_allclose(u['fastColor'], preset.get('fast_color', (1., 128/255, 0.)), atol=1/255)
            np.testing.assert_allclose(u['slowColor'], preset.get('slow_color', (1., 128/255, 0.)), atol=1/255)
            self.assertEqual(u['exposure'], 2*phosphor_uniforms(phosphor_profile(name, '#ff8000', None), .016, 1.)['exposure'])
        self.assertIsNone(phosphor_profile(PHOSPHOR_OFF, '#ff8000', DEFAULT_CUSTOM_PROFILE))

    def test_palette_preset_follows_palette_only(self):
        profile = phosphor_profile(PHOSPHOR_PALETTE, '#ff8000', DEFAULT_CUSTOM_PROFILE)
        self.assertEqual((profile['flash_color'], profile['afterglow_color']), ('#ff8000', '#ff8000'))
        self.assertEqual(profile['afterglow_time'], PHOSPHORS[PHOSPHOR_PALETTE]['slow'])

    def test_custom_profile_is_used_and_validated(self):
        custom = dict(DEFAULT_CUSTOM_PROFILE, afterglow_time=2., flash_color='#FF0000')
        profile = phosphor_profile(PHOSPHOR_CUSTOM, '#00ff00', custom)
        self.assertEqual(profile['flash_color'], '#ff0000')
        u = phosphor_uniforms(profile, .1, 1.)
        self.assertAlmostEqual(u['slowKeep'], math.exp(-.05))
        self.assertEqual(u['fastColor'], (1., 0., 0.))
        for bad in (dict(custom, afterglow_time=0.), dict(custom, afterglow_level=1.5), dict(custom, flash_color='red'),
                    dict(custom, afterglow_time=float('nan')), {k: v for k, v in custom.items() if k != 'flash_time'}, None):
            with self.assertRaises(ValueError):
                validated_profile(bad)

    def test_decay_preview_matches_the_display_and_fades_between_colours(self):
        profile = preset_profile('P7 blue / long yellow', '#000000')
        times = np.array([.001, .02, .2, 2.])
        flash, afterglow, light = decay_preview(profile, 5., times)
        self.assertTrue(np.all(np.diff(flash) < 0) and np.all(np.diff(afterglow) < 0))
        self.assertTrue(np.all(light <= 1) and np.all(light >= 0))
        # Blue flash first, yellow afterglow later.
        self.assertGreater(light[0, 2], light[0, 0])
        self.assertGreater(light[2, 0], light[2, 2])
        # Dragging the afterglow point inverts the afterglow curve at its decay time.
        _, at_decay, _ = decay_preview(profile, 5., [profile['afterglow_time']])
        self.assertAlmostEqual(afterglow_level_at(profile, 5., at_decay[0]), profile['afterglow_level'])
        self.assertEqual(afterglow_level_at(profile, 5., 2.), 1.)
        self.assertEqual(afterglow_level_at(profile, 5., -1.), 0.)

    def test_every_preset_has_ordered_decay(self):
        for name, preset in PHOSPHORS.items():
            if preset:
                self.assertLess(preset['fast'], preset['slow'], name)

    def test_gaussian_width_follows_thickness_and_stays_bounded(self):
        core, sigma = gaussian_beam(3., 2.)
        self.assertEqual(core, 1.)
        # The blur and the core's own width together give the requested width.
        self.assertAlmostEqual(math.sqrt(sigma**2+(core*2)**2/12)*2*math.sqrt(2*math.log(2)), 6.)
        self.assertEqual(gaussian_beam(.5, 1.)[1], MIN_SIGMA)
        # The shader's tap count assumes the blur never exceeds MAX_SIGMA.
        self.assertTrue(MAX_SIGMA-.1 < gaussian_beam(100., 2.)[1] <= MAX_SIGMA)

    def test_hard_profile_bypasses_blur(self):
        self.assertEqual(beam_uniforms(BEAM_HARD, 3., 2., 700, 700), dict(gaussian=False, depositGain=1.))
        blurred = beam_uniforms(BEAM_GAUSSIAN, 3., 2., 500, 250)
        self.assertTrue(blurred['gaussian'])
        self.assertEqual((blurred['texelX'], blurred['texelY']), ((.001, 0.), (0., .002)))

    def test_gaussian_energy_matches_at_any_speed_and_brightness(self):
        def energy(vertices):
            t = np.stack((vertices['x'], vertices['y']), 1).reshape(-1, 3, 2).astype(float)
            area = np.abs((t[:, 1, 0]-t[:, 0, 0])*(t[:, 2, 1]-t[:, 0, 1])-(t[:, 1, 1]-t[:, 0, 1])*(t[:, 2, 0]-t[:, 0, 0]))/2
            return float((area*vertices['r'].reshape(-1, 3)[:, 0]).sum())
        for strength in (0., .5, 1.):
            for thickness in (1., 3., 6.):
                core, _ = gaussian_beam(thickness, 2., strength)
                self.assertLessEqual(core, thickness)
                for step in (.3, 2., 10.):
                    path = np.column_stack((np.arange(60)*step, np.zeros(60)))
                    hard = energy(stroke_deposit(path, thickness, 1., strength))
                    gaussian = energy(stroke_deposit(path, core, 1., strength, None, thickness))
                    with self.subTest(strength=strength, thickness=thickness, step=step):
                        self.assertAlmostEqual(gaussian/hard, 1., delta=.02)

    def test_gaussian_core_widens_instead_of_clipping(self):
        core, _ = gaussian_beam(6., 2., 0.)
        self.assertGreater(core, 1.)
        level = int(stroke_deposit([(0., 0.), (.1, 0.), (.2, 0.)], core, 1., 0., None, 6.)['r'].max())
        # Fits in 8 bits with no energy lost: level times width equals Hard edge's.
        self.assertLessEqual(level, 255)
        self.assertAlmostEqual(level*core/(ENERGY_SCALE*6.), 1., delta=.01)

    def test_gaussian_keeps_hard_edge_total_energy(self):
        """Hard stroke energy across the beam equals the thin core's after a normalized blur."""
        self.assertEqual(beam_uniforms(BEAM_GAUSSIAN, 3., 1., 500, 500)['depositGain'], 1.)
        path = [(0., 0.), (.7, 0.), (1.4, 0.)]
        for thickness in (2., 3., 6.):
            core, sigma = gaussian_beam(thickness, 1.)
            hard = int(stroke_deposit(path, thickness, 1., 1.)['r'][0])*thickness
            gaussian = int(stroke_deposit(path, core, 1., 1.)['r'][0])*core
            self.assertAlmostEqual(gaussian/hard, 1., delta=.03)
            # A normalized blur spreads that energy: the peak sits under Hard edge's, lowest
            # for thin traces where sigma is held at its 1 px minimum.
            peak = gaussian/(sigma*math.sqrt(2*math.pi))/(hard/thickness)
            self.assertTrue(.75 < peak < 1., (thickness, peak))

    def test_bloom_is_off_without_glow(self):
        for args in ((False, 1, 12, 1), (True, 1, 0, 1), (True, 1, 12, 0)):
            uniforms = bloom_uniforms(*args, 2., 800, 600)
            self.assertFalse(uniforms['bloomOn'], args)
            self.assertEqual(uniforms['bloomGain'], 0.)
        self.assertFalse(bloom_uniforms(True, 1, 12, 1, 2., 0, 600)['bloomOn'])

    def test_bloom_downsamples_so_wide_glow_stays_cheap(self):
        previous = 0
        for size in (1, 3, 6, 12, 24):
            uniforms = bloom_uniforms(True, 1, size, 1, 2., 1000, 700)
            factor = uniforms['bloomFactor']
            self.assertGreaterEqual(factor, previous)
            previous = factor
            # The low-resolution blur radius stays within the shader's 32 taps.
            self.assertLessEqual(3*uniforms['bloomSigma'], 32, size)
            width, height = uniforms['bloomSize']
            self.assertEqual((width, height), (math.ceil(2000/factor), math.ceil(1400/factor)))
            self.assertAlmostEqual(uniforms['bloomTexelX'][0], 1/width)
            self.assertAlmostEqual(uniforms['energyTexel'][1], 1/1400)
        self.assertEqual(bloom_uniforms(True, 1, 24, 1, 2., 1000, 700)['bloomFactor'], 8.)
        self.assertEqual(bloom_uniforms(True, 1, 3, 1, 1., 1000, 700)['bloomFactor'], 1.)

    def test_bloom_peak_follows_intensity_not_size(self):
        def peak(thickness, size, intensity, ratio):
            u = bloom_uniforms(True, thickness, size, intensity, ratio, 1000, 700)
            # A line of unit energy and physical width t, blurred by the full-resolution sigma.
            sigma = u['bloomSigma']*u['bloomFactor']
            return u['bloomGain']*thickness*ratio/(math.sqrt(2*math.pi)*sigma)
        for thickness in (.5, 1, 4):
            for size in (2, 9, 24):
                for ratio in (1., 2.):
                    self.assertAlmostEqual(peak(thickness, size, 1, ratio), BLOOM_PEAK)
        self.assertAlmostEqual(peak(1, 12, 2.5, 2.), 2.5*BLOOM_PEAK)

    def test_compiled_shaders_are_present_and_current(self):
        folder = ASSETS_DIR/'shaders'
        for name in ('phosphor_accumulate.frag', 'phosphor_blur.frag', 'phosphor_display.frag',
                     'phosphor_bloom_down.frag', 'phosphor_bloom_blur.frag'):
            self.assertGreater((folder/f'{name}.qsb').stat().st_size, 0)

    def test_beam_profile_defaults_gaussian_and_rejects_unknown(self):
        self.assertEqual(DEFAULT_PREFERENCES['beam_profile'], BEAM_GAUSSIAN)
        old = {key: value for key, value in DEFAULT_PREFERENCES.items() if key != 'beam_profile'}
        self.assertEqual(validated_preferences(old)['beam_profile'], BEAM_GAUSSIAN)
        chosen = dict(DEFAULT_PREFERENCES, beam_profile=BEAM_HARD)
        self.assertEqual(validated_preferences(chosen), chosen)
        with self.assertRaises(ValueError):
            validated_preferences(dict(DEFAULT_PREFERENCES, beam_profile='Soft'))

    def test_grid_toggle_and_brightness_range_preferences(self):
        self.assertTrue(DEFAULT_PREFERENCES['show_grid'])
        old = {key: value for key, value in DEFAULT_PREFERENCES.items() if key != 'show_grid'}
        self.assertTrue(validated_preferences(old)['show_grid'])
        hidden = dict(DEFAULT_PREFERENCES, show_grid=False)
        self.assertEqual(validated_preferences(hidden), hidden)
        self.assertEqual(validated_preferences(dict(DEFAULT_PREFERENCES, beam_intensity=64.))['beam_intensity'], 64.)
        for bad in (dict(DEFAULT_PREFERENCES, show_grid=1), dict(DEFAULT_PREFERENCES, beam_intensity=64.5)):
            with self.assertRaises(ValueError):
                validated_preferences(bad)

    def test_preferences_keep_custom_profile_and_fill_it_in_for_old_files(self):
        saved = dict(DEFAULT_PREFERENCES, phosphor=PHOSPHOR_CUSTOM, phosphor_custom=dict(DEFAULT_CUSTOM_PROFILE, afterglow_level=.5))
        self.assertEqual(validated_preferences(saved), saved)
        old = {key: value for key, value in DEFAULT_PREFERENCES.items() if key != 'phosphor_custom'}
        self.assertEqual(validated_preferences(old)['phosphor_custom'], DEFAULT_CUSTOM_PROFILE)
        with self.assertRaises(ValueError):
            validated_preferences(dict(saved, phosphor_custom=dict(DEFAULT_CUSTOM_PROFILE, flash_time=9.)))

    def test_preferences_default_on_keep_saved_off_and_reject_unknown(self):
        self.assertEqual(DEFAULT_PREFERENCES['phosphor'], 'P31 green')
        saved_palette = dict(DEFAULT_PREFERENCES, phosphor=PHOSPHOR_PALETTE)
        self.assertEqual(validated_preferences(saved_palette), saved_palette)
        saved_off = dict(DEFAULT_PREFERENCES, phosphor=PHOSPHOR_OFF)
        self.assertEqual(validated_preferences(saved_off), saved_off)
        old = {key: value for key, value in DEFAULT_PREFERENCES.items() if key not in ('phosphor', 'beam_intensity', 'beam_brightness')}
        self.assertEqual(validated_preferences(old), DEFAULT_PREFERENCES)
        chosen = dict(DEFAULT_PREFERENCES, phosphor='P31 green')
        self.assertEqual(validated_preferences(chosen), chosen)
        for invalid in ('Unknown', 3, None):
            with self.assertRaises(ValueError):
                validated_preferences(dict(DEFAULT_PREFERENCES, phosphor=invalid))


class DisplayChainTests(unittest.TestCase):
    def test_feedback_never_uses_a_recursive_layer(self):
        # Resizing a recursive ShaderEffectSource crashes Qt 6.10.3 on Direct3D 11.
        source = (Path(__file__).resolve().parents[2]/'phosphor'/'display'/'qt_phosphor.py').read_text(encoding='utf-8')
        self.assertNotIn('recursive: true', source)
        self.assertIn('energyCopy.scheduleUpdate()', source)


if __name__ == '__main__':
    unittest.main()
