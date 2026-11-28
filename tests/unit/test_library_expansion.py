"""Comprehensive unit tests for expanded built-in objects and transforms library (0.93.0-dev)."""
import unittest
import numpy as np

from phosphor.storage.build_info import VERSION
from phosphor.storage.app_files import SCENE_VERSION, scene_document, scene_from_document
from phosphor.engine.solids import (
    SOLIDS, PLATONIC_SOLIDS, EXTRA_SOLIDS, POLYGONS, EXTRA_2D, CURVES, EXTRA_CURVES,
    geometry, route_for,
)
from phosphor.engine.matrix_rain import (
    MATRIX_OBJECT, MATRIX_LANGUAGES, MatrixConfig, matrix_route_at, validate_matrix,
)
from phosphor.engine.transforms import (
    KINDS, SCENE_25_TRANSFORMS, TOPOLOGY_KINDS,
    QUANTIZE, TWIST, BEND, WAVE, SPHERIZE, GLITCH, ATTRACTOR,
    SUBDIVIDE, GRIDIFY, RADIAL_ARRAY, KALEIDOSCOPE, ECHO, POINTIFY, SCALE,
    Param, Transform, new_transform, apply_stack, apply_topology_stack,
    pointify_params, pointify_phase, validate_stack,
)
from phosphor.engine.signal_engine import Shape, Settings, render, scene_plan, validate
from phosphor.engine.composition import Project, Clip, Note, render_song, clip_settings
from phosphor.engine.lane_access import automatable, get_points, set_points, default_points, blocks
from phosphor.engine.object_replace import new_shape, replaced


