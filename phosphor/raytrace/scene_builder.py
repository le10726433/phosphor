"""Exports Phosphor's exact late-80s C1-170 3D Lab Bench scene into GPU BVH + PBR textures
for the cross-platform wgpu compute path tracer (macOS Metal & Windows DX12/Vulkan).
"""
import math
from pathlib import Path
import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QMatrix4x4, QQuaternion, QVector3D

from phosphor.editor.desk3d import CH1_ANCHOR_WORLD, CH1_SOCKET_WORLD, CH2_ANCHOR_WORLD, CH2_SOCKET_WORLD
from phosphor.editor.desk3d_assets import (
    TEXTURE_ROOT, _paint_faceplate_image, _qimage_to_rgba, build_bail_handle_mesh,
    build_bnc_jack_mesh, build_bnc_plug_collar_mesh, build_crt_hood_mesh,
    build_curved_crt_mesh, build_faceplate_screw_mesh, build_keycap_mesh,
    build_knurled_knob_mesh, build_rounded_bezel_frame_mesh, build_strain_boot_mesh,
)
from phosphor.editor.desk3d_props import (
    BobbleheadFaceTexture, CalStickerTexture, CuttingMatTexture, LabWallChartTexture,
    VerletCoaxCable, _catmull_rom_subdivide, _render_notepad_page, build_coffee_mug_mesh,
    build_helical_spring_mesh, build_hex_pencil_barrel_mesh, build_hollow_lamp_shade_mesh,
    build_lamp_power_cord_mesh, build_lamp_shade_inner_mesh, build_mug_handle_mesh,
    build_spare_probe_cord_mesh, build_tube_along_polyline,
)
from phosphor.raytrace.bvh import build_bvh

# Material bitflags matching pathtrace.wgsl
MAT_FLAG_CRT_SCREEN = 1
MAT_FLAG_CRT_GLASS = 2
MAT_FLAG_LAMP_BULB = 4
MAT_FLAG_FLUOR_PANEL = 8
MAT_FLAG_LAMP_LINER = 16
MAT_FLAG_ALPHA_CUTOUT = 32
MAT_FLAG_UV_CLAMP = 64
MAT_FLAG_LED_GREEN = 128
MAT_FLAG_LED_AMBER = 256

TEX_SIZE = 1024  # Uniform 2D texture array layer size (1024x1024 RGBA8)


def _hex_to_linear(hex_str):
    s = hex_str.lstrip('#')
    r = int(s[0:2], 16) / 255.0
    g = int(s[2:4], 16) / 255.0
    b = int(s[4:6], 16) / 255.0
    return (r ** 2.2, g ** 2.2, b ** 2.2)


def _mat4(pos=(0.0, 0.0, 0.0), rot=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)):
    """Build a 4x4 transform matrix matching QtQuick3D Node (translate * eulerXYZ * scale)."""
    m = QMatrix4x4()
    m.translate(float(pos[0]), float(pos[1]), float(pos[2]))
    if rot != (0.0, 0.0, 0.0):
        m.rotate(QQuaternion.fromEulerAngles(float(rot[0]), float(rot[1]), float(rot[2])))
    if scale != (1.0, 1.0, 1.0):
        m.scale(float(scale[0]), float(scale[1]), float(scale[2]))
    # Qt QMatrix4x4.data() is column-major; transpose into row-major numpy 4x4
    arr = np.array(m.data(), dtype=np.float32).reshape((4, 4)).T
    return arr


# --- Standard QtQuick3D-matching primitives (#Cube, #Cylinder, #Sphere, #Cone, #Rectangle) ---

def _prim_box(sx=100.0, sy=100.0, sz=100.0):
    hx, hy, hz = sx * 0.5, sy * 0.5, sz * 0.5
    faces = [
        ((0, 0, 1),  [(-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]),
        ((0, 0, -1), [(hx, -hy, -hz), (-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz)]),
        ((1, 0, 0),  [(hx, -hy, hz), (hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz)]),
        ((-1, 0, 0), [(-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz)]),
        ((0, 1, 0),  [(-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz), (-hx, hy, -hz)]),
        ((0, -1, 0), [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz)]),
    ]
    pos, nrm, uv, idx = [], [], [], []
    base = 0
    for n, pts in faces:
        for p, t in zip(pts, ((0, 0), (1, 0), (1, 1), (0, 1))):
            pos.append(p); nrm.append(n); uv.append(t)
        idx.extend([base, base + 1, base + 2, base, base + 2, base + 3])
        base += 4
    return (np.array(pos, np.float32), np.array(nrm, np.float32),
            np.array(uv, np.float32), np.array(idx, np.uint32))


def _prim_rect(w=100.0, h=100.0):
    hw, hh = w * 0.5, h * 0.5
    pos = np.array([[-hw, -hh, 0], [hw, -hh, 0], [hw, hh, 0], [-hw, hh, 0]], np.float32)
    nrm = np.array([[0, 0, 1]] * 4, np.float32)
    uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)
    idx = np.array([0, 1, 2, 0, 2, 3], np.uint32)
    return pos, nrm, uv, idx


