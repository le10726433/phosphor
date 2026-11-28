"""Interactive 3D lab bench props, Verlet coax cable physics, bobblehead spring physics,
and high-DPI textures for Phosphor's late-80s C1-170 3D Photorealistic Mode.

Includes:
  - VerletCoaxCable + CoaxCableGeometry: real-time 3D Verlet rope physics for CH1 X / CH2 Y
    coaxial cables (grab, wiggle, pull past threshold to unplug, snap back to replug).
  - BobbleheadSpring: damped 2-axis spring-pendulum physics for the desk bobblehead.
  - CuttingMatTexture: worn green self-healing lab cutting mat with grid & angle guides.
  - CalStickerTexture: vintage peeling 'CAL DUE: NOV 1989' calibration sticker.
  - NotepadPageTexture: 5-page tattered paper notepad (Page 1: Voynich Manuscript pencil
    drawings; Pages 2–5: real math derivations for Phosphor's built-in recurrence equations,
    haphazard 3D solid doodles, and coffee ring stains).
  - BobbleheadFaceTexture: cartoonish face texture with bold X-ed out eyes.
  - CrtGraticuleGlassTexture: floating cover-glass etched graticule + studio sheen for true
    glass-thickness parallax in front of the curved phosphor layer.
"""
import math
import numpy as np
from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPolygonF, QRadialGradient, QVector3D)
from PySide6.QtQuick3D import QQuick3DGeometry, QQuick3DTextureData

from phosphor.editor.desk3d_assets import _pack_geometry, _qimage_to_rgba, _upload_rgba


# ==============================================================================
# 1. VERLET COAXIAL CABLE PHYSICS + DYNAMIC 3D TUBE MESH
# ==============================================================================

class VerletCoaxCable:
    """16-node Verlet rope simulation with bending stiffness, desk collision, sleeping,
    and unplugging/replugging into any open front-panel BNC port (supporting X/Y input swapping).

    Node 0 is the movable BNC plug end (pinned to the active port's socket while plugged in).
    Node N-1 is anchored at the front-edge desk cable exit (`anchor_pos`).
    """
    UNPLUG_DIST = 34.0
    REPLUG_DIST = 38.0
    DESK_Y = -126.0
    SLEEP_EPS = 0.018

    def __init__(self, sockets, initial_port, anchor_pos, nodes=16, slack=1.015):
        self.sockets = [np.array(s, dtype=np.float32) for s in sockets]
        self.port_idx = int(initial_port) if initial_port is not None else None
        self.anchor = np.array(anchor_pos, dtype=np.float32)
        self.n = int(nodes)
        ref_socket = self.sockets[self.port_idx if self.port_idx is not None else 0]
        span = float(np.linalg.norm(self.anchor - ref_socket))
        self.seg_len = (span * float(slack)) / max(1, self.n - 1)
        self.grabbed = False
        self.grab_target = ref_socket.copy()
        self.sleeping = False

        # Initialize hanging catenary curve between socket and front desk edge, then pre-settle
        t = np.linspace(0.0, 1.0, self.n, dtype=np.float32)[:, None]
        pts = ref_socket[None, :] * (1.0 - t) + self.anchor[None, :] * t
        pts[:, 1] -= np.sin(t[:, 0] * math.pi) * 28.0
        pts[:, 2] += np.sin(t[:, 0] * math.pi) * 18.0
        pts[:, 1] = np.maximum(pts[:, 1], self.DESK_Y)
        pts[0] = ref_socket
        pts[-1] = self.anchor
        self.pos = pts.copy()
        self.prev = pts.copy()

        # Pre-settle so plugged cables hang completely still from frame 0 (no startup jitter)
        for _ in range(90):
            self.step(1.0 / 60.0)
        self.prev = self.pos.copy()
        self.sleeping = True

    @property
    def plugged(self):
        return self.port_idx is not None

    @property
    def socket(self):
        idx = self.port_idx if self.port_idx is not None else 0
        return self.sockets[idx]

    @property
    def plug_pos(self):
        return self.pos[0]

    @property
    def plug_dir(self):
        """Unit direction vector pointing from cable body into the plug tip."""
        if self.plugged and not self.grabbed:
            return np.array([0.0, 0.0, -1.0], dtype=np.float32)
        v = self.pos[0] - self.pos[1]
        n = float(np.linalg.norm(v))
        return v / n if n > 1e-4 else np.array([0.0, 0.0, -1.0], dtype=np.float32)

    def wake(self):
        self.sleeping = False

    def grab(self, target_pos=None):
        self.grabbed = True
        self.sleeping = False
        self.grab_target = np.array(target_pos if target_pos is not None else self.pos[0], dtype=np.float32)

    def move_grab(self, target_pos):
        """Move grabbed plug toward target_pos; unplugs if pulled beyond UNPLUG_DIST from its port."""
        self.sleeping = False
        tgt = np.array(target_pos, dtype=np.float32)
        tgt[1] = max(self.DESK_Y + 2.0, tgt[1])
        self.grab_target = tgt
        if self.plugged:
            sock = self.socket
            dist = float(np.linalg.norm(tgt - sock))
            if dist > self.UNPLUG_DIST:
                self.port_idx = None
                recoil = (tgt - sock) / max(1.0, dist) * 4.5
                self.pos[0] = tgt
                self.prev[0] = tgt - recoil
                return 'unplugged'
            flex = (tgt - sock) * 0.16
            self.pos[0] = sock + flex
            self.prev[0] = self.pos[0]
            self.pos[1] += flex * 0.25
            return 'tugging'
        else:
            self.pos[0] = tgt
            self.prev[0] = tgt * 0.65 + self.prev[0] * 0.35
            return 'dragging'

    def nearest_open_port(self, occupied_ports=()):
        """Return (port_idx, dist) for the closest unoccupied BNC port, or (None, inf)."""
        best_port, best_dist = None, float('inf')
        for idx, sock in enumerate(self.sockets):
            if idx in occupied_ports:
                continue
            # Emphasize XY alignment with the jack on screen so dragging onto a port feels generous
            d = self.pos[0] - sock
            dist = math.sqrt(float(d[0] * d[0] + d[1] * d[1] + 0.35 * d[2] * d[2]))
            if dist < best_dist:
                best_dist = dist
                best_port = idx
        return best_port, best_dist

    def release(self, occupied_ports=()):
        """Release grabbed plug; snaps into any open BNC port within REPLUG_DIST."""
        self.grabbed = False
        self.sleeping = False
        if not self.plugged:
            port_idx, dist = self.nearest_open_port(occupied_ports)
            if port_idx is not None and dist <= self.REPLUG_DIST:
                self.port_idx = port_idx
                self.pos[0] = self.sockets[port_idx].copy()
                self.prev[0] = self.pos[0].copy()
                return ('replugged', port_idx)
        else:
            self.pos[0] = self.socket.copy()
            self.prev[0] = self.pos[0].copy()
        return ('released', self.port_idx)

    def replug(self, port_idx):
        self.port_idx = int(port_idx)
        self.grabbed = False
        self.sleeping = False
        self.pos[0] = self.sockets[self.port_idx].copy()
        self.prev[0] = self.pos[0].copy()

    def step(self, dt=1.0 / 60.0):
        """Advance Verlet integration with strong damping, floor friction, and auto-sleep.
        Returns True if the mesh moved and needs re-uploading."""
        if self.sleeping and not self.grabbed:
            return False

        dt = max(1e-3, min(0.033, float(dt)))
        vel = (self.pos - self.prev) * 0.84
        # Extra friction for nodes resting on the desk so they settle cleanly without buzzing
        on_desk = self.pos[:, 1] <= (self.DESK_Y + 0.35)
        vel[on_desk] *= 0.45

        before = self.pos.copy()
        self.prev = self.pos.copy()
        self.pos += vel
        self.pos[:, 1] -= 390.0 * (dt * dt)

        for _ in range(8):
            if self.plugged and not self.grabbed:
                self.pos[0] = self.socket
            elif self.grabbed:
                if self.plugged:
                    flex = (self.grab_target - self.socket) * 0.16
                    self.pos[0] = self.socket + flex
                else:
                    self.pos[0] = self.grab_target
            self.pos[-1] = self.anchor

            for i in range(self.n - 1):
                d = self.pos[i + 1] - self.pos[i]
                dist = float(np.linalg.norm(d))
                if dist > 1e-5:
                    diff = (dist - self.seg_len) / dist
                    w0 = 0.0 if (i == 0 and (self.plugged or self.grabbed)) else 0.5
                    w1 = 0.0 if (i + 1 == self.n - 1) else 0.5
                    wsum = w0 + w1
                    if wsum > 0.0:
                        self.pos[i] += d * (diff * (w0 / wsum))
                        self.pos[i + 1] -= d * (diff * (w1 / wsum))

            # Gentle bending stiffness: full 3D in air, horizontal XZ smoothing on desk so cables never zigzag
            for i in range(1, self.n - 1):
                mid = 0.5 * (self.pos[i - 1] + self.pos[i + 1])
                if self.pos[i, 1] > self.DESK_Y + 0.5:
                    self.pos[i] += (mid - self.pos[i]) * 0.08
                else:
                    self.pos[i, 0] += (mid[0] - self.pos[i, 0]) * 0.25
                    self.pos[i, 2] += (mid[2] - self.pos[i, 2]) * 0.25

            self.pos[:, 1] = np.maximum(self.pos[:, 1], self.DESK_Y)
            if self.plugged and not self.grabbed:
                self.pos[0] = self.socket
            self.pos[-1] = self.anchor

        max_disp = float(np.max(np.linalg.norm(self.pos - before, axis=1)))
        if not self.grabbed and max_disp < self.SLEEP_EPS:
            self.prev = self.pos.copy()
            self.sleeping = True
            return False
        return True


