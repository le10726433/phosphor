"""High-fidelity procedural 3D hardware meshes (QQuick3DGeometry) and PBR textures
(QQuick3DTextureData) modeled after the classic late-80s C1-170 bench oscilloscope.

Generates all 3D geometry deterministically in NumPy:
  - Rounded-corner extruded front/rear cast-aluminum chassis bezel frame
  - Rounded-outer / crisp-rectangular-inner CRT monitor hood + 48x48 curved CRT glass tube
  - Light-grey fluted dual-concentric selector knobs + small trim knobs
  - Stadium/pill-shaped tactile instrument pushbuttons
  - Machined 3D BNC coaxial connectors (6-sided hex nut + bayonet lugs)
Plus high-DPI PBR maps:
  - Procedural studio/lab equirectangular IBL light probe (lightProbe)
  - 512x512 hammered enamel albedo (with micro-scratches & patina), normal, and roughness maps
  - 512x256 perforated circular-hole side ventilation grille texture
  - 512x512 RGBA curved CRT cover-glass overlay (soda-lime edge glint, bezel shadow, studio sheen)
  - 2048x1024 two-tone C1-170 faceplate with baked contact AO + backlit dark-mode emissive mask
"""
import math
from pathlib import Path
import numpy as np
from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter, QPen,
                           QRadialGradient, QVector3D)
from PySide6.QtQuick3D import QQuick3DGeometry, QQuick3DTextureData

from phosphor.display.phosphor import QUALITY_LOW, QUALITY_MEDIUM

TEXTURE_ROOT = Path(__file__).resolve().parents[1] / 'assets' / 'textures' / 'desk3d'
TEXTURE_TIERS = ('1k', '2k')


def texture_tier_for_quality(quality):
    """Map Phosphor's graphics_quality setting to the 1K or 2K PBR texture tier."""
    return '1k' if quality in (QUALITY_LOW, QUALITY_MEDIUM) else '2k'


def texture_dir_url(tier):
    """Return a trailing-slash file:// URL for the requested 1k/2k PBR texture directory."""
    safe = tier if tier in TEXTURE_TIERS else '2k'
    return QUrl.fromLocalFile(str(TEXTURE_ROOT / safe)).toString().rstrip('/') + '/'


def format_sample_rate(rate_hz):
    """Format an audio sample rate in Hz for the C1-170 faceplate (e.g. '48kHz', '44.1kHz')."""
    hz = float(rate_hz or 48000.0)
    khz = hz / 1000.0
    if abs(khz - round(khz)) < 0.05:
        return f'{int(round(khz))}kHz'
    return f'{khz:.1f}kHz'


def _pack_geometry(geom, positions, normals, uvs, indices):
    """Upload interleaved float32 (pos3, norm3, uv2) + uint32 triangles into a QQuick3DGeometry."""
    pos = np.asarray(positions, dtype=np.float32)
    nrm = np.asarray(normals, dtype=np.float32)
    uv = np.asarray(uvs, dtype=np.float32)
    idx = np.asarray(indices, dtype=np.uint32).ravel()
    verts = np.ascontiguousarray(np.hstack([pos, nrm, uv]), dtype=np.float32)

    geom.clear()
    geom.setPrimitiveType(QQuick3DGeometry.PrimitiveType.Triangles)
    geom.setStride(8 * 4)
    geom.addAttribute(QQuick3DGeometry.Attribute.Semantic.PositionSemantic, 0,
                      QQuick3DGeometry.Attribute.ComponentType.F32Type)
    geom.addAttribute(QQuick3DGeometry.Attribute.Semantic.NormalSemantic, 3 * 4,
                      QQuick3DGeometry.Attribute.ComponentType.F32Type)
    geom.addAttribute(QQuick3DGeometry.Attribute.Semantic.TexCoordSemantic, 6 * 4,
                      QQuick3DGeometry.Attribute.ComponentType.F32Type)
    geom.addAttribute(QQuick3DGeometry.Attribute.Semantic.IndexSemantic, 0,
                      QQuick3DGeometry.Attribute.ComponentType.U32Type)
    geom.setVertexData(QByteArray(verts.tobytes()))
    geom.setIndexData(QByteArray(idx.tobytes()))
    mn = pos.min(axis=0)
    mx = pos.max(axis=0)
    geom.setBounds(QVector3D(float(mn[0]), float(mn[1]), float(mn[2])),
                   QVector3D(float(mx[0]), float(mx[1]), float(mx[2])))
    geom.update()


def _quads_to_mesh(quads):
    """Convert a list of 4-vertex quads into flat-shaded (positions, normals, uvs, indices)."""
    pos_list, nrm_list, uv_list, idx_list = [], [], [], []
    base = 0
    for q in quads:
        p0, p1, p2, p3 = [np.array(pt, dtype=np.float32) for pt in q]
        n = np.cross(p1 - p0, p2 - p0)
        nl = float(np.linalg.norm(n)) or 1.0
        n = n / nl
        for pt, uv in zip((p0, p1, p2, p3), ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))):
            pos_list.append(pt); nrm_list.append(n); uv_list.append(uv)
        idx_list.extend([base, base + 1, base + 2, base, base + 2, base + 3])
        base += 4
    return (np.array(pos_list, np.float32), np.array(nrm_list, np.float32),
            np.array(uv_list, np.float32), np.array(idx_list, np.uint32))


def _rounded_rect_loop(w, h, r, segs_per_corner=8):
    """CCW 2D polyline of a rounded rectangle centered at (0, 0)."""
    r = max(0.05, float(r))
    hw, hh = w * 0.5 - r, h * 0.5 - r
    pts = []
    corners = ((hw, hh, 0.0), (-hw, hh, 0.5 * math.pi),
               (-hw, -hh, math.pi), (hw, -hh, 1.5 * math.pi))
    for cx, cy, start_ang in corners:
        for i in range(segs_per_corner + 1):
            a = start_ang + (0.5 * math.pi) * (i / segs_per_corner)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _loft_rings(rings, closed=True, uv_scale=120.0, smooth_loop=True):
    """Loft a sequence of equal-length 3D closed rings with continuous arc-length UVs.

    When smooth_loop=True, adjacent quad normals along each ring perimeter are smoothly
    averaged so rounded corners render silky-smooth while preserving crisp bevel creases
    between successive longitudinal strips.
    """
    n_pts = len(rings[0])
    n_rings = len(rings)
    ring_arrs = [np.asarray(r, dtype=np.float32) for r in rings]
    s_rings = []
    for arr in ring_arrs:
        nxt = np.roll(arr, -1, axis=0)
        seg = np.linalg.norm(nxt - arr, axis=1)
        s = np.concatenate([[0.0], np.cumsum(seg)]) / float(uv_scale)
        s_rings.append(s)
    ring_pairs = [(r, r + 1) for r in range(n_rings - 1)]
    if closed and n_rings >= 3:
        ring_pairs.append((n_rings - 1, 0))
    t_vals = [0.0]
    for a, b in ring_pairs:
        step = float(np.mean(np.linalg.norm(ring_arrs[b] - ring_arrs[a], axis=1))) / float(uv_scale)
        t_vals.append(t_vals[-1] + step)

    pos_list, nrm_list, uv_list, idx_list = [], [], [], []
    base = 0
    for p_idx, (a, b) in enumerate(ring_pairs):
        r0, r1 = ring_arrs[a], ring_arrs[b]
        s0, s1 = s_rings[a], s_rings[b]
        t0, t1 = t_vals[p_idx], t_vals[p_idx + 1]

        # Precompute per-segment face normals around this strip
        seg_nrms = np.empty((n_pts, 3), dtype=np.float32)
        for i in range(n_pts):
            j = (i + 1) % n_pts
            n = np.cross(r0[j] - r0[i], r1[j] - r0[i])
            nl = float(np.linalg.norm(n)) or 1.0
            seg_nrms[i] = n / nl

        if smooth_loop:
            # Smooth-average adjacent normals when corner angle is smooth (< 65 deg), crease sharp corners
            vert_nrms = np.empty((n_pts, 3), dtype=np.float32)
            for i in range(n_pts):
                n_prev = seg_nrms[(i - 1) % n_pts]
                n_curr = seg_nrms[i]
                if float(np.dot(n_prev, n_curr)) > 0.42:
                    avg = n_prev + n_curr
                    al = float(np.linalg.norm(avg)) or 1.0
                    vert_nrms[i] = avg / al
                else:
                    vert_nrms[i] = n_curr
        else:
            vert_nrms = seg_nrms

        for i in range(n_pts):
            j = (i + 1) % n_pts
            p0, p1, p2, p3 = r0[i], r0[j], r1[j], r1[i]
            if smooth_loop:
                n_prev_i = seg_nrms[(i - 1) % n_pts]
                n_curr = seg_nrms[i]
                n_next_j = seg_nrms[j]
                ni = vert_nrms[i] if float(np.dot(n_prev_i, n_curr)) > 0.42 else n_curr
                nj = vert_nrms[j] if float(np.dot(n_curr, n_next_j)) > 0.42 else n_curr
            else:
                ni = nj = seg_nrms[i]
            u0, u1 = float(s0[i]), float(s0[i + 1])
            u3, u2 = float(s1[i]), float(s1[i + 1])
            for pt, vn, uv in ((p0, ni, (u0, t0)), (p1, nj, (u1, t0)),
                               (p2, nj, (u2, t1)), (p3, ni, (u3, t1))):
                pos_list.append(pt); nrm_list.append(vn); uv_list.append(uv)
            idx_list.extend([base, base + 1, base + 2, base, base + 2, base + 3])
            base += 4
    return (np.array(pos_list, np.float32), np.array(nrm_list, np.float32),
            np.array(uv_list, np.float32), np.array(idx_list, np.uint32))


