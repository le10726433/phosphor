"""Native Qt scale fidelity: judge by exit code; run on D3D11 and Metal.

Rebuild phosphor_blur.frag.qsb before running. Export uses render control;
ViewHost checks the separate, visible native window path (including its DPR).
"""
import _path  # noqa: F401
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QSurfaceFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from phosphor.display.crt_theme import PALETTES
from phosphor.display.frame_step import step_display
from phosphor.display.phosphor import BEAM_GAUSSIAN, PHOSPHOR_PALETTE, view_scale
from phosphor.display.video_renderer import VideoRenderer
from phosphor.editor.qt_app import ViewHost
from phosphor.editor.qt_platform import configure_graphics
from phosphor.storage.app_files import DEFAULT_PREFERENCES


configure_graphics()
surface = QSurfaceFormat(); surface.setSamples(4); QSurfaceFormat.setDefaultFormat(surface)
app = QApplication([])
preferences = dict(DEFAULT_PREFERENCES, palette='OLED white', phosphor=PHOSPHOR_PALETTE,
    beam_profile=BEAM_GAUSSIAN, crt_glow=True, glow_size=12., glow_intensity=1.,
    trace_thickness=6., beam_brightness=1., beam_intensity=.6, trace_interpolation='Off',
    phosphor_grain=0., phosphor_shimmer=0., show_grid=False, crt_scanlines=False)
phase = np.arange(48000)*2*np.pi*60/48000
samples = .7*np.column_stack((np.cos(phase), np.sin(phase)))


def metrics(picture):
    image = picture.scaled(360, 360, Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGBA8888)
    data = np.frombuffer(image.constBits(), np.uint8).reshape(360, image.bytesPerLine()//4, 4)[:, :360, :3]
    light = data.astype(float).mean(axis=2)/255
    # Average radial profiles over the circle, avoiding a single pixel's alignment.
    radii = np.linspace(.7*.45*360-12, .7*.45*360+12, 481)
    angles = np.linspace(0, 2*np.pi, 256, endpoint=False)
    x = 179.5+np.cos(angles[:, None])*radii
    y = 179.5+np.sin(angles[:, None])*radii
    ix, iy = np.floor(x).astype(int), np.floor(y).astype(int)
    dx, dy = x-ix, y-iy
    profile = ((1-dx)*(1-dy)*light[iy, ix] + dx*(1-dy)*light[iy, ix+1]
        + (1-dx)*dy*light[iy+1, ix] + dx*dy*light[iy+1, ix+1]).mean(axis=0)
    profile -= (profile[0]+profile[-1])/2
    peak = float(profile.max()); at = int(profile.argmax())
    assert peak > .05, ('No visible trace', peak)
    half = peak/2
    left = np.flatnonzero(profile[:at] < half)[-1]
    right = at+np.flatnonzero(profile[at:] < half)[0]
    lo = radii[left]+(radii[left+1]-radii[left])*(half-profile[left])/(profile[left+1]-profile[left])
    hi = radii[right-1]+(radii[right]-radii[right-1])*(half-profile[right-1])/(profile[right]-profile[right-1])
    return peak, (hi-lo)/360


def compare(results, label):
    reference = results[720]
    for side, (peak, width) in results.items():
        assert abs(peak/reference[0]-1) <= .08, (label, side, 'brightness', results)
        assert abs(width/reference[1]-1) <= .15, (label, side, 'half-max width', results)
    print(label, results)


offline = {}
for side in (360, 720, 1440):
    with VideoRenderer(preferences, side, side, 30, samples) as renderer:
        for k in range(30):
            image = renderer.frame(k)
        offline[side] = metrics(image)
compare(offline, 'Render-control export')

# Use fresh hosts so each size starts with empty feedback and identical exposure.
live = {}; closed_hosts = []
for side in (360, 720):
    host = ViewHost(); closed_hosts.append(host)
    try:
        host.resize(side, side); host.show(); QTest.qWait(150)
        assert host.phosphor is not None, 'Phosphor shaders unavailable'
        scope = host.scope
        scope.colors = PALETTES[preferences['palette']]
        # A bare ViewHost's window is white until the app sets the palette background.
        host.view.setColor(QColor(scope.colors['background']))
        scope.show_grid = False; scope.scanlines = False; scope.overlays = []
        signal = samples[:1601]
        for _ in range(30):
            step_display(host, preferences, signal, 1600, 1/30)
            scope.update(); QTest.qWait(35)
        assert host.phosphor.root.property('viewScale') == view_scale(scope.width(), scope.height())
        assert host.phosphor.root.property('gaussian') and host.phosphor.root.property('bloomOn')
        live[side] = metrics(host.view.grabWindow())
    finally:
        host.release_phosphor(); host.close(); host.view.close(); host.view.destroy()
        app.processEvents()
compare(live, 'Visible ViewHost')
print('Display scale passed: peak within 8%, relative half-max width within 15%.')
