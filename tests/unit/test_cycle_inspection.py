"""I1: inspect one cycle (uncached plan provenance, the audio's own beam), pin a reference, B10."""
import math
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace

import numpy as np

from phosphor.engine import signal_engine as se
from phosphor.engine import cycle_inspection as ci
from phosphor.engine import switch_lane as sw
from phosphor.engine.signal_engine import Settings, Shape, scene_plan, inspect_plan, shape_audio, render
from phosphor.engine.transforms import MAP, NOISE, NoiseOptions, new_transform, with_map_preset, Param
from phosphor.engine.vector_text import TEXT_OBJECT
from phosphor.engine.scene_path import INSTANT_TRAVEL
from phosphor.engine.custom_shape import Drawing, CUSTOM_OBJECT

OFF_2_TO_4 = ((0., 1., 'hold'), (2., 0., 'hold'), (4., 1., 'hold'))
CLOCK = (.5, 0.)  # Baked: 120 BPM, t = 0 at the clip start.


def tone(*shapes, **values):
    return Settings(source='Single tone', shapes=tuple(shapes), **values)


class CycleInspectionTests(unittest.TestCase):
    def setUp(self):
        self.cube = Shape(uid='cube', position_x=-.4, shape_size=.4, rotation_x=20., rotation_y=30.)
        self.text = Shape(uid='text', solid=TEXT_OBJECT, text='HI', position_x=.4, shape_size=.3)
        self.tetra = Shape(uid='tetra', solid='Tetrahedron', position_x=.4, shape_size=.3)

    def rendered(self, settings, inspection, frequency=None):
        """shape_audio for the inspected cycle's samples: the same times and phases."""
        k = np.arange(inspection.count)
        times = inspection.pose_seconds + k / inspection.sample_rate
        pitch = None if frequency is None else np.full(len(k), frequency)
        return shape_audio(times, inspection.sample_phase, np.ones(len(k)), settings, pitch)

    def assertMatchesRender(self, settings, seconds=0., frequency=440.):
        inspection = ci.inspect_cycle(settings, seconds, frequency)
        expected = self.rendered(settings, inspection, frequency if settings.corner_dwell_us > 0 else None)
        np.testing.assert_allclose(inspection.samples, expected, rtol=0, atol=1e-12)
        plan = scene_plan(inspection.frame, settings.drawing_hz, settings.shapes, settings.travel_mode,
                          settings.travel_speed, settings.motion_time_offset, settings.shape_redraw_count,
                          settings.route_mode, settings.tempo)
        self.assertTrue(np.array_equal(inspection.points, plan[0] + plan[1]))
        self.assertTrue(np.array_equal(inspection.timing, plan[2] / plan[2][-1]))
        total = sum(share.share for share in inspection.shares) + inspection.travel_share + inspection.dwell_share
        self.assertAlmostEqual(total, 1., places=12)
        counted = sum(share.samples for share in inspection.shares) + inspection.travel_samples + inspection.dwell_samples
        self.assertEqual(counted, inspection.count)
        return inspection

    def test_objects_route_samples_and_provenance_match_the_render(self):
        settings = tone(self.cube, self.text)
        inspection = self.assertMatchesRender(settings)
        self.assertEqual(inspection.count, 110)  # ceil(48000 / 440)
        self.assertEqual([share.shape for share in inspection.shares], [0, 1])
        # Every point is attributed to the object whose geometry it is.
        cube_points = inspection.point_shape == 0
        self.assertTrue(cube_points[0] and not cube_points[-2])
        self.assertLess(inspection.points[cube_points, 0].max(), 0.)  # The cube sits left of x = 0 …
        self.assertGreater(inspection.points[inspection.point_shape == 1, 0].min(), 0.)  # … the text right.
        # Samples lie on the segment they are attributed to.
        a = inspection.points[inspection.sample_segment]; b = inspection.points[inspection.sample_segment + 1]
        cross = (b - a)[:, 0] * (inspection.samples - a)[:, 1] - (b - a)[:, 1] * (inspection.samples - a)[:, 0]
        np.testing.assert_allclose(cross, 0., atol=1e-12)
        self.assertTrue(inspection.travel.any())

    def test_provenance_names_scene_indices_past_motion_guides_and_components(self):
        settings = tone(self.cube, self.text)
        plan, provenance = inspect_plan(0, 60., settings.shapes, 'No changes', 8.)
        self.assertEqual(provenance.part_shape.tolist(), [0, 1])
        self.assertEqual(len(provenance.part_of), len(plan[0]))
        self.assertEqual(provenance.absent, ())
        drawing = Drawing('Two strokes', ((1, -.5, 0), (2, -.1, 0), (3, .1, 0), (4, .5, 0)), ((1, 2), (3, 4)))
        custom = Shape(solid=CUSTOM_OBJECT, custom=drawing)
        _, provenance = inspect_plan(0, 60., (self.cube, custom), 'No changes', 8., 0., 2)
        self.assertEqual(set(provenance.part_shape.tolist()), {0, 1})

    def test_map_transform_matches_the_render(self):
        twist = with_map_preset(new_transform(MAP), 'Twist')
        self.assertMatchesRender(tone(replace(self.cube, transforms=(twist,)), self.text))

    def test_sample_and_wobble_noise_match_the_render(self):
        for rate in ('Sample', 'Wobble'):
            noise = replace(new_transform(NOISE), noise=NoiseOptions(rate=rate)).with_param('intensity', Param(.05))
            with self.subTest(rate=rate):
                inspection = self.assertMatchesRender(tone(replace(self.cube, transforms=(noise,)), self.text), .25)
                clean = ci.inspect_cycle(tone(self.cube, self.text), .25, 440.)
                self.assertFalse(np.allclose(inspection.samples, clean.samples))

    def test_corner_dwell_holds_are_identified(self):
        settings = tone(self.cube, corner_dwell_us=60.)
        inspection = self.assertMatchesRender(settings)
        self.assertGreater(len(inspection.corners), 0)
        self.assertAlmostEqual(inspection.dwell_share, len(inspection.corners) * inspection.hold)
        self.assertGreater(inspection.dwell_samples, 0)
        held = inspection.samples[inspection.sample_held]
        corners = inspection.points[np.searchsorted(inspection.timing, inspection.corners)]
        self.assertTrue(all(np.linalg.norm(corners - point, axis=1).min() < 1e-9 for point in held))

    def test_static_mute_and_on_off_lane_leave_the_route(self):
        lane = replace(self.tetra, lanes=(('on', OFF_2_TO_4),), lane_clock=CLOCK)
        on = self.assertMatchesRender(tone(self.cube, lane), 0.5)  # Beat 1: on.
        self.assertEqual([share.shape for share in on.shares], [0, 1])
        off = self.assertMatchesRender(tone(self.cube, lane), 1.5)  # Beat 3: off.
        self.assertEqual([share.shape for share in off.shares], [0])
        self.assertEqual(off.absent, ((1, 'off'),))
        self.assertAlmostEqual(off.shares[0].share, 1.)
        muted = self.assertMatchesRender(tone(self.cube, replace(self.tetra, muted=True)), 1.5)
        self.assertEqual(muted.absent, ((1, 'muted'),))
        window = self.assertMatchesRender(tone(self.cube, replace(self.tetra, path_trace=Param(0.))))
        self.assertEqual(window.absent, ((1, 'trace'),))

    def test_travel_modes_move_the_phase_budget(self):
        shares = {}
        for mode, speed in (('No changes', 8.), ('Faster travel', 8.), ('Faster travel', INSTANT_TRAVEL)):
            inspection = self.assertMatchesRender(tone(self.cube, self.text, travel_mode=mode, travel_speed=speed))
            shares[mode, speed] = inspection.travel_share
        self.assertGreater(shares['No changes', 8.], shares['Faster travel', 8.])
        self.assertEqual(shares['Faster travel', INSTANT_TRAVEL], 0.)
        instant = ci.inspect_cycle(tone(self.cube, self.text, travel_mode='Faster travel', travel_speed=INSTANT_TRAVEL), 0., 440.)
        self.assertEqual(instant.travel_samples, 0)
        self.assertTrue(instant.travel.any())  # The jump stays on the route (drawn dashed).

    def test_visits_count_retraced_edges_and_redraws(self):
        inspection = ci.inspect_cycle(tone(self.cube, self.text, shape_redraw_count=2), 0., 110.)
        self.assertEqual(inspection.visit.max(), 4)  # The cube's route retraces edges; drawn twice.
        index = next(i for i in range(inspection.count) if ci.sample_info(inspection, i).visits > 1)
        found = ci.next_visit(inspection, index)
        self.assertIsNotNone(found)
        a, b = ci.sample_info(inspection, index), ci.sample_info(inspection, found)
        self.assertEqual(inspection.edge[a.segment], inspection.edge[b.segment])
        self.assertNotEqual(a.segment, b.segment)
        self.assertEqual(ci.first_sample_of(inspection, 1), int(np.flatnonzero(
            (inspection.point_shape[inspection.sample_segment] == 1) & ~inspection.travel[inspection.sample_segment])[0]))

    def test_empty_and_fully_muted_scenes(self):
        for settings in (tone(), tone(replace(self.cube, muted=True))):
            inspection = ci.inspect_cycle(settings, 0., 440.)
            self.assertTrue(inspection.empty)
            self.assertEqual(inspection.shares, ())
            self.assertTrue(np.array_equal(inspection.samples, np.zeros((110, 2))))
            info = ci.sample_info(inspection, 5)
            self.assertEqual(info.shape, -1)
            self.assertIsNone(ci.first_sample_of(inspection, 0))

    def test_pitch_rate_and_validation(self):
        settings = tone(self.cube)
        self.assertEqual(ci.inspect_cycle(replace(settings, sample_rate=96000), 0., 440.).count, 219)
        self.assertEqual(ci.inspect_cycle(settings, 0., 110.).count, 437)
        for bad in (0., 4., 25000., float('nan')):
            with self.assertRaises(ValueError): ci.inspect_cycle(settings, 0., bad)

    def test_inspection_never_grows_the_plan_cache_or_changes_audio(self):
        settings = tone(self.cube, self.text)
        times = np.arange(4800) / 48000
        before = render(times, settings)[2]
        scene_plan.cache_clear()
        render(times, settings)
        info, size = scene_plan.cache_info(), scene_plan.cache_bytes()
        for seconds in (0., .5, 1., 3.):
            ci.inspect_cycle(settings, seconds, 440.)
        self.assertEqual(scene_plan.cache_info(), info)  # Not one hit, miss or entry more.
        self.assertEqual(scene_plan.cache_bytes(), size)
        self.assertTrue(np.array_equal(render(times, settings)[2], before))
        scene_plan.cache_clear()
        ci.inspect_cycle(settings, 0., 440.)
        self.assertEqual(scene_plan.cache_info().currsize, 0)

    def test_inspected_plan_equals_the_cached_plan(self):
        shapes = (replace(self.cube, transforms=(with_map_preset(new_transform(MAP), 'Twist'),)), self.text)
        for frame in (0, 17):
            plan, _ = inspect_plan(frame, 60., shapes, 'Retrace existing edges', 8.)
            cached = scene_plan(frame, 60., shapes, 'Retrace existing edges', 8.)
            for a, b in zip(plan[:3], cached[:3]): self.assertTrue(np.array_equal(a, b))

    def test_difference_needs_a_shared_time_base(self):
        settings = tone(self.cube, self.text)
        a = ci.inspect_cycle(settings, 0., 440.)
        self.assertEqual(ci.difference(a, ci.inspect_cycle(settings, 0., 440.)), ci.Difference(0., 0.))
        moved = ci.difference(a, ci.inspect_cycle(tone(replace(self.cube, position_x=-.3), self.text), 0., 440.))
        self.assertGreater(moved.max, 0.)
        self.assertIsNone(ci.difference(a, ci.inspect_cycle(settings, 0., 220.)))
        self.assertIsNone(ci.difference(None, a))

    def test_lanes_off_matches_posed_shapes(self):
        lane = replace(self.tetra, lanes=(('on', OFF_2_TO_4),), lane_clock=CLOCK)
        shapes = (self.cube, lane)
        for seconds in (0., .99, 1., 1.5, 2., 3.):
            posed = se.posed_shapes(shapes, seconds)
            self.assertEqual(sw.lanes_off(shapes, seconds), frozenset(i for i, s in enumerate(posed) if s.muted))
        self.assertEqual(sw.lanes_off((replace(lane, lane_clock=()),), 1.5), frozenset())  # Unbaked: first node.


