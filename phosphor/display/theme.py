"""Interface colours, fonts, sizes, borders and padding for Classic and Modern.

A Theme is chosen by a style, a colour preset and a resolved mode. The stylesheet and self-painted
widgets read their values from it. Classic is
today's interface: its tokens are the values the code used before ST1, and its stylesheet is
byte-identical to the one it replaces (tests/unit/test_theme.py).

The scope picture is not part of the theme: the trace, its background and the phosphor keep their
own display settings in every style (styles plan, decision 2). `colors` still carries the
palette's `background` and `trace` for the widgets that draw a small preview of the scope.

Qt-free: colours are '#rrggbb' strings (or (r, g, b, a) tuples where Qt needs alpha), so
check_core can test it and preferences can validate against STYLES.
"""
from dataclasses import dataclass, field
from functools import lru_cache
from types import MappingProxyType

from phosphor.display.crt_theme import PALETTES, button_colors

CLASSIC = 'Classic'
MODERN = 'Modern'
VINTAGE = 'Vintage'
STYLES = (CLASSIC, MODERN, VINTAGE)
MODES = ('System', 'Dark', 'Light')
DEFAULT_MODE = 'System'
DEFAULT_STYLE = CLASSIC


def _frozen(mapping): return MappingProxyType(dict(mapping))


@dataclass(frozen=True)
class Fonts:
    family: str = 'JetBrains Mono'
    app_points: int = 10      # QApplication's font before the stylesheet applies.
    base_px: int = 12         # Every widget (QWidget rule).
    brand_px: int = 19
    bar_px: int = 10          # Application bar button captions.
    version_px: int = 10      # The build version in the status bar.
    timeline_px: int = 11     # The note roll's ruler and clip names.
    timeline_small_px: int = 9  # Marker flags and the track header's second line.
    plot_points: float = 7.   # The noise spectrum's axis labels.
    plot_shrink: float = 2.   # The phosphor curve editor: the widget font less this many points...
    plot_min_points: float = 7.  # ...never below this.


@dataclass(frozen=True)
class Metrics:
    border: int = 1
    radius: int = 0
    field_padding: int = 4             # Push buttons, combos, line edits, spin boxes, lists.
    spin_button: int = 24              # Width of a spin box's up/down buttons.
    spin_arrow: tuple = (8, 5)         # Arrow image size (fallback bars are 2 px high).
    tool_padding: str = '1px 2px'
    bar_spacing: int = 2
    bar_padding: str = '2px 12px'
    bar_button_padding: str = '3px 4px'
    tab_padding: str = '10px 18px'
    compact_tab_padding: str = '8px 6px'  # Appearance's five tabs in the narrow side panel.
    groove: int = 4
    handle: int = 12
    handle_margin: int = -5
    menubar_padding: str = '7px 14px'
    scroll_width: int = 10
    scroll_min: int = 25
    corner_radius: int = 5
    corner_stroke: float = 1.6
    corner_alpha: int = 180
    bar_icon_size: int = 26
    bar_icon_canvas: int = 48
    bar_icon_stroke: int = 3
    lane_label_opacity: float = .7
    lane_disabled_opacity: float = .4
    lane_guide_alpha: int = 90
    lane_stroke: int = 1
    lane_selected_stroke: int = 2
    lane_line_stroke: float = 1.5
    ruler_height: int = 46
    ruler_flag_height: int = 10
    ruler_flag_width: int = 40
    ruler_label_spacing: int = 30
    ruler_label_x: int = 3
    ruler_label_bottom: int = 6
    marker_text_room: int = 9
    marker_text_inset: int = 6
    key_alpha: int = 110
    scale_root_alpha: int = 42
    scale_note_alpha: int = 18
    loop_alpha: int = 60
    scene_selected_alpha: int = 150
    scene_picked_alpha: int = 120
    scene_alpha: int = 70
    mute_divisor: int = 3
    selected_alpha: int = 255
    scene_handle_alpha: int = 180
    selection_alpha: int = 35
    note_alpha: int = 170
    dropped_voice_alpha: int = 45
    dropped_edge_alpha: int = 100
    lane_grid_alpha: int = 140
    lane_shade_alpha: int = 22
    empty_lane_alpha: int = 90
    parked_lane_alpha: int = 110
    active_chip_alpha: int = 60
    active_guide_alpha: int = 170
    guide_alpha: int = 90
    switch_alpha: int = 170
    track_handle_alpha: int = 120
    drop_target_alpha: int = 55
    drop_row_alpha: int = 22
    track_hint_alpha: int = 110
    add_hint_alpha: int = 150
    track_selected_alpha: int = 60
    missing_alpha: int = 25
    audio_selected_alpha: int = 95
    audio_alpha: int = 45
    hatch_alpha: int = 110
    muted_waveform_opacity: float = 0.35
    audio_handle_alpha: int = 160
    waveform_alpha: int = 200
    gain_alpha: int = 90
    gain_shade_alpha: int = 25


