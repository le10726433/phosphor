"""The theme layer (ST1): Classic reproduces the interface it replaced exactly."""
import re
import unittest

from phosphor.display import theme as themes
from phosphor.display.crt_theme import PALETTES, button_colors

ARROWS = dict(up='C:/t/up.png', down='C:/t/down.png', up_off='C:/t/up_off.png', down_off='C:/t/down_off.png')
MODERN_IMAGES = dict(ARROWS, chevron='C:/t/chevron.png', chevron_checked='C:/t/chevron_checked.png',
                     checkmark='C:/t/checkmark.png')


def old_stylesheet(p, arrows):
    """Verbatim from qt_app.apply_style before ST1 (0.89.0-dev): the reference Classic matches."""
    b = button_colors(p)
    arrow_rules = f'''
            QAbstractSpinBox::up-arrow {{ width:8px; height:5px; image:url("{arrows['up']}"); }}
            QAbstractSpinBox::down-arrow {{ width:8px; height:5px; image:url("{arrows['down']}"); }}
            QAbstractSpinBox::up-arrow:off,QAbstractSpinBox::up-arrow:disabled {{ image:url("{arrows['up_off']}"); }}
            QAbstractSpinBox::down-arrow:off,QAbstractSpinBox::down-arrow:disabled {{ image:url("{arrows['down_off']}"); }}
        ''' if arrows else f"QAbstractSpinBox::up-arrow,QAbstractSpinBox::down-arrow {{ width:8px; height:2px; background:{p['fg']}; }}"
    return f'''
            QWidget {{ background:{p['bg']}; color:{p['fg']}; font-family:"JetBrains Mono"; font-size:12px; }}
            QLabel#brand {{ color:{p['accent']}; font-size:19px; font-weight:bold; }}
            QPushButton,QComboBox,QLineEdit,QAbstractSpinBox,QListWidget {{ background:{p['field']}; border:1px solid {p['border']}; padding:4px; }}
            QAbstractSpinBox::up-button,QAbstractSpinBox::down-button {{ subcontrol-origin:border; width:24px; background:{p['field']}; border-left:1px solid {p['border']}; }}
            QAbstractSpinBox::up-button {{ subcontrol-position:top right; }}
            QAbstractSpinBox::down-button {{ subcontrol-position:bottom right; }}
            QAbstractSpinBox::up-button:hover,QAbstractSpinBox::down-button:hover {{ background:{p['active']}; }}
            {arrow_rules}
            QPushButton:hover,QPushButton:checked,QMenu::item:selected {{ background:{p['active']}; color:{p['accent']}; }}
            QToolButton {{ background:{b['fill']}; border:1px solid {b['frame']}; padding:1px 2px; }}
            QToolButton:hover {{ border-color:{p['fg']}; }}
            QToolButton:checked,QToolButton:pressed {{ background:{b['pressed']}; color:{p['accent']}; border-color:{p['accent']}; font-weight:bold; }}
            QToolButton:disabled {{ color:{p['muted']}; }}
            QToolButton#roll_notes,QToolButton#roll_transforms,QToolButton#roll_chords,QToolButton#roll_velocity {{ padding:0px; }}  /* Fixed-size gutter buttons. */
            QToolBar#app_bar {{ border:none; border-bottom:1px solid {p['border']}; spacing:2px; padding:2px 12px; }}
            QToolBar#app_bar QToolButton {{ font-size:10px; padding:3px 4px; border:1px solid transparent; }}
            QToolBar#app_bar QToolButton:hover {{ background:{p['field']}; border-color:{p['border']}; }}
            QToolBar#app_bar QToolButton:checked {{ background:{p['active']}; color:{p['accent']}; border-color:{p['border']}; }}
            QToolBar#app_bar QToolButton:disabled {{ color:{p['muted']}; }}
            QLabel#panel_title {{ color:{p['accent']}; font-weight:bold; }}
            QTabBar::tab {{ padding:10px 18px; color:{p['muted']}; }}
            QTabBar::tab:selected {{ background:{p['active']}; color:{p['accent']}; }}
            QTabWidget::pane {{ border:1px solid {p['border']}; }}
            QTabWidget::tab-bar {{ alignment:left; left:0px; }}  /* UI2: every tab bar starts at the panel border (macOS centres them). */
            QSlider::groove:horizontal {{ height:4px; background:{p.get('groove',p['field'])}; }}
            QSlider::handle:horizontal {{ background:{p['accent']}; width:12px; margin:-5px 0; }}
            QSlider::handle:horizontal:disabled {{ background:{p['border']}; }}
            QLabel:disabled,QLineEdit:disabled,QAbstractSpinBox:disabled,QPushButton:disabled {{ color:{p['muted']}; }}
            QMenuBar::item {{ padding:7px 14px; }}
            QListWidget::item:selected {{ background:{p['active']}; color:{p['accent']}; }}
            QScrollBar:vertical {{ width:10px; background:{p['bg']}; }}
            QScrollBar::handle:vertical {{ background:{p['border']}; min-height:25px; }}
        '''


