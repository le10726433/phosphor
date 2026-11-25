"""Qt-free, per-process recovery files. All storage locations are explicit."""
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import ctypes
import json
import os
from pathlib import Path
import threading
import time

from phosphor.storage.app_files import atomic_json, scene_document
from phosphor.storage.project_files import save_project

STARTED_AT = time.time()
SUFFIXES = {'Composer': 'project.json', 'Sandbox': 'scene.json'}


def process_alive(pid):
    if pid <= 0: return False
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() != 87  # Access denied/unknown: conservatively alive.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    except PermissionError: return True
    except OSError: return True
    return True


def recovery_path(directory, pid, workspace):
    return Path(directory)/f'{pid}-{workspace}.{SUFFIXES[workspace]}'


def meta_path(path):
    return path.with_name(path.name.rsplit('.', 2)[0] + '.meta.json')


def write_lock(directory, pid=None):
    pid = os.getpid() if pid is None else pid
    atomic_json(Path(directory)/f'{pid}.lock', {'started_at': STARTED_AT})


def write_recovery(directory, workspace, writer, original=None, pid=None):
    """writer(path) uses the existing atomic project/scene writer."""
    pid = os.getpid() if pid is None else pid
    write_lock(directory, pid)
    path = recovery_path(directory, pid, workspace)
    writer(path)
    atomic_json(meta_path(path), {'original': str(original) if original is not None else None,
                                 'saved_at': datetime.now(timezone.utc).isoformat()})
    return path


@dataclass(frozen=True)
class Recovery:
    path: Path
    pid: int
    workspace: str
    original: str | None
    saved_at: str


def find_stale(directory):
    directory = Path(directory)
    found = []
    pids = set()
    for workspace, suffix in SUFFIXES.items():
        for path in sorted(directory.glob(f'*-{workspace}.{suffix}')):
            try: pid = int(path.name.split('-', 1)[0])
            except ValueError: continue
            # Start time is recorded in the lock. Without a cheap portable start-time
            # query, ANY live pid is protected, including a reused pid.
            if process_alive(pid): continue
            pids.add(pid)
            try:
                meta = json.loads(meta_path(path).read_text(encoding='utf-8'))
                original, saved_at = meta.get('original'), meta.get('saved_at')
                if original is not None and not isinstance(original, str): original = None
                if not isinstance(saved_at, str): saved_at = ''
            except (OSError, ValueError, AttributeError): original, saved_at = None, ''
            if not saved_at:
                saved_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            found.append(Recovery(path, pid, workspace, original, saved_at))
    for lock in directory.glob('*.lock'):
        try: pid = int(lock.stem)
        except ValueError: continue
        if pid not in pids and not process_alive(pid): cleanup(directory, pid)
    return sorted(found, key=lambda item: item.saved_at)


def cleanup(directory, pid=None, workspace=None):
    pid = os.getpid() if pid is None else pid
    for name in ([workspace] if workspace else SUFFIXES):
        path = recovery_path(directory, pid, name)
        path.unlink(missing_ok=True)
        meta_path(path).unlink(missing_ok=True)
    if workspace is None:
        (Path(directory)/f'{pid}.lock').unlink(missing_ok=True)


def discard(directory, recoveries):
    for item in recoveries:
        cleanup(directory, item.pid, item.workspace)
    for pid in {item.pid for item in recoveries}:
        if not any(recovery_path(directory, pid, name).exists() for name in SUFFIXES):
            (Path(directory)/f'{pid}.lock').unlink(missing_ok=True)


def document_writer(workspace, document):
    """writer(path) for write_recovery from an immutable snapshot: a Composer Project, or a
    Sandbox (settings, listening mode, MIDI device). The file format is the saved one."""
    if workspace == 'Composer':
        return lambda path: save_project(path, document)
    settings, mode, device = document
    return lambda path: atomic_json(path, scene_document(settings, mode, device))


def _same(first, second):
    """Snapshot keys match when the directory and original path are equal and every
    document part is the same object (edits replace frozen values, never mutate them)."""
    if first is None or second is None or first[:2] != second[:2] or len(first[2]) != len(second[2]): return False
    return all(a is b or (type(a) is str and a == b) for a, b in zip(first[2], second[2]))


@dataclass(eq=False)
class _Job:
    directory: Path
    workspace: str
    document: object
    original: object
    key: tuple
    epoch: int
    quiet: bool = False
    error: Exception | None = None
    done: threading.Event = field(default_factory=threading.Event)