@dataclass(frozen=True)
class Theme:
    style: str
    preset: str
    colors: MappingProxyType            # Palette keys plus the derived control colours below.
    fonts: Fonts = field(default_factory=Fonts)
    metrics: Metrics = field(default_factory=Metrics)
    lane_colors: tuple = ()             # Automation lanes, in order.
    clip_colors: tuple = ()             # (name, colour) choices for clips.
    mode: str = ''                      # Modern's resolved 'Dark' or 'Light'; '' for Classic.

    def color(self, name): return self.colors[name]

    def builder_colors(self): return self.colors

    def timeline_colors(self):
        """The note roll's colour dict (background, trace, accent, grid, text)."""
        c = self.colors
        return dict(background=c['field'], trace=c['trace'], accent=c['accent'], grid=c['timeline_grid'], text=c['fg'])

    def arrow_rules(self, arrows):
        """Spin box arrows: images when the temp folder is writable, else plain bars."""
        c, m = self.colors, self.metrics
        w, h = m.spin_arrow
        if arrows:
            return f'''
            QAbstractSpinBox::up-arrow {{ width:{w}px; height:{h}px; image:url("{arrows['up']}"); }}
            QAbstractSpinBox::down-arrow {{ width:{w}px; height:{h}px; image:url("{arrows['down']}"); }}
            QAbstractSpinBox::up-arrow:off,QAbstractSpinBox::up-arrow:disabled {{ image:url("{arrows['up_off']}"); }}
            QAbstractSpinBox::down-arrow:off,QAbstractSpinBox::down-arrow:disabled {{ image:url("{arrows['down_off']}"); }}
        '''
        return f"QAbstractSpinBox::up-arrow,QAbstractSpinBox::down-arrow {{ width:{w}px; height:2px; background:{c['fg']}; }}"

    def stylesheet(self, arrows):
        """The window's stylesheet."""
        c, f, m = self.colors, self.fonts, self.metrics
        bw = m.border
        return f'''
            QWidget {{ background:{c['bg']}; color:{c['fg']}; font-family:"{f.family}"; font-size:{f.base_px}px; }}
            QLabel#brand {{ color:{c['accent']}; font-size:{f.brand_px}px; font-weight:bold; }}
            QPushButton,QComboBox,QLineEdit,QAbstractSpinBox,QListWidget {{ background:{c['field']}; border:{bw}px solid {c['border']}; padding:{m.field_padding}px; }}
            QAbstractSpinBox::up-button,QAbstractSpinBox::down-button {{ subcontrol-origin:border; width:{m.spin_button}px; background:{c['field']}; border-left:{bw}px solid {c['border']}; }}
            QAbstractSpinBox::up-button {{ subcontrol-position:top right; }}
            QAbstractSpinBox::down-button {{ subcontrol-position:bottom right; }}
            QAbstractSpinBox::up-button:hover,QAbstractSpinBox::down-button:hover {{ background:{c['active']}; }}
            {self.arrow_rules(arrows)}
            QPushButton:hover,QPushButton:checked,QMenu::item:selected {{ background:{c['active']}; color:{c['accent']}; }}
            QToolButton {{ background:{c['button_fill']}; border:{bw}px solid {c['button_frame']}; padding:{m.tool_padding}; }}
            QToolButton:hover {{ border-color:{c['fg']}; }}
            QToolButton:checked,QToolButton:pressed {{ background:{c['button_pressed']}; color:{c['accent']}; border-color:{c['accent']}; font-weight:bold; }}
            QToolButton:disabled {{ color:{c['muted']}; }}
            QToolButton#roll_notes,QToolButton#roll_transforms,QToolButton#roll_chords,QToolButton#roll_velocity {{ padding:0px; }}  /* Fixed-size gutter buttons. */
            QToolBar#app_bar {{ border:none; border-bottom:{bw}px solid {c['border']}; spacing:{m.bar_spacing}px; padding:{m.bar_padding}; }}
            QToolBar#app_bar QToolButton {{ font-size:{f.bar_px}px; padding:{m.bar_button_padding}; border:{bw}px solid transparent; }}
            QToolBar#app_bar QToolButton:hover {{ background:{c['field']}; border-color:{c['border']}; }}
            QToolBar#app_bar QToolButton:checked {{ background:{c['active']}; color:{c['accent']}; border-color:{c['border']}; }}
            QToolBar#app_bar QToolButton:disabled {{ color:{c['muted']}; }}
            QLabel#panel_title {{ color:{c['accent']}; font-weight:bold; }}
            QTabBar::tab {{ padding:{m.tab_padding}; color:{c['muted']}; }}
            QTabBar::tab:selected {{ background:{c['active']}; color:{c['accent']}; }}
            QTabWidget::pane {{ border:{bw}px solid {c['border']}; }}
            QTabWidget::tab-bar {{ alignment:left; left:0px; }}  /* UI2: every tab bar starts at the panel border (macOS centres them). */
            QSlider::groove:horizontal {{ height:{m.groove}px; background:{c['groove']}; }}
            QSlider::handle:horizontal {{ background:{c['accent']}; width:{m.handle}px; margin:{m.handle_margin}px 0; }}
            QSlider::handle:horizontal:disabled {{ background:{c['border']}; }}
            QLabel:disabled,QLineEdit:disabled,QAbstractSpinBox:disabled,QPushButton:disabled {{ color:{c['muted']}; }}
            QMenuBar::item {{ padding:{m.menubar_padding}; }}
            QListWidget::item:selected {{ background:{c['active']}; color:{c['accent']}; }}
            QScrollBar:vertical {{ width:{m.scroll_width}px; background:{c['bg']}; }}
            QScrollBar::handle:vertical {{ background:{c['border']}; min-height:{m.scroll_min}px; }}
        '''

    def version_stylesheet(self):
        return f"font-size: {self.fonts.version_px}px; color: {self.colors['version_text']};"

    def compact_tabs_stylesheet(self):
        return f'QTabBar::tab {{ padding:{self.metrics.compact_tab_padding}; }}'

    def record_stylesheet(self):
        return f"QToolButton:checked {{ background: {self.colors['record']}; color: {self.colors['record_text']}; }}"