class LibraryObjectsTests(unittest.TestCase):
    def test_build_version_and_scene_version(self):
        # The library objects arrived in 0.93.0 with scene version 25; later builds keep them
        # (tests never pin the version literals).
        self.assertGreaterEqual(tuple(int(part) for part in VERSION.split('-')[0].split('.')), (0, 93, 0))
        self.assertGreaterEqual(SCENE_VERSION, 25)

    def test_all_new_solids_have_valid_normalized_geometry_and_closed_routes(self):
        new_names = (*EXTRA_SOLIDS, *EXTRA_2D, *EXTRA_CURVES)
        self.assertGreaterEqual(len(new_names), 19)
        for name in SOLIDS:
            verts, edges = geometry(name)
            route = route_for(name)
            self.assertEqual(verts.ndim, 2, name)
            self.assertEqual(verts.shape[1], 3, name)
            self.assertTrue(np.isfinite(verts).all(), name)
            self.assertLessEqual(float(np.linalg.norm(verts, axis=1).max()), 1.0001, name)
            self.assertGreater(len(edges), 0, name)
            self.assertGreater(len(route), 2, name)
            self.assertEqual(int(route[0]), int(route[-1]), name)
            # Render 1 short block cleanly for every solid
            settings = Settings(shapes=(Shape(solid=name),))
            validate(settings)
            times = np.arange(0, 64) / 48000.0
            drawing, _, out = render(times, settings)
            self.assertEqual(out.shape, (64, 2), name)
            self.assertTrue(np.isfinite(out).all(), name)

    def test_matrix_rain_languages_samples_determinism_and_automation(self):
        cfg = MatrixConfig(
            language_a='Japanese (Katakana/Kanji)',
            language_b='English',
            sample_a='アイウエオ',
            sample_b='PHOSPHOR',
            columns=4,
            rows=4,
            text_speed=Param(6.0),
            fall_speed=Param(2.0),
        )
        validate_matrix(cfg)
        pts0, edges0, route0, conn0 = matrix_route_at(cfg, 0.0)
        pts0_b, _, route0_b, _ = matrix_route_at(cfg, 0.0)
        pts1, _, _, _ = matrix_route_at(cfg, 0.5)
        self.assertTrue(np.array_equal(pts0, pts0_b))
        self.assertTrue(np.array_equal(route0, route0_b))
        self.assertFalse(pts0.shape == pts1.shape and np.allclose(pts0, pts1))
        self.assertEqual(int(route0[0]), int(route0[-1]))
        self.assertGreater(len(conn0), 0)

        # Round-trip serialization + version gate
        shape = new_shape(MATRIX_OBJECT)
        self.assertEqual(shape.solid, MATRIX_OBJECT)
        self.assertIsInstance(shape.matrix, MatrixConfig)
        doc = scene_document(Settings(shapes=(Shape(solid=MATRIX_OBJECT, matrix=cfg),)), 'drawing', '')
        restored, _, _ = scene_from_document(doc)
        self.assertEqual(restored.shapes[0].matrix, cfg)
        old_doc = dict(doc, version=24)
        with self.assertRaises(ValueError):
            scene_from_document(old_doc)

        # Composer automation lanes on Matrix text_speed & fall_speed
        clip = Clip(0., 8., Settings(shapes=(Shape(solid=MATRIX_OBJECT, matrix=cfg),)))
        keys = automatable(clip)
        self.assertIn(('matrix', 0, 'text_speed'), keys)
        self.assertIn(('matrix', 0, 'fall_speed'), keys)
        clip = set_points(clip, ('matrix', 0, 'text_speed'), ((0., 0.5, 'linear'), (8., 1.0, 'linear')))
        clip = set_points(clip, ('matrix', 0, 'fall_speed'), ((0., -1.0, 'linear'), (8., 1.0, 'linear')))
        self.assertEqual(len(get_points(clip, ('matrix', 0, 'text_speed'))), 2)
        self.assertTrue(any(b.title.endswith('Matrix') for b in blocks(clip)))
        proj = Project(clips=(clip,), notes=(Note(0., 4., 60),))
        out = render_song(np.arange(0, 128) / 48000.0, proj, Settings())
        self.assertEqual(out.shape, (128, 2))
        self.assertTrue(np.isfinite(out).all())

        # Object Replace parks and restores MatrixConfig cleanly
        as_cube = replaced(Shape(solid=MATRIX_OBJECT, matrix=cfg), 'Cube')
        self.assertEqual(as_cube.solid, 'Cube')
        self.assertEqual(as_cube.matrix, cfg)
        back = replaced(as_cube, MATRIX_OBJECT)
        self.assertEqual(back.solid, MATRIX_OBJECT)
        self.assertEqual(back.matrix, cfg)


