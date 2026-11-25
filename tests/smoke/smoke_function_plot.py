"""Parametric equation objects: Add menu, equation editor, scope signal, presets and scenes. Native Qt or --headless."""
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
from _screen import check_label_fits
from phosphor.engine.transforms import Param
from phosphor.engine.function_plot import FunctionPlot, PRESETS, PARAMETRIC_OBJECT
q.os.environ['QSG_RENDER_LOOP'] = 'basic'
q.configure_graphics(); app = QApplication([])
w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox')); w.show(); QTest.qWait(100)
panel = w.function_panel


def enter(name, text):
    entry = panel.findChild(QLineEdit, name); entry.setText(text); entry.editingFinished.emit()


def plot(): return w.scene.shape.plot


# New scenes are empty, default to Fully shaped audio and have no function monitor input.
assert w.scene.settings.shapes == () and w.scene.mode == 'drawing'
assert w.parameters['mode'].currentText() == 'Fully shaped audio'
assert [w.monitor.itemText(i) for i in range(w.monitor.count())][1:] and w.monitor.count() == 3
assert all('Function' not in w.monitor.itemText(i) for i in range(w.monitor.count()))
# The Add menu places a parametric object; its editor appears in SCENE › Objects.
w.show_panel(q.PANEL_SCENE)
action = next(a for a in w.add_button.menu().actions() if a.text() == 'Parametric equation'); action.trigger()
assert w.scene.shape.solid == PARAMETRIC_OBJECT and plot() == FunctionPlot()
assert panel.isVisibleTo(w.sidebar) and not w.text_panel.isVisibleTo(w.sidebar)
assert w.shape_list.item(0).text().startswith('01  Equation:')
w.resize(1000, 700); QTest.qWait(50)
assert w.panels[q.PANEL_SCENE].horizontalScrollBar().maximum() == 0  # Vertical scrolling is allowed.
# UI1: the help line has its own word-wrapped row under Add variable, never clipped.
help = panel.findChild(QLabel, 'function_help')
for width in (340, 520):
    w.splitter.setSizes([1000 - width, width]); QTest.qWait(40)
    check_label_fits(help, 'function help', width)
    assert help.y() >= panel.add_button.geometry().bottom() and help.toolTip().startswith(help.text())
# On a single tone at instant travel, the scope shows the old function plot: a circle at the output level.
w.edit('source', 'Single tone'); w.edit('travel_mode', 'Faster travel'); w.edit('travel_speed', q.INSTANT_TRAVEL)
assert not w.tone_panel.isHidden() and w.tempo_panel.isHidden()
assert w.tone_panel.isVisibleTo(w.sidebar) and w.panels[q.PANEL_SCENE].horizontalScrollBar().maximum() == 0  # No sideways scroll beside the equation editor.
w.scene.change('drawing_gain', .5); w.sync(); w.tick()
factor = q.INTERPOLATION_FACTORS[w.preferences['trace_interpolation']]
signal = w.host.scope.signal[::factor]
assert abs(np.hypot(signal[:, 0], signal[:, 1]) - .5).max() < 1e-6  # Circle of radius 0.5.
# Typing expressions updates the object; errors keep the last good equations.
enter('function_x', 'math.sin(tau*s) * 3')
assert plot().x == 'math.sin(tau*s) * 3' and w.scene.dirty
w.tick(); assert panel.findChild(QLabel, 'function_clipping') is None
assert abs(w.host.scope.signal).max() <= 1
before = plot()
enter('function_y', '__import__("os").system("echo unsafe")')
assert plot() == before and 'not' in panel.error.text().lower(), panel.error.text()
enter('function_y', 'cos(tau*s')
assert plot() == before and 'syntax' in panel.error.text()
enter('function_z', 'sin(')
assert plot() == before and panel.error.text().startswith('z:')
enter('function_z', '0.4*sin(tau*s)')
assert plot().z == '0.4*sin(tau*s)' and not panel.error.text()
assert '3D' in w.shape_list.item(0).text()
# Variables: add, use, slide, rename (followed in expressions), and remove is refused while used.
add = next(b for b in panel.findChildren(QPushButton) if b.text() == 'Add variable')
QTest.mouseClick(add, Qt.MouseButton.LeftButton)
assert [v.name for v in plot().variables] == ['a']
enter('function_x', 'a*sin(tau*s)'); assert plot().x == 'a*sin(tau*s)'
enter('function_z', 'a*cos(tau*s)')
panel.variable_rows[0]['value'].slider.commit(.25)
assert plot().variables[0].value == .25 and w.output_settings().shapes[0].plot.variables[0].value == .25
# Right-click › Time varying drives the variable from t and s; Static returns to the slider value.
field = panel.variable_rows[0]['value']; field.make_varying(); QTest.qWait(20)
field = panel.variable_rows[0]['value']; assert plot().variables[0].expression == '0.25' and field.expression.isVisible()
field.expression.setText('0.5 + 0.5*sin(tau*t)'); QTest.keyClick(field.expression, Qt.Key.Key_Return)
assert plot().variables[0].expression == '0.5 + 0.5*sin(tau*t)' and w.scene.shape.plot.variables[0].expression
field.expression.setText('x*2'); QTest.keyClick(field.expression, Qt.Key.Key_Return)
assert plot().variables[0].expression == '0.5 + 0.5*sin(tau*t)' and 'x' in panel.error.text()  # Only t and s.
field.changed.emit(Param(.25)); assert plot().variables[0].expression == '' and plot().variables[0].value == .25
row = panel.variable_rows[0]; row['name'].setText('amp'); row['name'].editingFinished.emit()
assert plot().x == 'amp*sin(tau*s)' and plot().variables[0].name == 'amp'
assert plot().z == 'amp*cos(tau*s)' and panel.entries['z'].text() == plot().z
panel.remove_variable(0); assert plot().variables[0].name == 'amp' and 'still used' in panel.error.text()
# A second object keeps its own equations; the transforms apply to the chosen object.
w.add_object('Cube'); assert not panel.isVisibleTo(w.sidebar)
w.select_shape(0); assert panel.isVisibleTo(w.sidebar) and panel.entries['x'].text() == 'amp*sin(tau*s)', (panel.isVisibleTo(w.sidebar), panel.entries['x'].text(), w.scene.selected)
# Presets load in full, and the object saves and reopens with the scene.
panel.presets.setCurrentIndex(list(PRESETS).index('Spiral')+1); panel.presets.activated.emit(panel.presets.currentIndex())
assert plot() == PRESETS['Spiral'] and panel.presets.currentIndex() == 0
assert panel.findChild(QLineEdit, 'function_x').text() == PRESETS['Spiral'].x
for name in ('Helix', 'Trefoil knot', 'Toroidal spiral'):
    panel.presets.activated.emit(list(PRESETS).index(name)+1)
    assert plot() == PRESETS[name] and panel.entries['z'].text() == PRESETS[name].z
    w.tick(); assert np.isfinite(w.host.scope.signal).all()
