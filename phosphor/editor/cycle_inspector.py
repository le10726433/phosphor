"""INSPECT CYCLE panel: one drawing cycle at a frozen pose, a scrub cursor and a pinned reference.

Audit I1 (Astra's artwork tools 1 and 2). The data is engine/cycle_inspection.py's (an
uncached plan, the audio's own beam); this module only shows it. Display only: nothing here
changes a document, the audio or the plan cache. The pinned reference is session-local.
"""
from dataclasses import replace
import math

import numpy as np
from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QDoubleSpinBox, QSlider,
                               QSpinBox, QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
                               QSizePolicy)

from phosphor.engine import cycle_inspection as ci
from phosphor.engine.composition import COMPOSER, NoteSource, clip_settings
from phosphor.engine.signal_engine import source_frequency
from phosphor.audio.midi_synth import MIDI_SOURCE
from phosphor.display.qt_geometry import stroke_edges, stroke_triangles
from phosphor.editor.panel_widgets import InlineCaption

DEFAULT_PITCH = 110.  # When the source has no note at the pose (live MIDI, a rest).
LAYERS = 5  # Reference, route, focus, sample points, markers (GpuScope.OVERLAY_LAYERS has room).
ARROW_SPACING = 70.  # Screen pixels between direction chevrons.
MAX_ARROWS = 400
MAX_DASHES = 20000


def _screen(points, w, h, scale):
    return np.asarray(points, dtype=float) * [scale, -scale] + [w / 2, h / 2]


def _pairs(a, b):
    """Points and edges for separate segments a[i]–b[i]."""
    points = np.empty((2 * len(a), 2)); points[0::2] = a; points[1::2] = b
    return points, np.arange(2 * len(a)).reshape(-1, 2)


