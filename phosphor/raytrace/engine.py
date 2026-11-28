"""Cross-platform wgpu Compute BVH Path Tracer Engine (macOS Metal & Windows DX12/Vulkan)."""
import math
from pathlib import Path
import numpy as np
from PySide6.QtGui import QQuaternion, QVector3D

import wgpu

from phosphor.raytrace.scene_builder import TEX_SIZE, build_lab_bench_scene

SHADER_PATH = Path(__file__).with_name('shaders') / 'pathtrace.wgsl'
DENOISE_SHADER_PATH = Path(__file__).with_name('shaders') / 'denoise.wgsl'
BLIT_SHADER_PATH = Path(__file__).with_name('shaders') / 'blit.wgsl'

_SRGB_TO_LIN_LUT = np.power(np.arange(256, dtype=np.float32) / 255.0, 2.2)

# Matching camera presets from desk3d.qml: 0 = Desk, 1 = Scope Close-up, 2 = Controls Close-up
CAMERA_PRESETS = (
    dict(pos=(-6.0, 56.0, 622.0), rot=(-7.2, 1.0, 0.0), fov=36.0, aperture=0.0, focus=620.0),
    dict(pos=(-112.0, 6.0, 206.0), rot=(-2.5, 1.5, 0.0), fov=34.0, aperture=1.2, focus=205.0),
    dict(pos=(96.0, -2.0, 218.0), rot=(-3.0, -2.5, 0.0), fov=34.0, aperture=1.6, focus=216.0),
)


def _camera_basis(rot_xyz):
    """Compute camera (right, up, forward) matching QtQuick3D PerspectiveCamera eulerRotation."""
    q = QQuaternion.fromEulerAngles(float(rot_xyz[0]), float(rot_xyz[1]), float(rot_xyz[2]))
    right = q.rotatedVector(QVector3D(1.0, 0.0, 0.0))
    up = q.rotatedVector(QVector3D(0.0, 1.0, 0.0))
    fwd = q.rotatedVector(QVector3D(0.0, 0.0, -1.0))
    return (
        (right.x(), right.y(), right.z()),
        (up.x(), up.y(), up.z()),
        (fwd.x(), fwd.y(), fwd.z()),
    )


def make_demo_crt_image(size=512, kind='lissajous', color=(88, 255, 128)):
    """Generate a vibrant test phosphor oscilloscope image (RGBA8) for standalone verification."""
    img = np.zeros((size, size, 4), dtype=np.uint8)
    img[:, :, 0] = 12
    img[:, :, 1] = 24
    img[:, :, 2] = 17
    img[:, :, 3] = 255

    # Accumulate glowing 3D-projected Lissajous knot / Lorenz-like trace in float32
    buf = np.zeros((size, size), dtype=np.float32)
    t = np.linspace(0.0, 2.0 * math.pi, 3200, dtype=np.float32)
    if kind == 'lissajous':
        x = 0.72 * np.sin(3.0 * t + 0.55) * (0.85 + 0.15 * np.cos(7.0 * t))
        y = 0.72 * np.sin(4.0 * t) * (0.85 + 0.15 * np.sin(5.0 * t))
    else:
        x = 0.68 * np.cos(2.0 * t) * (0.6 + 0.4 * np.cos(5.0 * t))
        y = 0.68 * np.sin(3.0 * t) * (0.6 + 0.4 * np.sin(4.0 * t))
    px = np.clip(((x * 0.5 + 0.5) * (size - 1)).astype(np.int32), 2, size - 3)
    py = np.clip(((0.5 - y * 0.5) * (size - 1)).astype(np.int32), 2, size - 3)
    for dy in (-2, -1, 0, 1, 2):
        for dx in (-2, -1, 0, 1, 2):
            w = math.exp(-(dx * dx + dy * dy) * 0.45)
            np.add.at(buf, (py + dy, px + dx), w * 0.7)

    # Soft phosphor bloom halo via separable box passes
    halo = buf.copy()
    for step in (2, 4, 8, 14):
        halo = 0.25 * (np.roll(halo, step, 0) + np.roll(halo, -step, 0)
                       + np.roll(halo, step, 1) + np.roll(halo, -step, 1))
    glow = np.clip(buf * 0.95 + halo * 1.35, 0.0, 2.5)
    core = np.clip((buf - 0.55) * 0.75, 0.0, 1.0)

    r = np.clip(12.0 + glow * color[0] + core * 165.0, 0, 255).astype(np.uint8)
    g = np.clip(24.0 + glow * color[1] + core * 165.0, 0, 255).astype(np.uint8)
    b = np.clip(17.0 + glow * color[2] + core * 165.0, 0, 255).astype(np.uint8)
    img[:, :, 0] = r
    img[:, :, 1] = g
    img[:, :, 2] = b
    return np.ascontiguousarray(img)


