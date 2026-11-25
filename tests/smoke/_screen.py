"""Window sizes a smoke can count on, and a clean exit when a smoke fails.

A smoke that resizes the editor to a fixed size (1220x840) only gets that size when the
screen has room. At QT_SCALE_FACTOR 1.5 a laptop screen is too short, and macOS clamps the
window (1220x735 on the Mac that found this, November 20, 2026). check_size keeps the exact
check wherever the size fits and otherwise checks that the window filled the room it had.

A smoke whose assert fails while Qt windows are alive can crash in Qt's teardown
(signal 11 on macOS) instead of exiting 1. exit_cleanly_on_failure prints the traceback and
exits with 1 at once, so the runner reports the assert, not a crash.
"""
import os
import sys
import traceback

SLACK = 4  # Logical pixels a window manager may keep back beyond the frame it reports.


def room(window):
    """(width, height) the window's client area can have inside its screen's available area
    (menu bar, dock and taskbar excluded)."""
    area = window.screen().availableGeometry()
    frame, inner = window.frameGeometry(), window.geometry()
    return area.width() - (frame.width() - inner.width()), area.height() - (frame.height() - inner.height())


def check_size(window, width, height):
    """Assert the window took the requested size, or as much of it as the screen allows.

    Returns True for the exact size, False when the screen was too small (with a note)."""
    free_width, free_height = room(window)
    if width <= free_width and height <= free_height:
        assert (window.width(), window.height()) == (width, height), (window.size(), width, height)
        return True
    expected = min(width, free_width), min(height, free_height)
    print(f'Note: {width}x{height} does not fit this screen at this scale (room {free_width}x{free_height}); '
          f'got {window.width()}x{window.height()}.')
    for got, want, free in ((window.width(), width, free_width), (window.height(), height, free_height)):
        if want <= free: assert got == want, (window.size(), width, height)
        # Too big for the room: macOS clamps to the available area, Windows to roughly the whole
        # screen or not at all. Either way the window must use the room it has and never exceed
        # the request; the exact clamp depends on the window manager.
        else: assert free - SLACK - 40 <= got <= want, (window.size(), width, height, free_width, free_height)
    return False


def exit_cleanly_on_failure():
    def hook(kind, value, trace):
        traceback.print_exception(kind, value, trace)
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(1)
    sys.excepthook = hook


def check_label_fits(label, *where, viewport=None):
    """Assert a visible label shows all its text: a wrapped label is tall enough for its lines at
    its width, an unwrapped one wide enough (UI1: the equation panel's help line was clipped).
    With `viewport` (a scroll area's), its left and right edges must also lie inside it."""
    assert label.isVisible(), (*where, label.objectName(), 'hidden')
    if viewport is not None:
        left = label.mapTo(viewport.parentWidget(), label.rect().topLeft()).x() - viewport.x()
        assert left >= -1 and left + label.width() <= viewport.width() + 1, (*where, label.text(), left, label.width(), viewport.width())
    if label.wordWrap():
        needed = label.heightForWidth(label.width())
        assert needed <= label.height() + 1, (*where, label.text(), needed, label.height())
    else:
        assert label.width() + 1 >= label.sizeHint().width(), (*where, label.text(), label.width(), label.sizeHint().width())
    # The label's own row: nothing beside it squeezes it (it starts at its layout's left edge).
    parent = label.parentWidget()
    margin = parent.layout().contentsMargins().left() if parent is not None and parent.layout() is not None else 0
    assert label.x() <= margin + 1, (*where, label.text(), 'shares its row', label.x())
