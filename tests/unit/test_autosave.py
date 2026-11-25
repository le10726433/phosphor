"""Recovery persistence uses temporary directories and no Qt."""
from dataclasses import replace
import json
import os
from pathlib import Path
import statistics
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from phosphor.engine.composition import COMPOSER, SANDBOX, Note, new_project
from phosphor.engine.signal_engine import Settings
from phosphor.storage import autosave as a
from phosphor.storage import app_files
from phosphor.storage.app_files import read_scene
from phosphor.storage.project_files import save_project, read_project


class AutosaveTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.project = new_project()
        self.pid = 987654321

    def write(self, workspace='Composer', original=None, pid=None):
        return a.write_recovery(self.root, workspace,
            lambda path: save_project(path, self.project), original,
            self.pid if pid is None else pid)

    def stale(self):
        with patch.object(a, 'process_alive', return_value=False):
            return a.find_stale(self.root)

    def test_atomic_success_and_failure(self):
        path = self.write()
        self.assertEqual(read_project(path), self.project)
        self.assertEqual(list(self.root.glob('.saving-*')), [])
        before = path.read_bytes()
        # Fail the document replacement after the lock has been written.
        replace = app_files.os.replace
        def fail_document(source, target):
            if Path(target) == path: raise OSError('disk failure')
            return replace(source, target)
        with patch.object(app_files.os, 'replace', side_effect=fail_document):
            with self.assertRaises(OSError): self.write()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.saving-*')), [])

    def test_stale_pid_and_meta_round_trip(self):
        original = self.root/'Original.project.json'
        path = self.write(original=original)
        found = self.stale()
        self.assertEqual(len(found), 1)
        item = found[0]
        self.assertEqual((item.path, item.pid, item.workspace, item.original),
                         (path, self.pid, 'Composer', str(original)))
        self.assertTrue(item.saved_at)
        self.assertEqual(json.loads(a.meta_path(path).read_text(encoding='utf-8'))['saved_at'], item.saved_at)
        self.assertIn('started_at', json.loads((self.root/f'{self.pid}.lock').read_text(encoding='utf-8')))

    def test_live_pid_ignored(self):
        path = self.write(pid=os.getpid())
        self.assertTrue(a.process_alive(os.getpid()))
        self.assertEqual(a.find_stale(self.root), [])
        self.assertTrue(path.exists())
        self.assertTrue((self.root/f'{os.getpid()}.lock').exists())

    def test_document_cleanup_and_close_cleanup(self):
        project = self.write()
        scene = self.write('Sandbox')
        a.cleanup(self.root, self.pid, 'Composer')
        self.assertFalse(project.exists())
        self.assertFalse(a.meta_path(project).exists())
        self.assertTrue(scene.exists())
        self.assertTrue((self.root/f'{self.pid}.lock').exists())
        a.cleanup(self.root, self.pid)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_discard_and_empty_stale_lock(self):
        self.write(); self.write('Sandbox')
        a.discard(self.root, self.stale())
        self.assertEqual(list(self.root.iterdir()), [])
        a.write_lock(self.root, self.pid)
        self.assertEqual(self.stale(), [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_untitled_and_missing_meta(self):
        path = self.write()
        self.assertIsNone(self.stale()[0].original)
        a.meta_path(path).unlink()
        self.assertTrue(self.stale()[0].saved_at)


class RecoveryWriterTests(unittest.TestCase):
    """P12 (0.76.0-dev): recovery writes on a worker thread, with a deliberately stalled writer."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.pid = 987654321
        self.reports = []
        self.writer = a.RecoveryWriter(self.reports.append, self.pid)
        self.addCleanup(self.writer.close, 5.)
        self.path = a.recovery_path(self.root, self.pid, COMPOSER)
        self.gate = None  # A threading.Event: writes wait for it (the stalled writer).
        self.started = threading.Event()
        self.written = []  # bpm of every project the worker wrote, in order.
        write = a.write_recovery
        def stalled(directory, workspace, writer, original=None, pid=None):
            self.started.set()
            if self.gate is not None: self.assertTrue(self.gate.wait(10))
            path = write(directory, workspace, writer, original, pid)
            if workspace == COMPOSER: self.written.append(read_project(path).bpm)
            return path
        patcher = patch.object(a, 'write_recovery', side_effect=stalled)
        patcher.start(); self.addCleanup(patcher.stop)

    def project(self, bpm):
        return replace(new_project(), bpm=float(bpm))

    def stall(self):
        self.gate, self.started = threading.Event(), threading.Event()

    def test_coalescing_latest_wins(self):
        self.stall()
        self.assertIsNotNone(self.writer.submit(self.root, COMPOSER, self.project(100)))
        self.assertTrue(self.started.wait(5))  # 100 is in flight, held by the stalled writer.
        for bpm in (101, 102, 103, 104):
            self.assertIsNotNone(self.writer.submit(self.root, COMPOSER, self.project(bpm)))
        self.assertTrue(self.writer.busy())
        self.gate.set()
        self.assertTrue(self.writer.flush(10))
        self.assertEqual(self.written, [100., 104.])  # One in flight, then only the latest.
        self.assertEqual(read_project(self.path).bpm, 104.)
        self.assertEqual(self.reports, [])

    def test_repeated_ticks_skip_recovered_revision(self):
        project = self.project(120)
        self.writer.submit(self.root, COMPOSER, project); self.writer.flush(10)
        self.assertIsNone(self.writer.submit(self.root, COMPOSER, project))
        self.writer.flush(10)
        self.assertEqual(self.written, [120.])
        # A new original path is a new snapshot even for the same project.
        self.assertIsNotNone(self.writer.submit(self.root, COMPOSER, project, self.root/'Song.project.json'))
        self.writer.flush(10)
        self.assertEqual(len(self.written), 2)
        # Sandbox snapshots compare part by part (same settings object, equal strings).
        settings = Settings()
        self.writer.submit(self.root, SANDBOX, (settings, 'drawing', '')); self.writer.flush(10)
        self.assertIsNone(self.writer.submit(self.root, SANDBOX, (settings, 'drawing', '')))
        self.assertIsNotNone(self.writer.submit(self.root, SANDBOX, (settings, 'music', '')))
        self.writer.flush(10)
        scene = a.recovery_path(self.root, self.pid, SANDBOX)
        self.assertEqual(read_scene(scene)[:2], (settings, 'music'))

    def test_failed_write_keeps_previous_file_reports_and_retries(self):
        self.writer.submit(self.root, COMPOSER, self.project(120)); self.writer.flush(10)
        before = self.path.read_bytes()
        replace_file = app_files.os.replace
        def fail_document(source, target):  # Fails after the temporary file is written and fsynced.
            if Path(target) == self.path: raise OSError('disk failure')
            return replace_file(source, target)
        failing = self.project(130)
        with patch.object(app_files.os, 'replace', side_effect=fail_document):
            self.writer.submit(self.root, COMPOSER, failing); self.writer.flush(10)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.saving-*')), [])
        self.assertEqual(self.reports, ['Could not autosave: disk failure'])
        # The failed snapshot was not acknowledged: the next tick writes it.
        self.assertIsNotNone(self.writer.submit(self.root, COMPOSER, failing))
        self.writer.flush(10)
        self.assertEqual(read_project(self.path).bpm, 130.)

    def test_cleanup_wins_over_in_flight_write(self):
        self.stall()
        project = self.project(140)
        self.writer.submit(self.root, COMPOSER, project)
        self.assertTrue(self.started.wait(5))
        self.writer.submit(self.root, COMPOSER, self.project(141))  # Pending: dropped by cleanup.
        self.assertFalse(self.writer.cleanup(self.root, wait=.05))  # Ordered after the stalled write.
        self.gate.set()
        self.assertTrue(self.writer.flush(10))
        self.assertEqual(self.written, [140.])
        self.assertEqual(list(self.root.iterdir()), [])
        # The late success acknowledged nothing: the same project is written again.
        self.assertIsNotNone(self.writer.submit(self.root, COMPOSER, project))
        self.writer.flush(10)
        self.assertTrue(self.path.exists())
        # One workspace's cleanup leaves the other and the lock.
        self.writer.submit(self.root, SANDBOX, (Settings(), 'drawing', ''))
        self.assertTrue(self.writer.cleanup(self.root, COMPOSER, wait=10))
        self.assertFalse(self.path.exists())
        self.assertTrue(a.recovery_path(self.root, self.pid, SANDBOX).exists())
        self.assertTrue((self.root/f'{self.pid}.lock').exists())

    def test_write_now_orders_after_queue_and_raises(self):
        self.writer.submit(self.root, COMPOSER, self.project(150))
        self.writer.write_now(self.root, COMPOSER, self.project(151))
        self.assertEqual(read_project(self.path).bpm, 151.)
        with patch.object(app_files.os, 'replace', side_effect=OSError('read-only')):
            with self.assertRaises(OSError): self.writer.write_now(self.root, COMPOSER, self.project(152))
        self.assertEqual(self.reports, [])  # The caller reports it (restore shows an error).

    def test_flush_on_quit(self):
        self.stall()
        self.writer.submit(self.root, COMPOSER, self.project(160))
        self.assertTrue(self.started.wait(5))
        self.writer.submit(self.root, COMPOSER, self.project(161))
        threading.Timer(.1, self.gate.set).start()
        self.assertTrue(self.writer.close(10))  # The pending write is flushed before quitting.
        self.assertEqual(self.written, [160., 161.])
        self.assertIsNone(self.writer.submit(self.root, COMPOSER, self.project(162)))
        # A closed writer still cleans up, on the calling thread.
        self.assertTrue(self.writer.cleanup(self.root))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_close_times_out_on_stalled_write(self):
        self.stall()
        self.writer.submit(self.root, COMPOSER, self.project(170))
        self.assertTrue(self.started.wait(5))
        start = time.perf_counter()
        self.assertFalse(self.writer.close(.2))
        self.assertLess(time.perf_counter() - start, 2.)
        self.gate.set()
        self.assertTrue(self.writer.flush(10))
        self.assertEqual(read_project(self.path).bpm, 170.)  # The running write still completes.

    def test_gui_thread_cost_20k_notes(self):
        """The GUI thread only hands over references; serialization runs on the worker."""
        project = replace(new_project(), notes=tuple(Note(k * .25, .24, 60 + k % 8) for k in range(20000)))
        costs, gaps = [], []
        for k in range(3):
            revision = replace(project, bpm=100. + k)
            start = time.perf_counter()
            job = self.writer.submit(self.root, COMPOSER, revision)
            costs.append(time.perf_counter() - start)
            last, gap = time.perf_counter(), 0.
            while not job.done.is_set():  # The GUI thread keeps running Python meanwhile.
                sum(range(200)); now = time.perf_counter()
                gap, last = max(gap, now - last), now
            gaps.append(gap)
        self.assertEqual(self.written, [100., 101., 102.])
        self.assertGreater(self.path.stat().st_size, 1_000_000)
        self.assertLess(statistics.median(costs), .003)
        self.assertLess(statistics.median(gaps), .05)  # GIL hand-offs, never the whole write.