panel.presets.activated.emit(list(PRESETS).index('Spiral')+1)
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder)/'function.scene.json'
    w.scene.save(path); assert not w.scene.dirty
    w.plot_changed(FunctionPlot()); assert w.scene.dirty
    with patch.object(q.QFileDialog, 'getOpenFileName', return_value=(str(path), '')), \
         patch.object(q.QMessageBox, 'question', return_value=q.QMessageBox.StandardButton.Discard):
        w.open_scene()
    assert plot() == PRESETS['Spiral'] and not w.scene.dirty and w.scene.settings.source == 'Single tone'
    assert panel.findChild(QLineEdit, 'function_y').text() == PRESETS['Spiral'].y
    # Saved presets: the current equation joins the list, reloads, replaces and deletes.
    panel.reload_presets(Path(folder)/'function-presets.json')
    assert panel.presets.findText('★ Mine') < 0 and not panel.delete_button.isEnabled()
    panel.findChild(QLineEdit, 'function_x').setText('saw(s)*turns/20')  # Typed, Enter not pressed.
    panel.entries['z'].setText('0.3*cos(tau*s)')
    assert panel.save_preset(name='Mine') and plot().x == 'saw(s)*turns/20'
    mine = plot()
    assert mine.z == '0.3*cos(tau*s)'
    index = panel.presets.findText('★ Mine'); assert index > len(PRESETS) and panel.delete_button.isEnabled()
    with patch.object(q.QMessageBox, 'warning') as warning:
        assert not panel.save_preset(name='Circle') and warning.called
    panel.presets.activated.emit(list(PRESETS).index('Circle')+1); assert plot() == PRESETS['Circle']
    panel.presets.activated.emit(index); assert plot() == mine
    enter('function_y', 'tri(s)')
    with patch('phosphor.editor.function_panel.QMessageBox.question', return_value=q.QMessageBox.StandardButton.Yes):
        assert panel.save_preset(name='Mine')
    assert panel.saved['Mine'].y == 'tri(s)' and panel.presets.count() == len(PRESETS)+3
    assert panel.delete_preset(name='Mine') and panel.presets.findText('★ Mine') < 0
# Removing the last objects leaves an empty scene that still plays its source.
while w.scene.settings.shapes: w.shape_action('remove')
assert w.scene.selected == -1 and not w.scene_sections.isTabEnabled(1) and w.empty_note.isVisibleTo(w.sidebar)
w.tick()
w.resize(1220, 840); QTest.qWait(50)
(q.HERE/'.user').mkdir(exist_ok=True); w.grab().save(str(q.HERE/'.user/function-plot.png'))
w.timer.stop(); w.shutdown_audio(); w.release_views(); w.force_close = True; w.close(); app.processEvents()
print('Parametric equation objects: Add menu, editor, scope, variables, presets and scene files passed.')
