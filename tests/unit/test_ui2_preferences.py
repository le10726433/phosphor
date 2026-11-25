"""UI2 machine preferences: panel width, wheel and trackpad sensitivity, glow range (Qt-free)."""
import unittest

from phosphor.display.glow_trail import VISUAL_DEFAULTS, VISUAL_RANGES
from phosphor.editor.layout_metrics import PANEL_RANGE, PANEL_DEFAULT, panel_width
from phosphor.editor.shortcuts import MENU_GROUPS, BY_ACTION
from phosphor.storage.app_files import DEFAULT_PREFERENCES, validated_preferences
from phosphor.storage.shortcut_bindings import REBINDABLE


class StyleTests(unittest.TestCase):
    def test_style_mode_migration_and_validation(self):
        old = dict(DEFAULT_PREFERENCES)
        del old['style_mode']
        self.assertEqual(validated_preferences(old)['style_mode'], 'System')
        for mode in ('System', 'Dark', 'Light', 'unknown', None):
            prefs = validated_preferences(dict(old, style='Modern', style_mode=mode))
            self.assertEqual(prefs['style'], 'Modern')
            self.assertEqual(prefs['style_mode'], mode if mode in ('Dark', 'Light') else 'System')

    def test_pre_st1_preferences_default_to_classic(self):
        old = dict(DEFAULT_PREFERENCES)
        del old['style']
        self.assertEqual(validated_preferences(old)['style'], 'Classic')


class PanelWidthTests(unittest.TestCase):
    def test_library_page_preference(self):
        old = dict(DEFAULT_PREFERENCES)
        del old['left_page']
        self.assertEqual(validated_preferences(old)['left_page'], 'browser')
        for page in ('browser', 'effects', 'unknown', None):
            expected = 'effects' if page == 'effects' else 'browser'
            self.assertEqual(validated_preferences(dict(old, left_page=page))['left_page'], expected)

    def test_range_and_default(self):
        self.assertEqual(PANEL_RANGE, (280, 900)); self.assertEqual(PANEL_DEFAULT, 400)
        self.assertEqual(DEFAULT_PREFERENCES['panel_width'], PANEL_DEFAULT)
        for value in (280, 400, 900): self.assertEqual(panel_width(value), value)
        for value in (279, 901, 400., True, '400', None, -1): self.assertEqual(panel_width(value), PANEL_DEFAULT, value)

    def test_saved_values(self):
        self.assertEqual(validated_preferences(dict(DEFAULT_PREFERENCES, panel_width=640))['panel_width'], 640)
        self.assertEqual(validated_preferences(dict(DEFAULT_PREFERENCES, panel_width=2000))['panel_width'], PANEL_DEFAULT)
        old = dict(DEFAULT_PREFERENCES); del old['panel_width']; del old['wheel_sensitivity']; del old['trackpad_sensitivity']
        result = validated_preferences(old)  # A 0.83 file reads with the defaults.
        self.assertEqual((result['panel_width'], result['wheel_sensitivity'], result['trackpad_sensitivity']), (PANEL_DEFAULT, 1., 1.))


class SensitivityTests(unittest.TestCase):
    def test_saved_values(self):
        value = validated_preferences(dict(DEFAULT_PREFERENCES, wheel_sensitivity=.5, trackpad_sensitivity=3))
        self.assertEqual((value['wheel_sensitivity'], value['trackpad_sensitivity']), (.5, 3.))
        value = validated_preferences(dict(DEFAULT_PREFERENCES, wheel_sensitivity=9., trackpad_sensitivity=False))
        self.assertEqual((value['wheel_sensitivity'], value['trackpad_sensitivity']), (1., 1.))


class GlowTests(unittest.TestCase):
    def test_range_and_default(self):
        self.assertEqual(VISUAL_RANGES['glow_size'], (0., 36.)); self.assertEqual(VISUAL_DEFAULTS['glow_size'], 12.)
        self.assertEqual(DEFAULT_PREFERENCES['glow_size'], 12.)

    def test_saved_glow_is_kept(self):
        for glow in (3., 24., 36.):
            self.assertEqual(validated_preferences(dict(DEFAULT_PREFERENCES, glow_size=glow))['glow_size'], glow)
        with self.assertRaises(ValueError): validated_preferences(dict(DEFAULT_PREFERENCES, glow_size=37.))


class MenuGroupTests(unittest.TestCase):
    def test_every_rebindable_action_has_one_group(self):
        grouped = [action for _, actions in MENU_GROUPS for action in actions]
        self.assertEqual(sorted(grouped), sorted(REBINDABLE))
        self.assertEqual(len(grouped), len(set(grouped)))
        self.assertTrue(all(action in BY_ACTION for action in grouped))


if __name__ == '__main__':
    unittest.main()
