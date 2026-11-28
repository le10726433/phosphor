"""Late-80s Warm Beige 3D Photorealistic Oscilloscope Mode (QtQuick3D).

Hosts a real GPU-rendered 3D lab bench with a curved CRT oscilloscope (bound live to
Phosphor's GpuScope + PhosphorLayer), matte warm-beige textured ABS plastic enclosure,
brushed-metal C1-170 faceplate with live sample-rate branding, physical 3D knurled
rotary knobs (CH1 X Gain, CH2 Y Gain, Timebase Window) with recessed live LCD readouts
and double-click-to-reset, tactile 3D pushbuttons with glowing LED jewels and matching
local lights, working yellow PWR button with authentic CRT warm-up/collapse animation,
true glass-thickness graticule parallax + ExtendedSceneEnvironment bloom/DOF,
interactive Verlet-physics BNC coaxial cables, vintage multi-hinge architect desk lamp
with base switch, big red fluorescent light pushbutton, 5-page tattered Voynich/math
lab notepad, green self-healing cutting mat, peeling calibration sticker, interactive
bobblehead on top of the scope, subtle mouse parallax with Camera Fix (F), and clickable
camera zoom presets (Desk, Scope, Controls).
"""
from datetime import datetime
from pathlib import Path
import math
import sys

import numpy as np
from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QVector3D
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtWidgets import QStackedLayout, QWidget

from phosphor.raytrace import WGPU_AVAILABLE

from phosphor.display.glow_trail import VISUAL_RANGES
from phosphor.display.phosphor import DEFAULT_GRAPHICS_QUALITY, PHOSPHOR_PALETTE
from phosphor.display.qt_phosphor import PhosphorLayer
from phosphor.display.qt_scope import GpuScope
from phosphor.display.time_sweep import DEFAULT_WINDOW_MS, WINDOW_RANGE_MS
from phosphor.editor.desk3d_assets import (
    BailHandleGeometry, BezelGougeTexture, BezelSmudgeTexture, BncJackGeometry,
    BncPlugCollarGeometry, ControlEmissiveTexture, ControlFaceplateTexture,
    CrtHoodGeometry, CurvedCrtGeometry, FaceplateScrewGeometry, KeycapGeometry,
    KnurledKnobGeometry, RoundedBezelFrameGeometry, SideVentTexture,
    SmallTrimKnobGeometry, StrainBootGeometry, format_sample_rate, texture_dir_url,
    texture_tier_for_quality,
)
from phosphor.editor.desk3d_props import (
    BobbleheadFaceTexture, BobbleheadSpring, CalStickerTexture, CoaxCableGeometry,
    CoffeeMugGeometry, ContactShadowTexture, CrtGraticuleGlassTexture,
    CuttingMatNormalTexture, CuttingMatRoughnessTexture, CuttingMatTexture,
    HelicalSpringGeometry, HexPencilBarrelGeometry, HollowLampShadeGeometry,
    LabWallChartTexture, LampPowerCordGeometry, LampShadeInnerGeometry,
    LampVolumetricConeGeometry, MugHandleGeometry, NotepadPageTexture,
    SpareProbeCordGeometry, VerletCoaxCable,
)
from phosphor.engine.solids import PLATONIC_SOLIDS, EXTRA_SOLIDS, POLYGONS, CURVES

LIGHTING_MODES = ('Bright Fluorescent', 'Dark Ambient', 'Completely Dark')
CAMERA_PRESETS = ('Desk', 'Scope', 'Controls')
CYCLE_OBJECTS = (*list(PLATONIC_SOLIDS), *list(EXTRA_SOLIDS)[:4], *list(POLYGONS)[:3], *list(CURVES)[:3])

# World-space coordinates for CH1 X / CH2 Y BNC sockets (matching scopeRig at (0, 8, 0) tilted -7° on X)
CH1_SOCKET_WORLD = (46.0, -73.6, 24.7)
CH2_SOCKET_WORLD = (102.0, -73.6, 24.7)
CH1_ANCHOR_WORLD = (24.0, -127.0, 215.0)
CH2_ANCHOR_WORLD = (118.0, -127.0, 215.0)

# 3D-only phosphor beam color presets (label, phosphor_name, palette_override_or_None, led_and_bounce_hex)
BEAM_COLOR_PRESETS = (
    ('P31 Green', 'P31 green', None, '#5ce677'),
    ('P1 Yellow-Green', 'P1 yellow-green', None, '#8cff40'),
    ('Amber', PHOSPHOR_PALETTE, 'Amber', '#ffb84d'),
    ('Cyan', PHOSPHOR_PALETTE, 'Cyan', '#5de6ff'),
    ('P7 Blue / Yellow', 'P7 blue / long yellow', None, '#7399ff'),
    ('White', PHOSPHOR_PALETTE, 'OLED white', '#e8f0ff'),
)

# 3D-only unlit / blank CRT screen glass backing colors (label, hex_color)
CRT_BACKING_PRESETS = (
    ('Dark Green Glass', '#0c1811'),
    ('Tektronix Blue', '#193547'),
    ('Warm Amber Glass', '#1a140c'),
    ('Vintage Sage', '#14261b'),
    ('Pure Black', '#050607'),
)

QML_PATH = Path(__file__).with_name('desk3d.qml')


def _smoothstep(t):
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def format_time_window(ms):
    """Format sweep window in milliseconds for the C1-170 LCD readout."""
    val = float(ms)
    if val >= 1000.0:
        return f'{val / 1000.0:.2f}s'
    if val >= 100.0:
        return f'{val:.0f}ms'
    if val >= 10.0:
        return f'{val:.1f}ms'
    return f'{val:.2f}ms'


