"""Portable shortcut validation, scope rules and per-machine file handling."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from phosphor.editor.shortcuts import BY_ACTION
from phosphor.storage import shortcut_bindings as shortcuts
from phosphor.storage.shortcut_bindings import ShortcutBindings, ShortcutConflict


class KeyTests(unittest.TestCase):
    def test_normalization(self):
        for text, expected in (
                (' shift+aLt+CTRL+meta+s ', 'Meta+Ctrl+Alt+Shift+S'),
                ('Escape', 'Esc'), ('Ctrl+Delete', 'Ctrl+Del'), ('Ctrl++', 'Ctrl++'),
                ('+', '+'), ('Ctrl+,', 'Ctrl+,'), ('Ctrl+0', 'Ctrl+0'), ('Space', 'Space')):
            with self.subTest(text=text):
                self.assertEqual(shortcuts.normalize_key(text), expected)

    def test_invalid_single_chords(self):
        for text in ('', '  ', 'aLT', 'Ctrl+Shift', 'CTRL+Ctrl+X', 'Win+X', 'Ctrl+X, Ctrl+Y',
                     'Ctrl+ X', 'Pg Up', 'X'*13, 'X'*41, 'Ctrl+', 'Ctrl+++', 'Pg-Up', '1X'):
            with self.subTest(text=text), self.assertRaises(ValueError) as raised:
                shortcuts.normalize_key(text)
            self.assertEqual(str(raised.exception), f'"{text}" is not a single key with modifiers.')

    def test_key_lists(self):
        self.assertEqual(shortcuts.validated_keys(['alt+m', 'Ctrl+,']), ('Alt+M', 'Ctrl+,'))
        for keys, message in ((('Alt+M', 'alt+m'), 'Alt+M is listed twice.'),
                              (list('ABCDE'), 'Use at most 4 keys for one shortcut.')):
            with self.assertRaises(ValueError) as raised:
                shortcuts.validated_keys(keys)
            self.assertEqual(str(raised.exception), message)
        for keys in ('F11', [None]):
            with self.assertRaises(ValueError):
                shortcuts.validated_keys(keys)

    def test_physical_registry_text(self):
        convert = shortcuts.physical_to_portable
        self.assertEqual(convert('Shift+Command+S', 'darwin'), ('Ctrl+Shift+S',))
        self.assertEqual(convert('Control+Command+F', 'darwin'), ('Meta+Ctrl+F',))
        self.assertEqual(convert('Ctrl+Y or Ctrl+Shift+Z', 'win32'), ('Ctrl+Y', 'Ctrl+Shift+Z'))
        for platform in shortcuts.PLATFORMS:
            self.assertEqual(convert('Escape', platform), ('Esc',))


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.base = ShortcutBindings(platform='win32')

    def test_registry_defaults(self):
        self.assertTrue(set(shortcuts.REBINDABLE) <= BY_ACTION.keys())
        self.assertEqual(shortcuts.platform_name('linux'), 'win32')
        for platform, fullscreen in (('win32', 'F11'), ('darwin', 'Meta+Ctrl+F')):
            bindings = ShortcutBindings(platform=platform)
            self.assertEqual(tuple(shortcuts.default_bindings(platform)), shortcuts.REBINDABLE)
            self.assertEqual(bindings.effective(), shortcuts.default_bindings(platform))
            self.assertEqual(bindings.keys('fullscreen'), (fullscreen,))
            self.assertEqual(bindings.keys('object_unmute_all'), ('Alt+M',))
            self.assertEqual(bindings.has_conflicts(), ())

    def test_scopes_and_takeover(self):
        self.assertEqual(self.base.conflicts('object_solo', ['S']), ())
        self.assertEqual(self.base.conflicts('object_mute', ['ctrl+s']), (('save', 'Ctrl+S'),))
        conflicts = (('redo', 'Ctrl+Y'), ('object_solo', 'S'), ('split_clip', 'S'))
        with self.assertRaises(ShortcutConflict) as raised:
            self.base.with_keys('save', ['ctrl+y', 's'])
        self.assertEqual(raised.exception.conflicts, conflicts)
        self.assertEqual(str(raised.exception), f'Ctrl+Y is already used by "{BY_ACTION["redo"].description}".')
        edited = self.base.with_keys('save', ['ctrl+y', 's'], take_over=True)
        self.assertEqual(edited.keys('save'), ('Ctrl+Y', 'S'))
        self.assertEqual(edited.keys('redo'), ('Ctrl+Shift+Z',))
        self.assertEqual(edited.keys('object_solo'), ())
        self.assertEqual(edited.keys('split_clip'), ())
        self.assertEqual(edited.has_conflicts(), ())
        self.assertEqual(self.base.overrides, {})

    def test_resets(self):
        edited = self.base.with_keys('save', []).with_keys('new', ['F3'])
        self.assertFalse(edited.with_keys('save', ['ctrl+s']).is_custom('save'))
        self.assertEqual(edited.reset('new').overrides, {'save': ()})
        self.assertEqual(edited.reset_all(), self.base)
        self.assertEqual(edited.overrides, {'new': ('F3',), 'save': ()})

    def test_custom_defaults_and_independent_copies(self):
        defaults = self.base.defaults
        defaults['undo'] = ['Ctrl+Z', 'Alt+Backspace']
        bindings = ShortcutBindings({'save': iter(['F3'])}, 'win32', defaults)
        defaults['undo'].clear()
        bindings.defaults.clear()
        bindings.overrides.clear()
        self.assertEqual(bindings.keys('undo'), ('Ctrl+Z', 'Alt+Backspace'))
        self.assertEqual(bindings.keys('save'), ('F3',))
        self.assertEqual(bindings.conflicts('new', ['Alt+Backspace']), (('undo', 'Alt+Backspace'),))
        self.assertEqual(bindings.reset_all().defaults, bindings.defaults)
        self.assertNotEqual(bindings.reset_all(), self.base)
        self.assertEqual(shortcuts.bindings_from_document(bindings.document(), 'win32', bindings.defaults), bindings)
        with self.assertRaises(ValueError):
            ShortcutBindings(defaults={})
        with self.assertRaisesRegex(ValueError, 'Unknown shortcut action "copy"'):
            ShortcutBindings({'copy': []})


class BindingFileTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)/'keys.json'
        self.base = ShortcutBindings(platform='win32')

    def test_round_trips(self):
        edited = self.base.with_keys('redo', []).with_keys('open', ['F2', 'Ctrl+,'])
        doc = edited.document()
        self.assertEqual(list(doc['bindings']), ['open', 'redo'])
        self.assertEqual(doc['bindings'], {'open': ['F2', 'Ctrl+,'], 'redo': []})
        self.assertEqual(shortcuts.bindings_from_document(doc, 'win32'), edited)
        for save, load in ((shortcuts.write_bindings, shortcuts.read_bindings),
                           (shortcuts.export_bindings, shortcuts.import_bindings)):
            save(edited, self.path)
            self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), doc)
            self.assertEqual(load(self.path, 'win32'), edited)

    def test_missing_or_unreadable(self):
        self.assertEqual(shortcuts.read_bindings(self.path, 'win32'), self.base)
        with self.assertRaisesRegex(ValueError, 'Cannot read keyboard shortcuts:'):
            shortcuts.import_bindings(self.path)
        for damaged in (b'{', bytes([255])):
            self.path.write_bytes(damaged)
            with self.assertRaisesRegex(ValueError, 'Cannot read keyboard shortcuts:'):
                shortcuts.read_bindings(self.path)
            self.assertEqual(self.path.read_bytes(), damaged)

    def test_invalid_documents(self):
        doc = self.base.document()
        for change in (dict(format='other'), dict(version=True), dict(version=0), dict(version=1.0),
                       dict(extra=True), dict(platform='linux'), dict(bindings=[]),
                       dict(bindings={'open': 'F2'}), dict(bindings={'open': [True]})):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'Unsupported keyboard shortcuts file'):
                shortcuts.bindings_from_document(dict(doc, **change))
        for data in ([], {k: v for k, v in doc.items() if k != 'bindings'}):
            with self.assertRaises(ValueError):
                shortcuts.bindings_from_document(data)
        with self.assertRaisesRegex(ValueError, 'newer phosphor'):
            shortcuts.bindings_from_document(dict(doc, version=shortcuts.VERSION+1))
        with self.assertRaisesRegex(ValueError, 'Shortcut "open":'):
            shortcuts.bindings_from_document(dict(doc, bindings={'open': ['Ctrl+']}))

    def test_notices(self):
        for source, target, label in (('win32', 'darwin', 'Windows'), ('darwin', 'win32', 'macOS')):
            doc = dict(self.base.document(), platform=source, bindings={'future': [], 'save': ['F3']})
            notices = []
            bindings = shortcuts.bindings_from_document(doc, target, notices=notices)
            self.assertEqual(bindings.platform, target)
            self.assertEqual(bindings.overrides, {'save': ('F3',)})
            self.assertCountEqual(notices, [
                f'These shortcuts were saved on {label}; check keys that use Command or Control.',
                'Skipped an unknown shortcut "future" (from a newer phosphor?).'])

    def test_conflicting_document_and_safe_write(self):
        conflict = self.base.with_keys('save', ['S'], take_over=True).reset('object_solo').reset('split_clip')
        self.assertEqual(conflict.has_conflicts(), (('save', 'object_solo', 'S'), ('save', 'split_clip', 'S')))
        self.path.write_bytes(b'existing shortcuts')
        for operation in (lambda: shortcuts.write_bindings(conflict, self.path),
                          lambda: shortcuts.bindings_from_document(conflict.document(), 'win32')):
            with self.assertRaises(ValueError) as raised:
                operation()
            self.assertEqual(str(raised.exception),
                             f'"{BY_ACTION["save"].description}" and "{BY_ACTION["object_solo"].description}" both use S.')
        self.assertEqual(self.path.read_bytes(), b'existing shortcuts')
