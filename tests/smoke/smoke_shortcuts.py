import _path  # noqa: F401
import os
import sys
if '--headless' in sys.argv:
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from phosphor.editor import qt_app as q
from phosphor.editor.shortcuts import BY_ACTION, SHORTCUTS, filter_shortcuts, key_sequences, format_keys
from phosphor.engine.composition import SANDBOX


q.configure_graphics()
app = QApplication([])
with patch.object(q, 'write_workspace', lambda *args: None):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=SANDBOX))
    try:
        w.show(); app.processEvents(); w.timer.stop()
        checked = set()
        for action in w.findChildren(QAction):
            if not any(not key.isEmpty() for key in action.shortcuts()):
                continue
            action_id = action.property('shortcut_id')
            assert action_id in BY_ACTION, (action.text(), action.shortcuts())
            assert action.shortcuts() == key_sequences(action_id), action.text()
            checked.add(action_id)
        # Every general entry is bound; window actions for a context (S splits in the
        # Composer timeline) are in the registry under that context.
        assert {entry.action for entry in SHORTCUTS if entry.where == 'general'} <= checked
        menus = [action.menu() for action in w.menuBar().actions() if action.menu()]
        assert menus[-1].title() == 'Help'
        help_menu = menus[-1]
        assert any(action.text() == 'Manual' for action in help_menu.actions())
        about_menu = next(menu for menu in menus if menu.title() == 'About')
        assert not any(action.text() == 'Manual' for action in about_menu.actions())
        about = next(action for action in about_menu.actions() if action.text() == 'About phosphor')
        assert about.menuRole() == QAction.MenuRole.AboutRole
        action = next(action for action in help_menu.actions() if action.text() == 'Keyboard shortcuts…')
        action.trigger(); app.processEvents()
        dialog = w.shortcuts_window
        assert dialog.isVisible() and not dialog.isModal()
        assert dialog.windowModality() == Qt.WindowModality.NonModal
        assert app.activeModalWidget() is None
        assert dialog.table.rowCount() == len(SHORTCUTS)
        QTest.keyClicks(dialog.search, 'box notes'); app.processEvents()
        expected = filter_shortcuts('box notes')
        assert 0 < dialog.table.rowCount() == len(expected) < len(SHORTCUTS)
        for row, entry in enumerate(expected):
            assert [dialog.table.item(row, col).text() for col in range(3)] == [
                entry.description, format_keys(entry), entry.where]
        dialog.search.selectAll(); QTest.keyClicks(dialog.search, 'no-such-shortcut')
        assert dialog.table.rowCount() == 0
        dialog.search.clear(); assert dialog.table.rowCount() == len(SHORTCUTS)
        dialog.close(); action.trigger(); app.processEvents()
        assert w.shortcuts_window is dialog and dialog.isVisible()
    finally:
        w.history_timer.stop(); w.timer.stop(); w.force_close = True
        w.shutdown_audio(); w.release_views(); w.close()
print('Shortcuts passed: QAction registry bindings, Help menu, non-modal reference and search.')
