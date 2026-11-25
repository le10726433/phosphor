"""Audit stage P7b (0.73.0-dev): engine hot paths keep exact results.

P4 cached route positions, P5 scene_plan's byte-bounded cache, P6 Drawing's cached hash,
P8 output_settings' warm-up skip, B1 output_project's identity key and P10/B2 noise loops
on the realtime thread.
"""
import copy
import dataclasses
import pickle
import threading
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from phosphor.engine import noise_loops, signal_engine
from phosphor.engine.custom_shape import Curve, Drawing, CUSTOM_OBJECT
from phosphor.engine.drawing_motion import render_components
from phosphor.engine.model_object import model_from_obj, MODEL_OBJECT
from phosphor.engine.route_plan import ROUTE_OFF, SHORTEST_PATH
from phosphor.engine.signal_engine import (Settings, Shape, render, scene_plan, shape_geometry,
                                           cached_route_positions)
from phosphor.engine.transforms import (new_transform, NOISE, SCALE, SPIN, NoiseOptions, Param, route_positions,
                                        curve_of)
from tests.fixtures.dense_mesh import dense_mesh_obj

TIMES = np.arange(512) / 48000


def spin():
    return new_transform(SPIN).with_param('y', Param(.25))


def plans_equal(a, b):
    """Plans are (local, centres, cumulative, exact, noisy) of arrays and tuples."""
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray): return np.array_equal(a, b)
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(plans_equal(x, y) for x, y in zip(a, b))
    return a == b


class ScenePlanCacheTests(unittest.TestCase):
    def setUp(self):
        scene_plan.cache_clear()
        self.shapes = (Shape(uid='a', transforms=(spin(),)), Shape(uid='b', solid='Tetrahedron', position_x=.4))

    def tearDown(self):
        scene_plan.cache_clear()

    def plan(self, frame, switches=None):
        return scene_plan(frame, 60., self.shapes, 'No changes', 8., 0., 1, ROUTE_OFF, 120., switches)

    def test_switches_stay_in_the_key(self):
        whole = self.plan(3)
        swept = self.plan(3, ((1., None), (0., None)))  # Object b swept to nothing.
        self.assertFalse(np.array_equal(whole[0], swept[0]))
        self.assertIs(self.plan(3), whole)
        self.assertIs(self.plan(3, ((1., None), (0., None))), swept)
        info = scene_plan.cache_info()
        self.assertEqual((info.hits, info.misses, info.currsize), (2, 2, 2))

    def test_cached_plan_equals_a_fresh_one(self):
        cached = self.plan(5)
        self.assertTrue(plans_equal(cached, scene_plan.__wrapped__(5, 60., self.shapes, 'No changes', 8., 0., 1, ROUTE_OFF,
                                                                    120., None)))

    def test_bytes_bound_keeps_the_newest_plans(self):
        with patch.object(signal_engine, 'PLAN_CACHE_BYTES', 1):
            plans = [self.plan(frame) for frame in range(10)]
            self.assertEqual(scene_plan.cache_info().currsize, signal_engine.PLAN_CACHE_KEEP)
            self.assertEqual(scene_plan.cache_bytes(), sum(signal_engine._nbytes(p) for p in plans[-signal_engine.PLAN_CACHE_KEEP:]))
            self.assertIs(self.plan(9), plans[9])  # Newest kept,
            self.assertIsNot(self.plan(0), plans[0])  # oldest recomputed,
            self.assertTrue(plans_equal(self.plan(0), plans[0]))  # identically.

    def test_entry_bound_and_clear(self):
        with patch.object(signal_engine, 'PLAN_CACHE_ENTRIES', 3):
            for frame in range(6): self.plan(frame)
            self.assertEqual(scene_plan.cache_info().currsize, 3)
        scene_plan.cache_clear()
        self.assertEqual((scene_plan.cache_info().currsize, scene_plan.cache_bytes()), (0, 0))

    def test_dense_model_stays_under_the_byte_budget(self):
        model = model_from_obj(dense_mesh_obj(), 'Dense')[0]
        self.shapes = (Shape(solid=MODEL_OBJECT, model=model, transforms=(spin(),)),)
        size = signal_engine._nbytes(self.plan(0))
        with patch.object(signal_engine, 'PLAN_CACHE_BYTES', 10 * size):  # Every frame's plan is the same size.
            for frame in range(1, 60): self.plan(frame)
            self.assertEqual(scene_plan.cache_info().currsize, 10)
            self.assertEqual(scene_plan.cache_bytes(), 10 * size)

    def test_errors_are_not_cached(self):
        calls = []
        @signal_engine.plan_cache
        def failing(value):
            calls.append(value)
            raise ValueError('no')
        for _ in range(2):
            with self.assertRaises(ValueError): failing(1)
        self.assertEqual(calls, [1, 1])


