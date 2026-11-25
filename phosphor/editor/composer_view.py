"""Composer's timeline panel: transport, ruler, Scene track and note roll.

The Notes menu provides chord drawing, scale snapping and selection editing tools.
Alt/Option-drag copies notes; Control-wheel over pitch names zooms pitch rows.
The panel edits an immutable composition.Project and emits each new project; the
window owns the project, the playhead and playback. Times are in beats.

Mouse:
- Ruler: click or drag to move the playhead; Shift-drag sets the loop.
- Audio tracks (AW1, under the Scene track): each has a header (name, M, S, gain ×, meter and its
  lamp); click a header to select the track (Shift: a range, Ctrl/Cmd: add or remove), drag it up or
  down to reorder, double-click its name to rename, drag its gain up or down (double-click: Gain…),
  right-click for Rename, Gain, Mute, Solo, Collapse/Expand, Add, Duplicate, Copy, Paste, Move and
  Delete. Copy/Paste/Delete act on the selected tracks (with their clips and gain lanes) after a
  header click. The "+ Add audio track" row under the last track adds one (drop files on it for a
  new track). Drop audio files on a track, or double-click it (or right-click ›
  Add audio clip here…); drag a clip onto another track to move it there. Drag a clip to move it, its ends to trim it (the audio stays put in song
  time), the squares at its top corners to set fade in and out; Alt/Option-drag copies it.
  Right-click: Split at playhead, Duplicate, Gain…, Routing, Mute clip, Solo clip, Delete; a missing
  clip (hatched, with a Missing badge) has Locate file…. Solo plays only the soloed audio clips and
  silences the scenes (not saved). The meter in the ruler's gutter shows the
  summed output; its lamp latches red when the output clips (click to reset); each track's meter
  and lamp do the same for that track. The Gain button (left of "+ Add audio track")
  shows a gain lane under every expanded track: click to add a node, drag nodes (Shift: gain only, Alt:
  time only), right-click a node for Delete or Hold. Audio times are seconds (clips never stretch).
- Scene track: click a clip to edit its scene; drag to move, drag either end (the thick
  bars) to lengthen or shorten it; the left end keeps the clip's end and its content in
  place (lanes shift and the scene clock t continues, through Clip.time_offset); double-click empty space to add a clip; right-click › Delete.
- Notes: click empty space to draw a note (drag to set its length); drag a note to
  move the selection in time and pitch, its right edge to resize; Shift-drag empty
  space adds a box selection. Click selects one, Shift-click toggles; arrows move
  the selection (Shift+Up/Down: octave), Standard Select All selects the whole roll.
- Wheel scrolls pitch; Shift+wheel scrolls time; Ctrl/Cmd+wheel zooms.

Two icon buttons at the left of the roll switch between the note roll (♪) and the
transform roll (◆). The transform roll shows the selected clip's automation: one block
per object › transform step (and Placement, Path, Equation, Mix), each automated number a
gain line (−2…2; the dashed line is gain 1) through nodes. The block's legend selects
the line being edited (a dim name is not automated yet: click it to automate).
- Click a block to add a node on the selected line; drag a node to move it (Shift: gain
  only, Alt: time only); right-click a node to delete it or set its segment to Linear or
  Hold; wheel scrolls the blocks.
- Drag a line segment (not a node) to move it straight up or down; step nodes keep the
  rest of the lane in place (engine/lane_edit.py drag_segment).
- On/Off lanes (Objects or Transform list › right-click › Automate On/Off) are their own
  blocks, drawn as solid (on) and outlined (off) runs: drag across beats to paint the
  opposite of the state under the press (snapped to the grid), click a run to flip it.
  They have no nodes. Right-click: Turn on/off here, Turn all on, Remove On/Off automation.
- Reference line: each lane's first node's gain. It is drawn dotted once any node leaves
  it; nodes and segment ends snap to it (hold Ctrl/Cmd to place freely). Right-click it
  to set a new reference gain: nodes on it move with it, nodes placed elsewhere stay.

Copy, Cut, Paste and Delete (standard shortcuts, and the clip and node right-click menus)
act on what was clicked last: notes (Shift-click adds or removes notes), the selected
clip, or automation nodes (Shift-click adds nodes). Right-click a clip › Mute clip silences it
like a gap (Clip.muted; drawn dim and dashed). Paste puts notes and clips at the
playhead and nodes into the last edited lane at the playhead. The clipboard holds
engine/clipboard.py JSON under its own MIME type, so phosphor windows share it.
"""
from dataclasses import dataclass, replace
from phosphor.display import theme as themes
from phosphor.editor.shortcuts import key_sequences
from phosphor.editor.zoom_fit import zoom_fit, SCALE_RANGE
from phosphor.editor import wheel_mapping, scroll_mapping
import math
import sys

from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QTimer, QLineF
from PySide6.QtCore import QMimeData, QObject, QEvent
from PySide6.QtGui import QPainter, QBrush, QColor, QPen, QFont, QFontMetrics, QKeySequence, QAction, QActionGroup, QIcon, QPixmap, QInputDevice
from PySide6.QtWidgets import (QApplication, QInputDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QDoubleSpinBox,
                               QCheckBox, QToolButton, QMenu, QSizePolicy, QDialog, QDialogButtonBox, QSpinBox, QFormLayout)
from phosphor.engine.tempo_map import Meter
from phosphor.engine.composition import validate_project

from phosphor.engine.composition import (Note, BEATS_PER_BAR, PITCH_RANGE, BPM_RANGE, DEFAULT_CLIP_BEATS, MAX_BEATS,
                                         add_clip, remove_clip, fit_clip, move_clips, with_clip, split_clip, duplicate_clip)
from phosphor.audio.score_source import note_name
from phosphor.engine.automation import GAIN_RANGE
from phosphor.engine import clipboard, audio_tracks
from phosphor.engine import lane_edit
from phosphor.engine.note_edit import move_notes
from phosphor.engine import note_tools
from phosphor.engine.chord_names import chord_name, sounding_pitches
from phosphor.engine.composition import chord_voices
from phosphor.engine.composition import add_marker, move_marker, rename_marker, delete_marker
from phosphor.engine.lane_access import shift_clip_lanes, blocks, block_of, get_points, set_points, label as lane_label, static_value, default_points, is_switch, is_parked
from phosphor.engine import switch_lane
from phosphor.editor.paint_guard import guarded_paint
from phosphor.editor.lane_strip import LaneStrip, LaneItem, BARS, BLOCKS, LINE, LANE_HEIGHT
from phosphor.engine import audio_edit
from phosphor.engine.audio_clips import AUDIO_FILE_TYPES, ROUTINGS, LANE_CLOCK, waveform, source_end, audio_clip_end_beats, media_generation
from phosphor.engine.automation import gain_at
import numpy as np
from phosphor.engine.track_mix import MAX_DELAY_MS, to_lane, from_lane
from phosphor.engine import track_effects
from phosphor.editor.track_effects_window import value_text as effect_value_text


def is_effect_lane(lane): return isinstance(lane, tuple)


def effect_kinds(track): return tuple(e.kind for e in track.effects)


def effect_field(track, lane):
    """The registry Field an effect lane ('fx', e, name) automates."""
    effect = track.effects[lane[1]]
    return next(f for f in track_effects.EFFECTS[effect.kind].fields if f.name == lane[2])


def lane_kind(track, lane):
    """The value a track lane sets: 'pan', 'phase'/'delay' by the track's phase mode, or 'fx'."""
    if is_effect_lane(lane): return 'fx'
    return 'pan' if lane == 'pan' else ('phase' if track.phase_mode == 'true' else 'delay')


def track_lane_points(track, lane):
    if is_effect_lane(lane): return track_effects.effect_lane(track.effects[lane[1]], lane[2])
    return track.pan_points if lane == 'pan' else track.phase_points


def track_static(track, lane):
    if is_effect_lane(lane): return track_effects.effect_value(track.effects[lane[1]], lane[2])
    kind = lane_kind(track, lane)
    return track.pan if kind == 'pan' else track.phase if kind == 'phase' else track.delay_ms


def lane_to(track, lane, value):
    if is_effect_lane(lane): return track_effects.to_lane(effect_field(track, lane), value)
    return to_lane(lane_kind(track, lane), value)


def lane_from(track, lane, v):
    if is_effect_lane(lane): return track_effects.from_lane(effect_field(track, lane), v)
    return from_lane(lane_kind(track, lane), v)


def track_value(track, lane, seconds):
    """What the lane plays at song seconds: its nodes when it has any, else the header value."""
    points = track_lane_points(track, lane)
    if not points: return track_static(track, lane)
    return lane_from(track, lane, float(gain_at(points, LANE_CLOCK, float(seconds))))


def track_lane_label(track, lane):
    """The lane's name in the gutter: Pan, Phase, Delay or the effect parameter (e.g. 'Cutoff')."""
    if is_effect_lane(lane): return effect_field(track, lane).label
    return 'Delay' if lane == 'phase' and track.phase_mode == 'delay' else AUDIO_LANE_LABELS[lane]


def lane_value_text(track, lane, value):
    if is_effect_lane(lane):
        effect = track.effects[lane[1]]
        return f'{track_effects.EFFECTS[effect.kind].label} {lane[1] + 1} {effect_field(track, lane).label.lower()} {effect_value_text(effect_field(track, lane), value)}'
    return LANE_VALUE_TEXT[lane_kind(track, lane)](value)


def set_any_lane(project, t, lane, points):
    """Set a track lane: pan or phase (audio_tracks.set_lane) or an effect parameter's lane."""
    if not is_effect_lane(lane): return audio_tracks.set_lane(project, t, lane, points)
    track = project.audio_tracks[t]; effects = list(track.effects)
    effects[lane[1]] = track_effects.with_lane(effects[lane[1]], lane[2], tuple(points))
    return audio_tracks.set_effects(project, t, tuple(effects), track.effects_bypass)


def pan_label(pan):
    if abs(pan) < 5e-3: return 'C'
    return f'{"L" if pan < 0 else "R"}{round(abs(pan) * 100)}'


LANE_VALUE_TEXT = {'pan': lambda v: 'pan ' + pan_label(v), 'phase': lambda v: f'phase {v:+.0f}°',
                   'delay': lambda v: f'delay {v:+.2f} ms'}


def pan_text(track):
    return pan_label(track.pan) + ('~' if track.pan_points else '')


def phase_text(track):
    automated = '~' if track.phase_points else ''
    if track.phase_mode == 'true':
        return (f'{track.phase:+.0f}°' if track.phase else '0°') + automated
    return (f'{track.delay_ms:+.1f}ms' if track.delay_ms else '0ms') + automated


def add_track_node(points, seconds, value):
    """Track lane points with a node at (song seconds, 0..1 value), and its index."""
    seconds, value = max(0., float(seconds)), min(1., max(0., float(value)))
    nodes = list(points)
    same = next((i for i, p in enumerate(nodes) if abs(p[0] - seconds) < 1e-6), None)
    if same is not None:
        nodes[same] = (nodes[same][0], value, nodes[same][2]); return tuple(nodes), same
    shape = 'linear'
    for p in nodes:
        if p[0] <= seconds: shape = p[2]
    nodes.append((seconds, value, shape)); nodes.sort(key=lambda p: p[0])
    return tuple(nodes), next(i for i, p in enumerate(nodes) if p[0] == seconds and p[1] == value)

SNAPS = {'Bar': 4., '1/4': 1., '1/8': .5, '1/16': .25, 'Off': 0.}
GUTTER, TRACK = 64, 34
RULER = themes.theme_for().metrics.ruler_height  # Classic reference used by input smokes.
FLAG_HEIGHT = themes.theme_for().metrics.ruler_flag_height  # Separate tempo/meter rows, above markers and bar labels.
EDGE = 6  # Pixels from a clip's ends or a note's right edge that resize it.
HANDLE = 4  # Width of the grab bars drawn at both ends of a clip.
NOTE_ROLL, TRANSFORM_ROLL = 'notes', 'transforms'
BLOCK, LEGEND, NODE = 78, 16, 5  # Default block height, legend row height, node half size (pixels).
BLOCK_RANGE, BLOCK_STEP, ROW_RANGE = (40, 240), 10, (5, 24)  # Cmd/Ctrl + wheel limits over the roll.
# Trackpad (UI2): pixels of finger travel per wheel-notch equivalent for zoom and row height,
# angle-delta pixels for a touchpad that sends no pixel deltas, and row steps per doubling pinch.
TRACKPAD_NOTCH_PIXELS, TRACKPAD_ANGLE_PIXELS, PINCH_ROW_STEPS = 40., 1 / 3, 8.
LANES_TOP = 30  # Space under the Scenes track for the roll buttons.
SNAP_PIXELS, SEGMENT_PIXELS = 6, 10  # Snap distance to the reference line; line hit distance (click adds, drag moves).
CLICK_SLOP = 3  # Pixels a press may move and still count as a click.
COMBO_CHROME = 48  # Stylesheet padding (2×4) and border (2), the drop-down arrow (about 26) and 12 px of room.
# Classic's lane and clip colours (ST1: painting reads them from the current Theme).
LANE_COLORS = themes.CLASSIC_LANES
CLIP_COLORS = themes.CLASSIC_CLIPS
AUDIO_TRACK, GAIN_LANE = 56, 48  # Audio track row (name; M S gain; pan phase; meter) and its optional lane (pixels).
AUDIO_LANES = ('gain', 'pan', 'phase')  # What the lane under each track shows (roll preference roll_audio_lane).
AUDIO_LANE_LABELS = {'gain': 'Gain', 'pan': 'Pan', 'phase': 'Phase'}
TRACK_PAN_PIXELS, TRACK_PHASE_PIXELS, TRACK_DELAY_PIXELS = .01, 1., .1  # Per pixel of a header drag.
COLLAPSED_TRACK, ADD_TRACK_ROW = 18, 18  # A collapsed audio track; the "+ Add audio track" row (AW1).
TRACK_GAIN_PIXELS = .01  # Track gain change per pixel of a header gain drag (Shift: a fifth).
MIN_ROLL = 48 + LANE_HEIGHT  # Room kept for the roll (its buttons and the velocity lane); more audio tracks scroll
                             # (wheel or two fingers over them), or drag the timeline's top edge for more room.
FADE_HANDLE = 5  # Half width of the fade squares at an audio clip's top corners.
METER_FLOOR = -48.  # dB at the meter's left end.
CLIP_RED = themes.CLASSIC_FIXED['clip_red']
ROUTING_LABELS = {'stereo': 'Stereo (left → X, right → Y)', 'mono': 'Mono → X and Y',
                  'x': 'Mono → X only', 'y': 'Mono → Y only'}
ROLL_TIPS = {'notes': 'Note roll', 'transforms': "Transform roll: the selected clip's automation"}


@dataclass(frozen=True)
class TrackRow:
    """Where an audio track is drawn: its index, the AudioTrack (None for the empty row of a
    project without tracks), its row's top and height, and its gain lane's top (None: hidden)."""
    index: int
    track: object
    top: float
    height: float
    lane: float | None


def track_layout(project):
    """What identifies the tracks by index: their names and which sounds (file and offset) each holds.
    When it changes, indices kept for tracks (selection, solo, meters) may name other tracks. Moving
    a clip along its track, or a gain, mute or collapse change, leaves it alone."""
    holds = [[] for _ in project.audio_tracks]
    for c in project.audio_clips:
        if c.track < len(holds): holds[c.track].append((c.path, c.offset))
    return tuple(t.name for t in project.audio_tracks), tuple(tuple(sorted(h)) for h in holds)


class NoteIndex:
    """Beat-range index over one notes tuple (audit P1): queries return original note indices in
    score order, so selection, drag order, overlap priority and undo are unaffected."""
    def __init__(self, notes):
        self.notes = notes
        count = len(notes)
        starts = np.fromiter((n.start for n in notes), float, count)
        ends = np.fromiter((n.end for n in notes), float, count)
        self.order = np.argsort(starts, kind='stable')
        self.starts, self.ends = starts[self.order], ends[self.order]
        # Largest end among the first k notes by start: notes before `first` all end before `low`.
        self.reach = np.maximum.accumulate(self.ends) if count else self.ends

    def overlapping(self, low, high):
        """Indices of notes with start <= high and end >= low (a span touching [low, high])."""
        first = int(np.searchsorted(self.reach, low, 'left'))
        last = int(np.searchsorted(self.starts, high, 'right'))
        if first >= last: return []
        return np.sort(self.order[first:last][self.ends[first:last] >= low]).tolist()

    def starting(self, low, high):
        """Indices of notes with low <= start <= high."""
        first, last = np.searchsorted(self.starts, low, 'left'), np.searchsorted(self.starts, high, 'right')
        return np.sort(self.order[first:last]).tolist()


def position_text(project, beats):
    beats = max(0., beats)
    bar, beat = Meter(project.meter_changes).bar_of(beats)
    bar, beat = int(bar) + 1, float(beat) + 1
    seconds = float(project.seconds(beats))
    return f'{bar}.{beat:.2f}  ·  {int(seconds // 60)}:{seconds % 60:05.2f}'


