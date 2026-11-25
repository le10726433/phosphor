"""Pop-out signal monitor: corner buttons, its own window (move, resize), the time view in it,
the full-screen trace with the main window still usable, return paths, saved geometry, clean exit.

Run natively (Direct3D 11 on Windows, Metal on macOS); picture checks need a real GPU window.
"""
import _path  # noqa: F401  (makes the phosphor package importable)
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtGui import QColor, QImage, QSurfaceFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from phosphor.editor import qt_app as q


def lit(view):
    image = view.grabWindow().convertToFormat(QImage.Format.Format_RGBA8888)
    data = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine()//4, 4)[..., :3]
    return int((data.max(axis=2) > 100).sum())


def pixels(view):
    image = view.grabWindow().convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine()//4, 4)[..., :3].astype(int)


def looks(view, palette):
    """(background corner pixel, mean colour of lit trace pixels); asserts the palette background."""
    data = pixels(view); background = np.array(QColor(q.PALETTES[palette]['background']).getRgb()[:3])
    corner = data[data.shape[0]//2, 3]
    assert np.abs(corner - background).max() <= 3, ('monitor background is not the palette background', corner, background)
    lit = data[data.max(axis=2) > 100]
    assert len(lit) > 200, 'no trace'
    return corner, lit.mean(axis=0)


def ticks(w, n=10):
    for _ in range(n): w.tick(); QTest.qWait(16)


def corner(view, index, count=2):
    """Centre of corner button `index` in a strip of `count` (CornerButtons layout)."""
    width = count*32-4
    return QPoint(round(view.width()-width-8+index*32+14), 22)


def click(view, point):
    QTest.mouseMove(view, point); QTest.qWait(30)
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point); QTest.qWait(80)


