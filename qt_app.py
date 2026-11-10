"""Qt editor with native Metal (macOS) and Direct3D11 (Windows) rendering."""
import argparse
import gc
from dataclasses import replace
from pathlib import Path
from collections import deque
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid

HERE = Path(__file__).resolve().parent
for directory in ('.audio-deps', '.qt-deps'):
    if (HERE/directory).is_dir(): sys.path.append(str(HERE/directory))

import numpy as np
from PySide6.QtCore import Qt, QTimer, QSignalBlocker, Signal, QPoint, QEvent
from PySide6.QtGui import QColor, QFont, QFontDatabase, QAction, QSurfaceFormat, QPainter, QKeySequence, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QTabWidget, QScrollArea,
    QListWidget, QSlider, QLineEdit, QFileDialog, QMessageBox, QDialog, QCheckBox,
    QDialogButtonBox, QTextBrowser, QSplitter, QSizePolicy)
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from qt_platform import configure_graphics, graphics_api, SYSTEM_AUDIO_AVAILABLE, SYSTEM_AUDIO_NOTICE
from audio_preview import AudioPreview
from app_paths import USER_DIR, relaunch_command
from app_files import (read_preferences, DEFAULT_PREFERENCES, validated_preferences,
                       PREFERENCES_PATH, atomic_json, read_scene)
from signal_engine import render, select_signal, source_duration, export_wav, projected_solid, Shape,posed_shapes,is_motion_guide,world_motion_route,validate,planned_path,scene_shapes,MAX_SPIN
from custom_shape import Drawing, CUSTOM_OBJECT, compiled
from model_object import MODEL_OBJECT, MAX_MODEL_EDGES, load_obj
from svg_import import load_svg
from midi_synth import MIDI_SOURCE, PRESETS
from score_source import SOURCES, MIDI_FILE_SOURCE, file_tempo, score_arrays
from solids import SOLIDS
from vector_text import TEXT_OBJECT, MAX_TEXT_LENGTH
from scene_path import TRAVEL_MODES
from control_values import rotation_position, rotation_value, parse_rotation, format_rotation, integer_degrees
from crt_theme import PALETTES
from branding import APP_NAME, logo_path
from qt_scene import Scene
from qt_scope import GpuScope
from time_trace import TimeTrace
from signal_preferences import (SIGNAL_PREFERENCES_PATH, DEFAULT_SIGNAL_PREFERENCES,
    validated_signal_preferences,read_signal_preferences,MAX_REDRAWS)
from corner_dwell import MAX_DWELL_US
from system_audio import SystemAudio
from trace_interpolation import INTERPOLATION_FACTORS, interpolate_trace
from phosphor import BEAM_GAUSSIAN, BEAM_PROFILES, PHOSPHORS, PHOSPHOR_OFF, beam_uniforms, deposit_vertices, gaussian_beam, new_sample_count, phosphor_uniforms
from qt_phosphor import PhosphorLayer
from glow_trail import VISUAL_DEFAULTS, VISUAL_RANGES
from audio_help import AUDIO_TROUBLESHOOTING, MACOS_AUDIO_TROUBLESHOOTING
from qt_geometry import BEAM_VERTEX, stroke_triangles
from build_info import VERSION, BUILD_DATE, PROJECT_DATE, TRANSPARENCY, INSTRUCTIONS


class ResetSlider(QSlider):
    resetRequested = Signal()
    def mouseDoubleClickEvent(self, event):
        self.resetRequested.emit(); event.accept()


class SliderInput(QWidget):
    edited = Signal(object)
    def __init__(self, default, low=0, high=1, rotation=False, integer=False):
        super().__init__()
        self.default, self.low, self.high = default, low, high
        self.rotation, self.integer = rotation, integer
        layout = QHBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        self.slider = ResetSlider(Qt.Orientation.Horizontal); self.slider.setRange(0,10000)
        self.entry = QLineEdit(); self.entry.setFixedWidth(95)
        layout.addWidget(self.slider,1); layout.addWidget(self.entry)
        self.slider.valueChanged.connect(self.moved)
        self.entry.editingFinished.connect(self.typed)
        self.slider.resetRequested.connect(lambda: self.commit(self.default))
        self.setValue(default)

    def setValue(self, value):
        self.value = value
        position = rotation_position(value) if self.rotation else (value-self.low)/(self.high-self.low)
        blocker = QSignalBlocker(self.slider)
        self.slider.setValue(round(position*10000))
        self.entry.setText(format_rotation(value) if self.rotation else str(int(value)) if self.integer else f'{value:.6g}')

    def commit(self, value):
        self.setValue(value); self.edited.emit(value)

    def moved(self, position):
        value = rotation_value(position/10000) if self.rotation else self.low+(self.high-self.low)*position/10000
        self.commit(integer_degrees(value) if self.integer else value)

    def typed(self):
        try:
            value = parse_rotation(self.entry.text()) if self.rotation else float(self.entry.text())
            if not self.rotation and (not np.isfinite(value) or not self.low <= value <= self.high): raise ValueError('Value outside range')
            self.commit(integer_degrees(value) if self.integer else value)
        except ValueError:
            self.setValue(self.value)


class ViewHost(QWidget):
    def __init__(self):
        super().__init__()
        self.view = QQuickWindow()
        self.scope = GpuScope(self.view.contentItem())
        self.scope.setParentItem(self.view.contentItem())
        try: self.phosphor = PhosphorLayer(self.view)
        except RuntimeError as error:  # Shaders unavailable: keep the line trace.
            self.phosphor = None; print(f'Phosphor display unavailable: {error}',file=sys.stderr)
        container = QWidget.createWindowContainer(self.view, self)
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.addWidget(container)
        self.view.widthChanged.connect(self.resize_scope)
        self.view.heightChanged.connect(self.resize_scope)

    def resize_scope(self, *args):
        self.scope.setWidth(self.view.width()); self.scope.setHeight(self.view.height())
        if self.phosphor: self.phosphor.resize(self.view.width(),self.view.height())

    def release_phosphor(self):
        # Keep the closed layer's Python wrappers alive until the process ends.
        if self.phosphor: self.phosphor.close(); self.closed_phosphor,self.phosphor = self.phosphor,None