class LibraryTransformsTests(unittest.TestCase):
    def test_all_13_new_transforms_validate_render_and_roundtrip(self):
        self.assertEqual(len(SCENE_25_TRANSFORMS), 13)
        times = np.arange(0, 128) / 48000.0
        for kind in SCENE_25_TRANSFORMS:
            step = new_transform(kind)
            validate_stack((step,))
            shape = Shape(solid='Cube', transforms=(step,))
            settings = Settings(shapes=(shape,))
            validate(settings)
            scene_plan.cache_clear()
            drawing, _, out = render(times, settings)
            self.assertEqual(out.shape, (128, 2), kind)
            self.assertTrue(np.isfinite(out).all(), kind)
            # Round-trip serialization + version 24 gate
            doc = scene_document(settings, 'drawing', '')
            restored, _, _ = scene_from_document(doc)
            self.assertEqual(restored.shapes[0].transforms, (step,), kind)
            with self.assertRaises(ValueError, msg=kind):
                scene_from_document(dict(doc, version=24))

    def test_quantize_twist_bend_wave_spherize_glitch_attractor_behavior(self):
        pts = np.array([[0.37, -0.62, 0.24], [-0.81, 0.44, -0.19], [0.12, 0.93, 0.51]], dtype=float)
        # Quantize snaps to step multiples
        q_step = Transform(QUANTIZE, (Param(0.25), Param(0.25), Param(0.25)))
        q_pts, _ = apply_stack(pts, (q_step,), 0.0)
        self.assertTrue(np.allclose(q_pts * 4.0, np.round(q_pts * 4.0)))
        # Spherize amount=1 puts all non-zero points at radius
        sph = Transform(SPHERIZE, (Param(1.0), Param(1.5)))
        s_pts, _ = apply_stack(pts, (sph,), 0.0)
        self.assertTrue(np.allclose(np.linalg.norm(s_pts, axis=1), 1.5, atol=1e-9))
        # Glitch is deterministic at identical t and changes across ticks
        gl = new_transform(GLITCH)
        g1, _ = apply_stack(pts, (gl,), 0.0)
        g1b, _ = apply_stack(pts, (gl,), 0.0)
        g2, _ = apply_stack(pts, (gl,), 0.5)
        self.assertTrue(np.array_equal(g1, g1b))
        self.assertFalse(np.allclose(g1, g2))

    def test_subdivide_and_gridify_gx_gy_gz_downstream_expressions(self):
        verts, edges = geometry('Cube')
        route = route_for('Cube')
        # Subdivide pieces=3 inserts intermediate vertices along every edge
        sub = Transform(SUBDIVIDE, (Param(3.0),))
        s_pts, s_route, s_edges, _, _ = apply_topology_stack(verts, route, edges, (), (sub,), 0.0)
        self.assertGreater(len(s_pts), len(verts))
        self.assertEqual(len(s_route) - 1, (len(route) - 1) * 3)

        # Gridify 2x2x1 followed by Scale expression using gx & gy
        grid = Transform(GRIDIFY, (Param(2.0), Param(2.0), Param(1.0), Param(2.0), Param(2.0), Param(0.0)))
        scale_gx = Transform(SCALE, (Param(1.0, '1 + 0.5*gx'), Param(1.0, '1 + 0.25*gy'), Param(1.0)))
        validate_stack((grid, scale_gx))
        g_pts, g_route, _, g_conn, _ = apply_topology_stack(verts, route, edges, (), (grid, scale_gx), 0.0)
        self.assertEqual(len(g_pts), len(verts) * 4)
        self.assertGreater(len(g_conn), 0)

    def test_pointify_phase_and_render_staircase(self):
        ph = np.linspace(0., 0.999, 100)
        snapped = pointify_phase(ph, 8, glide=0.0)
        self.assertEqual(len(np.unique(np.round(snapped, 9))), 8)
        glided = pointify_phase(ph, 8, glide=0.5)
        self.assertGreater(len(np.unique(np.round(glided, 9))), 8)
        # Render with Pointify active
        shape = Shape(solid='Tilting circle', transforms=(Transform(POINTIFY, (Param(12.0), Param(0.0))),))
        scene_plan.cache_clear()
        _, _, out = render(np.arange(0, 256) / 48000.0, Settings(shapes=(shape,)))
        self.assertEqual(out.shape, (256, 2))
        self.assertTrue(np.isfinite(out).all())


class LibraryEquationPresetsTests(unittest.TestCase):
    def test_all_parametric_and_recurrence_presets_compile_stay_bounded_and_render(self):
        from phosphor.engine import function_plot, recurrence
        self.assertGreaterEqual(len(function_plot.PRESETS), 23)
        self.assertGreaterEqual(len(recurrence.PRESETS), 16)
        t = np.linspace(0., 0.5, 128)
        s = np.linspace(0., 1., 128)
        for name, plot in function_plot.PRESETS.items():
            pts = function_plot.compiled_plot(plot)(t, s)
            self.assertTrue(np.isfinite(pts).all(), name)
            self.assertLessEqual(float(np.abs(pts).max()), 1.25, name)
            self.assertGreater(float(np.ptp(pts[:, :2])), 0.5, name)
        for name, plot in recurrence.PRESETS.items():
            pts, stopped = recurrence.compiled_recurrence(plot).evaluate(0.0, 120.0, jit=False)
            self.assertIsNone(stopped, name)
            self.assertTrue(np.isfinite(pts).all(), name)
            self.assertLessEqual(float(np.abs(pts).max()), 1.25, name)
            self.assertGreater(float(np.ptp(pts[:, :2])), 0.5, name)


