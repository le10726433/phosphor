"""Keep a Python error in a paintEvent from becoming a native crash.

When a paintEvent override raises, PySide prints the error but the traceback keeps the
frame, and its QPainter, alive. Qt then finishes the backing store with that painter still
active, and on macOS the next flush segfaults inside QCALayerBackingStore (0.65.1: an
N2 chord-name KeyError crashed playback this way). Catching the error here ends the
traceback, so the painter is destroyed and ends before Qt flushes; the frame is just
drawn incompletely. Each distinct error is reported once.
"""
import functools
import sys
import traceback

_reported = set()


def guarded_paint(paint):
    @functools.wraps(paint)
    def paintEvent(self, event):
        try:
            return paint(self, event)
        except Exception as error:
            key = (type(self).__name__, type(error).__name__, str(error))
            if key not in _reported:
                _reported.add(key)
                print(f'Error painting {type(self).__name__} (reported once; the frame was drawn incompletely):',
                      file=sys.stderr)
                traceback.print_exc()
    return paintEvent
