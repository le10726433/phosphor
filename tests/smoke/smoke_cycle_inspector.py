"""I1: View › Inspect one cycle, the Objects menu entry, scrubbing, Next visit, pin and compare, Composer."""
import _path  # noqa: F401  (makes the phosphor package importable)
import sys
import os
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import math
from types import SimpleNamespace

import numpy as np
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt

from _screen import exit_cleanly_on_failure
from phosphor.editor import qt_app as q
from phosphor.engine.signal_engine import render, scene_plan
from phosphor.engine import cycle_inspection as ci

exit_cleanly_on_failure()


def action(menu, text):
    found = [a for a in menu.actions() if a.text() == text]
    assert found, (text, [a.text() for a in menu.actions()])
    return found[0]


def check_fits(*where):
    QTest.qWait(40)
    scroll = w.panels[q.PANEL_INSPECT]
    assert w.current_panel == q.PANEL_INSPECT, w.current_panel
    assert scroll.horizontalScrollBar().maximum() == 0, (*where, 'horizontal')
    for label in scroll.findChildren(QLabel):
        if label.isVisible():
            needed = label.heightForWidth(label.width())
            assert needed <= label.height() + 1, (where, label.text(), needed, label.height())
    table = inspector.table
    assert table.horizontalScrollBar().maximum() == 0 and table.viewport().width() <= scroll.viewport().width(), where
    assert table.height() >= table.horizontalHeader().height() + sum(table.rowHeight(r) for r in range(table.rowCount())), where


q.configure_graphics(); app = QApplication([])
w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
w.resize(1000, 700); w.show(); QTest.qWait(150)
w.add_object('Cube'); w.add_object('Text'); w.edit('source', q.TONE_SOURCE); w.edit('tone_frequency', 220.)
w.scene.saved = w.scene.document()
inspector = w.inspector
times = np.arange(4800) / 48000
before = render(times, w.output_settings())[2]

# View › Inspect one cycle opens the panel and freezes the pose.
toggle = w.inspect_action
assert toggle.text() == 'Inspect one cycle' and toggle in sum((a.menu().actions() for a in w.menuBar().actions()
                                                               if a.text() == 'View'), [])
assert toggle.isCheckable() and not toggle.isChecked()
toggle.trigger(); QTest.qWait(120)
assert inspector.active and w.current_panel == q.PANEL_INSPECT and w.sidebar.isVisible()
i = inspector.inspection; assert i is not None, inspector.error
rate = w.audio.sample_rate
assert inspector.pitch.value() == 220. and i.count == math.ceil(rate / 220. - 1e-9), (inspector.pitch.value(), i.count, rate)
assert [s.shape for s in i.shares] == [0, 1] and inspector.table.rowCount() == 3, inspector.table.rowCount()
layers = w.host.scope.overlays
assert len(layers) >= 5 and all(len(points) for points, _ in layers[-4:]), [len(p) for p, _ in layers]
pose = i.pose_seconds
QTest.qWait(200); assert inspector.inspection is i and inspector.inspection.pose_seconds == pose  # Frozen.
for size in ((1000, 700), (1220, 840), (1600, 1000)):
    w.resize(*size); QTest.qWait(60); check_fits(*size)
w.resize(1000, 700); QTest.qWait(60)

# Scrub point by point with the arrow keys; the readout follows.
inspector.slider.setFocus(); QTest.qWait(20)
for _ in range(3): QTest.keyClick(inspector.slider, Qt.Key.Key_Right)
assert inspector.index == 3 and inspector.sample_box.value() == 3, inspector.index
assert inspector.info.text().startswith(f'{3 / rate * 1000:.3f} ms'), inspector.info.text()
inspector.slider.setValue(inspector.slider.maximum()); assert inspector.index == i.count - 1  # End is Transport's.
inspector.sample_box.setValue(40); assert inspector.slider.value() == 40
# Next visit: the cube's route retraces edges.
start = next(k for k in range(i.count) if ci.next_visit(i, k) is not None)
inspector.scrub(start); assert inspector.next_button.isEnabled()
QTest.mouseClick(inspector.next_button, Qt.MouseButton.LeftButton)
assert inspector.index != start and i.edge[i.sample_segment[inspector.index]] == i.edge[i.sample_segment[start]]
assert 'visit' in inspector.info.text(), inspector.info.text()
# Clicking the text's row jumps to its first drawn sample.
inspector.table.cellClicked.emit(1, 0); QTest.qWait(20)
assert 'Text' in inspector.info.text() and 'drawing' in inspector.info.text(), inspector.info.text()

