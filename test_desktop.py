import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import tkinter as tk
import numpy as np
from app import App
from app_files import DEFAULT_PREFERENCES, atomic_json, read_preferences, read_scene, scene_document, validated_preferences
from crt_theme import PALETTES
from signal_engine import Settings, Shape, render


class FileTests(unittest.TestCase):
    def test_scene_roundtrip_and_preferences_are_separate(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            scene = Path(folder) / 'scene.json'
            preferences = Path(folder) / 'preferences.json'
            appearance = dict(DEFAULT_PREFERENCES, palette='Amber', grid_divisions=16)
            atomic_json(preferences, appearance)
            settings = Settings(shapes=(Shape('Octant sphere', 2, -3, 4, None, 13, -45, 90), Shape('Square (2D)')),
                                midi_gain=8, travel_mode='Faster travel', travel_speed=16)
            document = scene_document(settings, 'drawing', 'test input')
            self.assertNotIn('preferences', document)
            atomic_json(scene, document)
            loaded, mode, device = read_scene(scene)
            self.assertEqual(loaded.shapes, settings.shapes)
            self.assertEqual((mode, device), ('drawing', 'test input'))
            self.assertEqual(read_preferences(preferences), appearance)
            np.testing.assert_array_equal(render(np.arange(4096)/48000, settings)[2], render(np.arange(4096)/48000, loaded)[2])
            document['settings']['bpm'] = -1
            atomic_json(scene, document)
            with self.assertRaises(ValueError):
                read_scene(scene)


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = tk.Tk()
        self.app = App(self.root, preferences_path=Path(self.folder.name) / 'preferences.json')
        self.root.update()

    def tearDown(self):
        if self.root.winfo_exists():
            self.app.shutdown_audio()
            self.root.update_idletasks()
            for timer in self.root.tk.call('after', 'info'):
                self.root.after_cancel(timer)
            self.root.destroy()
        self.folder.cleanup()

    def test_slider_defaults_and_dirty_save_open(self):
        app = self.app
        self.assertFalse(app.is_dirty())
        app.sliders['rotation_x'].scale.set(55)
        self.assertTrue(app.is_dirty())
        app.sliders['rotation_x'].reset_default()
        self.assertEqual(app.orientation_controls['x'].get(), '0')
        app.sliders['drawing_gain'].scale.set(.8)
        app.sliders['drawing_gain'].reset_default()
        self.assertAlmostEqual(app.settings().drawing_gain, .55)
        app.controls['rotation_seconds'].set('inf')
        app.sliders['rotation_seconds'].reset_default()
        self.assertEqual(app.settings().rotation_seconds, 5)
        app.add_shape()
        app.orientation_controls['z'].set('36')
        path = Path(self.folder.name) / 'scene.json'
        with patch('desktop_ui.filedialog.asksaveasfilename', return_value=str(path)):
            self.assertTrue(app.save_scene())
        self.assertFalse(app.is_dirty())
        appearance = dict(DEFAULT_PREFERENCES, palette='Cyan', grid_divisions=12)
        app.apply_preferences(appearance)
        app.instances = [Shape()]
        app.selected = 0
        with patch('desktop_ui.filedialog.askopenfilename', return_value=str(path)), patch('desktop_ui.messagebox.askyesnocancel', return_value=False):
            app.open_scene()
        self.assertEqual(len(app.instances), 2)
        self.assertEqual(app.instances[1].rotation_z, 36)
        self.assertEqual(app.preferences, appearance)
        self.assertFalse(app.is_dirty())
        app.select_shape(1)
        self.assertFalse(app.is_dirty())

    def test_preferences_theme_grid_about_and_signal_independence(self):
        app = self.app
        before = app.scene_snapshot()
        samples = render(np.arange(2048)/48000, app.settings())[2]
        appearance = dict(DEFAULT_PREFERENCES, palette='Amber', grid_divisions=9)
        atomic_json(app.preferences_path, appearance)
        app.apply_preferences(read_preferences(app.preferences_path))
        app.tick()
        self.root.update()
        self.assertEqual(app.viewport.background_key[3], 9)
        width, height = app.viewport.background_image.size
        self.assertEqual(app.viewport.background_image.getpixel((round(width/9), 0)), tuple(int(PALETTES['Amber']['grid'][i:i+2], 16) for i in (1, 3, 5)))
        self.assertEqual(app.canvas.itemcget(app.canvas.find_withtag('viewport_trace')[0], 'fill'), PALETTES['Amber']['trace'])
        self.assertEqual(before, app.scene_snapshot())
        np.testing.assert_array_equal(samples, render(np.arange(2048)/48000, app.settings())[2])
        app.show_preferences()
        self.root.update()
        app.preferences_dialog.event_generate('<Escape>')
        app.show_about(instructions=True)
        self.root.update()
        self.assertEqual(app.about_notebook.index('current'), 1)

    def test_crt_palettes_tabs_effects_and_font(self):
        app = self.app
        self.assertEqual([app.control_tabs.tab(tab, 'text') for tab in app.control_tabs.tabs()], ['INPUT', 'SCENE', 'MIX'])
        if __import__('sys').platform == 'win32':
            self.assertEqual(app.font_family, 'JetBrains Mono')
        document = app.scene_snapshot()
        samples = render(np.arange(2048)/48000, app.settings())[2]
        for name in PALETTES:
            app.apply_preferences(dict(DEFAULT_PREFERENCES, palette=name))
            app.tick()
            self.root.update()
            self.assertEqual(app.canvas.cget('background'), PALETTES[name]['background'])
            self.assertEqual(app.canvas.itemcget(app.viewport.glow_id, 'state'), 'normal')
            self.assertTrue(app.canvas.find_withtag('crt_scanline'))
            self.assertEqual(document, app.scene_snapshot())
            np.testing.assert_array_equal(samples, render(np.arange(2048)/48000, app.settings())[2])
        app.apply_preferences(dict(DEFAULT_PREFERENCES, crt_scanlines=False, crt_glow=False))
        app.tick()
        self.assertEqual(app.canvas.itemcget(app.viewport.glow_id, 'state'), 'hidden')
        self.assertFalse(app.canvas.find_withtag('crt_scanline'))
        old = dict(night_mode=True, background='#000000', trace='#65ff9b', grid='#123456', grid_divisions=12)
        self.assertEqual(validated_preferences(old), dict(DEFAULT_PREFERENCES, grid_divisions=12))
        self.assertEqual(validated_preferences(dict(palette='Cyan', grid_divisions=7, crt_effects=False)),
                         dict(DEFAULT_PREFERENCES,palette='Cyan', grid_divisions=7, crt_scanlines=False, crt_glow=False))

    def test_retained_drawing_and_event_driven_settings(self):
        app = self.app
        for timer in self.root.tk.call('after', 'info'):
            self.root.after_cancel(timer)
        with patch.object(self.root, 'after', return_value=None):
            app.tick()
            initial_items = set(app.canvas.find_all())
            background_builds = app.viewport.background_builds
            snapshot = app.settings()
            with patch.object(app, 'scene_snapshot', wraps=app.scene_snapshot) as scene_check:
                for _ in range(5):
                    app.tick()
                self.assertEqual(scene_check.call_count, 0)
            self.assertIs(app.settings(), snapshot)
            self.assertEqual(app.viewport.background_builds, background_builds)
            self.assertEqual(set(app.canvas.find_all()), initial_items)
            app.controls['drawing_gain'].set('.4')
            app.tick()
            self.assertIsNot(app.settings(), snapshot)
            self.assertEqual(app.settings().drawing_gain, .4)

    def test_playback_reuses_audio_samples_without_resynthesis(self):
        app = self.app
        settings = app.settings()
        app.audio.thread = Mock()
        app.audio.thread.is_alive.return_value = True
        app.audio.parameters = (settings, app.mode.get())
        signal = np.full((2048, 2), .125, dtype='float32')
        app.audio.history.append(signal)
        try:
            with patch('app.render', side_effect=AssertionError('GUI must not resynthesize playing audio')):
                app.tick()
            coordinates = np.array(app.canvas.coords(app.viewport.trace_id)).reshape(-1, 2)
            self.assertEqual(len(coordinates), round(.04*settings.sample_rate))
            self.assertAlmostEqual(coordinates[0, 0], app.canvas.winfo_width()/2 + .125*.45*min(app.canvas.winfo_width(), app.canvas.winfo_height()))
        finally:
            app.audio.thread = None

    def test_new_instance_cancel_and_cancelled_save_keep_window(self):
        app = self.app
        with patch('desktop_ui.messagebox.askyesnocancel', return_value=None), patch('desktop_ui.subprocess.Popen') as launch:
            app.new_instance()
            launch.assert_not_called()
        with patch('desktop_ui.messagebox.askyesnocancel', return_value=True), patch('desktop_ui.filedialog.asksaveasfilename', return_value=''), patch('desktop_ui.subprocess.Popen') as launch:
            app.new_instance()
            launch.assert_not_called()
        self.assertTrue(self.root.winfo_exists())

    def test_new_instance_waits_for_ready_before_closing_old(self):
        app = self.app
        process = Mock()
        process.poll.return_value = None
        with patch('desktop_ui.messagebox.askyesnocancel', return_value=False), patch('desktop_ui.subprocess.Popen', return_value=process) as launch:
            app.new_instance()
        self.assertTrue(self.root.winfo_exists())
        self.assertIsNotNone(app.launch_pending)
        args = launch.call_args.args[0]
        self.assertIn('--ready-file', args)
        ready = Path(args[-1])
        ready.write_text('ready')
        with patch.object(self.root, 'destroy') as destroy:
            app.poll_new_instance()
            destroy.assert_called_once()
        self.assertFalse(ready.exists())

    def test_launch_failure_and_failed_save_keep_scene(self):
        app = self.app
        original = app.scene_snapshot()
        with patch('desktop_ui.messagebox.askyesnocancel', return_value=False), patch('desktop_ui.subprocess.Popen', side_effect=OSError('cannot launch')), patch('desktop_ui.messagebox.showerror') as error:
            app.new_instance()
            error.assert_called_once()
        self.assertEqual(original, app.scene_snapshot())
        self.assertTrue(self.root.winfo_exists())
        with patch('desktop_ui.messagebox.askyesnocancel', return_value=True), patch.object(app, 'save_scene', return_value=False), patch('desktop_ui.subprocess.Popen') as launch:
            app.new_instance()
            launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
