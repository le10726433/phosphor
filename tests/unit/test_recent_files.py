"""Recent-document persistence without Qt or user-directory writes."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from phosphor.storage import recent_files as recent


class RecentFilesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.path = self.root/'settings'/'recent-files.json'

    def file(self, name):
        path = self.root/name
        path.touch()
        return path.resolve()

    def test_order_and_resolved_deduplication(self):
        a, b = self.file('a.json'), self.file('b.json')
        recent.add_recent('scene', a, self.path)
        recent.add_recent('scene', b, self.path)
        self.assertEqual(recent.read_recent('scene', self.path), [b, a])
        recent.add_recent('scene', a.parent/'unused'/ '..'/a.name, self.path)
        self.assertEqual(recent.read_recent('scene', self.path), [a, b])
        if os.name == 'nt':
            recent.add_recent('scene', str(a).upper(), self.path)
            self.assertEqual(len(recent.read_recent('scene', self.path)), 2)

    def test_cap_per_kind(self):
        files = [self.file(f'{i}.json') for i in range(12)]
        for file in files: recent.add_recent('project', file, self.path)
        recent.add_recent('scene', files[0], self.path)
        self.assertEqual(recent.read_recent('project', self.path), list(reversed(files[2:])))
        self.assertEqual(recent.read_recent('scene', self.path), files[:1])

    def test_audio_and_model_limits_are_independent(self):
        files = [self.file(f'{i}.dat') for i in range(21)]
        for file in files:
            for kind in ('audio', 'model'):
                recent.add_recent(kind, file, self.path)
        self.assertEqual(recent.read_recent('audio', self.path), files[:0:-1])
        self.assertEqual(recent.read_recent('model', self.path), files[:-11:-1])

    def test_missing_files_are_pruned(self):
        file = self.file('gone.json')
        recent.add_recent('scene', file, self.path)
        file.unlink()
        self.assertEqual(recent.read_recent('scene', self.path), [])
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['entries'], [])

    def test_missing_and_corrupt_store(self):
        self.assertEqual(recent.read_recent('project', self.path), [])
        self.assertFalse(self.path.exists())
        self.path.parent.mkdir()
        for content in ('{broken', 'null', '{"version": 1, "entries": null}', '{"version": 99}'):
            self.path.write_text(content, encoding='utf-8')
            self.assertEqual(recent.read_recent('project', self.path), [])
        file = self.file('valid.json')
        recent.add_recent('project', file, self.path)
        self.assertEqual(recent.read_recent('project', self.path), [file])

    def test_kind_separation_clear_and_remove(self):
        file = self.file('both.json')
        for kind in recent.KINDS: recent.add_recent(kind, file, self.path)
        recent.clear_recent('scene', self.path)
        self.assertEqual(recent.read_recent('scene', self.path), [])
        self.assertEqual(recent.read_recent('project', self.path), [file])
        recent.remove_recent('project', file, self.path)
        self.assertEqual(recent.read_recent('project', self.path), [])

    def test_atomic_replace_failure_preserves_store(self):
        a, b = self.file('a.json'), self.file('b.json')
        recent.add_recent('scene', a, self.path)
        before = self.path.read_bytes()
        with patch.object(recent.os, 'replace', side_effect=OSError('write failed')):
            with self.assertRaises(OSError): recent.add_recent('scene', b, self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_damaged_file_is_not_rewritten_by_reading(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{ not json', encoding='utf-8')
        self.assertEqual(recent.read_recent('audio', self.path), [])
        self.assertEqual(self.path.read_text(encoding='utf-8'), '{ not json')

    def test_kinds_from_a_newer_version_survive_a_rewrite(self):
        a = self.file('a.json')
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({'version': 1, 'entries': [{'kind': 'future', 'path': 'x'}]}), encoding='utf-8')
        recent.add_recent('scene', a, self.path)
        recent.read_recent('scene', self.path)
        entries = json.loads(self.path.read_text(encoding='utf-8'))['entries']
        self.assertIn({'kind': 'future', 'path': 'x'}, entries)
        self.assertEqual(recent.read_recent('scene', self.path), [a])

    def test_read_without_prune_never_writes(self):
        a = self.file('a.json'); recent.add_recent('scene', a, self.path)
        a.unlink(); before = self.path.read_bytes()
        self.assertEqual(recent.read_recent('scene', self.path, prune=False), [])
        self.assertEqual(self.path.read_bytes(), before)
