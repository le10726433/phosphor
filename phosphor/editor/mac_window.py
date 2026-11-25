"""macOS window level and frame through the Objective-C runtime (ctypes, no PyObjC).

A frameless window at normal level is kept below the menu bar (and its frame shortened
by the menu bar's height), so a full-screen trace left a 38 px strip on a notched
MacBook. Raising the window above the menu bar and Dock levels lets it cover the whole
screen without a native full-screen Space; the rest of the app is unaffected.
"""
import ctypes
import ctypes.util
import platform
import sys

NORMAL_LEVEL = 0         # NSNormalWindowLevel
ABOVE_MENU_BAR = 25      # NSStatusWindowLevel: above the menu bar (24) and the Dock (20).


class _Rect(ctypes.Structure):
    _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double), ('width', ctypes.c_double), ('height', ctypes.c_double)]


_runtime = None


def _objc():
    global _runtime
    if _runtime is None:
        library = ctypes.cdll.LoadLibrary(ctypes.util.find_library('objc'))
        library.sel_registerName.restype = ctypes.c_void_p; library.sel_registerName.argtypes = [ctypes.c_char_p]
        _runtime = library
    return _runtime


def _send(receiver, selector, restype=ctypes.c_void_p, *args):
    objc = _objc()
    types = [type(a) for a in args]  # Every argument is already a ctypes value.
    if restype is _Rect and platform.machine() == 'x86_64':  # Large structs come back through _stret on Intel.
        function = ctypes.CFUNCTYPE(None, ctypes.POINTER(_Rect), ctypes.c_void_p, ctypes.c_void_p, *types)(('objc_msgSend_stret', objc))
        result = _Rect(); function(ctypes.byref(result), receiver, objc.sel_registerName(selector), *args); return result
    function = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *types)(('objc_msgSend', objc))
    return function(receiver, objc.sel_registerName(selector), *args)


def cover_screen(widget, on):
    """Raise widget's native window above the menu bar and Dock and fill its screen
    (on=True), or return it to the normal level (on=False). No-op off macOS; returns
    False if the native window could not be reached."""
    if sys.platform != 'darwin': return False
    try:
        view = ctypes.c_void_p(int(widget.winId()))
        window = _send(view, b'window')
        if not window: return False
        window = ctypes.c_void_p(window)
        _send(window, b'setLevel:', None, ctypes.c_long(ABOVE_MENU_BAR if on else NORMAL_LEVEL))
        if on:
            screen = _send(window, b'screen')
            if not screen: return False
            frame = _send(ctypes.c_void_p(screen), b'frame', _Rect)
            _send(window, b'setFrame:display:', None, frame, ctypes.c_bool(True))
        return True
    except (OSError, AttributeError, ValueError):
        return False
