"""Small native Qt Quick benchmark. app.py now launches the full Qt editor."""
import _path  # noqa: F401  (makes the phosphor package importable)
import argparse
import ctypes
from collections import deque
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent

import numpy as np
from phosphor.engine.signal_engine import Settings, render, select_signal
from phosphor.engine.solids import SOLIDS
from phosphor.audio.score_source import SOURCES
from phosphor.editor.qt_platform import configure_graphics, graphics_api
from phosphor.audio.audio_preview import AudioPreview
from phosphor.storage.app_files import read_scene, read_preferences
from phosphor.storage.app_paths import ASSETS_DIR
from phosphor.display.crt_theme import PALETTES
from phosphor.display.qt_geometry import stroke_triangles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', type=Path)
    parser.add_argument('--seconds', type=float, help='Timed smoke/benchmark run, then exit.')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--screenshot', type=Path)
    parser.add_argument('--play', action='store_true')
    parser.add_argument('--no-glow', action='store_true')
    parser.add_argument('--glow', action='store_true')
    parser.add_argument('--exercise', action='store_true', help='Exercise resize, palette and geometry changes.')
    args = parser.parse_args()
    # Python scene graph callbacks stay on the GUI thread; D3D11 still draws on GPU.
    os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
    os.environ.setdefault('QSG_INFO', '1')
    try:
        from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QUrl, Qt
        from PySide6.QtGui import QColor, QFont, QFontDatabase, QSurfaceFormat
        from PySide6.QtWidgets import QApplication, QFileDialog
        from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
        from PySide6.QtQuickControls2 import QQuickStyle
        from PySide6.QtQuick import (QQuickItem, QQuickWindow, QSGRendererInterface,
                                    QSGNode, QSGGeometryNode, QSGGeometry, QSGFlatColorMaterial)
    except ImportError as error:
        raise SystemExit('Install optional dependencies: python -m pip install -r requirements/requirements-qt.txt') from error

    from phosphor.display.qt_scope import GpuScope as Scope

    class Controller(QObject):
        changed = Signal()

        def __init__(self):
            super().__init__()
            self.settings = Settings()
            self.mode = 'mixed'
            self.device = ''
            self.preferences = read_preferences()
            self.palette_name = self.preferences['palette']
            self.glow = (self.preferences['crt_glow'] or args.glow) and not args.no_glow
            self.audio = AudioPreview()
            self.scope = None
            self.started = time.perf_counter()
            self.elapsed = 0.
            self.prep_ms, self.frame_times = deque(maxlen=4096), deque(maxlen=4096)
            self.frame_count = 0
            self.last_status = 0.
            self._status = 'SIMULATION — audio stopped'
            self._backend = graphics_api().name+' — initializing'
            self.adapter = ''
            self.failure = ''
            if args.scene:
                self.settings, self.mode, self.device = read_scene(args.scene)

        @Property('QVariantList', constant=True)
        def solids(self): return list(SOLIDS)
        @Property('QVariantList', constant=True)
        def sources(self): return list(SOURCES)
        @Property('QVariantList', constant=True)
        def palettes(self): return list(PALETTES)
        @Property('QVariantMap', notify=changed)
        def colors(self): return PALETTES[self.palette_name]
        @Property(str, notify=changed)
        def paletteName(self): return self.palette_name
        @Property(str, notify=changed)
        def status(self): return self._status
        @Property(str, notify=changed)
        def backend(self): return self._backend
        @Property('QVariantMap', notify=changed)
        def values(self):
            s = self.settings
            return dict(source=s.source, solid=s.solid, bpm=s.bpm, gain=s.drawing_gain,
                        blend=s.music_gain, mode=self.mode, glow=self.glow,
                        x=s.rotation_x, y=s.rotation_y, z=s.rotation_z,
                        loaded=bool(s.shapes))

        @Slot(str, 'QVariant')
        def setValue(self, key, value):
            try:
                if key == 'palette': self.palette_name = str(value)
                elif key == 'glow': self.glow = bool(value)
                elif key == 'mode': self.mode = str(value)
                elif key == 'solid':
                    self.settings = replace(self.settings, solid=str(value), shapes=())
                elif key in ('x', 'y', 'z'):
                    self.settings = replace(self.settings, **{'rotation_'+key:round(float(value)), 'shapes':()})
                else:
                    if key == 'source': self.audio.stop()
                    field = {'gain':'drawing_gain', 'blend':'music_gain'}.get(key, key)
                    self.settings = replace(self.settings, **{field: str(value) if key == 'source' else float(value)})
                self.audio.parameters = (self.settings, self.mode)
                self.changed.emit()
            except Exception as error:
                self._status = str(error); self.changed.emit()

        @Slot()
        def play(self):
            try:
                self.failure = ''
                if self.audio.running:
                    self.audio.stop()
                else:
                    if self.device and self.settings.source not in SOURCES:
                        self.audio.midi.connect(self.device)
                    self.audio.start(self.settings, self.mode)
                self.changed.emit()
            except Exception as error:
                self._status = str(error); self.failure = str(error); self.changed.emit()

        @Slot()
        def openScene(self):
            path, _ = QFileDialog.getOpenFileName(None, 'Open CPU app scene', '', 'Scene files (*.json);;All files (*)')
            if path:
                try:
                    self.audio.stop()
                    self.settings, self.mode, self.device = read_scene(path)
                    self.changed.emit()
                except Exception as error:
                    self._status = str(error); self.changed.emit()

        def tick(self):
            if not self.scope or window.visibility() == QQuickWindow.Visibility.Minimized:
                return
            now = time.perf_counter()
            self.elapsed = now-self.started
            start = now
            signal = self.audio.playback_snapshot(1920)[0] if self.audio.running else None
            if signal is None:
                times = self.elapsed + np.arange(1920)/self.settings.sample_rate
                signal = select_signal(*render(times, self.settings), self.settings, self.mode)
            self.scope.signal = signal
            self.scope.colors = PALETTES[self.palette_name]
            self.scope.glow = self.glow
            self.scope.scanlines = self.preferences['crt_scanlines']
            self.scope.divisions = self.preferences['grid_divisions']
            self.scope.update()
            self.prep_ms.append((time.perf_counter()-start)*1000)
            if now-self.last_status > .5:
                self._status = self.audio.error or self.failure or (f'PLAYING — {self.audio.underruns} underruns' if self.audio.running else 'SIMULATION — audio stopped')
                self.last_status = now
                self.changed.emit()

        def swapped(self):
            self.frame_count += 1
            self.frame_times.append(time.perf_counter())

    configure_graphics()
    QQuickStyle.setStyle('Basic')
    surface = QSurfaceFormat(); surface.setSamples(4)
    QSurfaceFormat.setDefaultFormat(surface)
    app = QApplication(sys.argv[:1])
    for name in ('JetBrainsMono-Regular.ttf', 'JetBrainsMono-Bold.ttf'):
        QFontDatabase.addApplicationFont(str(ASSETS_DIR/'fonts'/name))
    app.setFont(QFont('JetBrains Mono', 10))
    qmlRegisterType(Scope, 'Oscilloscope', 1, 0, 'GpuScope')
    controller = Controller()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty('control', controller)
    engine.load(QUrl.fromLocalFile(str(HERE/'qt_preview.qml')))
    if not engine.rootObjects():
        return 1
    window = engine.rootObjects()[0]
    controller.scope = window.findChild(Scope, 'scope')
    window.frameSwapped.connect(controller.swapped)
    timer = QTimer(); timer.setInterval(16); timer.timeout.connect(controller.tick); timer.start()

    def backend_info():
        api = window.rendererInterface().graphicsApi()
        controller._backend = str(api).split('.')[-1]
        rhi = window.rhi()
        if rhi:
            info = rhi.driverInfo()
            controller.adapter = bytes(info.deviceName).decode('utf-8', errors='replace')
            controller._backend += ' / '+controller.adapter
        controller.changed.emit()

    def finish():
        timer.stop()
        controller.audio.stop()
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grabWindow().save(str(args.screenshot))
        def stats(values):
            return dict(median=float(np.median(values)), p95=float(np.percentile(values,95))) if len(values) else {}
        result = dict(backend=controller._backend, adapter=controller.adapter,
                      frames=controller.frame_count, audio_frames=controller.audio.frames,
                      audio_underruns=controller.audio.underruns, audio_error=controller.audio.error or controller.failure,
                      sample_preparation_ms=stats(list(controller.prep_ms)[10:]),
                      geometry_upload_ms=stats(list(controller.scope.upload_ms)[10:]),
                      frame_interval_ms=stats(np.diff(list(controller.frame_times)[10:])*1000),
                      background_builds=controller.scope.background_builds, glow=controller.glow,
                      note='Frame interval includes scheduling/vsync; geometry time excludes GPU completion.')
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, indent=2))
        app.quit()

    def shutdown():
        controller.audio.stop()
        controller.audio.midi.disconnect()
    app.aboutToQuit.connect(shutdown)
    QTimer.singleShot(750, backend_info)
    if args.exercise:
        def exercise():
            controller.setValue('solid', SOLIDS[int(controller.elapsed*2) % len(SOLIDS)])
            controller.setValue('palette', list(PALETTES)[int(controller.elapsed) % len(PALETTES)])
            controller.setValue('x', int(controller.elapsed*30) % 180)
            controller.setValue('glow', int(controller.elapsed) % 2 == 0)
            window.resize(1180 + int(controller.elapsed) % 2 * 40, 820)
        exercise_timer = QTimer(); exercise_timer.setInterval(500)
        exercise_timer.timeout.connect(exercise); exercise_timer.start()
    if args.play: QTimer.singleShot(1000, controller.play)
    if args.seconds: QTimer.singleShot(round(args.seconds*1000), finish)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
