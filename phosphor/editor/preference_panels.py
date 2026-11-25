"""Appearance and Signal output editors, shared by the side panels and the Preferences dialogs.

Each editor shows one preferences dict: load() fills it without emitting, values()
reads it back, and changed(values) fires on every edit. Callers decide whether an
edit is a preview (dialogs) or applied and saved at once (side panels).
"""
from PySide6.QtCore import Signal, QSignalBlocker, QSize
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox,
                               QTabWidget, QSizePolicy, QLineEdit, QPushButton, QFileDialog, QMessageBox)

from phosphor.display import theme as themes
from phosphor.display.crt_theme import PALETTES
from phosphor.display.glow_trail import VISUAL_DEFAULTS, VISUAL_RANGES
from phosphor.display.phosphor import BEAM_PROFILES, PHOSPHOR_CUSTOM, PHOSPHOR_NAMES, PHOSPHOR_OFF, phosphor_profile
from phosphor.display.trace_interpolation import INTERPOLATION_FACTORS
from phosphor.editor.phosphor_editor import PhosphorEditor
from phosphor.engine.corner_dwell import MAX_DWELL_US
from phosphor.engine.switch_sweep import MAX_SWEEP_MS
from phosphor.engine.route_plan import ROUTE_MODES
from phosphor.storage.signal_preferences import FOLLOW_DEVICE, OUTPUT_RATES, SYSTEM_DEFAULT_DEVICE
from phosphor.storage.signal_preferences import MAX_REDRAWS
import sys

from phosphor.editor import wheel_mapping, scroll_mapping
from phosphor.editor.panel_widgets import FlowRow

BEAM_SLIDERS = (('trace_thickness', 'Trace thickness (px)'), ('glow_size', 'Glow size (px radius)'),
                ('glow_intensity', 'Glow intensity (×)'), ('glow_decay', 'Glow decay time (seconds, line trace only)'),
                ('beam_brightness', 'Beam-speed brightness (0 = Classic even trace)'))
PHOSPHOR_SLIDERS = (('beam_intensity', 'Phosphor intensity (×)'), ('phosphor_grain', 'Phosphor grain (0 = smooth)'),
                    ('phosphor_shimmer', 'Beam shimmer (0 = steady)'))


def _wrapping(label):
    """Wrap and size by the label's real width.

    A plain word-wrapped QLabel guesses a narrow width for its size hint, so short
    captions report two lines; the tab widget then sizes every page for the tallest
    guess (45 px too tall for Beam at 1000×700 with macOS fonts).
    """
    label.setWordWrap(True)
    policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred); policy.setHeightForWidth(True)
    label.setSizePolicy(policy)


