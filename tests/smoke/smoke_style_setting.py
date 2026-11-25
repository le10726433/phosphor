"""Live style, preset and system appearance changes in both Appearance editors and monitor hosts."""
import _path  # noqa: F401
import os
import sys
import tempfile
from pathlib import Path
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from types import SimpleNamespace
from unittest.mock import patch

import json

from PySide6.QtCore import Qt, SIGNAL
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QPushButton

from phosphor.display import theme as themes
from phosphor.editor import qt_app as q
from phosphor.engine.composition import SANDBOX
from phosphor.storage.app_files import validated_preferences

q.configure_graphics()
app = QApplication([])


def check_buttons(buttons, theme):
    assert buttons._foreground == QColor(theme.colors['fg'])
    for actual, key in ((buttons._background, 'bg'), (buttons._accent, 'accent')):
        expected = QColor(theme.colors[key])
        expected.setAlpha(theme.metrics.corner_alpha)
        assert actual == expected


def close_window(window):
    window.history_timer.stop(); window.timer.stop(); window.force_close = True
    window.shutdown_audio(); window.release_views(); window.close()


hints = QGuiApplication.styleHints()
SCHEME_SIGNAL = SIGNAL('colorSchemeChanged(Qt::ColorScheme)')


def scheme_receivers(): return hints.receivers(SCHEME_SIGNAL)


def os_scheme(scheme):
    """Deliver an OS colour scheme change the way Qt does: through the signal itself."""
    hints.colorSchemeChanged.emit(scheme); app.processEvents()


def saved(key, flush=False):
    if flush:
        assert w.preferences_save.isActive()  # The side panel saves after a short delay.
        w.preferences_save.stop(); w.save_preferences()
    return json.loads(q.PREFERENCES_PATH.read_text(encoding='utf-8'))[key]


