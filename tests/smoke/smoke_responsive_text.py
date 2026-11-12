"""Verify readable control layout and text editing on native Qt or --headless."""
import _path  # noqa: F401  (makes the phosphor package importable)
import os
import sys
if '--headless' in sys.argv:os.environ['QT_QPA_PLATFORM']='offscreen'
from types import SimpleNamespace
from PySide6.QtWidgets import QApplication,QLabel,QPushButton
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from phosphor.editor import qt_app as q
q.configure_graphics();app=QApplication([])
w=q.Window(SimpleNamespace(scene=None,ready_file=None));w.show();QTest.qWait(100)
# Native clicks go to the top child, so the text field must not cover either arrow.
from PySide6.QtWidgets import QDoubleSpinBox,QStyle,QStyleOptionSpinBox
probe=QDoubleSpinBox(w);probe.setRange(0,9);probe.setValue(4);probe.setGeometry(0,0,200,32);probe.show();QTest.qWait(30)
option=QStyleOptionSpinBox();probe.initStyleOption(option)
for control in (QStyle.SubControl.SC_SpinBoxUp,QStyle.SubControl.SC_SpinBoxDown):
    arrow=probe.style().subControlRect(QStyle.ComplexControl.CC_SpinBox,option,control,probe)
    assert arrow.isValid() and not probe.lineEdit().geometry().intersects(arrow),(control,arrow,probe.lineEdit().geometry())
probe.close();probe.deleteLater()
# An unwritable temp folder must degrade the arrows, never block startup.
working=w._arrow_directory;w._arrow_directory=SimpleNamespace(filePath=lambda name:str(q.HERE/'.missing-arrow-folder'/name));w._styled_palette=None
w.apply_preferences(w.preferences);assert 'image:url' not in w.styleSheet() and 'up-arrow' in w.styleSheet()
w._arrow_directory=working;w._styled_palette=None;w.apply_preferences(w.preferences);assert 'image:url' in w.styleSheet()
for width,height in ((1000,700),(1220,840),(1600,1000)):
    w.resize(width,height);QTest.qWait(50)
    for tab in range(3):
        w.tabs.setCurrentIndex(tab)
        nested=w.input_sections if tab==0 else w.scene_sections if tab==1 else None
        for section in range(nested.count() if nested else 1):
            if nested:nested.setCurrentIndex(section)
            QTest.qWait(30)
            scroll=w.control_scrolls[tab]
            assert scroll.horizontalScrollBar().maximum()==0,(width,height,tab,section,'horizontal')
            assert scroll.verticalScrollBar().maximum()==0,(width,height,tab,section,'vertical',scroll.verticalScrollBar().maximum())
            for label in scroll.findChildren(QLabel):
                if label.isVisible():
                    needed=label.heightForWidth(label.width())
                    assert needed<=label.height()+1,(label.text(),needed,label.height())
w.tabs.setCurrentIndex(1);w.scene_sections.setCurrentIndex(0)
add=next(b for b in w.findChildren(QPushButton) if b.text()=='Add text')
QTest.mouseClick(add,Qt.MouseButton.LeftButton)
assert w.scene.shape.solid=='Text' and not w.scene.shape.spin_z
assert w.text_panel.isVisible()
w.text_entry.setText('HELLO 2026!');QTest.keyClick(w.text_entry,Qt.Key.Key_Return)
assert w.scene.shape.text=='HELLO 2026!' and w.scene.dirty
assert 'HELLO 2026!' in w.shape_list.currentItem().text()
w.resize(1000,700);QTest.qWait(50)
assert w.control_scrolls[1].verticalScrollBar().maximum()==0
for width in (340,520):
    w.resize(1220,840);w.splitter.setSizes([1200-width,width]);QTest.qWait(40)
    assert w.control_scrolls[1].horizontalScrollBar().maximum()==0
w.tabs.setCurrentIndex(0);w.monitor.setCurrentIndex(1);w.monitor_changed(1);QTest.qWait(50)
assert w.control_scrolls[0].horizontalScrollBar().maximum()==0
assert w.control_scrolls[0].verticalScrollBar().maximum()==0
w.monitor.setCurrentIndex(0);w.monitor_changed(0);w.tabs.setCurrentIndex(1)
w.shape_action('duplicate');w.text_entry.setText('SECOND');QTest.keyClick(w.text_entry,Qt.Key.Key_Return)
assert w.scene.settings.shapes[-2].text=='HELLO 2026!'
w.select_shape(0);assert not w.text_panel.isVisible()
w.resize(1220,840);w.select_shape(1);QTest.qWait(50)
from pathlib import Path
(q.HERE/'.user').mkdir(exist_ok=True);w.grab().save(str(q.HERE/'.user/responsive-text-controls.png'))
w.timer.stop();w.shutdown_audio();w.force_close=True;w.close();app.processEvents()
print('Responsive controls and text object integration passed.')