def build_curved_crt_mesh(width=148.0, height=138.0, bulge=4.5, segs=48):
    """48x48 spherical-bulge curved CRT glass tube facing +Z with smooth normals and UVs."""
    u = np.linspace(0.0, 1.0, segs + 1, dtype=np.float32)
    v = np.linspace(0.0, 1.0, segs + 1, dtype=np.float32)
    uu, vv = np.meshgrid(u, v)
    nx_grid = (uu - 0.5) * 2.0
    ny_grid = (vv - 0.5) * 2.0
    r2 = nx_grid * nx_grid + ny_grid * ny_grid
    dome = np.cos(nx_grid * (math.pi * 0.42)) * np.cos(ny_grid * (math.pi * 0.42))
    z = bulge * (dome - 0.12 * r2 * 0.25)

    x = (uu - 0.5) * width
    y = (vv - 0.5) * height

    dz_dx = bulge * (-math.pi * 0.42 * np.sin(nx_grid * (math.pi * 0.42)) * np.cos(ny_grid * (math.pi * 0.42)) * (2.0 / width))
    dz_dy = bulge * (-math.pi * 0.42 * np.cos(nx_grid * (math.pi * 0.42)) * np.sin(ny_grid * (math.pi * 0.42)) * (2.0 / height))
    norm_x, norm_y, norm_z = -dz_dx, -dz_dy, np.ones_like(z)
    inv_len = 1.0 / np.sqrt(norm_x * norm_x + norm_y * norm_y + norm_z * norm_z)

    positions = np.stack([x, y, z], axis=-1).reshape(-1, 3)
    normals = np.stack([norm_x * inv_len, norm_y * inv_len, norm_z * inv_len], axis=-1).reshape(-1, 3)
    uvs = np.stack([uu, vv], axis=-1).reshape(-1, 2)

    stride = segs + 1
    j, i = np.meshgrid(np.arange(segs, dtype=np.uint32), np.arange(segs, dtype=np.uint32), indexing='ij')
    v0 = j * stride + i
    v1 = v0 + 1
    v2 = v0 + stride
    v3 = v2 + 1
    indices = np.stack([v0, v1, v3, v0, v3, v2], axis=-1).reshape(-1)
    return positions, normals, uvs, indices


def build_rounded_bezel_frame_mesh(outer_w=444.0, outer_h=222.0, inner_w=420.0, inner_h=198.0,
                                   outer_r=14.0, inner_r=8.0, depth=20.0):
    """C1-170 rounded-corner cast-aluminum front/rear perimeter chassis bezel frame."""
    def ring(w, h, r, z):
        return [(x, y, z) for x, y in _rounded_rect_loop(w, h, r, segs_per_corner=12)]

    rings = [
        ring(outer_w, outer_h, outer_r, 0.0),
        ring(outer_w, outer_h, outer_r, depth - 3.2),
        ring(outer_w - 2.0, outer_h - 2.0, max(2.0, outer_r - 1.0), depth - 1.0),
        ring(outer_w - 5.2, outer_h - 5.2, max(2.0, outer_r - 2.6), depth),
        ring(inner_w + 5.0, inner_h + 5.0, inner_r + 2.0, depth),
        ring(inner_w + 1.8, inner_h + 1.8, inner_r + 0.8, depth - 1.2),
        ring(inner_w, inner_h, inner_r, depth - 3.8),
        ring(inner_w, inner_h, inner_r, 0.0),
    ]
    return _loft_rings(rings, closed=True)


def build_crt_hood_mesh(inner_w=146.0, inner_h=136.0, outer_w=174.0, outer_h=164.0, depth=18.0):
    """C1-170 CRT hood: smooth rounded outer bezel framing a crisp 90-deg rectangular CRT opening."""
    def ring(w, h, r, z):
        return [(x, y, z) for x, y in _rounded_rect_loop(w, h, r, segs_per_corner=12)]

    rings = [
        ring(outer_w, outer_h, 8.5, 0.0),
        ring(outer_w, outer_h, 8.5, depth - 2.8),
        ring(outer_w - 1.5, outer_h - 1.5, 7.8, depth - 0.9),
        ring(outer_w - 3.8, outer_h - 3.8, 7.0, depth),
        ring(inner_w + 14.0, inner_h + 14.0, 2.4, depth),
        ring(inner_w, inner_h, 0.40, 1.0),
        ring(inner_w, inner_h, 0.40, 0.0),
    ]
    return _loft_rings(rings, closed=True)


def build_knurled_knob_mesh(radius=14.0, height=17.5, flutes=30, dual=True):
    """C1-170 light-grey fluted rotary selector knob (smooth bevels + crisp machined flutes)."""
    pos_list, nrm_list, uv_list, idx_list = [], [], [], []
    base = 0

    def add_ring_strip(r0, z0, r1, z1, fluted=False):
        nonlocal base
        steps = flutes * 2
        dz = z1 - z0
        dr = r1 - r0
        slope_len = math.hypot(dr, dz) or 1.0
        # Outward radial/axial normal components for smooth non-fluted cones/cylinders
        nr = dz / slope_len
        nz = -dr / slope_len
        for i in range(steps):
            a0 = 2.0 * math.pi * (i / steps)
            a1 = 2.0 * math.pi * ((i + 1) / steps)
            f0 = (0.91 if (i % 2 == 0) else 1.0) if fluted else 1.0
            f1 = (0.91 if ((i + 1) % 2 == 0) else 1.0) if fluted else 1.0
            p0 = np.array([r0 * f0 * math.cos(a0), r0 * f0 * math.sin(a0), z0], np.float32)
            p1 = np.array([r0 * f1 * math.cos(a1), r0 * f1 * math.sin(a1), z0], np.float32)
            p2 = np.array([r1 * f1 * math.cos(a1), r1 * f1 * math.sin(a1), z1], np.float32)
            p3 = np.array([r1 * f0 * math.cos(a0), r1 * f0 * math.sin(a0), z1], np.float32)
            if fluted:
                n = np.cross(p1 - p0, p2 - p0)
                n = n / (float(np.linalg.norm(n)) or 1.0)
                n0 = n1 = n
            else:
                n0 = np.array([nr * math.cos(a0), nr * math.sin(a0), nz], np.float32)
                n1 = np.array([nr * math.cos(a1), nr * math.sin(a1), nz], np.float32)
                n0 /= (float(np.linalg.norm(n0)) or 1.0)
                n1 /= (float(np.linalg.norm(n1)) or 1.0)
            for pt, vn, uv in ((p0, n0, (i / steps, 0.0)), (p1, n1, ((i + 1) / steps, 0.0)),
                               (p2, n1, ((i + 1) / steps, 1.0)), (p3, n0, (i / steps, 1.0))):
                pos_list.append(pt); nrm_list.append(vn); uv_list.append(uv)
            idx_list.extend([base, base + 1, base + 2, base, base + 2, base + 3])
            base += 4

    def add_cap(r_cap, z_cap):
        nonlocal base
        steps = flutes * 2
        center = np.array([0.0, 0.0, z_cap], np.float32)
        up = np.array([0.0, 0.0, 1.0], np.float32)
        for i in range(steps):
            a0 = 2.0 * math.pi * (i / steps)
            a1 = 2.0 * math.pi * ((i + 1) / steps)
            p1 = np.array([r_cap * math.cos(a0), r_cap * math.sin(a0), z_cap], np.float32)
            p2 = np.array([r_cap * math.cos(a1), r_cap * math.sin(a1), z_cap], np.float32)
            for pt, uv in ((center, (0.5, 0.5)), (p1, (0.5 + 0.5 * math.cos(a0), 0.5 + 0.5 * math.sin(a0))),
                           (p2, (0.5 + 0.5 * math.cos(a1), 0.5 + 0.5 * math.sin(a1)))):
                pos_list.append(pt); nrm_list.append(up); uv_list.append(uv)
            idx_list.extend([base, base + 1, base + 2])
            base += 3

    add_ring_strip(radius * 1.18, 0.0, radius * 1.16, height * 0.05, fluted=False)
    add_ring_strip(radius * 1.16, height * 0.05, radius * 1.04, height * 0.16, fluted=False)
    if dual:
        add_ring_strip(radius * 1.02, height * 0.16, radius * 0.94, height * 0.55, fluted=True)
        add_ring_strip(radius * 0.94, height * 0.55, radius * 0.88, height * 0.60, fluted=False)
        add_ring_strip(radius * 0.88, height * 0.60, radius * 0.68, height * 0.63, fluted=False)
        add_ring_strip(radius * 0.66, height * 0.63, radius * 0.62, height * 0.92, fluted=True)
        add_ring_strip(radius * 0.62, height * 0.92, radius * 0.57, height * 0.97, fluted=False)
        add_ring_strip(radius * 0.57, height * 0.97, radius * 0.52, height, fluted=False)
        add_cap(radius * 0.52, height)
    else:
        add_ring_strip(radius * 1.0, height * 0.16, radius * 0.92, height * 0.86, fluted=True)
        add_ring_strip(radius * 0.92, height * 0.86, radius * 0.84, height * 0.94, fluted=False)
        add_ring_strip(radius * 0.84, height * 0.94, radius * 0.76, height, fluted=False)
        add_cap(radius * 0.76, height)

    return (np.array(pos_list, np.float32), np.array(nrm_list, np.float32),
            np.array(uv_list, np.float32), np.array(idx_list, np.uint32))