# Classic's fixed colours: what the code wrote directly before ST1.
CLASSIC_FIXED = dict(
    version_text='#888888',            # The build version label.
    record='#a82828', record_text='white',  # Composer's Record button while recording.
    solo='#f4df83',                    # The track header's S while soloed.
    missing_text='#ffffff',            # A missing audio clip's name.
    clip_red='#ff4b4b',                # Clip warnings.
    search_highlight='#ffe082', search_text='#202020',  # Manual search matches.
    axis_x='#ff7979', axis_y='#8ee78e', axis_z='#80b8ff',  # The viewport's rotation gizmo.
    overlay_fore='white', overlay_back=(24, 24, 24, 180), overlay_accent=(65, 140, 105, 180),  # Monitor corner buttons.
    handle_line='#1d3a24', handle_hover='#7cff9c',  # Dividing lines before the first styling.
)
CLASSIC_LANES = ('#7cff9c', '#ffd27c', '#7cc8ff', '#ff8fb1', '#c9a2ff', '#9ff0e8')
CLASSIC_CLIPS = (('Rose', '#ff8fa3'), ('Orange', '#ffb878'), ('Gold', '#f4df83'),
                 ('Green', '#8ce5a0'), ('Teal', '#86e1d4'), ('Blue', '#8ebeff'),
                 ('Violet', '#bca0ff'), ('Pink', '#ed9fe2'))