class _FittedTabs(QTabWidget):
    """A tab widget whose height hint is its tallest page at the current width.

    QTabWidget sizes pages from their plain size hints, which ignore word wrapping
    at the real width; the side panels then scroll for height nobody needs.
    """
    def sizeHint(self):
        hint = super().sizeHint(); current = self.currentWidget()
        width = current.width() if current is not None else 0
        if width <= 0 or current.height() <= 0: return hint
        pages = [self.widget(i) for i in range(self.count())]
        tallest = max(page.heightForWidth(width) if page.hasHeightForWidth() else page.sizeHint().height() for page in pages)
        return QSize(hint.width(), max(tallest, *(page.minimumSizeHint().height() for page in pages))+self.height()-current.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width(): self.updateGeometry()


def _page():
    # Explicit margins: the macOS style's 12/20 px page margins overflowed 1000×700.
    page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(5); layout.setContentsMargins(10, 8, 10, 8)
    return page, layout


class AppearanceEditor(QWidget):
    """Preview-only display settings in four tabs: Display, Beam, Phosphor and Decay."""
    changed = Signal(object)

    def __init__(self, slider_class, values, compact=False):
        super().__init__()
        self.custom = dict(values['phosphor_custom'])
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = _FittedTabs(); self.tabs.setObjectName('appearance_tabs'); layout.addWidget(self.tabs)
        # 6 px sides: the five tabs (240 px) then fit the narrowest panel on macOS (280 px, 270 inside, less 22 px of margins).
        theme = themes.theme_for(values.get('style', themes.DEFAULT_STYLE), values['palette'])
        self.tabs.setStyleSheet(theme.compact_tabs_stylesheet())
        display, column = _page(); beam_page, beam = _page(); phosphor_page, side = _page(); decay_page, decay = _page()
        for page, title in ((display, 'Display'), (beam_page, 'Beam'), (phosphor_page, 'Phosphor'), (decay_page, 'Decay')):
            self.tabs.addTab(page, title)
        self.palette = QComboBox(); self.palette.addItems(list(PALETTES)); self.palette.setObjectName('palette')
        self.style_choice = QComboBox(); self.style_choice.addItems(list(themes.STYLES)); self.style_choice.setObjectName('style')
        self.style_choice.setToolTip('The look of the controls. The scope keeps its own display settings in every style.')
        self.grid = QCheckBox('Grid lines'); self.grid.setObjectName('show_grid')
        self.density = QSpinBox(); self.density.setRange(2, 64); self.density.setObjectName('grid_divisions')
        self.density_label = QLabel('Grid divisions per axis')
        self.scan = QCheckBox('CRT scanlines'); self.scan.setObjectName('crt_scanlines')
        self.glow = QCheckBox('GPU trace glow'); self.glow.setObjectName('crt_glow')
        self.interpolation = QComboBox(); self.interpolation.addItems(list(INTERPOLATION_FACTORS)); self.interpolation.setObjectName('trace_interpolation')
        self.display_rate = QComboBox(); self.display_rate.addItems(['Match monitor', '60 Hz']); self.display_rate.setObjectName('display_rate')
        self.beam_profile = QComboBox(); self.beam_profile.addItems(BEAM_PROFILES); self.beam_profile.setObjectName('beam_profile')
        for text, widget in (('Color preset', self.palette), ('', self.grid), (self.density_label, self.density), ('', self.scan),
                             ('', self.glow), ('Display interpolation', self.interpolation)):
            if text: column.addWidget(text if isinstance(text, QLabel) else QLabel(text))
            column.addWidget(widget)
        column.addWidget(QLabel('Interpolation smooths between samples and adds no audio detail; cubic curves can '
                                'overshoot. Appearance affects the preview only, never the audio.'))
        column.addStretch(1)
        # One compact form row keeps Display within the 1000x700 panel budget.
        rate_label = QLabel('Display rate'); rate_label.setProperty('inlineCaption', True)  # The sidebar wraps other labels to width 0.
        self.display_rate.setToolTip('Match monitor draws at the refresh rate of the screen showing the scope. '
                                     'The phosphor looks the same at any rate.')
        rate_row = QHBoxLayout(); rate_row.addWidget(rate_label); rate_row.addWidget(self.display_rate, 1)
        column.insertLayout(0, rate_row)
        self.mode_row = QWidget()
        mode_row = QHBoxLayout(self.mode_row); mode_row.setContentsMargins(0, 0, 0, 0)
        mode_label = QLabel('Mode'); mode_label.setProperty('inlineCaption', True)
        mode_label.setMinimumWidth(rate_label.sizeHint().width())
        self.mode_choice = QComboBox(); self.mode_choice.setObjectName('style_mode')
        self.mode_choice.addItems(['Dark', 'Light', 'System'])
        mode_row.addWidget(mode_label); mode_row.addWidget(self.mode_choice, 1)
        column.insertWidget(0, self.mode_row)
        style_label = QLabel('Style'); style_label.setProperty('inlineCaption', True)
        style_label.setToolTip(self.style_choice.toolTip())
        style_label.setMinimumWidth(rate_label.sizeHint().width())  # Both boxes start at one x.
        style_row = QHBoxLayout(); style_row.addWidget(style_label); style_row.addWidget(self.style_choice, 1)
        column.insertLayout(0, style_row)
        beam.addWidget(QLabel('Beam profile')); beam.addWidget(self.beam_profile)
        self.sliders, self.slider_labels = {}, {}
        for key, label in BEAM_SLIDERS: self.add_slider(slider_class, beam, key, label)
        beam.addWidget(QLabel('Decay 0 = no afterimage. Double-click a slider to reset it.'))
        beam.addStretch(1)
        self.phosphor = QComboBox(); self.phosphor.addItems(list(PHOSPHOR_NAMES)); self.phosphor.setObjectName('phosphor')
        side.addWidget(QLabel('Phosphor display')); side.addWidget(self.phosphor)
        for key, label in PHOSPHOR_SLIDERS: self.add_slider(slider_class, side, key, label)
        side.addWidget(QLabel('Decay shows and edits how long the selected phosphor glows.'))
        side.addStretch(1)
        self.editor = PhosphorEditor(theme, compact); self.editor.setObjectName('phosphor_editor')
        decay.addWidget(self.editor, 1)
        caption = QLabel('Light from one beam pass. Drag the flash and afterglow points or type values; '
                         'editing a preset switches to Custom.')
        caption.setObjectName('phosphor_caption'); decay.addWidget(caption)
        for label in self.findChildren(QLabel): _wrapping(label)
        self.load(values)
        self.editor.edited.connect(self.profile_edited)
        self.grid.toggled.connect(self.grid_toggled)
        for signal in (self.phosphor.currentTextChanged, self.palette.currentTextChanged, self.sliders['beam_intensity'].edited):
            signal.connect(lambda *args: self.refresh_editor())
        self.style_choice.currentTextChanged.connect(self.show_mode)
        for signal in (self.style_choice.currentTextChanged, self.mode_choice.currentTextChanged, self.palette.currentTextChanged, self.grid.toggled, self.density.valueChanged, self.scan.toggled,
                       self.glow.toggled, self.interpolation.currentTextChanged, self.phosphor.currentTextChanged,
                       self.beam_profile.currentTextChanged, self.display_rate.currentTextChanged, *[slider.edited for slider in self.sliders.values()]):
            signal.connect(lambda *args: self.changed.emit(self.values()))

    def add_slider(self, slider_class, layout, key, label):
        self.slider_labels[key] = QLabel(label); layout.addWidget(self.slider_labels[key])
        slider = slider_class(VISUAL_DEFAULTS[key], *VISUAL_RANGES[key], curve=key == 'beam_intensity')
        # 28 px like the side panels' other sliders; macOS otherwise pads the row to 35.
        slider.setObjectName(key); slider.setFixedHeight(28); self.sliders[key] = slider; layout.addWidget(slider)
        if key in ('trace_thickness', 'glow_size'):
            tip = 'Sizes scale with the scope: these are pixels when its short side is 720.'
            self.slider_labels[key].setToolTip(tip); slider.setToolTip(tip)

    def values(self):
        return dict(style=self.style_choice.currentText(), style_mode=self.mode_choice.currentText(),
                    palette=self.palette.currentText(), show_grid=self.grid.isChecked(), grid_divisions=self.density.value(),
                    crt_scanlines=self.scan.isChecked(), crt_glow=self.glow.isChecked(),
                    trace_interpolation=self.interpolation.currentText(), phosphor=self.phosphor.currentText(),
                    beam_profile=self.beam_profile.currentText(), phosphor_custom=dict(self.custom), display_rate=self.display_rate.currentText(),
                    **self.export_preferences, **self.wheel_preferences, **{key: slider.value for key, slider in self.sliders.items()})

    def load(self, values):
        """Show values without emitting changed."""
        self.export_preferences = {key: values[key] for key in ('export_format', 'export_depth', 'export_rate', 'export_range', 'ffmpeg_path', 'metronome_level')}
        self.wheel_preferences = wheel_mapping.validated_mapping(values)
        widgets = (self.style_choice, self.mode_choice, self.palette, self.grid, self.density, self.scan, self.glow, self.interpolation, self.phosphor, self.beam_profile, self.display_rate)
        blockers = [QSignalBlocker(widget) for widget in widgets]
        self.style_choice.setCurrentText(values.get('style', themes.DEFAULT_STYLE))
        self.mode_choice.setCurrentText(values.get('style_mode', themes.DEFAULT_MODE))
        self.show_mode(self.style_choice.currentText())
        self.palette.setCurrentText(values['palette']); self.grid.setChecked(values['show_grid'])
        self.display_rate.setCurrentText(values['display_rate'])
        self.density.setValue(values['grid_divisions']); self.scan.setChecked(values['crt_scanlines'])
        self.glow.setChecked(values['crt_glow']); self.interpolation.setCurrentText(values['trace_interpolation'])
        self.phosphor.setCurrentText(values['phosphor']); self.beam_profile.setCurrentText(values['beam_profile'])
        for key, slider in self.sliders.items():
            if slider.value != values[key]: slider.setValue(values[key])
        self.custom = dict(values['phosphor_custom'])
        del blockers
        self.grid_toggled(self.grid.isChecked()); self.refresh_editor()

    def show_mode(self, style):
        self.mode_row.setVisible(style == themes.MODERN)

    def grid_toggled(self, enabled):
        self.density.setEnabled(enabled); self.density_label.setEnabled(enabled)

    def set_theme(self, theme):
        self.tabs.setStyleSheet(theme.compact_tabs_stylesheet())
        self.editor.set_theme(theme)

    def refresh_editor(self):
        name = self.phosphor.currentText()
        palette = PALETTES[self.palette.currentText()]
        profile = phosphor_profile(name, palette['trace'], self.custom)
        self.editor.set_profile(profile or self.custom, self.sliders['beam_intensity'].value, profile is not None)
        # Phosphor decay replaces the line trace's glow afterimage.
        line = name == PHOSPHOR_OFF
        for key in ('glow_decay',): self.sliders[key].setEnabled(line); self.slider_labels[key].setEnabled(line)
        for key in ('phosphor_grain', 'phosphor_shimmer'): self.sliders[key].setEnabled(not line); self.slider_labels[key].setEnabled(not line)

    def profile_edited(self, profile):
        self.custom = dict(profile)
        if self.phosphor.currentText() != PHOSPHOR_CUSTOM:
            blocker = QSignalBlocker(self.phosphor); self.phosphor.setCurrentText(PHOSPHOR_CUSTOM); del blocker
        self.refresh_editor(); self.changed.emit(self.values())


class ControlsPreferences(QWidget):
    """Preferences › Controls (UI2): Keyboard shortcuts and Scroll & trackpad.

    The Scroll & trackpad page follows the dialog's preview, OK and Cancel like the other
    pages (values(), load(), changed). Keyboard shortcuts edit the Window's bindings and save
    at once; that tab is shown only when a window is given.
    """
    changed = Signal(object)

    def __init__(self, values, window=None, slider_class=None):
        super().__init__()
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(); self.tabs.setObjectName('controls_tabs'); outer.addWidget(self.tabs)
        self.shortcut_editor = None
        if window is not None:
            from phosphor.editor.shortcut_editor import ShortcutEditor
            self.shortcut_editor = ShortcutEditor(window); self.tabs.addTab(self.shortcut_editor, 'Keyboard shortcuts')
        page = QWidget(); layout = QVBoxLayout(page); self.tabs.addTab(page, 'Scroll & trackpad')
        caption = QLabel('A mouse wheel and a trackpad have separate sensitivities. On a trackpad, two fingers move the '
                         'Composer roll both ways and follow the fingers; pinch zooms time around the pointer and '
                         f'{"⌥" if sys.platform == "darwin" else "Alt"}+pinch changes row height (the Builder canvas zooms).')
        _wrapping(caption); layout.addWidget(caption)
        self.sensitivity = {}
        for key, label, default in (('wheel_sensitivity', 'Mouse wheel sensitivity (×)', scroll_mapping.DEFAULT_WHEEL_SENSITIVITY),
                                    ('trackpad_sensitivity', 'Trackpad sensitivity (×)', scroll_mapping.DEFAULT_TRACKPAD_SENSITIVITY)):
            layout.addWidget(QLabel(label))
            if slider_class is not None: control = slider_class(default, *scroll_mapping.SENSITIVITY_RANGE, curve='log')
            else:
                control = QDoubleSpinBox(); control.setRange(*scroll_mapping.SENSITIVITY_RANGE); control.setSingleStep(.05); control.setDecimals(2)
            control.setObjectName(key); self.sensitivity[key] = control; layout.addWidget(control)
        heading = QLabel('Wheel keys: choose physical keys for wheel gestures. Composer actions must use different '
                         'modifiers; builder zoom is independent. To swap keys, choose an unused modifier first.')
        _wrapping(heading); layout.addWidget(heading)
        self.boxes = {}
        for key, label in zip(wheel_mapping.ACTIONS, wheel_mapping.LABELS):
            layout.addWidget(QLabel(label))
            box = QComboBox(); box.setObjectName(key)
            for value in wheel_mapping.choices(): box.addItem(wheel_mapping.physical_label(value), value)
            self.boxes[key] = box; layout.addWidget(box)
        layout.addWidget(QLabel('Metronome level (monitoring only)'))
        self.metronome_level = QDoubleSpinBox(); self.metronome_level.setRange(0., 1.); self.metronome_level.setSingleStep(.05)
        layout.addWidget(self.metronome_level)
        self.message = QLabel(); _wrapping(self.message); layout.addWidget(self.message); layout.addStretch(1)
        self.load(values)
        for box in self.boxes.values(): box.currentIndexChanged.connect(self.edited)
        self.metronome_level.valueChanged.connect(self.edited)
        for control in self.sensitivity.values():
            (control.edited if hasattr(control, 'edited') else control.valueChanged).connect(self.edited)

    def sensitivity_value(self, key):
        control = self.sensitivity[key]
        return float(control.value if hasattr(control, 'edited') else control.value())

    def values(self):
        return dict({key: box.currentData() for key, box in self.boxes.items()}, metronome_level=self.metronome_level.value(),
                    **{key: self.sensitivity_value(key) for key in scroll_mapping.SENSITIVITY_KEYS})

    def load(self, values):
        self.accepted = wheel_mapping.validated_mapping(values)
        self.accepted['metronome_level'] = values.get('metronome_level', .3)
        for key, default in zip(scroll_mapping.SENSITIVITY_KEYS, (scroll_mapping.DEFAULT_WHEEL_SENSITIVITY, scroll_mapping.DEFAULT_TRACKPAD_SENSITIVITY)):
            self.accepted[key] = scroll_mapping.validated_sensitivity(values.get(key, default), default)
        blockers = [QSignalBlocker(widget) for widget in (self.metronome_level, *self.boxes.values(), *self.sensitivity.values())]
        self.metronome_level.setValue(self.accepted['metronome_level'])
        for key, box in self.boxes.items(): box.setCurrentIndex(box.findData(self.accepted[key]))
        for key, control in self.sensitivity.items():
            if (control.value if hasattr(control, 'edited') else control.value()) != self.accepted[key]: control.setValue(self.accepted[key])
        del blockers

    def edited(self, *args):
        values = self.values()
        try: mapping = wheel_mapping.validated_mapping(values)
        except ValueError as error:
            self.load(self.accepted); self.message.setText(str(error)); return
        self.accepted = dict(values, **mapping)
        self.message.clear(); self.changed.emit(self.accepted)


class ExportPreferences(QWidget):
    def __init__(self, path):
        super().__init__()
        layout = QVBoxLayout(self)
        label = QLabel('ffmpeg location (empty uses PATH). Download: https://ffmpeg.org/download.html')
        _wrapping(label); layout.addWidget(label)
        self.path = QLineEdit(path); layout.addWidget(self.path)
        row = QHBoxLayout(); layout.addLayout(row)
        browse = QPushButton('Browse…'); test = QPushButton('Test'); row.addWidget(browse); row.addWidget(test)
        browse.clicked.connect(self.browse); test.clicked.connect(self.test)
        self.result = QLabel(); _wrapping(self.result); layout.addWidget(self.result); layout.addStretch(1)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Locate ffmpeg', self.path.text())
        if path: self.path.setText(path)

    def test(self):
        import subprocess
        from phosphor.engine.video_export import find_ffmpeg
        path = find_ffmpeg(self.path.text().strip())
        if path is None: self.result.setText('ffmpeg was not found or did not pass ffmpeg -version.'); return
        try:
            version = subprocess.run([path, '-version'], capture_output=True, text=True, timeout=5)
            self.result.setText(path + '\n' + version.stdout.splitlines()[0])
        except (OSError, subprocess.SubprocessError) as error: self.result.setText(str(error))


class DeviceCombo(QComboBox):
    """The output device choice (S2): System default, the connected devices, and the saved
    choice marked "not connected" when it is missing. The list is read again each time the
    popup opens, so devices plugged in since are there."""
    def __init__(self, provider):
        super().__init__()
        self.provider = provider

    def fill(self, current=None):
        current = self.currentData() if current is None else current
        try: names = list(self.provider())
        except Exception: names = []
        blocker = QSignalBlocker(self)
        self.clear(); self.addItem('System default', SYSTEM_DEFAULT_DEVICE)
        for name in names: self.addItem(name, name)
        if current and current not in names: self.addItem(f'{current} (not connected)', current)
        self.setCurrentIndex(max(0, self.findData(current)))
        del blocker

    def showPopup(self):
        self.fill(); super().showPopup()


class SignalOutputEditor(QWidget):
    """Hardware signal calibration: corner dwell, shape redraw, route minimizing and switch sweep."""
    changed = Signal(object)

    def __init__(self, slider_class, values, device_names=None):
        super().__init__()
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(5)
        device_label = QLabel('Output device'); layout.addWidget(device_label)
        self.device = DeviceCombo(device_names or (lambda: ())); self.device.setObjectName('output_device')
        layout.addWidget(self.device)
        device_tip = ('Where playback, Live MIDI, cycle audition and file previews play. System default follows '
                      "the system's output, also while playing. A chosen device that is not connected plays on "
                      'the system default until it is back, and the status line says so.')
        device_label.setToolTip(device_tip); self.device.setToolTip(device_tip)
        dwell_label = QLabel('Hardware corner dwell (microseconds per sharp turn)'); layout.addWidget(dwell_label)
        self.dwell = slider_class(0., 0., MAX_DWELL_US); self.dwell.setObjectName('corner_dwell_us'); layout.addWidget(self.dwell)
        dwell_tip = ('Source cycle timing is preserved; timbre can change. Total dwell is capped at 25%. High notes or many '
                     'corners may shorten the requested hold. One sample is 20.83 µs at 48 kHz (10.42 µs at 96 kHz); '
                     'short holds may receive no samples. More dwell brightens corners and speeds up the edges. It may not '
                     'correct DAC filtering or phase errors. Start at 0 and increase gradually.')
        dwell_label.setToolTip(dwell_tip); self.dwell.setToolTip(dwell_tip)
        row = FlowRow(); layout.addLayout(row)  # The count moves under the box in a narrow panel (UI2).
        self.redraw = QCheckBox('Shape redraw (experimental)'); self.redraw.setObjectName('shape_redraw'); row.addWidget(self.redraw)
        self.redraw.setToolTip('Validated by the user on Mac; automated native checks passed on Windows.')
        self.count = QSpinBox(); self.count.setObjectName('shape_redraw_count'); self.count.setRange(1, MAX_REDRAWS); row.addWidget(self.count)
        self.redraw.toggled.connect(self.count.setEnabled)
        redraw_tip = ('Only applies with multiple drawable shapes; motion guides do not count. Repeats fit the existing '
                      'source cycle. Fewer samples per trace; shaped timbre can change.')
        self.redraw.setToolTip(self.redraw.toolTip()+' '+redraw_tip); self.count.setToolTip(redraw_tip)
        layout.addWidget(QLabel('Total draws per shape before moving to the next (1 = unchanged).'))
        layout.addWidget(QLabel('Route minimizing'))
        self.route = QComboBox(); self.route.setObjectName('route_mode'); self.route.addItems(ROUTE_MODES); layout.addWidget(self.route)
        self.route.setToolTip("Each shape's own beam path, for every shape type. A shape keeps its original route if a "
                              'plan is not shorter. Scene object order is unchanged.')
        layout.addWidget(QLabel('Shortest jumps visits separate parts nearest-first; Shortest total path also retraces '
                                'as little as possible.'))
        sweep_label = QLabel('Switch sweep (0–50 milliseconds; 0 = hard switch)'); layout.addWidget(sweep_label)
        self.sweep = slider_class(0., 0., MAX_SWEEP_MS); self.sweep.setObjectName('switch_sweep_ms'); layout.addWidget(self.sweep)
        sweep_tip = ('When an object switches off or on (Mute, Solo, an On/Off lane, a muted clip), its Path Trace '
                     'sweeps to zero before it leaves the beam path and back up when it returns, so the beam does '
                     'not jump (an audible click). Transform switches crossfade over the same time.')
        sweep_label.setToolTip(sweep_tip); self.sweep.setToolTip(sweep_tip)
        rate_label = QLabel('Output sample rate'); layout.addWidget(rate_label)
        self.rate = QComboBox(); self.rate.setObjectName('output_rate')
        self.rate.addItem('Follow device', FOLLOW_DEVICE)
        for rate in OUTPUT_RATES: self.rate.addItem(f'{rate / 1000:g} kHz', rate)
        layout.addWidget(self.rate)
        rate_tip = ('The rate playback opens the audio device at, from the next Play. Follow device uses the '
                    "device's own rate, so the system does not resample. If the device can't open the chosen rate, "
                    'playback uses the device rate and the status line says so. Scenes and exports are unchanged; '
                    'export has its own rate.')
        rate_label.setToolTip(rate_tip); self.rate.setToolTip(rate_tip)
        layout.addWidget(QLabel('Changes generated X/Y audio, Live MIDI shaping and processed WAV exports; source audio '
                                'and system capture are unchanged. Saved per machine, not in scenes. Hover a control '
                                'for details.'))
        layout.addStretch(1)
        for label in self.findChildren(QLabel): _wrapping(label)
        self.load(values)
        for signal in (self.dwell.edited, self.redraw.toggled, self.count.valueChanged, self.route.currentTextChanged,
                       self.sweep.edited, self.rate.currentIndexChanged, self.device.currentIndexChanged):
            signal.connect(lambda *args: self.changed.emit(self.values()))

    def values(self):
        return dict(corner_dwell_us=self.dwell.value, shape_redraw=self.redraw.isChecked(),
                    shape_redraw_count=self.count.value(), route_mode=self.route.currentText(),
                    switch_sweep_ms=self.sweep.value, output_rate=self.rate.currentData(),
                    output_device=self.device.currentData() or SYSTEM_DEFAULT_DEVICE)

    def load(self, values):
        blockers = [QSignalBlocker(widget) for widget in (self.redraw, self.count, self.route, self.rate)]
        if self.dwell.value != values['corner_dwell_us']: self.dwell.setValue(values['corner_dwell_us'])
        if self.sweep.value != values['switch_sweep_ms']: self.sweep.setValue(values['switch_sweep_ms'])
        self.redraw.setChecked(values['shape_redraw']); self.count.setValue(values['shape_redraw_count'])
        self.count.setEnabled(values['shape_redraw']); self.route.setCurrentText(values['route_mode'])
        self.rate.setCurrentIndex(max(0, self.rate.findData(values['output_rate'])))
        self.device.fill(values['output_device'])
        del blockers
