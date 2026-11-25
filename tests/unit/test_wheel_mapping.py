import unittest

from phosphor.editor import wheel_mapping as wm
from phosphor.editor.shortcuts import mapped_shortcuts, filter_shortcuts, format_keys
from phosphor.storage.app_files import DEFAULT_PREFERENCES, validated_preferences


class WheelMappingTests(unittest.TestCase):
    def test_platform_defaults(self):
        self.assertEqual(wm.defaults('win32'), dict(wheel_scroll='shift', wheel_zoom='control',
                                                   wheel_height='alt', wheel_builder_zoom='none'))
        self.assertEqual(wm.defaults('darwin'), dict(wheel_scroll='shift', wheel_zoom='control',
                                                    wheel_height='command', wheel_builder_zoom='none'))
        for platform in ('win32', 'darwin'):
            self.assertEqual(wm.validated_mapping({}, platform), wm.defaults(platform))

    def test_physical_labels(self):
        self.assertEqual([wm.physical_label(k, 'darwin') for k in wm.choices('darwin')],
                         ['None', 'Shift', 'Control', 'Option', 'Command'])
        self.assertEqual([wm.physical_label(k, 'win32') for k in wm.choices('win32')],
                         ['None', 'Shift', 'Ctrl', 'Alt'])

    def test_validation(self):
        for platform in ('win32', 'darwin'):
            with self.assertRaisesRegex(ValueError, 'different modifiers'):
                wm.validated_mapping(dict(wheel_zoom='shift'), platform)
            with self.assertRaisesRegex(ValueError, 'available'):
                wm.validated_mapping(dict(wheel_zoom=['shift']), platform)
            with self.assertRaisesRegex(ValueError, 'available'):
                wm.validated_mapping(dict(wheel_zoom='capslock'), platform)
            # Builder has no competing action and may reuse a Composer modifier.
            result = wm.validated_mapping(dict(wheel_scroll='control', wheel_zoom='shift',
                                               wheel_builder_zoom='shift'), platform)
            self.assertEqual(result['wheel_zoom'], 'shift')
            self.assertEqual(wm.validated_mapping(dict(wheel_zoom='none'), platform)['wheel_zoom'], 'none')
        with self.assertRaisesRegex(ValueError, 'available'):
            wm.validated_mapping(dict(wheel_zoom='command'), 'win32')

    def test_qt_resolution_and_matching(self):
        from PySide6.QtCore import Qt
        mod = Qt.KeyboardModifier
        self.assertEqual(wm.qt_modifier('command', 'darwin'), mod.ControlModifier)
        self.assertEqual(wm.qt_modifier('control', 'darwin'), mod.MetaModifier)
        self.assertEqual(wm.qt_modifier('control', 'win32'), mod.ControlModifier)
        for platform in ('win32', 'darwin'):
            for key in wm.choices(platform):
                self.assertTrue(wm.matches(wm.qt_modifier(key, platform), key, platform))
            self.assertFalse(wm.matches(mod.ShiftModifier, 'none', platform))
            self.assertTrue(wm.matches(mod.ShiftModifier, 'none', platform, unmodified_any=True))
            self.assertFalse(wm.matches(mod.NoModifier, 'shift', platform))
            self.assertTrue(wm.matches(mod.ShiftModifier | mod.AltModifier, 'shift', platform))

    def test_old_preferences_and_validation(self):
        old = {key: value for key, value in DEFAULT_PREFERENCES.items() if key not in wm.ACTIONS}
        loaded = validated_preferences(old)
        self.assertEqual({key: loaded[key] for key in wm.ACTIONS}, wm.defaults())
        with self.assertRaisesRegex(ValueError, 'different modifiers'):
            validated_preferences(dict(loaded, wheel_zoom='shift'))

    def test_current_shortcut_labels_and_search(self):
        for platform in ('win32', 'darwin'):
            mapping = wm.validated_mapping(dict(wheel_zoom='shift', wheel_scroll='control',
                                                wheel_builder_zoom='alt'), platform)
            entries = {entry.action: entry for entry in mapped_shortcuts(mapping, platform)}
            self.assertEqual(format_keys(entries['time_zoom'], platform),
                             '⇧wheel' if platform == 'darwin' else 'Shift+wheel')
            self.assertEqual(format_keys(entries['builder_zoom'], platform),
                             '⌥wheel' if platform == 'darwin' else 'Alt+wheel')
            self.assertIn(entries['time_zoom'], filter_shortcuts('zoom shift', platform, mapping))


if __name__ == '__main__':
    unittest.main()