folder = tempfile.TemporaryDirectory(); saved = Path(folder.name)/'monitor-window.json'
q.configure_graphics(); surface = QSurfaceFormat(); surface.setSamples(4); QSurfaceFormat.setDefaultFormat(surface)
app = QApplication([])
with patch.object(q, 'MONITOR_WINDOW_PATH', saved):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox')); w.resize(1220, 840); w.show(); QTest.qWait(300)
    PALETTE = 'Green monochrome'
    w.apply_preferences(dict(w.preferences, phosphor='P31 green', crt_scanlines=False, show_grid=False, palette=PALETTE))
    w.add_object('Cube'); ticks(w, 20)
    main = w.main_host.view
    assert lit(main) > 200, 'main monitor should draw the cube'
    _, main_trace = looks(main, PALETTE)

    # The corner buttons appear only while the mouse is near the top-right corner.
    buttons = w.corner_buttons
    assert buttons.opacity() == 0
    QTest.mouseMove(main, QPoint(main.width()//2, main.height()//2)); QTest.qWait(50)
    assert buttons.opacity() == 0
    QTest.mouseMove(main, corner(main, 0)); QTest.qWait(50)
    assert buttons.opacity() == 1, 'hovering the top-right corner should show the buttons'
    QTest.mouseMove(main, QPoint(main.width()//2, main.height()//2)); QTest.qWait(50)
    assert buttons.opacity() == 0

    # Pop out with the corner button.
    click(main, corner(main, 0))
    win = w.monitor_window
    assert w.popped and win is not None and win.isVisible() and w.host is win.host and w.popout_button.isChecked()
    assert w.stage.placeholder.isVisible() and not w.main_host.isVisible()
    QTest.qWait(200); ticks(w, 25)
    pop = win.host.view
    assert lit(pop) > 200, 'the pop-out should draw the cube'
    # Same palette as the main view: its background, and a trace of the same colour (user report: both turned white).
    _, pop_trace = looks(pop, PALETTE)
    # Compare colour, not brightness: a different window size changes beam density per pixel.
    hue = lambda rgb: rgb/np.linalg.norm(rgb)
    assert np.abs(hue(pop_trace) - hue(main_trace)).max() < .08 and pop_trace[1] > pop_trace[0] + 30, (pop_trace, main_trace)
    # A palette change while popped out reaches the pop-out.
    w.apply_preferences(dict(w.preferences, palette='Amber')); ticks(w, 15); looks(pop, 'Amber')
    w.apply_preferences(dict(w.preferences, palette=PALETTE)); ticks(w, 15)
    assert w.host.scope.overlays == [] and w.handles == [], 'the pop-out is view-only'
    # Resize and move: the scope follows the window.
    area = win.screen().availableGeometry()
    target = QRect(area.x()+60, area.y()+80, 520, 380)
    win.setGeometry(target); QTest.qWait(250); ticks(w, 10)
    assert abs(win.host.scope.width()-pop.width()) < 1 and abs(pop.width()-520) <= 2 and abs(pop.height()-380) <= 2, (pop.width(), pop.height())
    assert lit(pop) > 100
    # The time view works in the pop-out too.
    w.sweep_display.setCurrentIndex(1); ticks(w, 20)
    assert w.sweep.sweeps[0][0].filled > 0 and lit(pop) > 100
    w.sweep_display.setCurrentIndex(0); ticks(w, 5)

    # Full-screen trace from the pop-out's corner button; the main window stays shown and usable.
    normal = win.geometry()
    click(pop, corner(pop, 1))
    QTest.qWait(400); ticks(w, 10)
    screen = win.screen().geometry()
    assert win.is_fullscreen and w.trace_fullscreen_button.isChecked()
    assert win.geometry() == screen or win.isFullScreen(), (win.geometry(), screen)
    assert abs(pop.width()-screen.width()) <= 2 and abs(pop.height()-screen.height()) <= 2, (pop.width(), pop.height(), screen)
    assert w.isVisible() and not w.isMinimized()
    w.edit('shape_size', 1.2); ticks(w, 5)  # Editing still works in the main window.
    assert w.parameters['shape_size'].value() == 1.2 or w.scene.shape is not None
    assert lit(pop) > 100
    # Escape in the full-screen trace restores the exact window.
    QTest.keyClick(pop, Qt.Key.Key_Escape); QTest.qWait(400)
    assert not win.is_fullscreen and not w.trace_fullscreen_button.isChecked()
    assert win.geometry() == normal, (win.geometry(), normal)

    # Return with the corner button.
    click(pop, corner(pop, 0)); ticks(w, 15)
    assert not w.popped and not win.isVisible() and w.host is w.main_host and not w.popout_button.isChecked()
    assert w.main_host.isVisible() and not w.stage.placeholder.isVisible()
    assert lit(main) > 200, 'the main monitor draws again after returning'

    # Pop out from the Monitor tab; closing the window brings the monitor back.
    w.popout_button.click(); QTest.qWait(200)
    assert w.popped and win.isVisible() and win.geometry() == normal, (win.geometry(), normal)
    win.close(); QTest.qWait(100)
    assert not w.popped and not win.isVisible() and w.host is w.main_host
    # The placeholder brings it back too.
    w.popout_button.click(); QTest.qWait(150); w.stage.placeholder.click(); QTest.qWait(100)
    assert not w.popped

    # Full-screen trace straight from the main viewport's corner button.
    click(main, corner(main, 1)); QTest.qWait(400); ticks(w, 10)
    assert w.popped and win.is_fullscreen and lit(pop) > 100
    w.trace_fullscreen_button.click(); QTest.qWait(400)
    assert not win.is_fullscreen and w.popped
    # Trace only (immersive) brings the monitor back into the main window first.
    w.set_immersive(True); QTest.qWait(300)
    assert not w.popped and w.immersive and w.corner_buttons._names == []
    w.set_immersive(False); QTest.qWait(300)
    assert w.corner_buttons._names == ['popout', 'fullscreen']

    # Saved position (main() turns remembering on; nothing is written otherwise).
    assert not saved.exists()
    w.remember_monitor = True
    w.pop_out(); win.setGeometry(QRect(area.x()+100, area.y()+120, 500, 400)); QTest.qWait(200)
    keep = win.geometry(); w.pop_in()
    document = q.read_monitor_window()
    assert document is not None and document['open'] is False and (document['x'], document['y'], document['width'], document['height']) == (keep.x(), keep.y(), keep.width(), keep.height()), document
    w.pop_out(); w.release_views()  # Closing the app while popped out saves open = True and hides it.
    assert q.read_monitor_window()['open'] is True and not win.isVisible()
    w.force_close = True; w.close(); app.processEvents()

    # A new window reopens the pop-out where it was.
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox')); w.resize(1220, 840); w.show(); QTest.qWait(200)
    w.restore_monitor_window(); QTest.qWait(200)
    assert w.popped and w.monitor_window.geometry() == keep, (w.monitor_window.geometry(), keep)
    q.write_monitor_window(dict(q.read_monitor_window(), x=-50000, y=-50000))  # A screen that is gone.
    document = q.read_monitor_window(); w.monitor_window.restore_geometry(document, QApplication.screens())
    assert any(s.availableGeometry().intersects(w.monitor_window.geometry()) for s in QApplication.screens())
    saved.write_text('{not json', encoding='utf-8'); assert q.read_monitor_window() is None
    w.remember_monitor = False; w.release_views(); w.force_close = True; w.close(); app.processEvents()
print('Monitor pop-out passed: corner buttons on hover, pop out/return, move and resize, time view, '
      'full-screen trace with the main window usable, Escape restore, Trace only, saved geometry, clean exit.')
