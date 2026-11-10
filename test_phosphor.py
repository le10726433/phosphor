"""Phosphor presets, energy deposits and preference handling, without Qt or a GPU."""
import math
import unittest
from pathlib import Path
import numpy as np
from app_files import DEFAULT_PREFERENCES, validated_preferences
from phosphor import (ENERGY_SCALE, BEAM_GAUSSIAN, BEAM_HARD, MAX_SIGMA, MIN_SIGMA, PHOSPHORS, PHOSPHOR_OFF, PHOSPHOR_PALETTE, beam_uniforms,
                      SPOT_TRIANGLES, deposit_vertices, gaussian_beam, new_sample_count, phosphor_uniforms, stroke_deposit)


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
        name = 'P7 blue / long yellow'
        single = phosphor_uniforms(name, '#00ff00', 0., .032, 1.)
        double = phosphor_uniforms(name, '#00ff00', 0., .016, 1.)
        self.assertAlmostEqual(single['slowKeep'], double['slowKeep']**2)
        self.assertEqual(phosphor_uniforms(name, '#00ff00', 0., 0., 1.)['fastKeep'], 1.)
        self.assertLess(single['fastKeep'], single['slowKeep'])

    def test_palette_preset_follows_palette_and_glow_decay(self):
        short = phosphor_uniforms(PHOSPHOR_PALETTE, '#ff8000', 0., .016, 1.)
        np.testing.assert_allclose(short['fastColor'], (1., 128/255, 0.))
        long = phosphor_uniforms(PHOSPHOR_PALETTE, '#ff8000', 2., .016, 2.)
        self.assertAlmostEqual(long['slowKeep'], math.exp(-.016))
        self.assertEqual(long['exposure'], short['exposure']*2)

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

    def test_compiled_shaders_are_present_and_current(self):
        folder = Path(__file__).parent/'assets'/'shaders'
        for name in ('phosphor_accumulate.frag', 'phosphor_blur.frag', 'phosphor_display.frag'):
            self.assertGreater((folder/f'{name}.qsb').stat().st_size, 0)

    def test_beam_profile_defaults_gaussian_and_rejects_unknown(self):
        self.assertEqual(DEFAULT_PREFERENCES['beam_profile'], BEAM_GAUSSIAN)
        old = {key: value for key, value in DEFAULT_PREFERENCES.items() if key != 'beam_profile'}
        self.assertEqual(validated_preferences(old)['beam_profile'], BEAM_GAUSSIAN)
        chosen = dict(DEFAULT_PREFERENCES, beam_profile=BEAM_HARD)
        self.assertEqual(validated_preferences(chosen), chosen)
        with self.assertRaises(ValueError):
            validated_preferences(dict(DEFAULT_PREFERENCES, beam_profile='Soft'))

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


if __name__ == '__main__':
    unittest.main()
