"""Native D3D11/Metal refresh and fixed-elapsed brightness; judge by exit code.

Rebuild phosphor_accumulate.frag.qsb first. Requires an awake, visible display.
"""
import _path  # noqa: F401
from types import SimpleNamespace
import numpy as np
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QImage, QSurfaceFormat
from PySide6.QtWidgets import QApplication
from phosphor.display.frame_step import step_display
from phosphor.editor.qt_app import Window
from phosphor.editor.qt_platform import configure_graphics
from phosphor.storage.app_files import DEFAULT_PREFERENCES


def events(milliseconds):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def brightness(view):
    image = view.grabWindow().convertToFormat(QImage.Format.Format_RGBA8888)
    assert not image.isNull(), 'Native window grab failed'
    data = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
    rgb = data[:, :image.width()*4].reshape(image.height(), image.width(), 4)[..., :3]
    return rgb.astype(float).sum()/255


def fixed_elapsed(w, hz):
    # Timer and frame-swap display steps are disabled: only the explicit steps below
    # deposit energy, regardless of the wall-clock time spent waiting for the GPU.
    w.apply_preferences(dict(DEFAULT_PREFERENCES, display_rate='60 Hz', palette='OLED white',
        phosphor='Palette colour', show_grid=False, crt_scanlines=False, crt_glow=False,
        trace_thickness=6., beam_intensity=.6, phosphor_grain=0., phosphor_shimmer=0.))
    host = w.host
    assert host.phosphor is not None, 'Phosphor shaders unavailable'
    host.scope.overlays = []
    # Flush old energy with no deposit, preserving the native resources.
    step_display(host, w.preferences, np.zeros((2, 2)), 0, 10.)
    host.scope.update(); host.view.update(); events(50)
    dt = 1/hz
    total = 0.; measured = 0.; frame = 0
    while measured < .3-1e-12:
        # 40 Hz: under one pass per frame. Faster figures overlap within a 60 Hz frame and
        # clip in the 8-bit deposit layer, which shorter frames clip less (brighter).
        phase = (np.arange(1921)/48000 + frame*dt)*2*np.pi*40
        signal = .7*np.column_stack((np.cos(phase), np.sin(phase)))
        step_display(host, w.preferences, signal, dt*48000, dt)
        host.scope.update(); host.view.update()
        events(25)
        if frame >= hz:  # One simulated second to settle the feedback.
            weight = min(dt, .3-measured)
            total += brightness(host.view)*weight
            measured += weight
        frame += 1
    return total/measured


def rate(w, setting):
    w.apply_preferences(dict(w.preferences, display_rate=setting))
    events(300)
    w.frame_times.clear(); w.swap_times.clear()
    events(1500)
    stamps, swaps = list(w.frame_times), list(w.swap_times)
    assert len(stamps) > 2, (setting, 'Display clock stalled', stamps)
    assert len(swaps) > 2, (setting, 'No frame swaps recorded (audit B11)', swaps)
    fps = (len(stamps)-1)/(stamps[-1]-stamps[0])
    refresh = w.host.view.screen().refreshRate()
    print(setting, 'display fps:', fps, 'swap fps:', (len(swaps)-1)/(swaps[-1]-swaps[0]), 'screen Hz:', refresh, 'pop-out:', w.popped)
    if setting == '60 Hz':
        assert 50 <= fps <= 70, fps
    elif refresh > 61:
        assert abs(fps/refresh-1) <= .15, (fps, refresh)
    return fps


configure_graphics()
surface = QSurfaceFormat(); surface.setSamples(4); QSurfaceFormat.setDefaultFormat(surface)
app = QApplication([])
w = Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
try:
    w.timer.stop()
    w.apply_preferences(dict(DEFAULT_PREFERENCES, display_rate='60 Hz'))
    w.resize(1000, 700); w.show(); events(250)
    reference = fixed_elapsed(w, 60)
    high = fixed_elapsed(w, 144)
    print('Fixed elapsed mean light:', reference, high)
    assert reference > 0 and abs(high/reference-1) <= .08, (reference, high)
    w.add_object('Cube')
    w.timer.start()
    rate(w, 'Match monitor')
    rate(w, '60 Hz')
    w.pop_out()
    rate(w, 'Match monitor')
    rate(w, '60 Hz')
    w.pop_in()
finally:
    w.shutdown_audio()
    w.release_views()
    w.force_close = True
    w.close()
    app.processEvents()
print('Refresh rate smoke passed.')