class ModernTheme(Theme):
    def builder_colors(self):
        return dict(self.colors, background=self.colors['field'], trace=self.colors['fg'])

    def timeline_colors(self):
        c = self.colors
        return dict(background=c['bg'], trace=c['accent'], accent=c['accent'], playhead=c['accent'],
                    grid=c['timeline_grid'], text=c['fg'], selection=c['selection'],
                    selection_text=c['selection_text'], muted=c['muted'], accent_text=c['accent_text'])

    def stylesheet(self, arrows):
        c, m = self.colors, self.metrics
        def has(name): return bool(arrows) and name in arrows
        def image(name):
            return f'image:url("{arrows[name]}");' if has(name) else ''

        # A styled indicator or arrow without its image would draw nothing, so without the image
        # the rule is left out and Qt paints its native one (an unwritable temp folder).
        chevron = '' if not has('chevron') else f'''
            QComboBox::down-arrow {{ width:9px; height:6px; {image('chevron')} }}
            QToolButton::menu-arrow {{ width:9px; height:6px; {image('chevron')} }}
            QToolButton::menu-indicator,QPushButton::menu-indicator {{ subcontrol-origin:padding;
                subcontrol-position:center right; right:4px; width:9px; height:6px; {image('chevron')} }}'''
        if has('chevron') and has('chevron_checked'):
            chevron += f'''
            QToolButton::menu-arrow:checked {{ {image('chevron_checked')} }}'''
        indicators = '' if not has('checkmark') else f'''
            QCheckBox::indicator,QListWidget::indicator,QTreeView::indicator {{
                width:14px; height:14px; border-radius:4px; border:1px solid {c['muted']}; background:{c['field']}; }}
            QCheckBox::indicator:checked,QListWidget::indicator:checked,QTreeView::indicator:checked {{
                background:{c['accent']}; border-color:{c['accent']}; {image('checkmark')} }}
            QCheckBox::indicator:disabled,QListWidget::indicator:disabled,QTreeView::indicator:disabled {{
                background:{c['active']}; border-color:{c['border']}; }}
            QCheckBox::indicator:checked:disabled,QListWidget::indicator:checked:disabled,QTreeView::indicator:checked:disabled {{
                background:{c['muted']}; }}'''
        # Inherit the control geometry and subcontrols, including the image-less spin arrows.
        return super().stylesheet(arrows) + f'''
            QWidget {{ selection-background-color:{c['selection']}; selection-color:{c['selection_text']}; }}
            QWidget#timeline {{ font-size:{self.fonts.timeline_px}px; }}
            QLabel#panel_title {{ color:{c['accent_text']}; }}
            QPushButton,QComboBox,QLineEdit,QAbstractSpinBox,QListWidget {{ border-radius:{m.radius}px; }}
            QPushButton#library_browser {{ border-top-right-radius:0; border-bottom-right-radius:0; }}
            QPushButton#library_effects {{ border-top-left-radius:0; border-bottom-left-radius:0; }}
            QPushButton#library_browser:checked,QPushButton#library_effects:checked {{
                background:{c['selection']}; color:{c['selection_text']}; }}
            QComboBox {{ padding-right:20px; }}
            QComboBox::drop-down {{ subcontrol-origin:padding; subcontrol-position:center right;
                width:20px; border:0; border-top-right-radius:{m.radius}px; border-bottom-right-radius:{m.radius}px; }}
            QComboBox QAbstractItemView {{ background:{c['field']}; border:1px solid {c['border']};
                border-radius:{m.radius}px; selection-background-color:{c['selection']}; selection-color:{c['selection_text']}; }}{chevron}{indicators}
            QAbstractSpinBox::up-button {{ border-top-right-radius:{m.radius}px; }}
            QAbstractSpinBox::down-button {{ border-bottom-right-radius:{m.radius}px; }}
            QPushButton:hover,QPushButton:checked {{ color:{c['fg']}; }}
            QPushButton:checked,QPushButton:focus,QComboBox:focus,QLineEdit:focus,QAbstractSpinBox:focus {{ border-color:{c['accent']}; }}
            QToolButton {{ border:1px solid transparent; border-radius:{m.radius}px; background:transparent; }}
            QToolButton[composerTransport="true"] {{ background:{c['field']}; border-color:{c['border']}; }}
            QToolButton[popupMode="1"],QToolButton[popupMode="2"],QPushButton[menuButton="true"] {{ padding-right:18px; }}
            QToolButton::menu-button {{ subcontrol-origin:padding; subcontrol-position:center right;
                width:16px; border:0; }}
            QToolButton:hover,QToolBar#app_bar QToolButton:hover {{ background:{c['active']}; border-color:{c['border']}; }}
            QToolButton:checked,QToolButton:pressed,QToolBar#app_bar QToolButton:checked {{
                background:{c['button_pressed']}; border-color:{c['accent']}; color:{c['fg']}; }}
            QTabBar {{ background:{c['active']}; border-radius:{m.radius}px; }}
            QTabBar::tab {{ background:transparent; border-radius:{m.radius}px; color:{c['fg']}; margin:1px; }}
            QTabBar::tab:selected,QListWidget::item:selected,QMenu::item:selected {{
                background:{c['selection']}; color:{c['selection_text']}; border-radius:{m.radius}px; }}
            QTabWidget::pane {{ border:0; }}
            QMenu::item {{ padding:4px 14px; }}
            QSlider::groove:horizontal {{ border-radius:{m.groove // 2}px; }}
            QSlider::handle:horizontal {{ border-radius:{m.handle // 2}px; }}
            QScrollBar:horizontal {{ height:{m.scroll_width}px; background:{c['bg']}; }}
            QScrollBar::handle:horizontal,QScrollBar::handle:vertical {{
                background:{c['muted']}; border-radius:{m.scroll_width // 2}px; }}
            QScrollBar::handle:horizontal {{ min-width:{m.scroll_min}px; }}
            QScrollBar::add-line,QScrollBar::sub-line {{ width:0; height:0; }}
            QScrollBar::add-page,QScrollBar::sub-page {{ background:transparent; }}
        '''


