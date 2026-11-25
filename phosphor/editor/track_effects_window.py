"""Library › Effects edits one Composer track through the timeline's undoable track operations."""
from dataclasses import replace

from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu, QPushButton, QSlider, QToolButton,
                               QScrollArea, QSizePolicy, QStyle, QStyleOptionSpinBox, QVBoxLayout, QWidget)

from phosphor.engine import audio_tracks
from phosphor.engine.composition import COMPOSER
from phosphor.editor.panel_widgets import FlowRow
from phosphor.engine.track_effects import MAX_EFFECTS
from phosphor.engine.track_effects import (EFFECTS, effect_lane, effect_value, from_lane, new_effect, to_lane,
                                           with_lane, with_value)

SLIDER_STEPS = 1000


def number_fields(kind):
    return [f for f in EFFECTS[kind].fields if f.choices is None]


def value_text(field, value):
    if field.choices is not None: return str(value)
    if field.unit == 'Hz' and value >= 1000: return f'{value / 1000:.2f} kHz'
    digits = 0 if abs(value) >= 100 else 1 if abs(value) >= 10 else 2
    return f'{value:.{digits}f}' + (f' {field.unit}' if field.unit else '')


def effect_summary(effect):
    """'Filter · low-pass 1.00 kHz' for the chain list."""
    spec = EFFECTS[effect.kind]
    parts = []
    for field in spec.fields:
        value = effect_value(effect, field.name)
        parts.append(value if field.choices is not None else value_text(field, value) + ('~' if effect_lane(effect, field.name) else ''))
        if len(parts) == 2: break
    return f'{spec.label} · ' + ' '.join(parts)


class ParameterSlider(QSlider):
    """FlowRow starts the slider at a usable minimum and gives it the spare width."""
    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(48)
        return hint


class ValueBox(QDoubleSpinBox):
    """A spin box as wide as its widest value needs. Some base styles (Windows 11) reserve room for
    their own side-by-side arrows on top of the style sheet's buttons, which made a value box and
    Lane wider than the Library's narrowest width; the text field here still fits every value."""
    def sizeHint(self):
        hint = super().sizeHint()
        self.ensurePolished()
        option = QStyleOptionSpinBox(); self.initStyleOption(option)
        option.rect = QRect(0, 0, max(hint.width(), 200), hint.height())
        field = self.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option,
                                            QStyle.SubControl.SC_SpinBoxEditField, self)
        if not field.isValid(): return hint
        metrics = self.fontMetrics()
        fixed = self.prefix() + self.suffix()
        texts = [self.textFromValue(self.minimum()), self.textFromValue(self.maximum()), self.specialValueText()]
        text = max(metrics.horizontalAdvance(t + fixed) for t in texts if t) + 2  # As Qt: a cursor's room.
        edit = self.lineEdit(); margins = edit.textMargins()
        frame = edit.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth, None, edit) if edit.hasFrame() else 0
        inner = margins.left() + margins.right() + 2 * (frame + 2)  # QLineEdit's own 2 px text margin a side.
        need = text + inner + option.rect.width() - field.width()
        hint.setWidth(min(hint.width(), need))
        return hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(min(hint.width(), self.sizeHint().width()))
        return hint


class LaneButton(QToolButton):
    """As wide for 'Lane' as for 'Lane ~', so a lane gaining nodes never moves or rewraps its row."""
    WIDEST = 'Lane ~'

    def sizeHint(self):
        hint = super().sizeHint()
        metrics = self.fontMetrics()
        hint.setWidth(hint.width() + max(0, metrics.horizontalAdvance(self.WIDEST) - metrics.horizontalAdvance(self.text())))
        return hint

    def minimumSizeHint(self):
        return self.sizeHint()


class ChainList(QListWidget):
    """The chain sized to its rows: at least MIN_ROWS tall, at most MAX_ROWS, scrolling beyond."""
    MIN_ROWS, MAX_ROWS = 3, 6

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        for signal in (self.model().rowsInserted, self.model().rowsRemoved, self.model().modelReset):
            signal.connect(self.fit_rows)
        self.fit_rows()

    def row_height(self):
        if self.count(): return max(self.sizeHintForRow(0), 1)
        return self.fontMetrics().height() + 8  # No row to measure yet: about one text line.

    def fit_rows(self, *args):
        self.ensurePolished()
        rows = min(max(self.count(), self.MIN_ROWS), self.MAX_ROWS)
        margins = self.contentsMargins()
        self.setFixedHeight(rows * (self.row_height() + 2 * self.spacing()) + margins.top() + margins.bottom())

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange, QEvent.Type.ContentsRectChange):
            self.fit_rows()


