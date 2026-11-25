"""A Python error in a guarded paintEvent must not leave its QPainter active: Qt would
then flush a backing store with an active painter, which segfaults on macOS."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QWidget

from phosphor.editor import paint_guard
from phosphor.editor.paint_guard import guarded_paint


class Faulty(QWidget):
    painted = 0

    @guarded_paint
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), 0)
        Faulty.painted += 1
        {}[7]  # The KeyError that crashed 0.65.0.


class PaintGuardTests(unittest.TestCase):
    def test_shared_smoke_exit_reports_guarded_traceback(self):
        script = """import _path
from phosphor.editor.paint_guard import guarded_paint
@guarded_paint
def paint(widget, event):
    raise ValueError('injected smoke fault')
paint(object(), None)
"""
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                                cwd=Path(__file__).resolve().parents[1] / 'smoke')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Traceback (most recent call last)', result.stderr)
        self.assertIn('ValueError: injected smoke fault', result.stderr)
        self.assertIn('Smoke failed after a guarded paint exception', result.stderr)

    def test_error_ends_the_painter_and_is_reported_once(self):
        app = QApplication.instance() or QApplication([])
        messages = []
        previous = qInstallMessageHandler(lambda kind, context, text: messages.append(text))
        paint_guard._reported.clear()
        widget = Faulty()
        try:
            widget.resize(120, 80); widget.show()
            for _ in range(5):
                widget.repaint(); app.processEvents()
        finally:
            widget.close(); app.processEvents()
            qInstallMessageHandler(previous)
        self.assertGreaterEqual(Faulty.painted, 5)
        self.assertFalse([m for m in messages if 'active painter' in m or 'one painter at a time' in m], messages)
        self.assertEqual(len(paint_guard._reported), 1)


if __name__ == '__main__':
    unittest.main()