class ClassicTheme(unittest.TestCase):
    def test_stylesheet_is_byte_identical_for_every_preset(self):
        for name, palette in PALETTES.items():
            for arrows in (ARROWS, MODERN_IMAGES, None):
                with self.subTest(preset=name, arrows=bool(arrows)):
                    self.assertEqual(themes.theme_for(themes.CLASSIC, name).stylesheet(arrows), old_stylesheet(palette, arrows))

    def test_fixed_strings_match_the_old_inline_styles(self):
        t = themes.theme_for()
        self.assertEqual(t.version_stylesheet(), 'font-size: 10px; color: #888888;')
        self.assertEqual(t.compact_tabs_stylesheet(), 'QTabBar::tab { padding:8px 6px; }')
        self.assertEqual(t.record_stylesheet(), 'QToolButton:checked { background: #a82828; color: white; }')

    def test_timeline_colors_as_before(self):
        for name, p in PALETTES.items():
            expected = dict(background=p['field'], trace=p['trace'], accent=p['accent'],
                            grid=p['grid'] if p['grid'] != p['field'] else p['border'], text=p['fg'])
            self.assertEqual(themes.theme_for(themes.CLASSIC, name).timeline_colors(), expected)

    def test_unknown_values_fall_back_and_themes_are_frozen(self):
        t = themes.theme_for('Nope', 'Nope')
        self.assertEqual((t.style, t.preset), (themes.CLASSIC, 'Green monochrome'))
        with self.assertRaises(TypeError): t.colors['bg'] = '#000000'
        self.assertIs(themes.theme_for(), themes.theme_for())

    def test_current_defaults_to_classic_and_follows_set_current(self):
        previous = themes._current
        try:
            themes.set_current(None); self.assertEqual(themes.current(), themes.theme_for())
            amber = themes.theme_for(themes.CLASSIC, 'Amber'); themes.set_current(amber)
            self.assertIs(themes.current(), amber)
        finally:
            themes.set_current(previous)


def luminance(color):
    channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))


