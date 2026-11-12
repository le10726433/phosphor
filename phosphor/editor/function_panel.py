"""Qt controls for the x(t), y(t) function plot (Monitor input: Function)."""
from dataclasses import replace
import math
import re

from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox,
                               QInputDialog, QMessageBox)

from phosphor.engine.function_plot import (FunctionPlot, Variable, PRESETS, MAX_VARIABLES, FREQUENCY_RANGE,
                                           CompiledPlot, validate_plot)
from phosphor.storage import function_presets as saved_presets

HELP = ('t = seconds · s = 0–1 cycle at the s frequency',
        'sin cos tan asin acos atan atan2 sinh cosh tanh exp log log2 log10 sqrt abs',
        'floor ceil round sign hypot fmod min max clip',
        'square saw tri take a cycle position, like s',
        'pi tau e · math.sin(t) also works · a if s < 0.5 else b')
PRESET_PROMPT = 'Load a preset…'


def _caption(text):
    label = QLabel(text); label.setProperty('inlineCaption', True); label.setFixedWidth(28)
    return label


class FunctionPanel(QWidget):
    """Edits a FunctionPlot. Emits changed(plot) only with plots that compile."""
    changed = Signal(object)
    monitor_requested = Signal()

    def __init__(self, slider_class, plot=None):
        super().__init__()
        self.slider_class = slider_class
        self.plot = plot or FunctionPlot()
        layout = QVBoxLayout(self); layout.setAlignment(Qt.AlignmentFlag.AlignTop); layout.setSpacing(6)
        self.inactive = QWidget(); inactive = QVBoxLayout(self.inactive); inactive.setContentsMargins(0, 0, 0, 0)
        inactive.addWidget(QLabel('These play when Monitor input is Function x(t), y(t).'))
        self.use_button = QPushButton('Monitor this function'); inactive.addWidget(self.use_button)
        self.use_button.clicked.connect(self.monitor_requested)
        layout.addWidget(self.inactive)
        self.presets = QComboBox(); self.presets.setObjectName('function_presets'); layout.addWidget(self.presets)
        self.presets.activated.connect(self.load_preset)
        row = QHBoxLayout(); layout.addLayout(row)
        self.save_button = QPushButton('Save as preset…'); self.save_button.clicked.connect(self.save_preset); row.addWidget(self.save_button)
        self.delete_button = QPushButton('Delete saved…'); self.delete_button.clicked.connect(self.delete_preset); row.addWidget(self.delete_button)
        self.presets_path = saved_presets.FUNCTION_PRESETS_PATH
        self.saved = {}
        self.last_saved_name = ''
        self.entries = {}
        for axis in ('x', 'y'):
            row = QHBoxLayout(); layout.addLayout(row)
            row.addWidget(_caption(f'{axis} ='))
            entry = QLineEdit(); entry.setObjectName(f'function_{axis}'); entry.setPlaceholderText(f'{axis}(t, s)')
            entry.editingFinished.connect(lambda axis=axis: self.expression_edited(axis))
            row.addWidget(entry, 1); self.entries[axis] = entry
        row = QHBoxLayout(); layout.addLayout(row)
        caption = QLabel('s frequency (Hz)'); caption.setProperty('inlineCaption', True); caption.setFixedWidth(130); row.addWidget(caption)
        self.frequency = slider_class(self.plot.frequency, .1, 5000., curve='log'); self.frequency.setObjectName('function_frequency')
        self.frequency.setFixedHeight(28); self.frequency.edited.connect(self.frequency_edited); row.addWidget(self.frequency, 1)
        self.error = QLabel(); self.error.setObjectName('function_error'); self.error.setWordWrap(True); layout.addWidget(self.error)
        layout.addWidget(QLabel('Variables'))
        self.variable_box = QWidget(); self.variable_layout = QVBoxLayout(self.variable_box)
        self.variable_layout.setContentsMargins(0, 0, 0, 0); self.variable_layout.setSpacing(4)
        layout.addWidget(self.variable_box)
        self.add_button = QPushButton('Add variable'); self.add_button.clicked.connect(self.add_variable); layout.addWidget(self.add_button)
        for line in HELP: layout.addWidget(QLabel(line))
        layout.addWidget(QLabel('Output is scaled by MIX › Output level. Left = X · Right = Y.'))
        self.variable_rows = None
        self.set_plot(self.plot, force=True)
        self.reload_presets()

    # Display -----------------------------------------------------------------
    def set_active(self, active):
        self.inactive.setVisible(not active)

    def set_plot(self, plot, force=False):
        if plot == self.plot and not force: return
        structure = [(v.name, v.low, v.high) for v in plot.variables]
        rebuild = force or structure != [(v.name, v.low, v.high) for v in self.plot.variables]
        self.plot = plot
        for axis, entry in self.entries.items():
            blocker = QSignalBlocker(entry); entry.setText(getattr(plot, axis))
        blocker = QSignalBlocker(self.frequency); self.frequency.setValue(plot.frequency)
        if rebuild: self.build_variables()
        else:
            for variable, row in zip(plot.variables, self.variable_rows):
                blocker = QSignalBlocker(row['value']); row['value'].setValue(variable.value)
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
            value = self.slider_class(variable.value, variable.low, variable.high); value.setObjectName('variable_value')
            value.setFixedHeight(28); value.edited.connect(lambda number, index=index: self.variable_value_edited(index, number))
            rows.addWidget(value)
            self.variable_layout.addWidget(box)
            self.variable_rows.append(dict(fields, value=value))

    # Edits ---------------------------------------------------------------------
    def propose(self, plot, rebuild=False):
        """Publish plot if it compiles; otherwise show why and keep the last good plot."""
        try: CompiledPlot(plot)
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

    def frequency_edited(self, value):
        self.propose(replace(self.plot, frequency=float(value)))

    # Presets -------------------------------------------------------------------
    def reload_presets(self, path=None):
        """Fill the list: built-ins, then this user's saved presets."""
        if path is not None: self.presets_path = path
        try: self.saved = saved_presets.read_function_presets(self.presets_path)
        except (OSError, ValueError) as error:
            self.saved = {}; self.error.setText(f'Saved presets could not be read: {error}')
        blocker = QSignalBlocker(self.presets); self.presets.clear()
        self.presets.addItem(PRESET_PROMPT, None)
        for name in PRESETS: self.presets.addItem(name, ('builtin', name))
        if self.saved:
            self.presets.insertSeparator(self.presets.count())
            for name in self.saved: self.presets.addItem(f'★ {name}', ('saved', name))
        self.delete_button.setEnabled(bool(self.saved))

    def load_preset(self, index):
        source = self.presets.itemData(index)
        blocker = QSignalBlocker(self.presets); self.presets.setCurrentIndex(0)
        if not source: return
        kind, name = source
        plot = PRESETS[name] if kind == 'builtin' else self.saved[name]
        if kind == 'saved': self.last_saved_name = name
        if self.propose(plot, rebuild=True): self.set_plot(plot, force=True)

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
            name = saved_presets.preset_name(name)
            if name in self.saved and QMessageBox.question(self, 'Save preset', f'Replace the saved preset "{name}"?') != QMessageBox.StandardButton.Yes:
                return False
            saved_presets.save_function_preset(name, self.plot, self.presets_path)
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
        try: saved_presets.delete_function_preset(name, self.presets_path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Delete saved preset', str(error)); return False
        if self.last_saved_name == name: self.last_saved_name = ''
        self.reload_presets()
        self.error.setText(f'Deleted preset "{name}".')
        return True

    def add_variable(self):
        used = {v.name for v in self.plot.variables}
        name = next(n for n in (*'abcdfghjkmnpqruvwz', *(f'v{i}' for i in range(1, 20))) if n not in used)
        self.propose(replace(self.plot, variables=(*self.plot.variables, Variable(name, 1., 0., 10.))), rebuild=True)

    def remove_variable(self, index):
        variables = list(self.plot.variables); removed = variables.pop(index)
        if not self.propose(replace(self.plot, variables=tuple(variables)), rebuild=True):
            self.error.setText(f'{removed.name} is still used: {self.error.text()}')

    def variable_value_edited(self, index, value):
        variable = self.plot.variables[index]
        self.propose(self.plot.with_variable(variable.name, float(value)))

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
            if key == 'name':  # Follow the rename in both expressions.
                rename = lambda expression: re.sub(rf'(?<![\w.]){re.escape(variable.name)}\b', text, expression)
                plot = replace(plot, x=rename(plot.x), y=rename(plot.y))
            validate_plot(plot) if key != 'name' else None
        except ValueError as error:
            self.error.setText(str(error)); row[key].setText(variable.name if key == 'name' else f'{getattr(variable, key):g}')
            return
        if not self.propose(plot, rebuild=True):
            row[key].setText(variable.name if key == 'name' else f'{getattr(variable, key):g}')
