from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import tkinter as tk
import numpy as np
from phosphor.storage.app_files import DEFAULT_PREFERENCES
from phosphor.display.crt_theme import PALETTES
from phosphor.legacy_tk.viewport import Viewport
from phosphor.audio.live_midi import LiveMidi


class ViewportTests(unittest.TestCase):
    def test_background_cache_glow_cadence_and_full_resolution_trace(self):
        root = tk.Tk()
        canvas = tk.Canvas(root)
        canvas.pack()
        viewport = Viewport(canvas)
        signal = np.column_stack((np.linspace(-1, 1, 1920), np.linspace(1, -1, 1920)))
        try:
            viewport.draw(signal, 640, 480, PALETTES['Green monochrome'], DEFAULT_PREFERENCES, now=1)
            count = len(canvas.find_all())
            self.assertEqual(len(canvas.coords(viewport.trace_id)), signal.size)
            viewport.draw(signal*.5, 640, 480, PALETTES['Green monochrome'], DEFAULT_PREFERENCES, now=1.01)
            self.assertEqual(viewport.background_builds, 1)
            self.assertEqual(viewport.glow_builds, 1)
            viewport.draw(signal*.3, 640, 480, PALETTES['Green monochrome'], DEFAULT_PREFERENCES, now=1.1)
            self.assertEqual(viewport.glow_builds, 2)
            self.assertEqual(len(canvas.find_all()), count)
            viewport.draw(signal, 800, 600, PALETTES['Green monochrome'], DEFAULT_PREFERENCES, now=1.11)
            self.assertEqual(viewport.background_builds, 2)
            preferences = dict(DEFAULT_PREFERENCES, crt_scanlines=False, crt_glow=False)
            viewport.draw(signal, 800, 600, PALETTES['Green monochrome'], preferences, now=1.12)
            self.assertEqual(viewport.background_builds, 3)
            self.assertEqual(canvas.itemcget(viewport.glow_id, 'state'), 'hidden')
            self.assertFalse(canvas.find_withtag('crt_scanline'))
        finally:
            root.update_idletasks()
            root.destroy()

    def test_midi_device_check_does_not_wait_on_ui_thread(self):
        runtime = LiveMidi()
        runtime.port_name = 'Test MIDI'
        started, release = threading.Event(), threading.Event()
        main_thread = threading.get_ident()
        checked_on = []

        def delayed_names():
            checked_on.append(threading.get_ident())
            started.set()
            release.wait(timeout=2)
            return []

        with patch.object(runtime, 'names', side_effect=delayed_names):
            runtime.check_port_async()
            self.assertTrue(started.wait(timeout=1))
            self.assertIsNone(runtime.poll_port_check())
            release.set()
            runtime.probe_thread.join(timeout=1)
        self.assertNotEqual(checked_on[0], main_thread)
        result = runtime.poll_port_check()
        self.assertEqual(result[0], 'Test MIDI')
        self.assertIn('disappeared', result[1])


if __name__ == '__main__':
    unittest.main()