def _prim_cylinder(radius=50.0, height=100.0, segs=24):
    hh = height * 0.5
    ang = np.linspace(0.0, 2.0 * math.pi, segs + 1, dtype=np.float32)
    ca, sa = np.cos(ang), np.sin(ang)
    pos, nrm, uv, idx = [], [], [], []
    base = 0
    # Side wall
    for i in range(segs):
        c0, s0, c1, s1 = ca[i], sa[i], ca[i + 1], sa[i + 1]
        u0, u1 = i / segs, (i + 1) / segs
        pts = [
            ((radius * c0, -hh, radius * s0), (c0, 0, s0), (u0, 0)),
            ((radius * c1, -hh, radius * s1), (c1, 0, s1), (u1, 0)),
            ((radius * c1, hh, radius * s1),  (c1, 0, s1), (u1, 1)),
            ((radius * c0, hh, radius * s0),  (c0, 0, s0), (u0, 1)),
        ]
        for p, n, t in pts:
            pos.append(p); nrm.append(n); uv.append(t)
        idx.extend([base, base + 2, base + 1, base, base + 3, base + 2])
        base += 4
    # Top & bottom caps
    for sign, y in ((1.0, hh), (-1.0, -hh)):
        c_idx = base
        pos.append((0, y, 0)); nrm.append((0, sign, 0)); uv.append((0.5, 0.5))
        base += 1
        for i in range(segs):
            pos.append((radius * ca[i], y, radius * sa[i]))
            nrm.append((0, sign, 0))
            uv.append((0.5 + 0.5 * ca[i], 0.5 + 0.5 * sa[i]))
        for i in range(segs):
            a = c_idx + 1 + i
            b = c_idx + 1 + ((i + 1) % segs)
            if sign > 0:
                idx.extend([c_idx, b, a])
            else:
                idx.extend([c_idx, a, b])
        base += segs
    return (np.array(pos, np.float32), np.array(nrm, np.float32),
            np.array(uv, np.float32), np.array(idx, np.uint32))


def _prim_sphere(radius=50.0, stacks=16, slices=24):
    pos, nrm, uv, idx = [], [], [], []
    for i in range(stacks + 1):
        v = i / stacks
        phi = v * math.pi  # 0=top (+Y), pi=bottom (-Y)
        sp, cp = math.sin(phi), math.cos(phi)
        for j in range(slices + 1):
            u = j / slices
            # Align u=0.5 with +Z front face (matching QtQuick3D #Sphere UV wrap for BobbleheadFaceTexture!)
            theta = (u - 0.25) * 2.0 * math.pi
            nx = sp * math.cos(theta)
            ny = cp
            nz = sp * math.sin(theta)
            pos.append((radius * nx, radius * ny, radius * nz))
            nrm.append((nx, ny, nz))
            uv.append((u, 1.0 - v))
    stride = slices + 1
    for i in range(stacks):
        for j in range(slices):
            v0 = i * stride + j
            v1 = v0 + 1
            v2 = v0 + stride
            v3 = v2 + 1
            idx.extend([v0, v1, v3, v0, v3, v2])
    return (np.array(pos, np.float32), np.array(nrm, np.float32),
            np.array(uv, np.float32), np.array(idx, np.uint32))


def _prim_cone(radius=50.0, height=100.0, segs=20):
    hh = height * 0.5
    ang = np.linspace(0.0, 2.0 * math.pi, segs + 1, dtype=np.float32)
    ca, sa = np.cos(ang), np.sin(ang)
    slope = math.hypot(radius, height)
    nr, ny = height / slope, radius / slope
    pos, nrm, uv, idx = [], [], [], []
    base = 0
    for i in range(segs):
        c0, s0, c1, s1 = ca[i], sa[i], ca[i + 1], sa[i + 1]
        cm, sm = math.cos(0.5 * (ang[i] + ang[i + 1])), math.sin(0.5 * (ang[i] + ang[i + 1]))
        pos.extend([(0, hh, 0), (radius * c1, -hh, radius * s1), (radius * c0, -hh, radius * s0)])
        nrm.extend([(nr * cm, ny, nr * sm), (nr * c1, ny, nr * s1), (nr * c0, ny, nr * s0)])
        uv.extend([(0.5, 1.0), ((i + 1) / segs, 0.0), (i / segs, 0.0)])
        idx.extend([base, base + 1, base + 2])
        base += 3
    return (np.array(pos, np.float32), np.array(nrm, np.float32),
            np.array(uv, np.float32), np.array(idx, np.uint32))


# --- Scene Collector ---

class _SceneCollector:
    def __init__(self):
        self.v0, self.v1, self.v2 = [], [], []
        self.n0, self.n1, self.n2 = [], [], []
        self.uv0, self.uv1, self.uv2 = [], [], []
        self.mats = []
        self.mat_table = []  # List of 16-float/u32 rows (64 bytes each)

    def add_material(self, color='#ffffff', roughness=0.5, metalness=0.0,
                     emissive=(0.0, 0.0, 0.0), clearcoat=0.0, clearcoat_rough=0.15,
                     ior=1.5, tex_id=0, uv_scale=(1.0, 1.0), flags=0, normal_tex_id=0):
        mid = len(self.mat_table)
        rgb = _hex_to_linear(color) if isinstance(color, str) else tuple(float(c) for c in color)
        row = np.zeros(16, dtype=np.uint32)
        f = row.view(np.float32)
        f[0:3] = rgb
        f[3] = float(roughness)
        f[4:7] = emissive
        f[7] = float(metalness)
        f[8] = float(clearcoat)
        f[9] = float(clearcoat_rough)
        f[10] = float(ior)
        row[11] = np.uint32(tex_id)
        f[12] = float(uv_scale[0])
        f[13] = float(uv_scale[1])
        row[14] = np.uint32(flags)
        row[15] = np.uint32(normal_tex_id)
        self.mat_table.append(row)
        return mid

    def add_mesh(self, mesh_tuple, mat_id, xform):
        pos, nrm, uv, idx = mesh_tuple
        pos = np.asarray(pos, dtype=np.float32)
        nrm = np.asarray(nrm, dtype=np.float32)
        uv = np.asarray(uv, dtype=np.float32)
        idx = np.asarray(idx, dtype=np.uint32).reshape(-1, 3)

        # Transform positions by 4x4 matrix
        m3 = xform[:3, :3]
        t3 = xform[:3, 3]
        wpos = pos @ m3.T + t3[None, :]
        # Transform normals by inverse-transpose of 3x3
        try:
            nm = np.linalg.inv(m3).T
        except np.linalg.LinAlgError:
            nm = m3
        wnrm = nrm @ nm.T
        nlen = np.linalg.norm(wnrm, axis=1, keepdims=True)
        wnrm = np.where(nlen > 1e-6, wnrm / np.maximum(nlen, 1e-6), np.array([0, 1, 0], np.float32))

        i0, i1, i2 = idx[:, 0], idx[:, 1], idx[:, 2]
        # Skip degenerate zero-area triangles
        p0, p1, p2 = wpos[i0], wpos[i1], wpos[i2]
        area2 = np.linalg.norm(np.cross(p1 - p0, p2 - p0), axis=1)
        valid = area2 > 1e-5
        if not np.any(valid):
            return
        i0, i1, i2 = i0[valid], i1[valid], i2[valid]
        self.v0.append(wpos[i0]); self.v1.append(wpos[i1]); self.v2.append(wpos[i2])
        self.n0.append(wnrm[i0]); self.n1.append(wnrm[i1]); self.n2.append(wnrm[i2])
        self.uv0.append(uv[i0]); self.uv1.append(uv[i1]); self.uv2.append(uv[i2])
        self.mats.append(np.full(len(i0), mat_id, dtype=np.uint32))


