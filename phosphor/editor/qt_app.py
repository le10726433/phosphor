"""Qt editor with native Metal (macOS) and Direct3D11 (Windows) rendering."""
import argparse
from contextlib import contextmanager
import gc
from dataclasses import replace
from pathlib import Path
from collections import deque
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import uuid

import numpy as np
from PySide6.QtCore import Qt, QTimer, QSignalBlocker, Signal, Slot, QPoint, QPointF, QEvent, QSize, QUrl
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette, QAction, QSurfaceFormat, QPainter, QKeySequence, QKeyEvent, QIcon, QPixmap, QPen, QPolygonF, QValidator, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QTabWidget, QScrollArea,
    QListWidget, QSlider, QLineEdit, QFileDialog, QMessageBox, QDialog, QCheckBox,
    QDialogButtonBox, QTextBrowser, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QSplitter, QSplitterHandle, QSizePolicy, QLayout, QStackedWidget, QToolBar, QToolButton, QMenu)
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from phosphor.editor.qt_platform import configure_graphics, graphics_api, SYSTEM_AUDIO_AVAILABLE, SYSTEM_AUDIO_NOTICE
from phosphor.editor.history import (History, State, describe, SceneState, describe_scene, saved_value,
                                     with_live, heavy_payloads, STRUCTURAL, SCENE_HISTORY_BUDGET)
from phosphor.engine.midi_notes import read_midi, import_notes, write_midi
from phosphor.storage import autosave, crash_log
from phosphor.audio.audio_preview import AudioPreview
from phosphor.audio import output_devices
from phosphor.storage.app_paths import APP_DIR as HERE, ASSETS_DIR, USER_DIR, relaunch_command
from phosphor.storage.transform_presets import TRANSFORM_PRESETS_PATH
from phosphor.engine.transform_presets import (STEP as TRANSFORM_STEP, STACK as TRANSFORM_STACK,
                                               APPEND as TRANSFORM_APPEND, REPLACE as TRANSFORM_REPLACE)
from phosphor.storage.app_files import (read_preferences, DEFAULT_PREFERENCES, validated_preferences,
                       PREFERENCES_PATH, atomic_json, read_scene)
from phosphor.engine.signal_engine import render, select_signal, source_duration, export_wav, projected_solid, posed_object, shape_geometry, planned_components, Shape,posed_shapes,is_motion_guide,world_motion_route,validate,planned_path,scene_shapes,MAX_SPIN
from phosphor.engine.custom_shape import Drawing, CUSTOM_OBJECT, compiled
from phosphor.engine.model_object import MODEL_OBJECT, MAX_MODEL_EDGES
from phosphor.engine.mesh_formats import load_model, read_mesh, source_triangles, MODEL_FILTERS
from phosphor.editor.model_import_dialog import ModelImportDialog, run_with_progress, tradeoff_notes
from phosphor.engine.svg_import import load_svg
from phosphor.audio.midi_synth import MIDI_SOURCE, NOTE_DECAYS
from phosphor.audio.score_source import (SOURCES, MIDI_FILE_SOURCE, SCALE_NAME, TONE_SOURCE, SCALE_TYPES, NOTE_RANGE, TONE_RANGE,
    SCORE_FOLDER, file_tempo, note_name, scale_ladder, score_arrays)
from phosphor.engine.solids import (
    SOLIDS, PLATONIC_SOLIDS, EXTRA_SOLIDS, POLYGONS, EXTRA_2D, CURVES, EXTRA_CURVES,
    OBJECT_GROUPS, PARAMETRIC_SOLIDS, ANIMATED_SOLIDS, STAR_SOLIDS, GEAR_SOLIDS,
    RADAR_SOLID, CLOCK_SOLID, ShapeParams, default_shape_params,
)
from phosphor.engine.vector_text import TEXT_OBJECT, MAX_TEXT_LENGTH
from phosphor.engine.matrix_rain import (MATRIX_OBJECT, MATRIX_LANGUAGES, MAX_SAMPLE_LENGTH, MAX_MATRIX_COLUMNS,
    MAX_MATRIX_ROWS, TEXT_SPEED_FIELD, FALL_SPEED_FIELD, MatrixConfig)
from phosphor.engine.scene_path import TRAVEL_MODES, MAX_TRAVEL_SPEED, INSTANT_TRAVEL
from phosphor.editor.control_values import rotation_position, rotation_value, parse_rotation, format_rotation, integer_degrees
from phosphor.display.crt_theme import PALETTES, button_colors
from phosphor.display import theme as themes
from phosphor.display.branding import APP_NAME, logo_path
from phosphor.editor.qt_scene import Scene
from phosphor.editor.text_undo import TextUndoKeys, text_target, text_history
from phosphor.engine.signal_engine import audition_shapes, object_id, object_stack, shape_connectors, cached_route_positions, _final_pose
from phosphor.display.qt_scope import GpuScope
from phosphor.engine.automation import lane_points
from phosphor.engine.lane_access import get_points, set_points, default_points
from phosphor.engine.object_replace import replaced, needs_input, equation_variables
from phosphor.editor.transform_panel import TransformPanel, ParamField, AUTOMATED, automation_actions, switch_action
from phosphor.editor.composer_view import ComposerPanel, position_text, AUDIO_TRACK
from phosphor.editor.lane_strip import LANE_HEIGHT
from phosphor.engine.tempo_map import Meter
from phosphor.engine.composition import (COMPOSER, SANDBOX, WORKSPACES, BEATS_PER_BAR, DEFAULT_CLIP_BEATS, MAX_BEATS, new_project,
    render_song, song_cycle, song_seconds, export_song, add_clip, remove_clip, fit_clip, with_clip, duplicated_clip_named, validate_project, clip_settings)
from phosphor.engine.track_chain import ChainState
from phosphor.storage.project_files import read_project, save_project, project_document, read_workspace, write_workspace, PROJECT_SUFFIX
from phosphor.engine.audio_clips import audio_clip_from_file, audio_clip_end_beats, decoded, alias_decoded, matches, processing_tail
from phosphor.engine import audio_clips
from phosphor.engine import audio_edit, audio_media, audio_tracks
from phosphor.editor.composer_view import track_layout
from phosphor.engine.transforms import START_FIELD, TRACE_FIELD, PATH_HELP, PATH_EXPRESSION_HELP, path_expression, stack_varies_with_time
from phosphor.engine.transforms import apply_topology_stack, has_topology, uses_position, variable_values
from phosphor.editor.function_panel import FunctionPanel
from phosphor.editor.browser_panel import BrowserPanel, LeftPanel
from phosphor.editor.quick_add import QuickAddDialog, collect_entries, FILTER_ALL, FILTER_TRANSFORMS
from phosphor.editor.track_effects_window import TrackEffectsPanel
from phosphor.editor.recurrence_panel import RecurrencePanel
from phosphor.engine.recurrence import RECURRENCE_OBJECT, RECURRENCE_GROUPS
from phosphor.editor.video_player import VideoPanel, VideoPlayer, LINE_WIDTH, field_gain, clock
from phosphor.engine.function_plot import PARAMETRIC_OBJECT, PARAMETRIC_GROUPS
from phosphor.engine.transform_presets import BUILTIN_RECIPES
from phosphor.editor.preference_panels import AppearanceEditor, SignalOutputEditor
from phosphor.display.time_sweep import DEFAULT_WINDOW_MS, EDGES, EDGE_RISING, SEPARATION_RANGE, TRIGGERS, TRIGGER_BOTH, TRIGGER_X, WINDOW_RANGE_MS, DualSweep
from phosphor.storage.signal_preferences import (SIGNAL_PREFERENCES_PATH, DEFAULT_SIGNAL_PREFERENCES,
    validated_signal_preferences,read_signal_preferences,MAX_REDRAWS)
from phosphor.engine.corner_dwell import MAX_DWELL_US
from phosphor.engine.route_plan import ROUTE_MODES
from phosphor.audio.system_audio import SystemAudio
from phosphor.display.trace_interpolation import INTERPOLATION_FACTORS, interpolate_trace
from phosphor.display.phosphor import PHOSPHOR_OFF
from phosphor.display.qt_phosphor import PhosphorLayer
from phosphor.display.glow_trail import VISUAL_DEFAULTS, VISUAL_RANGES
from phosphor.editor.audio_help import AUDIO_TROUBLESHOOTING, MACOS_AUDIO_TROUBLESHOOTING
from phosphor.display.qt_geometry import stroke_triangles, stroke_edges
from phosphor.editor import monitor_window
from phosphor.editor.manual_view import ManualWindow
from phosphor.editor.monitor_window import exit_key
from phosphor.editor.shortcuts import key_sequences, default_key_sequences, set_overrides as set_shortcut_overrides
from phosphor.storage.shortcut_bindings import (SCOPES as SHORTCUT_SCOPES, ShortcutBindings, read_bindings, write_bindings,
                                               SHORTCUT_BINDINGS_PATH)
from phosphor.editor.shortcuts_view import ShortcutsView
from phosphor.storage.build_info import VERSION, BUILD_DATE, PROJECT_DATE, TRANSPARENCY
from phosphor.storage import recent_files
from phosphor.editor.paint_guard import guarded_paint
from phosphor.editor.cycle_inspector import CycleInspectorPanel
from phosphor.engine.switch_lane import lanes_off
from phosphor.editor.panel_widgets import InlineCaption, FlowRow
from phosphor.editor.layout_metrics import (ENTRY_WIDTH, ENTRY_MIN, SLIDER_STRETCH, ENTRY_STRETCH, PANEL_RANGE,
                                             PANEL_DEFAULT, SPLIT_HANDLE, SPIN_MIN, panel_width,
                                             BROWSER_RANGE, BROWSER_DEFAULT, browser_width)


PANEL_INPUT, PANEL_SCENE, PANEL_MIX, PANEL_APPEARANCE, PANEL_SIGNAL = 'Input', 'Scene', 'Mix', 'Appearance', 'Signal output'
PANELS = (PANEL_INPUT, PANEL_SCENE, PANEL_MIX, PANEL_APPEARANCE, PANEL_SIGNAL)
BROWSER = 'Browser'  # The bar button that shows the browser panel (AW4); not a side panel.
PANEL_INSPECT = 'Inspect cycle'  # No bar button: View › Inspect one cycle and the Objects list menu open it.
MONITOR_INPUTS = ('Generated scene / MIDI', 'System audio (visualization)', 'Video file (phosphor CRT)')
MONITOR_GENERATED, MONITOR_SYSTEM, MONITOR_VIDEO = range(3)
NO_MIDI_DEVICE = 'None'
# Viewport handles (UI1): shown while the pointer moves over the scope and for HANDLE_HOLD
# seconds after it last moved, then faded out over HANDLE_FADE; always shown during a drag.
# HANDLE_DIM is their opacity at full show (a dimmed accent); AXIS_OPACITY the XYZ key's.
HANDLE_HOLD, HANDLE_FADE, HANDLE_DIM = 1.5, .4, .55
AXIS_OPACITY = .5
_HANDLE_ANGLES = np.linspace(0, 2*np.pi, 20)
_HANDLE_RING = stroke_triangles(np.column_stack([np.cos(_HANDLE_ANGLES), np.sin(_HANDLE_ANGLES)])*6, 1.5)
_CORNER_BOX = stroke_triangles(np.array([[-6,-6],[6,-6],[6,6],[-6,6],[-6,-6]], dtype=np.float32), 1.5)
_AXIS_GLYPHS = tuple(
    np.concatenate([stroke_triangles(np.array(line, dtype=np.float32), 1.5) for line in lines])
    for lines in (([[0,0],[6,8]], [[6,0],[0,8]]), ([[0,0],[3,4],[6,0]], [[3,4],[3,8]]), ([[0,0],[6,0],[0,8],[6,8]],))
)

def _outline_geometry(seconds, shape):
    """(points, edges) of shape's muted outline in object space, posed at seconds. shape is posed
    (posed_shapes: audition state, lanes and On/Off steps resolved). Topology steps (Subdivide,
    Gridify, Radial array, Kaleidoscope, Echo) add their vertices and edges, as the audio path
    builds them; a bypassed stack draws the bare object."""
    raw, edges, route = shape_geometry(shape)
    stack = object_stack(shape)
    if not stack or not has_topology(stack):
        return posed_object(seconds, shape, vertices=raw)[0], edges
    values = variable_values(shape.transform_variables, seconds)
    connectors = shape_connectors(shape)
    s = cached_route_positions(raw, route) if uses_position(stack, tuple(values)) else None
    posed, route, edges, _, _ = apply_topology_stack(
        np.asarray(raw, dtype=float), route, edges, connectors[0] if connectors else np.empty((0, 2), dtype=int),
        stack, seconds, s, values=values)
    return _final_pose(seconds, shape, posed)[:, :2], edges


def muted_outline(cache, seconds, shape, w, h, scale):
    """Stroke triangles of a muted or switched-off object's faint outline in a w x h view.
    shape is posed from the audition copy (posed_shapes(audition_shapes(...))); the outline and
    its cache decision use the same bypass-aware, On/Off-resolved stack. cache: a dict, at most
    256 entries; a static outline is reused across poses."""
    pose_key = None if _outline_is_static(shape, object_stack(shape)) else seconds
    outline_key = (shape, w, h, scale, pose_key)
    tris = cache.get(outline_key)
    if tris is None:
        points, edges = _outline_geometry(seconds, shape)
        if len(edges):
            points = (points*shape.shape_size+[shape.position_x, shape.position_y])*[scale,-scale]+[w/2,h/2]
            tris = stroke_edges(points, edges, .7)  # One pass for every edge (audit P3).
        else:
            tris = np.empty((0, 2), dtype=np.float32)
        if len(cache) >= 256: cache.pop(next(iter(cache)))
        cache[outline_key] = tris
    return tris


def _outline_is_static(shape, transforms):
    """Whether shape's outline is the same at every pose. transforms: the stack that is drawn
    (object_stack of the posed shape: bypass-aware, On/Off steps resolved)."""
    if shape.spin_x or shape.spin_y or shape.spin_z:
        return False
    if shape.solid == MODEL_OBJECT and shape.model.animation is not None:
        return False
    if shape.solid in (PARAMETRIC_OBJECT, RECURRENCE_OBJECT, MATRIX_OBJECT, *ANIMATED_SOLIDS) or shape.motion is not None or shape.lanes:
        return False
    if stack_varies_with_time(transforms):
        return False
    if transforms and any(v.expression or v.points for v in shape.transform_variables):
        return False  # Variables move the outline only through a stack that is drawn.
    return True
# The note roll's view settings, kept in the preferences file (UI1 adds the velocity lane's collapse).
# What the Appearance side panel's Defaults and Revert reset (its editor's own preferences).
APPEARANCE_KEYS = ('style', 'style_mode', 'palette', 'show_grid', 'grid_style', 'grid_divisions', 'crt_scanlines', 'crt_glow', 'trace_interpolation', 'phosphor',
                   'beam_profile', 'graphics_quality', 'phosphor_custom', 'display_rate', *VISUAL_DEFAULTS)
ROLL_VIEW_KEYS = ('roll_scale', 'roll_scale_root', 'roll_scale_snap', 'roll_hear_notes', 'roll_velocity_lane',
                  'roll_velocity_collapsed', 'roll_audio_gain_lane', 'roll_audio_lane')


class ElidedLabel(QLabel):
    """Single line retaining full text for callers and tooltips."""
    def __init__(self, text=''):
        super().__init__()
        self._full_text = ''
        self.setWordWrap(False)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def text(self):
        return self._full_text

    def setText(self, text):
        self._full_text = text
        self.setToolTip(text)
        self._elide()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, self.contentsRect().width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange: self._elide()  # Style sheets set the font later.


class TravelSpeedBox(QDoubleSpinBox):
    """Faster travel multiplier, 1 to MAX_TRAVEL_SPEED; one step above it (or any larger
    typed value, or "instant") shows Instant and means travel takes no time."""
    INSTANT_STEP = MAX_TRAVEL_SPEED+1

    def __init__(self):
        super().__init__()
        self.setRange(1, self.INSTANT_STEP); self.setDecimals(3); self.setKeyboardTracking(False)

    @classmethod
    def speed(cls, value): return INSTANT_TRAVEL if value > MAX_TRAVEL_SPEED else float(value)

    def setValue(self, value): super().setValue(self.INSTANT_STEP if value > MAX_TRAVEL_SPEED else value)

    def textFromValue(self, value): return 'Instant' if value > MAX_TRAVEL_SPEED else super().textFromValue(value)

    def valueFromText(self, text):
        text = text.strip().lower()
        if text and 'instant'.startswith(text): return self.INSTANT_STEP
        try: value = float(text)
        except ValueError: return self.value()
        return self.INSTANT_STEP if value > MAX_TRAVEL_SPEED else value

    def fixup(self, text):
        typed = text.strip().lower()
        return 'Instant' if typed and 'instant'.startswith(typed) else text

    def validate(self, text, position):
        typed = text.strip().lower()
        if typed and 'instant'.startswith(typed):
            return (QValidator.State.Acceptable if typed == 'instant' else QValidator.State.Intermediate), text, position
        try: value = float(typed)
        except ValueError:
            return (QValidator.State.Intermediate if typed in ('', '.', '-') else QValidator.State.Invalid), text, position
        return (QValidator.State.Acceptable if value >= 1 and math.isfinite(value) else QValidator.State.Intermediate), text, position


def object_label(shape):
    """The objects list name of a scene object."""
    prefix = 'Motion: ' if is_motion_guide(shape) else ''
    if shape.solid == TEXT_OBJECT: return prefix+f'Text: {shape.text[:20] or "(blank)"}'
    if shape.solid == MATRIX_OBJECT:
        cfg = shape.matrix or MatrixConfig()
        return prefix+f'Matrix: {cfg.language_a.split()[0]} + {cfg.language_b.split()[0]}'
    if shape.solid == CUSTOM_OBJECT: return prefix+shape.custom.name
    if shape.solid == MODEL_OBJECT: return prefix+shape.model.name
    if shape.solid == RECURRENCE_OBJECT: return prefix+f'Recurrence: x = {shape.recurrence.x[:18]}'
    if shape.solid == PARAMETRIC_OBJECT:
        depth = ' · 3D' if shape.plot.z != '0' else ''
        return prefix+f'Equation: x = {shape.plot.x[:18]}{depth}'
    if shape.solid in STAR_SOLIDS and shape.shape_params is not None:
        return prefix+f'Star ({shape.shape_params.points}-point)'
    if shape.solid in GEAR_SOLIDS and shape.shape_params is not None:
        return prefix+f'Gear ({shape.shape_params.teeth}-tooth)'
    return prefix+shape.solid


def scale_end_for(kind, root, span):
    """The end note keeping the previous span when it is in the new scale, else the next scale note up."""
    notes = [note for note in scale_ladder(kind, root, NOTE_RANGE[1]) if note > root]
    if not notes: raise ValueError(f'No higher {kind.lower()} note fits above {note_name(root)}.')
    return next((note for note in notes if note >= root+span), notes[-1])


def bar_icon(name, color, metrics):
    """Simple line-art placeholder icons for the application bar, in the palette's colour."""
    pixmap = QPixmap(metrics.bar_icon_canvas, metrics.bar_icon_canvas); pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(metrics.bar_icon_canvas / 48, metrics.bar_icon_canvas / 48)  # Artwork uses 48-unit coordinates.
    pen = QPen(QColor(color), metrics.bar_icon_stroke); pen.setCapStyle(Qt.PenCapStyle.RoundCap); pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
    def line(points): painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in points]))
    if name == 'input':  # A sine wave.
        line([(6+36*i/40, 24-12*math.sin(2*math.pi*i/40)) for i in range(41)])
    elif name == 'browser':  # A folder.
        line([(6, 14), (6, 39), (42, 39), (42, 18), (22, 18), (18, 12), (6, 12), (6, 14)])
        painter.drawLine(6, 22, 42, 22)
    elif name == 'scene':  # A wireframe cube.
        painter.drawRect(8, 18, 22, 22); painter.drawRect(18, 8, 22, 22)
        for x, y in ((8, 18), (30, 18), (8, 40), (30, 40)): painter.drawLine(x, y, x+10, y-10)
    elif name == 'mix':  # Three faders.
        for x, knob in ((12, 30), (24, 16), (36, 24)):
            painter.drawLine(x, 8, x, 40); painter.fillRect(x-5, knob-3, 10, 6, QColor(color))
    elif name == 'appearance':  # Contrast: a half-filled circle.
        painter.drawEllipse(9, 9, 30, 30); painter.setBrush(QColor(color)); painter.drawPie(9, 9, 30, 30, 90*16, 180*16)
    elif name == 'signal':  # A square wave leaving through an arrow.
        line([(5, 30), (5, 16), (14, 16), (14, 30), (23, 30), (23, 16), (30, 16)])
        painter.drawLine(30, 24, 43, 24); line([(37, 18), (43, 24), (37, 30)])
    elif name == 'fullscreen':  # Four corner brackets.
        for x, y, dx, dy in ((8, 8, 1, 1), (40, 8, -1, 1), (8, 40, 1, -1), (40, 40, -1, -1)):
            line([(x, y+10*dy), (x, y), (x+10*dx, y)])
    elif name == 'trace':  # A Lissajous figure.
        line([(24+16*math.sin(3*t), 24+16*math.sin(2*t)) for t in np.linspace(0, 2*math.pi, 120)])
    elif name == 'desk3d':  # Vintage oscilloscope cabinet with CRT + knobs.
        painter.drawRect(6, 12, 36, 24); painter.drawRect(10, 16, 16, 16)
        painter.drawEllipse(31, 17, 6, 6); painter.drawEllipse(31, 26, 6, 6)
    elif name in ('play', 'stop', 'pause'):
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(color))
        if name == 'play': painter.drawPolygon(QPolygonF([QPointF(14, 9), QPointF(38, 24), QPointF(14, 39)]))
        elif name == 'stop': painter.drawRect(12, 12, 24, 24)
        else: painter.drawRect(12, 10, 9, 28); painter.drawRect(27, 10, 9, 28)
    painter.end()
    return QIcon(pixmap)


COMPOSER_HANDLE = 9  # Height of the drag zone at the Composer panel's top edge (pixels).
MONITOR_WINDOW_PATH = USER_DIR/'monitor-window.json'  # Pop-out geometry and whether it was open.


def read_monitor_window(path=None):
    """The saved pop-out document, or None when missing or unreadable."""
    try:
        document = json.loads(Path(path or MONITOR_WINDOW_PATH).read_text(encoding='utf-8'))
        if not isinstance(document,dict) or document.get('version') != 1: return None
        rect = tuple(document[key] for key in ('x','y','width','height'))
        if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in rect): return None
        if not isinstance(document.get('open'),bool) or not isinstance(document.get('screen',''),str): return None
        return document
    except (OSError,ValueError,KeyError,TypeError): return None


def write_monitor_window(document, path=None):
    path = Path(path or MONITOR_WINDOW_PATH); path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix('.tmp'); temporary.write_text(json.dumps(dict(document,version=1)),encoding='utf-8'); temporary.replace(path)