def build_keycap_mesh(width=15.0, height=7.8, depth=7.5):
    """C1-170 smooth-domed stadium/pill tactile instrument pushbutton extending along +Z."""
    r_base = height * 0.48
    r_top = r_base * 0.86
    ring0 = [(x, y, 0.0) for x, y in _rounded_rect_loop(width, height, r_base, segs_per_corner=10)]
    ring1 = [(x, y, depth - 1.9) for x, y in _rounded_rect_loop(width * 0.95, height * 0.94, r_top, segs_per_corner=10)]
    ring2 = [(x, y, depth - 0.65) for x, y in _rounded_rect_loop(width * 0.89, height * 0.86, r_top * 0.92, segs_per_corner=10)]
    ring3 = [(x, y, depth) for x, y in _rounded_rect_loop(width * 0.78, height * 0.74, r_top * 0.82, segs_per_corner=10)]

    pos_l, nrm_l, uv_l, idx_l = _loft_rings([ring0, ring1, ring2, ring3], closed=False)
    pos_list, nrm_list, uv_list, idx_list = list(pos_l), list(nrm_l), list(uv_l), list(idx_l)
    base = len(pos_list)
    center = np.array([0.0, 0.0, depth + 0.35], np.float32)  # Subtle tactile pillow dome
    up = np.array([0.0, 0.0, 1.0], np.float32)
    n_pts = len(ring3)
    for i in range(n_pts):
        j = (i + 1) % n_pts
        p1 = np.array(ring3[i], np.float32)
        p2 = np.array(ring3[j], np.float32)
        for pt in (center, p1, p2):
            pos_list.append(pt); nrm_list.append(up); uv_list.append((0.5, 0.5))
        idx_list.extend([base, base + 1, base + 2])
        base += 3
    return (np.array(pos_list, np.float32), np.array(nrm_list, np.float32),
            np.array(uv_list, np.float32), np.array(idx_list, np.uint32))


def build_bnc_jack_mesh():
    """Machined 3D BNC coaxial connector: 6-sided hex nut + stepped shoulder + bayonet barrel."""
    pos_list, nrm_list, uv_list, idx_list = [], [], [], []
    base = 0

    def add_prism(r0, z0, r1, z1, sides):
        nonlocal base
        for i in range(sides):
            a0 = 2.0 * math.pi * (i / sides)
            a1 = 2.0 * math.pi * ((i + 1) / sides)
            p0 = np.array([r0 * math.cos(a0), r0 * math.sin(a0), z0], np.float32)
            p1 = np.array([r0 * math.cos(a1), r0 * math.sin(a1), z0], np.float32)
            p2 = np.array([r1 * math.cos(a1), r1 * math.sin(a1), z1], np.float32)
            p3 = np.array([r1 * math.cos(a0), r1 * math.sin(a0), z1], np.float32)
            n = np.cross(p1 - p0, p2 - p0)
            n = n / (float(np.linalg.norm(n)) or 1.0)
            for pt, uv in zip((p0, p1, p2, p3), ((0, 0), (1, 0), (1, 1), (0, 1))):
                pos_list.append(pt); nrm_list.append(n); uv_list.append(uv)
            idx_list.extend([base, base + 1, base + 2, base, base + 2, base + 3])
            base += 4

    def add_annulus(r_in, r_out, z, sides):
        nonlocal base
        up = np.array([0.0, 0.0, 1.0], np.float32)
        for i in range(sides):
            a0 = 2.0 * math.pi * (i / sides)
            a1 = 2.0 * math.pi * ((i + 1) / sides)
            p0 = np.array([r_in * math.cos(a0), r_in * math.sin(a0), z], np.float32)
            p1 = np.array([r_out * math.cos(a0), r_out * math.sin(a0), z], np.float32)
            p2 = np.array([r_out * math.cos(a1), r_out * math.sin(a1), z], np.float32)
            p3 = np.array([r_in * math.cos(a1), r_in * math.sin(a1), z], np.float32)
            for pt, uv in zip((p0, p1, p2, p3), ((0, 0), (1, 0), (1, 1), (0, 1))):
                pos_list.append(pt); nrm_list.append(up); uv_list.append(uv)
            idx_list.extend([base, base + 1, base + 2, base, base + 2, base + 3])
            base += 4

    add_prism(8.5, 0.0, 8.5, 3.2, 6)
    add_annulus(5.2, 8.5, 3.2, 6)
    add_prism(5.6, 3.2, 5.2, 5.5, 24)
    add_prism(4.9, 5.5, 4.9, 14.0, 24)
    add_annulus(3.6, 4.9, 14.0, 24)
    for sx in (-1.0, 1.0):
        x0, x1 = sx * 4.7, sx * 6.2
        xa, xb = min(x0, x1), max(x0, x1)
        y0, y1, z0, z1 = -1.1, 1.1, 9.0, 10.8
        lug_quads = [
            ((xa, y0, z1), (xb, y0, z1), (xb, y1, z1), (xa, y1, z1)),
            ((xa, y1, z0), (xb, y1, z0), (xb, y1, z1), (xa, y1, z1)),
            ((xa, y0, z1), (xb, y0, z1), (xb, y0, z0), (xa, y0, z0)),
            ((xb, y0, z0), (xb, y1, z0), (xb, y1, z1), (xb, y0, z1)) if sx > 0 else
            ((xa, y1, z0), (xa, y0, z0), (xa, y0, z1), (xa, y1, z1)),
        ]
        lp, ln, lu, li = _quads_to_mesh(lug_quads)
        pos_list.extend(lp); nrm_list.extend(ln); uv_list.extend(lu)
        idx_list.extend((li + base).tolist())
        base += len(lp)

    return (np.array(pos_list, np.float32), np.array(nrm_list, np.float32),
            np.array(uv_list, np.float32), np.array(idx_list, np.uint32))


class CurvedCrtGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_curved_crt_mesh())


class RoundedBezelFrameGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_rounded_bezel_frame_mesh())


class CrtHoodGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_crt_hood_mesh())


class KnurledKnobGeometry(QQuick3DGeometry):
    """Large dual-concentric C1-170 selector knob."""
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_knurled_knob_mesh(radius=14.5, height=19.5, flutes=26, dual=True))


class SmallTrimKnobGeometry(QQuick3DGeometry):
    """Compact fluted C1-170 trim / vernier knob."""
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_knurled_knob_mesh(radius=8.5, height=13.0, flutes=22, dual=False))