class ModernThemeTests(unittest.TestCase):
    def test_palette_and_scope(self):
        keys = ('bg', 'field', 'border', 'fg', 'muted', 'accent', 'active', 'selection', 'selection_text')
        for mode, values in (
                ('Dark', '#1e1e1e #2c2c2e #3a3a3c #f5f5f7 #98989d #0a84ff #3a3a3c #0074e8 #ffffff'),
                ('Light', '#f5f5f7 #ffffff #d2d2d7 #1d1d1f #6e6e73 #007aff #e8e8ed #0072ef #ffffff')):
            for name, palette in PALETTES.items():
                with self.subTest(mode=mode, preset=name):
                    t = themes.theme_for('Modern', name, mode)
                    self.assertEqual([t.color(key) for key in keys], values.split())
                    self.assertEqual(set(t.colors), set(themes.theme_for('Classic', name).colors))
                    for key in ('trace', 'background'): self.assertEqual(t.color(key), palette[key])
                    self.assertEqual(t.timeline_colors()['trace'], t.color('accent'))
                    self.assertEqual(t.timeline_colors()['playhead'], t.color('accent'))
                    self.assertEqual(t.fonts.family, '')
                    for front, back in (('fg', 'bg'), ('fg', 'field'), ('selection_text', 'selection'), ('muted', 'bg')):
                        low, high = sorted(luminance(t.color(key)) for key in (front, back))
                        self.assertGreaterEqual((high + .05) / (low + .05), 4.5, (front, back))

    def test_selector_coverage(self):
        def selectors(sheet):
            sheet = re.sub(r'/\*.*?\*/', '', sheet, flags=re.S)
            return {selector.strip() for group in re.findall(r'([^{}]+)\{', sheet)
                    for selector in group.split(',')}
        controls = {'QComboBox::drop-down', 'QComboBox QAbstractItemView', 'QToolButton::menu-button'}
        # Rules that need an image: without it they are left out, so Qt paints its native one.
        chevrons = {'QComboBox::down-arrow', 'QToolButton::menu-indicator', 'QToolButton::menu-arrow',
                    'QPushButton::menu-indicator'}
        indicators = {'QCheckBox::indicator', 'QListWidget::indicator', 'QTreeView::indicator'}
        no_checkmark = {key: value for key, value in MODERN_IMAGES.items() if key != 'checkmark'}
        for arrows in (None, ARROWS, no_checkmark, MODERN_IMAGES):
            classic = selectors(themes.theme_for().stylesheet(arrows))
            for mode in ('Dark', 'Light'):
                with self.subTest(arrows=sorted(arrows or ()), mode=mode):
                    sheet = themes.theme_for('Modern', 'Amber', mode).stylesheet(arrows)
                    found = selectors(sheet)
                    self.assertFalse((classic | controls) - found)
                    has_chevron = bool(arrows) and 'chevron' in arrows
                    has_check = bool(arrows) and 'checkmark' in arrows
                    self.assertEqual(chevrons <= found, has_chevron)
                    self.assertEqual(bool(chevrons & found), has_chevron)
                    self.assertEqual(indicators <= found, has_check)
                    self.assertEqual('::indicator' in sheet, has_check)
                    for name in ('chevron', 'chevron_checked', 'checkmark'):
                        if arrows and name in arrows:
                            self.assertIn(f'image:url("{arrows[name]}")', sheet)

    def test_painted_text_pairs(self):
        # (text, what it is painted on): marker names and tempo flags on the flag fill, panel
        # titles on the window, other flag labels in the text colour on the flag fill.
        for mode in ('Dark', 'Light'):
            t = themes.theme_for('Modern', 'Green monochrome', mode)
            timeline = t.timeline_colors()
            pairs = ((timeline['accent_text'], timeline['grid']), (timeline['accent_text'], timeline['background']),
                     (timeline['text'], timeline['grid']), (t.color('accent_text'), t.color('bg')))
            for front, back in pairs:
                with self.subTest(mode=mode, pair=(front, back)):
                    self.assertGreaterEqual(themes.contrast_ratio(front, back), 4.5)
            self.assertIn(f"QLabel#panel_title {{ color:{t.color('accent_text')}; }}", t.stylesheet(None))
            self.assertNotEqual(t.color('accent_text'), t.color('accent'))  # Lines keep the plain accent.
        for name, palette in PALETTES.items():  # Classic keeps its exact colours.
            self.assertEqual(themes.theme_for('Classic', name).color('accent_text'), palette['accent'])
            self.assertNotIn('accent_text', themes.theme_for('Classic', name).timeline_colors())

    def test_under_stroke_by_luminance(self):
        from phosphor.display.phosphor import PHOSPHORS
        field = themes.theme_for('Modern', 'Green monochrome', 'Light').color('field')
        p31 = '#' + ''.join(f'{round(v * 255):02x}' for v in PHOSPHORS['P31 green']['fast_color'])
        for color in (p31, '#ffff00', '#ffffff'):  # Light enough in HSL terms, yet nearly invisible on white.
            self.assertTrue(themes.needs_under_stroke(color, field), color)
        for color in ('#1d1d1f', '#0000ff'):
            self.assertFalse(themes.needs_under_stroke(color, field), color)
        self.assertAlmostEqual(themes.relative_luminance('#ffffff'), 1.)
        self.assertAlmostEqual(themes.contrast_ratio('#000000', '#ffffff'), 21.)

    def test_resolve_mode(self):
        for mode, other in (('Dark', 'Light'), ('Light', 'Dark')):
            self.assertEqual(themes.resolve_mode(mode, other, .9 if mode == 'Dark' else .02), mode)
        self.assertEqual(themes.resolve_mode('System', 'Dark', .9), 'Dark')
        self.assertEqual(themes.resolve_mode('System', 'Light', .02), 'Light')
        self.assertEqual(themes.resolve_mode('System', 'Unknown', .02), 'Dark')
        self.assertEqual(themes.resolve_mode('System', 'Unknown', .9), 'Light')
        self.assertEqual(themes.resolve_mode('System', 'Unknown', .5), 'Light')
        # Without any OS information it agrees with theme_for's fallback.
        self.assertEqual(themes.resolve_mode('System'), 'Dark')
        self.assertEqual(themes.resolve_mode('nonsense'), 'Dark')
        self.assertEqual(themes.theme_for('Modern', 'Amber', 'System').mode, 'Dark')
        self.assertEqual(themes.theme_for('Modern', 'Amber', 'Light').mode, 'Light')
        self.assertEqual(themes.theme_for('Classic', 'Amber', 'Light').mode, '')

    def test_fallbacks(self):
        self.assertEqual(themes.theme_for('Modern', 'unknown', 'unknown'), themes.theme_for('Modern'))
        self.assertEqual(themes.theme_for('Modern', 'Amber', 'System'), themes.theme_for('Modern', 'Amber', 'Dark'))
        self.assertEqual(themes.theme_for('Classic', 'Cyan', 'Light'), themes.theme_for('Classic', 'Cyan'))