class InspectorWindowTests(unittest.TestCase):
    """The panel, its overlay layers and the pinned reference, and B10's lane-aware outline."""
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from phosphor.editor.qt_app import Window, PANEL_SCENE
        self.w = Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
        self.w.timer.stop(); self.w.show_panel(PANEL_SCENE)
        self.w.add_object('Cube'); self.w.add_object('Tetrahedron')
        self.w.show(); self.app.processEvents()

    def tearDown(self):
        self.w.force_close = True
        self.w.shutdown_audio()
        self.w.scene.mark_saved(); self.w.project_dirty = False
        self.w.release_views(); self.w.close(); self.w.deleteLater(); self.app.processEvents()

    def test_inspector_layers_pin_and_audio_untouched(self):
        from phosphor.editor.qt_app import PANEL_INSPECT
        w = self.w
        times = np.arange(2400) / 48000
        before = render(times, w.output_settings())[2]
        w.overlay(.25); base = len(w.host.scope.overlays)
        w.inspect_cycle(True)
        self.assertEqual(w.current_panel, PANEL_INSPECT)
        self.assertTrue(w.inspect_action.isChecked())
        w.overlay(.25)
        layers = w.host.scope.overlays
        self.assertEqual(len(layers), base + 5)
        self.assertEqual(len(layers[base][0]), 0)  # No reference yet.
        self.assertTrue(all(len(points) for points, _ in layers[base + 1:]))
        inspection = w.inspector.inspection
        self.assertEqual([share.shape for share in inspection.shares], [0, 1])
        w.inspector.pin(); w.overlay(.25)
        self.assertTrue(len(w.host.scope.overlays[base][0]))
        self.assertIn('differs by up to 0.0000', w.inspector.reference_info.text())
        # An edit re-inspects at the same pose; the reference stays where it was pinned.
        pinned = w.inspector.reference
        w.select_shape(0); w.scene.change('position_x', -.5); w.overlay(3.)
        self.assertIsNot(w.inspector.inspection, inspection)
        self.assertEqual(w.inspector.inspection.pose_seconds, pinned.pose_seconds)
        self.assertIs(w.inspector.reference, pinned)
        self.assertNotIn('up to 0.0000', w.inspector.reference_info.text())
        w.inspector.scrub(10); self.assertEqual(w.inspector.slider.value(), 10)
        self.assertTrue(w.inspector.info.text())
        w.inspect_cycle(False); w.overlay(.25)
        self.assertEqual(len(w.host.scope.overlays), base + 5)  # The reference alone still shows.
        self.assertEqual([len(points) for points, _ in w.host.scope.overlays[base + 1:]], [0] * 4)
        w.inspector.clear_reference(); w.overlay(.25)
        self.assertEqual(len(w.host.scope.overlays), base)
        w.select_shape(0); w.scene.change('position_x', 0.)
        self.assertTrue(np.array_equal(render(times, w.output_settings())[2], before))

    def test_muted_outline_follows_the_on_off_lane_at_the_playhead(self):
        """Audit B10: the faint outline shows where the lane switches the object off."""
        from phosphor.engine.composition import with_clip
        from phosphor.engine.lane_access import set_points
        w = self.w; w.set_workspace('Composer')
        w.add_object('Cube'); w.add_object('Tetrahedron'); w.store_clip_scene()
        clip = set_points(w.project.clips[w.clip_index], ('on', 1), OFF_2_TO_4)
        w.set_project(with_clip(w.project, w.clip_index, clip)); w.select_shape(0)
        w.handles_seen = -math.inf  # Hidden handles (UI1): their layer stays empty, whatever its fading alpha.
        faint = lambda: [layer for layer in w.host.scope.overlays if layer[1].alphaF() < .25 and len(layer[0])]
        for beat, off in ((1., False), (3., True), (5., False)):
            w.playhead = float(w.project.seconds(beat)); w.overlay(w.playhead)
            self.assertEqual(bool(faint()), off, beat)
        w.playhead = float(w.project.seconds(3.)); w.overlay(w.playhead)
        static = faint()[0][0]
        w.toggle_objects('mute', 1); w.playhead = float(w.project.seconds(1.)); w.overlay(w.playhead)
        self.assertTrue(np.array_equal(faint()[0][0], static))  # The same outline as a static mute.
        # The inspector agrees with the audio: Off at beat 3.
        w.toggle_objects('mute', 1); w.playhead = float(w.project.seconds(3.))
        w.inspect_cycle(True); w.overlay(w.playhead)
        self.assertEqual(w.inspector.inspection.absent, ((1, 'off'),))


if __name__ == '__main__': unittest.main()
