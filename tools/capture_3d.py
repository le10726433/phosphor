"""Capture a 6-shot live Metal-rendered visual verification suite of Phosphor's 3D Lab Bench.

Run from the app folder (the one with app.py):
    .venv/bin/python tools/capture_3d.py

Saves PNGs to .captures/ for direct visual inspection.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.pop('QT_QPA_PLATFORM', None)

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from phosphor.editor.qt_platform import configure_graphics
from phosphor.editor.qt_app import Window

OUT_DIR = ROOT / '.captures'


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    configure_graphics()
    app = QApplication([])
    opts = argparse.Namespace(scene=None, ready_file=None, workspace='Sandbox', skip_recovery=True)
    win = Window(opts)
    win.resize(1440, 900)
    win.show()

    def snap(name):
        img = win.desk3d_host.view.grabWindow()
        path = OUT_DIR / f'{name}.png'
        img.save(str(path))
        print(f'Saved {path} ({img.width()}x{img.height()})', flush=True)

    def step0():
        win.set_desk3d(True)
        win.desk3d_host.controller.toggleCameraFix()

    def step1():
        snap('01_desk_lamp')
        win.desk3d_host.controller.pressButton('btn_fluorescent')

    def step2():
        snap('02_desk_fluorescent')
        win.desk3d_host.controller.adjustZoom(108.0)

    def step3():
        snap('03_desk_zoomed_out')
        win.desk3d_host.controller.setCameraPreset(0)
        win.desk3d_host.controller.clickNotepad()

    def step4():
        snap('04_notepad_page1')
        win.desk3d_host.controller.closeNotepad()
        win.desk3d_host.controller.setCameraPreset(1)

    def step5():
        snap('05_scope_closeup')
        win.desk3d_host.controller.setCameraPreset(2)
        ctrl = win.desk3d_host.controller
        ctrl.grabCable(0); ctrl.dragCable(0, 120.0, 80.0); ctrl.releaseCable(0)
        ctrl.grabCable(1); ctrl.dragCable(1, 120.0, 80.0); ctrl.releaseCable(1)
        ctrl.cables[0].replug(1)
        ctrl.cables[1].replug(0)
        ctrl._cable_geoms[0].sync_mesh()
        ctrl._cable_geoms[1].sync_mesh()
        ctrl.physicsChanged.emit()

    def step6():
        snap('06_controls_swapped')
        win.force_close = True
        win.close()
        app.quit()

    steps = [step0, step1, step2, step3, step4, step5, step6]
    idx = [0]

    def run_next():
        if idx[0] < len(steps):
            steps[idx[0]]()
            idx[0] += 1
            QTimer.singleShot(900, run_next)

    QTimer.singleShot(600, run_next)
    app.exec()


if __name__ == '__main__':
    main()