def _modern_theme(preset, mode):
    dark = mode == 'Dark'
    # accent_text: the accent for small captions (marker names, tempo flags, panel titles). It
    # reaches 4.5:1 on the window and on the timeline's flag fill; lines keep the plain accent.
    bg, field, border, fg, muted, accent, active, selection, accent_text = (
        ('#1e1e1e', '#2c2c2e', '#3a3a3c', '#f5f5f7', '#98989d', '#0a84ff', '#3a3a3c', '#0074e8', '#6cb4ff')
        if dark else
        ('#f5f5f7', '#ffffff', '#d2d2d7', '#1d1d1f', '#6e6e73', '#007aff', '#e8e8ed', '#0072ef', '#004fbd'))
    palette = PALETTES[preset]
    colors = dict(bg=bg, field=field, border=border, fg=fg, muted=muted, accent=accent, active=active,
                  accent_text=accent_text,
                  selection=selection, selection_text='#ffffff', button_fill=field, button_frame=border,
                  button_pressed=active, groove=border, timeline_grid=border, grid=border,
                  background=palette['background'], trace=palette['trace'], version_text=muted,
                  scope_fg=palette['fg'], scope_muted=palette['muted'],
                  record='#b32632', record_text='#ffffff', solo='#ffd166' if dark else '#805500',
                  clip_red='#ff6961' if dark else '#b42330', missing_text='#1e1e1e' if dark else '#ffffff',
                  search_highlight='#ffe082', search_text='#202020',
                  axis_x='#ff7979', axis_y='#8ee78e', axis_z='#80b8ff',
                  overlay_fore=fg, overlay_back=bg, overlay_accent=accent,
                  handle_line=border, handle_hover=accent)
    fonts = Fonts(family='', app_points=10, base_px=13, bar_px=11, timeline_small_px=10,
                  plot_points=7.5, plot_min_points=7.5)
    metrics = Metrics(radius=6, field_padding=5, tool_padding='2px 3px', tab_padding='6px 8px',
                      scene_selected_alpha=80, scene_picked_alpha=65, scene_alpha=50,
                      compact_tab_padding='5px 4px', handle=16, handle_margin=-6, scroll_width=8)
    lanes = CLASSIC_LANES if dark else ('#26733b', '#865a13', '#216ca0', '#a43d61', '#7744a0', '#26736a')
    return ModernTheme(MODERN, preset, _frozen(colors), fonts, metrics, lanes, CLASSIC_CLIPS, mode)


