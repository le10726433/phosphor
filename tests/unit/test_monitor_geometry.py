"""Qt-free monitor geometry contracts (no QApplication or PySide6 import)."""
import unittest

from phosphor.editor.monitor_window import fit_geometry, pick_fullscreen_screen


class MonitorGeometryTests(unittest.TestCase):
    def test_keeps_usable_geometry(self):
        screens = [(0, 0, 1920, 1080)]
        for rect in ((100, 50, 800, 600), (-736, 0, 800, 600),
                     (1856, 1048, 800, 600)):
            with self.subTest(rect=rect):
                self.assertEqual(fit_geometry(rect, screens), rect)

    def test_rescues_unplugged_display(self):
        self.assertEqual(fit_geometry((2400, 100, 800, 600), [(0, 0, 1920, 1080)]),
                         (560, 240, 800, 600))

    def test_title_visibility_threshold(self):
        screens = [(0, 0, 1920, 1080)]
        for rect in ((1857, 0, 800, 600), (0, -1, 800, 600),
                     (0, 1049, 800, 600)):
            self.assertEqual(fit_geometry(rect, screens), (560, 240, 800, 600))

    def test_size_bounds(self):
        screens = [(0, 0, 1000, 800)]
        self.assertEqual(fit_geometry((0, 0, 2000, 1600), screens),
                         (20, 20, 960, 760))
        self.assertEqual(fit_geometry((10, 10, 100, 100), screens),
                         (380, 280, 240, 240))
        self.assertEqual(fit_geometry((0, 0, 400, 400), [(0, 0, 200, 200)]),
                         (-20, -20, 240, 240))

    def test_negative_display_coordinates(self):
        screens = [(0, 0, 1920, 1080), (-1600, -200, 1600, 1000)]
        rect = (-1400, -100, 800, 600)
        self.assertEqual(fit_geometry(rect, screens), rect)
        self.assertEqual(fit_geometry((3000, 0, 800, 600), screens[::-1]),
                         (-1200, 0, 800, 600))

    def test_screen_selection_respects_monitor_position(self):
        screens = [(-1200, 0, 1000, 800), (0, 0, 1000, 800)]
        self.assertEqual(pick_fullscreen_screen((100, 100), (100, 100), screens), 1)
        self.assertEqual(pick_fullscreen_screen((-500, 100), (100, 100), screens), 0)
        self.assertEqual(pick_fullscreen_screen((100, 100), (100, 100), screens[1:]), 0)
        self.assertEqual(pick_fullscreen_screen((-150, 100), (100, 100), screens), 0)
        self.assertEqual(pick_fullscreen_screen((-50, 100), (100, 100), screens), 1)
        self.assertEqual(pick_fullscreen_screen((-100, 100), (100, 100), screens), 0)
        self.assertEqual(pick_fullscreen_screen((1000, 900), (0, 0), screens), 1)

    def test_invalid_inputs(self):
        screen = [(0, 0, 1920, 1080)]
        for rect in (None, (), (0, 0, 1), (0, 0, 0, 240), (0, 0, -1, 240),
                     (float("nan"), 0, 240, 240), (0, 0, float("inf"), 240),
                     ("0", 0, 240, 240), (True, 0, 240, 240)):
            with self.subTest(rect=rect), self.assertRaises(ValueError):
                fit_geometry(rect, screen)
        for screens in (None, [], [(0, 0, -1, 800)], [(0, 0, 800)],
                        [(0, float("inf"), 800, 600)]):
            with self.subTest(screens=screens):
                with self.assertRaises(ValueError):
                    fit_geometry((0, 0, 300, 300), screens)
                with self.assertRaises(ValueError):
                    pick_fullscreen_screen((0, 0), (0, 0), screens)
        for point in (None, (), (0,), (float("nan"), 0), (0, float("inf"))):
            with self.assertRaises(ValueError):
                pick_fullscreen_screen(point, (0, 0), screen)
            with self.assertRaises(ValueError):
                pick_fullscreen_screen((0, 0), point, screen)


if __name__ == "__main__":
    unittest.main()
