"""Undo and Redo keys in text fields that have nothing of their own to undo (U1, 0.78.0-dev).

A focused line edit, spin box or text edit always claims Ctrl+Z / Ctrl+Shift+Z (Cmd on macOS)
through ShortcutOverride, so the window's Undo never ran while a field kept focus after a
wheel, arrow or typed edit. TextUndoKeys leaves the key to the field while it can undo or redo
its own typing and passes it to the window's Undo/Redo actions otherwise. Edit › Undo uses
text_history for the same choice.
"""
from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QAbstractSpinBox, QLineEdit, QPlainTextEdit, QTextEdit


def text_target(widget):
    """The editable text widget behind widget (a spin box's line edit), or None."""
    if isinstance(widget, QAbstractSpinBox): widget = widget.lineEdit()
    if isinstance(widget, QLineEdit): return None if widget.isReadOnly() else widget
    if isinstance(widget, (QTextEdit, QPlainTextEdit)): return None if widget.isReadOnly() else widget
    return None


def text_history(widget, redo):
    """The text widget that should undo (redo) its own typing, or None for the app's history."""
    target = text_target(widget)
    if target is None: return None
    if isinstance(target, QLineEdit): available = target.isRedoAvailable() if redo else target.isUndoAvailable()
    else:
        document = target.document()
        available = document.isRedoAvailable() if redo else document.isUndoAvailable()
    return target if available else None


class TextUndoKeys(QObject):
    """Installed on each text field as it gains focus (Window.watch_text_undo)."""
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            for redo, key in ((False, QKeySequence.StandardKey.Undo), (True, QKeySequence.StandardKey.Redo)):
                if event.matches(key) and text_target(watched) is not None and text_history(watched, redo) is None:
                    return True  # Not accepted: the window's shortcut runs instead.
        return False
