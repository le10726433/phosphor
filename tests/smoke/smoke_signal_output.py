"""Native/--headless redraw preferences Apply/Cancel/Defaults and output routing."""
import _path  # noqa: F401  (makes the phosphor package importable)
import os,sys,tempfile
from dataclasses import replace
if '--headless' in sys.argv:os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QDialogButtonBox
from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
from phosphor.editor import qt_app as q
from phosphor.engine.custom_shape import Drawing,Curve,CUSTOM_OBJECT
q.configure_graphics();app=QApplication([]);w=q.Window(SimpleNamespace(scene=None,ready_file=None));w.show();QTest.qWait(50)
w.scene.add();w.sync();before=w.scene.document();dirty=w.scene.dirty
w.apply_signal_preferences(q.DEFAULT_SIGNAL_PREFERENCES)
with tempfile.TemporaryDirectory() as folder:
    prefs=Path(folder)/'signal.json'
    with patch.object(q,'SIGNAL_PREFERENCES_PATH',prefs):
        w.show_signal_preferences();d=w.signal_dialog;QTest.qWait(30)
        enabled=d.findChild(q.QCheckBox,'shape_redraw');count=d.findChild(q.QSpinBox,'shape_redraw_count')
        assert not enabled.isChecked() and not count.isEnabled() and count.value()==1
        QTest.mouseClick(enabled,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,QPoint(8,enabled.height()//2));count.setValue(3)
        assert count.isEnabled() and w.output_settings().shape_redraw_count==3
        assert w.audio.parameters[0].shape_redraw_count==3
        assert w.scene.document()==before and w.scene.dirty==dirty
        buttons=d.findChild(QDialogButtonBox)
        QTest.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Apply),Qt.MouseButton.LeftButton)
        assert q.read_signal_preferences(prefs)['shape_redraw_count']==3
        count.setValue(5);d.reject();assert w.output_settings().shape_redraw_count==3
        w.show_signal_preferences();d=w.signal_dialog;buttons=d.findChild(QDialogButtonBox)
        QTest.mouseClick(buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults),Qt.MouseButton.LeftButton)
        assert w.output_settings().shape_redraw_count==1 and not w.signal_preferences['shape_redraw']
        d.reject();assert w.output_settings().shape_redraw_count==3
w.apply_signal_preferences(dict(corner_dwell_us=0.,shape_redraw=True,shape_redraw_count=32))
complex=Drawing('Large circle',((0,0,0),(1,100,0)),(),(Curve('Circle',(0,1)),))
large=q.Shape(solid=CUSTOM_OBJECT,custom=complex)
w.scene.settings=replace(w.scene.settings,shapes=(large,large))
assert w.output_settings().shape_redraw_count==1 and 'Redraw inactive' in w.redraw_notice
w.timer.stop();w.shutdown_audio();w.force_close=True;w.close();app.processEvents()
print('Signal output redraw passed: enable/integer, effective playback settings, Apply/Cancel/Defaults, persistence and scene independence.')
