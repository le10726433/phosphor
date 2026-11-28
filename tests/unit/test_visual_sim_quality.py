"""Visual simulation upgrades, graphics quality tiers, graticule geometry, and preferences."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np

from phosphor.storage.app_files import DEFAULT_PREFERENCES, validated_preferences
from phosphor.storage.build_info import VERSION
from phosphor.display.glow_trail import VISUAL_DEFAULTS, VISUAL_RANGES
from phosphor.display.phosphor import (
    BEAM_GAUSSIAN, DEFAULT_GRAPHICS_QUALITY, DEFAULT_GRID_STYLE, GRAPHICS_QUALITIES,
    GRID_GRATICULE, GRID_SIMPLE, GRID_STYLES, PHOSPHOR_P31,
    QUALITY_HIGH, QUALITY_LOW, QUALITY_MEDIUM, QUALITY_ULTRA,
    halo_uniforms, quality_uniforms,
)
from phosphor.display.frame_step import step_display
from phosphor.display.qt_geometry import grid_lines, graticule_lines


class VisualSimPreferenceTests(unittest.TestCase):
    def test_build_version_bumped(self):
        self.assertRegex(VERSION, r'^0\.9[2-9]\.\d+(-dev)?$')

    def test_defaults_and_migration_of_new_visual_preferences(self):
        self.assertEqual(DEFAULT_PREFERENCES['graphics_quality'], QUALITY_HIGH)
        self.assertEqual(DEFAULT_PREFERENCES['grid_style'], GRID_GRATICULE)
        self.assertEqual(DEFAULT_PREFERENCES['glass_vignette'], .15)
        self.assertEqual(VISUAL_DEFAULTS['glass_vignette'], .15)
        self.assertEqual(VISUAL_RANGES['glass_vignette'], (0., 1.))
        old = {k: v for k, v in DEFAULT_PREFERENCES.items()
               if k not in ('graphics_quality', 'grid_style', 'glass_vignette')}
        migrated = validated_preferences(old)
        self.assertEqual(migrated['graphics_quality'], DEFAULT_GRAPHICS_QUALITY)
        self.assertEqual(migrated['grid_style'], DEFAULT_GRID_STYLE)
        self.assertEqual(migrated['glass_vignette'], .15)

    def test_valid_and_invalid_quality_and_grid_style(self):
        for q in GRAPHICS_QUALITIES:
            self.assertEqual(validated_preferences(dict(DEFAULT_PREFERENCES, graphics_quality=q))['graphics_quality'], q)
        for bad in ('Extreme', '', None, 2):
            prefs = validated_preferences(dict(DEFAULT_PREFERENCES, graphics_quality=bad, grid_divisions=17))
            self.assertEqual(prefs['graphics_quality'], DEFAULT_GRAPHICS_QUALITY)
            self.assertEqual(prefs['grid_divisions'], 17)
        for style in GRID_STYLES:
            self.assertEqual(validated_preferences(dict(DEFAULT_PREFERENCES, grid_style=style))['grid_style'], style)
        for bad in ('Tektronix', '', None, 0):
            prefs = validated_preferences(dict(DEFAULT_PREFERENCES, grid_style=bad, grid_divisions=17))
            self.assertEqual(prefs['grid_style'], DEFAULT_GRID_STYLE)
            self.assertEqual(prefs['grid_divisions'], 17)


class QualityTierUniformTests(unittest.TestCase):
    def _step(self, **overrides):
        points = np.array([(0., 0.), (20., 0.), (20., 20.)], dtype=np.float32)
        scope = SimpleNamespace(width=lambda: 720., height=lambda: 720., screen_points=lambda: points)
        view = SimpleNamespace(width=lambda: 720., height=lambda: 720., devicePixelRatio=lambda: 2.)
        layer = Mock()
        host = SimpleNamespace(scope=scope, view=view, phosphor=layer)
        prefs = dict(DEFAULT_PREFERENCES, phosphor=PHOSPHOR_P31, beam_profile=BEAM_GAUSSIAN,
                     crt_glow=True, trace_interpolation='Off')
        prefs.update(overrides)
        step_display(host, prefs, points, 2, .016)
        _, uniforms = layer.step.call_args.args
        return uniforms, prefs

    def test_low_tier_bypasses_blur_bloom_and_sim_without_mutating_prefs(self):
        u, prefs = self._step(graphics_quality=QUALITY_LOW)
        self.assertFalse(u['gaussian'])
        self.assertFalse(u['bloomOn'])
        self.assertEqual(u['bloomGain'], 0.)
        self.assertFalse(u['haloOn'])
        for k in ('softSat', 'powerLaw', 'cascade', 'haloGain', 'linearTone', 'vignette', 'edgeGraticule'):
            self.assertEqual(u[k], 0., k)
        # User preferences are independent and untouched.
        self.assertEqual(prefs['beam_profile'], BEAM_GAUSSIAN)
        self.assertTrue(prefs['crt_glow'])

    def test_medium_tier_enables_gaussian_bloom_and_soft_saturation(self):
        u, _ = self._step(graphics_quality=QUALITY_MEDIUM)
        self.assertTrue(u['gaussian'])
        self.assertTrue(u['bloomOn'])
        self.assertFalse(u['haloOn'])
        self.assertEqual(u['softSat'], 1.)
        for k in ('powerLaw', 'cascade', 'haloGain', 'linearTone', 'vignette', 'edgeGraticule'):
            self.assertEqual(u[k], 0., k)

    def test_high_tier_enables_becquerel_linear_tone_scatter_vignette_and_p7_cascade(self):
        u_p31, _ = self._step(graphics_quality=QUALITY_HIGH, phosphor=PHOSPHOR_P31, glass_vignette=.25)
        self.assertTrue(u_p31['gaussian'])
        self.assertTrue(u_p31['bloomOn'])
        # High runs the real coarse halo pass (no offset bloom taps, which drew ghost lines).
        self.assertTrue(u_p31['haloOn'])
        self.assertEqual(u_p31['softSat'], 1.)
        self.assertGreater(u_p31['powerLaw'], 0.)
        self.assertEqual(u_p31['cascade'], 0.)
        self.assertAlmostEqual(u_p31['haloGain'], .14)
        self.assertEqual(u_p31['haloTexOn'], 1.)
        self.assertIn('haloSize', u_p31)
        self.assertEqual(u_p31['linearTone'], 1.)
        self.assertAlmostEqual(u_p31['vignette'], .25)
        self.assertEqual(u_p31['edgeGraticule'], 0.)

        u_p7, _ = self._step(graphics_quality=QUALITY_HIGH, phosphor='P7 blue / long yellow')
        self.assertGreater(u_p7['cascade'], 0.)

    def test_ultra_tier_enables_dual_scale_halo_and_edge_lit_graticule(self):
        u_grat, _ = self._step(graphics_quality=QUALITY_ULTRA, show_grid=True, grid_style=GRID_GRATICULE)
        self.assertTrue(u_grat['haloOn'])
        self.assertEqual(u_grat['haloTexOn'], 1.)
        self.assertGreater(u_grat['haloGain'], .14)
        self.assertGreater(u_grat['edgeGraticule'], 0.)
        self.assertIn('haloSize', u_grat)

        u_simple, _ = self._step(graphics_quality=QUALITY_ULTRA, show_grid=True, grid_style=GRID_SIMPLE)
        self.assertEqual(u_simple['edgeGraticule'], 0.)
        u_hidden, _ = self._step(graphics_quality=QUALITY_ULTRA, show_grid=False, grid_style=GRID_GRATICULE)
        self.assertEqual(u_hidden['edgeGraticule'], 0.)

    def test_halo_uniforms_disabled_and_scaled(self):
        self.assertEqual(halo_uniforms(False, 720, 720, 2.), dict(haloOn=False, haloTexOn=0.))
        u = halo_uniforms(True, 720, 720, 2.)
        self.assertTrue(u['haloOn'])
        self.assertEqual(u['haloSize'], (120, 120))

    def test_halo_needs_bloom_and_high_or_ultra(self):
        # Without bloom there is no halo on any tier; Low/Medium never run the pass.
        for quality in (QUALITY_HIGH, QUALITY_ULTRA):
            u, _ = self._step(graphics_quality=quality, crt_glow=False)
            self.assertFalse(u['haloOn'], quality)
            self.assertEqual(u['haloTexOn'], 0., quality)
        for quality in (QUALITY_LOW, QUALITY_MEDIUM):
            u, _ = self._step(graphics_quality=quality)
            self.assertFalse(u['haloOn'], quality)
            self.assertEqual(u['haloGain'], 0., quality)


class TierGatedControlTests(unittest.TestCase):
    def test_low_disables_grid_style_and_beam_profile_and_keeps_their_selections(self):
        # B2 #6: Low draws the Simple grid and a hard-edged beam whatever is selected.
        from PySide6.QtWidgets import QApplication
        from phosphor.editor.preference_panels import AppearanceEditor
        from phosphor.editor.qt_app import SliderInput
        app = QApplication.instance() or QApplication([])
        values = dict(DEFAULT_PREFERENCES, graphics_quality=QUALITY_LOW, show_grid=True,
                      grid_style=GRID_GRATICULE, beam_profile=BEAM_GAUSSIAN)
        editor = AppearanceEditor(SliderInput, values)
        try:
            # On panel load.
            self.assertFalse(editor.grid_style.isEnabled())
            self.assertFalse(editor.beam_profile.isEnabled())
            self.assertFalse(editor.beam_profile_label.isEnabled())
            for widget in (editor.grid_style, editor.beam_profile, editor.beam_profile_label):
                self.assertIn('Low', widget.toolTip())
            editor.graphics_quality.setCurrentText(QUALITY_MEDIUM)
            self.assertTrue(editor.grid_style.isEnabled())
            self.assertTrue(editor.beam_profile.isEnabled())
            self.assertEqual((editor.values()['grid_style'], editor.values()['beam_profile']),
                             (GRID_GRATICULE, BEAM_GAUSSIAN))
            editor.grid.setChecked(False)
            self.assertFalse(editor.grid_style.isEnabled())
            editor.grid.setChecked(True)
            editor.graphics_quality.setCurrentText(QUALITY_LOW)
            self.assertFalse(editor.grid_style.isEnabled())
            self.assertEqual(editor.values()['grid_style'], GRID_GRATICULE)
            editor.load(dict(values, graphics_quality=QUALITY_HIGH))
            self.assertTrue(editor.grid_style.isEnabled() and editor.beam_profile.isEnabled())
        finally:
            editor.deleteLater(); app.processEvents()


class GraticuleGeometryTests(unittest.TestCase):
    def test_graticule_adds_crosshairs_border_and_minor_ticks(self):
        simple = grid_lines(720., 720., 8)
        major, accent = graticule_lines(720., 720., 8)
        self.assertEqual(major, simple)
        # 2 center crosshairs + 4 border edges + minor ticks along both axes
        self.assertGreater(len(accent), 40)
        self.assertIn([(360.0, 0.0), (360.0, 720.0)], accent)
        self.assertIn([(0.0, 360.0), (720.0, 360.0)], accent)

    def test_graticule_handles_degenerate_sizes(self):
        major, accent = graticule_lines(0., 0., 8)
        self.assertEqual(major, [])
        self.assertEqual(accent, [])


if __name__ == '__main__':
    unittest.main()
