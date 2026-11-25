"""File navigation, cancellable searches and bounded audio previews for the browser."""
from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import string
import subprocess
import sys
import threading

import numpy as np
import soundfile

from phosphor.engine import audio_clips

AUDIO_TYPES = audio_clips.AUDIO_FILE_TYPES
SCENE_TYPES = ('.scene.json',)
MODEL_TYPES = ('.obj', '.stl', '.gltf', '.glb')


def matches(name, types):
    return str(name).casefold().endswith(tuple(ext.casefold() for ext in types))


def _local_drive(path):
    if str(path).replace('\\', '/').startswith('//'):
        return False
    if os.name == 'nt' and path.drive:
        import ctypes
        # Query the drive mapping before doing any filesystem I/O on its root.
        return ctypes.windll.kernel32.GetDriveTypeW(path.anchor) in (2, 3, 5, 6)
    return True


def _remote_mounts():
    """Read the OS mount table without querying any of the mounted filesystems."""
    if sys.platform == 'darwin':
        table = subprocess.run(['/sbin/mount'], capture_output=True, timeout=.25, check=True)
        mounts = []
        for line in os.fsdecode(table.stdout).splitlines():
            _, _, target = line.partition(' on ')
            path, _, options = target.rpartition(' (')
            flags = options.rstrip(')').split(', ')
            if path and ('local' not in flags or 'autofs' in flags):
                mounts.append(Path(path))
        return mounts
    if sys.platform.startswith('linux'):
        mounts = []
        for line in Path('/proc/self/mountinfo').read_text(errors='surrogateescape').splitlines():
            mount, _, details = line.partition(' - ')
            filesystem = details.split()[0]
            # Unknown mounts (in particular FUSE and automounters) can make a stat block.
            if filesystem not in {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs', 'tmpfs',
                                  'ramfs', 'overlay', 'vfat', 'exfat', 'ntfs3', 'f2fs',
                                  'squashfs', 'erofs', 'bcachefs', 'jfs', 'hfsplus'}:
                target = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), mount.split()[4])
                mounts.append(Path(target))
        return mounts
    return []


