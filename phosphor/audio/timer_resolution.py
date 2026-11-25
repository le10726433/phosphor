"""A 1 ms system timer resolution while audio plays (Windows only; P7c, hot spots H1 and H4).

Windows wakes timed waits on its 15.6 ms timer tick. That includes CPython's GIL: a thread waiting
for it asks the holder to let go after `sys.getswitchinterval()` (5 ms), but the wait itself
rounds up to the next tick, so the audio thread could wait up to about 16 ms behind the GUI thread
for every block. `timeBeginPeriod(1)` makes those waits about 1 ms.

Since Windows 10 version 2004 the request only affects the process that makes it (not the whole
system), and Windows 11 ignores it while the process's windows are minimized or hidden. It costs
some power, so it is held only while an audio stream is open: `begin()` and `end()` are counted,
so nested streams are safe. macOS needs nothing: its waits are not tied to a coarse tick.
"""
import sys
import threading

PERIOD_MS = 1
_lock = threading.Lock()
_count = 0
_winmm = None


def _api():
    global _winmm
    if _winmm is None:
        import ctypes
        _winmm = ctypes.WinDLL('winmm')
    return _winmm


def begin():
    """Ask for the fine timer; True if this call raised the resolution."""
    global _count
    if sys.platform != 'win32': return False
    with _lock:
        _count += 1
        if _count > 1: return False
        try:
            return _api().timeBeginPeriod(PERIOD_MS) == 0  # TIMERR_NOERROR
        except (OSError, AttributeError):
            return False


def end():
    """Release one begin(); the last release restores the default resolution."""
    global _count
    if sys.platform != 'win32': return
    with _lock:
        if _count == 0: return
        _count -= 1
        if _count: return
        try:
            _api().timeEndPeriod(PERIOD_MS)
        except (OSError, AttributeError):
            pass


def active():
    return _count > 0
