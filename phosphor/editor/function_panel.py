"""Qt controls for a Parametric equation object, shown in SCENE › Objects."""
from dataclasses import replace
import math
import re

from PySide6.QtCore import QEvent, Qt, Signal, QSignalBlocker, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox,
                               QInputDialog, QMenu, QMessageBox, QToolButton)

from phosphor.engine.function_plot import (FunctionPlot, Variable, PRESETS, PARAMETRIC_GROUPS, MAX_VARIABLES,
                                           compiled_plot, validate_plot)
from phosphor.engine.object_replace import renamed_clashes
from phosphor.storage import function_presets as saved_presets
from phosphor.engine.transforms import Field, Param
from phosphor.editor.transform_panel import ParamField
from phosphor.editor.panel_widgets import InlineCaption, FlowRow

HELP = ('s = 0–1 along the object · t = seconds · p = beats',
        'sin cos tan asin acos atan atan2 sinh cosh tanh exp log log2 log10 sqrt abs',
        'floor ceil round sign hypot fmod min max clip',
        'square saw tri take a cycle position, like s',
        'pi tau e · math.sin(t) also works · a if s < 0.5 else b')
PRESET_PROMPT = 'Load a preset…'
VARIABLE_HELP = ('Time varying: an expression of t (seconds), p (beats at the Composer tempo) and s (0–1 along the object), evaluated every sample '
                 'and clipped to min–max. Press Enter to apply; right-click › Static to go back to the slider.')


class _Message(QLabel):
    """A message line that takes no room while it is empty (keeps SCENE unscrolled at 1000×700)."""
    def setText(self, text):
        super().setText(text); self.setVisible(bool(text))


def _caption(text):
    label = InlineCaption(text, 28, 28)
    return label