class _AltWheelFilter(QObject):
    """Consume a wheel or curve-bending gesture's Alt release before QMenuBar sees it.

    QMenuBar installs its own application filter when Alt is pressed, and the newest
    filter runs first, so mark() reinstalls this one after that press.
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.used = False
        QApplication.instance().installEventFilter(self)

    def mark(self):
        app = QApplication.instance(); app.removeEventFilter(self); app.installEventFilter(self)
        self.used = True

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Alt:
            self.used = False
        elif event.type() == QEvent.Type.KeyRelease and event.key() == Qt.Key.Key_Alt:
            used, self.used = self.used, False
            if used:
                return True
        return False


class TimelineView(QWidget):
    edited = Signal(object, int)  # New project, selected clip index.
    clip_selected = Signal(int)
    # A clip drag reordered the clips (UI1): new project, the edited clip's new index. The window keeps
    # editing the same clip at its new index instead of loading whichever clip took its old place.
    clip_moved = Signal(object, int)
    clip_move_finished = Signal()  # A clip drag that moved clips ended: one Undo step, "Move clip".
    seek = Signal(float)  # Beats.
    notice = Signal(str)  # Short message for the status line (copy/paste results and errors).
    audition = Signal(int, float)
    audition_chord = Signal(tuple, float)
    roll_preferences_changed = Signal()
    chords_requested = Signal(bool)
    velocity_requested = Signal(bool)
    audio_files_dropped = Signal(list, float, int)  # Paths, beat, track (-1: a new track): the window adds them one after another.
    audio_add_requested = Signal(float, int)  # Beat, track: the window asks for a file.
    audio_track_solo_requested = Signal(int)  # Audio track index: the window toggles its solo (not saved).
    track_solos_requested = Signal(list, bool)  # Audio track indices, on: the window solos or unsolos them all.
    track_move_finished = Signal(int)  # Tracks a header drag moved: the window names the Undo step.
    audio_locate_requested = Signal(int)  # Audio clip index: the window asks where its missing file is.
    audio_solo_requested = Signal(int)  # Audio clip index: the window toggles its solo (not saved).
    track_effects_requested = Signal(int)
    track_selected = Signal(int)  # Explicit header selection, independent of structural edits.
    shown_lane_changed = Signal()

    def __init__(self):
        super().__init__()
        self.roll_audio_gain_lane = False  # The lane under each audio track (a roll preference).
        self.roll_audio_lane = 'gain'  # What that lane shows: clip gain, track pan or track phase (AW2).
        self.effect_lanes = {}  # Track index -> (effect index, field): that track's lane shows an effect parameter (AW3; not saved).
        self.selected_audio = None  # Index into project.audio_clips.
        self.audio_solos = frozenset()  # Soloed audio clip indices (the window owns them; not saved).
        self.audio_track_solos = frozenset()  # Soloed audio track indices (the window owns them; not saved).
        self.track_picks, self.track_anchor = set(), None  # Selected audio tracks (header clicks; AW1) and the Shift anchor.
        self.track_meters, self.track_clipped = [], set()  # Per-track meter levels and latched lamps.
        self._rows = (None, None)  # (key, rows): track_rows() cache.
        self.track_scroll = 0.  # Pixels the audio track rows are scrolled up (when they do not all fit).
        self.track_extent = (0., 0.)  # (all rows' height, the height shown).
        self.header_painted = {}  # Track index: the header rectangles last painted (header_hit_targets).
        self.gain_node = None  # The gain-lane node pressed last (drawn selected).
        self.track_node = None  # (track, node) of the track lane (pan or phase) pressed last.
        self.gain_strip = LaneStrip(LINE)
        self.meter_level, self.meter_clipped = 0., False
        self.drop_beat, self.dragging_audio = None, False
        self.drop_track = None  # Track under a file drag (-1: the Add row, a new track).
        self.ask_text = lambda title, label, text: QInputDialog.getText(self, title, label, text=text)  # Smokes replace it.
        self.pending_audio_menu = None
        self.audio_gain_button = QToolButton(self)
        self.audio_gain_button.setText('Gain'); self.audio_gain_button.setObjectName('audio_gain_lane')
        self.audio_gain_button.setCheckable(True); self.audio_gain_button.setFixedSize(54, 16)
        self.audio_gain_button.setToolTip('Show a lane under each audio track: the clips\' gain, or the track\'s pan or phase '
                                          '(right-click a track or the lane › Lane to choose)')
        self.audio_gain_button.clicked.connect(lambda checked=False: self.set_audio_gain_lane(self.audio_gain_button.isChecked()))
        self.setAcceptDrops(True)
        self.wheel_mapping = wheel_mapping.defaults()
        self.wheel_sensitivity = scroll_mapping.DEFAULT_WHEEL_SENSITIVITY  # Preferences › Controls (UI2).
        self.trackpad_sensitivity = scroll_mapping.DEFAULT_TRACKPAD_SENSITIVITY
        self.row_steps, self.height_steps, self.lane_steps = (scroll_mapping.Accumulator() for _ in range(3))  # Fractions carried over.
        self.last_trackpad = False
        self.alt_wheel_filter = _AltWheelFilter(self) if sys.platform != 'darwin' else None
        self.project, self.selected_clip, self.playhead = None, 0, 0.
        self.clip_picks = set()  # Scene clips a drag moves as one block (Shift+click adds; UI1), with selected_clip.
        self.selected_note = None  # Index into project.notes (the one being dragged).
        self.note_selection = set()  # Notes that Copy/Cut/Delete act on.
        self.draw_mode, self.chord_type, self.chord_inversion = 'Note', 'major', 0
        self.roll_scale, self.roll_scale_root = 'Off', 0
        self.roll_scale_snap, self.roll_hear_notes = False, True
        self.roll_velocity_lane = True
        self.velocity_strip = LaneStrip(BARS)  # Its collapsed flag is the roll_velocity_collapsed preference.
        self.switch_strip = LaneStrip(BLOCKS)  # On/Off lanes in the transform roll.
        self.pending_switch_menu = None  # (key, beat, position): opened after the right release.
        self.tool_values = {}
        self.note_click = None
        self.node_selection = (None, set())  # (lane key, node indices) in the transform roll.
        self.pending_node_gain = None  # (key, points, index, indices): Node gain dialog after the release.
        self.pressed_node = None  # (key, index) when the last press hit an existing node.
        self.lane_target = None  # The lane nodes are pasted into: the last one clicked.
        self.focus_kind = 'notes'  # What Copy/Cut/Delete act on: notes, clips or nodes.
        self.scale, self.scroll = 28., 0.  # Pixels per beat; first visible beat.
        self.row, self.top_pitch = 9, 76  # Note row height; highest visible pitch.
        self.block = BLOCK  # Transform roll block height (Cmd/Ctrl + wheel over the roll).
        self.pending_reference = None  # Reference dialog requested by a right press; opened on release.
        self.pending_marker_menu = None
        self.pending_ruler_menu = None
        self.pending_note_menu = None
        self.snap = SNAPS['1/16']
        self.hint_text = ''
        self.drag = None
        self.selection_rect = None
        self.roll, self.lane_scroll = NOTE_ROLL, 0  # Which roll; transform roll scroll in pixels.
        self.active = {}  # Block key -> the lane key being edited in that block.
        self.roll_buttons = {}
        for roll, text in ((NOTE_ROLL, '♪'), (TRANSFORM_ROLL, '◆')):
            button = QToolButton(self); button.setText(text); button.setToolTip(ROLL_TIPS[roll]); button.setCheckable(True)
            button.setObjectName(f'roll_{roll}'); button.setFixedSize(26, 22)
            button.clicked.connect(lambda checked=False, roll=roll: self.set_roll(roll))
            self.roll_buttons[roll] = button
        self.roll_chords = QToolButton(self)
        self.roll_chords.setText('Chords'); self.roll_chords.setObjectName('roll_chords')
        self.roll_chords.setCheckable(True); self.roll_chords.setFixedSize(54, 18)
        self.roll_chords.setToolTip('Chords: play overlapping notes as chords (Transport › Chords)')
        self.roll_chords.clicked.connect(lambda checked: self.chords_requested.emit(checked))
        self.roll_velocity = QToolButton(self)
        self.roll_velocity.setText('Vel'); self.roll_velocity.setObjectName('roll_velocity')
        self.roll_velocity.setCheckable(True); self.roll_velocity.setFixedSize(54, 18)
        self.roll_velocity.setToolTip('Velocity: note velocity sets loudness and size (Transport › Velocity)')
        # A plain slot would pick clicked()'s no-argument overload and emit without the state.
        self.roll_velocity.clicked.connect(lambda checked=False: self.velocity_requested.emit(self.roll_velocity.isChecked()))
        self.place_roll_buttons()
        self.colors = themes.for_widget(self).timeline_colors()
        self.setMinimumHeight(150 + LANE_HEIGHT + AUDIO_TRACK); self.setFocusPolicy(Qt.FocusPolicy.ClickFocus); self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.split_action = QAction('Split at playhead', self)
        self.split_action.setShortcuts(key_sequences('split_clip'))
        self.split_action.setProperty('shortcut_id', 'split_clip')
        self.split_action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.split_action.triggered.connect(self.split_at_playhead)
        self.addAction(self.split_action)
        self.marker_action = QAction('Add marker', self)
        self.marker_action.setShortcuts(key_sequences('add_marker'))
        self.marker_action.setProperty('shortcut_id', 'add_marker')
        self.marker_action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.marker_action.triggered.connect(lambda: self.add_marker_at(self.playhead))
        self.addAction(self.marker_action)
        for name, text, slot in self.selection_commands():
            if name is None: continue
            action = QAction(text, self); action.setShortcuts(key_sequences(name))
            action.setProperty('shortcut_id', name)
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            action.triggered.connect(lambda checked=False, slot=slot, name=name: slot() if self.notes_active()
                                     or (name == 'note_duplicate' and self.focus_kind == 'tracks' and self.track_picks) else None)
            self.addAction(action)

    def notes_active(self):
        return self.project is not None and self.roll == NOTE_ROLL and self.focus_kind == 'notes'

    def apply_note_tool(self, tool, *values, indices=None):
        if not self.notes_active(): return False
        try:
            notes, picked = tool(self.project.notes, self.note_selection if indices is None else indices, *values)
            project = validate_project(replace(self.project, notes=notes))
        except ValueError as error:
            self.notice.emit(str(error)); return False
        self.note_selection, self.selected_note = set(picked), None
        if project != self.project: self.emit(project)
        else: self.update()
        return True

    def duplicate_selection(self):
        if self.focus_kind == 'tracks' and self.track_picks: return self.duplicate_tracks(self.track_picks)
        return self.apply_note_tool(note_tools.duplicate, self.snap_step(self.playhead))

    def split_selection(self, beat=None):
        beat = self.playhead if beat is None else beat
        indices = self.note_selection or {i for i, n in enumerate(self.project.notes) if n.start < beat < n.end}
        return self.apply_note_tool(note_tools.split, beat, indices=indices)

    def join_selection(self): return self.apply_note_tool(note_tools.join)
    def set_selection_velocity(self, value): return self.apply_note_tool(note_tools.set_velocity, value)
    def ramp_selection_velocity(self, first, last): return self.apply_note_tool(note_tools.ramp_velocity, first, last)
    def legato_selection(self): return self.apply_note_tool(note_tools.legato)
    def quantize_selection(self, grid, strength=1., swing=0., ends=False):
        return self.apply_note_tool(note_tools.quantize, grid, strength, swing, ends)
    def humanize_selection(self, timing=.02, velocity=.05, seed=1):
        return self.apply_note_tool(note_tools.humanize, timing, velocity, seed)
    def strum_selection(self, amount=1/32, direction='up'):
        return self.apply_note_tool(note_tools.strum, amount, direction)
    def arpeggiate_selection(self, pattern='up'):
        return self.apply_note_tool(note_tools.arpeggiate, pattern, self.snap_step(self.playhead) or .25)

    def selection_commands(self):
        return (('note_duplicate', 'Duplicate', self.duplicate_selection),
                ('note_split', 'Split at playhead', self.split_selection),
                ('note_join', 'Join', self.join_selection), ('note_legato', 'Legato', self.legato_selection),
                ('note_quantize', 'Quantize…', lambda: self.note_tool_dialog('Quantize')),
                (None, 'Humanize…', lambda: self.note_tool_dialog('Humanize')),
                (None, 'Strum…', lambda: self.note_tool_dialog('Strum')))

    def add_selection_tools(self, menu):
        for name, text, slot in self.selection_commands():
            action = menu.addAction(text); action.setEnabled(bool(self.note_selection) and self.notes_active())
            if name:
                action.setShortcuts(key_sequences(name)); action.setProperty('shortcut_id', name)
            action.triggered.connect(lambda checked=False, slot=slot: slot())
        sub = QMenu('Arpeggiate', menu); menu.addMenu(sub); menu.arpeggiate = sub
        sub.setEnabled(bool(self.note_selection) and self.notes_active())
        for pattern in ('up', 'down', 'up-down'):
            sub.addAction(pattern.capitalize()).triggered.connect(lambda checked=False, p=pattern: self.arpeggiate_selection(p))
        for kind in ('Set velocity', 'Ramp velocity'):
            action = menu.addAction(kind + '…')
            action.setEnabled(self.notes_active() and len(self.note_selection) >= (2 if kind == 'Ramp velocity' else 1))
            action.triggered.connect(lambda checked=False, kind=kind: self.note_tool_dialog(kind))

    def set_roll_option(self, name, value):
        setattr(self, name, value)
        if name in ('roll_audio_lane', 'roll_audio_gain_lane'): self.shown_lane_changed.emit()
        self.place_roll_buttons()
        self.update(); self.roll_preferences_changed.emit()

    def build_note_tools_menu(self):
        menu = QMenu(self); menu.submenus = []
        def choices(title, field, values, labels=None, enabled=None, persistent=False):
            sub = QMenu(title, menu); menu.addMenu(sub); menu.submenus.append(sub)
            group = QActionGroup(sub); group.setExclusive(True)
            for index, value in enumerate(values):
                action = sub.addAction(labels[index] if labels else str(value)); action.setCheckable(True)
                group.addAction(action); action.setChecked(getattr(self, field) == value)
                if enabled: action.setEnabled(enabled(value))
                def choose(checked=False, value=value):
                    if persistent: self.set_roll_option(field, value)
                    else: setattr(self, field, value)
                    if field == 'chord_type': self.chord_inversion = min(self.chord_inversion, len(note_tools.CHORD_TYPES[value]) - 1)
                    if field == 'roll_scale' and hasattr(menu, 'scale_snap'):
                        menu.scale_snap.setEnabled(value != 'Off')
                    # Menus are rebuilt on opening, including inversion availability.
                action.triggered.connect(choose)
            return sub
        choices('Draw', 'draw_mode', ('Note', 'Chord'))
        choices('Chord type', 'chord_type', tuple(note_tools.CHORD_TYPES))
        inversion = choices('Inversion', 'chord_inversion', tuple(range(5)), ('Root', '1st', '2nd', '3rd', '4th'),
                            lambda value: value < len(note_tools.CHORD_TYPES[self.chord_type]))
        def refresh_inversions():
            for i, action in enumerate(inversion.actions()):
                action.setEnabled(i < len(note_tools.CHORD_TYPES[self.chord_type])); action.setChecked(i == self.chord_inversion)
        inversion.aboutToShow.connect(refresh_inversions)
        choices('Scale', 'roll_scale', note_tools.ROLL_SCALES, persistent=True)
        choices('Root', 'roll_scale_root', tuple(range(12)), ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'), persistent=True)
        for text, field in (('Snap to scale', 'roll_scale_snap'), ('Hear notes', 'roll_hear_notes'), ('Velocity lane', 'roll_velocity_lane')):
            action = menu.addAction(text); action.setCheckable(True); action.setChecked(getattr(self, field))
            if field == 'roll_scale_snap':
                action.setEnabled(self.roll_scale != 'Off'); menu.scale_snap = action
            action.toggled.connect(lambda value, field=field: self.set_roll_option(field, value))
        menu.addSeparator(); self.add_selection_tools(menu)
        return menu

    def note_tool_dialog(self, kind):
        if not self.notes_active() or not self.note_selection: return
        dialog = QDialog(self); dialog.setWindowTitle(kind); layout = QFormLayout(dialog)
        widgets = []
        def number(label, value, low, high, integer=False, suffix=''):
            widget = QSpinBox() if integer else QDoubleSpinBox()
            widget.setRange(low, high)
            if not integer: widget.setDecimals(5)
            widget.setValue(value); widget.setSuffix(suffix); layout.addRow(label, widget); widgets.append(widget)
            return widget
        if kind in ('Set velocity', 'Ramp velocity'):
            ordered = (sorted(self.note_selection) if kind == 'Set velocity' else
                       sorted(self.note_selection, key=lambda i: (self.project.notes[i].start, i)))
            first = round(self.project.notes[ordered[0]].velocity * 127)
            if kind == 'Set velocity':
                value = number('Velocity', first, 0, 127, integer=True)
                read = lambda: (value.value() / 127,); apply = self.set_selection_velocity
            else:
                first_box = number('From', first, 0, 127, integer=True)
                last_box = number('To', round(self.project.notes[ordered[-1]].velocity * 127), 0, 127, integer=True)
                read = lambda: (first_box.value() / 127, last_box.value() / 127); apply = self.ramp_selection_velocity
        elif kind == 'Quantize':
            grids = {'1/2': 2., '1/4': 1., '1/8': .5, '1/16': .25, '1/32': .125, '1/8T': 1/3, '1/16T': 1/6}
            values = self.tool_values.get(kind, (self.snap_step(self.playhead) or .25, 1., 0., False))
            grid = QComboBox(); grid.addItems(grids); grid.setCurrentIndex(min(range(len(grids)), key=lambda i: abs(list(grids.values())[i] - values[0])))
            layout.addRow('Grid', grid)
            strength = number('Strength', values[1] * 100, 0, 100, suffix=' %')
            swing = number('Swing', values[2] * 100, 0, 100, suffix=' %')
            ends = QCheckBox('Quantize ends'); ends.setChecked(values[3]); layout.addRow(ends)
            read = lambda: (grids[grid.currentText()], strength.value()/100, swing.value()/100, ends.isChecked())
            apply = self.quantize_selection
        elif kind == 'Humanize':
            values = self.tool_values.get(kind, (.02, .05, 1))
            timing = number('Timing ± beats', values[0], 0, MAX_BEATS)
            velocity = number('Velocity ±', values[1], 0, 1)
            seed = number('Seed', values[2], 0, 2147483647, integer=True)
            button = QPushButton('New seed'); layout.addRow(button)
            import secrets
            button.clicked.connect(lambda: seed.setValue(secrets.randbelow(2147483648)))
            read = lambda: (timing.value(), velocity.value(), seed.value()); apply = self.humanize_selection
        else:
            values = self.tool_values.get(kind, (1/32, 'up'))
            amount = number('Amount (beats)', values[0], 0, MAX_BEATS)
            direction = QComboBox(); direction.addItems(('Up', 'Down')); direction.setCurrentText(values[1].capitalize()); layout.addRow('Direction', direction)
            read = lambda: (amount.value(), direction.currentText().lower()); apply = self.strum_selection
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = read(); self.tool_values[kind] = values; apply(*values)

    def scale_pitch(self, pitch):
        return note_tools.snap_pitch(pitch, self.roll_scale, self.roll_scale_root) if self.roll_scale_snap and self.roll_scale != 'Off' else pitch

    # Geometry ------------------------------------------------------------------
    def x_of(self, beats): return GUTTER + (beats - self.scroll) * self.scale
    def velocity_lane_rect(self):
        if self.roll != NOTE_ROLL or not self.roll_velocity_lane: return None
        height = self.velocity_strip.height(LANE_HEIGHT)
        return QRectF(GUTTER, self.height() - height, max(0, self.width() - GUTTER), height)

    @property
    def roll_velocity_collapsed(self): return self.velocity_strip.collapsed

    @roll_velocity_collapsed.setter
    def roll_velocity_collapsed(self, value): self.velocity_strip.collapsed = bool(value)

    def collapse_velocity_lane(self, collapsed=True):
        """Right-click the velocity lane › Collapse (a thin strip) or › Expand (UI1)."""
        self.drag = None; self.set_roll_option('roll_velocity_collapsed', bool(collapsed))

    def note_bottom(self):
        rect = self.velocity_lane_rect()
        return rect.top() if rect is not None else self.height()

    def note_index(self):
        """NoteIndex of the current notes, rebuilt only when the notes tuple changes."""
        notes = self.project.notes
        index = getattr(self, '_note_index', None)
        if index is None or index.notes is not notes: index = self._note_index = NoteIndex(notes)
        return index

    def velocity_items(self):
        # Bars of notes starting in view, in score order (later notes drawn on top).
        notes, low, high = self.project.notes, self.beats_at(GUTTER), self.beats_at(self.width())
        return [(i, LaneItem(notes[i].start, notes[i].velocity, selected=i in self.note_selection))
                for i in self.note_index().starting(low - 1e-6, high + 1e-6)
                if GUTTER <= self.x_of(notes[i].start) <= self.width()]

    def paint_velocity_lane(self, p, c):
        rect = self.velocity_lane_rect()
        if rect is None: return
        self.velocity_strip.set_rect(rect)
        if self.velocity_strip.collapsed:
            self.velocity_strip.paint_collapsed(p, c, 'Velocity (collapsed) · right-click › Expand'); return
        self.velocity_strip.paint(p, c, self.x_of, [item for _, item in self.velocity_items()], self.project.velocity)
        if self.drag and self.drag[0] == 'velocity-line':
            p.save(); p.setClipRect(rect); p.setPen(QPen(c['accent'], 1, Qt.PenStyle.DashLine))
            p.drawLine(self.drag[2], self.drag[3]); p.restore()

    def beats_at(self, x): return self.scroll + (x - GUTTER) / self.scale
    @property
    def roll_top(self): return self.audio_end + (ADD_TRACK_ROW if self.has_add_row else 0)
    @property
    def has_add_row(self):
        """The "+ Add audio track" row shows once a track exists; without tracks the layout is A2's."""
        return self.project is not None and bool(self.project.audio_tracks)
    @property
    def metrics(self): return themes.for_widget(self).metrics

    @property
    def audio_top(self): return self.metrics.ruler_height + TRACK
    @property
    def audio_end(self):
        """The bottom of the audio track rows as shown (with gain lanes), where the Add row starts."""
        self.track_rows(); return self.audio_top + self.track_extent[1]

    def track_rows(self):
        """One TrackRow per audio track, top to bottom (cached on the tracks tuple). A project
        without tracks shows one empty row for track 0 (track None): a drop there makes 'Audio 1'."""
        tracks = self.project.audio_tracks if self.project is not None else ()
        key = (tracks, self.roll_audio_gain_lane, self.height(), self.track_scroll)
        if self._rows[0] is not None and self._rows[0][0] is tracks and self._rows[0][1:] == key[1:]: return self._rows[1]
        sizes = []
        for track in tracks or (None,):
            height = COLLAPSED_TRACK if track is not None and track.collapsed else AUDIO_TRACK
            sizes.append((track, height, self.roll_audio_gain_lane and height == AUDIO_TRACK))
        total = float(sum(h + (GAIN_LANE if lane else 0) for _, h, lane in sizes))
        room = self.height() - self.audio_top - (ADD_TRACK_ROW if tracks else 0) - MIN_ROLL
        shown = min(total, max(float(AUDIO_TRACK), float(room)))
        self.track_scroll = min(max(0., self.track_scroll), total - shown)
        rows, y = [], self.audio_top - self.track_scroll
        for t, (track, height, lane) in enumerate(sizes):
            rows.append(TrackRow(t, track, y, height, y + height if lane else None))
            y += height + (GAIN_LANE if lane else 0)
        self.track_extent = (total, shown)
        self._rows = ((tracks, self.roll_audio_gain_lane, self.height(), self.track_scroll), rows)
        return rows

    def scroll_tracks(self, pixels):
        """Scroll the audio track rows by pixels (positive: down to later tracks)."""
        self.track_rows(); total, shown = self.track_extent
        value = min(max(0., self.track_scroll + pixels), total - shown)
        if value != self.track_scroll: self.track_scroll = value; self.update()

    def track_row(self, t):
        rows = self.track_rows(); return rows[min(max(0, t), len(rows) - 1)]

    def track_at(self, y):
        """(row, part) under y in the audio area: part 'track', 'lane' or 'add'; (None, None) elsewhere."""
        if self.project is None or not self.audio_top <= y < self.roll_top: return None, None
        if y >= self.audio_end: return (None, 'add') if self.has_add_row else (None, None)
        for row in self.track_rows():
            if row.top <= y < row.top + row.height: return row, 'track'
            if row.lane is not None and row.lane <= y < row.lane + GAIN_LANE: return row, 'lane'
        return (None, 'add') if self.has_add_row else (None, None)

    def add_row_rect(self): return QRectF(0, self.audio_end, self.width(), ADD_TRACK_ROW)
    def y_of(self, pitch): return self.roll_top + (self.top_pitch - pitch) * self.row
    def pitch_at(self, y): return int(math.floor(self.top_pitch - (y - self.roll_top) / self.row)) + 1
    def snap_step(self, beats):
        if self.snap == SNAPS['Bar'] and self.project is not None:
            return float(Meter(self.project.meter_changes).beats_per_bar_at(max(0., beats)))
        return self.snap

    def snapped(self, beats, mode=round, origin=0.):
        if self.snap == SNAPS['Bar'] and self.project is not None:
            meter = Meter(self.project.meter_changes)
            absolute = max(0., beats + origin)
            bar, offset = meter.bar_of(absolute)
            return max(0., float(meter.bar_start(int(bar) + mode(float(offset) / float(meter.beats_per_bar_at(absolute))))) - origin)
        return max(0., mode(beats / self.snap) * self.snap if self.snap else beats)

    def set_project(self, project, selected_clip):
        if project is not self.project: self.clip_picks = set()  # Indices name other clips after an outside edit.
        old = self.project
        previous_lanes = self.effect_lanes
        if project is not old and project is not getattr(self, '_emitted', None):
            self.track_picks, self.track_anchor = set(), None  # Undo, Redo or another project: indices may name other tracks.
        if old is None or track_layout(old) != track_layout(project):
            self.track_meters, self.track_clipped = [], set()  # Tracks added, removed or reordered: meters start again.
            self.effect_lanes = {}  # Effect lanes name tracks by index.
        if project is not old and project is not getattr(self, '_emitted', None):
            self.track_node = None  # Undo or another project: node indices may name other nodes.
            if old is not None:  # Undo/Redo changed a chain: its effect lane may name another effect.
                self.effect_lanes = {t: lane for t, lane in self.effect_lanes.items()
                                     if t < len(old.audio_tracks) and t < len(project.audio_tracks)
                                     and effect_kinds(old.audio_tracks[t]) == effect_kinds(project.audio_tracks[t])}
            if self.drag and self.drag[0] == 'track-lane': self.drag = None
        self.project, self.selected_clip = project, selected_clip
        if self.effect_lanes != previous_lanes: self.shown_lane_changed.emit()
        blocked = self.roll_chords.blockSignals(True)
        self.roll_chords.setChecked(project.chords); self.roll_chords.blockSignals(blocked)
        blocked = self.roll_velocity.blockSignals(True)
        self.roll_velocity.setChecked(project.velocity); self.roll_velocity.blockSignals(blocked)
        if self.selected_note is not None and self.selected_note >= len(project.notes): self.selected_note = None
        if self.selected_audio is not None and self.selected_audio >= len(project.audio_clips): self.selected_audio = None
        self.track_picks = {t for t in self.track_picks if t < len(project.audio_tracks)}
        if self.track_anchor is not None and self.track_anchor >= len(project.audio_tracks): self.track_anchor = None
        self.note_selection = {i for i in self.note_selection if i < len(project.notes)}
        if self.roll_top != getattr(self, '_placed_top', None):  # Tracks added, removed or collapsed move the roll.
            self._placed_top = self.roll_top; self.place_roll_buttons()
        self.update()

    def set_playhead(self, beats, *, follow=True):
        if beats == self.playhead: return  # A project edit can refresh a stationary playhead.
        old = self.playhead_region() if self.project is not None else []
        self.playhead = beats
        # Follow the playhead during playback when it leaves the view.
        if follow and not self.drag and (beats < self.scroll or self.x_of(beats) > self.width() - 20):
            self.scroll = max(0., beats - 1.)
            self.update(); return
        if self.project is None: return
        # Only the old and new playhead strips and chord pills change (audit P1).
        for rect in old + self.playhead_region(): super().update(rect)

    def zoom(self, factor):
        self.scale = min(SCALE_RANGE[1], max(SCALE_RANGE[0], self.scale * factor)); self.update()

    def zoom_to_fit(self, selection=False):
        if self.project is None: return
        if selection:
            items = [self.project.notes[i] for i in self.note_selection
                     if 0 <= i < len(self.project.notes)]
            if not items and self.clip is not None: items = [self.clip]
            if not items: return
            span = (min(item.start for item in items), max(item.end for item in items))
        else:
            span = (0., self.project.end_beats)
        self.scroll, self.scale = zoom_fit(span, self.width(), GUTTER)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event); self.place_roll_buttons()

    def place_roll_buttons(self):
        for index, roll in enumerate((NOTE_ROLL, TRANSFORM_ROLL)):
            button = self.roll_buttons[roll]; button.move(4 + index * 28, self.roll_top + 4)
            blocked = button.blockSignals(True); button.setChecked(self.roll == roll); button.blockSignals(blocked)
        self.roll_chords.move(4, self.roll_top + 28)
        self.roll_chords.setVisible(self.roll == NOTE_ROLL)
        rect = self.velocity_lane_rect()
        self.roll_velocity.setVisible(rect is not None and not self.roll_velocity_collapsed)
        if rect is not None: self.roll_velocity.move(4, int(rect.top()) + 4)
        self.audio_gain_button.move(4, int(self.audio_end) + 1 if self.has_add_row else int(self.audio_top + AUDIO_TRACK - 19))
        blocked = self.audio_gain_button.blockSignals(True)
        self.audio_gain_button.setChecked(self.roll_audio_gain_lane); self.audio_gain_button.blockSignals(blocked)

    def set_roll(self, roll):
        self.roll = roll; self.drag = None; self.selection_rect = None
        self.place_roll_buttons(); self.update()

    # Transform roll geometry ---------------------------------------------------
    @property
    def clip(self):
        if self.project is None or not 0 <= self.selected_clip < len(self.project.clips): return None
        return self.project.clips[self.selected_clip]

    def lane_blocks(self):
        clip = self.clip
        return blocks(clip) if clip is not None else ()

    def lane_room(self):
        """Largest transform-roll scroll that still shows the last block."""
        return max(0, len(self.lane_blocks()) * self.block - (self.height() - self.roll_top - LANES_TOP))

    def block_rect(self, index):
        top = self.roll_top + LANES_TOP + index * self.block - self.lane_scroll
        return QRectF(GUTTER, top, max(1., self.width() - GUTTER), self.block - 6)

    def gain_y(self, rect, gain):
        low, high = GAIN_RANGE; body = rect.adjusted(0, LEGEND, 0, -3)
        return body.top() + (high - gain) / (high - low) * body.height()

    def gain_at_y(self, rect, y):
        low, high = GAIN_RANGE; body = rect.adjusted(0, LEGEND, 0, -3)
        return min(high, max(low, high - (y - body.top()) / max(1., body.height()) * (high - low)))

    def active_key(self, block):
        key = self.active.get(block.key)
        if key not in block.keys or not get_points(self.clip, key):
            key = next((k for k in block.keys if get_points(self.clip, k)), block.keys[0])
        return key

    def legend_rects(self, block, rect):
        """(key, rect) for each name in the block's legend, left to right."""
        metrics, x, result = QFontMetrics(self.font()), rect.left() + 4, []
        for key in block.keys:
            width = metrics.horizontalAdvance(lane_label(self.clip, key)) + 10
            result.append((key, QRectF(x, rect.top() + 1, width, LEGEND - 2))); x += width + 4
        return result

    def node_points(self, rect, points):
        start = self.clip.start
        return [QPointF(self.x_of(start + p[0]), self.gain_y(rect, p[1])) for p in points]

    def show_lane(self, key):
        """Switch to the transform roll with key's block in view and its line selected."""
        self.active[block_of(key)] = key; self.set_roll(TRANSFORM_ROLL)
        for index, block in enumerate(self.lane_blocks()):
            if block.key == block_of(key):
                rect = self.block_rect(index)
                if rect.top() < self.roll_top + LANES_TOP or rect.bottom() > self.height(): self.lane_scroll = min(index * self.block, self.lane_room())
        self.update()

    def marker_rect(self, index):
        beat, name = self.project.markers[index]
        x = self.x_of(beat)
        next_x = (self.x_of(self.project.markers[index + 1][0])
                  if index + 1 < len(self.project.markers) else self.width())
        width = min(140., max(8., next_x - x), QFontMetrics(self.font()).horizontalAdvance(name) + 14.)
        top = 2 * self.metrics.ruler_flag_height
        return QRectF(x, top, width, self.metrics.ruler_height - top - 1)

    def flags(self):
        meter = Meter(self.project.meter_changes)
        return [('tempo', i, b, f'{v:g}') for i, (b, v) in enumerate(self.project.tempo_changes)] + [
            ('meter', i, float(meter.bar_start(b)), f'{n}/{d}')
            for i, (b, n, d) in enumerate(self.project.meter_changes)]

    def flag_rect(self, kind, beat):
        height = self.metrics.ruler_flag_height
        return QRectF(self.x_of(beat), 0 if kind == 'tempo' else height, self.metrics.ruler_flag_width, height)

    def flag_hit(self, x, y):
        if x < GUTTER: return None
        return next(((kind, i) for kind, i, beat, _ in reversed(self.flags())
                     if self.flag_rect(kind, beat).contains(QPointF(x, y))), None)

    def change_map(self, kind, position, values, original=None, remove=None):
        project = original or self.project
        field = 'tempo_changes' if kind == 'tempo' else 'meter_changes'
        changes = list(getattr(project, field))
        if remove is not None: changes.pop(remove)
        changes = [entry for entry in changes if entry[0] != position]
        if values is not None:
            if kind == 'tempo' and position == 0:
                project = replace(project, bpm=values[0])
            else: changes.append((position, *values))
        try: self.emit(validate_project(replace(project, **{field: tuple(sorted(changes))})))
        except ValueError as error: self.notice.emit(str(error))

    def edit_tempo(self, beat):
        dialog = QDialog(self); dialog.setWindowTitle('Tempo change here')
        layout = QVBoxLayout(dialog)
        value = QDoubleSpinBox(); value.setRange(*BPM_RANGE); value.setDecimals(3)
        value.setValue(next((v for b, v in reversed(self.project.tempo_changes) if b <= beat), self.project.bpm))
        layout.addWidget(value)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted: self.change_map('tempo', beat, (value.value(),))

    def edit_meter(self, bar):
        dialog = QDialog(self); dialog.setWindowTitle('Time signature here')
        layout = QVBoxLayout(dialog)
        n, d = next(((n, d) for b, n, d in reversed(self.project.meter_changes) if b <= bar), (4, 4))
        count = QSpinBox(); count.setRange(1, 16); count.setValue(n); layout.addWidget(count)
        unit = QComboBox(); unit.addItems(('2', '4', '8', '16')); unit.setCurrentText(str(d)); layout.addWidget(unit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted: self.change_map('meter', bar, (count.value(), int(unit.currentText())))

    def build_ruler_menu(self, beat, flag=None):
        if flag is not None:
            kind, index = flag
            entry = getattr(self.project, kind + '_changes')[index]
            beat = entry[0] if kind == 'tempo' else float(Meter(self.project.meter_changes).bar_start(entry[0]))
        beat = min(MAX_BEATS, max(0., beat))
        meter = Meter(self.project.meter_changes)
        bar, offset = meter.bar_of(beat)
        bar = int(bar) + round(float(offset) / float(meter.beats_per_bar_at(beat)))
        menu = self.build_marker_menu(None, beat)
        menu.addSeparator()
        menu.addAction('Tempo change here…').triggered.connect(lambda: self.edit_tempo(self.snapped(beat)))
        menu.addAction('Time signature here…').triggered.connect(lambda: self.edit_meter(bar))
        if flag is None:
            flag = next(((k, i) for k, i, b, _ in self.flags() if abs(b - beat) < 1e-8), None)
        if flag is not None:
            kind, index = flag
            position = getattr(self.project, kind + '_changes')[index][0]
            menu.addAction('Remove').triggered.connect(lambda: self.change_map(kind, position, None, remove=index))
        return menu

    def ruler_menu(self, beat, flag, position):
        menu = self.build_ruler_menu(beat, flag)
        menu.exec(position); menu.deleteLater()

    def ruler_bars(self):
        """Visible (quarter-note position, one-based bar label) pairs."""
        meter = Meter(self.project.meter_changes)
        return [(float(beat), int(meter.bar_of(beat)[0]) + 1)
                for beat in meter.bar_lines(max(0., self.scroll), self.beats_at(self.width()))]

    def marker_hit(self, x, y):
        if x < GUTTER: return None
        return next((i for i in reversed(range(len(self.project.markers)))
                     if self.marker_rect(i).contains(QPointF(x, y))), None)

    def add_marker_at(self, beat):
        if self.project is None: return
        try: self.emit(add_marker(self.project, min(MAX_BEATS, max(0., beat))))
        except ValueError as error: self.notice.emit(str(error))

    def rename_section(self, index):
        name, ok = QInputDialog.getText(self, 'Rename marker', 'Name (up to 64 characters):',
                                       text=self.project.markers[index][1])
        if ok:
            try: self.emit(rename_marker(self.project, index, name))
            except ValueError as error: self.notice.emit(str(error))

    def build_marker_menu(self, index=None, beat=None):
        menu = QMenu(self)
        if index is None:
            menu.addAction('Add marker').triggered.connect(lambda: self.add_marker_at(beat))
        else:
            menu.addAction('Rename…').triggered.connect(lambda: self.rename_section(index))
            menu.addAction('Delete').triggered.connect(lambda: self.emit(delete_marker(self.project, index)))
            start = self.project.markers[index][0]
            end = next((b for b, _ in self.project.markers[index + 1:] if b > start), None)
            action = menu.addAction('Loop between here and next'); action.setEnabled(end is not None)
            action.triggered.connect(lambda: self.emit(replace(self.project, loop=True, loop_start=start, loop_end=end)))
        return menu

    def marker_menu(self, index, beat, position):
        menu = self.build_marker_menu(index, beat)
        menu.exec(position); menu.deleteLater()

    # Painting ------------------------------------------------------------------
    # The roll is two layers (audit P1). The static layer (rows, grid, clips, the Audio track and
    # its waveforms, lanes, notes, markers, the velocity lane) is drawn into a pixmap, redrawn only
    # when something it shows changes. Every argument-free update() call marks it stale, as every
    # edit, drag, selection or scroll already calls update(); static_key() covers what changes
    # without one (size, pixel ratio, scroll, zoom, palette, font). The dynamic layer (playhead
    # line, chord pills, output meter) is drawn over it on each paint, and set_playhead repaints
    # only the old and new playhead strips and pills. The result equals a full repaint (tests).
    cache_static = True  # False paints everything directly, the pre-P7a path (tests compare them).

    def update(self, *args):
        if not args: self._static_valid = False
        super().update(*args)

    def static_key(self):
        """What the static layer shows besides the project (compared by identity in paintEvent)."""
        return (self.width(), self.height(), self.devicePixelRatioF(), self.scroll, self.scale,
                self.row, self.top_pitch, self.roll, self.lane_scroll, self.block, tuple(self.colors.items()),
                self.font().key(), self.roll_audio_gain_lane, self.roll_velocity_lane, self.roll_velocity_collapsed, media_generation())

    @guarded_paint
    def paintEvent(self, event):
        if self.project is None: return
        c = {k: QColor(v) for k, v in self.colors.items()}
        if not self.cache_static:
            p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            self.paint_static(p, c); self.paint_dynamic(p, c); p.end(); return
        key = self.static_key()
        cached = getattr(self, '_static', None)
        if (cached is None or not getattr(self, '_static_valid', False) or cached[0] is not self.project
                or cached[1] != key):
            ratio = self.devicePixelRatioF()
            pixmap = cached[2] if cached is not None and cached[1][:3] == key[:3] else None
            if pixmap is None:
                pixmap = QPixmap(max(1, math.ceil(self.width() * ratio)), max(1, math.ceil(self.height() * ratio)))
                pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(c['background'])
            painter = QPainter(pixmap); painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setFont(self.font())
            try: self.paint_static(painter, c)
            finally: painter.end()
            self._static, self._static_valid = (self.project, key, pixmap), True
            self.static_paints = getattr(self, 'static_paints', 0) + 1
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.drawPixmap(0, 0, self._static[2])
        self.paint_dynamic(p, c)
        p.end()

    def playhead_line_rect(self):
        """Where the playhead line is drawn (its pixel column, a little wider), or None off view."""
        x = self.x_of(self.playhead)
        return QRectF(int(x) - 2, 0, 5, self.height()) if x >= GUTTER else None

    def pill_rect(self, name, x, y, flip, metrics):
        width, height = metrics.horizontalAdvance(name) + 10, metrics.height() + 4
        if flip and x + width > self.width() - 2: x -= width + 8
        x = max(GUTTER + 2, min(x, self.width() - width - 2))
        y = max(self.roll_top + 2, min(y, self.height() - height - 2))
        return QRectF(x, y, width, height)

    def pill_font(self):
        font = QFont(self.font()); font.setPixelSize(themes.for_widget(self).fonts.timeline_px); return font

    def playhead_pill_rect(self):
        """The playhead's chord pill (as paint_chord_labels draws it), or None without one."""
        if self.roll != NOTE_ROLL: return None
        name = self.playhead_chord()
        if not name: return None
        return self.pill_rect(name, self.x_of(self.playhead) + 4, self.roll_top + 2, True, QFontMetrics(self.pill_font()))

    def playhead_region(self):
        rects = [r for r in (self.playhead_line_rect(), self.playhead_pill_rect()) if r is not None]
        return [r.adjusted(-2, -2, 2, 2).toAlignedRect() for r in rects]

    def paint_dynamic(self, p, c):
        """Drawn over the static layer on every paint: the playhead, the chord pills and the meter."""
        h = self.height()
        x = self.x_of(self.playhead)
        if x >= GUTTER:
            # The velocity lane is drawn over the playhead in a full repaint: stop the line above it.
            p.save(); p.setClipRect(QRectF(0, 0, self.width(), self.note_bottom()))
            p.setPen(QPen(c.get('playhead', c['text']), 1)); p.drawLine(int(x), 0, int(x), h); p.restore()
        if self.roll == NOTE_ROLL:
            self.paint_chord_labels(p, c)
        self.paint_meter(p, c)

    def paint_static(self, p, c):
        m = self.metrics
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), c['background'])
        dim = QColor(c['grid']); faint = QColor(c['grid']); faint.setAlpha(m.key_alpha)
        # Note rows: black keys shaded, C rows labelled.
        p.save(); p.setClipRect(QRectF(0, self.roll_top, w, max(0, self.note_bottom() - self.roll_top)))
        lowest = self.pitch_at(self.note_bottom()) if self.roll == NOTE_ROLL else PITCH_RANGE[1] + 1  # No note rows in the transform roll.
        for pitch in range(max(PITCH_RANGE[0], lowest), min(PITCH_RANGE[1], self.top_pitch) + 1):
            y = self.y_of(pitch)
            if pitch % 12 in (1, 3, 6, 8, 10): p.fillRect(QRectF(GUTTER, y, w - GUTTER, self.row), faint)
            if self.roll_scale != 'Off' and note_tools.in_scale(pitch, self.roll_scale, self.roll_scale_root):
                tint = QColor(c['accent']); tint.setAlpha(m.scale_root_alpha if pitch % 12 == self.roll_scale_root else m.scale_note_alpha)
                p.fillRect(QRectF(GUTTER, y, w - GUTTER, self.row), tint)
            if pitch % 12 == 0:
                p.setPen(dim); p.drawLine(GUTTER, int(y + self.row), w, int(y + self.row))
                label = QRectF(2, y - 2, GUTTER - 6, self.row + 4)
                if label.top() > self.roll_chords.geometry().bottom():
                    p.setPen(c['text']); p.drawText(label, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, note_name(pitch))
        p.restore()
        # Beat and bar lines.
        first, last = int(self.scroll), int(self.beats_at(w)) + 1
        for beat in range(max(0, first), last + 1):
            x = int(self.x_of(beat))
            if x < GUTTER: continue
            p.setPen(faint)
            p.drawLine(x, m.ruler_height, x, h)
        meter = Meter(self.project.meter_changes)
        for beat, number in self.ruler_bars():
            x = int(self.x_of(beat)); bar = number - 1
            p.setPen(c['grid']); p.drawLine(x, m.ruler_height, x, h)
            if self.scale * meter.beats_per_bar_at(beat) > m.ruler_label_spacing or bar % 4 == 0:
                p.setPen(c['text']); p.drawText(x + m.ruler_label_x, m.ruler_height - m.ruler_label_bottom, str(bar + 1))
        # Loop range.
        if self.project.loop:
            a, b = self.x_of(self.project.loop_start), self.x_of(self.project.loop_end)
            band = QColor(c['accent']); band.setAlpha(m.loop_alpha); p.fillRect(QRectF(a, 0, b - a, m.ruler_height), band)
        p.setPen(c['grid']); p.drawLine(0, m.ruler_height, w, m.ruler_height); p.drawLine(0, self.roll_top, w, self.roll_top)
        p.drawLine(0, self.audio_top, w, self.audio_top)
        p.setPen(c['text']); p.drawText(QRectF(2, m.ruler_height, GUTTER - 6, TRACK), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, 'Scenes')
        # Clips.
        for index, clip in enumerate(self.project.clips):
            rect = QRectF(self.x_of(clip.start), m.ruler_height + 3, clip.length * self.scale, TRACK - 6)
            if rect.right() < GUTTER or rect.left() > w: continue
            tint = self.clip_color(clip.color); silent = clip.muted or bool(self.audio_solos or self.audio_track_solos)
            picked = index == self.selected_clip or index in self.clip_picks
            alpha = m.scene_picked_alpha if picked else m.scene_alpha
            if index == self.selected_clip: alpha = m.scene_selected_alpha
            fill = QColor(tint)
            fill.setAlpha(alpha // m.mute_divisor if silent else alpha)
            p.fillRect(rect, fill)
            p.setPen(QPen(tint, 2 if picked else 1, Qt.PenStyle.DashLine if silent else Qt.PenStyle.SolidLine)); p.drawRect(rect)
            # Grab bars at both ends show where to drag to resize.
            bar = QColor(c['text']); bar.setAlpha(m.selected_alpha if index == self.selected_clip else m.scene_handle_alpha)
            width = min(HANDLE, rect.width() / 3)
            p.fillRect(QRectF(rect.left(), rect.top(), width, rect.height()), bar)
            p.fillRect(QRectF(rect.right() - width, rect.top(), width, rect.height()), bar)
            classic_selected = 'selection' not in c and index == self.selected_clip and not clip.muted
            p.setPen(c['background'] if classic_selected else c['text'])
            p.drawText(rect.adjusted(5 + HANDLE, 0, -4 - HANDLE, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       f'{clip.name} (muted)' if clip.muted else clip.name)
        self.paint_audio_track(p, c)
        if self.roll == TRANSFORM_ROLL: self.paint_lanes(p, c)
        # Notes.
        p.setClipRect(QRectF(GUTTER, self.roll_top, w - GUTTER, self.note_bottom() - self.roll_top))
        if self.roll == NOTE_ROLL:
            visible = self.visible_notes()
            self.paint_notes(p, c, visible)
            self.paint_note_overlaps(p, c, visible)
            self.paint_dropped_voices(p, c)
        if self.selection_rect is not None:
            fill = QColor(c['accent']); fill.setAlpha(m.selection_alpha)
            p.fillRect(self.selection_rect, fill)
            p.setPen(QPen(c['accent'], 1, Qt.PenStyle.DashLine)); p.drawRect(self.selection_rect)
        p.setClipping(False)
        p.save(); p.setClipRect(QRectF(GUTTER, 0, w - GUTTER, m.ruler_height))
        for index, (beat, name) in enumerate(self.project.markers):
            rect = self.marker_rect(index)
            p.fillRect(rect, c['grid']); p.setPen(c['accent'])
            p.drawLine(QPointF(rect.left(), rect.top()), QPointF(rect.left(), m.ruler_height - 1))
            text = QFontMetrics(self.font()).elidedText(name, Qt.TextElideMode.ElideRight, max(0, int(rect.width() - m.marker_text_room)))
            if 'accent_text' in c: p.setPen(c['accent_text'])  # Modern: a caption colour that reads on the flag fill.
            p.drawText(rect.adjusted(m.marker_text_inset, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, text)
        p.restore()
        p.save(); p.setClipRect(QRectF(GUTTER, 0, w - GUTTER, 2 * m.ruler_flag_height))
        flag_font = QFont(self.font()); flag_font.setPixelSize(themes.for_widget(self).fonts.timeline_small_px); p.setFont(flag_font)
        for kind, index, beat, label in self.flags():
            rect = self.flag_rect(kind, beat)
            p.fillRect(rect, c['grid']); p.setPen(c.get('accent_text', c['accent']) if kind == 'tempo' else c['text'])
            p.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        p.restore()
        # The playhead and chord pills come next in paint_dynamic, then (here) the velocity lane:
        # it covers the playhead, which paint_dynamic stops above it.
        self.selection_pill = self.selection_pill_args()
        self.paint_velocity_lane(p, c)
        p.fillRect(QRectF(0, m.ruler_height, GUTTER - 2, 1), c['grid'])

    def note_rect(self, note, start=None):
        start = note.start if start is None else start
        return QRectF(self.x_of(start), self.y_of(note.pitch) + 1,
                      max(2., (note.end - start) * self.scale), self.row - 1)

    def visible_notes(self):
        """(index, note) of the notes drawn in the roll, in score order.

        The index narrows the search to notes whose span reaches the view (with the 2-pixel
        minimum width as margin); the exact rectangle test decides, as before.
        """
        room = QRectF(GUTTER, self.roll_top, self.width() - GUTTER, self.note_bottom() - self.roll_top)
        notes = self.project.notes
        low, high = self.beats_at(GUTTER) - 2. / self.scale - 1e-6, self.beats_at(self.width()) + 1e-6
        return [(i, notes[i]) for i in self.note_index().overlapping(low, high) if self.note_rect(notes[i]).intersects(room)]

    def paint_notes(self, p, c, visible):
        m = self.metrics
        for index, note in visible:
            rect = self.note_rect(note)
            chosen = index == self.selected_note or index in self.note_selection
            fill = QColor(c['trace']); fill.setAlpha(m.selected_alpha if chosen else m.note_alpha)
            p.fillRect(rect, fill)
            p.setPen(QPen(c['text'] if chosen else c['trace'].darker(220), 1)); p.drawRect(rect)
            if note.glide > 0:
                p.setPen(QPen(c['text'], 1))
                p.drawLine(QPointF(rect.left() + 1, rect.bottom() - 1),
                           QPointF(rect.left() + min(9., rect.width() - 1), rect.top() + 1))

    def note_overlaps(self, visible):
        """Union of spans with at least two visible notes on the same pitch row."""
        rows = {}
        for _, note in visible:
            events = rows.setdefault(note.pitch, {})
            events[note.start] = events.get(note.start, 0) + 1
            events[note.end] = events.get(note.end, 0) - 1
        spans = []
        for pitch, events in sorted(rows.items()):
            count, start = 0, None
            for beat, delta in sorted(events.items()):
                after = count + delta
                if count < 2 <= after: start = beat
                if count >= 2 > after: spans.append((pitch, start, beat))
                count = after
        return tuple(spans)

    def paint_note_overlaps(self, p, c, visible):
        for pitch, start, end in self.note_overlaps(visible):
            rect = self.note_rect(Note(start, end - start, pitch))
            p.fillRect(rect, c['trace'].darker(180))
            p.setPen(QPen(c['trace'].darker(260), 2)); p.drawRect(rect)

    def chord_layout(self):
        """chord_voices for the current notes, kept per notes tuple so repaints skip hashing every note."""
        notes = self.project.notes
        if getattr(self, '_chord_layout', (None,))[0] is not notes: self._chord_layout = (notes, chord_voices(notes))
        return self._chord_layout[1]

    def dropped_voices(self):
        return self.chord_layout().dropped if self.project is not None and self.project.chords else ()

    def paint_dropped_voices(self, p, c):
        m = self.metrics
        for index, beat in self.dropped_voices():
            rect = self.note_rect(self.project.notes[index], beat)
            # Erase the normal note/overlap fill before drawing the faded remainder.
            p.fillRect(rect.adjusted(-1, -1, 1, 1), c['background'])
            fill = QColor(c['trace']); fill.setAlpha(m.dropped_voice_alpha)
            edge = QColor(c['text']); edge.setAlpha(m.dropped_edge_alpha)
            p.fillRect(rect, fill); p.setPen(QPen(edge, 1, Qt.PenStyle.DashLine)); p.drawRect(rect)
            p.setPen(QPen(c['text'], 1))
            p.drawLine(QPointF(rect.left(), rect.top()), QPointF(rect.left(), rect.bottom()))
            if rect.width() >= 18 and self.row >= 8:
                p.drawText(rect.adjusted(3, -2, -2, 2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, '×')

    def playhead_chord(self):
        if self.project is None or not self.project.chords: return ''
        return chord_name(sounding_pitches(self.project.notes, self.playhead, self.chord_layout()))

    def selection_chord(self):
        if self.project is None or len(self.note_selection) < 2: return ''
        return chord_name(self.project.notes[i].pitch for i in sorted(self.note_selection))

    def paint_chord_pill(self, p, c, name, x, y, flip=False):
        if not name: return
        rect = self.pill_rect(name, x, y, flip, QFontMetrics(p.font()))
        p.setBrush(c['background']); p.setPen(QPen(c['grid'], 1)); p.drawRoundedRect(rect, 4, 4)
        p.setBrush(Qt.BrushStyle.NoBrush); p.setPen(c['text'])
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, name)

    def selection_pill_args(self):
        """(name, x, y) of the selected notes' chord pill, or None. Worked out with the static layer
        (selection changes always call update()), so playback frames don't walk a large selection."""
        name = self.selection_chord() if self.roll == NOTE_ROLL else ''
        if not name: return None
        notes = [self.project.notes[i] for i in self.note_selection]
        return name, self.x_of(min(n.start for n in notes)), self.y_of(max(n.pitch for n in notes)) - QFontMetrics(self.pill_font()).height() - 6

    def paint_chord_labels(self, p, c):
        p.save()
        p.setClipRect(QRectF(GUTTER, self.roll_top, self.width() - GUTTER, self.note_bottom() - self.roll_top))
        p.setFont(self.pill_font())
        self.paint_chord_pill(p, c, self.playhead_chord(), self.x_of(self.playhead) + 4, self.roll_top + 2, flip=True)
        selection = getattr(self, 'selection_pill', None)
        if selection is not None: self.paint_chord_pill(p, c, *selection)
        p.restore()

    def dropped_voice_tooltip(self, x, y):
        if self.roll != NOTE_ROLL or x < GUTTER or y < self.roll_top or y >= self.note_bottom(): return ''
        for index, beat in self.dropped_voices():
            if self.note_rect(self.project.notes[index], beat).contains(QPointF(x, y)):
                return 'Voice dropped: more than 8 notes sound here'
        return ''

    def paint_lanes(self, p, c):
        m = self.metrics
        clip, w, h = self.clip, self.width(), self.height()
        top = self.roll_top + LANES_TOP - 2
        p.save(); p.setClipRect(QRectF(0, top, w, h - top))
        shown = self.lane_blocks()
        self.lane_scroll = min(self.lane_scroll, self.lane_room())  # Blocks may have gone since the last scroll.
        if clip is None or not shown:
            p.setPen(c['text'])
            p.drawText(QRectF(GUTTER + 8, top + 2, w - GUTTER - 16, 60), int(Qt.AlignmentFlag.AlignLeft) | int(Qt.TextFlag.TextWordWrap),
                       'No automation in this clip. Right-click a number in SCENE, Transform or MIX › Automate to add a gain lane here,'
                       ' or an object or transform row › Automate On/Off.')
            p.restore(); return
        faint = QColor(c['grid']); faint.setAlpha(m.lane_grid_alpha)
        clip_left, clip_right = self.x_of(clip.start), self.x_of(clip.end)
        metrics = QFontMetrics(self.font())
        for index, block in enumerate(shown):
            rect = self.block_rect(index)
            if rect.bottom() < top or rect.top() > h: continue
            shade = QColor(c['accent']); shade.setAlpha(m.lane_shade_alpha)
            left = max(GUTTER, clip_left); p.fillRect(QRectF(left, rect.top(), max(0., min(w, clip_right) - left), rect.height()), shade)
            p.setPen(c['grid']); p.drawRect(rect)
            p.setPen(c['text'])
            p.drawText(QRectF(2, rect.top() + LEGEND, GUTTER - 6, rect.height() - LEGEND),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       metrics.elidedText(block.title.split(' › ')[-1], Qt.TextElideMode.ElideRight, GUTTER - 6))
            active = self.active_key(block)
            title = QRectF(rect.right() - metrics.horizontalAdvance(block.title) - 8, rect.top() + 1, metrics.horizontalAdvance(block.title) + 4, LEGEND - 2)
            p.setPen(c['text']); p.drawText(title, Qt.AlignmentFlag.AlignCenter, block.title)
            for slot, (key, chip) in enumerate(self.legend_rects(block, rect)):
                lanes = themes.for_widget(self).lane_colors; color = QColor(lanes[slot % len(lanes)]); points = get_points(clip, key)
                if not points: color.setAlpha(m.empty_lane_alpha)
                elif is_parked(key): color.setAlpha(m.parked_lane_alpha)  # Parked (object Replace): kept, no effect.
                if key == active: p.fillRect(chip, QColor(color.red(), color.green(), color.blue(), m.active_chip_alpha))
                p.setPen(c.get('muted', color) if not points or is_parked(key) else color)
                p.drawText(chip, Qt.AlignmentFlag.AlignCenter, lane_label(clip, key))
                if not points: continue
                if is_switch(key): self.paint_switch(p, c, rect, points, color); continue
                nodes = self.node_points(rect, points)
                reference = self.reference(key, points)
                if len(lane_edit.on_reference(points, reference)) < len(points):
                    # Dotted line at the lane's reference (first node) once a node leaves it.
                    y = self.gain_y(rect, reference); guide = QColor(color); guide.setAlpha(m.active_guide_alpha if key == active else m.guide_alpha)
                    p.setPen(QPen(guide, 1, Qt.PenStyle.DotLine)); p.drawLine(QPointF(max(GUTTER, clip_left), y), QPointF(min(w, clip_right), y))
                p.setPen(QPen(color, 2 if key == active else 1))
                p.drawLine(QPointF(max(GUTTER, clip_left), nodes[0].y()), nodes[0])
                for index in range(len(points)-1):
                    polyline = self.segment_polyline(rect, points, index, nodes)
                    for a, b in zip(polyline, polyline[1:]): p.drawLine(a, b)
                p.drawLine(nodes[-1], QPointF(min(w, clip_right), nodes[-1].y()))
                if key == active:
                    picked = self.node_selection[1] if self.node_selection[0] == key else ()
                    for index, node in enumerate(nodes):
                        p.fillRect(QRectF(node.x() - NODE, node.y() - NODE, 2 * NODE, 2 * NODE), color)
                        if index in picked:
                            p.setPen(QPen(c['text'], 2)); p.drawRect(QRectF(node.x() - NODE - 2, node.y() - NODE - 2, 2 * NODE + 4, 2 * NODE + 4))
        p.restore()

    def switch_rect(self, rect):
        """An On/Off block's drawing area: under the legend, across the clip."""
        left, right = max(GUTTER, self.x_of(self.clip.start)), min(self.width(), self.x_of(self.clip.end))
        return QRectF(left, rect.top() + LEGEND, max(0., right - left), rect.height() - LEGEND - 3)

    def paint_switch(self, p, c, rect, points, color):
        m = self.metrics
        clip = self.clip
        fill = QColor(color); fill.setAlpha(m.switch_alpha)
        self.switch_strip.set_rect(self.switch_rect(rect))
        items = [LaneItem(clip.start + a, 1. if on else 0., end=clip.start + b)
                 for a, b, on in switch_lane.blocks(points, clip.length)]
        self.switch_strip.paint(p, dict(background=QColor(0, 0, 0, 0), grid=c['grid'], trace=fill, text=c['text']), self.x_of, items)

    def switch_press(self, event, key, x):
        clip = self.clip; points = get_points(clip, key)
        beat = min(max(0., self.beats_at(x) - clip.start), clip.length)
        if event.button() == Qt.MouseButton.RightButton:
            # Opened after the release, like the other roll menus (macOS swallows it otherwise).
            self.pending_switch_menu = (key, beat, event.globalPosition().toPoint()); return
        if not points: self.set_lane(key, switch_lane.DEFAULT); return
        if not clip.start <= self.beats_at(x) <= clip.end: return
        # Paint the opposite of the state under the press; a click without moving flips the run.
        self.drag = ('switch', key, points, beat, not switch_lane.state_at(points, beat), x, False)

    def switch_move(self, x):
        _, key, original, press, on, press_x, moved = self.drag
        if not moved and abs(x - press_x) < CLICK_SLOP: return
        self.drag = (*self.drag[:6], True)
        clip = self.clip
        low, high = sorted((press, min(max(0., self.beats_at(x) - clip.start), clip.length)))
        start = self.snapped(low, math.floor, origin=clip.start)
        end = self.snapped(high, math.ceil, origin=clip.start) if self.snap else high
        points = switch_lane.paint(original, min(start, clip.length), math.inf if end >= clip.length else end, on)
        if points != get_points(clip, key): self.set_lane(key, points)

    def build_switch_menu(self, key, beat):
        points = get_points(self.clip, key)
        menu = QMenu(self)
        flip = menu.addAction('Turn off here' if switch_lane.state_at(points, beat) else 'Turn on here')
        flip.setEnabled(bool(points))
        flip.triggered.connect(lambda: self.set_lane(key, switch_lane.flip(points, beat, self.clip.length)))
        on = menu.addAction('Turn all on'); on.setEnabled(bool(points) and not switch_lane.all_on(points))
        on.triggered.connect(lambda: self.set_lane(key, switch_lane.DEFAULT))
        menu.addSeparator()
        remove = menu.addAction('Remove On/Off automation'); remove.setEnabled(bool(points))
        remove.triggered.connect(lambda: self.set_lane(key, ()))
        return menu

    # Transform roll hit tests and edits ------------------------------------------
    def block_at(self, y):
        for index, block in enumerate(self.lane_blocks()):
            rect = self.block_rect(index)
            if rect.top() <= y < rect.bottom() and y >= self.roll_top + LANES_TOP - 2: return block, rect
        return None, None

    def node_at(self, block, rect, x, y):
        key = self.active_key(block)
        for index, node in enumerate(self.node_points(rect, get_points(self.clip, key))):
            if abs(node.x() - x) <= NODE + 2 and abs(node.y() - y) <= NODE + 2: return key, index
        return key, None

    def reference(self, key, points):
        """The lane's reference gain; frozen at mouse-press while that lane is dragged."""
        if self.drag and self.drag[0] in ('node', 'segment', 'bend') and self.drag[1] == key: return self.drag[-1]
        return lane_edit.reference_gain(points)

    def snap_tolerance(self, rect, modifiers):
        """Snap distance in gain units (SNAP_PIXELS on screen); 0 while Ctrl/Cmd is held."""
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier): return 0.
        return abs(self.gain_at_y(rect, rect.center().y()) - self.gain_at_y(rect, rect.center().y() + SNAP_PIXELS))

    def segment_polyline(self, rect, points, index, nodes=None):
        if nodes is None: nodes = self.node_points(rect, points)
        a, b = nodes[index:index+2]
        p, q = points[index:index+2]
        if p[2] == 'hold': return [a, QPointF(b.x(), a.y()), b]
        if p[2] != 'curve': return [a, b]
        result = []
        for n in range(24):
            u = n / 23
            gain = (1-u)**3*p[1] + 3*(1-u)**2*u*p[3] + 3*(1-u)*u*u*p[4] + u**3*q[1]
            result.append(QPointF(a.x() + u*(b.x()-a.x()), self.gain_y(rect, gain)))
        return result

    def segment_at(self, rect, points, x, y):
        """Index of the segment's left node under (x, y), or None (vertical jump steps excluded)."""
        nodes = self.node_points(rect, points)
        for index in range(len(points)-1):
            polyline = self.segment_polyline(rect, points, index, nodes)
            for a, b in zip(polyline, polyline[1:]):
                if b.x() - a.x() < 1e-9 or not a.x() <= x <= b.x(): continue
                line_y = a.y() + (b.y()-a.y())*(x-a.x())/(b.x()-a.x())
                if abs(line_y-y) <= SEGMENT_PIXELS: return index
        return None

    def reference_menu(self, key, points, position):
        """Right-click on the reference line: type a new reference gain."""
        reference = lane_edit.reference_gain(points)
        try: value = static_value(self.clip, key); detail = f'\n{lane_label(self.clip, key)}: {value:.4g} × gain'
        except (KeyError, ValueError, TypeError): detail = ''
        gain, ok = QInputDialog.getDouble(self, 'Reference gain', f'New reference gain ({GAIN_RANGE[0]:g} to {GAIN_RANGE[1]:g}).'
                                          f' Nodes on the reference line move with it; other nodes stay.{detail}',
                                          reference, GAIN_RANGE[0], GAIN_RANGE[1], 3)
        if ok and abs(gain - reference) > 1e-9: self.set_lane(key, lane_edit.set_reference(points, gain))

    def node_gain_dialog(self, key, points, index, indices):
        gain, ok = QInputDialog.getDouble(self, 'Node gain',
                                          f'New node gain ({GAIN_RANGE[0]:g} to {GAIN_RANGE[1]:g}).',
                                          points[index][1], GAIN_RANGE[0], GAIN_RANGE[1], 3)
        if ok:
            result = lane_edit.set_gains(points, indices, gain)
            if result != points: self.set_lane(key, result)

    def set_lane(self, key, points):
        self.emit(with_clip(self.project, self.selected_clip, set_points(self.clip, key, tuple(points))))

    def lane_press(self, event, x, y):
        self.pressed_node = None
        block, rect = self.block_at(y)
        if block is None or x < GUTTER: return
        clip = self.clip
        for key, chip in self.legend_rects(block, rect):
            if chip.contains(QPointF(x, y)):
                self.active[block.key] = key
                if not is_switch(key): self.lane_target = key  # Gain nodes never paste into On/Off.
                if not get_points(clip, key): self.set_lane(key, default_points(clip, key))
                self.update(); return
        if is_switch(block.key): self.switch_press(event, block.key, x); return
        key, index = self.node_at(block, rect, x, y)
        # A double-click types a gain only on a node that was there before the first click.
        self.pressed_node = (key, index) if index is not None else None
        points = list(get_points(clip, key))
        self.lane_target, self.focus_kind = key, 'nodes'
        reference = lane_edit.reference_gain(points)
        if event.button() == Qt.MouseButton.RightButton:
            inside = self.x_of(clip.start) <= x <= self.x_of(clip.end)
            if index is not None:
                if index not in self.picked_nodes(key): self.node_selection = (key, {index})
                self.pending_lane_node_menu = (key, tuple(points), index, event.globalPosition().toPoint())
            elif (segment := self.segment_at(rect, points, x, y)) is not None and points[segment][2] == 'curve':
                self.pending_segment_menu = (key, tuple(points), segment, event.globalPosition().toPoint())
            elif points and inside and abs(y - self.gain_y(rect, reference)) <= 4:  # The reference line itself.
                # Opened after the release (mouseReleaseEvent): a modal dialog inside the press
                # handler swallows the release on macOS, and the window then acts as if the
                # button were still held, so clicks elsewhere (even Play) misbehave.
                self.pending_reference = (key, tuple(points), event.globalPosition().toPoint())
            else: self.paste_menu(event.globalPosition().toPoint())
            return
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if index is not None and shift:
            # Shift adds the node and still drags (gain only); a Shift-click without moving
            # on a node that was already selected takes it out of the selection.
            was = index in self.picked_nodes(key)
            self.node_selection = (key, self.picked_nodes(key) | {index})
            self.node_press = (x, y)
            self.drag = ('node', key, index, rect, was, False, reference); self.update(); return
        if not points: self.active[block.key] = key; self.set_lane(key, default_points(clip, key)); return
        if index is None:
            segment = self.segment_at(rect, points, x, y)
            if segment is not None:  # Drag the segment straight up or down.
                self.node_selection = (key, set())
                # Released without moving, it adds a node on the line instead (mouseReleaseEvent).
                bend = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
                if bend and self.alt_wheel_filter is not None: self.alt_wheel_filter.mark()
                self.drag = ('bend' if bend else 'segment', key, segment, rect, tuple(points), y, (x, False), reference); return
            beat = min(max(0., self.snapped(self.beats_at(x) - clip.start, origin=clip.start)), clip.length)
            if any(abs(p[0] - beat) < 1e-6 for p in points): return
            gain = lane_edit.snap(round(self.gain_at_y(rect, y), 4), reference, self.snap_tolerance(rect, event.modifiers()))
            points.append((beat, gain, 'linear')); points.sort(key=lambda point: point[0])
            index = next(i for i, point in enumerate(points) if point[0] == beat)
            self.set_lane(key, points)
        if index not in self.picked_nodes(key): self.node_selection = (key, {index})
        self.node_press = (x, y)  # A node moves only once the press leaves CLICK_SLOP.
        self.drag = ('node', key, index, rect, False, False, reference)

    def lane_move(self, event, x, y):
        modifiers = event.modifiers()
        if self.drag[0] in ('segment', 'bend'):
            _, key, index, rect, original, press_y, (press_x, moved), reference = self.drag
            if not moved:
                if abs(y - press_y) <= CLICK_SLOP and abs(x - press_x) <= CLICK_SLOP: return  # Still a click.
                self.drag = (*self.drag[:6], (press_x, True), reference)
            delta = self.gain_at_y(rect, y) - self.gain_at_y(rect, press_y)
            delta = lane_edit.snap_delta(original, index, delta, reference, self.snap_tolerance(rect, modifiers))
            if self.drag[0] == 'bend': points = lane_edit.bend_segment(original, index, self.gain_at_y(rect, y) - self.gain_at_y(rect, press_y))
            else: points, _ = lane_edit.drag_segment(original, index, delta)
            if points != get_points(self.clip, key): self.set_lane(key, points)
            return
        _, key, index, rect, was, moved, reference = self.drag
        # Click jitter would re-read the gain from the pixel row and nudge it (0.25 to 0.2453).
        press_x, press_y = self.node_press
        if not moved and abs(x - press_x) < CLICK_SLOP and abs(y - press_y) < CLICK_SLOP: return  # 1–2 px.
        self.drag = ('node', key, index, rect, was, True, reference)  # Moved: not a click.
        points = get_points(self.clip, key)
        if index >= len(points): return
        beat, gain = points[index][:2]
        if not modifiers & Qt.KeyboardModifier.ShiftModifier:  # Shift: gain only.
            beat = self.snapped(self.beats_at(x) - self.clip.start, origin=self.clip.start)
        if not modifiers & Qt.KeyboardModifier.AltModifier:  # Alt/Option: time only.
            gain = lane_edit.snap(self.gain_at_y(rect, y), reference, self.snap_tolerance(rect, modifiers))
        points = lane_edit.move_node(points, index, beat, gain, self.clip.length)
        if points != get_points(self.clip, key): self.set_lane(key, points)

    def node_menu(self, key, points, index, position):
        self.build_node_menu(key, points, index).exec(position)

    def build_segment_menu(self, key, points, index):
        menu = QMenu(self)
        action = menu.addAction('Straighten'); action.setEnabled(points[index][2] == 'curve')
        action.triggered.connect(lambda: self.set_lane(key, lane_edit.straighten(tuple(points), index)))
        return menu

    def build_node_menu(self, key, points, index):
        points = tuple(points)
        menu = QMenu(self)
        self.edit_actions(menu)
        delete = menu.addAction('Delete node')
        linear = menu.addAction('Linear to next'); linear.setCheckable(True); linear.setChecked(points[index][2] == 'linear')
        hold = menu.addAction('Hold to next'); hold.setCheckable(True); hold.setChecked(points[index][2] == 'hold')
        menu.addSeparator(); remove = menu.addAction(f'Remove automation of {lane_label(self.clip, key)}')
        delete.triggered.connect(lambda: self.set_lane(key, points[:index] + points[index+1:]))
        for action, shape in ((linear, 'linear'), (hold, 'hold')):
            action.triggered.connect(lambda checked=False, shape=shape: self.set_lane(key, points[:index] + ((*points[index][:2], shape),) + points[index+1:]))
        remove.triggered.connect(lambda: self.set_lane(key, ()))
        return menu

    def lane_tooltip(self, x, y):
        block, rect = self.block_at(y)
        if block is None or x < GUTTER: return ''
        key = self.active_key(block); clip = self.clip
        beat, gain = self.beats_at(x) - clip.start, self.gain_at_y(rect, y)
        if is_switch(key):
            points = get_points(clip, key)
            if not points: return 'On/Off: click to automate'
            state = 'on' if switch_lane.state_at(points, beat) else 'off'
            return f'On/Off: {state} at beat {beat:.2f} of the clip. Drag to paint, click a run to flip it.'
        try: value = f'{static_value(clip, key) * gain:.4g}'
        except (KeyError, ValueError, TypeError): value = '—'
        parked = ' (no effect until the object is that type again)' if is_parked(key) else ''
        return f'{lane_label(clip, key)}: gain {gain:.2f} → {value} at beat {beat:.2f} of the clip{parked}'

    # Audio track ------------------------------------------------------------------
    def set_audio_gain_lane(self, on):
        self.set_roll_option('roll_audio_gain_lane', bool(on))

    def gain_lane_rect(self, track=None):
        """Track's gain lane (default: the selected clip's track, else the first), or None while hidden."""
        if not self.roll_audio_gain_lane: return None
        if track is None:
            selected = self.selected_audio
            track = self.project.audio_clips[selected].track if selected is not None and selected < len(self.project.audio_clips) else 0
        row = self.track_row(track)
        if row.lane is None: return None
        return QRectF(GUTTER, row.lane, max(0, self.width() - GUTTER), GAIN_LANE)

    def audio_seconds(self, beats): return float(self.project.seconds(beats))

    def audio_span(self, clip):
        return clip.start, float(audio_clip_end_beats(self.project, clip))

    def audio_rect(self, clip):
        a, b = self.audio_span(clip); row = self.track_row(clip.track)
        return QRectF(self.x_of(a), row.top + 3, max(2., (b - a) * self.scale), row.height - 6)

    def fade_handles(self, clip):
        """x of the fade-in end and of the fade-out start (the top-corner squares)."""
        s0 = self.audio_seconds(clip.start)
        return (self.x_of(float(self.project.beats(s0 + clip.fade_in))),
                self.x_of(float(self.project.beats(s0 + clip.duration - clip.fade_out))))

    def audio_order(self):
        """Audio clip indices, topmost (drawn last, hit first) first: the selected one, then later ones."""
        order = list(reversed(range(len(self.project.audio_clips))))
        if self.selected_audio in order: order.remove(self.selected_audio); order.insert(0, self.selected_audio)
        return order

    def audio_hit(self, x, y):
        """(index, part) under (x, y) in the Audio track; part is 'fade-in', 'fade-out', 'left', 'right' or None."""
        row, part = self.track_at(y)
        if x < GUTTER or part != 'track': return None, None
        for index in self.audio_order():
            clip = self.project.audio_clips[index]
            if clip.track != row.index: continue
            rect = self.audio_rect(clip)
            if not rect.left() - FADE_HANDLE <= x <= rect.right() + FADE_HANDLE: continue
            if y <= rect.top() + 2 * FADE_HANDLE:
                fade_in, fade_out = self.fade_handles(clip)
                if abs(x - fade_out) <= FADE_HANDLE + 1 and x >= fade_in: return index, 'fade-out'
                if abs(x - fade_in) <= FADE_HANDLE + 1: return index, 'fade-in'
            if rect.left() <= x <= rect.right():
                if rect.right() - x <= EDGE: return index, 'right'
                if x - rect.left() <= EDGE and rect.width() > 3 * EDGE: return index, 'left'
                return index, None
        return None, None

    def audio_loud(self, clip):
        """True when the clip alone passes full scale (gain × the file's peak)."""
        if clip.missing: return False
        wave = waveform(clip.path, clip.fingerprint)
        return wave is not None and wave.peak * clip.gain > 1.

    # The output meter sits in the ruler's gutter; each track header has its own (AW1).
    def clip_lamp_rect(self): return QRectF(GUTTER - 15, 6, 9, 9)

    def meter_rect(self): return QRectF(4, 20, GUTTER - 11, 4)

    def header_line_rect(self, row, x, y, width, height, line):
        # A partially visible last track can still fit its three lines above the Add row.
        if row.height == AUDIO_TRACK and themes.for_widget(self).style == themes.MODERN:
            visible = self.audio_top + self.track_extent[1] - row.top
            if 30 <= visible < 43:
                line_height = min(12, int(visible) // 3)
                y, height = line * line_height, line_height
        return QRectF(x, row.top + y, width, height)

    def name_rect(self, row):
        width = GUTTER - 8 if row.height == AUDIO_TRACK else GUTTER - 40
        if self.track_fx_rect(row) is not None: width -= 13
        return self.header_line_rect(row, 4, 2, width, 14, 0)

    def track_fx_rect(self, row):
        """The header's fx badge (expanded tracks with effects): click to open Track effects."""
        if row.height != AUDIO_TRACK or row.track is None or not row.track.effects: return None
        return self.header_line_rect(row, GUTTER - 16, 3, 13, 12, 0)

    def mute_rect(self, row):
        return self.header_line_rect(row, 4, 17, 15, 12, 1) if row.height == AUDIO_TRACK else QRectF(GUTTER - 35, row.top + 3, 15, 12)

    def solo_rect(self, row):
        return self.header_line_rect(row, 21, 17, 15, 12, 1) if row.height == AUDIO_TRACK else QRectF(GUTTER - 18, row.top + 3, 15, 12)

    def header_right_room(self, rect):
        # Modern leaves room at the right edge for the track scrollbar.
        return rect.adjusted(0, 0, -2, 0) if themes.for_widget(self).style == themes.MODERN else rect

    def track_gain_rect(self, row):
        if row.height != AUDIO_TRACK: return None
        return self.header_right_room(self.header_line_rect(row, 37, 17, GUTTER - 39, 12, 1))

    def track_pan_rect(self, row):
        return self.header_line_rect(row, 4, 31, 26, 12, 2) if row.height == AUDIO_TRACK else None

    def track_phase_rect(self, row):
        if row.height != AUDIO_TRACK: return None
        return self.header_right_room(self.header_line_rect(row, 32, 31, GUTTER - 34, 12, 2))

    def header_hit_targets(self, row):
        """(kind, rect) of a track header's controls in precedence order: the rectangles painted,
        except the fx badge and the clip lamp, which take a slightly larger click area clipped so
        that no two targets overlap. Presses, tooltips and double-clicks all use these."""
        if row.track is None: return []
        mute, solo, fx = self.mute_rect(row), self.solo_rect(row), self.track_fx_rect(row)
        gain, pan, phase, lamp = (self.track_gain_rect(row), self.track_pan_rect(row),
                                  self.track_phase_rect(row), self.track_lamp_rect(row))
        targets = []
        if lamp is not None:
            hit = lamp.adjusted(-3, -3, 3, 3)
            if phase is not None: hit.setTop(max(hit.top(), phase.bottom()))
            targets.append(('lamp', hit))
        targets += [('mute', mute), ('solo', solo)]
        if fx is not None:
            hit = fx.adjusted(-1, -1, 1, 1)
            if gain is not None: hit.setBottom(min(hit.bottom(), gain.top()))
            hit.setRight(min(hit.right(), GUTTER - 2))
            targets.append(('fx', hit))
        targets += [(kind, rect) for kind, rect in (('gain', gain), ('pan', pan), ('phase', phase)) if rect is not None]
        targets.append(('name', self.name_rect(row)))
        return targets

    def header_target(self, row, point):
        """The kind of header control under point, or None."""
        return next((kind for kind, rect in self.header_hit_targets(row) if rect.contains(point)), None)

    def track_lamp_rect(self, row):
        return QRectF(GUTTER - 11, row.top + 45, 7, 7) if row.height == AUDIO_TRACK else None

    def track_meter_rect(self, row):
        return QRectF(4, row.top + 47, GUTTER - 19, 4) if row.height == AUDIO_TRACK else None

    def set_meter(self, peak):
        """Show the summed output's peak since the last call; returns True when it clipped."""
        peak = float(peak)
        level = max(peak, self.meter_level * .85)
        if level < 1e-4: level = 0.
        clipped = self.meter_clipped or peak > 1.
        if level != self.meter_level or clipped != self.meter_clipped:
            self.meter_level, self.meter_clipped = level, clipped
            self.update(0, 0, GUTTER, self.metrics.ruler_height)
        return peak > 1.

    def set_track_meters(self, peaks):
        """Show each track's peak since the last call (decaying like the output meter); a track
        whose peak passes full scale latches its lamp."""
        count = len(self.project.audio_tracks) if self.project is not None else 0
        old, clipped = self.track_meters, set(self.track_clipped)
        levels = []
        for t in range(count):
            peak = float(peaks[t]) if t < len(peaks) else 0.
            level = max(peak, (old[t] if t < len(old) else 0.) * .85)
            levels.append(0. if level < 1e-4 else level)
            if peak > 1.: self.track_clipped.add(t)
        if levels != old or clipped != self.track_clipped:
            self.track_meters = levels
            self.update(0, int(self.audio_top), GUTTER, int(self.audio_end - self.audio_top))

    def reset_meter(self):
        self.meter_clipped = False; self.update()

    def reset_track_meter(self, t):
        self.track_clipped.discard(t); self.update()

    def paint_audio_track(self, p, c):
        m = self.metrics
        w = self.width()
        drop_row = self.drop_track
        rows = self.track_rows()
        p.save(); p.setClipRect(QRectF(0, self.audio_top, w, self.audio_end - self.audio_top))
        self.paint_track_rows(p, c, rows, drop_row)
        total, shown = self.track_extent
        if total > shown:  # A thin bar at the gutter's edge: where the shown rows are among all of them.
            bar = QColor(c['text']); bar.setAlpha(m.track_handle_alpha)
            p.fillRect(QRectF(GUTTER - 3, self.audio_top + shown * self.track_scroll / total, 2, max(6., shown * shown / total)), bar)
        p.restore()
        self.paint_add_row(p, c, drop_row)

    def paint_track_rows(self, p, c, rows, drop_row):
        m = self.metrics
        w = self.width()
        for row in rows:
            top, height = row.top, row.height
            if self.dragging_audio:
                band = QColor(c['accent']); band.setAlpha(m.drop_target_alpha if self.drop_beat is not None and drop_row == row.index else m.drop_row_alpha)
                p.fillRect(QRectF(GUTTER, top, w - GUTTER, height), band)
            p.setPen(c['grid']); p.drawLine(0, int(top + height), w, int(top + height))
            self.paint_track_header(p, c, row)
            if top > self.audio_end or top + height + GAIN_LANE < self.audio_top: continue  # Scrolled out of view.
            p.save(); p.setClipRect(QRectF(GUTTER, top, w - GUTTER, height), Qt.ClipOperation.IntersectClip)
            for index in reversed(self.audio_order()):
                clip = self.project.audio_clips[index]
                if clip.track != row.index: continue
                rect = self.audio_rect(clip)
                if rect.right() < GUTTER or rect.left() > w: continue
                self.paint_audio_clip(p, c, index, clip, rect)
            if self.drop_beat is not None and drop_row == row.index:
                x = self.x_of(self.drop_beat); p.setPen(QPen(c['accent'], 2)); p.drawLine(QPointF(x, top), QPointF(x, top + height))
            empty = not any(clip.track == row.index for clip in self.project.audio_clips)
            if empty and not self.dragging_audio and height == AUDIO_TRACK:
                hint = QColor(c['text']); hint.setAlpha(m.track_hint_alpha); p.setPen(hint)
                p.drawText(QRectF(GUTTER + 8, top, w - GUTTER - 16, height), Qt.AlignmentFlag.AlignVCenter,
                           'Drop audio files here, or double-click to add one')
            p.restore()
            self.paint_gain_lane(p, c, row)

    def paint_add_row(self, p, c, drop_row):
        m = self.metrics
        w = self.width()
        if not self.has_add_row: return
        add = self.add_row_rect()
        if self.dragging_audio:
            band = QColor(c['accent']); band.setAlpha(m.drop_target_alpha if self.drop_beat is not None and drop_row == -1 else m.drop_row_alpha)
            p.fillRect(add.adjusted(GUTTER, 0, 0, 0), band)
        hint = QColor(c['text']); hint.setAlpha(m.add_hint_alpha); p.setPen(hint)
        p.drawText(add.adjusted(GUTTER + 8, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   '+ Add audio track' + ('  (drop files here for a new track)' if self.dragging_audio else ''))
        p.setPen(c['grid']); p.drawLine(0, int(add.bottom()), w, int(add.bottom()))

    def paint_track_header(self, p, c, row):
        """Name, M, S, gain and (expanded) the meter of one track in the gutter."""
        m = self.metrics
        if row.track is None:
            p.setPen(c['text'])
            p.drawText(self.name_rect(row), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, 'Audio')
            return
        theme = themes.for_widget(self)
        if theme.style == themes.MODERN and (row.top >= self.audio_end or row.top + row.height <= self.audio_top):
            return
        t, track = row.index, row.track
        if t in self.track_picks:
            shade = QColor(c['accent']); shade.setAlpha(m.track_selected_alpha); p.fillRect(QRectF(0, row.top + 1, GUTTER - 2, row.height - 1), shade)
        base = QFont(p.font())
        font = QFont(base); font.setBold(t in self.track_picks)
        rect = self.name_rect(row)
        if theme.style == themes.MODERN:
            font.setPixelSize(theme.fonts.timeline_px)
            while font.pixelSize() > 8 and QFontMetrics(font).height() > rect.height():
                font.setPixelSize(font.pixelSize() - 1)
        p.setFont(font)
        p.setPen(c['text'])
        p.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(font).elidedText(track.name, Qt.TextElideMode.ElideRight, int(rect.width())))
        gain, pan, phase = self.track_gain_rect(row), self.track_pan_rect(row), self.track_phase_rect(row)
        gain_label, pan_label, phase_label = f'×{track.gain:.2f}', pan_text(track), phase_text(track)
        small = QFont(base); small.setPixelSize(theme.fonts.timeline_small_px)
        if theme.style == themes.MODERN and gain is not None:
            values = ((gain, gain_label), (pan, pan_label), (phase, phase_label))
            while True:
                metrics = QFontMetrics(small)
                if metrics.height() <= gain.height() and all(metrics.horizontalAdvance(s) <= r.width() for r, s in values):
                    break
                if small.pixelSize() <= 8:
                    gain_label, pan_label, phase_label = (
                        metrics.elidedText(s, Qt.TextElideMode.ElideRight, int(r.width())) for r, s in values)
                    break
                small.setPixelSize(small.pixelSize() - 1)
        p.setFont(small)
        fx = self.track_fx_rect(row)
        # What was drawn where, for the smokes to compare with header_hit_targets.
        self.header_painted[t] = dict(name=rect, mute=self.mute_rect(row), solo=self.solo_rect(row), fx=fx,
                                      gain=gain, pan=pan, phase=phase)
        if fx is not None:
            active = not track.effects_bypass and any(not e.bypass for e in track.effects)
            p.setPen(c['accent'] if active else c.get('muted', c['grid']))
            p.drawText(fx, Qt.AlignmentFlag.AlignCenter, 'fx')
        for box, text, on, color in ((self.mute_rect(row), 'M', track.muted, c.get('selection', c['accent'])),
                                     (self.solo_rect(row), 'S', t in self.audio_track_solos, QColor(themes.for_widget(self).colors['solo']))):
            p.setBrush(Qt.BrushStyle.NoBrush)
            if on: p.fillRect(box, color)
            else: p.setPen(c['text']); p.drawRect(box.adjusted(0, 0, -1, -1))
            ink = c.get('selection_text', c['background']) if text == 'M' else c['background']
            p.setPen(ink if on else c['text'])
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        if gain is not None:
            p.setPen(c['text']); p.drawText(gain, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, gain_label)
        if pan is not None:
            p.setPen(c['text'])
            p.drawText(pan, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, pan_label)
            p.drawText(phase, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, phase_label)
        p.setFont(base)
        self.paint_track_meter(p, c, row)

    def paint_track_meter(self, p, c, row):
        meter, lamp = self.track_meter_rect(row), self.track_lamp_rect(row)
        if meter is None or row.track is None: return
        t = row.index
        p.fillRect(lamp, QColor(themes.for_widget(self).colors['clip_red']) if t in self.track_clipped else c['grid'])
        p.fillRect(meter, c['grid'])
        level = self.track_meters[t] if t < len(self.track_meters) else 0.
        if level > 0:
            db = 20 * math.log10(max(level, 1e-9))
            fraction = min(1., max(0., (db - METER_FLOOR) / -METER_FLOOR))
            p.fillRect(QRectF(meter.left(), meter.top(), meter.width() * fraction, meter.height()),
                       QColor(themes.for_widget(self).colors['clip_red']) if level > 1 else c['trace'])

    def paint_meter(self, p, c):
        """The output meter and its clip lamp in the ruler's gutter, and every track's meter."""
        p.fillRect(QRectF(0, 0, GUTTER, self.metrics.ruler_height - 1), c['background'])
        p.setPen(c['text'])
        p.drawText(QRectF(4, 3, GUTTER - 22, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, 'Out')
        p.fillRect(self.clip_lamp_rect(), QColor(themes.for_widget(self).colors['clip_red']) if self.meter_clipped else c['grid'])
        meter = self.meter_rect(); p.fillRect(meter, c['grid'])
        if self.meter_level > 0:
            db = 20 * math.log10(max(self.meter_level, 1e-9))
            fraction = min(1., max(0., (db - METER_FLOOR) / -METER_FLOOR))
            p.fillRect(QRectF(meter.left(), meter.top(), meter.width() * fraction, meter.height()),
                       QColor(themes.for_widget(self).colors['clip_red']) if self.meter_level > 1 else c['trace'])
        if self.project is not None and self.project.audio_tracks:
            p.save(); p.setClipRect(QRectF(0, self.audio_top, GUTTER, self.audio_end - self.audio_top))
            for row in self.track_rows(): self.paint_track_meter(p, c, row)
            p.restore()

    def audio_silenced(self, index):
        """Whether audio clip `index` is silent: muted, on a muted track, or left out by a solo
        (audio_edit.soloed's rule: the clip or its track must be soloed while any audio solo is on)."""
        clip = self.project.audio_clips[index]
        track = self.project.audio_tracks[clip.track] if clip.track < len(self.project.audio_tracks) else None
        if clip.muted or (track is not None and track.muted): return True
        if not (self.audio_solos or self.audio_track_solos): return False
        return index not in self.audio_solos and clip.track not in self.audio_track_solos

    def paint_audio_clip(self, p, c, index, clip, rect):
        m = self.metrics
        selected = index == self.selected_audio; silent = self.audio_silenced(index)
        tint = QColor(c['accent']); fill = QColor(tint)
        alpha = m.audio_selected_alpha if selected else m.audio_alpha
        if silent: alpha //= m.mute_divisor
        fill.setAlpha(m.missing_alpha if clip.missing else alpha)
        p.fillRect(rect, fill)
        if clip.missing:
            hatch = QColor(tint); hatch.setAlpha(m.hatch_alpha)
            p.fillRect(rect, QBrush(hatch, Qt.BrushStyle.BDiagPattern))
        else:
            if silent: p.setOpacity(m.muted_waveform_opacity)
            self.paint_waveform(p, c, clip, rect)
            p.setOpacity(1.)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(c['text'] if selected else tint, 2 if selected else 1,
                      Qt.PenStyle.DashLine if clip.missing or silent else Qt.PenStyle.SolidLine))
        p.drawRect(rect)
        bar = QColor(c['text']); bar.setAlpha(m.selected_alpha if selected else m.audio_handle_alpha); width = min(HANDLE, rect.width() / 3)
        p.fillRect(QRectF(rect.left(), rect.top(), width, rect.height()), bar)
        p.fillRect(QRectF(rect.right() - width, rect.top(), width, rect.height()), bar)
        fade_in, fade_out = self.fade_handles(clip)
        p.setPen(QPen(c['text'], 1))
        if clip.fade_in: p.drawLine(QPointF(rect.left(), rect.bottom()), QPointF(fade_in, rect.top()))
        if clip.fade_out: p.drawLine(QPointF(fade_out, rect.top()), QPointF(rect.right(), rect.bottom()))
        for x in (fade_in, fade_out):
            x = min(max(x, rect.left() + FADE_HANDLE), rect.right() - FADE_HANDLE)
            p.fillRect(QRectF(x - FADE_HANDLE + 1, rect.top(), 2 * FADE_HANDLE - 2, 2 * FADE_HANDLE - 2), c['text'])
        label = clip.name + (' (muted)' if clip.muted else '')
        loud = self.audio_loud(clip) and not silent
        badge = self.audio_badge(index)
        metrics = QFontMetrics(p.font())
        badge_width = metrics.horizontalAdvance(badge) + 8 if badge else 0
        text = rect.adjusted(FADE_HANDLE + HANDLE + 4, 2 * FADE_HANDLE - 1, -HANDLE - 4 - (12 if loud else 0) - badge_width, 0)
        p.setPen(c['text'])
        p.drawText(text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                   metrics.elidedText(label, Qt.TextElideMode.ElideRight, max(0, int(text.width()))))
        if badge and rect.width() > badge_width + 2 * HANDLE + 8:
            box = QRectF(rect.right() - HANDLE - 4 - badge_width, rect.top() + 2 * FADE_HANDLE, badge_width, min(14., rect.height() - 2 * FADE_HANDLE - 2))
            p.fillRect(box, QColor(themes.for_widget(self).colors['clip_red']) if clip.missing else c['text'])
            p.setPen(QColor(themes.for_widget(self).colors['missing_text']) if clip.missing else c['background'])
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, badge)
        if loud:
            p.setPen(QColor(themes.for_widget(self).colors['clip_red']))
            p.drawText(QRectF(rect.right() - HANDLE - 14 - badge_width, rect.top() + 2 * FADE_HANDLE - 1, 10, 14), Qt.AlignmentFlag.AlignCenter, '!')

    def audio_badge(self, index):
        """The badge at a clip's top right: Missing, or S while soloed."""
        if self.project.audio_clips[index].missing: return 'Missing'
        return 'S' if index in self.audio_solos else ''

    def paint_waveform(self, p, c, clip, rect):
        """Peak per pixel column, shaped by the clip's gain, fades and gain lane (as it is heard)."""
        m = self.metrics
        wave = waveform(clip.path, clip.fingerprint)
        if wave is None or not len(wave.peaks): return
        left, right = max(GUTTER, rect.left()), min(self.width(), rect.right())
        if right - left < 1: return
        xs = np.arange(math.floor(left), math.ceil(right) + 1, dtype=float)
        local = np.asarray(self.project.seconds(np.maximum(0., self.beats_at(xs))), dtype=float) - self.audio_seconds(clip.start)
        index = np.clip(np.floor((clip.offset + local) / wave.block_seconds).astype(np.int64), 0, len(wave.peaks) - 1)
        starts, ends = index[:-1], np.maximum(index[1:], index[:-1] + 1)
        values = np.maximum.reduceat(wave.peaks, starts).astype(float)
        values[-1] = wave.peaks[starts[-1]:ends[-1]].max()
        middle = (local[:-1] + local[1:]) / 2
        valid = (middle >= 0) & (middle <= clip.duration) & (clip.offset + middle < source_end(clip))
        envelope = np.ones_like(middle)
        if clip.fade_in: envelope = np.minimum(envelope, middle / clip.fade_in)
        if clip.fade_out: envelope = np.minimum(envelope, (clip.duration - middle) / clip.fade_out)
        level = values * clip.gain * np.clip(envelope, 0., 1.)
        if clip.gain_points: level = level * gain_at(clip.gain_points, LANE_CLOCK, np.maximum(middle, 0.))
        half, centre = rect.height() / 2 - 2, rect.center().y()
        normal, over = [], []
        for x, value, ok in zip(xs[:-1], level, valid):
            height = min(1., value) * half
            if ok and height >= .5: (over if value > 1 else normal).append(QLineF(x + .5, centre - height, x + .5, centre + height))
        trace = QColor(c['trace']); trace.setAlpha(m.waveform_alpha)
        if normal: p.setPen(QPen(trace, 1)); p.drawLines(normal)
        if over: p.setPen(QPen(QColor(themes.for_widget(self).colors['clip_red']), 1)); p.drawLines(over)

    def gain_items(self, index):
        clip = self.project.audio_clips[index]; s0 = self.audio_seconds(clip.start)
        beats = np.asarray(self.project.beats(s0 + np.array([t for t, _, _ in clip.gain_points], dtype=float)), dtype=float)
        return [LaneItem(float(b), g, selected=index == self.selected_audio and k == self.gain_node, hold=shape == 'hold')
                for k, (b, (_, g, shape)) in enumerate(zip(beats, clip.gain_points))]

    def track_lane_items(self, t):
        track = self.project.audio_tracks[t]; points = track_lane_points(track, self.lane_for(t))
        beats = np.asarray(self.project.beats(np.array([s for s, _, _ in points], dtype=float)), dtype=float)
        return [LaneItem(float(b), v, selected=self.track_lane_node() == (t, k), hold=shape == 'hold')
                for k, (b, (_, v, shape)) in enumerate(zip(beats, points))]

    def paint_track_lane(self, p, c, row, rect):
        track = row.track; lane = self.lane_for(row.index)
        label = track_lane_label(track, lane)
        p.setPen(c['text'])
        p.drawText(QRectF(2, rect.top(), GUTTER - 6, rect.height()), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
        self.gain_strip.set_rect(rect)
        items = self.track_lane_items(row.index)
        start, end = 0., max(float(self.project.end_beats), self.beats_at(self.width()))
        default = lane_to(track, lane, track_static(track, lane))
        self.gain_strip.paint(p, c, self.x_of, items, span=(start, end), default=default)
        self.paint_tempo_bends(p, c, row.index)
        p.setPen(c['grid']); p.drawLine(QPointF(0, rect.bottom()), QPointF(self.width(), rect.bottom()))

    def paint_tempo_bends(self, p, c, t):
        """A track lane is linear in seconds, so across a tempo change it bends on the beat grid:
        redraw each linear segment that spans a tempo change through its value there."""
        if not self.project.tempo_changes: return
        points = track_lane_points(self.project.audio_tracks[t], self.lane_for(t))
        bends = [float(self.project.seconds(change[0])) for change in self.project.tempo_changes]
        p.save(); p.setClipRect(self.gain_strip.rect); p.setPen(QPen(c['trace'], 1.5))
        for (s0, v0, shape), (s1, v1, _) in zip(points, points[1:]):
            inside = [s for s in bends if s0 < s < s1]
            if shape == 'hold' or not inside: continue
            seconds = [s0, *inside, s1]
            values = [v0 + (v1 - v0) * (s - s0) / (s1 - s0) for s in seconds]
            beats = np.asarray(self.project.beats(np.array(seconds)), dtype=float)
            line = [QPointF(self.x_of(float(b)), self.gain_strip.y_of(v)) for b, v in zip(beats, values)]
            erase = QColor(c['background']); p.setPen(QPen(erase, 3))
            p.drawLine(line[0], line[-1])  # Hide the straight beat-grid line first.
            p.setPen(QPen(c['trace'], 1.5))
            for a, b in zip(line, line[1:]): p.drawLine(a, b)
            for point in (line[0], line[-1]): p.fillRect(QRectF(point.x() - 3, point.y() - 3, 6, 6), c['trace'])
        p.restore()

    def paint_gain_lane(self, p, c, row):
        m = self.metrics
        rect = self.gain_lane_rect(row.index)
        if rect is None: return
        if self.lane_for(row.index) != 'gain' and row.track is not None:
            self.paint_track_lane(p, c, row, rect); return
        p.setPen(c['text'])
        p.drawText(QRectF(2, rect.top(), GUTTER - 6, rect.height()), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, 'Gain')
        self.gain_strip.set_rect(rect)
        self.gain_strip.paint(p, c, self.x_of, [])
        for index in reversed(self.audio_order()):
            clip = self.project.audio_clips[index]
            if clip.track != row.index: continue
            span = self.audio_span(clip)
            if self.x_of(span[1]) < GUTTER or self.x_of(span[0]) > self.width(): continue
            selected = index == self.selected_audio
            trace = QColor(c['trace']); trace.setAlpha(m.selected_alpha if selected else m.gain_alpha)
            if selected:
                shade = QColor(c['accent']); shade.setAlpha(m.gain_shade_alpha)
                p.fillRect(QRectF(self.x_of(span[0]), rect.top(), (span[1] - span[0]) * self.scale, rect.height()).intersected(rect), shade)
            self.gain_strip.paint(p, dict(c, trace=trace), self.x_of, self.gain_items(index), span=span, background=False)
        p.setPen(c['grid']); p.drawLine(QPointF(0, rect.bottom()), QPointF(self.width(), rect.bottom()))

    def audio_tooltip(self, x, y):
        cursor = Qt.CursorShape.ArrowCursor
        if y < self.metrics.ruler_height and x < GUTTER:
            level = f'{20 * math.log10(self.meter_level):.1f} dB' if self.meter_level > 0 else 'silent'
            return (f'Output meter: the summed output\'s peak ({level}). The lamp turns red when the output clips'
                    ' (passes full scale); click it to reset.'), cursor
        row, part = self.track_at(y)
        if part == 'add':
            if x < GUTTER: return 'Gain: show a gain lane under every expanded audio track.', cursor
            return 'Click to add an audio track; drop audio files here to put them on a new track.', cursor
        if part == 'lane' and row.track is not None and self.lane_for(row.index) != 'gain':
            track = row.track; lane = self.lane_for(row.index)
            if x < GUTTER: return 'Track lane: right-click a track header or this lane › Lane to show Gain, Pan, Phase or an effect parameter.', cursor
            seconds = self.track_lane_seconds(x)
            self.gain_strip.set_rect(self.gain_lane_rect(row.index))
            value = track_value(track, lane, seconds)
            return (f'{track.name}: {lane_value_text(track, lane, value)} at {seconds:.2f} s. Click to add a node; drag a node '
                    '(Shift: value only, Alt: time only); right-click a node for Delete or Hold. Nodes set the value; '
                    'without nodes the header value plays.'), cursor
        if part == 'lane':
            index = self.gain_clip_at(x, row.index) if x >= GUTTER else None
            if index is None: return 'Gain lane: shows the audio clips\' gain (×0 to ×1 of the clip gain).', cursor
            clip = self.project.audio_clips[index]
            self.gain_strip.set_rect(self.gain_lane_rect(row.index))
            seconds = self.audio_seconds(max(0., self.beats_at(x))) - self.audio_seconds(clip.start)
            return (f'{clip.name}: gain ×{audio_edit.lane_value(clip.gain_points, seconds):.2f} at {seconds:.2f} s. Click to add a node; '
                    'drag a node (Shift: gain only, Alt: time only); right-click a node for Delete or Hold.'), cursor
        if x < GUTTER: return self.header_tooltip(row, x, y), cursor
        index, part = self.audio_hit(x, y)
        if index is None: return 'Audio track: drop audio files here, or double-click to add one.', cursor
        clip = self.project.audio_clips[index]
        if part is not None: cursor = Qt.CursorShape.SizeHorCursor
        if clip.missing:
            return (f'{clip.name} is missing: it plays silence until you locate it (right-click › Locate file…).'
                    f' Last location: {clip.path}'), cursor
        loud = ' Loud: gain × the file\'s peak passes full scale, so it clips.' if self.audio_loud(clip) else ''
        if clip.muted: loud += ' Muted.'
        elif self.audio_silenced(index): loud += ' Silent: its track is muted, or another clip or track is soloed.'
        elif index in self.audio_solos: loud += ' Soloed: only soloed audio clips and tracks play, and the scenes are silent.'
        return (f'{clip.name}: gain {clip.gain:.2f}, {ROUTING_LABELS[clip.routing]}; fades {clip.fade_in:.2f} s and {clip.fade_out:.2f} s.'
                f' Drag to move, the ends to trim, the top corner squares for fades; Alt/Option-drag copies; right-click for more.{loud}'), cursor

    def header_tooltip(self, row, x, y):
        if row.track is None: return 'Audio: drop audio files on this row to make the first audio track.'
        point, track = QPointF(x, y), row.track
        kind = self.header_target(row, point)
        if kind == 'mute': return f'Mute {track.name}: its clips are silent (saved with the project).'
        if kind == 'solo':
            return f'Solo {track.name}: only soloed tracks and clips play, and the scenes are silent. Solo is not saved.'
        if kind == 'fx':
            names = ', '.join(track_effects.EFFECTS[e.kind].label + (' (bypassed)' if e.bypass else '') for e in track.effects)
            return f'Effects: {names}{" — all bypassed" if track.effects_bypass else ""}. Click to open Track effects.'
        if kind == 'gain':
            return f'Track gain ×{track.gain:.2f} (0 to 2), on top of each clip\'s gain: drag up or down (Shift: finer); double-click to type it.'
        if kind == 'pan':
            lane = ' The pan lane is automating it (~).' if track.pan_points else ''
            return (f'Pan {pan_label(track.pan)}: the balance between X (left) and Y (right); the far side fades, '
                    f'nothing is boosted. Drag up or down (Shift: finer); double-click to type it.{lane}')
        if kind == 'phase':
            lane = ' The phase lane is automating it (~).' if track.phase_points else ''
            what = ('True phase: Y is rotated against X by this angle at every frequency (90° turns a line into a circle).'
                    if track.phase_mode == 'true' else 'Time delay: Y plays this many ms behind X (negative: X behind Y).')
            return f'{what} Drag up or down (Shift: finer); double-click to type it; right-click for True phase or Time delay.{lane}'
        if kind == 'lamp':
            level = self.track_meters[row.index] if row.index < len(self.track_meters) else 0.
            text = f'{20 * math.log10(level):.1f} dB' if level > 0 else 'silent'
            return f'{track.name} meter ({text}): the lamp turns red when this track passes full scale; click it to reset.'
        return (f'{track.name}: click to select (Shift: a range, Ctrl/Cmd: add or remove), drag up or down to reorder,'
                ' double-click to rename; right-click for more.')

    def select_audio(self, index):
        self.selected_audio, self.focus_kind = index, 'audio'
        self.track_picks = set()
        if index is None: self.gain_node = None

    def audio_area_press(self, event, x, y):
        """A press anywhere in the audio tracks: a header, a clip row, a gain lane or the Add row."""
        row, part = self.track_at(y)
        if part == 'lane':
            if x >= GUTTER: self.gain_lane_press(event, x, y, row.index)
            return
        if part == 'add':
            if x >= GUTTER and event.button() == Qt.MouseButton.LeftButton: self.add_track()
            elif x >= GUTTER and event.button() == Qt.MouseButton.RightButton:
                self.pending_audio_menu = ('header', None, None, event.globalPosition().toPoint())
            return
        if x < GUTTER: self.track_header_press(event, x, y, row); return
        self.audio_press(event, x, y, row)

    def audio_press(self, event, x, y, row=None):
        right = event.button() == Qt.MouseButton.RightButton
        if row is None: row = self.track_at(y)[0] or self.track_row(0)
        index, part = self.audio_hit(x, y)
        position = event.globalPosition().toPoint()
        if index is None:
            self.selected_audio = None; self.gain_node = None
            if right: self.pending_audio_menu = ('track', row.index, self.snapped(self.beats_at(x)), position)
            self.update(); return
        if index != self.selected_audio: self.gain_node = None
        self.select_audio(index)
        if right: self.pending_audio_menu = ('clip', index, None, position); self.update(); return
        if event.button() != Qt.MouseButton.LeftButton: return
        clip = self.project.audio_clips[index]
        kind = {'left': 'audio-trim-left', 'right': 'audio-trim-right', 'fade-in': 'audio-fade-in',
                'fade-out': 'audio-fade-out'}.get(part, 'audio-move')
        if kind == 'audio-move' and event.modifiers() & Qt.KeyboardModifier.AltModifier:
            if self.alt_wheel_filter is not None: self.alt_wheel_filter.mark()
            kind = 'audio-duplicate'
        self.drag = (kind, index, self.beats_at(x), self.project, clip, x, y)
        self.update()

    def audio_move(self, x, y=None):
        kind, index, origin, original, clip, press_x, press_y = self.drag
        track = clip.track
        if y is not None and kind in ('audio-move', 'audio-duplicate') and original.audio_tracks:
            row, part = self.track_at(y)
            if part == 'track': track = row.index
            elif y >= self.audio_end: track = len(original.audio_tracks) - 1
            elif y < self.audio_top: track = 0
        if abs(x - press_x) <= CLICK_SLOP and track == clip.track:  # Still a click: nothing moves.
            if self.project != original: self.selected_audio = index; self.emit(original)
            return
        delta = self.beats_at(x) - origin if abs(x - press_x) > CLICK_SLOP else 0.
        try:
            if kind == 'audio-move': project, selected = audio_edit.move(original, index, self.snapped(clip.start + delta) if delta else clip.start, track=track)
            elif kind == 'audio-duplicate':
                project, selected = audio_edit.duplicate(original, index, self.snapped(clip.start + delta) if delta else clip.start)
                if track != clip.track: project, selected = audio_edit.move(project, selected, project.audio_clips[selected].start, track=track)
            elif kind == 'audio-trim-left': project, selected = audio_edit.trim_start(original, index, self.snapped(clip.start + delta))
            elif kind == 'audio-trim-right':
                end = float(audio_clip_end_beats(original, clip))
                project, selected = audio_edit.trim_end(original, index, self.snapped(end + delta))
            else:
                s0, at = float(original.seconds(clip.start)), float(original.seconds(max(0., self.beats_at(x))))
                fades = dict(fade_in=at - s0) if kind == 'audio-fade-in' else dict(fade_out=s0 + clip.duration - at)
                project, selected = audio_edit.set_fades(original, index, **fades)
        except ValueError as error:
            self.notice.emit(str(error)); return
        self.selected_audio = selected
        if project != self.project: self.emit(project)

    def gain_clip_at(self, x, track=None):
        """The topmost clip under x (on `track` when given) whose gain lane a press edits."""
        beat = self.beats_at(x)
        return next((i for i in self.audio_order()
                     if (track is None or self.project.audio_clips[i].track == track)
                     and self.audio_span(self.project.audio_clips[i])[0] <= beat <= self.audio_span(self.project.audio_clips[i])[1]), None)

    def gain_seconds(self, clip, x):
        beat = self.snapped(self.beats_at(x)) if self.snap else max(0., self.beats_at(x))
        return min(max(0., self.audio_seconds(beat) - self.audio_seconds(clip.start)), clip.duration)

    def track_lane_seconds(self, x):
        beat = self.snapped(self.beats_at(x)) if self.snap else max(0., self.beats_at(x))
        return max(0., self.audio_seconds(beat))

    def set_track_lane(self, t, points):
        return self.apply_tracks(set_any_lane, t, self.lane_for(t), tuple(points))

    def track_lane_press(self, event, x, y, t):
        self.gain_strip.set_rect(self.gain_lane_rect(t))
        lane = self.lane_for(t)
        track = self.project.audio_tracks[t]; points = track_lane_points(track, lane)
        node = self.gain_strip.node_at(x, y, self.x_of, self.track_lane_items(t)) if points else None
        self.track_picks, self.selected_audio, self.gain_node = set(), None, None
        self.focus_kind = 'track-lane'  # Delete acts on the lane node, never on tracks selected earlier.
        if event.button() == Qt.MouseButton.RightButton:
            self.track_node = (t, lane, node) if node is not None else None
            self.pending_audio_menu = ('track-lane', t, node, event.globalPosition().toPoint()); self.update(); return
        if event.button() != Qt.MouseButton.LeftButton: return
        if node is None:
            points, node = add_track_node(points, self.track_lane_seconds(x), round(self.gain_strip.value_at(y), 3))
            if not self.set_track_lane(t, points): return
            self.track_picks, self.focus_kind = set(), 'track-lane'
        self.track_node = (t, lane, node)
        self.drag = ('track-lane', t, node, x, y, False, lane, self.project.audio_tracks[t])
        self.update()

    def track_lane_node(self):
        """(track, node) of the selected lane node while it still names a node of the shown lane."""
        if self.track_node is None or self.project is None: return None
        t, lane, node = self.track_node
        if t >= len(self.project.audio_tracks) or lane != self.lane_for(t): return None
        return (t, node) if node < len(track_lane_points(self.project.audio_tracks[t], lane)) else None

    def delete_track_node(self, t, node):
        points = track_lane_points(self.project.audio_tracks[t], self.lane_for(t))
        self.track_node = None
        return self.set_track_lane(t, points[:node] + points[node + 1:])  # One node left still sets the whole song.

    def track_lane_move(self, event, x, y):
        _, t, node, press_x, press_y, moved, lane, track = self.drag
        if not moved and abs(x - press_x) < CLICK_SLOP and abs(y - press_y) < CLICK_SLOP: return
        if t >= len(self.project.audio_tracks) or lane != self.lane_for(t) or self.project.audio_tracks[t].name != track.name:
            self.drag = None; return  # The lane, or the track under the drag, changed: stop.
        self.drag = ('track-lane', t, node, press_x, press_y, True, lane, track)
        points = track_lane_points(self.project.audio_tracks[t], lane)
        if node >= len(points): return
        seconds, value = points[node][:2]
        self.gain_strip.set_rect(self.gain_lane_rect(t))
        if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier: seconds = self.track_lane_seconds(x)
        if not event.modifiers() & Qt.KeyboardModifier.AltModifier: value = round(self.gain_strip.value_at(y), 3)
        moved_points = audio_edit.move_gain_node(points, node, seconds, value)
        if moved_points != points:
            try: project = set_any_lane(self.project, t, lane, moved_points)
            except ValueError as error: self.notice.emit(str(error)); return
            self.emit(project)

    def build_track_lane_menu(self, t, node):
        if t is None or t >= len(self.project.audio_tracks): return QMenu(self)
        lane = self.lane_for(t); track = self.project.audio_tracks[t]
        points = track_lane_points(track, lane)
        menu = QMenu(self)
        if node is not None and node < len(points):
            menu.addAction('Delete node').triggered.connect(lambda: self.delete_track_node(t, node))
            for text, shape in (('Linear to next', 'linear'), ('Hold to next', 'hold')):
                action = menu.addAction(text); action.setCheckable(True); action.setChecked(points[node][2] == shape)
                action.triggered.connect(lambda checked=False, shape=shape: self.set_track_lane(t, audio_edit.set_gain_shape(points, node, shape)))
            menu.addSeparator()
        name = track_lane_label(track, lane).lower()
        menu.remove = menu.addAction(f'Remove {name} automation'); menu.remove.setEnabled(bool(points))
        menu.remove.triggered.connect(lambda: self.set_track_lane(t, ()))
        self.add_lane_choices(menu, t)
        return menu

    def gain_lane_press(self, event, x, y, track=0):
        if track < len(self.project.audio_tracks) and self.lane_for(track) != 'gain':
            self.track_lane_press(event, x, y, track); return
        self.gain_strip.set_rect(self.gain_lane_rect(track))
        index = self.gain_clip_at(x, track)
        if index is None:
            if event.button() == Qt.MouseButton.RightButton:
                self.pending_audio_menu = ('lane-choices', None, None, event.globalPosition().toPoint())
            return
        if index != self.selected_audio: self.gain_node = None
        self.select_audio(index)
        clip = self.project.audio_clips[index]; points = clip.gain_points
        node = self.gain_strip.node_at(x, y, self.x_of, self.gain_items(index)) if points else None
        if event.button() == Qt.MouseButton.RightButton:
            self.gain_node = node
            self.pending_audio_menu = ('gain', index, node, event.globalPosition().toPoint()); self.update(); return
        if event.button() != Qt.MouseButton.LeftButton: return
        if node is None:
            points, node = audio_edit.add_gain_node(points, clip.duration, self.gain_seconds(clip, x), round(self.gain_strip.value_at(y), 3))
            project, index = audio_edit.set_gain_points(self.project, index, points)
            self.emit(project)
        self.gain_node = node
        self.drag = ('audio-gain', index, node, x, y, False)
        self.update()

    def gain_lane_move(self, event, x, y):
        _, index, node, press_x, press_y, moved = self.drag
        if not moved and abs(x - press_x) < CLICK_SLOP and abs(y - press_y) < CLICK_SLOP: return
        self.drag = ('audio-gain', index, node, press_x, press_y, True)
        clip = self.project.audio_clips[index]; points = clip.gain_points
        if node >= len(points): return
        seconds, gain = points[node][:2]
        self.gain_strip.set_rect(self.gain_lane_rect(clip.track))
        if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier: seconds = self.gain_seconds(clip, x)
        if not event.modifiers() & Qt.KeyboardModifier.AltModifier: gain = round(self.gain_strip.value_at(y), 3)
        moved_points = audio_edit.move_gain_node(points, node, seconds, gain)
        if moved_points != points: self.emit(audio_edit.set_gain_points(self.project, index, moved_points)[0])

    def apply_audio(self, operation, index, *args):
        """Run an audio_edit operation on the project and select the clip it returns."""
        try: project, selected = operation(self.project, index, *args)
        except ValueError as error:
            self.notice.emit(str(error)); return False
        self.selected_audio, self.focus_kind = selected, 'audio'
        if project != self.project: self.emit(project)
        else: self.update()
        return True

    def split_audio(self, index=None):
        index = self.selected_audio if index is None else index
        return index is not None and self.apply_audio(audio_edit.split, index, self.playhead)

    def duplicate_audio(self, index): return self.apply_audio(audio_edit.duplicate, index)

    def delete_audio(self, index):
        self.gain_node = None
        return self.apply_audio(audio_edit.delete, index)

    def set_audio_gain_points(self, index, points): return self.apply_audio(audio_edit.set_gain_points, index, tuple(points))

    def audio_gain_dialog(self, index):
        clip = self.project.audio_clips[index]
        gain, ok = QInputDialog.getDouble(self, 'Audio clip gain', 'Gain (0 to 2; 1 plays the file as it is):', clip.gain, 0., 2., 3)
        if ok: self.apply_audio(audio_edit.set_gain, index, gain)

    def build_audio_menu(self, index):
        """An audio clip's right-click menu (built separately so smokes can inspect it)."""
        clip = self.project.audio_clips[index]
        menu = QMenu(self)
        menu.locate = None
        if clip.missing:
            menu.locate = menu.addAction('Locate file…')
            menu.locate.setToolTip(f'Find {clip.name}; other missing files in the same folder are offered too.')
            menu.locate.triggered.connect(lambda: self.audio_locate_requested.emit(index))
            menu.addSeparator()
        split = menu.addAction('Split at playhead'); split.setShortcuts(key_sequences('split_clip'))
        a, b = self.audio_span(clip); split.setEnabled(a < self.playhead < b)
        split.triggered.connect(lambda: self.split_audio(index))
        menu.addAction('Duplicate').triggered.connect(lambda: self.duplicate_audio(index))
        menu.addAction('Gain…').triggered.connect(lambda: self.audio_gain_dialog(index))
        routing = QMenu('Routing', menu); menu.addMenu(routing); menu.routing = routing
        group = QActionGroup(routing); group.setExclusive(True)
        for name in ROUTINGS:
            action = routing.addAction(ROUTING_LABELS[name]); action.setCheckable(True); action.setChecked(clip.routing == name)
            group.addAction(action); action.setData(name)
            action.triggered.connect(lambda checked=False, name=name: self.apply_audio(audio_edit.set_routing, index, name))
        lane = self.add_lane_choices(menu)
        menu.addSeparator()
        menu.mute = menu.addAction('Mute clip'); menu.mute.setCheckable(True); menu.mute.setChecked(clip.muted)
        menu.mute.setToolTip('A muted audio clip is silent; it stays in the project.')
        menu.mute.triggered.connect(lambda checked=False: self.apply_audio(audio_edit.set_muted, index, not clip.muted))
        menu.solo = menu.addAction('Solo clip'); menu.solo.setCheckable(True); menu.solo.setChecked(index in self.audio_solos)
        menu.solo.setToolTip('Play only the soloed audio clips; the scenes are silent. Solo is not saved.')
        menu.solo.triggered.connect(lambda checked=False: self.audio_solo_requested.emit(index))
        menu.addSeparator()
        menu.delete = menu.addAction('Delete audio clip'); menu.delete.triggered.connect(lambda: self.delete_audio(index))
        menu.split = split
        return menu

    def build_audio_track_menu(self, beat, track=0):
        """Right-click on a track's empty space: add a clip there, the gain lane, Collapse/Expand."""
        menu = QMenu(self)
        menu.addAction('Add audio clip here…').triggered.connect(lambda: self.audio_add_requested.emit(beat, track))
        lane = self.add_lane_choices(menu)
        menu.collapse = None
        if track < len(self.project.audio_tracks):
            menu.addSeparator()
            collapsed = self.project.audio_tracks[track].collapsed
            menu.collapse = menu.addAction('Expand track' if collapsed else 'Collapse track')
            menu.collapse.setObjectName('expand_audio_track' if collapsed else 'collapse_audio_track')
            menu.collapse.triggered.connect(lambda checked=False: self.collapse_tracks(self.header_targets(track), not collapsed))
        return menu

    # Audio tracks (AW1): headers, selection, reorder and track commands ----------------
    def header_targets(self, t):
        """The tracks a header command acts on: the selected tracks when t is one of them, else t."""
        return sorted(self.track_picks) if t in self.track_picks else [t]

    def apply_tracks(self, operation, *args):
        """Run an audio_tracks operation (or clipboard.paste_tracks); tracks it returns become the selection."""
        try: result = operation(self.project, *args)
        except ValueError as error:
            self.notice.emit(str(error)); return False
        project, picks = result if isinstance(result, tuple) else (result, None)
        if isinstance(picks, int): picks = [picks]
        if picks is not None: self.track_picks = set(picks); self.track_anchor = min(picks) if picks else None
        self.focus_kind = 'tracks' if self.track_picks else self.focus_kind
        self.selected_audio, self.gain_node = None, None
        reordered = operation is audio_tracks.reorder and any(a is not b for a, b in zip(project.audio_tracks, self.project.audio_tracks))
        if reordered or project != self.project: self.emit(project)
        else: self.update()
        return True

    def add_track(self, at=None):
        return self.apply_tracks(audio_tracks.add_track, at)

    def delete_tracks(self, tracks):
        self.track_picks, self.track_anchor = set(), None
        return self.apply_tracks(audio_tracks.remove_tracks, sorted(tracks))

    def duplicate_tracks(self, tracks): return self.apply_tracks(audio_tracks.duplicate_tracks, sorted(tracks))

    def collapse_tracks(self, tracks, collapsed=True):
        return self.apply_tracks(audio_tracks.set_collapsed, list(tracks), bool(collapsed))

    def mute_tracks(self, tracks, muted):
        return self.apply_tracks(audio_tracks.set_muted, list(tracks), bool(muted))

    def set_track_gain(self, tracks, gain):
        return self.apply_tracks(audio_tracks.set_gain, list(tracks), float(gain))

    def move_tracks(self, tracks, step):
        """Move the tracks one place up (step -1) or down (+1) as a block."""
        tracks = sorted(tracks); count = len(self.project.audio_tracks)
        first = sum(1 for t in range(tracks[0]) if t not in tracks)
        order, picks = audio_tracks.moved_order(count, tracks, first + step)
        if list(order) == list(range(count)): return False
        self.track_picks, self.track_anchor = set(picks), picks[0]
        return self.apply_tracks(audio_tracks.reorder, order)

    def rename_track_dialog(self, t):
        if t >= len(self.project.audio_tracks): return
        name, ok = self.ask_text('Rename audio track', 'Name:', self.project.audio_tracks[t].name)
        if ok: self.apply_tracks(audio_tracks.rename_track, t, name)

    def track_gain_dialog(self, t):
        if t >= len(self.project.audio_tracks): return
        tracks = self.header_targets(t)
        gain, ok = QInputDialog.getDouble(self, 'Audio track gain', 'Track gain (0 to 2; on top of each clip\'s gain):',
                                          self.project.audio_tracks[t].gain, 0., 2., 3)
        if ok: self.set_track_gain(tracks, gain)

    def track_pan_dialog(self, t):
        if t >= len(self.project.audio_tracks): return
        pan, ok = QInputDialog.getDouble(self, 'Audio track pan', 'Pan (−1 left/X only … 0 centre … 1 right/Y only):',
                                         self.project.audio_tracks[t].pan, -1., 1., 3)
        if ok: self.apply_tracks(audio_tracks.set_pan, self.header_targets(t), float(pan))

    def track_phase_dialog(self, t):
        if t >= len(self.project.audio_tracks): return
        track = self.project.audio_tracks[t]
        if track.phase_mode == 'true':
            value, ok = QInputDialog.getDouble(self, 'Audio track phase', 'True phase of Y against X (degrees, −180 to 180):',
                                               track.phase, -180., 180., 1)
            if ok: self.apply_tracks(audio_tracks.set_phase, self.header_targets(t), float(value))
        else:
            value, ok = QInputDialog.getDouble(self, 'Audio track delay',
                                               f'Time delay of Y against X (ms, −{MAX_DELAY_MS:g} to {MAX_DELAY_MS:g}; negative delays X):',
                                               track.delay_ms, -MAX_DELAY_MS, MAX_DELAY_MS, 2)
            if ok: self.apply_tracks(audio_tracks.set_delay, self.header_targets(t), float(value))

    def build_phase_mode_menu(self, t, menu=None):
        """True phase or Time delay for the header's tracks (the phase field's right-click, and a
        submenu of the track menu)."""
        menu = menu or QMenu(self); track = self.project.audio_tracks[t]
        group = QActionGroup(menu); group.setExclusive(True); menu.modes = {}
        for mode, text, tip in (('true', 'True phase', 'Rotate the phase of Y by a fixed angle at every frequency (90° turns a line into a circle).'),
                                ('delay', 'Time delay', 'Delay Y behind X by a time (milliseconds); negative delays X.')):
            action = menu.addAction(text); action.setCheckable(True); action.setChecked(track.phase_mode == mode)
            action.setToolTip(tip); group.addAction(action); menu.modes[mode] = action
            action.triggered.connect(lambda checked=False, mode=mode: self.apply_tracks(audio_tracks.set_phase_mode, self.header_targets(t), mode))
        menu.addSeparator()
        menu.edit = menu.addAction('Phase…' if track.phase_mode == 'true' else 'Delay…')
        menu.edit.triggered.connect(lambda: self.track_phase_dialog(t))
        return menu

    def set_audio_lane(self, kind):
        """Show the lane under every expanded track, set to clip gain, track pan or track phase."""
        if kind not in AUDIO_LANES: return
        self.track_node = None; self.effect_lanes = {}
        if self.drag and self.drag[0] == 'track-lane': self.drag = None
        if kind != self.roll_audio_lane: self.set_roll_option('roll_audio_lane', kind)
        if not self.roll_audio_gain_lane: self.set_audio_gain_lane(True)
        self.shown_lane_changed.emit()
        self.update()

    def lane_for(self, t):
        """What track t's lane shows: its effect parameter ('fx', e, name) when one was chosen and
        still exists, else the roll preference (gain, pan or phase)."""
        chosen = self.effect_lanes.get(t)
        if chosen is not None and self.project is not None and t < len(self.project.audio_tracks):
            effects = self.project.audio_tracks[t].effects; e, name = chosen
            if e < len(effects) and any(f.name == name and f.choices is None for f in track_effects.EFFECTS[effects[e].kind].fields):
                return ('fx', e, name)
        return self.roll_audio_lane

    def remap_effect_lane(self, t, order):
        """After a chain edit on track t: order[new index] = old index (None for an added effect).
        The track's effect lane follows its effect, or ends when the effect was removed."""
        chosen = self.effect_lanes.get(t)
        if chosen is None: return
        if chosen[0] in order: self.effect_lanes[t] = (order.index(chosen[0]), chosen[1])
        else: self.effect_lanes.pop(t, None)
        self.shown_lane_changed.emit()
        self.track_node = None
        if self.drag and self.drag[0] == 'track-lane': self.drag = None

    def show_effect_lane(self, t, e, name):
        """Show effect e's parameter `name` in track t's lane (the other tracks keep theirs)."""
        if self.project is None or t >= len(self.project.audio_tracks) or e >= len(self.project.audio_tracks[t].effects): return
        self.track_node = None
        if self.drag and self.drag[0] == 'track-lane': self.drag = None
        self.effect_lanes[t] = (e, name)
        if self.project.audio_tracks[t].collapsed: self.collapse_tracks([t], False)
        if not self.roll_audio_gain_lane: self.set_audio_gain_lane(True)
        self.reveal_track_lane(t)
        self.shown_lane_changed.emit()
        self.update()

    def reveal_track_lane(self, t):
        """Scroll the track rows so track t's lane is in view (with its header when both fit): with the
        Library open the timeline is short, and a lane shown from the Effects page could be scrolled away."""
        rows = self.track_rows()
        if not 0 <= t < len(rows) or rows[t].lane is None: return
        row, (total, shown) = rows[t], self.track_extent
        top = row.top - self.audio_top + self.track_scroll  # In track-content pixels.
        bottom = row.lane - self.audio_top + self.track_scroll + GAIN_LANE
        top = max(top, bottom - shown)
        scroll = min(max(self.track_scroll, bottom - shown), top)
        self.scroll_tracks(scroll - self.track_scroll)

    def add_lane_choices(self, menu, t=None):
        sub = QMenu('Lane', menu); menu.addMenu(sub); menu.lane_menu = sub
        group = QActionGroup(sub); group.setExclusive(True)
        show = sub.addAction('Show lane'); show.setCheckable(True); show.setChecked(self.roll_audio_gain_lane)
        show.triggered.connect(lambda checked=False: self.set_audio_gain_lane(not self.roll_audio_gain_lane))
        sub.addSeparator()
        for kind in AUDIO_LANES:
            action = sub.addAction(AUDIO_LANE_LABELS[kind] + (' (clips)' if kind == 'gain' else ' (track)'))
            action.setCheckable(True); action.setChecked(self.roll_audio_gain_lane and self.roll_audio_lane == kind and t not in self.effect_lanes)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, kind=kind: self.set_audio_lane(kind))
        sub.effect_actions = {}
        if t is not None and self.project is not None and t < len(self.project.audio_tracks):
            track = self.project.audio_tracks[t]; shown = self.lane_for(t)
            if track.effects: sub.addSeparator()
            for e, effect in enumerate(track.effects):
                spec = track_effects.EFFECTS[effect.kind]
                for field in spec.fields:
                    if field.choices is not None: continue
                    action = sub.addAction(f'{spec.label} {e + 1}: {field.label}' + (' ~' if track_effects.effect_lane(effect, field.name) else ''))
                    action.setCheckable(True); action.setChecked(self.roll_audio_gain_lane and shown == ('fx', e, field.name))
                    group.addAction(action); sub.effect_actions[(e, field.name)] = action
                    action.triggered.connect(lambda checked=False, e=e, name=field.name: self.show_effect_lane(t, e, name))
        return sub

    def copy_tracks(self, tracks):
        self.track_picks = set(tracks); self.focus_kind = 'tracks'; return self.copy_selection()

    def paste_tracks(self, at=None):
        """Paste tracks from the clipboard after the selected tracks (or at `at`, or at the end)."""
        payload = self.clipboard_payload()
        if payload is None or payload.get('kind') != 'tracks':
            self.notice.emit('The clipboard has no audio tracks.'); return False
        if at is None: at = max(self.track_picks) + 1 if self.track_picks else len(self.project.audio_tracks)
        if not self.apply_tracks(clipboard.paste_tracks, payload, at): return False
        self.notice.emit(f'Pasted {clipboard.describe(payload)}.')
        return True

    def select_track(self, t, modifiers=Qt.KeyboardModifier.NoModifier):
        """Click (one), Shift+click (a range from the last click) or Ctrl/Cmd+click (toggle) a header."""
        toggle = modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        if modifiers & Qt.KeyboardModifier.ShiftModifier and self.track_anchor is not None:
            low, high = sorted((self.track_anchor, t)); self.track_picks = set(range(low, high + 1))
        elif toggle:
            self.track_picks ^= {t}; self.track_anchor = t
        else:
            self.track_picks, self.track_anchor = {t}, t
        self.focus_kind = 'tracks' if self.track_picks else 'audio'
        self.selected_audio, self.gain_node = None, None
        self.update()
        if t in self.track_picks: self.track_selected.emit(t)

    def track_header_press(self, event, x, y, row):
        point, position = QPointF(x, y), event.globalPosition().toPoint()
        if row.track is None:
            if event.button() == Qt.MouseButton.RightButton: self.pending_audio_menu = ('header', None, None, position)
            return
        t = row.index
        if event.button() == Qt.MouseButton.RightButton:
            if t not in self.track_picks: self.select_track(t)
            if self.header_target(row, point) == 'phase':
                self.pending_audio_menu = ('phase-mode', t, None, position); return
            self.pending_audio_menu = ('header', t, None, position); return
        if event.button() != Qt.MouseButton.LeftButton: return
        kind = self.header_target(row, point)
        if kind == 'lamp': self.reset_track_meter(t); return
        if kind == 'mute': self.mute_tracks([t], not row.track.muted); return
        if kind == 'solo': self.audio_track_solo_requested.emit(t); return
        if kind == 'fx': self.track_effects_requested.emit(t); return
        if kind == 'gain':
            self.drag = ('track-gain', t, y, self.project, row.track.gain); return
        if kind == 'pan':
            self.drag = ('track-pan', t, y, self.project, row.track.pan); return
        if kind == 'phase':
            value = row.track.phase if row.track.phase_mode == 'true' else row.track.delay_ms
            self.drag = ('track-phase', t, y, self.project, value); return
        modifiers = event.modifiers()
        plain = not modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        if not (plain and t in self.track_picks): self.select_track(t, modifiers)
        if t in self.track_picks: self.drag = ('track-move', sorted(self.track_picks), y, self.project, t if plain else None)

    def track_drag_move(self, event, y):
        kind = self.drag[0]
        if kind == 'track-gain':
            _, t, press_y, original, gain = self.drag
            step = TRACK_GAIN_PIXELS / (5 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
            value = min(2., max(0., round(gain + (press_y - y) * step, 3)))
            try: project = audio_tracks.set_gain(original, self.header_targets(t), value)
            except ValueError as error: self.notice.emit(str(error)); return
            if project != self.project: self.emit(project)
            return
        if kind in ('track-pan', 'track-phase'):
            _, t, press_y, original, start = self.drag
            fine = 5 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            track = original.audio_tracks[t]
            try:
                if kind == 'track-pan':
                    value = min(1., max(-1., round(start + (press_y - y) * TRACK_PAN_PIXELS / fine, 3)))
                    project = audio_tracks.set_pan(original, self.header_targets(t), value)
                elif track.phase_mode == 'true':
                    value = min(180., max(-180., round(start + (press_y - y) * TRACK_PHASE_PIXELS / fine, 1)))
                    project = audio_tracks.set_phase(original, self.header_targets(t), value)
                else:
                    value = min(MAX_DELAY_MS, max(-MAX_DELAY_MS, round(start + (press_y - y) * TRACK_DELAY_PIXELS / fine, 2)))
                    project = audio_tracks.set_delay(original, self.header_targets(t), value)
            except ValueError as error: self.notice.emit(str(error)); return
            if project != self.project: self.emit(project)
            return
        _, picks, press_y, original, pressed = self.drag
        if abs(y - press_y) <= CLICK_SLOP and self.project is original: return
        # Target: how many of the other tracks lie above the pointer (rows as they were at the press).
        others = [t for t in range(len(original.audio_tracks)) if t not in picks]
        tops = {}; top = self.audio_top - self.track_scroll
        for t, track in enumerate(original.audio_tracks):
            height = COLLAPSED_TRACK if track.collapsed else AUDIO_TRACK
            lane = GAIN_LANE if self.roll_audio_gain_lane and height == AUDIO_TRACK else 0
            tops[t] = (top, height + lane); top += height + lane
        target = sum(1 for t in others if tops[t][0] + tops[t][1] / 2 < y)
        order, new = audio_tracks.moved_order(len(original.audio_tracks), picks, target)
        if getattr(self, '_drag_order', None) == (original, order): return
        self._drag_order = (original, order)
        try: project = audio_tracks.reorder(original, order)
        except ValueError as error: self.notice.emit(str(error)); return
        self.track_picks, self.track_anchor = set(new), new[0] if new else None
        if project != self.project or any(a is not b for a, b in zip(project.audio_tracks, self.project.audio_tracks)):
            self.emit(project)
        else: self.update()

    def build_track_menu(self, t):
        """A track header's right-click menu (built separately so smokes can inspect it). It acts
        on the selected tracks when the header is one of them."""
        menu = QMenu(self); count = len(self.project.audio_tracks)
        menu.add = menu.addAction('Add audio track'); menu.add.triggered.connect(lambda: self.add_track(None if t is None else t + 1))
        payload = self.clipboard_payload()
        menu.paste = menu.addAction('Paste ' + (clipboard.describe(payload) if payload and payload.get('kind') == 'tracks' else 'audio tracks'))
        menu.paste.setEnabled(bool(payload) and payload.get('kind') == 'tracks')
        menu.paste.triggered.connect(lambda: self.paste_tracks(None if t is None else max(self.header_targets(t)) + 1))
        if t is None or t >= count: return menu
        tracks, track = self.header_targets(t), self.project.audio_tracks[t]
        several = len(tracks) > 1; noun = f'{len(tracks)} tracks' if several else 'track'
        menu.addSeparator()
        menu.rename = menu.addAction('Rename…'); menu.rename.setEnabled(not several)
        menu.rename.triggered.connect(lambda: self.rename_track_dialog(t))
        menu.gain = menu.addAction('Gain…'); menu.gain.triggered.connect(lambda: self.track_gain_dialog(t))
        menu.pan = menu.addAction('Pan…'); menu.pan.triggered.connect(lambda: self.track_pan_dialog(t))
        phase = QMenu('Phase', menu); menu.addMenu(phase); menu.phase = self.build_phase_mode_menu(t, phase)
        menu.effects = menu.addAction(f'Effects… ({len(track.effects)})' if track.effects else 'Effects…')
        menu.effects.setToolTip('Open the track\'s effect chain: filter, saturate, bitcrush, delay, compressor.')
        menu.effects.triggered.connect(lambda: self.track_effects_requested.emit(t))
        self.add_lane_choices(menu, t)
        menu.mute = menu.addAction('Mute ' + noun); menu.mute.setCheckable(True); menu.mute.setChecked(track.muted)
        menu.mute.triggered.connect(lambda checked=False: self.mute_tracks(tracks, not track.muted))
        menu.solo = menu.addAction('Solo ' + noun); menu.solo.setCheckable(True); menu.solo.setChecked(t in self.audio_track_solos)
        menu.solo.setToolTip('Play only the soloed tracks and clips; the scenes are silent. Solo is not saved.')
        menu.solo.triggered.connect(lambda checked=False: self.track_solos_requested.emit(tracks, t not in self.audio_track_solos))
        menu.collapse = menu.addAction(('Expand ' if track.collapsed else 'Collapse ') + noun)
        menu.collapse.setObjectName('expand_audio_track' if track.collapsed else 'collapse_audio_track')
        menu.collapse.triggered.connect(lambda checked=False: self.collapse_tracks(tracks, not track.collapsed))
        menu.addSeparator()
        menu.copy = menu.addAction('Copy ' + noun); menu.copy.setShortcuts(key_sequences('copy'))
        menu.copy.triggered.connect(lambda: self.copy_tracks(tracks))
        menu.duplicate = menu.addAction('Duplicate ' + noun); menu.duplicate.triggered.connect(lambda: self.duplicate_tracks(tracks))
        menu.up = menu.addAction('Move up'); menu.up.setEnabled(tracks != list(range(len(tracks))))
        menu.up.triggered.connect(lambda: self.move_tracks(tracks, -1))
        menu.down = menu.addAction('Move down'); menu.down.setEnabled(tracks != list(range(count - len(tracks), count)))
        menu.down.triggered.connect(lambda: self.move_tracks(tracks, 1))
        menu.addSeparator()
        menu.delete = menu.addAction('Delete ' + noun); menu.delete.triggered.connect(lambda: self.delete_tracks(tracks))
        return menu

    def build_gain_menu(self, index, node):
        points = self.project.audio_clips[index].gain_points
        menu = QMenu(self)
        if node is not None:
            menu.addAction('Delete node').triggered.connect(lambda: self.set_audio_gain_points(index, audio_edit.delete_gain_node(points, node)))
            for text, shape in (('Linear to next', 'linear'), ('Hold to next', 'hold')):
                action = menu.addAction(text); action.setCheckable(True); action.setChecked(points[node][2] == shape)
                action.triggered.connect(lambda checked=False, shape=shape: self.set_audio_gain_points(index, audio_edit.set_gain_shape(points, node, shape)))
            menu.addSeparator()
        remove = menu.addAction('Remove gain automation'); remove.setEnabled(bool(points))
        remove.triggered.connect(lambda: self.set_audio_gain_points(index, ()))
        self.add_lane_choices(menu)
        return menu

    def open_audio_menu(self, kind, index, extra, position):
        if self.project is None: return
        if kind == 'track': menu = self.build_audio_track_menu(extra, index)
        elif kind == 'header': menu = self.build_track_menu(index)
        elif kind == 'phase-mode':
            if index is None or index >= len(self.project.audio_tracks): return
            menu = self.build_phase_mode_menu(index)
        elif kind == 'track-lane': menu = self.build_track_lane_menu(index, extra)
        elif kind == 'lane-choices': menu = QMenu(self); self.add_lane_choices(menu)
        elif index is None or index >= len(self.project.audio_clips): return
        else: menu = self.build_audio_menu(index) if kind == 'clip' else self.build_gain_menu(index, extra)
        menu.exec(position); menu.deleteLater()

    # Drag and drop of audio files onto the Audio track --------------------------------
    @staticmethod
    def audio_paths(mime):
        if mime is None or not mime.hasUrls(): return []
        return [url.toLocalFile() for url in mime.urls()
                if url.isLocalFile() and url.toLocalFile().lower().endswith(AUDIO_FILE_TYPES)]

    def drop_target(self, position):
        """The track a file drop at position lands on (-1: the Add row, a new track), or None."""
        if position.x() < GUTTER: return None
        row, part = self.track_at(position.y())
        return row.index if part == 'track' else -1 if part == 'add' else None

    def drop_allowed(self, position): return self.drop_target(position) is not None

    def dragEnterEvent(self, event):
        if self.project is not None and self.audio_paths(event.mimeData()):
            self.dragging_audio = True; event.acceptProposedAction(); self.update()
        else: event.ignore()

    def dragMoveEvent(self, event):
        if self.audio_paths(event.mimeData()) and self.drop_allowed(event.position()):
            self.drop_beat = self.snapped(self.beats_at(event.position().x())); event.acceptProposedAction()
            self.drop_track = self.drop_target(event.position())
        else:
            self.drop_beat = self.drop_track = None; event.ignore()
        self.update()

    def dragLeaveEvent(self, event):
        self.drop_beat, self.drop_track, self.dragging_audio = None, None, False; self.update()

    def dropEvent(self, event):
        paths = self.audio_paths(event.mimeData())
        target = self.drop_target(event.position()) if paths else None
        self.drop_beat, self.drop_track, self.dragging_audio = None, None, False; self.update()
        if target is None: event.ignore(); return
        event.acceptProposedAction()
        self.audio_files_dropped.emit(paths, self.snapped(self.beats_at(event.position().x())), target)

    # Hit tests -----------------------------------------------------------------
    def clip_hit(self, x):
        beats = self.beats_at(x)
        for index, clip in enumerate(self.project.clips):
            if clip.start <= beats < clip.end:
                # Right end wins on very short clips; the left end resizes from the start.
                if self.x_of(clip.end) - x <= EDGE: return index, 'right'
                if x - self.x_of(clip.start) <= EDGE and clip.length * self.scale > 3 * EDGE: return index, 'left'
                return index, None
        return None, False

    def note_hit(self, x, y):
        if y >= self.note_bottom() or y < self.roll_top or x < GUTTER: return None, False
        beats, pitch = self.beats_at(x), self.pitch_at(y)
        # The last matching note in score order wins (drawn on top), as before the index.
        for index in reversed(self.note_index().overlapping(beats, beats)):
            note = self.project.notes[index]
            if note.pitch == pitch and note.start <= beats < note.end: return index, self.x_of(note.end) - x <= EDGE
        return None, False

    # Mouse ---------------------------------------------------------------------
    def velocity_bar(self, x, y):
        self.velocity_strip.set_rect(self.velocity_lane_rect())
        items = self.velocity_items()
        hit = self.velocity_strip.bar_at(x, y, self.x_of, [n for _, n in items])
        return items[hit][0] if hit is not None else None

    def velocity_press(self, event, x, y):
        self.focus_kind = 'notes'
        if self.velocity_strip.collapsed:
            if event.button() == Qt.MouseButton.RightButton:
                position = event.globalPosition().toPoint()
                QTimer.singleShot(0, lambda: self.velocity_menu(None, position))
            return
        self.selected_note = None; self.note_click = None
        index = self.velocity_bar(x, y)
        if event.button() == Qt.MouseButton.RightButton:
            if index is not None:
                if index not in self.note_selection: self.note_selection = {index}
                self.pending_note_menu = (index, event.globalPosition().toPoint(), self.beats_at(x), True)
            else:
                position = event.globalPosition().toPoint()
                QTimer.singleShot(0, lambda: self.velocity_menu(None, position))
            self.update(); return
        if event.button() != Qt.MouseButton.LeftButton: return
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.drag = ('velocity-line', self.project.notes, QPointF(x, y), QPointF(x, y),
                         tuple(self.note_selection or range(len(self.project.notes))))
        elif index is not None:
            if index not in self.note_selection: self.note_selection = {index}
            # Relative to the press, so a click (or a double-click's first press) changes nothing.
            self.velocity_strip.set_rect(self.velocity_lane_rect())
            self.drag = ('velocity-bar', self.project.notes, index, tuple(self.note_selection), self.velocity_strip.value_at(y))
        else:
            self.drag = ('velocity-paint', x, tuple(self.note_selection or range(len(self.project.notes))))
        self.velocity_move(x, y)

    def velocity_move(self, x, y):
        self.velocity_strip.set_rect(self.velocity_lane_rect())
        value = round(self.velocity_strip.value_at(y) * 127) / 127
        kind = self.drag[0]
        if kind == 'velocity-line':
            self.setToolTip(f'Velocity {round(value * 127)}')
            self.drag = (*self.drag[:3], QPointF(x, y), self.drag[4]); self.update(); return
        if kind == 'velocity-bar':
            _, original, index, picked, pressed = self.drag
            # Whole MIDI steps from the press; the dragged bar lands on a step.
            target = min(1., max(0., round((original[index].velocity + self.velocity_strip.value_at(y) - pressed) * 127) / 127))
            self.setToolTip(f'Velocity {round(target * 127)}')
            notes, _ = note_tools.shift_velocity(original, picked, target - original[index].velocity)
        else:
            _, previous, picked = self.drag
            items = self.velocity_items()
            crossed = self.velocity_strip.crossed(previous, x, self.x_of, [n for _, n in items])
            notes, _ = note_tools.set_velocity(self.project.notes, [items[i][0] for i in crossed if items[i][0] in picked], value)
            self.drag = (kind, x, picked)
        if notes != self.project.notes: self.emit(replace(self.project, notes=notes))

    def mousePressEvent(self, event):
        if self.project is None: return
        x, y = event.position().x(), event.position().y()
        right = event.button() == Qt.MouseButton.RightButton
        if y < self.metrics.ruler_height and x < GUTTER:  # The output meter.
            if self.clip_lamp_rect().adjusted(-4, -4, 4, 4).contains(QPointF(x, y)): self.reset_meter()
            return
        if y < self.metrics.ruler_height:
            flag = self.flag_hit(x, y)
            index = self.marker_hit(x, y)
            if right:
                if index is not None:
                    self.pending_marker_menu = (index, min(MAX_BEATS, self.snapped(self.beats_at(x))), event.globalPosition().toPoint())
                else: self.pending_ruler_menu = (max(0., self.beats_at(x)), flag, event.globalPosition().toPoint())
                return
            if flag is not None:
                self.drag = ('flag', *flag, self.beats_at(x), self.project)
                return
            if index is not None and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.seek.emit(self.project.markers[index][0])
                self.drag = ('marker', index, self.beats_at(x), self.project, QPointF(x, y))
                return
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                start = self.snapped(self.beats_at(x)); self.drag = ('loop', start)
                self.emit(replace(self.project, loop=True, loop_start=start, loop_end=start + max(self.snap_step(start), .25)))
            else:
                self.drag = ('seek',); self.seek.emit(max(0., self.beats_at(x)))
            return
        if self.audio_top <= y < self.roll_top: self.audio_area_press(event, x, y); return
        if y < self.roll_top:
            index, edge = self.clip_hit(x)
            if index is not None: self.focus_kind = 'clips'
            if right:
                if index is not None:
                    if index not in self.picked_clips(): self.clip_picks = set()
                    if index != self.selected_clip: self.selected_clip = index; self.clip_selected.emit(index)
                    position = event.globalPosition().toPoint()
                    QTimer.singleShot(0, lambda: self.clip_menu(index, position))
                else: self.paste_menu(event.globalPosition().toPoint())
                return
            if index is None: return
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and not edge:
                # Shift+click: add the clip to the block a drag moves, or take it out (UI1).
                picks = set(self.picked_clips())
                if index in picks and len(picks) > 1:
                    picks.discard(index)
                    if index == self.selected_clip: self.selected_clip = min(picks); self.clip_selected.emit(self.selected_clip)
                else:
                    picks.add(index)
                self.clip_picks = picks; self.update(); return
            if index not in self.picked_clips(): self.clip_picks = set()
            if index != self.selected_clip: self.selected_clip = index; self.clip_selected.emit(index)
            clip = self.project.clips[index]
            kind = {'right': 'clip-resize', 'left': 'clip-resize-left'}.get(edge, 'clip-move')
            if kind != 'clip-move': self.clip_picks = set()  # Resizing acts on one clip.
            if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                if self.alt_wheel_filter is not None: self.alt_wheel_filter.mark()
                self.drag = ('clip-duplicate', index, self.beats_at(x), clip.start, self.project)
                return
            self.drag = (kind, index, self.beats_at(x), clip.start, clip.length, clip, self.project, tuple(self.picked_clips()),
                         QPointF(x, y), [self.project])  # Press point; the project this drag last showed.
            self.update(); return
        if self.roll == TRANSFORM_ROLL: self.lane_press(event, x, y); return
        if x < GUTTER: return
        rect = self.velocity_lane_rect()
        if rect is not None and rect.contains(QPointF(x, y)):
            self.velocity_press(event, x, y); return
        if y >= self.note_bottom(): return
        index, edge = self.note_hit(x, y)
        self.focus_kind = 'notes'
        if right:
            if index is not None:
                if index not in self.note_selection: self.note_selection = {index}
                self.pending_note_menu = (index, event.globalPosition().toPoint(), self.snapped(self.beats_at(x), round))
                self.update()
            else: self.paste_menu(event.globalPosition().toPoint())
            return
        if index is not None and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.note_selection ^= {index}; self.selected_note = None; self.update(); return
        if index is None and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Shift-drag on empty space draws a selection box (adding to the selection).
            self.selected_note = None
            self.drag = ('note-box', QPointF(x, y), set(self.note_selection))
            self.selection_rect = QRectF(QPointF(x, y), QPointF(x, y))
            self.update(); return
        if index is None:
            # Draw a note: one snap step long (a quarter beat with Snap off); dragging sets its length.
            start = min(MAX_BEATS, self.snapped(self.beats_at(x), math.floor)); pitch = self.scale_pitch(min(max(self.pitch_at(y), PITCH_RANGE[0]), PITCH_RANGE[1]))
            note = Note(start, self.snap_step(start) or .25, pitch)
            try:
                if self.draw_mode == 'Chord':
                    notes, indices = note_tools.stamp_chord(self.project.notes, start, note.length, pitch, self.chord_type, self.chord_inversion)
                else: notes, indices = (*self.project.notes, note), (len(self.project.notes),)
                project = validate_project(replace(self.project, notes=notes))
            except ValueError as error: self.notice.emit(str(error)); return
            if not indices: return
            self.selected_note = indices[0]; self.note_selection = set(indices)
            self.drag = ('note-resize', indices, self.beats_at(x), note)
            self.emit(project)
            if self.roll_hear_notes:
                if self.draw_mode == 'Chord': self.audition_chord.emit(tuple(notes[i].pitch for i in indices), start)
                else: self.audition.emit(pitch, start)
            return
        self.selected_note = index; note = self.project.notes[index]
        self.note_click = (index, QPointF(x, y))
        if index not in self.note_selection: self.note_selection = {index}
        copying = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if copying and self.alt_wheel_filter is not None: self.alt_wheel_filter.mark()
        self.drag = (('note-resize', (index,), self.beats_at(x), note) if edge and not copying else
                     ('note-copy' if copying else 'note-move', index, self.beats_at(x), note, self.pitch_at(y),
                      self.project.notes, set(self.note_selection), QPointF(x, y), [False]))
        self.update()

    def mouseMoveEvent(self, event):
        x, y = event.position().x(), event.position().y()
        if self.note_click is not None and (QPointF(x, y) - self.note_click[1]).manhattanLength() > CLICK_SLOP:
            self.note_click = None
        if not self.drag:
            rect = self.velocity_lane_rect()
            if rect is not None and rect.contains(QPointF(x, y)) and self.velocity_strip.collapsed:
                self.setToolTip('Velocity lane, collapsed: right-click › Expand velocity lane.')
                self.setCursor(Qt.CursorShape.ArrowCursor); return
            if rect is not None and rect.contains(QPointF(x, y)):
                self.velocity_strip.set_rect(rect)
                self.setToolTip('Velocity is off for this project (Transport › Velocity): it is kept and exported to MIDI.' if not self.project.velocity else f'Velocity {round(self.velocity_strip.value_at(y) * 127)}')
                self.setCursor(Qt.CursorShape.ArrowCursor); return
            if self.audio_top <= y < self.roll_top or (y < self.metrics.ruler_height and x < GUTTER):
                tip, cursor = self.audio_tooltip(x, y)
                self.setToolTip(tip); self.setCursor(cursor); return
            if self.roll == TRANSFORM_ROLL and y >= self.roll_top:
                self.setToolTip(self.lane_tooltip(x, y) or self.hint_text); self.setCursor(Qt.CursorShape.ArrowCursor); return
            self.setToolTip(self.dropped_voice_tooltip(x, y) or self.hint_text)
            over = (self.clip_hit(x)[1] if self.metrics.ruler_height <= y < self.audio_top else self.note_hit(x, y)[1] if y >= self.roll_top else False)
            self.setCursor(Qt.CursorShape.SizeHorCursor if over else Qt.CursorShape.ArrowCursor); return
        kind = self.drag[0]; beats = self.beats_at(x)
        if kind.startswith('velocity-'): self.velocity_move(x, y); return
        if kind == 'audio-gain': self.gain_lane_move(event, x, y); return
        if kind == 'track-lane': self.track_lane_move(event, x, y); return
        if kind.startswith('audio-'): self.audio_move(x, y); return
        if kind.startswith('track-'): self.track_drag_move(event, y); return
        if kind in ('node', 'segment', 'bend'): self.lane_move(event, x, y); return
        if kind == 'switch': self.switch_move(x); return
        if kind == 'flag':
            _, flag_kind, index, origin, original = self.drag
            if abs(beats - origin) * self.scale <= CLICK_SLOP:
                if self.project != original: self.emit(original)
                return
            entry = getattr(original, flag_kind + '_changes')[index]
            meter = Meter(original.meter_changes)
            old = entry[0] if flag_kind == 'tempo' else float(meter.bar_start(entry[0]))
            target = min(MAX_BEATS, max(0., old + beats - origin))
            if flag_kind == 'tempo': target = self.snapped(target)
            else:
                bar, offset = meter.bar_of(target)
                target = int(bar) + round(float(offset) / float(meter.beats_per_bar_at(target)))
            self.change_map(flag_kind, target, entry[1:], original, index)
        elif kind == 'marker':
            _, index, origin, original, press = self.drag
            if (QPointF(x, y) - press).manhattanLength() <= CLICK_SLOP:
                if self.project != original: self.emit(original)
                return
            beat = min(MAX_BEATS, self.snapped(original.markers[index][0] + beats - origin))
            self.emit(move_marker(original, index, beat))
        elif kind == 'seek': self.seek.emit(max(0., beats))
        elif kind == 'clip-duplicate':
            # Resolve from the press snapshot, so repeated move events never make extra copies.
            _, index, origin, start, original = self.drag
            if abs(beats - origin) * self.scale <= CLICK_SLOP:
                if self.project != original:
                    self.selected_clip = index
                    self.emit(original); self.clip_selected.emit(index)
                return
            try: project, selected = duplicate_clip(original, index, self.snapped(start + beats - origin))
            except ValueError as error: self.notice.emit(str(error)); return
            self.selected_clip = selected
            self.emit(project); self.clip_selected.emit(selected)
        elif kind == 'loop':
            a, b = sorted((self.drag[1], self.snapped(beats)))
            if b - a < .25: b = a + max(self.snap_step(a), .25)
            self.emit(replace(self.project, loop=True, loop_start=a, loop_end=b))
        elif kind == 'clip-move':
            # From the press snapshot (UI1): past a neighbour's middle the clips swap, a free gap
            # that holds the block takes it, and the block never overlaps another clip.
            _, index, origin, start, length, original, snapshot, picks, press, shown = self.drag
            if self.project is not shown[0]:  # Another edit (a key, Undo) replaced the project mid-drag.
                self.drag = None; return
            if len(shown) == 1 and (QPointF(x, y) - press).manhattanLength() > CLICK_SLOP: shown.append(True)  # Moved: not a click.
            first = snapshot.clips[picks[0]].start
            wanted = self.snapped(start + beats - origin) - (start - first)
            project, moved = move_clips(snapshot, picks, wanted)
            self.selected_clip = moved[picks.index(index)]
            if len(picks) > 1: self.clip_picks = set(moved)
            if project is not self.project:
                shown[0] = project
                self.project = project; self.update(); self.clip_moved.emit(project, self.selected_clip)
        elif kind in ('clip-resize', 'clip-resize-left'):
            _, index, origin, start, length, original = self.drag[:6]; delta = beats - origin
            if kind == 'clip-resize-left':
                end, low = start + length, self.project.clips[index - 1].end if index > 0 else 0.
                first = max(low, min(self.snapped(start + delta), end - (self.snap_step(start + delta) or .25)))
                # The clip's content stays put in song time: lanes count from the clip start, so shift
                # them by the start's move, and advance the scene clock t by the same amount
                # (always from the clip as it was at mouse-press).
                moved = shift_clip_lanes(original, start - first)
                if original.local_time: moved = replace(moved, time_offset=original.time_offset + (first - start))
                project = fit_clip(with_clip(self.project, index, moved), index, first, end - first)
            else: project = fit_clip(self.project, index, start, max(self.snap_step(start + length + delta) or .25, self.snapped(start + length + delta) - start))
            self.emit(project)
        elif kind in ('note-move', 'note-copy'):
            _, index, origin, note, pitch, original, picked, press, moved = self.drag
            if not moved[0] and (QPointF(x, y) - press).manhattanLength() <= CLICK_SLOP: return
            moved[0] = True
            try:
                tool = note_tools.copy_moved if kind == 'note-copy' else move_notes
                notes, indices = tool(original, picked, self.pitch_at(y) - pitch,
                                      self.snapped(note.start + beats - origin) - note.start)
                notes = tuple(replace(n, pitch=self.scale_pitch(n.pitch)) if i in indices else n for i, n in enumerate(notes))
                project = validate_project(replace(self.project, notes=notes))
            except ValueError as error: self.notice.emit(str(error)); return
            self.note_selection = set(indices)
            if kind == 'note-copy': self.selected_note = None
            self.emit(project)
        elif kind == 'note-box':
            self.selection_rect = QRectF(self.drag[1], QPointF(max(GUTTER, x), min(self.note_bottom(), max(self.roll_top, y)))).normalized()
            self.note_selection = self.drag[2] | {
                i for i, n in enumerate(self.project.notes)
                if self.selection_rect.intersects(QRectF(self.x_of(n.start), self.y_of(n.pitch) + 1,
                                                        max(2., n.length * self.scale), self.row - 1))}
            self.update()
        elif kind == 'note-resize':
            _, indices, origin, note = self.drag[:4]
            step = self.snap_step(note.start)
            end = self.snapped(max(beats, note.start + (step or .25)), math.ceil) if self.snap else beats
            length = min(MAX_BEATS, max(step or note_tools.MIN_LENGTH, end - note.start))
            notes = tuple(replace(n, length=length, glide=min(n.glide, length)) if i in indices else n
                          for i, n in enumerate(self.project.notes))
            self.emit(replace(self.project, notes=notes))

    def add_node_on_line(self, key, original, segment, rect, x):
        """A click on a segment: a new node at the clicked beat, exactly on the line."""
        clip = self.clip; points = list(original)
        beat = min(max(0., self.snapped(self.beats_at(x) - clip.start, origin=clip.start)), clip.length)
        b0, b1 = points[segment][0], points[segment+1][0]
        if not b0 < beat < b1: return  # Snapped onto (or past) an existing node.
        left, right = lane_edit.split_segment(tuple(points), segment, (beat-b0)/(b1-b0))
        points[segment:segment+1] = [left, right]
        self.set_lane(key, points); self.node_selection = (key, {segment+1}); self.lane_target = key

    def mouseReleaseEvent(self, event):
        pending_audio, self.pending_audio_menu = self.pending_audio_menu, None
        if pending_audio is not None and event.button() == Qt.MouseButton.RightButton:
            QTimer.singleShot(0, lambda: self.open_audio_menu(*pending_audio)); return
        if self.drag and self.drag[0] == 'track-move' and self.drag[4] is not None and self.project is self.drag[3]:
            self.select_track(self.drag[4])  # A plain click on a selected header (no drag) selects only it.
        elif self.drag and self.drag[0] == 'track-move' and self.project != self.drag[3]:
            self.track_move_finished.emit(len(self.drag[1]))
        if self.drag and self.drag[0].startswith(('audio-', 'track-')):
            self.drag = None; self.update(); return
        if self.drag and self.drag[0].startswith('velocity-'):
            self.velocity_move(event.position().x(), event.position().y())
            if self.drag[0] == 'velocity-line':
                _, original, a, b, picked = self.drag
                def endpoint(p): return (self.beats_at(p.x()), round(self.velocity_strip.value_at(p.y()) * 127) / 127)
                notes, _ = note_tools.velocity_line(original, picked, endpoint(a), endpoint(b))
                if notes != self.project.notes: self.emit(replace(self.project, notes=notes))
            self.drag = None; self.update(); return
        click, self.note_click = self.note_click, None
        if click is not None and event.button() == Qt.MouseButton.LeftButton and self.roll_hear_notes:
            if (event.position() - click[1]).manhattanLength() <= CLICK_SLOP:
                note = self.project.notes[click[0]]
                self.audition.emit(note.pitch, note.start)
        if self.pending_node_gain is not None and event.button() == Qt.MouseButton.LeftButton:
            pending, self.pending_node_gain = self.pending_node_gain, None
            self.drag = None
            QTimer.singleShot(0, lambda: self.node_gain_dialog(*pending))
            return
        pending_node = getattr(self, 'pending_lane_node_menu', None)
        self.pending_lane_node_menu = None
        if pending_node is not None and event.button() == Qt.MouseButton.RightButton:
            QTimer.singleShot(0, lambda: self.node_menu(*pending_node))
            return
        pending_switch, self.pending_switch_menu = self.pending_switch_menu, None
        if pending_switch is not None and event.button() == Qt.MouseButton.RightButton:
            key, beat, position = pending_switch
            QTimer.singleShot(0, lambda: self.build_switch_menu(key, beat).exec(position))
            return
        pending_segment = getattr(self, 'pending_segment_menu', None)
        self.pending_segment_menu = None
        if pending_segment is not None and event.button() == Qt.MouseButton.RightButton:
            key, points, index, position = pending_segment
            QTimer.singleShot(0, lambda: self.build_segment_menu(key, points, index).exec(position))
            return
        if self.drag and self.drag[0] == 'clip-move' and event.button() == Qt.MouseButton.LeftButton:
            moved = len(self.drag[9]) > 1 or (event.position() - self.drag[8]).manhattanLength() > CLICK_SLOP
            if moved:
                if self.project is not self.drag[6] and self.project is self.drag[9][0]: self.clip_move_finished.emit()  # Not over another edit.
            elif len(self.drag[7]) > 1 and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.clip_picks = set(); self.update()  # A click on a picked clip picks it alone.
        pending_note, self.pending_note_menu = self.pending_note_menu, None
        if pending_note is not None and event.button() == Qt.MouseButton.RightButton:
            QTimer.singleShot(0, lambda: self.note_menu(*pending_note))
            return
        pending_ruler, self.pending_ruler_menu = self.pending_ruler_menu, None
        if pending_ruler is not None and event.button() == Qt.MouseButton.RightButton:
            QTimer.singleShot(0, lambda: self.ruler_menu(*pending_ruler))
            return
        pending_marker, self.pending_marker_menu = self.pending_marker_menu, None
        if pending_marker is not None and event.button() == Qt.MouseButton.RightButton:
            QTimer.singleShot(0, lambda: self.marker_menu(*pending_marker))
            return
        if self.drag and self.drag[0] in ('marker', 'flag'): self.mouseMoveEvent(event)
        if self.drag and self.drag[0] == 'note-box': self.mouseMoveEvent(event)
        if self.drag and self.drag[0] in ('note-move', 'note-copy'):
            if not self.drag[8][0]:
                self.note_selection = {self.drag[1]}
        self.selection_rect = None
        self.update()
        pending, self.pending_reference = self.pending_reference, None
        if pending is not None and event.button() == Qt.MouseButton.RightButton:
            QTimer.singleShot(0, lambda: self.reference_menu(*pending)); return  # After this release is fully handled.
        drag = self.drag; self.drag = None
        if drag and drag[0] == 'switch' and self.clip is not None:
            if not drag[6]: self.set_lane(drag[1], switch_lane.flip(drag[2], drag[3], self.clip.length))
            self.update(); return
        if drag and drag[0] == 'segment' and not drag[6][1] and self.clip is not None:
            self.add_node_on_line(drag[1], drag[4], drag[2], drag[3], drag[6][0]); self.update(); return
        if drag and drag[0] in ('node', 'segment', 'bend') and self.clip is not None:
            try: points = get_points(self.clip, drag[1])
            except (KeyError, ValueError): points = ()
            tidy = lane_edit.tidy(points) if points else points
            if tidy != points: self.set_lane(drag[1], tidy)  # Drop step nodes that ended up level.
            self.update()
        if drag and drag[0] == 'node' and drag[4] and not drag[5]:  # Shift-click on a selected node.
            key, index = drag[1], drag[2]; self.node_selection = (key, self.picked_nodes(key) - {index}); self.update()

    def mouseDoubleClickEvent(self, event):
        x, y = event.position().x(), event.position().y()
        rect = self.velocity_lane_rect()
        if self.project is not None and rect is not None and rect.contains(QPointF(x, y)) and event.button() == Qt.MouseButton.LeftButton:
            if self.velocity_strip.collapsed: return
            index = self.velocity_bar(x, y)
            if index is not None:
                if index not in self.note_selection: self.note_selection = {index}
                self.drag = None
                QTimer.singleShot(0, lambda: self.note_tool_dialog('Set velocity'))
            return
        if (self.project is not None and self.clip is not None and self.roll == TRANSFORM_ROLL
                and y >= self.roll_top and x >= GUTTER and event.button() == Qt.MouseButton.LeftButton):
            block, rect = self.block_at(y)
            if block is not None:
                key, index = self.node_at(block, rect, x, y)
                if index is not None and self.pressed_node == (key, index):
                    # The first click already selected an unselected node and left a selected
                    # one's selection alone, so a double-click on a selected node edits them all.
                    self.drag = None
                    picked = self.picked_nodes(key)
                    indices = picked if index in picked else {index}
                    self.pending_node_gain = (key, get_points(self.clip, key), index, indices)
                    return
        row, part = self.track_at(y)
        if part == 'track' and event.button() == Qt.MouseButton.LeftButton:
            if x >= GUTTER and self.audio_hit(x, y)[0] is None:
                self.drag = None; self.audio_add_requested.emit(self.snapped(self.beats_at(x)), row.index); return
            if x < GUTTER and row.track is not None:
                self.drag = None; kind = self.header_target(row, QPointF(x, y))
                if kind == 'gain': QTimer.singleShot(0, lambda: self.track_gain_dialog(row.index))
                elif kind == 'pan': QTimer.singleShot(0, lambda: self.track_pan_dialog(row.index))
                elif kind == 'phase': QTimer.singleShot(0, lambda: self.track_phase_dialog(row.index))
                elif kind == 'name': QTimer.singleShot(0, lambda: self.rename_track_dialog(row.index))
                return
        if self.metrics.ruler_height <= y < self.audio_top and self.clip_hit(x)[0] is None and x >= GUTTER:
            meter = Meter(self.project.meter_changes)
            start = float(meter.bar_start(int(meter.bar_of(max(0., self.beats_at(x)))[0])))
            try: project, index = add_clip(self.project, start)
            except ValueError: return
            self.selected_clip = index; self.emit(project); self.clip_selected.emit(index)

    def wheelEvent(self, event):
        """Mouse wheel and trackpad (UI2). The wheel steps by notches times the wheel sensitivity;
        a trackpad's two-finger drag moves the roll both ways by the finger distance times the
        trackpad sensitivity, like a touchscreen. Modifier gestures (wheel_mapping) work on both."""
        modifiers = event.modifiers()
        mapping = self.wheel_mapping
        height = wheel_mapping.matches(modifiers, mapping['wheel_height'])
        zoom = wheel_mapping.matches(modifiers, mapping['wheel_zoom'])
        scroll = wheel_mapping.matches(modifiers, mapping['wheel_scroll'])
        if self.alt_wheel_filter is not None and modifiers & Qt.KeyboardModifier.AltModifier and any(
                mapping[key] == 'alt' for key in wheel_mapping.ACTIONS[:3]):
            self.alt_wheel_filter.mark()
        pixel, angle = event.pixelDelta(), event.angleDelta()
        device = event.pointingDevice()
        trackpad = scroll_mapping.is_trackpad(pixel.x(), pixel.y(), device is not None and device.type() == QInputDevice.DeviceType.TouchPad,
                                              event.phase() != Qt.ScrollPhase.NoScrollPhase)
        if event.phase() in (Qt.ScrollPhase.ScrollBegin, Qt.ScrollPhase.ScrollEnd) or trackpad != self.last_trackpad:
            for steps in (self.row_steps, self.height_steps, self.lane_steps): steps.reset()  # No fraction from another gesture.
        self.last_trackpad = trackpad
        if trackpad:
            # Pixels the fingers moved (a touchpad without pixel deltas: TRACKPAD_ANGLE_PIXELS per angle unit).
            dx, dy = ((pixel.x(), pixel.y()) if not pixel.isNull() else (angle.x() * TRACKPAD_ANGLE_PIXELS, angle.y() * TRACKPAD_ANGLE_PIXELS))
            dx, dy = (scroll_mapping.trackpad_pixels(v, self.trackpad_sensitivity) for v in (dx, dy))
            steps = (dy or dx) / TRACKPAD_NOTCH_PIXELS  # For modifier gestures: zoom and row height.
        else:
            # A mouse wheel keeps the old steps: one signed step per event for rows, pitch, blocks and zoom,
            # the notch size for time; both times the wheel sensitivity (exactly the old wheel at 1×).
            delta = angle.y() or angle.x()
            steps = math.copysign(self.wheel_sensitivity, delta) if delta else 0.
            dx, dy = scroll_mapping.wheel_notches(angle.x(), self.wheel_sensitivity), steps
        x, y = event.position().x(), event.position().y()
        if zoom and self.roll == NOTE_ROLL and x < GUTTER and y >= self.roll_top and steps:
            self.resize_rows(y, self.height_steps.add(steps))
        elif height and y >= self.roll_top and steps:
            # Command (Alt elsewhere) over the cells: note row or automation block height.
            self.resize_rows(y, self.height_steps.add(steps))
        elif zoom or height:  # Height off the cells also zooms time.
            if steps: self.zoom_at(x, 1.15 ** steps)
        elif (self.audio_top <= y < self.audio_end and self.track_extent[0] > self.track_extent[1] and not scroll
              and (trackpad or not angle.x())):  # More audio tracks than fit: scroll them (AW1); fingers still move time.
            if trackpad: self.scroll_tracks(-dy); self.pan(dx, 0)
            else: self.scroll_tracks(-dy * AUDIO_TRACK / 2)
        elif trackpad and scroll:  # The Scroll time modifier: the fingers move time, either way (Codex review).
            self.scroll = max(0., self.scroll - (dx or dy) / self.scale)
        elif trackpad:
            self.pan(dx, dy)
        elif scroll or angle.x():
            notches = scroll_mapping.wheel_notches(angle.y() or angle.x(), self.wheel_sensitivity)
            self.scroll = max(0., self.scroll - notches * 4 / max(1., self.scale / 28))
        elif self.roll == TRANSFORM_ROLL:
            self.move_lanes(-dy * (self.block // 2))
        else:
            self.top_pitch = min(PITCH_RANGE[1], max(PITCH_RANGE[0] + 12, self.top_pitch + self.row_steps.add(2 * dy)))
        self.update(); event.accept()

    def pan(self, dx, dy):
        """Move the content by (dx, dy) pixels with the fingers: time sideways, pitch rows or blocks up and down."""
        if dx: self.scroll = max(0., self.scroll - dx / self.scale)
        if not dy: return
        if self.roll == TRANSFORM_ROLL: self.move_lanes(-dy)
        else: self.top_pitch = min(PITCH_RANGE[1], max(PITCH_RANGE[0] + 12, self.top_pitch + self.row_steps.add(dy / self.row)))

    def move_lanes(self, pixels):
        """Scroll the transform roll's blocks by pixels, carrying fractions (slow fingers still move it)."""
        self.lane_scroll = min(self.lane_room(), max(0, self.lane_scroll + self.lane_steps.add(pixels)))

    def zoom_at(self, x, factor):
        """Zoom time by factor keeping the beat under x in place."""
        old = self.scale; self.zoom(factor)
        self.scroll = max(0., scroll_mapping.zoom_about(x - GUTTER, self.scroll, old, self.scale))

    def event(self, event):
        # Trackpad pinch (UI2): zoom time around the pointer; with Option/Alt, row or block height.
        if event.type() == QEvent.Type.NativeGesture:
            kind = event.gestureType()
            if kind == Qt.NativeGestureType.ZoomNativeGesture:
                factor = scroll_mapping.pinch_factor(event.value(), self.trackpad_sensitivity)
                x, y = event.position().x(), event.position().y()
                if (event.modifiers() | QApplication.keyboardModifiers()) & Qt.KeyboardModifier.AltModifier:
                    self.resize_rows(max(y, self.roll_top), self.height_steps.add((factor - 1) * PINCH_ROW_STEPS))
                else: self.zoom_at(x, factor)
                self.update(); event.accept(); return True
            if kind in (Qt.NativeGestureType.BeginNativeGesture, Qt.NativeGestureType.EndNativeGesture):
                self.height_steps.reset(); event.accept(); return True
        return super().event(event)

    def resize_rows(self, y, step):
        """Taller (step > 0) or shorter (< 0) note rows or automation blocks, keeping the row under y."""
        if not step: return
        if self.roll == TRANSFORM_ROLL:
            offset = y - self.roll_top - LANES_TOP + self.lane_scroll; old = self.block
            self.block = min(BLOCK_RANGE[1], max(BLOCK_RANGE[0], self.block + step * BLOCK_STEP))
            self.lane_scroll = int(min(self.lane_room(), max(0, offset * self.block / old - (y - self.roll_top - LANES_TOP))))
        else:
            pitch = self.top_pitch - (y - self.roll_top) / self.row
            self.row = min(ROW_RANGE[1], max(ROW_RANGE[0], self.row + step))
            self.top_pitch = int(round(min(PITCH_RANGE[1], max(PITCH_RANGE[0] + 12, pitch + (y - self.roll_top) / self.row))))

    def keyPressEvent(self, event):
        if self.project is not None and self.roll == NOTE_ROLL and self.focus_kind == 'notes':
            if event.matches(QKeySequence.StandardKey.SelectAll):
                self.note_selection = set(range(len(self.project.notes)))
                self.selected_note = None; self.update(); return
            key = event.key()
            modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
            if (self.note_selection and modifiers in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier)
                    and key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right)):
                semitones = (12 if modifiers & Qt.KeyboardModifier.ShiftModifier else 1) * (
                    1 if key == Qt.Key.Key_Up else -1 if key == Qt.Key.Key_Down else 0)
                beats = (self.snap_step(self.playhead) or .25) * (1 if key == Qt.Key.Key_Right else -1 if key == Qt.Key.Key_Left else 0)
                notes, indices = move_notes(self.project.notes, self.note_selection, semitones, beats)
                if semitones and not modifiers & Qt.KeyboardModifier.ShiftModifier and self.roll_scale_snap and self.roll_scale != 'Off':
                    notes = tuple(replace(n, pitch=note_tools.step_in_scale(self.project.notes[i].pitch, semitones, self.roll_scale, self.roll_scale_root))
                                  if i in self.note_selection else n for i, n in enumerate(notes))
                self.note_selection = set(indices); self.emit(replace(self.project, notes=notes)); return
        if event.matches(QKeySequence.StandardKey.Copy): self.copy_selection(); return
        if event.matches(QKeySequence.StandardKey.Cut): self.cut_selection(); return
        if event.matches(QKeySequence.StandardKey.Paste): self.paste(); return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace): self.delete_selection(); return
        super().keyPressEvent(event)

    # Clipboard -----------------------------------------------------------------
    def picked_nodes(self, key):
        return set(self.node_selection[1]) if self.node_selection[0] == key else set()

    def picked_clips(self):
        """Indices a clip drag moves: the Shift+clicked block, always with the selected clip."""
        count = len(self.project.clips) if self.project is not None else 0
        return sorted(i for i in {*self.clip_picks, self.selected_clip} if 0 <= i < count)

    def selection_payload(self):
        """The clipboard payload for what Copy acts on now, or None."""
        project, kind = self.project, self.focus_kind
        if project is None: return None
        if kind == 'notes' and self.note_selection: return clipboard.copy_notes(project, sorted(self.note_selection))
        if kind == 'clips' and self.clip is not None: return clipboard.copy_clips(project, [self.selected_clip])
        if kind == 'tracks' and self.track_picks: return clipboard.copy_tracks(project, sorted(self.track_picks))
        if kind == 'nodes' and self.node_selection[1] and self.clip is not None:
            key, picked = self.node_selection
            try: points = get_points(self.clip, key)
            except (KeyError, ValueError): return None
            picked = sorted(i for i in picked if i < len(points))
            return clipboard.copy_nodes(points, picked) if picked else None
        return None

    def copy_selection(self):
        if self.focus_kind == 'audio':
            self.notice.emit('Audio clips are not copied to the clipboard: right-click › Duplicate, or Alt/Option-drag.'); return False
        try: payload = self.selection_payload()
        except ValueError as error: self.notice.emit(f'Copy failed: {error}'); return False
        if payload is None: self.notice.emit('Nothing selected to copy.'); return False
        mime = QMimeData(); mime.setData(clipboard.MIME, clipboard.to_text(payload).encode('utf-8'))
        QApplication.clipboard().setMimeData(mime)
        self.notice.emit(f'Copied {clipboard.describe(payload)}.'); return True

    def cut_selection(self):
        if self.copy_selection(): self.delete_selection()

    def clipboard_payload(self):
        mime = QApplication.clipboard().mimeData()
        if mime is None or not mime.hasFormat(clipboard.MIME): return None
        try: return clipboard.from_text(bytes(mime.data(clipboard.MIME)).decode('utf-8'))
        except (ValueError, UnicodeDecodeError): return None

    def paste(self):
        """Paste notes or clips at the playhead, or nodes into the last edited lane."""
        mime = QApplication.clipboard().mimeData()
        if mime is None or not mime.hasFormat(clipboard.MIME): self.notice.emit('The clipboard has no phosphor notes, clips or nodes.'); return
        try:
            notices = []; payload = clipboard.from_text(bytes(mime.data(clipboard.MIME)).decode('utf-8'), notices)
            if notices: self.notice.emit(' · '.join(notices))
            beat = max(0., self.playhead)
            if payload['kind'] == 'tracks':
                self.paste_tracks(); return
            if payload['kind'] == 'notes':
                project, indices = clipboard.paste_notes(self.project, payload, beat)
                self.note_selection, self.selected_note, self.focus_kind = set(indices), None, 'notes'
                if self.roll != NOTE_ROLL: self.set_roll(NOTE_ROLL)
                self.emit(project)
            elif payload['kind'] == 'clips':
                project, indices = clipboard.paste_clips(self.project, payload, beat)
                self.selected_clip, self.focus_kind = indices[0], 'clips'
                self.emit(project); self.clip_selected.emit(indices[0])
            else:
                clip, key = self.clip, self.lane_target
                if clip is None or key is None: raise ValueError('Click a lane in the transform roll first; nodes paste into it.')
                try: points = get_points(clip, key)
                except (KeyError, ValueError): raise ValueError('The lane nodes were copied from is gone; click another lane.') from None
                points, indices = clipboard.paste_nodes(points, payload, beat - clip.start, clip.length)
                self.node_selection, self.focus_kind = (key, set(indices)), 'nodes'
                self.active[block_of(key)] = key; self.set_lane(key, points)
                if self.roll != TRANSFORM_ROLL: self.set_roll(TRANSFORM_ROLL)
        except (ValueError, UnicodeDecodeError) as error:
            self.notice.emit(f'Paste failed: {error}'); return
        self.notice.emit(f'Pasted {clipboard.describe(payload)}.')

    def delete_selection(self):
        kind = self.focus_kind
        if kind == 'notes' and self.note_selection:
            notes = [n for i, n in enumerate(self.project.notes) if i not in self.note_selection]
            self.note_selection, self.selected_note = set(), None
            self.emit(replace(self.project, notes=tuple(notes)))
        elif kind == 'audio' and self.selected_audio is not None:
            self.delete_audio(self.selected_audio)
        elif kind == 'tracks' and self.track_picks:
            self.delete_tracks(self.track_picks)
        elif kind == 'track-lane':
            node = self.track_lane_node()
            if node is not None: self.delete_track_node(*node)
        elif kind == 'clips' and self.clip is not None:
            if len(self.project.clips) > 1: self.delete_clip(self.selected_clip)
            else: self.notice.emit('The last clip cannot be deleted.')
        elif kind == 'nodes' and self.node_selection[1] and self.clip is not None:
            key, picked = self.node_selection
            try: points = get_points(self.clip, key)
            except (KeyError, ValueError): return
            self.node_selection = (key, set())
            self.set_lane(key, [point for i, point in enumerate(points) if i not in picked])

    def edit_actions(self, menu):
        """Copy / Cut / Paste at the top of a right-click menu."""
        payload = self.clipboard_payload()
        for text, keys, slot, enabled in (('Copy', 'copy', self.copy_selection, True),
                                          ('Cut', 'cut', self.cut_selection, True),
                                          ('Paste' + (f' {clipboard.describe(payload)}' if payload else ''), 'paste', self.paste, payload is not None)):
            action = menu.addAction(text); action.setShortcuts(key_sequences(keys)); action.setProperty('shortcut_id', keys); action.setEnabled(enabled)
            action.triggered.connect(slot)
        menu.addSeparator()

    def paste_menu(self, position):
        menu = QMenu(self); payload = self.clipboard_payload()
        action = menu.addAction('Paste' + (f' {clipboard.describe(payload)}' if payload else '') + ' at the playhead')
        action.setShortcuts(key_sequences('paste')); action.setProperty('shortcut_id', 'paste'); action.setEnabled(payload is not None); action.triggered.connect(self.paste)
        menu.exec(position)

    def build_note_menu(self, index, beat=None):
        menu = QMenu(self)
        if index not in self.note_selection: self.note_selection = {index}
        self.focus_kind = 'notes'
        self.edit_actions(menu)
        self.add_selection_tools(menu)
        action = menu.addAction('Split here')
        action.triggered.connect(lambda: self.split_selection(self.snapped(self.playhead if beat is None else beat, round)))
        menu.addAction('Glide…').triggered.connect(lambda: self.edit_glide(index))
        menu.addAction('Delete notes').triggered.connect(self.delete_selection)
        return menu

    def note_menu(self, index, position, beat=None, velocity=False):
        menu = self.build_velocity_menu(index, beat) if velocity else self.build_note_menu(index, beat)
        menu.exec(position); menu.deleteLater()

    def build_velocity_menu(self, index=None, beat=None):
        """The velocity lane's menu: the note menu on a bar, Paste elsewhere, then Collapse / Expand."""
        if self.velocity_strip.collapsed: menu = QMenu(self)
        elif index is not None: menu = self.build_note_menu(index, beat); menu.addSeparator()
        else:
            menu = QMenu(self); payload = self.clipboard_payload()
            action = menu.addAction('Paste' + (f' {clipboard.describe(payload)}' if payload else '') + ' at the playhead')
            action.setShortcuts(key_sequences('paste')); action.setProperty('shortcut_id', 'paste')
            action.setEnabled(payload is not None); action.triggered.connect(self.paste)
            menu.addSeparator()
        collapsed = self.velocity_strip.collapsed
        action = menu.addAction('Expand velocity lane' if collapsed else 'Collapse velocity lane')
        action.setObjectName('expand_velocity_lane' if collapsed else 'collapse_velocity_lane')
        action.triggered.connect(lambda checked=False: self.collapse_velocity_lane(not collapsed))
        return menu

    def velocity_menu(self, index, position):
        menu = self.build_velocity_menu(index); menu.exec(position); menu.deleteLater()

    def edit_glide(self, index):
        indices = self.note_selection if index in self.note_selection else {index}
        limit = min(self.project.notes[i].length for i in indices)
        value, ok = QInputDialog.getDouble(self, 'Glide', 'Glide (beats; 0 disables):',
                                          self.project.notes[index].glide, 0., limit, 4)
        if ok:
            notes = tuple(replace(n, glide=value) if i in indices else n
                          for i, n in enumerate(self.project.notes))
            self.emit(replace(self.project, notes=notes))

    # Edits ---------------------------------------------------------------------
    def emit(self, project):
        self.clip_picks = set()  # Indices of a Shift+clicked block may name other clips after this edit.
        self._emitted = project
        previous_lanes = self.effect_lanes
        if self.project is not None and track_layout(self.project) != track_layout(project):
            self.track_meters, self.track_clipped = [], set()
            self.effect_lanes = {}  # Tracks moved or changed: an index may name another track (review).
        self.project = project; self.update(); self.edited.emit(project, self.selected_clip)
        if previous_lanes != self.effect_lanes: self.shown_lane_changed.emit()

    def replace_note(self, index, note):
        notes = list(self.project.notes); notes[index] = note; self.emit(replace(self.project, notes=tuple(notes)))

    def remove_note(self, index):
        notes = list(self.project.notes); notes.pop(index); self.selected_note = None; self.note_selection = set()
        self.emit(replace(self.project, notes=tuple(notes)))

    def clip_menu(self, index, position):
        menu, delete = self.build_clip_menu(index)
        chosen = menu.exec(position)
        if chosen is delete: self.delete_clip(index)

    def build_clip_menu(self, index):
        """The clip's right-click menu (built separately so smokes can inspect it)."""
        menu = QMenu(self)
        self.edit_actions(menu)
        split = menu.addAction('Split at playhead')
        split.setShortcuts(key_sequences('split_clip'))
        split.setEnabled(self.project.clips[index].start < self.playhead < self.project.clips[index].end)
        split.triggered.connect(self.split_at_playhead)
        # Parented explicitly: a submenu returned by addMenu(title) dies with its wrapper.
        colours = QMenu('Colour', menu); menu.addMenu(colours); menu.colours = colours
        for color, name in [(None, 'Default')] + [(i, item[0]) for i, item in enumerate(themes.for_widget(self).clip_colors)]:
            swatch = QPixmap(16, 16); swatch.fill(self.clip_color(color))
            action = colours.addAction(QIcon(swatch), name)
            action.setCheckable(True); action.setChecked(self.project.clips[index].color == color)
            action.setData(color)
            action.triggered.connect(lambda checked=False, color=color: self.set_clip_color(index, color))
        mute = menu.addAction('Mute clip'); mute.setCheckable(True); mute.setChecked(self.project.clips[index].muted)
        mute.setToolTip('A muted clip is silent, like a gap; its scene and lanes are kept.')
        mute.triggered.connect(lambda checked=False: self.set_clip_muted(index, not self.project.clips[index].muted))
        menu.mute = mute
        delete = menu.addAction('Delete clip'); delete.setEnabled(len(self.project.clips) > 1)
        return menu, delete

    def set_clip_muted(self, index, muted):
        self.emit(with_clip(self.project, index, replace(self.project.clips[index], muted=muted)))

    def clip_color(self, index):
        if index is None: return QColor(self.colors['accent'])
        hue, trace = QColor(themes.for_widget(self).clip_colors[index][1]), QColor(self.colors['trace'])
        # Bright pastel hues retain contrast on all five CRT backgrounds.
        return QColor(*(round(.85 * a + .15 * b) for a, b in zip(
            (hue.red(), hue.green(), hue.blue()), (trace.red(), trace.green(), trace.blue()))))

    def set_clip_color(self, index, color):
        self.emit(with_clip(self.project, index, replace(self.project.clips[index], color=color)))

    def split_at_playhead(self):
        if self.project is None: return
        if self.focus_kind == 'audio' and self.selected_audio is not None: self.split_audio(); return
        index = next((i for i, c in enumerate(self.project.clips) if c.start < self.playhead < c.end), None)
        if index is None: return
        try: project = split_clip(self.project, index, self.playhead)
        except ValueError as error: self.notice.emit(str(error)); return
        self.selected_clip = index + 1
        self.emit(project); self.clip_selected.emit(index + 1)

    def delete_clip(self, index):
        if len(self.project.clips) <= 1: return  # There is always a scene to edit.
        project = remove_clip(self.project, index)
        self.selected_clip = min(index, len(project.clips) - 1)
        self.emit(project); self.clip_selected.emit(self.selected_clip)


class ComposerPanel(QWidget):
    """Transport row above the timeline. The window connects its signals."""
    def __init__(self):
        super().__init__()
        self.setObjectName('composer_panel')
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 4, 0, 0); layout.setSpacing(4)
        self.transport_row = row = QHBoxLayout(); layout.addLayout(row); row.setSpacing(6)
        self.clip_row = QHBoxLayout(); layout.addLayout(self.clip_row); self.clip_row.addStretch(1)  # Wrapped clip controls sit left.
        self.clip_row_wrapped = False
        self.to_start = QToolButton(); self.to_start.setText('⏮'); self.to_start.setToolTip('Move the playhead to the start')
        self.play = QPushButton('Play'); self.play.setObjectName('composer_play'); self.play.setToolTip('Play the song from the playhead, or stop')
        self.position = QLabel(); self.position.setObjectName('composer_position'); self.position.setMinimumWidth(150)
        row.addWidget(self.to_start); row.addWidget(self.play); row.addWidget(self.position)
        self.record = QToolButton(); self.record.setText('Record'); self.record.setCheckable(True)
        self.record.setObjectName('composer_record')
        self.record.setStyleSheet(themes.for_widget(self).record_stylesheet())
        self.record.setToolTip('Record Live MIDI into notes; arrow: recording options')
        self.record.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        menu = QMenu(self.record)
        modes = QActionGroup(menu); modes.setExclusive(True)
        self.record_overdub = menu.addAction('Overdub'); self.record_overdub.setCheckable(True); modes.addAction(self.record_overdub)
        self.record_overdub.setChecked(True)
        self.record_replace = menu.addAction('Replace'); self.record_replace.setCheckable(True); modes.addAction(self.record_replace)
        self.record_quantise = menu.addAction('Quantise to Snap'); self.record_quantise.setCheckable(True)
        self.record.setMenu(menu); row.addWidget(self.record)
        row.addSpacing(8); row.addWidget(QLabel('Tempo'))
        self.bpm = QDoubleSpinBox(); self.bpm.setRange(*BPM_RANGE); self.bpm.setDecimals(1); self.bpm.setSuffix(' BPM'); self.bpm.setKeyboardTracking(False)
        row.addWidget(self.bpm)
        self.tap = QToolButton(); self.tap.setText('Tap'); self.tap.setToolTip('Tap tempo (last four taps; resets after 2 seconds)')
        row.addWidget(self.tap)
        self.loop = QCheckBox('Loop'); self.loop.setToolTip('Repeat the loop range (Shift-drag on the ruler to set it)'); row.addWidget(self.loop)
        row.addSpacing(8); row.addWidget(QLabel('Snap'))
        self.snap = QComboBox(); self.snap.addItems(list(SNAPS)); self.snap.setCurrentText('1/16'); row.addWidget(self.snap)
        self.zoom_out = QToolButton(); self.zoom_out.setText('−'); self.zoom_out.setToolTip('Zoom out (Control+wheel)')
        self.zoom_in = QToolButton(); self.zoom_in.setText('+'); self.zoom_in.setToolTip('Zoom in (Control+wheel)')
        row.addWidget(self.zoom_out); row.addWidget(self.zoom_in)
        self.note_tools = QToolButton(); self.note_tools.setText('Notes'); self.note_tools.setObjectName('composer_note_tools')
        self.note_tools.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        row.addWidget(self.note_tools)
        row.addStretch(1)
        self.clip_controls = QWidget(); self.clip_controls.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        clip_layout = QHBoxLayout(self.clip_controls); clip_layout.setContentsMargins(0, 0, 0, 0); clip_layout.setSpacing(row.spacing())
        row.addWidget(self.clip_controls)
        row = clip_layout
        # Monitoring controls share the wrapping clip row at narrow widths.
        self.metronome = QToolButton(); self.metronome.setText('Click'); self.metronome.setCheckable(True)
        self.metronome.setToolTip('Metronome: monitoring clicks only; never included in exports'); row.addWidget(self.metronome)
        # A toggle button like Click: a checkbox made the one-row transport wrap at 1700 px.
        self.cycle = QToolButton(); self.cycle.setText('Cycle'); self.cycle.setCheckable(True)
        self.cycle.setToolTip('While stopped, loop one trace cycle of the song at the playhead')
        row.addWidget(self.cycle)
        for button in (self.to_start, self.record, self.tap, self.zoom_out, self.zoom_in,
                       self.note_tools, self.metronome, self.cycle):
            button.setProperty('composerTransport', True)
        self.count_in = QComboBox(); self.count_in.addItems(('No count-in', 'Count 1 bar', 'Count 2 bars'))
        self.count_in.setToolTip('Count-in before playback starts'); row.addWidget(self.count_in)
        self.clip_label = QLabel(); self.clip_label.setObjectName('composer_clip'); row.addWidget(self.clip_label)
        row.addSpacing(8); row.addWidget(QLabel('Length'))
        self.clip_length = QDoubleSpinBox(); self.clip_length.setRange(.25, MAX_BEATS / BEATS_PER_BAR); self.clip_length.setDecimals(2)
        self.clip_length.setSuffix(' bars'); self.clip_length.setKeyboardTracking(False); self.clip_length.setToolTip('Length of the selected clip')
        row.addWidget(self.clip_length)
        self.add_clip = QPushButton('Add clip'); self.add_clip.setToolTip('Add an empty scene clip after the last one')
        self.delete_clip = QPushButton('Delete clip'); self.delete_clip.setToolTip('Delete the selected clip')
        row.addWidget(self.add_clip); row.addWidget(self.delete_clip)
        self.timeline = TimelineView(); self.timeline.setObjectName('timeline'); layout.addWidget(self.timeline, 1)
        tools_menu = QMenu(self.note_tools); self.note_tools.setMenu(tools_menu)
        def refresh_tools():
            tools_menu.clear()
            old = getattr(tools_menu, 'built', None)
            if old is not None: old.deleteLater()
            tools_menu.built = self.timeline.build_note_tools_menu()
            tools_menu.addActions(tools_menu.built.actions())
        tools_menu.aboutToShow.connect(refresh_tools)
        self.zoom_in.clicked.connect(lambda: self.timeline.zoom(1.25)); self.zoom_out.clicked.connect(lambda: self.timeline.zoom(1/1.25))
        self.snap.currentTextChanged.connect(lambda text: setattr(self.timeline, 'snap', SNAPS[text]))
        self.hint = ('Ruler: click to move the playhead, Shift-drag to set the loop · Scenes: click to edit, drag to move, '
                     'drag the right edge to resize, double-click to add · Notes: click empty space to draw, '
                     'Shift-drag empty space to select, Shift-click to toggle, drag selected notes to move, right edge to resize, '
                     'arrows to move (Shift+Up/Down: octave), Notes menu for chords, scale and selection tools; Alt/Option-drag to copy · Automation: click a line to add a node on it, drag it to move · Wheel: pitch · '
                     'Shift+wheel: move in time · Control+wheel: zoom · ' + ('Command' if sys.platform == 'darwin' else 'Alt') + '+wheel over the cells: row or block height')
        self.timeline.setToolTip(self.hint); self.timeline.hint_text = self.hint

    @staticmethod
    def _preferred_width(layout, omit=None):
        margins = layout.contentsMargins()
        widths = []
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if (omit is not None and item.widget() is omit) or (item.spacerItem() and item.expandingDirections()):
                continue
            widths.append(max(item.sizeHint().width(), item.minimumSize().width()))
        return sum(widths) + max(0, len(widths) - 1) * layout.spacing() + margins.left() + margins.right()

    def fit_snap(self):
        """Snap fits its widest entry with the font in use (measured late: the app font and
        stylesheet arrive after construction). The content hint left no room for the
        stylesheet's padding, so macOS showed "1/1…" (user report)."""
        metrics = self.snap.fontMetrics()
        self.snap.setMinimumWidth(max(metrics.horizontalAdvance(self.snap.itemText(i)) for i in range(self.snap.count())) + COMBO_CHROME)

    def transport_widgets(self):
        for row in (self.transport_row, self.clip_controls.layout()):
            for index in range(row.count()):
                widget = row.itemAt(index).widget()
                if widget is not None and widget is not self.clip_controls: yield widget

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_transport()

    def restyled(self):
        """A new style or font: size the transport again from each control's original minimum,
        so switching styles and back gives a fresh window's geometry (no ratchet)."""
        minimums = self.original_minimums()
        for widget in self.transport_widgets():
            if widget in minimums: widget.setMinimumWidth(minimums[widget])
        self.layout().activate()
        for widget in self.transport_widgets():
            if isinstance(widget, QLabel):
                # A QLabel keeps the size hint it measured with the previous stylesheet font;
                # setting its text again makes it measure with the new one.
                text = widget.text(); widget.clear(); widget.setText(text)
        self.fit_transport()

    def original_minimums(self):
        """Each transport control's own minimum width, from before any fitting (Snap fits itself)."""
        if not hasattr(self, 'transport_minimums'):
            self.transport_minimums = {widget: widget.minimumWidth() for widget in self.transport_widgets()
                                       if widget is not self.snap}
        return self.transport_minimums

    def fit_transport(self):
        self.original_minimums()
        self.fit_snap()
        for widget in self.transport_widgets():
            # Within a style a minimum only grows, so the row never jitters as texts change.
            widget.setMinimumWidth(max(widget.minimumWidth(), widget.sizeHint().width()))
        # The one-row requirement is independent of the container's current parent.
        threshold = (self._preferred_width(self.transport_row, self.clip_controls)
                     + self.transport_row.spacing() + self._preferred_width(self.clip_controls.layout()))
        margins = self.layout().contentsMargins()
        wrapped = self.width() - margins.left() - margins.right() < threshold
        if wrapped != self.clip_row_wrapped:
            self.clip_row_wrapped = wrapped
            source, target = ((self.transport_row, self.clip_row) if wrapped else
                              (self.clip_row, self.transport_row))
            source.removeWidget(self.clip_controls)
            self.clip_controls.setParent(None)
            if wrapped: target.insertWidget(0, self.clip_controls)
            else: target.addWidget(self.clip_controls)
            self.clip_controls.show()
