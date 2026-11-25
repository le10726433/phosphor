"""Crash log for the packaged app: Python stacks on a native crash, plus optional Qt
window-buffer logging (no Qt import here; start() runs before Qt loads).

The packaged app has no terminal, so a segfault leaves only the macOS crash report.
start() points the process's stdout and stderr at logs/phosphor.log in the user folder
and enables faulthandler there, so a crash also records every thread's Python stack,
and Python errors and Qt warnings land in the same file. The previous session's log is
kept as phosphor.previous.log, so the log of a crashed session survives the relaunch.

Diagnostic logging (Help menu, from the next launch) adds Qt's backing-store, window
and screen categories, about 280 KB/s during playback; the file is emptied whenever it
passes MAX_BYTES, so it always holds the most recent few minutes.
"""
import datetime
import faulthandler
import json
import os
import platform
import sys
import threading

from phosphor.storage.app_paths import USER_DIR, FROZEN

LOG_DIR = USER_DIR/'logs'
LOG_PATH = LOG_DIR/'phosphor.log'
PREVIOUS_PATH = LOG_DIR/'phosphor.previous.log'
MAX_BYTES = 64*1024*1024
DIAGNOSTIC_RULES = 'qt.qpa.backingstore*=true;qt.qpa.window*=true;qt.qpa.screen*=true;qt.qpa.screen.updates=false'
PREFERENCE = 'diagnostic_logging'

_stream = None
_lock = threading.Lock()


def wanted():
    """Log in the packaged app, or anywhere with PHOSPHOR_CRASH_LOG=1."""
    return FROZEN or os.environ.get('PHOSPHOR_CRASH_LOG') == '1'


def diagnostics_requested(preferences_path=None):
    """The saved diagnostic_logging preference, read without the preferences module."""
    path = preferences_path or USER_DIR/'preferences.json'
    try:
        with open(path, encoding='utf-8') as file:
            return json.load(file).get(PREFERENCE) is True
    except (OSError, ValueError, AttributeError):
        return False


def start(diagnostics=None):
    """Open the log and route stdout, stderr and faulthandler to it. Returns its path,
    or None when logging is off or the folder cannot be written."""
    global _stream
    if _stream is not None or not wanted(): return LOG_PATH if _stream is not None else None
    if diagnostics is None: diagnostics = diagnostics_requested()
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists(): os.replace(LOG_PATH, PREVIOUS_PATH)
        stream = open(LOG_PATH, 'a', encoding='utf-8', buffering=1, errors='backslashreplace')
    except OSError:
        return None
    _stream = stream
    # Native writers (Qt's message handler, faulthandler) use the file descriptors.
    os.dup2(stream.fileno(), 1); os.dup2(stream.fileno(), 2)
    sys.stdout = sys.stderr = stream
    faulthandler.enable(file=stream, all_threads=True)
    # Qt sends messages to the unified log, not stderr, unless forced.
    os.environ['QT_FORCE_STDERR_LOGGING'] = '1'
    os.environ.setdefault('QT_MESSAGE_PATTERN', '%{time process} %{category} %{type}: %{message}')
    if diagnostics:
        rules = os.environ.get('QT_LOGGING_RULES')
        os.environ['QT_LOGGING_RULES'] = DIAGNOSTIC_RULES + (';' + rules if rules else '')
        threading.Thread(target=_cap, name='crash-log-cap', daemon=True).start()
    from phosphor.storage.build_info import VERSION
    note(f'phosphor {VERSION} started; Python {platform.python_version()}, {platform.platform()}; '
         f'diagnostic logging {"on" if diagnostics else "off"}')
    return LOG_PATH


def note(text):
    """One timestamped line from the app (a breadcrumb for the moments before a crash)."""
    if _stream is None: return
    stamp = datetime.datetime.now().isoformat(timespec='milliseconds')
    with _lock:
        try: _stream.write(f'{stamp} phosphor: {text}\n')
        except (OSError, ValueError): pass


def _cap():
    """Empty the file when it passes MAX_BYTES. The descriptor is in append mode, so
    every writer simply continues at the new end."""
    import time
    while True:
        time.sleep(5)
        try:
            if os.fstat(_stream.fileno()).st_size > MAX_BYTES:
                with _lock:
                    os.ftruncate(_stream.fileno(), 0)
                note(f'log passed {MAX_BYTES // (1024*1024)} MB and was emptied; earlier lines are gone')
        except (OSError, ValueError):
            return