# Pin, change travel, compare at the same pose; hide and clear the reference.
QTest.mouseClick(inspector.pin_button, Qt.MouseButton.LeftButton); QTest.qWait(60)
assert inspector.reference is i and 'up to 0.0000' in inspector.reference_info.text(), inspector.reference_info.text()
travel = (w.scene.settings.travel_mode, w.scene.settings.travel_speed)
w.edit('travel_mode', 'Faster travel'); w.edit('travel_speed', 8.); QTest.qWait(150)  # Slower than the 64× default.
assert inspector.inspection is not i and inspector.inspection.pose_seconds == pose
assert inspector.inspection.travel_share > i.travel_share and inspector.reference is i, (inspector.inspection.travel_share, i.travel_share)
assert 'up to 0.0000' not in inspector.reference_info.text(), inspector.reference_info.text()
reference = [len(p) for p, _ in w.host.scope.overlays][-5]; assert reference > 0
inspector.reference_box.setChecked(False); QTest.qWait(60)
assert len(w.host.scope.overlays[-5][0]) == 0
inspector.reference_box.setChecked(True); QTest.qWait(60)
plans = scene_plan.cache_info().currsize
inspector.pitch.setValue(440.); QTest.qWait(120)
assert inspector.inspection.count == math.ceil(rate / 440. - 1e-9)
assert 'Different pitch' in inspector.reference_info.text(), inspector.reference_info.text()
assert scene_plan.cache_info().currsize <= max(plans, 4) + 8  # Display frames only, never the inspector.
check_fits('pinned')
QTest.mouseClick(inspector.clear_button, Qt.MouseButton.LeftButton); QTest.qWait(60)
assert inspector.reference is None and not inspector.clear_button.isEnabled()

# The Objects list menu starts it for that object; View toggles it off. Audio never changed.
toggle.trigger(); QTest.qWait(60); assert not inspector.active and not toggle.isChecked()
assert len(w.host.scope.overlays) <= 5
w.edit('travel_mode', travel[0]); w.edit('travel_speed', travel[1])
assert np.array_equal(render(times, w.output_settings())[2], before)
action(w.build_object_menu(1), 'Inspect one cycle').trigger(); QTest.qWait(100)
assert inspector.active and w.scene.selected == 1 and toggle.isChecked()
(q.HERE / '.user').mkdir(exist_ok=True); w.grab().save(str(q.HERE / '.user/cycle-inspector.png'))

# Composer: the playhead's pose in clip time; switching workspace ends the inspection.
w.set_workspace(q.COMPOSER); QTest.qWait(80); assert not inspector.active
w.add_object('Cube'); w.store_clip_scene(); w.seek_song(.5); QTest.qWait(60)
action(w.build_object_menu(0), 'Inspect one cycle').trigger(); QTest.qWait(120)
assert inspector.active and inspector.inspection is not None, inspector.error
assert 'clip time' in inspector.pose_label.text() and abs(inspector.inspection.pose_seconds - w.playhead) < 1e-9 and w.playhead > 0, w.playhead
check_fits('composer')
w.project_dirty = False; w.sandbox_scene.saved = w.sandbox_scene.document()
w.timer.stop(); w.shutdown_audio(); w.force_close = True; w.release_views(); w.close(); app.processEvents()
print('Cycle inspector passed: View and Objects menus, frozen pose, scrub, Next visit, shares, pin and compare, Composer.')