class RoutePositionTests(unittest.TestCase):
    def test_static_model_positions_are_cached_and_exact(self):
        model = model_from_obj(dense_mesh_obj(), 'Dense')[0]
        vertices, _, route = shape_geometry(Shape(solid=MODEL_OBJECT, model=model))
        self.assertFalse(vertices.flags.writeable or route.flags.writeable)
        first = cached_route_positions(vertices, route)
        self.assertIs(cached_route_positions(vertices, route), first)
        self.assertTrue(np.array_equal(first, route_positions(vertices, route)))
        self.assertFalse(first.flags.writeable)

    def test_writeable_arrays_are_never_cached(self):
        vertices = np.array([[0., 0., 0.], [1., 0., 0.], [1., 1., 0.]]); route = np.array([0, 1, 2, 0])
        first = cached_route_positions(vertices, route)
        vertices[2] = (3., 3., 0.)  # Mutable data may change between frames.
        self.assertTrue(np.array_equal(cached_route_positions(vertices, route), route_positions(vertices, route)))
        self.assertFalse(np.array_equal(first, route_positions(vertices, route)))

    def test_s_expression_render_unchanged(self):
        model = model_from_obj(dense_mesh_obj(), 'Dense')[0]
        scale = new_transform(SCALE).with_param('x', Param(1., '1 + 0.1*sin(tau*(t + s))'))
        settings = Settings(shapes=(Shape(solid=MODEL_OBJECT, model=model, transforms=(spin(), scale)),),
                            source='Single tone', tone_frequency=55.)
        scene_plan.cache_clear()
        cached = render(TIMES, settings)[2]
        scene_plan.cache_clear()
        with patch.object(signal_engine, 'cached_route_positions', route_positions):
            plain = render(TIMES, settings)[2]
        scene_plan.cache_clear()
        self.assertTrue(np.array_equal(cached, plain))


class DrawingHashTests(unittest.TestCase):
    def drawing(self, name='Square'):
        return Drawing(name, ((1, 0., 0.), (2, .5, 0.), (3, .5, .5)), ((1, 2), (2, 3)))

    def test_hash_is_cached_and_equal_to_the_field_hash(self):
        drawing = self.drawing()
        self.assertNotIn('_hash', drawing.__dict__)
        value = hash(drawing)
        self.assertEqual(drawing.__dict__['_hash'], value)
        self.assertEqual(value, hash(tuple(getattr(drawing, f.name) for f in dataclasses.fields(Drawing))))
        self.assertEqual(hash(self.drawing()), value)
        self.assertEqual(self.drawing(), drawing)
        self.assertNotEqual(self.drawing('Other'), drawing)

    def test_fields_cover_every_dataclass_field(self):
        self.assertEqual(self.drawing()._fields(), tuple(getattr(self.drawing(), f.name) for f in dataclasses.fields(Drawing)))

    def test_copies_drop_the_cache(self):
        drawing = self.drawing(); hash(drawing)
        for other in (pickle.loads(pickle.dumps(drawing)), copy.copy(drawing), copy.deepcopy(drawing),
                      replace(drawing, name='Renamed')):
            self.assertNotIn('_hash', other.__dict__)
        self.assertEqual(pickle.loads(pickle.dumps(drawing)), drawing)
        self.assertNotEqual(hash(replace(drawing, name='Renamed')), hash(drawing))

    def test_lookups_find_equal_drawings(self):
        self.assertEqual({self.drawing(): 1}[self.drawing()], 1)
        settings = Settings(shapes=(Shape(solid=CUSTOM_OBJECT, custom=self.drawing()),), source='Single tone')
        self.assertTrue(np.array_equal(render(TIMES, settings)[2],
                                       render(TIMES, replace(settings, shapes=(Shape(solid=CUSTOM_OBJECT, custom=self.drawing()),)))[2]))


def old_connected(drawing, indices):
    """0.71.0's Drawing.connected, kept as the reference."""
    chosen = set(indices); nodes = {i for k in chosen for i in drawing.segments[k]}
    while True:
        more = {k for k, segment in enumerate(drawing.segments) if nodes.intersection(segment)}
        if more <= chosen: return frozenset(chosen)
        chosen |= more; nodes.update(i for k in more for i in drawing.segments[k])


