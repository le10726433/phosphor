"""Slider drags commit once per display frame; equation text checks are cached (0.58.2)."""
import os
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from phosphor.editor import qt_app
from phosphor.editor.qt_app import SliderInput, flush_sliders
from phosphor.engine.function_plot import parse_expression


class SliderDragTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.slider = SliderInput(.5); self.edits = []
        self.slider.edited.connect(self.edits.append)
        self.addCleanup(qt_app._pending_sliders.clear)

    def test_moves_without_a_drag_commit_at_once(self):
        self.slider.slider.setValue(2500)
        self.assertEqual(self.edits, [.25])

    def test_a_drag_commits_only_its_latest_value_per_flush(self):
        self.slider.slider.setSliderDown(True)
        for position in (1000, 2000, 3000): self.slider.slider.setValue(position)
        self.assertEqual(self.edits, [])
        flush_sliders(); flush_sliders()
        self.assertEqual(self.edits, [.3])
        self.slider.slider.setValue(4000)
        self.slider.slider.setSliderDown(False)  # Release commits the waiting value.
        self.assertEqual(self.edits, [.3, .4])
        flush_sliders(); self.assertEqual(self.edits, [.3, .4])

    def test_a_value_set_from_outside_replaces_a_waiting_drag_value(self):
        self.slider.slider.setSliderDown(True); self.slider.slider.setValue(1000)
        self.slider.setValue(.7); flush_sliders()
        self.assertEqual(self.edits, []); self.assertEqual(self.slider.value, .7)

    def test_the_fallback_timer_commits_when_no_frame_is_drawn(self):
        self.slider.slider.setSliderDown(True); self.slider.slider.setValue(6000)
        self.assertTrue(qt_app._slider_timer.isActive())
        qt_app._slider_timer.timeout.emit()
        self.assertEqual(self.edits, [.6])


class ExpressionCacheTests(unittest.TestCase):
    def test_same_text_reuses_the_code_and_errors_still_raise(self):
        self.assertIs(parse_expression('a*sin(tau*s)', ['a'], 'x'), parse_expression('a*sin(tau*s)', ('a',), 'x'))
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'unknown name'): parse_expression('b*s', ['a'], 'x')
        with self.assertRaises(ValueError): parse_expression(None)


if __name__ == '__main__':
    unittest.main()
