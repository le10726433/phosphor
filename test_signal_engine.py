import unittest
import numpy as np
from signal_engine import (Settings, render, prepare_audio, shape_audio,
                           projected_solid, scale_components)
from solids import SOLIDS, POLYGONS, geometry, route_for
from signal_engine import Shape, validate


class SignalTests(unittest.TestCase):
    def test_manual_xyz_rotation_changes_view_and_preserves_depth(self):
        base = Settings(solid='Tilting circle', rotation_seconds=None)
        face_on = projected_solid(0, base)
        x_view = projected_solid(0, Settings(solid='Tilting circle', rotation_seconds=None, rotation_x=90))
        y_view = projected_solid(0, Settings(solid='Tilting circle', rotation_seconds=None, rotation_y=90))
        self.assertAlmostEqual(float(np.ptp(x_view[:, 1])), 0, places=12)
        self.assertAlmostEqual(float(np.ptp(y_view[:, 0])), 0, places=12)
        z_view = projected_solid(0, Settings(solid='Tilting circle', rotation_seconds=None, rotation_z=90))
        np.testing.assert_allclose(z_view, face_on @ np.array([[0, 1], [-1, 0]]), atol=1e-12)
        # A manual view rotation can reveal the depth of an animated edge-on circle.
        revealed = projected_solid(1.25, Settings(solid='Tilting circle', rotation_y=90))
        self.assertAlmostEqual(float(np.ptp(revealed[:, 0])), 2, places=12)
        for name in SOLIDS:
            settings = Settings(solid=name, rotation_seconds=None, rotation_x=23, rotation_y=-71, rotation_z=105)
            validate(settings)
            self.assertTrue(np.isfinite(render(np.arange(2048)/48000, settings)[2]).all())
            np.testing.assert_array_equal(projected_solid(0, settings), projected_solid(100, settings))
        with self.assertRaises(ValueError):
            validate(Settings(shapes=(Shape(rotation_x=181),)))

    def test_no_rotation_stays_fixed_and_is_valid_for_scene_and_audio(self):
        for name in SOLIDS:
            settings = Settings(solid=name, rotation_seconds=None)
            validate(settings)
            np.testing.assert_array_equal(projected_solid(0, settings), projected_solid(123, settings))
            times = np.arange(4000) / 48000
            output = render(times, settings)[2]
            self.assertTrue(np.isfinite(output).all())
            scene = Settings(shapes=(Shape(name, rotation_seconds=None),))
            np.testing.assert_array_equal(output, render(times, scene)[2])
        for value in (0, .001, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                validate(Settings(rotation_seconds=value))

    def test_placement_changes_samples_and_preserves_headroom(self):
        times = np.arange(4800) / 48000
        base = render(times, Settings())[0]
        settings = Settings(position_x=.3, position_y=-.2, shape_size=.6)
        moved, _, output = render(times, settings)
        np.testing.assert_allclose(moved, base * .6 + [.3, -.2], atol=1e-14)
        self.assertLessEqual(abs(output).max(), .95)
        # Translation is independent of envelope, including a silent note boundary.
        np.testing.assert_allclose(moved[0], [.3, -.2])
        oversized = render(times, Settings(position_x=3, shape_size=4))[2]
        self.assertGreater(abs(oversized).max(), 1)

    def test_solid_topology_and_closed_routes(self):
        expected = {'Cube': (8, 12), 'Tetrahedron': (4, 6), 'Octahedron': (6, 12),
                    'Dodecahedron': (20, 30), 'Icosahedron': (12, 30),
                    'Triangle (2D)': (3, 3), 'Square (2D)': (4, 4), 'Pentagon (2D)': (5, 5),
                    'Tilting circle': (128, 128), 'Octant sphere': (282, 288)}
        self.assertEqual(set(SOLIDS), set(expected))
        for solid in SOLIDS:
            vertices, edges = geometry(solid)
            self.assertEqual((len(vertices), len(edges)), expected[solid])
            degree = np.bincount(np.array(edges).ravel())
            if solid == 'Octant sphere':
                self.assertEqual(np.count_nonzero(degree == 4), 6)
                self.assertTrue(np.all(np.isin(degree, [2, 4])))
            else:
                self.assertTrue(np.all(degree == degree[0]))
            lengths = np.array([np.linalg.norm(vertices[a] - vertices[b]) for a, b in edges])
            np.testing.assert_allclose(lengths, lengths[0], atol=1e-12)
            route = route_for(solid)
            self.assertEqual(route[0], route[-1])
            covered = {tuple(sorted((int(a), int(b)))) for a, b in zip(route[:-1], route[1:])}
            self.assertEqual(covered, set(edges))

    def test_tilting_circle_is_circle_ellipse_line_and_returns(self):
        settings = Settings(solid='Tilting circle', rotation_seconds=4)
        for seconds, width in ((0, 2), (.5, np.sqrt(2)), (1, 0), (1.5, np.sqrt(2)), (2, 2), (4, 2)):
            points = projected_solid(seconds, settings)
            self.assertAlmostEqual(float(np.ptp(points[:, 0])), width, places=12)
            self.assertAlmostEqual(float(np.ptp(points[:, 1])), 2, places=12)
        np.testing.assert_allclose(projected_solid(0, settings), projected_solid(4, settings), atol=1e-14)

    def test_octant_sphere_has_three_great_circles_and_no_connector_edges(self):
        vertices, edges = geometry('Octant sphere')
        np.testing.assert_allclose(np.linalg.norm(vertices, axis=1), 1, atol=1e-12)
        for normal in range(3):
            self.assertEqual(np.count_nonzero(np.isclose(vertices[:, normal], 0)), 96)
            plane_edges = [(a, b) for a, b in edges if abs(vertices[a, normal]) < 1e-12 and abs(vertices[b, normal]) < 1e-12]
            self.assertEqual(len(plane_edges), 96)
        for a, b in edges:
            self.assertTrue(np.any((abs(vertices[a]) < 1e-12) & (abs(vertices[b]) < 1e-12)))
        self.assertEqual(len(route_for('Octant sphere')), 289)

    def test_polygons_rotate_in_plane_and_trace_perimeter_once(self):
        for name, sides in POLYGONS.items():
            settings = Settings(solid=name)
            vertices = geometry(name)[0]
            np.testing.assert_array_equal(vertices[:, 2], 0)
            for time in (0, .7, 1.25, 2.5):
                projected = projected_solid(time, settings)
                np.testing.assert_allclose(np.linalg.norm(projected, axis=1), 1, atol=1e-12)
            first = projected_solid(0, settings)
            quarter = projected_solid(1.25, settings)
            np.testing.assert_allclose(quarter, first @ np.array([[0, 1], [-1, 0]]), atol=1e-12)
            self.assertEqual(len(route_for(name)), sides + 1)

    def test_audio_drives_each_solid_and_chunk_boundaries_do_not_change_output(self):
        times = np.arange(12000) / 48000
        for solid in SOLIDS:
            settings = Settings(solid=solid)
            drawing, original, output = render(times, settings)
            np.testing.assert_allclose(output, settings.drawing_gain * drawing)
            pieces = np.concatenate([render(times[:3951], settings)[2], render(times[3951:], settings)[2]])
            np.testing.assert_array_equal(output, pieces)
            self.assertTrue(np.isfinite(output).all())
            self.assertLessEqual(abs(output).max(), .55)
            self.assertFalse(np.allclose(output, .55 * original))
            np.testing.assert_allclose(projected_solid(0, settings), projected_solid(5, settings), atol=1e-14)

    def test_path_samples_stay_on_edges_with_unit_envelope(self):
        for solid in SOLIDS:
            settings = Settings(solid=solid)
            phase = np.linspace(0, 1, 2000, endpoint=False)
            points = shape_audio(np.zeros(len(phase)), phase, np.ones(len(phase)), settings)
            vertices = projected_solid(0, settings)
            edges = geometry(solid)[1]
            distances = []
            for a, b in edges:
                start, delta = vertices[a], vertices[b] - vertices[a]
                fraction = np.clip((points - start) @ delta / max(delta @ delta, 1e-15), 0, 1)
                distances.append(np.linalg.norm(points - start - fraction[:, None] * delta, axis=1))
            self.assertLess(np.min(distances, axis=0).max(), 1e-12)

    def test_arbitrary_stereo_analysis_handles_antiphase_and_silence(self):
        times = np.arange(48000) / 48000
        sine = np.sin(2 * np.pi * 220 * times)
        for stereo in (np.column_stack((sine, sine)), np.column_stack((sine, -sine)),
                       np.column_stack((np.zeros(len(sine)), sine))):
            source = prepare_audio(stereo)
            cycles = np.unwrap(source.phase * 2 * np.pi) / (2 * np.pi)
            self.assertAlmostEqual(float(np.median(np.diff(cycles)) * 48000), 220, places=7)
            np.testing.assert_allclose(source.envelope, 1, atol=1e-11)
            output = render(times[:4000], Settings(), source)[2]
            self.assertGreater(abs(output).max(), .1)
        silent = prepare_audio(np.zeros((4800, 2)))
        np.testing.assert_array_equal(render(times[:4000], Settings(), silent)[2], 0)

    def test_blend_endpoints_and_quarter_note_rhythm(self):
        times = np.arange(48000) / 48000
        _, source, output = render(times, Settings(music_gain=1))
        np.testing.assert_allclose(output, .55 * source)
        _, phase, envelope = scale_components(np.array([.1, .2, .5, .6]), Settings())
        self.assertAlmostEqual((phase[1] - phase[0]) / .1, 130.8127826502993)
        self.assertEqual(envelope[2], 0)
        self.assertAlmostEqual(phase[3] / .1, 146.8323839587038)


if __name__ == '__main__':
    unittest.main()