class ParameterRow(QWidget):
    """A slider (lane-mapped, so log fields feel even), a spin box and the parameter's lane button."""
    changed = Signal(str, object)
    lane_requested = Signal(str)

    def __init__(self, field):
        super().__init__()
        self.field = field
        # One line while it fits (the slider takes the spare width and shrinks first); narrower, the
        # value box and Lane wrap onto a second line. The unit is in the label, not a box suffix, so
        # the box and Lane fit one line at the Library's narrowest width.
        layout = FlowRow(self)
        self.label = QLabel(f'{field.label} ({field.unit})' if field.unit else field.label)
        layout.addWidget(self.label)
        self.slider = ParameterSlider(Qt.Orientation.Horizontal); self.slider.setRange(0, SLIDER_STEPS)
        self.spin = ValueBox(); self.spin.setRange(field.low, field.high)
        span = field.high - field.low
        self.spin.setDecimals(0 if span >= 1000 else 1 if span >= 20 else 2 if span >= 1 else 3)
        self.spin.setAccessibleName(self.label.text())
        self.spin.setKeyboardTracking(False)
        self.lane = LaneButton(); self.lane.setText('Lane'); self.lane.setCheckable(True)
        for widget in (self.label, self.spin, self.lane):
            widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.lane.setToolTip(f'Show the {field.label.lower()} lane under this track in the roll. While the lane has nodes it sets the value (~).')
        group = QWidget()
        pair = QHBoxLayout(group); pair.setContentsMargins(0, 0, 0, 0); pair.setSpacing(layout.spacing())
        pair.addWidget(self.spin); pair.addWidget(self.lane)
        group.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.slider, 1); layout.addWidget(group)
        self.spin.valueChanged.connect(lambda value: self.changed.emit(field.name, float(value)))
        self.lane.clicked.connect(lambda checked=False: self.lane_requested.emit(field.name))

    def show_value(self, value, automated, shown):
        for widget in (self.slider, self.spin): widget.blockSignals(True)
        self.slider.setValue(round(to_lane(self.field, value) * SLIDER_STEPS)); self.spin.setValue(value)
        for widget in (self.slider, self.spin): widget.blockSignals(False)
        self.slider.setEnabled(not automated); self.spin.setEnabled(not automated)
        tip = f'{self.field.label}: {value_text(self.field, value)}'
        if automated: tip += ' — set by its lane (~); remove the lane\'s nodes to edit it here.'
        self.slider.setToolTip(tip); self.spin.setToolTip(tip)
        self.lane.setText('Lane ~' if automated else 'Lane'); self.lane.setChecked(shown)


