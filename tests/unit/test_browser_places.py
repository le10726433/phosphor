import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from phosphor.storage import browser_places as places


class FavouriteTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.path = self.root / 'browser.json'

    def test_order_offline_folders_and_resolved_duplicates(self):
        self.assertEqual(places.read_favourites(self.path), [])
        a, b = self.root / 'A', self.root / 'B'
        for folder in (a, b, b / '..' / a.name):
            places.add_favourite(folder, self.path)
        if os.name == 'nt':
            places.add_favourite(str(b).lower(), self.path)
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(data, dict(format='phosphor-browser', version=1, favourites=[str(a), str(b)]))
        data['favourites'].insert(1, None)
        self.path.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(places.read_favourites(self.path), [a, b])
        places.remove_favourite(b / '..' / a.name, self.path)
        self.assertEqual(places.read_favourites(self.path), [b])

    def test_limit_allows_duplicate_at_capacity(self):
        folders = [self.root / str(i) for i in range(50)]
        self.path.write_text(json.dumps(dict(format='phosphor-browser', version=1,
                                             favourites=list(map(str, folders)))), encoding='utf-8')
        places.add_favourite(folders[0], self.path)
        with self.assertRaises(ValueError):
            places.add_favourite(self.root, self.path)
        self.assertEqual(places.read_favourites(self.path), folders)

    def test_damaged_files_survive_both_mutations(self):
        valid = dict(format='phosphor-browser', version=1, favourites=[])
        for content in ('{', 'null', json.dumps(dict(valid, format='other')),
                        json.dumps(dict(valid, version=2)), json.dumps(dict(valid, favourites='bad'))):
            self.path.write_text(content, encoding='utf-8')
            for change in (places.add_favourite, places.remove_favourite):
                with self.subTest(content=content, change=change), self.assertRaises(ValueError):
                    change(self.root, self.path)
                self.assertEqual(self.path.read_text(encoding='utf-8'), content)

    def test_atomic_replace_failure(self):
        places.add_favourite(self.root, self.path)
        with patch('os.replace', side_effect=OSError), self.assertRaises(OSError):
            places.remove_favourite(self.root, self.path)
        self.assertEqual(places.read_favourites(self.path), [self.root])
        self.assertEqual(list(self.root.iterdir()), [self.path])
