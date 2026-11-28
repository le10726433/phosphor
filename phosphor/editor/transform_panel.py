"""Qt controls for an object's transform stack, shown in SCENE › Transform.

Laid out like SCENE › Objects: a list of steps (power = on/off, top runs first),
Add transform / Duplicate / Remove / Up / Down, and the selected step's fields below.
Every number field has a right-click menu: Static (default) or Time varying…, which
swaps the slider for an expression of x, y, z, t and s (engine/transforms.py), and, in
Composer, Automate / Remove automation, which adds or removes the number's gain lane
(engine/automation.py) in the timeline's transform roll. Edits keep the lane.
Map's X′, Y′ and Z′ are formula fields: always an expression, with no slider or menu.
Variables (T5) sit at the top: the object's own numbers, each a slider (or Time varying, or
automated in Composer), that every step, Path Start and Trace read by name. Renaming one
rewrites the expressions that read it.
Saved transforms (T6): right-click a step › Save step… or Save stack…, and Add › Saved inserts a
copy (a step below the selection; a stack appended or replacing the stack) in one Undo step;
Add › Saved › Rename…/Delete… manage the user's file (storage/transform_presets.py).
"""
from dataclasses import replace
import math

from PySide6.QtCore import Qt, Signal, QSignalBlocker, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox,
                               QListWidget, QListWidgetItem, QMenu, QCheckBox, QSpinBox, QDoubleSpinBox, QSizePolicy,
                               QInputDialog, QMessageBox, QToolButton)

from phosphor.engine.solids import LOW_POLY_SOLIDS
from phosphor.engine.transforms import (KINDS, TRANSFORM_GROUPS, DEFORM_NEEDS_SUBDIVIDE, SUBDIVIDE, FIELDS, HELP,
                                        MOTION_PATH, MOTION_PRESETS, MAX_TRANSFORMS,
                                        Param, Field, Transform, new_transform, with_motion_preset, validate_stack, compiled_expression,
                                        field_label, NOISE, COLOURS, RATES, NoiseOptions, curve_of,
                                        MAP, MAP_PRESETS, with_map_preset, is_identity, Variable, MAX_OBJECT_VARIABLES,
                                        TRANSFORM_RESERVED, VARIABLE_EXPRESSION_HELP, check_variable_name,
                                        validate_object_variables, new_variable_name, variable_names, renamed_variable,
                                        validate_path)
from phosphor.engine.matrix_rain import validate_matrix
from phosphor.engine.transform_presets import STEP, STACK, APPEND, REPLACE, BUILTIN_RECIPES, make_preset, inserted
from phosphor.editor.noise_editor import SpectrumPlot
from phosphor.storage.noise_presets import read_noise_presets, save_noise_preset, delete_noise_preset
from phosphor.storage.transform_presets import (read_transform_presets, save_transform_preset,
                                                rename_transform_preset, delete_transform_preset)
from phosphor.editor.panel_widgets import InlineCaption, FlowRow

EXPRESSION_HELP = ('Time varying: x, y, z = the point before this step · t = seconds · p = beats (t × tempo / 60) · s = 0–1 along the object '
                   '· the object\'s Variables by name.\n'
                   'Uses the parametric-equation functions (sin, cos, tri, clip…). Press Enter to apply; '
                   'right-click › Static to go back.')
PRESET_PROMPT = 'Load a path…'
MAP_PRESET_PROMPT = 'Load an example…'
VARIABLES_HELP = ("The object's own numbers: every step's expressions, Path Start and Trace can use them by name. "
                  'Right-click a value for Time varying… (or Automate, in Composer). Renaming one rewrites the expressions that use it.')
FORMULA_HELP = ('New coordinate: x, y, z = the point before this step (every formula sees the same point) · '
                't = seconds · p = beats · s = 0–1 along the object · the object\'s Variables.\n'
                'Uses the parametric-equation functions (sin, cos, sqrt, hypot…). NaN or infinity gives 0; results are '
                'clipped to −10…10. Press Enter to apply; clear the field to restore the axis.')
AUTOMATED = '◆ '  # Caption mark of a number with a gain lane.
AUTOMATE_ONLY_IN_COMPOSER = 'Automate (in Composer)'
SAVED_OFF = 'Saved transforms are off in this session'  # No user files (harnesses, --seconds…).
INSERT_LABELS = {STEP: 'Insert saved step', APPEND: 'Insert saved stack', REPLACE: 'Replace with saved stack'}


def switch_action(menu, automated, allowed):
    """Add Automate On/Off (or Remove On/Off automation) to a row's menu; returns the action."""
    if automated: return menu.addAction('Remove On/Off automation')
    action = menu.addAction('Automate On/Off' if allowed else 'Automate On/Off (in Composer)')
    action.setEnabled(allowed)
    action.setToolTip('Add an On/Off lane in the transform roll of the timeline: paint where this is on or off.')
    return action


def automation_actions(menu, automated, allowed):
    """Add Automate / Remove automation to a field's menu; returns (automate, remove) actions."""
    menu.addSeparator()
    automate = menu.addAction('Automate' if allowed else AUTOMATE_ONLY_IN_COMPOSER)
    automate.setCheckable(True); automate.setChecked(automated); automate.setEnabled(allowed and not automated)
    automate.setToolTip('Add a gain lane for this number in the transform roll of the timeline: it multiplies the value over the clip.')
    remove = menu.addAction('Remove automation'); remove.setEnabled(automated)
    return automate, remove


def _number(value):
    return f'{value:.6g}'