def _catmull_rom_subdivide(points, substeps=3):
    """Smoothly subdivide a 3D polyline using centripetal-like Catmull-Rom interpolation."""
    pts = np.asarray(points, dtype=np.float32)
    n = len(pts)
    if n < 3 or substeps <= 1:
        return pts
    ext = np.vstack([pts[0:1] * 2.0 - pts[1:2], pts, pts[-1:] * 2.0 - pts[-2:-1]])
    out = []
    ts = np.linspace(0.0, 1.0, substeps, endpoint=False, dtype=np.float32)
    for i in range(n - 1):
        p0, p1, p2, p3 = ext[i], ext[i + 1], ext[i + 2], ext[i + 3]
        for t in ts:
            t2 = t * t
            t3 = t2 * t
            q = 0.5 * ((2.0 * p1)
                       + (-p0 + p2) * t
                       + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
                       + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3)
            out.append(q)
    out.append(pts[-1])
    return np.asarray(out, dtype=np.float32)


def build_tube_along_polyline(points, radius=2.3, sides=8):
    """Sweep a smooth outward-wound circular cross-section tube along a 3D polyline."""
    pts = np.asarray(points, dtype=np.float32)
    n = len(pts)
    pos_list = np.empty((n, sides + 1, 3), dtype=np.float32)
    nrm_list = np.empty((n, sides + 1, 3), dtype=np.float32)
    uv_list = np.empty((n, sides + 1, 2), dtype=np.float32)

    angles = np.linspace(0.0, 2.0 * math.pi, sides + 1, dtype=np.float32)
    ca, sa = np.cos(angles), np.sin(angles)

    # Parallel-transport frame along the cable so there is zero twist flipping
    prev_n = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    for i in range(n):
        if i == 0:
            t = pts[1] - pts[0]
        elif i == n - 1:
            t = pts[-1] - pts[-2]
        else:
            t = pts[i + 1] - pts[i - 1]
        tl = float(np.linalg.norm(t))
        t = t / tl if tl > 1e-5 else np.array([0.0, 0.0, 1.0], dtype=np.float32)

        # Orthogonalize prev_n against tangent t
        normal = prev_n - t * float(np.dot(prev_n, t))
        nl = float(np.linalg.norm(normal))
        if nl < 1e-4:
            ref = np.array([1.0, 0.0, 0.0], dtype=np.float32) if abs(t[0]) < 0.8 else np.array([0.0, 1.0, 0.0], dtype=np.float32)
            normal = np.cross(t, ref)
            nl = float(np.linalg.norm(normal)) or 1.0
        normal = normal / nl
        prev_n = normal
        binorm = np.cross(t, normal)

        ring_n = ca[:, None] * normal[None, :] + sa[:, None] * binorm[None, :]
        pos_list[i] = pts[i][None, :] + ring_n * float(radius)
        nrm_list[i] = ring_n
        uv_list[i, :, 0] = angles / (2.0 * math.pi)
        uv_list[i, :, 1] = i / max(1.0, float(n - 1))

    stride = sides + 1
    j, k = np.meshgrid(np.arange(n - 1, dtype=np.uint32), np.arange(sides, dtype=np.uint32), indexing='ij')
    v0 = j * stride + k
    v1 = v0 + 1
    v2 = v0 + stride
    v3 = v2 + 1
    indices = np.stack([v0, v1, v3, v0, v3, v2], axis=-1).reshape(-1)
    return pos_list.reshape(-1, 3), nrm_list.reshape(-1, 3), uv_list.reshape(-1, 2), indices


class CoaxCableGeometry(QQuick3DGeometry):
    """Dynamic 3D coaxial cable mesh updated from a VerletCoaxCable each physics step.

    Couples Catmull-Rom spline smoothness and radial tessellation to quality tier, and
    drapes a natural tail over the oak workbench front bullnose lip (z ~ 230).
    """
    def __init__(self, cable, radius=2.3, sides=12, high_quality=True, parent=None):
        super().__init__(parent)
        self.cable = cable
        self.radius = radius
        self.sides = sides
        self._high_quality = bool(high_quality)
        self.sync_mesh()

    def set_high_quality(self, high_quality):
        flag = bool(high_quality)
        if flag != self._high_quality:
            self._high_quality = flag
            self.sync_mesh()

    def sync_mesh(self):
        pts = self.cable.pos
        # Append a smooth natural drape tail over the oak bench bullnose edge (z=230) so cables never end mid-air
        anc = pts[-1]
        tail = np.array([
            [anc[0] + (anc[0] - pts[-2][0]) * 0.45, -127.6, 228.0],
            [anc[0] + (anc[0] - pts[-2][0]) * 0.70, -133.5, 237.5],
            [anc[0] + (anc[0] - pts[-2][0]) * 0.85, -152.0, 242.0],
            [anc[0] + (anc[0] - pts[-2][0]) * 0.90, -184.0, 243.0],
        ], dtype=np.float32)
        full_pts = np.vstack([pts, tail])
        if self._high_quality:
            full_pts = _catmull_rom_subdivide(full_pts, substeps=3)
            sides = max(10, int(self.sides))
        else:
            sides = 8
        _pack_geometry(self, *build_tube_along_polyline(full_pts, radius=self.radius, sides=sides))


# ==============================================================================
# 2. BOBBLEHEAD DAMPED SPRING-PENDULUM PHYSICS
# ==============================================================================

class BobbleheadSpring:
    """2-axis damped harmonic spring for a cartoonish bobblehead head."""
    def __init__(self):
        self.rx = 0.0
        self.rz = 0.0
        self.ry = 0.0
        self.vx = 0.0
        self.vz = 0.0
        self.vy = 0.0
        self._rng = np.random.default_rng(2026)

    def poke(self):
        """Give the bobblehead a lively random wobble impulse."""
        ang = float(self._rng.uniform(0.0, 2.0 * math.pi))
        strength = float(self._rng.uniform(220.0, 310.0))
        self.vx += math.cos(ang) * strength
        self.vz += math.sin(ang) * strength
        self.vy += float(self._rng.uniform(-140.0, 140.0))

    @property
    def is_moving(self):
        return (abs(self.rx) + abs(self.rz) + abs(self.ry)
                + abs(self.vx) + abs(self.vz) + abs(self.vy)) > 0.08

    def step(self, dt=1.0 / 60.0):
        dt = max(1e-3, min(0.033, float(dt)))
        k, c = 115.0, 2.6  # Spring stiffness & light underdamped wobble
        for attr, vattr in (('rx', 'vx'), ('rz', 'vz'), ('ry', 'vy')):
            x = getattr(self, attr)
            v = getattr(self, vattr)
            acc = -k * x - c * v
            v += acc * dt
            x += v * dt
            x = max(-38.0, min(38.0, x))
            setattr(self, attr, x)
            setattr(self, vattr, v)


