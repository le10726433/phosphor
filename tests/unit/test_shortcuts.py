import unittest

from phosphor.editor.shortcuts import SHORTCUTS, BY_ACTION, filter_shortcuts, format_keys
from phosphor.storage.app_paths import APP_DIR


class ShortcutsTests(unittest.TestCase):
    def test_registry(self):
        self.assertEqual(len(SHORTCUTS), len(BY_ACTION))
        for entry in SHORTCUTS:
            for value in (entry.action, entry.macos, entry.windows, entry.where, entry.description):
                self.assertIsInstance(value, str)
                self.assertTrue(value.strip())
                self.assertNotIn('|', value)
        self.assertTrue({'new', 'open', 'save', 'save_as', 'undo', 'redo', 'play',
                         'home', 'end', 'fullscreen', 'trace_only', 'escape',
                         'note_box', 'select_all', 'builder_finish'} <= BY_ACTION.keys())

    def test_platform_formatting(self):
        self.assertEqual(format_keys(BY_ACTION['save_as'], 'darwin'), '⇧⌘S')
        self.assertEqual(format_keys(BY_ACTION['fullscreen'], 'darwin'), '⌃⌘F')
        self.assertEqual(format_keys(BY_ACTION['node_time'], 'darwin'), '⌥drag node')
        self.assertEqual(format_keys(BY_ACTION['builder_snap'], 'darwin'), '⌥')
        self.assertEqual(format_keys(BY_ACTION['save_as'], 'win32'), 'Ctrl+Shift+S')
        self.assertEqual(format_keys(BY_ACTION['row_height'], 'win32'), 'Alt+wheel')
        self.assertEqual(format_keys(BY_ACTION['play'], 'darwin'), 'Space')

    def test_search(self):
        self.assertEqual(filter_shortcuts('  '), SHORTCUTS)
        self.assertEqual(filter_shortcuts('no-such-shortcut'), ())
        self.assertEqual(filter_shortcuts('BOX notes'), (BY_ACTION['note_box'],))
        self.assertIn(BY_ACTION['save'], filter_shortcuts('⌘S', 'darwin'))
        self.assertIn(BY_ACTION['row_height'], filter_shortcuts('alt+wheel', 'win32'))
        self.assertIn(BY_ACTION['node_gain_entry'], filter_shortcuts('double-click node'))
        # Every builder entry is found; general entries whose text mentions the builder may be too.
        found = filter_shortcuts('shape builder')
        builder = [entry for entry in SHORTCUTS if 'builder' in entry.where.casefold()]
        self.assertTrue(builder and all(entry in found for entry in builder))
        self.assertTrue(all('builder' in (entry.where + ' ' + entry.description).casefold() for entry in found))

    def test_manual_general_table(self):
        text = (APP_DIR / 'docs' / 'manual' / '12-reference.md').read_text(encoding='utf-8')
        section = text.split('## 12.1 Keyboard shortcuts', 1)[1].split('### Composer note roll', 1)[0]
        rows = [tuple(cell.strip() for cell in line.strip().strip('|').split('|'))
                for line in section.splitlines() if line.startswith('|')]
        self.assertEqual(rows[0], ('Action', 'macOS', 'Windows'))
        expected = [(entry.description, format_keys(entry, 'darwin'), format_keys(entry, 'win32'))
                    for entry in SHORTCUTS if entry.where == 'general']
        self.assertEqual(rows[2:], expected)

    def test_manual_note_roll_table(self):
        text = (APP_DIR / 'docs' / 'manual' / '12-reference.md').read_text(encoding='utf-8')
        section = text.split('### Composer note roll', 1)[1].split('### Objects list', 1)[0]
        rows = [tuple(cell.strip() for cell in line.strip().strip('|').split('|'))
                for line in section.splitlines() if line.startswith('|')]
        self.assertEqual(rows[2:], [(entry.description, format_keys(entry, 'darwin'), format_keys(entry, 'win32'))
                                   for entry in SHORTCUTS if entry.where == 'Composer note roll'])

    def test_manual_objects_table(self):
        text = (APP_DIR / 'docs' / 'manual' / '12-reference.md').read_text(encoding='utf-8')
        section = text.split('### Objects list', 1)[1].split('## 12.2', 1)[0]
        rows = [tuple(cell.strip() for cell in line.strip().strip('|').split('|'))
                for line in section.splitlines() if line.startswith('|')]
        self.assertEqual(rows[2:], [(entry.description, format_keys(entry, 'darwin'), format_keys(entry, 'win32'))
                                   for entry in SHORTCUTS if entry.where == 'Objects list'])

    def test_note_tools_and_remapped_pitch_zoom(self):
        from phosphor.editor.shortcuts import mapped_shortcuts
        ids = ('note_duplicate', 'note_split', 'note_join', 'note_legato', 'note_quantize')
        for platform in ('macos', 'windows'):
            keys = [getattr(BY_ACTION[action], platform) for action in ids]
            self.assertEqual(len(keys), len(set(keys)))
            others = [getattr(entry, platform) for entry in SHORTCUTS if entry.action not in ids]
            self.assertFalse(set(keys) & set(others))
        from phosphor.editor.wheel_mapping import defaults
        prefs = dict(defaults('win32'), wheel_zoom='alt', wheel_height='control')
        entries = {entry.action: entry for entry in mapped_shortcuts(prefs, 'win32')}
        self.assertEqual(entries['pitch_zoom'].windows, entries['time_zoom'].windows)
        self.assertEqual(entries['pitch_zoom'].windows, 'Alt+wheel')


if __name__ == '__main__':
    unittest.main()