def _local_folder(path, remote):
    """Check components in order so a redirected parent cannot trigger a network lookup."""
    path = path.absolute()
    current = Path(path.anchor)
    if current in remote or not _local_drive(path):
        return False
    parts = deque(path.parts[1:])
    links = 0
    while parts:
        part = parts.popleft()
        if part == '..':
            current = current.parent
            continue
        current /= part
        if current in remote:
            return False
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            links += 1
            if links > 40:
                return False
            target = current.readlink()
            if target.is_absolute():
                if not _local_drive(target) or Path(target.anchor) in remote:
                    return False
                current = Path(target.anchor)
                parts.extendleft(reversed(target.parts[1:]))
            else:
                current = current.parent
                parts.extendleft(reversed(target.parts))
        elif (not stat.S_ISDIR(info.st_mode) or
              getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            return False
    return True


def standard_places(home=None, platform=None):
    home = Path.home() if home is None else Path(home)
    platform = sys.platform if platform is None else platform
    places = []
    remote = None
    try:
        remote = set(_remote_mounts())
        local_home = _local_folder(home, remote)
    except (OSError, subprocess.SubprocessError):
        local_home = False
    if local_home:
        for label in ('Home', 'Desktop', 'Documents', 'Music', 'Downloads'):
            path = home if label == 'Home' else home / label
            try:
                if label == 'Home' or _local_folder(path, remote):
                    places.append((label, path))
            except OSError:
                continue
    if platform == 'win32':
        if hasattr(os, 'listdrives'):
            roots = os.listdrives()  # Drive names only; never stat the returned roots.
        elif os.name == 'nt':
            import ctypes
            # The same local drive-letter bitmap used by listdrives on newer Python.
            mask = ctypes.windll.kernel32.GetLogicalDrives()
            roots = [f'{letter}:\\' for index, letter in enumerate(string.ascii_uppercase)
                     if index >= 2 and mask & (1 << index)]
        else:
            roots = []
            for letter in string.ascii_uppercase[2:]:
                root = Path(f'{letter}:\\')
                if _local_drive(root) and root.exists():
                    roots.append(root)
        for root in roots:
            drive = PureWindowsPath(root).drive
            if len(drive) == 2 and drive[0].upper() in string.ascii_uppercase[2:]:
                places.append((drive, Path(root)))
    elif platform == 'darwin' and remote is not None:
        try:
            if _local_folder(Path('/Volumes'), remote):
                with os.scandir('/Volumes') as entries:
                    places.extend((entry.name, Path(entry.path)) for entry in entries)
        except OSError:
            pass
    elif platform != 'darwin':
        places.append(('/', Path('/')))
    return places


@dataclass(frozen=True)
class Listing:
    folders: tuple[Path, ...]
    files: tuple[Path, ...]
    error: str


def _hidden(entry):
    if entry.name.startswith('.'):
        return True
    if os.name == 'nt':
        attrs = entry.stat(follow_symlinks=False).st_file_attributes
        return bool(attrs & stat.FILE_ATTRIBUTE_HIDDEN)
    # macOS hides ~/Library and others with the UF_HIDDEN flag, not a dot.
    return bool(getattr(entry.stat(follow_symlinks=False), 'st_flags', 0) & getattr(stat, 'UF_HIDDEN', 0))


def _search_folder(entry):
    if not entry.is_dir(follow_symlinks=False):
        return False
    if os.name == 'nt':
        # Junctions can point to an ancestor just like a directory symlink.
        attrs = entry.stat(follow_symlinks=False).st_file_attributes
        return not (attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return True


def list_folder(folder, types):
    folders, files = [], []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if _hidden(entry):
                    continue
                if entry.is_dir():
                    folders.append(Path(entry.path))
                elif matches(entry.name, types) and entry.is_file():
                    files.append(Path(entry.path))
    except OSError as error:
        return Listing((), (), error.strerror or str(error))
    folders.sort(key=lambda path: path.name.casefold())
    files.sort(key=lambda path: path.name.casefold())
    return Listing(tuple(folders), tuple(files), '')


@dataclass(frozen=True)
class SearchResult:
    files: tuple[Path, ...]
    truncated: bool


def search(folder, text, types, stop=None, limit=500, max_entries=50000):
    words = text.casefold().split()
    if not words:
        return SearchResult((), False)
    if limit <= 0 or max_entries <= 0:
        return SearchResult((), True)
    queue = deque([folder])
    files = []
    scanned = 0
    while queue:
        if stop is not None and stop.is_set():
            return SearchResult(tuple(files), True)
        try:
            with os.scandir(queue.popleft()) as entries:
                for entry in entries:
                    if stop is not None and stop.is_set():
                        return SearchResult(tuple(files), True)
                    scanned += 1
                    try:
                        if not _hidden(entry):
                            if _search_folder(entry):
                                queue.append(entry.path)
                            elif matches(entry.name, types) and entry.is_file():
                                name = entry.name.casefold()
                                if all(word in name for word in words):
                                    files.append(Path(entry.path))
                    except OSError:
                        pass  # A single entry may vanish during a search.
                    if len(files) >= limit or scanned >= max_entries:
                        return SearchResult(tuple(files), True)
        except OSError:
            continue
    return SearchResult(tuple(files), stop is not None and stop.is_set())


@dataclass(frozen=True)
class AudioInfo:
    seconds: float
    rate: int
    channels: int


def audio_info(path):
    try:
        info = soundfile.info(path)
        return AudioInfo(info.duration, info.samplerate, info.channels)
    except (OSError, RuntimeError, ValueError):
        return None


def describe(info):
    if info.seconds < 60:
        duration = f'{info.seconds:.1f} s'
    else:
        minutes, seconds = divmod(int(info.seconds), 60)
        duration = f'{minutes}:{seconds:02}'
    channels = {1: 'mono', 2: 'stereo'}.get(info.channels, f'{info.channels} channels')
    return f'{duration} · {info.rate / 1000:g} kHz · {channels}'


def read_preview(path, rate, seconds=30.):
    samples = audio_clips._read_stereo(path, rate, seconds)
    attack = min(len(samples), max(1, round(.002 * rate)))
    release = min(len(samples), max(1, round(.005 * rate)))
    if len(samples):
        samples[:attack] *= np.linspace(0., 1., attack)[:, None]
        samples[-release:] *= np.linspace(0., 1., release)[::-1, None]
    samples.setflags(write=False)
    return samples


@dataclass(frozen=True)
class BrowseResult:
    channel: str
    token: int
    value: object
    error: Exception | None


class BrowseWorker:
    def __init__(self):
        self._condition = threading.Condition()
        self._pending = {}
        self._latest = {}
        self._results = []
        self._token = 0
        self._running = None
        self._thread = None
        self._closed = False

    def submit(self, channel, fn, *args, stoppable=False):
        with self._condition:
            if self._closed:
                raise RuntimeError('Browser worker is closed.')
            if self._running and self._running[0] == channel:
                self._running[1].set()
            self._token += 1
            self._latest[channel] = self._token
            self._pending[channel] = (self._token, fn, args, stoppable)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True, name='browser')
                self._thread.start()
            self._condition.notify()
            return self._token

    def latest(self, channel):
        with self._condition:
            return self._latest.get(channel)

    def take(self):
        if not self._condition.acquire(blocking=False):
            return []
        try:
            results, self._results = self._results, []
            return results
        finally:
            self._condition.release()

    def close(self, timeout=1.):
        with self._condition:
            self._closed = True
            self._pending.clear()
            if self._running:
                self._running[1].set()
            self._condition.notify()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    @staticmethod
    def _execute(channel, token, fn, args, stoppable, stop):
        try:
            value = fn(*args, stop=stop) if stoppable else fn(*args)
            return BrowseResult(channel, token, value, None)
        except Exception as error:
            return BrowseResult(channel, token, None, error)

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending)
                if self._closed:
                    return
                channel = next(iter(self._pending))
                token, fn, args, stoppable = self._pending.pop(channel)
                stop = threading.Event()
                self._running = (channel, stop)
            result = self._execute(channel, token, fn, args, stoppable, stop)
            with self._condition:
                self._results.append(result)
                self._running = None
            # An idle browser can live for hours. Let take() release the last decoded file.
            del result, fn, args