class KeycapGeometry(QQuick3DGeometry):
    """C1-170 pill/stadium tactile pushbutton."""
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_keycap_mesh())


class BncJackGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_bnc_jack_mesh())


def build_bnc_plug_collar_mesh(knurls=28):
    """Machined nickel-plated BNC cable plug: stepped bayonet sleeve + knurled grip + rear collar along +Z."""
    pos_list, nrm_list, uv_list, idx_list = [], [], [], []
    base = 0

    def add_strip(r0, z0, r1, z1, fluted=False):
        nonlocal base
        steps = knurls * 2
        dz, dr = z1 - z0, r1 - r0
        sl = math.hypot(dr, dz) or 1.0
        nr, nz = dz / sl, -dr / sl
        for i in range(steps):
            a0 = 2.0 * math.pi * (i / steps)
            a1 = 2.0 * math.pi * ((i + 1) / steps)
            f0 = (0.93 if (i % 2 == 0) else 1.0) if fluted else 1.0
            f1 = (0.93 if ((i + 1) % 2 == 0) else 1.0) if fluted else 1.0
            p0 = np.array([r0 * f0 * math.cos(a0), r0 * f0 * math.sin(a0), z0], np.float32)
            p1 = np.array([r0 * f1 * math.cos(a1), r0 * f1 * math.sin(a1), z0], np.float32)
            p2 = np.array([r1 * f1 * math.cos(a1), r1 * f1 * math.sin(a1), z1], np.float32)
            p3 = np.array([r1 * f0 * math.cos(a0), r1 * f0 * math.sin(a0), z1], np.float32)
            if fluted:
                n = np.cross(p1 - p0, p2 - p0)
                n = n / (float(np.linalg.norm(n)) or 1.0)
                n0 = n1 = n
            else:
                n0 = np.array([nr * math.cos(a0), nr * math.sin(a0), nz], np.float32)
                n1 = np.array([nr * math.cos(a1), nr * math.sin(a1), nz], np.float32)
                n0 /= (float(np.linalg.norm(n0)) or 1.0)
                n1 /= (float(np.linalg.norm(n1)) or 1.0)
            for pt, vn, uv in ((p0, n0, (i / steps, 0.0)), (p1, n1, ((i + 1) / steps, 0.0)),
                               (p2, n1, ((i + 1) / steps, 1.0)), (p3, n0, (i / steps, 1.0))):
                pos_list.append(pt); nrm_list.append(vn); uv_list.append(uv)
            idx_list.extend([base, base + 1, base + 2, base, base + 2, base + 3])
            base += 4

    # Front nose chamfer -> smooth bayonet locking sleeve -> knurled barrel -> tapered nut -> ferrule
    add_strip(5.0, -7.8, 6.2, -6.5, fluted=False)
    add_strip(6.2, -6.5, 6.2, -2.5, fluted=False)
    add_strip(6.2, -2.5, 6.8, -1.8, fluted=False)
    add_strip(6.8, -1.8, 6.8, 4.8, fluted=True)
    add_strip(6.8, 4.8, 5.8, 5.8, fluted=False)
    add_strip(5.8, 5.8, 5.2, 8.2, fluted=False)
    add_strip(5.2, 8.2, 0.01, 8.2, fluted=False)
    return (np.array(pos_list, np.float32), np.array(nrm_list, np.float32),
            np.array(uv_list, np.float32), np.array(idx_list, np.uint32))


def build_strain_boot_mesh(segs=28):
    """Tapered multi-rib rubber BNC strain-relief boot extending along +Z (z = 7.6..21.0)."""
    profile = [(5.3, 7.6), (5.3, 9.6), (4.8, 10.2)]
    # 5 flexible elastomer relief ribs stepping down toward cable diameter (2.4)
    for k in range(5):
        z0 = 10.5 + k * 1.95
        r_hi = 4.65 - k * 0.36
        r_lo = r_hi - 0.65
        profile.extend([
            (r_hi, z0),
            (r_hi, z0 + 0.85),
            (r_lo, z0 + 1.15),
            (r_lo, z0 + 1.65),
        ])
    profile.extend([(2.75, 20.6), (2.4, 21.4), (0.01, 21.4)])

    m = len(profile)
    angles = np.linspace(0.0, 2.0 * math.pi, segs + 1, dtype=np.float32)
    ca, sa = np.cos(angles), np.sin(angles)
    pos = np.empty((m, segs + 1, 3), dtype=np.float32)
    nrm = np.empty((m, segs + 1, 3), dtype=np.float32)
    uv = np.empty((m, segs + 1, 2), dtype=np.float32)

    for i, (r, z) in enumerate(profile):
        i0, i1 = max(0, i - 1), min(m - 1, i + 1)
        dr = profile[i1][0] - profile[i0][0]
        dz = profile[i1][1] - profile[i0][1]
        ln = math.hypot(dr, dz) or 1.0
        nr, nz = dz / ln, -dr / ln
        pos[i, :, 0] = r * ca
        pos[i, :, 1] = r * sa
        pos[i, :, 2] = z
        nrm[i, :, 0] = nr * ca
        nrm[i, :, 1] = nr * sa
        nrm[i, :, 2] = nz
        uv[i, :, 0] = angles / (2.0 * math.pi)
        uv[i, :, 1] = i / max(1.0, float(m - 1))

    nlen = np.linalg.norm(nrm, axis=2, keepdims=True)
    nrm = np.where(nlen > 1e-5, nrm / np.maximum(nlen, 1e-5), np.array([0.0, 0.0, 1.0], dtype=np.float32))
    stride = segs + 1
    j, k = np.meshgrid(np.arange(m - 1, dtype=np.uint32), np.arange(segs, dtype=np.uint32), indexing='ij')
    v0 = j * stride + k
    v1 = v0 + 1
    v2 = v0 + stride
    v3 = v2 + 1
    indices = np.stack([v0, v1, v3, v0, v3, v2], axis=-1).reshape(-1)
    return pos.reshape(-1, 3), nrm.reshape(-1, 3), uv.reshape(-1, 2), indices


def build_bail_handle_mesh(radius=11.0, sides=20):
    """Continuous smooth-bent tubular C1-170 carry-handle / tilt-stand U-bail."""
    # Spine points from left side pivot -> smooth corner bend -> front bar -> smooth corner bend -> right side pivot
    p_l_piv = np.array([-233.0, -8.0, -112.0], dtype=np.float32)
    p_l_cor = np.array([-233.0, -120.0, 24.0], dtype=np.float32)
    p_r_cor = np.array([233.0, -120.0, 24.0], dtype=np.float32)
    p_r_piv = np.array([233.0, -8.0, -112.0], dtype=np.float32)

    bend = 24.0
    v_l_arm = p_l_piv - p_l_cor
    v_l_arm /= float(np.linalg.norm(v_l_arm))
    v_r_arm = p_r_piv - p_r_cor
    v_r_arm /= float(np.linalg.norm(v_r_arm))
    v_bar = np.array([1.0, 0.0, 0.0], dtype=np.float32)

    pts = [p_l_piv]
    # Left angled arm
    p_l_in = p_l_cor + v_l_arm * bend
    p_l_out = p_l_cor + v_bar * bend
    for t in np.linspace(0.15, 0.9, 6):
        pts.append(p_l_piv * (1.0 - t) + p_l_in * t)
    # Smooth quadratic Bezier corner bend (left)
    for t in np.linspace(0.0, 1.0, 9):
        pts.append((1 - t) ** 2 * p_l_in + 2 * (1 - t) * t * p_l_cor + t ** 2 * p_l_out)
    # Front horizontal bar
    p_r_in = p_r_cor - v_bar * bend
    p_r_out = p_r_cor + v_r_arm * bend
    for t in np.linspace(0.15, 0.85, 6):
        pts.append(p_l_out * (1.0 - t) + p_r_in * t)
    # Smooth quadratic Bezier corner bend (right)
    for t in np.linspace(0.0, 1.0, 9):
        pts.append((1 - t) ** 2 * p_r_in + 2 * (1 - t) * t * p_r_cor + t ** 2 * p_r_out)
    # Right angled arm
    for t in np.linspace(0.1, 1.0, 6):
        pts.append(p_r_out * (1.0 - t) + p_r_piv * t)

    pts = np.asarray(pts, dtype=np.float32)
    n = len(pts)
    pos = np.empty((n, sides + 1, 3), dtype=np.float32)
    nrm = np.empty((n, sides + 1, 3), dtype=np.float32)
    uv = np.empty((n, sides + 1, 2), dtype=np.float32)
    angles = np.linspace(0.0, 2.0 * math.pi, sides + 1, dtype=np.float32)
    ca, sa = np.cos(angles), np.sin(angles)

    prev_n = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    arc = 0.0
    for i in range(n):
        if i > 0:
            arc += float(np.linalg.norm(pts[i] - pts[i - 1]))
        t = (pts[1] - pts[0]) if i == 0 else ((pts[-1] - pts[-2]) if i == n - 1 else (pts[i + 1] - pts[i - 1]))
        t /= (float(np.linalg.norm(t)) or 1.0)
        normal = prev_n - t * float(np.dot(prev_n, t))
        nl = float(np.linalg.norm(normal))
        if nl < 1e-4:
            ref = np.array([0.0, 0.0, 1.0], dtype=np.float32) if abs(t[2]) < 0.8 else np.array([1.0, 0.0, 0.0], dtype=np.float32)
            normal = np.cross(t, ref)
            nl = float(np.linalg.norm(normal)) or 1.0
        normal /= nl
        prev_n = normal
        binorm = np.cross(t, normal)
        ring_n = ca[:, None] * normal[None, :] + sa[:, None] * binorm[None, :]
        pos[i] = pts[i][None, :] + ring_n * float(radius)
        nrm[i] = ring_n
        uv[i, :, 0] = angles / (2.0 * math.pi)
        uv[i, :, 1] = arc / 120.0

    stride = sides + 1
    j, k = np.meshgrid(np.arange(n - 1, dtype=np.uint32), np.arange(sides, dtype=np.uint32), indexing='ij')
    v0 = j * stride + k
    v1 = v0 + 1
    v2 = v0 + stride
    v3 = v2 + 1
    indices = np.stack([v0, v1, v3, v0, v3, v2], axis=-1).reshape(-1)
    return pos.reshape(-1, 3), nrm.reshape(-1, 3), uv.reshape(-1, 2), indices


