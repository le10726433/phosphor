"""Arrow events carrying macOS Qt's keypad modifier still edit selected notes."""
import os
import unittest

from phosphor.engine.composition import Note, Project


class NoteRollKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_keypad_arrows_transpose_and_shift_moves_octaves(self):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        from phosphor.editor.composer_view import TimelineView

        view = TimelineView()
        edits = []
        view.edited.connect(lambda project, clip: edits.append(project))
        try:
            for extra in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier):
                for shift, distance in ((Qt.KeyboardModifier.NoModifier, 1),
                                        (Qt.KeyboardModifier.ShiftModifier, 12)):
                    for key, direction in ((Qt.Key.Key_Up, 1), (Qt.Key.Key_Down, -1)):
                        with self.subTest(extra=extra, shift=shift, key=key):
                            view.set_project(Project(notes=(Note(0., 1., 60), Note(0., 1., 67))), 0)
                            view.note_selection = {0}
                            edits.clear()
                            event = QKeyEvent(QEvent.Type.KeyPress, key, extra | shift)
                            self.app.sendEvent(view, event)
                            self.assertEqual([n.pitch for n in view.project.notes], [60 + direction * distance, 67])
                            self.assertEqual(len(edits), 1)
                            self.assertEqual(view.note_selection, {0})
        finally:
            view.close()
            view.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