green = dict(q.DEFAULT_PREFERENCES, palette='Green monochrome')
with (tempfile.TemporaryDirectory() as folder,
      patch.object(q, 'PREFERENCES_PATH', Path(folder) / 'preferences.json'),
      patch.object(q, 'read_preferences', return_value=green),
      patch.object(q, 'read_signal_preferences', return_value=dict(q.DEFAULT_SIGNAL_PREFERENCES)),
      patch.object(q, 'write_workspace', lambda *args: None)):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=SANDBOX))
    try:
        check_buttons(w.corner_buttons, w.theme)
        check_buttons(w.ensure_monitor_window().buttons, w.theme)
        w.resize(1100, 760); w.show(); app.processEvents()
        editor = w.appearance_editor
        assert [editor.style_choice.itemText(i) for i in range(editor.style_choice.count())] == ['Classic', 'Modern']
        assert editor.mode_row.isHidden()
        assert w.preferences['style'] == 'Classic' and editor.values()['style'] == 'Classic'
        theme = w.theme
        assert theme is themes.current() and theme.style == 'Classic' and theme.preset == w.preferences['palette']
        assert w.styleSheet() == theme.stylesheet(w.spin_arrow_images(theme.colors))
        assert w.composer_panel.timeline.colors == theme.timeline_colors()
        scene_button = w.bar_buttons[q.PANEL_SCENE]
        old_icon = scene_button.icon().pixmap(26, 26).toImage()
        old_colors = dict(w.composer_panel.timeline.colors)
        w.show_preferences()
        dialog_editor = w.preferences_dialog.findChild(q.AppearanceEditor)
        for appearance in (editor, dialog_editor):
            appearance.tabs.setStyleSheet('')
        # A preset change re-themes the window, the painted widgets and the record button.
        w.apply_preferences(dict(w.preferences, palette='Amber')); app.processEvents()
        amber = themes.current()
        assert amber.preset == 'Amber' and w.theme is amber and w.styleSheet() == amber.stylesheet(w.spin_arrow_images(amber.colors))
        assert w.composer_panel.timeline.colors == amber.timeline_colors()
        assert w.composer_panel.record.styleSheet() == amber.record_stylesheet()
        assert w.version_label.styleSheet() == amber.version_stylesheet()
        assert scene_button.icon().pixmap(26, 26).toImage() != old_icon
        assert w.composer_panel.timeline.colors != old_colors
        for appearance in (editor, dialog_editor):
            assert appearance.tabs.styleSheet() == amber.compact_tabs_stylesheet()
            assert appearance.editor.plot.theme is amber
        with patch.object(w, 'setStyleSheet', wraps=w.setStyleSheet) as restyle:
            w.apply_preferences(w.preferences)
            restyle.assert_not_called()
        classic_sheet = w.styleSheet()
        dialog_editor.style_choice.setCurrentText('Modern')
        assert all(not e.mode_row.isHidden() for e in (editor, dialog_editor))
        family = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
        def check_modern(mode):
            app.processEvents()
            t = w.theme
            expected = themes.theme_for('Modern', 'Amber', mode)
            assert t.colors == expected.colors and t.fonts.family == family
            assert w.styleSheet() == t.stylesheet(w.spin_arrow_images(t.colors)) != classic_sheet
            assert w.composer_panel.timeline.colors == expected.timeline_colors()
            assert w.composer_panel.timeline.font().family() == family
            for e in (editor, dialog_editor):
                assert e.editor.plot.theme is t and e.values()['style'] == 'Modern'
            for buttons in w.all_corner_buttons(): check_buttons(buttons, t)
            assert all(host.view.color() == QColor(q.PALETTES['Amber']['background']) for host in w.hosts())
        for mode in ('Dark', 'Light'):
            editor.mode_choice.setCurrentText(mode)
            assert dialog_editor.values()['style_mode'] == mode
            check_modern(mode)
        dialog_editor.mode_choice.setCurrentText('System')
        for mode, hint in (('Dark', Qt.ColorScheme.Dark), ('Light', Qt.ColorScheme.Light)):
            os_scheme(hint)
            check_modern(mode)
        editor.style_choice.setCurrentText('Classic')
        assert w.styleSheet() == classic_sheet and w.composer_panel.timeline.colors == amber.timeline_colors()
        assert all(e.mode_row.isHidden() for e in (editor, dialog_editor))
        # Give the display a distinctive background so reading the theme would fail.
        display = dict(q.PALETTES['Amber'], background='#123456')
        with patch.dict(q.PALETTES, Amber=display):
            w.style_hosts()
            assert all(host.view.color() == QColor(display['background']) for host in w.hosts())
        w.style_hosts()
        other = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=SANDBOX))
        try:
            for window in (w, other):
                window.apply_preferences(window.preferences)
            for window, expected in ((w, amber), (other, theme)):
                timeline = window.composer_panel.timeline
                assert timeline.theme is expected and timeline.colors == expected.timeline_colors()
                for splitter in (window.vsplit, window.splitter, window.browser_split):
                    assert splitter.handle(1).color == QColor(expected.colors['border'])
                    assert splitter.handle(1).hover_color == QColor(expected.colors['accent'])
        finally:
            close_window(other)
        # The Style box in Preferences shows the same value.
        boxes = w.preferences_dialog.findChildren(q.QComboBox, 'style')
        assert boxes and all(box.currentText() == 'Classic' for box in boxes)
        w.preferences_dialog.close(); app.processEvents()

        # The side panel's real Defaults and Revert buttons, and what they save.
        w.apply_preferences(dict(w.preferences, style='Modern', style_mode='Dark')); w.save_preferences()
        w.show_panel(q.PANEL_SCENE); w.show_panel(q.PANEL_APPEARANCE); app.processEvents()  # Revert's snapshot: Modern Dark.
        panel_buttons = {b.text(): b for b in w.panels[q.PANEL_APPEARANCE].widget().findChildren(QPushButton)}
        editor.mode_choice.setCurrentText('Light')
        assert w.preferences['style_mode'] == 'Light' and saved('style_mode', True) == 'Light'
        panel_buttons['Defaults'].click(); app.processEvents()
        assert (w.preferences['style'], w.preferences['style_mode']) == ('Classic', themes.DEFAULT_MODE)
        assert saved('style_mode', True) == themes.DEFAULT_MODE and saved('style') == 'Classic'
        panel_buttons['Revert'].click(); app.processEvents()
        assert (w.preferences['style'], w.preferences['style_mode']) == ('Modern', 'Dark') and w.theme.mode == 'Dark'
        assert saved('style_mode', True) == 'Dark' and saved('style') == 'Modern'

        # Preferences: the real Apply and Cancel buttons. Cancel restores the last applied values.
        w.show_preferences(); dialog = w.preferences_dialog; app.processEvents()
        box = dialog.findChild(QDialogButtonBox)
        apply_button = box.button(QDialogButtonBox.StandardButton.Apply)
        cancel_button = box.button(QDialogButtonBox.StandardButton.Cancel)
        dialog_editor = dialog.findChild(q.AppearanceEditor)
        dialog_editor.mode_choice.setCurrentText('Light'); app.processEvents()
        assert w.theme.mode == 'Light'  # Preview.
        apply_button.click(); app.processEvents()
        assert saved('style_mode') == 'Light'
        # A failed write keeps the previous baseline (and the file) for Cancel.
        errors = []
        with patch.object(q, 'atomic_json', side_effect=OSError('disk full')), patch.object(w, 'error', errors.append):
            dialog_editor.mode_choice.setCurrentText('System'); apply_button.click(); app.processEvents()
        assert errors and saved('style_mode') == 'Light'
        cancel_button.click(); app.processEvents()
        assert w.preferences_dialog is None and w.preferences['style_mode'] == 'Light' and w.theme.mode == 'Light'
        assert saved('style_mode') == 'Light'

        # One System window, one explicit-mode window and a pop-out, all driven by the OS signal.
        w.apply_preferences(dict(w.preferences, style='Modern', style_mode='System'))
        w.pop_out(); app.processEvents()
        explicit = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=SANDBOX))
        try:
            explicit.apply_preferences(dict(explicit.preferences, style='Modern', style_mode='Light', palette='Cyan'))
            connected = scheme_receivers()
            assert connected >= 2, connected
            light_sheet = explicit.styleSheet()
            # Typed, uncommitted text in an open Preferences dialog survives a scheme change.
            w.show_preferences(); app.processEvents()
            entry = w.preferences_dialog.findChild(q.AppearanceEditor).sliders['beam_intensity'].entry
            entry.setText('0.123')
            with patch.object(w, 'apply_preferences', wraps=w.apply_preferences) as reload:
                for mode, hint in (('Dark', Qt.ColorScheme.Dark), ('Light', Qt.ColorScheme.Light), ('Dark', Qt.ColorScheme.Dark)):
                    os_scheme(hint)
                    assert w.theme.mode == mode and w.theme.colors == themes.theme_for('Modern', w.preferences['palette'], mode).colors
                    assert w.styleSheet() == w.theme.stylesheet(w.spin_arrow_images(w.theme.colors))
                    assert w.monitor_window is not None and w.monitor_window.isVisible()
                    for buttons in w.all_corner_buttons(): check_buttons(buttons, w.theme)
                    assert w.composer_panel.timeline.colors == w.theme.timeline_colors()
                    assert explicit.theme.mode == 'Light' and explicit.styleSheet() == light_sheet
                    assert entry.text() == '0.123' and not w.preferences_save.isActive()
                reload.assert_not_called()
            w.preferences_dialog.close(); app.processEvents()
            w.pop_in(); app.processEvents()
            # Release the System window: its connection goes (once), and the others keep working.
            dark_sheet = w.styleSheet()
            assert not w.grab().isNull()
            w.release_views(); w.release_views()
            assert scheme_receivers() == connected - 1, (scheme_receivers(), connected)
            explicit.apply_preferences(dict(explicit.preferences, style_mode='System'))
            for mode, hint in (('Light', Qt.ColorScheme.Light), ('Dark', Qt.ColorScheme.Dark)):
                os_scheme(hint)
                assert explicit.theme.mode == mode and w.theme.mode == 'Dark' and w.styleSheet() == dark_sheet
            explicit.release_views()
            assert scheme_receivers() == connected - 2
        finally:
            close_window(explicit)
        # A style this version doesn't know (from a newer one) falls back to Classic.
        assert validated_preferences(dict(w.preferences, style='Vintage'))['style'] == 'Classic'
    finally:
        close_window(w)
print('Style setting passed for Classic and Modern, including live System changes through the OS signal.')