def build_faceplate_screw_mesh(radius=3.4, height=2.2, segs=20):
    """Domed slotted pan-head machine screw facing +Z for the C1-170 faceplate corners."""
    pos_list, nrm_list, uv_list, idx_list = [], [], [], []
    base = 0
    # Smooth domed perimeter profile
    rings = [(radius, 0.0), (radius, height * 0.42), (radius * 0.86, height * 0.80), (radius * 0.50, height)]
    angles = np.linspace(0.0, 2.0 * math.pi, segs + 1, dtype=np.float32)
    ca, sa = np.cos(angles), np.sin(angles)
    m = len(rings)
    pos = np.empty((m, segs + 1, 3), dtype=np.float32)
    nrm = np.empty((m, segs + 1, 3), dtype=np.float32)
    uv = np.empty((m, segs + 1, 2), dtype=np.float32)
    for i, (r, z) in enumerate(rings):
        i0, i1 = max(0, i - 1), min(m - 1, i + 1)
        dr, dz = rings[i1][0] - rings[i0][0], rings[i1][1] - rings[i0][1]
        ln = math.hypot(dr, dz) or 1.0
        nr, nz = dz / ln, -dr / ln
        pos[i, :, 0] = r * ca; pos[i, :, 1] = r * sa; pos[i, :, 2] = z
        nrm[i, :, 0] = nr * ca; nrm[i, :, 1] = nr * sa; nrm[i, :, 2] = nz
        uv[i, :, 0] = angles / (2.0 * math.pi); uv[i, :, 1] = i / max(1.0, float(m - 1))
    nlen = np.linalg.norm(nrm, axis=2, keepdims=True)
    nrm = np.where(nlen > 1e-5, nrm / np.maximum(nlen, 1e-5), np.array([0.0, 0.0, 1.0], dtype=np.float32))
    pos_list.extend(pos.reshape(-1, 3)); nrm_list.extend(nrm.reshape(-1, 3)); uv_list.extend(uv.reshape(-1, 2))
    stride = segs + 1
    j, k = np.meshgrid(np.arange(m - 1, dtype=np.uint32), np.arange(segs, dtype=np.uint32), indexing='ij')
    v0 = j * stride + k; v1 = v0 + 1; v2 = v0 + stride; v3 = v2 + 1
    idx_list.extend(np.stack([v0, v1, v3, v0, v3, v2], axis=-1).reshape(-1).tolist())
    base = len(pos_list)
    # Flat top cap
    r_top, z_top = rings[-1]
    c = np.array([0.0, 0.0, z_top], np.float32)
    up = np.array([0.0, 0.0, 1.0], np.float32)
    for i in range(segs):
        p1 = np.array([r_top * ca[i], r_top * sa[i], z_top], np.float32)
        p2 = np.array([r_top * ca[i + 1], r_top * sa[i + 1], z_top], np.float32)
        for pt in (c, p1, p2):
            pos_list.append(pt); nrm_list.append(up); uv_list.append((0.5, 0.5))
        idx_list.extend([base, base + 1, base + 2])
        base += 3
    return (np.array(pos_list, np.float32), np.array(nrm_list, np.float32),
            np.array(uv_list, np.float32), np.array(idx_list, np.uint32))


class BncPlugCollarGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_bnc_plug_collar_mesh())


class StrainBootGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_strain_boot_mesh())


class BailHandleGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_bail_handle_mesh())


class FaceplateScrewGeometry(QQuick3DGeometry):
    def __init__(self, parent=None):
        super().__init__(parent)
        _pack_geometry(self, *build_faceplate_screw_mesh())


# --- High-DPI Procedural PBR Textures (QQuick3DTextureData) ---

def _upload_rgba(tex_data, rgba_u8, has_alpha=False):
    h, w, _ = rgba_u8.shape
    buf = np.ascontiguousarray(rgba_u8, dtype=np.uint8)
    tex_data.setSize(QSize(w, h))
    tex_data.setFormat(QQuick3DTextureData.Format.RGBA8)
    tex_data.setHasTransparency(bool(has_alpha))
    tex_data.setTextureData(QByteArray(buf.tobytes()))


def _qimage_to_rgba(img):
    conv = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = conv.width(), conv.height()
    ptr = conv.constBits()
    return np.frombuffer(ptr, dtype=np.uint8, count=w * h * 4).reshape((h, w, 4)).copy()