class ParamField(QWidget):
    """One number of a transform: a slider (static), a checkbox, a whole number, or an expression."""
    changed = Signal(object)  # Param
    automate = Signal(bool)  # Automate (True) or Remove automation (False) from the menu.

    def __init__(self, slider_class, field, help=None, placeholder='f(x, y, z, t, s)', caption_width=92):
        super().__init__()
        self.field, self.param = field, Param(field.default)
        self._automation_allowed = False  # Composer only: Sandbox has no timeline.
        self.fx_button = self.auto_button = None
        row = QHBoxLayout(self); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(3)
        self.caption = InlineCaption(field.label, caption_width)
        row.addWidget(self.caption)
        if field.toggle:
            self.check = QCheckBox(); self.check.toggled.connect(lambda on: self.changed.emit(Param(1. if on else 0.)))
            row.addWidget(self.check, 1); self.slider = self.expression = None; return
        if field.formula:
            self.slider = None
            self.expression = QLineEdit(); self.expression.setFixedHeight(28); self.expression.setToolTip(help or FORMULA_HELP)
            self.expression.setPlaceholderText(field.name); self.expression.editingFinished.connect(self.expression_edited)
            self.caption.setToolTip(FORMULA_HELP); row.addWidget(self.expression, 1); return
        if field.integer:
            self.spin = QSpinBox(); self.spin.setRange(int(field.low), int(field.high)); self.spin.setKeyboardTracking(False)
            self.spin.valueChanged.connect(lambda value: self.changed.emit(Param(float(value))))
            row.addWidget(self.spin, 1); self.slider = self.expression = None; return
        self.slider = slider_class(field.default, field.low, field.high, curve=field.curve); self.slider.setFixedHeight(28)
        self.slider.edited.connect(lambda value: self.changed.emit(replace(self.param, value=float(value))))
        self.expression = QLineEdit(); self.expression.setFixedHeight(28); self.expression.setToolTip(help or EXPRESSION_HELP)
        self.expression.setPlaceholderText(placeholder)
        self.expression.editingFinished.connect(self.expression_edited)
        row.addWidget(self.slider, 1); row.addWidget(self.expression, 1); self.expression.hide()
        # Compact inline Formula (ƒ) & Automation (◆) micro-pills (also accessible via right-click).
        self.fx_button = QToolButton(self); self.fx_button.setObjectName('param_fx_btn')
        self.fx_button.setText('ƒ'); self.fx_button.setCheckable(True); self.fx_button.setFixedSize(22, 24)
        self.fx_button.setToolTip('Toggle Time-varying formula expression (or right-click)')
        self.fx_button.clicked.connect(self._toggle_fx)
        row.addWidget(self.fx_button)
        self.auto_button = QToolButton(self); self.auto_button.setObjectName('param_auto_btn')
        self.auto_button.setText('◆'); self.auto_button.setCheckable(True); self.auto_button.setFixedSize(22, 24)
        self.auto_button.setToolTip('Toggle Composer automation gain lane (or right-click)')
        self.auto_button.clicked.connect(self._toggle_auto)
        self.auto_button.hide()
        row.addWidget(self.auto_button)
        for widget in (self, self.caption, self.slider, self.slider.slider, self.slider.entry, self.expression):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(lambda point, widget=widget: self.menu(widget.mapToGlobal(point)))

    @property
    def automation_allowed(self):
        return self._automation_allowed

    @automation_allowed.setter
    def automation_allowed(self, allowed):
        self._automation_allowed = bool(allowed)
        if self.auto_button is not None:
            self.auto_button.setVisible(self._automation_allowed)

    def _toggle_fx(self, checked):
        if checked and not self.varying:
            self.make_varying()
        elif not checked and self.varying:
            self.changed.emit(replace(self.param, expression=''))

    def _toggle_auto(self, checked):
        if self.auto_button is not None:
            blocker = QSignalBlocker(self.auto_button); self.auto_button.setChecked(self.automated)
        if self._automation_allowed:
            self.automate.emit(not self.automated)

    @property
    def varying(self): return bool(self.param.expression)

    @property
    def automated(self): return bool(self.param.points)

    def set_param(self, param):
        self.param = param
        if self.field.toggle:
            blocker = QSignalBlocker(self.check); self.check.setChecked(bool(param.value)); return
        if self.field.integer:
            blocker = QSignalBlocker(self.spin); self.spin.setValue(int(param.value)); return
        if self.field.formula:
            if self.expression.text() != param.expression:
                blocker = QSignalBlocker(self.expression); self.expression.setText(param.expression)
            return
        self.slider.setValue(param.value)
        if self.expression.text() != param.expression:  # Unchanged text: setText costs ~0.25 ms.
            blocker = QSignalBlocker(self.expression); self.expression.setText(param.expression)
        self.slider.setVisible(not self.varying); self.expression.setVisible(self.varying)
        if self.fx_button is not None:
            blocker = QSignalBlocker(self.fx_button); self.fx_button.setChecked(self.varying)
        if self.auto_button is not None:
            blocker = QSignalBlocker(self.auto_button); self.auto_button.setChecked(self.automated)
        self.caption.setText((AUTOMATED if self.automated else '') + ('ƒ ' if self.varying else '') + self.field.label)
        tip = 'Time varying; click ƒ or right-click for Static.' if self.varying else 'Click ƒ or right-click for Time varying….'
        if self.automated: tip += '\nAutomated: a gain lane in the timeline multiplies this number.'
        self.caption.setToolTip(tip)

    def menu(self, position):
        menu = QMenu(self)
        static = menu.addAction('Static'); static.setCheckable(True); static.setChecked(not self.varying)
        varying = menu.addAction('Time varying…'); varying.setCheckable(True); varying.setChecked(self.varying)
        automate, remove = automation_actions(menu, self.automated, self.automation_allowed)
        chosen = menu.exec(position)
        if chosen is static and self.varying: self.changed.emit(replace(self.param, expression=''))
        elif chosen is varying and not self.varying: self.make_varying()
        elif chosen is automate: self.automate.emit(True)
        elif chosen is remove: self.automate.emit(False)

    def make_varying(self, text=None):
        """Start from the static value, so the object does not jump until the expression changes."""
        self.changed.emit(replace(self.param, expression=text or _number(self.param.value)))
        self.expression.setFocus(); self.expression.selectAll()

    def expression_edited(self):
        text = self.expression.text().strip()
        if self.field.formula:
            text = text or self.field.name  # Clearing a formula restores the axis (no change).
            if text != self.param.expression: self.changed.emit(replace(self.param, expression=text))
            elif self.expression.text() != text:
                blocker = QSignalBlocker(self.expression); self.expression.setText(text)
            return
        if not self.varying or text == self.param.expression: return
        if not text: self.changed.emit(replace(self.param, expression='')); return  # Clearing it means Static.
        self.changed.emit(replace(self.param, expression=text))


class _Message(QLabel):
    """A message line that takes no room while it is empty."""
    def setText(self, text):
        super().setText(text); self.setVisible(bool(text))


