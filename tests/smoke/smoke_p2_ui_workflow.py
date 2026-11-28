"""Smoke test: P2 UI usability and workflow improvements."""
import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from phosphor.editor.qt_platform import configure_graphics
configure_graphics()

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from phosphor.editor.qt_app import Window, PANEL_MIX, PANEL_SCENE
from phosphor.engine.composition import COMPOSER, SANDBOX


def pump(app, n=10):
    for _ in range(n):
        app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scale', type=float, default=1.0)
    parser.parse_args()

    app = QApplication.instance() or QApplication([])
    opts = SimpleNamespace(scene=None, workspace=COMPOSER, ready_file=None, skip_recovery=True)
    window = Window(opts)
    try:
        window.show()
        pump(app, 15)

        # 1. Objects list grows dynamically (3..7 rows) and reorders cleanly with single-step Undo.
        window.add_object('Cube')
        window.add_object('Octahedron')
        window.add_object('Tetrahedron')
        window.add_object('Dodecahedron')
        pump(app, 5)
        assert window.shape_list.height() == 4 * 26 + 6, f'Unexpected shape_list height: {window.shape_list.height()}'
        assert [s.solid for s in window.scene.settings.shapes] == ['Cube', 'Octahedron', 'Tetrahedron', 'Dodecahedron']
        assert window.reorder_objects(3, 0)
        assert [s.solid for s in window.scene.settings.shapes] == ['Dodecahedron', 'Cube', 'Octahedron', 'Tetrahedron']
        window.undo()
        assert [s.solid for s in window.scene.settings.shapes] == ['Cube', 'Octahedron', 'Tetrahedron', 'Dodecahedron']

        # 2. Transform list grows dynamically and reorders cleanly.
        tp = window.transform_panel
        for kind in ('Rotate', 'Scale', 'Translate', 'Shear'):
            tp.add(kind)
        pump(app, 5)
        assert tp.list.height() == 4 * 26 + 6, f'Unexpected transform_list height: {tp.list.height()}'
        tp._rows_moved(3, 0)
        pump(app, 10)
        assert [t.kind for t in window.scene.shape.transforms] == ['Shear', 'Rotate', 'Scale', 'Translate']

        # 3. Solo pill shows when solo is active and clears all solos on click.
        assert not window.solo_pill.isVisible()
        window.toggle_objects('solo', 0)
        pump(app, 5)
        assert window.solo_pill.isVisible(), 'Solo pill did not appear when object soloed'
        window.solo_pill.click()
        pump(app, 5)
        assert not window.solo_pill.isVisible() and not window.any_solo(), 'Solo pill click did not clear solos'

        # 4. flash() routes to QStatusBar without prepending into top telemetry self.status.
        window.flash('P2 test notice')
        assert window.statusBar().currentMessage() == 'P2 test notice'
        assert window.notice and window.notice[0] == 'P2 test notice'
        window.last_status = 0
        window.housekeeping(display=False)
        assert 'P2 test notice' not in window.status.text(), f'flash leaked into top status: {window.status.text()}'

        # 5. Composer Record MIDI input submenu populates cleanly.
        window.rebuild_record_midi_menu()
        actions = [a.text() for a in window.composer_panel.record_midi_menu.actions() if a.text()]
        assert 'None' in actions and 'Refresh MIDI devices' in actions, f'Unexpected MIDI menu actions: {actions}'

        # 6. New window shortcut actions exist and switch panels/workspace cleanly.
        window.toggle_panel(PANEL_MIX)
        assert window.current_panel == PANEL_MIX
        window.set_workspace(SANDBOX)
        assert window.workspace == SANDBOX
        window.set_workspace(COMPOSER)
        assert window.workspace == COMPOSER

        # 7. Shape Builder single-key tool shortcuts switch tools when canvas has focus.
        window.open_builder(new=True)
        pump(app, 10)
        b = window.builder
        for key, expected in ((Qt.Key.Key_V, 'Select'), (Qt.Key.Key_C, 'Circle'), (Qt.Key.Key_L, 'Line')):
            ev = QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, '')
            b.canvas.keyPressEvent(ev)
            assert b.tool == expected, f'Key {key} expected tool {expected}, got {b.tool}'
        window.leave_builder()
        pump(app, 5)

        print('smoke_p2_ui_workflow OK')
    finally:
        window.force_close = True
        window.close()
        pump(app, 5)


if __name__ == '__main__':
    main()
