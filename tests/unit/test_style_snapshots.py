"""Snapshot folder ownership and comparison preconditions, without a GUI."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from tools import style_snapshots as snapshots


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.before = self.root / 'before'
        self.after = self.root / 'after'

    def populate(self, folder, scale=1):
        folder.mkdir()
        for surface in snapshots.SURFACES:
            (folder / f'{surface}.png').touch()
        (folder / 'snapshots.json').write_text(json.dumps({
            'surfaces': sorted(snapshots.SURFACES), 'scale': scale,
        }), encoding='utf-8')

    def assert_refused(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(snapshots.compare(self.before, self.after), 1)

    def test_refuses_existing_unmarked_folder(self):
        self.before.mkdir()
        tone = self.before / 'tone.wav'
        tone.write_bytes(b'original')
        with self.assertRaises(ValueError):
            snapshots.prepare_capture(self.after, self.before)
        self.assertEqual(tone.read_bytes(), b'original')
        self.assertEqual(list(self.before.iterdir()), [tone])

    def test_reuses_own_folder(self):
        snapshots.marked_folder(self.before)
        self.assertTrue((self.before / snapshots.MARKER).is_file())
        self.assertEqual(snapshots.marked_folder(self.before), self.before)

    def test_capture_cleans_only_top_level_pngs_in_marked_output(self):
        self.after.mkdir()
        stale = self.after / 'old.png'
        stale.touch()
        with self.assertRaises(ValueError):
            snapshots.prepare_capture(self.after, self.before)
        self.assertTrue(stale.is_file())
        out = self.root / 'marked'
        snapshots.prepare_capture(out, self.before)
        (out / 'old.png').touch()
        (out / 'notes.txt').touch()
        nested = out / 'nested'
        nested.mkdir()
        (nested / 'old.png').touch()
        snapshots.prepare_capture(out, self.before)
        self.assertFalse((out / 'old.png').exists())
        self.assertTrue((out / 'notes.txt').is_file())
        self.assertTrue((nested / 'old.png').is_file())

    def test_missing_folders(self):
        self.assert_refused()

    def test_different_names_and_shared_missing_surface(self):
        self.populate(self.before)
        self.populate(self.after)
        (self.after / 'browser.png').unlink()
        self.assert_refused()
        (self.before / 'browser.png').unlink()
        self.assert_refused()

    def test_manifest_required_and_scale_must_match(self):
        self.populate(self.before)
        self.populate(self.after, 1.5)
        self.assert_refused()
        (self.after / 'snapshots.json').unlink()
        self.assert_refused()

    def write(self, folder, **values):
        data = dict(surfaces=sorted(snapshots.SURFACES), scale=1, **values)
        (folder / 'snapshots.json').write_text(json.dumps(data), encoding='utf-8')

    def assert_compares_images(self):
        # Matching manifests reach the images (null ones here, so still refused).
        with patch.object(snapshots, 'load_image', return_value=Mock(isNull=lambda: True)) as loader:
            self.assert_refused()
            loader.assert_called()

    def test_legacy_and_new_style_metadata(self):
        self.populate(self.before)
        self.populate(self.after)
        legacy = snapshots.manifest(self.before)
        self.assertEqual((legacy['style'], legacy['mode'], legacy['requested_mode']), ('Classic', '', ''))
        # Classic has no mode: whatever an older run recorded, it is normalised to ''.
        self.write(self.after, style='Classic', mode='Dark')
        self.assertEqual(snapshots.manifest(self.after), legacy)
        self.assert_compares_images()
        for mode in ('Dark', 'Light'):
            self.write(self.after, style='Modern', mode=mode, requested_mode=mode)
            with patch.object(snapshots, 'load_image') as loader:
                self.assert_refused()
                loader.assert_not_called()

    def test_modern_compares_the_resolved_mode(self):
        self.populate(self.before)
        self.populate(self.after)
        self.write(self.before, style='Modern', mode='Dark', requested_mode='System')
        self.write(self.after, style='Modern', mode='Dark', requested_mode='Dark')
        self.assertEqual(snapshots.manifest(self.before)['requested_mode'], 'System')
        self.assert_compares_images()
        self.write(self.after, style='Modern', mode='Light', requested_mode='System')
        with patch.object(snapshots, 'load_image') as loader:
            self.assert_refused()
            loader.assert_not_called()
        # A Modern manifest must name the resolved mode.
        self.write(self.after, style='Modern', mode='System')
        with self.assertRaises(ValueError):
            snapshots.manifest(self.after)
        self.assert_refused()

    def test_two_null_images_cannot_match(self):
        self.populate(self.before)
        self.populate(self.after)
        with patch.object(snapshots, 'load_image', return_value=Mock(isNull=lambda: True)) as loader:
            self.assert_refused()
            loader.assert_called()
