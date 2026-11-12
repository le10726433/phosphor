"""Function x(t), y(t) input: panel edits, scope signal, scenes. Native Qt or --headless."""
import _path  # noqa: F401  (makes the phosphor package importable)
import os
import sys
import tempfile
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from phosphor.editor import qt_app as q
from phosphor.engine.function_plot import FunctionPlot, PRESETS
q.os.environ['QSG_RENDER_LOOP'] = 'basic'
q.configure_graphics(); app = QApplication([])
w = q.Window(SimpleNamespace(scene=None, ready_file=None)); w.show(); QTest.qWait(100)
panel = w.function_panel


def enter(name, text):
    entry = panel.findChild(QLineEdit, name); entry.setText(text); entry.editingFinished.emit()


# New scenes default to Fully shaped audio, and start with the default plot.
assert w.scene.mode == 'drawing' and w.parameters['mode'].currentText() == 'Fully shaped audio'
assert w.scene.function == FunctionPlot() and w.monitor.count() == 4
assert panel.inactive.isVisibleTo(panel)
# Selecting the input opens the tab; the scene tab and handles are off.
w.monitor.setCurrentIndex(2); w.monitor_changed(2)
assert w.function_monitor and w.tabs.currentIndex() == 3 and not w.tabs.isTabEnabled(1) and w.tabs.isTabEnabled(2)
assert not panel.inactive.isVisibleTo(panel) and w.viewer_heading.text() == 'X–Y FUNCTION PLOT'
assert w.audio.function is w.function_compiled
w.resize(1000, 700); QTest.qWait(50)
assert w.control_scrolls[3].horizontalScrollBar().maximum() == 0
# The scope shows the function at the output level when stopped.
w.scene.change('drawing_gain', .5); w.sync()
times = np.arange(10)/48000
with patch.object(q, 'render', side_effect=AssertionError('Function input bypasses the scene')):
    w.tick()
factor = q.INTERPOLATION_FACTORS[w.preferences['trace_interpolation']]
signal = w.host.scope.signal[::factor]
assert abs(np.hypot(signal[:, 0], signal[:, 1]) - .5).max() < 1e-6  # Circle of radius 0.5.
# Typing expressions updates the scene; errors keep the last good plot.
enter('function_x', 'math.sin(tau*s) * 3')
assert w.scene.function.x == 'math.sin(tau*s) * 3' and w.scene.dirty and w.audio.function.plot == w.scene.function
w.tick(); assert panel.findChild(QLabel, 'function_clipping') is None
assert abs(w.host.scope.signal).max() <= 1
before = w.scene.function
enter('function_y', '__import__("os").system("echo unsafe")')
assert w.scene.function == before and 'not' in panel.error.text().lower(), panel.error.text()
enter('function_y', 'cos(tau*s')
assert w.scene.function == before and 'syntax' in panel.error.text()
# Variables: add, use, slide, rename (followed in expressions), and remove is refused while used.
add = next(b for b in panel.findChildren(QPushButton) if b.text() == 'Add variable')
QTest.mouseClick(add, Qt.MouseButton.LeftButton)
assert [v.name for v in w.scene.function.variables] == ['a']
enter('function_x', 'a*sin(tau*s)'); assert w.scene.function.x == 'a*sin(tau*s)'
panel.variable_rows[0]['value'].commit(.25)
assert w.scene.function.variables[0].value == .25 and w.audio.function.plot.variables[0].value == .25
row = panel.variable_rows[0]; row['name'].setText('amp'); row['name'].editingFinished.emit()
assert w.scene.function.x == 'amp*sin(tau*s)' and w.scene.function.variables[0].name == 'amp'
panel.remove_variable(0); assert w.scene.function.variables[0].name == 'amp' and 'still used' in panel.error.text()
# Presets load in full, and the plot saves and reopens with the scene.
panel.presets.setCurrentIndex(list(PRESETS).index('Spiral')+1); panel.presets.activated.emit(panel.presets.currentIndex())
assert w.scene.function == PRESETS['Spiral'] and panel.presets.currentIndex() == 0
assert panel.findChild(QLineEdit, 'function_x').text() == PRESETS['Spiral'].x
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder)/'function.scene.json'
    w.scene.save(path); assert not w.scene.dirty
    w.scene.function = FunctionPlot(); w.sync(); assert w.scene.dirty
    with patch.object(q.QFileDialog, 'getOpenFileName', return_value=(str(path), '')), \
         patch.object(q.QMessageBox, 'question', return_value=q.QMessageBox.StandardButton.Discard):
        w.open_scene()
    assert w.scene.function == PRESETS['Spiral'] and not w.scene.dirty and w.function_compiled.plot == PRESETS['Spiral']
    assert panel.findChild(QLineEdit, 'function_y').text() == PRESETS['Spiral'].y
    # Saved presets: the current equation joins the list, reloads, replaces and deletes.
    panel.reload_presets(Path(folder)/'function-presets.json')
    assert panel.presets.findText('★ Mine') < 0 and not panel.delete_button.isEnabled()
    panel.findChild(QLineEdit, 'function_x').setText('saw(s)*turns/20')  # Typed, Enter not pressed.
    assert panel.save_preset(name='Mine') and w.scene.function.x == 'saw(s)*turns/20'
    mine = w.scene.function
    index = panel.presets.findText('★ Mine'); assert index > len(PRESETS) and panel.delete_button.isEnabled()
    with patch.object(q.QMessageBox, 'warning') as warning:
        assert not panel.save_preset(name='Circle') and warning.called
    panel.presets.activated.emit(list(PRESETS).index('Circle')+1); assert w.scene.function == PRESETS['Circle']
    panel.presets.activated.emit(index); assert w.scene.function == mine
    enter('function_y', 'tri(s)')
    with patch('phosphor.editor.function_panel.QMessageBox.question', return_value=q.QMessageBox.StandardButton.Yes):
        assert panel.save_preset(name='Mine')
    assert panel.saved['Mine'].y == 'tri(s)' and panel.presets.count() == len(PRESETS)+3
    assert panel.delete_preset(name='Mine') and panel.presets.findText('★ Mine') < 0
    panel.presets.activated.emit(list(PRESETS).index('Spiral')+1)
# Opening a scene returns to the generated input.
assert not w.function_monitor and w.audio.function is None and w.tabs.isTabEnabled(1)
w.monitor_changed(2)
with patch.object(q.QMessageBox, 'information') as information:
    w.export(); assert information.called
w.resize(1220, 840); QTest.qWait(50)
(q.HERE/'.user').mkdir(exist_ok=True); w.grab().save(str(q.HERE/'.user/function-plot.png'))
w.timer.stop(); w.shutdown_audio(); w.release_views(); w.force_close = True; w.close(); app.processEvents()
print('Function x(t), y(t) input: panel, scope, variables, presets and scene files passed.')