def chain(count):
    angle = np.linspace(0., 40. * np.pi, count); radius = np.linspace(.1, .9, count)
    nodes = tuple((k, float(r * np.cos(a)), float(r * np.sin(a))) for k, (a, r) in enumerate(zip(angle, radius)))
    return Drawing('Chain', nodes, tuple((k, k + 1) for k in range(count - 1)))


class ConnectedTests(unittest.TestCase):
    def test_same_closure_as_the_old_loop(self):
        rng = np.random.default_rng(73)
        for trial in range(60):
            count = int(rng.integers(2, 40))
            nodes = tuple((k, float(k), 0.) for k in range(count))
            edges = tuple(tuple(int(i) for i in rng.choice(count, 2, replace=False)) for _ in range(int(rng.integers(0, count))))
            curves = tuple(Curve('Spline', tuple(int(i) for i in rng.choice(count, 3, replace=False)))
                           for _ in range(int(rng.integers(0, 4))))
            drawing = Drawing('Random', nodes, edges, curves)
            total = len(drawing.segments)
            for size in (0, 1, 2, 3):
                if total == 0 and size: continue
                indices = tuple(int(k) for k in rng.integers(-total, total, size)) if size else ()
                with self.subTest(trial=trial, indices=indices):
                    self.assertEqual(drawing.connected(indices), old_connected(drawing, indices))
                    self.assertEqual(drawing.node_ids(indices), {i for k in indices for i in drawing.segments[k]})

    def test_components_unchanged(self):
        drawing = Drawing('Two', tuple((k, float(k % 5), float(k // 5)) for k in range(20)),
                          ((0, 1), (1, 2), (2, 7), (10, 11), (11, 16), (16, 15)), (Curve('Spline', (3, 4, 9)),))
        new = render_components.__wrapped__(drawing)
        with patch.object(Drawing, 'connected', old_connected):
            old = render_components.__wrapped__(drawing)
        self.assertEqual(len(new), 3)
        for a, b in zip(new, old):
            for x, y in zip(a[:3], b[:3]): self.assertTrue(np.array_equal(np.asarray(x), np.asarray(y)))

    def test_long_chain_is_linear(self):
        # 16,000 nodes took hours in 0.71.0 (cubic); now about 0.1 s.
        drawing = chain(16000)
        self.assertEqual(drawing.connected((0,)), frozenset(range(15999)))
        self.assertEqual(len(render_components.__wrapped__(drawing)), 1)


CURVE = ((20., 0.), (700., -5.), (20000., -40.))


def wobble_settings(curve=CURVE):
    step = replace(new_transform(NOISE), noise=NoiseOptions('Custom', 'Wobble', curve=curve)).with_param('intensity', Param(.05))
    return Settings(shapes=(Shape(transforms=(spin(), step)),), source='Single tone', tone_frequency=110.)


class RealtimeWobbleTests(unittest.TestCase):
    def setUp(self):
        noise_loops.clear(); scene_plan.cache_clear()

    def tearDown(self):
        noise_loops.set_realtime(False); noise_loops.clear(); scene_plan.cache_clear()

    def test_missing_loop_falls_back_on_the_realtime_thread_only(self):
        settings = wobble_settings()
        unwobbled = replace(settings, shapes=(replace(settings.shapes[0], transforms=settings.shapes[0].transforms[:1]),))
        reference = render(TIMES, unwobbled)[2]
        scene_plan.cache_clear()
        gate = threading.Event(); real = noise_loops.build
        def slow(curve):
            gate.wait(10); return real(curve)
        with patch.object(noise_loops, 'build', slow):
            noise_loops.set_realtime(True)
            try:
                before = noise_loops.misses()
                fallback = render(TIMES, settings)[2]  # Must not wait for the gated build.
                self.assertGreater(noise_loops.misses(), before)
                self.assertTrue(np.array_equal(fallback, reference))
                self.assertEqual(scene_plan.cache_info().currsize, 0)  # The fallback pose is never cached.
            finally:
                noise_loops.set_realtime(False)
            gate.set()
            noise_loops.get(CURVE)
        wobbled = render(TIMES, settings)[2]
        self.assertFalse(np.array_equal(wobbled, reference))
        scene_plan.cache_clear()
        noise_loops.set_realtime(True)
        try: self.assertTrue(np.array_equal(render(TIMES, settings)[2], wobbled))  # Loop ready: the real pose.
        finally: noise_loops.set_realtime(False)

    def test_export_waits_for_an_evicted_loop(self):
        settings = wobble_settings()
        expected = render(TIMES, settings)[2]
        for k in range(noise_loops.MAX_CUSTOM):  # Evict CURVE.
            noise_loops.get(((20., 0.), (900., -2. * k - 1), (20000., -30.)))
        self.assertIsNone(noise_loops.get(CURVE, wait=False))
        scene_plan.cache_clear()
        before = noise_loops.misses()
        self.assertTrue(np.array_equal(render(TIMES, settings)[2], expected))
        self.assertEqual(noise_loops.misses(), before)

    def test_failing_build_gives_up_and_the_worker_survives(self):
        real = noise_loops.build
        def fail(curve):
            if curve == noise_loops._key(CURVE): raise MemoryError('test')
            return real(curve)
        with patch.object(noise_loops, 'build', fail):
            with self.assertRaisesRegex(ValueError, 'could not be built'):
                noise_loops.get(CURVE)
            self.assertIsNotNone(noise_loops.get(noise_loops.BUILTINS[1]))
        noise_loops.clear()
        self.assertIsNotNone(noise_loops.get(CURVE))  # clear() forgets the failures.


def window_namespace(settings, **values):
    from phosphor.editor.qt_app import Window
    namespace = SimpleNamespace(
        signal_preferences=dict(corner_dwell_us=0., shape_redraw_count=3, shape_redraw=False, route_mode=ROUTE_OFF,
                                switch_sweep_ms=0.),
        scene=SimpleNamespace(settings=settings), motion_audio_offset=0.,
        audio=SimpleNamespace(running=False, sample_rate=48000), project=SimpleNamespace(bpm=120.), **values)
    namespace.audition_shapes = lambda: namespace.scene.settings.shapes
    return Window, namespace


class EditorCacheTests(unittest.TestCase):
    def test_output_settings_warms_routes_once_per_settings(self):
        from phosphor.editor import qt_app
        settings = Settings(shapes=(Shape(), Shape(solid='Tetrahedron')), source='Single tone')
        Window, w = window_namespace(settings)
        with patch.object(qt_app, 'shape_geometry', wraps=qt_app.shape_geometry) as warm:
            first = Window.output_settings(w)
            for _ in range(5): self.assertEqual(Window.output_settings(w), first)
            self.assertEqual(warm.call_count, 2)
            w.signal_preferences['route_mode'] = SHORTEST_PATH
            Window.output_settings(w)
            self.assertEqual(warm.call_count, 4)
            w.scene.settings = replace(settings, shapes=settings.shapes[:1])  # An edit is a new settings object.
            Window.output_settings(w)
            self.assertEqual(warm.call_count, 5)

    def test_output_settings_keeps_the_redraw_notice(self):
        from phosphor.editor import qt_app
        settings = Settings(shapes=(Shape(), Shape(solid='Tetrahedron')), source='Single tone')
        Window, w = window_namespace(settings)
        w.signal_preferences['shape_redraw'] = True
        def refuse(*args): raise ValueError('budget')
        with patch.object(qt_app, 'planned_path', refuse):
            for _ in range(3):
                self.assertEqual(Window.output_settings(w).shape_redraw_count, 1)
                self.assertEqual(w.redraw_notice, 'Redraw inactive: budget')
        w.signal_preferences['shape_redraw'] = False
        self.assertEqual(Window.output_settings(w).shape_redraw_count, 1)
        self.assertEqual(w.redraw_notice, '')

    def test_output_project_never_reuses_another_projects_audition(self):
        from phosphor.editor import qt_app
        from phosphor.engine.composition import Project, Clip, COMPOSER
        def project(frequency):
            return Project(clips=(Clip(0., 4., Settings(shapes=(Shape(uid='a'), Shape(uid='b')), tone_frequency=frequency)),))
        Window, w = window_namespace(None, workspace=COMPOSER, solo_ids={'a'}, transform_solos={}, audio_solos=set(), track_solos=set(), clip_index=0)
        # The old code keyed the copy by id(project): a new project at a freed project's address
        # got the old copy. Giving every object the same id makes that reuse certain.
        with patch.object(qt_app, 'id', lambda value: 1, create=True):
            w.project = project(220.)
            old = Window.output_project(w)
            self.assertIs(Window.output_project(w), old)
            self.assertEqual(old.clips[0].settings.tone_frequency, 220.)
            w.project = project(330.)
            new = Window.output_project(w)
        self.assertEqual(new.clips[0].settings.tone_frequency, 330.)
        self.assertTrue(new.clips[0].settings.shapes[1].muted)
        self.assertIs(Window.output_project(w), new)

if __name__ == '__main__':
    unittest.main()
