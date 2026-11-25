"""Recurrence controls using the equation panel's shared preset and variable rows."""
from dataclasses import replace
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSpinBox, QComboBox,
                               QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy)
from phosphor.editor.function_panel import FunctionPanel, _Message
from phosphor.engine.recurrence import (RecurrencePlot, Variable, compiled_recurrence,
                                       validate_plot, sequence)
from phosphor.storage.recurrence_presets import RECURRENCE_STORE


def _caption(text):
    # inlineCaption: never wraps. A Fixed policy sizes it from the font in use when laid
    # out; a width fixed at construction is measured before the app font arrives (macOS
    # cut "Order" to "Orde").
    label = QLabel(text); label.setProperty('inlineCaption', True)
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
    return label


class RecurrencePanel(FunctionPanel):
    compile_plot = staticmethod(compiled_recurrence)
    validate_plot = staticmethod(validate_plot)
    store = RECURRENCE_STORE  # Six built-ins plus USER_DIR/recurrence-presets.json.
    variable_help = ('Time varying: an expression of t (seconds) and p (beats), evaluated once per pose '
                     'and clipped to min–max. No s. Press Enter to apply; right-click › Static for the slider.')
    variable_placeholder = 'f(t, p)'

    def __init__(self, slider_class):
        super().__init__(slider_class, RecurrencePlot())
        help = self.findChild(QLabel, 'function_help')
        help.setText('n: step · t/p: pose')
        help.setToolTip('n: step · t/p: pose\nx[n-k], y[n-k], z[n-k]: history up to Order. N = Steps. No s. Variables use t/p only.')
        for axis, entry in self.entries.items():
            entry.setObjectName(f'recurrence_{axis}'); entry.setPlaceholderText(f'{axis} at step n')
        # Two rows (the side panel scrolls vertically now): Order and Steps, then Draw.
        controls = QWidget(); rows = QVBoxLayout(controls); rows.setContentsMargins(0, 0, 0, 0); rows.setSpacing(6)
        counts = QHBoxLayout(); counts.setSpacing(6); rows.addLayout(counts)
        self.order = QSpinBox(); self.order.setRange(1, 8); self.order.setObjectName('recurrence_order')
        self.steps = QSpinBox(); self.steps.setRange(16, 4096); self.steps.setObjectName('recurrence_steps')
        self.order.setToolTip('Order: how many earlier steps the equations may use (x[n-1] … x[n-Order])')
        self.steps.setToolTip('Steps: N points drawn per cycle')
        for caption, widget in (('Order', self.order), ('Steps', self.steps)):
            widget.setMinimumWidth(64); counts.addWidget(_caption(caption)); counts.addWidget(widget, 1)
        draw_row = QHBoxLayout(); draw_row.setSpacing(6); rows.addLayout(draw_row)
        self.draw = QComboBox(); self.draw.addItems(['Path', 'Points']); self.draw.setObjectName('recurrence_draw')
        self.draw.setToolTip('Draw: Path joins points; Points holds each point for 1/N of the cycle.')
        draw_row.addWidget(_caption('Draw')); draw_row.addWidget(self.draw, 1)
        self.initial = QTableWidget(1, 3); self.initial.setObjectName('recurrence_initial')
        self.initial.setHorizontalHeaderLabels(['x', 'y', 'z'])
        self.initial.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.initial.setToolTip('Initial values: steps 0 through Order − 1')
        self.initial.horizontalHeader().setFixedHeight(24)
        self.initial.verticalHeader().setDefaultSectionSize(26)
        self.layout().insertWidget(4, controls)
        self.layout().insertWidget(5, _caption('Initial values'))
        self.layout().insertWidget(6, self.initial)
        self.warning = _Message(); self.warning.setObjectName('recurrence_warning'); self.warning.hide()
        self.layout().addWidget(self.warning)
        self.order.valueChanged.connect(self.order_edited)
        self.steps.valueChanged.connect(lambda value: self.propose(replace(self.plot, steps=value)))
        self.draw.currentIndexChanged.connect(lambda index: self.propose(replace(self.plot, draw=('path', 'points')[index])))
        self.initial.cellChanged.connect(self.initial_edited)
        self.set_plot(self.plot, force=True)

    def add_variable(self):
        used = {v.name for v in self.plot.variables} | self.reserved
        name = next(n for n in (*'abcdfghjkmqruvw', *(f'v{i}' for i in range(1, 20))) if n not in used)
        self.propose(replace(self.plot, variables=(*self.plot.variables, Variable(name))), rebuild=True)

    def set_plot(self, plot, force=False):
        super().set_plot(plot, force)
        if not hasattr(self, 'initial'): return
        blockers = [QSignalBlocker(w) for w in (self.order, self.steps, self.draw, self.initial)]
        self.order.setValue(plot.order); self.steps.setValue(plot.steps)
        self.draw.setCurrentIndex(('path', 'points').index(plot.draw))
        self.initial.setRowCount(plot.order)
        self.initial.setVerticalHeaderLabels([str(i) for i in range(plot.order)])
        for i, row in enumerate(plot.initial):
            for j, value in enumerate(row): self.initial.setItem(i, j, QTableWidgetItem(f'{value:g}'))
        self.initial.setFixedHeight(self.initial.horizontalHeader().height() +
                                    sum(self.initial.rowHeight(i) for i in range(plot.order)) + 4)
        self.update_warning()

    def propose(self, plot, rebuild=False):
        if not super().propose(plot, rebuild): return False
        if hasattr(self, 'initial'): self.set_plot(plot, force=False)
        return True

    def order_edited(self, order):
        initial = (self.plot.initial + ((0., 0., 0.),)*order)[:order]
        if self.propose(replace(self.plot, order=order, initial=initial)):
            self.set_plot(self.plot, force=True)
        else:
            blocker = QSignalBlocker(self.order); self.order.setValue(self.plot.order)

    def initial_edited(self, row, column):
        try:
            initial = tuple(tuple(float(self.initial.item(i, j).text()) for j in range(3))
                            for i in range(self.plot.order))
        except ValueError:
            self.error.setText('Initial values must be finite numbers.'); return
        self.propose(replace(self.plot, initial=initial))

    def update_warning(self, seconds=0., tempo=120., plot=None):
        stopped = sequence(plot or self.plot, seconds, tempo)[1]
        self.warning.setText('' if stopped is None else f'Sequence stopped at step {stopped}')