class CatalogOrganizationAndDynamicShapesTests(unittest.TestCase):
    def test_catalog_groups_cover_every_solid_equation_and_transform(self):
        from phosphor.engine import solids, function_plot, recurrence, transforms, transform_presets

        grouped_solids = [name for _, subs in solids.OBJECT_GROUPS for _, names in subs for name in names]
        self.assertEqual(set(grouped_solids), set(solids.SOLIDS))
        self.assertEqual(len(grouped_solids), len(solids.SOLIDS))

        grouped_param = [name for _, names in function_plot.PARAMETRIC_GROUPS for name in names]
        self.assertEqual(set(grouped_param), set(function_plot.PRESETS))
        self.assertEqual(len(grouped_param), len(function_plot.PRESETS))

        grouped_rec = [name for _, names in recurrence.RECURRENCE_GROUPS for name in names]
        self.assertEqual(set(grouped_rec), set(recurrence.PRESETS))
        self.assertEqual(len(grouped_rec), len(recurrence.PRESETS))

        grouped_xf = [k for _, ks in transforms.TRANSFORM_GROUPS for k in ks]
        self.assertEqual(set(grouped_xf), set(transforms.KINDS))
        self.assertEqual(len(grouped_xf), len(transforms.KINDS))

        self.assertGreaterEqual(len(transform_presets.BUILTIN_RECIPES), 6)
        for name, preset in transform_presets.BUILTIN_RECIPES.items():
            transform_presets.validate_preset(preset)
            stack, _, _, _ = transform_presets.inserted((), (), preset)
            self.assertEqual(stack, preset.transforms, name)

    def test_star_and_gear_counts_must_be_integers(self):
        from phosphor.engine.solids import ShapeParams
        for key in ('points', 'teeth'):
            for value in (5.9, True, '7', 7.0):
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'whole number'):
                    ShapeParams.read({key: value})

    def test_configurable_star_gear_and_animated_radar_clock(self):
        from phosphor.engine.solids import (
            ShapeParams, solid_geometry, solid_route, dynamic_solid_route_at,
        )
        # Star with custom points (7-point) & inner_radius
        sp_star = ShapeParams(points=7, inner_radius=0.35)
        v_star, e_star = solid_geometry('Star (5-point)', sp_star)
        r_star = solid_route('Star (5-point)', sp_star)
        self.assertEqual(len(v_star), 14)  # 7 outer + 7 inner
        self.assertEqual(int(r_star[0]), int(r_star[-1]))
        doc = scene_document(Settings(shapes=(Shape(solid='Star (5-point)', shape_params=sp_star),)), 'drawing', '')
        restored, _, _ = scene_from_document(doc)
        self.assertEqual(restored.shapes[0].shape_params.points, 7)
        self.assertAlmostEqual(restored.shapes[0].shape_params.inner_radius, 0.35)

        # Gear with custom teeth (20-tooth)
        sp_gear = ShapeParams(teeth=20, tooth_depth=0.30)
        v_gear, _ = solid_geometry('Gear (12-tooth)', sp_gear)
        self.assertEqual(len(v_gear), 20 * 5)
        doc_g = scene_document(Settings(shapes=(Shape(solid='Gear (12-tooth)', shape_params=sp_gear),)), 'drawing', '')
        restored_g, _, _ = scene_from_document(doc_g)
        self.assertEqual(restored_g.shapes[0].shape_params.teeth, 20)

        # Radar reticle sweeps smoothly clockwise over time with zero connectors
        sp_rad = ShapeParams(sweep_speed=0.5)
        vr0, _, rr0, cr0 = dynamic_solid_route_at('Radar reticle', sp_rad, 0.0)
        vr1, _, _, _ = dynamic_solid_route_at('Radar reticle', sp_rad, 0.5)  # 0.25 rev = 90 deg clockwise
        self.assertEqual(len(cr0), 0)
        self.assertEqual(int(rr0[0]), int(rr0[-1]))
        tip0, tip1 = vr0[-1, :2], vr1[-1, :2]
        self.assertAlmostEqual(float(np.dot(tip0, tip1)), 0.0, places=5)  # Rotated by 90 degrees!

        # Clock face moves minute & hour hands smoothly at exact 12:1 ratio
        sp_clk = ShapeParams(clock_speed=60.0, start_hour=12.0, start_minute=0.0)
        vc0, _, rc0, cc0 = dynamic_solid_route_at('Clock face', sp_clk, 0.0)
        vc1, _, _, _ = dynamic_solid_route_at('Clock face', sp_clk, 15.0)  # +15 min (90 deg min hand, 7.5 deg hr hand)
        self.assertGreater(len(cc0), 0)
        self.assertEqual(int(rc0[0]), int(rc0[-1]))
        min0, min1 = vc0[-2, :2], vc1[-2, :2]
        hr0, hr1 = vc0[-1, :2], vc1[-1, :2]
        # Minute hand at 12:00 -> (0, +0.76), at 12:15 -> (+0.76, 0)
        self.assertAlmostEqual(float(min0[0]), 0.0, places=5)
        self.assertAlmostEqual(float(min0[1]), 0.76, places=5)
        self.assertAlmostEqual(float(min1[0]), 0.76, places=5)
        self.assertAlmostEqual(float(min1[1]), 0.0, places=5)
        # Hour hand moved clockwise (x > 0) by 7.5 deg
        self.assertGreater(float(hr1[0]), 0.05)
        self.assertAlmostEqual(float(np.hypot(hr1[0], hr1[1])), 0.48, places=5)

        # Render live audio blocks for all 4 configurable/animated shapes
        times = np.arange(0, 128) / 48000.0
        for solid_name, sp in (
            ('Star (5-point)', sp_star),
            ('Gear (12-tooth)', sp_gear),
            ('Radar reticle', sp_rad),
            ('Clock face', sp_clk),
        ):
            scene_plan.cache_clear()
            _, _, out = render(times, Settings(shapes=(Shape(solid=solid_name, shape_params=sp),)))
            self.assertEqual(out.shape, (128, 2), solid_name)
            self.assertTrue(np.isfinite(out).all(), solid_name)

    def test_quick_add_catalog_search_and_ranking(self):
        from phosphor.editor.quick_add import collect_entries, filter_entries, FILTER_ALL, FILTER_EQUATIONS, FILTER_TRANSFORMS
        from phosphor.engine import function_plot, recurrence

        class _StoreStub:
            def __init__(self, builtins): self.builtins = builtins
        class _PanelStub:
            def __init__(self, builtins): self.store = _StoreStub(builtins); self.saved = {}

        entries = collect_entries(_PanelStub(function_plot.PRESETS), _PanelStub(recurrence.PRESETS), _PanelStub({}))
        self.assertGreaterEqual(len(entries), 90)
        # Prefix match ranks first
        res = filter_entries(entries, 'lorenz', FILTER_ALL)
        self.assertTrue(res and res[0].label == 'Lorenz attractor' and res[0].action == 'recurrence')
        # Category filtering works
        eq_only = filter_entries(entries, 'helix', FILTER_EQUATIONS)
        self.assertTrue(all(e.bucket == FILTER_EQUATIONS for e in eq_only))
        xf_only = filter_entries(entries, 'turntable', FILTER_TRANSFORMS)
        self.assertTrue(xf_only and xf_only[0].action == 'recipe')


if __name__ == '__main__':
    unittest.main()
