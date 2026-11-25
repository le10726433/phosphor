"""Asynchronous, debounced preview of an optional mesh simplification, with progress."""
import threading

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal, Slot, Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                               QLabel, QMessageBox, QProgressBar, QProgressDialog, QSpinBox,
                               QVBoxLayout)

from phosphor.engine import mesh_formats, mesh_simplify


def tradeoff_notes(report):
    """What a simplification gave up to reach its target, for the preview and the message."""
    notes = []
    if report.boundary_collapses:
        notes.append(f'{report.boundary_collapses:,} vertices on the model\'s open edges were '
                     'merged to reach the target, so its outlines lost detail.')
    if report.topology_changed:
        holes = (f'about {report.handles_closed:,} holes or handles were' if report.handles_closed
                 else 'some holes or gaps were')
        notes.append(f'With its shape intact this model only goes down to '
                     f'{report.floor_triangles:,} triangles, so {holes} closed to reach the target.')
    return notes


def run_with_progress(parent, text, work, title='Import 3D model'):
    """Run work() on a worker thread behind a busy progress dialog; return its result or
    raise its exception. The GUI keeps painting meanwhile."""
    box = QProgressDialog(text, '', 0, 0, parent)
    box.setWindowTitle(title)
    box.setCancelButton(None)
    box.setWindowModality(Qt.WindowModality.WindowModal)
    box.setMinimumDuration(300)
    box.setAutoClose(False)
    result, done = {}, threading.Event()

    def run():
        try:
            result['value'] = work()
        except BaseException as error:  # Re-raised on the GUI thread below.
            result['error'] = error
        finally:
            done.set()

    threading.Thread(target=run, name='model-import-read', daemon=True).start()
    try:
        while not done.wait(0.01):
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 40)
    finally:
        box.close()
        box.deleteLater()
    if 'error' in result:
        raise result['error']
    return result['value']


class _Results(QObject):
    done = Signal(int, object)