class PathTracerEngine:
    """Real-time progressive GPU Compute BVH Path Tracer backed by wgpu (Metal / DX12 / Vulkan)."""

    def __init__(self, width=1280, height=800, sample_rate_label='48kHz', scene=None):
        self.width = int(width)
        self.height = int(height)
        self.adapter = wgpu.gpu.request_adapter_sync(power_preference='high-performance')
        if self.adapter is None:
            raise RuntimeError('No suitable WebGPU adapter found (Metal / DX12 / Vulkan)')
        self.device = self.adapter.request_device_sync()
        self.backend_name = f"{self.adapter.info.get('backend_type', 'GPU')} · {self.adapter.info.get('device', '')}".strip(' ·')

        self.scene = scene if scene is not None else build_lab_bench_scene(sample_rate_label=sample_rate_label)
        self.lights = self.scene['lights']
        self.frame_idx = 0
        self.total_spp = 0

        # Lighting & camera state
        self.lamp_on = True
        self.fluor_on = False
        self.crt_power = 1.0
        self.show_grid = True
        self.exposure = 1.15
        self.max_bounces = 3
        self.ambient_gain = 1.0
        self.ema_floor = 0.0
        self.denoise_enabled = True
        self.camera_preset = 0
        self.zoom_offset = 0.0
        self.parallax_x = 0.0
        self.parallax_y = 0.0

        # Optional zero-copy GPU swapchain blit resources
        self._blit_pipeline = None
        self._blit_bind_group = None
        self._blit_sampler = None
        self._surface_format = None
        self._read_buf = None
        self._read_bpr = 0

        self._upload_scene_resources()
        self._create_output_targets(self.width, self.height)
        self._create_pipeline()

    def _upload_scene_resources(self):
        dev = self.device
        nodes = self.scene['nodes']
        tris = self.scene['tris']
        mats = self.scene['materials']
        tex_arr = self.scene['textures']

        self.nodes_buf = dev.create_buffer_with_data(data=nodes, usage=wgpu.BufferUsage.STORAGE)
        self.tris_buf = dev.create_buffer_with_data(data=tris, usage=wgpu.BufferUsage.STORAGE)
        self.mats_buf = dev.create_buffer_with_data(data=mats, usage=wgpu.BufferUsage.STORAGE)
        self.uniform_buf = dev.create_buffer(size=256, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST)
        self.denoise_ubufs = [
            dev.create_buffer(size=32, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST)
            for _ in range(4)
        ]

        layers = tex_arr.shape[0]
        self.tex_array = dev.create_texture(
            size=(TEX_SIZE, TEX_SIZE, layers),
            usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST,
            dimension=wgpu.TextureDimension.d2,
            format=wgpu.TextureFormat.rgba8unorm,
        )
        dev.queue.write_texture(
            {'texture': self.tex_array, 'origin': (0, 0, 0)},
            tex_arr,
            {'bytes_per_row': TEX_SIZE * 4, 'rows_per_image': TEX_SIZE},
            (TEX_SIZE, TEX_SIZE, layers),
        )
        self.tex_array_view = self.tex_array.create_view(
            dimension=wgpu.TextureViewDimension.d2_array,
            array_layer_count=layers,
        )
        self.sampler = dev.create_sampler(
            mag_filter=wgpu.FilterMode.linear,
            min_filter=wgpu.FilterMode.linear,
            address_mode_u=wgpu.AddressMode.repeat,
            address_mode_v=wgpu.AddressMode.repeat,
        )

        # Live 512x512 crisp CRT phosphor texture + smooth 16x16 pre-blurred CRT GI irradiance map
        self.crt_size = 512
        self.crt_tex = dev.create_texture(
            size=(self.crt_size, self.crt_size, 1),
            usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST,
            dimension=wgpu.TextureDimension.d2,
            format=wgpu.TextureFormat.rgba8unorm,
        )
        self.crt_tex_view = self.crt_tex.create_view()

        self.crt_irr_size = 16
        self.crt_irr_rgba = np.zeros((self.crt_irr_size, self.crt_irr_size, 4), dtype=np.uint8)
        self.crt_irr_rgba[:, :, 3] = 255
        self.crt_irr_tex = dev.create_texture(
            size=(self.crt_irr_size, self.crt_irr_size, 1),
            usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST,
            dimension=wgpu.TextureDimension.d2,
            format=wgpu.TextureFormat.rgba8unorm,
        )
        self.crt_irr_view = self.crt_irr_tex.create_view()
        self.update_crt_texture(make_demo_crt_image(self.crt_size))

    def _create_output_targets(self, width, height):
        self.width = max(64, int(width))
        self.height = max(64, int(height))
        dev = self.device
        buf_bytes = self.width * self.height * 16
        self.accum_buf = dev.create_buffer(size=buf_bytes, usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_DST)
        self.gbuf = dev.create_buffer(size=buf_bytes, usage=wgpu.BufferUsage.STORAGE)
        self.albedo_buf = dev.create_buffer(size=buf_bytes, usage=wgpu.BufferUsage.STORAGE)
        self.denoise_a = dev.create_buffer(size=buf_bytes, usage=wgpu.BufferUsage.STORAGE)
        self.denoise_b = dev.create_buffer(size=buf_bytes, usage=wgpu.BufferUsage.STORAGE)
        self.out_tex = dev.create_texture(
            size=(self.width, self.height, 1),
            usage=wgpu.TextureUsage.STORAGE_BINDING | wgpu.TextureUsage.COPY_SRC | wgpu.TextureUsage.TEXTURE_BINDING,
            dimension=wgpu.TextureDimension.d2,
            format=wgpu.TextureFormat.rgba8unorm,
        )
        self.out_tex_view = self.out_tex.create_view()
        self._read_buf = None
        self._read_bpr = ((self.width * 4 + 255) // 256) * 256
        self.frame_idx = 0
        self.total_spp = 0

    def _create_pipeline(self):
        dev = self.device
        pt_mod = dev.create_shader_module(code=SHADER_PATH.read_text(encoding='utf-8'))
        self.pipeline = dev.create_compute_pipeline(
            layout='auto',
            compute={'module': pt_mod, 'entry_point': 'main'},
        )
        dn_mod = dev.create_shader_module(code=DENOISE_SHADER_PATH.read_text(encoding='utf-8'))
        self.denoise_pipeline = dev.create_compute_pipeline(
            layout='auto',
            compute={'module': dn_mod, 'entry_point': 'main'},
        )
        self._rebuild_bind_group()

    def _rebuild_blit_bind_group(self):
        if self._blit_pipeline is None or self._blit_sampler is None:
            return
        self._blit_bind_group = self.device.create_bind_group(
            layout=self._blit_pipeline.get_bind_group_layout(0),
            entries=[
                {'binding': 0, 'resource': self.out_tex_view},
                {'binding': 1, 'resource': self._blit_sampler},
            ],
        )

    def _rebuild_bind_group(self):
        layout = self.pipeline.get_bind_group_layout(0)
        self.bind_group = self.device.create_bind_group(
            layout=layout,
            entries=[
                {'binding': 0, 'resource': {'buffer': self.uniform_buf, 'offset': 0, 'size': 256}},
                {'binding': 1, 'resource': {'buffer': self.nodes_buf, 'offset': 0, 'size': self.nodes_buf.size}},
                {'binding': 2, 'resource': {'buffer': self.tris_buf, 'offset': 0, 'size': self.tris_buf.size}},
                {'binding': 3, 'resource': {'buffer': self.mats_buf, 'offset': 0, 'size': self.mats_buf.size}},
                {'binding': 4, 'resource': self.tex_array_view},
                {'binding': 5, 'resource': self.sampler},
                {'binding': 6, 'resource': self.crt_tex_view},
                {'binding': 7, 'resource': {'buffer': self.accum_buf, 'offset': 0, 'size': self.accum_buf.size}},
                {'binding': 8, 'resource': {'buffer': self.gbuf, 'offset': 0, 'size': self.gbuf.size}},
                {'binding': 9, 'resource': {'buffer': self.albedo_buf, 'offset': 0, 'size': self.albedo_buf.size}},
                {'binding': 10, 'resource': self.crt_irr_view},
            ],
        )
        dn_layout = self.denoise_pipeline.get_bind_group_layout(0)
        # 4-pass A-Trous ping-pong (steps 1, 2, 4, 8):
        # (accum -> denoise_a) -> (denoise_a -> denoise_b) -> (denoise_b -> denoise_a) -> (denoise_a -> denoise_b + out_tex)
        pairs = (
            (self.accum_buf, self.denoise_a),
            (self.denoise_a, self.denoise_b),
            (self.denoise_b, self.denoise_a),
            (self.denoise_a, self.denoise_b),
        )
        self.denoise_bind_groups = []
        for idx, (src_b, dst_b) in enumerate(pairs):
            self.denoise_bind_groups.append(
                self.device.create_bind_group(
                    layout=dn_layout,
                    entries=[
                        {'binding': 0, 'resource': {'buffer': self.denoise_ubufs[idx], 'offset': 0, 'size': 32}},
                        {'binding': 1, 'resource': {'buffer': src_b, 'offset': 0, 'size': src_b.size}},
                        {'binding': 2, 'resource': {'buffer': self.gbuf, 'offset': 0, 'size': self.gbuf.size}},
                        {'binding': 3, 'resource': {'buffer': self.albedo_buf, 'offset': 0, 'size': self.albedo_buf.size}},
                        {'binding': 4, 'resource': {'buffer': dst_b, 'offset': 0, 'size': dst_b.size}},
                        {'binding': 5, 'resource': self.out_tex_view},
                    ],
                )
            )
        self._rebuild_blit_bind_group()

    def configure_surface(self, wgpu_context):
        """Configure a native rendercanvas/wgpu swapchain for zero-copy GPU fullscreen presentation."""
        pref = wgpu_context.get_preferred_format(self.adapter)
        # `out_tex` already holds ACES + sRGB-encoded values, so configure a non-sRGB swapchain format
        fmt = pref[:-5] if isinstance(pref, str) and pref.endswith('-srgb') else pref
        try:
            wgpu_context.configure(device=self.device, format=fmt)
            self._surface_format = fmt
        except Exception:
            wgpu_context.configure(device=self.device, format=pref)
            self._surface_format = pref

        dev = self.device
        blit_mod = dev.create_shader_module(code=BLIT_SHADER_PATH.read_text(encoding='utf-8'))
        self._blit_sampler = dev.create_sampler(
            mag_filter=wgpu.FilterMode.linear,
            min_filter=wgpu.FilterMode.linear,
            address_mode_u=wgpu.AddressMode.clamp_to_edge,
            address_mode_v=wgpu.AddressMode.clamp_to_edge,
        )
        self._blit_pipeline = dev.create_render_pipeline(
            layout='auto',
            vertex={'module': blit_mod, 'entry_point': 'vs_main'},
            fragment={
                'module': blit_mod,
                'entry_point': 'fs_main',
                'targets': [{'format': self._surface_format}],
            },
            primitive={'topology': wgpu.PrimitiveTopology.triangle_list},
        )
        self._rebuild_blit_bind_group()
        return self._surface_format

    def resize(self, width, height):
        width, height = max(64, int(width)), max(64, int(height))
        if width != self.width or height != self.height:
            self._create_output_targets(width, height)
            self._rebuild_bind_group()

    def reset_accumulation(self):
        self.frame_idx = 0
        self.total_spp = 0

    def update_crt_texture(self, rgba_u8, reset_accum=False):
        """Upload live (512, 512, 4) RGBA8 CRT phosphor image + pre-blurred 16x16 GI irradiance map."""
        arr = np.ascontiguousarray(rgba_u8, dtype=np.uint8)
        h, w = arr.shape[:2]
        if (w, h) != (self.crt_size, self.crt_size):
            from PIL import Image
            arr = np.asarray(Image.fromarray(arr, 'RGBA').resize((self.crt_size, self.crt_size), Image.Resampling.BILINEAR), np.uint8)
        self.device.queue.write_texture(
            {'texture': self.crt_tex, 'origin': (0, 0, 0)},
            arr,
            {'bytes_per_row': self.crt_size * 4, 'rows_per_image': self.crt_size},
            (self.crt_size, self.crt_size, 1),
        )

        # Energy-conserving linear downsample 512x512 -> 16x16 + 2x Gaussian-weighted 3x3 blur
        # Eliminates ~95% of green CRT GI speckle when sampling uniformly across a mostly dark screen
        lin = _SRGB_TO_LIN_LUT[arr[:, :, :3]]
        block = lin.reshape(16, 32, 16, 32, 3).mean(axis=(1, 3))
        for _ in range(2):
            p = np.pad(block, ((1, 1), (1, 1), (0, 0)), mode='edge')
            block = (
                p[0:-2, 0:-2] + 2.0 * p[0:-2, 1:-1] + p[0:-2, 2:]
                + 2.0 * p[1:-1, 0:-2] + 4.0 * p[1:-1, 1:-1] + 2.0 * p[1:-1, 2:]
                + p[2:, 0:-2] + 2.0 * p[2:, 1:-1] + p[2:, 2:]
            ) * (1.0 / 16.0)
        self.crt_irr_rgba[:, :, :3] = np.clip(
            np.power(np.clip(block, 0.0, 1.0), 1.0 / 2.2) * 255.0, 0.0, 255.0
        ).astype(np.uint8)
        self.device.queue.write_texture(
            {'texture': self.crt_irr_tex, 'origin': (0, 0, 0)},
            self.crt_irr_rgba,
            {'bytes_per_row': self.crt_irr_size * 4, 'rows_per_image': self.crt_irr_size},
            (self.crt_irr_size, self.crt_irr_size, 1),
        )
        if reset_accum:
            self.reset_accumulation()

    def _pack_uniforms(self, spp):
        preset = CAMERA_PRESETS[int(self.camera_preset) % len(CAMERA_PRESETS)]
        px_scale = 16.0 if self.camera_preset == 0 else 6.0
        py_scale = 10.0 if self.camera_preset == 0 else 4.0
        pos = (
            preset['pos'][0] + self.parallax_x * px_scale,
            preset['pos'][1] - self.parallax_y * py_scale,
            preset['pos'][2] + self.zoom_offset,
        )
        rot = (
            preset['rot'][0] - self.parallax_y * 2.0,
            preset['rot'][1] - self.parallax_x * 2.8,
            preset['rot'][2],
        )
        right, up, fwd = _camera_basis(rot)
        fov_tan = math.tan(math.radians(preset['fov'] * 0.5))
        aspect = self.width / max(1.0, float(self.height))

        buf = np.zeros(64, dtype=np.uint32)  # 256 bytes
        f = buf.view(np.float32)

        f[0:3] = pos;           f[3] = fov_tan
        f[4:7] = right;         f[7] = aspect
        f[8:11] = up;           f[11] = preset['aperture']
        f[12:15] = fwd;         f[15] = preset['focus']

        f[16:19] = self.lights['lamp_bulb_pos']; f[19] = self.lights['lamp_bulb_radius']
        f[20:23] = self.lights['crt_origin'];    f[23] = float(self.crt_power)
        f[24:27] = self.lights['crt_u'];         f[27] = 1.0 if self.lamp_on else 0.0
        f[28:31] = self.lights['crt_v'];         f[31] = 1.0 if self.fluor_on else 0.0
        f[32:35] = self.lights['crt_n'];         f[35] = 1.0 if self.show_grid else 0.0

        buf[36] = np.uint32(self.width)
        buf[37] = np.uint32(self.height)
        buf[38] = np.uint32(self.frame_idx)
        buf[39] = np.uint32(max(1, int(spp)))
        f[40] = float(self.exposure)
        buf[41] = np.uint32(self.max_bounces)
        f[42] = float(self.ambient_gain)
        f[43] = float(self.ema_floor)
        f[44:47] = self.lights['lamp_dir']
        f[47] = 0.0
        return buf

    def _pack_denoise_uniforms(self, step_size, is_final):
        buf = np.zeros(8, dtype=np.uint32)
        f = buf.view(np.float32)
        buf[0] = np.uint32(self.width)
        buf[1] = np.uint32(self.height)
        buf[2] = np.uint32(step_size)
        buf[3] = np.uint32(1 if is_final else 0)
        f[4] = float(self.exposure)
        f[5] = 1.0 if self.fluor_on else 0.0
        f[6] = 1.0 if self.denoise_enabled else 0.0
        eff_spp = float(max(1, self.total_spp))
        if self.ema_floor > 0.001:
            eff_spp = min(eff_spp, 1.0 / self.ema_floor)
        f[7] = eff_spp
        return buf

    def _encode_resolve(self, enc):
        steps = (1, 2, 4, 8) if self.denoise_enabled else (1,)
        wg_x = (self.width + 7) // 8
        wg_y = (self.height + 7) // 8
        for idx, step_size in enumerate(steps):
            is_final = (idx == len(steps) - 1)
            self.device.queue.write_buffer(self.denoise_ubufs[idx], 0, self._pack_denoise_uniforms(step_size, is_final))
        for idx in range(len(steps)):
            cp = enc.begin_compute_pass()
            cp.set_pipeline(self.denoise_pipeline)
            cp.set_bind_group(0, self.denoise_bind_groups[idx])
            cp.dispatch_workgroups(wg_x, wg_y, 1)
            cp.end()

    def _encode_blit(self, enc, wgpu_context):
        if self._blit_pipeline is None or self._blit_bind_group is None:
            self.configure_surface(wgpu_context)
        cur_tex = wgpu_context.get_current_texture()
        cur_view = cur_tex.create_view()
        rp = enc.begin_render_pass(
            color_attachments=[{
                'view': cur_view,
                'resolve_target': None,
                'clear_value': (0.02, 0.03, 0.04, 1.0),
                'load_op': wgpu.LoadOp.clear,
                'store_op': wgpu.StoreOp.store,
            }]
        )
        rp.set_pipeline(self._blit_pipeline)
        rp.set_bind_group(0, self._blit_bind_group)
        rp.draw(3, 1, 0, 0)
        rp.end()

    def resolve_output(self):
        """Run the 4-pass demodulated A-Trous denoiser + ACES Filmic tonemapper into `out_tex`."""
        enc = self.device.create_command_encoder()
        self._encode_resolve(enc)
        self.device.queue.submit([enc.finish()])

    def present_to_context(self, wgpu_context):
        """Zero-copy GPU blit of the latest `out_tex` into a configured native wgpu swapchain."""
        enc = self.device.create_command_encoder()
        self._encode_blit(enc, wgpu_context)
        self.device.queue.submit([enc.finish()])

    def step(self, spp=1, resolve=True):
        """Dispatch one progressive path-tracing compute pass of `spp` samples per pixel."""
        n = max(1, int(spp))
        ubuf = self._pack_uniforms(n)
        self.device.queue.write_buffer(self.uniform_buf, 0, ubuf)

        enc = self.device.create_command_encoder()
        cp = enc.begin_compute_pass()
        cp.set_pipeline(self.pipeline)
        cp.set_bind_group(0, self.bind_group)
        cp.dispatch_workgroups((self.width + 7) // 8, (self.height + 7) // 8, 1)
        cp.end()
        self.frame_idx += 1
        self.total_spp += n
        if resolve:
            self._encode_resolve(enc)
        self.device.queue.submit([enc.finish()])

    def step_and_present(self, wgpu_context, spp=1, trace=True, resolve=True):
        """Trace + 4-pass denoise + zero-copy native swapchain blit in ONE GPU command buffer submit."""
        enc = self.device.create_command_encoder()
        if trace:
            n = max(1, int(spp))
            ubuf = self._pack_uniforms(n)
            self.device.queue.write_buffer(self.uniform_buf, 0, ubuf)
            cp = enc.begin_compute_pass()
            cp.set_pipeline(self.pipeline)
            cp.set_bind_group(0, self.bind_group)
            cp.dispatch_workgroups((self.width + 7) // 8, (self.height + 7) // 8, 1)
            cp.end()
            self.frame_idx += 1
            self.total_spp += n
        if trace or resolve:
            self._encode_resolve(enc)
        self._encode_blit(enc, wgpu_context)
        self.device.queue.submit([enc.finish()])

    def render_samples(self, total_spp=64, batch_spp=8):
        """Reset accumulation and render `total_spp` samples in GPU batches, then denoise + tonemap."""
        self.reset_accumulation()
        done = 0
        while done < total_spp:
            n = min(batch_spp, total_spp - done)
            self.step(spp=n, resolve=False)
            done += n
        self.resolve_output()

    def read_rgba(self):
        """Read back the current tonemapped (H, W, 4) uint8 RGBA frame from the GPU (for snapshots/captures)."""
        bpr = self._read_bpr
        if self._read_buf is None:
            self._read_buf = self.device.create_buffer(
                size=bpr * self.height,
                usage=wgpu.BufferUsage.COPY_DST | wgpu.BufferUsage.MAP_READ,
            )
        enc = self.device.create_command_encoder()
        enc.copy_texture_to_buffer(
            {'texture': self.out_tex, 'origin': (0, 0, 0)},
            {'buffer': self._read_buf, 'bytes_per_row': bpr, 'rows_per_image': self.height},
            (self.width, self.height, 1),
        )
        self.device.queue.submit([enc.finish()])
        self._read_buf.map_sync(wgpu.MapMode.READ)
        raw = bytes(self._read_buf.read_mapped())
        self._read_buf.unmap()
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((self.height, bpr))
        return np.ascontiguousarray(arr[:, :self.width * 4].reshape((self.height, self.width, 4)))