class VintageThemeTests(unittest.TestCase):
    def test_palette_scope_and_contrast(self):
        for name, palette in PALETTES.items():
            with self.subTest(preset=name):
                t = themes.theme_for('Vintage', name)
                self.assertEqual(t.style, themes.VINTAGE)
                self.assertEqual(t.mode, '')
                self.assertEqual(set(t.colors), set(themes.theme_for('Classic', name).colors))
                for key in ('trace', 'background'):
                    self.assertEqual(t.color(key), palette[key])
                for front, back in (('fg', 'bg'), ('fg', 'field'), ('selection_text', 'selection'),
                                    ('muted', 'bg'), ('muted', 'field'), ('accent_text', 'bg')):
                    self.assertGreaterEqual(themes.contrast_ratio(t.color(front), t.color(back)), 4.5, (front, back))
                timeline = t.timeline_colors()
                for front, back in ((timeline['text'], timeline['background']),
                                    (timeline['text'], timeline['grid']),
                                    (timeline['accent_text'], timeline['background']),
                                    (timeline['accent_text'], timeline['grid'])):
                    self.assertGreaterEqual(themes.contrast_ratio(front, back), 4.5, (front, back))

    def test_stylesheet_has_hardware_3d_rules(self):
        t = themes.theme_for('Vintage', 'Green monochrome')
        sheet = t.stylesheet(MODERN_IMAGES)
        self.assertIn('qradialgradient', sheet)
        self.assertIn('QLabel#composer_position', sheet)
        self.assertIn('QComboBox QAbstractItemView::item', sheet)
        self.assertIn('QListWidget[customRows="true"]::item', sheet)
        # Ignores mode argument (single fixed look).
        self.assertEqual(themes.theme_for('Vintage', 'Amber', 'Light'), themes.theme_for('Vintage', 'Amber', 'Dark'))


if __name__ == '__main__':
    unittest.main()
