"""Unit tests for P2 build version info, Scene.move reordering, and new shortcuts."""
import unittest
from phosphor.editor.qt_scene import Scene
from phosphor.editor.shortcuts import BY_ACTION, MENU_GROUPS
from phosphor.storage import build_info, shortcut_bindings


class BuildInfoTests(unittest.TestCase):
    def test_version_and_date_updated(self):
        self.assertRegex(build_info.VERSION, r'^0\.9[2-9]\.\d+(-dev)?$')
        self.assertEqual(build_info.BUILD_DATE, 'November 25, 2026')


class SceneMoveTests(unittest.TestCase):
    def test_move_reorders_shapes_and_tracks_selection(self):
        scene = Scene()
        scene.add('Cube')
        scene.add('Octahedron')
        scene.add('Tetrahedron')
        self.assertEqual([s.solid for s in scene.settings.shapes], ['Cube', 'Octahedron', 'Tetrahedron'])
        self.assertEqual(scene.selected, 2)

        # Move selected item (2 -> 0)
        self.assertTrue(scene.move(2, 0))
        self.assertEqual([s.solid for s in scene.settings.shapes], ['Tetrahedron', 'Cube', 'Octahedron'])
        self.assertEqual(scene.selected, 0)

        # Move unselected item past selected (2 -> 0 shifts selected 0 -> 1)
        self.assertTrue(scene.move(2, 0))
        self.assertEqual([s.solid for s in scene.settings.shapes], ['Octahedron', 'Tetrahedron', 'Cube'])
        self.assertEqual(scene.selected, 1)

        # No-op / out of range
        self.assertFalse(scene.move(1, 1))
        self.assertFalse(scene.move(-1, 0))
        self.assertFalse(scene.move(0, 99))


class NewShortcutsTests(unittest.TestCase):
    def test_new_window_shortcuts_registered_and_conflict_free(self):
        expected = (
            'zoom_selection', 'toggle_library', 'panel_input', 'panel_scene',
            'panel_mix', 'panel_appearance', 'panel_signal', 'switch_workspace',
        )
        view_actions = dict(MENU_GROUPS)['View']
        for action in expected:
            self.assertIn(action, BY_ACTION)
            self.assertIn(action, shortcut_bindings.SCOPES)
            self.assertIn(action, view_actions)
        for platform in shortcut_bindings.PLATFORMS:
            bindings = shortcut_bindings.ShortcutBindings(platform=platform)
            self.assertEqual(bindings.has_conflicts(), ())


if __name__ == '__main__':
    unittest.main()