class SquareStage(QWidget):
    def __init__(self):
        super().__init__()
        self.host = ViewHost()
        self.host.setParent(self)
        self.fill_screen = False

    def set_fill_screen(self, enabled):
        self.fill_screen = enabled
        self.layout_host()

    def layout_host(self):
        if self.fill_screen:
            self.host.setGeometry(self.rect())
        else:
            size = max(1,min(self.width(),self.height()))
            self.host.setGeometry((self.width()-size)//2,(self.height()-size)//2,size,size)

    def resizeEvent(self, event):
        self.layout_host()
        super().resizeEvent(event)


def prepare_app_fonts():
    app=QApplication.instance()
    if app.property('phosphorFontsLoaded'):return
    for name in ('JetBrainsMono-Regular.ttf','JetBrainsMono-Bold.ttf'):
        if QFontDatabase.addApplicationFont(str(HERE/'assets/fonts'/name))<0:
            raise RuntimeError(f'Bundled font could not be loaded: {name}')
    app.setFont(QFont('JetBrains Mono',10))
    app.setProperty('phosphorFontsLoaded',True)


class Window(QMainWindow):
    def __init__(self, options):
        prepare_app_fonts()
        super().__init__()
        self.options = options
        self.scene = Scene()
        self.audio = AudioPreview()
        self.capture = SystemAudio()
        self.system_monitor = False
        self.visualization_gain = 1.
        self.capture_scanning = False
        self.capture_notice = ''
        self.jobs = queue.Queue()
        self.exporting = self.scanning = False
        self.launch_pending = None
        self.force_close = False
        self.drag = None
        self.handles = []
        self.parameters = {}
        self.started = time.perf_counter()
        self.motion_epoch=self.started;self.motion_audio_offset=0.;self.motion_dialog=None
        self.last_status = self.last_midi = 0
        self.backend = graphics_api().name+' — initializing'
        self.frame_count = 0
        self.playback_ticks = self.fresh_playback_windows = 0
        self.last_playback_frame = None
        self.frame_times = deque(maxlen=4096)
        self.tick_times = deque(maxlen=4096)
        self.prep_ms = deque(maxlen=4096)
        self.preferences_dialog = None
        self.signal_dialog = None
        self.immersive = False
        self.builder = None
        self.building = False
        self.builder_index = None
        try:self.signal_preferences=read_signal_preferences()
        except (ValueError,OSError):self.signal_preferences=dict(DEFAULT_SIGNAL_PREFERENCES)
        try: self.preferences = read_preferences()
        except (ValueError,OSError): self.preferences = dict(DEFAULT_PREFERENCES)
        if options.scene: self.scene.load(options.scene)
        self.resize(1220,840); self.setMinimumSize(1000,700)
        self.make_ui()
        self.apply_preferences(self.preferences)
        self.sync()
        self.host.scope.pointer = self.pointer
        self.host.view.installEventFilter(self)
        self.host.view.frameSwapped.connect(self.swapped)
        self.timer = QTimer(self); self.timer.setInterval(16); self.timer.timeout.connect(self.tick); self.timer.start()
        QTimer.singleShot(750,self.backend_info)
        if options.ready_file:
            ready = Path(options.ready_file).resolve()
            if ready.parent != USER_DIR.resolve(): raise ValueError('Readiness file must be in the user data folder')
            self.ready_file = ready
        else: self.ready_file = None

    def menu_action(self, menu, label, callback, shortcut=None):
        action = QAction(label,self); action.triggered.connect(lambda checked=False: callback())
        if shortcut: action.setShortcut(shortcut)
        menu.addAction(action)
        return action

    def make_ui(self):
        file = self.menuBar().addMenu('File')
        self.menu_action(file,'New instance…',self.new_instance,QKeySequence.StandardKey.New)
        self.menu_action(file,'Open scene…',self.open_scene,QKeySequence.StandardKey.Open)
        self.menu_action(file,'Save scene',self.save_scene,QKeySequence.StandardKey.Save)
        self.menu_action(file,'Save scene as…',lambda:self.save_scene(True),QKeySequence.StandardKey.SaveAs)
        self.menu_action(file,'Import 3D model…',self.import_model)
        self.menu_action(file,'Import SVG…',self.import_svg)
        file.addSeparator()
        self.menu_action(file,'Export source WAV…',lambda:self.export(True))
        self.menu_action(file,'Export processed WAV…',self.export)
        file.addSeparator()
        self.menu_action(file,'Record MIDI performance…',self.record)
        self.menu_action(file,'Stop recording',self.audio.midi.stop_record)
        self.menu_action(file,'Exit',self.close)
        pref = self.menuBar().addMenu('Preferences')
        self.menu_action(pref,'Appearance…',self.show_preferences)
        self.menu_action(pref,'Signal output…',self.show_signal_preferences)
        view = self.menuBar().addMenu('View')
        self.fullscreen_action=QAction('Fullscreen',self);self.fullscreen_action.setCheckable(True)
        self.fullscreen_action.setShortcut('Ctrl+Meta+F' if sys.platform=='darwin' else 'F11')
        self.fullscreen_action.toggled.connect(self.set_fullscreen);view.addAction(self.fullscreen_action);self.addAction(self.fullscreen_action)
        self.immersive_action=QAction('Trace-only fullscreen',self);self.immersive_action.setCheckable(True)
        self.immersive_action.setShortcut('Ctrl+Shift+F');self.immersive_action.toggled.connect(self.set_immersive)
        view.addAction(self.immersive_action);self.addAction(self.immersive_action)
        escape=QAction(self);escape.setShortcut('Esc');escape.triggered.connect(self.exit_view_modes);self.addAction(escape)
        about = self.menuBar().addMenu('About')
        self.menu_action(about,'About phosphor',self.about)
        self.menu_action(about,'How to use',lambda:self.about(True))
        self.menu_action(about,'Audio troubleshooting',lambda:self.about(troubleshooting=True))
        central = QWidget(); self.setCentralWidget(central)
        self.body = body = QHBoxLayout(central); body.setContentsMargins(18,14,18,14); body.setSpacing(18)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False); body.addWidget(self.splitter)
        self.sidebar = sidebar = QWidget(); sidebar.setMinimumWidth(340); sidebar.setMaximumWidth(640)
        left = QVBoxLayout(sidebar); left.setContentsMargins(0,0,0,0); left.setSpacing(8)
        self.splitter.addWidget(sidebar)
        self.brand_logo = QLabel(); self.brand_logo.setObjectName('brand')
        self.brand_logo.setFixedSize(64,64)
        self.brand_logo.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.brand_logo.setAccessibleName(APP_NAME)
        brand_row = QHBoxLayout(); brand_row.setSpacing(12)
        brand_row.addWidget(self.brand_logo)
        # Register macOS Courier explicitly, including when Qt runs offscreen.
        if sys.platform=='darwin': QFontDatabase.addApplicationFont('/System/Library/Fonts/Courier.ttc')
        self.brand_text = QLabel('SOUND LAB'); self.brand_text.setObjectName('brandText')
        brand_row.addWidget(self.brand_text); brand_row.addStretch()
        left.addLayout(brand_row)
        self.tabs = QTabWidget(); left.addWidget(self.tabs,1)
        panels = []
        self.control_scrolls = []
        for title in ('INPUT','SCENE','MIX'):
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.control_scrolls.append(scroll)
            panel = QWidget(); layout = QVBoxLayout(panel); layout.setSpacing(6); layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            scroll.setWidget(panel); self.tabs.addTab(scroll,title); panels.append(layout)
        inp, scene, mix = panels
        inp.addWidget(QLabel('Monitor input'))
        self.monitor = QComboBox(); self.monitor.addItems(['Generated scene / MIDI','System audio (visualization)'])
        if not SYSTEM_AUDIO_AVAILABLE:
            self.monitor.setItemText(1,'System audio — unavailable on macOS')
            self.monitor.model().item(1).setEnabled(False)
            self.monitor.setItemData(1,SYSTEM_AUDIO_NOTICE,Qt.ItemDataRole.ToolTipRole)
            notice = QLabel(SYSTEM_AUDIO_NOTICE); notice.setWordWrap(True); inp.addWidget(notice)
        inp.addWidget(self.monitor); self.monitor.activated.connect(self.monitor_changed)
        self.capture_panel = QWidget(); capture_layout = QVBoxLayout(self.capture_panel); capture_layout.setContentsMargins(0,0,0,0); capture_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        capture_layout.addWidget(QLabel('Audio capture source' if sys.platform == 'darwin' else 'Playback device to visualize'))
        self.capture_devices = QComboBox(); self.capture_devices.addItem('All system audio (Core Audio)' if sys.platform == 'darwin' else 'Default Windows playback device',None)
        capture_layout.addWidget(self.capture_devices)
        self.capture_refresh = QPushButton('Refresh playback devices'); capture_layout.addWidget(self.capture_refresh)
        self.capture_refresh.clicked.connect(self.refresh_capture_devices)
        capture_layout.addWidget(QLabel('Visualization input gain (0–32×)'))
        self.visualization_gain_control = SliderInput(1.,0,32)
        self.visualization_gain_control.edited.connect(lambda value:setattr(self,'visualization_gain',value))
        capture_layout.addWidget(self.visualization_gain_control)
        capture_layout.addWidget(QLabel('Display only · double-click slider to reset 1×'))
        capture_layout.addWidget(QLabel('Live stereo X–Y only. No processing,\nrecording, or audio playback by this app.\nPlay music in another application.\nMonitor/device selection is temporary;\nscene files keep your generated scene.'))
        if sys.platform == 'darwin':
            permission = QLabel('Requires macOS System Audio Recording permission.\nCaptures all apps; no microphone, screen images,\nrecording, or audio output. Stop/restart after granting access.')
            permission.setWordWrap(True); capture_layout.addWidget(permission)
        self.capture_status = QLabel(); self.capture_status.setWordWrap(True); capture_layout.addWidget(self.capture_status)
        inp.addWidget(self.capture_panel); self.capture_panel.hide()
        self.generated_panel = QWidget(); inp.addWidget(self.generated_panel)
        generated = QVBoxLayout(self.generated_panel); generated.setContentsMargins(0,0,0,0)
        self.input_sections = QTabWidget(); generated.addWidget(self.input_sections)
        source_panel,midi_panel = QWidget(),QWidget()
        inp = QVBoxLayout(source_panel); midi_controls = QVBoxLayout(midi_panel)
        for layout in (inp,midi_controls): layout.setAlignment(Qt.AlignmentFlag.AlignTop); layout.setSpacing(6)
        self.input_sections.addTab(source_panel,'Source'); self.input_sections.addTab(midi_panel,'Live MIDI')
        self.combo(inp,'Source','source',(*SOURCES,MIDI_SOURCE))
        self.midi_file_row = QWidget(); row = QHBoxLayout(self.midi_file_row); row.setContentsMargins(0,0,0,0)
        self.midi_file_label = QLabel(); self.midi_file_label.setMinimumWidth(0); row.addWidget(self.midi_file_label,1)
        choose = QPushButton('Choose…'); choose.clicked.connect(self.choose_midi_file); row.addWidget(choose)
        inp.addWidget(self.midi_file_row)
        self.number(inp,'Tempo (quarter notes / BPM)','bpm',20,600,120)
        inp = midi_controls
        inp.addWidget(QLabel('MIDI input device'))
        self.devices = QComboBox(); self.devices.setEditable(True); inp.addWidget(self.devices)
        self.devices.currentTextChanged.connect(self.device_changed)
        self.buttons(inp, [('Refresh',self.refresh_ports),('Connect',self.connect_midi),('Disconnect',self.disconnect_midi)])
        self.combo(inp,'Synth preset','midi_preset',PRESETS)
        self.combo(inp,'MIDI channel','midi_channel',('All',*[str(i) for i in range(1,17)]))
        self.number(inp,'MIDI gain before shaping (×)','midi_gain',0,16,1)
        self.buttons(inp,[('Panic',self.audio.midi.panic),('Record…',self.record),('Stop recording',self.audio.midi.stop_record)])
        self.midi_status = QLabel(); self.midi_status.setWordWrap(True); inp.addWidget(self.midi_status)
        self.scene_sections = QTabWidget(); scene.addWidget(self.scene_sections)
        objects_panel,transform_panel = QWidget(),QWidget()
        scene = QVBoxLayout(objects_panel); transform = QVBoxLayout(transform_panel)
        for layout in (scene,transform): layout.setAlignment(Qt.AlignmentFlag.AlignTop); layout.setSpacing(5)
        self.scene_sections.addTab(objects_panel,'Objects'); self.scene_sections.addTab(transform_panel,'Transform')
        self.shape_list = QListWidget(); self.shape_list.setMaximumHeight(100); scene.addWidget(self.shape_list)
        self.shape_list.currentRowChanged.connect(self.select_shape)
        self.buttons(scene,[('Add shape',lambda:self.shape_action('add')),('Add text',lambda:self.shape_action('text'))])
        shape_actions=QHBoxLayout();scene.addLayout(shape_actions)
        for label,action in (('Duplicate','duplicate'),('Remove','remove')):
            button=QPushButton(label);button.clicked.connect(lambda checked=False,a=action:self.shape_action(a));shape_actions.addWidget(button)
        self.build_button=QPushButton('Build shape…');self.build_button.clicked.connect(self.open_builder);shape_actions.addWidget(self.build_button)
        self.combo(scene,'Selected object','solid',(*SOLIDS,TEXT_OBJECT,CUSTOM_OBJECT,MODEL_OBJECT))
        self.text_panel = QWidget(); text_layout = QVBoxLayout(self.text_panel); text_layout.setContentsMargins(0,0,0,0); text_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.text_panel.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Maximum)
        text_layout.addWidget(QLabel('Text to trace'))
        self.text_entry = QLineEdit(); self.text_entry.setMaxLength(MAX_TEXT_LENGTH)
        self.text_entry.setPlaceholderText('Enter text…'); text_layout.addWidget(self.text_entry)
        self.text_entry.editingFinished.connect(lambda:self.edit('text',self.text_entry.text()))
        self.parameters['text'] = self.text_entry
        text_layout.addWidget(QLabel('A–Z, numbers and punctuation · up to 64 characters. Lowercase traces as capitals. Press Enter to apply.'))
        text_layout.addWidget(QLabel('Travel between strokes/letters is visible on a stereo scope. More text needs more samples per cycle.'))
        scene.addWidget(self.text_panel)
        scene.addWidget(QLabel('Use Transform to move, resize or rotate the selected object.'))
        scene = transform
        for axis in 'xyz': self.inline_slider(scene,f'{axis.upper()} view (°)','rotation_'+axis,0,-180,180,integer=True)
        for axis in 'xyz': self.inline_slider(scene,f'{axis.upper()} spin (rev/s)','spin_'+axis,0,-MAX_SPIN,MAX_SPIN)
        self.buttons(scene,[('Reset view',self.reset_view),('Reset placement',self.reset_placement)])
        self.inline_slider(scene,'Sec per rev','rotation_seconds',5,rotation=True)
        self.parameters['rotation_seconds'].setToolTip('Seconds per revolution: 0.01 s, slower, up to ∞ for no rotation.')
        self.number(scene,'Position X','position_x',-1e6,1e6,0,inline=True)
        self.number(scene,'Position Y','position_y',-1e6,1e6,0,inline=True)
        self.number(scene,'Size','shape_size',.05,1e6,1,inline=True)
        scene.addWidget(QLabel('Drag a center to move/select; drag the\nselected corner to resize. Edges may clip.'))
        self.combo(mix,'Travel between shapes','travel_mode',TRAVEL_MODES)
        self.number(mix,'Travel speed (×)','travel_speed',1,64,8)
        self.number(mix,'Rotation updates (Hz)','drawing_hz',1,240,60)
        self.slider(mix,'Output level','drawing_gain',.55,0,.95)
        self.slider(mix,'Original blend','music_gain',0,0,1)
        self.combo(mix,'Listening mode','mode',('Processed output','Fully shaped audio','Original audio'))
        self.buttons(mix,[('Export source…',lambda:self.export(True)),('Export output…',self.export)])
        mix.addWidget(QLabel('Left = X · Right = Y · 48 kHz\nTravel strategies approximate blanking;\nthey do not control CRT intensity.'))
        time_panel=QWidget();time_layout=QVBoxLayout(time_panel)
        time_layout.addWidget(QLabel('X and Y against time · recent 40 ms'))
        self.time_trace=TimeTrace();time_layout.addWidget(self.time_trace,1)
        freeze=QCheckBox('Freeze these traces');freeze.toggled.connect(lambda enabled:setattr(self.time_trace,'frozen',enabled));time_layout.addWidget(freeze)
        time_layout.addWidget(QLabel('Same raw playback/capture samples as the X–Y view. Display gain and interpolation are excluded. Freeze does not stop audio.'))
        self.tabs.addTab(time_panel,'X(t), Y(t)')
        self.play_button = QPushButton('▶ PLAY AUDIO'); self.play_button.clicked.connect(self.play); left.addWidget(self.play_button)
        right_panel = QWidget(); right = QVBoxLayout(right_panel); right.setContentsMargins(0,0,0,0)
        self.splitter.addWidget(right_panel)
        self.splitter.setStretchFactor(0,0); self.splitter.setStretchFactor(1,1)
        self.splitter.setSizes([400,780])
        self.viewer_heading=QLabel('X–Y SIGNAL MONITOR');right.addWidget(self.viewer_heading)
        # Normal editing stays square; immersive mode extends the background to the screen.
        self.stage = SquareStage(); self.host = self.stage.host
        right.addWidget(self.stage,1)
        self.status = QLabel(); self.status.setWordWrap(True); right.addWidget(self.status)
        self.backend_label = QLabel(self.backend); right.addWidget(self.backend_label)
        for label in sidebar.findChildren(QLabel):
            if label in (self.brand_logo,self.brand_text): continue
            if label.property('inlineCaption'): continue  # Keeps a one-line width beside its slider.
            label.setWordWrap(True); label.setMinimumWidth(0)
            policy = QSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Maximum)
            policy.setHeightForWidth(True); label.setSizePolicy(policy)
        for combo in sidebar.findChildren(QComboBox):
            combo.setMinimumContentsLength(1)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumWidth(0)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)

    def eventFilter(self,watched,event):
        if watched is getattr(getattr(self,'host',None),'view',None) and event.type()==QEvent.Type.KeyPress:
            if event.key()==Qt.Key.Key_Escape and (self.immersive or self.isFullScreen()):
                self.exit_view_modes();return True
        return super().eventFilter(watched,event)

    def set_fullscreen(self,enabled):
        if self.building:return
        if not enabled and self.immersive:self.set_immersive(False)
        self.showFullScreen() if enabled else self.showNormal()

    def set_immersive(self,enabled):
        if self.building:return
        if enabled==self.immersive:return
        if enabled:
            self._was_fullscreen=self.isFullScreen();self._split_sizes=self.splitter.sizes()
        self.immersive=enabled;self.drag=None
        self.stage.set_fill_screen(enabled)
        for widget in (self.sidebar,self.viewer_heading,self.status,self.backend_label,self.menuBar()):widget.setVisible(not enabled)
        self.body.setContentsMargins(0,0,0,0) if enabled else self.body.setContentsMargins(18,14,18,14)
        self.body.setSpacing(0 if enabled else 18)
        self.host.view.setCursor(Qt.CursorShape.BlankCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.centralWidget().setCursor(Qt.CursorShape.BlankCursor if enabled else Qt.CursorShape.ArrowCursor)
        blocker=QSignalBlocker(self.immersive_action);self.immersive_action.setChecked(enabled)
        if enabled:self.showFullScreen()
        else:
            self.splitter.setSizes(self._split_sizes)
            self.showFullScreen() if self._was_fullscreen else self.showNormal()

    def exit_view_modes(self):
        if self.building:self.builder.cancel_gesture();return
        if self.immersive:self.set_immersive(False)
        elif self.isFullScreen():self.showNormal()

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.Type.WindowStateChange and hasattr(self,'fullscreen_action'):
            if getattr(self,'immersive',False) and not self.isFullScreen():
                self._was_fullscreen=False;self.set_immersive(False)
            blocker=QSignalBlocker(self.fullscreen_action);self.fullscreen_action.setChecked(self.isFullScreen())

    def output_settings(self):
        dwell=self.signal_preferences['corner_dwell_us']
        count=self.signal_preferences['shape_redraw_count'] if self.signal_preferences['shape_redraw'] else 1
        self.redraw_notice=''
        if count>1:
            settings=self.scene.settings
            try:planned_path(0,settings.drawing_hz,scene_shapes(settings),settings.travel_mode,settings.travel_speed,0.,count)
            except ValueError as error:count=1;self.redraw_notice='Redraw inactive: '+str(error)
        return replace(self.scene.settings,corner_dwell_us=dwell,motion_time_offset=self.motion_audio_offset if self.audio.running else 0.,shape_redraw_count=count)

    def restart_motion(self):
        self.motion_epoch=time.perf_counter()
        self.motion_audio_offset=-self.audio.frames/self.scene.settings.sample_rate if self.audio.running else 0.

    def show_motion(self):
        if self.building and not self.leave_builder():return
        if self.motion_dialog is not None:self.motion_dialog.raise_();return
        from motion_dialog import MotionDialog
        self.motion_dialog=MotionDialog(self);self.motion_dialog.finished.connect(lambda result:setattr(self,'motion_dialog',None));self.motion_dialog.show()

    def apply_signal_preferences(self,value):
        value=validated_signal_preferences(value)
        if value['shape_redraw'] and value['shape_redraw_count']>1:
            settings=self.scene.settings
            planned_path(0,settings.drawing_hz,scene_shapes(settings),settings.travel_mode,settings.travel_speed,0.,value['shape_redraw_count'])
        self.signal_preferences=value
        self.audio.parameters=(self.output_settings(),self.scene.mode)

    def show_signal_preferences(self):
        if self.signal_dialog:self.signal_dialog.raise_();return
        dialog=QDialog(self);self.signal_dialog=dialog;dialog.setWindowTitle('Preferences — Signal output')
        layout=QVBoxLayout(dialog);baseline=dict(self.signal_preferences)
        layout.addWidget(QLabel('Hardware corner dwell (microseconds per sharp turn)'))
        slider=SliderInput(0.,0.,MAX_DWELL_US);slider.setObjectName('corner_dwell_us');slider.setValue(baseline['corner_dwell_us']);layout.addWidget(slider)
        row=QHBoxLayout();layout.addLayout(row)
        redraw=QCheckBox('Shape redraw (experimental)');redraw.setObjectName('shape_redraw');redraw.setChecked(baseline['shape_redraw']);row.addWidget(redraw)
        redraw.setToolTip('Validated by the user on Mac; automated native checks passed on Windows.')
        count=QSpinBox();count.setObjectName('shape_redraw_count');count.setRange(1,MAX_REDRAWS);count.setValue(baseline['shape_redraw_count']);count.setEnabled(redraw.isChecked());row.addWidget(count)
        redraw.toggled.connect(count.setEnabled)
        redraw_info=QLabel('Total draws per shape before moving to the next (1 = unchanged).\nOnly applies with multiple drawable shapes; motion guides do not count.\nRepeats fit the existing source cycle. Fewer samples per trace; shaped timbre can change.')
        redraw_info.setWordWrap(True);layout.addWidget(redraw_info)
        info=QLabel('Changes actual generated X/Y audio, MIDI shaping and processed WAV exports.\nOriginal/source audio and system-audio capture are unchanged.\nUse Fully shaped audio or blend 0 for clean corner holds.\n\nSource cycle timing is preserved; timbre can change. Total dwell is capped at 25%.\nHigh notes or many corners may shorten the requested hold. At 48 kHz,\none sample is 20.83 µs; short holds may receive no samples.\n\nMore dwell brightens corners and speeds up the edges. It may not correct\nDAC filtering or phase errors. Start at 0 and increase gradually.\nThis hardware preference is saved separately from scenes and appearance.')
        info.setWordWrap(True);layout.addWidget(info)
        def current():return dict(corner_dwell_us=slider.value,shape_redraw=redraw.isChecked(),shape_redraw_count=count.value())
        def preview(*args):
            try:self.apply_signal_preferences(current())
            except ValueError as error:self.error(error)
        slider.edited.connect(preview);redraw.toggled.connect(preview);count.valueChanged.connect(preview)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.RestoreDefaults);layout.addWidget(buttons)
        def apply():
            nonlocal baseline
            try:
                value=validated_signal_preferences(current())
                self.apply_signal_preferences(value);atomic_json(SIGNAL_PREFERENCES_PATH,value);baseline=value;return True
            except (ValueError,OSError) as error:self.error(error);return False
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(apply)
        def defaults():
            redraw.setChecked(False);count.setValue(1);slider.commit(0.)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(defaults)
        buttons.accepted.connect(lambda:dialog.accept() if apply() else None);buttons.rejected.connect(dialog.reject)
        dialog.finished.connect(lambda result:(self.apply_signal_preferences(baseline),setattr(self,'signal_dialog',None)))
        dialog.setModal(True);dialog.show()

    def buttons(self, layout, entries):
        row = QHBoxLayout()
        for label, action in entries:
            button = QPushButton(label); button.clicked.connect(lambda checked=False,action=action:action()); row.addWidget(button)
        layout.addLayout(row)

    def combo(self, layout, label, key, choices):
        layout.addWidget(QLabel(label)); widget = QComboBox(); widget.addItems(list(choices)); layout.addWidget(widget)
        widget.activated.connect(lambda index:self.edit(key,widget.currentText()))
        self.parameters[key] = widget

    def number(self, layout, label, key, low, high, default, inline=False):
        target = QHBoxLayout() if inline else layout
        if inline: layout.addLayout(target)
        target.addWidget(QLabel(label)); widget = QDoubleSpinBox(); widget.setRange(low,high); widget.setDecimals(3)
        widget.setKeyboardTracking(False); widget.setValue(default); target.addWidget(widget)
        widget.valueChanged.connect(lambda value:self.edit(key,value)); self.parameters[key] = widget

    def inline_slider(self, layout, label, key, default, low=0, high=1, **kwargs):
        row = QHBoxLayout(); layout.addLayout(row); caption = QLabel(label); caption.setProperty('inlineCaption',True); caption.setFixedWidth(104); row.addWidget(caption)
        widget = SliderInput(default,low,high,**kwargs); widget.setFixedHeight(28); row.addWidget(widget,1)
        widget.edited.connect(lambda value:self.edit(key,value)); self.parameters[key] = widget

    def slider(self, layout, label, key, default, low=0, high=1, **kwargs):
        layout.addWidget(QLabel(label)); widget = SliderInput(default,low,high,**kwargs); layout.addWidget(widget)
        widget.edited.connect(lambda value:self.edit(key,value)); self.parameters[key] = widget

    def device_changed(self, name):
        self.scene.device = name; self.update_title()

    def monitor_changed(self, index):
        if index == 1 and not SYSTEM_AUDIO_AVAILABLE:
            self.monitor.setCurrentIndex(0)
            self.status.setText(SYSTEM_AUDIO_NOTICE)
            return
        try:
            self.audio.stop(); self.capture.stop()
            self.system_monitor = index == 1
            self.generated_panel.setEnabled(not self.system_monitor)
            self.generated_panel.setVisible(not self.system_monitor)
            self.capture_panel.setVisible(self.system_monitor)
            for tab in (1,2): self.tabs.setTabEnabled(tab,not self.system_monitor)
            if self.system_monitor:
                self.tabs.setCurrentIndex(0); self.drag = None; self.handles = []
                if self.capture_devices.count() == 1: self.refresh_capture_devices()
            self.last_status = 0
        except Exception as error:
            blocker = QSignalBlocker(self.monitor); self.monitor.setCurrentIndex(1 if self.system_monitor else 0)
            self.error(error)

    def refresh_capture_devices(self):
        if self.capture_scanning or self.capture.running: return
        self.capture_scanning = True; self.capture_notice = 'Finding system-audio sources…'
        def work():
            try: self.jobs.put(('capture_devices',SystemAudio.devices()))
            except Exception as error: self.jobs.put(('capture_error',str(error)))
        threading.Thread(target=work,daemon=True).start()

    def edit(self, key, value):
        try:
            if key=='solid' and value==CUSTOM_OBJECT and self.scene.shape.solid!=CUSTOM_OBJECT:
                self.sync();self.open_builder();return
            if key=='solid' and value==MODEL_OBJECT and self.scene.shape.solid!=MODEL_OBJECT:
                self.sync();self.import_model(True);return
            if key=='source' and value==MIDI_FILE_SOURCE and self.scene.settings.source!=MIDI_FILE_SOURCE:
                self.sync();self.choose_midi_file();return
            if key == 'source' and value != self.scene.settings.source: self.audio.stop()
            if key == 'mode': self.scene.mode = {'Processed output':'mixed','Fully shaped audio':'drawing','Original audio':'music'}[value]
            else:
                if key == 'midi_channel': value = -1 if value == 'All' else int(value)-1
                self.scene.change(key,value)
            self.audio.parameters = (self.output_settings(),self.scene.mode)
            self.sync()
        except Exception as error:
            self.error(error); self.sync()

    def choose_midi_file(self):
        current = self.scene.settings.midi_file
        value,_ = QFileDialog.getOpenFileName(self,'Play the melody of a MIDI file',str(Path(current).parent) if current else '','MIDI file (*.mid *.midi)')
        if not value: self.sync(); return
        try:
            score_arrays(value)  # reject unreadable or empty files before switching
            self.audio.stop()
            self.scene.change('midi_file',value)
            self.scene.change('source',MIDI_FILE_SOURCE)
            # Start at the file's own speed; Tempo still scales it afterwards.
            self.scene.change('bpm',min(600.,max(20.,file_tempo(value))))
        except Exception as error: self.error(error)
        self.sync()

    def sync(self):
        values = {**vars(self.scene.settings),**vars(self.scene.shape)}
        values['mode'] = {'mixed':'Processed output','drawing':'Fully shaped audio','music':'Original audio'}[self.scene.mode]
        values['midi_channel'] = 'All' if self.scene.settings.midi_channel < 0 else str(self.scene.settings.midi_channel+1)
        for key, widget in self.parameters.items():
            blocker = QSignalBlocker(widget)
            if isinstance(widget,QComboBox): widget.setCurrentText(str(values[key]))
            elif isinstance(widget,QLineEdit): widget.setText(str(values[key]))
            else: widget.setValue(values[key])
        blocker = QSignalBlocker(self.devices); self.devices.setCurrentText(self.scene.device)
        blocker = QSignalBlocker(self.shape_list)
        labels = [f'{i+1:02}  '+('Motion: ' if is_motion_guide(shape) else '')+(f'Text: {shape.text[:20] or "(blank)"}' if shape.solid == TEXT_OBJECT else shape.custom.name if shape.solid==CUSTOM_OBJECT else shape.model.name if shape.solid==MODEL_OBJECT else shape.solid) for i,shape in enumerate(self.scene.settings.shapes)]
        if [self.shape_list.item(i).text() for i in range(self.shape_list.count())] != labels:
            self.shape_list.clear(); self.shape_list.addItems(labels)
        self.shape_list.setCurrentRow(self.scene.selected)
        self.text_panel.setVisible(self.scene.shape.solid == TEXT_OBJECT)
        self.midi_file_row.setVisible(self.scene.settings.source == MIDI_FILE_SOURCE)
        self.midi_file_label.setText(Path(self.scene.settings.midi_file).name)
        self.midi_file_label.setToolTip(self.scene.settings.midi_file)
        self.build_button.setText('Edit shape…' if self.scene.shape.solid==CUSTOM_OBJECT else 'Build shape…')
        self.audio.parameters = (self.output_settings(),self.scene.mode)
        self.update_title()

    def update_title(self):
        self.setWindowTitle(f'{"* " if self.scene.dirty else ""}{self.scene.path.name if self.scene.path else "Untitled scene"} — phosphor')

    def select_shape(self, index):
        if 0 <= index < len(self.scene.settings.shapes): self.scene.selected = index; self.sync()

    def shape_action(self, action):
        if action == 'remove': self.scene.remove()
        else: self.scene.add(action == 'duplicate',text=action == 'text')
        self.sync()

    def reset_view(self):
        for axis in 'xyz': self.scene.change('rotation_'+axis,0); self.scene.change('spin_'+axis,0)
        self.sync()

    def reset_placement(self):
        for key,value in (('position_x',0),('position_y',0),('shape_size',1)): self.scene.change(key,value)
        self.sync()

    def error(self, error): QMessageBox.critical(self,'phosphor',str(error))

    def open_builder(self):
        if self.system_monitor:
            self.error('Switch to Generated scene / MIDI before building a shape.');return
        self.exit_view_modes()
        if self.isFullScreen():self.showNormal()
        if self.builder is None:
            from shape_builder import ShapeBuilder
            self.builder=ShapeBuilder(ViewHost,self.centralWidget());self.body.addWidget(self.builder)
            self.builder.applied.connect(self.apply_custom_drawing)
            self.builder.back.connect(self.leave_builder)
            self.builder.play_button.clicked.connect(self.play)
        self.builder_index=self.scene.selected if self.scene.shape.solid==CUSTOM_OBJECT else None
        drawing=self.scene.shape.custom if self.builder_index is not None else Drawing()
        self.builder.begin(drawing);self.builder.set_colors(PALETTES[self.preferences['palette']])
        self.splitter.hide();self.builder.show();self.building=True
        self.fullscreen_action.setEnabled(False);self.immersive_action.setEnabled(False)
        self.builder.canvas.forceActiveFocus()

    def import_svg(self):
        value,_ = QFileDialog.getOpenFileName(self,'Import SVG','','SVG drawing (*.svg)')
        if value: self.add_svg(value)

    def add_svg(self,path):
        try:
            drawing,report=load_svg(path)
            self.scene.add_drawing(drawing)
            self.audio.parameters=(self.output_settings(),self.scene.mode);self.sync()
        except (ValueError,OSError) as error:self.error(error);return
        notes=[]
        if report.simplified:notes.append(f'Simplified to {report.nodes} nodes and {report.edges} lines to fit a custom shape.')
        if report.dropped:notes.append(f'{report.dropped} of {report.subpaths} parts were too small to keep.')
        if report.skipped:notes.append('Not imported: '+', '.join(report.skipped)+'. Convert text to outlines first.')
        if notes:QMessageBox.information(self,'SVG simplified','\n\n'.join(notes))

    def import_model(self,replace_selected=False):
        value,_ = QFileDialog.getOpenFileName(self,'Import 3D model','','Wavefront OBJ (*.obj)')
        if value: self.add_model(value,replace_selected)

    def add_model(self,path,replace_selected=False):
        try:
            model,report=load_obj(path)
            self.scene.add_model(model,replace_selected)
            self.audio.parameters=(self.output_settings(),self.scene.mode);self.sync()
        except (ValueError,OSError) as error:self.error(error);return
        notes=[]
        if report.smooth:notes.append('This model has few sharp edges, so its sharpest available edges were used. Smooth forms may look incomplete.')
        if report.limited:notes.append(f'Only the {MAX_MODEL_EDGES} sharpest edges fit in the drawing budget.')
        if notes:QMessageBox.information(self,'Model simplified',f'Kept {report.kept_edges} of {report.source_edges} edges.\n\n'+'\n\n'.join(notes))

    def apply_custom_drawing(self,drawing):
        compiled(drawing)
        shapes=list(self.scene.settings.shapes)
        index=self.builder_index
        if index is None:
            index=len(shapes)
            shapes.append(Shape(solid=CUSTOM_OBJECT,custom=drawing,rotation_seconds=None,uid=uuid.uuid4().hex if drawing.motion_path else ''))
        else:shapes[index]=replace(shapes[index],solid=CUSTOM_OBJECT,custom=drawing)
        if drawing.motion_path:
            guide=shapes[index]
            shapes[index]=replace(guide,uid=guide.uid or uuid.uuid4().hex,rotation_seconds=None)
        candidate=replace(self.scene.settings,shapes=tuple(shapes))
        try:validate(candidate)
        except ValueError as error:self.builder.apply_succeeded=False;self.error(error);return
        previous=self.scene.settings.shapes[index].custom if index<len(self.scene.settings.shapes) else None
        self.builder_index=index;self.scene.settings=candidate
        if drawing.attachments and (previous is None or previous.attachments!=drawing.attachments):self.restart_motion()
        self.scene.selected=index;self.sync()

    def leave_builder(self):
        if not self.building:return True
        if self.builder.unapplied:
            result=QMessageBox.question(self,'Unapplied shape edits','Apply the completed shape before returning to the scene?\nDiscard leaves the scene at its last applied or auditioned state.',
                QMessageBox.StandardButton.Apply|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
            if result==QMessageBox.StandardButton.Cancel:return False
            if result==QMessageBox.StandardButton.Apply:
                self.builder.apply()
                if self.builder.unapplied:return False
        self.builder.cancel_gesture();self.builder.hide();self.splitter.show();self.building=False
        self.fullscreen_action.setEnabled(True);self.immersive_action.setEnabled(True);return True

    def confirm_save(self, action, force=False):
        if not force and not self.scene.dirty: return True
        result = QMessageBox.question(self,'Save current scene?',f'Save the current scene before {action}?',
            QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        return self.save_scene() if result == QMessageBox.StandardButton.Save else result == QMessageBox.StandardButton.Discard

    def save_scene(self, save_as=False):
        if self.building and not self.leave_builder():return False
        path = self.scene.path
        if save_as or path is None:
            value,_ = QFileDialog.getSaveFileName(self,'Save scene',str(path or 'Untitled.scene.json'),'Scene JSON (*.scene.json)')
            if not value: return False
            path = Path(value if value.lower().endswith('.json') else value+'.scene.json')
        try: self.scene.save(path); self.update_title(); return True
        except Exception as error: self.error(error); return False

    def open_scene(self):
        if self.building and not self.leave_builder():return
        value,_ = QFileDialog.getOpenFileName(self,'Open scene','','Scene JSON (*.json)')
        if not value: return
        try:
            read_scene(value)
            if not self.confirm_save('opening another scene'): return
            read_scene(value)
            self.shutdown_audio(); self.scene.load(Path(value)); self.restart_motion();self.sync()
            self.monitor.setCurrentIndex(0); self.monitor_changed(0)
        except Exception as error: self.error(error)

    def shutdown_audio(self):
        self.capture.stop(); self.audio.stop(); self.audio.midi.disconnect()

    def new_instance(self):
        if self.building and not self.leave_builder():return
        if self.launch_pending or self.exporting: return
        if not self.confirm_save('opening a new instance',True): return
        try:
            self.shutdown_audio()
            ready = USER_DIR/f'qt-launch-{uuid.uuid4().hex}.ready'; ready.parent.mkdir(parents=True,exist_ok=True)
            process = subprocess.Popen(relaunch_command('--ready-file',str(ready)),cwd=HERE,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.launch_pending = (process,ready,time.monotonic()+20)
            self.centralWidget().setEnabled(False); self.menuBar().setEnabled(False)
        except Exception as error: self.error(error)

    def closeEvent(self, event):
        if self.force_close: self.host.release_phosphor(); event.accept(); return
        if self.building and not self.leave_builder():event.ignore();return
        if self.launch_pending: event.ignore(); return
        if self.exporting:
            QMessageBox.information(self,'Export in progress','Wait for the WAV export to finish before exiting.'); event.ignore(); return
        if not self.confirm_save('exiting'): event.ignore(); return
        try: self.shutdown_audio(); self.host.release_phosphor(); event.accept()
        except Exception as error: self.error(error); event.ignore()

    def play(self):
        try:
            if self.system_monitor:
                if self.capture.running: self.capture.stop()
                else:
                    self.audio.stop()
                    self.capture.start(self.capture_devices.currentData())
                self.last_status = 0
                return
            if self.audio.running: self.audio.stop()
            else:
                if self.scene.settings.source == MIDI_SOURCE and self.audio.midi.port is None:
                    raise ValueError('Connect a MIDI input before starting Live MIDI audio.')
                self.restart_motion();self.motion_audio_offset=0.
                settle_garbage()  # Before audio starts, while a pause cannot be heard.
                self.audio.start(replace(self.output_settings(),motion_time_offset=0.),self.scene.mode)
        except Exception as error: self.error(error)

    def refresh_ports(self):
        if self.scanning: return
        self.scanning = True
        def work():
            try: self.jobs.put(('ports',self.audio.midi.names()))
            except Exception as error: self.jobs.put(('ports_error',str(error)))
        threading.Thread(target=work,daemon=True).start()

    def connect_midi(self):
        try:
            if not self.scene.device: raise ValueError('Select a MIDI input first.')
            self.audio.stop(); self.audio.midi.connect(self.scene.device)
            self.scene.change('source',MIDI_SOURCE); self.sync()
        except Exception as error: self.error(error)

    def disconnect_midi(self):
        try:
            if self.audio.live: self.audio.stop()
            self.audio.midi.disconnect()
        except Exception as error: self.error(error)

    def record(self):
        if self.system_monitor:
            QMessageBox.information(self,'System audio','System-audio mode is visualization only; recording is disabled.'); return
        if not self.audio.running or not self.audio.live:
            QMessageBox.information(self,'Record MIDI','Connect MIDI and start Live MIDI audio first.'); return
        if self.audio.midi.capture: return
        path,_ = QFileDialog.getSaveFileName(self,'Record stereo WAV + MIDI + scene metadata','','Performance WAV (*.wav)')
        if path: self.audio.midi.record(path)

    def export(self, source_only=False):
        if self.system_monitor:
            QMessageBox.information(self,'System audio','System-audio mode is visualization only; WAV export is disabled.'); return
        if self.exporting: return
        if self.scene.settings.source == MIDI_SOURCE:
            QMessageBox.information(self,'Export','Use Record performance for live MIDI.'); return
        path,_ = QFileDialog.getSaveFileName(self,'Export source WAV' if source_only else 'Export processed WAV','','Stereo WAV (*.wav)')
        if not path: return
        target = Path(path if path.lower().endswith('.wav') else path+'.wav')
        settings = replace(self.output_settings(),motion_time_offset=0.)
        self.exporting = True
        def work():
            temporary = target.with_name('.export-'+uuid.uuid4().hex+'.wav')
            try:
                export_wav(temporary,settings,source_duration(settings),source_only=source_only)
                os.replace(temporary,target); self.jobs.put(('export',str(target)))
            except Exception as error: self.jobs.put(('export_error',str(error)))
            finally:
                if temporary.exists(): temporary.unlink()
        threading.Thread(target=work,daemon=True).start()

    def apply_preferences(self, preferences):
        self.preferences = validated_preferences(preferences)
        if getattr(self,'_styled_palette',None) == self.preferences['palette']: return
        p = PALETTES[self.preferences['palette']]
        arrows = self.spin_arrow_images(p)
        # Without images (unwritable temp folder) the buttons still work, shown as plain bars.
        arrow_rules = f'''
            QAbstractSpinBox::up-arrow {{ width:8px; height:5px; image:url("{arrows['up']}"); }}
            QAbstractSpinBox::down-arrow {{ width:8px; height:5px; image:url("{arrows['down']}"); }}
            QAbstractSpinBox::up-arrow:off,QAbstractSpinBox::up-arrow:disabled {{ image:url("{arrows['up_off']}"); }}
            QAbstractSpinBox::down-arrow:off,QAbstractSpinBox::down-arrow:disabled {{ image:url("{arrows['down_off']}"); }}
        ''' if arrows else f"QAbstractSpinBox::up-arrow,QAbstractSpinBox::down-arrow {{ width:8px; height:2px; background:{p['fg']}; }}"
        self.setStyleSheet(f'''
            QWidget {{ background:{p['bg']}; color:{p['fg']}; font-family:"JetBrains Mono"; font-size:12px; }}
            QLabel#brand {{ color:{p['accent']}; font-size:19px; font-weight:bold; }}
            QLabel#brandText {{ color:{p['trace']}; font-family:"Courier","Courier New"; font-size:36px; font-weight:normal; }}
            QPushButton,QComboBox,QLineEdit,QAbstractSpinBox,QListWidget {{ background:{p['field']}; border:1px solid {p['border']}; padding:4px; }}
            QAbstractSpinBox::up-button,QAbstractSpinBox::down-button {{ subcontrol-origin:border; width:24px; background:{p['field']}; border-left:1px solid {p['border']}; }}
            QAbstractSpinBox::up-button {{ subcontrol-position:top right; }}
            QAbstractSpinBox::down-button {{ subcontrol-position:bottom right; }}
            QAbstractSpinBox::up-button:hover,QAbstractSpinBox::down-button:hover {{ background:{p['active']}; }}
            {arrow_rules}
            QPushButton:hover,QPushButton:checked,QMenu::item:selected {{ background:{p['active']}; color:{p['accent']}; }}
            QTabBar::tab {{ padding:10px 18px; color:{p['muted']}; }}
            QTabBar::tab:selected {{ background:{p['active']}; color:{p['accent']}; }}
            QTabWidget::pane {{ border:1px solid {p['border']}; }}
            QSlider::groove:horizontal {{ height:4px; background:{p['field']}; }}
            QSlider::handle:horizontal {{ background:{p['accent']}; width:12px; margin:-5px 0; }}
            QMenuBar::item {{ padding:7px 14px; }}
            QListWidget::item:selected {{ background:{p['active']}; color:{p['accent']}; }}
            QScrollBar:vertical {{ width:10px; background:{p['bg']}; }}
            QScrollBar::handle:vertical {{ background:{p['border']}; min-height:25px; }}
        ''')
        artwork = str(logo_path(self.preferences['palette']))
        self.brand_logo.setPixmap(QPixmap(artwork).scaled(64,64,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        icon = QIcon(artwork)
        self.setWindowIcon(icon)
        QApplication.instance().setWindowIcon(icon)
        if self.builder is not None:self.builder.set_colors(p)
        self.host.view.setColor(QColor(p['background']))
        self._styled_palette = self.preferences['palette']

    def update_phosphor(self, new_frames, elapsed):
        layer,scope,p = self.host.phosphor,self.host.scope,self.preferences
        active = layer is not None and p['phosphor'] != PHOSPHOR_OFF
        scope.draw_trace = not active
        if layer is None: return
        layer.set_active(active)
        if not active: return
        count = new_sample_count(new_frames,INTERPOLATION_FACTORS[p['trace_interpolation']],len(scope.signal))
        view = self.host.view
        core = gaussian_beam(p['trace_thickness'],view.devicePixelRatio(),p['beam_brightness'])[0] if p['beam_profile'] == BEAM_GAUSSIAN else p['trace_thickness']
        bands = ()
        if p['crt_glow'] and p['glow_intensity'] > 0:
            bands = ((p['trace_thickness']+2*p['glow_size'],.08*p['glow_intensity']),(p['trace_thickness']+2*p['glow_size']/3,.15*p['glow_intensity']))
        vertices = deposit_vertices(scope.screen_points()[-count-1:],core,p['beam_brightness'],bands,p['trace_thickness'],p['trace_thickness']) if count else np.empty(0,BEAM_VERTEX)
        layer.step(vertices,phosphor_uniforms(p['phosphor'],PALETTES[p['palette']]['trace'],p['glow_decay'],elapsed,p['beam_intensity'])|beam_uniforms(p['beam_profile'],p['trace_thickness'],view.devicePixelRatio(),view.width(),view.height(),p['beam_brightness']))

    def spin_arrow_images(self, p):
        # Stylesheets cannot draw triangles, so write palette-coloured images for them.
        from PySide6.QtCore import QPointF, QTemporaryDir
        from PySide6.QtGui import QImage, QPainter, QPolygonF
        if getattr(self,'_arrow_directory',None) is None:
            self._arrow_directory = QTemporaryDir()
        paths = {}
        shapes = dict(up=((0,10),(8,0),(16,10)), down=((0,0),(16,0),(8,10)))
        for name,points in shapes.items():
            for suffix,color in (('',p['fg']),('_off',p['muted'])):
                image = QImage(16,10,QImage.Format.Format_ARGB32_Premultiplied); image.fill(0)
                painter = QPainter(image); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(color))
                painter.drawPolygon(QPolygonF([QPointF(*point) for point in points])); painter.end()
                path = self._arrow_directory.filePath(f'{name}{suffix}_{color.lstrip("#")}.png')
                if not image.save(path): return None
                paths[name+suffix] = path.replace('\\','/')
        return paths

    def show_preferences(self):
        if self.preferences_dialog:
            self.preferences_dialog.raise_(); return
        dialog = QDialog(self); self.preferences_dialog = dialog; dialog.setWindowTitle('Preferences — Appearance')
        layout = QVBoxLayout(dialog); baseline = dict(self.preferences)
        palette = QComboBox(); palette.addItems(list(PALETTES)); palette.setCurrentText(baseline['palette'])
        density = QSpinBox(); density.setRange(2,64); density.setValue(baseline['grid_divisions'])
        scan = QCheckBox('CRT scanlines'); scan.setChecked(baseline['crt_scanlines'])
        glow = QCheckBox('GPU trace glow'); glow.setChecked(baseline['crt_glow'])
        interpolation = QComboBox(); interpolation.addItems(list(INTERPOLATION_FACTORS))
        interpolation.setCurrentText(baseline['trace_interpolation'])
        interpolation.setObjectName('trace_interpolation')
        phosphor = QComboBox(); phosphor.addItems(list(PHOSPHORS)); phosphor.setCurrentText(baseline['phosphor']); phosphor.setObjectName('phosphor')
        beam_profile = QComboBox(); beam_profile.addItems(BEAM_PROFILES); beam_profile.setCurrentText(baseline['beam_profile']); beam_profile.setObjectName('beam_profile')
        for text,widget in (('Color preset',palette),('Grid divisions per axis',density),('',scan),('',glow),('Display interpolation',interpolation),('Phosphor display',phosphor),('Beam profile',beam_profile)):
            if text: layout.addWidget(QLabel(text))
            layout.addWidget(widget)
        visual = {}
        for key,label in (('trace_thickness','Trace thickness (px)'),('glow_size','Glow size (px radius)'),('glow_intensity','Glow intensity (×)'),('glow_decay','Glow decay time (seconds)'),('beam_brightness','Beam-speed brightness (0 = Classic even trace)'),('beam_intensity','Phosphor intensity (×)')):
            layout.addWidget(QLabel(label))
            slider = SliderInput(VISUAL_DEFAULTS[key],*VISUAL_RANGES[key]); slider.setObjectName(key)
            slider.setValue(baseline[key]); visual[key] = slider; layout.addWidget(slider)
        layout.addWidget(QLabel('Decay 0 = no afterimage. Double-click a slider to reset it.'))
        layout.addWidget(QLabel('Interpolation smooths between samples; it adds no audio detail.\nCubic curves can overshoot. Higher settings use more rendering work.\nAppearance affects the preview only.'))
        def current(): return dict(palette=palette.currentText(),grid_divisions=density.value(),crt_scanlines=scan.isChecked(),crt_glow=glow.isChecked(),trace_interpolation=interpolation.currentText(),phosphor=phosphor.currentText(),beam_profile=beam_profile.currentText(),**{key:slider.value for key,slider in visual.items()})
        for signal in (palette.currentTextChanged,density.valueChanged,scan.toggled,glow.toggled,interpolation.currentTextChanged,phosphor.currentTextChanged,beam_profile.currentTextChanged,*[slider.edited for slider in visual.values()]): signal.connect(lambda *args:self.apply_preferences(current()))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.RestoreDefaults)
        layout.addWidget(buttons)
        def apply():
            nonlocal baseline
            try: atomic_json(PREFERENCES_PATH,current()); baseline = current(); self.apply_preferences(baseline); return True
            except Exception as error: self.error(error); return False
        def defaults():
            p = DEFAULT_PREFERENCES; palette.setCurrentText(p['palette']); density.setValue(p['grid_divisions']); scan.setChecked(p['crt_scanlines']); glow.setChecked(p['crt_glow']); interpolation.setCurrentText(p['trace_interpolation']); phosphor.setCurrentText(p['phosphor']); beam_profile.setCurrentText(p['beam_profile'])
            for key,slider in visual.items(): slider.setValue(p[key])
            self.apply_preferences(current())
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(apply)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(defaults)
        buttons.accepted.connect(lambda:dialog.accept() if apply() else None)
        buttons.rejected.connect(dialog.reject)
        dialog.finished.connect(lambda result:(self.apply_preferences(baseline),setattr(self,'preferences_dialog',None)))
        dialog.setModal(True); dialog.show()

    def about(self, instructions=False, troubleshooting=False):
        dialog = QDialog(self); dialog.setWindowTitle('Help & About — phosphor'); dialog.resize(780,640)
        layout = QVBoxLayout(dialog); tabs = QTabWidget(); layout.addWidget(tabs)
        for title,text in (('About',f'phosphor {VERSION}\n\nMade by Mike Le\n\nProject date: {PROJECT_DATE}\nBuild date: {BUILD_DATE}\n\nAI transparency\n\n{TRANSPARENCY}'),('How to use',INSTRUCTIONS),('Audio troubleshooting',MACOS_AUDIO_TROUBLESHOOTING if sys.platform == 'darwin' else AUDIO_TROUBLESHOOTING)):
            browser = QTextBrowser(); browser.setPlainText(text); tabs.addTab(browser,title)
        tabs.setCurrentIndex(2 if troubleshooting else 1 if instructions else 0); dialog.exec()

    def pointer(self, kind, point):
        if self.system_monitor or self.immersive: return False
        point = np.array([point.x(),point.y()])
        if kind == 'release': self.drag = None; return True
        if kind == 'press':
            self.drag = None
            if self.scene.mode == 'music' or self.scene.settings.drawing_gain <= 0: return False
            for index,center,corner in sorted(self.handles,key=lambda h:h[0] != self.scene.selected):
                resize = index == self.scene.selected and np.max(abs(point-corner)) <= 12
                if resize or np.linalg.norm(point-center) <= 12:
                    self.select_shape(index)
                    self.drag = ('size' if resize else 'move',point,self.scene.shape,.45*min(self.host.scope.width(),self.host.scope.height())*self.scene.settings.drawing_gain)
                    return True
            return False
        if self.drag:
            kind,start,shape,pixels = self.drag
            delta = point-start
            if kind == 'move':
                self.scene.change('position_x',float(shape.position_x+delta[0]/pixels))
                self.scene.change('position_y',float(shape.position_y-delta[1]/pixels))
            else: self.scene.change('shape_size',float(max(.05,shape.shape_size+(delta[0]-delta[1])/(2*pixels))))
            self.sync()
        return True

    def overlay(self, seconds,motion_seconds=None):
        scope = self.host.scope; w,h = scope.width(),scope.height()
        scale = .45*min(w,h)*self.scene.settings.drawing_gain
        strips = []; self.handles = []
        if self.scene.mode != 'music':
            moving=iter(posed_shapes(self.scene.settings.shapes,seconds if motion_seconds is None else motion_seconds))
            for i,base in enumerate(self.scene.settings.shapes):
                shape=base if is_motion_guide(base) else next(moving)
                if shape.solid==CUSTOM_OBJECT and shape.custom.guides:
                    from drawing_motion import segment_indices
                    from motion_path import guide_route
                    from builder_selection import dashed_lines
                    for g in shape.custom.guides:
                        if not g.enabled:continue
                        points,_=guide_route(shape.custom.subset(segment_indices(shape.custom,g.nodes)))
                        points=projected_solid(seconds,shape,vertices=np.column_stack((points,np.zeros(len(points)))))*shape.shape_size+[shape.position_x,shape.position_y]
                        for line in dashed_lines(points*[scale,-scale]+[w/2,h/2]):strips.append(stroke_triangles(line,.8))
                if is_motion_guide(shape):
                    points,_=world_motion_route(shape)
                    from builder_selection import dashed_lines
                    for line in dashed_lines(points*[scale,-scale]+[w/2,h/2]):strips.append(stroke_triangles(line,.8))
                center = np.array([w/2+shape.position_x*scale,h/2-shape.position_y*scale]); r = shape.shape_size*scale
                corner = center+[r,-r]
                ch = np.clip(center,[8,8],[max(8,w-8),max(8,h-8)])
                cr = np.clip(corner,[20,20],[max(20,w-20),max(20,h-20)])
                self.handles.append((i,ch,cr))
                angles = np.linspace(0,2*np.pi,20)
                strips.append(stroke_triangles(ch+np.column_stack([np.cos(angles),np.sin(angles)])*6,2))
                if i == self.scene.selected:
                    strips.append(stroke_triangles(center+np.array([[-r,-r],[r,-r],[r,r],[-r,r],[-r,-r]]),.6))
                    strips.append(stroke_triangles(cr+np.array([[-6,-6],[6,-6],[6,6],[-6,6],[-6,-6]]),2))
        p = PALETTES[self.preferences['palette']]
        layers = [(np.concatenate(strips) if strips else np.empty((0,2)),QColor(p['accent']))]
        axes = projected_solid(np.floor(seconds*self.scene.settings.drawing_hz)/self.scene.settings.drawing_hz,self.scene.shape,vertices=np.eye(3),include_depth=True)
        origin = np.array([65.,h-65.])
        # Small vector X/Y/Z glyphs keep the entire orientation key on the GPU.
        glyphs = [([[0,0],[6,8]],[[6,0],[0,8]]),([[0,0],[3,4],[6,0]],[[3,4],[3,8]]),([[0,0],[6,0],[0,8],[6,8]],)]
        for i in range(3):
            end = origin+axes[i,:2]*[39,-39]
            pieces = [stroke_triangles([origin,end],2)]
            label = end+[5,-5]
            pieces.extend(stroke_triangles(label+np.array(line),1.5) for line in glyphs[i])
            layers.append((np.concatenate(pieces),QColor(('#ff7979','#8ee78e','#80b8ff')[i])))
        scope.overlays = layers

    def swapped(self):
        self.frame_count += 1; self.frame_times.append(time.perf_counter())
        if self.ready_file:
            self.ready_file.write_text('ready',encoding='utf-8'); self.ready_file = None

    def backend_info(self):
        rhi = self.host.view.rhi()
        self.backend = str(self.host.view.rendererInterface().graphicsApi()).split('.')[-1]
        if rhi:
            info = rhi.driverInfo()  # Retain the struct owning the QByteArray view.
            self.backend += ' / '+bytes(info.deviceName).decode(errors='replace')
        self.backend_label.setText(self.backend)

    def tick(self):
        now = time.perf_counter()
        self.tick_times.append(now)
        while not self.jobs.empty():
            kind,value = self.jobs.get()
            if kind.startswith('capture_'):
                self.capture_scanning = False
                if kind == 'capture_devices':
                    selected = self.capture_devices.currentData()
                    self.capture_devices.clear(); self.capture_devices.addItem('All system audio (Core Audio)' if sys.platform == 'darwin' else 'Default Windows playback device',None)
                    for name in value:
                        if name != self.capture_devices.itemText(0): self.capture_devices.addItem(name,name)
                    self.capture_devices.setCurrentIndex(max(0,self.capture_devices.findData(selected)))
                    self.capture_notice = '' if value else 'No system-audio sources found. Refresh and try again.'
                else: self.capture_notice = value
            elif kind.startswith('ports'):
                self.scanning = False
                if kind == 'ports':
                    blocker = QSignalBlocker(self.devices); self.devices.clear(); self.devices.addItems(value); self.devices.setCurrentText(self.scene.device)
                else: self.audio.midi.notice = value
            elif kind.startswith('export'):
                self.exporting = False
                if kind == 'export_error': self.error(value)
                else: self.status.setText('Exported '+value)
        if self.launch_pending:
            process,ready,deadline = self.launch_pending
            if ready.exists():
                ready.unlink(); self.launch_pending = None; self.force_close = True; self.close(); return
            if process.poll() is not None or time.monotonic() > deadline:
                if process.poll() is None: process.terminate()
                self.launch_pending = None
                self.centralWidget().setEnabled(True); self.menuBar().setEnabled(True)
                self.error('The new window did not become ready. The current scene is still open.')
        midi = self.audio.midi
        result = midi.poll_port_check()
        if result and result[0] == midi.port_name and result[2] == midi.port_epoch and result[1]:
            self.disconnect_midi(); midi.notice = result[1]
        if midi.port_name and now-self.last_midi > 2: self.last_midi = now; midi.check_port_async()
        if now-self.last_status > .2:
            self.last_status = now
            self.play_button.setText('■ STOP AUDIO' if self.audio.running else '▶ PLAY AUDIO')
            self.status.setText(('EXPORTING WAV…  |  ' if self.exporting else '')+('Playing' if self.audio.running else 'Audio stopped')+f'  |  48 kHz stereo XY  |  Underruns: {self.audio.underruns}  |  Clipped samples: {self.audio.clipped_samples}'+('  |  '+self.audio.error if self.audio.error else ''))
            if self.audio.running and self.audio.live:
                self.status.setText(self.status.text()+f'  |  MIDI buffer: {self.audio.block_frames} frames · device latency: {self.audio.output_latency*1000:.1f} ms'+(f' · {self.audio.output_mode}' if self.audio.output_mode else ''))
            if getattr(self,'redraw_notice',''):self.status.setText(self.status.text()+'  |  '+self.redraw_notice)
            self.midi_status.setText(f'{"RECORDING — " if midi.capture else ""}{midi.port_name or "MIDI not connected"}\nVoices: {midi.voice_count}/8\n{midi.notice}\n{midi.last_message}')
            if self.system_monitor:
                c = self.capture
                self.play_button.setText('■ STOP VISUALIZATION' if c.running else '▶ START VISUALIZATION')
                self.capture_devices.setEnabled(not c.running)
                self.capture_refresh.setEnabled(not c.running and not self.capture_scanning)
                waiting = c.ready and (c.clock()-c.last_sample > c.STALE_SECONDS or c.peak < 1e-6)
                state = 'Waiting for audio / silence' if waiting else 'Visualizing' if c.ready else 'Starting capture…' if c.running else 'Capture stopped'
                details = f'{c.device_name}\n{c.sample_rate} Hz · {c.channels} channel(s) · {c.frames} captured frames\nRaw input peak: {0. if waiting else c.peak:.4f}\nCapture status events: {c.status_events}' if c.device_name else ''
                self.capture_status.setText(c.error or self.capture_notice or details)
                self.status.setText(f'SYSTEM AUDIO · VISUALIZATION ONLY  |  {state}'+('  |  '+c.error if c.error else ''))
        if self.isMinimized(): return
        start = time.perf_counter(); settings = self.output_settings()
        seconds = now-self.started
        elapsed = min(.25,max(0.,start-getattr(self,'last_phosphor_time',start))); self.last_phosphor_time = start
        new_frames = elapsed*settings.sample_rate
        if not self.audio.running:settings=replace(settings,motion_time_offset=self.started-self.motion_epoch)
        if self.system_monitor:
            signal = self.capture.samples(round(.04*self.capture.sample_rate))
            new_frames = elapsed*self.capture.sample_rate
        elif self.audio.running:
            signal, playback_frame = self.audio.playback_snapshot(1920)
            seconds = playback_frame/settings.sample_rate
            new_frames = playback_frame-self.last_playback_frame if self.last_playback_frame is not None else 0
            self.playback_ticks += 1
            if playback_frame != self.last_playback_frame:
                self.fresh_playback_windows += 1
            self.last_playback_frame = playback_frame
        else:
            signal = None
            self.last_playback_frame = None
        if signal is None:
            times = seconds+np.arange(1920)/settings.sample_rate
            signal = select_signal(*render(times,settings),settings,self.scene.mode)
        if self.time_trace.isVisible() and not self.immersive:
            rate=self.capture.sample_rate if self.system_monitor else settings.sample_rate
            self.time_trace.set_samples(signal,rate,PALETTES[self.preferences['palette']])
        scope = self.host.scope
        trace = interpolate_trace(signal,self.preferences['trace_interpolation'])
        scope.signal = trace*self.visualization_gain if self.system_monitor else trace
        scope.colors = PALETTES[self.preferences['palette']]
        scope.glow = self.preferences['crt_glow']; scope.scanlines = self.preferences['crt_scanlines']; scope.divisions = self.preferences['grid_divisions']
        for key in VISUAL_DEFAULTS: setattr(scope,key,self.preferences[key])
        scope.history_key = (self.system_monitor,self.scene.settings.source,self.scene.mode,self.capture.device_name)
        self.update_phosphor(new_frames,elapsed)
        if self.system_monitor or self.immersive:
            scope.overlays = []; self.handles = []
        else: self.overlay(seconds,seconds+settings.motion_time_offset)
        scope.update()
        if self.building:
            preview=self.builder.preview.scope
            preview.signal=scope.signal;preview.colors=scope.colors;preview.glow=scope.glow
            preview.scanlines=scope.scanlines;preview.divisions=scope.divisions;preview.overlays=[]
            for key in VISUAL_DEFAULTS:setattr(preview,key,getattr(scope,key))
            self.builder.preview.view.setColor(QColor(scope.colors['background']));preview.update()
            self.builder.play_button.setText('■ STOP AUDIO' if self.audio.running else '▶ PLAY AUDIO')
        self.prep_ms.append((time.perf_counter()-start)*1000)


def settle_garbage():
    """Collect, then exempt everything alive from later collections.

    A full collection can take about 10 ms and runs on whichever thread happens to
    allocate, often the audio callback, whose budget is 2.67 ms. That is heard as a
    short, irregular stutter. Freezing long-lived objects keeps collections small.
    """
    gc.collect()
    gc.freeze()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene',type=Path); parser.add_argument('--ready-file')
    parser.add_argument('--seconds',type=float); parser.add_argument('--report',type=Path)
    parser.add_argument('--screenshot',type=Path); parser.add_argument('--play',action='store_true')
    parser.add_argument('--exercise',action='store_true')
    parser.add_argument('--diagnose-playback',action='store_true',help='Run a 13-second stopped/play/stopped native performance comparison, then exit.')
    parser.add_argument('--system-audio',action='store_true',help='Select visualization-only native system-audio capture; --play starts capture.')
    options = parser.parse_args()
    if options.system_audio and not SYSTEM_AUDIO_AVAILABLE: parser.error(SYSTEM_AUDIO_NOTICE)
    if options.diagnose_playback:
        if options.play or options.exercise or options.system_audio:
            parser.error('--diagnose-playback cannot be combined with --play, --exercise or --system-audio')
        options.seconds = 13
    configure_graphics()
    surface = QSurfaceFormat(); surface.setSamples(4); QSurfaceFormat.setDefaultFormat(surface)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    prepare_app_fonts()
    window = Window(options); window.show()
    settle_garbage()
    diagnostics = None
    if options.diagnose_playback:
        from playback_diagnostics import PlaybackDiagnostics
        diagnostics = PlaybackDiagnostics(window)
        window.centralWidget().setEnabled(False); window.menuBar().setEnabled(False)
        window.setWindowTitle('Performance diagnostic — stopped / playing / stopped (13 seconds)')
        diagnostic_timers = []
        def diagnostic_at(milliseconds, callback):
            timer = QTimer(window); timer.setSingleShot(True)
            timer.setTimerType(Qt.TimerType.PreciseTimer)
            timer.timeout.connect(callback); timer.start(milliseconds)
            diagnostic_timers.append(timer)
        diagnostic_at(1000,lambda:diagnostics.begin('stopped_before'))
        diagnostic_at(4000,diagnostics.end)
        diagnostic_at(4100,diagnostics.start_audio)
        diagnostic_at(5000,lambda:diagnostics.begin('playing'))
        diagnostic_at(8000,diagnostics.end)
        diagnostic_at(8100,window.shutdown_audio)
        diagnostic_at(9000,lambda:diagnostics.begin('stopped_after'))
        diagnostic_at(12000,diagnostics.end)
    if options.system_audio:
        window.monitor.setCurrentIndex(1); window.monitor_changed(1)
    if options.play: QTimer.singleShot(1000,window.play)
    if options.exercise:
        def exercise():
            i = int(time.perf_counter()-window.started)
            if i == 1: window.shape_action('add')
            if i == 2: window.edit('solid','Octant sphere')
            if i == 3: window.edit('rotation_x',45)
            if i == 4: window.edit('position_x',1.2)
            if i == 5: window.edit('shape_size',2.)
            if i == 6: window.apply_preferences(dict(window.preferences,palette='Amber'))
        exercise_timer = QTimer(); exercise_timer.setInterval(1000); exercise_timer.timeout.connect(exercise); exercise_timer.start()
    def finish():
        window.timer.stop(); window.shutdown_audio()
        if options.screenshot:
            options.screenshot.parent.mkdir(parents=True,exist_ok=True)
            viewport = window.host.view.grabWindow()
            # QWidget.grab excludes native child windows. Composite only our own
            # Quick readback, avoiding screenshots of unrelated desktop windows.
            screenshot = window.grab(); painter = QPainter(screenshot)
            painter.drawImage(window.host.mapTo(window,QPoint(0,0)),viewport); painter.end()
            screenshot.save(str(options.screenshot))
            viewport.save(str(options.screenshot.with_name(options.screenshot.stem+'-viewport.png')))
        def stats(values):
            values = list(values)[10:]
            return dict(median=float(np.median(values)),p95=float(np.percentile(values,95))) if values else {}
        intervals = np.diff(list(window.frame_times))[10:]*1000
        frame_pacing = dict(fps=1000/float(np.mean(intervals)),
            median_interval_ms=float(np.median(intervals)),p95_interval_ms=float(np.percentile(intervals,95))) if len(intervals) else {}
        report = dict(version=VERSION,backend=window.backend,frames=window.frame_count,frame_pacing=frame_pacing,audio_frames=window.audio.frames,
            audio_underruns=window.audio.underruns,audio_error=window.audio.error,
            audio_block_frames=window.audio.block_frames,device_output_latency_ms=window.audio.output_latency*1000,audio_output_mode=window.audio.output_mode,
            synthesis_ms=stats(window.audio.synthesis_ms),audio_write_wait_ms=stats(window.audio.write_wait_ms),
            playback_viewport_ticks=window.playback_ticks,fresh_playback_windows=window.fresh_playback_windows,
            sample_preparation_ms=stats(window.prep_ms),geometry_upload_ms=stats(window.host.scope.upload_ms),
            shape_count=len(window.scene.settings.shapes),corner_dwell_us=window.signal_preferences['corner_dwell_us'],note='Upload timing excludes GPU completion.')
        if diagnostics is not None: report['playback_comparison'] = diagnostics.report()
        if window.system_monitor:
            report.update(system_audio=True,captured_frames=window.capture.frames,
                capture_device=window.capture.device_name,capture_rate=window.capture.sample_rate,
                capture_channels=window.capture.channels,capture_error=window.capture.error,
                capture_status_events=window.capture.status_events,capture_peak_max=window.capture.peak_max,visualization_gain=window.visualization_gain,
                trace_interpolation=window.preferences['trace_interpolation'],display_points=len(window.host.scope.signal))
        if options.report:
            options.report.parent.mkdir(parents=True,exist_ok=True); options.report.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2)); window.force_close = True; window.close()
    if options.seconds: QTimer.singleShot(round(options.seconds*1000),finish)
    app.aboutToQuit.connect(window.shutdown_audio)
    return app.exec()


if __name__ == '__main__': sys.exit(main())
