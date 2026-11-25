"""UI2 in the Composer, native or --headless: the side panel's dividing line (drag, double-click
reset, controls widen, width kept as a preference), left-aligned tab bars, Preferences › Controls
(rebind a shortcut and use it, conflict prompt, reset, export and import, a damaged file) and
synthetic wheel, trackpad pixel-delta and pinch events on the roll and the Builder canvas."""
import _path  # noqa: F401
import json
import os
import sys
import tempfile
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint, QPointF, QEvent
from PySide6.QtGui import QWheelEvent, QMouseEvent, QNativeGestureEvent, QPointingDevice, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTabWidget

from _screen import exit_cleanly_on_failure
from phosphor.editor import qt_app as q
from phosphor.editor import composer_view as cv
from phosphor.editor.layout_metrics import PANEL_RANGE, PANEL_DEFAULT, SPLIT_HANDLE
from phosphor.storage.shortcut_bindings import read_bindings

exit_cleanly_on_failure()
q.configure_graphics()
app = QApplication([])
folder = Path(tempfile.mkdtemp(prefix='phosphor-ui2-'))


def mouse(widget, kind, x, buttons=Qt.MouseButton.LeftButton, origin=None):
    """A mouse event x px from origin (the handle's place when the drag began: the handle moves with it)."""
    globe = QPointF(origin) + QPointF(x, widget.height() / 2)
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    app.sendEvent(widget, QMouseEvent(kind, QPointF(widget.mapFromGlobal(globe.toPoint())), globe, button, buttons, Qt.KeyboardModifier.NoModifier))


def wheel(view, pos, pixel=QPoint(), angle=QPoint(), phase=Qt.ScrollPhase.NoScrollPhase, modifiers=Qt.KeyboardModifier.NoModifier):
    app.sendEvent(view, QWheelEvent(QPointF(pos), QPointF(view.mapToGlobal(pos)), pixel, angle, Qt.MouseButton.NoButton,
                                    modifiers, phase, False))


def pinch(target, pos, value, alt=False):
    event = QNativeGestureEvent(Qt.NativeGestureType.ZoomNativeGesture, QPointingDevice.primaryPointingDevice(), 2,
                                QPointF(pos), QPointF(pos), QPointF(pos), value, QPointF(), 1)
    if alt: event.setModifiers(Qt.KeyboardModifier.AltModifier)
    event.setAccepted(False); app.sendEvent(target, event)  # Through Qt's delivery (a Quick window finds its item).
    return event.isAccepted()


