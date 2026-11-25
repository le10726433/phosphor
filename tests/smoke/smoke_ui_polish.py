"""UI1 polish in the Sandbox, native or --headless: viewport handles auto-hide (timing, picking,
drags), XYZ guides at half opacity, ✕ Close inspector and Esc, and the manual in its own
non-modal window beside an interactive main window (chapter buttons, one instance, place kept)."""
import _path  # noqa: F401
import math
import os
import sys
import time
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from _screen import exit_cleanly_on_failure
from phosphor.editor import qt_app as q
from phosphor.editor.manual_view import ManualWindow

exit_cleanly_on_failure()
q.configure_graphics()
app = QApplication([])


def handle_layer():
    w.overlay(.25); return w.host.scope.overlays[-1]  # No inspector: the handles are the top layer.


with patch.object(q, 'write_workspace', lambda *args: None):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
    try:
        w.resize(1220, 840); w.show(); w.activateWindow(); QTest.qWait(300)
        w.add_object('Tilting circle'); w.add_object('Cube'); QTest.qWait(50)

        # 1. Handles: hidden until the pointer moves over the scope; picking works while hidden.
        w.handles_seen = -math.inf
        points, colour = handle_layer()
        assert len(points) == 0 and len(w.handles) == 2, (len(points), w.handles)
        _, centre, corner = next(h for h in w.handles if h[0] == w.scene.selected)
        QTest.mouseMove(w.host.view, QPoint(round(centre[0]) + 40, round(centre[1]) + 30)); QTest.qWait(60)
        assert time.perf_counter() - w.handles_seen < 1., 'hovering the scope shows the handles'
        points, colour = handle_layer()
        assert len(points) and abs(colour.alphaF() - q.HANDLE_DIM) < .01, colour.alphaF()  # Dimmed accent.
        accent = q.QColor(q.PALETTES[w.preferences['palette']]['accent'])
        assert colour.rgb() == accent.rgb() and w.host.scope.overlays[0][1].alphaF() == 1.  # Guides keep full accent.
        # The selection box is dashed: more pieces than a solid box (5 points) would make.
        solid = len(q.stroke_triangles(centre + [[-9, -9], [9, -9], [9, 9], [-9, 9], [-9, -9]], .6))
        assert len(points) > 2 * solid, (len(points), solid)
        # Timing: full for HANDLE_HOLD seconds after the last move, then a fade, then hidden.
        now = time.perf_counter()
        assert w.handle_opacity(w.handles_seen + q.HANDLE_HOLD - .05) == 1.
        middle = w.handle_opacity(w.handles_seen + q.HANDLE_HOLD + q.HANDLE_FADE / 2)
        assert .3 < middle < .7, middle
        assert w.handle_opacity(w.handles_seen + q.HANDLE_HOLD + q.HANDLE_FADE + .01) == 0.
        w.handles_seen = now - q.HANDLE_HOLD - q.HANDLE_FADE / 2
        points, colour = handle_layer()
        assert len(points) and 0 < colour.alphaF() < q.HANDLE_DIM, colour.alphaF()  # Fading, not vanished.
        # Real time on the live display: gone about two seconds after the pointer stopped.
        w.timer.start(); w.reveal_handles(); QTest.qWait(400)
        assert len(w.host.scope.overlays[-1][0]), 'shown while the pointer has just moved'
        QTest.qWait(round(1000 * (q.HANDLE_HOLD + q.HANDLE_FADE)) + 400)
        assert len(w.host.scope.overlays[-1][0]) == 0, 'hidden after the pointer stopped'
        w.timer.stop()
        # A press on a hidden handle still grabs it, and the handles show for the whole drag.
        w.handles_seen = -math.inf; handle_layer()
        x0 = w.scene.shape.position_x
        assert w.pointer('press', QPointF(*centre)) and w.drag is not None
        w.handles_seen = -math.inf  # Even with no pointer motion recorded, a drag keeps them shown.
        assert w.handle_opacity() == 1. and len(handle_layer()[0])
        w.pointer('move', QPointF(centre[0] + 60, centre[1])); w.pointer('release', QPointF(centre[0] + 60, centre[1]))
        assert w.scene.shape.position_x > x0 and w.drag is None
        # Selecting an object in the list shows the handles briefly, so you see where it is.
        w.handles_seen = -math.inf; w.select_shape(0); assert w.handle_opacity() == 1.

        # 5. XYZ guides: about half their old opacity.
        w.overlay(.25)
        axes = w.host.scope.overlays[-4:-1]
        assert [round(c.alphaF(), 2) for _, c in axes] == [q.AXIS_OPACITY] * 3, [c.alphaF() for _, c in axes]

        # 2. INSPECT CYCLE: ✕ Close inspector and Esc turn it off and go back to the previous panel.
        w.show_panel(q.PANEL_MIX); w.inspect_cycle(True)
        assert w.current_panel == q.PANEL_INSPECT and w.inspector.active and w.inspect_action.isChecked()
        QTest.mouseClick(w.inspector.close_button, Qt.MouseButton.LeftButton)
        assert w.current_panel == q.PANEL_MIX and not w.inspector.active and not w.inspect_action.isChecked()
        w.show_panel(q.PANEL_APPEARANCE); w.inspect_action.trigger(); QTest.qWait(30)
        assert w.current_panel == q.PANEL_INSPECT and w.inspector.active
        w.activateWindow(); QTest.qWaitForWindowActive(w); w.inspector.slider.setFocus(); QTest.qWait(30)
        QTest.keyClick(w.inspector.slider, Qt.Key.Key_Escape); QTest.qWait(30)
        assert w.current_panel == q.PANEL_APPEARANCE and not w.inspector.active and not w.inspect_action.isChecked()
        # Opened with the side panel hidden: Close returns to Objects. Esc outside the panel leaves it alone.
        w.show_panel(q.PANEL_MIX); w.hide_panel(); w.inspect_cycle(True); QTest.qWait(30)
        w.host.view.requestActivate(); QTest.qWait(30)
        QTest.keyClick(w.host.view, Qt.Key.Key_Escape); QTest.qWait(30)
        assert w.current_panel == q.PANEL_INSPECT and w.inspector.active, 'Esc outside the panel does not close it'
        w.inspector.close_button.click()
        assert w.current_panel == q.PANEL_SCENE and w.scene_sections.currentIndex() == 0 and w.sidebar.isVisible()
        # View › Inspect one cycle stays a toggle and follows.
        w.show_panel(q.PANEL_MIX); w.inspect_action.trigger(); w.inspect_action.trigger()
        assert w.current_panel == q.PANEL_MIX and not w.inspector.active

        # 4. The manual: its own non-modal window owned by the main window; the main window keeps working.
        w.preferences['manual_geometry'] = []
        manual_action = next(a for a in w.findChildren(q.QAction) if a.text() == 'Manual')
        manual_action.trigger(); QTest.qWait(200)
        window = w.manual_window
        assert isinstance(window, ManualWindow) and window.isVisible() and window.parent() is w
        assert window.windowFlags() & Qt.WindowType.Tool == Qt.WindowType.Tool and not window.isModal()
        assert QApplication.activeModalWidget() is None
        assert window.windowHandle().transientParent() is w.windowHandle()  # Stays on top of its owner.
        view = window.view
        QTest.mouseClick(w.bar_buttons[q.PANEL_SIGNAL], Qt.MouseButton.LeftButton)
        assert w.current_panel == q.PANEL_SIGNAL and window.isVisible(), 'the main window takes clicks beside the manual'
        # Chapters: ◀ Previous chapter, the title, Next chapter ▶.
        assert view.chapter_title.text() == 'Contents' and not view.previous_chapter.isEnabled() and view.next_chapter.isEnabled()
        view.next_chapter.click()
        assert view.browser.source().fileName() == '01-introduction.md' and view.chapter_title.text() == '1. Introduction'
        view.next_chapter.click()
        assert view.browser.source().fileName().startswith('02-'), view.browser.source().fileName()
        view.previous_chapter.click(); assert view.browser.source().fileName() == '01-introduction.md'
        view.previous_chapter.click(); assert view.browser.source().fileName() == 'README.md'
        for _ in range(len(view.chapters)): view.next_chapter.click()
        assert view.browser.source().fileName() == view.chapters[-1][0].name and not view.next_chapter.isEnabled()
        assert view.previous_chapter.text() == '◀ Previous chapter' and view.next_chapter.text() == 'Next chapter ▶'
        # Enter and Shift+Enter in the search field still step search results, not chapters.
        page = view.browser.source()
        QTest.keyClicks(view.search_field, 'phosphor'); QTest.keyClick(view.search_field, Qt.Key.Key_Return)
        first = view.browser.textCursor().selectionStart()
        QTest.keyClick(view.search_field, Qt.Key.Key_Return)
        QTest.keyClick(view.search_field, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        assert view.browser.source() == page and view.browser.textCursor().selectionStart() == first
        assert 'Shift+Enter' in view.search_field.placeholderText()
        view.search_field.clear()
        # One instance: opening it again raises the same window.
        manual_action.trigger(); QTest.qWait(50)
        assert w.manual_window is window and len(w.findChildren(ManualWindow)) == 1
        # Its place is remembered (preferences), for this session's window and the next one.
        area = window.screen().availableGeometry()
        place = (area.x() + 60, area.y() + 80, 640, 480)
        window.setGeometry(*place); QTest.qWait(700)
        assert w.preferences['manual_geometry'] == list(window.placed()), (w.preferences['manual_geometry'], window.placed())
        saved = w.preferences['manual_geometry']
        window.close(); QTest.qWait(50); assert not window.isVisible()
        manual_action.trigger(); QTest.qWait(100)
        assert w.manual_window is window and window.isVisible() and list(window.placed()[2:]) == saved[2:]
        window.close(); w.manual_window = None; window.deleteLater(); QTest.qWait(50)
        reopened = w.open_manual(); QTest.qWait(150)
        assert reopened is not window and list(reopened.placed()[2:]) == saved[2:], (reopened.placed(), saved)
        reopened.close()
        # About and Audio troubleshooting stay in the dialog, without the manual.
        shown = []
        w.run_dialog = lambda dialog: shown.append([dialog.findChild(q.QTabWidget).tabText(i) for i in range(dialog.findChild(q.QTabWidget).count())])
        w.about(); assert shown == [['About', 'Audio troubleshooting']], shown
    finally:
        w.timer.stop(); w.shutdown_audio(); w.force_close = True; w.close(); app.processEvents()
print('UI1 polish (handles, guides, inspector close, manual window) passed.')
