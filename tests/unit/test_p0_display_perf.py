"""Unit tests for P0 display, geometry, phosphor, and tail interpolation optimizations."""
import unittest
import numpy as np
from phosphor.display.trace_interpolation import INTERPOLATION_FACTORS, interpolate_trace, interpolate_tail
from phosphor.display.qt_geometry import (
    BEAM_VERTEX, beam_triangles, segment_quads, stroke_edges, stroke_triangles,
)
from phosphor.display.phosphor import (
    PHOSPHOR_P31, deposit_vertices, hex_color, preset_profile, stroke_deposit,
)


class TailInterpolationTests(unittest.TestCase):
    def test_interpolate_tail_matches_full_interpolation_slice(self):
        rng = np.random.default_rng(42)
        for length in (0, 1, 2, 3, 4, 5, 10, 33, 200):
            pts = rng.uniform(-1.0, 1.0, (length, 2)).astype(np.float32)
            for mode in INTERPOLATION_FACTORS:
                full = interpolate_trace(pts, mode)
                for count in (0, 1, 2, 3, 4, 5, 7, 16, 50, 5000):
                    tail = interpolate_tail(pts, mode, count)
                    if count <= 0 or length < 2:
                        self.assertEqual(len(tail), 0, f'length={length} mode={mode} count={count}')
                    else:
                        expected = full[-count - 1:]
                        self.assertEqual(tail.shape, expected.shape, f'length={length} mode={mode} count={count}')
                        np.testing.assert_allclose(tail, expected, rtol=0, atol=1e-6,
                                                   err_msg=f'length={length} mode={mode} count={count}')


class GeometryOptimizationTests(unittest.TestCase):
    def test_stroke_triangles_and_edges_basic(self):
        pts = np.array([[0., 0.], [10., 0.], [10., 10.], [10., 10.], [20., 10.]], dtype=np.float32)
        tris = stroke_triangles(pts, 2.0)
        self.assertEqual(tris.dtype, np.float32)
        # 3 non-zero segments (segment [10,10]->[10,10] is degenerate and omitted) * 6 = 18
        self.assertEqual(tris.shape, (18, 2))
        self.assertTrue(np.all(np.isfinite(tris)))

        edges = np.array([[0, 1], [1, 2], [2, 3], [3, 4]], dtype=np.int32)
        edge_tris = stroke_edges(pts, edges, 2.0)
        self.assertEqual(edge_tris.dtype, np.float32)
        self.assertEqual(edge_tris.shape, (18, 2))
        np.testing.assert_allclose(edge_tris, tris, atol=1e-6)

    def test_segment_quads_empty_and_nonempty(self):
        empty = np.empty((0, 2), dtype=np.float32)
        out = segment_quads(empty, empty, empty, np.empty(0, dtype=np.float32), 2.0)
        self.assertEqual(out.shape, (0, 2))
        self.assertEqual(out.dtype, np.float32)

        a = np.array([[0., 0.]], dtype=np.float32)
        b = np.array([[10., 0.]], dtype=np.float32)
        delta = b - a
        length = np.array([10.], dtype=np.float32)
        quad = segment_quads(a, b, delta, length, 2.0)
        self.assertEqual(quad.shape, (6, 2))
        self.assertAlmostEqual(float(quad[:, 1].max()), 1.0, places=5)
        self.assertAlmostEqual(float(quad[:, 1].min()), -1.0, places=5)

    def test_beam_triangles_shape_and_dtype(self):
        rng = np.random.default_rng(7)
        pts = rng.uniform(0, 600, (100, 2)).astype(np.float32)
        verts = beam_triangles(pts, 2.0, (0, 255, 128), 0.8, 0.8)
        self.assertEqual(verts.dtype, BEAM_VERTEX)
        self.assertEqual(len(verts), 99 * 6)
        self.assertTrue(np.all(verts['g'] > 0))


class PhosphorDepositTests(unittest.TestCase):
    def test_stroke_deposit_filters_blanked_and_stationary(self):
        pts = np.array([
            [100., 100.],
            [110., 100.],  # moving, drawn
            [110., 100.],  # stationary, drawn -> spot only (12 triangles = 36 verts)
            [200., 200.],  # moving, blanked -> skipped
            [210., 200.],  # moving, drawn
        ], dtype=np.float32)
        drawn = np.array([True, True, False, True], dtype=bool)
        verts = stroke_deposit(pts, 2.0, 1.0, 1.0, 1.0, drawn=drawn)
        self.assertEqual(verts.dtype, BEAM_VERTEX)
        # 2 drawn moving segments (2 * 6 = 12) + 1 drawn stationary spot (36) = 48
        self.assertEqual(len(verts), 48)

    def test_deposit_vertices_no_glow_matches_stroke_deposit(self):
        rng = np.random.default_rng(11)
        pts = rng.uniform(50, 550, (80, 2)).astype(np.float32)
        direct = stroke_deposit(pts, 2.5, 1.0, 0.9, 1.0, 1.0, view_scale=1.0)
        via_deposit = deposit_vertices(pts, 2.5, 0.9, (), 1.0, 1.0, view_scale=1.0)
        np.testing.assert_array_equal(via_deposit, direct)

    def test_deposit_vertices_with_glow_bands(self):
        # Short segments ensure non-zero energy in both core and glow band
        t = np.linspace(0, 190, 20, dtype=np.float32)
        pts = np.column_stack((100.0 + t, 100.0 + 0.5 * t))
        verts = deposit_vertices(pts, 2.0, 1.0, ((6.0, 0.3),), 1.0, 1.0, view_scale=1.0)
        self.assertEqual(verts.dtype, BEAM_VERTEX)
        self.assertGreater(len(verts), 19 * 6)

    def test_preset_profile_cache_returns_fresh_copy(self):
        p1 = preset_profile(PHOSPHOR_P31, '#00ff80')
        p2 = preset_profile(PHOSPHOR_P31, '#00ff80')
        self.assertEqual(p1, p2)
        self.assertIsNot(p1, p2)
        p1['flash_time'] = 9999.0
        p3 = preset_profile(PHOSPHOR_P31, '#00ff80')
        self.assertNotEqual(p3['flash_time'], 9999.0)

    def test_hex_color_cached(self):
        self.assertEqual(hex_color('#00ff80'), (0.0, 1.0, 128 / 255.0))
        self.assertEqual(hex_color('#00ff80'), (0.0, 1.0, 128 / 255.0))


if __name__ == '__main__':
    unittest.main()