def dashes(a, b, dash=6., gap=4.):
    """Dash pieces (points, edges) along screen segments a[i]→b[i], at most MAX_DASHES."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    delta = b - a; length = np.linalg.norm(delta, axis=1)
    period = dash + gap
    count = np.where(length > 1e-6, np.ceil(length / period), 0).astype(np.int64)
    if count.sum() > MAX_DASHES:  # A huge zoom: longer dashes rather than an unbounded list.
        period *= count.sum() / MAX_DASHES; dash = period * .6
        count = np.where(length > 1e-6, np.ceil(length / period), 0).astype(np.int64)
    segment = np.repeat(np.arange(len(a)), count)
    k = np.arange(count.sum()) - np.repeat(np.cumsum(count) - count, count)
    start = k * period; end = np.minimum(start + dash, length[segment])
    scale = 1 / np.maximum(length[segment], 1e-12)
    return _pairs(a[segment] + delta[segment] * (start * scale)[:, None], a[segment] + delta[segment] * (end * scale)[:, None])


def route_strokes(points, travel, width, dashed=True):
    """Triangles for a route: drawing segments solid, travel dashed (or left out)."""
    if len(points) < 2: return np.empty((0, 2), dtype=np.float32)
    a, b = points[:-1], points[1:]
    pieces = [stroke_edges(*_pairs(a[~travel], b[~travel]), width)]
    if dashed and travel.any(): pieces.append(stroke_edges(*dashes(a[travel], b[travel]), width))
    return np.concatenate(pieces)


def _ring(center, radius, width, sides=20):
    angles = np.linspace(0, 2 * np.pi, sides + 1)
    return stroke_triangles(center + np.column_stack((np.cos(angles), np.sin(angles))) * radius, width)


def _chevrons(points, travel, size=6.):
    """Direction marks: a small arrowhead every ARROW_SPACING pixels of route."""
    a, b = points[:-1], points[1:]
    delta = b - a; length = np.linalg.norm(delta, axis=1)
    arc = np.concatenate(([0.], np.cumsum(length)))
    if arc[-1] <= 0: return np.empty((0, 2), dtype=np.float32)
    spacing = max(ARROW_SPACING, arc[-1] / MAX_ARROWS)
    marks = np.arange(spacing / 2, arc[-1], spacing)
    segment = np.clip(np.searchsorted(arc, marks, side='right') - 1, 0, len(a) - 1)
    keep = length[segment] > 1e-6
    segment, marks = segment[keep], marks[keep]
    unit = delta[segment] / length[segment, None]
    tip = a[segment] + unit * (marks - arc[segment])[:, None]
    normal = np.column_stack((-unit[:, 1], unit[:, 0]))
    left, right = tip - unit * size + normal * size * .6, tip - unit * size - normal * size * .6
    return stroke_edges(*_pairs(np.r_[left, right], np.r_[tip, tip]), 1.4)


def overlay_layers(inspection, reference, index, w, h, scale, palette, samples=True, show_reference=True):
    """Five (vertices, QColor) layers in screen pixels (scope.overlays entries)."""
    empty = np.empty((0, 2), dtype=np.float32)
    accent = QColor(palette['accent'])
    fg = QColor(palette.get('scope_fg', palette['fg']))
    muted = QColor(palette.get('scope_muted', palette['muted']))
    def faded(colour, alpha):
        colour = QColor(colour); colour.setAlphaF(alpha); return colour
    layers = []
    if reference is not None and show_reference:
        points = _screen(reference.points, w, h, scale)
        layers.append((route_strokes(points, np.asarray(reference.travel), 1.6), faded(muted, .85)))
    else: layers.append((empty, QColor('transparent')))
    if inspection is None:
        return layers + [(empty, QColor('transparent'))] * (LAYERS - 1)
    points = _screen(inspection.points, w, h, scale); travel = np.asarray(inspection.travel)
    layers.append((route_strokes(points, travel, 1.), faded(accent, .55)))
    info = ci.sample_info(inspection, index)
    focus = []
    if info.shape >= 0 and not info.travel:
        mine = (inspection.point_shape[:-1] == info.shape) & ~travel
        a, b = points[:-1][mine], points[1:][mine]
        focus.append(stroke_edges(*_pairs(a, b), 2.2))
    current = points[[info.segment, info.segment + 1]]
    focus.append(stroke_triangles(current, 3.5) if not info.travel else stroke_edges(*dashes(current[:1], current[1:]), 3.))
    layers.append((np.concatenate(focus), faded(accent, .95)))
    beam = _screen(inspection.samples, w, h, scale)
    if samples and len(beam):
        square = np.array([[-1.3, -1.3], [1.3, -1.3], [1.3, 1.3], [-1.3, -1.3], [1.3, 1.3], [-1.3, 1.3]])
        layers.append(((beam[:, None, :] + square[None]).reshape(-1, 2).astype(np.float32), faded(fg, .7)))
    else: layers.append((empty, QColor('transparent')))
    marks = [_chevrons(points, travel)]
    start = beam[0]
    marks.append(stroke_triangles(start + np.array([[-6, -6], [6, -6], [6, 6], [-6, 6], [-6, -6]]), 1.6))
    if len(inspection.corners):
        at = points[np.clip(np.searchsorted(inspection.timing, inspection.corners), 0, len(points) - 1)]
        diamond = np.array([[0, -4], [4, 0], [0, 4], [-4, 0], [0, -4]])
        marks.extend(stroke_triangles(point + diamond, 1.2) for point in at[:500])
    marks.append(_ring(beam[info.index], 7., 2.))
    layers.append((np.concatenate(marks), faded(fg, 1.)))
    return layers


class CycleInspectorPanel(QWidget):
    """The panel's controls plus the window glue: freezing, inspecting and overlay layers."""

    def __init__(self, window, label):
        super().__init__()
        self.window, self.label = window, label
        self.active = False
        self.frozen = None  # (workspace, clip index, pose seconds, motion offset)
        self.last_pose = (0., 0.)  # The display's latest (seconds, motion offset), from overlay().
        self.inspection = None; self.key = None; self.error = ''
        self.reference = None; self.reference_label = ''
        self.index = 0
        self.geometry_key = None; self.geometry = None
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.close_button = QPushButton('✕ Close inspector'); self.close_button.setObjectName('close_inspector')
        self.close_button.setToolTip('Turn the inspector off and go back to the panel shown before it (Esc).')
        self.close_button.clicked.connect(lambda checked=False: self.window.close_inspector())
        layout.addWidget(self.close_button)
        intro = QLabel('Freezes the pose and shows one drawing cycle: solid lines draw, dashed lines travel, '
                       'arrows give the direction. Display only: the audio never changes.')
        intro.setWordWrap(True); layout.addWidget(intro)
        row = QHBoxLayout(); layout.addLayout(row)
        self.inspect_button = QPushButton('Inspect'); self.inspect_button.setObjectName('inspect_cycle')
        self.inspect_button.setCheckable(True)
        self.inspect_button.setToolTip('Freeze the current pose and show one cycle of the beam over the scope.')
        self.inspect_button.clicked.connect(lambda checked=False: self.set_active(checked))
        row.addWidget(self.inspect_button, 1)
        self.freeze_button = QPushButton('Freeze now'); self.freeze_button.setObjectName('freeze_pose')
        self.freeze_button.setToolTip('Inspect the pose showing now (the playhead in Composer).')
        self.freeze_button.clicked.connect(lambda checked=False: self.freeze())
        row.addWidget(self.freeze_button, 1)
        self.pose_label = QLabel(); self.pose_label.setObjectName('inspect_pose'); self.pose_label.setWordWrap(True)
        layout.addWidget(self.pose_label)
        row = QHBoxLayout(); layout.addLayout(row)
        caption = InlineCaption('Pitch (Hz)')
        row.addWidget(caption)
        self.pitch = QDoubleSpinBox(); self.pitch.setObjectName('inspect_pitch'); self.pitch.setRange(*ci.PITCH_RANGE)
        self.pitch.setDecimals(2); self.pitch.setValue(DEFAULT_PITCH); self.pitch.setKeyboardTracking(False)
        self.pitch.setMinimumWidth(90)
        tip = 'The note the cycle is drawn at: higher notes have fewer samples per cycle.'
        caption.setToolTip(tip); self.pitch.setToolTip(tip)
        self.pitch.valueChanged.connect(lambda value: self.window.host.scope.update())
        row.addWidget(self.pitch, 1)
        self.source_button = QPushButton('Source'); self.source_button.setObjectName('inspect_source_pitch')
        self.source_button.setToolTip("Use the source's note at the frozen pose.")
        self.source_button.clicked.connect(lambda checked=False: self.use_source_pitch())
        row.addWidget(self.source_button)
        row = QHBoxLayout(); layout.addLayout(row)
        caption = InlineCaption('Sample')
        caption.setToolTip('Follow the beam sample by sample (arrow keys on the slider).'); row.addWidget(caption)
        self.slider = QSlider(Qt.Orientation.Horizontal); self.slider.setObjectName('inspect_scrub')
        self.slider.setRange(0, 0); self.slider.setSingleStep(1); self.slider.setPageStep(10)
        self.slider.setToolTip(caption.toolTip())
        self.slider.valueChanged.connect(self.scrub)
        row.addWidget(self.slider, 1)
        row = QHBoxLayout(); layout.addLayout(row)
        self.sample_box = QSpinBox(); self.sample_box.setObjectName('inspect_sample'); self.sample_box.setRange(0, 0)
        self.sample_box.setKeyboardTracking(False); self.sample_box.setMinimumWidth(80)
        self.sample_box.valueChanged.connect(self.scrub)
        row.addWidget(self.sample_box)
        self.count_label = QLabel(); self.count_label.setObjectName('inspect_count'); row.addWidget(self.count_label, 1)
        self.next_button = QPushButton('Next visit'); self.next_button.setObjectName('inspect_next_visit')
        self.next_button.setToolTip('Jump to the next time the beam runs along this edge (retraced edges and redraws).')
        self.next_button.clicked.connect(lambda checked=False: self.next_visit())
        row.addWidget(self.next_button)
        self.info = QLabel(); self.info.setObjectName('inspect_info'); self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.points_box = QCheckBox('Show sample points'); self.points_box.setObjectName('inspect_points')
        self.points_box.setChecked(True); self.points_box.setToolTip('A dot at every output sample of the cycle.')
        self.points_box.toggled.connect(lambda on: self.window.host.scope.update())
        layout.addWidget(self.points_box)
        heading = QLabel('Share of the cycle'); heading.setToolTip(
            'Phase budget per object, travel and corner dwell, and the samples of this cycle on each.')
        layout.addWidget(heading)
        self.table = QTableWidget(0, 3); self.table.setObjectName('inspect_shares')
        self.table.setHorizontalHeaderLabels(('Object', 'Cycle', 'Samples'))
        self.table.verticalHeader().hide(); self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2): header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.cellClicked.connect(lambda row, column: self.jump_to_row(row))
        layout.addWidget(self.table)
        heading = QLabel('Reference'); layout.addWidget(heading)
        row = QHBoxLayout(); layout.addLayout(row)
        self.pin_button = QPushButton('Pin this cycle'); self.pin_button.setObjectName('pin_reference')
        self.pin_button.setToolTip('Keep this cycle as a faint outline, then change a setting and compare at the same pose.')
        self.pin_button.clicked.connect(lambda checked=False: self.pin())
        row.addWidget(self.pin_button, 1)
        self.clear_button = QPushButton('Clear'); self.clear_button.setObjectName('clear_reference')
        self.clear_button.setToolTip('Remove the pinned reference.')
        self.clear_button.clicked.connect(lambda checked=False: self.clear_reference())
        row.addWidget(self.clear_button)
        self.reference_box = QCheckBox('Show reference'); self.reference_box.setObjectName('show_reference')
        self.reference_box.setChecked(True); self.reference_box.setToolTip('Show or hide the pinned outline.')
        self.reference_box.toggled.connect(lambda on: self.window.host.scope.update())
        layout.addWidget(self.reference_box)
        self.reference_info = QLabel(); self.reference_info.setObjectName('reference_info')
        self.reference_info.setWordWrap(True); layout.addWidget(self.reference_info)
        self.refresh_controls()

    def keyPressEvent(self, event):
        # Esc from a control in the panel (UI1). The window's Escape action normally takes it first
        # (exit_view_modes); this catches it when the shortcut did not run.
        from phosphor.editor.monitor_window import exit_key  # The window's Escape action's keys (rebindable, UI2).
        if exit_key(event): self.window.close_inspector(); event.accept(); return
        super().keyPressEvent(event)

    # Freezing and inspecting ------------------------------------------------------------
    def available(self):
        w = self.window
        return not (w.system_monitor or w.video_monitor) and w.scene.mode != 'music'

    def set_active(self, on):
        on = bool(on)
        if on and not self.active:
            self.active = True
            if self.reference is not None and self.reference_pose is not None and self.reference_pose[0] == self.window.workspace:
                self.frozen = self.reference_pose  # Compare at the reference's pose.
                self.inspection = None; self.key = None; self.use_source_pitch(update=False)
            else: self.freeze()
        elif not on:
            self.active = False; self.frozen = None; self.inspection = None; self.key = None
        action = getattr(self.window, 'inspect_action', None)
        if action is not None and action.isChecked() != self.active:
            blocker = QSignalBlocker(action); action.setChecked(self.active)
        self.refresh_controls(); self.window.host.scope.update()

    def observe(self, seconds, motion_seconds):
        """The display's pose this frame (overlay() reports it)."""
        self.last_pose = (float(seconds), float(motion_seconds - seconds))

    def freeze(self):
        """Freeze the pose showing now: the display's in Sandbox, the playhead's in Composer."""
        w = self.window
        if w.workspace == COMPOSER:
            clip = w.project.clips[w.clip_index]
            offset = float(w.project.seconds(clip.start - clip.time_offset)) if clip.local_time else 0.
            self.frozen = (COMPOSER, w.clip_index, max(0., w.playhead - offset), 0.)
        else:
            self.frozen = (w.workspace, None, max(0., self.last_pose[0]), self.last_pose[1])
        self.active = True; self.inspection = None; self.key = None
        self.use_source_pitch(update=False)
        self.refresh_controls(); w.host.scope.update()

    @property
    def reference_pose(self):
        return getattr(self, '_reference_pose', None)

    def context(self):
        """(settings, pose seconds, source pitch or 0) as the audio renders the frozen pose."""
        w = self.window
        workspace, clip_index, pose, motion = self.frozen
        machine = w.output_settings()
        if workspace == COMPOSER:
            project = w.output_project(); clip = project.clips[clip_index]
            settings = clip_settings(clip, machine, project)
            offset = float(project.seconds(clip.start - clip.time_offset)) if clip.local_time else 0.
            pitch = float(NoteSource(project, offset).frequency_at(np.array([pose]))[0])
            return settings, pose, pitch
        settings = replace(machine, motion_time_offset=motion)
        pitch = 0. if settings.source == MIDI_SOURCE else float(source_frequency(np.array([pose]), settings)[0])
        return settings, pose, pitch

    def use_source_pitch(self, update=True):
        if self.frozen is None: return
        try: pitch = self.context()[2]
        except (ValueError, IndexError): pitch = 0.
        if not (ci.PITCH_RANGE[0] <= pitch <= ci.PITCH_RANGE[1]): pitch = self.pitch.value() or DEFAULT_PITCH
        blocker = QSignalBlocker(self.pitch); self.pitch.setValue(pitch); del blocker
        if update: self.window.host.scope.update()

    def current_key(self):
        """(documents compared by identity, values compared by value) the cycle depends on."""
        w = self.window
        document = w.output_project() if self.frozen[0] == COMPOSER else w.scene.settings
        prefs = tuple(sorted((k, v) for k, v in w.signal_preferences.items() if isinstance(v, (int, float, str, bool))))
        return ((document, w.scene.settings),
                (self.frozen, frozenset(w.solo_ids), tuple(sorted(w.transform_solos.items())), prefs,
                 w.audio.sample_rate, self.pitch.value()))

    def refresh(self):
        """Inspect again when anything the cycle depends on changed (bounded: once per change)."""
        if not self.active or self.frozen is None: return
        w = self.window
        if self.frozen[0] != w.workspace or (self.frozen[0] == COMPOSER and self.frozen[1] >= len(w.project.clips)):
            self.set_active(False); return
        if not self.available():
            message = 'Inspect shows the generated scene: choose Generated signal as the input, and a listening mode that draws it.'
            if self.inspection is not None or self.error != message:
                self.inspection = None; self.key = None; self.error = message; self.refresh_controls()
            return
        key = self.current_key()
        if (self.key is not None and all(a is b for a, b in zip(key[0], self.key[0])) and key[1] == self.key[1]):
            return
        self.key = key
        try:
            settings, pose, _ = self.context()
            self.inspection = ci.inspect_cycle(settings, pose, self.pitch.value()); self.error = ''
        except (ValueError, IndexError) as error:
            self.inspection = None; self.error = str(error)
        self.refresh_controls()

    def layers(self, w, h, scale, palette):
        """scope.overlays entries for this frame: none while idle (keeps the base layers' indices)."""
        self.refresh()
        if self.inspection is None and (self.reference is None or not self.reference_box.isChecked()): return []
        key = (self.inspection, self.reference, self.index, w, h, scale, palette['accent'],
               palette.get('scope_fg', palette['fg']), palette.get('scope_muted', palette['muted']),
               self.points_box.isChecked(), self.reference_box.isChecked())
        if self.geometry_key is None or any(a is not b and a != b for a, b in zip(key, self.geometry_key)):
            self.geometry = overlay_layers(self.inspection, self.reference, self.index, w, h, scale, palette,
                                           self.points_box.isChecked(), self.reference_box.isChecked())
            self.geometry_key = key
        return self.geometry

    # Scrubbing --------------------------------------------------------------------------
    def scrub(self, value):
        if self.inspection is None: return
        self.index = int(np.clip(value, 0, self.inspection.count - 1))
        for widget in (self.slider, self.sample_box):
            if widget.value() != self.index:
                blocker = QSignalBlocker(widget); widget.setValue(self.index)
        self.refresh_info(); self.window.host.scope.update()

    def next_visit(self):
        if self.inspection is None: return
        found = ci.next_visit(self.inspection, self.index)
        if found is not None: self.scrub(found)

    def jump_to_row(self, row):
        item = self.table.item(row, 0)
        shape = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if self.inspection is None or shape is None: return
        found = ci.first_sample_of(self.inspection, shape)
        if found is not None: self.scrub(found)

    # Reference --------------------------------------------------------------------------
    def pin(self):
        if self.inspection is None: return
        self.reference = self.inspection; self._reference_pose = self.frozen
        r = self.reference
        self.reference_label = f'Pinned: pose {r.pose_seconds:.3f} s, {r.frequency:g} Hz, {r.count} samples.'
        self.refresh_controls(); self.window.host.scope.update()

    def clear_reference(self):
        self.reference = None; self._reference_pose = None; self.reference_label = ''
        self.refresh_controls(); self.window.host.scope.update()

    # Readouts ---------------------------------------------------------------------------
    def name(self, index):
        shapes = self.window.scene.settings.shapes
        return f'{index + 1} · {self.label(shapes[index])}' if 0 <= index < len(shapes) else f'Object {index + 1}'

    def refresh_controls(self):
        i = self.inspection
        self.inspect_button.setChecked(self.active)
        for widget in (self.freeze_button, self.source_button, self.pitch, self.slider, self.sample_box,
                       self.points_box): widget.setEnabled(self.active)
        self.pin_button.setEnabled(i is not None)
        self.clear_button.setEnabled(self.reference is not None); self.reference_box.setEnabled(self.reference is not None)
        if not self.active: self.pose_label.setText('Not inspecting. Press Inspect to freeze the pose.')
        elif self.error: self.pose_label.setText(self.error)
        elif i is not None:
            where = ' (clip time)' if self.frozen[0] == COMPOSER else ''
            self.pose_label.setText(f'Pose {i.pose_seconds:.3f} s{where} · frame {i.frame} · {i.sample_rate / 1000:g} kHz')
        count = i.count if i is not None else 1
        for widget in (self.slider, self.sample_box):
            blocker = QSignalBlocker(widget); widget.setRange(0, count - 1)
        self.index = min(self.index, count - 1)
        for widget in (self.slider, self.sample_box):
            blocker = QSignalBlocker(widget); widget.setValue(self.index)
        self.count_label.setText(f'of {count}' if i is not None else '')
        self.refresh_table(); self.refresh_info(); self.refresh_reference()

    def refresh_info(self):
        i = self.inspection
        if i is None:
            self.info.setText(''); self.next_button.setEnabled(False); return
        if i.empty:
            self.info.setText('Nothing is drawn at this pose: the beam rests at the centre.')
            self.next_button.setEnabled(False); return
        s = ci.sample_info(i, self.index)
        head = f'{s.seconds * 1000:.3f} ms · phase {s.phase * 100:.1f}%'
        if s.held: what = f'Holding at a corner of {self.name(s.shape)} (corner dwell).'
        elif s.travel and s.shape != s.target: what = f'Travel: {self.name(s.shape)} → {self.name(s.target)}'
        elif s.travel: what = f'Travel inside {self.name(s.shape)} (between strokes).'
        else: what = f'{self.name(s.shape)} · drawing'
        visit = f' · visit {s.visit} of {s.visits}' if s.visits > 1 and not s.held else ''
        self.info.setText(f'{head}\n{what}{visit}')
        self.next_button.setEnabled(s.visits > 1)

    def refresh_table(self):
        i = self.inspection
        rows = []
        if i is not None:
            for share in i.shares:
                rows.append((self.name(share.shape), f'{share.share * 100:.1f}%', str(share.samples), share.shape, ''))
            if i.travel_share > 0 or i.travel_samples or len(i.shares) > 1 or i.travel.any():
                rows.append(('Travel', f'{i.travel_share * 100:.1f}%', str(i.travel_samples), None, ''))
            if i.dwell_share > 0:
                rows.append(('Corner dwell', f'{i.dwell_share * 100:.1f}%', str(i.dwell_samples), None, ''))
            for index, reason in i.absent:
                rows.append((self.name(index), '—', '0', None, f'Not drawn: {ci.REASONS.get(reason, reason)}.'))
        self.table.setRowCount(len(rows))
        for row, (name, share, samples, shape, tip) in enumerate(rows):
            for column, text in enumerate((name, share, samples)):
                item = QTableWidgetItem(text)
                if column: item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                item.setToolTip(tip or text)
                if column == 0 and shape is not None: item.setData(Qt.ItemDataRole.UserRole, shape)
                self.table.setItem(row, column, item)
        self.table.resizeRowsToContents()
        height = self.table.horizontalHeader().height() + 2 * self.table.frameWidth()
        height += sum(self.table.rowHeight(row) for row in range(len(rows)))
        self.table.setFixedHeight(height)
        self.table.setVisible(bool(rows))

    def refresh_reference(self):
        if self.reference is None:
            self.reference_info.setText('No reference pinned.'); return
        text = self.reference_label
        difference = ci.difference(self.reference, self.inspection)
        if difference is not None:
            text += f'\nThis cycle differs by up to {difference.max:.4f} (RMS {difference.rms:.4f}).'
        elif self.inspection is not None:
            text += '\nDifferent pitch, rate or sample count: compare the outlines.'
        self.reference_info.setText(text)