class ModelImportDialog(QDialog):
    preview_done = Signal()

    def __init__(self, parent, mesh: mesh_formats.MeshSource, source_triangles: int):
        super().__init__(parent)
        self.setWindowTitle('Import 3D model')
        self.mesh = mesh
        self._source_triangles = source_triangles
        self._large = source_triangles > mesh_formats.SUGGESTED_TRIANGLES
        self._generation = 0
        self._result = None
        self._closed = False
        self._running = self._pending = False
        self._progress = None
        self._signals = _Results(self)
        self._signals.done.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(mesh.name))
        layout.addWidget(QLabel(f'Source: {source_triangles:,} triangles'))
        axis_row = QHBoxLayout()
        axis_row.addWidget(QLabel('Up axis'))
        self.up_axis = QComboBox()
        self.up_axis.addItems(['Y', 'Z'])
        self.up_axis.setCurrentText(mesh.up_axis)
        axis_row.addWidget(self.up_axis)
        layout.addLayout(axis_row)
        self.animation = QComboBox()
        self.animation.addItem('None (static)', None)
        source = mesh.animation_source
        if source is not None:
            for i in source.picker:
                info = source.animations[i]
                self.animation.addItem(f'{info.name} — {info.duration:.2f} s — {info.key_count} keys', i)
            if source.picker:
                longest = max(source.picker, key=lambda i: source.animations[i].duration)
                self.animation.setCurrentIndex(self.animation.findData(longest))
        self.animation.setMinimumWidth(0)
        self.animation.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.in_place = QCheckBox('In place'); self.in_place.setChecked(True)
        if source is not None:
            layout.addWidget(QLabel('Animation')); layout.addWidget(self.animation)
            layout.addWidget(self.in_place)
        if self._large:
            hint = QLabel(f'This model is large, so it is simplified to '
                          f'{mesh_formats.SUGGESTED_TRIANGLES:,} triangles unless you change it.')
            hint.setWordWrap(True)
            layout.addWidget(hint)
        row = QHBoxLayout()
        self.checkbox = QCheckBox('Simplify to')
        self.spin = QSpinBox()
        self.spin.setRange(4, max(4, source_triangles-1))
        if self._large:
            self.spin.setValue(mesh_formats.SUGGESTED_TRIANGLES)
            self.checkbox.setChecked(True)
        else:
            self.spin.setValue(max(4, min(source_triangles-1, source_triangles//4)))
        self.spin.setGroupSeparatorShown(True)
        self.spin.setEnabled(self.checkbox.isChecked())
        row.addWidget(self.checkbox)
        row.addWidget(self.spin)
        row.addWidget(QLabel('triangles'))
        layout.addLayout(row)
        if source_triangles <= 4:
            self.checkbox.setEnabled(False)
            layout.addWidget(QLabel('Too few triangles to simplify.'))
        else:
            mesh_simplify.warm_up()
        self.preview = QLabel('Computing…')
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                        QDialogButtonBox.StandardButton.Cancel)
        self.import_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.import_button.setText('Import')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self._start)
        self.poll = QTimer(self)  # Progress of the running worker.
        self.poll.setInterval(50)
        self.poll.timeout.connect(self._show_progress)
        self.checkbox.toggled.connect(self._toggled)
        self.spin.valueChanged.connect(self._changed)
        self.up_axis.currentTextChanged.connect(self._changed)
        self.animation.currentIndexChanged.connect(self._changed)
        self.in_place.toggled.connect(self._changed)
        self.finished.connect(self._close_preview)
        self._changed()

    def target(self):
        return self.spin.value() if self.checkbox.isChecked() else None

    def ready_result(self):
        if self._result is not None and self._result[0] == self.target():
            return self._result[1:]
        return None

    def _toggled(self, checked):
        if not checked and self._large and not self._confirm_full():
            self.checkbox.blockSignals(True)
            self.checkbox.setChecked(True)
            self.checkbox.blockSignals(False)
            return
        self._changed()

    def _confirm_full(self):
        answer = QMessageBox.question(
            self, 'Import full model',
            f'This model has {self._source_triangles:,} triangles. Without simplifying, it '
            'takes longer to import, makes the editor slower and traces dimly. '
            'Import the full model anyway?')
        return answer == QMessageBox.StandardButton.Yes

    def _changed(self, *args):
        self._generation += 1
        self._result = None
        self.spin.setEnabled(self.checkbox.isChecked())
        self.import_button.setEnabled(False)
        self.preview.setText('Computing…')
        self.progress.setRange(0, 0)
        self.progress.show()
        self.timer.start()

    def _start(self):
        if self._running:  # One worker at a time; the latest settings run next.
            self._pending = True
            return
        self._running, self._pending = True, False
        generation, target = self._generation, self.target()
        up_axis = self.up_axis.currentText()
        animation, in_place = self.animation.currentData(), self.in_place.isChecked()
        mesh, signals = self.mesh, self._signals
        progress = self._progress = mesh_simplify.Progress()

        def work():
            try:
                model, report = mesh_formats.model_from_source(mesh, target, progress, up_axis=up_axis,
                                                              animation=animation, in_place=in_place)
                result = (target, model, report)
            except Exception as error:
                result = error
            # The dialog may have been deleted while the worker was running.
            try:
                signals.done.emit(generation, result)
            except RuntimeError:
                pass

        threading.Thread(target=work, name='model-import-preview', daemon=True).start()
        self.poll.start()

    def _show_progress(self):
        progress = self._progress
        if progress is None or self._generation_running_is_stale():
            return
        fraction = progress.fraction()
        if progress.stage:
            self.preview.setText(progress.stage)
        if fraction is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(fraction*1000))

    def _generation_running_is_stale(self):
        # A superseded worker is still running: keep the "Computing…" text it would replace.
        return self._pending or self.timer.isActive()

    @Slot(int, object)
    def _finished(self, generation, result):
        self._running = False
        self.poll.stop()
        if self._closed:
            return
        if generation != self._generation:
            if self._pending:
                self._start()
            return
        self.progress.hide()
        if isinstance(result, Exception):
            self.preview.setText(str(result))
        else:
            self._result = result
            target, model, report = result
            count = report.result_triangles if report.simplified else self._source_triangles
            text = f'Result: {count:,} triangles, {len(model.edges):,} feature edges'
            if model.animation is not None:
                text += f'\nEmbedded animation: {len(model.animation.data)*4/3/1024:.1f} KiB, {model.animation.count} frames at 30 fps'
            notes = tradeoff_notes(report) if report.simplified else []
            self.preview.setText('\n\n'.join([text]+notes))
            self.import_button.setEnabled(True)
        self.preview_done.emit()

    def _close_preview(self, *args):
        self._closed = True
        self._generation += 1
        self.timer.stop()
        self.poll.stop()