def compute_crt_light_metrics(signal, traces=None, beam_scale=1.0, crt_size=512.0):
    """Extract luminous energy, spatial centroid (cx, cy), and 4-edge bezel bounce weights
    from the live oscilloscope signal (or time-sweep traces) actually being rendered.
    """
    pts = None
    if traces:
        chunks = []
        half = max(1.0, float(crt_size) * 0.5)
        for trace in traces:
            arr = np.asarray(trace[0] if isinstance(trace, tuple) else trace, dtype=np.float32)
            if arr.ndim == 2 and arr.shape[0] > 0 and arr.shape[1] >= 2:
                nx = np.clip((arr[:, 0] - half) / half, -1.0, 1.0)
                ny = np.clip((half - arr[:, 1]) / half, -1.0, 1.0)
                chunks.append(np.column_stack([nx, ny]))
        if chunks:
            pts = np.vstack(chunks)
    elif signal is not None:
        arr = np.asarray(signal, dtype=np.float32)
        if arr.ndim == 2 and arr.shape[0] > 0 and arr.shape[1] >= 2:
            step = max(1, arr.shape[0] // 512)
            pts = np.clip(arr[::step, :2], -1.0, 1.0)

    if pts is None or pts.shape[0] < 2:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    finite = np.isfinite(pts[:, 0]) & np.isfinite(pts[:, 1])
    if not np.any(finite):
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    pts = pts[finite]
    if pts.shape[0] < 2:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    x, y = pts[:, 0], pts[:, 1]
    rad = np.hypot(x, y)
    diffs = np.diff(pts, axis=0)
    seg_len = np.hypot(diffs[:, 0], diffs[:, 1])
    mean_step = float(np.mean(seg_len))
    rms_rad = float(np.sqrt(np.mean(rad * rad)))
    max_rad = float(np.max(rad))

    if max_rad < 1e-4 and mean_step < 1e-4:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    # Keep bezel energy strictly proportional to actual on-screen trace coverage & density (no inflated floor)
    coverage = min(1.0, rms_rad * 1.25)
    density = min(1.0, math.log1p(mean_step * 20.0) / math.log1p(7.0))
    raw_energy = (0.04 + 0.48 * coverage + 0.48 * density) * max(0.0, float(beam_scale))
    energy = float(min(1.0, raw_energy))

    weights = 0.25 + rad
    w_sum = float(np.sum(weights)) + 1e-6
    cx = float(np.clip(np.sum(x * weights) / w_sum, -1.0, 1.0))
    cy = float(np.clip(np.sum(y * weights) / w_sum, -1.0, 1.0))

    left_w = float(np.mean(np.clip((-x - 0.12) / 0.88, 0.0, 1.0) ** 1.45))
    right_w = float(np.mean(np.clip((x - 0.12) / 0.88, 0.0, 1.0) ** 1.45))
    top_w = float(np.mean(np.clip((y - 0.12) / 0.88, 0.0, 1.0) ** 1.45))
    bottom_w = float(np.mean(np.clip((-y - 0.12) / 0.88, 0.0, 1.0) ** 1.45))

    scale = energy * 1.35
    return (
        energy,
        cx,
        cy,
        float(min(1.0, left_w * scale)),
        float(min(1.0, right_w * scale)),
        float(min(1.0, top_w * scale)),
        float(min(1.0, bottom_w * scale)),
    )


class _CrtHostAdapter:
    """Adapter presenting the offscreen 512x512 CRT item with the same interface as ViewHost."""
    def __init__(self, view, scope, phosphor):
        self.view = view
        self.scope = scope
        self.phosphor = phosphor
        self.closed_phosphor = None

    def release_phosphor(self):
        if self.phosphor is not None:
            self.phosphor.close()
            self.closed_phosphor, self.phosphor = self.phosphor, None


class Desk3DController(QObject):
    """Bidirectional controller binding QML 3D hardware controls & lab props to Phosphor's Window."""
    stateChanged = Signal()
    crtLightChanged = Signal()
    physicsChanged = Signal()
    pathTraceToggled = Signal(bool)
    exitRequested = Signal()

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        # Independent physical lab lights: start in atmospheric Dark Ambient (overhead off, warm desk lamp on)
        self._fluorescent_on = False
        self._desk_lamp_on = True
        self._camera_preset = 0  # Desk view
        self._camera_fixed = False
        self._raytracing_enabled = True  # Hybrid RT: ReflectionProbe + CRT area-light shader + SSAO + PCF16 + SSR
        self._path_trace_active = False
        self._path_trace_viewport = None
        self._zoom_offset = 0.0  # [ zooms out (+), ] zooms in (-)
        self._beam_preset_idx = 0  # 3D-only phosphor beam color preset (defaults to P31 Green)
        self._crt_bg_idx = 0       # 3D-only unlit CRT screen glass backing color
        self._crt_bg_item = None
        self._crt_glass_tex = None
        self._notepad_tex = None
        self._gain_x = 1.0         # Independent CH1 X display gain (both modes; double-click resets to 1.0)
        self._gain_y = 1.0         # Independent CH2 Y display gain (both modes; double-click resets to 1.0)
        self._hud_readout = ''
        self._readout_timer = QTimer(self)
        self._readout_timer.setSingleShot(True)
        self._readout_timer.setInterval(1800)
        self._readout_timer.timeout.connect(self._clear_readout)

        # CRT mains power warm-up / collapse-to-dot state
        self._crt_powered = True
        self._crt_power_level = 1.0
        self._crt_scale_x = 1.0
        self._crt_scale_y = 1.0
        self._crt_dot_glow = 0.0

        # Interactive 5-page tattered notepad
        self._notepad_open = False
        self._notepad_page = 0

        # Interactive bobblehead damped spring
        self.bobble = BobbleheadSpring()

        # Interactive Verlet-physics coaxial cables (cable 0 = X / Red, cable 1 = Y / Blue)
        _sockets = (CH1_SOCKET_WORLD, CH2_SOCKET_WORLD)
        self.cables = [
            VerletCoaxCable(_sockets, 0, CH1_ANCHOR_WORLD),
            VerletCoaxCable(_sockets, 1, CH2_ANCHOR_WORLD),
        ]
        self._cable_geoms = []
        self._grab_origin = [None, None]

        # Live signal-driven CRT phosphor bounce lighting state (smoothed per frame)
        self._crt_energy = 0.45
        self._crt_cx = 0.0
        self._crt_cy = 0.0
        self._crt_left = 0.25
        self._crt_right = 0.25
        self._crt_top = 0.25
        self._crt_bottom = 0.25

        # 60 Hz real-time physics & CRT power animation timer
        self._physics_timer = QTimer(self)
        self._physics_timer.setInterval(16)
        self._physics_timer.timeout.connect(self._on_physics_tick)

    def bind_crt_backing(self, item):
        self._crt_bg_item = item
        self._apply_crt_backing()

    def bind_props(self, crt_glass_tex, notepad_tex, cable_geoms):
        self._crt_glass_tex = crt_glass_tex
        self._notepad_tex = notepad_tex
        self._cable_geoms = list(cable_geoms)
        self._sync_graticule_overlay()

    def set_physics_active(self, active):
        if active and not self._physics_timer.isActive():
            self._physics_timer.start()
        elif not active and self._physics_timer.isActive():
            self._physics_timer.stop()

    def _apply_crt_backing(self):
        if self._crt_bg_item is not None:
            self._crt_bg_item.setProperty('color', QColor(CRT_BACKING_PRESETS[self._crt_bg_idx][1]))

    def _sync_graticule_overlay(self):
        if self._crt_glass_tex is not None:
            self._crt_glass_tex.set_show_grid(self.showGrid)

    def crt_prefs(self, base_prefs):
        """Return a 3D-local display preferences copy applying the 3D beam color preset
        and moving the graticule off the flat phosphor shader onto the floating outer cover-glass
        (true glass-thickness parallax) without mutating the 2D application's preferences."""
        _, phosphor_name, palette_override, _ = BEAM_COLOR_PRESETS[self._beam_preset_idx]
        prefs = dict(base_prefs, glass_vignette=0.0, show_grid=False, phosphor=phosphor_name)
        if palette_override is not None:
            prefs['palette'] = palette_override
        return prefs

    def _flash_readout(self, text):
        self._hud_readout = text
        self._readout_timer.start()
        self.stateChanged.emit()

    def _clear_readout(self):
        self._hud_readout = ''
        self.stateChanged.emit()

    def refresh(self):
        self._sync_graticule_overlay()
        self.stateChanged.emit()

    def _on_physics_tick(self):
        self.step_physics(1.0 / 60.0)

    def step_physics(self, dt=1.0 / 60.0):
        """Advance Verlet coax cables, bobblehead spring, and CRT warm-up/collapse animation."""
        dt = max(1e-3, min(0.05, float(dt)))
        changed = False

        # 1. Step both Verlet coaxial cables & update 3D tube meshes only while moving
        for idx, cable in enumerate(self.cables):
            if cable.step(dt):
                if idx < len(self._cable_geoms):
                    self._cable_geoms[idx].sync_mesh()
                changed = True

        # 2. Step bobblehead spring
        if self.bobble.is_moving:
            self.bobble.step(dt)
            changed = True

        # 3. Step CRT power warm-up / collapse-to-dot envelope
        if self._crt_powered and self._crt_power_level < 1.0:
            self._crt_power_level = min(1.0, self._crt_power_level + dt / 1.30)
            self._update_crt_power_envelope(warming=True)
            changed = True
        elif not self._crt_powered and self._crt_power_level > 0.0:
            self._crt_power_level = max(0.0, self._crt_power_level - dt / 0.48)
            self._update_crt_power_envelope(warming=False)
            changed = True

        if changed:
            self.physicsChanged.emit()

    def _update_crt_power_envelope(self, warming):
        p = self._crt_power_level
        if p <= 0.0:
            self._crt_scale_x, self._crt_scale_y, self._crt_dot_glow = 0.02, 0.02, 0.0
            return
        if p >= 1.0:
            self._crt_scale_x, self._crt_scale_y, self._crt_dot_glow = 1.0, 1.0, 0.0
            return
        if warming:
            if p < 0.25:
                u = p / 0.25
                self._crt_scale_x, self._crt_scale_y, self._crt_dot_glow = 0.06, 0.04, u * 0.65
            elif p < 0.65:
                u = (p - 0.25) / 0.40
                self._crt_scale_x = 0.06 + 0.94 * _smoothstep(u)
                self._crt_scale_y = 0.04 + 0.14 * u
                self._crt_dot_glow = (1.0 - u) * 0.65
            else:
                v = (p - 0.65) / 0.35
                self._crt_scale_x = 1.0
                self._crt_scale_y = 0.18 + 0.82 * _smoothstep(v)
                self._crt_dot_glow = 0.0
        else:
            # Authentic CRT power-off collapse: vertical collapses to a bright line, then collapses to a lingering dot
            if p > 0.55:
                v = (p - 0.55) / 0.45
                self._crt_scale_x = 1.0
                self._crt_scale_y = 0.03 + 0.97 * (v * v)
                self._crt_dot_glow = (1.0 - v) * 0.55
            elif p > 0.20:
                u = (p - 0.20) / 0.35
                self._crt_scale_x = 0.04 + 0.96 * (u * u)
                self._crt_scale_y = 0.03
                self._crt_dot_glow = 0.55 + (1.0 - u) * 0.45
            else:
                w = p / 0.20
                self._crt_scale_x = 0.04
                self._crt_scale_y = 0.03
                self._crt_dot_glow = w

    def update_crt_light(self, signal, traces=None, elapsed=1.0 / 60.0):
        """Update real-time CRT bezel bounce lighting from the live rendered signal/traces."""
        if self._path_trace_active and self._path_trace_viewport is not None:
            self._path_trace_viewport.push_signal(signal, traces=traces, elapsed=elapsed)
        power_scale = self._crt_power_level
        energy, cx, cy, lb, rb, tb, bb = compute_crt_light_metrics(
            signal, traces=traces, beam_scale=self.crtGlow * power_scale, crt_size=Desk3DHost.CRT_SIZE
        )
        dt = max(1e-3, min(0.1, float(elapsed)))
        a_up = 1.0 - math.exp(-dt * 28.0)
        a_dn = 1.0 - math.exp(-dt * 10.0)
        a_pos = 1.0 - math.exp(-dt * 18.0)

        def smooth(cur, tgt):
            a = a_up if tgt >= cur else a_dn
            return cur + (tgt - cur) * a

        new_energy = smooth(self._crt_energy, energy)
        new_cx = self._crt_cx + (cx - self._crt_cx) * a_pos
        new_cy = self._crt_cy + (cy - self._crt_cy) * a_pos
        new_lb = smooth(self._crt_left, lb)
        new_rb = smooth(self._crt_right, rb)
        new_tb = smooth(self._crt_top, tb)
        new_bb = smooth(self._crt_bottom, bb)

        if (abs(new_energy - self._crt_energy) > 1e-3
                or abs(new_cx - self._crt_cx) > 2e-3
                or abs(new_cy - self._crt_cy) > 2e-3
                or abs(new_lb - self._crt_left) > 2e-3
                or abs(new_rb - self._crt_right) > 2e-3
                or abs(new_tb - self._crt_top) > 2e-3
                or abs(new_bb - self._crt_bottom) > 2e-3):
            self._crt_energy = new_energy
            self._crt_cx = new_cx
            self._crt_cy = new_cy
            self._crt_left = new_lb
            self._crt_right = new_rb
            self._crt_top = new_tb
            self._crt_bottom = new_bb
            self.crtLightChanged.emit()

    @Property(bool, notify=stateChanged)
    def fluorescentOn(self):
        return self._fluorescent_on

    @Property(bool, notify=stateChanged)
    def deskLampOn(self):
        return self._desk_lamp_on

    @Property(int, notify=stateChanged)
    def lightingMode(self):
        if self._fluorescent_on:
            return 0
        return 1 if self._desk_lamp_on else 2

    @Property(int, notify=stateChanged)
    def cameraPreset(self):
        return self._camera_preset

    @Property(bool, notify=stateChanged)
    def cameraFixed(self):
        return self._camera_fixed

    @Property(float, notify=stateChanged)
    def zoomOffset(self):
        return self._zoom_offset

    @Property(bool, notify=stateChanged)
    def raytracingEnabled(self):
        return self._raytracing_enabled

    @Property(bool, notify=stateChanged)
    def pathTraceAvailable(self):
        return bool(WGPU_AVAILABLE)

    @Property(bool, notify=stateChanged)
    def pathTraceActive(self):
        return self._path_trace_active

    @Property(str, notify=stateChanged)
    def shaderBaseUrl(self):
        return QUrl.fromLocalFile(str(Path(__file__).with_name('shaders'))).toString() + '/'

    @Property(bool, notify=stateChanged)
    def crtPowered(self):
        return self._crt_powered

    @Property(float, notify=physicsChanged)
    def crtPowerLevel(self):
        return self._crt_power_level

    @Property(float, notify=physicsChanged)
    def crtScaleX(self):
        return self._crt_scale_x

    @Property(float, notify=physicsChanged)
    def crtScaleY(self):
        return self._crt_scale_y

    @Property(float, notify=physicsChanged)
    def crtDotGlow(self):
        return self._crt_dot_glow

    @Property(bool, notify=stateChanged)
    def notepadOpen(self):
        return self._notepad_open

    @Property(int, notify=stateChanged)
    def notepadPage(self):
        return self._notepad_page

    @Property(float, notify=physicsChanged)
    def bobbleRx(self):
        return float(self.bobble.rx)

    @Property(float, notify=physicsChanged)
    def bobbleRy(self):
        return float(self.bobble.ry)

    @Property(float, notify=physicsChanged)
    def bobbleRz(self):
        return float(self.bobble.rz)

    def port_source(self, port_idx):
        """Return which signal channel (0=X, 1=Y) is plugged into BNC port port_idx, or None."""
        for sig_idx, cable in enumerate(self.cables):
            if cable.port_idx == port_idx:
                return sig_idx
        return None

    def route_signal(self, arr):
        """Route stereo signal through the physical BNC patch cables (supporting X/Y swapping and unplugged channels)."""
        arr = np.asarray(arr, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[1] < 2:
            return arr
        if self._crt_power_level < 0.02:
            return np.zeros_like(arr)
        s0, s1 = self.port_source(0), self.port_source(1)
        gx, gy = self._gain_x, self._gain_y
        if s0 == 0 and s1 == 1 and gx == 1.0 and gy == 1.0:
            return arr
        out = np.empty_like(arr)
        out[:, 0] = arr[:, s0] * gx if s0 is not None else 0.0
        out[:, 1] = arr[:, s1] * gy if s1 is not None else 0.0
        return out

    @Property(bool, notify=stateChanged)
    def ch1Plugged(self):
        return self.port_source(0) is not None

    @Property(bool, notify=stateChanged)
    def ch2Plugged(self):
        return self.port_source(1) is not None

    @Property(bool, notify=stateChanged)
    def inputsSwapped(self):
        return self.port_source(0) == 1 and self.port_source(1) == 0

    @Property(QVector3D, notify=physicsChanged)
    def plug1Pos(self):
        p = self.cables[0].plug_pos
        return QVector3D(float(p[0]), float(p[1]), float(p[2]))

    @Property(QVector3D, notify=physicsChanged)
    def plug2Pos(self):
        p = self.cables[1].plug_pos
        return QVector3D(float(p[0]), float(p[1]), float(p[2]))

    @Property(int, notify=stateChanged)
    def beamPresetIndex(self):
        return self._beam_preset_idx

    @Property(int, notify=stateChanged)
    def crtBackingIndex(self):
        return self._crt_bg_idx

    @Property(str, notify=stateChanged)
    def hudReadout(self):
        return self._hud_readout

    @Property(str, notify=stateChanged)
    def sampleRateLabel(self):
        w = self._window
        rate = getattr(getattr(w, 'audio', None), 'sample_rate', None) or w.signal_preferences.get('output_rate', 48000)
        return format_sample_rate(rate)

    @Property(str, notify=stateChanged)
    def textureTier(self):
        quality = self._window.preferences.get('graphics_quality', DEFAULT_GRAPHICS_QUALITY)
        return texture_tier_for_quality(quality)

    @Property(str, notify=stateChanged)
    def textureBaseUrl(self):
        return texture_dir_url(self.textureTier)

    @Property(QColor, notify=stateChanged)
    def crtColor(self):
        return QColor(BEAM_COLOR_PRESETS[self._beam_preset_idx][3])

    @Property(QColor, notify=stateChanged)
    def crtBgColor(self):
        return QColor(CRT_BACKING_PRESETS[self._crt_bg_idx][1])

    @Property(float, notify=stateChanged)
    def crtGlow(self):
        lo, hi = VISUAL_RANGES['beam_intensity']
        val = float(self._window.preferences.get('beam_intensity', 1.0))
        norm = max(0.0, min(1.0, (math.log1p(val) - math.log1p(lo)) / max(1e-6, math.log1p(hi) - math.log1p(lo))))
        return 0.35 + norm * 1.05

    @Property(float, notify=crtLightChanged)
    def crtEnergy(self):
        return self._crt_energy

    @Property(float, notify=crtLightChanged)
    def crtCentroidX(self):
        return self._crt_cx

    @Property(float, notify=crtLightChanged)
    def crtCentroidY(self):
        return self._crt_cy

    @Property(float, notify=crtLightChanged)
    def crtLeftBounce(self):
        return self._crt_left

    @Property(float, notify=crtLightChanged)
    def crtRightBounce(self):
        return self._crt_right

    @Property(float, notify=crtLightChanged)
    def crtTopBounce(self):
        return self._crt_top

    @Property(float, notify=crtLightChanged)
    def crtBottomBounce(self):
        return self._crt_bottom

    @Property(bool, notify=stateChanged)
    def playing(self):
        w = self._window
        return bool(w.capture.running if w.system_monitor else w.audio.running and not w.audio.cycle and not w.audio.preview)

    @Property(bool, notify=stateChanged)
    def sweepMode(self):
        return bool(self._window.sweep_mode)

    @Property(bool, notify=stateChanged)
    def showGrid(self):
        return bool(self._window.preferences.get('show_grid', True))

    @Property(float, notify=stateChanged)
    def gainX(self):
        return self._gain_x

    @Property(float, notify=stateChanged)
    def gainY(self):
        return self._gain_y

    @Property(str, notify=stateChanged)
    def lcdGainX(self):
        if not self._crt_powered:
            return '---'
        return f'{self._gain_x:.2f}×' if self._gain_x < 10.0 else f'{self._gain_x:.1f}×'

    @Property(str, notify=stateChanged)
    def lcdGainY(self):
        if not self._crt_powered:
            return '---'
        return f'{self._gain_y:.2f}×' if self._gain_y < 10.0 else f'{self._gain_y:.1f}×'

    @Property(str, notify=stateChanged)
    def lcdTimeWin(self):
        if not self._crt_powered:
            return '---'
        if not self._window.sweep_mode:
            return 'X–Y'
        return format_time_window(self._window.sweep_window_ms)

    @Property(float, notify=stateChanged)
    def knobIntensity(self):
        lo, hi = VISUAL_RANGES['beam_intensity']
        val = float(self._window.preferences.get('beam_intensity', 1.0))
        return max(0.0, min(1.0, (math.log1p(val) - math.log1p(lo)) / max(1e-6, math.log1p(hi) - math.log1p(lo))))

    @Property(float, notify=stateChanged)
    def knobGain(self):
        val = float(getattr(self._window, 'visualization_gain', 1.0))
        return max(0.0, min(1.0, math.log1p(val) / math.log1p(32.0)))

    @Property(float, notify=stateChanged)
    def knobOutput(self):
        val = float(self._window.scene.settings.drawing_gain)
        return max(0.0, min(1.0, val / 0.95))

    @Property(float, notify=stateChanged)
    def knobGainX(self):
        return max(0.0, min(1.0, math.log1p(self._gain_x) / math.log1p(32.0)))

    @Property(float, notify=stateChanged)
    def knobGainY(self):
        return max(0.0, min(1.0, math.log1p(self._gain_y) / math.log1p(32.0)))

    @Property(float, notify=stateChanged)
    def knobTimeWin(self):
        lo, hi = WINDOW_RANGE_MS
        val = max(lo, min(hi, float(self._window.sweep_window_ms)))
        return max(0.0, min(1.0, math.log(val / lo) / math.log(hi / lo)))

    @Slot(str, result=float)
    def getKnob(self, name):
        return {
            'knob_intensity': self.knobIntensity,
            'knob_gain': self.knobGain,
            'knob_output': self.knobOutput,
            'knob_gain_x': self.knobGainX,
            'knob_gain_y': self.knobGainY,
            'knob_time_win': self.knobTimeWin,
        }.get(name, 0.5)

    @Slot(str, float)
    def setKnob(self, name, norm):
        norm = max(0.0, min(1.0, float(norm)))
        w = self._window
        if name == 'knob_intensity':
            lo, hi = VISUAL_RANGES['beam_intensity']
            val = math.expm1(math.log1p(lo) + norm * (math.log1p(hi) - math.log1p(lo)))
            w.set_brightness(val)
            self._flash_readout(f'INTENSITY · {val:.2f}×')
        elif name == 'knob_gain':
            val = math.expm1(norm * math.log1p(32.0))
            w.visualization_gain = val
            w.visualization_gain_control.setValue(val)
            self._flash_readout(f'SIGNAL GAIN · {val:.2f}×')
        elif name == 'knob_output':
            val = norm * 0.95
            w.edit('drawing_gain', val)
            self._flash_readout(f'OUTPUT LEVEL · {val:.2f}')
        elif name == 'knob_gain_x':
            self._gain_x = math.expm1(norm * math.log1p(32.0))
            self._flash_readout(f'CH1 X GAIN · {self.lcdGainX}')
        elif name == 'knob_gain_y':
            self._gain_y = math.expm1(norm * math.log1p(32.0))
            self._flash_readout(f'CH2 Y GAIN · {self.lcdGainY}')
        elif name == 'knob_time_win':
            # In X-Y mode the right-hand timebase knob intentionally does nothing
            if not w.sweep_mode:
                return
            lo, hi = WINDOW_RANGE_MS
            val = lo * ((hi / lo) ** norm)
            w.sweep_window_ms = val
            w.sweep_window_control.setValue(val)
            self._flash_readout(f'TIME WINDOW · {format_time_window(val)}')
        self.stateChanged.emit()

    @Slot(str)
    def resetKnob(self, name):
        """Double-click on CH1 X Gain / CH2 Y Gain resets to 1.0x; Time Window resets to default in Time mode."""
        w = self._window
        if name == 'knob_gain_x':
            self._gain_x = 1.0
            self._flash_readout('CH1 X GAIN · 1.00× (RESET)')
        elif name == 'knob_gain_y':
            self._gain_y = 1.0
            self._flash_readout('CH2 Y GAIN · 1.00× (RESET)')
        elif name == 'knob_time_win':
            if not w.sweep_mode:
                return
            w.sweep_window_ms = DEFAULT_WINDOW_MS
            w.sweep_window_control.setValue(DEFAULT_WINDOW_MS)
            self._flash_readout(f'TIME WINDOW · {format_time_window(DEFAULT_WINDOW_MS)} (RESET)')
        elif name == 'knob_gain':
            w.visualization_gain = 1.0
            w.visualization_gain_control.setValue(1.0)
            self._flash_readout('SIGNAL GAIN · 1.00× (RESET)')
        else:
            return
        self.stateChanged.emit()

    @Slot(str)
    def pressButton(self, name):
        w = self._window
        if name == 'btn_play':
            w.play()
            self._flash_readout('PLAYING' if self.playing else 'STOPPED')
        elif name == 'btn_view':
            next_idx = 0 if w.sweep_mode else 1
            w.sweep_display.setCurrentIndex(next_idx)
            self._flash_readout('VIEW · TIME X(t), Y(t)' if next_idx else 'VIEW · X–Y')
        elif name == 'btn_beam':
            self._beam_preset_idx = (self._beam_preset_idx + 1) % len(BEAM_COLOR_PRESETS)
            label = BEAM_COLOR_PRESETS[self._beam_preset_idx][0]
            self._flash_readout(f'BEAM · {label.upper()}')
            self.crtLightChanged.emit()
        elif name == 'btn_grid':
            on = not w.preferences.get('show_grid', True)
            w.apply_preferences(dict(w.preferences, show_grid=on))
            self._sync_graticule_overlay()
            self._flash_readout('GRATICULE · ON' if on else 'GRATICULE · OFF')
        elif name == 'btn_object':
            cur = w.scene.shape.solid if w.scene.shape is not None else ''
            idx = (CYCLE_OBJECTS.index(cur) + 1) % len(CYCLE_OBJECTS) if cur in CYCLE_OBJECTS else 0
            nxt = CYCLE_OBJECTS[idx]
            if w.scene.shape is not None:
                w.apply_replace([w.scene.selected], nxt)
            else:
                w.add_object(nxt)
            self._flash_readout(f'OBJECT · {nxt.upper()}')
        elif name == 'btn_palette':
            self._crt_bg_idx = (self._crt_bg_idx + 1) % len(CRT_BACKING_PRESETS)
            self._apply_crt_backing()
            label = CRT_BACKING_PRESETS[self._crt_bg_idx][0]
            self._flash_readout(f'CRT GLASS · {label.upper()}')
        elif name == 'btn_light':
            self.cycleLighting()
        elif name == 'btn_fluorescent':
            self.toggleFluorescent()
        elif name == 'btn_lamp':
            self.toggleDeskLamp()
        elif name == 'btn_power':
            self.togglePower()
        self.stateChanged.emit()

    @Slot()
    def toggleFluorescent(self):
        self._fluorescent_on = not self._fluorescent_on
        self._flash_readout('FLUORESCENTS · ON' if self._fluorescent_on else 'FLUORESCENTS · OFF')

    @Slot()
    def toggleDeskLamp(self):
        self._desk_lamp_on = not self._desk_lamp_on
        self._flash_readout('DESK LAMP · ON' if self._desk_lamp_on else 'DESK LAMP · OFF')

    @Slot()
    def togglePower(self):
        self._crt_powered = not self._crt_powered
        self._flash_readout('MAINS POWER · ON (WARMING UP)' if self._crt_powered else 'MAINS POWER · OFF')

    @Slot()
    def toggleCameraFix(self):
        self._camera_fixed = not self._camera_fixed
        self._flash_readout('CAMERA FIX · LOCKED' if self._camera_fixed else 'CAMERA FIX · PARALLAX ON')

    @Slot()
    def toggleRaytracing(self):
        self._raytracing_enabled = not self._raytracing_enabled
        self._flash_readout('HYBRID RAYTRACING · ON' if self._raytracing_enabled else 'HYBRID RAYTRACING · OFF')

    @Slot()
    def togglePathTrace(self):
        if not WGPU_AVAILABLE:
            self._flash_readout('PATH TRACER UNAVAILABLE (INSTALL WGPU)')
            return
        self._path_trace_active = not self._path_trace_active
        self.pathTraceToggled.emit(self._path_trace_active)
        self.stateChanged.emit()

    @Slot()
    def clickNotepad(self):
        if not self._notepad_open:
            self._notepad_open = True
            self._notepad_page = 0
            if self._notepad_tex is not None:
                self._notepad_tex.set_page(0)
            self._flash_readout('LAB NOTEPAD · PAGE 1 / 5 (CLICK TO FLIP)')
        elif self._notepad_page < NotepadPageTexture.PAGE_COUNT - 1:
            self._notepad_page += 1
            if self._notepad_tex is not None:
                self._notepad_tex.set_page(self._notepad_page)
            self._flash_readout(f'LAB NOTEPAD · PAGE {self._notepad_page + 1} / 5')
        else:
            self._notepad_open = False
            self._notepad_page = 0
            if self._notepad_tex is not None:
                self._notepad_tex.set_page(0)
            self._flash_readout('LAB NOTEPAD · SET DOWN')

    @Slot()
    def closeNotepad(self):
        if self._notepad_open:
            self._notepad_open = False
            self._flash_readout('LAB NOTEPAD · SET DOWN')

    @Slot()
    def pokeBobblehead(self):
        self.bobble.poke()
        self.physicsChanged.emit()

    @Slot(int)
    def grabCable(self, idx):
        idx = int(idx)
        if 0 <= idx < len(self.cables):
            cable = self.cables[idx]
            self._grab_origin[idx] = cable.plug_pos.copy()
            cable.grab(self._grab_origin[idx])

    @Slot(int, float, float)
    def dragCable(self, idx, dx, dy):
        idx = int(idx)
        if not (0 <= idx < len(self.cables)):
            return
        cable = self.cables[idx]
        origin = self._grab_origin[idx]
        if origin is None:
            origin = cable.plug_pos.copy()
            self._grab_origin[idx] = origin
        prev_port = cable.port_idx
        if cable.plugged:
            wx = float(origin[0]) + float(dx) * 0.58
            wy = float(origin[1]) - float(dy) * 0.58
            pull_mag = math.hypot(float(dx), float(dy))
            wz = float(origin[2]) + min(55.0, pull_mag * 0.42)
        else:
            wx = float(origin[0]) + float(dx) * 0.65
            wy = float(origin[1]) - float(dy) * 0.65
            wz = max(24.7, min(95.0, 24.7 + max(0.0, (-73.6 - wy) * 0.65)))
        event = cable.move_grab((wx, wy, wz))
        if idx < len(self._cable_geoms):
            self._cable_geoms[idx].sync_mesh()
        self.physicsChanged.emit()
        if event == 'unplugged':
            port_lbl = f'CH{(prev_port if prev_port is not None else idx) + 1}'
            sig_lbl = 'X (RED)' if idx == 0 else 'Y (BLUE)'
            self._flash_readout(f'{sig_lbl} UNPLUGGED FROM {port_lbl}')

    @Slot(int)
    def releaseCable(self, idx):
        idx = int(idx)
        if not (0 <= idx < len(self.cables)):
            return
        occupied = {c.port_idx for i, c in enumerate(self.cables) if i != idx and c.port_idx is not None}
        status, port = self.cables[idx].release(occupied)
        self._grab_origin[idx] = None
        if idx < len(self._cable_geoms):
            self._cable_geoms[idx].sync_mesh()
        self.physicsChanged.emit()
        if status == 'replugged' and port is not None:
            sig_lbl = 'X (RED)' if idx == 0 else 'Y (BLUE)'
            tag = ' (SWAPPED)' if port != idx else ''
            self._flash_readout(f'CH{port + 1} ← {sig_lbl}{tag}')

    @Slot(int)
    def replugCable(self, idx):
        idx = int(idx)
        if not (0 <= idx < len(self.cables)):
            return
        occupied = {c.port_idx for i, c in enumerate(self.cables) if i != idx and c.port_idx is not None}
        target_port = idx if idx not in occupied else (1 - idx if (1 - idx) not in occupied else None)
        if target_port is None:
            return
        self.cables[idx].replug(target_port)
        self._grab_origin[idx] = None
        if idx < len(self._cable_geoms):
            self._cable_geoms[idx].sync_mesh()
        self.physicsChanged.emit()
        sig_lbl = 'X (RED)' if idx == 0 else 'Y (BLUE)'
        tag = ' (SWAPPED)' if target_port != idx else ''
        self._flash_readout(f'CH{target_port + 1} ← {sig_lbl}{tag}')

    @Slot(int)
    def replugPort(self, port_idx):
        """Click an empty BNC socket to snap an unplugged coax cable into it."""
        port_idx = int(port_idx)
        if self.port_source(port_idx) is not None:
            return
        for idx in (port_idx, 1 - port_idx):
            if not self.cables[idx].plugged:
                self.cables[idx].replug(port_idx)
                self._grab_origin[idx] = None
                if idx < len(self._cable_geoms):
                    self._cable_geoms[idx].sync_mesh()
                self.physicsChanged.emit()
                sig_lbl = 'X (RED)' if idx == 0 else 'Y (BLUE)'
                tag = ' (SWAPPED)' if port_idx != idx else ''
                self._flash_readout(f'CH{port_idx + 1} ← {sig_lbl}{tag}')
                return

    @Slot(int)
    def setLightingMode(self, mode):
        mode = int(mode) % len(LIGHTING_MODES)
        if mode == 0:
            self._fluorescent_on, self._desk_lamp_on = True, True
        elif mode == 1:
            self._fluorescent_on, self._desk_lamp_on = False, True
        else:
            self._fluorescent_on, self._desk_lamp_on = False, False
        self._flash_readout(f'LIGHTING · {LIGHTING_MODES[mode].upper()}')

    @Slot()
    def cycleLighting(self):
        self.setLightingMode((self.lightingMode + 1) % len(LIGHTING_MODES))

    @Slot(float)
    def adjustZoom(self, delta):
        """Zoom camera in (negative delta, ]) or out (positive delta, [)."""
        nxt = max(-160.0, min(240.0, self._zoom_offset + float(delta)))
        if abs(nxt - self._zoom_offset) > 1e-3:
            self._zoom_offset = nxt
            pct = int(round(100.0 * 468.0 / max(120.0, 468.0 + nxt)))
            self._flash_readout(f'CAMERA ZOOM · {pct}%')

    @Slot(int)
    def setCameraPreset(self, preset):
        preset = int(preset) % len(CAMERA_PRESETS)
        changed = (preset != self._camera_preset) or (abs(self._zoom_offset) > 1e-3)
        self._camera_preset = preset
        self._zoom_offset = 0.0
        if changed:
            self._flash_readout(f'CAMERA · {CAMERA_PRESETS[preset].upper()}')

    @Slot()
    def captureSnapshot(self):
        """Save the current Metal-rendered 3D frame into .captures/ in the app folder."""
        view = self.parent()
        if view is None or not hasattr(view, 'grabWindow'):
            return
        out_dir = Path(__file__).resolve().parents[2] / '.captures'
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = out_dir / f'snap_{stamp}.png'
        img = view.grabWindow()
        if not img.isNull() and img.save(str(path)):
            self._flash_readout(f'SNAPSHOT · .captures/{path.name}')
        else:
            self._flash_readout('SNAPSHOT · FAILED')

    @Slot()
    def exit3D(self):
        self.exitRequested.emit()


class Desk3DHost(QWidget):
    """QWidget container embedding the QQuickWindow hosting QtQuick3D + offscreen CRT source."""
    CRT_SIZE = 512

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.view = QQuickWindow()
        self.controller = Desk3DController(window, self.view)
        self.engine = QQmlEngine(self.view)

        # Offscreen 512x512 CRT item with opaque dark-phosphor glass backing behind GpuScope + PhosphorLayer
        bg_comp = QQmlComponent(self.engine)
        bg_comp.setData(b'import QtQuick\nRectangle { x: -40000; width: 512; height: 512; color: "#0c1811" }\n',
                        QUrl('crt_bg.qml'))
        self.crt_source = bg_comp.create()
        self.crt_source.setParentItem(self.view.contentItem())
        self.controller.bind_crt_backing(self.crt_source)
        self._bg_comp = bg_comp
        self.scope = GpuScope(self.crt_source)
        self.scope.setWidth(self.CRT_SIZE)
        self.scope.setHeight(self.CRT_SIZE)
        try:
            self.phosphor = PhosphorLayer(self.view)
            self.phosphor.root.setParentItem(self.crt_source)
            self.phosphor.root.setWidth(self.CRT_SIZE)
            self.phosphor.root.setHeight(self.CRT_SIZE)
            self.phosphor.resize(self.CRT_SIZE, self.CRT_SIZE)
        except RuntimeError as error:
            self.phosphor = None
            print(f'3D CRT phosphor unavailable: {error}', file=sys.stderr)

        self.crt_host = _CrtHostAdapter(self.view, self.scope, self.phosphor)

        tier = self.controller.textureTier
        high_q = (tier == '2k')
        rate_lbl = self.controller.sampleRateLabel
        crt_glass_tex = CrtGraticuleGlassTexture(show_grid=self.controller.showGrid)
        notepad_tex = NotepadPageTexture(page=0)
        cable_ch1_geom = CoaxCableGeometry(self.controller.cables[0], radius=2.3, sides=12, high_quality=high_q)
        cable_ch2_geom = CoaxCableGeometry(self.controller.cables[1], radius=2.3, sides=12, high_quality=high_q)
        self.controller.bind_props(crt_glass_tex, notepad_tex, [cable_ch1_geom, cable_ch2_geom])

        # Keep strong Python references to 3D meshes & composited faceplate/overlay/wear/prop textures
        self._assets = dict(
            crtMesh=CurvedCrtGeometry(),
            bezelFrameMesh=RoundedBezelFrameGeometry(),
            crtHoodMesh=CrtHoodGeometry(),
            knobMesh=KnurledKnobGeometry(),
            trimKnobMesh=SmallTrimKnobGeometry(),
            keycapMesh=KeycapGeometry(),
            bncMesh=BncJackGeometry(),
            bncPlugMesh=BncPlugCollarGeometry(),
            strainBootMesh=StrainBootGeometry(),
            bailHandleMesh=BailHandleGeometry(),
            screwMesh=FaceplateScrewGeometry(),
            cableCh1Mesh=cable_ch1_geom,
            cableCh2Mesh=cable_ch2_geom,
            lampShadeMesh=HollowLampShadeGeometry(),
            lampShadeInnerMesh=LampShadeInnerGeometry(),
            lampConeMesh=LampVolumetricConeGeometry(),
            lampSpringMesh=HelicalSpringGeometry(coil_radius=3.2, wire_radius=0.72, height=102.0, turns=16.0),
            bobbleSpringMesh=HelicalSpringGeometry(coil_radius=2.6, wire_radius=0.52, height=12.0, turns=5.5),
            lampCordMesh=LampPowerCordGeometry(),
            coffeeMugMesh=CoffeeMugGeometry(),
            mugHandleMesh=MugHandleGeometry(),
            pencilBarrelMesh=HexPencilBarrelGeometry(),
            spareProbeCordMesh=SpareProbeCordGeometry(),
            contactShadowData=ContactShadowTexture(),
            labWallChartData=LabWallChartTexture(),
            sideVentData=SideVentTexture(),
            crtGlassOverlayData=crt_glass_tex,
            bezelSmudgeData=BezelSmudgeTexture(),
            bezelGougeData=BezelGougeTexture(),
            cuttingMatData=CuttingMatTexture(),
            cuttingMatNormalData=CuttingMatNormalTexture(),
            cuttingMatRoughnessData=CuttingMatRoughnessTexture(),
            calStickerData=CalStickerTexture(),
            bobbleFaceData=BobbleheadFaceTexture(),
            notepadPageData=notepad_tex,
            faceplateAlbedoData=ControlFaceplateTexture(tier=tier, sample_rate_label=rate_lbl),
            faceplateEmissiveData=ControlEmissiveTexture(tier=tier, sample_rate_label=rate_lbl),
        )
        ctx = self.engine.rootContext()
        ctx.setContextProperty('deskCtrl', self.controller)
        ctx.setContextProperty('crtSource', self.crt_source)
        for key, obj in self._assets.items():
            ctx.setContextProperty(key, obj)

        self.component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(QML_PATH)))
        self.root_item = self.component.create()
        if self.root_item is None:
            raise RuntimeError('; '.join(e.toString() for e in self.component.errors()))
        self.root_item.setParentItem(self.view.contentItem())
        self.view.widthChanged.connect(self._sync_size)
        self.view.heightChanged.connect(self._sync_size)
        self._sync_size()

        self.qt3d_container = QWidget.createWindowContainer(self.view, self)
        self.pt_viewport = None
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self.qt3d_container)
        self.controller.pathTraceToggled.connect(self._on_path_trace_toggled)

    def _on_path_trace_toggled(self, active):
        if active:
            if self.pt_viewport is None:
                try:
                    from phosphor.raytrace.qt_host import PathTraceViewport
                    self.pt_viewport = PathTraceViewport(self.controller, self)
                    self.pt_viewport.switchToRasterRequested.connect(self.controller.togglePathTrace)
                    self.pt_viewport.exitRequested.connect(self.controller.exit3D)
                    self.controller._path_trace_viewport = self.pt_viewport
                    self._stack.addWidget(self.pt_viewport)
                except Exception as exc:
                    self.controller._path_trace_active = False
                    self.controller._flash_readout(f'PATH TRACER ERROR · {exc}')
                    self.controller.stateChanged.emit()
                    return
            self.controller.set_physics_active(False)
            self._stack.setCurrentWidget(self.pt_viewport)
            self.pt_viewport.set_active(True)
        else:
            if self.pt_viewport is not None:
                self.pt_viewport.set_active(False)
            self._stack.setCurrentWidget(self.qt3d_container)
            self.controller.set_physics_active(True)

    def showEvent(self, event):
        super().showEvent(event)
        if self.controller.pathTraceActive and self.pt_viewport is not None:
            self.pt_viewport.set_active(True)
        else:
            self.controller.set_physics_active(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.controller.set_physics_active(False)
        if self.pt_viewport is not None:
            self.pt_viewport.set_active(False)

    def sync_quality(self):
        """Sync 1K/2K texture tier, spline cable detail, graticule visibility, and live sample-rate faceplate label."""
        tier = self.controller.textureTier
        high_q = (tier == '2k')
        rate_lbl = self.controller.sampleRateLabel
        self._assets['faceplateAlbedoData'].set_params(tier=tier, sample_rate_label=rate_lbl)
        self._assets['faceplateEmissiveData'].set_params(tier=tier, sample_rate_label=rate_lbl)
        self._assets['cableCh1Mesh'].set_high_quality(high_q)
        self._assets['cableCh2Mesh'].set_high_quality(high_q)
        self.controller.refresh()

    def _sync_size(self, *args):
        self.root_item.setWidth(self.view.width())
        self.root_item.setHeight(self.view.height())

    def release_phosphor(self):
        self.controller.set_physics_active(False)
        if getattr(self, 'root_item', None) is not None:
            self.root_item.setParentItem(None)
        self.crt_host.release_phosphor()