class TransformPanel(QWidget):
    """Edits a transform stack. Emits changed(stack) only with stacks that validate, and
    variables_changed({field: value}) with object-variable edits (T5) that validate: the new
    transform_variables, plus transforms, path_start and path_trace when a rename rewrote them."""
    changed = Signal(object)
    bypass_changed = Signal(bool)
    solo_changed = Signal(int)
    animation_changed = Signal(object)
    automate = Signal(int, str, bool)  # Step index, field name, on.
    automate_switch = Signal(int, bool)  # Step index, on: the step's On/Off lane.
    variables_changed = Signal(object)  # {Shape field: value}.
    automate_variable = Signal(str, bool)  # Object variable name, on.
    preset_inserted = Signal(object, str)  # {transforms, transform_variables}, Undo label (T6).

    def __init__(self, slider_class):
        super().__init__()
        self.stack, self.current = (), -1
        self.solo_index = None
        self._target_solid = ''
        self.automation_allowed = False  # Composer only: Sandbox has no timeline.
        self.slider_class = slider_class
        self.variables, self.taken, self.path = (), {}, (Param(0.), Param(1.))
        self.matrix = None  # A Matrix object's config: its speed expressions read the variables too.
        self.variable_rows = []
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(5)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        # Variables (T5): the object's own numbers, above the stack they feed.
        row = QHBoxLayout(); layout.addLayout(row)
        heading = QLabel('Variables'); heading.setToolTip(VARIABLES_HELP); row.addWidget(heading, 1)
        self.add_variable_button = QPushButton('+ Variable'); self.add_variable_button.setObjectName('add_transform_variable')
        self.add_variable_button.setToolTip('Add a variable to this object: ' + VARIABLES_HELP)
        self.add_variable_button.clicked.connect(lambda checked=False: self.add_variable())
        row.addWidget(self.add_variable_button)
        self.variable_box = QWidget(); self.variable_layout = QVBoxLayout(self.variable_box)
        self.variable_layout.setContentsMargins(0, 0, 0, 0); self.variable_layout.setSpacing(4)
        layout.addWidget(self.variable_box)
        self.variable_error = _Message(); self.variable_error.setObjectName('transform_variable_error')
        self.variable_error.setWordWrap(True); self.variable_error.hide(); layout.addWidget(self.variable_error)
        self.bypass_button = QPushButton('Bypass all'); self.bypass_button.setCheckable(True)
        self.bypass_button.setToolTip('A/B the whole stack without changing individual power switches')
        self.bypass_button.toggled.connect(self.bypass_changed)
        layout.addWidget(self.bypass_button)
        self.animation_box = QWidget()
        animation_layout = QVBoxLayout(self.animation_box); animation_layout.setContentsMargins(0, 0, 0, 0)
        animation_layout.addWidget(QLabel('Animation'))
        self.animation_fields = {}
        for key, label in (('speed', 'Speed'), ('beats', 'Loop (beats)'), ('mode', 'Mode'),
                           ('trim_in', 'Trim in (s)'), ('trim_out', 'Trim out (s)')):
            row = QHBoxLayout(); row.addWidget(QLabel(label))
            if key == 'mode':
                widget = QComboBox(); widget.addItems(['Loop', 'Ping-pong', 'Hold'])
                widget.currentTextChanged.connect(self.edit_animation)
            else:
                widget = QDoubleSpinBox(); widget.setDecimals(3); widget.setRange(0., 3600.)
                widget.setKeyboardTracking(False); widget.valueChanged.connect(self.edit_animation)
            widget.setObjectName(f'animation_{key}')
            row.addWidget(widget, 1); animation_layout.addLayout(row); self.animation_fields[key] = widget
        self.animation_fields['beats'].setToolTip('One loop takes this many project beats; 0 uses Speed.')
        layout.addWidget(self.animation_box); self.animation_box.hide()
        self.list = QListWidget(); self.list.setObjectName('transform_list'); self.list.setProperty('customRows', True)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setToolTip('Transforms in order, top runs first. Drag to reorder.')
        self.list.currentRowChanged.connect(self.select); self.list.itemChanged.connect(self.item_checked)
        self.list.model().rowsMoved.connect(lambda parent, start, end, dest, row: self._rows_moved(start, row))
        layout.addWidget(self.list)
        row = FlowRow(); layout.addLayout(row)  # Wraps in a narrow panel (UI2).
        self.add_button = QPushButton('Add'); self.add_button.setObjectName('add_transform'); self.add_button.setToolTip('Add a transform after the selected one')
        menu = QMenu(self.add_button)
        self.kind_actions = []
        for group_name, group_kinds in TRANSFORM_GROUPS:
            sub = menu.addMenu(group_name)
            for kind in group_kinds:
                self.kind_actions.append(sub.addAction(kind, lambda kind=kind: self.add(kind)))
        menu.addSeparator()
        self.recipes_menu = menu.addMenu('Recipes'); self.recipes_menu.setObjectName('transform_recipes_menu')
        for rname, rpreset in BUILTIN_RECIPES.items():
            sub = self.recipes_menu.addMenu(rname)
            sub.addAction('Append to stack', lambda n=rname: self.insert_recipe(n, APPEND))
            sub.addAction('Replace stack', lambda n=rname: self.insert_recipe(n, REPLACE))
        self.saved_menu = menu.addMenu('Saved'); self.saved_menu.setObjectName('saved_transforms_menu')
        self.saved_menu.aboutToShow.connect(lambda: self.build_saved_menu(self.saved_menu))
        self.add_button.setMenu(menu); self.add_button.setProperty('menuButton', True)
        row.addWidget(self.add_button, 1)
        # Saved transforms (T6). presets_path stays None until main() enables user files.
        self.presets_path, self.saved, self.saved_error = None, {}, ''
        self.last_saved_name, self.saved_submenus = '', []
        # Instance hooks, so smokes can answer the dialogs (never patch Qt classes).
        self.ask_text = lambda title, label, text='': QInputDialog.getText(self, title, label, text=text)
        self.ask_item = lambda title, label, items, current=0: QInputDialog.getItem(self, title, label, items, current, False)
        self.confirm = lambda title, text: QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes
        self.warn = lambda title, text: QMessageBox.warning(self, title, text)
        self.buttons = {}
        for key, text, tip in (('duplicate', 'Duplicate', 'Duplicate the selected transform'), ('remove', 'Remove', 'Remove the selected transform'),
                               ('up', '↑', 'Run earlier'), ('down', '↓', 'Run later')):
            button = QPushButton(text); button.setToolTip(tip); button.clicked.connect(lambda checked=False, key=key: self.action(key))
            if len(text) == 1: button.setFixedWidth(34)
            row.addWidget(button); self.buttons[key] = button
        self.help = QLabel(); self.help.setWordWrap(True); self.help.setObjectName('transform_help'); layout.addWidget(self.help)
        # Smart contextual helper: offer 1-click Subdivide above warp deformers on low-poly shapes
        self.subdivide_bar = QWidget(); self.subdivide_bar.setObjectName('subdivide_hint_bar')
        sub_row = FlowRow(); self.subdivide_bar.setLayout(sub_row)
        sub_lbl = QLabel('Low-poly edges stay straight ·'); sub_lbl.setWordWrap(True)
        self.insert_subdivide_btn = QPushButton('+ Insert Subdivide above'); self.insert_subdivide_btn.setObjectName('insert_subdivide_btn')
        self.insert_subdivide_btn.setToolTip('Insert Subdivide (4 pieces/edge) right before this deformer so straight lines bend smoothly.')
        self.insert_subdivide_btn.clicked.connect(self.insert_subdivide_above)
        sub_row.addWidget(sub_lbl); sub_row.addWidget(self.insert_subdivide_btn)
        layout.addWidget(self.subdivide_bar); self.subdivide_bar.hide()
        self.presets = QComboBox(); self.presets.setObjectName('motion_presets'); self.presets.addItem(PRESET_PROMPT)
        self.presets.addItems(list(MOTION_PRESETS)); self.presets.activated.connect(self.load_preset); layout.addWidget(self.presets)
        self.map_presets = QComboBox(); self.map_presets.setObjectName('map_presets'); self.map_presets.addItem(MAP_PRESET_PROMPT)
        self.map_presets.addItems(list(MAP_PRESETS)); self.map_presets.activated.connect(self.load_map_preset)
        self.map_presets.setToolTip('Replace the three formulas with an example (manual §7.7). Undo brings yours back.')
        layout.addWidget(self.map_presets)
        self.editors = {}
        for kind in KINDS:
            box = QWidget(); rows = QVBoxLayout(box); rows.setContentsMargins(0, 0, 0, 0); rows.setSpacing(3)
            if kind == NOISE:
                self.noise_colour = QComboBox(); self.noise_colour.setObjectName('noise_colour')
                self.noise_rate = QComboBox(); self.noise_rate.setObjectName('noise_rate'); self.noise_rate.addItems(RATES)
                for label, widget in (('Colour', self.noise_colour), ('Rate', self.noise_rate)):
                    row = QHBoxLayout(); row.addWidget(QLabel(label)); row.addWidget(widget, 1); rows.addLayout(row)
                self.noise_spectrum = SpectrumPlot(); self.noise_spectrum.setObjectName('noise_spectrum')
                rows.addWidget(self.noise_spectrum)
                self.noise_colour.activated.connect(self.colour_edited)
                self.noise_rate.currentTextChanged.connect(self.rate_edited)
                self.noise_spectrum.edited.connect(lambda curve: self.options_edited(NoiseOptions('Custom', self.stack[self.current].noise.rate, curve)))
            fields = []
            for field in FIELDS[kind]:
                widget = ParamField(slider_class, field, caption_width=106 if kind == NOISE else 92); widget.setObjectName(f'transform_{field.name}')
                widget.changed.connect(lambda param, name=field.name: self.param_edited(name, param))
                widget.automate.connect(lambda on, name=field.name: self.automate.emit(self.current, name, on))
                rows.addWidget(widget); fields.append(widget)
            layout.addWidget(box); box.hide(); self.editors[kind] = (box, fields)
        self.error = QLabel(); self.error.setObjectName('transform_error'); self.error.setWordWrap(True); layout.addWidget(self.error)
        self.noise_presets = {}; self.noise_preset_error = ''
        self.reload_noise_presets()
        self.empty = QLabel('No transforms: the object is drawn as built. Add › Rotate, Spin, Shear…')
        self.empty.setWordWrap(True); layout.addWidget(self.empty)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.refresh()

    def reload_noise_presets(self):
        try:
            self.noise_presets = read_noise_presets(); self.noise_preset_error = ''
        except (ValueError, OSError) as error:
            self.noise_presets = {}; self.noise_preset_error = str(error)
        blocker = QSignalBlocker(self.noise_colour)
        self.noise_colour.clear(); self.noise_colour.addItems(COLOURS); self.noise_colour.addItems(list(self.noise_presets))
        self.noise_colour.insertSeparator(self.noise_colour.count())
        self.noise_colour.addItem('Save curve as preset…', 'save'); self.noise_colour.addItem('Delete preset…', 'delete')

    def options_edited(self, options):
        if not 0 <= self.current < len(self.stack) or self.stack[self.current].kind != NOISE: return
        stack = list(self.stack); stack[self.current] = replace(stack[self.current], noise=options)
        self.propose(stack, self.current)

    def rate_edited(self, rate):
        if 0 <= self.current < len(self.stack) and self.stack[self.current].kind == NOISE:
            self.options_edited(replace(self.stack[self.current].noise, rate=rate))

    def colour_edited(self, index):
        if not 0 <= self.current < len(self.stack) or self.stack[self.current].kind != NOISE: return
        options = self.stack[self.current].noise
        name, action = self.noise_colour.itemText(index), self.noise_colour.itemData(index)
        try:
            if action == 'save':
                name, ok = QInputDialog.getText(self, 'Save Noise curve', 'Preset name', text=options.preset)
                if not ok: self.refresh(); return
                name = name.strip()
                if name in self.noise_presets and QMessageBox.question(self, 'Replace preset', f'Replace “{name}”?') != QMessageBox.StandardButton.Yes:
                    self.refresh(); return
                save_noise_preset(name, curve_of(options)); self.reload_noise_presets()
                self.options_edited(NoiseOptions('Custom', options.rate, curve_of(options), name)); return
            if action == 'delete':
                names = list(self.noise_presets)
                if not names: self.refresh(); return
                name, ok = QInputDialog.getItem(self, 'Delete Noise preset', 'Preset', names, 0, False)
                if ok and QMessageBox.question(self, 'Delete preset', f'Delete “{name}”?') == QMessageBox.StandardButton.Yes:
                    delete_noise_preset(name); self.reload_noise_presets()
                self.refresh(); return
            if name in self.noise_presets: updated = NoiseOptions('Custom', options.rate, self.noise_presets[name], name)
            elif name == 'Custom': updated = NoiseOptions('Custom', options.rate, curve_of(options))
            else: updated = NoiseOptions(name, options.rate)
            self.options_edited(updated)
        except (ValueError, OSError) as error:
            self.refresh(); self.error.setText(str(error))

    def set_animation(self, shape):
        animated = shape is not None and shape.model is not None and shape.model.animation is not None
        self.animation_box.setVisible(animated)
        if not animated: return
        self.animation_duration = shape.model.animation.duration
        for key, widget in self.animation_fields.items():
            value = getattr(shape.animation, key)
            blocker = QSignalBlocker(widget)
            if key == 'mode': widget.setCurrentText(value)
            else:
                if key.startswith('trim_'): widget.setMaximum(self.animation_duration)
                widget.setValue(self.animation_duration if value is None else value)

    def edit_animation(self, *args):
        from phosphor.engine.model_animation import Playback
        values = {key: widget.currentText() if key == 'mode' else widget.value()
                  for key, widget in self.animation_fields.items()}
        if values['trim_out'] >= self.animation_fields['trim_out'].maximum():
            values['trim_out'] = None
        self.animation_changed.emit(Playback(**values))

    # Display -----------------------------------------------------------------
    def set_audition(self, bypassed, solo_index):
        blocker = QSignalBlocker(self.bypass_button); self.bypass_button.setChecked(bypassed)
        if self.solo_index != solo_index:
            self.solo_index = solo_index; self.refresh()

    def set_stack(self, stack):
        if stack == self.stack and self.list.count() == len(stack): return
        self.stack = tuple(stack)
        self.current = min(max(self.current, 0 if stack else -1), len(stack)-1)
        self.error.setText(''); self.refresh()

    def refresh(self):
        blocker = QSignalBlocker(self.list)
        labels = [f'{i+1:02}  {t.kind}' + (' ƒ' if any(p.expression and not is_identity(f, p) for f, p in zip(FIELDS[t.kind], t.params)) else '')
                  + (' ◆' if any(p.points for p in (*t.params, t.on)) else '') for i, t in enumerate(self.stack)]
        if [self.list.item(i).text() for i in range(self.list.count())] != labels:
            self.list.clear()
            for label in labels:
                item = QListWidgetItem(label)
                item.setFlags((item.flags() | Qt.ItemFlag.ItemIsDragEnabled) & ~Qt.ItemFlag.ItemIsDropEnabled)
                self.list.addItem(item)
        rows = min(max(len(self.stack), 3), 7)
        self.list.setFixedHeight(rows * 26 + 6)
        for i, transform in enumerate(self.stack):
            item = self.list.item(i)
            row = self.list.itemWidget(item)
            if row is None:
                from PySide6.QtGui import QColor
                item.setForeground(QColor('transparent'))
                row = QWidget(); layout = QHBoxLayout(row); layout.setContentsMargins(0, 0, 0, 0)
                button = QPushButton('⏻'); button.setCheckable(True); button.setFixedSize(28, 24)
                button.setToolTip('Power on/off; Alt+click: solo this step (click again to clear)')
                button.clicked.connect(lambda checked=False, i=i: self.power_clicked(i))
                layout.addWidget(button); layout.addWidget(QLabel(labels[i]), 1); row.power = button
                row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                row.customContextMenuRequested.connect(lambda point, i=i, row=row: self.step_menu(i, row.mapToGlobal(point)))
                item.setSizeHint(button.sizeHint()); self.list.setItemWidget(item, row)
            row.power.setChecked(i == self.solo_index if self.solo_index is not None else transform.enabled)
            row.power.setText('S' if i == self.solo_index else '⏻')
        self.list.setCurrentRow(self.current)
        selected = self.stack[self.current] if 0 <= self.current < len(self.stack) else None
        for kind, (box, fields) in self.editors.items():
            box.setVisible(selected is not None and selected.kind == kind)
            if selected is not None and selected.kind == kind:
                for widget, param in zip(fields, selected.params): widget.set_param(param)
                if kind == NOISE:
                    options = selected.noise
                    colour = options.preset if options.preset in self.noise_presets else options.colour
                    blocker_colour = QSignalBlocker(self.noise_colour); self.noise_colour.setCurrentText(colour)
                    blocker_rate = QSignalBlocker(self.noise_rate); self.noise_rate.setCurrentText(options.rate)
                    curve = curve_of(options)
                    if self.noise_spectrum.curve != curve: self.noise_spectrum.set_curve(curve)
                    for widget in fields[1:]: widget.setVisible(options.rate == 'Wobble')
                    if self.noise_preset_error: self.error.setText(self.noise_preset_error)
        self.presets.setVisible(selected is not None and selected.kind == MOTION_PATH)
        self.map_presets.setVisible(selected is not None and selected.kind == MAP)
        # The step's description is a tooltip, so six Shear rows still fit 1000×700.
        def help_text(kind): return HELP[kind] + ('' if kind == MAP else '\nRight-click a number for Time varying….')
        tip = help_text(selected.kind) if selected is not None else ''
        for kind, (box, _) in self.editors.items(): box.setToolTip(help_text(kind))
        for i, transform in enumerate(self.stack): self.list.item(i).setToolTip(HELP[transform.kind])
        self.help.setText(tip); self.help.hide()
        needs_sub = (
            self._target_solid in LOW_POLY_SOLIDS
            and selected is not None
            and selected.kind in DEFORM_NEEDS_SUBDIVIDE
            and len(self.stack) < MAX_TRANSFORMS
            and not any(t.enabled and t.kind == SUBDIVIDE for t in self.stack[:self.current])
        )
        self.subdivide_bar.setVisible(needs_sub)
        self.empty.setVisible(not self.stack)
        for key in ('duplicate', 'remove'): self.buttons[key].setEnabled(selected is not None)
        self.buttons['up'].setEnabled(self.current > 0); self.buttons['down'].setEnabled(0 <= self.current < len(self.stack)-1)
        # Add stays open at the limit so Saved › Replace stack, Rename and Delete stay reachable.
        for action in self.kind_actions: action.setEnabled(len(self.stack) < MAX_TRANSFORMS)
        self.buttons['duplicate'].setEnabled(selected is not None and len(self.stack) < MAX_TRANSFORMS)

    def build_step_menu(self, index):
        """A transform row's right-click menu (built separately so smokes can inspect it)."""
        menu = QMenu(self)
        automated = 0 <= index < len(self.stack) and bool(self.stack[index].on.points)
        action = switch_action(menu, automated, self.automation_allowed)
        action.triggered.connect(lambda checked=False: self.automate_switch.emit(index, not automated))
        menu.addSeparator()
        self.add_save_actions(menu, index)
        return menu

    # Saved transforms (T6) -------------------------------------------------------
    def set_presets_path(self, path):
        """Use path for saved transforms (None: off, the default until main() enables user files)."""
        self.presets_path = path; self.reload_saved()

    def reload_saved(self):
        """Read the user's saved transforms; a damaged or newer file is reported, never overwritten."""
        self.saved, self.saved_error = {}, ''
        if self.presets_path is None: return
        try: self.saved = read_transform_presets(self.presets_path)
        except (OSError, ValueError) as error:
            self.saved_error = f'Saved transforms could not be read: {error}'; self.error.setText(self.saved_error)

    def add_save_actions(self, menu, index):
        on = self.presets_path is not None
        step = menu.addAction('Save step…'); step.setEnabled(on and 0 <= index < len(self.stack))
        step.setToolTip('Save this step, with the variables it reads, to Add › Saved.')
        step.triggered.connect(lambda checked=False: self.save_step(index))
        stack = menu.addAction('Save stack…'); stack.setEnabled(on and bool(self.stack))
        stack.setToolTip('Save the whole stack, with the variables it reads, to Add › Saved.')
        stack.triggered.connect(lambda checked=False: self.save_stack())

    def build_saved_menu(self, menu):
        """Add › Saved: the user's steps and stacks, then Save, Rename and Delete (smokes inspect it)."""
        menu.clear()
        for sub in self.saved_submenus: sub.deleteLater()
        self.saved_submenus = []  # Kept, so PySide never frees a stack's submenu early.
        if self.presets_path is None:
            menu.addAction(SAVED_OFF).setEnabled(False); return menu
        self.reload_saved()
        if self.saved_error: menu.addAction(self.saved_error).setEnabled(False)
        full = len(self.stack) >= MAX_TRANSFORMS
        for kind, heading in ((STEP, 'Steps'), (STACK, 'Stacks')):
            names = [name for name, preset in self.saved.items() if preset.kind == kind]
            if not names: continue
            menu.addSection(heading)
            for name in names:
                label = name.replace('&', '&&')
                if kind == STEP:
                    action = menu.addAction(label); action.setEnabled(not full)
                    action.triggered.connect(lambda checked=False, name=name: self.insert_saved(name))
                else:
                    sub = QMenu(label, menu); menu.addMenu(sub); self.saved_submenus.append(sub)
                    for mode, text in ((APPEND, 'Append to stack'), (REPLACE, 'Replace stack')):
                        action = sub.addAction(text); action.setEnabled(mode == REPLACE or not full)
                        action.triggered.connect(lambda checked=False, name=name, mode=mode: self.insert_saved(name, mode))
        if not self.saved and not self.saved_error:
            menu.addAction('No saved transforms yet: right-click a step › Save step…').setEnabled(False)
        menu.addSeparator()
        self.add_save_actions(menu, self.current)
        rename = menu.addAction('Rename…'); rename.setEnabled(bool(self.saved))
        rename.triggered.connect(lambda checked=False: self.rename_saved())
        delete = menu.addAction('Delete…'); delete.setEnabled(bool(self.saved))
        delete.triggered.connect(lambda checked=False: self.delete_saved())
        return menu

    def _store(self, kind, transforms, default, name):
        if self.presets_path is None: self.error.setText(SAVED_OFF + '.'); return False
        title = 'Save step' if kind == STEP else 'Save stack'
        if name is None:
            name, accepted = self.ask_text(title, 'Name', self.last_saved_name or default)
            if not accepted: return False
        try:
            preset = make_preset(kind, transforms, self.variables)
            name = name.strip()
            existing = read_transform_presets(self.presets_path)
            if name in existing and not self.confirm(title, f'Replace the saved transform "{name}"?'): return False
            save_transform_preset(name, preset, self.presets_path)
        except (OSError, ValueError) as error:
            self.warn(title, str(error)); return False
        self.last_saved_name = name; self.reload_saved()
        self.error.setText(f'Saved "{name}" in Add › Saved.')
        return True

    def save_step(self, index=None, name=None):
        index = self.current if index is None else index
        if not 0 <= index < len(self.stack): return False
        return self._store(STEP, (self.stack[index],), self.stack[index].kind, name)

    def save_stack(self, name=None):
        if not self.stack: return False
        return self._store(STACK, self.stack, '', name)

    def _insert_preset(self, name, preset, mode=APPEND):
        if preset is None: return False
        notices = []
        try:
            stack, variables, first, count = inserted(self.stack, self.variables, preset,
                index=self.current+1 if self.stack else 0, mode=mode, taken=self.taken, notices=notices)
        except ValueError as error:
            self.error.setText(f'Could not insert "{name}": {error}'); return False
        label = INSERT_LABELS[STEP if preset.kind == STEP else mode]
        rebuild = variables != self.variables
        self.stack, self.current, self.variables = stack, first, variables
        if rebuild: self.build_variables()
        self.add_variable_button.setEnabled(len(self.variables) < MAX_OBJECT_VARIABLES)
        self.refresh()
        self.error.setText(' '.join(notices))
        self.preset_inserted.emit(dict(transforms=stack, transform_variables=variables), label)
        return self.stack == stack and self.variables == variables  # False if the scene refused it (and resynced).

    def insert_recipe(self, name, mode=APPEND):
        """Insert a built-in starter transform stack recipe (appended or replacing the stack)."""
        return self._insert_preset(name, BUILTIN_RECIPES.get(name), mode=mode)

    def insert_saved(self, name, mode=APPEND):
        """Insert a copy of the saved transform name (a step below the selection; a stack
        appended, or replacing the stack) and its variables, renaming clashing variables."""
        return self._insert_preset(name, self.saved.get(name), mode=mode)

    def rename_saved(self, old=None, new=None):
        if self.presets_path is None or not self.saved: return False
        names = list(self.saved)
        if old is None:
            old, accepted = self.ask_item('Rename saved transform', 'Saved transform', names,
                                          names.index(self.last_saved_name) if self.last_saved_name in names else 0)
            if not accepted: return False
        if new is None:
            new, accepted = self.ask_text('Rename saved transform', 'New name', old)
            if not accepted: return False
        try: rename_transform_preset(old, new, self.presets_path)
        except (OSError, ValueError) as error:
            self.warn('Rename saved transform', str(error)); return False
        if self.last_saved_name == old: self.last_saved_name = new.strip()
        self.reload_saved(); self.error.setText(f'Renamed "{old}" to "{new.strip()}".')
        return True

    def delete_saved(self, name=None):
        if self.presets_path is None or not self.saved: return False
        if name is None:
            names = list(self.saved)
            name, accepted = self.ask_item('Delete saved transform', 'Saved transform', names,
                                           names.index(self.last_saved_name) if self.last_saved_name in names else 0)
            if not accepted or not self.confirm('Delete saved transform', f'Delete "{name}"? This cannot be undone.'): return False
        try: delete_transform_preset(name, self.presets_path)
        except (OSError, ValueError) as error:
            self.warn('Delete saved transform', str(error)); return False
        if self.last_saved_name == name: self.last_saved_name = ''
        self.reload_saved(); self.error.setText(f'Deleted "{name}".')
        return True

    def step_menu(self, index, position):
        menu = self.build_step_menu(index); menu.exec(position); menu.deleteLater()

    def set_automation(self, allowed):
        self.automation_allowed = allowed
        for _, fields in self.editors.values():
            for widget in fields: widget.automation_allowed = allowed
        for row in self.variable_rows: row['value'].automation_allowed = allowed

    # Object variables (T5) -----------------------------------------------------
    def set_variables(self, variables, taken=None, path=None, matrix=None):
        """Show the object's variables; taken: {name: what uses it} (its equation variables,
        refused as names); path: (path_start, path_trace), which a rename also rewrites;
        matrix: a Matrix object's MatrixConfig, whose speed expressions a rename also rewrites."""
        variables = tuple(variables)
        self.taken = dict(taken or {})
        self.matrix = matrix
        if path is not None: self.path = tuple(path)
        structure = lambda values: [(v.name, v.low, v.high) for v in values]
        rebuild = structure(variables) != structure(self.variables) or len(self.variable_rows) != len(variables)
        self.variables = variables
        if rebuild: self.build_variables()
        else:
            for variable, row in zip(variables, self.variable_rows):
                row['value'].set_param(Param(variable.value, variable.expression, variable.points))
        self.add_variable_button.setEnabled(len(variables) < MAX_OBJECT_VARIABLES)

    def build_variables(self):
        while self.variable_layout.count():
            item = self.variable_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.variable_rows = []
        for index, variable in enumerate(self.variables):
            box = QWidget(); box.setObjectName('transform_variable'); rows = QVBoxLayout(box)
            rows.setContentsMargins(0, 0, 0, 0); rows.setSpacing(2)
            top = QHBoxLayout(); rows.addLayout(top)
            fields = {}
            for key, width, text in (('name', 0, variable.name), ('low', 70, f'{variable.low:g}'), ('high', 70, f'{variable.high:g}')):
                if key != 'name': top.addWidget(QLabel('min' if key == 'low' else 'max'))
                field = QLineEdit(text); field.setObjectName(f'transform_variable_{key}')
                if width: field.setFixedWidth(width)
                else: field.setToolTip('Name: letters, digits and _, not starting with a digit. Press Enter to rename; '
                                       'expressions that use it follow.')
                field.editingFinished.connect(lambda index=index, key=key: self.variable_field_edited(index, key))
                top.addWidget(field, 0 if width else 1); fields[key] = field
            remove = QPushButton('✕'); remove.setFixedWidth(32); remove.setToolTip(f'Remove {variable.name}')
            remove.setObjectName('remove_transform_variable')
            remove.clicked.connect(lambda checked=False, index=index: self.remove_variable(index)); top.addWidget(remove)
            value = ParamField(self.slider_class, Field(variable.name, 'value', variable.value, variable.low, variable.high),
                               VARIABLE_EXPRESSION_HELP, 'f(t, p)', caption_width=44)
            value.setObjectName('transform_variable_value')
            value.set_param(Param(variable.value, variable.expression, variable.points))
            value.automation_allowed = self.automation_allowed
            value.automate.connect(lambda on, name=variable.name: self.automate_variable.emit(name, on))
            value.changed.connect(lambda param, index=index: self.variable_param_edited(index, param))
            rows.addWidget(value)
            self.variable_layout.addWidget(box)
            self.variable_rows.append(dict(fields, value=value))

    def propose_variables(self, variables, rename=None):
        """Publish variables (and, for rename (old, new), the rewritten steps and path) if the
        object stays valid; otherwise show why and keep the last good variables."""
        from phosphor.engine.signal_engine import Shape
        try:
            validate_object_variables(tuple(variables), self.taken)
            shape = Shape(transforms=self.stack, path_start=self.path[0], path_trace=self.path[1],
                          transform_variables=self.variables, matrix=self.matrix)
            shape = renamed_variable(shape, *rename) if rename else shape
            shape = replace(shape, transform_variables=tuple(variables))
            validate_stack(shape.transforms, shape.transform_variables)
            validate_path(shape.path_start, shape.path_trace, shape.transform_variables)
            if shape.matrix is not None: validate_matrix(shape.matrix, variable_names(shape.transform_variables))
        except ValueError as error:
            self.variable_error.setText(str(error)); return False
        fields = dict(transform_variables=shape.transform_variables)
        if rename:
            fields.update(transforms=shape.transforms, path_start=shape.path_start, path_trace=shape.path_trace)
            if shape.matrix is not None and shape.matrix != self.matrix: fields['matrix'] = shape.matrix
            self.stack = shape.transforms; self.path = (shape.path_start, shape.path_trace); self.matrix = shape.matrix
        self.variable_error.setText('')
        rebuild = variable_names(variables) != variable_names(self.variables) or len(variables) != len(self.variables)
        self.variables = tuple(variables)
        if rebuild: self.build_variables()
        self.add_variable_button.setEnabled(len(self.variables) < MAX_OBJECT_VARIABLES)
        if rename: self.refresh()
        self.variables_changed.emit(fields)
        return True

    def add_variable(self):
        if len(self.variables) >= MAX_OBJECT_VARIABLES: return False
        name = new_variable_name({*variable_names(self.variables), *self.taken, *TRANSFORM_RESERVED})
        return self.propose_variables((*self.variables, Variable(name, 1., 0., 10.)))

    def remove_variable(self, index):
        variables = list(self.variables); removed = variables.pop(index)
        if self.propose_variables(tuple(variables)): return True
        self.variable_error.setText(f'{removed.name} is still used: {self.variable_error.text()}')
        return False

    def variable_param_edited(self, index, param):
        if not 0 <= index < len(self.variables): return
        variable = self.variables[index]
        changed = replace(variable, value=min(max(param.value, variable.low), variable.high), expression=param.expression)
        variables = list(self.variables); variables[index] = changed
        if self.propose_variables(tuple(variables)):
            self.variable_rows[index]['value'].set_param(Param(changed.value, changed.expression, changed.points))
        else:
            self.variable_rows[index]['value'].set_param(Param(variable.value, variable.expression, variable.points))

    def variable_field_edited(self, index, key):
        if not 0 <= index < len(self.variables) or index >= len(self.variable_rows): return
        row, variable = self.variable_rows[index], self.variables[index]
        text = row[key].text().strip()
        restore = lambda: row[key].setText(variable.name if key == 'name' else f'{getattr(variable, key):g}')
        try:
            if key == 'name':
                if text == variable.name: return
                check_variable_name(text, [v.name for v in self.variables if v is not variable], self.taken)
                changed, rename = replace(variable, name=text), (variable.name, text)
            else:
                number = float(text)
                if not math.isfinite(number): raise ValueError(f'{key} must be a finite number.')
                if number == getattr(variable, key): return
                changed = replace(variable, **{key: number}); rename = None
                changed = replace(changed, value=min(max(changed.value, changed.low), changed.high))
        except ValueError as error:
            self.variable_error.setText(str(error)); restore(); return
        variables = list(self.variables); variables[index] = changed
        if not self.propose_variables(tuple(variables), rename): restore()
        elif rename is None: self.build_variables()  # The slider's range changed.

    # Edits ---------------------------------------------------------------------
    def power_clicked(self, row):
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier:
            self.solo_changed.emit(row); self.refresh(); return
        stack = list(self.stack); stack[row] = replace(stack[row], enabled=not stack[row].enabled)
        self.propose(stack, row)

    def propose(self, stack, current):
        """Publish stack if it validates; otherwise show why and keep the last good stack."""
        try: validate_stack(tuple(stack), self.variables)
        except ValueError as error:
            self.error.setText(str(error)); self.refresh(); return False
        self.stack, self.current = tuple(stack), current
        self.error.setText(''); self.refresh(); self.changed.emit(self.stack)
        return True

    def select(self, row):
        self.current = row; self.error.setText(''); self.refresh()

    def item_checked(self, item):
        row = self.list.row(item); on = item.checkState() == Qt.CheckState.Checked
        if 0 <= row < len(self.stack) and self.stack[row].enabled != on:
            from dataclasses import replace
            stack = list(self.stack); stack[row] = replace(stack[row], enabled=on); self.propose(stack, row)

    def set_target_solid(self, solid):
        solid = solid or ''
        if solid != self._target_solid:
            self._target_solid = solid
            self.refresh()

    def insert_subdivide_above(self):
        if not 0 <= self.current < len(self.stack) or len(self.stack) >= MAX_TRANSFORMS:
            return False
        stack = list(self.stack)
        stack.insert(self.current, Transform(SUBDIVIDE, (Param(4.),)))
        return self.propose(stack, self.current + 1)

    def add(self, kind):
        index = self.current + 1 if self.stack else 0
        stack = list(self.stack); stack.insert(index, new_transform(kind)); self.propose(stack, index)

    def _rows_moved(self, start, row):
        if not 0 <= start < len(self.stack): return
        to = min(row if row < start else row - 1, len(self.stack) - 1)
        if to == start: return
        stack = list(self.stack); moved = stack.pop(start); stack.insert(to, moved)
        QTimer.singleShot(0, lambda stack=stack, to=to: self.propose(stack, to))

    def action(self, key):
        i, stack = self.current, list(self.stack)
        if not 0 <= i < len(stack): return
        if key == 'duplicate': stack.insert(i+1, stack[i]); self.propose(stack, i+1)
        elif key == 'remove': stack.pop(i); self.propose(stack, min(i, len(stack)-1))
        elif key == 'up' and i > 0: stack[i-1], stack[i] = stack[i], stack[i-1]; self.propose(stack, i-1)
        elif key == 'down' and i < len(stack)-1: stack[i+1], stack[i] = stack[i], stack[i+1]; self.propose(stack, i+1)

    def param_edited(self, name, param):
        i = self.current
        if not 0 <= i < len(self.stack): return
        transform = self.stack[i]
        if param.expression:
            field = next(f for f in FIELDS[transform.kind] if f.name == name)
            try: compiled_expression(param.expression, field_label(transform.kind, field), variable_names(self.variables))
            except ValueError as error: self.error.setText(str(error)); return
        stack = list(self.stack); stack[i] = transform.with_param(name, param); self.propose(stack, i)

    def load_map_preset(self, index):
        blocker = QSignalBlocker(self.map_presets); self.map_presets.setCurrentIndex(0)
        i = self.current
        if index <= 0 or not 0 <= i < len(self.stack) or self.stack[i].kind != MAP: return
        stack = list(self.stack); stack[i] = with_map_preset(stack[i], self.map_presets.itemText(index)); self.propose(stack, i)

    def load_preset(self, index):
        blocker = QSignalBlocker(self.presets); self.presets.setCurrentIndex(0)
        i = self.current
        if index <= 0 or not 0 <= i < len(self.stack) or self.stack[i].kind != MOTION_PATH: return
        stack = list(self.stack); stack[i] = with_motion_preset(stack[i], self.presets.itemText(index)); self.propose(stack, i)
