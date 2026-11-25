import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import phosphor
import numpy as np
import soundfile

from phosphor.engine import audio_browse as browse


class BrowseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_file_types_and_places(self):
        self.assertTrue(browse.matches('blue.SCENE.JSON', browse.SCENE_TYPES))
        self.assertFalse(browse.matches('scene.json.bak', browse.SCENE_TYPES))
        self.assertTrue(browse.matches('Cube.oBj', browse.MODEL_TYPES))
        for name in ('Downloads', 'Music', 'Desktop'):
            (self.root / name).mkdir()
        (self.root / 'Documents').touch()
        labels = ['Home', 'Desktop', 'Music', 'Downloads']
        self.assertEqual([label for label, _ in browse.standard_places(self.root, 'linux')], labels + ['/'])
        with patch.object(os, 'listdrives', return_value=['C:/', 'Z:/'], create=True):
            self.assertEqual([k for k, _ in browse.standard_places(self.root, 'win32')], labels + ['C:', 'Z:'])

    def test_places_skip_remote_home_before_metadata_lookup(self):
        mount = self.root.resolve()
        with (patch.object(browse, '_remote_mounts', return_value=[mount]),
              patch.object(Path, 'lstat', autospec=True, side_effect=Path.lstat) as lookup):
            self.assertEqual(browse.standard_places(mount / 'home', 'linux'), [('/', Path('/'))])
        looked_up = [call.args[0] for call in lookup.call_args_list]
        self.assertNotIn(mount, looked_up)
        self.assertNotIn(mount / 'home', looked_up)

    def test_listing_and_search(self):
        for name in ('beta', 'Alpha', '.hidden'):
            (self.root / name).mkdir()
        for name in ('Beta.WAV', 'a.wav', '.secret.wav', 'notes.txt', 'Alpha/beat kick.wav',
                     'beat KICK.wav', 'beta/kick bright beat.flac', '.hidden/beat kick.wav'):
            (self.root / name).touch()
        listing = browse.list_folder(self.root, browse.AUDIO_TYPES)
        self.assertEqual(listing.error, '')
        self.assertEqual([p.name for p in listing.folders], ['Alpha', 'beta'])
        self.assertEqual([p.name for p in listing.files], ['a.wav', 'beat KICK.wav', 'Beta.WAV'])
        self.assertTrue(browse.list_folder(self.root / 'gone', browse.AUDIO_TYPES).error)
        result = browse.search(self.root, '  KICK beAT ', browse.AUDIO_TYPES)
        self.assertFalse(result.truncated)
        self.assertEqual(result.files[0], self.root / 'beat KICK.wav')
        self.assertCountEqual(result.files[1:], [self.root / 'Alpha/beat kick.wav',
                                                self.root / 'beta/kick bright beat.flac'])

    @unittest.skipUnless(os.name == 'nt', 'Windows hidden attribute')
    def test_hidden_attribute(self):
        import ctypes
        path = self.root / 'secret.wav'
        path.touch()
        self.assertTrue(ctypes.windll.kernel32.SetFileAttributesW(str(path), 2))
        self.assertEqual(browse.list_folder(self.root, browse.AUDIO_TYPES).files, ())
        self.assertEqual(browse.search(self.root, 'secret', browse.AUDIO_TYPES).files, ())

    def test_search_budgets_and_cancellation(self):
        for i in range(4):
            (self.root / f'sample{i}.wav').touch()
        for kwargs, count in ((dict(limit=2), 2), (dict(max_entries=3), 3)):
            result = browse.search(self.root, 'sample', browse.AUDIO_TYPES, **kwargs)
            self.assertEqual(len(result.files), count)
            self.assertTrue(result.truncated)
        stop = threading.Event()
        stop.set()
        with patch.object(os, 'scandir', side_effect=AssertionError):
            self.assertEqual(browse.search(self.root, 'x', (), stop), browse.SearchResult((), True))
            self.assertEqual(browse.search(self.root, '  ', ()), browse.SearchResult((), False))

    def test_folder_symlink_listed_but_not_searched(self):
        folder = self.root / 'real'
        folder.mkdir()
        (folder / 'test.wav').touch()
        link = self.root / 'linked'
        try:
            link.symlink_to(folder, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(str(error))
        self.assertIn(link, browse.list_folder(self.root, browse.AUDIO_TYPES).folders)
        self.assertEqual(browse.search(self.root, 'test', browse.AUDIO_TYPES).files, (folder / 'test.wav',))

    def test_info_and_description(self):
        for info, expected in ((browse.AudioInfo(3.2, 48000, 2), '3.2 s · 48 kHz · stereo'),
                               (browse.AudioInfo(65, 44100, 1), '1:05 · 44.1 kHz · mono'),
                               (browse.AudioInfo(12, 96000, 6), '12.0 s · 96 kHz · 6 channels')):
            self.assertEqual(browse.describe(info), expected)
        path = self.root / 'test.wav'
        self.assertIsNone(browse.audio_info(path))
        soundfile.write(path, np.ones((3200, 2)), 32000)
        self.assertEqual(browse.audio_info(path), browse.AudioInfo(.1, 32000, 2))

    def test_preview_cap_stereo_resampling_and_fades(self):
        path = self.root / 'test.wav'
        soundfile.write(path, np.ones(8000), 8000, subtype='FLOAT')
        samples = browse.read_preview(path, 8000, seconds=.1)
        self.assertEqual(samples.shape, (800, 2))
        self.assertEqual(samples.dtype, np.float32)
        self.assertFalse(samples.flags.writeable)
        np.testing.assert_array_equal(samples[:, 0], samples[:, 1])
        np.testing.assert_allclose(samples[:16, 0], np.linspace(0, 1, 16))
        np.testing.assert_array_equal(samples[16:-40], 1)
        np.testing.assert_allclose(samples[-40:, 0], np.linspace(1, 0, 40))
        resampled = browse.read_preview(path, 12000, seconds=.1)
        self.assertEqual(resampled.shape, (1200, 2))
        np.testing.assert_array_equal(resampled[[0, -1]], 0)
        self.assertEqual(browse.read_preview(path, 12000, seconds=0).shape, (0, 2))

    def test_preview_multichannel_nonfinite_and_missing(self):
        path = self.root / 'test.wav'
        with self.assertRaises((OSError, RuntimeError, ValueError)):
            browse.read_preview(path, 8000)
        data = np.tile([.5, -.25, .75], (800, 1))
        data[100:103, 0] = [np.inf, np.nan, -np.inf]
        soundfile.write(path, data, 8000, subtype='FLOAT')
        samples = browse.read_preview(path, 8000)
        np.testing.assert_array_equal(samples[80:100], data[80:100, :2])
        np.testing.assert_array_equal(samples[100:103, 0], 0.)


class WorkerTests(unittest.TestCase):
    def test_replacement_cancellation_poll_and_error(self):
        worker = browse.BrowseWorker()
        self.addCleanup(worker.close)
        started, cancelled, release, finished = (threading.Event() for _ in range(4))
        self.addCleanup(release.set)

        def slow(*, stop):
            started.set()
            if stop.wait(5):
                cancelled.set()
            release.wait(5)
            return 'old'

        first = worker.submit('search', slow, stoppable=True)
        self.assertTrue(started.wait(2))
        self.assertEqual(worker.take(), [])
        worker.submit('search', self.fail, 'superseded job ran')
        last = worker.submit('search', str, 3)
        self.assertEqual(worker.latest('search'), last)
        self.assertTrue(cancelled.wait(2))
        bad = worker.submit('header', int, 'invalid')
        worker.submit('barrier', finished.set)
        release.set()
        self.assertTrue(finished.wait(2))
        worker.close()
        old, new, error, _ = worker.take()
        self.assertEqual(old, browse.BrowseResult('search', first, 'old', None))
        self.assertEqual(new, browse.BrowseResult('search', last, '3', None))
        self.assertEqual((error.channel, error.token, error.value), ('header', bad, None))
        self.assertIsInstance(error.error, ValueError)
        self.assertEqual(worker.take(), [])