def relative_luminance(color):
    """WCAG 2 relative luminance of '#rrggbb' or an (r, g, b) tuple of 0-255 channels."""
    if isinstance(color, str):
        color = tuple(int(color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in (c / 255 for c in color[:3])]
    return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]


def contrast_ratio(a, b):
    """WCAG 2 contrast ratio of two colours (1 to 21)."""
    low, high = sorted((relative_luminance(a), relative_luminance(b)))
    return (high + .05) / (low + .05)


def needs_under_stroke(color, field):
    """A curve below WCAG's 3:1 for graphics against its field gets a contrasting under-stroke."""
    return contrast_ratio(color, field) < 3.


def resolve_mode(mode, scheme=None, window_luminance=None):
    """Modern's Dark or Light for a mode preference.

    Dark and Light are used as they are. System (or an unknown value) follows the OS colour
    scheme: scheme 'Dark' or 'Light'. For an unknown scheme the application palette's window
    colour decides: relative luminance below 0.5 is Dark, otherwise Light. With neither, the
    result is Dark, the same fallback theme_for uses for an unresolved mode.
    """
    if mode in ('Dark', 'Light'): return mode
    if scheme in ('Dark', 'Light'): return scheme
    if window_luminance is not None: return 'Dark' if window_luminance < .5 else 'Light'
    return 'Dark'