class FunctionPanel(QWidget):
    """Edits a FunctionPlot. Emits changed(plot) only with plots that compile."""
    changed = Signal(object)
    automate = Signal(str, bool)  # Variable name, on (Composer only).
    add_3d_view = Signal()  # 1-click helper to append 3D Spin & Perspective for 3D equations.
    compile_plot = staticmethod(compiled_plot)
    validate_plot = staticmethod(validate_plot)
    store = saved_presets.FUNCTION_STORE  # Built-in and saved presets (subclasses swap it).
    preset_groups = PARAMETRIC_GROUPS
    reference_tokens = ('s', 't', 'p', 'tau', 'sin()', 'cos()', 'tri()', 'saw()', 'abs()', 'sqrt()', 'exp()', 'clip()')
    variable_help = VARIABLE_HELP
    variable_placeholder = 'f(t, s)'

    def __init__(self, slider_class, plot=None):
        super().__init__()
        self.slider_class = slider_class
        self.automation_allowed = False
        self._reserved = set()  # The object's own variables (Transform › Variables, T5): refused here.
        self.plot = plot or FunctionPlot()
        self._live_timer = QTimer(self); self._live_timer.setSingleShot(True); self._live_timer.setInterval(250)
        self._live_timer.timeout.connect(self._live_validate)
        # No layout alignment: an aligned layout is placed at its size hint, which ignores the wrapped help line's
        # height for width (UI2: it lost a line at a 280 px panel). The panel is never given spare height.
        layout = QVBoxLayout(self); layout.setSpacing(6)
        layout.setContentsMargins(0, 0, 0, 0)
        row = FlowRow(); layout.addLayout(row)  # Wraps in a narrow panel (UI2).
        self.presets = QComboBox(); self.presets.setObjectName('function_presets'); row.addWidget(self.presets, 1)
        self.presets.activated.connect(self.load_preset)
        self.browse_button = QPushButton('Browse'); self.browse_button.setObjectName('browse_presets')
        self.browse_button.setProperty('menuButton', True)
        self.browse_button.setToolTip('Browse equation presets grouped by category')
        self._browse_menu = QMenu(self.browse_button)
        self.browse_button.setMenu(self._browse_menu)
        row.addWidget(self.browse_button)
        self.save_button = QPushButton('Save…'); self.save_button.setToolTip('Save these equations as a preset')
        self.save_button.clicked.connect(self.save_preset); row.addWidget(self.save_button)
        self.delete_button = QPushButton('Delete…'); self.delete_button.setToolTip('Delete a saved preset')
        self.delete_button.clicked.connect(self.delete_preset); row.addWidget(self.delete_button)
        for button in (self.browse_button, self.save_button, self.delete_button): button.setMinimumWidth(button.sizeHint().width())
        self.presets_path = self.store.path
        self.saved = {}
        self.last_saved_name = ''
        self.entries = {}
        mono_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        for axis in ('x', 'y', 'z'):
            row = QHBoxLayout(); layout.addLayout(row)
            row.addWidget(_caption(f'{axis} ='))
            entry = QLineEdit(); entry.setObjectName(f'function_{axis}'); entry.setPlaceholderText(f'{axis}(t, s)')
            entry.setFont(mono_font)
            entry.installEventFilter(self)
            entry.textEdited.connect(lambda _text, e=entry: (setattr(self, '_last_entry', e), self._live_timer.start()))
            entry.editingFinished.connect(lambda axis=axis: self.expression_edited(axis))
            row.addWidget(entry, 1); self.entries[axis] = entry
        self._last_entry = self.entries['x']
        self.error = _Message(); self.error.setObjectName('function_error'); self.error.setWordWrap(True); self.error.hide(); layout.addWidget(self.error)
        self.variable_box = QWidget(); self.variable_layout = QVBoxLayout(self.variable_box)
        self.variable_layout.setContentsMargins(0, 0, 0, 0); self.variable_layout.setSpacing(4)
        layout.addWidget(self.variable_box)
        row = QHBoxLayout(); layout.addLayout(row)
        self.add_button = QPushButton('Add variable'); self.add_button.clicked.connect(self.add_variable); row.addWidget(self.add_button)
        self.preset_status = QLabel(''); self.preset_status.setObjectName('preset_status')
        row.addWidget(self.preset_status, 1)
        self.ref_toggle = QToolButton(self); self.ref_toggle.setObjectName('equation_ref_toggle')
        self.ref_toggle.setText('? Tokens'); self.ref_toggle.setCheckable(True)
        self.ref_toggle.setToolTip('Show/hide clickable math tokens to insert at cursor')
        self.ref_toggle.toggled.connect(lambda on: self.ref_drawer.setVisible(on))
        row.addWidget(self.ref_toggle)
        # The help line has its own word-wrapped row under the button (UI1: beside it, it was clipped).
        help = QLabel(HELP[0]+' · hover for functions'); help.setObjectName('function_help')
        help.setToolTip('\n'.join((help.text(), *HELP[1:], 'Pitch comes from the source; with the Single tone source, the Frequency slider at the bottom of SCENE sets how often s runs 0–1, in Hz.',
                                    'Transform moves, sizes and rotates the curve.')))
        help.setWordWrap(True); layout.addWidget(help)
        # Collapsible quick-reference clickable token chips drawer
        self.ref_drawer = QWidget(); self.ref_drawer.setObjectName('equation_ref_drawer')
        self._ref_flow = FlowRow(); self.ref_drawer.setLayout(self._ref_flow)
        self._rebuild_ref_tokens()
        layout.addWidget(self.ref_drawer); self.ref_drawer.hide()
        # Cross-tab awareness of Transform › Variables (hidden when empty)
        self.object_vars_label = _Message(); self.object_vars_label.setObjectName('equation_object_vars')
        self.object_vars_label.setWordWrap(True); self.object_vars_label.hide(); layout.addWidget(self.object_vars_label)
        # Smart contextual helper for 3D curves/attractors without rotation/spin yet
        self._has_3d_rotation = False
        self.spin3d_bar = QWidget(); self.spin3d_bar.setObjectName('spin3d_hint_bar')
        s3_row = FlowRow(); self.spin3d_bar.setLayout(s3_row)
        s3_lbl = QLabel('3D curve ·'); s3_lbl.setWordWrap(True)
        self.spin3d_button = QPushButton('+ Add 3D Spin & Perspective'); self.spin3d_button.setObjectName('add_3d_spin_btn')
        self.spin3d_button.setToolTip('Append Rotate + Spin + Perspective so this 3D curve rotates smoothly in depth.')
        self.spin3d_button.clicked.connect(self.add_3d_view.emit)
        s3_row.addWidget(s3_lbl); s3_row.addWidget(self.spin3d_button)
        layout.addWidget(self.spin3d_bar); self.spin3d_bar.hide()
        self.variable_rows = None
        self.set_plot(self.plot, force=True)
        self.reload_presets()

    @property
    def reserved(self):
        return self._reserved

    @reserved.setter
    def reserved(self, names):
        self._reserved = set(names or ())
        if hasattr(self, 'object_vars_label'):
            if self._reserved:
                self.object_vars_label.setText('Object vars (Transform): ' + ', '.join(sorted(self._reserved)))
            else:
                self.object_vars_label.setText('')

    def _rebuild_ref_tokens(self):
        while self._ref_flow.count():
            item = self._ref_flow.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for tok in self.reference_tokens:
            btn = QToolButton(self.ref_drawer)
            btn.setText(tok)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setToolTip(f'Insert "{tok}" into the active expression field')
            btn.clicked.connect(lambda _=False, t=tok: self.insert_token(t))
            self._ref_flow.addWidget(btn)

    def insert_token(self, token):
        target = self._last_entry if self._last_entry is not None else self.entries['x']
        target.insert(token)
        if token.endswith('()'):
            target.setCursorPosition(target.cursorPosition() - 1)
        target.setFocus()
        self._live_timer.start()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusIn and watched in self.entries.values():
            self._last_entry = watched
        return super().eventFilter(watched, event)

    def _live_validate(self):
        draft = replace(self.plot, x=self.entries['x'].text(), y=self.entries['y'].text(), z=self.entries['z'].text())
        if draft == self.plot:
            return
        try:
            self.validate_plot(draft)
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.error.setText('')

    def _sync_preset_status(self):
        if not hasattr(self, 'preset_status'): return
        for name, p in self.store.builtins.items():
            if p == self.plot:
                self.preset_status.setText(f'Preset: {name}')
                return
        for name, p in self.saved.items():
            if p == self.plot:
                self.preset_status.setText(f'Preset: ★ {name}')
                return
        self.preset_status.setText('Custom')

    def set_has_3d_rotation(self, has_rot):
        self._has_3d_rotation = bool(has_rot)
        self._sync_spin3d_bar()

    def _sync_spin3d_bar(self):
        z_text = (getattr(self.plot, 'z', '0') or '0').strip()
        is_3d = z_text not in ('', '0', '0.0', '0.')
        self.spin3d_bar.setVisible(is_3d and not self._has_3d_rotation)

    # Display -----------------------------------------------------------------
    def set_plot(self, plot, force=False):
        self._live_timer.stop()
        if plot == self.plot and not force: return
        structure = [(v.name, v.low, v.high) for v in plot.variables]
        rebuild = force or structure != [(v.name, v.low, v.high) for v in self.plot.variables]
        self.plot = plot
        for axis, entry in self.entries.items():
            blocker = QSignalBlocker(entry); entry.setText(getattr(plot, axis))
        if rebuild: self.build_variables()
        else:
            for variable, row in zip(plot.variables, self.variable_rows):
                blocker = QSignalBlocker(row['value']); row['value'].set_param(Param(variable.value, variable.expression, variable.points))
        self.add_button.setEnabled(len(plot.variables) < MAX_VARIABLES)
        self.error.setText('')
        self._sync_spin3d_bar()
        self._sync_preset_status()

    def build_variables(self):
        while self.variable_layout.count():
            item = self.variable_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.variable_rows = []
        for index, variable in enumerate(self.plot.variables):
            box = QWidget(); box.setObjectName('function_variable'); rows = QVBoxLayout(box)
            rows.setContentsMargins(0, 0, 0, 0); rows.setSpacing(2)
            top = QHBoxLayout(); rows.addLayout(top)
            fields = {}
            for key, width, text in (('name', 0, variable.name), ('low', 70, f'{variable.low:g}'), ('high', 70, f'{variable.high:g}')):
                if key != 'name': top.addWidget(QLabel('min' if key == 'low' else 'max'))
                field = QLineEdit(text); field.setObjectName(f'variable_{key}')
                if width: field.setFixedWidth(width)
                field.editingFinished.connect(lambda index=index, key=key: self.variable_field_edited(index, key))
                top.addWidget(field, 0 if width else 1); fields[key] = field
            remove = QPushButton('✕'); remove.setFixedWidth(32); remove.setToolTip(f'Remove {variable.name}')
            remove.clicked.connect(lambda checked=False, index=index: self.remove_variable(index)); top.addWidget(remove)
            # Right-click: Static (the slider) or Time varying (an expression of t and s).
            value = ParamField(self.slider_class, Field(variable.name, 'value', variable.value, variable.low, variable.high),
                               self.variable_help, self.variable_placeholder, caption_width=44)
            value.setObjectName('variable_value'); value.set_param(Param(variable.value, variable.expression, variable.points))
            value.automation_allowed = self.automation_allowed
            value.automate.connect(lambda on, name=variable.name: self.automate.emit(name, on))
            value.changed.connect(lambda param, index=index: self.variable_param_edited(index, param))
            rows.addWidget(value)
            self.variable_layout.addWidget(box)
            self.variable_rows.append(dict(fields, value=value))

    def set_automation(self, allowed):
        self.automation_allowed = allowed
        for row in getattr(self, "variable_rows", ()): row["value"].automation_allowed = allowed

    # Edits ---------------------------------------------------------------------
    def propose(self, plot, rebuild=False):
        """Publish plot if it compiles; otherwise show why and keep the last good plot."""
        clash = next((v.name for v in plot.variables if v.name in self.reserved), None)
        if clash is not None:  # User decision, November 21, 2026: equation and object variables never share a name.
            self.error.setText(f'{clash} is a variable of this object (Transform › Variables); choose another name.'); return False
        try: self.compile_plot(plot)
        except ValueError as error:
            self.error.setText(str(error)); return False
        previous = self.plot
        self.plot = plot
        if rebuild: self.build_variables()
        self.add_button.setEnabled(len(plot.variables) < MAX_VARIABLES)
        self.error.setText('')
        self._sync_spin3d_bar()
        self._sync_preset_status()
        if plot != previous: self.changed.emit(plot)
        return True

    def expression_edited(self, axis):
        text = self.entries[axis].text()
        if text == getattr(self.plot, axis): self.error.setText(''); return
        self.propose(replace(self.plot, **{axis: text}))

    # Presets -------------------------------------------------------------------
    def reload_presets(self, path=None):
        """Fill the list: built-ins, then this user's saved presets."""
        if path is not None: self.presets_path = path
        notices = []
        try:
            self.saved = self.store.read(self.presets_path, notices)
            if notices: self.error.setText('Saved presets: ' + ' · '.join(notices))
        except (OSError, ValueError) as error:
            self.saved = {}; self.error.setText(f'Saved presets could not be read: {error}')
        blocker = QSignalBlocker(self.presets); self.presets.clear()
        self.presets.addItem(PRESET_PROMPT, None)
        for name in self.store.builtins: self.presets.addItem(name, ('builtin', name))
        if self.saved:
            self.presets.insertSeparator(self.presets.count())
            for name in self.saved: self.presets.addItem(f'★ {name}', ('saved', name))
        self.delete_button.setEnabled(bool(self.saved))
        self._rebuild_browse_menu()
        self._sync_preset_status()

    def _rebuild_browse_menu(self):
        menu = self._browse_menu
        menu.clear()
        seen = set()
        for group_title, names in self.preset_groups:
            sub = menu.addMenu(group_title)
            for name in names:
                if name in self.store.builtins:
                    seen.add(name)
                    sub.addAction(name, lambda n=name: self.load_named_preset('builtin', n))
        leftover = [n for n in self.store.builtins if n not in seen]
        if leftover:
            sub = menu.addMenu('Other')
            for name in leftover:
                sub.addAction(name, lambda n=name: self.load_named_preset('builtin', n))
        if self.saved:
            menu.addSeparator()
            sub = menu.addMenu('★ Saved')
            for name in self.saved:
                sub.addAction(name, lambda n=name: self.load_named_preset('saved', n))

    def load_named_preset(self, kind, name):
        plot = self.store.builtins.get(name) if kind == 'builtin' else self.saved.get(name)
        if plot is None: return False
        if kind == 'saved': self.last_saved_name = name
        notices = []  # A preset variable named like an object variable is renamed (T5).
        plot = renamed_clashes(plot, self.reserved, notices)
        if self.propose(plot, rebuild=True):
            self.set_plot(plot, force=True)
            if notices: self.error.setText(' '.join(notices))
            return True
        return False

    def load_preset(self, index):
        source = self.presets.itemData(index)
        blocker = QSignalBlocker(self.presets); self.presets.setCurrentIndex(0)
        if not source: return
        kind, name = source
        self.load_named_preset(kind, name)

    def apply_pending_edits(self):
        """Apply typed but unentered expressions; False if one does not compile."""
        for axis, entry in self.entries.items():
            if entry.text() != getattr(self.plot, axis) and not self.propose(replace(self.plot, **{axis: entry.text()})):
                return False
        return True

    def save_preset(self, checked=False, name=None):
        if not self.apply_pending_edits():
            QMessageBox.warning(self, 'Save preset', 'Fix the expression before saving: '+self.error.text()); return False
        if name is None:
            name, accepted = QInputDialog.getText(self, 'Save as preset', 'Preset name', text=self.last_saved_name)
            if not accepted: return False
        try:
            name = self.store.preset_name(name)
            if name in self.saved and QMessageBox.question(self, 'Save preset', f'Replace the saved preset "{name}"?') != QMessageBox.StandardButton.Yes:
                return False
            self.store.save(name, self.plot, self.presets_path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Save preset', str(error)); return False
        self.last_saved_name = name
        self.reload_presets()
        self.error.setText(f'Saved preset "{name}".')
        return True

    def delete_preset(self, checked=False, name=None):
        if not self.saved: return False
        if name is None:
            names = list(self.saved)
            current = names.index(self.last_saved_name) if self.last_saved_name in names else 0
            name, accepted = QInputDialog.getItem(self, 'Delete saved preset', 'Preset', names, current, False)
            if not accepted: return False
            if QMessageBox.question(self, 'Delete saved preset', f'Delete "{name}"? This cannot be undone.') != QMessageBox.StandardButton.Yes:
                return False
        try: self.store.delete(name, self.presets_path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Delete saved preset', str(error)); return False
        if self.last_saved_name == name: self.last_saved_name = ''
        self.reload_presets()
        self.error.setText(f'Deleted preset "{name}".')
        return True

    def add_variable(self):
        used = {v.name for v in self.plot.variables} | self.reserved
        name = next(n for n in (*'abcdfghjkmnpqruvwz', *(f'v{i}' for i in range(1, 20))) if n not in used)
        self.propose(replace(self.plot, variables=(*self.plot.variables, Variable(name, 1., 0., 10.))), rebuild=True)

    def remove_variable(self, index):
        variables = list(self.plot.variables); removed = variables.pop(index)
        if not self.propose(replace(self.plot, variables=tuple(variables)), rebuild=True):
            self.error.setText(f'{removed.name} is still used: {self.error.text()}')

    def variable_value_edited(self, index, value):
        variable = self.plot.variables[index]
        self.propose(self.plot.with_variable(variable.name, float(value)))

    def variable_param_edited(self, index, param):
        variables = list(self.plot.variables)
        variables[index] = replace(variables[index], value=min(max(param.value, variables[index].low), variables[index].high),
                                   expression=param.expression)
        if self.propose(replace(self.plot, variables=tuple(variables))):
            self.variable_rows[index]['value'].set_param(Param(variables[index].value, variables[index].expression, variables[index].points))

    def variable_field_edited(self, index, key):
        row, variable = self.variable_rows[index], self.plot.variables[index]
        text = row[key].text().strip()
        try:
            if key == 'name':
                if text == variable.name: return
                changed = replace(variable, name=text)
            else:
                number = float(text)
                if not math.isfinite(number): raise ValueError(f'{key} must be a finite number.')
                if number == getattr(variable, key): return
                changed = replace(variable, **{key: number})
                changed = replace(changed, value=min(max(changed.value, changed.low), changed.high))
            variables = list(self.plot.variables); variables[index] = changed
            plot = replace(self.plot, variables=tuple(variables))
            if key == 'name':  # Follow the rename in every coordinate expression.
                rename = lambda expression: re.sub(rf'(?<![\w.]){re.escape(variable.name)}\b', text, expression)
                plot = replace(plot, x=rename(plot.x), y=rename(plot.y), z=rename(plot.z))
            self.validate_plot(plot) if key != 'name' else None
        except ValueError as error:
            self.error.setText(str(error)); row[key].setText(variable.name if key == 'name' else f'{getattr(variable, key):g}')
            return
        if not self.propose(plot, rebuild=True):
            row[key].setText(variable.name if key == 'name' else f'{getattr(variable, key):g}')
        elif key == 'name':  # Show the renamed variable in every coordinate expression.
            for axis, entry in self.entries.items():
                blocker = QSignalBlocker(entry); entry.setText(getattr(plot, axis))
