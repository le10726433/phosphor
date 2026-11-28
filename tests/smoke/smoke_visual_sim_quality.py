"""Native smoke: graphics quality tiers (Low/Medium/High/Ultra), graticule vs simple grid, and glass vignette."""
import _path  # noqa: F401
from types import SimpleNamespace
import numpy as np
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from phosphor.editor import qt_app as q
from phosphor.display.phosphor import (
    BEAM_GAUSSIAN, GRAPHICS_QUALITIES, GRID_GRATICULE, GRID_SIMPLE,
    QUALITY_HIGH, QUALITY_LOW, QUALITY_MEDIUM, QUALITY_ULTRA,
)

q.configure_graphics()
surface = QSurfaceFormat(); surface.setSamples(4); QSurfaceFormat.setDefaultFormat(surface)
app = QApplication([])
w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
w.resize(1000, 700); w.show(); QTest.qWait(250)
w.add_object('Tilting circle'); w.scene.saved = w.scene.document()
w.timer.stop(); QTest.qWait(30)

# 1. Appearance UI controls exist with expected defaults and enable/disable behavior.
ed = w.appearance_editor
assert ed.graphics_quality.currentText() == QUALITY_HIGH, ed.graphics_quality.currentText()
assert ed.grid_style.currentText() == GRID_GRATICULE, ed.grid_style.currentText()
assert abs(ed.sliders['glass_vignette'].value - 0.15) < 1e-6, ed.sliders['glass_vignette'].value
ed.grid.setChecked(False); QTest.qWait(20)
assert not ed.grid_style.isEnabled(), 'Grid style should disable when Grid lines unchecked'
ed.grid.setChecked(True); QTest.qWait(20)
assert ed.grid_style.isEnabled()

# 2. All 4 graphics quality tiers render through PhosphorLayer without mutating independent user prefs.
layer = w.host.phosphor
scope = w.host.scope
assert layer is not None, 'Shaders failed to load'
phase = np.linspace(0, 2 * np.pi, 1920)
circle = 0.7 * np.column_stack((np.cos(phase), np.sin(phase))).astype(np.float32)

base = dict(w.preferences, phosphor='P7 blue / long yellow', beam_profile=BEAM_GAUSSIAN,
            crt_glow=True, show_grid=True, grid_style=GRID_GRATICULE, glass_vignette=0.2)

w.apply_preferences(base)
for tier in GRAPHICS_QUALITIES:
    ed.graphics_quality.setCurrentText(tier)
    assert ed.glow.isEnabled() == (tier != QUALITY_LOW)
    assert ed.sliders['glow_size'].isEnabled() == (tier != QUALITY_LOW)
    assert ed.sliders['glass_vignette'].isEnabled() == (tier in (QUALITY_HIGH, QUALITY_ULTRA))
    assert ed.editor.plot.quality == tier
    assert w.preferences['glass_vignette'] == base['glass_vignette']
    w.tick(); scope.signal = circle
    w.update_phosphor(1920, 0.016); scope.update(); QTest.qWait(30)
    assert w.preferences['beam_profile'] == BEAM_GAUSSIAN
    assert w.preferences['crt_glow'] is True
    if tier == QUALITY_LOW:
        assert not layer.root.property('gaussian')
        assert not layer.root.property('bloomOn')
        assert not layer.root.property('haloOn')
    elif tier == QUALITY_MEDIUM:
        assert layer.root.property('gaussian')
        assert layer.root.property('bloomOn')
        assert not layer.root.property('haloOn')
        assert layer.root.property('softSat') == 1.0
        assert layer.root.property('linearTone') == 0.0
    elif tier == QUALITY_HIGH:
        assert layer.root.property('gaussian')
        assert layer.root.property('bloomOn')
        assert layer.root.property('haloOn')  # 0.93.1: the real halo pass (the 4-tap fake drew ghosts).
        assert abs(layer.root.property('haloGain') - 0.14) < 1e-6
        assert layer.root.property('linearTone') == 1.0
        assert layer.root.property('cascade') > 0.0
        assert abs(layer.root.property('vignette') - 0.2) < 1e-5
    elif tier == QUALITY_ULTRA:
        assert layer.root.property('haloOn')
        assert layer.root.property('edgeGraticule') > 0.0

# 3. Graticule vs Simple grid geometry rebuilds cleanly and Graticule produces more vertices.
w.apply_preferences(dict(base, graphics_quality=QUALITY_HIGH, grid_style=GRID_GRATICULE))
w.tick(); scope.update(); QTest.qWait(40)
grat_verts = scope.layers[0][1].vertexCount()
builds_before = scope.background_builds

w.apply_preferences(dict(base, graphics_quality=QUALITY_HIGH, grid_style=GRID_SIMPLE))
w.tick(); scope.update(); QTest.qWait(40)
simple_verts = scope.layers[0][1].vertexCount()
assert scope.background_builds > builds_before
assert grat_verts > simple_verts > 0, (grat_verts, simple_verts)

# Low tier simplifies graticule to simple vertex count.
w.apply_preferences(dict(base, graphics_quality=QUALITY_LOW, grid_style=GRID_GRATICULE))
w.tick(); scope.update(); QTest.qWait(40)
low_verts = scope.layers[0][1].vertexCount()
assert low_verts == simple_verts, (low_verts, simple_verts)

w.shutdown_audio(); w.force_close = True; w.close(); app.processEvents()
print(f'Visual sim & quality smoke passed (graticule verts={grat_verts}, simple verts={simple_verts}).')
