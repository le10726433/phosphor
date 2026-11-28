"""Unit tests for Phosphor's cross-platform wgpu Compute BVH Path Tracer."""
import os
from pathlib import Path
import unittest

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

from phosphor.raytrace.bvh import INTERNAL_FLAG, build_bvh
from phosphor.raytrace.scene_builder import TEX_SIZE, build_lab_bench_scene

try:  # The live path tracer is optional: wgpu and rendercanvas (requirements/requirements-raytrace.txt).
    from phosphor.raytrace.qt_host import LiveCrtPhosphorBuffer
except ImportError:
    LiveCrtPhosphorBuffer = None


def _app():
    return QApplication.instance() or QApplication([])


class RaytraceUnitTests(unittest.TestCase):
    def test_sah_bvh_packs_valid_nodes_and_covers_all_triangles(self):
        rng = np.random.default_rng(42)
        n = 64
        v0 = rng.uniform(-10.0, 10.0, (n, 3)).astype(np.float32)
        v1 = v0 + rng.uniform(0.2, 2.0, (n, 3)).astype(np.float32)
        v2 = v0 + rng.uniform(-2.0, 2.0, (n, 3)).astype(np.float32)
        nrm = np.tile(np.array([[0.0, 1.0, 0.0]], np.float32), (n, 1))
        uv = np.zeros((n, 2), dtype=np.float32)
        mats = np.arange(n, dtype=np.uint32) % 4

        nodes, tris, stats = build_bvh(v0, v1, v2, nrm, nrm, nrm, uv, uv, uv, mats)
        self.assertEqual(stats['num_tris'], n)
        self.assertEqual(nodes.shape[1], 8)
        self.assertEqual(tris.shape, (n, 28))
        self.assertLessEqual(stats['max_depth'], 32)

        # Every leaf triangle index must be in bounds and total leaf triangles must equal N
        leaf_total = 0
        for row in nodes:
            if (int(row[7]) & int(INTERNAL_FLAG)) == 0:
                first, cnt = int(row[3]), int(row[7])
                self.assertGreater(cnt, 0)
                self.assertLessEqual(first + cnt, n)
                leaf_total += cnt
        self.assertEqual(leaf_total, n)

    def test_lab_bench_scene_exports_bvh_materials_textures_and_lights(self):
        _app()
        scene = build_lab_bench_scene(sample_rate_label='48kHz')
        self.assertGreater(scene['stats']['num_tris'], 20000)
        self.assertLessEqual(scene['stats']['max_depth'], 34)
        self.assertEqual(scene['nodes'].shape[1], 8)
        self.assertEqual(scene['tris'].shape[1], 28)
        self.assertEqual(scene['materials'].shape[1], 16)
        self.assertEqual(scene['textures'].shape[1:], (TEX_SIZE, TEX_SIZE, 4))
        self.assertGreaterEqual(scene['textures'].shape[0], 15)
        self.assertIn('lamp_bulb_pos', scene['lights'])
        self.assertIn('lamp_dir', scene['lights'])
        self.assertAlmostEqual(float(np.linalg.norm(scene['lights']['lamp_dir'])), 1.0, places=4)
        self.assertIn('crt_origin', scene['lights'])

    @unittest.skipIf(LiveCrtPhosphorBuffer is None, 'the optional wgpu path tracer is not installed')
    def test_live_crt_phosphor_buffer_deposits_and_decays_signal(self):
        buf = LiveCrtPhosphorBuffer(size=256)
        t = np.linspace(0.0, 2.0 * np.pi, 512, dtype=np.float32)
        sig = np.column_stack([0.6 * np.cos(t), 0.6 * np.sin(t)])
        rgba = buf.deposit(sig, elapsed=1.0 / 60.0)
        self.assertEqual(rgba.shape, (256, 256, 4))
        peak_energy = float(buf.fast.max())
        self.assertGreater(peak_energy, 0.2)

        # Decays smoothly with empty signal
        for _ in range(20):
            buf.deposit(None, elapsed=1.0 / 60.0)
        self.assertLess(float(buf.fast.max()), peak_energy * 0.2)

    def test_wgsl_compute_shaders_exist(self):
        sdir = Path(__file__).resolve().parents[2] / 'phosphor' / 'raytrace' / 'shaders'
        for name, token in (
            ('pathtrace.wgsl', '@compute'),
            ('denoise.wgsl', '@compute'),
            ('blit.wgsl', '@fragment'),
        ):
            p = sdir / name
            self.assertTrue(p.is_file(), f'Missing {p}')
            txt = p.read_text(encoding='utf-8')
            self.assertIn(token, txt)


if __name__ == '__main__':
    unittest.main()