class VintageTheme(Theme):
    """Option 2 — Warm Beige Lab Oscilloscope (Photorealistic 3D Hardware Panel).

    Leaves buttons, toolbuttons, combos, sliders, tabs, checkboxes, and the app bar free of
    QSS background/border overrides so VintageProxyStyle paints them with procedural matte ABS
    plastic, brushed aluminum, recessed cutout wells, soft cast shadows, glowing 3D glass LED
    domes, and machined ribbed faders with calibrated tick scales.
    """
    def builder_colors(self):
        return dict(self.colors, background='#131c14', trace='#8ff08a')

    def timeline_colors(self):
        return dict(background='#161e15', trace='#5ce677', accent='#e8a838', playhead='#ff5a45',
                    grid='#2d372b', text='#dde8d6', selection='#1c6b36', selection_text='#ffffff',
                    muted='#9bb095', accent_text='#f5c45e')

    def record_stylesheet(self):
        # VintageProxyStyle draws the glowing red glass LED dome on composer_record when checked.
        return "QToolButton:checked { font-weight: bold; color: #7a120b; }"

    def compact_tabs_stylesheet(self):
        return ''

    def stylesheet(self, arrows):
        c, f, m = self.colors, self.fonts, self.metrics
        cap_up = 'qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #eee9dc, stop:0.16 #e1dbca, stop:0.86 #c8c0ac, stop:1 #b6ae99)'
        cap_hover = 'qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f5f1e6, stop:0.18 #eae4d4, stop:0.86 #d2cab7, stop:1 #c0b8a3)'
        cap_down = 'qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #b5ad98, stop:0.2 #c2baa6, stop:1 #d4cdb9)'
        bar_metal = 'qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #e3dfd4, stop:0.45 #d3cec2, stop:0.55 #c9c4b6, stop:1 #b8b2a3)'
        return f'''
            QWidget {{ color:{c['fg']}; font-family:"{f.family}"; font-size:{f.base_px}px;
                selection-background-color:{c['selection']}; selection-color:{c['selection_text']}; }}
            QWidget#timeline {{ font-size:{f.timeline_px}px; }}
            QLabel#brand {{ color:{c['accent_text']}; font-size:{f.brand_px}px; font-weight:bold; }}
            QLabel#panel_title {{ color:{c['accent_text']}; font-weight:bold; letter-spacing:1px; }}
            QLabel:disabled, QPushButton:disabled, QToolButton:disabled {{ color:{c['muted']}; }}
            QToolBar#app_bar {{ border:none; spacing:3px; padding:4px 18px; }}
            QToolBar#app_bar QToolButton {{ font-size:{f.bar_px}px; }}
            QToolButton#roll_notes, QToolButton#roll_transforms, QToolButton#roll_chords,
            QToolButton#roll_velocity, QToolButton#audio_gain_lane {{ font-size:10px; }}

            /* Menu bar and dropdown menus (warm instrument-paper for crisp contrast on macOS) */
            QMenuBar {{ background:{bar_metal}; border-bottom:1px solid #7c7565; }}
            QMenuBar::item {{ background:transparent; color:{c['fg']}; padding:{m.menubar_padding}; }}
            QMenuBar::item:selected {{ background:{c['selection']}; color:{c['selection_text']}; }}
            QMenu {{ background:#e4decb; color:{c['fg']};
                border-top:2px solid #f2eee2; border-left:2px solid #f2eee2;
                border-right:2px solid #595447; border-bottom:2px solid #595447; }}
            QMenu::item {{ background-color:transparent; color:{c['fg']}; padding:4px 16px; }}
            QMenu::item:disabled {{ color:{c['muted']}; }}
            QMenu::item:selected {{ background-color:{c['selection']}; color:{c['selection_text']}; }}
            QComboBox QAbstractItemView {{
                background-color:#e8e2d0; color:{c['fg']}; border:2px solid #595447; outline:none;
                selection-background-color:{c['selection']}; selection-color:{c['selection_text']}; }}
            QComboBox QAbstractItemView::item {{
                background-color:#e8e2d0; color:{c['fg']}; padding:3px 8px; }}
            QComboBox QAbstractItemView::item:selected, QComboBox QAbstractItemView::item:hover {{
                background-color:{c['selection']}; color:{c['selection_text']}; }}

            /* Recessed backlit dark-LCD glass readouts */
            QLabel#composer_position {{
                background:qradialgradient(cx:0.5, cy:0.5, radius:0.8, fx:0.5, fy:0.5, stop:0 #1d2d1f, stop:1 #0d150e);
                color:#8ff08a; padding:2px 8px; font-weight:bold;
                border-top:2px solid #3f3b32; border-left:2px solid #3f3b32;
                border-right:2px solid #f4f0e4; border-bottom:2px solid #f4f0e4; border-radius:3px; }}
            QLineEdit, QAbstractSpinBox, QListWidget, QTreeView, QTextEdit, QPlainTextEdit {{
                background:qradialgradient(cx:0.5, cy:0.5, radius:0.85, fx:0.5, fy:0.45, stop:0 #1c2b1e, stop:1 #0e160f);
                color:#8ff08a; border-radius:3px; padding:3px 5px;
                border-top:2px solid #3f3b32; border-left:2px solid #3f3b32;
                border-right:2px solid #f4f0e4; border-bottom:2px solid #f4f0e4; }}
            QLineEdit:focus, QAbstractSpinBox:focus, QListWidget:focus {{
                border-top:2px solid #1e7a3d; border-left:2px solid #1e7a3d; }}
            QLineEdit:disabled, QAbstractSpinBox:disabled, QListWidget:disabled {{
                background:#232a22; color:#779c76; }}
            QListWidget QWidget {{ background:transparent; color:#8ff08a; }}
            QListWidget QLabel {{ background:transparent; color:#8ff08a; }}
            QListWidget QLabel:disabled {{ color:#6d8c6b; }}
            QListWidget QPushButton {{ color:{c['fg']}; }}
            QListWidget::item:selected {{ background:{c['selection']}; color:{c['selection_text']}; }}
            QListWidget[customRows="true"]::item, QListWidget[customRows="true"]::item:selected,
            QListWidget[customRows="true"]::item:hover {{ color:transparent; }}
            QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
                subcontrol-origin:border; width:{m.spin_button}px; background:{cap_up};
                border-left:1px solid #6e6859; border-top:1px solid #f5f2e8;
                border-right:1px solid #595447; border-bottom:1px solid #595447; }}
            QAbstractSpinBox::up-button {{ subcontrol-position:top right; }}
            QAbstractSpinBox::down-button {{ subcontrol-position:bottom right; }}
            QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background:{cap_hover}; }}
            QAbstractSpinBox::up-button:pressed, QAbstractSpinBox::down-button:pressed {{ background:{cap_down}; }}
            {self.arrow_rules(arrows)}

            /* Chunky beveled scrollbars */
            QScrollBar:vertical {{ width:{m.scroll_width}px; background:#b5ad98; border-left:1px solid #6e6859; }}
            QScrollBar:horizontal {{ height:{m.scroll_width}px; background:#b5ad98; border-top:1px solid #6e6859; }}
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
                background:{cap_up}; min-height:{m.scroll_min}px; min-width:{m.scroll_min}px;
                border-top:2px solid #f2eee2; border-left:2px solid #f2eee2;
                border-right:2px solid #595447; border-bottom:2px solid #595447; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
        '''


