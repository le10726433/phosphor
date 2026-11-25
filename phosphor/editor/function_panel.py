"""Qt controls for a Parametric equation object, shown in SCENE › Objects."""
from dataclasses import replace
import math
import re

from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox,
                               QInputDialog, QMessageBox)

from phosphor.engine.function_plot import (FunctionPlot, Variable, PRESETS, MAX_VARIABLES,
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
    compile_plot = staticmethod(compiled_plot)
    validate_plot = staticmethod(validate_plot)
    store = saved_presets.FUNCTION_STORE  # Built-in and saved presets (subclasses swap it).
    variable_help = VARIABLE_HELP
    variable_placeholder = 'f(t, s)'

    def __init__(self, slider_class, plot=None):
        super().__init__()
        self.slider_class = slider_class
        self.automation_allowed = False
        self.reserved = set()  # The object's own variables (Transform › Variables, T5): refused here.
        self.plot = plot or FunctionPlot()
        # No layout alignment: an aligned layout is placed at its size hint, which ignores the wrapped help line's
        # height for width (UI2: it lost a line at a 280 px panel). The panel is never given spare height.
        layout = QVBoxLayout(self); layout.setSpacing(6)
        layout.setContentsMargins(0, 0, 0, 0)
        row = FlowRow(); layout.addLayout(row)  # Wraps in a narrow panel (UI2).
        self.presets = QComboBox(); self.presets.setObjectName('function_presets'); row.addWidget(self.presets, 1)
        self.presets.activated.connect(self.load_preset)
        self.save_button = QPushButton('Save…'); self.save_button.setToolTip('Save these equations as a preset')
        self.save_button.clicked.connect(self.save_preset); row.addWidget(self.save_button)
        self.delete_button = QPushButton('Delete…'); self.delete_button.setToolTip('Delete a saved preset')
        self.delete_button.clicked.connect(self.delete_preset); row.addWidget(self.delete_button)
        for button in (self.save_button, self.delete_button): button.setMinimumWidth(button.sizeHint().width())
        self.presets_path = self.store.path
        self.saved = {}
        self.last_saved_name = ''
        self.entries = {}
        for axis in ('x', 'y', 'z'):
            row = QHBoxLayout(); layout.addLayout(row)
            row.addWidget(_caption(f'{axis} ='))
            entry = QLineEdit(); entry.setObjectName(f'function_{axis}'); entry.setPlaceholderText(f'{axis}(t, s)')
            entry.editingFinished.connect(lambda axis=axis: self.expression_edited(axis))
            row.addWidget(entry, 1); self.entries[axis] = entry
        self.error = _Message(); self.error.setObjectName('function_error'); self.error.setWordWrap(True); self.error.hide(); layout.addWidget(self.error)
        self.variable_box = QWidget(); self.variable_layout = QVBoxLayout(self.variable_box)
        self.variable_layout.setContentsMargins(0, 0, 0, 0); self.variable_layout.setSpacing(4)
        layout.addWidget(self.variable_box)
        row = QHBoxLayout(); layout.addLayout(row)
        self.add_button = QPushButton('Add variable'); self.add_button.clicked.connect(self.add_variable); row.addWidget(self.add_button)
        row.addStretch(1)
        # The help line has its own word-wrapped row under the button (UI1: beside it, it was clipped).
        help = QLabel(HELP[0]+' · hover for functions'); help.setObjectName('function_help')
        help.setToolTip('\n'.join((help.text(), *HELP[1:], 'Pitch comes from the source; with the Single tone source, the Frequency slider at the bottom of SCENE sets how often s runs 0–1, in Hz.',
                                    'Transform moves, sizes and rotates the curve.')))
        help.setWordWrap(True); layout.addWidget(help)
        self.variable_rows = None
        self.set_plot(self.plot, force=True)
        self.reload_presets()

    # Display -----------------------------------------------------------------
    def set_plot(self, plot, force=False):
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

    def load_preset(self, index):
        source = self.presets.itemData(index)
        blocker = QSignalBlocker(self.presets); self.presets.setCurrentIndex(0)
        if not source: return
        kind, name = source
        plot = self.store.builtins[name] if kind == 'builtin' else self.saved[name]
        if kind == 'saved': self.last_saved_name = name
        notices = []  # A preset variable named like an object variable is renamed (T5).
        plot = renamed_clashes(plot, self.reserved, notices)
        if self.propose(plot, rebuild=True):
            self.set_plot(plot, force=True)
            if notices: self.error.setText(' '.join(notices))

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