def _load_jpg_layer(path, size=TEX_SIZE, flip_y=True):
    im = Image.open(path).convert('RGBA').resize((size, size), Image.Resampling.LANCZOS)
    arr = np.asarray(im, dtype=np.uint8)
    if flip_y:
        arr = arr[::-1].copy()
    return arr


def _qtex_to_layer(rgba_arr, size=TEX_SIZE):
    im = Image.fromarray(rgba_arr, mode='RGBA').resize((size, size), Image.Resampling.LANCZOS)
    return np.asarray(im, dtype=np.uint8)


def _paint_lcd_layer(text, size=TEX_SIZE):
    """Paint a crisp vintage twisted-nematic LCD readout panel texture (e.g. '1.00×' or 'X–Y')."""
    from PySide6.QtGui import QColor, QFont, QPainter
    from PySide6.QtCore import QRectF
    W, H = 320, 96
    img = QImage(W, H, QImage.Format.Format_RGBA8888)
    img.fill(QColor('#8ba37c'))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setPen(QColor('#1b241c'))
    p.drawRect(QRectF(2, 2, W - 4, H - 4))
    f = QFont('JetBrains Mono')
    f.setPixelSize(46)
    f.setBold(True)
    p.setFont(f)
    # Subtle TN LCD digit drop-shadow
    p.setPen(QColor(18, 28, 18, 52))
    p.drawText(QRectF(4, 4, W, H), Qt.AlignmentFlag.AlignCenter, text)
    p.setPen(QColor('#142014'))
    p.drawText(QRectF(0, 0, W, H), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return _qtex_to_layer(_qimage_to_rgba(img)[::-1].copy(), size=size)


def _load_texture_array(sample_rate_label='48kHz'):
    """Pack all scene PBR albedo & normal textures into a single (L, 1024, 1024, 4) uint8 array."""
    tdir = TEXTURE_ROOT / '2k'
    layers = [
        _load_jpg_layer(tdir / 'chassis_albedo.jpg'),        # 1: chassis ABS
        _load_jpg_layer(tdir / 'desk_albedo.jpg'),           # 2: oak desk
        _load_jpg_layer(tdir / 'wall_albedo.jpg'),           # 3: plaster wall
        _load_jpg_layer(tdir / 'aluminum_albedo.jpg'),       # 4: brushed aluminum
        _qtex_to_layer(_paint_faceplate_image(False, '2k', sample_rate_label)),  # 5: faceplate (already flipped)
        _qtex_to_layer(np.frombuffer(CuttingMatTexture().textureData(), np.uint8).reshape(512, 1024, 4)),  # 6: cutting mat
        _qtex_to_layer(np.frombuffer(CalStickerTexture().textureData(), np.uint8).reshape(256, 512, 4)),   # 7: CAL sticker
        _qtex_to_layer(_render_notepad_page(0)),             # 8: Voynich notepad p.1
        _qtex_to_layer(np.frombuffer(BobbleheadFaceTexture().textureData(), np.uint8).reshape(512, 512, 4)),  # 9: bobblehead
        _qtex_to_layer(np.frombuffer(LabWallChartTexture().textureData(), np.uint8).reshape(384, 512, 4)),    # 10: wall chart
        _load_jpg_layer(tdir / 'desk_normal.jpg'),           # 11: desk normal
        _load_jpg_layer(tdir / 'chassis_normal.jpg'),        # 12: chassis normal
        _load_jpg_layer(tdir / 'wall_normal.jpg'),           # 13: wall normal
        _paint_lcd_layer('1.00×'),                           # 14: LCD gain readout
        _paint_lcd_layer('X–Y'),                             # 15: LCD timebase readout
    ]
    return np.ascontiguousarray(np.stack(layers, axis=0), dtype=np.uint8)


def build_lab_bench_scene(sample_rate_label='48kHz'):
    """Construct the complete C1-170 late-80s lab bench BVH, materials, textures, and area light metadata."""
    sc = _SceneCollector()

    # Reusable primitives (lean tessellation for fast GPU BVH traversal with smooth normals)
    box = _prim_box()
    rect = _prim_rect()
    cyl = _prim_cylinder(segs=16)
    sph = _prim_sphere(stacks=10, slices=16)
    cone = _prim_cone(segs=14)

    # Procedural hero & prop meshes (exact same C1-170 silhouettes, tuned for fast BVH traversal)
    crt_mesh = build_curved_crt_mesh(width=148.0, height=138.0, bulge=4.5, segs=16)
    bezel_mesh = build_rounded_bezel_frame_mesh()
    hood_mesh = build_crt_hood_mesh()
    knob_mesh = build_knurled_knob_mesh(radius=14.5, height=19.5, flutes=22, dual=True)
    trim_knob_mesh = build_knurled_knob_mesh(radius=8.5, height=13.0, flutes=18, dual=False)
    keycap_mesh = build_keycap_mesh()
    bnc_mesh = build_bnc_jack_mesh()
    bnc_plug_mesh = build_bnc_plug_collar_mesh(knurls=18)
    strain_boot_mesh = build_strain_boot_mesh(segs=18)
    bail_mesh = build_bail_handle_mesh(sides=14)
    screw_mesh = build_faceplate_screw_mesh(segs=12)
    lamp_shade_mesh = build_hollow_lamp_shade_mesh(segs=28)
    lamp_inner_mesh = build_lamp_shade_inner_mesh(segs=24)
    lamp_spring_mesh = build_helical_spring_mesh(coil_radius=3.2, wire_radius=0.72, height=102.0, turns=14.0, steps_per_turn=7, sides=5)
    bobble_spring_mesh = build_helical_spring_mesh(coil_radius=2.6, wire_radius=0.52, height=12.0, turns=5.0, steps_per_turn=7, sides=5)
    lamp_cord_mesh = build_lamp_power_cord_mesh(sides=6)
    mug_mesh = build_coffee_mug_mesh(segs=24)
    mug_handle_mesh = build_mug_handle_mesh(sides=8)
    pencil_mesh = build_hex_pencil_barrel_mesh()
    probe_cord_mesh = build_spare_probe_cord_mesh(sides=6)

    # --- PBR Materials ---
    m_chassis = sc.add_material('#ffffff', roughness=0.76, tex_id=1, uv_scale=(1.5, 1.5), normal_tex_id=12)
    m_handle = sc.add_material('#dcd3c2', roughness=0.74, tex_id=1, uv_scale=(1.5, 1.5), normal_tex_id=12)
    m_desk = sc.add_material('#ffffff', roughness=0.58, clearcoat=0.35, clearcoat_rough=0.22, tex_id=2, uv_scale=(2.2, 1.5), normal_tex_id=11)
    m_wall = sc.add_material('#c2bfb5', roughness=0.90, tex_id=3, uv_scale=(3.0, 1.8), normal_tex_id=13)
    m_faceplate = sc.add_material('#ffffff', roughness=0.36, metalness=0.58, tex_id=5, flags=MAT_FLAG_UV_CLAMP)
    m_cutting_mat = sc.add_material('#ffffff', roughness=0.72, clearcoat=0.10, clearcoat_rough=0.45, tex_id=6, flags=MAT_FLAG_UV_CLAMP)
    m_cal_sticker = sc.add_material('#ffffff', roughness=0.62, tex_id=7, flags=MAT_FLAG_ALPHA_CUTOUT | MAT_FLAG_UV_CLAMP)
    m_notepad = sc.add_material('#ffffff', roughness=0.88, tex_id=8, flags=MAT_FLAG_UV_CLAMP)
    m_bobble_face = sc.add_material('#ffffff', roughness=0.36, clearcoat=0.45, clearcoat_rough=0.20, tex_id=9, flags=MAT_FLAG_UV_CLAMP)
    m_wall_chart = sc.add_material('#ffffff', roughness=0.88, tex_id=10, flags=MAT_FLAG_UV_CLAMP)

    m_steel = sc.add_material('#e2e4df', roughness=0.22, metalness=0.92, tex_id=4)
    m_lamp_enamel = sc.add_material('#2e3733', roughness=0.26, metalness=0.65, clearcoat=0.65, clearcoat_rough=0.18)
    m_lamp_inner = sc.add_material('#fff2d6', roughness=0.48, emissive=(1.2, 0.72, 0.28), flags=MAT_FLAG_LAMP_LINER)
    m_lamp_bulb = sc.add_material('#fff5d8', roughness=0.15, emissive=(38.0, 21.0, 7.5), flags=MAT_FLAG_LAMP_BULB)
    m_fluor_panel = sc.add_material('#f5f9ff', roughness=0.20, emissive=(6.5, 7.0, 7.6), flags=MAT_FLAG_FLUOR_PANEL)
    m_brass = sc.add_material('#d1aa5e', roughness=0.26, metalness=0.92)
    m_rubber = sc.add_material('#1d1f22', roughness=0.82, metalness=0.0)
    m_apron = sc.add_material('#16181a', roughness=0.84, metalness=0.35)
    m_backsplash = sc.add_material('#2a241e', roughness=0.78)
    m_knob = sc.add_material('#d4cec0', roughness=0.48, clearcoat=0.25, clearcoat_rough=0.30)
    m_pointer = sc.add_material('#1a1c1f', roughness=0.42)
    m_grey_pill = sc.add_material('#ddd8cb', roughness=0.42, clearcoat=0.35, clearcoat_rough=0.24)
    m_green_pill = sc.add_material('#1ea458', roughness=0.34, clearcoat=0.48, clearcoat_rough=0.20)
    m_yellow_pill = sc.add_material('#ebb920', roughness=0.32, clearcoat=0.48, clearcoat_rough=0.20)
    m_red_mushroom = sc.add_material('#eb2020', roughness=0.16, clearcoat=0.92, clearcoat_rough=0.10, emissive=(0.18, 0.02, 0.01))
    m_red_boot = sc.add_material('#cb2a2a', roughness=0.42, clearcoat=0.30, clearcoat_rough=0.30)
    m_blue_boot = sc.add_material('#2566c7', roughness=0.42, clearcoat=0.30, clearcoat_rough=0.30)
    m_box_steel = sc.add_material('#2c3033', roughness=0.52, metalness=0.38)
    m_pstrip = sc.add_material('#4d5257', roughness=0.44, metalness=0.68)
    m_neon_sw = sc.add_material('#ee4d22', roughness=0.22, emissive=(3.2, 0.65, 0.14))
    m_led_green = sc.add_material('#3ef56e', roughness=0.14, emissive=(2.8, 9.5, 3.6), flags=MAT_FLAG_LED_GREEN)
    m_led_amber = sc.add_material('#ffbc32', roughness=0.16, emissive=(7.5, 4.4, 0.7), flags=MAT_FLAG_LED_AMBER)
    m_lcd_gain = sc.add_material('#ffffff', roughness=0.14, clearcoat=0.88, clearcoat_rough=0.06,
                                 emissive=(0.12, 0.24, 0.14), tex_id=14, flags=MAT_FLAG_UV_CLAMP)
    m_lcd_xy = sc.add_material('#ffffff', roughness=0.14, clearcoat=0.88, clearcoat_rough=0.06,
                               emissive=(0.12, 0.24, 0.14), tex_id=15, flags=MAT_FLAG_UV_CLAMP)
    m_mug_ceramic = sc.add_material('#eee4d2', roughness=0.26, clearcoat=0.82, clearcoat_rough=0.12)
    m_mug_stripe = sc.add_material('#21556a', roughness=0.24, clearcoat=0.82, clearcoat_rough=0.12)
    m_coffee = sc.add_material('#120904', roughness=0.04, clearcoat=1.0, clearcoat_rough=0.03)
    m_pencil_yellow = sc.add_material('#e8b620', roughness=0.30, clearcoat=0.48, clearcoat_rough=0.22)
    m_pencil_cedar = sc.add_material('#cfa374', roughness=0.84)
    m_pencil_eraser = sc.add_material('#d97a7e', roughness=0.80)
    m_cardboard = sc.add_material('#8c7a5b', roughness=0.92)
    m_hoodie = sc.add_material('#363c46', roughness=0.82)
    m_hair = sc.add_material('#3b2a1e', roughness=0.76)
    m_skin = sc.add_material('#efc8a8', roughness=0.48, clearcoat=0.25, clearcoat_rough=0.30)
    m_gold_pin = sc.add_material('#e8c455', roughness=0.20, metalness=0.94)
    m_ptfe = sc.add_material('#e6e2d8', roughness=0.42)
    m_crt_screen = sc.add_material('#0c1811', roughness=0.22, flags=MAT_FLAG_CRT_SCREEN | MAT_FLAG_UV_CLAMP)
    m_crt_glass = sc.add_material('#0b1610', roughness=0.04, ior=1.52, flags=MAT_FLAG_CRT_GLASS | MAT_FLAG_UV_CLAMP)

    # === 1. WORKBENCH, BULLNOSE, APRON, CUTTING MAT, BACK WALL, CONDUIT, POWER STRIP, WALL CHART ===
    sc.add_mesh(box, m_desk, _mat4((60, -138, -130), scale=(12.5, 0.16, 7.2)))
    sc.add_mesh(cyl, m_desk, _mat4((60, -138, 230), (0, 0, 90), (0.16, 12.5, 0.16)))
    sc.add_mesh(box, m_apron, _mat4((60, -256, 198), scale=(13.0, 2.2, 0.24)))
    sc.add_mesh(box, m_apron, _mat4((60, -370, 80), scale=(24.0, 0.2, 16.0)))
    sc.add_mesh(box, m_cutting_mat, _mat4((18, -129.4, -22), scale=(6.8, 0.012, 3.6)))
    sc.add_mesh(box, m_wall, _mat4((40, 20, -360), scale=(18.0, 10.0, 0.2)))
    sc.add_mesh(box, m_backsplash, _mat4((40, -118, -349), scale=(16.0, 0.28, 0.08)))
    sc.add_mesh(cyl, m_steel, _mat4((40, -48, -346), (0, 0, 90), (0.065, 16.0, 0.065)))
    for cx in (-320, -140, 160, 340):
        sc.add_mesh(box, m_steel, _mat4((cx, -48, -347), scale=(0.06, 0.14, 0.09)))

    # Overhead physical fluorescent shop softbox panels (real area lights in path tracer!)
    sc.add_mesh(rect, m_fluor_panel, _mat4((-140, 340, 90), (90, 0, 0), (2.4, 0.65, 1.0)))
    sc.add_mesh(rect, m_fluor_panel, _mat4((160, 340, 90), (90, 0, 0), (2.4, 0.65, 1.0)))

    # Power strip rear-right
    m_ps = _mat4((296, -124, -172), (0, -10, 0))
    sc.add_mesh(box, m_pstrip, m_ps @ _mat4(scale=(0.42, 0.12, 1.55)))
    for oz in (-50, -18, 14, 46):
        sc.add_mesh(box, m_rubber, m_ps @ _mat4((0, 6.2, oz), scale=(0.24, 0.015, 0.22)))
    sc.add_mesh(box, m_neon_sw, m_ps @ _mat4((0, 6.8, 66), (8, 0, 0), (0.14, 0.03, 0.10)))

    # Pinned Lissajous wall chart
    m_wc = _mat4((344, 150, -349), (0, 0, -1.4))
    sc.add_mesh(rect, m_wall_chart, m_wc @ _mat4(scale=(1.62, 1.22, 1.0)))
    for tx in (-68, 68):
        sc.add_mesh(cyl, m_brass, m_wc @ _mat4((tx, 53, 1.0), (90, 0, 0), (0.042, 0.018, 0.042)))

    # === 2. ARCHITECT DESK LAMP (HOLLOW SHADE + REAL GLOWING BULB INSIDE + HELICAL SPRING + CORD) ===
    sc.add_mesh(lamp_cord_mesh, m_rubber, _mat4())
    m_lamp = _mat4((-286, -130, 26))
    sc.add_mesh(cyl, m_lamp_enamel, m_lamp @ _mat4((0, 7, 0), scale=(0.76, 0.14, 0.76)))
    sc.add_mesh(cyl, m_lamp_enamel, m_lamp @ _mat4((0, 15, 0), scale=(0.42, 0.07, 0.42)))
    sc.add_mesh(box, m_rubber, m_lamp @ _mat4((20, 14, 24), scale=(0.16, 0.05, 0.18)))
    sc.add_mesh(cyl, m_yellow_pill, m_lamp @ _mat4((20, 17.5, 24), scale=(0.10, 0.08, 0.10)))
    m_lo = m_lamp @ _mat4((0, 18, 0), (-4, 18, 16))
    sc.add_mesh(cyl, m_lamp_enamel, m_lo @ _mat4((0, 96, -6), scale=(0.07, 1.94, 0.07)))
    sc.add_mesh(cyl, m_lamp_enamel, m_lo @ _mat4((0, 96, 6), scale=(0.07, 1.94, 0.07)))
    sc.add_mesh(lamp_spring_mesh, m_brass, m_lo @ _mat4((-6.5, 86, 0)))
    m_el = m_lo @ _mat4((0, 192, 0))
    sc.add_mesh(cyl, m_brass, m_el @ _mat4(rot=(90, 0, 0), scale=(0.16, 0.22, 0.16)))
    sc.add_mesh(box, m_brass, m_el @ _mat4((0, 0, 13), (0, 0, 28), (0.18, 0.045, 0.03)))
    m_up = m_el @ _mat4(rot=(42, -12, -62))
    sc.add_mesh(cyl, m_lamp_enamel, m_up @ _mat4((0, 68, 0), scale=(0.08, 1.38, 0.08)))
    m_wr = m_up @ _mat4((0, 136, 0), (-36, 0, 56))
    sc.add_mesh(lamp_shade_mesh, m_lamp_enamel, m_wr)
    sc.add_mesh(lamp_inner_mesh, m_lamp_inner, m_wr)
    # Real physical glowing tungsten bulb inside the hollow shade + open mouth aperture for low-variance NEE!
    m_bulb = m_wr @ _mat4((0, -18, 0), scale=(0.24, 0.28, 0.24))
    sc.add_mesh(sph, m_lamp_bulb, m_bulb)
    bulb_world = (m_wr @ np.array([0.0, -33.0, 0.0, 1.0], np.float32))[:3]
    lamp_dir = (m_wr[:3, :3] @ np.array([0.0, -1.0, 0.0], np.float32))
    lamp_dir /= (float(np.linalg.norm(lamp_dir)) or 1.0)

    # === 3. BIG RED INDUSTRIAL PUSHBUTTON BOX ===
    m_rb = _mat4((-215, -130, 96), (0, 18, 0))
    sc.add_mesh(box, m_box_steel, m_rb @ _mat4((0, 11, 0), scale=(0.46, 0.22, 0.46)))
    for sx in (-17.5, 17.5):
        for sz in (-17.5, 17.5):
            sc.add_mesh(screw_mesh, m_steel, m_rb @ _mat4((sx, 22.1, sz), (-90, 0, 0), (0.72, 0.72, 0.72)))
    sc.add_mesh(cyl, m_yellow_pill, m_rb @ _mat4((0, 22.5, 0), scale=(0.37, 0.04, 0.37)))
    sc.add_mesh(cyl, m_red_mushroom, m_rb @ _mat4((0, 32.0, 0), scale=(0.30, 0.12, 0.30)))
    sc.add_mesh(sph, m_red_mushroom, m_rb @ _mat4((0, 37.5, 0), scale=(0.32, 0.11, 0.32)))

    # === 4. COFFEE MUG, NOTEPAD, YELLOW NO. 2 PENCIL, SPARE COIL PROBE ===
    m_mug = _mat4((268, -130, 86), (0, -48, 0))
    sc.add_mesh(mug_mesh, m_mug_ceramic, m_mug)
    sc.add_mesh(mug_handle_mesh, m_mug_ceramic, m_mug)
    sc.add_mesh(cyl, m_mug_stripe, m_mug @ _mat4((0, 39.5, 0), scale=(0.412, 0.032, 0.412)))
    sc.add_mesh(cyl, m_coffee, m_mug @ _mat4((0, 36.2, 0), scale=(0.345, 0.01, 0.345)))

    m_pad = _mat4((182, -126.2, 118), (-90, -18, 0), (0.78, 0.78, 0.78))
    sc.add_mesh(box, m_cardboard, m_pad @ _mat4((0, 0, -2.2), scale=(0.98, 1.22, 0.04)))
    sc.add_mesh(rect, m_notepad, m_pad @ _mat4((0, 0, 0.2), scale=(0.96, 1.20, 1.0)))

    m_pen = _mat4((104, -126.8, 128), (0, 26, 0))
    sc.add_mesh(pencil_mesh, m_pencil_yellow, m_pen)
    sc.add_mesh(cyl, m_brass, m_pen @ _mat4((-39.5, 0, 0), (0, 0, 90), (0.044, 0.065, 0.044)))
    sc.add_mesh(cyl, m_pencil_eraser, m_pen @ _mat4((-44.0, 0, 0), (0, 0, 90), (0.041, 0.055, 0.041)))
    sc.add_mesh(cone, m_pencil_cedar, m_pen @ _mat4((41.5, 0, 0), (0, 0, -90), (0.042, 0.095, 0.042)))
    sc.add_mesh(cone, m_pointer, m_pen @ _mat4((45.6, 0, 0), (0, 0, -90), (0.014, 0.032, 0.014)))

    m_prb = _mat4((280, -129.2, -44), (0, 18, 0))
    sc.add_mesh(probe_cord_mesh, m_rubber, m_prb)
    m_wand = m_prb @ _mat4((-12, 3.6, 46), (0, -62, 0))
    sc.add_mesh(cyl, m_rubber, m_wand @ _mat4(rot=(0, 0, 90), scale=(0.065, 0.36, 0.065)))
    sc.add_mesh(cyl, m_yellow_pill, m_wand @ _mat4((19, 0, 0), (0, 0, 90), (0.072, 0.06, 0.072)))
    sc.add_mesh(cone, m_steel, m_wand @ _mat4((27, 0, 0), (0, 0, -90), (0.022, 0.14, 0.022)))

    # === 5. VERLET COAXIAL CABLES (SPLINE-DRAPED OVER OAK BULLNOSE) + MACHINED BNC PLUGS ===
    sockets = (CH1_SOCKET_WORLD, CH2_SOCKET_WORLD)
    for idx, (port, anc, boot_mat) in enumerate(((0, CH1_ANCHOR_WORLD, m_red_boot),
                                                 (1, CH2_ANCHOR_WORLD, m_blue_boot))):
        cable = VerletCoaxCable(sockets, port, anc)
        pts = cable.pos
        a = pts[-1]
        tail = np.array([
            [a[0] + (a[0] - pts[-2][0]) * 0.45, -127.6, 228.0],
            [a[0] + (a[0] - pts[-2][0]) * 0.70, -133.5, 237.5],
            [a[0] + (a[0] - pts[-2][0]) * 0.85, -152.0, 242.0],
            [a[0] + (a[0] - pts[-2][0]) * 0.90, -184.0, 243.0],
        ], dtype=np.float32)
        c_pts = _catmull_rom_subdivide(np.vstack([pts, tail]), substeps=2)
        sc.add_mesh(build_tube_along_polyline(c_pts, radius=2.3, sides=8), m_rubber, _mat4())
        m_plug = _mat4(tuple(float(v) for v in cable.plug_pos))
        sc.add_mesh(bnc_plug_mesh, m_steel, m_plug)
        sc.add_mesh(strain_boot_mesh, boot_mat, m_plug)

    # === 6. HERO C1-170 BENCH OSCILLOSCOPE RIG ===
    m_scope = _mat4((0, 8, 0), (-7.0, 0, 0))
    sc.add_mesh(box, m_chassis, m_scope @ _mat4((0, 0, -136), scale=(4.36, 2.14, 2.70)))
    sc.add_mesh(bezel_mesh, m_chassis, m_scope @ _mat4((0, 0, -7.5)))
    sc.add_mesh(bezel_mesh, m_chassis, m_scope @ _mat4((0, 0, -276)))
    sc.add_mesh(rect, m_cal_sticker, m_scope @ _mat4((-30, 89.2, 0.65), (0, 0, -2.0), (0.31, 0.142, 1.0)))

    # Bobblehead on top of scope
    m_bob = m_scope @ _mat4((118, 107.2, -28), (7.0, -12, 0), (0.74, 0.74, 0.74))
    sc.add_mesh(cyl, m_rubber, m_bob @ _mat4((0, 3.5, 0), scale=(0.38, 0.07, 0.38)))
    sc.add_mesh(cyl, m_hoodie, m_bob @ _mat4((-5.5, 13, 0), scale=(0.08, 0.14, 0.08)))
    sc.add_mesh(cyl, m_hoodie, m_bob @ _mat4((5.5, 13, 0), scale=(0.08, 0.14, 0.08)))
    sc.add_mesh(cyl, m_hoodie, m_bob @ _mat4((0, 30, 0), scale=(0.24, 0.25, 0.19)))
    sc.add_mesh(bobble_spring_mesh, m_steel, m_bob @ _mat4((0, 46, 0)))
    m_head = m_bob @ _mat4((0, 48, 0))
    sc.add_mesh(sph, m_bobble_face, m_head @ _mat4((0, 17, 0), scale=(0.36, 0.40, 0.35)))
    sc.add_mesh(sph, m_hair, m_head @ _mat4((0, 33, -2), scale=(0.30, 0.13, 0.28)))
    sc.add_mesh(sph, m_skin, m_head @ _mat4((-17.5, 16, 0), scale=(0.07, 0.10, 0.06)))
    sc.add_mesh(sph, m_skin, m_head @ _mat4((17.5, 16, 0), scale=(0.07, 0.10, 0.06)))

    # Continuous tubular U-bail stand + rubber grip pads + rear feet
    for sx in (-221, 221):
        m_piv = m_scope @ _mat4((sx, -8, -112))
        sc.add_mesh(cyl, m_handle, m_piv @ _mat4(rot=(0, 0, 90), scale=(0.68, 0.14, 0.68)))
        sc.add_mesh(cyl, m_handle, m_piv @ _mat4((-9 if sx < 0 else 9, 0, 0), (0, 0, 90), (0.46, 0.12, 0.46)))
        sc.add_mesh(cyl, m_steel, m_piv @ _mat4((-15 if sx < 0 else 15, 0, 0), (0, 0, 90), (0.18, 0.04, 0.18)))
    sc.add_mesh(bail_mesh, m_handle, m_scope)
    for gx in (-168, 168):
        sc.add_mesh(cyl, m_rubber, m_scope @ _mat4((gx, -120, 24), (0, 0, 90), (0.245, 0.56, 0.245)))
    for fx in (-176, 176):
        sc.add_mesh(box, m_rubber, m_scope @ _mat4((fx, -111, -226), scale=(0.26, 0.08, 0.22)))

    # Faceplate + 4 slotted pan-head screws
    sc.add_mesh(rect, m_faceplate, m_scope @ _mat4((0, 0, 0.4), scale=(4.24, 2.02, 1.0)))
    for idx, (sx, sy) in enumerate(((-203, 93), (203, 93), (-203, -93), (203, -93))):
        m_sc = m_scope @ _mat4((sx, sy, 0.5), (0, 0, idx * 37 + 22))
        sc.add_mesh(screw_mesh, m_steel, m_sc)
        sc.add_mesh(box, m_rubber, m_sc @ _mat4((0, 0, 2.15), scale=(0.056, 0.010, 0.008)))

    # CRT Bay: Hood + Curved Live Phosphor Layer + Floating Thick Refractive/Reflective Cover Glass
    m_crt = m_scope @ _mat4((-112, -2, 0.5))
    sc.add_mesh(hood_mesh, m_chassis, m_crt)
    sc.add_mesh(crt_mesh, m_crt_screen, m_crt @ _mat4((0, 0, 2.2)))
    sc.add_mesh(crt_mesh, m_crt_glass, m_crt @ _mat4((0, 0, 4.8)))

    # Compute world-space CRT area light frame for Next Event Estimation (NEE)
    crt_origin = (m_crt @ np.array([0.0, 0.0, 5.2, 1.0], np.float32))[:3]
    crt_u = (m_crt[:3, :3] @ np.array([74.0, 0.0, 0.0], np.float32))
    crt_v = (m_crt[:3, :3] @ np.array([0.0, 69.0, 0.0], np.float32))
    crt_n = (m_crt[:3, :3] @ np.array([0.0, 0.0, 1.0], np.float32))
    crt_n /= (float(np.linalg.norm(crt_n)) or 1.0)

    # Trim knobs (INTENS, GAIN, OUTPUT)
    for ky, norm_val in ((42, 0.55), (-2, 0.45), (-46, 0.65)):
        m_k = m_scope @ _mat4((-2, ky, 0.5), (0, 0, 135.0 - norm_val * 270.0))
        sc.add_mesh(trim_knob_mesh, m_knob, m_k)
        sc.add_mesh(cyl, m_steel, m_k @ _mat4((0, 0, 12.8), (90, 0, 0), (0.11, 0.012, 0.11)))
        sc.add_mesh(box, m_pointer, m_k @ _mat4((0, 3.6, 13.4), scale=(0.016, 0.055, 0.012)))

    # Large dual-concentric selector knobs (CH1 X GAIN, CH2 Y GAIN, TIME WINDOW) + recessed LCDs
    for kx, norm_val, lcd_m in ((48, 0.24, m_lcd_gain), (104, 0.24, m_lcd_gain), (170, 0.50, m_lcd_xy)):
        m_k = m_scope @ _mat4((kx, -8, 0.5), (0, 0, 135.0 - norm_val * 270.0))
        sc.add_mesh(knob_mesh, m_knob, m_k)
        sc.add_mesh(cyl, m_steel, m_k @ _mat4((0, 0, 19.3), (90, 0, 0), (0.12, 0.012, 0.12)))
        sc.add_mesh(box, m_pointer, m_k @ _mat4((0, 5.2, 19.8), scale=(0.020, 0.085, 0.016)))
        sc.add_mesh(box, m_rubber, m_scope @ _mat4((kx, -34, 1.0), scale=(0.365, 0.128, 0.026)))
        sc.add_mesh(rect, lcd_m, m_scope @ _mat4((kx, -34, 2.4), scale=(0.32, 0.095, 1.0)))

    # Pill pushbuttons & glowing LED jewels
    def add_pill(px, py, mat_cap, pressed=False, led_mat=None):
        m_b = m_scope @ _mat4((px, py, 0.5))
        sc.add_mesh(box, m_rubber, m_b @ _mat4((0, 0, 0.5), scale=(0.17, 0.095, 0.02)))
        sc.add_mesh(keycap_mesh, mat_cap, m_b @ _mat4((0, 0, 0.9 if pressed else 4.2)))
        if led_mat is not None:
            sc.add_mesh(sph, led_mat, m_b @ _mat4((0, 6.8, 1.8), scale=(0.028, 0.028, 0.024)))

    add_pill(34, 76, m_yellow_pill, pressed=True)
    add_pill(68, 76, m_green_pill, pressed=False, led_mat=m_rubber)
    add_pill(94, 76, m_green_pill, pressed=False, led_mat=m_led_green)
    add_pill(120, 76, m_green_pill, pressed=True, led_mat=m_led_green)
    for tx in (154, 174, 194):
        sc.add_mesh(cyl, m_steel, m_scope @ _mat4((tx, 78, 0.7), (90, 0, 0), (0.085, 0.02, 0.085)))
        sc.add_mesh(cyl, m_rubber, m_scope @ _mat4((tx, 78, 1.7), (90, 0, 0), (0.055, 0.02, 0.055)))
    for bx in (26, 48, 76, 104, 154, 182):
        add_pill(bx, 36, m_grey_pill)
    add_pill(48, -52, m_grey_pill, led_mat=m_rubber)
    for bx in (92, 118, 154, 182):
        add_pill(bx, -52, m_grey_pill)

    # Bottom BNC jacks + PWR button
    for bx in (46, 102, 152):
        m_j = m_scope @ _mat4((bx, -84, 0.6))
        sc.add_mesh(bnc_mesh, m_steel, m_j)
        sc.add_mesh(cyl, m_ptfe, m_j @ _mat4((0, 0, 13.0), (90, 0, 0), (0.068, 0.015, 0.068)))
        sc.add_mesh(cyl, m_gold_pin, m_j @ _mat4((0, 0, 13.6), (90, 0, 0), (0.018, 0.02, 0.018)))
    m_pwr = m_scope @ _mat4((192, -84, 0.6))
    sc.add_mesh(cyl, m_steel, m_pwr @ _mat4(rot=(90, 0, 0), scale=(0.13, 0.03, 0.13)))
    sc.add_mesh(cyl, m_led_amber, m_pwr @ _mat4((0, 0, 3.2), (90, 0, 0), (0.092, 0.09, 0.092)))

    # Assemble flat arrays & build SAH BVH
    v0 = np.concatenate(sc.v0, axis=0)
    v1 = np.concatenate(sc.v1, axis=0)
    v2 = np.concatenate(sc.v2, axis=0)
    n0 = np.concatenate(sc.n0, axis=0)
    n1 = np.concatenate(sc.n1, axis=0)
    n2 = np.concatenate(sc.n2, axis=0)
    uv0 = np.concatenate(sc.uv0, axis=0)
    uv1 = np.concatenate(sc.uv1, axis=0)
    uv2 = np.concatenate(sc.uv2, axis=0)
    mat_ids = np.concatenate(sc.mats, axis=0)

    nodes_buf, tris_buf, bvh_stats = build_bvh(v0, v1, v2, n0, n1, n2, uv0, uv1, uv2, mat_ids)
    mats_buf = np.ascontiguousarray(np.stack(sc.mat_table, axis=0), dtype=np.uint32)
    tex_array = _load_texture_array(sample_rate_label=sample_rate_label)

    lights = dict(
        lamp_bulb_pos=tuple(float(x) for x in bulb_world),
        lamp_bulb_radius=22.0,
        lamp_dir=tuple(float(x) for x in lamp_dir),
        crt_origin=tuple(float(x) for x in crt_origin),
        crt_u=tuple(float(x) for x in crt_u),
        crt_v=tuple(float(x) for x in crt_v),
        crt_n=tuple(float(x) for x in crt_n),
    )
    return dict(
        nodes=nodes_buf,
        tris=tris_buf,
        materials=mats_buf,
        textures=tex_array,
        lights=lights,
        stats=bvh_stats,
    )