def _vintage_theme(preset):
    palette = PALETTES[preset]
    bg, field, border, fg, muted = '#cdc6b4', '#e8e2d0', '#7c7565', '#1e1c17', '#4a453a'
    accent, active, selection, accent_text = '#1e7a3d', '#bfb7a2', '#1c6b36', '#165a2d'
    colors = dict(bg=bg, field=field, border=border, fg=fg, muted=muted, accent=accent, active=active,
                  accent_text=accent_text,
                  selection=selection, selection_text='#ffffff', button_fill='#dfd9c8', button_frame='#6e6859',
                  button_pressed='#bfb7a2', groove='#2b2923', timeline_grid='#323c30', grid='#323c30',
                  background=palette['background'], trace=palette['trace'], version_text=muted,
                  scope_fg=palette['fg'], scope_muted=palette['muted'],
                  record='#b82424', record_text='#ffffff', solo='#e8a830',
                  clip_red='#d93829', missing_text='#ffffff',
                  search_highlight='#ffe082', search_text='#202020',
                  axis_x='#ff7979', axis_y='#8ee78e', axis_z='#80b8ff',
                  overlay_fore='#f2eedc', overlay_back='#3a362d', overlay_accent='#1c6b36',
                  handle_line='#6e6859', handle_hover='#1e7a3d')
    fonts = Fonts(family='JetBrains Mono', app_points=10, base_px=12, brand_px=18, bar_px=10,
                  version_px=10, timeline_px=11, timeline_small_px=9,
                  plot_points=7.5, plot_min_points=7.5)
    metrics = Metrics(border=2, radius=3, field_padding=4, tool_padding='2px 4px',
                      tab_padding='6px 10px', compact_tab_padding='5px 4px',
                      groove=7, handle=14, handle_margin=-6, scroll_width=12,
                      corner_radius=3)
    return VintageTheme(VINTAGE, preset, _frozen(colors), fonts, metrics, CLASSIC_LANES, CLASSIC_CLIPS, '')


@lru_cache(maxsize=None)
def theme_for(style=DEFAULT_STYLE, preset='Green monochrome', mode='Dark'):
    """Use a resolved Dark/Light mode. An unresolved (System) or unknown mode falls back to Dark,
    as resolve_mode does without a scheme or window colour; windows resolve System first."""
    if style not in STYLES: style = DEFAULT_STYLE
    if preset not in PALETTES: preset = 'Green monochrome'
    if style == MODERN:
        return _modern_theme(preset, resolve_mode(mode))
    if style == VINTAGE:
        return _vintage_theme(preset)
    p = PALETTES[preset]; b = button_colors(p)
    colors = dict(CLASSIC_FIXED, **p)
    colors.update(selection=p['active'], selection_text=p['accent'], scope_fg=p['fg'], scope_muted=p['muted'],
                  accent_text=p['accent'])
    colors.update(groove=p.get('groove', p['field']), button_fill=b['fill'], button_frame=b['frame'],
                  button_pressed=b['pressed'], timeline_grid=p['grid'] if p['grid'] != p['field'] else p['border'])
    return Theme(style, preset, _frozen(colors), lane_colors=CLASSIC_LANES, clip_colors=CLASSIC_CLIPS)


_current = None


def current():
    """Fallback for standalone widgets (Classic green before the first window)."""
    return _current if _current is not None else theme_for()


def for_widget(widget):
    """Use the owning window's theme, or the current theme for standalone widgets."""
    return getattr(widget, "theme", None) or current()


def set_current(theme):
    global _current
    _current = theme