def _abs_heightfield(size=512):
    rng = np.random.default_rng(1988)
    fine = rng.normal(0.0, 1.0, (size, size)).astype(np.float32)
    med = np.repeat(np.repeat(rng.normal(0.0, 1.0, (size // 2, size // 2)).astype(np.float32), 2, axis=0), 2, axis=1)
    broad = np.repeat(np.repeat(rng.normal(0.0, 0.8, (size // 8, size // 8)).astype(np.float32), 8, axis=0), 8, axis=1)
    med = 0.4 * med + 0.15 * (np.roll(med, 1, 0) + np.roll(med, -1, 0) + np.roll(med, 1, 1) + np.roll(med, -1, 1))
    broad = 0.5 * broad + 0.125 * (np.roll(broad, 2, 0) + np.roll(broad, -2, 0) + np.roll(broad, 2, 1) + np.roll(broad, -2, 1))
    return 0.36 * fine + 0.40 * med + 0.24 * broad


class LabEnvironmentProbeTexture(QQuick3DTextureData):
    """512x256 equirectangular lab IBL map for realistic metallic & enamel PBR reflections."""
    def __init__(self, parent=None):
        super().__init__(parent)
        w, h = 512, 256
        u = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :]
        v = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]  # 0=bottom, 1=top in QQuick3D
        # Base studio/lab vertical gradient: dark oak floor -> neutral lab walls -> bright ceiling
        sky = np.clip((v - 0.45) / 0.55, 0.0, 1.0)
        floor = np.clip((0.45 - v) / 0.45, 0.0, 1.0)
        r = 72.0 + sky * 110.0 - floor * 38.0
        g = 74.0 + sky * 114.0 - floor * 42.0
        b = 76.0 + sky * 122.0 - floor * 48.0
        # Two bright overhead fluorescent ceiling light tubes + side softbox window
        tube1 = np.exp(-((v - 0.84) / 0.045) ** 2) * np.exp(-((u - 0.32) / 0.14) ** 2)
        tube2 = np.exp(-((v - 0.84) / 0.045) ** 2) * np.exp(-((u - 0.72) / 0.14) ** 2)
        win = np.exp(-((v - 0.58) / 0.12) ** 2) * np.exp(-((u - 0.15) / 0.09) ** 2)
        r = np.clip(r + (tube1 + tube2) * 145.0 + win * 90.0, 0, 255).astype(np.uint8)
        g = np.clip(g + (tube1 + tube2) * 150.0 + win * 85.0, 0, 255).astype(np.uint8)
        b = np.clip(b + (tube1 + tube2) * 160.0 + win * 75.0, 0, 255).astype(np.uint8)
        _upload_rgba(self, np.dstack([r, g, b, np.full((h, w), 255, dtype=np.uint8)]))


class AbsPlasticAlbedoTexture(QQuick3DTextureData):
    """512x512 C1-170 aged warm silver-grey hammered enamel with micro-scratches & subtle patina."""
    def __init__(self, parent=None):
        super().__init__(parent)
        h = _abs_heightfield(512)
        base = np.array([196.0, 194.0, 186.0], dtype=np.float32)
        rgb = np.clip(base[None, None, :] + h[:, :, None] * 6.5, 0, 255).astype(np.uint8)
        img = QImage(np.ascontiguousarray(np.dstack([rgb, np.full((512, 512), 255, np.uint8)])).data,
                     512, 512, 512 * 4, QImage.Format.Format_RGBA8888).copy()
        # Subtle hairline scratches & worn metallic scuffs
        rng = np.random.default_rng(1992)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for _ in range(55):
            x0, y0 = float(rng.uniform(0, 512)), float(rng.uniform(0, 512))
            ang = float(rng.uniform(0, 2 * math.pi))
            length = float(rng.uniform(8, 34))
            bright = bool(rng.random() > 0.45)
            col = QColor(235, 234, 228, 36) if bright else QColor(92, 90, 84, 34)
            p.setPen(QPen(col, float(rng.uniform(0.6, 1.3))))
            p.drawLine(QPointF(x0, y0), QPointF(x0 + math.cos(ang) * length, y0 + math.sin(ang) * length))
        p.end()
        _upload_rgba(self, _qimage_to_rgba(img))


class AbsPlasticNormalTexture(QQuick3DTextureData):
    """512x512 tangent-space normal map for hammered enamel & ABS micro-texture."""
    def __init__(self, parent=None):
        super().__init__(parent)
        h = _abs_heightfield(512)
        dx = (h - np.roll(h, 1, axis=1)) * 0.58
        dy = (h - np.roll(h, 1, axis=0)) * 0.58
        dz = np.ones_like(h)
        inv = 1.0 / np.sqrt(dx * dx + dy * dy + dz * dz)
        nx = np.clip((dx * inv * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
        ny = np.clip((dy * inv * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
        nz = np.clip((dz * inv * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
        _upload_rgba(self, np.dstack([nx, ny, nz, np.full((512, 512), 255, dtype=np.uint8)]))


class ChassisRoughnessTexture(QQuick3DTextureData):
    """512x512 organic roughness map (matte hammered enamel with glossy worn streaks & smudges)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        h = _abs_heightfield(512)
        # Map into realistic enamel roughness range ~0.26..0.68
        r_val = np.clip(118.0 + h * 34.0, 62, 185).astype(np.uint8)
        _upload_rgba(self, np.dstack([r_val, r_val, r_val, np.full((512, 512), 255, dtype=np.uint8)]))


class SideVentTexture(QQuick3DTextureData):
    """512x256 C1-170 upper-rear side panel perforated circular-hole ventilation grille."""
    def __init__(self, parent=None):
        super().__init__(parent)
        W, H = 512, 256
        img = QImage(W, H, QImage.Format.Format_RGBA8888)
        img.fill(QColor('#d5cdbc'))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cols, rows = 26, 7
        mx, my = 24.0, 28.0
        sx = (W - 2 * mx) / (cols - 1)
        sy = (H - 2 * my) / (rows - 1)
        for r in range(rows):
            for c in range(cols):
                cx = mx + c * sx
                cy = my + r * sy
                # Bright metallic catchlight on lower lip of each punched hole
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor('#e4e2da'))
                p.drawEllipse(QPointF(cx + 0.7, cy + 0.9), 4.6, 4.6)
                # Deep dark interior cavity
                p.setBrush(QColor('#121315'))
                p.drawEllipse(QPointF(cx, cy), 4.4, 4.4)
        p.end()
        _upload_rgba(self, _qimage_to_rgba(img)[::-1].copy())


class CrtGlassOverlayTexture(QQuick3DTextureData):
    """512x512 seamless RGBA curved CRT cover-glass reflection sheen (no hard border lines)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        S = 512
        img = QImage(S, S, QImage.Format.Format_RGBA8888)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1. Soft diagonal studio softbox / room-window reflection sweep
        ref = QLinearGradient(QPointF(S * 0.05, S * 0.05), QPointF(S * 0.80, S * 0.85))
        ref.setColorAt(0.0, QColor(235, 248, 240, 42))
        ref.setColorAt(0.26, QColor(230, 245, 236, 22))
        ref.setColorAt(0.45, QColor(220, 240, 230, 4))
        ref.setColorAt(0.65, QColor(220, 240, 230, 0))
        ref.setColorAt(0.84, QColor(215, 238, 225, 12))
        ref.setColorAt(1.0, QColor(215, 238, 225, 0))
        p.fillRect(QRectF(0, 0, S, S), ref)

        # 2. Soft curved-glass highlight in upper-left quadrant (fades smoothly to zero before edges)
        spot = QRadialGradient(QPointF(S * 0.28, S * 0.24), S * 0.34)
        spot.setColorAt(0.0, QColor(245, 255, 248, 38))
        spot.setColorAt(0.55, QColor(235, 250, 240, 12))
        spot.setColorAt(1.0, QColor(235, 250, 240, 0))
        p.fillRect(QRectF(0, 0, S, S), spot)
        p.end()
        _upload_rgba(self, _qimage_to_rgba(img)[::-1].copy(), has_alpha=True)


def _desk_wood_height(size=512):
    rng = np.random.default_rng(1985)
    y = np.linspace(0, 22.0 * math.pi, size, dtype=np.float32)[:, None]
    x = np.linspace(0, 5.0 * math.pi, size, dtype=np.float32)[None, :]
    warp = np.sin(x * 0.85 + np.sin(y * 0.14) * 1.6) * 2.4
    rings = np.sin(y + warp) * 0.5 + 0.5
    streaks = rng.normal(0.0, 1.0, (size, size)).astype(np.float32)
    for _ in range(5):
        streaks = 0.5 * streaks + 0.25 * np.roll(streaks, 1, axis=1) + 0.25 * np.roll(streaks, -1, axis=1)
    return rings * 0.65 + streaks * 0.28


class LabDeskAlbedoTexture(QQuick3DTextureData):
    """512x512 warm late-80s oak/walnut lab workbench wood-grain albedo."""
    def __init__(self, parent=None):
        super().__init__(parent)
        tone = _desk_wood_height(512)
        r = np.clip(104.0 + tone * 36.0, 0, 255).astype(np.uint8)
        g = np.clip(76.0 + tone * 27.0, 0, 255).astype(np.uint8)
        b = np.clip(52.0 + tone * 17.0, 0, 255).astype(np.uint8)
        _upload_rgba(self, np.dstack([r, g, b, np.full((512, 512), 255, dtype=np.uint8)]))


class LabDeskNormalTexture(QQuick3DTextureData):
    """512x512 wood-pore tangent-space normal map."""
    def __init__(self, parent=None):
        super().__init__(parent)
        h = _desk_wood_height(512)
        dx = (h - np.roll(h, 1, axis=1)) * 0.35
        dy = (h - np.roll(h, 1, axis=0)) * 0.65
        dz = np.ones_like(h)
        inv = 1.0 / np.sqrt(dx * dx + dy * dy + dz * dz)
        nx = np.clip((dx * inv * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
        ny = np.clip((dy * inv * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
        nz = np.clip((dz * inv * 0.5 + 0.5) * 255.0, 0, 255).astype(np.uint8)
        _upload_rgba(self, np.dstack([nx, ny, nz, np.full((512, 512), 255, dtype=np.uint8)]))


def _paint_faceplate_image(emissive_only=False, tier='2k', sample_rate_label='48kHz'):
    """Two-tone C1-170 silkscreen faceplate composited over real brushed-aluminum CC0 texture.

    Renders at 2048x1024 for the 2k tier (High/Ultra) and 1024x512 for the 1k tier (Low/Medium).
    """
    safe_tier = tier if tier in TEXTURE_TIERS else '2k'
    W, H = (1024, 512) if safe_tier == '1k' else (2048, 1024)
    scale = W / 2048.0
    if emissive_only:
        img = QImage(W, H, QImage.Format.Format_RGBA8888)
        img.fill(QColor(0, 0, 0, 255))
    else:
        al_path = TEXTURE_ROOT / safe_tier / 'aluminum_albedo.jpg'
        base_img = QImage(str(al_path)) if al_path.is_file() else QImage()
        if not base_img.isNull():
            img = base_img.scaled(W, H, Qt.AspectRatioMode.IgnoreAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGBA8888)
        else:
            img = QImage(W, H, QImage.Format.Format_RGBA8888)
            img.fill(QColor(194, 193, 186, 255))

    def wx(x): return (x + 212.0) / 424.0 * 2048.0
    def wy(y): return (101.0 - y) / 202.0 * 1024.0

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    if scale != 1.0:
        p.scale(scale, scale)

    ink = QColor('#4ef278') if emissive_only else QColor('#1f2224')
    blue = QColor('#5ca8ff') if emissive_only else QColor('#21539c')
    bar_bg = QColor('#1a3322') if emissive_only else QColor('#585c60')
    rule = QColor('#266638') if emissive_only else QColor('#5a5e61')

    def paint_ao_disc(cx, cy, r_px, alpha=88):
        g = QRadialGradient(QPointF(cx + 3.0, cy + 5.0), r_px)
        g.setColorAt(0.0, QColor(18, 20, 22, alpha))
        g.setColorAt(0.62, QColor(18, 20, 22, alpha // 2))
        g.setColorAt(1.0, QColor(18, 20, 22, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(cx + 3.0, cy + 5.0), r_px, r_px)

    if not emissive_only:
        # Iconic C1-170 dark slate-charcoal lower BNC/input strip
        bot_strip = QRectF(wx(-212), wy(-66), 2048.0, 1024.0 - wy(-66))
        p.fillRect(bot_strip, QColor(72, 76, 80, 224))
        p.setPen(QPen(QColor('#363a3e'), 3.0))
        p.drawLine(QPointF(0, wy(-66)), QPointF(2048.0, wy(-66)))

        # Perimeter ambient occlusion shadow tucked inside the front bezel frame
        for rect, x0, y0, x1, y1 in (
            (QRectF(0, 0, 2048.0, 30), 0, 0, 0, 30),
            (QRectF(0, 1024.0 - 30, 2048.0, 30), 0, 1024.0, 0, 1024.0 - 30),
            (QRectF(0, 0, 30, 1024.0), 0, 0, 30, 0),
            (QRectF(2048.0 - 30, 0, 30, 1024.0), 2048.0, 0, 2048.0 - 30, 0),
        ):
            ag = QLinearGradient(QPointF(x0, y0), QPointF(x1, y1))
            ag.setColorAt(0.0, QColor(24, 22, 18, 95))
            ag.setColorAt(1.0, QColor(24, 22, 18, 0))
            p.fillRect(rect, ag)

        # Baked contact AO shadow around the CRT hood perimeter
        crt_ao = QRectF(wx(-199) - 10, wy(80) - 8, wx(-25) - wx(-199) + 24, wy(-84) - wy(80) + 22)
        p.setPen(QPen(QColor(24, 22, 18, 60), 16.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(crt_ao, 24, 24)

        # Top-right boxed utility strip
        top_box = QRectF(wx(14), wy(93), wx(202) - wx(14), wy(59) - wy(93))
        p.setPen(QPen(QColor('#4a4e52'), 3.0))
        p.drawRoundedRect(top_box, 10, 10)

        # Dark charcoal section header bars
        p.fillRect(QRectF(wx(14), wy(53), wx(132) - wx(14), 22), bar_bg)
        p.fillRect(QRectF(wx(138), wy(53), wx(202) - wx(138), 22), bar_bg)
        p.setPen(QPen(QColor('#5c6064'), 2.8))
        p.drawLine(QPointF(wx(135), wy(53)), QPointF(wx(135), wy(-62)))
        p.setPen(QPen(QColor('#cfd1cc'), 2.4))
        for dx in (16, 132, 174):
            p.drawLine(QPointF(wx(dx), wy(-69)), QPointF(wx(dx), wy(-98)))

        # Baked contact AO discs around every 3D knob, BNC jack, and power button
        for ky in (42.0, -2.0, -46.0):
            paint_ao_disc(wx(-2.0), wy(ky), 58.0, alpha=82)
        for kx in (48.0, 104.0, 170.0):
            paint_ao_disc(wx(kx), wy(-8.0), 94.0, alpha=95)
        for bx in (46.0, 102.0, 152.0, 192.0):
            paint_ao_disc(wx(bx), wy(-84.0), 54.0, alpha=105)

        # Natural, irregular finger-oil smudges strictly near frequently pressed buttons (non-periodic)
        def paint_finger_smudge(cx, cy, rx, ry, alpha):
            p.save()
            p.translate(cx, cy)
            p.scale(1.0, ry / max(1.0, rx))
            wg = QRadialGradient(QPointF(0.0, 0.0), rx)
            wg.setColorAt(0.0, QColor(54, 48, 36, alpha))
            wg.setColorAt(0.55, QColor(68, 60, 44, alpha // 2))
            wg.setColorAt(1.0, QColor(68, 60, 44, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(wg)
            p.drawEllipse(QPointF(0.0, 0.0), rx, rx)
            p.restore()

        for bx, by, dx, dy, rx, ry, al in (
            (68.0, 76.0, 9.0, 16.0, 52.0, 38.0, 38),
            (34.0, 76.0, -6.0, 14.0, 44.0, 32.0, 26),
            (94.0, 76.0, 11.0, 12.0, 38.0, 28.0, 22),
            (48.0, -52.0, 12.0, 10.0, 48.0, 34.0, 32),
            (48.0, 36.0, 7.0, 11.0, 40.0, 30.0, 24),
            (192.0, -84.0, -10.0, 8.0, 56.0, 42.0, 36),
        ):
            paint_finger_smudge(wx(bx) + dx, wy(by) + dy, rx, ry, al)

        # Probe-plugging hairline scratches around BNC jacks
        rng_wear = np.random.default_rng(1988)
        for bx in (46.0, 102.0, 152.0):
            bcx, bcy = wx(bx), wy(-84.0)
            for _ in range(7):
                ang = float(rng_wear.uniform(0.0, 2.0 * math.pi))
                r_start = float(rng_wear.uniform(32.0, 50.0))
                r_len = float(rng_wear.uniform(8.0, 22.0))
                p.setPen(QPen(QColor(235, 232, 224, 50), float(rng_wear.uniform(0.8, 1.5))))
                p.drawLine(QPointF(bcx + math.cos(ang) * r_start, bcy + math.sin(ang) * r_start),
                           QPointF(bcx + math.cos(ang + 0.16) * (r_start + r_len),
                                   bcy + math.sin(ang + 0.16) * (r_start + r_len)))

        # A few sparse, non-periodic physical dings & gouges on the metal faceplate
        for gx, gy, gdx, gdy, w_px in (
            (wx(-22.0), wy(88.0), 26.0, 9.0, 2.6),
            (wx(134.0), wy(-58.0), -18.0, 12.0, 2.2),
            (wx(198.0), wy(46.0), -14.0, -7.0, 2.4),
            (wx(18.0), wy(-74.0), 22.0, -5.0, 2.0),
        ):
            p.setPen(QPen(QColor(34, 32, 28, 150), w_px, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(gx, gy), QPointF(gx + gdx, gy + gdy))
            p.setPen(QPen(QColor(246, 245, 240, 130), w_px * 0.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(gx + 1.5, gy + 2.2), QPointF(gx + gdx + 1.5, gy + gdy + 2.2))

    # Top-left cobalt-blue C1-170 model branding with LIVE current sample rate
    brand_font = QFont('JetBrains Mono')
    brand_font.setPixelSize(28)
    brand_font.setBold(True)
    brand_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
    p.setFont(brand_font)
    p.setPen(blue)
    p.drawText(QRectF(wx(-198), wy(95), wx(-20) - wx(-198), 42),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
               f'PHOSPHOR C1-170  OSCILLOSCOPE {sample_rate_label}')

    # Section header titles on dark charcoal bars
    hdr_font = QFont('JetBrains Mono')
    hdr_font.setPixelSize(17)
    hdr_font.setBold(True)
    p.setFont(hdr_font)
    p.setPen(QColor('#5eff88') if emissive_only else QColor('#f0f1ed'))
    p.drawText(QRectF(wx(14), wy(53), wx(132) - wx(14), 22), Qt.AlignmentFlag.AlignCenter, 'X / Y GAIN')
    p.drawText(QRectF(wx(138), wy(53), wx(202) - wx(138), 22), Qt.AlignmentFlag.AlignCenter, 'TIMEBASE')

    # Narrow vertical CRT-trim column labels (x = -2: INTENS, GAIN, OUTPUT)
    sm_font = QFont('JetBrains Mono')
    sm_font.setPixelSize(16)
    sm_font.setBold(True)
    p.setFont(sm_font)
    p.setPen(ink)
    for ky, lbl in ((42.0, 'INTENS'), (-2.0, 'GAIN'), (-46.0, 'OUTPUT')):
        cx, cy = wx(-2.0), wy(ky)
        p.drawText(QRectF(cx - 64, cy - 82, 128, 22), Qt.AlignmentFlag.AlignCenter, lbl)
        for step in range(9):
            rad = math.radians(-135.0 + step * 33.75 - 90.0)
            p.setPen(QPen(ink if step in (0, 4, 8) else rule, 2.5))
            p.drawLine(QPointF(cx + math.cos(rad) * 44, cy + math.sin(rad) * 44),
                       QPointF(cx + math.cos(rad) * 54, cy + math.sin(rad) * 54))
        p.setPen(ink)

    # Top-right pill switch labels (LIGHT, PLAY, BEAM, GRID) + TEST JACKS
    for bx, lbl_top, lbl_bot in ((34.0, 'LIGHT', 'MODE'), (68.0, 'PLAY', 'STOP'),
                                 (94.0, 'BEAM', 'COLOR'), (120.0, 'GRID', 'ON/OFF')):
        cx, cy = wx(bx), wy(76.0)
        p.drawText(QRectF(cx - 46, cy - 68, 92, 20), Qt.AlignmentFlag.AlignCenter, lbl_top)
        p.drawText(QRectF(cx - 46, cy + 28, 92, 20), Qt.AlignmentFlag.AlignCenter, lbl_bot)
    p.drawText(QRectF(wx(140), wy(73), 120, 20), Qt.AlignmentFlag.AlignCenter, 'TEST')

    # Upper-row pill switches (OBJ, PAL)
    for bx, lbl in ((48.0, 'OBJ'), (104.0, 'PAL')):
        cx, cy = wx(bx), wy(36.0)
        p.drawText(QRectF(cx - 44, cy - 42, 88, 20), Qt.AlignmentFlag.AlignCenter, lbl)

    # 3 Large dual-concentric selector knobs: CH1 X GAIN, CH2 Y GAIN, TIME WINDOW
    num_font = QFont('JetBrains Mono')
    num_font.setPixelSize(13)
    num_font.setBold(True)
    knob_defs = (
        (48.0, -8.0, 'CH1 X · GAIN', ('0×', '4×', '32×')),
        (104.0, -8.0, 'CH2 Y · GAIN', ('0×', '4×', '32×')),
        (170.0, -8.0, 'TIME · WIN', ('0.1', '14m', '2s')),
    )
    for kx, ky, label, ticks in knob_defs:
        cx, cy = wx(kx), wy(ky)
        p.setFont(sm_font)
        p.setPen(ink)
        p.drawText(QRectF(cx - 92, cy - 142, 184, 22), Qt.AlignmentFlag.AlignCenter, label)
        for step in range(13):
            ang_deg = -135.0 + step * 22.5
            rad = math.radians(ang_deg - 90.0)
            major = (step % 3 == 0)
            p.setPen(QPen(ink if major else rule, 3.6 if major else 2.0))
            r0, r1 = 78.0, 96.0 if major else 90.0
            p.drawLine(QPointF(cx + math.cos(rad) * r0, cy + math.sin(rad) * r0),
                       QPointF(cx + math.cos(rad) * r1, cy + math.sin(rad) * r1))
        p.setFont(num_font)
        p.setPen(rule)
        for ang_deg, txt in zip((-135.0, 0.0, 135.0), ticks):
            rad = math.radians(ang_deg - 90.0)
            tx, ty = cx + math.cos(rad) * 112.0, cy + math.sin(rad) * 112.0
            p.drawText(QRectF(tx - 32, ty - 10, 64, 20), Qt.AlignmentFlag.AlignCenter, txt)

    # Bottom pill switch directly under CH1 X GAIN: X-Y / TIME mode toggle
    p.setFont(sm_font)
    p.setPen(ink)
    vx, vy = wx(48.0), wy(-52.0)
    p.drawText(QRectF(vx - 62, vy + 24, 124, 20), Qt.AlignmentFlag.AlignCenter, 'X–Y / TIME')

    # Bottom dark charcoal BNC strip labels
    bot_ink = QColor('#4ef278') if emissive_only else QColor('#ebede8')
    p.setFont(sm_font)
    p.setPen(bot_ink)
    for bx, lbl in ((46.0, 'CH1  X'), (102.0, 'CH2  Y'), (152.0, 'SYNC'), (192.0, 'PWR')):
        p.drawText(QRectF(wx(bx) - 54, wy(-70), 108, 22), Qt.AlignmentFlag.AlignCenter, lbl)
    p.setFont(num_font)
    p.drawText(QRectF(wx(58), wy(-88), 140, 36), Qt.AlignmentFlag.AlignCenter, '1MΩ 25pF\n<300Vpk')

    p.end()
    return _qimage_to_rgba(img)[::-1].copy()


class BezelSmudgeTexture(QQuick3DTextureData):
    """Non-periodic transparent organic smudge decal placed on unique bezel spots."""
    def __init__(self, parent=None):
        super().__init__(parent)
        S = 256
        img = QImage(S, S, QImage.Format.Format_RGBA8888)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        for cx, cy, rx, ry, al in (
            (126, 130, 82, 54, 44),
            (148, 112, 58, 42, 32),
            (98, 144, 48, 36, 28),
        ):
            p.save()
            p.translate(cx, cy)
            p.scale(1.0, ry / rx)
            g = QRadialGradient(QPointF(0, 0), rx)
            g.setColorAt(0.0, QColor(58, 50, 36, al))
            g.setColorAt(0.6, QColor(74, 64, 46, al // 2))
            g.setColorAt(1.0, QColor(74, 64, 46, 0))
            p.setBrush(g)
            p.drawEllipse(QPointF(0, 0), rx, rx)
            p.restore()
        p.end()
        _upload_rgba(self, _qimage_to_rgba(img)[::-1].copy(), has_alpha=True)


class BezelGougeTexture(QQuick3DTextureData):
    """Non-periodic transparent plastic ding / gouge decal placed sparsely on bezel edges."""
    def __init__(self, parent=None):
        super().__init__(parent)
        S = 256
        img = QImage(S, S, QImage.Format.Format_RGBA8888)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor(52, 46, 36, 155), 11.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(76, 138), QPointF(178, 114))
        p.setPen(QPen(QColor(238, 232, 216, 145), 6.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(80, 146), QPointF(182, 122))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(58, 52, 40, 135))
        p.drawEllipse(QPointF(192, 108), 5.0, 3.5)
        p.end()
        _upload_rgba(self, _qimage_to_rgba(img)[::-1].copy(), has_alpha=True)


class ControlFaceplateTexture(QQuick3DTextureData):
    def __init__(self, tier='2k', sample_rate_label='48kHz', parent=None):
        super().__init__(parent)
        self._key = None
        self.set_params(tier=tier, sample_rate_label=sample_rate_label)

    def set_tier(self, tier):
        _, rate = self._key if self._key else ('2k', '48kHz')
        self.set_params(tier=tier, sample_rate_label=rate)

    def set_params(self, tier='2k', sample_rate_label='48kHz'):
        safe = tier if tier in TEXTURE_TIERS else '2k'
        key = (safe, str(sample_rate_label))
        if key != self._key:
            self._key = key
            _upload_rgba(self, _paint_faceplate_image(emissive_only=False, tier=safe, sample_rate_label=key[1]))


class ControlEmissiveTexture(QQuick3DTextureData):
    def __init__(self, tier='2k', sample_rate_label='48kHz', parent=None):
        super().__init__(parent)
        self._key = None
        self.set_params(tier=tier, sample_rate_label=sample_rate_label)

    def set_tier(self, tier):
        _, rate = self._key if self._key else ('2k', '48kHz')
        self.set_params(tier=tier, sample_rate_label=rate)

    def set_params(self, tier='2k', sample_rate_label='48kHz'):
        safe = tier if tier in TEXTURE_TIERS else '2k'
        key = (safe, str(sample_rate_label))
        if key != self._key:
            self._key = key
            _upload_rgba(self, _paint_faceplate_image(emissive_only=True, tier=safe, sample_rate_label=key[1]))