# ==============================================================================
# 3. HIGH-DPI PROP & OPTICAL TEXTURES
# ==============================================================================

class CuttingMatTexture(QQuick3DTextureData):
    """1024x512 worn dark-emerald self-healing lab cutting mat with grid, angles & knife scuffs."""
    def __init__(self, parent=None):
        super().__init__(parent)
        W, H = 1024, 512
        rng = np.random.default_rng(1986)
        grain = rng.normal(0.0, 3.2, (H, W)).astype(np.float32)
        base = np.array([26.0, 72.0, 52.0], dtype=np.float32)
        rgb = np.clip(base[None, None, :] + grain[:, :, None], 0, 255).astype(np.uint8)
        rgba = np.ascontiguousarray(np.dstack([rgb, np.full((H, W), 255, dtype=np.uint8)]))
        img = QImage(rgba.data, W, H, W * 4, QImage.Format.Format_RGBA8888).copy()

        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Outer border & centimeter grid
        mx, my = 44, 36
        gw, gh = W - 2 * mx, H - 2 * my
        p.setPen(QPen(QColor(175, 215, 190, 145), 2.2))
        p.drawRect(QRectF(mx, my, gw, gh))

        step = 32
        for x in range(mx + step, W - mx, step):
            major = ((x - mx) // step) % 5 == 0
            p.setPen(QPen(QColor(175, 215, 190, 125 if major else 65), 1.6 if major else 1.0))
            p.drawLine(QPointF(x, my), QPointF(x, H - my))
        for y in range(my + step, H - my, step):
            major = ((y - my) // step) % 5 == 0
            p.setPen(QPen(QColor(175, 215, 190, 125 if major else 65), 1.6 if major else 1.0))
            p.drawLine(QPointF(mx, y), QPointF(W - mx, y))

        # 30 / 45 / 60 deg drafting angle lines on lower-left corner
        p.setPen(QPen(QColor(185, 224, 198, 95), 1.3, Qt.PenStyle.DashLine))
        for deg in (30, 45, 60):
            rad = math.radians(deg)
            p.drawLine(QPointF(mx, H - my), QPointF(mx + math.cos(rad) * 320, H - my - math.sin(rad) * 320))

        # Subtle ruler numbers along top & bottom edge
        font = QFont('JetBrains Mono')
        font.setPixelSize(11)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(185, 222, 198, 155))
        for idx, x in enumerate(range(mx, W - mx + 1, step * 2)):
            p.drawText(QRectF(x - 14, my - 20, 28, 16), Qt.AlignmentFlag.AlignCenter, str(idx * 2))
            p.drawText(QRectF(x - 14, H - my + 4, 28, 16), Qt.AlignmentFlag.AlignCenter, str(idx * 2))

        # Worn X-Acto knife cut marks & faint solder-flux scuffs
        for _ in range(38):
            x0, y0 = float(rng.uniform(mx + 20, W - mx - 20)), float(rng.uniform(my + 20, H - my - 20))
            ang = float(rng.uniform(0, 2 * math.pi))
            ln = float(rng.uniform(18, 85))
            p.setPen(QPen(QColor(16, 42, 30, int(rng.integers(55, 125))), float(rng.uniform(0.8, 1.8))))
            p.drawLine(QPointF(x0, y0), QPointF(x0 + math.cos(ang) * ln, y0 + math.sin(ang) * ln))
        p.end()
        _upload_rgba(self, _qimage_to_rgba(img)[::-1].copy())


def _cutting_mat_heightfield(W=1024, H=512):
    """Shared deterministic micro-grain + X-Acto cut-groove heightfield matching CuttingMatTexture."""
    rng = np.random.default_rng(1986)
    grain = rng.normal(0.0, 0.35, (H, W)).astype(np.float32)
    img = QImage(W, H, QImage.Format.Format_RGBA8888)
    img.fill(QColor(128, 128, 128, 255))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    mx, my = 44, 36
    # Consume same random draw sequence as CuttingMatTexture knife marks
    for _ in range(38):
        x0, y0 = float(rng.uniform(mx + 20, W - mx - 20)), float(rng.uniform(my + 20, H - my - 20))
        ang = float(rng.uniform(0, 2 * math.pi))
        ln = float(rng.uniform(18, 85))
        _ = int(rng.integers(55, 125))
        w = float(rng.uniform(0.8, 1.8))
        p.setPen(QPen(QColor(52, 52, 52, 210), w + 0.6))
        p.drawLine(QPointF(x0, y0), QPointF(x0 + math.cos(ang) * ln, y0 + math.sin(ang) * ln))
    p.end()
    cuts = _qimage_to_rgba(img)[::-1, :, 0].astype(np.float32) / 255.0 - 0.5
    return cuts * 2.4 + grain * 0.14


class CuttingMatNormalTexture(QQuick3DTextureData):
    """1024x512 tangent-space normal map for self-healing PVC grain & X-Acto knife grooves."""
    def __init__(self, parent=None):
        super().__init__(parent)
        h = _cutting_mat_heightfield(1024, 512)
        dx = (h - np.roll(h, 1, axis=1)) * 1.45
        dy = (h - np.roll(h, 1, axis=0)) * 1.45
        dz = np.ones_like(h)
        inv = 1.0 / np.sqrt(dx * dx + dy * dy + dz * dz)
        nx = np.clip((dx * inv * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
        ny = np.clip((dy * inv * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
        nz = np.clip((dz * inv * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
        _upload_rgba(self, np.dstack([nx, ny, nz, np.full((512, 1024), 255, dtype=np.uint8)]))


class CuttingMatRoughnessTexture(QQuick3DTextureData):
    """1024x512 roughness map: matte worn PVC base with subtle glossier ink grid & scuffed work center."""
    def __init__(self, parent=None):
        super().__init__(parent)
        W, H = 1024, 512
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        # Slightly polished/worn work zone in front of the scope
        wear = np.exp(-(((xx - W * 0.48) / (W * 0.30)) ** 2 + ((yy - H * 0.65) / (H * 0.28)) ** 2))
        rng = np.random.default_rng(1987)
        noise = rng.normal(0.0, 6.0, (H, W)).astype(np.float32)
        rough = np.clip(208.0 - wear * 44.0 + noise, 130, 235).astype(np.uint8)
        _upload_rgba(self, np.dstack([rough, rough, rough, np.full((H, W), 255, dtype=np.uint8)]))


class CalStickerTexture(QQuick3DTextureData):
    """512x256 yellowed vintage 'CAL DUE: NOV 1989' calibration sticker with peeling corner."""
    def __init__(self, parent=None):
        super().__init__(parent)
        W, H = 512, 256
        img = QImage(W, H, QImage.Format.Format_RGBA8888)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # Shadow underneath peeling upper-right corner
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(22, 20, 16, 110))
        p.drawRoundedRect(QRectF(18, 22, W - 32, H - 34), 8, 8)

        # Slightly aged cream-yellow vinyl sticker body with peeled corner clipped
        body = QPainterPath()
        body.moveTo(16, 18)
        body.lineTo(W - 86, 18)
        body.lineTo(W - 18, 84)
        body.lineTo(W - 18, H - 18)
        body.lineTo(16, H - 18)
        body.closeSubpath()
        p.fillPath(body, QColor('#e6dcb8'))
        p.setPen(QPen(QColor('#9c916f'), 3.0))
        p.drawPath(body)

        # Red header stripe
        p.save()
        p.setClipPath(body)
        p.fillRect(QRectF(16, 18, W - 34, 58), QColor('#b3362b'))
        f_hdr = QFont('JetBrains Mono')
        f_hdr.setPixelSize(28)
        f_hdr.setBold(True)
        p.setFont(f_hdr)
        p.setPen(QColor('#f6efd6'))
        p.drawText(QRectF(24, 22, W - 110, 50), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   'CALIBRATION')

        f_body = QFont('JetBrains Mono')
        f_body.setPixelSize(22)
        f_body.setBold(True)
        p.setFont(f_body)
        p.setPen(QColor('#28251e'))
        p.drawText(QRectF(30, 88, W - 64, 34), Qt.AlignmentFlag.AlignLeft, 'BY:  J.M.   ID: C1-170')
        p.setPen(QColor('#8f231a'))
        p.drawText(QRectF(30, 134, W - 64, 34), Qt.AlignmentFlag.AlignLeft, 'DUE: NOV 1989')
        p.setPen(QColor('#464237'))
        f_sm = QFont('JetBrains Mono')
        f_sm.setPixelSize(16)
        p.setFont(f_sm)
        p.drawText(QRectF(30, 184, W - 64, 28), Qt.AlignmentFlag.AlignLeft, 'DO NOT BREAK SEAL')
        p.restore()

        # Curled/peeling upper-right triangle flap catching overhead light
        flap = QPolygonF([QPointF(W - 86, 18), QPointF(W - 18, 84), QPointF(W - 74, 88)])
        fg = QLinearGradient(QPointF(W - 86, 18), QPointF(W - 50, 88))
        fg.setColorAt(0.0, QColor('#c7be9d'))
        fg.setColorAt(1.0, QColor('#f4edd4'))
        p.setPen(QPen(QColor('#8a8060'), 2.0))
        p.setBrush(fg)
        p.drawPolygon(flap)

        p.end()
        _upload_rgba(self, _qimage_to_rgba(img)[::-1].copy(), has_alpha=True)


class BobbleheadFaceTexture(QQuick3DTextureData):
    """512x512 cartoonish bobblehead face texture with bold X-ed out eyes & wry grin."""
    def __init__(self, parent=None):
        super().__init__(parent)
        W, H = 512, 512
        img = QImage(W, H, QImage.Format.Format_RGBA8888)
        img.fill(QColor('#efc8a8'))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Subtle stylized cartoon cheek shading & nose shadow
        for cx, cy, rx, ry in ((182, 288, 36, 22), (330, 288, 36, 22)):
            g = QRadialGradient(QPointF(cx, cy), rx)
            g.setColorAt(0.0, QColor(225, 132, 118, 95))
            g.setColorAt(1.0, QColor(225, 132, 118, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(QPointF(cx, cy), rx, ry)

        # Tousled dark-brown cartoon hairline across the top of the UV wrap
        p.setBrush(QColor('#3b2a1e'))
        p.drawRect(QRectF(0, 0, W, 128))
        for hx, hr in ((70, 48), (150, 56), (245, 62), (340, 54), (435, 50)):
            p.drawEllipse(QPointF(hx, 126), hr, 34)

        # Expressive cartoon eyebrows
        p.setPen(QPen(QColor('#342318'), 10.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(158, 198), QPointF(224, 190))
        p.drawLine(QPointF(288, 190), QPointF(354, 198))

        # Bold cartoonish X-ed out eyes (front of sphere maps near u=0.5 -> x=256)
        p.setPen(QPen(QColor('#161414'), 13.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for ex, ey in ((204, 244), (308, 244)):
            r = 24
            p.drawLine(QPointF(ex - r, ey - r), QPointF(ex + r, ey + r))
            p.drawLine(QPointF(ex + r, ey - r), QPointF(ex - r, ey + r))

        # Cartoon nose & wry smirk
        p.setPen(QPen(QColor('#b88362'), 6.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(242, 266, 28, 34), -40 * 16, 110 * 16)
        p.setPen(QPen(QColor('#2c1c16'), 8.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(206, 314, 102, 48), 205 * 16, 130 * 16)

        p.end()
        _upload_rgba(self, _qimage_to_rgba(img)[::-1].copy())


class CrtGraticuleGlassTexture(QQuick3DTextureData):
    """512x512 floating cover-glass texture combining etched graticule + studio sheen
    for true glass-thickness parallax in front of the curved CRT phosphor layer."""
    def __init__(self, show_grid=True, parent=None):
        super().__init__(parent)
        self._show_grid = None
        self.set_show_grid(show_grid)

    def set_show_grid(self, show_grid):
        flag = bool(show_grid)
        if flag != self._show_grid:
            self._show_grid = flag
            self._rebuild()

    def _rebuild(self):
        S = 512
        img = QImage(S, S, QImage.Format.Format_RGBA8888)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1. Soft diagonal studio softbox / room-window reflection sweep
        ref = QLinearGradient(QPointF(S * 0.05, S * 0.05), QPointF(S * 0.80, S * 0.85))
        ref.setColorAt(0.0, QColor(235, 248, 240, 36))
        ref.setColorAt(0.26, QColor(230, 245, 236, 18))
        ref.setColorAt(0.45, QColor(220, 240, 230, 4))
        ref.setColorAt(0.65, QColor(220, 240, 230, 0))
        ref.setColorAt(0.84, QColor(215, 238, 225, 10))
        ref.setColorAt(1.0, QColor(215, 238, 225, 0))
        p.fillRect(QRectF(0, 0, S, S), ref)

        spot = QRadialGradient(QPointF(S * 0.28, S * 0.24), S * 0.34)
        spot.setColorAt(0.0, QColor(245, 255, 248, 32))
        spot.setColorAt(0.55, QColor(235, 250, 240, 10))
        spot.setColorAt(1.0, QColor(235, 250, 240, 0))
        p.fillRect(QRectF(0, 0, S, S), spot)

        # 2. Etched physical CRT graticule on the outer glass (true glass-thickness parallax!)
        if self._show_grid:
            divs = 8
            step = S / divs
            p.setPen(QPen(QColor(18, 28, 22, 145), 1.3))
            for i in range(1, divs):
                c = i * step
                p.drawLine(QPointF(c, 10), QPointF(c, S - 10))
                p.drawLine(QPointF(10, c), QPointF(S - 10, c))
            # Center crosshairs with etched fine sub-division ticks
            mid = S * 0.5
            p.setPen(QPen(QColor(24, 38, 30, 175), 1.7))
            p.drawLine(QPointF(mid, 10), QPointF(mid, S - 10))
            p.drawLine(QPointF(10, mid), QPointF(S - 10, mid))
            sub = step / 5.0
            for k in range(1, divs * 5):
                pos = k * sub
                p.drawLine(QPointF(pos, mid - 5), QPointF(pos, mid + 5))
                p.drawLine(QPointF(mid - 5, pos), QPointF(mid + 5, pos))
            # Faint pale edge-lit graticule highlight
            p.setPen(QPen(QColor(190, 235, 205, 42), 1.0))
            p.drawLine(QPointF(mid + 1, 10), QPointF(mid + 1, S - 10))
            p.drawLine(QPointF(10, mid + 1), QPointF(S - 10, mid + 1))

        p.end()
        _upload_rgba(self, _qimage_to_rgba(img)[::-1].copy(), has_alpha=True)


# ==============================================================================
# 4. 5-PAGE TATTERED LAB NOTEPAD (VOYNICH + REAL PHOSPHOR RECURRENCE MATH)
# ==============================================================================

def _draw_coffee_ring(p, cx, cy, r, seed=1):
    rng = np.random.default_rng(seed)
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    # Soft inner stain wash
    g = QRadialGradient(QPointF(cx, cy), r)
    g.setColorAt(0.0, QColor(126, 82, 38, 12))
    g.setColorAt(0.80, QColor(115, 72, 30, 20))
    g.setColorAt(0.95, QColor(94, 54, 18, 86))
    g.setColorAt(1.0, QColor(94, 54, 18, 0))
    p.setBrush(g)
    p.drawEllipse(QPointF(cx, cy), r, r)
    # Irregular double rim arc
    for k in range(3):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(92, 52, 18, int(rng.integers(45, 95))), float(rng.uniform(2.0, 4.5))))
        ox, oy = float(rng.uniform(-4, 4)), float(rng.uniform(-4, 4))
        p.drawEllipse(QPointF(cx + ox, cy + oy), r * float(rng.uniform(0.95, 1.03)), r * float(rng.uniform(0.94, 1.02)))
    p.restore()


def _sketch_line(p, x0, y0, x1, y1, rng, jitter=1.6):
    mx, my = (x0 + x1) * 0.5 + float(rng.uniform(-jitter, jitter)), (y0 + y1) * 0.5 + float(rng.uniform(-jitter, jitter))
    path = QPainterPath(QPointF(x0, y0))
    path.quadTo(QPointF(mx, my), QPointF(x1, y1))
    p.drawPath(path)


def _render_notepad_page(page_idx):
    """Render one of 5 tattered lab notepad pages (768x960 RGBA8)."""
    W, H = 768, 960
    rng = np.random.default_rng(1989 + page_idx * 37)
    paper = np.full((H, W, 3), [234.0, 224.0, 198.0], dtype=np.float32)
    paper += rng.normal(0.0, 4.0, (H, W, 1)).astype(np.float32)
    # Aged darkened paper borders
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    edge = np.minimum(np.minimum(xx / 70.0, (W - 1 - xx) / 70.0), np.minimum(yy / 70.0, (H - 1 - yy) / 70.0))
    edge = np.clip(edge, 0.0, 1.0)
    paper -= (1.0 - edge)[:, :, None] * np.array([22.0, 26.0, 32.0], dtype=np.float32)
    rgba = np.ascontiguousarray(np.dstack([np.clip(paper, 0, 255).astype(np.uint8), np.full((H, W), 255, dtype=np.uint8)]))
    img = QImage(rgba.data, W, H, W * 4, QImage.Format.Format_RGBA8888).copy()

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    # Tattered notepad perforated top strip + ruled lines
    p.fillRect(QRectF(0, 0, W, 56), QColor('#c7b892'))
    p.setPen(QPen(QColor('#8c7b58'), 2.0, Qt.PenStyle.DashLine))
    p.drawLine(QPointF(0, 56), QPointF(W, 56))
    p.setPen(QPen(QColor(110, 148, 175, 72), 1.2))
    for y in range(110, H - 36, 38):
        p.drawLine(QPointF(24, y), QPointF(W - 24, y))
    p.setPen(QPen(QColor(186, 86, 82, 82), 1.5))
    p.drawLine(QPointF(86, 58), QPointF(86, H - 24))

    pencil = QColor(56, 54, 50, 215)
    pen_blue = QColor(34, 62, 128, 225)
    pen_red = QColor(156, 44, 38, 210)
    f_title = QFont('JetBrains Mono'); f_title.setPixelSize(24); f_title.setBold(True)
    f_math = QFont('JetBrains Mono'); f_math.setPixelSize(18)
    f_note = QFont('JetBrains Mono'); f_note.setPixelSize(15); f_note.setItalic(True)

    # Page number in corner
    p.setFont(f_note)
    p.setPen(QColor(90, 82, 68, 180))
    p.drawText(QRectF(W - 140, 68, 110, 26), Qt.AlignmentFlag.AlignRight, f'p. {page_idx + 1} / 5')

    if page_idx == 0:
        # PAGE 1: Voynich Manuscript haphazard pencil drawings (botanical plant + celestial wheel + script)
        p.setFont(f_title)
        p.setPen(pencil)
        p.drawText(QRectF(96, 76, 560, 34), Qt.AlignmentFlag.AlignLeft, 'folio 41v  —  voynich study (pencil)')

        # Fanciful Voynich botanical plant with bulbous roots & curled leaves
        p.setPen(QPen(pencil, 2.4))
        cx, stem_top, stem_bot = 250, 220, 620
        _sketch_line(p, cx, stem_top, cx - 10, stem_bot, rng, 9.0)
        _sketch_line(p, cx + 8, stem_top + 10, cx + 4, stem_bot, rng, 8.0)
        # Bulbous tangled Voynich roots
        for rx, ry in ((-72, 70), (-42, 92), (-8, 104), (34, 96), (68, 72)):
            _sketch_line(p, cx, stem_bot, cx + rx, stem_bot + ry, rng, 14.0)
            p.drawEllipse(QPointF(cx + rx, stem_bot + ry), 14, 9)
        # Curled scalloped leaves along stem
        for ly, side in ((290, -1), (345, 1), (410, -1), (465, 1), (525, -1)):
            path = QPainterPath(QPointF(cx, ly))
            path.cubicTo(QPointF(cx + side * 65, ly - 48), QPointF(cx + side * 125, ly + 12),
                         QPointF(cx + side * 95, ly + 44))
            path.cubicTo(QPointF(cx + side * 55, ly + 52), QPointF(cx + side * 20, ly + 22), QPointF(cx, ly + 12))
            p.drawPath(path)
            _sketch_line(p, cx, ly + 6, cx + side * 88, ly + 14, rng, 4.0)
        # Starburst flower crown
        for i in range(12):
            a = 2 * math.pi * i / 12.0
            p.drawEllipse(QPointF(cx + math.cos(a) * 34, stem_top - 12 + math.sin(a) * 28), 14, 9)
        p.drawEllipse(QPointF(cx, stem_top - 12), 18, 18)

        # Voynich celestial/astrological rosette wheel on the right
        wx, wy = 555, 340
        for rr in (34, 72, 112, 138):
            p.drawEllipse(QPointF(wx, wy), rr, rr)
        for i in range(16):
            a = 2 * math.pi * i / 16.0
            _sketch_line(p, wx + math.cos(a) * 34, wy + math.sin(a) * 34,
                         wx + math.cos(a) * 138, wy + math.sin(a) * 138, rng, 2.0)
            # Tiny 5-point stars inside outer band
            sx, sy = wx + math.cos(a + 0.19) * 124, wy + math.sin(a + 0.19) * 124
            p.drawEllipse(QPointF(sx, sy), 3.5, 3.5)

        # Flowing unreadable Voynich-esque marginalia glyphs
        p.setPen(QPen(QColor(64, 56, 46, 190), 2.0))
        for row_y in (540, 582, 624, 666, 760, 802, 844):
            x_cur = 415 if row_y < 700 else 108
            x_end = 710 if row_y < 700 else 680
            while x_cur < x_end:
                wlen = int(rng.integers(24, 66))
                path = QPainterPath(QPointF(x_cur, row_y))
                for step in range(4):
                    nx = x_cur + wlen * (step + 1) / 4.0
                    ny = row_y + float(rng.uniform(-11, 11))
                    path.quadTo(QPointF(nx - 6, ny - float(rng.uniform(-10, 12))), QPointF(nx, ny))
                p.drawPath(path)
                x_cur += wlen + int(rng.integers(12, 26))
        _draw_coffee_ring(p, 595, 765, 92, seed=101)

    elif page_idx == 1:
        # PAGE 2: Hénon Map derivation (Phosphor built-in preset!)
        p.setFont(f_title); p.setPen(pen_blue)
        p.drawText(QRectF(96, 76, 600, 34), Qt.AlignmentFlag.AlignLeft, 'HÉNON MAP — fixed pts & contraction')
        lines = [
            (pencil, 'x[n+1] = 0.5 - 2.8*x[n]^2 + y[n]'),
            (pencil, 'y[n+1] = 0.3*x[n]'),
            (pen_blue, 'At fixed point (x*, y*): y* = 0.3*x*'),
            (pen_blue, '=> 2.8*x*^2 + 0.7*x* - 0.5 = 0'),
            (pencil, 'x* = (-0.7 +/- sqrt(0.49 + 5.6)) / 5.6'),
            (pen_red, 'x* = +0.3156,  -0.5656   (check sign!)'),
            (pencil, 'Jacobian J = [ -5.6*x   1.0 ]'),
            (pencil, '             [   0.3    0.0 ]'),
            (pen_blue, 'det(J) = -0.3  => |det(J)| < 1 (dissipative!)'),
            (pencil, 'trace(J) = -5.6*x*  => unstable saddle'),
        ]
        p.setFont(f_math)
        for i, (col, txt) in enumerate(lines):
            p.setPen(col)
            p.drawText(QRectF(100 + float(rng.uniform(-4, 5)), 142 + i * 44, 620, 32), Qt.AlignmentFlag.AlignLeft, txt)
        # Hénon banana attractor doodle + wireframe octahedron doodle
        p.setPen(QPen(pencil, 2.0))
        x, y = 0.0, 0.0
        for _ in range(420):
            x, y = 0.5 - 2.8 * x * x + y, 0.3 * x
            if abs(x) < 2 and abs(y) < 2:
                p.drawEllipse(QPointF(260 + x * 170, 740 - y * 240), 1.6, 1.6)
        # Octahedron doodle on right
        ox, oy = 575, 745
        pts = [(ox, oy - 85), (ox - 78, oy), (ox, oy + 85), (ox + 78, oy), (ox + 22, oy - 18), (ox - 22, oy + 18)]
        p.setPen(QPen(pen_blue, 2.0))
        for a, b in ((0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (1, 4), (2, 4), (3, 4), (0, 5), (1, 5), (2, 5), (3, 5)):
            _sketch_line(p, pts[a][0], pts[a][1], pts[b][0], pts[b][1], rng, 2.2)
        _draw_coffee_ring(p, 165, 815, 78, seed=202)

    elif page_idx == 2:
        # PAGE 3: Lorenz Attractor derivation (Phosphor built-in preset!)
        p.setFont(f_title); p.setPen(pen_blue)
        p.drawText(QRectF(96, 76, 620, 34), Qt.AlignmentFlag.AlignLeft, 'LORENZ ATTRACTOR  (dt = 0.01)')
        lines = [
            (pencil, 'dx/dt = 10*(y - x)'),
            (pencil, 'dy/dt = x*(28 - z) - y'),
            (pencil, 'dz/dt = x*y - (8/3)*z'),
            (pen_blue, 'Equilibria (dx=dy=dz=0):'),
            (pencil, '  x = y,  z = rho - 1 = 27'),
            (pencil, '  x^2 = beta*(rho - 1) = (8/3)*27 = 72'),
            (pen_red, '  C+/- = (+/- 8.485, +/- 8.485, 27)'),
            (pen_blue, 'Div(F) = -(sigma + 1 + beta) = -13.67 < 0'),
            (pencil, 'Scale for scope [-1, +1]: z_sc = (z - 25)/30'),
        ]
        p.setFont(f_math)
        for i, (col, txt) in enumerate(lines):
            p.setPen(col)
            p.drawText(QRectF(100 + float(rng.uniform(-4, 5)), 142 + i * 44, 620, 32), Qt.AlignmentFlag.AlignLeft, txt)
        # Haphazard butterfly trajectory sketch in pencil
        p.setPen(QPen(pencil, 2.1))
        lx, ly, lz = 0.1, 0.0, 0.0
        prev = None
        for _ in range(900):
            dx, dy, dz = 10 * (ly - lx), lx * (28 - lz) - ly, lx * ly - (8 / 3) * lz
            lx, ly, lz = lx + 0.008 * dx, ly + 0.008 * dy, lz + 0.008 * dz
            cur = QPointF(385 + lx * 11.5, 865 - lz * 6.8)
            if prev is not None:
                p.drawLine(prev, cur)
            prev = cur
        _draw_coffee_ring(p, 620, 195, 86, seed=303)

    elif page_idx == 3:
        # PAGE 4: Second-Order Oscillator & Damped Harmonograph + 3D Cube doodle
        p.setFont(f_title); p.setPen(pen_blue)
        p.drawText(QRectF(96, 76, 620, 34), Qt.AlignmentFlag.AlignLeft, '2ND-ORDER OSCILLATOR / HARMONOGRAPH')
        lines = [
            (pencil, 'x[n] = 2*r*cos(w)*x[n-1] - r^2*x[n-2]'),
            (pen_blue, 'Char. eq:  L^2 - 2*r*cos(w)*L + r^2 = 0'),
            (pencil, 'L = r*(cos(w) +/- i*sin(w)) = r*e^(+/- i*w)'),
            (pen_red, 'Closed form: x[n] = A * r^n * cos(n*w + phi)'),
            (pencil, 'For r = 0.9992, wx = 0.12, wy = 0.181:'),
            (pencil, '  freq ratio wy/wx = 1.5083 (near 3:2 fifth!)'),
            (pen_blue, '  => slowly precessing Lissajous spiral'),
        ]
        p.setFont(f_math)
        for i, (col, txt) in enumerate(lines):
            p.setPen(col)
            p.drawText(QRectF(100 + float(rng.uniform(-4, 5)), 142 + i * 48, 630, 32), Qt.AlignmentFlag.AlignLeft, txt)
        # Wireframe 3D cube doodle (left) + Lissajous doodle (right)
        p.setPen(QPen(pencil, 2.4))
        c0 = [(150, 620), (270, 620), (270, 740), (150, 740)]
        c1 = [(202, 576), (322, 576), (322, 696), (202, 696)]
        for i in range(4):
            _sketch_line(p, c0[i][0], c0[i][1], c0[(i + 1) % 4][0], c0[(i + 1) % 4][1], rng, 2.4)
            _sketch_line(p, c1[i][0], c1[i][1], c1[(i + 1) % 4][0], c1[(i + 1) % 4][1], rng, 2.4)
            _sketch_line(p, c0[i][0], c0[i][1], c1[i][0], c1[i][1], rng, 2.4)
        p.setPen(QPen(pen_blue, 2.0))
        ts = np.linspace(0, 6 * math.pi, 320)
        prev = None
        for t in ts:
            cur = QPointF(540 + 115 * math.exp(-0.04 * t) * math.cos(2 * t),
                          680 + 115 * math.exp(-0.04 * t) * math.sin(3.02 * t))
            if prev is not None:
                p.drawLine(prev, cur)
            prev = cur
        _draw_coffee_ring(p, 355, 810, 74, seed=404)

    else:
        # PAGE 5: Rössler Attractor & Ikeda Map + Torus knot doodle
        p.setFont(f_title); p.setPen(pen_blue)
        p.drawText(QRectF(96, 76, 620, 34), Qt.AlignmentFlag.AlignLeft, 'RÖSSLER & IKEDA MAP NOTES')
        lines = [
            (pencil, 'Rössler (c = 5.7):'),
            (pencil, '  x\' = -y - z,   y\' = x + 0.2*y'),
            (pencil, '  z\' = 0.2 + z*(x - c)'),
            (pen_blue, 'Period-doubling cascade -> chaos near c ~ 4.2'),
            (pencil, 'Ikeda optical cavity map (u = 0.918):'),
            (pencil, '  theta[n] = 0.4 - 6 / (1 + x^2 + y^2)'),
            (pen_red, '  z[n+1] = 1 + u * z[n] * exp(i * theta[n])'),
            (pen_blue, 'Note: keep CH1/CH2 cables seated or X drops!'),
        ]
        p.setFont(f_math)
        for i, (col, txt) in enumerate(lines):
            p.setPen(col)
            p.drawText(QRectF(100 + float(rng.uniform(-4, 5)), 142 + i * 48, 630, 32), Qt.AlignmentFlag.AlignLeft, txt)
        # Trefoil torus-knot pencil doodle
        p.setPen(QPen(pencil, 2.3))
        ts = np.linspace(0, 2 * math.pi, 260)
        prev = None
        for t in ts:
            r = 72 + 34 * math.cos(3 * t)
            cur = QPointF(380 + r * math.cos(2 * t) * 1.45, 725 + r * math.sin(2 * t) * 1.15)
            if prev is not None:
                p.drawLine(prev, cur)
            prev = cur
        _draw_coffee_ring(p, 595, 735, 96, seed=505)
        _draw_coffee_ring(p, 190, 620, 68, seed=506)

    p.end()
    return _qimage_to_rgba(img)[::-1].copy()


class NotepadPageTexture(QQuick3DTextureData):
    """Dynamic 5-page tattered lab notepad texture; call set_page(0..4) to flip pages."""
    PAGE_COUNT = 5

    def __init__(self, page=0, parent=None):
        super().__init__(parent)
        self._cache = {}
        self._page = None
        self.set_page(page)

    def set_page(self, page):
        idx = int(page) % self.PAGE_COUNT
        if idx != self._page:
            self._page = idx
            if idx not in self._cache:
                self._cache[idx] = _render_notepad_page(idx)
            _upload_rgba(self, self._cache[idx])


# ==============================================================================
# 5. PHASE 2: HOLLOW SPUN-METAL LAMP SHADE, VOLUMETRIC CONE & CONTACT SHADOWS
# ==============================================================================

def build_lathe_mesh(profile_ry, segs=36, flip_normals=False):
    """Build a smooth-shaded surface of revolution around the Y axis from a list of (radius, y) pairs.

    Duplicate consecutive points (or tiny steps) create crisp crease edges while smooth runs
    interpolate normals continuously around the circumference and along the profile.
    """
    prof = [(float(r), float(y)) for r, y in profile_ry]
    m = len(prof)
    angles = np.linspace(0.0, 2.0 * math.pi, segs + 1, dtype=np.float32)
    ca, sa = np.cos(angles), np.sin(angles)

    # Arc-length V parameter along profile
    v_coords = [0.0]
    for i in range(1, m):
        dr = prof[i][0] - prof[i - 1][0]
        dy = prof[i][1] - prof[i - 1][1]
        v_coords.append(v_coords[-1] + math.hypot(dr, dy))
    total_v = v_coords[-1] or 1.0
    v_norm = [v / total_v for v in v_coords]

    # Compute 2D profile normals (nr, ny) pointing outward (to the right of increasing profile index)
    prof_n = []
    for i in range(m):
        i0 = max(0, i - 1)
        i1 = min(m - 1, i + 1)
        dr = prof[i1][0] - prof[i0][0]
        dy = prof[i1][1] - prof[i0][1]
        ln = math.hypot(dr, dy)
        if ln < 1e-6:
            nr, ny = 1.0, 0.0
        else:
            # Profile goes top->bottom (dy < 0); outward radial is (-dy, dr)
            nr, ny = -dy / ln, dr / ln
        if flip_normals:
            nr, ny = -nr, -ny
        prof_n.append((nr, ny))

    pos = np.empty((m, segs + 1, 3), dtype=np.float32)
    nrm = np.empty((m, segs + 1, 3), dtype=np.float32)
    uv = np.empty((m, segs + 1, 2), dtype=np.float32)

    for i, ((r, y), (nr, ny), vn) in enumerate(zip(prof, prof_n, v_norm)):
        pos[i, :, 0] = r * ca
        pos[i, :, 1] = y
        pos[i, :, 2] = r * sa
        nrm[i, :, 0] = nr * ca
        nrm[i, :, 1] = ny
        nrm[i, :, 2] = nr * sa
        uv[i, :, 0] = angles / (2.0 * math.pi)
        uv[i, :, 1] = vn

    # Ensure strictly unit normals
    nlen = np.linalg.norm(nrm, axis=2, keepdims=True)
    nrm = np.where(nlen > 1e-5, nrm / np.maximum(nlen, 1e-5), np.array([0.0, 1.0, 0.0], dtype=np.float32))

    stride = segs + 1
    j, k = np.meshgrid(np.arange(m - 1, dtype=np.uint32), np.arange(segs, dtype=np.uint32), indexing='ij')
    v0 = j * stride + k
    v1 = v0 + 1
    v2 = v0 + stride
    v3 = v2 + 1
    if flip_normals:
        indices = np.stack([v0, v2, v3, v0, v3, v1], axis=-1).reshape(-1)
    else:
        indices = np.stack([v0, v1, v3, v0, v3, v2], axis=-1).reshape(-1)
    return pos.reshape(-1, 3), nrm.reshape(-1, 3), uv.reshape(-1, 2), indices


def build_hollow_lamp_shade_mesh(segs=40):
    """Spun-metal mid-century/80s vintage architect lamp outer dome with neck collar & rolled rim."""
    profile = [
        (0.01, 22.0),   # Top cap center
        (11.5, 22.0),   # Top cap flat
        (13.2, 20.5),   # Neck chamfer
        (13.5, 11.0),   # Ventilated cylindrical lamp-socket neck
        (14.5, 9.2),    # Shoulder flare start
        (20.0, 4.5),    # Upper dome curve
        (27.5, -4.5),   # Mid flared cone
        (35.0, -17.5),  # Lower flare
        (38.8, -28.0),  # Wide shade mouth
        (39.4, -30.5),  # Rolled metal lip outer
        (38.0, -31.8),  # Rolled metal lip bottom
        (36.6, -30.4),  # Rolled lip inner edge
    ]
    return build_lathe_mesh(profile, segs=segs, flip_normals=False)


def build_lamp_shade_inner_mesh(segs=36):
    """Cream-enamel inner reflector liner inside the hollow architect lamp shade."""
    profile = [
        (0.01, 8.5),
        (13.2, 8.5),
        (19.0, 4.0),
        (26.2, -4.5),
        (33.5, -17.5),
        (36.8, -29.8),
    ]
    return build_lathe_mesh(profile, segs=segs, flip_normals=True)


def build_lamp_volumetric_cone_mesh(segs=32):
    """Open tapered atmospheric light-beam cone emanating from the architect lamp mouth."""
    profile = [
        (35.0, -29.0),
        (68.0, -105.0),
        (112.0, -205.0),
        (158.0, -310.0),
    ]
    return build_lathe_mesh(profile, segs=segs, flip_normals=False)


class HollowLampShadeGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_hollow_lamp_shade_mesh())


class LampShadeInnerGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_lamp_shade_inner_mesh())


class LampVolumetricConeGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_lamp_volumetric_cone_mesh())


class ContactShadowTexture(QQuick3DTextureData):
    """256x256 soft RGBA8 contact-shadow decal (smooth rounded-box Gaussian occlusion)
    for physically grounding heavy props onto the oak workbench and cutting mat."""
    def __init__(self, parent=None):
        super().__init__(parent)
        S = 256
        u = np.linspace(-1.0, 1.0, S, dtype=np.float32)[None, :]
        v = np.linspace(-1.0, 1.0, S, dtype=np.float32)[:, None]
        # Soft elliptical Gaussian falloff reaching exact 0 at border (zero quad edge seams)
        r2 = u * u + v * v
        core = np.exp(-r2 * 4.2)
        window = np.clip(1.0 - r2, 0.0, 1.0) ** 1.35
        alpha = np.clip(core * window * 215.0, 0.0, 255.0).astype(np.uint8)
        rgb = np.zeros((S, S, 3), dtype=np.uint8)
        rgb[:, :, 0] = 10
        rgb[:, :, 1] = 8
        rgb[:, :, 2] = 6
        _upload_rgba(self, np.dstack([rgb, alpha]), has_alpha=True)


# ==============================================================================
# 6. PHASE 4: BENCH PROPS & LATE-80s LAB STORYTELLING MESHES + WALL CHART
# ==============================================================================

def build_helical_spring_mesh(coil_radius=3.0, wire_radius=0.65, height=96.0, turns=14.0, steps_per_turn=14, sides=8):
    """True 3D helical coiled wire spring along Y (used for architect lamp & bobblehead neck)."""
    total_steps = max(24, int(turns * steps_per_turn))
    t = np.linspace(0.0, 1.0, total_steps + 1, dtype=np.float32)
    ang = t * float(turns) * 2.0 * math.pi
    pts = np.column_stack([
        coil_radius * np.cos(ang),
        (t - 0.5) * float(height),
        coil_radius * np.sin(ang),
    ])
    return build_tube_along_polyline(pts, radius=wire_radius, sides=sides)


def build_coffee_mug_mesh(segs=36):
    """Late-80s glazed stoneware lab coffee mug (smooth lathe outer + lip + hollow inner wall)."""
    profile = [
        (0.01, 0.0),    # Base center
        (17.5, 0.0),    # Foot ring bottom
        (18.6, 1.4),    # Base chamfer
        (19.4, 5.5),    # Lower body
        (20.0, 22.0),   # Mid cylindrical body
        (20.4, 41.0),   # Upper body
        (20.6, 44.5),   # Rim outer curve
        (19.6, 46.2),   # Rim crest
        (18.2, 45.8),   # Rim inner roll
        (17.4, 43.2),   # Inner upper wall
        (16.8, 12.0),   # Inner lower wall
        (15.2, 4.5),    # Inner bottom curve
        (0.01, 4.2),    # Inner bottom center
    ]
    return build_lathe_mesh(profile, segs=segs, flip_normals=False)


def build_mug_handle_mesh(sides=12):
    """Swept ergonomic ceramic C-handle attached to the +X side of the coffee mug."""
    ts = np.linspace(0.0, 1.0, 22, dtype=np.float32)
    ang = (ts - 0.5) * math.pi * 1.15
    x = 18.8 + 13.5 * np.cos(ang)
    y = 24.5 + 14.2 * np.sin(ang)
    z = np.zeros_like(x)
    pts = np.column_stack([x, y, z])
    return build_tube_along_polyline(pts, radius=2.65, sides=sides)


def build_hex_pencil_barrel_mesh(radius=2.1, length=74.0):
    """Classic 6-sided hexagonal No. 2 lab pencil barrel centered along X."""
    pos_list, nrm_list, uv_list, idx_list = [], [], [], []
    base = 0
    x0, x1 = -length * 0.5, length * 0.5
    for i in range(6):
        a0 = 2.0 * math.pi * (i / 6.0)
        a1 = 2.0 * math.pi * ((i + 1) / 6.0)
        p0 = np.array([x0, radius * math.cos(a0), radius * math.sin(a0)], np.float32)
        p1 = np.array([x1, radius * math.cos(a0), radius * math.sin(a0)], np.float32)
        p2 = np.array([x1, radius * math.cos(a1), radius * math.sin(a1)], np.float32)
        p3 = np.array([x0, radius * math.cos(a1), radius * math.sin(a1)], np.float32)
        am = 0.5 * (a0 + a1)
        n = np.array([0.0, math.cos(am), math.sin(am)], np.float32)
        for pt, uv in ((p0, (0, 0)), (p1, (1, 0)), (p2, (1, 1)), (p3, (0, 1))):
            pos_list.append(pt); nrm_list.append(n); uv_list.append(uv)
        idx_list.extend([base, base + 1, base + 2, base, base + 2, base + 3])
        base += 4
    return (np.array(pos_list, np.float32), np.array(nrm_list, np.float32),
            np.array(uv_list, np.float32), np.array(idx_list, np.uint32))


def build_spare_probe_cord_mesh(sides=10):
    """Casually coiled black oscilloscope probe cable resting on the right bench."""
    ts = np.linspace(0.0, 2.35 * math.pi, 48, dtype=np.float32)
    r = 24.0 + 3.2 * (ts / (2.0 * math.pi))
    x = r * np.cos(ts)
    z = r * np.sin(ts) * 0.85
    y = np.full_like(x, 1.8) + np.sin(ts * 2.0) * 0.55
    # Lead-out tail toward the probe wand handle
    tail = np.array([
        [x[-1] - 6.0, 2.2, z[-1] + 10.0],
        [x[-1] - 12.0, 3.0, z[-1] + 22.0],
        [x[-1] - 16.0, 3.5, z[-1] + 34.0],
    ], dtype=np.float32)
    pts = np.vstack([np.column_stack([x, y, z]), tail])
    return build_tube_along_polyline(pts, radius=1.55, sides=sides)


def build_lamp_power_cord_mesh(sides=10):
    """Braided dark power cord trailing naturally from the rear of the architect lamp to the back wall."""
    ctrl = np.array([
        [-295.0, -126.0, -6.0],
        [-305.0, -127.6, -55.0],
        [-285.0, -127.8, -135.0],
        [-255.0, -127.8, -225.0],
        [-248.0, -127.5, -335.0],
    ], dtype=np.float32)
    pts = _catmull_rom_subdivide(ctrl, substeps=6)
    return build_tube_along_polyline(pts, radius=1.9, sides=sides)


class HelicalSpringGeometry(QQuick3DGeometry):
    def __init__(self, coil_radius=3.0, wire_radius=0.65, height=96.0, turns=14.0, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_helical_spring_mesh(
            coil_radius=coil_radius, wire_radius=wire_radius, height=height, turns=turns
        ))


class CoffeeMugGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_coffee_mug_mesh())


class MugHandleGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_mug_handle_mesh())


class HexPencilBarrelGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_hex_pencil_barrel_mesh())


class SpareProbeCordGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_spare_probe_cord_mesh())


class LampPowerCordGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_lamp_power_cord_mesh())


class LabWallChartTexture(QQuick3DTextureData):
    """512x384 aged late-80s Lissajous & harmonic ratio reference wall chart pinned behind the bench."""
    def __init__(self, parent=None):
        super().__init__(parent)
        W, H = 512, 384
        rng = np.random.default_rng(1988)
        paper = np.full((H, W, 3), [226.0, 218.0, 196.0], dtype=np.float32)
        paper += rng.normal(0.0, 3.2, (H, W, 1)).astype(np.float32)
        rgba = np.ascontiguousarray(np.dstack([np.clip(paper, 0, 255).astype(np.uint8),
                                               np.full((H, W), 255, dtype=np.uint8)]))
        img = QImage(rgba.data, W, H, W * 4, QImage.Format.Format_RGBA8888).copy()

        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # Double technical border
        p.setPen(QPen(QColor('#3b3d3a'), 3.0))
        p.drawRect(QRectF(14, 14, W - 28, H - 28))
        p.setPen(QPen(QColor('#6c6e68'), 1.2))
        p.drawRect(QRectF(20, 20, W - 40, H - 40))

        # Header band
        p.fillRect(QRectF(20, 20, W - 40, 46), QColor('#293640'))
        f_hdr = QFont('JetBrains Mono'); f_hdr.setPixelSize(17); f_hdr.setBold(True)
        p.setFont(f_hdr); p.setPen(QColor('#ebe4cf'))
        p.drawText(QRectF(24, 24, W - 48, 38), Qt.AlignmentFlag.AlignCenter,
                   'X-Y LISSAJOUS HARMONIC RATIOS  ·  C1-170')

        # 2x3 grid of crisp Lissajous reference figures
        ratios = ((1, 1, '1 : 1  (UNISON)'), (1, 2, '1 : 2  (OCTAVE)'), (2, 3, '2 : 3  (FIFTH)'),
                  (3, 4, '3 : 4  (FOURTH)'), (3, 5, '3 : 5  (SIXTH)'), (4, 5, '4 : 5  (THIRD)'))
        f_lbl = QFont('JetBrains Mono'); f_lbl.setPixelSize(11); f_lbl.setBold(True)
        p.setFont(f_lbl)
        ts = np.linspace(0.0, 2.0 * math.pi, 220)
        for idx, (fx, fy, lbl) in enumerate(ratios):
            col, row = idx % 3, idx // 3
            cx = 104 + col * 152
            cy = 140 + row * 136
            p.setPen(QPen(QColor('#9c9684'), 1.0))
            p.drawRect(QRectF(cx - 58, cy - 48, 116, 96))
            p.setPen(QPen(QColor('#1e6e3e'), 2.0))
            prev = None
            for t in ts:
                pt = QPointF(cx + 42.0 * math.sin(fx * t + math.pi * 0.25),
                             cy + 34.0 * math.sin(fy * t))
                if prev is not None:
                    p.drawLine(prev, pt)
                prev = pt
            p.setPen(QColor('#2c2a26'))
            p.drawText(QRectF(cx - 64, cy + 50, 128, 18), Qt.AlignmentFlag.AlignCenter, lbl)

        p.end()
        _upload_rgba(self, _qimage_to_rgba(img)[::-1].copy())


