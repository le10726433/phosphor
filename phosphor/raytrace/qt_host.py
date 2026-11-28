"""Interactive PySide6 viewport hosting the cross-platform wgpu Compute BVH Path Tracer
(macOS Metal & Windows Direct3D 12 / Vulkan) with zero-copy GPU swapchain presentation
and live oscilloscope phosphor streaming."""
from datetime import datetime
import math
from pathlib import Path
import sys
import time
import numpy as np

# NOTE: PySide6 MUST be imported before `rendercanvas.qt` so rendercanvas selects PySide6.
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from rendercanvas.qt import QRenderWidget

from phosphor.raytrace.engine import PathTracerEngine, make_demo_crt_image


class LiveCrtPhosphorBuffer:
    """Fast 512x512 float32 phosphor persistence & bloom rasterizer for streaming live audio
    waveforms into the path tracer's curved CRT area light (< 0.5 ms per frame on CPU)."""

    def __init__(self, size=512):
        self.size = int(size)
        self.fast = np.zeros((self.size, self.size), dtype=np.float32)
        self.slow = np.zeros((self.size, self.size), dtype=np.float32)
        self.rgba = np.zeros((self.size, self.size, 4), dtype=np.uint8)
        self.rgba[:, :, 3] = 255
        self._has_live = False

    def deposit(self, signal, traces=None, elapsed=1.0 / 60.0, beam_color=(92, 230, 119),
                bg_color=(12, 24, 17), intensity=1.0):
        dt = max(1e-3, min(0.1, float(elapsed)))
        # Authentic P31 dual-component exponential phosphor decay
        self.fast *= math.exp(-dt / 0.042)
        self.slow *= math.exp(-dt / 0.24)

        S = self.size
        pts = None
        if traces:
            chunks = []
            for tr in traces:
                arr = np.asarray(tr[0] if isinstance(tr, tuple) else tr, dtype=np.float32)
                if arr.ndim == 2 and arr.shape[0] > 0 and arr.shape[1] >= 2:
                    chunks.append(arr[:, :2])
            if chunks:
                raw = np.vstack(chunks)
                px = np.clip(raw[:, 0], 1, S - 2).astype(np.int32)
                py = np.clip(raw[:, 1], 1, S - 2).astype(np.int32)
                pts = (px, py)
        elif signal is not None:
            arr = np.asarray(signal, dtype=np.float32)
            if arr.ndim == 2 and arr.shape[0] > 1 and arr.shape[1] >= 2:
                step = max(1, arr.shape[0] // 2400)
                sub = arr[::step, :2]
                finite = np.isfinite(sub[:, 0]) & np.isfinite(sub[:, 1])
                sub = sub[finite]
                if len(sub) > 1:
                    half = 0.45 * S
                    px = np.clip((sub[:, 0] * half + S * 0.5).astype(np.int32), 2, S - 3)
                    py = np.clip((S * 0.5 - sub[:, 1] * half).astype(np.int32), 2, S - 3)
                    pts = (px, py)

        gain = max(0.15, float(intensity)) * 0.42
        if pts is not None:
            px, py = pts
            self._has_live = True
            for dy, dx, w in ((0, 0, 1.0), (-1, 0, 0.55), (1, 0, 0.55), (0, -1, 0.55), (0, 1, 0.55)):
                np.add.at(self.fast, (py + dy, px + dx), w * gain)
            np.add.at(self.slow, (py, px), 0.35 * gain)
        elif not self._has_live:
            return None

        np.clip(self.fast, 0.0, 4.0, out=self.fast)
        np.clip(self.slow, 0.0, 2.5, out=self.slow)

        energy = self.fast + self.slow * 0.65
        # Fast separable phosphor halo bloom
        halo = energy
        for step in (2, 5, 10):
            halo = 0.25 * (np.roll(halo, step, 0) + np.roll(halo, -step, 0)
                           + np.roll(halo, step, 1) + np.roll(halo, -step, 1))
        glow = np.clip(energy * 0.85 + halo * 1.15, 0.0, 2.4)
        core = np.clip((energy - 0.55) * 0.72, 0.0, 1.0)

        br, bg, bb = bg_color
        cr, cg, cb = beam_color
        self.rgba[:, :, 0] = np.clip(br + glow * cr + core * 165.0, 0, 255).astype(np.uint8)
        self.rgba[:, :, 1] = np.clip(bg + glow * cg + core * 165.0, 0, 255).astype(np.uint8)
        self.rgba[:, :, 2] = np.clip(bb + glow * cb + core * 165.0, 0, 255).astype(np.uint8)
        return self.rgba


_HUD_BTN_STYLE = (
    "QPushButton { background: #26241f; color: #ddd7c5; border: 1px solid #4f4a3e;"
    " border-radius: 4px; padding: 3px 10px; font-family: 'JetBrains Mono', monospace; font-size: 11px; }"
    "QPushButton:hover { background: #38352d; }"
    "QPushButton:checked { background: #217a3e; color: #ffffff; border: 1px solid #4ef278; font-weight: bold; }"
)


class _PathTraceCanvas(QRenderWidget):
    """Zero-copy wgpu QRenderWidget (CAMetalLayer on macOS, DXGI HWND on Windows)
    that delegates keyboard, drag-tilt, and wheel controls cleanly to PathTraceViewport."""

    def __init__(self, host, parent=None, present_method='screen'):
        self._host = host
        super().__init__(
            parent,
            size=(800, 500),
            update_mode='ondemand',
            max_fps=60.0,
            vsync=True,
            present_method=present_method,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def keyPressEvent(self, event):
        self._host.keyPressEvent(event)

    def mousePressEvent(self, event):
        self._host._on_canvas_mouse_press(event)

    def mouseMoveEvent(self, event):
        self._host._on_canvas_mouse_move(event)

    def mouseReleaseEvent(self, event):
        self._host._on_canvas_mouse_release(event)

    def mouseDoubleClickEvent(self, event):
        self._host._on_canvas_double_click(event)

    def wheelEvent(self, event):
        self._host._on_canvas_wheel(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._host._on_canvas_resized()


class PathTraceViewport(QWidget):
    """Interactive PySide6 progressive GPU Path Tracing viewport (macOS Metal & Windows DX12/Vulkan)."""
    switchToRasterRequested = Signal()
    exitRequested = Signal()

    def __init__(self, controller, parent=None, present_method=None):
        super().__init__(parent)
        self.controller = controller
        self._requested_present_method = present_method
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet('background: #0a0b0d;')

        tier = controller.textureTier
        # Adaptive internal pixel budget: crisp linear GPU blit while maintaining ~40-60 FPS interactive!
        self._pixel_budget = 400_000 if tier == '2k' else 256_000
        init_w, init_h = (800, 500) if tier == '2k' else (640, 400)

        self.engine = PathTracerEngine(width=init_w, height=init_h, sample_rate_label=controller.sampleRateLabel)
        self.crt_buf = LiveCrtPhosphorBuffer(size=512)
        self.engine.update_crt_texture(make_demo_crt_image(512))

        self._active = False
        self._max_idle_spp = 128
        self._live_playing = False
        self._dragging = False
        self._drag_last_pos = None
        self._needs_trace = True
        self._last_tick_t = 0.0
        self._fps_ema = 0.0
        self._drawn_frames = 0

        self._build_layout()
        self._sync_from_controller(reset=True)

        # Configure zero-copy native swapchain (CAMetalLayer on macOS, DXGI on Windows)
        self.wgpu_ctx = self.canvas.get_wgpu_context()
        self.engine.configure_surface(self.wgpu_ctx)
        self.canvas.request_draw(self._draw_frame)

        # Vsync-friendly progressive accumulation ticker (16 ms -> up to 60 FPS)
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(16)
        self._render_timer.timeout.connect(self._on_tick)

    def _build_layout(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top non-overlapping status strip (guarantees zero native CAMetalLayer / HWND z-order bugs!)
        top_bar = QWidget(self)
        top_bar.setFixedHeight(30)
        top_bar.setStyleSheet('background: #141311; border-bottom: 1px solid #2e2b24;')
        top_row = QHBoxLayout(top_bar)
        top_row.setContentsMargins(12, 2, 12, 2)
        self.badge = QLabel(f'PATH TRACED · {self.engine.backend_name.upper()} · 0 SPP')
        self.badge.setStyleSheet(
            "background: #111b13; color: #8ff08a; border: 1px solid #308c4a;"
            " border-radius: 3px; padding: 2px 10px; font-family: 'JetBrains Mono', monospace;"
            " font-size: 11px; font-weight: bold;"
        )
        hint = QLabel('Drag to tilt · Scroll zoom · Dbl-click reset · P snapshot')
        hint.setStyleSheet(
            "color: #9e9788; font-family: 'JetBrains Mono', monospace; font-size: 10px; border: none;"
        )
        top_row.addWidget(self.badge)
        top_row.addStretch(1)
        top_row.addWidget(hint)
        root.addWidget(top_bar)

        # Center native zero-copy GPU canvas
        if self._requested_present_method is not None:
            self._present_mode = self._requested_present_method
        else:
            use_screen = (
                sys.platform in ('darwin', 'win32')
                and QApplication.platformName() != 'offscreen'
            )
            self._present_mode = 'screen' if use_screen else 'bitmap'
        self.canvas = _PathTraceCanvas(self, parent=self, present_method=self._present_mode)
        root.addWidget(self.canvas, 1)

        # Bottom non-overlapping HUD control bar matching desk3d.qml
        bot_bar = QWidget(self)
        bot_bar.setFixedHeight(40)
        bot_bar.setStyleSheet('background: #141311; border-top: 1px solid #2e2b24;')
        hb = QHBoxLayout(bot_bar)
        hb.setContentsMargins(12, 4, 12, 4)
        hb.setSpacing(6)
        hb.addStretch(1)

        self._cam_btns = []
        for idx, lbl in enumerate(('1 Desk', '2 Scope', '3 Controls')):
            b = QPushButton(lbl, bot_bar)
            b.setCheckable(True)
            b.setStyleSheet(_HUD_BTN_STYLE)
            b.clicked.connect(lambda _=False, i=idx: self._set_preset(i))
            hb.addWidget(b)
            self._cam_btns.append(b)

        self.btn_light = QPushButton('L · Lighting', bot_bar)
        self.btn_light.setStyleSheet(_HUD_BTN_STYLE)
        self.btn_light.clicked.connect(self._cycle_lighting)
        hb.addWidget(self.btn_light)

        self.btn_denoise = QPushButton('D · Denoise', bot_bar)
        self.btn_denoise.setCheckable(True)
        self.btn_denoise.setChecked(True)
        self.btn_denoise.setStyleSheet(_HUD_BTN_STYLE)
        self.btn_denoise.clicked.connect(self._toggle_denoise)
        hb.addWidget(self.btn_denoise)

        self.btn_fix = QPushButton('F · Camera Fix', bot_bar)
        self.btn_fix.setCheckable(True)
        self.btn_fix.setStyleSheet(_HUD_BTN_STYLE)
        self.btn_fix.clicked.connect(self._toggle_camera_fix)
        hb.addWidget(self.btn_fix)

        self.btn_raster = QPushButton('T · Qt3D Bench', bot_bar)
        self.btn_raster.setStyleSheet(_HUD_BTN_STYLE)
        self.btn_raster.clicked.connect(self.switchToRasterRequested.emit)
        hb.addWidget(self.btn_raster)

        self.btn_exit = QPushButton('Esc · 2D Editor', bot_bar)
        self.btn_exit.setStyleSheet(_HUD_BTN_STYLE)
        self.btn_exit.clicked.connect(self.exitRequested.emit)
        hb.addWidget(self.btn_exit)

        hb.addStretch(1)
        root.addWidget(bot_bar)
        self._sync_hud_buttons()

    def _sync_hud_buttons(self):
        for i, b in enumerate(self._cam_btns):
            b.setChecked(self.engine.camera_preset == i)
        self.btn_denoise.setChecked(self.engine.denoise_enabled)
        self.btn_fix.setChecked(self.controller.cameraFixed)

    def _compute_aspect_resolution(self):
        cw = max(128, self.canvas.width())
        ch = max(96, self.canvas.height())
        aspect = cw / float(ch)
        h = math.sqrt(self._pixel_budget / max(0.25, aspect))
        w = h * aspect
        # Round to multiples of 8 (compute shader workgroup size) within safe bounds
        rw = max(320, min(1152, int(round(w / 8.0)) * 8))
        rh = max(200, min(720, int(round(h / 8.0)) * 8))
        return rw, rh

    def _on_canvas_resized(self):
        rw, rh = self._compute_aspect_resolution()
        if abs(rw - self.engine.width) >= 16 or abs(rh - self.engine.height) >= 16:
            self.engine.resize(rw, rh)
            self._needs_trace = True
            if self._active:
                self.canvas.request_draw()

    def _sync_from_controller(self, reset=False):
        c = self.controller
        changed = (
            self.engine.lamp_on != c.deskLampOn
            or self.engine.fluor_on != c.fluorescentOn
            or self.engine.camera_preset != c.cameraPreset
            or abs(self.engine.zoom_offset - c.zoomOffset) > 1e-2
            or self.engine.show_grid != c.showGrid
            or abs(self.engine.crt_power - c.crtPowerLevel) > 1e-2
        )
        self.engine.lamp_on = c.deskLampOn
        self.engine.fluor_on = c.fluorescentOn
        self.engine.camera_preset = c.cameraPreset
        self.engine.zoom_offset = c.zoomOffset
        self.engine.show_grid = c.showGrid
        self.engine.crt_power = c.crtPowerLevel
        self.engine.exposure = 1.02 if c.fluorescentOn else (1.18 if c.deskLampOn else 1.35)
        self._live_playing = bool(c.playing)
        if reset or changed:
            self.engine.reset_accumulation()
            self._needs_trace = True

    def set_active(self, active):
        self._active = bool(active)
        if self._active:
            self._on_canvas_resized()
            self._sync_from_controller(reset=True)
            self._sync_hud_buttons()
            self._last_tick_t = 0.0
            self.canvas.setFocus()
            self.canvas.request_draw()
            if not self._render_timer.isActive():
                self._render_timer.start()
        else:
            self._render_timer.stop()

    def push_signal(self, signal, traces=None, elapsed=1.0 / 60.0):
        if not self._active:
            return
        c = self.controller
        col = c.crtColor
        bg = c.crtBgColor
        rgba = self.crt_buf.deposit(
            signal, traces=traces, elapsed=elapsed,
            beam_color=(col.red(), col.green(), col.blue()),
            bg_color=(bg.red(), bg.green(), bg.blue()),
            intensity=c.crtGlow,
        )
        if rgba is not None:
            self._live_playing = bool(c.playing)
            self.engine.update_crt_texture(rgba, reset_accum=False)
            self._needs_trace = True

    def _on_tick(self):
        if not self._active:
            return
        self._sync_from_controller(reset=False)
        if self._live_playing or self.engine.total_spp < self._max_idle_spp or self._needs_trace:
            self.canvas.request_draw()

    def _draw_frame(self):
        if not self._active:
            return
        self._sync_from_controller(reset=False)
        # During live audio playback use smooth EMA (12.5 eff SPP) so moving CRT GI stays silky & responsive
        self.engine.ema_floor = 0.08 if self._live_playing else 0.0

        do_trace = self._live_playing or self.engine.total_spp < self._max_idle_spp or self._needs_trace
        self._needs_trace = False

        now = time.perf_counter()
        if do_trace and self._last_tick_t > 0.0:
            dt = max(1e-3, now - self._last_tick_t)
            inst_fps = 1.0 / dt
            self._fps_ema = inst_fps if self._fps_ema <= 0.1 else (0.85 * self._fps_ema + 0.15 * inst_fps)
        self._last_tick_t = now

        # Single-submit GPU pipeline: Trace (1 SPP) -> 4-pass A-Trous Denoise -> Zero-Copy Swapchain Blit!
        self.engine.step_and_present(self.wgpu_ctx, spp=1, trace=do_trace, resolve=do_trace)
        self._drawn_frames += 1

        mode_lbl = 'FLUORESCENT' if self.engine.fluor_on else ('TUNGSTEN LAMP' if self.engine.lamp_on else 'DARK CRT GI')
        if self._live_playing:
            status = f'LIVE · {self._fps_ema:.0f} FPS' if self._fps_ema > 1.0 else 'LIVE'
        elif self.engine.total_spp >= self._max_idle_spp:
            status = f'{self.engine.total_spp} SPP · CONVERGED'
        else:
            fps_s = f' · {self._fps_ema:.0f} FPS' if self._fps_ema > 1.0 else ''
            status = f'{self.engine.total_spp} SPP{fps_s}'
        self.badge.setText(f'PATH TRACED · {self.engine.backend_name.upper()} · {mode_lbl} · {status}')

    def _set_preset(self, idx):
        self.controller.setCameraPreset(idx)
        self._sync_from_controller(reset=True)
        self._sync_hud_buttons()
        self.canvas.request_draw()

    def _cycle_lighting(self):
        self.controller.cycleLighting()
        self._sync_from_controller(reset=True)
        self.canvas.request_draw()

    def _toggle_denoise(self):
        self.engine.denoise_enabled = not self.engine.denoise_enabled
        self.engine.resolve_output()
        self._sync_hud_buttons()
        self.canvas.request_draw()

    def _toggle_camera_fix(self):
        self.controller.toggleCameraFix()
        if self.controller.cameraFixed:
            self.engine.parallax_x = 0.0
            self.engine.parallax_y = 0.0
            self.engine.reset_accumulation()
            self._needs_trace = True
        self._sync_hud_buttons()
        self.canvas.request_draw()

    def capture_snapshot(self):
        rgba = self.engine.read_rgba()
        h, w = rgba.shape[:2]
        img = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
        out_dir = Path(__file__).resolve().parents[2] / '.captures'
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        path = out_dir / f'pathtrace_{stamp}.png'
        img.save(str(path))
        self.badge.setText(f'SAVED · .captures/{path.name}')

    # --- Click-drag camera tilt (passive hover never wipes progressive accumulation!) ---
    def _on_canvas_mouse_press(self, event):
        self.canvas.setFocus()
        if event.button() == Qt.MouseButton.LeftButton and not self.controller.cameraFixed:
            self._dragging = True
            self._drag_last_pos = event.position()

    def _on_canvas_mouse_move(self, event):
        if not self._dragging or self.controller.cameraFixed or self._drag_last_pos is None:
            return
        pos = event.position()
        cw = max(1.0, float(self.canvas.width()))
        ch = max(1.0, float(self.canvas.height()))
        dx = (pos.x() - self._drag_last_pos.x()) / cw * 2.4
        dy = (pos.y() - self._drag_last_pos.y()) / ch * 2.4
        self._drag_last_pos = pos
        if abs(dx) > 1e-4 or abs(dy) > 1e-4:
            self.engine.parallax_x = max(-1.2, min(1.2, self.engine.parallax_x + dx))
            self.engine.parallax_y = max(-1.2, min(1.2, self.engine.parallax_y + dy))
            self.engine.reset_accumulation()
            self._needs_trace = True
            self.canvas.request_draw()

    def _on_canvas_mouse_release(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._drag_last_pos = None

    def _on_canvas_double_click(self, event):
        if not self.controller.cameraFixed:
            self.engine.parallax_x = 0.0
            self.engine.parallax_y = 0.0
            self.engine.reset_accumulation()
            self._needs_trace = True
            self.canvas.request_draw()

    def _on_canvas_wheel(self, event):
        delta = event.angleDelta().y()
        if delta != 0:
            self.controller.adjustZoom(-24.0 if delta > 0 else 24.0)
            self._sync_from_controller(reset=True)
            self.canvas.request_draw()

    def keyPressEvent(self, event):
        k = event.key()
        if k == Qt.Key.Key_1:
            self._set_preset(0)
        elif k == Qt.Key.Key_2:
            self._set_preset(1)
        elif k == Qt.Key.Key_3:
            self._set_preset(2)
        elif k == Qt.Key.Key_BracketLeft:
            self.controller.adjustZoom(36.0)
            self._sync_from_controller(reset=True)
            self.canvas.request_draw()
        elif k == Qt.Key.Key_BracketRight:
            self.controller.adjustZoom(-36.0)
            self._sync_from_controller(reset=True)
            self.canvas.request_draw()
        elif k == Qt.Key.Key_L:
            self._cycle_lighting()
        elif k == Qt.Key.Key_D:
            self._toggle_denoise()
        elif k == Qt.Key.Key_F:
            self._toggle_camera_fix()
        elif k == Qt.Key.Key_G:
            self.controller.pressButton('btn_grid')
            self._sync_from_controller(reset=True)
            self.canvas.request_draw()
        elif k == Qt.Key.Key_Space:
            self.controller.pressButton('btn_play')
        elif k in (Qt.Key.Key_P, Qt.Key.Key_F12):
            self.capture_snapshot()
        elif k == Qt.Key.Key_T:
            self.switchToRasterRequested.emit()
        elif k == Qt.Key.Key_Escape:
            self.exitRequested.emit()
        else:
            super().keyPressEvent(event)