class LineHandle(QSplitterHandle):
    """A splitter handle drawn as one faint line across its middle.

    The line sits in the centre of the grab zone, so pressing on it or a few pixels
    either side resizes (a line on the zone's last row made grabs just below it miss).
    """
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.set_theme(themes.for_widget(parent))

    def set_theme(self, theme):
        self.color = QColor(theme.colors['border'])
        self.hover_color = QColor(theme.colors['accent'])
        self.vintage = theme.style == themes.VINTAGE
        self.update()

    @guarded_paint
    def paintEvent(self, event):
        painter = QPainter(self); color = self.hover_color if self.underMouse() else self.color
        if self.vintage and not self.underMouse():
            hi = QColor('#f2eee2')
            if self.orientation() == Qt.Orientation.Horizontal:
                x = self.width() // 2
                painter.fillRect(x - 1, 0, 1, self.height(), color)
                painter.fillRect(x, 0, 1, self.height(), hi)
            else:
                y = self.height() // 2
                painter.fillRect(0, y - 1, self.width(), 1, color)
                painter.fillRect(0, y, self.width(), 1, hi)
            return
        if self.orientation() == Qt.Orientation.Horizontal: painter.fillRect(self.width() // 2, 0, 1, self.height(), color)
        else: painter.fillRect(0, self.height() // 2, self.width(), 1, color)

    def mouseDoubleClickEvent(self, event):
        # UI2: double-clicking the side panel's line restores its default width.
        self.splitter().handle_double_clicked.emit(self.splitter().indexOf(self)); event.accept()


class ComposerSplitter(QSplitter):
    """A splitter whose handles are LineHandles: Composer's top edge and the side panel's edge."""
    handle_double_clicked = Signal(int)
    def createHandle(self): return LineHandle(self.orientation(), self)




class ResetSlider(QSlider):
    resetRequested = Signal()
    def mouseDoubleClickEvent(self, event):
        self.resetRequested.emit(); event.accept()


CURVE_STEEPNESS = 4.  # Exponential slider curve: about 1/14 of full value at half travel.


def curve_position(value, low, high):
    """Slider position (0-1) for value on an exponential curve, finest near zero.

    A range symmetric about zero is mirrored, so zero stays exactly at the centre.
    """
    span = math.expm1(CURVE_STEEPNESS)
    if low < 0 < high:
        u = math.copysign(math.log1p(min(1., abs(value)/high)*span)/CURVE_STEEPNESS, value)
        return (u+1)/2
    return math.log1p(min(1., max(0., (value-low)/(high-low)))*span)/CURVE_STEEPNESS


def curve_value(position, low, high):
    span = math.expm1(CURVE_STEEPNESS)
    if low < 0 < high:
        u = 2*position-1
        return math.copysign(high*math.expm1(abs(u)*CURVE_STEEPNESS)/span, u)
    return low+(high-low)*math.expm1(position*CURVE_STEEPNESS)/span


# Mouse drags move a slider far more often than the display draws (about 1000 moves a
# second from a fast mouse). Each move is a full edit, so drags are coalesced: the latest
# value per slider waits here and is committed once per display frame (Window._display_frame
# calls flush_sliders), on release, or by the fallback timer when no frame is drawn.
_pending_sliders = {}
SLIDER_FLUSH_MS = 16
_slider_timer = None


def flush_sliders():
    while _pending_sliders:
        slider = next(iter(_pending_sliders))
        slider.commit(_pending_sliders.pop(slider))


class NumberEntry(QLineEdit):
    """A slider's number field: ENTRY_WIDTH by default, down to ENTRY_MIN in a narrow panel (UI2)."""
    def __init__(self):
        super().__init__(); self.setMinimumWidth(ENTRY_MIN)
    def sizeHint(self): return QSize(ENTRY_WIDTH, super().sizeHint().height())


class SliderInput(QWidget):
    edited = Signal(object)
    def __init__(self, default, low=0, high=1, rotation=False, integer=False, curve=False):
        super().__init__()
        self.default, self.low, self.high = default, low, high
        self.rotation, self.integer, self.curve = rotation, integer, curve
        layout = QHBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        self.slider = ResetSlider(Qt.Orientation.Horizontal); self.slider.setRange(0,10000)
        # UI2: the field widens with the panel (ENTRY_WIDTH is its minimum), sharing extra width with the slider.
        self.entry = NumberEntry()
        self.entry.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.slider,SLIDER_STRETCH); layout.addWidget(self.entry,ENTRY_STRETCH)
        self.slider.valueChanged.connect(self.moved)
        self.slider.sliderReleased.connect(self.released)
        self.entry.editingFinished.connect(self.typed)
        self.slider.resetRequested.connect(lambda: self.commit(self.default))
        self.setValue(default)

    def setValue(self, value):
        _pending_sliders.pop(self, None)  # A value set from outside replaces a waiting drag value.
        self.value = value
        position = rotation_position(value) if self.rotation else math.log(value/self.low)/math.log(self.high/self.low) if self.curve == 'log' else curve_position(value,self.low,self.high) if self.curve else (value-self.low)/(self.high-self.low)
        blocker = QSignalBlocker(self.slider)
        self.slider.setValue(round(position*10000))
        text = format_rotation(value) if self.rotation else str(int(value)) if self.integer else f'{value:.6g}'
        if self.entry.text() != text: self.entry.setText(text)  # Each edit refreshes every field.
        self.shown = self.entry.text()

    def commit(self, value):
        self.setValue(value); self.edited.emit(value)

    def moved(self, position):
        value = rotation_value(position/10000) if self.rotation else self.low*(self.high/self.low)**(position/10000) if self.curve == 'log' else curve_value(position/10000,self.low,self.high) if self.curve else self.low+(self.high-self.low)*position/10000
        value = integer_degrees(value) if self.integer else value
        if not self.slider.isSliderDown(): self.commit(value); return  # Keys, wheel, code: at once.
        global _slider_timer
        _pending_sliders[self] = value
        if _slider_timer is None:
            _slider_timer = QTimer(); _slider_timer.setSingleShot(True); _slider_timer.setInterval(SLIDER_FLUSH_MS)
            _slider_timer.timeout.connect(flush_sliders)
        if not _slider_timer.isActive(): _slider_timer.start()

    def released(self):
        if self in _pending_sliders: self.commit(_pending_sliders.pop(self))

    def typed(self):
        # editingFinished also fires when the field merely loses focus; that is not an edit.
        if self.entry.text() == getattr(self,'shown',None): return
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


class _VintageToolBar(QToolBar):
    """Application bar that paints brushed aluminum + rackmount screws when Vintage is active."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._vintage = False

    def set_vintage(self, enabled):
        enabled = bool(enabled)
        if enabled != self._vintage:
            self._vintage = enabled
            self.update()

    @guarded_paint
    def paintEvent(self, event):
        if self._vintage:
            from phosphor.editor import vintage_style
            p = QPainter(self)
            vintage_style.paint_aluminum_bar(p, self.rect())
            p.end()
            return
        super().paintEvent(event)


class SquareStage(QWidget):
    BEZEL = 34

    def __init__(self):
        super().__init__()
        self.host = ViewHost()
        self.host.setParent(self)
        self.fill_screen = False
        self._vintage = False
        self._trace_color = '#5ce677'
        # Shown instead of the host while the monitor is in its own window.
        self.placeholder = QPushButton('Signal monitor popped out\n\nClick to bring it back', self)
        self.placeholder.setObjectName('monitor_placeholder'); self.placeholder.setFlat(True); self.placeholder.hide()

    def set_vintage(self, enabled, trace_color='#5ce677'):
        enabled = bool(enabled)
        if enabled == self._vintage and trace_color == self._trace_color: return
        self._vintage = enabled
        self._trace_color = trace_color
        self.layout_host()
        self.update()

    def set_popped(self, popped):
        self.host.setVisible(not popped); self.placeholder.setVisible(popped); self.layout_host()

    def set_fill_screen(self, enabled):
        self.fill_screen = enabled
        self.layout_host()

    def layout_host(self):
        if self.fill_screen:
            self.host.setGeometry(self.rect())
        else:
            pad = self.BEZEL if self._vintage else 0
            size = max(1, min(self.width(), self.height()) - 2 * pad)
            self.host.setGeometry((self.width()-size)//2, (self.height()-size)//2, size, size)
        self.placeholder.setGeometry(self.host.geometry())

    @guarded_paint
    def paintEvent(self, event):
        if not self._vintage or self.fill_screen:
            return
        from phosphor.editor import vintage_style
        p = QPainter(self)
        vintage_style.draw_crt_hood(p, self.host.geometry(), self.BEZEL, self._trace_color)
        p.end()

    def resizeEvent(self, event):
        self.layout_host()
        super().resizeEvent(event)


def prepare_app_fonts():
    app=QApplication.instance()
    if app.property('phosphorFontsLoaded'):return
    for name in ('JetBrainsMono-Regular.ttf','JetBrainsMono-Bold.ttf'):
        if QFontDatabase.addApplicationFont(str(ASSETS_DIR/'fonts'/name))<0:
            raise RuntimeError(f'Bundled font could not be loaded: {name}')
    fonts = themes.theme_for().fonts; app.setFont(QFont(fonts.family,fonts.app_points))
    app.setProperty('phosphorFontsLoaded',True)


class Window(QMainWindow):
    def __init__(self, options):
        prepare_app_fonts()
        super().__init__()
        self.options = options
        # Two documents: the Sandbox scene, and the Composer project whose selected clip's
        # scene is edited through the same panels. self.scene is the one being edited.
        self.sandbox_scene = self.scene = Scene()
        self.solo_ids = set()
        self.transform_solos = {}
        self.audio_solos = set()  # Soloed audio clip indices (Composer; not saved).
        self.track_solos = set()  # Soloed audio track indices (Composer; not saved; AW1).
        self.project = new_project(); self.project_path = None; self.project_dirty = False
        self.clip_index = 0; self.clip_scene = None
        self.history = History(); self.restoring = False
        self.history_timer = QTimer(self); self.history_timer.setSingleShot(True)
        self.history_timer.timeout.connect(self.commit_history)
        # The Sandbox scene's own undo history (U1): scene snapshots, committed like the Composer's.
        self.scene_history = History(weigh=heavy_payloads, budget=SCENE_HISTORY_BUDGET); self.scene_restoring = False
        self.scene_history_timer = QTimer(self); self.scene_history_timer.setSingleShot(True)
        self.scene_history_timer.timeout.connect(self.commit_scene_history)
        self.playhead = 0.  # Song seconds.
        self.play_start = 0.  # Song second where Composer playback began.
        requested = getattr(options,'workspace',None)
        self.workspace = SANDBOX if options.scene else requested if requested in WORKSPACES else read_workspace()
        self.remember_workspace = requested is None
        self.audio = AudioPreview()
        self.capture = SystemAudio()
        self.system_monitor = False
        self.video_monitor = False  # Monitor input: Video file (phosphor CRT).
        self.video = None  # VideoPlayer, created on first use.
        self.current_panel = None  # Application bar panel shown on the right.
        self.visualization_gain = 1.
        # System-audio X(t)/Y(t) sweep on the phosphor display (display only).
        self.sweep = DualSweep(); self.sweep_mode = False; self.sweep_trigger = TRIGGER_X; self.sweep_edge = EDGE_RISING
        self.sweep_separation = 0.; self.sweep_window_ms = DEFAULT_WINDOW_MS; self.sweep_frame = None
        # Time view of generated signals: the second the next stopped-Sandbox samples start at (float),
        # or the position in the stopped Composer window it repeats (int).
        self.sweep_feed = None
        self.monitor_window = None  # MonitorWindow (the pop-out), created on first use.
        self.popped = False  # The monitor shows in the pop-out; the main viewport shows a placeholder.
        self.capture_scanning = False
        self.capture_notice = ''
        self.jobs = queue.Queue()
        self.exporting = self.scanning = False
        self.launch_pending = None
        self.force_close = False
        self.drag = None
        self.handles = []
        self.handles_seen = -math.inf  # perf_counter time the pointer last moved over the scope.
        self.parameters = {}; self.slider_captions = {}
        self.started = time.perf_counter()
        self.motion_epoch=self.started;self.motion_audio_offset=0.;self.motion_dialog=None
        self.last_status = self.last_midi = 0
        self.backend = graphics_api().name+' — initializing'
        self.frame_count = 0
        self.playback_ticks = self.fresh_playback_windows = 0
        self.last_playback_frame = None
        self.frame_times = deque(maxlen=4096)  # display_frame starts: when a frame's samples are prepared.
        self.swap_times = deque(maxlen=4096)  # The active host's frame swaps: frames actually presented (audit B11).
        # The first swap after each display_frame: new content presented (P7c). At '60 Hz' Qt presents
        # each frame about twice (a live: false layer's scheduleUpdate asks for one more render),
        # so the readout counts these, not every swap.
        self.content_swap_times = deque(maxlen=4096); self.frame_fresh = False
        self.display_pending = self.display_running = self.views_released = False
        self.last_display_time = self.started
        self.tick_times = deque(maxlen=4096)
        self.prep_ms = deque(maxlen=4096)
        self.preferences_dialog = None
        self.signal_dialog = None
        self.immersive = False
        self.desk3d = False
        self.desk3d_host = None
        self.builder = None
        self.building = False
        self.builder_index = None
        self.builder_replace = None  # Objects list › Replace › Custom shape…: rows the Builder's Apply replaces.
        try:self.signal_preferences=read_signal_preferences()
        except (ValueError,OSError):self.signal_preferences=dict(DEFAULT_SIGNAL_PREFERENCES)
        self.audio.requested_rate=self.signal_preferences['output_rate']
        self.audio.output_device=self.signal_preferences['output_device']
        try: self.preferences = read_preferences()
        except (ValueError,OSError): self.preferences = dict(DEFAULT_PREFERENCES)
        if options.scene: self.scene.load(options.scene)
        self.load_clip_scene(0)
        self.history.reset(self.history_state()); self.reset_scene_history()
        self.resize(1220,840); self.setMinimumSize(1000,700)
        self.apply_theme()
        self.make_ui()
        # Keyboard shortcuts (UI2): registry defaults until init_shortcuts reads the machine's file.
        self.shortcuts_path, self.shortcut_error = None, ''
        self.shortcut_bindings = ShortcutBindings(defaults=self.shortcut_defaults()); self.apply_shortcut_bindings()
        self.apply_preferences(self.preferences)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self.color_scheme_changed)
        self.set_workspace(self.workspace,initial=True)
        if self.scene.settings.source == MIDI_SOURCE: self.refresh_ports()  # Reconnects to the scene's device.
        self.host.scope.pointer = self.pointer
        self.host.view.installEventFilter(self)
        # Hover the viewport's top-right corner: pop the monitor out, or show the trace full screen.
        self.corner_buttons = monitor_window.CornerButtons(self.main_host.view.contentItem(), self.theme)
        self.corner_buttons.clicked.connect(self.corner_clicked); self.sync_monitor_controls()
        self.host.view.frameSwapped.connect(self.swapped, Qt.ConnectionType.QueuedConnection)
        self.timer = QTimer(self); self.timer.setInterval(16); self.timer.timeout.connect(self.housekeeping); self.timer.start()
        # S2: about once a second, notice a changed system default or a lost output device.
        self.output_timer = QTimer(self); self.output_timer.setInterval(1000); self.output_timer.timeout.connect(self.audio.check_output); self.output_timer.start()
        # '60 Hz': one display_frame per 1/60 s deadline (P7c, H4). The housekeeping timer gave
        # 40 a second: Qt re-arms a late repeating timer from now, and each tick waited for presents.
        self.frame_timer = QTimer(self); self.frame_timer.setSingleShot(True)
        self.frame_timer.setTimerType(Qt.TimerType.PreciseTimer); self.frame_timer.timeout.connect(self.paced_frame)
        self.defer_timer = QTimer(self); self.defer_timer.setSingleShot(True)
        self.defer_timer.setTimerType(Qt.TimerType.PreciseTimer); self.defer_timer.timeout.connect(self.queued_display_frame)
        self.next_frame_time = None
        self.deferred_frames = 0  # Display frames that waited for a starving audio stream (P7c, H1).
        self.autosave_timer = QTimer(self); self.autosave_timer.setInterval(120000)
        self.autosave_timer.timeout.connect(self.autosave_now); self.autosave_timer.start()
        QTimer.singleShot(750,self.backend_info)
        if options.ready_file:
            ready = Path(options.ready_file).resolve()
            if ready.parent != USER_DIR.resolve(): raise ValueError('Readiness file must be in the user data folder')
            self.ready_file = ready
        else: self.ready_file = None

    def menu_action(self, menu, label, callback, shortcut=None):
        action = QAction(label,self); action.triggered.connect(lambda checked=False: callback())
        if shortcut:
            action.setShortcuts(key_sequences(shortcut))
            action.setProperty('shortcut_id', shortcut)
        menu.addAction(action)
        return action

    def make_ui(self):
        file = self.menuBar().addMenu('File')
        self.menu_action(file,'New instance…',self.new_instance,'new')
        # Open/Save follow the workspace: projects in Composer, scenes in Sandbox.
        self.new_project_action = self.menu_action(file,'New project',self.new_project)
        self.open_action = self.menu_action(file,'Open scene…',self.open_document,'open')
        self.recent_menu = file.addMenu('Open Recent')
        self.recent_menu.aboutToShow.connect(self.rebuild_recent_menu)
        self.save_action = self.menu_action(file,'Save scene',self.save_document,'save')
        self.save_as_action = self.menu_action(file,'Save scene as…',lambda:self.save_document(True),'save_as')
        self.send_action = self.menu_action(file,'Send scene to Composer',self.send_to_composer)
        self.collect_media_action = self.menu_action(file,'Collect media and save',self.collect_media)
        self.collect_media_action.setToolTip('Copy the audio files the project uses into a "<project> media" folder beside it, point the clips there and save.')
        self.missing_audio_action = self.menu_action(file,'Find missing audio files…',self.show_missing_audio)
        self.menu_action(file,'Import 3D model…',self.import_model)
        self.menu_action(file,'Import SVG…',self.import_svg)
        self.import_midi_action = self.menu_action(file,'Import MIDI into notes…',lambda:self.import_midi_notes())
        self.export_midi_action = self.menu_action(file,'Export notes as MIDI…',lambda:self.export_midi_notes())
        file.addSeparator()
        self.export_source_action = self.menu_action(file,'Export source…',lambda:self.export(True))
        self.export_action = self.menu_action(file,'Export processed…',self.export)
        self.export_video_action = self.menu_action(file,'Export video…',self.export_video)
        file.addSeparator()
        self.menu_action(file,'Record MIDI performance…',self.record)
        self.menu_action(file,'Stop recording',self.audio.midi.stop_record)
        self.menu_action(file,'Exit',self.close)
        edit = self.menuBar().addMenu('Edit')
        self.undo_action = self.menu_action(edit,'Undo',self.undo,'undo')
        self.redo_action = self.menu_action(edit,'Redo',self.redo,'redo')
        edit.addSeparator()
        self.quick_add_action = QAction('Quick Add…', self)
        self.quick_add_action.setShortcuts(key_sequences('quick_add')); self.quick_add_action.setProperty('shortcut_id', 'quick_add')
        self.quick_add_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.quick_add_action.triggered.connect(lambda _=False: self.open_quick_add(FILTER_ALL))
        edit.addAction(self.quick_add_action); self.addAction(self.quick_add_action)
        self.add_audio_action = self.menu_action(edit, 'Add audio clip at playhead…', self.choose_audio_clip)
        for action in (self.undo_action, self.redo_action):
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            self.addAction(action)
        self.text_undo_keys = TextUndoKeys(self)
        QApplication.instance().focusChanged.connect(self.watch_text_undo)
        self.update_history_actions()
        pref = self.menuBar().addMenu('Preferences')
        self.menu_action(pref,'Appearance…',self.show_preferences)
        self.menu_action(pref,'Signal output…',self.show_signal_preferences)
        self.menu_action(pref,'Controls…',lambda:self.show_preferences('Controls'))  # Shortcuts, scroll and trackpad (UI2).
        view = self.menuBar().addMenu('View')
        self.zoom_song_action = self.menu_action(view, 'Zoom to song', lambda:self.zoom_composer(), 'zoom_song')
        self.zoom_selection_action = self.menu_action(view, 'Zoom to selection', lambda:self.zoom_composer(selection=True), 'zoom_selection')
        for shortcut_id, callback in (
                ('toggle_library', lambda: None if self.building or self.immersive else self.toggle_browser()),
                ('panel_input', lambda: None if self.building or self.immersive else self.toggle_panel(PANEL_INPUT)),
                ('panel_scene', lambda: None if self.building or self.immersive else self.toggle_panel(PANEL_SCENE)),
                ('panel_mix', lambda: None if self.building or self.immersive else self.toggle_panel(PANEL_MIX)),
                ('panel_appearance', lambda: None if self.building or self.immersive else self.toggle_panel(PANEL_APPEARANCE)),
                ('panel_signal', lambda: None if self.building or self.immersive else self.toggle_panel(PANEL_SIGNAL)),
                ('switch_workspace', lambda: None if self.building else self.set_workspace(SANDBOX if self.workspace == COMPOSER else COMPOSER))):
            action = QAction(self); action.setShortcuts(key_sequences(shortcut_id))
            action.setProperty('shortcut_id', shortcut_id)
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(lambda checked=False, cb=callback: cb())
            self.addAction(action)
        view.addSeparator()
        self.fullscreen_action=QAction('Full screen',self);self.fullscreen_action.setCheckable(True)
        self.fullscreen_action.setShortcuts(key_sequences('fullscreen'));self.fullscreen_action.setProperty('shortcut_id','fullscreen')
        self.fullscreen_action.toggled.connect(self.set_fullscreen);view.addAction(self.fullscreen_action);self.addAction(self.fullscreen_action)
        self.immersive_action=QAction('Trace only',self);self.immersive_action.setCheckable(True)
        self.immersive_action.setShortcuts(key_sequences('trace_only'));self.immersive_action.setProperty('shortcut_id','trace_only');self.immersive_action.toggled.connect(self.set_immersive)
        view.addAction(self.immersive_action);self.addAction(self.immersive_action)
        self.desk3d_action=QAction('3D Lab Bench',self);self.desk3d_action.setCheckable(True)
        self.desk3d_action.setShortcuts(key_sequences('desk3d'));self.desk3d_action.setProperty('shortcut_id','desk3d');self.desk3d_action.toggled.connect(self.set_desk3d)
        view.addAction(self.desk3d_action);self.addAction(self.desk3d_action)
        view.addSeparator()
        self.inspect_action=QAction('Inspect one cycle',self);self.inspect_action.setCheckable(True)
        self.inspect_action.setToolTip('Freeze the pose and show one cycle of the beam: route, direction, travel and a scrub cursor (display only).')
        self.inspect_action.toggled.connect(lambda on:self.inspect_cycle(on));view.addAction(self.inspect_action)
        escape=QAction(self);escape.setShortcuts(key_sequences('escape'));escape.setProperty('shortcut_id','escape');escape.triggered.connect(self.exit_view_modes);self.addAction(escape)
        transport = self.menuBar().addMenu('Transport')
        self.chords_action = QAction('Chords', self); self.chords_action.setCheckable(True)
        self.chords_action.setToolTip('Play overlapping notes as chords (up to 8 voices)')
        self.chords_action.setStatusTip(self.chords_action.toolTip())
        self.chords_action.toggled.connect(self.toggle_chords); transport.addAction(self.chords_action)
        self.velocity_action = QAction('Velocity', self); self.velocity_action.setCheckable(True)
        self.velocity_action.setToolTip("Note velocity sets each note's loudness and drawing size")
        self.velocity_action.setStatusTip(self.velocity_action.toolTip())
        self.velocity_action.toggled.connect(self.toggle_velocity); transport.addAction(self.velocity_action)
        for label, key, shortcut in (('Play / Stop', Qt.Key.Key_Space, 'play'),
                           ('Go to start / loop start', Qt.Key.Key_Home, 'home'),
                           ('Go to song end', Qt.Key.Key_End, 'end')):
            action = self.menu_action(transport, label, lambda key=key,shortcut=shortcut:self.transport_key(key, shortcut), shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.setAutoRepeat(False)
            self.addAction(action)
        about = self.menuBar().addMenu('About')
        about_action = self.menu_action(about,'About phosphor',self.about)
        about_action.setMenuRole(QAction.MenuRole.AboutRole)
        self.menu_action(about,'Audio troubleshooting',lambda:self.about(troubleshooting=True))
        self.help_menu = self.menuBar().addMenu('Help')
        self.menu_action(self.help_menu,'Keyboard shortcuts…',self.show_shortcuts)
        self.menu_action(self.help_menu,'Manual',self.open_manual)
        self.help_menu.addSeparator()
        self.diagnostic_action = self.menu_action(self.help_menu,'Diagnostic logging (from next launch)',lambda:None)
        self.diagnostic_action.setCheckable(True); self.diagnostic_action.setChecked(self.preferences['diagnostic_logging'])
        self.diagnostic_action.toggled.connect(self.set_diagnostic_logging)
        self.menu_action(self.help_menu,'Show crash logs',self.show_crash_logs)
        self.make_app_bar()
        central = QWidget(); self.setCentralWidget(central)
        self.body = body = QVBoxLayout(central); body.setContentsMargins(18,10,18,14); body.setSpacing(8)
        # Heading, status and brightness sit in one strip above the display, so the
        # Composer timeline can take the full width of the window below it.
        self.top_row = QWidget(); self.top_row.setObjectName('top_row'); body.addWidget(self.top_row)
        self.vsplit = ComposerSplitter(Qt.Orientation.Vertical); self.vsplit.setChildrenCollapsible(False); body.addWidget(self.vsplit,1)
        # Composer's top edge: a faint 1 px line centred in a taller grab zone (LineHandle).
        self.vsplit.setObjectName('composer_split'); self.vsplit.setHandleWidth(COMPOSER_HANDLE)
        # The side panel's edge (UI2): a visible line like Composer's, PANEL_RANGE wide, double-click resets.
        self.splitter = ComposerSplitter(Qt.Orientation.Horizontal); self.splitter.setObjectName('panel_split')
        self.splitter.setHandleWidth(SPLIT_HANDLE)
        self.splitter.setChildrenCollapsible(False)
        # The browser (AW4) sits left of the scope in its own splitter, so the side panel's
        # splitter keeps its two widgets (scope, panel) and their sizing code.
        self.browser_split = ComposerSplitter(Qt.Orientation.Horizontal); self.browser_split.setObjectName('browser_split')
        self.browser_split.setHandleWidth(SPLIT_HANDLE); self.browser_split.setChildrenCollapsible(False)
        self.vsplit.addWidget(self.browser_split)
        self.browser_split.addWidget(self.splitter)
        self.browser_started = False
        self.browser_split.splitterMoved.connect(lambda *args: self.remember_browser_width())
        self.sidebar = sidebar = QWidget(); sidebar.setObjectName('sidebar'); sidebar.setMinimumWidth(PANEL_RANGE[0]); sidebar.setMaximumWidth(PANEL_RANGE[1])
        left = QVBoxLayout(sidebar); left.setContentsMargins(0,0,0,0); left.setSpacing(8)
        self.splitter.addWidget(sidebar)  # The viewport is inserted before it: controls sit on the right.
        self.panel_title = QLabel(); self.panel_title.setObjectName('panel_title'); left.addWidget(self.panel_title)
        self.panel_stack = QStackedWidget(); left.addWidget(self.panel_stack,1)
        self.panels = {}; self.fresh_panels = set()
        self.control_scrolls = []
        def page(title):
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.control_scrolls.append(scroll)
            # Explicit margins: the macOS style's 12 px sides left no room in a 340 px panel.
            panel = QWidget(); layout = QVBoxLayout(panel); layout.setSpacing(6); layout.setAlignment(Qt.AlignmentFlag.AlignTop); layout.setContentsMargins(8,8,8,8)
            scroll.setWidget(panel); self.panel_stack.addWidget(scroll); self.panels[title] = scroll
            return layout
        inp, scene, mix = page(PANEL_INPUT), page(PANEL_SCENE), page(PANEL_MIX)
        def inline(layout,label,widget,tip=''):
            row = QHBoxLayout(); layout.addLayout(row); caption = InlineCaption(label)
            row.addWidget(caption); row.addWidget(widget,1)
            if tip: caption.setToolTip(tip); widget.setToolTip(tip)
        self.inline_row = inline
        # INPUT ------------------------------------------------------------------
        inp.addWidget(QLabel('Monitor input'))
        self.monitor = QComboBox(); self.monitor.addItems(MONITOR_INPUTS)
        if not SYSTEM_AUDIO_AVAILABLE:
            self.monitor.setItemText(MONITOR_SYSTEM,'System audio — unavailable on macOS')
            self.monitor.model().item(MONITOR_SYSTEM).setEnabled(False)
            self.monitor.setItemData(MONITOR_SYSTEM,SYSTEM_AUDIO_NOTICE,Qt.ItemDataRole.ToolTipRole)
            notice = QLabel(SYSTEM_AUDIO_NOTICE); notice.setWordWrap(True); inp.addWidget(notice)
        inp.addWidget(self.monitor); self.monitor.activated.connect(self.monitor_changed)
        self.capture_panel = QWidget(); capture_layout = QVBoxLayout(self.capture_panel); capture_layout.setContentsMargins(0,0,0,0); capture_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        capture_layout.addWidget(QLabel('Audio capture source' if sys.platform == 'darwin' else 'Playback device to visualize'))
        self.capture_devices = QComboBox(); self.capture_devices.addItem('All system audio (Core Audio)' if sys.platform == 'darwin' else 'Default Windows playback device',None)
        capture_layout.addWidget(self.capture_devices)
        self.capture_refresh = QPushButton('Refresh playback devices'); capture_layout.addWidget(self.capture_refresh)
        self.capture_refresh.clicked.connect(self.refresh_capture_devices)
        # APPEARANCE › Monitor: how the signal monitor shows any input (display only), and the pop-out.
        self.monitor_page = QWidget(); self.monitor_page.setObjectName('monitor_page'); monitor = QVBoxLayout(self.monitor_page)
        monitor.setSpacing(5); monitor.setContentsMargins(10,8,10,8); monitor.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.sweep_display = QComboBox(); self.sweep_display.addItems(('X–Y','Time: X(t), Y(t)')); self.sweep_display.setObjectName('sweep_display')
        inline(monitor,'View',self.sweep_display,'Time draws both channels against time on the monitor. Not available for Video file input.')
        self.visualization_gain_control = SliderInput(1.,0,32,curve=True); self.visualization_gain_control.setObjectName('visualization_gain')
        self.visualization_gain_control.edited.connect(lambda value:setattr(self,'visualization_gain',value)); self.visualization_gain_control.setFixedHeight(28)
        inline(monitor,'Signal gain',self.visualization_gain_control,'Monitor gain, 0–32×, for every input. Display only, never the audio; double-click the slider to reset 1×.')
        self.monitor_brightness = SliderInput(VISUAL_DEFAULTS['beam_intensity'],*VISUAL_RANGES['beam_intensity'],curve=True)
        self.monitor_brightness.setObjectName('monitor_brightness'); self.monitor_brightness.setFixedHeight(28)
        self.monitor_brightness.edited.connect(self.set_brightness)
        inline(monitor,'Brightness',self.monitor_brightness,'Phosphor brightness: the same setting as the brightness slider above the monitor and Phosphor › Phosphor intensity.')
        self.sweep_panel = QWidget(); sweep_layout = QVBoxLayout(self.sweep_panel); sweep_layout.setContentsMargins(0,0,0,0)
        self.sweep_separation_control = SliderInput(0.,*SEPARATION_RANGE); self.sweep_separation_control.setObjectName('sweep_separation')
        self.sweep_separation_control.edited.connect(lambda value:setattr(self,'sweep_separation',value)); self.sweep_separation_control.setFixedHeight(28)
        inline(sweep_layout,'Separation',self.sweep_separation_control,'Moves the X trace up and the Y trace down from the centre line.')
        self.sweep_window_control = SliderInput(DEFAULT_WINDOW_MS,*WINDOW_RANGE_MS,curve='log'); self.sweep_window_control.setObjectName('sweep_window')
        self.sweep_window_control.edited.connect(lambda value:setattr(self,'sweep_window_ms',value)); self.sweep_window_control.setFixedHeight(28)
        inline(sweep_layout,'Window (ms)',self.sweep_window_control,'Scan window, 0.1–2000 ms. Each sweep starts on a zero crossing of the trigger channel, or free-runs without one.')
        self.sweep_trigger_control = QComboBox(); self.sweep_trigger_control.addItems(TRIGGERS); self.sweep_trigger_control.setObjectName('sweep_trigger')
        self.sweep_edge_control = QComboBox(); self.sweep_edge_control.addItems(EDGES); self.sweep_edge_control.setObjectName('sweep_edge')
        trigger_row = QWidget(); trigger_layout = QHBoxLayout(trigger_row); trigger_layout.setContentsMargins(0,0,0,0)
        trigger_layout.addWidget(self.sweep_trigger_control,1); trigger_layout.addWidget(self.sweep_edge_control,1)
        inline(sweep_layout,'Trigger',trigger_row,'Trigger channel (X = left, Y = right, Both = one sweep per trace) and edge.')
        self.sweep_both_note = QLabel('Both: X and Y no longer share a time base.')
        self.sweep_both_note.setToolTip('Each trace sweeps on its own trigger, so both stand still even at unrelated frequencies, but their timing cannot be compared.'); self.sweep_both_note.setObjectName('sweep_both_note')
        sweep_layout.addWidget(self.sweep_both_note); self.sweep_both_note.setVisible(False)
        def trigger_changed(text):
            self.sweep_trigger = text; self.sweep_both_note.setVisible(text == TRIGGER_BOTH)
        self.sweep_trigger_control.currentTextChanged.connect(trigger_changed)
        self.sweep_edge_control.currentTextChanged.connect(lambda text:setattr(self,'sweep_edge',text))
        monitor.addWidget(self.sweep_panel); self.sweep_panel.setVisible(False)
        def sweep_display_changed(index):
            self.sweep_mode = index == 1; self.sweep_panel.setVisible(self.sweep_mode); self.sweep.reset(); self.sweep_frame = None; self.sweep_feed = None
            self.update_viewer_heading()
        self.sweep_display.currentIndexChanged.connect(sweep_display_changed)
        note = QLabel('Display only. Play audio in another app.')
        note.setToolTip('No processing, recording or playback by phosphor. Monitor and device selection are temporary; scene files keep your generated scene.'); capture_layout.addWidget(note)
        if sys.platform == 'darwin':
            permission = QLabel('Needs System Audio Recording permission.')
            permission.setToolTip('Stop and restart visualization after granting it.')
            permission.setToolTip(permission.toolTip()+' Captures all apps through Core Audio: no microphone, screen images, recording or audio output.')
            permission.setWordWrap(True); capture_layout.addWidget(permission)
        self.capture_status = QLabel(); self.capture_status.setWordWrap(True); capture_layout.addWidget(self.capture_status)
        inp.addWidget(self.capture_panel); self.capture_panel.hide()
        self.video_panel = VideoPanel(SliderInput); inp.addWidget(self.video_panel); self.video_panel.hide()
        self.video_panel.open_requested.connect(self.open_video)
        self.generated_panel = QWidget(); inp.addWidget(self.generated_panel)
        generated = QVBoxLayout(self.generated_panel); generated.setContentsMargins(0,0,0,0); generated.setSpacing(6)
        self.combo(generated,'Source','source',(*SOURCES,MIDI_SOURCE))
        def section():
            widget = QWidget(); layout = QVBoxLayout(widget); layout.setContentsMargins(0,0,0,0); layout.setSpacing(6)
            generated.addWidget(widget); return widget, layout
        self.scale_panel, scale = section()
        self.scale_type_box = QComboBox(); self.scale_type_box.addItems(list(SCALE_TYPES)); self.scale_type_box.setObjectName('scale_type')
        self.scale_type_box.activated.connect(lambda index:self.edit('scale_type',self.scale_type_box.currentText()))
        inline(scale,'Scale',self.scale_type_box,'Notes climb from the start note to the end note, then come back down: one note per beat.')
        self.scale_root_box = QComboBox(); self.scale_root_box.setObjectName('scale_root')
        for note in range(NOTE_RANGE[0],NOTE_RANGE[1]): self.scale_root_box.addItem(note_name(note),note)
        self.scale_root_box.activated.connect(lambda index:self.edit('scale_root',self.scale_root_box.itemData(index)))
        inline(scale,'Start note',self.scale_root_box,'The fundamental: the scale is built on this note.')
        self.scale_end_box = QComboBox(); self.scale_end_box.setObjectName('scale_end')
        self.scale_end_box.activated.connect(lambda index:self.edit('scale_end',self.scale_end_box.itemData(index)))
        inline(scale,'End note',self.scale_end_box,'The highest note: only notes of the chosen scale.')
        self.midi_file_row = QWidget(); row = QHBoxLayout(self.midi_file_row); row.setContentsMargins(0,0,0,0)
        self.midi_file_label = QLabel(); self.midi_file_label.setMinimumWidth(0); row.addWidget(self.midi_file_label,1)
        choose = QPushButton('Choose…'); choose.clicked.connect(self.choose_midi_file); row.addWidget(choose)
        generated.addWidget(self.midi_file_row)
        self.tone_note = QLabel('One steady note. Set its frequency at the bottom of the SCENE panel.'); self.tone_note.setWordWrap(True)
        generated.addWidget(self.tone_note)
        self.tempo_panel, tempo = section()
        self.number(tempo,'Tempo (BPM)','bpm',20,600,120,inline=True)
        self.live_panel, live = section()
        live.addWidget(QLabel('MIDI input device'))
        device_row = QHBoxLayout(); live.addLayout(device_row)
        self.devices = QComboBox(); self.devices.setObjectName('midi_devices'); self.devices.addItem(NO_MIDI_DEVICE,None)
        self.devices.activated.connect(self.device_selected); device_row.addWidget(self.devices,1)
        self.ports_refresh = QPushButton('Refresh'); self.ports_refresh.setToolTip('Look for MIDI input devices again.')
        self.ports_refresh.clicked.connect(self.refresh_ports); device_row.addWidget(self.ports_refresh)
        self.combo(live,'MIDI channel','midi_channel',('All',*[str(i) for i in range(1,17)]),inline=True)
        self.combo(live,'Note decay','note_decay',NOTE_DECAYS,inline=True)
        self.parameters['note_decay'].setToolTip('Sustained holds each note while its key is down; Plucked fades out after about 0.4 s.')
        self.number(live,'MIDI gain (×)','midi_gain',0,16,1,inline=True)
        self.buttons(live,[('Panic',self.audio.midi.panic),('Record…',self.record),('Stop recording',self.audio.midi.stop_record)])
        self.midi_status = QLabel(); self.midi_status.setWordWrap(True); live.addWidget(self.midi_status)
        # SCENE ------------------------------------------------------------------
        scene_root = scene
        self.scene_context_label = ElidedLabel('SANDBOX · Empty scene')
        self.scene_context_label.setObjectName('scene_context')
        self.scene_context_label.setToolTip('Active workspace, clip, and selected object.')
        scene_root.addWidget(self.scene_context_label)
        self.scene_sections = QTabWidget(); scene_root.addWidget(self.scene_sections)
        # Pinned Placement bar: visible across both Objects and Transform tabs whenever an object is selected.
        self.placement_panel = QWidget(); self.placement_panel.setObjectName('placement_panel')
        place_l = QVBoxLayout(self.placement_panel); place_l.setContentsMargins(2,4,2,2); place_l.setSpacing(4)
        placement_tip = 'Position and Size apply after the transforms. Drag a center on the scope to move/select; drag the selected corner to resize.'
        row = QHBoxLayout(); place_l.addLayout(row)
        caption = InlineCaption('Position X, Y'); caption.setToolTip(placement_tip); row.addWidget(caption)
        for key in ('position_x','position_y'):
            widget = QDoubleSpinBox(); widget.setRange(-1e6,1e6); widget.setDecimals(3); widget.setObjectName(key); widget.setMinimumWidth(SPIN_MIN)
            widget.setKeyboardTracking(False); widget.setToolTip(f'Position {key[-1].upper()}: applied after the transforms.'); row.addWidget(widget,1)
            widget.valueChanged.connect(lambda value,key=key:self.edit(key,value)); self.parameters[key] = widget
        row = QHBoxLayout(); place_l.addLayout(row)
        caption = InlineCaption('Size'); caption.setToolTip(placement_tip); row.addWidget(caption)
        widget = QDoubleSpinBox(); widget.setRange(.05,1e6); widget.setDecimals(3); widget.setObjectName('shape_size'); widget.setMinimumWidth(SPIN_MIN)
        widget.setKeyboardTracking(False); widget.setValue(1); row.addWidget(widget,1)
        widget.valueChanged.connect(lambda value:self.edit('shape_size',value)); self.parameters['shape_size'] = widget
        for key in ('position_x','position_y','shape_size'): self.automation_menu(self.parameters[key],lambda key=key:('shape',self.scene.selected,key))
        reset = QPushButton('Reset'); reset.setObjectName('reset_placement'); reset.setToolTip('Reset placement: position 0, 0 and size 1'); reset.clicked.connect(self.reset_placement); row.addWidget(reset)
        scene_root.addWidget(self.placement_panel); self.placement_panel.hide()
        # Single tone only: the whole scene is traced once per cycle, so this is the scene's pitch.
        # It sits at the bottom of the panel, below both tabs, so the object list stays at the top.
        scene_root.setAlignment(Qt.AlignmentFlag(0)); scene_root.addStretch(1)
        self.tone_panel = QWidget(); tone = QVBoxLayout(self.tone_panel); tone.setContentsMargins(0,0,0,0); tone.setSpacing(5)
        self.inline_slider(tone,'Frequency (Hz)','tone_frequency',110,*TONE_RANGE,curve='log')
        self.parameters['tone_frequency'].setToolTip('Single tone: the scene is traced once per cycle at this rate, and parametric equations see s run 0–1 at it.')
        scene_root.addWidget(self.tone_panel)
        objects_panel,transform_panel = QWidget(),QWidget()
        scene = QVBoxLayout(objects_panel); transform = QVBoxLayout(transform_panel)
        # Explicit page margins: the macOS style's 20 px sides overflowed a 340 px side panel.
        for layout in (scene,transform): layout.setAlignment(Qt.AlignmentFlag.AlignTop); layout.setSpacing(5); layout.setContentsMargins(10,8,10,8)
        self.scene_sections.addTab(objects_panel,'Objects'); self.scene_sections.addTab(transform_panel,'Transform')
        self.shape_list = QListWidget(); self.shape_list.setProperty('customRows', True); scene.addWidget(self.shape_list)
        self.shape_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.shape_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.shape_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.shape_list.setToolTip('Scene objects in trace order. Drag to reorder.')
        self.shape_list.model().rowsMoved.connect(lambda parent, start, end, dest, row: self._objects_moved(start, row))
        for action, callback in (('object_mute', lambda: self.toggle_objects('mute')),
                                 ('object_solo', lambda: self.toggle_objects('solo')),
                                 ('object_unmute_all', self.unmute_all)):
            binding = QAction(self.shape_list); binding.setShortcuts(key_sequences(action))
            binding.setProperty('shortcut_id', action)
            binding.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            binding.triggered.connect(callback); self.shape_list.addAction(binding)
        self.shape_list.currentRowChanged.connect(self.select_shape)
        self.shape_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.shape_list.customContextMenuRequested.connect(
            lambda point: (item := self.shape_list.itemAt(point)) is not None and
            self.object_menu(self.shape_list.row(item), self.shape_list.viewport().mapToGlobal(point)))
        self.shape_list.itemDoubleClicked.connect(lambda item:self.open_builder() if self.scene.shape is not None and self.scene.shape.solid==CUSTOM_OBJECT else None)
        shape_actions=FlowRow();scene.addLayout(shape_actions)  # Wraps in a narrow panel (UI2).
        self.add_button=QPushButton('Add object');self.add_button.setObjectName('add_object');self.add_button.setMenu(self.add_menu())
        self.add_button.setProperty('menuButton', True);shape_actions.addWidget(self.add_button)
        self.quick_add_button=QPushButton('Quick…');self.quick_add_button.setObjectName('quick_add_btn')
        self.quick_add_button.setToolTip('Search & add any object, equation preset, transform, or recipe (Cmd/Ctrl+K)')
        self.quick_add_button.clicked.connect(lambda:self.open_quick_add(FILTER_ALL));shape_actions.addWidget(self.quick_add_button)
        self.duplicate_button=QPushButton('Duplicate');self.duplicate_button.clicked.connect(lambda:self.shape_action('duplicate'));shape_actions.addWidget(self.duplicate_button)
        self.remove_button=QPushButton('Remove');self.remove_button.clicked.connect(lambda:self.shape_action('remove'));shape_actions.addWidget(self.remove_button)
        self.edit_shape_button=QPushButton('Edit shape…');self.edit_shape_button.setObjectName('edit_shape');self.edit_shape_button.clicked.connect(self.open_builder);scene.addWidget(self.edit_shape_button)
        self.empty_note=QLabel('The scene is empty. Add an object to draw it; the source is still heard through MIX › Original blend.');scene.addWidget(self.empty_note)
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
        self.matrix_panel = QWidget(); self.matrix_panel.setObjectName('matrix_panel')
        mat_layout = QVBoxLayout(self.matrix_panel); mat_layout.setContentsMargins(0,0,0,0); mat_layout.setSpacing(4)
        self.matrix_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.matrix_lang_a = QComboBox(); self.matrix_lang_a.setObjectName('matrix_lang_a'); self.matrix_lang_a.addItems(MATRIX_LANGUAGES)
        self.matrix_lang_a.activated.connect(lambda _: self.matrix_changed(language_a=self.matrix_lang_a.currentText()))
        inline(mat_layout, 'Language 1', self.matrix_lang_a, 'Primary glyph set for Matrix digital rain.')
        self.matrix_sample_a = QLineEdit(); self.matrix_sample_a.setObjectName('matrix_sample_a'); self.matrix_sample_a.setMaxLength(MAX_SAMPLE_LENGTH)
        self.matrix_sample_a.setPlaceholderText('Optional sample string…')
        self.matrix_sample_a.editingFinished.connect(lambda: self.matrix_changed(sample_a=self.matrix_sample_a.text()))
        inline(mat_layout, 'Sample 1', self.matrix_sample_a, 'Optional custom characters sampled for Language 1.')
        self.matrix_lang_b = QComboBox(); self.matrix_lang_b.setObjectName('matrix_lang_b'); self.matrix_lang_b.addItems(MATRIX_LANGUAGES)
        self.matrix_lang_b.activated.connect(lambda _: self.matrix_changed(language_b=self.matrix_lang_b.currentText()))
        inline(mat_layout, 'Language 2', self.matrix_lang_b, 'Secondary glyph set mixed into the rain.')
        self.matrix_sample_b = QLineEdit(); self.matrix_sample_b.setObjectName('matrix_sample_b'); self.matrix_sample_b.setMaxLength(MAX_SAMPLE_LENGTH)
        self.matrix_sample_b.setPlaceholderText('Optional sample string…')
        self.matrix_sample_b.editingFinished.connect(lambda: self.matrix_changed(sample_b=self.matrix_sample_b.text()))
        inline(mat_layout, 'Sample 2', self.matrix_sample_b, 'Optional custom characters sampled for Language 2.')
        grid_row = QHBoxLayout(); mat_layout.addLayout(grid_row)
        grid_row.addWidget(InlineCaption('Cols / Rows'))
        self.matrix_cols = QSpinBox(); self.matrix_cols.setObjectName('matrix_cols'); self.matrix_cols.setRange(1, MAX_MATRIX_COLUMNS); self.matrix_cols.setKeyboardTracking(False)
        self.matrix_cols.valueChanged.connect(lambda v: self.matrix_changed(columns=int(v))); grid_row.addWidget(self.matrix_cols, 1)
        self.matrix_rows = QSpinBox(); self.matrix_rows.setObjectName('matrix_rows'); self.matrix_rows.setRange(2, MAX_MATRIX_ROWS); self.matrix_rows.setKeyboardTracking(False)
        self.matrix_rows.valueChanged.connect(lambda v: self.matrix_changed(rows=int(v))); grid_row.addWidget(self.matrix_rows, 1)
        self.matrix_fields = {}
        for field in (TEXT_SPEED_FIELD, FALL_SPEED_FIELD):
            w = ParamField(SliderInput, field, PATH_EXPRESSION_HELP, 'f(t)', caption_width=124)
            w.setObjectName('matrix_' + field.name)
            w.changed.connect(lambda param, key=field.name: self.matrix_changed(**{key: param}))
            w.automate.connect(lambda on, key=field.name: self.automate(('matrix', self.scene.selected, key), on))
            mat_layout.addWidget(w); self.matrix_fields[field.name] = w
        scene.addWidget(self.matrix_panel); self.matrix_panel.hide()
        # Configurable/animated built-in shapes (Star points, Gear teeth, Radar sweep, Clock hands)
        self.shape_params_panel = QWidget(); self.shape_params_panel.setObjectName('shape_params_panel')
        sp_root = QVBoxLayout(self.shape_params_panel); sp_root.setContentsMargins(0,0,0,0); sp_root.setSpacing(4)
        self.shape_params_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        # Star controls
        self.sp_star_box = QWidget(); star_l = QVBoxLayout(self.sp_star_box); star_l.setContentsMargins(0,0,0,0); star_l.setSpacing(4)
        self.sp_star_points = QSpinBox(); self.sp_star_points.setObjectName('star_points'); self.sp_star_points.setRange(3, 32); self.sp_star_points.setKeyboardTracking(False)
        self.sp_star_points.valueChanged.connect(lambda v: self.shape_params_changed(points=int(v)))
        inline(star_l, 'Points', self.sp_star_points, 'Number of star points (3–32).')
        self.sp_star_inner = SliderInput(0.42, 0.15, 0.90); self.sp_star_inner.setObjectName('star_inner_radius'); self.sp_star_inner.setFixedHeight(28)
        self.sp_star_inner.edited.connect(lambda v: self.shape_params_changed(inner_radius=float(v)))
        inline(star_l, 'Inner radius', self.sp_star_inner, 'Inner valley radius relative to outer points (0.15–0.90).')
        sp_root.addWidget(self.sp_star_box)
        # Gear controls
        self.sp_gear_box = QWidget(); gear_l = QVBoxLayout(self.sp_gear_box); gear_l.setContentsMargins(0,0,0,0); gear_l.setSpacing(4)
        self.sp_gear_teeth = QSpinBox(); self.sp_gear_teeth.setObjectName('gear_teeth'); self.sp_gear_teeth.setRange(4, 48); self.sp_gear_teeth.setKeyboardTracking(False)
        self.sp_gear_teeth.valueChanged.connect(lambda v: self.shape_params_changed(teeth=int(v)))
        inline(gear_l, 'Teeth', self.sp_gear_teeth, 'Number of gear teeth (4–48).')
        self.sp_gear_depth = SliderInput(0.24, 0.08, 0.45); self.sp_gear_depth.setObjectName('gear_tooth_depth'); self.sp_gear_depth.setFixedHeight(28)
        self.sp_gear_depth.edited.connect(lambda v: self.shape_params_changed(tooth_depth=float(v)))
        inline(gear_l, 'Tooth depth', self.sp_gear_depth, 'Radial depth of gear teeth (0.08–0.45).')
        sp_root.addWidget(self.sp_gear_box)
        # Radar controls
        self.sp_radar_box = QWidget(); radar_l = QVBoxLayout(self.sp_radar_box); radar_l.setContentsMargins(0,0,0,0); radar_l.setSpacing(4)
        self.sp_radar_speed = SliderInput(0.35, -4.0, 4.0, curve=True); self.sp_radar_speed.setObjectName('radar_sweep_speed'); self.sp_radar_speed.setFixedHeight(28)
        self.sp_radar_speed.edited.connect(lambda v: self.shape_params_changed(sweep_speed=float(v)))
        inline(radar_l, 'Sweep (rev/s)', self.sp_radar_speed, 'Radar sweep arm speed in revolutions per second (positive = clockwise).')
        sp_root.addWidget(self.sp_radar_box)
        # Clock controls
        self.sp_clock_box = QWidget(); clock_l = QVBoxLayout(self.sp_clock_box); clock_l.setContentsMargins(0,0,0,0); clock_l.setSpacing(4)
        self.sp_clock_speed = SliderInput(60.0, -3600.0, 3600.0, curve=True); self.sp_clock_speed.setObjectName('clock_speed'); self.sp_clock_speed.setFixedHeight(28)
        self.sp_clock_speed.edited.connect(lambda v: self.shape_params_changed(clock_speed=float(v)))
        inline(clock_l, 'Speed (×)', self.sp_clock_speed, 'Clock hand speed multiplier (1× = real time, 60× = 1 min/sec, 600× = fast sweep).')
        clk_btns = FlowRow(); clock_l.addLayout(clk_btns)
        for lbl, spd in (('Real 1×', 1.0), ('Fast 60×', 60.0), ('Turbo 600×', 600.0), ('Pause', 0.0)):
            b = QPushButton(lbl); b.clicked.connect(lambda _=False, s=spd: self.shape_params_changed(clock_speed=s)); clk_btns.addWidget(b)
        clk_start = QHBoxLayout(); clock_l.addLayout(clk_start)
        clk_start.addWidget(InlineCaption('Start H / M'))
        self.sp_clock_hour = QSpinBox(); self.sp_clock_hour.setObjectName('clock_start_hour'); self.sp_clock_hour.setRange(0, 12); self.sp_clock_hour.setKeyboardTracking(False)
        self.sp_clock_hour.valueChanged.connect(lambda v: self.shape_params_changed(start_hour=float(v))); clk_start.addWidget(self.sp_clock_hour, 1)
        self.sp_clock_min = QSpinBox(); self.sp_clock_min.setObjectName('clock_start_minute'); self.sp_clock_min.setRange(0, 59); self.sp_clock_min.setKeyboardTracking(False)
        self.sp_clock_min.valueChanged.connect(lambda v: self.shape_params_changed(start_minute=float(v))); clk_start.addWidget(self.sp_clock_min, 1)
        sp_root.addWidget(self.sp_clock_box)
        scene.addWidget(self.shape_params_panel); self.shape_params_panel.hide()
        self.function_panel = FunctionPanel(SliderInput); self.function_panel.setObjectName('parametric_panel')
        self.function_panel.changed.connect(self.plot_changed); scene.addWidget(self.function_panel)
        self.function_panel.automate.connect(lambda name,on:self.automate(('variable',self.scene.selected,name),on))
        self.function_panel.add_3d_view.connect(self.add_3d_turntable_to_selected)
        self.recurrence_panel = RecurrencePanel(SliderInput); self.recurrence_panel.setObjectName('recurrence_panel')
        self.recurrence_panel.changed.connect(self.recurrence_changed); scene.addWidget(self.recurrence_panel)
        self.recurrence_panel.automate.connect(lambda name,on:self.automate(('variable',self.scene.selected,name),on))
        self.recurrence_panel.add_3d_view.connect(self.add_3d_turntable_to_selected)
        # Path window: where along its route the object starts, and how much of it is drawn.
        self.path_panel = QWidget(); self.path_panel.setObjectName('path_panel'); path = QVBoxLayout(self.path_panel)
        path.setContentsMargins(0,0,0,0); path.setSpacing(3)
        heading = QLabel('Path'); heading.setToolTip('Start and Trace choose which part of the object is drawn. Right-click either for Time varying….'); path.addWidget(heading)
        self.path_fields = {}
        for field in (START_FIELD,TRACE_FIELD):
            widget = ParamField(SliderInput,field,PATH_EXPRESSION_HELP,'f(t)'); widget.setObjectName(field.name); widget.setToolTip(PATH_HELP[field.name])
            widget.changed.connect(lambda param,key=field.name:self.path_changed(key,param)); path.addWidget(widget); self.path_fields[field.name] = widget
            widget.automate.connect(lambda on,key=field.name:self.automate(('path',self.scene.selected,key),on))
        self.path_error = QLabel(); self.path_error.setObjectName('path_error'); self.path_error.setWordWrap(True); path.addWidget(self.path_error); self.path_error.hide()
        scene.addWidget(self.path_panel)
        self.transform_hint = QLabel('Use Transform to rotate, spin, shear, move or add noise to the selected object.'); scene.addWidget(self.transform_hint)
        self.transform_target = QComboBox(); self.transform_target.setObjectName('transform_target')
        self.transform_target.activated.connect(self.select_shape)
        inline(transform,'Object',self.transform_target,'The object these transforms apply to.')
        scene = transform
        self.transform_panel = TransformPanel(SliderInput); self.transform_panel.setObjectName('transform_panel')
        self.transform_panel.bypass_changed.connect(lambda on: self.edit('stack_bypassed', on))
        self.transform_panel.solo_changed.connect(self.solo_transform)
        self.transform_panel.changed.connect(self.transforms_changed); scene.addWidget(self.transform_panel)
        self.transform_panel.animation_changed.connect(self.animation_changed)
        self.transform_panel.automate.connect(lambda step,name,on:self.automate(('transform',self.scene.selected,step,name),on))
        self.transform_panel.automate_switch.connect(lambda step,on:self.automate(('on',self.scene.selected,step),on))
        self.transform_panel.variables_changed.connect(self.transform_variables_changed)
        self.transform_panel.preset_inserted.connect(self.transform_preset_inserted)
        self.transform_panel.automate_variable.connect(lambda name,on:self.automate(('transform_variable',self.scene.selected,name),on))
        # MIX --------------------------------------------------------------------
        self.combo(mix,'Travel between shapes','travel_mode',TRAVEL_MODES)
        mix.addWidget(QLabel('Travel speed (×)'))
        speed = TravelSpeedBox(); speed.setObjectName('travel_speed'); mix.addWidget(speed)
        speed.setToolTip(f'Faster travel multiplier, 1–{MAX_TRAVEL_SPEED:g}×. Above {MAX_TRAVEL_SPEED:g}×, or typing "instant", jumps take no time.')
        speed.valueChanged.connect(lambda value:self.edit('travel_speed',TravelSpeedBox.speed(value))); self.parameters['travel_speed'] = speed
        self.number(mix,'Rotation updates (Hz)','drawing_hz',1,240,60)
        self.slider(mix,'Output level','drawing_gain',.55,0,.95)
        self.slider(mix,'Original blend','music_gain',0,0,1)
        for key in ('drawing_gain','music_gain'): self.automation_menu(self.parameters[key],lambda key=key:('mix',key))
        self.combo(mix,'Listening mode','mode',('Processed output','Fully shaped audio','Original audio'))
        self.buttons(mix,[('Export source…',lambda:self.export(True)),('Export output…',self.export)])
        mix.addWidget(QLabel('Left = X · Right = Y · output rate\nTravel strategies approximate blanking;\nthey do not control CRT intensity.'))
        # APPEARANCE and SIGNAL OUTPUT: the Preferences dialogs' editors, applied and saved as you edit.
        self.appearance_editor = AppearanceEditor(SliderInput,self.preferences,compact=True)
        self.appearance_editor.changed.connect(self.appearance_edited)
        self.appearance_editor.tabs.addTab(self.monitor_page,'Monitor')
        window_row = QHBoxLayout(); self.monitor_page.layout().addLayout(window_row)
        self.popout_button = QPushButton('Pop out'); self.popout_button.setObjectName('popout_monitor'); self.popout_button.setCheckable(True)
        self.popout_button.setToolTip('Show the monitor in its own window: move and resize it, or put it on another screen.')
        self.popout_button.clicked.connect(lambda on:self.pop_out() if on else self.pop_in()); window_row.addWidget(self.popout_button)
        self.trace_fullscreen_button = QPushButton('Full screen trace'); self.trace_fullscreen_button.setObjectName('trace_fullscreen'); self.trace_fullscreen_button.setCheckable(True)
        self.trace_fullscreen_button.setToolTip('The trace alone, full screen on one screen; the main window stays usable on another. Escape leaves.')
        self.trace_fullscreen_button.clicked.connect(self.set_trace_fullscreen); window_row.addWidget(self.trace_fullscreen_button)
        self.signal_editor = SignalOutputEditor(SliderInput,self.signal_preferences,self.output_device_names)
        self.signal_editor.changed.connect(self.signal_edited)
        for title,editor,defaults,revert in ((PANEL_APPEARANCE,self.appearance_editor,self.appearance_defaults,self.appearance_revert),
                                             (PANEL_SIGNAL,self.signal_editor,self.signal_defaults,self.signal_revert)):
            layout = page(title); layout.addWidget(editor)
            self.buttons(layout,[('Defaults',defaults),('Revert',revert)])
            note = QLabel('Changes apply and save at once. Revert restores the values from when this panel opened.'); layout.addWidget(note)
        # INSPECT CYCLE (audit I1): one cycle at a frozen pose and a pinned reference; display only.
        self.inspector = CycleInspectorPanel(self, object_label); page(PANEL_INSPECT).addWidget(self.inspector)
        self.preferences_save = QTimer(self); self.preferences_save.setSingleShot(True); self.preferences_save.setInterval(400)
        self.preferences_save.timeout.connect(self.save_preferences)
        self.signal_save = QTimer(self); self.signal_save.setSingleShot(True); self.signal_save.setInterval(400)
        self.signal_save.timeout.connect(self.save_signal_preferences)
        right_panel = QWidget(); right = QVBoxLayout(right_panel); right.setContentsMargins(0,0,0,0)
        self.splitter.insertWidget(0,right_panel)
        self.splitter.setStretchFactor(0,1); self.splitter.setStretchFactor(1,0)
        self.splitter.setSizes([780,panel_width(self.preferences['panel_width'])])
        self.splitter.splitterMoved.connect(lambda *args: self.remember_panel_width())
        self.splitter.handle_double_clicked.connect(lambda index: self.set_panel_width(PANEL_DEFAULT))
        top = QHBoxLayout(self.top_row); top.setContentsMargins(0,0,0,0); top.setSpacing(16)
        self.viewer_heading=QLabel('X–Y SIGNAL MONITOR');top.addWidget(self.viewer_heading,0,Qt.AlignmentFlag.AlignVCenter)
        status_text = QHBoxLayout(); status_text.setSpacing(8); top.addLayout(status_text,1)
        self.status = ElidedLabel(); self.status.setObjectName('status')
        self.version_label = QLabel(f'{APP_NAME} {VERSION}')
        self.version_label.setObjectName('version_label')
        self.version_label.setToolTip(BUILD_DATE)
        self.version_label.setStyleSheet(self.theme.version_stylesheet())
        self.version_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.version_label.mousePressEvent = lambda event: QApplication.clipboard().setText(VERSION)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().addPermanentWidget(self.version_label)
        self.backend_label = ElidedLabel(self.backend); self.backend_label.setObjectName('backend')
        self.solo_pill = QPushButton('Solo active · Clear'); self.solo_pill.setObjectName('solo_pill')
        self.solo_pill.setToolTip('One or more objects, transforms, audio clips or tracks are soloed. Click to clear all solos.')
        self.solo_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.solo_pill.clicked.connect(self.clear_all_solos); self.solo_pill.hide()
        # One line: the status takes most of the width, the backend the rest; both elide
        # with … and keep their full text in the tooltip.
        status_text.addWidget(self.status,3); status_text.addWidget(self.backend_label,1); status_text.addWidget(self.solo_pill,0)
        # Same preference as Appearance › Phosphor intensity; saved as soon as it settles.
        self.brightness_panel = QWidget(); self.brightness_panel.setObjectName('brightness_panel')
        brightness = QHBoxLayout(self.brightness_panel); brightness.setContentsMargins(0,0,0,0); brightness.setSpacing(8)
        brightness.addWidget(QLabel('Phosphor brightness (×)'))
        self.brightness_control = SliderInput(VISUAL_DEFAULTS['beam_intensity'],*VISUAL_RANGES['beam_intensity'],curve=True)
        self.brightness_control.setObjectName('brightness'); self.brightness_control.setFixedWidth(260); brightness.addWidget(self.brightness_control)
        self.brightness_control.edited.connect(self.set_brightness)
        top.addWidget(self.brightness_panel,0,Qt.AlignmentFlag.AlignVCenter)
        # Normal editing stays square; immersive mode extends the background to the screen.
        self.stage = SquareStage(); self.host = self.main_host = self.stage.host
        self.stage.placeholder.clicked.connect(self.pop_in)
        right.addWidget(self.stage,1)
        # COMPOSER: transport and timeline across the bottom of the window.
        self.composer_panel = ComposerPanel(); self.vsplit.addWidget(self.composer_panel)
        self.browser = BrowserPanel(self)
        self.effects_panel = TrackEffectsPanel(self, self.composer_panel.timeline)
        self.left_panel = LeftPanel(self.browser, self.effects_panel)
        self.left_panel.setMinimumWidth(BROWSER_RANGE[0]); self.left_panel.setMaximumWidth(BROWSER_RANGE[1])
        self.browser_split.insertWidget(0, self.left_panel)
        self.browser_split.setStretchFactor(0, 0); self.browser_split.setStretchFactor(1, 1)
        self.left_panel.hide()
        if self.preferences['left_page'] == 'effects':
            self.left_panel.stack.setCurrentWidget(self.effects_panel)
        self.left_panel.stack.currentChanged.connect(self.remember_left_page)
        self.vsplit.setStretchFactor(0,1); self.vsplit.setStretchFactor(1,0); self.vsplit.setSizes([520,260+LANE_HEIGHT+AUDIO_TRACK])  # The velocity lane and Audio track add their own room.
        self.connect_composer()
        self.brightness_save = QTimer(self); self.brightness_save.setSingleShot(True); self.brightness_save.setInterval(400)
        self.brightness_save.timeout.connect(self.save_brightness)
        for label in sidebar.findChildren(QLabel):
            if label.property('inlineCaption'): continue  # Keeps a one-line width beside its slider.
            if self.appearance_editor.isAncestorOf(label) or self.signal_editor.isAncestorOf(label): continue  # Laid out by the editors.
            label.setWordWrap(True); label.setMinimumWidth(0)
            policy = QSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
            policy.setHeightForWidth(True); label.setSizePolicy(policy)
        for combo in sidebar.findChildren(QComboBox):
            combo.setMinimumContentsLength(1)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumWidth(0)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.show_panel(PANEL_INPUT)
        if self.preferences['browser_shown']: self.toggle_browser(True)

    def make_app_bar(self):
        """Ribbon-style bar: panel buttons open their panel on the right; view and transport buttons act."""
        self.app_bar = bar = _VintageToolBar('Application bar'); bar.setObjectName('app_bar')
        bar.setMovable(False); bar.setFloatable(False); bar.setIconSize(QSize(self.theme.metrics.bar_icon_size, self.theme.metrics.bar_icon_size))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        bar.toggleViewAction().setVisible(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea,bar)
        self.bar_buttons = {}
        def button(name,icon,tip):
            widget = QToolButton(); widget.setObjectName('bar_'+icon); widget.setText(name); widget.setToolTip(tip)
            widget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon); widget.setMinimumWidth(78)
            widget.setProperty('barIcon',icon); bar.addWidget(widget); self.bar_buttons[name] = widget
            return widget
        widget = button(BROWSER,'browser','Show or hide the browser and track effects left of the scope')
        widget.setText('Library')
        widget.setCheckable(True); widget.clicked.connect(lambda checked=False: self.toggle_browser(checked))
        bar.addSeparator()
        for name,icon,tip in ((PANEL_INPUT,'input','Monitor input and the generated source'),
                              (PANEL_SCENE,'scene','Scene objects and their transforms'),
                              (PANEL_MIX,'mix','Travel, levels, listening mode and export'),
                              (PANEL_APPEARANCE,'appearance','Preview colours, beam and phosphor (preview only)'),
                              (PANEL_SIGNAL,'signal','Corner dwell, shape redraw, route minimizing, switch sweep and output rate (changes audio)')):
            widget = button(name,icon,tip); widget.setCheckable(True)
            widget.clicked.connect(lambda checked=False,name=name:self.toggle_panel(name))
        bar.addSeparator()
        for name,icon,action in (('Full screen','fullscreen',self.fullscreen_action),('Trace only','trace',self.immersive_action),('3D Lab','desk3d',self.desk3d_action)):
            widget = button(name,icon,action.text()+f' ({action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)})')
            widget.setCheckable(True); widget.toggled.connect(action.setChecked)
            action.toggled.connect(widget.setChecked)
        bar.addSeparator()
        self.play_button = button('Play','play','Start or stop audio (or capture/video for those inputs)')
        self.play_button.clicked.connect(self.play)
        # Workspace switch at the right end: Composer (timeline) or Sandbox (one scene).
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred); bar.addWidget(spacer)
        switch = QWidget(); switch.setObjectName('workspace_switch'); row = QHBoxLayout(switch); row.setContentsMargins(0,0,6,0); row.setSpacing(0)
        self.workspace_buttons = {}
        for name,tip in ((COMPOSER,'Composer: timeline, note roll and scene clips'),(SANDBOX,'Sandbox: one scene with its own source')):
            widget = QToolButton(); widget.setObjectName('workspace_'+name.lower()); widget.setText(name); widget.setToolTip(tip)
            widget.setCheckable(True); widget.setMinimumWidth(92); widget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            widget.clicked.connect(lambda checked=False,name=name:self.set_workspace(name)); row.addWidget(widget); self.workspace_buttons[name] = widget
        bar.addWidget(switch)

    def add_menu(self):
        menu = QMenu(self)
        self.type_actions(menu, self.add_object, self.import_svg)
        return menu

    def type_actions(self, menu, choose, svg, current=None):
        """Add object's categories and items in menu (Add object and Objects list › Replace).
        choose(kind) runs for a type, svg() for SVG drawing…; current, the type being replaced,
        is checked and disabled (SVG drawing… is disabled too: it makes a Custom shape)."""
        def item(target, text, kind, run):
            action = target.addAction(text)
            action.triggered.connect(lambda checked=False: run())
            if current is not None and kind == current:
                action.setCheckable(True); action.setChecked(text != 'SVG drawing…'); action.setEnabled(False)
            return action
        for title, subgroups in OBJECT_GROUPS:
            group = menu.addMenu(title)
            for idx, (_sub_title, names) in enumerate(subgroups):
                if idx > 0: group.addSeparator()
                for name in names: item(group, name, name, lambda name=name: choose(name))
        menu.addSeparator()
        item(menu, 'Text', TEXT_OBJECT, lambda: choose(TEXT_OBJECT))
        item(menu, 'Matrix', MATRIX_OBJECT, lambda: choose(MATRIX_OBJECT))
        item(menu, 'Parametric equation', PARAMETRIC_OBJECT, lambda: choose(PARAMETRIC_OBJECT))
        item(menu, RECURRENCE_OBJECT, RECURRENCE_OBJECT, lambda: choose(RECURRENCE_OBJECT))
        item(menu, 'Custom shape…', CUSTOM_OBJECT, lambda: choose(CUSTOM_OBJECT))
        menu.addSeparator()
        item(menu, '3D model…', MODEL_OBJECT, lambda: choose(MODEL_OBJECT))
        item(menu, 'SVG drawing…', CUSTOM_OBJECT, svg)

    def toggle_panel(self, name):
        """Open name's panel; choosing the open panel again hides the side panel."""
        if self.sidebar.isVisible() and self.current_panel == name: self.hide_panel()
        else: self.show_panel(name)

    def show_panel(self, name):
        button = self.bar_buttons.get(name)
        if button is not None and not button.isEnabled(): name = next(n for n in PANELS if self.bar_buttons[n].isEnabled())
        if name == PANEL_APPEARANCE and self.current_panel != name: self.appearance_snapshot = dict(self.preferences)
        if name == PANEL_SIGNAL and self.current_panel != name: self.signal_snapshot = dict(self.signal_preferences)
        if name == PANEL_INSPECT and self.current_panel != name:  # Close inspector returns here (UI1).
            self.panel_before_inspect = self.current_panel if self.sidebar.isVisible() else None
        self.current_panel = name
        self.panel_stack.setCurrentWidget(self.panels[name]); self.panel_title.setText(name.upper())
        for title in PANELS:
            blocker = QSignalBlocker(self.bar_buttons[title]); self.bar_buttons[title].setChecked(title == name)
        if not self.sidebar.isVisible() and not self.immersive: self.sidebar.show()
        if name not in self.fresh_panels:
            # Hidden tab pages postpone updateGeometry, so their layouts can keep minimums from
            # before the sidebar's width fix-ups; a stale hidden tab made a narrow panel scroll
            # sideways (UI2). Recompute the panel's layouts once, the first time it is shown.
            self.fresh_panels.add(name); scroll = self.panels[name]
            for layout in scroll.widget().findChildren(QLayout): layout.invalidate()  # Nested rows too.

    def hide_panel(self):
        self.sidebar.hide()
        for title in PANELS:
            blocker = QSignalBlocker(self.bar_buttons[title]); self.bar_buttons[title].setChecked(False)

    def eventFilter(self,watched,event):
        if watched is getattr(getattr(self,'host',None),'view',None) and event.type()==QEvent.Type.KeyPress:
            if exit_key(event) and (self.immersive or self.isFullScreen()):
                self.exit_view_modes();return True
        return super().eventFilter(watched,event)

    def set_fullscreen(self,enabled):
        if self.building:return
        if not enabled and self.immersive:self.set_immersive(False)
        self.showFullScreen() if enabled else self.showNormal()
        self.statusBar().setVisible(not enabled and not self.immersive)

    def set_immersive(self,enabled):
        if self.building:return
        if enabled==self.immersive:return
        if enabled and self.popped: self.pop_in()
        if enabled:
            self._was_fullscreen=self.isFullScreen();self._split_sizes=self.splitter.sizes();self._sidebar_shown=self.sidebar.isVisible()
        self.immersive=enabled;self.drag=None
        self.stage.set_fill_screen(enabled); self.sync_monitor_controls()
        for widget in (self.top_row,self.viewer_heading,self.status,self.backend_label,self.brightness_panel,self.menuBar(),self.app_bar):widget.setVisible(not enabled)
        self.sync_solo_pill()
        self.statusBar().setVisible(not enabled and not self._was_fullscreen)
        self.composer_panel.setVisible(not enabled and self.workspace==COMPOSER)
        self.sidebar.setVisible(not enabled and getattr(self,'_sidebar_shown',True))
        if enabled and self.left_panel.isVisible(): self.browser.stop_preview()
        self.left_panel.setVisible(not enabled and self.preferences['browser_shown'])
        self.body.setContentsMargins(0,0,0,0) if enabled else self.body.setContentsMargins(18,14,18,14)
        self.body.setSpacing(0 if enabled else 18)
        self.host.view.setCursor(Qt.CursorShape.BlankCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.centralWidget().setCursor(Qt.CursorShape.BlankCursor if enabled else Qt.CursorShape.ArrowCursor)
        blocker=QSignalBlocker(self.immersive_action);self.immersive_action.setChecked(enabled);del blocker;self.sync_view_buttons()
        if enabled:self.showFullScreen()
        else:
            self.splitter.setSizes(self._split_sizes)
            self.showFullScreen() if self._was_fullscreen else self.showNormal()

    def set_desk3d(self, enabled):
        """Enter or leave the late-80s 3D Photorealistic Oscilloscope Lab Bench mode (QtQuick3D)."""
        enabled = bool(enabled)
        if self.building: return
        if enabled == self.desk3d: return
        if enabled:
            if self.immersive: self.set_immersive(False)
            if self.popped: self.pop_in()
            if self.desk3d_host is None:
                try:
                    from phosphor.editor.desk3d import Desk3DHost
                    self.desk3d_host = Desk3DHost(self, self.centralWidget())
                    self.desk3d_host.controller.exitRequested.connect(lambda: self.set_desk3d(False))
                    self.desk3d_host.view.frameSwapped.connect(self.swapped, Qt.ConnectionType.QueuedConnection)
                    self.body.addWidget(self.desk3d_host)
                except Exception as error:
                    self.error(error)
                    with QSignalBlocker(self.desk3d_action): self.desk3d_action.setChecked(False)
                    self.sync_view_buttons()
                    return
            self.desk3d = True
            self.top_row.hide(); self.vsplit.hide(); self.app_bar.hide()
            self.body.setContentsMargins(0, 0, 0, 0); self.body.setSpacing(0)
            self.desk3d_host.show()
            self.switch_host(self.desk3d_host.crt_host)
            self.desk3d_host.controller.refresh()
            self.desk3d_host.setFocus()
        else:
            self.desk3d = False
            if self.desk3d_host is not None: self.desk3d_host.hide()
            self.body.setContentsMargins(18, 10, 18, 14); self.body.setSpacing(8)
            self.top_row.show(); self.vsplit.show(); self.app_bar.show()
            self.switch_host(self.main_host)
        with QSignalBlocker(self.desk3d_action): self.desk3d_action.setChecked(enabled)
        self.sync_view_buttons()

    def set_panel_width(self, width):
        """Show the side panel at width (clamped to PANEL_RANGE and the window) and remember it."""
        width = min(PANEL_RANGE[1], max(PANEL_RANGE[0], int(width)))
        total = sum(self.splitter.sizes()); self.splitter.setSizes([max(1, total - width), width])
        self.remember_panel_width(self.splitter.sizes()[1])

    # Browser panel (AW4) -----------------------------------------------------------
    browser_places_path = None  # Favourite folders (browser.json): only main() enables user files.

    def toggle_browser(self, shown=None):
        """Show or hide the browser left of the scope; the first show lists the remembered folder."""
        shown = not self.left_panel.isVisible() if shown is None else bool(shown)
        if shown and not self.browser_started:
            self.browser_started = True
            folder = self.preferences['browser_folder']  # Not checked here: an offline share must not stall the launch.
            self.browser.start(Path(folder) if folder else None)
        if shown and not self.immersive:
            self.left_panel.show(); width = self.preferences['browser_width']; total = sum(self.browser_split.sizes())
            self.browser_split.setSizes([width, max(1, total - width)])
        elif not shown:
            self.browser.stop_preview(); self.left_panel.hide()
        widget = self.bar_buttons.get(BROWSER)
        if widget is not None and widget.isChecked() != shown:
            blocker = QSignalBlocker(widget); widget.setChecked(shown); del blocker
        if self.preferences['browser_shown'] != shown:
            self.preferences['browser_shown'] = shown
            if self.remember_recent: self.preferences_save.start()

    def remember_left_page(self, index):
        self.preferences['left_page'] = 'effects' if self.left_panel.stack.widget(index) is self.effects_panel else 'browser'
        if self.remember_recent: self.preferences_save.start()

    def remember_browser_width(self):
        if not self.left_panel.isVisible() or self.immersive: return
        width = browser_width(min(BROWSER_RANGE[1], max(BROWSER_RANGE[0], self.browser_split.sizes()[0])))
        if width == self.preferences['browser_width']: return
        self.preferences['browser_width'] = width
        if self.remember_recent: self.preferences_save.start()

    def browser_folder_changed(self, folder):
        if self.preferences['browser_folder'] == str(folder): return
        self.preferences['browser_folder'] = str(folder)
        if self.remember_recent: self.preferences_save.start()

    def browser_can_add(self): return self.workspace == COMPOSER and not self.building

    def preview_blocked(self):
        """Why a file preview can't start now, or ''."""
        if self.building: return 'Leave the shape builder to preview.'
        if self.exporting: return 'Previews wait for the export to finish.'
        if self.midi_recorder is not None or self.audio.midi.capture: return 'Previews wait for recording to stop.'
        if self.system_monitor or self.video_monitor: return 'Previews play with the generated input (INPUT › Monitor input).'
        if self.audio.running and not (self.audio.preview or self.audio.cycle): return 'Stop playback to preview.'
        return ''

    def start_preview(self, samples, rate, level):
        """Play a decoded file once through the output (BrowserPanel's worker decoded it at rate)."""
        reason = self.preview_blocked()
        if reason: self.browser.message.setText(reason); return False
        try:
            if self.audio.running: self.audio.stop()  # A cycle, a note audition or an older preview.
            self.cycle_key = None; self.note_audition = None
            self.audio.preview_level = level
            settle_garbage()
            machine = replace(self.output_settings(), source=TONE_SOURCE, sample_rate=rate, motion_time_offset=0.)
            self.audio.start_preview(samples, rate, machine, self.scene.mode)
            self.last_status = 0
            return True
        except Exception as error:
            self.audio.error = str(error); self.browser.message.setText(str(error)); return False

    def stop_preview(self):
        if self.audio.preview_running: self.audio.stop()
        self.last_status = 0

    def browser_open_scene(self, path):
        if self.workspace != SANDBOX: self.set_workspace(SANDBOX)
        if self.workspace == SANDBOX: self.open_scene_path(str(path))

    def browser_import_model(self, path):
        chosen = self.choose_model(str(path))
        if chosen is not None: self.add_model(*chosen)

    def browser_objects(self):
        """Grouped 2D shapes, 3D solids, curves & math/generative objects for Library › Objects."""
        rows = []
        for cat_title, subgroups in OBJECT_GROUPS:
            for sub_title, names in subgroups:
                rows.append((f'{cat_title} · {sub_title}', '', None))
                for name in names:
                    tag = 'Animated 2D' if name in ANIMATED_SOLIDS else ('Configurable 2D' if name in PARAMETRIC_SOLIDS else cat_title)
                    rows.append((name, f'{tag} — {sub_title}', name))
        rows.append(('Math & Generative', '', None))
        for name, tip in (
            (TEXT_OBJECT, 'Vector text string'),
            (MATRIX_OBJECT, 'Falling digital rain glyphs'),
            (PARAMETRIC_OBJECT, 'Custom x(t, s), y(t, s), z(t, s) equation'),
            (RECURRENCE_OBJECT, 'Iterative strange attractor / recurrence map'),
        ):
            rows.append((name, tip, name))
        return rows

    def browser_equations(self):
        """Grouped Parametric & Recurrence presets (built-in + saved) as browser rows."""
        fpanel, rpanel = self.function_panel, self.recurrence_panel
        fpanel.reload_presets(); rpanel.reload_presets()
        ftip = lambda plot, cat: f'Parametric · {cat}\nx = {plot.x}\ny = {plot.y}' + (f'\nz = {plot.z}' if plot.z != '0' else '')
        rtip = lambda plot, cat: f'Recurrence · {cat} ({plot.draw}, {plot.steps} steps)\nx = {plot.x}\ny = {plot.y}' + (f'\nz = {plot.z}' if plot.z != '0' else '')
        rows = []
        for group_title, names in PARAMETRIC_GROUPS:
            rows.append((f'Parametric · {group_title}', '', None))
            for name in names:
                if name in fpanel.store.builtins:
                    rows.append((name, ftip(fpanel.store.builtins[name], group_title), ('parametric', 'builtin', name)))
        if fpanel.saved:
            rows.append(('Parametric · ★ Saved', '', None))
            for name, plot in fpanel.saved.items():
                rows.append((name, ftip(plot, 'Saved'), ('parametric', 'saved', name)))
        for group_title, names in RECURRENCE_GROUPS:
            rows.append((f'Recurrence · {group_title}', '', None))
            for name in names:
                if name in rpanel.store.builtins:
                    rows.append((name, rtip(rpanel.store.builtins[name], group_title), ('recurrence', 'builtin', name)))
        if rpanel.saved:
            rows.append(('Recurrence · ★ Saved', '', None))
            for name, plot in rpanel.saved.items():
                rows.append((name, rtip(plot, 'Saved'), ('recurrence', 'saved', name)))
        return rows

    def browser_add_equation(self, *args):
        """Add a Parametric or Recurrence object and load the preset into it, as one Undo step."""
        if len(args) == 2:
            eq_type, kind, name = 'parametric', args[0], args[1]
        else:
            eq_type, kind, name = args[0], args[1], args[2]
        is_rec = (eq_type == 'recurrence')
        panel = self.recurrence_panel if is_rec else self.function_panel
        solid = RECURRENCE_OBJECT if is_rec else PARAMETRIC_OBJECT
        count = len(self.scene.settings.shapes)
        with self.undo_step(f'Add {name}'):
            self.add_object(solid)
            # Only a new object takes the preset: never overwrite a selected equation (review 14).
            if len(self.scene.settings.shapes) != count + 1 or self.scene.shape is None or self.scene.shape.solid != solid:
                return False
            panel.load_named_preset(kind, name)
        return True

    def browser_transforms(self):
        panel = self.transform_panel; panel.reload_saved()
        rows = []
        for kind, heading in ((TRANSFORM_STEP, 'Saved Steps'), (TRANSFORM_STACK, 'Saved Stacks')):
            names = [name for name, preset in panel.saved.items() if preset.kind == kind]
            if names: rows.append((heading, '', None))
            rows += [(name, ' → '.join(t.kind for t in panel.saved[name].transforms), ('saved', name)) for name in names]
        if BUILTIN_RECIPES:
            rows.append(('Built-in Recipes', '', None))
            rows += [(name, ' → '.join(t.kind for t in preset.transforms), ('recipe', name)) for name, preset in BUILTIN_RECIPES.items()]
        return rows

    def browser_transform_modes(self, key):
        if isinstance(key, tuple):
            source, name = key
        else:
            source, name = ('saved', key)
        if source == 'recipe':
            if name not in BUILTIN_RECIPES: return []
            return [('Append recipe to stack', TRANSFORM_APPEND), ('Replace stack with recipe', TRANSFORM_REPLACE)]
        preset = self.transform_panel.saved.get(name)
        if preset is None: return []
        if preset.kind == TRANSFORM_STEP: return [('Insert below the selected step', TRANSFORM_APPEND)]
        return [('Append to stack', TRANSFORM_APPEND), ('Replace stack', TRANSFORM_REPLACE)]

    def browser_insert_transform(self, key, mode=None):
        if self.scene.shape is None:
            page = self.browser.transforms; hint = page.hint.text()
            page.hint.setText('Select an object first: transforms go into its stack.')
            QTimer.singleShot(4000, lambda: page.hint.setText(hint))  # The usual hint comes back (review 15).
            return False
        if isinstance(key, tuple):
            source, name = key
        else:
            source, name = ('saved', key)
        eff_mode = TRANSFORM_APPEND if mode is None else mode
        if source == 'recipe':
            return self.transform_panel.insert_recipe(name, eff_mode)
        return self.transform_panel.insert_saved(name, eff_mode)

    def remember_panel_width(self, width=None):
        """Keep the side panel's width as a machine preference at once (a drag reports every move); only the
        disk write waits (preferences_save, flushed on close), so hiding or quitting right after keeps it."""
        if width is None:
            if not self.sidebar.isVisible() or self.immersive: return
            width = self.splitter.sizes()[1]
        width = panel_width(width)
        if width == self.preferences['panel_width']: return
        self.preferences['panel_width'] = width
        if self.remember_recent: self.preferences_save.start()

    def sync_view_buttons(self):
        """The bar's Full screen, Trace only and 3D Lab buttons show their actions' states."""
        for name,action in (('Full screen',self.fullscreen_action),('Trace only',self.immersive_action),('3D Lab',self.desk3d_action)):
            button=self.bar_buttons.get(name)
            if button is not None and button.isChecked()!=action.isChecked():
                blocker=QSignalBlocker(button);button.setChecked(action.isChecked())

    def exit_view_modes(self):
        if self.building:self.builder.cancel_gesture();return
        if self.desk3d:self.set_desk3d(False);return
        if self.immersive:self.set_immersive(False)
        elif self.isFullScreen():self.showNormal()
        elif self.inspector_has_focus():self.close_inspector()  # UI1: Esc in the INSPECT CYCLE panel.

    def inspector_has_focus(self):
        focus = QApplication.focusWidget()
        page = self.panels[PANEL_INSPECT]
        return (self.current_panel == PANEL_INSPECT and self.sidebar.isVisible() and focus is not None
                and (focus is page or page.isAncestorOf(focus)))

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.Type.WindowStateChange and hasattr(self,'fullscreen_action'):
            self.statusBar().setVisible(not self.isFullScreen() and not self.immersive)
            if getattr(self,'immersive',False) and not self.isFullScreen():
                self._was_fullscreen=False;self.set_immersive(False)
            blocker=QSignalBlocker(self.fullscreen_action);self.fullscreen_action.setChecked(self.isFullScreen());del blocker;self.sync_view_buttons()

    # WORKSPACES ----------------------------------------------------------------
    def set_workspace(self, name, initial=False):
        """Composer (timeline) or Sandbox (one scene). Each keeps its own document."""
        if name not in WORKSPACES: return
        if not initial and name == self.workspace: self.sync_workspace_buttons(); return
        if not initial and self.building and not self.leave_builder(): self.sync_workspace_buttons(); return
        if not initial:
            if self.midi_recorder is not None: self.stop_song()
            try: self.audio.stop(); self.capture.stop()
            except Exception as error: self.error(error); self.sync_workspace_buttons(); return
        self.audio.composer = None
        self.workspace = name; composer = name == COMPOSER
        if composer and (self.system_monitor or self.video_monitor):
            # Monitor inputs are Sandbox tools; Composer always plays its timeline.
            self.monitor.setCurrentIndex(MONITOR_GENERATED); self.monitor_changed(MONITOR_GENERATED)
        if composer: self.load_clip_scene(min(self.clip_index,len(self.project.clips)-1))
        self.clear_solos()
        self.scene = self.clip_scene if composer else self.sandbox_scene
        self.composer_panel.setVisible(composer and not self.immersive)
        self.bar_buttons[PANEL_INPUT].setEnabled(not composer)
        self.bar_buttons[PANEL_INPUT].setToolTip('Sandbox only: in Composer the Notes track drives the pitch' if composer else 'Monitor input and the generated source')
        if composer and self.current_panel == PANEL_INPUT: self.show_panel(PANEL_SCENE)
        noun = 'project' if composer else 'scene'
        self.open_action.setText(f'Open {noun}…'); self.save_action.setText(f'Save {noun}'); self.save_as_action.setText(f'Save {noun} as…')
        self.new_project_action.setVisible(composer); self.send_action.setVisible(not composer)
        self.collect_media_action.setVisible(composer); self.missing_audio_action.setVisible(composer)
        self.audio_solos.clear(); self.track_solos.clear()
        self.import_midi_action.setVisible(composer); self.export_midi_action.setVisible(composer)
        self.chords_action.setVisible(composer); self.chords_action.setEnabled(composer)
        self.velocity_action.setVisible(composer); self.velocity_action.setEnabled(composer)
        self.sync_chords_action()
        self.export_source_action.setText('Export notes…' if composer else 'Export source…')
        self.export_action.setText('Export song…' if composer else 'Export processed…')
        self.sync_workspace_buttons()
        if not initial and self.remember_workspace:
            try: write_workspace(name)
            except OSError: pass
        self.restart_motion(); self.refresh_composer(); self.sync()
        self.update_history_actions()

    def sync_workspace_buttons(self):
        for name,button in self.workspace_buttons.items():
            blocker = QSignalBlocker(button); button.setChecked(name == self.workspace)

    def load_clip_scene(self, index):
        """Edit clip index's scene through the Scene, Transform and Mix panels."""
        if index != self.clip_index: self.clear_solos()
        clip = self.project.clips[index]
        scene = Scene(); scene.settings = clip.settings; scene.mode = clip.mode
        scene.selected = 0 if clip.settings.shapes else -1; scene.mark_saved()
        self.clip_index, self.clip_scene = index, scene
        if getattr(self,'workspace',None) == COMPOSER: self.scene = scene

    def store_clip_scene(self):
        """Panel edits change self.scene; copy them into the project's clip."""
        if self.workspace != COMPOSER or self.scene is not self.clip_scene: return
        clip = self.project.clips[self.clip_index]
        if clip.settings is self.scene.settings and clip.mode == self.scene.mode: return
        self.set_project(with_clip(self.project,self.clip_index,replace(clip,settings=self.scene.settings,mode=self.scene.mode)))

    def prepare_audio(self, project):
        """Queue decodes of the project's audio clips on the decode worker, at the display's rate
        and the output device's, so opening or editing never decodes on the GUI thread (audit P9)."""
        rates = (self.audio.sample_rate, self.audio.expected_rate()) if project.audio_clips else ()
        audio_clips.prepare(project, rates)

    def set_project(self, project, dirty=True, clip_index=None):
        """Show project; clip_index is where the edited clip is now when a drag reordered the clips."""
        if clip_index is not None and 0 <= clip_index < len(project.clips): self.clip_index = clip_index
        self.prepare_audio(project)
        if self.audio_solos and [(c.path, c.offset, c.track) for c in project.audio_clips] != [(c.path, c.offset, c.track) for c in self.project.audio_clips]:
            self.audio_solos.clear()
        if (self.track_solos or self.audio_solos) and track_layout(project) != track_layout(self.project):
            self.track_solos.clear(); self.audio_solos.clear()  # Tracks added, deleted, renamed, reordered or refilled.
        if hasattr(self, 'effects_panel'):
            self.effects_panel.project_changing(self.project, project, self.restoring)
        self.project = project
        if self.clip_index >= len(project.clips): self.clear_solos()
        self.sync_chords_action()
        if dirty and not self.restoring:
            self.project_dirty = True
            self.history_timer.start(0)
        if self.audio.composer is not None: self.audio.composer = (self.output_project(),self.play_start)
        index = min(self.clip_index,len(project.clips)-1); clip = project.clips[index]
        if index != self.clip_index or clip.settings is not self.clip_scene.settings or clip.mode != self.clip_scene.mode:
            # The same clip keeps its selected object (lane edits replace its settings while dragging).
            keep = self.clip_scene.selected if index == self.clip_index else None
            self.load_clip_scene(index)
            if keep is not None and clip.settings.shapes: self.clip_scene.selected = min(max(keep,0),len(clip.settings.shapes)-1)
            if self.workspace == COMPOSER: self.sync()
        self.refresh_composer(); self.update_title()

    def history_state(self):
        return State(self.project, self.clip_index, self.clip_scene.selected)

    def update_history_actions(self):
        if not hasattr(self, 'undo_action'): return
        self.add_audio_action.setEnabled(self.workspace == COMPOSER)
        history = self.history if self.workspace == COMPOSER else self.scene_history
        for action, label, available, verb in (
                (self.undo_action, history.undo_label, history.can_undo, 'Undo'),
                (self.redo_action, history.redo_label, history.can_redo, 'Redo')):
            if self.building: label, available = '', True  # The shape builder's own history.
            action.setText(verb + (' ' + label if label else ''))
            action.setEnabled(available)

    def commit_history(self, now=None, label=None, force=False):
        """Push the Composer project as one step. label names it and stops it merging; label or
        force commits even while a mouse button is held (undo_step)."""
        self.history_timer.stop()
        if self.restoring: return
        if not force and label is None and self.effects_panel.gesture is not None: return
        if label is None and not force and QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            self.history_timer.start(50); return
        self.store_clip_scene()
        self.history_timer.stop()  # store_clip_scene may have scheduled another push.
        state = self.history_state()
        if state.project is not self.history.state.project:
            focus = QApplication.focusWidget()
            audio_edit = (state.project.audio_clips != self.history.state.project.audio_clips
                          or state.project.audio_tracks != self.history.state.project.audio_tracks)
            self.history.push(label or describe(self.history.state.project, state.project), state,
                              key=id(focus) if label is None and not audio_edit and focus is not None and focus is not self.composer_panel.timeline else None, now=now)
        self.project_dirty = self.history.dirty
        self.update_history_actions(); self.update_title()

    # Sandbox undo (U1, 0.78.0-dev) -------------------------------------------------------
    # Every Sandbox edit replaces sandbox_scene.settings (frozen) or its mode. sync() and the
    # housekeeping tick notice the new object (watch_scene_history, an identity test) and push
    # one snapshot once no mouse button is held, so a slider or viewport drag is one step and
    # repeated wheel or key steps on one control within 0.4 s merge, as in the Composer.
    def watch_text_undo(self, old, new):
        if text_target(new) is not None and not new.property('textUndoKeys'):
            new.installEventFilter(self.text_undo_keys); new.setProperty('textUndoKeys', True)

    def scene_snapshot(self):
        scene = self.sandbox_scene
        return SceneState(scene.settings, scene.mode, scene.selected)

    def reset_scene_history(self):
        """A newly opened or recovered Sandbox scene starts a fresh history."""
        self.scene_history_timer.stop(); self.scene_history.reset(self.scene_snapshot())
        self.update_history_actions()

    def watch_scene_history(self):
        if self.scene_restoring: return
        scene, state = self.sandbox_scene, self.scene_history.state
        if scene.settings is state.settings and scene.mode == state.mode:
            if scene.selected != state.selected: self.scene_history.amend(replace(state, selected=scene.selected))
        elif not self.scene_history_timer.isActive(): self.scene_history_timer.start(0)

    def commit_scene_history(self, now=None, label=None, force=False):
        """Push the Sandbox scene as one step if its saved content changed. label names the step
        and stops it merging (undo_step); force commits even while a mouse button is held."""
        self.scene_history_timer.stop()
        if self.scene_restoring: return
        if label is None and not force and QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            self.scene_history_timer.start(50); return
        before, state = self.scene_history.state, self.scene_snapshot()
        if state.settings is before.settings and state.mode == before.mode: return
        if saved_value(state) == saved_value(before):
            self.scene_history.amend(state); return  # Only runtime-only fields changed.
        name = label or describe_scene(before, state)
        focus = QApplication.focusWidget()
        key = None if label is not None or name in STRUCTURAL else (id(focus) if focus is not None else None, name)
        self.scene_history.push(name, state, key=key, now=now)
        self.update_history_actions()

    @contextmanager
    def undo_step(self, label):
        """Make every edit inside one Undo step named label in the active workspace (R1's
        'Replace object'). Edits still pending are committed first as their own step."""
        composer = self.workspace == COMPOSER
        if composer: self.commit_history(force=True)
        else: self.commit_scene_history(force=True)
        try: yield
        finally:
            if composer: self.commit_history(label=label)
            else: self.commit_scene_history(label=label)

    def restore_scene(self, state):
        """Show a Sandbox snapshot: settings (keeping runtime-only fields), mode and selection."""
        scene = self.sandbox_scene; current = scene.settings
        ids = lambda settings: [object_id(s, i) for i, s in enumerate(settings.shapes)]
        moving = lambda settings: [(s.motion, s.custom.attachments if s.custom is not None else None) for s in settings.shapes]
        self.scene_restoring = True
        try:
            scene.settings = with_live(state.settings, current); scene.mode = state.mode
            count = len(scene.settings.shapes)
            scene.selected = min(max(state.selected, 0), count-1) if count else -1
            if ids(current) != ids(scene.settings): self.clear_solos()
            else:
                kinds = {object_id(s, i): tuple(t.kind for t in s.transforms) for i, s in enumerate(scene.settings.shapes)}
                for i, s in enumerate(current.shapes):
                    uid = object_id(s, i)
                    if kinds.get(uid) != tuple(t.kind for t in s.transforms): self.transform_solos.pop(uid, None)
            self.cycle_key = None
            if moving(current) != moving(scene.settings): self.restart_motion()
            self.sync()
        finally:
            self.scene_restoring = False
        self.update_history_actions()

    def undo(self): self.restore_history(False)

    def redo(self): self.restore_history(True)

    def restore_history(self, redo):
        if self.building:
            (self.builder.redo if redo else self.builder.undo)(); return
        own = text_history(QApplication.focusWidget(), redo)
        if own is not None:  # A text field undoes its own typing first.
            (own.redo if redo else own.undo)(); return
        if self.workspace != COMPOSER:
            self.commit_scene_history(force=True)
            state = self.scene_history.redo() if redo else self.scene_history.undo()
            if state is not None: self.restore_scene(state)
            return
        self.effects_panel.finish_gesture()
        self.commit_history(force=True)
        state = self.history.redo() if redo else self.history.undo()
        if state is None: return
        self.restoring = True
        try:
            self.set_project(state.project, dirty=False)
            self.load_clip_scene(state.clip_index)
            self.clip_scene.selected = state.selected_object
            self.refresh_composer(); self.sync()
        finally:
            self.restoring = False
        self.project_dirty = self.history.dirty
        self.update_history_actions(); self.update_title()

    def select_clip(self, index):
        if not 0 <= index < len(self.project.clips): return
        self.store_clip_scene(); self.load_clip_scene(index); self.refresh_composer(); self.sync()
        # Keep the current Undo entry's edited clip up to date (as the Sandbox does for its
        # selection), so undoing a later edit, a clip reorder above all, reopens this clip.
        if not self.restoring and self.history.state.project is self.project and not self.history_timer.isActive():
            self.history.amend(self.history_state())

    @property
    def playhead_beats(self): return float(self.project.beats(self.playhead))

    def connect_composer(self):
        panel = self.composer_panel; view = panel.timeline
        self.midi_recorder = None
        self.cycle_key = None
        self.cycle_reason = ''
        self.cycle_buffer = None
        self.cycle_closing = False
        self.note_audition = None
        for key in ROLL_VIEW_KEYS:
            setattr(view, key, self.preferences[key])
        view.place_roll_buttons()
        view.roll_preferences_changed.connect(self.roll_preferences_changed)
        view.audition.connect(lambda pitch, beat: self.audition_note(pitch, beat))
        view.audition_chord.connect(lambda pitches, beat: self.audition_note(pitches, beat))
        panel.cycle.toggled.connect(self.cycle_toggled)
        panel.record.toggled.connect(self.toggle_note_recording)
        panel.record_midi_menu.aboutToShow.connect(self.rebuild_record_midi_menu)
        panel.play.clicked.connect(self.play)
        panel.to_start.clicked.connect(lambda:self.seek_song(0.))
        view.seek.connect(lambda beats:self.seek_song(float(self.project.seconds(beats))))
        view.edited.connect(lambda project,index:self.set_project(project))
        view.clip_moved.connect(lambda project,index:self.set_project(project,clip_index=index))
        view.clip_move_finished.connect(lambda:self.commit_history(label='Move clip'))  # Equal-length swaps too.
        view.chords_requested.connect(self.chords_action.setChecked)
        view.velocity_requested.connect(self.velocity_action.setChecked)
        view.clip_selected.connect(self.select_clip)
        view.notice.connect(self.flash)  # Copy/paste results and errors.
        view.audio_files_dropped.connect(lambda paths, beat, track: self.add_audio_clip_files(paths, beat, track))
        view.audio_add_requested.connect(lambda beat, track: self.choose_audio_clip(beat, track))
        view.audio_track_solo_requested.connect(lambda index: self.toggle_track_solo(index))
        view.track_solos_requested.connect(lambda tracks, on: self.toggle_track_solo(tracks, on))
        view.track_move_finished.connect(lambda count: self.commit_history(label='Move audio track' if count == 1 else 'Move audio tracks'))
        view.audio_locate_requested.connect(lambda index: self.locate_audio_clip(index))
        view.audio_solo_requested.connect(lambda index: self.toggle_audio_solo(index))
        view.track_effects_requested.connect(lambda index: self.open_track_effects(index))
        view.shown_lane_changed.connect(self.effects_panel.update_lane_buttons)
        view.track_selected.connect(lambda t: self.effects_panel.set_track(t) if self.effects_panel.isVisible() else None)
        view.automate_requested.connect(self.automate)
        view.clip_template_requested.connect(self.append_clip_template)
        self.clip_warned = False
        panel.bpm.valueChanged.connect(self.tempo_changed)
        from phosphor.engine.metronome import TapTempo
        self.tap_clock = TapTempo()
        panel.tap.clicked.connect(self.tap_tempo)
        panel.metronome.toggled.connect(lambda on: setattr(self.audio, 'metronome', on))
        panel.loop.toggled.connect(lambda on:self.set_project(replace(self.project,loop=on)) if on != self.project.loop else None)
        panel.clip_length.valueChanged.connect(self.clip_length_changed)
        panel.add_clip.clicked.connect(self.append_clip)
        panel.delete_clip.clicked.connect(lambda:view.delete_clip(self.clip_index))

    def refresh_composer(self, *, effects_track=None, follow=False):
        if not hasattr(self,'composer_panel'): return
        panel, project = self.composer_panel, self.project
        panel.timeline.audio_solos = frozenset(self.audio_solos)
        panel.timeline.audio_track_solos = frozenset(self.track_solos)
        panel.timeline.set_project(project,self.clip_index); panel.timeline.set_playhead(self.playhead_beats, follow=follow)
        clip = project.clips[self.clip_index]
        bar_length = float(Meter(project.meter_changes).beats_per_bar_at(clip.start))
        with QSignalBlocker(panel.clip_length): panel.clip_length.setRange(.01, MAX_BEATS / bar_length)
        for widget,value in ((panel.bpm,project.bpm),(panel.loop,project.loop),(panel.clip_length,clip.length/bar_length)):
            blocker = QSignalBlocker(widget)
            widget.setChecked(value) if widget is panel.loop else widget.setValue(value)
        panel.clip_label.setText(f'Editing: {project.clips[self.clip_index].name}')
        self.update_scene_context()
        panel.delete_clip.setEnabled(len(project.clips) > 1)
        panel.position.setText(position_text(project,self.playhead_beats))
        panel.play.setText('Stop' if self.audio.running and self.audio.composer is not None else 'Play')
        self.sync_solo_pill()
        if effects_track is not None: self.effects_panel.set_track(effects_track)
        elif self.effects_panel.isVisible(): self.effects_panel.refresh()

    def song_tail(self):
        """Seconds the song plays on past its end for effect tails, cached per project (read per frame)."""
        cached = getattr(self, '_song_tail', None)
        if cached is None or cached[0] is not self.project:
            cached = self._song_tail = (self.project, processing_tail(self.project))
        return cached[1]

    def open_track_effects(self, t):
        if self.left_panel.isHidden(): self.toggle_browser(True)
        self.left_panel.stack.setCurrentWidget(self.effects_panel)
        self.effects_panel.set_track(t)
        return self.effects_panel

    def toggle_chords(self, on):
        if self.workspace != COMPOSER: return
        self.commit_history()
        self.composer_panel.timeline.setFocus()
        self.set_project(replace(self.project, chords=on))
        self.commit_history()

    def sync_chords_action(self):
        self.sync_velocity_action()
        if hasattr(self, 'chords_action'):
            blocker = QSignalBlocker(self.chords_action)
            self.chords_action.setChecked(self.project.chords)

    def toggle_velocity(self, on):
        if self.workspace != COMPOSER: return
        self.commit_history()
        self.composer_panel.timeline.setFocus()
        self.set_project(replace(self.project, velocity=on))
        self.commit_history()

    def sync_velocity_action(self):
        if hasattr(self, 'velocity_action'):
            blocker = QSignalBlocker(self.velocity_action)
            self.velocity_action.setChecked(self.project.velocity)

    def import_midi_notes(self, path=None, mode=None, set_tempo=None, chords=None):
        if self.workspace != COMPOSER: return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, 'Import MIDI into notes', str(ASSETS_DIR/'scores'), 'MIDI file (*.mid *.midi)')
            if not path: return False
        if mode is None or set_tempo is None:
            dialog = QDialog(self); dialog.setWindowTitle('Import MIDI into notes')
            layout = QVBoxLayout(dialog)
            choice = QComboBox(); choice.addItems(('Replace notes', 'Insert at playhead'))
            choice.setCurrentIndex(1 if mode == 'insert' else 0); layout.addWidget(choice)
            voices = QComboBox(); voices.addItems(('All notes (chords)', 'Melody only'))
            voices.setCurrentIndex(0 if (self.project.chords if chords is None else chords) else 1)
            layout.addWidget(voices)
            tempo = QCheckBox('Set the project tempo from the file'); tempo.setChecked(bool(set_tempo)); layout.addWidget(tempo)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted: return False
            mode = ('replace', 'insert')[choice.currentIndex()]; set_tempo = tempo.isChecked()
            chords = voices.currentIndex() == 0
        if chords is None: chords = self.project.chords
        try:
            midi = read_midi(path, chords=chords)
            self.composer_panel.timeline.setFocus()  # A discrete edit, outside numeric-field coalescing.
            self.commit_history()
            beats = self.playhead_beats
            view = self.composer_panel.timeline
            project = import_notes(self.project, midi, mode, view.snapped(beats), 0., set_tempo)
        except (OSError, ValueError, EOFError, KeyError, IndexError) as error:
            self.error(error); return False
        self.playhead = float(project.seconds(beats))
        self.set_project(project)
        if self.audio.running and self.audio.composer is not None: self.start_song()
        self.flash(f'Imported {len(midi.notes)} notes; dropped {midi.dropped} (pitch, duration or note limit).')
        return True

    def export_midi_notes(self, path=None):
        if self.workspace != COMPOSER: return False
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, 'Export notes as MIDI', '', 'MIDI file (*.mid)')
        if not path: return False
        try: write_midi(path, self.project)
        except (OSError, ValueError) as error:
            self.error(error); return False
        self.flash('Exported notes as MIDI.')
        return True

    def tempo_changed(self, bpm):
        if self.midi_recorder is not None: self.stop_song()
        beats = self.playhead_beats  # Keep the playhead on its beat.
        self.set_project(replace(self.project,bpm=float(bpm))); self.playhead = float(self.project.seconds(beats))
        if self.audio.running and self.audio.composer is not None: self.start_song()
        self.refresh_composer()

    def tap_tempo(self):
        bpm = self.tap_clock.tap()
        if bpm is not None:
            self.commit_history()
            self.tempo_changed(bpm)
            self.commit_history()  # Retain the existing 400 ms tempo undo coalescing.

    def clip_length_changed(self, bars):
        clip = self.project.clips[self.clip_index]
        self.set_project(fit_clip(self.project,self.clip_index,clip.start,bars*float(Meter(self.project.meter_changes).beats_per_bar_at(clip.start))))

    def append_clip(self):
        start = self.project.clips[-1].end if self.project.clips else 0.
        meter = Meter(self.project.meter_changes)
        bar, offset = meter.bar_of(start)
        start = float(meter.bar_start(int(bar) + (float(offset) > 1e-9)))
        try: project,index = add_clip(self.project,start)
        except ValueError as error: self.error(error); return
        self.set_project(project); self.select_clip(index)

    def append_clip_template(self, template_id, beat=-1.0):
        """Create a pre-populated Composer clip from a template (at `beat`, or appended at the next bar)."""
        if self.workspace != COMPOSER or self.project is None: return False
        from phosphor.engine.signal_engine import Settings
        from phosphor.engine.function_plot import PRESETS as PARAM_PRESETS
        from phosphor.engine.recurrence import PRESETS as REC_PRESETS
        from phosphor.engine.object_replace import new_shape
        self.store_clip_scene()
        if beat >= 0.0:
            start = float(beat)
        else:
            end = self.project.clips[-1].end if self.project.clips else 0.
            meter = Meter(self.project.meter_changes)
            bar, offset = meter.bar_of(end)
            start = float(meter.bar_start(int(bar) + (float(offset) > 1e-9)))

        turntable = BUILTIN_RECIPES['3D Turntable'].transforms
        clip_name = None
        new_settings = None
        mode = 'drawing'
        if template_id == 'empty':
            pass
        elif template_id == 'duplicate' and 0 <= self.clip_index < len(self.project.clips):
            # A full copy (lanes, length, local time, offset, colour, mute), as Alt+drag makes.
            try: project, index = duplicated_clip_named(self.project, self.clip_index, start)
            except ValueError as error:
                self.error(error); return False
            with self.undo_step('Duplicate clip'):
                self.set_project(project); self.select_clip(index)
            return True
        elif template_id.startswith('solid:'):
            solid = template_id.split(':', 1)[1]
            shape = replace(new_shape(solid), transforms=turntable)
            new_settings = replace(Settings(), travel_mode='Faster travel', travel_speed=64., shapes=(shape,))
            clip_name = solid
        elif template_id.startswith('anim:'):
            solid = template_id.split(':', 1)[1]
            shape = new_shape(solid)
            new_settings = replace(Settings(), travel_mode='Faster travel', travel_speed=64., shapes=(shape,))
            clip_name = solid
        elif template_id.startswith('param:'):
            pname = template_id.split(':', 1)[1]
            plot = PARAM_PRESETS.get(pname)
            if plot is None: return False
            xforms = turntable if plot.z != '0' else ()
            shape = replace(new_shape(PARAMETRIC_OBJECT), plot=plot, transforms=xforms)
            new_settings = replace(Settings(), travel_mode='Faster travel', travel_speed=64., shapes=(shape,))
            clip_name = pname
        elif template_id.startswith('rec:'):
            rname = template_id.split(':', 1)[1]
            plot = REC_PRESETS.get(rname)
            if plot is None: return False
            xforms = turntable if plot.z != '0' else ()
            shape = replace(new_shape(RECURRENCE_OBJECT), recurrence=plot, transforms=xforms)
            new_settings = replace(Settings(), travel_mode='Faster travel', travel_speed=64., shapes=(shape,))
            clip_name = rname
        else:
            return False

        try:
            project, index = add_clip(self.project, start, settings=new_settings,
                                      name=(clip_name[:48] if clip_name else None), mode=mode)
        except ValueError as error:
            self.error(error); return False
        self.set_project(project); self.select_clip(index)
        return True

    def seek_song(self, seconds):
        if self.midi_recorder is not None: self.stop_song()
        self.playhead = max(0.,float(seconds))
        if self.audio.running and self.audio.composer is not None:
            self.start_song()
        else:
            self.refresh_composer(follow=True)

    def cycle_toggled(self, on):
        self.cycle_key = None
        if not on and self.audio.cycle:
            self.audio.stop()
        # Synthesis/start is deferred to the next display frame.
        self.last_status = 0

    def roll_preferences_changed(self):
        view = self.composer_panel.timeline
        for key in ROLL_VIEW_KEYS:
            self.preferences[key] = getattr(view, key)
        if self.remember_recent: self.preferences_save.start()

    def audition_note(self, pitches, beat):
        """Use one cycle buffer, then request silence at its first boundary.

        update_cycle observes publication by the audio worker before requesting
        silence; this avoids a timer racing the worker's first read of the buffer.
        """
        if (self.workspace != COMPOSER or self.exporting or self.midi_recorder is not None
                or self.audio.composer is not None or self.audio.midi.capture
                or self.system_monitor or self.video_monitor or self.building
                or (self.audio.running and not self.audio.cycle)):
            return
        from phosphor.engine.composition import Note, Project
        pitches = (pitches,) if isinstance(pitches, int) else tuple(pitches)
        if not pitches: return
        project = self.output_project()
        clip = next((c for c in project.clips if c.start <= beat < c.end), project.clips[self.clip_index])
        start = float(self.project.seconds(beat))
        sources = [next((n for n in reversed(self.project.notes) if n.pitch == p and abs(n.start - beat) < 1e-6), Note(beat, .25, p)) for p in pitches]
        duration = min(.3, max(float(self.project.seconds(n.end)) - start for n in sources))
        end = float(self.project.beats(start + duration))
        preview = Project(bpm=self.project.bpm, tempo_changes=self.project.tempo_changes, velocity=self.project.velocity,
                          notes=tuple(replace(n, start=beat, length=min(n.length, end - beat), glide=0.) for n in sources),
                          clips=(replace(clip, start=beat, length=end - beat,
                                         time_offset=clip.time_offset + beat - clip.start),))
        def samples(machine):
            count = max(1, round(duration * machine.sample_rate))
            buffer = render_song(start + np.arange(count) / machine.sample_rate, preview, machine)
            fade = min(count, max(1, round(.008 * machine.sample_rate)))
            buffer[-fade:] *= np.linspace(1., 0., fade)[:, None]
            # The cycle stop fades the beginning at its next boundary. Silence
            # there prevents a second attack; a short silent tail gives the GUI
            # time to observe even the shortest allowed note before that boundary.
            return np.pad(np.clip(buffer, -1., 1.).astype('float32'),
                          ((round(.005 * machine.sample_rate), round(.04 * machine.sample_rate)), (0, 0)))
        machine = replace(self.output_settings(), source=TONE_SOURCE, sample_rate=self.audio.sample_rate, motion_time_offset=0.)
        try:
            old = self.audio.cycle_samples
            self.audio.set_cycle(samples(machine)); self.audio.cycle = True
            self.note_audition = ('pending', old)
            self.cycle_key = None
            if not self.audio.running: self.audio.start(machine, self.scene.mode, cycle_factory=samples)
        except Exception as error:
            self.note_audition = None; self.audio.stop(); self.flash(str(error))

    def update_cycle(self, settings):
        if self.note_audition is not None:
            if (self.workspace != COMPOSER or self.exporting or self.midi_recorder is not None
                    or self.audio.composer is not None or not self.audio.cycle or not self.audio.running):
                self.note_audition = None
            else:
                phase, old = self.note_audition
                current = self.audio.cycle_samples
                if phase == 'pending' and current is not None and current is not old:
                    self.audio.set_cycle(None); self.note_audition = ('playing', None)
                elif phase == 'playing' and current is None:
                    self.note_audition = None; self.cycle_key = None
                if self.note_audition is not None: return
        eligible = (self.composer_panel.cycle.isChecked() and self.workspace == COMPOSER
                    and not self.cycle_closing and not self.system_monitor and not self.video_monitor
                    and self.midi_recorder is None and not self.audio.midi.capture
                    and not self.building and self.audio.composer is None
                    and (not self.audio.running or self.audio.cycle))
        if not eligible:
            if self.audio.cycle: self.audio.stop()
            self.cycle_key = None
            return
        machine = replace(settings, source=TONE_SOURCE, motion_time_offset=0.)
        # Projects are replaced, never mutated: identity avoids a deep compare every frame.
        old = self.cycle_key
        if old is not None and old[0] is self.project and old[1] == self.playhead and old[2] == machine: return
        self.cycle_key = (self.project, self.playhead, machine)
        samples, period, reason = song_cycle(self.output_project(), self.playhead, machine)
        self.cycle_buffer = samples
        if reason != self.cycle_reason:
            self.cycle_reason = reason
            if reason: self.flash(reason)
        self.audio.set_cycle(samples)
        if not self.audio.running and samples is not None:
            try:
                self.audio.cycle = True
                settle_garbage()
                def at_device_rate(device_machine):
                    buffer, _, _ = song_cycle(self.output_project(), self.playhead, device_machine)
                    self.cycle_buffer = buffer
                    self.cycle_key = (self.project, self.playhead, device_machine)
                    return buffer
                self.audio.start(machine, self.scene.mode, cycle_factory=at_device_rate)
            except Exception as error:
                self.audio.stop()
                self.audio.error = str(error)
                self.flash(str(error))

    def start_song(self, count_in=None):
        """Play from the playhead; count_in is 0/1/2 bars, reusable by recording.

        None uses the transport selector on fresh starts and no pre-roll on restarts.
        """
        crash_log.note(f'play at beat {self.composer_panel.timeline.playhead:.3f}; {len(self.project.notes)} notes, chords {"on" if self.project.chords else "off"}')
        restarting = self.audio.running and self.audio.composer is not None
        if self.audio.running or self.audio.cycle: self.audio.stop()
        self.cycle_key = None
        if count_in is None:
            count_in = 0 if restarting else self.composer_panel.count_in.currentIndex()
        if count_in not in (0, 1, 2): raise ValueError('Count-in must be 0, 1 or 2 bars.')
        self.audio.count_in_seconds = float(self.project.seconds(count_in * BEATS_PER_BAR))
        self.audio.metronome_level = self.preferences['metronome_level']
        end = float(self.project.seconds(self.project.end_beats))
        if not self.project.loop and self.playhead >= end - 1e-6: self.playhead = 0.
        self.play_start = self.playhead
        self.clip_warned = False  # Warn about clipping once per playback.
        self.audio.composer = (self.output_project(),self.play_start)
        settle_garbage()  # Before audio starts, while a pause cannot be heard.
        machine = replace(self.output_settings(),source=TONE_SOURCE,motion_time_offset=0.)
        self.audio.start(machine,self.scene.mode)
        self.refresh_composer(follow=True)

    def recording_seconds(self):
        # Continuous submitted clock, rather than the worker's bursty frame counter.
        # PlaybackHistory.origin includes output latency; compensation happens once below.
        history = self.audio.playback
        elapsed = (max(0., history.clock() - history.origin + self.audio.output_latency)
                   if history.origin is not None else self.audio.frames / self.audio.sample_rate)
        return self.play_start + elapsed - self.audio.count_in_seconds

    def toggle_note_recording(self, on):
        if not on:
            self.stop_song(); return
        if self.workspace != COMPOSER or self.audio.midi.port is None:
            with QSignalBlocker(self.composer_panel.record): self.composer_panel.record.setChecked(False)
            self.flash('Connect a Live MIDI input in Sandbox › Input before recording.'); return
        from phosphor.engine.midi_recorder import MidiRecorder
        self.composer_panel.timeline.setFocus(); self.commit_history()
        try:
            if not self.audio.running or self.audio.cycle or self.audio.preview: self.start_song()  # Record replaces a preview (AW4).
            panel = self.composer_panel
            self.midi_recorder = MidiRecorder(self.project,
                'replace' if panel.record_replace.isChecked() else 'overdub',
                panel.timeline.snap if panel.record_quantise.isChecked() else 0., self.audio.output_latency,
                chords=self.project.chords)
            self.note_events = queue.Queue(maxsize=40000)
            self.audio.midi.note_listener = self.queue_recorded_message
        except Exception as error:
            self.stop_song()
            self.error(error)

    def queue_recorded_message(self, message):
        seconds = self.recording_seconds()
        # Ignore the count-in even when starting at a nonzero playhead.
        if seconds < self.play_start: return
        try: self.note_events.put_nowait((message, seconds))
        except queue.Full: pass

    def drain_note_recording(self):
        if self.midi_recorder is None: return
        while True:
            try: message, seconds = self.note_events.get_nowait()
            except queue.Empty: break
            self.midi_recorder.feed(message, seconds)

    def stop_song(self):
        crash_log.note('stop')
        self.audio.midi.note_listener = None
        recorder = getattr(self, 'midi_recorder', None)
        if recorder is not None:
            self.drain_note_recording()
            project = recorder.stop(self.recording_seconds(), self.project)
            self.midi_recorder = None
            self.composer_panel.timeline.setFocus(); self.commit_history()
            if project is not self.project:
                self.set_project(project); self.commit_history()
            self.flash(f'Recorded {len(recorder.notes)} notes into the roll.')
        with QSignalBlocker(self.composer_panel.record): self.composer_panel.record.setChecked(False)
        self.audio.stop(); self.audio.composer = None; self.refresh_composer()
        self.cycle_key = None  # Resume lazily at the next display frame.

    def send_to_composer(self):
        """Copy the Sandbox scene into the project as a clip after the last one."""
        start = self.project.clips[-1].end if self.project.clips else 0.
        meter = Meter(self.project.meter_changes)
        bar, offset = meter.bar_of(start)
        start = float(meter.bar_start(int(bar) + (float(offset) > 1e-9)))
        name = (self.sandbox_scene.path.name.removesuffix('.json').removesuffix('.scene') if self.sandbox_scene.path else 'Sandbox scene')[:64]
        try: project,index = add_clip(self.project,start,self.sandbox_scene.settings,DEFAULT_CLIP_BEATS,name,self.sandbox_scene.mode)
        except ValueError as error: self.error(error); return
        self.set_project(project); self.clip_index = index; self.load_clip_scene(index)
        self.flash(f'Added "{name}" to Composer as clip {index+1} (bar {int(meter.bar_of(start)[0])+1}).')

    def flash(self, text):
        self.notice = (text,time.perf_counter()+5); self.last_status = 0
        self.statusBar().showMessage(text, 5000)

    # Documents follow the workspace ---------------------------------------------
    def open_document(self): self.open_project() if self.workspace == COMPOSER else self.open_scene()

    def save_document(self, save_as=False): return self.save_project(save_as) if self.workspace == COMPOSER else self.save_scene(save_as)

    def confirm_project(self, action, force=False):
        if not force and not self.project_dirty: return True
        result = QMessageBox.question(self,'Save current project?',f'Save the current Composer project before {action}?',
            QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        return self.save_project() if result == QMessageBox.StandardButton.Save else result == QMessageBox.StandardButton.Discard

    def save_project(self, save_as=False):
        if self.building and not self.leave_builder(): return False
        path = self.project_path
        if save_as or path is None:
            value,_ = QFileDialog.getSaveFileName(self,'Save project',str(path or 'Untitled'+PROJECT_SUFFIX),'phosphor project (*.project.json)')
            if not value: return False
            path = Path(value if value.lower().endswith('.json') else value+PROJECT_SUFFIX)
        try:
            self.commit_history(); save_project(path,self.project)
            self.history.mark_saved(); self.update_history_actions()
            self.project_path, self.project_dirty = path, False; self.update_title()
            self.clear_autosave(COMPOSER)
            self.remember_file('project',path); return True
        except Exception as error: self.error(error); return False

    def choose_audio_clip(self, beat=None, track=None):
        title = 'Add audio clip at playhead' if beat is None else 'Add audio clip'
        paths, _ = QFileDialog.getOpenFileNames(self, title, '', 'Audio files (*.wav *.flac *.ogg *.mp3 *.aif *.aiff)')
        if paths: self.add_audio_clip_files(paths, beat, track)

    def add_audio_clip_file(self, path):
        return self.add_audio_clip_files([path])

    def add_audio_clip_files(self, paths, beat=None, track=None):
        """Add files as audio clips one after another from `beat` (default: the playhead) on `track`
        (default: the first selected track, else track 0; -1: a new track); one Undo step."""
        if self.workspace != COMPOSER or not paths: return False
        try:
            self.commit_history()
            project, beat = self.project, float(self.project.beats(self.playhead)) if beat is None else float(beat)
            view = self.composer_panel.timeline
            if track is None: track = min(view.track_picks) if view.track_picks else 0
            if not project.audio_tracks: track = 0  # The first clip makes 'Audio 1' (Project.__post_init__).
            elif track == -1 or track >= len(project.audio_tracks): project, track = audio_tracks.add_track(project)
            names = []
            for path in paths:
                clip = replace(audio_clip_from_file(path, beat), track=track)
                project, index = audio_edit.add(project, clip)
                beat = float(audio_clip_end_beats(project, clip)); names.append(clip.name)
                self.remember_file('audio', path)
            self.composer_panel.timeline.select_audio(index)
            self.composer_panel.timeline.setFocus()
            self.set_project(project)
            self.commit_history()
            self.flash(f'Added audio clip: {names[0]}' if len(names) == 1 else f'Added {len(names)} audio clips.')
            return True
        except Exception as error:
            self.error(error); return False

    def load_project(self, project, path):
        self.effects_panel.finish_gesture()
        self.prepare_audio(project)
        self.clear_solos(); self.audio_solos.clear(); self.track_solos.clear()
        self.stop_song(); self.project, self.project_path, self.project_dirty = project, path, False
        self.sync_chords_action()
        self.playhead = 0.; self.load_clip_scene(0)
        self.history_timer.stop(); self.history.reset(self.history_state())
        self.update_history_actions()
        if self.workspace == COMPOSER: self.scene = self.clip_scene
        self.restart_motion(); self.refresh_composer(effects_track=0); self.sync()
        if path is not None: self.remember_file('project',path)

    def open_project(self):
        if self.building and not self.leave_builder(): return
        value,_ = QFileDialog.getOpenFileName(self,'Open project','','phosphor project (*.project.json *.json)')
        if not value: return
        self.open_project_path(value)

    def open_project_path(self, value, recent=False):
        if self.building and not self.leave_builder(): return
        try:
            if not self.confirm_project('opening another project'): return
            notices = []; project = read_project(value,notices)
            self.load_project(project,Path(value))
            missing = audio_media.missing_count(project)
            if missing: notices = [n for n in notices if not n.startswith('Audio file not found: ')]
            if notices: self.flash(' · '.join(notices))
            if missing: self.show_missing_audio()
        except Exception as error:
            if recent: self.forget_file('project',value)
            self.error(error)

    def new_project(self):
        if self.building and not self.leave_builder(): return
        if not self.confirm_project('starting a new project'): return
        self.load_project(new_project(),None)

    def output_settings(self):
        dwell=self.signal_preferences['corner_dwell_us']
        count=self.signal_preferences['shape_redraw_count'] if self.signal_preferences['shape_redraw'] else 1
        # The redraw check and the route warm-up depend only on these; skip them on display
        # frames where nothing changed (audit P8). The tuple holds the settings, so `is` is safe.
        warm=getattr(self,'_warm_key',None);settings=self.scene.settings;mode=self.signal_preferences['route_mode']
        if warm is not None and warm[0] is settings and warm[1:3]==(mode,count):count,self.redraw_notice=warm[3:]
        else:
            requested=count;self.redraw_notice=''
            if count>1:
                try:planned_path(0,settings.drawing_hz,scene_shapes(settings),settings.travel_mode,settings.travel_speed,0.,count)
                except ValueError as error:count=1;self.redraw_notice='Redraw inactive: '+str(error)
            # Plan dense routes here, on the UI thread and once (cached), so the audio thread
            # never stalls on a large import's first frame.
            for shape in settings.shapes:
                try:
                    shape_geometry(shape,mode)
                    if shape.solid==CUSTOM_OBJECT:planned_components(shape.custom,mode)
                except ValueError:pass
            self._warm_key=(settings,mode,requested,count,self.redraw_notice)
        # Playback renders at the output device's rate (AudioPreview.sample_rate).
        return replace(self.scene.settings,shapes=self.audition_shapes(),corner_dwell_us=dwell,motion_time_offset=self.motion_audio_offset if self.audio.running else 0.,shape_redraw_count=count,route_mode=self.signal_preferences['route_mode'],sample_rate=self.audio.sample_rate,
                       switch_sweep_ms=self.signal_preferences['switch_sweep_ms'],
                       tempo=self.project.bpm)  # p (beats) follows the Composer tempo, in the Sandbox too.

    def any_solo(self):
        return bool(self.solo_ids or self.transform_solos or (self.workspace == COMPOSER and (self.audio_solos or self.track_solos)))

    def sync_solo_pill(self):
        if hasattr(self, 'solo_pill'):
            self.solo_pill.setVisible(not self.immersive and self.any_solo())

    def clear_all_solos(self):
        self.clear_solos()
        self.audio_solos.clear(); self.track_solos.clear()
        if self.audio.composer is not None: self.audio.composer = (self.output_project(), self.play_start)
        self.refresh_composer(); self.sync()
        self.flash('Cleared all solos.')

    def rebuild_record_midi_menu(self):
        menu = self.composer_panel.record_midi_menu
        menu.clear()
        current = self.audio.midi.port_name if self.audio.midi.port is not None else None
        names = [self.devices.itemData(i) for i in range(self.devices.count()) if self.devices.itemData(i)]
        if current and current not in names: names.insert(0, current)
        none_action = menu.addAction(NO_MIDI_DEVICE)
        none_action.setCheckable(True); none_action.setChecked(current is None)
        none_action.triggered.connect(lambda checked=False: self._pick_composer_midi(None))
        for name in names:
            action = menu.addAction(name.replace('&', '&&'))
            action.setCheckable(True); action.setChecked(name == current)
            action.triggered.connect(lambda checked=False, n=name: self._pick_composer_midi(n))
        if not names:
            empty = menu.addAction('No MIDI inputs listed (click Refresh)'); empty.setEnabled(False)
        menu.addSeparator()
        menu.addAction('Refresh MIDI devices', self.refresh_ports)

    def _pick_composer_midi(self, name):
        self.scene.device = name or ''
        if name: self.connect_midi()
        else: self.disconnect_midi()
        self.sync()

    def _objects_moved(self, start, row):
        count = len(self.scene.settings.shapes)
        if not 0 <= start < count: return
        to = min(row if row < start else row - 1, count - 1)
        if to == start: return
        QTimer.singleShot(0, lambda s=start, d=to: self.reorder_objects(s, d))

    def reorder_objects(self, source, destination):
        if source == destination: return False
        with self.undo_step('Reorder objects'):
            if not self.scene.move(source, destination): return False
            self.clear_solos()
            self.cycle_key = None
            self.audio.parameters = (self.output_settings(), self.scene.mode)
            self.sync()
        return True

    def clear_solos(self):
        self.solo_ids.clear(); self.transform_solos.clear()
        self.cycle_key = None

    def audition_shapes(self):
        return audition_shapes(self.scene.settings.shapes, self.solo_ids, self.transform_solos.items())

    def output_project(self):
        if self.workspace != COMPOSER or not (self.solo_ids or self.transform_solos or self.audio_solos or self.track_solos): return self.project
        # Keyed on the project object itself (audit B1): an id() key let a new project that
        # reused a freed one's address get the old audition copy.
        key = (self.clip_index, frozenset(self.solo_ids), tuple(self.transform_solos.items()), frozenset(self.audio_solos),
               frozenset(self.track_solos))
        if getattr(self, '_audition_source', None) is not self.project or getattr(self, '_audition_key', None) != key:
            project = self.project
            if self.solo_ids or self.transform_solos:
                clip = project.clips[self.clip_index]
                shapes = audition_shapes(clip.settings.shapes, self.solo_ids, self.transform_solos.items())
                project = with_clip(project, self.clip_index, replace(clip, settings=replace(clip.settings, shapes=shapes)))
            if self.audio_solos or self.track_solos:
                project = audio_edit.soloed(project, frozenset(self.audio_solos), frozenset(self.track_solos))
            self._audition_project = project
            self._audition_source, self._audition_key = self.project, key
        return self._audition_project

    def toggle_audio_solo(self, index):
        """Solo or unsolo an audio clip: only soloed audio clips play, and the scenes are silent."""
        if self.workspace != COMPOSER or not 0 <= index < len(self.project.audio_clips): return
        if index in self.audio_solos: self.audio_solos.remove(index)
        else: self.audio_solos.add(index)
        self.cycle_key = None
        if self.audio.composer is not None: self.audio.composer = (self.output_project(), self.play_start)
        self.refresh_composer()
        if self.audio_solos: self.flash('Audio solo: only the soloed audio clips play; the scenes are silent. Solo is not saved.')

    def toggle_track_solo(self, index, on=None):
        """Solo or unsolo an audio track, or a list of them (all on or all off) (AW1): only soloed
        tracks and clips play; the scenes are silent."""
        tracks = [index] if isinstance(index, int) else list(index)
        if self.workspace != COMPOSER or not tracks or any(not 0 <= t < len(self.project.audio_tracks) for t in tracks): return
        if on is None: on = tracks[0] not in self.track_solos
        if on: self.track_solos.update(tracks)
        else: self.track_solos.difference_update(tracks)
        self.cycle_key = None
        if self.audio.composer is not None: self.audio.composer = (self.output_project(), self.play_start)
        self.refresh_composer()
        if self.track_solos: self.flash('Track solo: only the soloed tracks (and soloed clips) play; the scenes are silent. Solo is not saved.')

    def toggle_objects(self, kind, row=None):
        rows = [self.shape_list.row(item) for item in self.shape_list.selectedItems()] if row is None else [row]
        shapes = list(self.scene.settings.shapes)
        for index in rows:
            if not 0 <= index < len(shapes): continue
            shape = shapes[index]; uid = object_id(shape, index)
            if kind == 'mute':
                if QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier:
                    self.unmute_all(); return
                shapes[index] = replace(shape, muted=not shape.muted)
            elif QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier:
                self.solo_ids = {uid}
            elif uid in self.solo_ids: self.solo_ids.remove(uid)
            else: self.solo_ids.add(uid)
        if kind == 'mute': self.set_object_mutes(tuple(shapes))
        self.cycle_key = None
        self.sync()

    def build_object_menu(self, row):
        """An Objects list row's right-click menu (built separately so smokes can inspect it)."""
        menu = QMenu(self)
        shapes = self.scene.settings.shapes
        if not 0 <= row < len(shapes): return menu
        key = ('on', row); automated = self.automated(key)
        allowed = self.workspace == COMPOSER and not is_motion_guide(shapes[row])
        action = switch_action(menu, automated, allowed)
        action.triggered.connect(lambda checked=False: self.automate(key, not automated))
        menu.addSeparator()
        inspect = menu.addAction('Inspect one cycle')
        inspect.setToolTip('Freeze the pose and follow the beam through one cycle (display only).')
        inspect.triggered.connect(lambda checked=False: self.inspect_cycle(True, row))
        menu.addSeparator()
        rows = self.replace_rows(row)
        kinds = {shapes[i].solid for i in rows}
        swap = menu.addMenu('Replace')
        swap.setObjectName('replace_menu'); swap.setToolTipsVisible(True)
        swap.menuAction().setToolTip("Change what the object is: its place, transforms and automation stay; "
                                     "the old type's settings are kept aside for switching back.")
        self.type_actions(swap, lambda kind: self.replace_objects(rows, kind),
                          lambda: self.replace_objects(rows, 'svg'), next(iter(kinds)) if len(kinds) == 1 else None)
        swap.setEnabled(bool(rows))  # Motion paths are never replaced.
        return menu

    def replace_rows(self, row):
        """The rows Replace changes from row's menu: the selection when row is in it, else row;
        motion paths are left out (other objects follow them)."""
        shapes = self.scene.settings.shapes
        selected = sorted(self.shape_list.row(item) for item in self.shape_list.selectedItems())
        rows = selected if row in selected else [row]
        return [i for i in rows if 0 <= i < len(shapes) and not is_motion_guide(shapes[i])]

    def replace_objects(self, rows, kind):
        """Objects list › Replace: every row becomes kind, as one Undo step 'Replace object'
        (engine/object_replace.py). A parked drawing or model comes back; otherwise 3D model…
        asks for a file and Custom shape… opens the Builder, and cancelling changes nothing."""
        shapes = self.scene.settings.shapes
        svg = kind == 'svg'
        if svg: kind = CUSTOM_OBJECT
        rows = [i for i in rows if 0 <= i < len(shapes) and shapes[i].solid != kind]
        if not rows: return False
        if svg:
            drawing = self.choose_svg()
            if drawing is None: return False
            return self.apply_replace(rows, kind, custom=drawing, fresh=True)
        missing = any(needs_input(shapes[i], kind) for i in rows)
        if kind == CUSTOM_OBJECT and missing:
            self.builder_replace = rows; self.open_builder(new=True)
            if not self.building: self.builder_replace = None
            return False
        model = report = None
        if kind == MODEL_OBJECT and missing:
            chosen = self.choose_model()
            if chosen is None: return False
            path, target, result = chosen
            try:
                if result is None: result = run_with_progress(self, f'Importing {Path(path).name}…', lambda: load_model(path, target))
            except (ValueError, OSError) as error: self.error(error); return False
            model, report = result
        changed = self.apply_replace(rows, kind, model=model)
        if changed and report is not None: self.model_notes(report)
        return changed

    def apply_replace(self, rows, kind, custom=None, model=None, fresh=False):
        """Replace rows with kind in one Undo step; False (and nothing changed) on an error.
        fresh: use custom even where a drawing is parked (SVG drawing…)."""
        settings = self.scene.settings
        try:
            changes = {i: replaced(replace(settings.shapes[i], custom=None) if fresh else settings.shapes[i],
                                   kind, custom=custom, model=model) for i in rows}
            for shape in changes.values():
                if shape.solid == CUSTOM_OBJECT: compiled(shape.custom)
            candidate = replace(settings, shapes=tuple(changes.get(i, s) for i, s in enumerate(settings.shapes)))
            validate(candidate)
        except ValueError as error: self.error(error); return False
        moving = any(CUSTOM_OBJECT in (settings.shapes[i].solid, kind) for i in rows)
        with self.undo_step('Replace object'):
            self.scene.settings = candidate
            if len(rows) == 1: self.scene.selected = rows[0]
            self.cycle_key = None
            self.audio.parameters = (self.output_settings(), self.scene.mode)
            self.sync()
        if moving: self.restart_motion()
        return True

    def inspect_cycle(self, on=True, row=None):
        """Start (and show) or stop the cycle inspector; row selects that object first."""
        if row is not None and 0 <= row < len(self.scene.settings.shapes) and row != self.scene.selected: self.select_shape(row)
        if on and not self.inspector.active: self.inspector.set_active(True)
        elif not on and self.inspector.active: self.inspector.set_active(False)
        if on: self.show_panel(PANEL_INSPECT)
        elif self.current_panel == PANEL_INSPECT: self.leave_inspector()

    def close_inspector(self):
        """✕ Close inspector and Esc (UI1): inspector off, back to the panel shown before it."""
        self.inspect_cycle(False)
        if self.current_panel == PANEL_INSPECT: self.leave_inspector()

    def leave_inspector(self):
        previous = getattr(self, 'panel_before_inspect', None)
        if previous in PANELS and previous != PANEL_INSPECT: self.show_panel(previous)
        else: self.show_panel(PANEL_SCENE); self.scene_sections.setCurrentIndex(0)  # Objects.

    def object_menu(self, row, position):
        if row < 0: return
        menu = self.build_object_menu(row); menu.exec(position); menu.deleteLater()

    def set_object_mutes(self, shapes):
        self.scene.settings = replace(self.scene.settings, shapes=shapes)  # Sandbox and Composer history record it.

    def unmute_all(self):
        self.set_object_mutes(tuple(replace(s, muted=False) if s.muted else s for s in self.scene.settings.shapes))
        self.sync()

    def solo_transform(self, index):
        if self.scene.shape is None: return
        uid = object_id(self.scene.shape, self.scene.selected)
        if self.transform_solos.get(uid) == index: self.transform_solos.pop(uid)
        else: self.transform_solos[uid] = index
        self.cycle_key = None
        self.sync()

    def warn(self, title, text):
        """A warning box (smokes replace this on the instance)."""
        QMessageBox.warning(self, title, text)

    def ask(self, title, text):
        """A Yes/No question, Yes by default (smokes replace this on the instance)."""
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        return QMessageBox.question(self, title, text, buttons, QMessageBox.StandardButton.Yes) == QMessageBox.StandardButton.Yes

    def warn_solo_export(self):
        if self.solo_ids or self.transform_solos:
            self.warn('Solo active', 'This export uses the current object and transform solo state. Solo is not saved.')
        if self.workspace == COMPOSER and (self.audio_solos or self.track_solos):
            self.warn('Solo active', 'Audio clips or tracks are soloed: this export has only the soloed audio, and no scenes. Solo is not saved.')
        count = sum(clip.missing for clip in self.project.audio_clips) if self.workspace == COMPOSER else 0
        if count:
            self.warn('Missing audio files', f'{count} audio clip{"s are" if count != 1 else " is"} missing and will be silent in this export. '
                      'Locate the files with File › Find missing audio files….')

    # Missing audio files and Collect media -----------------------------------------
    def pick_audio_file(self, name, folder=''):
        return QFileDialog.getOpenFileName(self, f'Locate {name}', folder, 'Audio files (*.wav *.flac *.ogg *.mp3 *.aif *.aiff)')[0]

    def pick_folder(self, title):
        return QFileDialog.getExistingDirectory(self, title)

    def run_dialog(self, dialog):
        """exec() a dialog (smokes replace this on the instance to drive it)."""
        return dialog.exec()

    def show_missing_audio(self):
        """The Missing audio files dialog (Locate…, Search folder…, Skip)."""
        if self.workspace != COMPOSER: return
        if not audio_media.missing_count(self.project): self.flash('No audio files are missing.'); return
        from phosphor.editor.missing_audio_dialog import MissingAudioDialog
        dialog = MissingAudioDialog(self)
        try: self.run_dialog(dialog)
        finally: dialog.deleteLater()

    def locate_audio_clip(self, index):
        if 0 <= index < len(self.project.audio_clips) and self.project.audio_clips[index].missing:
            self.locate_audio_file(audio_media.file_key(self.project.audio_clips[index]))

    def locate_audio_file(self, key, path=None):
        """Locate one missing file by hand (a file picker unless `path` is given). A file with other
        contents asks Use it anyway?; other missing files in the same folder are offered next."""
        files = audio_media.missing_files(self.project)
        if key not in files: return False
        clip = self.project.audio_clips[files[key][0]]
        if path is None:
            folder = Path(clip.path).parent
            path = self.pick_audio_file(clip.name, str(folder) if folder.is_dir() else '')
        if not path: return False
        path = Path(path)
        if not matches(clip, path) and not self.ask(
                'Different file', f'{path.name} is not the same file as {clip.name}: its size or contents differ. Use it anyway?'):
            return False
        located = {key: path}
        others = audio_media.find_in_folder(self.project, path.parent, set(files) - {key})
        if others and self.ask('Missing audio files', f'{len(others)} other missing audio file{"s are" if len(others) != 1 else " is"} '
                               f'in {path.parent.name or path.parent}. Relink {"them" if len(others) != 1 else "it"} too?'):
            located.update(others)
        return self.relink_audio(located)

    def search_missing_audio(self, folder=None):
        """Search a folder and its subfolders for every missing file; returns how many were found."""
        if not audio_media.missing_count(self.project): return 0
        if folder is None: folder = self.pick_folder('Search a folder for the missing audio files')
        if not folder: return 0
        project = self.project
        try:
            found = run_with_progress(self, f'Searching {Path(folder).name or folder}…',
                                      lambda: audio_media.search_folder(project, folder), title='Missing audio files')
        except OSError as error: self.error(error); return 0
        if not found:
            self.flash(f'No missing audio files were found in {Path(folder).name or folder}.'); return 0
        return len(found) if self.relink_audio(found) else 0

    def relink_audio(self, located):
        """Point the files in `located` ({file key: path}) at their new paths: one Undo step. The
        files are decoded on a worker thread first, so the GUI thread never decodes them."""
        try: project = audio_media.relink(self.project, located)
        except ValueError as error: self.error(error); return False
        rate = self.audio.sample_rate
        fresh = {(new.path, new.fingerprint) for old, new in zip(self.project.audio_clips, project.audio_clips)
                 if new != old and not new.missing and new.gain > 0}
        if fresh:
            run_with_progress(self, 'Reading audio…', lambda: [decoded(path, fingerprint, rate) for path, fingerprint in fresh],
                              title='Missing audio files')
        self.commit_history(); self.set_project(project); self.commit_history()
        count = len(located)
        self.flash(f'Relinked {count} audio file{"s" if count != 1 else ""}.')
        return True

    def collect_media(self):
        """Copy the audio files into `<project> media` beside the project, relink the clips and save."""
        if self.workspace != COMPOSER: return False
        if not self.project.audio_clips: self.flash('This project has no audio clips to collect.'); return False
        if self.project_path is None:
            if not self.ask('Collect media', 'Collect media copies the audio files next to the project file. Save the project first?'):
                return False
            if not self.save_project(): return False
        self.commit_history()
        before = self.project
        plan = audio_media.collect_plan(before, self.project_path)
        def work():
            audio_media.copy_media(plan)
            return audio_media.collected(before, plan)
        try:
            project = run_with_progress(self, f'Copying {len(plan.copies)} audio file{"s" if len(plan.copies) != 1 else ""} '
                                        f'into "{plan.folder.name}"…', work, title='Collect media')
        except (OSError, ValueError) as error: self.error(error); return False
        for old, new in zip(before.audio_clips, project.audio_clips):
            if old.path != new.path: alias_decoded(old.path, new.path, old.fingerprint)  # A copy: reuse the decode.
        if project != before: self.set_project(project); self.commit_history()
        if not self.save_project(): return False
        moved = len(plan.copies)
        text = f'Collected media into "{plan.folder.name}": {moved} file{"s" if moved != 1 else ""} copied.'
        if plan.skipped: text += f' {plan.skipped} missing file{"s were" if plan.skipped != 1 else " was"} skipped.'
        self.flash(text)
        return True

    def restart_motion(self):
        self.motion_epoch=time.perf_counter()
        self.motion_audio_offset=-self.audio.frames/self.audio.sample_rate if self.audio.running else 0.

    def show_motion(self):
        if self.building and not self.leave_builder():return
        if self.motion_dialog is not None:self.motion_dialog.raise_();return
        from phosphor.editor.motion_dialog import MotionDialog
        self.motion_dialog=MotionDialog(self);self.motion_dialog.finished.connect(lambda result:setattr(self,'motion_dialog',None));self.motion_dialog.show()

    def apply_signal_preferences(self,value,source=None):
        value=validated_signal_preferences(value)
        if value['shape_redraw'] and value['shape_redraw_count']>1:
            settings=self.scene.settings
            planned_path(0,settings.drawing_hz,scene_shapes(settings),settings.travel_mode,settings.travel_speed,0.,value['shape_redraw_count'])
        self.signal_preferences=value
        self.audio.requested_rate=value['output_rate']  # Applies from the next Play.
        self.audio.output_device=value['output_device']  # Moves open playback to it (S2).
        if getattr(self,'builder',None) is not None:self.builder.route_mode=value['route_mode'];self.builder.canvas.update()
        if source is not getattr(self,'signal_editor',source):self.signal_editor.load(value)
        self.audio.parameters=(self.output_settings(),self.scene.mode)
        if getattr(self, 'desk3d_host', None) is not None:
            self.desk3d_host.sync_quality()

    def signal_edited(self,values):
        try:self.apply_signal_preferences(values,self.signal_editor);self.signal_save.start()
        except ValueError as error:self.error(error);self.signal_editor.load(self.signal_preferences)

    def save_signal_preferences(self):
        try:atomic_json(SIGNAL_PREFERENCES_PATH,self.signal_preferences)
        except OSError as error:self.error(error)

    def signal_defaults(self):
        self.signal_edited(dict(DEFAULT_SIGNAL_PREFERENCES))

    def signal_revert(self):
        self.signal_edited(dict(getattr(self,'signal_snapshot',self.signal_preferences)))

    def output_device_names(self):
        """The output devices for Signal output's list (S2). PortAudio is re-read only while no
        stream is open; during playback the list is the one it read at Play."""
        try: sd=self.audio._sounddevice()
        except ImportError: return []
        if not self.audio.running: output_devices.refresh(sd)
        return output_devices.names(sd)

    def show_signal_preferences(self):
        """Legacy dialog: previews edits, saves on OK/Apply and restores the saved values on Cancel."""
        if self.signal_dialog:self.signal_dialog.raise_();return
        dialog=QDialog(self);self.signal_dialog=dialog;dialog.setWindowTitle('Preferences — Signal output')
        layout=QVBoxLayout(dialog);baseline=dict(self.signal_preferences)
        editor=SignalOutputEditor(SliderInput,baseline,self.output_device_names);layout.addWidget(editor);dialog.resize(560,560)
        current=editor.values
        def preview(values):
            try:self.apply_signal_preferences(values)
            except ValueError as error:self.error(error)
        editor.changed.connect(preview)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.RestoreDefaults);layout.addWidget(buttons)
        def apply():
            nonlocal baseline
            try:
                value=validated_signal_preferences(current())
                self.apply_signal_preferences(value);atomic_json(SIGNAL_PREFERENCES_PATH,value);baseline=value;return True
            except (ValueError,OSError) as error:self.error(error);return False
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(apply)
        def defaults():
            editor.load(DEFAULT_SIGNAL_PREFERENCES);preview(editor.values())
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(defaults)
        buttons.accepted.connect(lambda:dialog.accept() if apply() else None);buttons.rejected.connect(dialog.reject)
        dialog.finished.connect(lambda result:(self.apply_signal_preferences(baseline),setattr(self,'signal_dialog',None)))
        dialog.setModal(True);dialog.show()

    def buttons(self, layout, entries):
        row = FlowRow()  # Wraps in a narrow panel (UI2).
        for label, action in entries:
            button = QPushButton(label); button.clicked.connect(lambda checked=False,action=action:action()); row.addWidget(button)
        layout.addLayout(row)

    def combo(self, layout, label, key, choices, inline=False):
        widget = QComboBox(); widget.addItems(list(choices)); widget.setObjectName(key)
        if inline: self.inline_row(layout,label,widget)
        else: layout.addWidget(QLabel(label)); layout.addWidget(widget)
        widget.activated.connect(lambda index:self.edit(key,widget.currentText()))
        self.parameters[key] = widget

    def number(self, layout, label, key, low, high, default, inline=False):
        target = QHBoxLayout() if inline else layout
        if inline: layout.addLayout(target)
        caption = InlineCaption(label) if inline else QLabel(label); target.addWidget(caption)
        widget = QDoubleSpinBox(); widget.setRange(low,high); widget.setDecimals(3); widget.setObjectName(key)
        if inline: widget.setMinimumWidth(SPIN_MIN)  # Wide ranges size spin boxes for their longest value; a minimum, it widens with the panel.
        widget.setKeyboardTracking(False); widget.setValue(default); target.addWidget(widget,1 if inline else 0)
        widget.valueChanged.connect(lambda value:self.edit(key,value)); self.parameters[key] = widget

    def inline_slider(self, layout, label, key, default, low=0, high=1, **kwargs):
        row = QHBoxLayout(); layout.addLayout(row); caption = InlineCaption(label); row.addWidget(caption)
        widget = SliderInput(default,low,high,**kwargs); widget.setFixedHeight(28); row.addWidget(widget,1)
        widget.edited.connect(lambda value:self.edit(key,value)); self.parameters[key] = widget

    def slider(self, layout, label, key, default, low=0, high=1, **kwargs):
        caption = QLabel(label); layout.addWidget(caption); widget = SliderInput(default,low,high,**kwargs); layout.addWidget(widget)
        self.slider_captions[key] = (caption,label)
        widget.edited.connect(lambda value:self.edit(key,value)); self.parameters[key] = widget

    def device_selected(self, index):
        """Choosing a MIDI device connects to it; None disconnects."""
        name = self.devices.itemData(index)
        self.scene.device = name or ''; self.update_title()
        if name: self.connect_midi()
        else: self.disconnect_midi()

    def monitor_changed(self, index):
        if index != MONITOR_GENERATED: self.browser.stop_preview()
        if index == MONITOR_SYSTEM and not SYSTEM_AUDIO_AVAILABLE:
            self.monitor.setCurrentIndex(MONITOR_GENERATED)
            self.status.setText(SYSTEM_AUDIO_NOTICE)
            return
        try:
            self.audio.stop(); self.capture.stop()
            self.system_monitor = index == MONITOR_SYSTEM
            if self.video is not None and self.video_monitor and index != MONITOR_VIDEO: self.video.player.pause()
            self.video_monitor = index == MONITOR_VIDEO
            generated = not (self.system_monitor or self.video_monitor)
            self.generated_panel.setEnabled(generated)
            self.generated_panel.setVisible(generated)
            self.capture_panel.setVisible(self.system_monitor)
            self.video_panel.setVisible(self.video_monitor)
            # The time view draws beam samples; the video raster has none.
            self.sweep_display.setEnabled(not self.video_monitor); self.sweep_panel.setEnabled(not self.video_monitor)
            self.sweep.reset(); self.sweep_frame = self.sweep_feed = None
            self.update_viewer_heading()
            for name in (PANEL_SCENE,PANEL_MIX): self.bar_buttons[name].setEnabled(generated)
            if not generated:
                self.drag = None; self.handles = []
                if self.current_panel in (PANEL_SCENE,PANEL_MIX): self.show_panel(PANEL_INPUT)
            if self.system_monitor:
                if self.capture_devices.count() == 1: self.refresh_capture_devices()
            blocker = QSignalBlocker(self.monitor); self.monitor.setCurrentIndex(index)
            self.last_status = 0
        except Exception as error:
            blocker = QSignalBlocker(self.monitor); self.monitor.setCurrentIndex(MONITOR_SYSTEM if self.system_monitor else MONITOR_VIDEO if self.video_monitor else MONITOR_GENERATED)
            self.error(error)

    def video_player(self):
        """The media player, wired to the phosphor layer's VideoOutput (created on first use)."""
        if self.host.phosphor is None: raise RuntimeError('Video needs the phosphor display, which is unavailable on this system.')
        output = self.host.phosphor.video_output()
        if self.video is None:
            self.video = VideoPlayer(); self.video_panel.attach_player(self.video)
        self.video.attach(output)
        return self.video

    def open_video(self, path=None):
        try:
            path = path or self.video_panel.choose_file(self)
            if not path: return
            player = self.video_player()
            player.open(path)
            self.video_panel.name.setText(Path(path).name)
            player.player.play()
            self.last_status = 0
        except Exception as error: self.error(error)

    def path_changed(self, key, param):
        """Start/Trace edits; a bad expression is shown under the fields and keeps the last good value."""
        if self.scene.shape is None: return
        field = START_FIELD if key == START_FIELD.name else TRACE_FIELD
        try:
            names = tuple(v.name for v in self.scene.shape.transform_variables)  # Object variables (T5).
            if param.expression: path_expression(param.expression,f'Path {field.label}',names)
            self.scene.change(key,param)
        except ValueError as error:
            self.path_error.setText(str(error)); self.path_error.show(); return
        self.path_error.hide(); self.audio.parameters = (self.output_settings(),self.scene.mode); self.sync()

    def transforms_changed(self, stack):
        """The transform editor only emits stacks that validate."""
        if self.scene.shape is None: return
        if tuple(t.kind for t in stack) != tuple(t.kind for t in self.scene.shape.transforms):
            self.transform_solos.pop(object_id(self.scene.shape, self.scene.selected), None)
        try: self.scene.change('transforms',stack)
        except ValueError as error: self.error(error)
        self.sync()

    def transform_variables_changed(self, fields):
        """Object variables (T5): the panel emits only edits that validate against the stack and
        path; a rename also brings the rewritten transforms, path_start and path_trace."""
        if self.scene.shape is None: return
        try: self.scene.change_object(fields)
        except ValueError as error: self.transform_panel.variable_error.setText(str(error))
        self.audio.parameters = (self.output_settings(),self.scene.mode); self.sync()

    def transform_preset_inserted(self, fields, label):
        """Saved transforms (T6): the panel's validated stack and variables with a preset
        inserted, as one Undo step named label (Sandbox history or Composer's)."""
        if self.scene.shape is None: return
        uid = object_id(self.scene.shape, self.scene.selected)
        try:
            with self.undo_step(label):
                self.scene.change_object(fields)
                self.transform_solos.pop(uid, None)  # Steps moved: a solo index no longer means the same step.
                self.audio.parameters = (self.output_settings(),self.scene.mode); self.sync()
        except ValueError as error:
            self.sync(); self.transform_panel.error.setText(str(error))

    def animation_changed(self, playback):
        if self.scene.shape is None: return
        try: self.scene.change('animation', playback)
        except ValueError as error: self.error(error)
        self.sync()

    def plot_changed(self, plot):
        """The equation editor only emits plots that compile."""
        if self.scene.shape is None or self.scene.shape.solid != PARAMETRIC_OBJECT: return
        try: self.scene.change('plot',plot)
        except ValueError as error: self.error(error)
        self.sync()

    def recurrence_changed(self, plot):
        if self.scene.shape is None or self.scene.shape.solid != RECURRENCE_OBJECT: return
        try: self.scene.change('recurrence', plot)
        except ValueError as error: self.error(error)
        self.sync()

    def matrix_changed(self, **updates):
        if self.scene.shape is None or self.scene.shape.solid != MATRIX_OBJECT: return
        cfg = replace(self.scene.shape.matrix or MatrixConfig(), **updates)
        try: self.scene.change('matrix', cfg)
        except ValueError as error: self.error(error)
        self.audio.parameters = (self.output_settings(), self.scene.mode); self.sync()

    def shape_params_changed(self, **updates):
        if self.scene.shape is None or self.scene.shape.solid not in PARAMETRIC_SOLIDS: return
        base = self.scene.shape.shape_params or default_shape_params(self.scene.shape.solid)
        updated = replace(base, **updates)
        try: self.scene.change('shape_params', updated)
        except ValueError as error: self.error(error)
        self.audio.parameters = (self.output_settings(), self.scene.mode); self.sync()

    def update_scene_context(self):
        """Keep the SCENE context banner synced with the active workspace, clip name/bars, and object."""
        if not hasattr(self, 'scene_context_label'): return
        shapes = self.scene.settings.shapes
        if 0 <= self.scene.selected < len(shapes):
            obj_summary = f'{self.scene.selected + 1:02}  ' + object_label(shapes[self.scene.selected])
        else:
            obj_summary = 'Empty scene'
        if self.workspace == COMPOSER and self.project is not None and 0 <= self.clip_index < len(self.project.clips):
            clip = self.project.clips[self.clip_index]
            b0, b1 = Meter(self.project.meter_changes).bar_range(clip.start, clip.end)
            cname = getattr(clip, 'name', '') or f'Clip {self.clip_index + 1}'
            self.scene_context_label.setText(f'{cname} (Bars {b0}–{b1}) · {obj_summary}')
        else:
            self.scene_context_label.setText(f'SANDBOX · {obj_summary}')

    def add_3d_turntable_to_selected(self):
        if self.scene.shape is None: return False
        return self.transform_panel.insert_recipe('3D Turntable', TRANSFORM_APPEND)

    def apply_quick_add_entry(self, entry):
        """Execute a chosen QuickAddEntry from the Quick Add palette."""
        if entry is None: return False
        self.show_panel(PANEL_SCENE)
        if entry.action == 'object':
            self.add_object(entry.payload)
            return True
        if entry.action == 'svg':
            self.import_svg()
            return True
        if entry.action == 'parametric':
            kind, name = entry.payload
            return self.browser_add_equation('parametric', kind, name)
        if entry.action == 'recurrence':
            kind, name = entry.payload
            return self.browser_add_equation('recurrence', kind, name)
        if self.scene.shape is None:
            return False
        self.scene_sections.setCurrentIndex(1)
        if entry.action == 'transform':
            self.transform_panel.add(entry.payload)
            return True
        if entry.action == 'recipe':
            return self.transform_panel.insert_recipe(entry.payload, TRANSFORM_APPEND)
        if entry.action == 'saved_transform':
            return self.transform_panel.insert_saved(entry.payload, TRANSFORM_APPEND)
        return False

    def open_quick_add(self, initial_filter=FILTER_ALL):
        """Open the searchable Cmd/Ctrl+K Quick-Add palette."""
        if self.building or self.desk3d: return False
        self.function_panel.reload_presets()
        self.recurrence_panel.reload_presets()
        self.transform_panel.reload_saved()
        entries = collect_entries(self.function_panel, self.recurrence_panel, self.transform_panel)
        dialog = QuickAddDialog(entries, has_object=(self.scene.shape is not None),
                                initial_filter=initial_filter, parent=self)
        if self.run_dialog(dialog) == QDialog.DialogCode.Accepted and dialog.chosen is not None:
            return self.apply_quick_add_entry(dialog.chosen)
        return False

    def choose_midi_file(self):
        current = self.scene.settings.midi_file
        folder = str(Path(current).parent) if current else str(SCORE_FOLDER)
        value,_ = QFileDialog.getOpenFileName(self,'Play the melody of a MIDI file',folder,'MIDI file (*.mid *.midi)')
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

    def refresh_capture_devices(self):
        if self.capture_scanning or self.capture.running: return
        self.capture_scanning = True; self.capture_notice = 'Finding system-audio sources…'
        def work():
            try: self.jobs.put(('capture_devices',SystemAudio.devices()))
            except Exception as error: self.jobs.put(('capture_error',str(error)))
        threading.Thread(target=work,daemon=True).start()

    def edit(self, key, value):
        try:
            if key=='source' and value==MIDI_FILE_SOURCE and self.scene.settings.source!=MIDI_FILE_SOURCE and not self.scene.settings.midi_file:
                self.sync();self.choose_midi_file();return
            if key == 'source' and value != self.scene.settings.source:
                self.audio.stop()
                if value == MIDI_SOURCE: self.refresh_ports()
            if key == 'mode': self.scene.mode = {'Processed output':'mixed','Fully shaped audio':'drawing','Original audio':'music'}[value]
            elif key in ('scale_type','scale_root'):
                settings = self.scene.settings
                kind = value if key == 'scale_type' else settings.scale_type
                root = value if key == 'scale_root' else settings.scale_root
                # The end note follows: same span when it is in the new scale, else the next scale note up.
                candidate = replace(settings,**{key:value,'scale_end':scale_end_for(kind,root,settings.scale_end-settings.scale_root)})
                validate(candidate); self.scene.settings = candidate
            else:
                if key == 'midi_channel': value = -1 if value == 'All' else int(value)-1
                self.scene.change(key,value)
            self.audio.parameters = (self.output_settings(),self.scene.mode)
            self.sync()
        except Exception as error:
            self.error(error); self.sync()

    def sync(self):
        self.store_clip_scene(); self.watch_scene_history()
        settings, shape = self.scene.settings, self.scene.shape
        values = {**vars(settings),**(vars(shape) if shape is not None else {})}
        values['mode'] = {'mixed':'Processed output','drawing':'Fully shaped audio','music':'Original audio'}[self.scene.mode]
        values['midi_channel'] = 'All' if settings.midi_channel < 0 else str(settings.midi_channel+1)
        for key, widget in self.parameters.items():
            if key not in values: continue  # Object fields in an empty scene.
            blocker = QSignalBlocker(widget)
            if isinstance(widget,QComboBox): widget.setCurrentText(str(values[key]))
            elif isinstance(widget,QLineEdit): widget.setText(str(values[key]))
            else: widget.setValue(values[key])
        source = settings.source
        self.scale_panel.setVisible(source == SCALE_NAME); self.midi_file_row.setVisible(source == MIDI_FILE_SOURCE)
        self.tone_panel.setVisible(source == TONE_SOURCE and self.workspace == SANDBOX); self.tone_note.setVisible(source == TONE_SOURCE); self.live_panel.setVisible(source == MIDI_SOURCE)
        self.tempo_panel.setVisible(source in (SCALE_NAME,MIDI_FILE_SOURCE))
        self.midi_file_label.setText(Path(settings.midi_file).name); self.midi_file_label.setToolTip(settings.midi_file)
        blocker = QSignalBlocker(self.scale_type_box); self.scale_type_box.setCurrentText(settings.scale_type)
        blocker = QSignalBlocker(self.scale_root_box); self.scale_root_box.setCurrentIndex(self.scale_root_box.findData(settings.scale_root))
        ends = [note for note in scale_ladder(settings.scale_type,settings.scale_root,NOTE_RANGE[1]) if note > settings.scale_root]
        blocker = QSignalBlocker(self.scale_end_box)
        if [self.scale_end_box.itemData(i) for i in range(self.scale_end_box.count())] != ends:
            self.scale_end_box.clear()
            for note in ends: self.scale_end_box.addItem(note_name(note),note)
        self.scale_end_box.setCurrentIndex(self.scale_end_box.findData(settings.scale_end))
        blocker = QSignalBlocker(self.devices); self.devices.setCurrentIndex(max(0,self.devices.findData(self.scene.device or None)))
        labels = [f'{i+1:02}  '+object_label(item) for i,item in enumerate(settings.shapes)]
        for widget in (self.shape_list,self.transform_target):
            blocker = QSignalBlocker(widget)
            items = [widget.item(i).text() for i in range(widget.count())] if widget is self.shape_list else [widget.itemText(i) for i in range(widget.count())]
            if items != labels: widget.clear(); widget.addItems(labels)
        rows = min(max(len(settings.shapes), 3), 7)
        self.shape_list.setFixedHeight(rows * 26 + 6)
        blocker = QSignalBlocker(self.shape_list)
        if self.shape_list.currentRow() != self.scene.selected: self.shape_list.setCurrentRow(self.scene.selected)
        effective = self.audition_shapes()
        for i, item_shape in enumerate(settings.shapes):
            item = self.shape_list.item(i)
            item.setFlags((item.flags() | Qt.ItemFlag.ItemIsDragEnabled) & ~Qt.ItemFlag.ItemIsDropEnabled)
            row = self.shape_list.itemWidget(item)
            if row is None:
                item.setForeground(QColor('transparent'))  # The row's own caption draws the text.
                row = QWidget(); layout = QHBoxLayout(row); layout.setContentsMargins(2, 0, 2, 0)
                label = QLabel(labels[i]); label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                layout.addWidget(label, 1); row.caption = label; row.buttons = {}
                for kind, text in (('mute', 'M'), ('solo', 'S')):
                    button = QPushButton(text); button.setCheckable(True); button.setFixedSize(26, 24)
                    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                    button.setToolTip('Mute (Alt+click: unmute all)' if kind == 'mute' else 'Solo (Ctrl+click: exclusive)')
                    button.clicked.connect(lambda checked=False, kind=kind, i=i: self.toggle_objects(kind, i))
                    layout.addWidget(button); row.buttons[kind] = button
                row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                row.customContextMenuRequested.connect(lambda point, i=i, row=row: self.object_menu(i, row.mapToGlobal(point)))
                item.setSizeHint(QSize(0, 26)); self.shape_list.setItemWidget(item, row)
            switched = self.workspace == COMPOSER and bool(lane_points(item_shape.lanes, 'on'))
            row.caption.setText(labels[i] + (' ◆' if switched else ''))
            row.caption.setToolTip(labels[i] + ('\n◆ On/Off is automated in the timeline.' if switched else ''))
            row.caption.setEnabled(not effective[i].muted)
            row.buttons['mute'].setChecked(item_shape.muted)
            row.buttons['solo'].setChecked(object_id(item_shape, i) in self.solo_ids)
        blocker = QSignalBlocker(self.transform_target); self.transform_target.setCurrentIndex(self.scene.selected)
        kind = shape.solid if shape is not None else None
        self.text_panel.setVisible(kind == TEXT_OBJECT)
        self.matrix_panel.setVisible(kind == MATRIX_OBJECT)
        if kind == MATRIX_OBJECT:
            cfg = shape.matrix or MatrixConfig()
            with QSignalBlocker(self.matrix_lang_a): self.matrix_lang_a.setCurrentText(cfg.language_a)
            with QSignalBlocker(self.matrix_lang_b): self.matrix_lang_b.setCurrentText(cfg.language_b)
            if self.matrix_sample_a.text() != cfg.sample_a:
                with QSignalBlocker(self.matrix_sample_a): self.matrix_sample_a.setText(cfg.sample_a)
            if self.matrix_sample_b.text() != cfg.sample_b:
                with QSignalBlocker(self.matrix_sample_b): self.matrix_sample_b.setText(cfg.sample_b)
            with QSignalBlocker(self.matrix_cols): self.matrix_cols.setValue(cfg.columns)
            with QSignalBlocker(self.matrix_rows): self.matrix_rows.setValue(cfg.rows)
            for key, widget in self.matrix_fields.items(): widget.set_param(getattr(cfg, key))
        show_sp = kind in PARAMETRIC_SOLIDS
        self.shape_params_panel.setVisible(show_sp)
        if show_sp:
            sp = shape.shape_params or default_shape_params(kind)
            self.sp_star_box.setVisible(kind in STAR_SOLIDS)
            self.sp_gear_box.setVisible(kind in GEAR_SOLIDS)
            self.sp_radar_box.setVisible(kind == RADAR_SOLID)
            self.sp_clock_box.setVisible(kind == CLOCK_SOLID)
            if kind in STAR_SOLIDS:
                with QSignalBlocker(self.sp_star_points): self.sp_star_points.setValue(sp.points)
                self.sp_star_inner.setValue(sp.inner_radius)
            elif kind in GEAR_SOLIDS:
                with QSignalBlocker(self.sp_gear_teeth): self.sp_gear_teeth.setValue(sp.teeth)
                self.sp_gear_depth.setValue(sp.tooth_depth)
            elif kind == RADAR_SOLID:
                self.sp_radar_speed.setValue(sp.sweep_speed)
            elif kind == CLOCK_SOLID:
                self.sp_clock_speed.setValue(sp.clock_speed)
                with QSignalBlocker(self.sp_clock_hour): self.sp_clock_hour.setValue(int(round(sp.start_hour)))
                with QSignalBlocker(self.sp_clock_min): self.sp_clock_min.setValue(int(round(sp.start_minute)) % 60)
        self.function_panel.setVisible(kind == PARAMETRIC_OBJECT)
        reserved = {v.name for v in shape.transform_variables} if shape is not None else set()  # Object variables (T5).
        self.function_panel.reserved = self.recurrence_panel.reserved = reserved
        has_3d_rot = shape is not None and (
            any(t.enabled and t.kind in ('Rotate', 'Spin') for t in shape.transforms)
            or bool(shape.rotation_x or shape.rotation_y or shape.spin_x or shape.spin_y)
        )
        if kind == PARAMETRIC_OBJECT: self.function_panel.set_plot(shape.plot)
        self.function_panel.set_has_3d_rotation(has_3d_rot)
        self.recurrence_panel.setVisible(kind == RECURRENCE_OBJECT)
        if kind == RECURRENCE_OBJECT: self.recurrence_panel.set_plot(shape.recurrence)
        self.recurrence_panel.set_has_3d_rotation(has_3d_rot)
        self.edit_shape_button.setVisible(kind == CUSTOM_OBJECT)
        self.empty_note.setVisible(shape is None); self.transform_hint.setVisible(shape is not None)
        self.placement_panel.setVisible(shape is not None)
        self.transform_panel.set_target_solid(kind or '')
        self.transform_panel.set_stack(shape.transforms if shape is not None else ())
        self.update_scene_context()
        self.transform_panel.set_audition(shape.stack_bypassed if shape else False,
            self.transform_solos.get(object_id(shape, self.scene.selected)) if shape else None)
        self.transform_panel.set_animation(shape)
        self.transform_panel.set_variables(shape.transform_variables if shape is not None else (),
                                           equation_variables(shape) if shape is not None else {},
                                           (shape.path_start, shape.path_trace) if shape is not None else None,
                                           shape.matrix if shape is not None and shape.solid == MATRIX_OBJECT else None)
        self.path_panel.setVisible(shape is not None)
        if shape is not None:
            for key,widget in self.path_fields.items(): widget.set_param(getattr(shape,key))
        self.sync_automation(shape)
        self.duplicate_button.setEnabled(shape is not None); self.remove_button.setEnabled(shape is not None)
        self.scene_sections.setTabEnabled(1,shape is not None)
        if shape is None and self.scene_sections.currentIndex() == 1: self.scene_sections.setCurrentIndex(0)
        self.audio.parameters = (self.output_settings(),self.scene.mode)
        if self.audio.composer is not None: self.audio.composer = (self.output_project(), self.play_start)
        self.sync_solo_pill()
        if getattr(self, 'desk3d_host', None) is not None:
            self.desk3d_host.controller.refresh()
        self.update_title()

    # Automation (Composer): gain lanes edited in the timeline's transform roll ----------
    def automation_menu(self, widget, key):
        """Right-click › Automate / Remove automation on a spin box or slider; key() gives its lane key."""
        def show(target, point):
            edit = target.lineEdit() if isinstance(target,QDoubleSpinBox) else target if isinstance(target,QLineEdit) else None
            menu = edit.createStandardContextMenu() if edit is not None else QMenu(self)
            lane = key(); automated = self.automated(lane)
            automate, remove = automation_actions(menu,automated,self.workspace == COMPOSER and lane is not None)
            chosen = menu.exec(target.mapToGlobal(point)); menu.deleteLater()
            if chosen is automate: self.automate(lane,True)
            elif chosen is remove: self.automate(lane,False)
        parts = [widget] + ([widget.lineEdit()] if isinstance(widget,QDoubleSpinBox) else [widget.slider,widget.entry] if isinstance(widget,SliderInput) else [])
        for part in parts:
            part.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            part.customContextMenuRequested.connect(lambda point,part=part:show(part,point))

    def automated(self, key):
        if key is None or self.workspace != COMPOSER: return False
        try: return bool(get_points(self.project.clips[self.clip_index],key))
        except (KeyError,ValueError,IndexError): return False

    def automate(self, key, on):
        """Add a flat gain lane (1 across the clip) for key, or remove it; shows it in the transform roll."""
        if self.workspace != COMPOSER or key is None: return
        self.store_clip_scene()
        clip = self.project.clips[self.clip_index]
        try: clip = set_points(clip,key,default_points(clip,key) if on else ())
        except (KeyError,ValueError) as error: self.error(error); return
        self.set_project(with_clip(self.project,self.clip_index,clip)); self.sync()  # Mix lanes live on the clip, not its scene.
        view = self.composer_panel.timeline
        if on and hasattr(view,'show_lane'): view.show_lane(key)
        elif hasattr(view,'update'): view.update()

    def sync_automation(self, shape):
        composer = self.workspace == COMPOSER
        self.transform_panel.set_automation(composer); self.function_panel.set_automation(composer)
        self.recurrence_panel.set_automation(composer)
        for widget in self.path_fields.values(): widget.automation_allowed = composer
        for widget in self.matrix_fields.values(): widget.automation_allowed = composer
        clip = self.project.clips[self.clip_index] if composer else None
        for key in ('position_x','position_y','shape_size'):
            on = composer and shape is not None and bool(lane_points(shape.lanes,key))
            widget = self.parameters[key]; widget.setPrefix(AUTOMATED if on else '')
            note = '\n◆ Automated: a gain lane in the timeline multiplies this number.'
            widget.setToolTip(widget.toolTip().split(note)[0]+(note if on else ''))
        for key,(caption,label) in self.slider_captions.items():
            if key in ('drawing_gain','music_gain'): caption.setText((AUTOMATED if clip is not None and lane_points(clip.lanes,key) else '')+label)

    def update_title(self):
        if getattr(self,'workspace',None) == COMPOSER:
            name = self.project_path.name if self.project_path else 'Untitled project'
            self.setWindowTitle(f'{"* " if self.project_dirty else ""}{name} — phosphor Composer'); return
        scene = self.sandbox_scene
        self.setWindowTitle(f'{"* " if scene.dirty else ""}{scene.path.name if scene.path else "Untitled scene"} — phosphor')

    def select_shape(self, index):
        if 0 <= index < len(self.scene.settings.shapes):
            self.scene.selected = index; self.reveal_handles(); self.sync()

    def reveal_handles(self):
        """Show the viewport handles now; they fade out HANDLE_HOLD seconds later (UI1)."""
        self.handles_seen = time.perf_counter()

    def handle_opacity(self, now=None):
        """0 (hidden) to 1 (shown): how visible the viewport handles are at `now`."""
        if self.drag: return 1.
        age = (time.perf_counter() if now is None else now)-self.handles_seen
        return float(np.clip(1-(age-HANDLE_HOLD)/HANDLE_FADE, 0., 1.))

    def add_object(self, kind):
        """Add menu: built-in kinds are added at once; custom shapes open the builder."""
        if kind == CUSTOM_OBJECT: self.open_builder(new=True); return
        if kind == MODEL_OBJECT: self.import_model(); return
        try: self.scene.add(kind)
        except ValueError as error: self.error(error)
        self.sync()

    def shape_action(self, action):
        self.clear_solos()
        if action == 'remove': self.scene.remove()
        elif action == 'duplicate': self.scene.duplicate()
        else: self.add_object(TEXT_OBJECT if action == 'text' else 'Cube'); return
        self.sync()

    def reset_view(self):
        if self.scene.shape is None: return
        for axis in 'xyz': self.scene.change('rotation_'+axis,0); self.scene.change('spin_'+axis,0)
        self.scene.change('transforms',())
        self.sync()

    def reset_placement(self):
        if self.scene.shape is None: return
        for key,value in (('position_x',0),('position_y',0),('shape_size',1)): self.scene.change(key,value)
        self.sync()

    def error(self, error): QMessageBox.critical(self,'phosphor',str(error))

    def open_builder(self, new=False):
        if self.system_monitor or self.video_monitor:
            self.error('Switch to Generated scene / MIDI before building a shape.');return
        self.browser.stop_preview()
        self.exit_view_modes()
        if self.isFullScreen():self.showNormal()
        if self.builder is None:
            from phosphor.editor.shape_builder import ShapeBuilder
            self.builder=ShapeBuilder(ViewHost,self.centralWidget());self.body.addWidget(self.builder);self.builder.route_mode=self.signal_preferences['route_mode']
            self.builder.applied.connect(self.apply_custom_drawing)
            self.builder.back.connect(self.leave_builder)
            self.builder.play_button.clicked.connect(self.play)
        shape=self.scene.shape
        from phosphor.editor.wheel_mapping import validated_mapping
        self.builder.wheel_mapping = validated_mapping(self.preferences)
        self.builder.wheel_sensitivity, self.builder.trackpad_sensitivity = self.preferences['wheel_sensitivity'], self.preferences['trackpad_sensitivity']
        self.builder_index=self.scene.selected if not new and shape is not None and shape.solid==CUSTOM_OBJECT else None
        drawing=shape.custom if self.builder_index is not None else Drawing()
        self.builder.begin(drawing);self.builder.set_colors(self.theme.builder_colors())
        self.vsplit.hide();self.app_bar.hide();self.builder.show();self.building=True  # The whole splitter: an empty one still takes space.
        self.update_history_actions()
        self.fullscreen_action.setEnabled(False);self.immersive_action.setEnabled(False)
        self.builder.canvas.forceActiveFocus()

    def import_svg(self):
        value,_ = QFileDialog.getOpenFileName(self,'Import SVG','','SVG drawing (*.svg)')
        if value: self.add_svg(value)

    def choose_svg(self):
        """Replace › SVG drawing…: a drawing read from a picked file, or None."""
        value,_ = QFileDialog.getOpenFileName(self,'Import SVG','','SVG drawing (*.svg)')
        if not value: return None
        try: drawing,report=load_svg(value)
        except (ValueError,OSError) as error:self.error(error);return None
        self.svg_notes(report)
        return drawing

    def add_svg(self,path):
        try:
            drawing,report=load_svg(path)
            self.scene.add_drawing(drawing)
            self.audio.parameters=(self.output_settings(),self.scene.mode);self.sync()
        except (ValueError,OSError) as error:self.error(error);return
        self.svg_notes(report)

    def svg_notes(self, report):
        notes=[]
        if report.simplified:notes.append(f'Simplified to {report.nodes} nodes and {report.edges} lines to fit a custom shape.')
        if report.dropped:notes.append(f'{report.dropped} of {report.subpaths} parts were too small to keep.')
        if report.skipped:notes.append('Not imported: '+', '.join(report.skipped)+'. Convert text to outlines first.')
        if notes:QMessageBox.information(self,'SVG simplified','\n\n'.join(notes))

    def import_model(self):
        chosen=self.choose_model()
        if chosen is not None:self.add_model(*chosen)

    def choose_model(self, value=None):
        """The file picker (unless value names the file) and the import dialog: (path, simplify_to,
        ready result or None), or None."""
        if value is None: value,_ = QFileDialog.getOpenFileName(self,'Import 3D model','',MODEL_FILTERS)
        if not value:return None
        def read():
            mesh=read_mesh(value)
            return mesh,source_triangles(mesh)
        try:mesh,n=run_with_progress(self,f'Reading {Path(value).name}…',read)
        except (ValueError,OSError,MemoryError) as error:self.error(error);return None
        dialog=self.model_import_dialog(mesh,n)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return None
        return value,dialog.target(),dialog.ready_result()

    def model_import_dialog(self,mesh,n):
        return ModelImportDialog(self,mesh,n)

    def add_model(self,path,simplify_to=None,result=None):
        try:
            if result is None:result=run_with_progress(self,f'Importing {Path(path).name}…',lambda:load_model(path,simplify_to))
            model,report=result
            self.scene.add_model(model)
            self.audio.parameters=(self.output_settings(),self.scene.mode);self.sync()
        except (ValueError,OSError) as error:self.error(error);return
        self.remember_file('model',path)
        self.model_notes(report)

    def model_notes(self, report):
        notes=[]
        if report.simplified:notes.append(f'Simplified from {report.source_triangles:,} to {report.result_triangles:,} triangles.')
        if report.simplified:notes.extend(tradeoff_notes(report))
        if report.smooth:notes.append('This model has few sharp edges, so every mesh edge was kept: it draws as a full wireframe.')
        if report.limited:notes.append(f'Only the {MAX_MODEL_EDGES} sharpest edges were kept (safety limit).')
        if report.simplified:
            QMessageBox.information(self,'Model imported',notes[0]+f'\n\nKept {report.kept_edges:,} of {report.source_edges:,} edges.'+('\n\n'+'\n\n'.join(notes[1:]) if len(notes)>1 else ''))
        elif notes:QMessageBox.information(self,'Model simplified',f'Kept {report.kept_edges} of {report.source_edges} edges.\n\n'+'\n\n'.join(notes))

    def apply_custom_drawing(self,drawing):
        compiled(drawing)
        if self.builder_replace is not None:  # Objects list › Replace › Custom shape…
            if drawing.motion_path:
                self.builder.apply_succeeded=False
                self.error('A motion path cannot replace an object (other objects would follow it). Turn Motion path off, or add it as a new object.');return
            rows=self.builder_replace
            if not self.apply_replace(rows,CUSTOM_OBJECT,custom=drawing):self.builder.apply_succeeded=False;return
            self.builder_replace=None;self.builder_index=rows[0];return  # Later Applies edit the first one.
        shapes=list(self.scene.settings.shapes)
        index=self.builder_index
        if index is None:
            index=len(shapes)
            shapes.append(Shape(solid=CUSTOM_OBJECT,custom=drawing,uid=uuid.uuid4().hex if drawing.motion_path else ''))
        else:shapes[index]=replace(shapes[index],solid=CUSTOM_OBJECT,custom=drawing)
        if drawing.motion_path:
            guide=shapes[index]
            shapes[index]=replace(guide,uid=guide.uid or uuid.uuid4().hex)
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
        self.builder.cancel_gesture();self.builder.hide();self.vsplit.show();self.app_bar.show();self.building=False
        self.builder_replace=None  # A Replace the Builder never applied leaves the objects unchanged.
        self.update_history_actions()
        self.fullscreen_action.setEnabled(True);self.immersive_action.setEnabled(True);return True

    def confirm_save(self, action, force=False):
        """Offer to save the Sandbox scene (whichever workspace is showing)."""
        if not force and not self.sandbox_scene.dirty: return True
        result = QMessageBox.question(self,'Save current scene?',f'Save the current scene before {action}?',
            QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        return self.save_scene() if result == QMessageBox.StandardButton.Save else result == QMessageBox.StandardButton.Discard

    def confirm_all(self, action, force=False):
        """Both documents: the one showing first (force asks even when it is saved)."""
        composer = self.workspace == COMPOSER
        first = (lambda:self.confirm_project(action,force)) if composer else (lambda:self.confirm_save(action,force))
        second = (lambda:self.confirm_save(action)) if composer else (lambda:self.confirm_project(action))
        return first() and second()

    def save_scene(self, save_as=False):
        if self.building and not self.leave_builder():return False
        path = self.sandbox_scene.path
        if save_as or path is None:
            value,_ = QFileDialog.getSaveFileName(self,'Save scene',str(path or 'Untitled.scene.json'),'Scene JSON (*.scene.json)')
            if not value: return False
            path = Path(value if value.lower().endswith('.json') else value+'.scene.json')
        try:
            self.commit_scene_history(force=True)
            self.sandbox_scene.save(path); self.scene_history.mark_saved(); self.update_title()
            self.clear_autosave(SANDBOX)
            self.remember_file('scene',path); return True
        except Exception as error: self.error(error); return False

    def open_scene(self):
        if self.building and not self.leave_builder():return
        value,_ = QFileDialog.getOpenFileName(self,'Open scene','','Scene JSON (*.json)')
        if not value: return
        self.open_scene_path(value)

    def open_scene_path(self, value, recent=False):
        if self.building and not self.leave_builder(): return
        try:
            if not self.confirm_save('opening another scene'): return
            read_scene(value)
            self.clear_solos()
            notices = []; self.shutdown_audio(); self.sandbox_scene.load(Path(value),notices); self.reset_scene_history(); self.restart_motion();self.sync()
            if notices: self.flash(' · '.join(notices))
            self.monitor.setCurrentIndex(0); self.monitor_changed(0)
            if self.scene.settings.source == MIDI_SOURCE: self.refresh_ports()  # Reconnects to the scene's device.
            self.remember_file('scene',value)
        except Exception as error:
            if recent: self.forget_file('scene',value)
            self.error(error)

    autosave_enabled = False  # Only main() enables this; harnesses opt in with a temporary directory.
    autosave_dir = USER_DIR/'autosave'
    autosave_report = Signal(str)  # Worker-thread messages, queued to flash on the GUI thread.
    recovery_writer = None

    def autosave_worker(self):
        """Recovery files are written on autosave.RecoveryWriter's thread (0.76.0-dev, P12)."""
        if self.recovery_writer is None:
            self.autosave_report.connect(self.flash)
            self.recovery_writer = autosave.RecoveryWriter(self.autosave_report.emit)
        return self.recovery_writer

    def clear_autosave(self, workspace=None):
        # Runs after any in-flight write and drops pending ones; waits at most 1 s.
        if self.autosave_enabled: self.autosave_worker().cleanup(self.autosave_dir, workspace)

    def finish_autosave(self):
        """Quit: give queued recovery writes up to 2 s."""
        if self.recovery_writer is not None: self.recovery_writer.close(2.)

    def autosave_now(self):
        if not self.autosave_enabled: return
        try:
            self.store_clip_scene()  # Include panel edits even while history waits for a mouse release.
            self.commit_history()
            # Snapshots only: Project is frozen and Scene replaces its settings on every edit.
            scene, worker = self.sandbox_scene, self.autosave_worker()
            if self.project_dirty: worker.submit(self.autosave_dir, COMPOSER, self.project, self.project_path)
            if scene.dirty: worker.submit(self.autosave_dir, SANDBOX, (scene.settings, scene.mode, scene.device), scene.path)
        except Exception as error: self.flash(f'Could not autosave: {error}')

    def restore_autosave(self, item):
        """Restore one copy without a dialog; keep it until a fresh recovery is durable."""
        original = Path(item.original) if item.original is not None else None
        if item.workspace == COMPOSER:
            self.load_project(read_project(item.path), None)
            self.project_path = original
            self.history_timer.stop(); self.history.reset(self.history_state(), dirty=True)
            self.project_dirty = True; self.update_history_actions()
            missing = audio_media.missing_count(self.project)
            if missing: self.flash(f'{missing} audio file{"s are" if missing != 1 else " is"} missing: File › Find missing audio files….')
        else:
            self.clear_solos()
            self.shutdown_audio(); self.sandbox_scene.load(item.path)
            self.sandbox_scene.path = original; self.sandbox_scene.saved = None; self.reset_scene_history()
            if self.workspace == SANDBOX: self.scene = self.sandbox_scene
            self.restart_motion(); self.sync()
        self.update_title()
        # Transfer ownership before deleting the crashed instance's only copy.
        if self.autosave_enabled:
            scene = self.sandbox_scene
            document = self.project if item.workspace == COMPOSER else (scene.settings, scene.mode, scene.device)
            self.autosave_worker().write_now(self.autosave_dir, item.workspace, document, original)
            autosave.discard(self.autosave_dir, [item])

    def offer_autosave(self):
        if not self.autosave_enabled or getattr(self.options, 'skip_recovery', False): return
        try:
            found = autosave.find_stale(self.autosave_dir)
            if not found: return
            box = QMessageBox(self); box.setWindowTitle('Recover unsaved work')
            box.setText('Recovery copies were found:\n\n' + '\n'.join(
                f'{item.workspace}: {Path(item.original).name if item.original else "Untitled"} — {item.saved_at}'
                for item in found))
            restore = box.addButton('Restore', QMessageBox.ButtonRole.AcceptRole)
            discard = box.addButton('Discard', QMessageBox.ButtonRole.DestructiveRole)
            box.exec()
            if box.clickedButton() == restore:
                for item in found: self.restore_autosave(item)
            elif box.clickedButton() == discard: autosave.discard(self.autosave_dir, found)
        except Exception as error: self.error(error)

    remember_recent = False  # Only main() enables user-file access; harnesses opt in with a temporary path.
    recent_path = recent_files.RECENT_PATH

    def remember_file(self, kind, path):
        if self.remember_recent:
            try: recent_files.add_recent(kind,path,self.recent_path)
            except OSError as error: self.flash(f'Could not save recent files: {error}')

    def forget_file(self, kind, path):
        if self.remember_recent:
            try: recent_files.remove_recent(kind,path,self.recent_path)
            except OSError as error: self.flash(f'Could not save recent files: {error}')

    def clear_recent_menu(self, kind):
        if self.remember_recent:
            try: recent_files.clear_recent(kind,self.recent_path)
            except OSError as error: self.error(error)

    def rebuild_recent_menu(self):
        menu = self.recent_menu; menu.clear()
        kind = 'project' if self.workspace == COMPOSER else 'scene'
        entries = []
        if self.remember_recent:
            try: entries = recent_files.read_recent(kind,self.recent_path)
            except OSError as error: self.flash(f'Could not read recent files: {error}')
        opener = self.open_project_path if kind == 'project' else self.open_scene_path
        for path in entries:
            action = menu.addAction(path.name.replace('&','&&'))
            action.setToolTip(str(path)); action.setStatusTip(str(path))
            action.triggered.connect(lambda checked=False, path=path:opener(path,recent=True))
        if not entries: menu.addAction('No recent files').setEnabled(False)
        menu.addSeparator()
        menu.addAction('Clear Menu',lambda:self.clear_recent_menu(kind))

    def shutdown_audio(self):
        if self.midi_recorder is not None: self.stop_song()
        self.capture.stop(); self.audio.stop(); self.audio.midi.disconnect()
        if self.video is not None: self.video.player.stop()

    def new_instance(self):
        if self.building and not self.leave_builder():return
        if self.launch_pending or self.exporting: return
        if not self.confirm_all('opening a new instance',True): return
        try:
            self.shutdown_audio()
            ready = USER_DIR/f'qt-launch-{uuid.uuid4().hex}.ready'; ready.parent.mkdir(parents=True,exist_ok=True)
            process = subprocess.Popen(relaunch_command('--ready-file',str(ready)),cwd=HERE,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.launch_pending = (process,ready,time.monotonic()+20)
            self.centralWidget().setEnabled(False); self.menuBar().setEnabled(False)
        except Exception as error: self.error(error)

    # Pop-out monitor ------------------------------------------------------------
    remember_monitor = False  # main() turns this on; smokes and harnesses never touch the saved file.

    def hosts(self):
        return [self.main_host]+([self.monitor_window.host] if self.monitor_window is not None else [])

    def all_corner_buttons(self):
        buttons = [getattr(self,'corner_buttons',None)]+([self.monitor_window.buttons] if self.monitor_window is not None else [])
        return [b for b in buttons if b is not None]

    def style_hosts(self):
        """Palette background on every monitor host and colours on the corner buttons."""
        if not hasattr(self,'main_host'): return
        p = PALETTES[self.preferences['palette']]  # The scope's background is the display's, not the style's.
        for host in self.hosts():
            if host.view.color() != QColor(p['background']): host.view.setColor(QColor(p['background']))
        for buttons in self.all_corner_buttons(): buttons.set_theme(self.theme)

    def ensure_monitor_window(self):
        if self.monitor_window is None:
            host = ViewHost(); host.setMinimumSize(1,1)
            host.view.frameSwapped.connect(self.swapped, Qt.ConnectionType.QueuedConnection)
            window = monitor_window.MonitorWindow(host); window.setWindowIcon(self.windowIcon()); window.resize(640,640)
            window.buttons = monitor_window.CornerButtons(host.view.contentItem(), self.theme)
            window.buttons.clicked.connect(self.corner_clicked)
            window.returned.connect(self.pop_in)
            window.fullscreen_changed.connect(lambda on:self.sync_monitor_controls())
            self.monitor_window = window
            saved = read_monitor_window() if self.remember_monitor else None
            if saved is not None: window.restore_geometry(saved,QApplication.screens())
            else: self.place_monitor_window()
            self.apply_preferences(self.preferences)  # Background and button colours for the new host.
        return self.monitor_window

    def place_monitor_window(self):
        """First pop-out: beside the main window, on another screen when there is one."""
        window = self.monitor_window; own = self.screen()
        others = [s for s in QApplication.screens() if s is not own]
        area = (others[0] if others else own or QApplication.primaryScreen()).availableGeometry()
        size = min(640,area.width()-40,area.height()-40)
        window.setGeometry(area.x()+(area.width()-size)//2,area.y()+(area.height()-size)//2,size,size)

    def switch_host(self, host):
        """Draw on `host` from the next tick: the main viewport or the pop-out (never both)."""
        if host is self.host: return
        playing = self.video is not None and self.video.playing
        self.host = host; self.drag = None; self.handles = []
        self.frame_times.clear(); self.swap_times.clear(); self.content_swap_times.clear()
        host.view.update()
        if self.video is not None and self.video_monitor and host.phosphor is not None:
            self.video.attach(host.phosphor.video_output())
            if playing: self.video.player.play()
        self.sweep.reset(); self.sweep_frame = self.sweep_feed = None
        self.host.scope.history_key = None
        self.apply_preferences(self.preferences)

    def pop_out(self):
        if self.building or self.popped:
            self.sync_monitor_controls(); return
        if self.immersive: self.set_immersive(False)
        window = self.ensure_monitor_window()
        self.popped = True; self.stage.set_popped(True)
        window.show(); window.raise_(); window.activateWindow()
        self.switch_host(window.host); self.sync_monitor_controls()
        if self.remember_monitor: self.save_monitor_window()

    def pop_in(self):
        if not self.popped:
            self.sync_monitor_controls(); return
        window = self.monitor_window
        if window.is_fullscreen: window.set_fullscreen(False)
        if self.remember_monitor: self.save_monitor_window(opened=False)
        self.popped = False; window.hide(); self.stage.set_popped(False)
        self.switch_host(self.main_host); self.sync_monitor_controls()

    def set_trace_fullscreen(self, on):
        """The trace alone, full screen on one screen (the pop-out, borderless)."""
        if on:
            self.pop_out()
            if self.popped: self.monitor_window.set_fullscreen(True)
        elif self.monitor_window is not None and self.monitor_window.is_fullscreen:
            self.monitor_window.set_fullscreen(False)
        self.sync_monitor_controls()

    def corner_clicked(self, name):
        if name == 'popout': self.pop_out()
        elif name == 'return': self.pop_in()
        elif name == 'fullscreen': self.set_trace_fullscreen(True)
        elif name == 'exit_fullscreen': self.set_trace_fullscreen(False)

    def sync_monitor_controls(self):
        full = self.monitor_window is not None and self.monitor_window.is_fullscreen
        if hasattr(self,'corner_buttons'):
            self.corner_buttons.set_buttons([] if self.immersive or self.popped else ['popout','fullscreen'])
        if self.monitor_window is not None:
            self.monitor_window.buttons.set_buttons(['return','exit_fullscreen' if full else 'fullscreen'])
        for button,state in ((getattr(self,'popout_button',None),self.popped),(getattr(self,'trace_fullscreen_button',None),full)):
            if button is not None and button.isChecked() != state:
                blocker = QSignalBlocker(button); button.setChecked(state); del blocker

    def save_monitor_window(self, opened=None):
        if self.monitor_window is None: return
        try: write_monitor_window(dict(self.monitor_window.geometry_document(),open=self.popped if opened is None else opened))
        except OSError as error: print(f'Monitor window position not saved: {error}',file=sys.stderr)

    def restore_monitor_window(self):
        """At launch (main() only): reopen the pop-out if it was open when the app closed."""
        self.remember_monitor = True
        saved = read_monitor_window()
        if saved is not None and saved['open']: self.pop_out()

    def release_views(self):
        self.views_released = True
        if not getattr(self, 'scheme_disconnected', False):  # A closed window never restyles; release_views may run twice.
            self.scheme_disconnected = True
            try: QGuiApplication.styleHints().colorSchemeChanged.disconnect(self.color_scheme_changed)
            except (RuntimeError, TypeError): pass
        self.browser.close_worker()
        self.timer.stop()
        # The builder's preview has its own phosphor layer. Left attached, Qt destroys it
        # during interpreter shutdown in an order that segfaults.
        if self.video is not None: self.video.detach()  # Before its VideoOutput is released.
        if self.monitor_window is not None:
            if self.remember_monitor: self.save_monitor_window()
            self.monitor_window.hide()
        for host in self.hosts(): host.release_phosphor()
        if self.builder is not None: self.builder.preview.release_phosphor()
        if self.desk3d_host is not None: self.desk3d_host.release_phosphor()

    def flush_manual_geometry(self):
        """Write the manual window's place now if a debounced save is still pending (quit)."""
        window = getattr(self, 'manual_window', None)
        if window is not None and window.isVisible(): window.save_geometry()
        if self.remember_recent and self.preferences_save.isActive(): self.preferences_save.stop(); self.save_preferences()

    def closeEvent(self, event):
        if self.audio.cycle: self.audio.stop()
        if self.midi_recorder is not None: self.stop_song()
        if self.force_close: self.cycle_closing = True; self.release_views(); self.clear_autosave(); event.accept(); return
        if self.building and not self.leave_builder():event.ignore();return
        if self.launch_pending: event.ignore(); return
        if self.exporting:
            QMessageBox.information(self,'Export in progress','Wait for the export to finish, or cancel video export, before exiting.'); event.ignore(); return
        if not self.confirm_all('exiting'): event.ignore(); return
        try: self.cycle_closing = True; self.flush_manual_geometry(); self.shutdown_audio(); self.release_views(); self.clear_autosave(); event.accept()
        except Exception as error: self.error(error); event.ignore()

    def transport_key(self, key, shortcut=None):
        """Window shortcuts must leave editing keys with the focused field. The field gets the original key
        (Space, Home, End) only while it is still this action's shortcut: a rebound key acts (UI2)."""
        focus = QApplication.focusWidget()
        widget = focus if shortcut is None or QKeySequence(key) in key_sequences(shortcut) else None
        while widget is not None:
            if (isinstance(widget, (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit))
                    or isinstance(widget, QComboBox) and widget.isEditable()):
                # Send KeyPress directly: the QAction consumed the original press.
                # The physical release still goes to the same focused widget.
                QApplication.sendEvent(focus, QKeyEvent(QEvent.Type.KeyPress, key,
                    Qt.KeyboardModifier.NoModifier, ' ' if key == Qt.Key.Key_Space else ''))
                return
            widget = widget.parentWidget()
        if self.building: return
        if key == Qt.Key.Key_Space:
            self.play()
        elif self.workspace == COMPOSER:
            if key == Qt.Key.Key_Home:
                beat = self.project.loop_start if self.project.loop else 0.
                self.seek_song(float(self.project.seconds(beat)))
            elif key == Qt.Key.Key_End:
                self.stop_song()
                self.seek_song(float(self.project.seconds(self.project.end_beats)))
        elif key == Qt.Key.Key_Home and not self.system_monitor and not self.video_monitor:
            if self.audio.running:
                self.audio.stop(); self.play()
            else:
                self.restart_motion()

    def play(self):
        try:
            if self.video_monitor:
                if self.video is None or self.video.player.source().isEmpty(): self.open_video()
                else: self.video.toggle()
                self.last_status = 0
                return
            if self.system_monitor:
                if self.capture.running: self.capture.stop()
                else:
                    self.audio.stop()
                    self.capture.start(self.capture_devices.currentData())
                self.last_status = 0
                return
            if self.workspace == COMPOSER:
                self.stop_song() if self.audio.running and self.audio.composer is not None else self.start_song(); return
            if self.audio.preview_running: self.audio.stop()  # Play replaces a file preview (AW4).
            if self.audio.running: self.audio.stop()
            else:
                self.audio.composer = None
                if self.scene.settings.source == MIDI_SOURCE and self.audio.midi.port is None:
                    raise ValueError('Choose a MIDI input device in INPUT before starting Live MIDI audio.')
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
            if self.audio.midi.port is not None and self.audio.midi.port_name == self.scene.device: return
            self.audio.stop(); self.audio.midi.connect(self.scene.device)
        except Exception as error: self.error(error)

    def disconnect_midi(self):
        try:
            if self.midi_recorder is not None: self.stop_song()
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

    def export(self, source_only=False, path=None, format=None, depth=None, rate=None, range=None):
        from phosphor.editor.export_dialog import ExportDialog
        from phosphor.storage.audio_writer import validate_options
        self.browser.stop_preview()
        if self.system_monitor:
            QMessageBox.information(self,'System audio','System-audio mode is visualization only; WAV export is disabled.'); return
        if self.exporting: return
        if self.video_monitor:
            QMessageBox.information(self,'Export','Video input is display only; WAV export is disabled.'); return
        composer = self.workspace == COMPOSER
        if not composer and self.scene.settings.source == MIDI_SOURCE:
            QMessageBox.information(self,'Export','Use Record performance for live MIDI.'); return
        self.store_clip_scene()
        settings = replace(self.output_settings(), motion_time_offset=0.)
        project = self.output_project()
        self.warn_solo_export()
        if path is None and all(value is None for value in (format, depth, rate, range)):
            dialog = ExportDialog(self, self.preferences,
                float(project.seconds(project.end_beats)) if composer else source_duration(settings),
                float(project.seconds(project.loop_end-project.loop_start)) if composer else None)
            if dialog.exec() != QDialog.DialogCode.Accepted: return
            format, depth, rate, range = dialog.choices()
        format = self.preferences['export_format'] if format is None else format
        depth = self.preferences['export_depth'] if depth is None else depth
        rate = self.preferences['export_rate'] if rate is None else rate
        range = self.preferences['export_range'] if range is None else range
        validate_options(format, depth, rate)
        if range not in ('whole', 'loop'): raise ValueError('Select whole song or loop range.')
        extension = '.' + format.lower()
        title = ('Export notes' if source_only else 'Export song') if composer else ('Export source' if source_only else 'Export processed')
        if path is None: path,_ = QFileDialog.getSaveFileName(self,title,'',f'Stereo {format} (*{extension})')
        if not path: return
        target = Path(path)
        if target.suffix.lower() != extension: target = target.with_suffix(extension)
        settings = replace(settings, sample_rate=rate)
        self.preferences = validated_preferences(dict(self.preferences, export_format=format,
            export_depth=depth, export_rate=rate, export_range=range))
        self.appearance_editor.load(self.preferences)
        self.preferences_save.start()
        self.exporting = True
        def work():
            temporary = target.with_name('.export-'+uuid.uuid4().hex+extension)
            try:
                # Composer renders the selected span; Sandbox keeps its fixed source duration.
                if composer: export_song(temporary,project,settings,
                    start_beats=project.loop_start if range == 'loop' else 0.,
                    end_beats=project.loop_end if range == 'loop' else None,
                    source_only=source_only,format=format,depth=depth)
                else: export_wav(temporary,settings,source_duration(settings),source_only=source_only,format=format,depth=depth)
                os.replace(temporary,target); self.jobs.put(('export',str(target)))
            except Exception as error: self.jobs.put(('export_error',str(error)))
            finally:
                if temporary.exists(): temporary.unlink()
        threading.Thread(target=work,daemon=True).start()

    def export_video(self):
        from phosphor.editor.video_export import require_ffmpeg, VideoExportDialog, VideoExportJob
        from phosphor.engine.composition import render_song
        if self.exporting: return
        self.browser.stop_preview()
        ffmpeg = require_ffmpeg(self, self.preferences['ffmpeg_path'])
        if ffmpeg is None: return
        if self.system_monitor or self.video_monitor or (self.workspace != COMPOSER and self.scene.settings.source == MIDI_SOURCE):
            QMessageBox.information(self, 'Export video', 'Video export needs a Composer song or a generated Sandbox scene.'); return
        self.store_clip_scene()
        composer = self.workspace == COMPOSER; project = self.output_project()
        self.warn_solo_export()
        settings = replace(self.output_settings(), sample_rate=48000, motion_time_offset=0.)
        duration = float(project.seconds(project.end_beats)) + processing_tail(project) if composer else source_duration(settings)
        dialog = VideoExportDialog(self, duration, composer)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        width, height = dialog.resolution.currentData(); fps = int(dialog.fps.currentText())
        container = 'MKV' if dialog.container.currentIndex() else 'MP4'; encoder = dialog.encoder.currentText()
        if not composer: duration = dialog.duration.value()
        path, _ = QFileDialog.getSaveFileName(self, 'Export video', '', f'{container} video (*.{container.lower()})')
        if not path: return
        target = Path(path).with_suffix('.' + container.lower())
        self.audio.stop(); self.exporting = True
        count = round(duration * 48000)
        def prepare(stop):
            # Blocks stream to the job, which starts the video after the first one. One ChainState
            # carries audio-track memory across the blocks, as export_song does (AW1b).
            state = ChainState()
            for start in range(0, count, 48000):
                if stop.is_set(): return
                times = np.arange(start, min(start+48000, count)) / 48000
                yield render_song(times, project, settings, chain_state=state) if composer else render(times, settings)[2]
        def finished(success, error):
            self.exporting = False
            if error: self.error(error)
            elif success: self.status.setText('Exported ' + str(target))
        try:
            self.video_export_job = VideoExportJob(self, ffmpeg, target, self.preferences, width, height, fps, prepare, finished, container,
                                                   encoder, count)
        except Exception as error:
            self.exporting = False; self.error(error)

    def set_brightness(self, value):
        self.apply_preferences(dict(self.preferences,beam_intensity=value))
        self.brightness_save.start()

    def save_brightness(self):
        try:
            saved = read_preferences(PREFERENCES_PATH)
            if saved['beam_intensity'] != self.preferences['beam_intensity']:
                atomic_json(PREFERENCES_PATH,dict(saved,beam_intensity=self.preferences['beam_intensity']))
        except Exception as error: self.error(error)

    def apply_preferences(self, preferences, source=None):
        old_display_rate = self.preferences.get('display_rate', 'Match monitor')
        preferences = dict(preferences)
        from phosphor.editor.wheel_mapping import ACTIONS, validated_mapping, gesture
        for key in ACTIONS: preferences.setdefault(key, self.preferences[key])
        for key in ('export_format', 'export_depth', 'export_rate', 'export_range', 'ffmpeg_path', 'metronome_level', 'style', 'style_mode',
                    *ROLL_VIEW_KEYS, 'diagnostic_logging', 'manual_geometry', 'panel_width', 'wheel_sensitivity', 'trackpad_sensitivity',
                    'browser_shown', 'browser_width', 'browser_folder', 'left_page'):
            preferences.setdefault(key, self.preferences[key])
        self.preferences = validated_preferences(preferences)
        if hasattr(self, 'left_panel'):
            page = self.effects_panel if self.preferences['left_page'] == 'effects' else self.browser
            self.left_panel.stack.setCurrentWidget(page)
        theme = self.apply_theme()
        if hasattr(self, 'composer_panel'):
            for key in ROLL_VIEW_KEYS:
                setattr(self.composer_panel.timeline, key, self.preferences[key])
            self.composer_panel.timeline.place_roll_buttons()
            self.composer_panel.timeline.update()
        if old_display_rate != self.preferences['display_rate']:
            self.frame_times.clear(); self.swap_times.clear(); self.content_swap_times.clear()
            self.display_pending = False
            self.next_frame_time = None
            if hasattr(self, 'frame_timer'): self.frame_timer.stop()
            self.host.view.update()
        self.audio.metronome_level = self.preferences['metronome_level']
        mapping = validated_mapping(self.preferences)
        if self.builder is not None:
            self.builder.wheel_mapping = mapping
            self.builder.wheel_sensitivity, self.builder.trackpad_sensitivity = self.preferences['wheel_sensitivity'], self.preferences['trackpad_sensitivity']
        if getattr(self, 'shortcuts_window', None) is not None:
            self.shortcuts_window.refresh(self.shortcuts_window.search.text())
        if source is not getattr(self,'appearance_editor',source): self.appearance_editor.load(self.preferences)
        if self.preferences_dialog is not None:
            for editor in self.preferences_dialog.findChildren(AppearanceEditor):
                if editor is not source: editor.load(self.preferences)
        if hasattr(self,'composer_panel'):
            self.composer_panel.timeline.wheel_mapping = mapping
            timeline = self.composer_panel.timeline
            timeline.wheel_sensitivity, timeline.trackpad_sensitivity = self.preferences['wheel_sensitivity'], self.preferences['trackpad_sensitivity']
            hint = (gesture(mapping['wheel_scroll']) + ': move in time · ' + gesture(mapping['wheel_zoom']) +
                    ': zoom · ' + gesture(mapping['wheel_height']) + ' over the cells: row or block height')
            hint = self.composer_panel.hint.split('Wheel:', 1)[0] + 'Wheel: pitch · ' + hint
            self.composer_panel.timeline.setToolTip(hint)
            self.composer_panel.timeline.hint_text = hint
            self.composer_panel.zoom_in.setToolTip('Zoom in (' + gesture(mapping['wheel_zoom']) + ')')
            self.composer_panel.zoom_out.setToolTip('Zoom out (' + gesture(mapping['wheel_zoom']) + ')')
        if hasattr(self,'brightness_control'):
            for control in (self.brightness_control,self.monitor_brightness):
                if control.value != self.preferences['beam_intensity']: control.setValue(self.preferences['beam_intensity'])
                control.setEnabled(self.host.phosphor is not None and self.preferences['phosphor'] != PHOSPHOR_OFF)
        if getattr(self, 'desk3d_host', None) is not None:
            self.desk3d_host.sync_quality()
        self.restyle(theme)

    def restyle(self, theme):
        """Show theme: the stylesheet, the painted widgets, the monitor hosts and the plots. Never
        touches preference values or what an editor shows (a System colour scheme change calls this)."""
        if hasattr(self,'composer_panel'):
            timeline = self.composer_panel.timeline
            timeline.theme = theme
            timeline.colors = theme.timeline_colors()
            for strip in (timeline.velocity_strip, timeline.switch_strip, timeline.gain_strip):
                strip.theme = theme
            for splitter in (self.vsplit, self.splitter, self.browser_split):
                splitter.theme = theme
                for index in range(splitter.count()):
                    splitter.handle(index).set_theme(theme)
            timeline.update()
        self.style_hosts()  # Every time: a pop-out host created later must not keep Qt's white default.
        if hasattr(self, 'stage'):
            self.stage.set_vintage(theme.style == themes.VINTAGE, PALETTES[self.preferences['palette']]['trace'])
        if hasattr(self, 'app_bar') and hasattr(self.app_bar, 'set_vintage'):
            self.app_bar.set_vintage(theme.style == themes.VINTAGE)
        if hasattr(self, 'transform_panel'):
            self.transform_panel.noise_spectrum.theme = theme
            self.transform_panel.noise_spectrum.update()
        previous = getattr(self,'_styled_theme',None)
        if previous is theme: return
        app = QApplication.instance()
        if app is not None:
            from PySide6.QtGui import QBrush
            from PySide6.QtWidgets import QStyleFactory
            from phosphor.editor.vintage_style import VintageProxyStyle, plastic_tile, aluminum_tile
            if not app.property('phosphorBaseStyle'):
                app.setProperty('phosphorBaseStyle', app.style().objectName())
                app.setProperty('phosphorBasePalette', app.palette())
            if theme.style == themes.VINTAGE:
                if not isinstance(app.style(), VintageProxyStyle):
                    app.setStyle(VintageProxyStyle(app.property('phosphorBaseStyle')))
                pal = QPalette(app.property('phosphorBasePalette') or app.palette())
                pal.setBrush(QPalette.ColorRole.Window, QBrush(plastic_tile()))
                pal.setColor(QPalette.ColorRole.WindowText, QColor(theme.colors['fg']))
                pal.setColor(QPalette.ColorRole.ButtonText, QColor(theme.colors['fg']))
                pal.setColor(QPalette.ColorRole.Base, QColor('#131c14'))
                pal.setColor(QPalette.ColorRole.Text, QColor('#8ff08a'))
                app.setPalette(pal)
                bar_pal = QPalette(pal)
                bar_pal.setBrush(QPalette.ColorRole.Window, QBrush(aluminum_tile()))
                self.app_bar.setAutoFillBackground(True)
                self.app_bar.setPalette(bar_pal)
            elif isinstance(app.style(), VintageProxyStyle):
                base = QStyleFactory.create(app.property('phosphorBaseStyle') or 'Fusion')
                if base is not None: app.setStyle(base)
                base_pal = app.property('phosphorBasePalette')
                if base_pal is not None:
                    app.setPalette(base_pal)
                    self.app_bar.setAutoFillBackground(False)
                    self.app_bar.setPalette(base_pal)
        p = theme.colors
        arrows = self.spin_arrow_images(p)
        # Without images (unwritable temp folder) the buttons still work, shown as plain bars.
        self.setStyleSheet(theme.stylesheet(arrows))
        self.version_label.setStyleSheet(theme.version_stylesheet())
        self.composer_panel.record.setStyleSheet(theme.record_stylesheet())
        self.appearance_editor.set_theme(theme)
        if self.preferences_dialog is not None:
            for editor in self.preferences_dialog.findChildren(AppearanceEditor):
                editor.set_theme(theme)
        if getattr(self, 'manual_window', None) is not None:
            self.manual_window.view.theme = theme
            self.manual_window.view.highlight_matches()
        size = theme.metrics.bar_icon_size
        self.app_bar.setIconSize(QSize(size, size))
        artwork = str(logo_path(self.preferences['palette']))
        icon = QIcon(artwork)
        self.setWindowIcon(icon)
        QApplication.instance().setWindowIcon(icon)
        if self.builder is not None:self.builder.set_colors(theme.builder_colors())
        self.refresh_bar_icons()
        self._styled_theme = theme
        if previous is not None and (previous.style, previous.fonts) != (theme.style, theme.fonts) and hasattr(self, 'composer_panel'):
            self.composer_panel.restyled()  # New sizes for the transport controls (the stylesheet is in place).

    def color_scheme_changed(self, scheme):
        """The OS colour scheme changed: only the theme follows. Editors keep what they show, typed
        text included, and nothing is saved."""
        if self.preferences['style'] == themes.MODERN and self.preferences['style_mode'] == 'System':
            self.restyle(self.apply_theme(scheme))

    @staticmethod
    def system_mode(scheme=None):
        """Modern's mode for System: the OS scheme, or for an unknown scheme the application
        palette's window luminance (themes.resolve_mode)."""
        if scheme is None: scheme = QGuiApplication.styleHints().colorScheme()
        name = 'Dark' if scheme == Qt.ColorScheme.Dark else 'Light' if scheme == Qt.ColorScheme.Light else 'Unknown'
        window = QApplication.palette().color(QPalette.ColorRole.Window)
        return themes.resolve_mode('System', name, themes.relative_luminance((window.red(), window.green(), window.blue())))

    def apply_theme(self, scheme=None):
        """Select the window theme and update the fallback for standalone widgets."""
        style = self.preferences.get('style', themes.DEFAULT_STYLE)
        mode = self.preferences.get('style_mode', themes.DEFAULT_MODE)
        if style == themes.MODERN and mode == 'System':
            mode = self.system_mode(scheme)
        theme = themes.theme_for(style, self.preferences['palette'], mode if style == themes.MODERN else 'Dark')
        if not theme.fonts.family:
            if getattr(self, '_system_font_theme', None) is not theme:
                family = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
                self._system_font_theme = theme
                self._resolved_theme = replace(theme, fonts=replace(theme.fonts, family=family))
            theme = self._resolved_theme
        self.theme = theme; themes.set_current(theme)
        return theme

    def refresh_bar_icons(self):
        p = self.theme.colors
        playing = self.capture.running if self.system_monitor else (self.video is not None and self.video.playing) if self.video_monitor else self.audio.running and not self.audio.cycle and not self.audio.preview
        icon = ('pause' if self.video_monitor else 'stop') if playing else 'play'
        ink = p['accent'] if self.theme.style == themes.MODERN else p['fg']
        for widget in self.bar_buttons.values():
            name = icon if widget is self.play_button else widget.property('barIcon')
            key = (name, ink, self.theme.metrics.bar_icon_canvas, self.theme.metrics.bar_icon_stroke)
            if widget.property('shownIcon') != key:
                widget.setIcon(bar_icon(name, ink, self.theme.metrics)); widget.setProperty('shownIcon', key)

    def appearance_edited(self, values):
        try: self.apply_preferences(values,self.appearance_editor); self.preferences_save.start()
        except ValueError as error: self.error(error); self.appearance_editor.load(self.preferences)

    def set_diagnostic_logging(self, on):
        self.preferences = dict(self.preferences, diagnostic_logging=bool(on))
        if self.remember_recent: self.save_preferences()
        self.flash('Diagnostic logging turns ' + ('on' if on else 'off') + ' at the next launch.')

    def show_crash_logs(self):
        crash_log.LOG_DIR.mkdir(parents=True, exist_ok=True)
        target = crash_log.LOG_PATH if crash_log.LOG_PATH.exists() else crash_log.LOG_DIR
        if sys.platform == 'darwin': subprocess.Popen(['open', '-R', str(target)] if target.is_file() else ['open', str(target)])
        else: QDesktopServices.openUrl(QUrl.fromLocalFile(str(crash_log.LOG_DIR)))

    def save_preferences(self):
        try: atomic_json(PREFERENCES_PATH,self.preferences)
        except OSError as error: self.error(error)

    def appearance_defaults(self):
        # Only the Appearance editor's own keys: Controls, the panel width and the roll view keep theirs (Codex review, UI2).
        self.apply_preferences(dict(self.preferences, **{key: DEFAULT_PREFERENCES[key] for key in APPEARANCE_KEYS})); self.preferences_save.start()

    def appearance_revert(self):
        snapshot = getattr(self,'appearance_snapshot',self.preferences)
        self.apply_preferences(dict(self.preferences, **{key: snapshot[key] for key in APPEARANCE_KEYS})); self.preferences_save.start()

    def update_viewer_heading(self):
        sweep = self.sweep_mode and not self.video_monitor
        self.viewer_heading.setText('X(t), Y(t) TIME VIEW' if sweep else 'RASTER CRT · 60 Hz' if self.video_monitor else 'X–Y SIGNAL MONITOR')

    def captured_sweep_samples(self):
        """System audio captured since the last tick (at most 0.25 s) and its rate."""
        rate = self.capture.sample_rate
        frames = self.capture.frames
        new = 0 if self.sweep_frame is None or frames < self.sweep_frame else frames-self.sweep_frame
        self.sweep_frame = frames
        new = min(new,round(.25*rate))
        return (self.capture.samples(new) if new else np.empty((0,2),dtype=np.float32)), rate

    def sweep_traces(self, scope, samples, rate):
        """Advance the time view by new samples; return its new beam paths.

        Each trace is one polyline with a flag per segment, so retrace jumps between
        sweeps stay blanked while the whole frame is deposited in one call.
        """
        self.sweep.configure(self.sweep_trigger,self.sweep_edge,self.sweep_window_ms,rate)
        arr = np.asarray(samples, dtype=np.float32)
        if self.desk3d and self.desk3d_host is not None and arr.ndim == 2 and arr.shape[1] >= 2:
            arr = self.desk3d_host.controller.route_signal(arr)
        return self.sweep.traces(arr,scope.width(),scope.height(),self.visualization_gain,self.sweep_separation)

    def update_phosphor(self, new_frames, elapsed, traces=None, *, raw_signal=None, gain=1., decay_elapsed=None):
        if self.exporting: return
        from phosphor.display.frame_step import step_display
        layer = self.host.phosphor
        prefs = (self.desk3d_host.controller.crt_prefs(self.preferences)
                 if self.desk3d and self.desk3d_host is not None else self.preferences)
        step_display(self.host, prefs, self.host.scope.signal, new_frames, elapsed,
                     traces=traces, video=bool(self.video_monitor and layer is not None and layer.video is not None),
                     video_gain=field_gain(elapsed), scan_lines=self.video_panel.scan_lines,
                     line_width=LINE_WIDTH, video_monitor=self.video_monitor,
                     raw_signal=raw_signal, gain=gain, decay_elapsed=decay_elapsed)
        if self.desk3d and self.desk3d_host is not None:
            self.desk3d_host.controller.update_crt_light(
                self.host.scope.signal, traces=traces, elapsed=elapsed
            )

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
        if self.theme.style in (themes.MODERN, themes.VINTAGE):
            chevron = ((2, 2), (9, 9), (16, 2))
            for name, points, size, color in (
                    ('chevron', chevron, (18, 12), p['fg']),
                    ('chevron_checked', chevron, (18, 12), p['record_text']),
                    ('checkmark', ((5, 14), (11, 20), (23, 7)), (28, 28), p['selection_text'])):
                image = QImage(*size, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(0)
                painter = QPainter(image)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                pen = QPen(QColor(color), 3)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPolyline(QPolygonF([QPointF(*point) for point in points]))
                painter.end()
                path = self._arrow_directory.filePath(f'{name}_{color.lstrip("#")}.png')
                if image.save(path):
                    paths[name] = path.replace('\\', '/')
        return paths

    def show_preferences(self, page=None):
        """Preferences (Appearance, Export, Controls); page names the tab to show."""
        if self.preferences_dialog:
            if page: self.preferences_tabs.setCurrentIndex(max(0, [self.preferences_tabs.tabText(i) for i in range(self.preferences_tabs.count())].index(page)))
            self.preferences_dialog.raise_(); return
        dialog = QDialog(self); self.preferences_dialog = dialog; dialog.setWindowTitle('Preferences')
        outer = QVBoxLayout(dialog); baseline = dict(self.preferences)
        from phosphor.editor.preference_panels import ExportPreferences, ControlsPreferences
        tabs = self.preferences_tabs = QTabWidget(); tabs.setObjectName('preferences_tabs'); outer.addWidget(tabs)
        editor = AppearanceEditor(SliderInput,baseline); editor.set_theme(self.theme); tabs.addTab(editor, 'Appearance'); dialog.resize(560,700)
        export_page = ExportPreferences(baseline['ffmpeg_path']); tabs.addTab(export_page, 'Export')
        controls = ControlsPreferences(baseline, window=self, slider_class=SliderInput); tabs.addTab(controls, 'Controls')
        if page: tabs.setCurrentWidget(controls if page == 'Controls' else export_page if page == 'Export' else editor)
        def current(): return dict(editor.values(), ffmpeg_path=export_page.path.text().strip(), **controls.values())
        editor.changed.connect(lambda *args: self.apply_preferences(current(), editor))  # Preview; Cancel restores baseline.
        controls.changed.connect(lambda *args: self.apply_preferences(current()))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.RestoreDefaults)
        outer.addWidget(buttons)
        def apply():
            nonlocal baseline
            # Write the applied preferences: the dialog does not edit every key (the roll
            # scale lives in the Notes menu), and apply_preferences keeps the others.
            # Cancel restores the baseline, so it moves only once the file is written.
            try:
                applied = current(); self.apply_preferences(applied); atomic_json(PREFERENCES_PATH,self.preferences)
                baseline = applied; return True
            except Exception as error: self.error(error); return False
        def defaults():
            editor.load(DEFAULT_PREFERENCES); controls.load(DEFAULT_PREFERENCES); export_page.path.clear(); self.apply_preferences(current())
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(apply)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(defaults)
        buttons.accepted.connect(lambda:dialog.accept() if apply() else None)
        buttons.rejected.connect(dialog.reject)
        dialog.finished.connect(lambda result:(self.apply_preferences(baseline),setattr(self,'preferences_dialog',None)))
        dialog.setModal(True); dialog.show()

    def zoom_composer(self, selection=False):
        if self.workspace == COMPOSER and not self.building:
            self.composer_panel.timeline.zoom_to_fit(selection)

    # Keyboard shortcuts (UI2) ---------------------------------------------------------
    def shortcut_defaults(self):
        """The rebindable actions' platform keys as PortableText (Qt's standard keys included)."""
        portable = QKeySequence.SequenceFormat.PortableText
        return {action: tuple(dict.fromkeys(s.toString(portable) for s in default_key_sequences(action) if not s.isEmpty()))
                for action in SHORTCUT_SCOPES}

    def init_shortcuts(self, path):
        """Read the per-machine bindings (main() and smokes call this once user files are on). A damaged or
        newer file is reported, the defaults are used, and the file is left alone until Reset all or Import."""
        from phosphor.editor.shortcut_editor import checked_bindings, file_state
        self.shortcuts_path, notices = path, []
        self.shortcuts_disk = file_state(path)  # What this window last read or wrote (Codex review: never clobber).
        try: bindings, error = checked_bindings(read_bindings(path, defaults=self.shortcut_defaults(), notices=notices)), ''
        except ValueError as problem: bindings, error = ShortcutBindings(defaults=self.shortcut_defaults()), str(problem)
        self.shortcut_bindings, self.shortcut_error = bindings, error
        self.apply_shortcut_bindings()
        if error or notices: self.status.setText(' '.join([error, *notices]).strip())

    def set_shortcut_bindings(self, bindings, replace=False):
        """Apply and save new bindings (Controls › Keyboard shortcuts); False, with a warning, if not saved.
        A damaged file is written only with replace (Reset all, Import), and the damage is cleared only once
        that write succeeded. If the file changed since this window read it (another phosphor window, a
        newer version), nothing is written: the file is read again and the user is told."""
        from phosphor.editor.shortcut_editor import file_state
        if self.shortcuts_path is not None:
            if self.shortcut_error and not replace: return False
            if not replace and file_state(self.shortcuts_path) != self.shortcuts_disk:
                self.init_shortcuts(self.shortcuts_path)
                self.warn('Keyboard shortcuts', 'The shortcuts file changed outside this window (another phosphor window?). '
                          'It was read again; make your change once more.' + (f' {self.shortcut_error}' if self.shortcut_error else ''))
                return False
            try: write_bindings(bindings, self.shortcuts_path)
            except (OSError, ValueError) as error: self.warn('Keyboard shortcuts', f'Could not save shortcuts: {error}'); return False
            self.shortcuts_disk = file_state(self.shortcuts_path)
        self.shortcut_error = ''
        self.shortcut_bindings = bindings; self.apply_shortcut_bindings(); return True

    def apply_shortcut_bindings(self):
        """Give every QAction with a rebindable shortcut_id its current keys."""
        set_shortcut_overrides(self.shortcut_bindings.overrides)
        for action in self.findChildren(QAction):
            name = action.property('shortcut_id')
            if name in SHORTCUT_SCOPES: action.setShortcuts(key_sequences(name))
        for name, action in (('Full screen', self.fullscreen_action), ('Trace only', self.immersive_action)):
            button = self.bar_buttons.get(name)
            if button is not None: button.setToolTip(action.text() + f' ({action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)})')
        if getattr(self, 'shortcuts_window', None) is not None: self.shortcuts_window.refresh(self.shortcuts_window.search.text())

    def capture_shortcut(self, description, current):
        """Ask for one new key (PortableText), or None; smokes replace this on the instance."""
        from phosphor.editor.shortcut_editor import capture_dialog
        return capture_dialog(self.preferences_dialog or self, description, current)

    def pick_shortcuts_file(self, save):
        """Export (save=True) or import path for Keyboard shortcuts, or ''; smokes replace this."""
        name = 'phosphor-shortcuts.json'; flt = 'phosphor shortcuts (*.json)'
        if save: return QFileDialog.getSaveFileName(self.preferences_dialog or self, 'Export shortcuts', name, flt)[0]
        return QFileDialog.getOpenFileName(self.preferences_dialog or self, 'Import shortcuts', '', flt)[0]

    def show_shortcuts(self):
        if getattr(self, 'shortcuts_window', None) is None:
            self.shortcuts_window = ShortcutsView(self)
        self.shortcuts_window.refresh(self.shortcuts_window.search.text())
        self.shortcuts_window.show()
        self.shortcuts_window.raise_()
        self.shortcuts_window.activateWindow()
        self.shortcuts_window.search.setFocus()

    def about(self, manual=False, troubleshooting=False):
        if manual: self.open_manual(); return  # The manual has its own window (UI1).
        dialog = QDialog(self); dialog.setWindowTitle('Help & About — phosphor'); dialog.resize(780,640)
        layout = QVBoxLayout(dialog); tabs = QTabWidget(); layout.addWidget(tabs)
        overview = QTextBrowser()
        overview.setPlainText(f'phosphor {VERSION}\n\nMade by Mike Le\n\nProject date: {PROJECT_DATE}\nBuild date: {BUILD_DATE}\n\nAI transparency\n\n{TRANSPARENCY}')
        tabs.addTab(overview, 'About')
        audio_help = QTextBrowser()
        audio_help.setPlainText(MACOS_AUDIO_TROUBLESHOOTING if sys.platform == 'darwin' else AUDIO_TROUBLESHOOTING)
        tabs.addTab(audio_help, 'Audio troubleshooting')
        tabs.setCurrentIndex(1 if troubleshooting else 0)
        self.run_dialog(dialog)
        dialog.deleteLater()

    def open_manual(self):
        """Help › Manual: one non-modal window on top of this one; opening it again raises it (UI1)."""
        window = getattr(self, 'manual_window', None)
        if window is None:
            geometry = self.preferences.get('manual_geometry', ())
            if geometry:
                screens = [s.availableGeometry() for s in QApplication.screens()]
                try: geometry = tuple(int(v) for v in monitor_window.fit_geometry(geometry, [(r.x(), r.y(), r.width(), r.height()) for r in screens]))
                except ValueError: geometry = ()
            window = self.manual_window = ManualWindow(self, geometry, self.remember_manual_geometry)
            window.view.theme = self.theme
        window.show(); window.raise_(); window.activateWindow()
        window.view.focus_search()
        return window

    def remember_manual_geometry(self, geometry):
        geometry = [int(v) for v in geometry]  # A list, as the preferences file reads it back.
        if self.preferences.get('manual_geometry') == geometry: return
        self.preferences['manual_geometry'] = geometry
        if self.remember_recent: self.preferences_save.start()

    def pointer(self, kind, point):
        if self.system_monitor or self.video_monitor or self.immersive: return False
        point = np.array([point.x(),point.y()])
        if kind == 'hover':
            # Qt Quick repeats hover events while the cursor rests over a repainting item: only motion counts.
            last, self.hover_point = getattr(self, 'hover_point', None), point
            if last is None or np.any(last != point): self.reveal_handles()
            return False
        self.reveal_handles()
        if kind == 'release':
            self.drag = None
            if getattr(self, '_drag_sync_pending', False):
                self._drag_sync_pending = False
                self.sync()
            return True
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
                px = float(shape.position_x+delta[0]/pixels)
                py = float(shape.position_y-delta[1]/pixels)
                self.scene.change('position_x',px)
                self.scene.change('position_y',py)
                for key, val in (('position_x', px), ('position_y', py)):
                    w = self.parameters.get(key)
                    if w is not None:
                        with QSignalBlocker(w): w.setValue(val)
            else:
                sz = float(max(.05,shape.shape_size+(delta[0]-delta[1])/(2*pixels)))
                self.scene.change('shape_size',sz)
                w = self.parameters.get('shape_size')
                if w is not None:
                    with QSignalBlocker(w): w.setValue(sz)
            self._drag_sync_pending = True
        return True

    def stopped_signal(self, settings):
        """The 40 ms of song at the playhead that the stopped Composer shows.

        Render once and reuse it while nothing it depends on changes (audit P7): the audition
        project (by identity: edits, undo, solo and clip changes replace it), the playhead, the
        machine settings render_song reads (by value: output_settings builds a new object every
        frame) and decoded media, so a clip whose decode lands appears. Media are never decoded
        here (wait_media=False).
        """
        project = self.output_project()
        key = (self.playhead, settings.sample_rate, settings.route_mode, settings.corner_dwell_us,
               settings.shape_redraw_count, settings.switch_sweep_ms, audio_clips.media_generation())
        cached = getattr(self, '_stopped_signal', None)
        if cached is not None and cached[0] is project and cached[1] == key: return cached[2]
        times = self.playhead+np.arange(round(.04*settings.sample_rate))/settings.sample_rate
        signal = np.clip(render_song(times, project, settings, wait_media=False), -1, 1)
        signal.setflags(write=False)
        self._stopped_signal = (project, key, signal)
        return signal

    def lane_off_objects(self, motion_seconds):
        """Objects whose On/Off lane is off at the playhead, as the audio decides (audit B10).

        Composer evaluates the edited clip's baked lanes at its pose frame (render_song's clip
        time); Sandbox plays lanes unbaked (their first node), as posed_shapes does.
        """
        shapes = self.scene.settings.shapes
        if not any(name == 'on' for shape in shapes for name, _ in shape.lanes): return frozenset()
        if self.workspace != COMPOSER or self.scene is not self.clip_scene: return lanes_off(shapes, motion_seconds)
        project = self.output_project(); clip = project.clips[self.clip_index]
        key = (clip, project.bpm, project.tempo_changes)
        cached = getattr(self, '_lane_shapes', None)
        if cached is None or any(a is not b for a, b in zip(cached[0], key)):
            cached = self._lane_shapes = (key, clip_settings(clip, clip.settings, project).shapes)
        offset = float(project.seconds(clip.start-clip.time_offset)) if clip.local_time else 0.
        hz = clip.settings.drawing_hz
        return lanes_off(cached[1], math.floor(max(0., self.playhead-offset)*hz)/hz)

    def overlay(self, seconds,motion_seconds=None):
        scope = self.host.scope; w,h = scope.width(),scope.height()
        scale = .45*min(w,h)*self.scene.settings.drawing_gain
        strips = []; faint = []; handles = []; self.handles = []
        shown = self.handle_opacity()
        effective = self.audition_shapes()
        self.inspector.observe(seconds, seconds if motion_seconds is None else motion_seconds)
        if self.scene.mode != 'music':
            # Posed from the audition copies, so a transform solo is applied before the On/Off
            # lanes resolve and the outline draws the stack the audio plays (B2 review).
            moving=iter(posed_shapes(effective,seconds if motion_seconds is None else motion_seconds))
            off = self.lane_off_objects(seconds if motion_seconds is None else motion_seconds)
            for i,base in enumerate(self.scene.settings.shapes):
                shape=base if is_motion_guide(base) else next(moving)
                if (effective[i].muted or i in off) and not is_motion_guide(shape):
                    cache = getattr(self, '_muted_outline_cache', None)
                    if cache is None: cache = self._muted_outline_cache = {}
                    tris = muted_outline(cache, seconds, shape, w, h, scale)
                    if len(tris): faint.append(tris)
                if shape.solid==CUSTOM_OBJECT and shape.custom.guides:
                    from phosphor.engine.drawing_motion import segment_indices
                    from phosphor.engine.motion_path import guide_route
                    from phosphor.editor.builder_selection import dashed_lines
                    for g in shape.custom.guides:
                        if not g.enabled:continue
                        points,_=guide_route(shape.custom.subset(segment_indices(shape.custom,g.nodes)))
                        points=projected_solid(seconds,shape,vertices=np.column_stack((points,np.zeros(len(points)))))*shape.shape_size+[shape.position_x,shape.position_y]
                        for line in dashed_lines(points*[scale,-scale]+[w/2,h/2]):strips.append(stroke_triangles(line,.8))
                if is_motion_guide(shape):
                    points,_=world_motion_route(shape)
                    from phosphor.editor.builder_selection import dashed_lines
                    for line in dashed_lines(points*[scale,-scale]+[w/2,h/2]):strips.append(stroke_triangles(line,.8))
                center = np.array([w/2+shape.position_x*scale,h/2-shape.position_y*scale]); r = shape.shape_size*scale
                corner = center+[r,-r]
                ch = np.clip(center,[8,8],[max(8,w-8),max(8,h-8)])
                cr = np.clip(corner,[20,20],[max(20,w-20),max(20,h-20)])
                self.handles.append((i,ch,cr))  # Picking works while they are hidden.
                if shown <= 0: continue
                handles.append(_HANDLE_RING + ch.astype(np.float32))
                if i == self.scene.selected:
                    from phosphor.editor.builder_selection import dashed_lines
                    box = center+np.array([[-r,-r],[r,-r],[r,r],[-r,r],[-r,-r]])
                    # Clamped just outside the viewport: dashes for the visible part only, so a huge
                    # object costs no more than one around the whole view (Codex review, UI1).
                    box = np.clip(box,[-20,-20],[w+20,h+20])
                    handles.extend(stroke_triangles(line,.6) for line in dashed_lines(box))
                    handles.append(_CORNER_BOX + cr.astype(np.float32))
        p = self.theme.colors
        layers = [(np.concatenate(strips) if strips else np.empty((0,2)),QColor(p['accent']))]
        if faint:
            colour = QColor(p['accent']); colour.setAlphaF(.22)
            layers.append((faint[0] if len(faint) == 1 else np.concatenate(faint), colour))
        handle_colour = QColor(p['accent']); handle_colour.setAlphaF(HANDLE_DIM*shown)
        handle_layer = (handles[0] if len(handles) == 1 else np.concatenate(handles) if handles else np.empty((0,2)), handle_colour)
        if self.scene.shape is None: scope.overlays = layers+[handle_layer]+self.inspector.layers(w,h,scale,p); return
        pose_time = np.floor(seconds*self.scene.settings.drawing_hz)/self.scene.settings.drawing_hz
        try:  # Orientation key: where the stack sends unit axes from the origin (exact for linear steps).
            posed,_ = posed_object(pose_time,self.scene.shape,vertices=np.vstack((np.eye(3),np.zeros(3))),include_depth=True)
            axes = posed[:3]-posed[3]
            length = np.linalg.norm(axes,axis=1,keepdims=True); axes = np.where(length>1,axes/np.maximum(length,1e-12),axes)
        except ValueError: axes = projected_solid(pose_time,self.scene.shape,vertices=np.eye(3),include_depth=True)
        origin = np.array([65.,h-65.])
        for i in range(3):
            end = origin+axes[i,:2]*[39,-39]
            label = (end+[5,-5]).astype(np.float32)
            pieces = (stroke_triangles([origin,end],2), _AXIS_GLYPHS[i] + label)
            colour = QColor(self.theme.colors[('axis_x','axis_y','axis_z')[i]]); colour.setAlphaF(AXIS_OPACITY)
            layers.append((np.concatenate(pieces),colour))
        layers.append(handle_layer)  # Above the axes (UI1: its own layer, so it fades alone).
        scope.overlays = layers+self.inspector.layers(w,h,scale,p)  # Above the handles; none while idle.

    @Slot()
    def swapped(self):
        # Explicit queued QObject slot: frameSwapped may originate on the render thread.
        if self.views_released or self.sender() is not self.host.view: return
        stamp = time.perf_counter(); self.swap_times.append(stamp)
        if self.frame_fresh: self.frame_fresh = False; self.content_swap_times.append(stamp)
        if self.ready_file:
            self.ready_file.write_text('ready',encoding='utf-8'); self.ready_file = None
        # The housekeeping timer is the app clock: stopping it (smokes driving frames by
        # hand) also stops the vsync-driven display.
        if self.preferences['display_rate'] == 'Match monitor' and not self.display_pending and self.timer.isActive():
            self.display_pending = True
            QTimer.singleShot(0, self.queued_display_frame)

    def queued_display_frame(self):
        if not self.display_pending: return
        if self.preferences['display_rate'] != 'Match monitor' or not self.timer.isActive():
            self.display_pending = False; return
        if self.defer_for_audio(time.perf_counter()):
            self.defer_timer.start(AUDIO_DEFER_MS); return
        self.display_pending = False
        self.display_frame()

    def paced_frame(self):
        """'60 Hz': display_frame at each 1/60 s deadline on a precise single-shot timer (P7c, H4)."""
        if self.preferences['display_rate'] != '60 Hz' or not self.timer.isActive() or self.views_released:
            self.next_frame_time = None; return
        now = time.perf_counter()
        if self.next_frame_time is None: self.next_frame_time = now
        if now < self.next_frame_time-.001: self.schedule_frame(); return  # Woke early: whole-millisecond timer.
        if self.defer_for_audio(now): self.frame_timer.start(AUDIO_DEFER_MS); return
        if self.frame_fresh and now-self.last_display_time < 1.5/60:
            # The last frame hasn't reached the screen yet: a new one now would replace it unseen.
            self.frame_timer.start(1); return
        self.display_frame()
        self.next_frame_time += 1/60
        if time.perf_counter() > self.next_frame_time+1/60: self.next_frame_time = time.perf_counter()  # Stalled: resync.
        self.schedule_frame()

    def schedule_frame(self):
        if self.next_frame_time is None: self.next_frame_time = time.perf_counter()
        self.frame_timer.start(max(0, int((self.next_frame_time-time.perf_counter())*1000)))

    def defer_for_audio(self, now):
        """True while playback's device buffer runs low: the next display frame waits a moment so
        the audio thread gets the GIL first (P7c, H1). Never below 30 display frames a second."""
        if not PACE_FOR_AUDIO or now-self.last_display_time >= 1/MIN_PACED_FPS: return False
        queued = self.audio.buffered_seconds()
        if queued is None or queued >= AUDIO_LOW_WATER*self.audio.output_latency: return False
        self.deferred_frames += 1
        return True

    def backend_info(self):
        rhi = self.host.view.rhi()
        self.backend = str(self.host.view.rendererInterface().graphicsApi()).split('.')[-1]
        if rhi:
            info = rhi.driverInfo()  # Retain the struct owning the QByteArray view.
            self.backend += ' / '+bytes(info.deviceName).decode(errors='replace')
        self.backend_label.setText(self.backend)

    def tick(self):
        """Synchronous housekeeping + display entry point retained for smoke harnesses."""
        self.housekeeping(display=False)
        self.display_frame()

    def housekeeping(self, display=True):
        now = time.perf_counter()
        self.watch_scene_history()  # Catches a Sandbox edit made without sync() (an identity test).
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
                    blocker = QSignalBlocker(self.devices); self.devices.clear(); self.devices.addItem(NO_MIDI_DEVICE,None)
                    for name in value: self.devices.addItem(name,name)
                    self.devices.setCurrentIndex(max(0,self.devices.findData(self.scene.device or None)))
                    # Reconnect to the scene's last device when it is present.
                    if self.scene.settings.source == MIDI_SOURCE and self.scene.device in value: self.connect_midi()
                    elif self.scene.device and self.scene.device not in value: self.audio.midi.notice = f'{self.scene.device} is not connected.'
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
        self.drain_note_recording()
        if self.midi_recorder is not None and not self.audio.running: self.stop_song()
        midi = self.audio.midi
        result = midi.poll_port_check()
        if result and result[0] == midi.port_name and result[2] == midi.port_epoch and result[1]:
            self.disconnect_midi(); midi.notice = result[1]
        if midi.port_name and now-self.last_midi > 2: self.last_midi = now; midi.check_port_async()
        if now-self.last_status > .2:
            self.last_status = now
            play_text = 'Stop' if self.audio.running and not self.audio.cycle and not self.audio.preview else 'Play'
            exporting_video = self.exporting and hasattr(self, 'video_export_job') and not self.video_export_job.closed
            status_text = (('EXPORTING VIDEO…  |  ' if exporting_video else 'EXPORTING AUDIO…  |  ') if self.exporting else '')+('Cycle audition' if self.audio.cycle_running else 'Previewing a file' if self.audio.preview_running else 'Playing' if self.audio.running else 'Audio stopped')+f'  |  {self.audio.sample_rate/1000:g} kHz stereo XY  |  Underruns: {self.audio.underruns}'+('  |  '+self.audio.notice if self.audio.notice and self.audio.running else '')+('  |  '+self.audio.error if self.audio.error else '')
            if self.audio.running and self.audio.live:
                status_text += f'  |  MIDI buffer: {self.audio.block_frames} frames · device latency: {self.audio.output_latency*1000:.1f} ms'+(f' · {self.audio.output_mode}' if self.audio.output_mode else '')
            if getattr(self,'redraw_notice',''): status_text += '  |  '+self.redraw_notice
            if self.workspace == COMPOSER: status_text = 'COMPOSER  |  '+status_text
            midi_text = f'{"RECORDING — " if midi.capture else ""}{midi.port_name or "MIDI not connected"}\nVoices: {midi.voice_count}/8\n{midi.notice}\n{midi.last_message}'
            if self.midi_status.text() != midi_text: self.midi_status.setText(midi_text)
            if self.system_monitor:
                c = self.capture
                play_text = 'Stop' if c.running else 'Start'
                self.capture_devices.setEnabled(not c.running)
                self.capture_refresh.setEnabled(not c.running and not self.capture_scanning)
                waiting = c.ready and (c.clock()-c.last_sample > c.STALE_SECONDS or c.peak < 1e-6)
                state = 'Waiting for audio / silence' if waiting else 'Visualizing' if c.ready else 'Starting capture…' if c.running else 'Capture stopped'
                details = f'{c.device_name}\n{c.sample_rate} Hz · {c.channels} channel(s) · {c.frames} captured frames\nRaw input peak: {0. if waiting else c.peak:.4f}\nCapture status events: {c.status_events}' if c.device_name else ''
                capture_text = c.error or self.capture_notice or details
                if self.capture_status.text() != capture_text: self.capture_status.setText(capture_text)
                status_text = f'SYSTEM AUDIO · VISUALIZATION ONLY  |  {state}'+('  |  '+c.error if c.error else '')
            if self.video_monitor:
                v = self.video
                playing = v is not None and v.playing
                play_text = 'Pause' if playing else 'Play'
                if v is None or v.player.source().isEmpty(): state = 'No video loaded'
                else:
                    position, duration = v.player.position(), v.player.duration()
                    size = v.size_text()
                    state = ('Playing' if playing else 'Paused')+f'  |  {clock(position)} / {clock(duration)}'+(f'  |  {size}' if size else '')
                status_text = f'VIDEO · {self.video_panel.scan_lines}-LINE RASTER  |  {state}'+('  |  '+v.error if v is not None and v.error else '')
            if self.play_button.text() != play_text: self.play_button.setText(play_text)
            if self.status.text() != status_text: self.status.setText(status_text)
            # Presented frames (swaps), not preparation starts: a GUI thread blocked in paint still
            # starts frames on time, but the scope stops updating (audit B11).
            recent = [stamp for stamp in self.content_swap_times if now-stamp <= 1.]
            fps = (len(recent)-1)/(recent[-1]-recent[0]) if len(recent) > 1 else 0.
            backend_text = f'{fps:.0f} fps · '+self.backend
            if self.backend_label.text() != backend_text: self.backend_label.setText(backend_text)
            self.refresh_bar_icons()
        if display and self.preferences['display_rate'] == '60 Hz':
            if not self.frame_timer.isActive(): self.schedule_frame()
        elif display and now-self.last_display_time >= .1:
            self.display_frame()

    def display_frame(self):
        if self.views_released or self.display_running or self.isMinimized(): return
        self.display_pending = False
        self.display_running = True
        try:
            now = time.perf_counter()
            self.last_display_time = now
            self.frame_count += 1; self.frame_times.append(now)
            self._display_frame(now)
            self.frame_fresh = True
        finally:
            self.display_running = False

    def _display_frame(self, now):
        flush_sliders()  # At most one edit per dragged slider per frame.
        if getattr(self, '_drag_sync_pending', False):
            self._drag_sync_pending = False
            self.sync()
        start = time.perf_counter(); settings = self.output_settings()
        self.update_cycle(settings)
        if settings.sample_rate != self.audio.sample_rate:
            settings = replace(settings, sample_rate=self.audio.sample_rate)
        sweeping = self.sweep_mode and not self.video_monitor
        sweep_samples = None  # New samples for the time view of a generated signal.
        seconds = now-self.started
        # Samples use at most 250 ms after a stall; the phosphor decays the whole of it.
        decay_elapsed = max(0.,start-getattr(self,'last_phosphor_time',start)); self.last_phosphor_time = start
        elapsed = min(.25,decay_elapsed)
        new_frames = elapsed*settings.sample_rate
        if not self.audio.running:settings=replace(settings,motion_time_offset=self.started-self.motion_epoch)
        if self.system_monitor:
            signal = self.capture.samples(round(.04*self.capture.sample_rate))
            new_frames = elapsed*self.capture.sample_rate
        elif self.audio.cycle_running:
            signal = self.audio.cycle_samples
            if signal is None: signal = np.zeros((8, 2), dtype='float32')
            self.last_playback_frame = None
        elif self.audio.running:
            # The viewport always shows 40 ms of signal, at any output rate.
            window = round(.04*settings.sample_rate)
            history, playback_frame = self.audio.playback_snapshot(max(window,round(.25*settings.sample_rate)) if sweeping else window)
            signal = history[-window:]
            seconds = playback_frame/settings.sample_rate
            new_frames = playback_frame-self.last_playback_frame if self.last_playback_frame is not None else 0
            if sweeping: sweep_samples = history[len(history)-min(len(history),max(0,int(new_frames))):]
            self.playback_ticks += 1
            if playback_frame != self.last_playback_frame:
                self.fresh_playback_windows += 1
            self.last_playback_frame = playback_frame
        else:
            signal = None
            self.last_playback_frame = None
        if self.workspace == COMPOSER:
            if self.audio.running and self.audio.composer is not None:
                pre_roll = round(self.audio.count_in_seconds * settings.sample_rate) / settings.sample_rate
                self.playhead = float(song_seconds(self.project,self.play_start,max(0.,seconds-pre_roll)))
                # The song plays on by its effects' ring-out (AW3), as a whole-song export does.
                if not self.project.loop and self.playhead >= float(self.project.seconds(self.project.end_beats)) + self.song_tail(): self.stop_song()
            elif signal is None:
                # Stopped: show the song at the playhead.
                signal = self.stopped_signal(settings)
            clip = self.project.clips[self.clip_index]
            seconds = self.playhead-(float(self.project.seconds(clip.start)) if clip.local_time else 0.)  # The edited clip's own time.
            beats = self.playhead_beats; view = self.composer_panel.timeline
            media = audio_clips.media_generation()
            if media != getattr(self, '_media_seen', None):
                self._media_seen = media; view.update()  # A decode landed: draw its waveform.
            playing = self.audio.running and self.audio.composer is not None
            if abs(view.playhead-beats) > 1e-9:
                view.set_playhead(beats, follow=playing); self.composer_panel.position.setText(position_text(self.project,beats))
            if playing:
                view.set_track_meters(self.audio.take_track_peaks())
                if view.set_meter(self.audio.take_output_peak()) and not self.clip_warned:
                    self.clip_warned = True
                    self.flash('The output clipped: the summed mix passed full scale. Lower the audio clip gains (right-click › Gain…).')
            elif view.meter_level or any(view.track_meters): view.set_meter(0.); view.set_track_meters(())
            if self.composer_panel.play.text() != ('Stop' if playing else 'Play'): self.composer_panel.play.setText('Stop' if playing else 'Play')
        if self.video_monitor:
            signal = np.zeros((2,2))  # The raster replaces the beam path.
            self.video_panel.refresh()
        if self.scene.shape is not None and self.scene.shape.solid == RECURRENCE_OBJECT:
            composer = self.workspace == COMPOSER
            clip = self.project.clips[self.clip_index] if composer else None
            if composer:
                origin = float(self.project.seconds(clip.start-clip.time_offset)) if clip.local_time else 0.
                warning_seconds = max(0., self.playhead-origin)
            else:
                warning_seconds = seconds
            pose_seconds = math.floor(max(0., warning_seconds)*settings.drawing_hz)/settings.drawing_hz
            rec_key = (self.workspace, clip, self.scene.selected, pose_seconds, settings.tempo, self.scene.shape.recurrence)
            if rec_key != getattr(self, '_recurrence_warning_key', None):
                self._recurrence_warning_key = rec_key
                warning_settings = clip_settings(clip, settings, self.project) if composer else settings
                self.recurrence_panel.update_warning(pose_seconds, warning_settings.tempo,
                    warning_settings.shapes[self.scene.selected].recurrence)
        stopped_song = self.workspace == COMPOSER and (not self.audio.running or self.audio.cycle) and not self.system_monitor and not self.video_monitor
        feed = None
        if signal is None:
            times = seconds+np.arange(round(.04*settings.sample_rate))/settings.sample_rate
            signal = select_signal(*render(times,settings),settings,self.scene.mode)
            if sweeping:
                # Stopped Sandbox: render exactly the samples since the last tick, so the time view
                # sees one continuous signal (at most 0.25 s after a stall).
                rate = settings.sample_rate; end = seconds+.04; last = self.sweep_feed
                begin = last if isinstance(last,float) and end-.25 <= last <= end else end-min(elapsed,.25)
                count = int(round((end-begin)*rate))
                sweep_samples = select_signal(*render(begin+np.arange(count)/rate,settings),settings,self.scene.mode) if count > 0 else np.empty((0,2))
                feed = begin+count/rate
        elif sweeping and stopped_song:
            # Stopped Composer: the 40 ms at the playhead, repeated as a steady signal.
            count = int(round(elapsed*settings.sample_rate)); first = self.sweep_feed if isinstance(self.sweep_feed,int) else 0
            sweep_samples = np.take(signal,(first+np.arange(count))%len(signal),axis=0) if count > 0 and len(signal) else np.empty((0,2))
            feed = (first+count)%max(1,len(signal))
        self.sweep_feed = feed
        if self.building:
            preview = self.builder.preview.scope
            trace = interpolate_trace(signal, self.preferences['trace_interpolation'])
            preview.signal = trace if self.video_monitor else trace*self.visualization_gain
            preview.colors = PALETTES[self.preferences['palette']]
            preview.glow = self.preferences['crt_glow']
            preview.scanlines = self.preferences['crt_scanlines'] and not self.video_monitor
            preview.divisions = self.preferences['grid_divisions']
            preview.show_grid = self.preferences['show_grid'] and not self.video_monitor
            preview.grid_style = self.preferences['grid_style']
            preview.graphics_quality = self.preferences['graphics_quality']
            preview.overlays = []
            for key in VISUAL_DEFAULTS: setattr(preview, key, self.preferences[key])
            bg = QColor(preview.colors['background'])
            if self.builder.preview.view.color() != bg: self.builder.preview.view.setColor(bg)
            preview.update()
            btn_text = '■ STOP AUDIO' if self.audio.running else '▶ PLAY AUDIO'
            if self.builder.play_button.text() != btn_text: self.builder.play_button.setText(btn_text)
            self.prep_ms.append((time.perf_counter()-start)*1000)
            return
        scope = self.host.scope
        phosphor_on = self.host.phosphor is not None and self.preferences['phosphor'] != PHOSPHOR_OFF
        desk_route = (self.desk3d_host.controller.route_signal
                      if self.desk3d and self.desk3d_host is not None and not self.video_monitor else None)
        if phosphor_on and not sweeping and not self.video_monitor and not self.system_monitor:
            # Phosphor mode deposits only the newest tail; skip full-buffer Catmull-Rom interpolation.
            sig_scaled = signal if desk_route is None else desk_route(signal)
            scope.signal = sig_scaled if self.visualization_gain == 1. else sig_scaled * self.visualization_gain
            raw_signal = sig_scaled
        else:
            trace = interpolate_trace(signal,self.preferences['trace_interpolation'])
            if self.video_monitor:
                scope.signal = trace
            else:
                trace_scaled = trace if desk_route is None else desk_route(trace)
                scope.signal = trace_scaled * self.visualization_gain  # Display gain, every input but video.
            raw_signal = None
        if sweeping and self.system_monitor: traces = self.sweep_traces(scope,*self.captured_sweep_samples())
        elif sweeping: traces = self.sweep_traces(scope,sweep_samples if sweep_samples is not None else np.empty((0,2)),settings.sample_rate)
        else: traces = None
        if not (sweeping and self.system_monitor): self.sweep_frame = None
        # The line trace draws the whole sweep buffer; the phosphor deposits only new paths.
        scope.polylines = self.sweep.display_polylines(scope.width(),scope.height(),self.visualization_gain,self.sweep_separation) if sweeping and not phosphor_on else None
        scope.colors = PALETTES[self.preferences['palette']]
        scope.glow = self.preferences['crt_glow']; scope.scanlines = self.preferences['crt_scanlines'] and not self.video_monitor; scope.divisions = self.preferences['grid_divisions']; scope.show_grid = self.preferences['show_grid'] and not self.video_monitor; scope.grid_style = self.preferences['grid_style']; scope.graphics_quality = self.preferences['graphics_quality']
        for key in VISUAL_DEFAULTS: setattr(scope,key,self.preferences[key])
        scope.history_key = (self.system_monitor,self.video_monitor,sweeping,self.audio.running,self.scene.settings.source,self.scene.mode,self.capture.device_name,self.workspace)
        self.update_phosphor(new_frames,elapsed,traces,raw_signal=raw_signal,gain=self.visualization_gain,decay_elapsed=decay_elapsed)
        if self.system_monitor or self.video_monitor or self.immersive or self.popped:
            scope.overlays = []; self.handles = []
        else: self.overlay(seconds,seconds+settings.motion_time_offset)
        scope.update()
        self.prep_ms.append((time.perf_counter()-start)*1000)


# Display pacing for audio (P7c, hot spot H1): while a blocking stream's buffer is under
# AUDIO_LOW_WATER of its latency, display frames wait AUDIO_DEFER_MS at a time, down to
# MIN_PACED_FPS. measure_pacing --no-pacing turns it off to compare.
PACE_FOR_AUDIO = True
AUDIO_LOW_WATER = .5
AUDIO_DEFER_MS = 2
MIN_PACED_FPS = 30


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
    parser.add_argument('--skip-recovery',action='store_true',help='Skip the startup recovery offer (automation).')
    parser.add_argument('--workspace',choices=WORKSPACES,help='Start in Composer or Sandbox (default: the last one used); not remembered.')
    parser.add_argument('--diagnose-playback',action='store_true',help='Run a 13-second stopped/play/stopped native performance comparison, then exit.')
    parser.add_argument('--system-audio',action='store_true',help='Select visualization-only native system-audio capture; --play starts capture.')
    options = parser.parse_args()
    if options.workspace is None and (options.exercise or options.diagnose_playback or options.system_audio): options.workspace = SANDBOX  # Sandbox tools.
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
    window = Window(options); window.remember_recent = True
    window.transform_panel.set_presets_path(TRANSFORM_PRESETS_PATH)  # Saved transforms (T6): user files on.
    from phosphor.storage.browser_places import BROWSER_PATH
    window.browser_places_path = BROWSER_PATH  # Browser favourites (AW4): user files on.
    window.init_shortcuts(SHORTCUT_BINDINGS_PATH)  # Keyboard shortcuts (UI2): per machine, user files on.
    if options.scene: window.remember_file('scene',options.scene)
    window.show(); window.restore_monitor_window()
    if crash_log.wanted():  # Breadcrumbs: screen and pixel-ratio changes precede backing-store rebuilds.
        describe = lambda screen: f'{screen.name()} {screen.size().width()}x{screen.size().height()} dpr {screen.devicePixelRatio():g}'
        crash_log.note('screens: ' + '; '.join(describe(screen) for screen in app.screens()))
        handle = window.windowHandle()
        if handle is not None: handle.screenChanged.connect(lambda screen: crash_log.note('window moved to screen ' + describe(screen)))
    window.autosave_enabled = not (options.seconds is not None or options.exercise
        or os.environ.get('QT_QPA_PLATFORM') == 'offscreen')
    if window.autosave_enabled:
        try: autosave.write_lock(window.autosave_dir)
        except OSError as error: window.flash(f'Could not create autosave lock: {error}')
        if not options.skip_recovery: QTimer.singleShot(0, window.offer_autosave)
    settle_garbage()
    diagnostics = None
    if options.diagnose_playback:
        from phosphor.audio.playback_diagnostics import PlaybackDiagnostics
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
        window.monitor.setCurrentIndex(MONITOR_SYSTEM); window.monitor_changed(MONITOR_SYSTEM)
    if options.play: QTimer.singleShot(1000,window.play)
    if options.exercise:
        def exercise():
            i = int(time.perf_counter()-window.started)
            if i == 1: window.add_object('Cube')
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
        from phosphor.audio.playback_diagnostics import pacing
        report = dict(version=VERSION,backend=window.backend,frames=window.frame_count,
            frame_pacing=pacing(window.swap_times,'swaps of the active view (frames presented)'),
            preparation_pacing=pacing(window.frame_times,'display_frame starts (samples prepared)'),audio_frames=window.audio.frames,
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
    app.aboutToQuit.connect(window.finish_autosave)
    return app.exec()


if __name__ == '__main__': sys.exit(main())