class TrackEffectsPanel(QWidget):
    def __init__(self, owner, view):
        super().__init__()
        self.owner = owner
        self.view, self.track, self.selected_track = view, -1, None
        self.gesture = None
        self.applying = False
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.pending_row = None
        self.refresh_timer.timeout.connect(self.refresh)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        content = QWidget()
        body = QVBoxLayout(content); body.setContentsMargins(4, 4, 4, 4)
        self.message = QLabel(); self.message.setWordWrap(True); body.addWidget(self.message)
        self.add_track = QPushButton('Add audio track'); body.addWidget(self.add_track)
        self.add_track.clicked.connect(self.add_audio_track)
        # The list, its buttons and the parameters sit at the top; spare height goes below them.
        self.editor = QWidget(); body.addWidget(self.editor)
        layout = QVBoxLayout(self.editor); layout.setContentsMargins(0, 0, 0, 0)
        top = FlowRow(); layout.addLayout(top)  # Bypass all wraps under the track box in a narrow panel.
        self.track_box = QComboBox(); self.track_box.setAccessibleName('Audio track')
        self.track_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.track_box.setMinimumContentsLength(1)
        self.track_box.currentIndexChanged.connect(self.set_track); top.addWidget(self.track_box)
        self.bypass = QCheckBox('Bypass all'); top.addWidget(self.bypass)
        self.bypass.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.bypass.setToolTip('Play this track without any of its effects (the chain is kept).')
        self.bypass.toggled.connect(lambda on: self.set_effects(bypass=on))
        self.chain = ChainList(); layout.addWidget(self.chain)
        self.chain.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chain.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.chain.setToolTip('The effects in order, top first, after pan and phase. Drag to reorder; untick to bypass one.')
        # The list's own drag and check signals apply their edit at once; the list is rebuilt
        # after the signal returns (rebuilding inside it is unsafe).
        self.chain.model().rowsMoved.connect(lambda parent, start, end, destination, row: self.rows_moved(start, row))
        self.chain.itemChanged.connect(lambda item: self.set_bypass(self.chain.row(item), item.checkState() != Qt.CheckState.Checked))
        self.chain.currentRowChanged.connect(self.chain_selection_changed)
        buttons = FlowRow(); layout.addLayout(buttons)
        self.add = QPushButton('Add'); add_menu = QMenu(self.add); self.add.setMenu(add_menu); self.add_actions = {}
        self.add.setProperty('menuButton', True)
        for kind, spec in EFFECTS.items():
            action = add_menu.addAction(spec.label); self.add_actions[kind] = action
            action.triggered.connect(lambda checked=False, kind=kind: self.add_effect(kind))
        self.remove = QPushButton('Remove'); self.remove.clicked.connect(self.remove_effect)
        self.up = QPushButton('Up'); self.up.clicked.connect(lambda: self.move_effect(-1))
        self.down = QPushButton('Down'); self.down.clicked.connect(lambda: self.move_effect(1))
        for button in (self.add, self.remove, self.up, self.down): buttons.addWidget(button)
        self.form_host = QWidget(); layout.addWidget(self.form_host)
        # A plain column, not a QFormLayout: the wrapping rows need their height for the panel's width.
        self.form = QVBoxLayout(self.form_host); self.form.setContentsMargins(0, 4, 0, 0)
        self.rows, self.choices, self.shown_kind = {}, {}, None
        body.addStretch()
        self.scroll.setWidget(content)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def hideEvent(self, event):
        self.finish_gesture()
        super().hideEvent(event)

    # The model ------------------------------------------------------------------------
    def add_audio_track(self):
        if self.view.add_track():
            self.set_track(self.view.track_anchor)

    def track_data(self):
        """The selection has already been mapped to the current project."""
        project = self.view.project
        if self.owner.workspace != COMPOSER or project is None: return None
        if 0 <= self.track < len(project.audio_tracks) and project.audio_tracks[self.track] is self.selected_track:
            return self.selected_track

    def project_changing(self, old, new, restoring):
        before, after = old.audio_tracks, new.audio_tracks
        if before is after: return
        if not self.applying: self.finish_gesture()
        previous = self.selected_track
        if previous is None: return
        matches = [i for i, track in enumerate(after) if track is previous]
        timeline_edit = new is getattr(self.view, '_emitted', None) and not restoring
        if not matches and self.track >= 0 and timeline_edit and len(before) == len(after):
            # In-place header edits replace frozen tracks while retaining their positions.
            if all(after[i] is before[i] for i in range(len(after)) if i != self.track):
                matches = [self.track]
        if not matches:
            named = [i for i, track in enumerate(after) if track.name == previous.name]
            # Deleting one of two names must not redirect the editor to the surviving sibling.
            if restoring or (len(named) == 1 and not any(after[named[0]] is t for t in before)):
                matches = named
        self.track = matches[0] if matches else -1
        if matches: self.selected_track = after[self.track]

    def set_track(self, t):
        self.finish_gesture()
        project = self.view.project
        self.track = t
        self.selected_track = project.audio_tracks[t] if project is not None and 0 <= t < len(project.audio_tracks) else None
        self.refresh(select=0)

    def set_effects(self, effects=None, bypass=None, select=None, order=None, later=False, dragging=False):
        """Apply a chain edit as one Undo step. order[new] = old effect index (None: added) lets
        the track's effect lane follow its effect; later defers the list rebuild."""
        track = self.track_data()
        if track is None: return False
        effects = track.effects if effects is None else tuple(effects)
        bypass = track.effects_bypass if bypass is None else bool(bypass)
        if effects == track.effects and bypass == track.effects_bypass:
            if later:
                if order is not None: self.view.remap_effect_lane(self.track, list(order))
                self.pending_row = (track, track.effects, select)
                self.refresh_timer.start(0)
            return False
        self.applying = True
        try:
            if not dragging:
                self.finish_gesture()
                self.commit()
            if order is not None: self.view.remap_effect_lane(self.track, list(order))
            ok = self.view.apply_tracks(audio_tracks.set_effects, self.track, effects, bypass)
            if not dragging: self.commit()
        finally:
            self.applying = False
        if later:
            self.pending_row = (self.track_data(), effects, select)
            self.refresh_timer.start(0)
        else: self.refresh(select)
        return ok

    def begin_gesture(self, row):
        self.finish_gesture()
        self.commit()
        e = self.current()
        if e is not None:
            track = self.track_data()
            self.gesture = (row, track, e, track.effects[e])

    def finish_gesture(self):
        if self.gesture is not None:
            self.gesture = None
            self.commit()

    def slider_changed(self, row, step):
        if self.rows.get(row.field.name) is not row: return
        dragging = row.slider.isSliderDown()
        if dragging:
            track, e = self.track_data(), self.current()
            if self.gesture is None:
                self.show_parameters()
                return
            source, start_track, index, effect = self.gesture
            if source is not row or track is not start_track or index != e or track.effects[e] is not effect:
                self.show_parameters()
                return
        self.set_parameter(row.field.name, from_lane(row.field, step / SLIDER_STEPS), dragging)
        if dragging and self.gesture is not None:
            track = self.track_data()
            self.gesture = (row, track, e, track.effects[e])

    def commit(self):
        self.owner.commit_history(force=True)

    def current(self):
        track, row = self.track_data(), self.chain.currentRow()
        return row if track is not None and 0 <= row < len(track.effects) else None

    def add_effect(self, kind):
        track = self.track_data()
        if track is None: return
        at = self.current(); at = len(track.effects) if at is None else at + 1
        effects = list(track.effects); effects.insert(at, new_effect(kind))
        order = list(range(len(track.effects))); order.insert(at, None)
        self.set_effects(effects, select=at, order=order)

    def remove_effect(self):
        e = self.current()
        if e is None: return
        effects = list(self.track_data().effects); del effects[e]
        order = [k for k in range(len(effects) + 1) if k != e]
        self.set_effects(effects, select=min(e, len(effects) - 1), order=order)

    def move_effect(self, step):
        e = self.current()
        if e is None: return
        effects = list(self.track_data().effects); to = e + step
        if not 0 <= to < len(effects): return
        effects[e], effects[to] = effects[to], effects[e]
        order = list(range(len(effects))); order[e], order[to] = order[to], order[e]
        self.set_effects(effects, select=to, order=order)

    def rows_moved(self, start, row):
        track = self.track_data()
        if track is None or not 0 <= start < len(track.effects): return
        to = min(row if row < start else row - 1, len(track.effects) - 1)
        if to == start: return
        effects = list(track.effects); moved = effects.pop(start); effects.insert(to, moved)
        order = list(range(len(effects))); order.insert(to, order.pop(start))
        self.set_effects(effects, select=to, order=order, later=True)

    def set_bypass(self, row, bypass):
        track = self.track_data()
        if track is None or not 0 <= row < len(track.effects) or track.effects[row].bypass == bypass: return
        effects = list(track.effects); effects[row] = replace(effects[row], bypass=bool(bypass))
        self.set_effects(effects, select=row, later=True)

    def set_parameter(self, name, value, dragging=False):
        e = self.current()
        if e is None: return
        effects = list(self.track_data().effects)
        try: effects[e] = with_value(effects[e], name, value)
        except ValueError as error: self.view.notice.emit(str(error)); return
        self.set_effects(effects, select=e, dragging=dragging)

    def show_lane(self, name):
        e = self.current()
        if e is not None: self.view.show_effect_lane(self.track, e, name)

    def update_lane_buttons(self):
        if not self.isVisible() or self.applying: return
        e = self.current()
        shown = self.view.lane_for(self.track) if e is not None and self.view.roll_audio_gain_lane else None
        for name, row in self.rows.items():
            row.lane.setChecked(shown == ('fx', e, name))

    # The view -------------------------------------------------------------------------
    def refresh(self, select=None):
        if self.applying: return
        self.refresh_timer.stop()
        if self.pending_row is not None:
            target, chain, requested = self.pending_row
            if select is None and target is self.track_data() and target is not None and target.effects is chain:
                select = requested
            self.pending_row = None
        composer = self.owner.workspace == COMPOSER
        tracks = self.view.project.audio_tracks if composer and self.view.project is not None else ()
        names = [t.name for t in tracks]
        self.editor.setVisible(bool(tracks))
        self.message.setVisible(not tracks); self.add_track.setVisible(composer and not tracks)
        self.message.setText('Add an audio track to edit its effects.' if composer else
                             "Effects live on Composer's audio tracks.")
        if names and self.selected_track is None:
            anchor = self.view.track_anchor
            self.track = anchor if anchor is not None and 0 <= anchor < len(names) else 0
            self.selected_track = tracks[self.track]
        track = self.track_data()
        self.track_box.blockSignals(True)
        if names != [self.track_box.itemText(i) for i in range(self.track_box.count())]:
            self.track_box.clear(); self.track_box.addItems(names)
        self.track_box.setPlaceholderText('Choose a track')
        self.track_box.setCurrentIndex(self.track if track is not None and names else -1)
        self.track_box.blockSignals(False)
        row = self.chain.currentRow() if select is None else select
        # Block the list's own signals only: blocking its model would hide the reset and inserted
        # rows from the view, which then draws an empty list until something relays it out.
        self.chain.blockSignals(True)
        self.chain.clear()
        if track is not None:
            for k, effect in enumerate(track.effects):
                item = QListWidgetItem(f'{k + 1}  {effect_summary(effect)}')
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
                item.setCheckState(Qt.CheckState.Unchecked if effect.bypass else Qt.CheckState.Checked)
                self.chain.addItem(item)
            if track.effects: self.chain.setCurrentRow(min(max(row, 0), len(track.effects) - 1))
        self.chain.blockSignals(False)
        self.bypass.blockSignals(True); self.bypass.setChecked(bool(track and track.effects_bypass)); self.bypass.blockSignals(False)
        count = len(track.effects) if track is not None else 0
        self.bypass.setEnabled(track is not None)
        self.add.setEnabled(track is not None and count < MAX_EFFECTS)
        self.add.setToolTip(f'Add an effect after the selected one (at most {MAX_EFFECTS} per track).')
        self.selection_changed()

    def chain_selection_changed(self, row):
        self.finish_gesture()
        self.selection_changed()

    def selection_changed(self):
        track, e = self.track_data(), self.current()
        count = len(track.effects) if track is not None else 0
        self.remove.setEnabled(e is not None); self.up.setEnabled(bool(e)); self.down.setEnabled(e is not None and e < count - 1)
        self.show_parameters()

    def show_parameters(self):
        e = self.current(); track = self.track_data()
        effect = track.effects[e] if e is not None else None
        kind = effect.kind if effect is not None else None
        rebuilt = kind != self.shown_kind
        if rebuilt:
            while self.form.count():
                widget = self.form.takeAt(0).widget()
                if widget is not None: widget.hide(); widget.deleteLater()
            self.rows, self.choices, self.shown_kind = {}, {}, kind
            if kind is not None:
                for field in EFFECTS[kind].fields:
                    if field.choices is not None:
                        box = QComboBox(); box.addItems([c.capitalize() for c in field.choices])
                        box.currentIndexChanged.connect(lambda i, field=field: self.set_parameter(field.name, field.choices[i]))
                        choice = QWidget(); line = FlowRow(choice)
                        label = QLabel(field.label); label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                        line.addWidget(label); line.addWidget(box)
                        self.choices[field.name] = box; self.form.addWidget(choice)
                    else:
                        row = ParameterRow(field)
                        row.changed.connect(self.set_parameter); row.lane_requested.connect(self.show_lane)
                        row.slider.sliderPressed.connect(lambda row=row: self.begin_gesture(row))
                        row.slider.valueChanged.connect(lambda step, row=row: self.slider_changed(row, step))
                        row.slider.sliderReleased.connect(lambda row=row: self.end_slider(row))
                        self.rows[field.name] = row; self.form.addWidget(row)
        if effect is None: return
        shown = self.view.lane_for(self.track) if self.view.roll_audio_gain_lane else None
        for name, box in self.choices.items():
            box.blockSignals(True); box.setCurrentIndex(EFFECTS[kind].fields[[f.name for f in EFFECTS[kind].fields].index(name)].choices.index(effect_value(effect, name)))
            box.blockSignals(False)
        for name, row in self.rows.items():
            row.show_value(effect_value(effect, name), bool(effect_lane(effect, name)), shown == ('fx', e, name))
        if rebuilt: self.fit_page()

    def fit_page(self):
        """Size the page for new rows now. The scroll area would otherwise resize the page a few
        posted layout requests later, and until then the wrapped rows are squeezed into the
        previous effect's height (overlapping controls for a moment)."""
        if not self.isVisible(): return
        for index in range(self.form.count()):  # A layout shows new widgets later (a queued call).
            widget = self.form.itemAt(index).widget()
            if widget is not None: widget.show()
        # Polishing the new rows queued layout requests (their value boxes' widths); run them now, so
        # each row is placed and knows its wrapped height.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        self.scroll.widget().layout().activate()
        self.scroll.setWidgetResizable(True)  # Re-fits the page to the rows' height for the viewport's width.

    def end_slider(self, row):
        if self.gesture is not None and self.gesture[0] is row:
            self.finish_gesture()
