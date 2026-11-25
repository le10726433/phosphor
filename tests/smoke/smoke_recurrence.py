import _path  # noqa: F401
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from phosphor.editor import qt_app as q
from _screen import check_label_fits
from phosphor.engine.recurrence import RecurrencePlot, RECURRENCE_OBJECT, PRESETS
from phosphor.engine.function_plot import Variable
from phosphor.engine.signal_engine import render, select_signal
from phosphor.storage.app_files import scene_document, scene_from_document
from phosphor.storage.recurrence_presets import read_recurrence_presets


class FramePreview(q.AudioPreview):
    """Exercise Play and real render blocks without opening an audio device."""
    @property
    def running(self): return getattr(self, 'playing', False)

    def start(self, settings, mode):
        self.parameters = settings, mode
        self.playing = True; self.frame = 0; self.live = False

    def stop(self): self.playing = False

    def playback_snapshot(self, count):
        settings, mode = self.parameters
        times = (self.frame+np.arange(count))/settings.sample_rate
        signal = select_signal(*render(times, settings), settings, mode)
        self.frame += count
        return signal, self.frame


q.os.environ['QSG_RENDER_LOOP'] = 'basic'
q.configure_graphics(); app = QApplication([])
# Patch module-level names; never replace a method on a Qt class.
with patch.object(q, 'AudioPreview', FramePreview), patch.object(q, 'settle_garbage', lambda: None):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
    try:
        w.show(); w.show_panel(q.PANEL_SCENE); QTest.qWait(100)
        action = next(a for a in w.add_button.menu().actions() if a.text() == RECURRENCE_OBJECT)
        action.trigger(); panel = w.recurrence_panel
        assert w.scene.shape.solid == RECURRENCE_OBJECT
        assert w.scene.shape.recurrence == RecurrencePlot()
        assert panel.isVisibleTo(w.sidebar) and not w.function_panel.isVisibleTo(w.sidebar)
        assert w.shape_list.item(0).text().startswith('01  Recurrence: x =')
        # Panels may scroll vertically (0.53.1); they must never need to scroll sideways.
        w.resize(1000, 700); QTest.qWait(50)
        assert panel.initial.rowCount() == 1
        help = panel.findChild(q.QLabel, 'function_help')  # UI1: its own row, never clipped.
        for width in (340, 520):
            w.splitter.setSizes([1000 - width, width]); QTest.qWait(40)
            check_label_fits(help, 'recurrence help', width)
        assert help.y() >= panel.add_button.geometry().bottom() and help.toolTip().startswith(help.text())
        assert w.panels[q.PANEL_SCENE].horizontalScrollBar().maximum() == 0
        w.edit('source', 'Single tone'); QTest.qWait(30)
        panel.order.setValue(2); assert panel.initial.rowCount() == 2
        assert len(w.scene.shape.recurrence.initial) == 2
        panel.initial.item(1, 0).setText('0.25')
        assert w.scene.shape.recurrence.initial[1][0] == .25
        panel.entries['x'].setText('x[n-2]+0.01'); panel.entries['x'].editingFinished.emit()
        assert w.scene.shape.recurrence.x == 'x[n-2]+0.01'
        before = w.scene.shape.recurrence
        panel.entries['y'].setText('s'); panel.entries['y'].editingFinished.emit()
        assert w.scene.shape.recurrence == before and 'unknown name' in panel.error.text()
        panel.entries['y'].setText(before.y); panel.entries['y'].editingFinished.emit()
        panel.order.setValue(1)  # Cannot drop history used in x.
        assert w.scene.shape.recurrence.order == 2 and panel.order.value() == 2
        for name, plot in PRESETS.items():
            panel.presets.activated.emit(panel.presets.findText(name))
            assert w.scene.shape.recurrence == plot
            assert panel.initial.rowCount() == plot.order
            assert panel.entries['z'].text() == plot.z
            w.play(); assert w.audio.running
            for _ in range(3): w.tick(); app.processEvents()
            assert np.isfinite(w.host.scope.signal).all()
            w.play(); assert not w.audio.running
        panel.propose(RecurrencePlot('1/0', '0'))
        assert panel.warning.text() == 'Sequence stopped at step 1' and not panel.error.text()
        panel.propose(RecurrencePlot('a', '0', variables=(Variable('a', .5, 0., 1.),)), rebuild=True)
        panel.variable_rows[0]['value'].slider.commit(.25)
        assert w.scene.shape.recurrence.variables[0].value == .25
        document = scene_document(w.scene.settings, w.scene.mode, '')
        assert scene_from_document(document)[0] == w.scene.settings
        # Captions and the Draw box are never cut off (macOS once showed "Orde" and "Step").
        from PySide6.QtWidgets import QLabel
        for label in panel.findChildren(QLabel):
            if label.isVisibleTo(w.sidebar) and label.property('inlineCaption'):
                assert label.width() >= label.sizeHint().width(), (label.text(), label.width(), label.sizeHint().width())
        assert panel.draw.width() >= panel.draw.sizeHint().width(), (panel.draw.width(), panel.draw.sizeHint().width())
        # Saved presets: Save and Delete are shown and use their own file.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'recurrence-presets.json'
            panel.reload_presets(path)
            assert panel.save_button.isVisibleTo(w.sidebar) and not panel.delete_button.isEnabled()
            # propose() above left the x/y boxes showing the earlier preset; Save would apply
            # that text as a typed edit (as the parametric panel does), so show the plot first.
            mine = w.scene.shape.recurrence; panel.set_plot(mine, force=True)
            assert panel.save_preset(name='Mine') and read_recurrence_presets(path) == {'Mine': mine}
            index = panel.presets.findText('★ Mine'); assert index > len(PRESETS) and panel.delete_button.isEnabled()
            with patch.object(q.QMessageBox, 'warning') as warning:
                assert not panel.save_preset(name=next(iter(PRESETS))) and warning.called  # Built-in names are reserved.
            panel.presets.activated.emit(panel.presets.findText(next(iter(PRESETS))))
            assert w.scene.shape.recurrence == PRESETS[next(iter(PRESETS))]
            panel.presets.activated.emit(panel.presets.findText('★ Mine')); assert w.scene.shape.recurrence == mine
            assert panel.delete_preset(name='Mine') and read_recurrence_presets(path) == {}
            assert panel.presets.findText('★ Mine') < 0 and not panel.delete_button.isEnabled()
        w.add_object('Cube'); assert not panel.isVisibleTo(w.sidebar)
        w.select_shape(0); assert panel.isVisibleTo(w.sidebar)
        panel.propose(RecurrencePlot())
        QTest.qWait(30)
        assert panel.initial.rowCount() == 1
        assert w.panels[q.PANEL_SCENE].horizontalScrollBar().maximum() == 0
    finally:
        w.timer.stop(); w.shutdown_audio(); w.release_views()
        w.force_close = True; w.close(); app.processEvents()

print('Recurrence: Add, layout without clipped captions, saved presets, history table, presets, warnings, variables, scenes and synthetic playback passed.')