class RecoveryWriter:
    """Recovery writes on one background thread (0.76.0-dev, audit P12).

    The GUI thread hands over immutable snapshots; the worker serializes, writes a temporary
    file, fsyncs and replaces (write_recovery). Operations run in submission order. Pending
    writes coalesce per workspace (the latest snapshot wins, at most one in flight); a
    cleanup drops pending writes for its workspaces and runs after any in-flight write, so an
    older write never resurrects a recovery file. The last durably written snapshot is
    remembered per workspace, so repeated ticks skip it; a failed write stays eligible for
    retry, and a write that finishes after a cleanup acknowledges nothing.
    report(message) is called on the worker thread: the editor passes a queued Qt signal.
    """

    def __init__(self, report=None, pid=None):
        self.report, self.pid = report, os.getpid() if pid is None else pid
        self._cond = threading.Condition()
        self._queue = deque()  # ('write', workspace) markers and ('cleanup', directory, workspace, event).
        self._pending, self._recovered, self._epoch = {}, {}, {}
        self._active = None
        self._thread = None
        self._closed = False

    def _key(self, directory, document, original):
        return (Path(directory), None if original is None else str(original),
                document if isinstance(document, tuple) else (document,))

    def _start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='phosphor-autosave', daemon=True)
            self._thread.start()
        self._cond.notify_all()

    def submit(self, directory, workspace, document, original=None, force=False, quiet=False):
        """Queue a recovery write; returns its job, or None when this snapshot is already
        recovered (or queued) or the writer is closed. Cheap: no serialization here."""
        key = self._key(directory, document, original)
        with self._cond:
            if self._closed: return None
            pending = self._pending.get(workspace)
            if not force:
                active = self._active  # A write from before a cleanup counts for nothing.
                if not (isinstance(active, _Job) and active.workspace == workspace
                        and active.epoch == self._epoch.get(workspace, 0)): active = None
                latest = pending or active
                if _same(latest.key if latest else self._recovered.get(workspace), key): return None
            job = _Job(Path(directory), workspace, document, original, key, self._epoch.get(workspace, 0), quiet)
            if pending is None: self._queue.append(('write', workspace))
            else: pending.done.set()  # Superseded: the newer snapshot takes its place in the queue.
            self._pending[workspace] = job
            self._start()
            return job

    def write_now(self, directory, workspace, document, original=None):
        """Write in order with everything queued and wait; raises the write's error."""
        job = self.submit(directory, workspace, document, original, force=True, quiet=True)
        if job is None: raise RuntimeError('Recovery writer is closed')
        job.done.wait()
        if job.error is not None: raise job.error

    def cleanup(self, directory, workspace=None, wait=1.):
        """Remove this process's recovery files (one workspace, or all and the lock) after any
        in-flight write; pending writes are dropped. Waits up to wait seconds; returns
        whether the files are gone. Ordering holds even if the wait times out."""
        names = [workspace] if workspace else list(SUFFIXES)
        done = threading.Event()
        with self._cond:
            for name in names:
                self._epoch[name] = self._epoch.get(name, 0) + 1
                self._recovered.pop(name, None)
                job = self._pending.pop(name, None)
                if job is not None: job.done.set()
            self._queue = deque(entry for entry in self._queue if not (entry[0] == 'write' and entry[1] in names))
            closed = self._closed
            if not closed:
                self._queue.append(('cleanup', Path(directory), workspace, done))
                self._start()
        if closed:  # No worker any more: clean up here, as before 0.76.0.
            try: cleanup(directory, self.pid, workspace)
            except OSError as error: self._report(f'Could not remove recovery files: {error}')
            return True
        return done.wait(wait)

    def busy(self):
        with self._cond: return bool(self._queue) or self._active is not None

    def flush(self, timeout=None):
        """Wait until everything queued has run; False if the timeout expired first."""
        with self._cond:
            return self._cond.wait_for(lambda: not self._queue and self._active is None, timeout)

    def close(self, timeout=2.):
        """Quit: give queued work up to timeout seconds, then stop accepting more. A write
        still running stays on its daemon thread; its temporary-file replace keeps the
        previous recovery intact if the process ends first. Returns whether it all finished."""
        finished = self.flush(timeout)
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        return finished

    def _report(self, message):
        if self.report is not None and not self._closed:
            try: self.report(message)
            except Exception: pass  # A closing window must not kill the worker.

    def _run(self):
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._queue or self._closed)
                if not self._queue: return
                entry = self._queue.popleft()
                if entry[0] == 'write': entry = self._pending.pop(entry[1])
                self._active = entry
            message = None
            if isinstance(entry, _Job):
                try:
                    write_recovery(entry.directory, entry.workspace,
                                   document_writer(entry.workspace, entry.document), entry.original, self.pid)
                except Exception as error:
                    entry.error = error
                    if not entry.quiet: message = f'Could not autosave: {error}'
            else:
                _, directory, workspace, done = entry
                try: cleanup(directory, self.pid, workspace)
                except OSError as error: message = f'Could not remove recovery files: {error}'
            with self._cond:
                if isinstance(entry, _Job) and entry.error is None and self._epoch.get(entry.workspace, 0) == entry.epoch:
                    self._recovered[entry.workspace] = entry.key
                self._active = None
                self._cond.notify_all()
            (entry.done if isinstance(entry, _Job) else done).set()
            if message: self._report(message)