with patch.object(q, 'write_workspace', lambda *args: None):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Composer'))
    try:
        w.resize(1220, 840); w.show(); w.activateWindow(); QTest.qWait(300)
        w.show_panel(q.PANEL_SCENE); w.add_object('Tilting circle'); QTest.qWait(50)

        # 1. The side panel's edge: a visible line in a SPLIT_HANDLE grab zone with a resize cursor.
        handle = w.splitter.handle(1)
        assert isinstance(handle, q.LineHandle) and w.splitter.handleWidth() == SPLIT_HANDLE
        assert handle.cursor().shape() == Qt.CursorShape.SplitHCursor
        assert w.sidebar.width() == PANEL_DEFAULT, w.sidebar.width()
        field, combo = w.shape_list, w.add_button
        assert field.isVisible() and combo.isVisible()
        narrow_field, narrow_combo = field.width(), combo.width()
        # Drag the line 200 px left: the panel widens, its controls with it (text keeps its size).
        origin = handle.mapToGlobal(QPoint(0, 0))
        mouse(handle, QEvent.Type.MouseButtonPress, SPLIT_HANDLE // 2, Qt.MouseButton.LeftButton, origin)
        for step in range(1, 11): mouse(handle, QEvent.Type.MouseMove, SPLIT_HANDLE // 2 - 20 * step, origin=origin)
        mouse(handle, QEvent.Type.MouseButtonRelease, SPLIT_HANDLE // 2 - 200, Qt.MouseButton.NoButton, origin)
        QTest.qWait(600)  # The width is saved once the drag settles.
        wide = w.sidebar.width()
        assert abs(wide - (PANEL_DEFAULT + 200)) <= 2, wide
        assert w.preferences['panel_width'] == wide
        assert field.width() > narrow_field and combo.width() > narrow_combo, (field.width(), narrow_field, combo.width(), narrow_combo)
        assert w.panels[q.PANEL_SCENE].horizontalScrollBar().maximum() == 0
        # Narrowest and widest the range allows.
        w.set_panel_width(10); QTest.qWait(50); assert w.sidebar.width() == PANEL_RANGE[0], w.sidebar.width()
        assert w.panels[q.PANEL_SCENE].horizontalScrollBar().maximum() == 0
        w.set_panel_width(5000); QTest.qWait(50); assert w.sidebar.width() <= PANEL_RANGE[1]
        # Double-click resets the default width.
        QTest.mouseDClick(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(SPLIT_HANDLE // 2, handle.height() // 2))
        QTest.qWait(600); assert w.sidebar.width() == PANEL_DEFAULT and w.preferences['panel_width'] == PANEL_DEFAULT, w.sidebar.width()

        # 2. Every tab bar starts at its panel border (macOS centres tabs by default).
        w.show_preferences('Controls'); dialog = w.preferences_dialog; QTest.qWait(100)
        controls = dialog.findChild(q.QWidget, 'controls_tabs').parentWidget()
        assert w.preferences_tabs.currentWidget() is controls
        tabs = [t for t in (*w.findChildren(QTabWidget), *dialog.findChildren(QTabWidget))]
        assert {'controls_tabs', 'preferences_tabs', 'appearance_tabs'} <= {t.objectName() for t in tabs}
        for widget in tabs:
            if not widget.isVisible(): continue
            first = widget.tabBar().tabRect(0).translated(widget.tabBar().pos())
            assert first.left() <= 1, (widget.objectName(), first, widget.tabBar().pos())

        # 3. Keyboard shortcuts: rebind Trace only, use it, a conflict prompt, reset, export/import, damaged file.
        path = folder / 'shortcuts.json'; w.init_shortcuts(path)
        editor = controls.shortcut_editor; controls.tabs.setCurrentWidget(editor)
        asked = []
        w.ask = lambda title, text: asked.append(text) or answers.pop(0)
        w.warn = lambda title, text: (_ for _ in ()).throw(AssertionError(text))
        editor.search.setText('trace only'); found = [i.data(0, Qt.ItemDataRole.UserRole) for i in editor.items()]
        assert 'trace_only' in found and 'save' not in found, found
        editor.search.clear()
        editor.select('trace_only'); w.capture_shortcut = lambda description, current: 'Ctrl+Alt+T'; editor.change()
        assert w.immersive_action.shortcuts() == [QKeySequence('Ctrl+Alt+T')]
        assert read_bindings(path, defaults=w.shortcut_defaults()).keys('trace_only') == ('Ctrl+Alt+T',)
        dialog.reject(); QTest.qWait(50); w.activateWindow(); QTest.qWait(100)
        QTest.keyClick(w, Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier); QTest.qWait(100)
        assert w.immersive; w.set_immersive(False); QTest.qWait(100)
        w.show_shortcuts(); table = w.shortcuts_window.table
        shown = next(table.item(r, 1).text() for r in range(table.rowCount()) if table.item(r, 0).text() == 'Trace only')
        assert shown == QKeySequence('Ctrl+Alt+T').toString(QKeySequence.SequenceFormat.NativeText), shown
        w.shortcuts_window.close()
        w.show_preferences('Controls'); dialog = w.preferences_dialog; controls = dialog.findChild(q.QWidget, 'controls_tabs').parentWidget()
        editor = controls.shortcut_editor
        # Play takes Ctrl+Alt+T: No keeps both as they were; Yes moves the key and Trace only loses it.
        answers = [False]; editor.select('play'); editor.change()
        assert asked and 'Trace only' in asked[-1] and w.shortcut_bindings.keys('trace_only') == ('Ctrl+Alt+T',)
        answers = [True]; editor.select('play'); editor.change()
        assert w.shortcut_bindings.keys('play') == ('Ctrl+Alt+T',) and w.shortcut_bindings.keys('trace_only') == ()
        assert w.immersive_action.shortcuts() == [] and w.findChildren(q.QAction, '') is not None
        # Export, reset all, import back.
        exported = folder / 'exported.json'; w.pick_shortcuts_file = lambda save: str(exported); editor.export()
        assert json.loads(exported.read_text(encoding='utf-8'))['format'] == 'phosphor-shortcuts'
        answers = [True]; editor.reset_all(); assert not w.shortcut_bindings.overrides and not json.loads(path.read_text(encoding='utf-8'))['bindings']
        assert w.immersive_action.shortcut() == q.default_key_sequences('trace_only')[0]
        editor.import_file(); assert w.shortcut_bindings.keys('play') == ('Ctrl+Alt+T',)
        editor.select('play'); editor.reset(); assert not w.shortcut_bindings.is_custom('play')
        # Reset after a takeover asks again (Play took Save's key; resetting Save takes it back).
        w.capture_shortcut = lambda description, current: w.shortcut_bindings.keys('save')[0]
        answers = [True]; editor.select('play'); editor.change(); taken = w.shortcut_bindings.keys('play')[0]; assert taken not in w.shortcut_bindings.keys('save')
        answers = [True]; editor.select('save'); editor.reset()
        assert not w.shortcut_bindings.is_custom('save') and taken not in w.shortcut_bindings.keys('play')
        answers = [True]; editor.reset_all()
        # Keys a view keeps (Copy) are refused, never taken.
        refused = []; warn = w.warn; w.warn = lambda title, text: refused.append(text)
        w.capture_shortcut = lambda description, current: QKeySequence(QKeySequence.StandardKey.Copy).toString(QKeySequence.SequenceFormat.PortableText)
        editor.select('add_marker'); editor.change(); assert refused and 'Copy' in refused[-1] and not w.shortcut_bindings.is_custom('add_marker')
        # The file changed in another window: nothing is written, the file is read again and the user is told.
        other = dict(format='phosphor-shortcuts', version=1, platform='darwin' if sys.platform == 'darwin' else 'win32', bindings=dict(home=['F7']))
        path.write_text(json.dumps(other), encoding='utf-8')
        w.capture_shortcut = lambda description, current: 'F8'; editor.select('end'); editor.change()
        assert refused and 'changed outside' in refused[-1], refused
        assert w.shortcut_bindings.keys('home') == ('F7',) and not w.shortcut_bindings.is_custom('end')
        assert json.loads(path.read_text(encoding='utf-8')) == other
        editor.select('end'); editor.change(); assert w.shortcut_bindings.keys('end') == ('F8',) and w.shortcut_bindings.keys('home') == ('F7',)
        w.warn = warn
        # A damaged file is reported, the defaults are used and the file is left alone.
        path.write_text('{damaged', encoding='utf-8'); w.init_shortcuts(path)
        assert w.shortcut_error and not w.shortcut_bindings.overrides and path.read_text(encoding='utf-8') == '{damaged'
        editor.refresh(); assert not editor.buttons['change'].isEnabled() and editor.buttons['reset_all'].isEnabled()
        assert 'Reset all' in editor.message.text()
        dialog.reject(); QTest.qWait(50)

        # 4. Wheel, trackpad and pinch on the roll (note roll, Scenes track) and the Builder canvas.
        view = w.composer_panel.timeline; view.scroll, view.scale, view.top_pitch = 4., 40., 68
        w.apply_preferences(dict(w.preferences, wheel_sensitivity=1., trackpad_sensitivity=1.))
        cell = QPoint(cv.GUTTER + 200, view.roll_top + 60)
        wheel(view, cell, angle=QPoint(0, 120)); assert view.top_pitch == 70  # One notch: two rows, as before.
        w.apply_preferences(dict(w.preferences, wheel_sensitivity=.5))
        wheel(view, cell, angle=QPoint(0, 120)); assert view.top_pitch == 71  # Half sensitivity: one row per notch.
        # Two fingers follow the fingers both ways: 3 rows up and 80 px to the right.
        row, top, scroll = view.row, view.top_pitch, view.scroll
        for _ in range(3): wheel(view, cell, pixel=QPoint(40, row), angle=QPoint(120, 120), phase=Qt.ScrollPhase.ScrollUpdate)
        assert view.top_pitch == top + 3 and abs(view.scroll - (scroll - 120 / view.scale)) < 1e-9, (view.top_pitch, view.scroll)
        w.apply_preferences(dict(w.preferences, trackpad_sensitivity=2.))
        scroll = view.scroll; wheel(view, cell, pixel=QPoint(-20, 0), phase=Qt.ScrollPhase.ScrollUpdate)
        assert abs(view.scroll - (scroll + 40 / view.scale)) < 1e-9  # Twice the finger distance.
        w.apply_preferences(dict(w.preferences, trackpad_sensitivity=1.))
        # With the Scroll time modifier (Shift) two fingers up and down move time, not pitch.
        scroll, top = view.scroll, view.top_pitch
        wheel(view, cell, pixel=QPoint(0, -40), phase=Qt.ScrollPhase.ScrollUpdate, modifiers=Qt.KeyboardModifier.ShiftModifier)
        assert view.top_pitch == top and abs(view.scroll - (scroll + 40 / view.scale)) < 1e-9
        # Over the Scenes track the fingers move time the same way.
        track = QPoint(cv.GUTTER + 200, cv.RULER + 4); scroll = view.scroll
        wheel(view, track, pixel=QPoint(-40, 0), phase=Qt.ScrollPhase.ScrollUpdate); assert abs(view.scroll - (scroll + 1)) < 1e-9
        # Pinch zooms time around the pointer; Alt+pinch changes the row height.
        beat, scale = view.beats_at(cell.x()), view.scale
        assert pinch(view, cell, .1) and abs(view.scale - scale * 1.1) < 1e-9 and abs(view.beats_at(cell.x()) - beat) < 1e-9
        row = view.row
        for _ in range(3): pinch(view, cell, .05, alt=True)
        assert view.row == row + 1, (view.row, row)  # 0.15 × PINCH_ROW_STEPS rows.
        # Builder canvas: a pinch and a trackpad scroll zoom about the pointer.
        w.set_workspace('Sandbox'); QTest.qWait(100); w.show_panel(q.PANEL_SCENE)
        next(a for a in w.add_button.menu().actions() if a.text() == 'Custom shape…').trigger(); QTest.qWait(200)
        canvas = w.builder.canvas; zoom = canvas.zoom; QTest.qWait(100)
        at = canvas.mapToScene(QPointF(100, 100)); world = canvas.world([100., 100.])
        assert pinch(w.builder.view, at, .2) and abs(canvas.zoom - zoom * 1.2) < 1e-9, (canvas.zoom, zoom)
        assert abs(canvas.pixel(world) - [100., 100.]).max() < 1e-6  # The point under the fingers stays put.
        # Two fingers pan the canvas with the fingers (times the trackpad sensitivity); the zoom stays.
        def canvas_wheel(pixel, angle, phase):
            canvas.wheelEvent(QWheelEvent(QPointF(100, 100), QPointF(100, 100), pixel, angle, Qt.MouseButton.NoButton,
                                          Qt.KeyboardModifier.NoModifier, phase, False))
        zoom, pan = canvas.zoom, canvas.pan.copy()
        canvas_wheel(QPoint(30, -20), QPoint(90, -60), Qt.ScrollPhase.ScrollUpdate)
        assert canvas.zoom == zoom and abs(canvas.pan - (pan + [30, -20])).max() < 1e-9, (canvas.pan, pan)
        w.apply_preferences(dict(w.preferences, trackpad_sensitivity=2.)); pan = canvas.pan.copy()
        canvas_wheel(QPoint(10, 5), QPoint(30, 15), Qt.ScrollPhase.ScrollUpdate)
        assert abs(canvas.pan - (pan + [20, 10])).max() < 1e-9 and canvas.zoom == zoom
        w.apply_preferences(dict(w.preferences, trackpad_sensitivity=1., wheel_sensitivity=1.))
        # The mouse wheel still zooms about the pointer, exactly as before (one notch: 1.15).
        canvas_wheel(QPoint(), QPoint(0, 120), Qt.ScrollPhase.NoScrollPhase)
        assert abs(canvas.zoom - zoom * 1.15) < 1e-9, (canvas.zoom, zoom)
        w.leave_builder() if hasattr(w, 'leave_builder') else None
    finally:
        w.timer.stop(); w.shutdown_audio(); w.force_close = True; w.close(); app.processEvents()
print('UI2 controls (panel line, tabs, shortcuts, wheel, trackpad and pinch) passed.')
