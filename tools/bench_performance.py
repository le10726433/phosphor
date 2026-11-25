"""Observational hot-path benchmark. No audio device is opened or sound played.

Run from the app folder. Native editor timings measure CPU submission, not GPU
completion; VideoRenderer timings include offscreen QRhi readback and composition.
Every run uses fresh temporary preferences, even if PHOSPHOR_USER_DIR was set.
"""
import atexit
import os
import tempfile

# Must precede _path, which imports phosphor and resolves storage paths.
_user = tempfile.TemporaryDirectory(prefix='phosphor-bench-user-')
os.environ['PHOSPHOR_USER_DIR'] = _user.name
atexit.register(_user.cleanup)
import _path  # noqa: E402,F401

import argparse
from contextlib import contextmanager
from dataclasses import replace
import json
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import urllib.request

import numpy as np

from phosphor.engine.signal_engine import Settings, Shape, render, scene_plan
from phosphor.engine.model_object import model_from_obj, model_route, MODEL_OBJECT
from phosphor.engine.transforms import (new_transform, SPIN, PERSPECTIVE, MOTION_PATH, NOISE, SCALE, MAP, Param, NoiseOptions,
                                        curve_of, with_map_preset)
from phosphor.engine import noise_loops
from phosphor.engine.function_plot import PRESETS as PLOTS, Variable, PARAMETRIC_OBJECT
from phosphor.engine.recurrence import PRESETS, RecurrencePlot, compiled_recurrence, RECURRENCE_OBJECT
from phosphor.engine import recurrence_jit
from tests.fixtures.dense_mesh import dense_mesh_obj
from tests.unit.golden_scenes import dynamic_lorenz, spin

SCENARIOS = ('model_simplify', 'model_import', 'model_engine', 'editor_model', 'audio_under_edit',
             'recurrence', 'song_audio', 'composer_chords', 'noise', 'map', 'motion_path_dense', 'scene_plan_memory', 'drawing_16k', 'wobble_eviction', 'output_settings',
             'song_20k', 'roll_repaint', 'muted_outline', 'stopped_composer', 'audio_clip_decode',
             'recovery_write_20k', 'q1_complex_scene', 'q1_automated_scene', 'q1_path_window', 'q1_audio_tracks',
             'export_render', 'export_encode', 'export_end_to_end')
RESOLUTIONS = ((1920, 1080), (3840, 2160))
RATE = 48000


def elapsed(function):
    start = time.perf_counter()
    result = function()
    return result, time.perf_counter() - start


def llm_warning():
    try:
        with urllib.request.urlopen('http://127.0.0.1:11434/api/ps', timeout=1) as response:
            if json.load(response).get('models'):
                print('WARNING: a local LLM is using the GPU; display/export numbers are skewed.')
    except Exception:
        pass


class Benchmark:
    def __init__(self, args, before):
        self.args, self.before = args, before
        self.results = {}
        self.app = None
        self._model = self._song = None
        self.header = dict(platform=platform.platform(), cpu_count=os.cpu_count(),
                           python=platform.python_version(), numpy=np.__version__,
                           pyside6=self.qt_version(), graphics_api='not initialized')
        try: self.header['numba'] = version('numba')
        except PackageNotFoundError: self.header['numba'] = None

    @staticmethod
    def qt_version():
        try:
            import PySide6
            return getattr(PySide6, '__version__', 'unavailable')
        except ImportError:
            return 'unavailable'

    def row(self, scenario, metric, value, unit=''):
        self.results.setdefault(scenario, {})[metric] = value
        previous = self.before.get(scenario, {}).get(metric)
        ratio = (f'{value / previous:.3f}x' if isinstance(value, (float, int))
                 and isinstance(previous, (float, int)) and previous != 0 else '-')
        shown = f'{value:.6g}' if isinstance(value, float) else str(value)
        print(f'{scenario:20s} {metric:42s} {shown:>12s} {unit:9s}'
              + (f' {ratio:>9s}' if self.args.compare else ''), flush=True)

    def stats(self, scenario, metric, seconds, maximum=False):
        if not seconds:
            raise RuntimeError(f'No samples for {metric}')
        for suffix, value in (('median', np.median(seconds)), ('p90', np.percentile(seconds, 90))):
            self.row(scenario, f'{metric}_{suffix}', float(value) * 1000, 'ms')
        if maximum:
            self.row(scenario, f'{metric}_max', float(max(seconds)) * 1000, 'ms')

    def obj_text(self):
        return (self.args.obj.read_text(encoding='utf-8-sig', errors='replace')
                if self.args.obj else dense_mesh_obj())

    def model(self):
        if self._model is None:
            self._model = model_from_obj(self.obj_text(), self.args.obj.stem if self.args.obj else 'Dense')[0]
        return self._model

    def model_settings(self, stack=None):
        if stack is None:
            stack = (spin(), new_transform(PERSPECTIVE))
        return Settings(shapes=(Shape(solid=MODEL_OBJECT, model=self.model(), transforms=stack),),
                        source='Single tone', tone_frequency=55.)

    def qt(self):
        if self.app is None:
            from PySide6.QtGui import QSurfaceFormat
            from PySide6.QtWidgets import QApplication
            from PySide6.QtQuick import QQuickWindow
            from phosphor.editor.qt_app import configure_graphics
            fmt = QSurfaceFormat.defaultFormat()
            fmt.setSamples(4)
            QSurfaceFormat.setDefaultFormat(fmt)
            configure_graphics()
            self.app = QApplication.instance() or QApplication([])
            self.app.setQuitOnLastWindowClosed(False)
            self.header['graphics_api'] = str(QQuickWindow.graphicsApi())
        return self.app

    def loop(self, milliseconds):
        """Real Qt event loop releases the GIL; never use QTest.qWait here.

        A local QEventLoop, not app.exec(): quitting the application emits aboutToQuit,
        which makes the editor release its views (frames then return at once).
        """
        from PySide6.QtCore import QEventLoop, QTimer
        self.qt()
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(milliseconds)
        try:
            loop.exec()
        finally:
            timer.stop()

    @contextmanager
    def window(self):
        from phosphor.editor import qt_app as q
        self.qt()
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
        try:
            w.resize(1400, 900)
            w.show()
            self.loop(200)
            w.timer.stop()  # Explicit measurements/Qt edit timer drive frames.
            yield w
        finally:
            w.audio.stop()
            w.release_views()
            w.force_close = True
            w.close()
            self.app.processEvents()

    def load_editor(self, w):
        from phosphor.editor import qt_app as q
        w.scene.settings = replace(w.scene.settings, source='Single tone', tone_frequency=55.)
        def fail(message):
            raise RuntimeError(str(message))
        messages = SimpleNamespace(information=lambda parent, title, text: None)
        # --obj is read directly: never copy the user's file.
        with tempfile.TemporaryDirectory(prefix='phosphor-bench-obj-') as folder:
            path = self.args.obj
            if path is None:
                path = Path(folder) / 'dense.obj'
                path.write_text(dense_mesh_obj(), encoding='utf-8')
            w.show_panel(q.PANEL_SCENE)
            # Patch module/instance names, never native Shiboken class methods.
            model_route.cache_clear()  # Cold import: model_import may have routed this model already.
            with patch.object(q, 'QMessageBox', messages), patch.object(w, 'error', fail):
                _, seconds = elapsed(lambda: w.add_model(str(path)))
        if w.scene.shape is None or w.scene.shape.solid != MODEL_OBJECT:
            raise RuntimeError('add_model did not select a model')
        w.scene_sections.setCurrentIndex(1)
        panel = w.transform_panel
        panel.add(SPIN)
        panel.add(PERSPECTIVE)
        panel.list.setCurrentRow(0)
        field = panel.editors[SPIN][1][1]
        field.changed.emit(Param(.25))
        self.app.processEvents()
        return seconds, field

    def model_simplify(self):
        from phosphor.engine import mesh_simplify as simplify
        from phosphor.engine.model_object import parse_obj, _weld, _triangles, model_from_mesh
        scenario = 'model_simplify'
        start = time.perf_counter()
        simplify.warm_up()
        # No GUI or audio exists yet; wait on the compiler's condition without polling.
        with simplify._condition:
            simplify._condition.wait_for(lambda: simplify.status()['ready'] or simplify.status()['reason'] is not None)
        self.row(scenario, 'numba_compile_ms', (time.perf_counter()-start)*1000, 'ms')
        if not simplify.status()['ready']:
            self.row(scenario, 'numba_unavailable', simplify.status()['reason'])
            return
        cases = [('dense', dense_mesh_obj(), None)]
        if not self.args.quick:
            cases.append(('dense_200k', dense_mesh_obj(rings=312, segments=320), [10000]))
            if self.args.obj:
                cases.append((self.args.obj.stem, self.args.obj.read_text(encoding='utf-8-sig', errors='replace'), [1000, 2500]))
        for label, text, targets in cases:
            vertices, faces, lines = parse_obj(text)
            points, welded = _weld(vertices)
            tris = _triangles(faces, welded)
            self.row(scenario, f'{label}_source_triangles', len(tris))
            if targets is None:
                targets = [len(tris)//5]
            locked = np.zeros(len(points), dtype=bool)
            for line in lines:
                locked[welded[line]] = True
            for target in targets:
                prefix = f'{label}_{target}'
                result = simplify.simplify(points, tris, target, locked, backend='numba')
                self.row(scenario, prefix+'_result_triangles', result.result_triangles)
                timings = [elapsed(lambda: simplify.simplify(points, tris, target, locked, backend='numba'))[1] for _ in range(5)]
                self.stats(scenario, prefix+'_simplify', timings)
                timings = []
                for _ in range(3):
                    (model, report), seconds = elapsed(lambda: model_from_mesh(vertices, faces, lines, label, simplify_to=target))
                    timings.append(seconds)
                self.row(scenario, prefix+'_import', float(np.median(timings))*1000, 'ms')
                self.row(scenario, prefix+'_kept_edges', report.kept_edges)
                if len(tris) <= 20000:
                    _, seconds = elapsed(lambda: simplify.simplify(points, tris, target, locked, backend='python'))
                    self.row(scenario, prefix+'_python_ms', seconds*1000, 'ms')

    def model_import(self):
        text = self.obj_text()
        label = self.args.obj.stem if self.args.obj else 'dense'
        (model, _), seconds = elapsed(lambda: model_from_obj(text, label))
        self._model = model
        self.row('model_import', f'{label}_model_from_obj', seconds * 1000, 'ms')
        model_route.cache_clear()
        _, seconds = elapsed(lambda: model_route(model))
        self.row('model_import', f'{label}_model_route', seconds * 1000, 'ms')

    def model_engine(self):
        for label, stack in (('none', ()), ('spin_perspective', (spin(), new_transform(PERSPECTIVE))),
                             ('spin_motion_expression', (spin(), new_transform(MOTION_PATH)))):
            settings = self.model_settings(stack)
            # Route preparation is not pose evaluation; measured separately above.
            model_route(self.model())
            scene_plan.cache_clear()
            n = 10 if self.args.quick else 30
            _, seconds = elapsed(lambda: [scene_plan(k, 60., settings.shapes, 'No changes', 8.) for k in range(n)])
            self.row('model_engine', f'{label}_scene_plan_per_frame', seconds * 1000 / n, 'ms')
            scene_plan.cache_clear()
            _, seconds = elapsed(lambda: render(np.arange(RATE) / RATE, settings))
            self.row('model_engine', f'{label}_render_1s', seconds * 1000, 'ms')

    def editor_model(self):
        with self.window() as w:
            seconds, field = self.load_editor(w)
            self.row('editor_model', 'add_model', seconds * 1000, 'ms')
            frames = []
            for _ in range(30 if self.args.quick else 120):
                _, seconds = elapsed(w.display_frame)
                frames.append(seconds)
                self.app.processEvents()
            self.stats('editor_model', 'stopped_display_frame', frames)
            edits = []
            for k in range(10 if self.args.quick else 30):
                def edit():
                    field.changed.emit(Param(.05 * k))
                    w.display_frame()
                _, seconds = elapsed(edit)
                edits.append(seconds)
                self.app.processEvents()
            self.stats('editor_model', 'transform_edit_and_frame', edits)

    def audio_under_edit(self):
        from PySide6.QtCore import QTimer
        from phosphor.audio.audio_preview import AudioPreview
        with self.window() as w:
            _, field = self.load_editor(w)
            for editing in (False, True):
                field.changed.emit(Param(.25))
                player = AudioPreview()
                player.parameters = (w.output_settings(), 'mixed')
                blocks, lateness, errors = [], [], []
                original = player._next_block

                def timed_block(count):
                    value, seconds = elapsed(lambda: original(count))
                    blocks.append(seconds)
                    return value

                # Instance-only wrapper: no production hook or class monkeypatch.
                player._next_block = timed_block

                class PacedStream:
                    def __init__(self):
                        self.deadline = None

                    def write(self, block):
                        now = time.perf_counter()
                        if self.deadline is None:
                            self.deadline = now + len(block) / RATE
                        lateness.append(max(0., now - self.deadline))
                        time.sleep(max(0., self.deadline - time.perf_counter()))
                        self.deadline += len(block) / RATE
                        return False

                    def close(self):
                        pass

                timer = QTimer()
                k = 0

                def edit():
                    nonlocal k
                    try:
                        field.changed.emit(Param(.05 * (k % 30)))
                        player.parameters = (w.output_settings(), 'mixed')
                        w.display_frame()
                        k += 1
                    except Exception as error:
                        errors.append(error)
                        timer.stop()

                timer.timeout.connect(edit)
                player.thread = threading.Thread(target=player._run, args=(PacedStream(),))
                try:
                    player.thread.start()
                    if editing:
                        timer.start(16)
                    self.loop(1500 if self.args.quick else 3000)
                finally:
                    timer.stop()
                    player.stop()
                if player.error or errors:
                    raise RuntimeError(player.error or str(errors[0]))
                label = 'editing' if editing else 'idle'
                self.stats('audio_under_edit', f'{label}_block_render', blocks, maximum=True)
                self.row('audio_under_edit', f'{label}_late_writes', sum(x > 0 for x in lateness), 'writes')
                self.row('audio_under_edit', f'{label}_worst_lateness', max(lateness, default=0.) * 1000, 'ms')

    def recurrence(self):
        from phosphor.engine import recurrence as rc
        cases = dict(lorenz_static=PRESETS['Lorenz attractor'], lorenz_dynamic=dynamic_lorenz(),
                     clifford_dynamic=replace(PRESETS['Clifford attractor'], steps=4096,
                         x='(sin(a*y*3)+1.6*cos(a*x*3))/3',
                         variables=(Variable('a', -1.4, -3., 3., expression='-1.4+0.2*sin(t)'),)),
                     order8=RecurrencePlot(
                         'clip(0.5*x[n-1]+0.3*sin(3*x[n-4])-0.2*x[n-8]+0.1*cos(n*0.01+t)*y[n-2], -1, 1)',
                         'clip(0.5*y[n-1]+0.3*cos(3*y[n-3])-0.2*y[n-7]+0.1*sin(n*0.013+t)*x[n-5], -1, 1)',
                         '0', order=8, initial=tuple((.1*k, .05*k, 0.) for k in range(1, 9)), steps=4096))
        for label, plot in cases.items():
            rc._sequence.cache_clear()
            rc._static_sequence.cache_clear()
            compiled = compiled_recurrence(plot)
            n = 5 if self.args.quick else 20
            if compiled.dynamic and compiled.kernel_source is not None:
                # A harmless unique comment ensures a cold source-cache entry even
                # when an earlier benchmark has already requested the same plot.
                source = compiled.kernel_source + f'# benchmark {time.perf_counter_ns()}\n'
                compiled.kernel_source = source
                start = time.perf_counter()
                recurrence_jit.request(source)
                recurrence_jit.wait(source)
                self.row('recurrence', f'{label}_first_compile', (time.perf_counter()-start)*1000, 'ms')
                _, seconds = elapsed(lambda: [compiled.evaluate(k / 60, 120., jit=False) for k in range(n)])
                self.row('recurrence', f'{label}_interpreter_per_pose', seconds * 1000 / n, 'ms')
            _, seconds = elapsed(lambda: [compiled.evaluate(k / 60, 120.) for k in range(n)])
            self.row('recurrence', f'{label}_evaluate_per_pose', seconds * 1000 / n, 'ms')
            rc._sequence.cache_clear()
            rc._static_sequence.cache_clear()
            scene_plan.cache_clear()
            settings = Settings(shapes=(Shape(solid=RECURRENCE_OBJECT, recurrence=plot),),
                                source='Single tone', tone_frequency=55.)
            _, seconds = elapsed(lambda: render(np.arange(RATE) / RATE, settings))
            self.row('recurrence', f'{label}_render_1s', seconds * 1000, 'ms')

    def song_settings(self):
        if self._song is None:
            model = model_from_obj(dense_mesh_obj(rings=30, segments=30), 'Mixed dense')[0]
            model_route(model)
            self._song = Settings(shapes=(Shape(solid=MODEL_OBJECT, model=model, transforms=(spin(),)),
                Shape(solid=RECURRENCE_OBJECT, recurrence=dynamic_lorenz()),
                Shape(solid=PARAMETRIC_OBJECT, plot=PLOTS['Trefoil knot'])))
        return self._song

    def blocks(self, duration, stop=None):
        """Audio blocks as the editor's export streams them (Window.export_video)."""
        settings = self.song_settings()
        count = round(duration * RATE)
        for start in range(0, count, RATE):
            if stop is not None and stop.is_set():
                return
            yield render(np.arange(start, min(start + RATE, count)) / RATE, settings)[2]

    def prepare(self, duration, stop=None):
        blocks = list(self.blocks(duration, stop))
        return np.concatenate(blocks) if blocks and not (stop is not None and stop.is_set()) else np.empty((0, 2))

    def song_audio(self):
        self.song_settings()
        scene_plan.cache_clear()
        duration = 2 if self.args.quick else 10
        _, seconds = elapsed(lambda: self.prepare(duration))
        self.row('song_audio', 'compute_per_audio_second', seconds / duration, 's/s')

    def composer_chords(self):
        from phosphor.engine.composition import Project, Note, Clip, render_song
        from tests.unit.golden_scenes import scene
        times = np.arange(RATE) / RATE
        costs = []
        for count in (1, 8):
            project = Project(notes=tuple(Note(0., 4., pitch) for pitch in
                (48, 52, 55, 60, 64, 67, 72, 76)[:count]),
                clips=(Clip(0., 4., scene('cube')),), chords=True)
            scene_plan.cache_clear()
            repeats = 1 if self.args.quick else 5
            timings = [elapsed(lambda: render_song(times, project, Settings()))[1]
                       for _ in range(repeats)]
            cost = float(np.median(timings)); costs.append(cost)
            self.row('composer_chords', f'{count}_voice_compute_per_audio_second', cost, 's/s')
        self.row('composer_chords', 'eight_to_one_ratio', costs[1]/costs[0], 'x')

    def noise_cases(self):
        """Label -> transform stack after the cube's tilt and spin; None is the noiseless baseline."""
        cases = dict(none=None, white=(new_transform(NOISE),))
        for label, options in (('pink', NoiseOptions('Pink')), ('brownian', NoiseOptions('Brownian')),
                               ('custom', NoiseOptions('Custom', curve=((20., 0.), (200., -6.), (1000., -3.), (4000., -24.), (20000., -30.)))),
                               ('wobble_pink', NoiseOptions('Pink', 'Wobble'))):
            step = replace(new_transform(NOISE), noise=options)
            if label == 'wobble_pink': step = step.with_param('intensity', Param(.05))
            cases[label] = (step,)
        return cases

    def noise(self):
        from tests.unit.golden_scenes import scene
        for label, stack in self.noise_cases().items():
            if label in ('none', 'wobble_pink'): continue
            noise_loops.clear()
            _, seconds = elapsed(lambda: noise_loops.get(curve_of(stack[0].noise)))
            self.row('noise', f'{label}_loop_build_ms', seconds * 1000, 'ms')
        cube = scene('cube')
        block = 512
        blocks = [np.arange(start, start + block) / RATE for start in range(0, RATE, block)]
        costs = {}
        for label, extra in self.noise_cases().items():
            settings = cube if extra is None else replace(cube, shapes=(replace(
                cube.shapes[0], transforms=cube.shapes[0].transforms + extra),))
            render(blocks[0], settings)  # Builds any noise loop outside the timing.
            repeats = 2 if self.args.quick else 7
            timings = []
            for _ in range(repeats):
                scene_plan.cache_clear()
                timings.append(elapsed(lambda: [render(times, settings) for times in blocks])[1])
            costs[label] = float(np.median(timings))
            self.row('noise', f'{label}_compute_per_audio_second', costs[label], 's/s')
            if label != 'none':
                self.row('noise', f'{label}_extra_per_512_block', (costs[label] - costs['none']) * 1000 / len(blocks), 'ms')

    def map(self):
        """Map on the dense model, per pose frame, against a three-axis Scale expression."""
        scale = new_transform(SCALE)
        for axis in 'xyz': scale = scale.with_param(axis, Param(1., '1 + 0.2*sin(tau*t + 3*y)'))
        cases = (('spin_only', ()), ('scale_expression', (scale,)), ('map_default', (new_transform(MAP),)),
                 ('map_wave', (with_map_preset(new_transform(MAP), 'Wave'),)),
                 ('map_spherize', (with_map_preset(new_transform(MAP), 'Spherize'),)),
                 ('map_twist', (with_map_preset(new_transform(MAP), 'Twist'),)))
        self.row('map', 'model_points', len(self.model().vertices))
        model_route(self.model())
        frames = 10 if self.args.quick else 40
        repeats = 2 if self.args.quick else 5
        for label, extra in cases:
            settings = self.model_settings((spin(), *extra))
            timings = []
            for _ in range(repeats):
                scene_plan.cache_clear()
                timings.append(elapsed(lambda: [scene_plan(k, 60., settings.shapes, 'No changes', 8.) for k in range(frames)])[1])
            self.row('map', f'{label}_scene_plan_per_frame', float(np.median(timings)) * 1000 / frames, 'ms')

    # Audit stage P7b (planning/agent_recommendations.md P4, P5, P6, P8, P10).
    def motion_path_dense(self):
        """P4: an expression that reads s on a dense model (120 x 120 rings: about 25,000 route points)."""
        from phosphor.engine.signal_engine import shape_geometry
        from phosphor.engine.transforms import route_positions
        model = model_from_obj(dense_mesh_obj(120, 120), 'Dense 120')[0]
        vertices, _, route = shape_geometry(Shape(solid=MODEL_OBJECT, model=model))
        self.row('motion_path_dense', 'route_points', len(route))
        repeats = 10 if self.args.quick else 50
        self.stats('motion_path_dense', 'route_positions', [elapsed(lambda: route_positions(vertices, route))[1]
                                                            for _ in range(repeats)])
        scale = new_transform(SCALE).with_param('x', Param(1., '1 + 0.1*sin(tau*(t + s))'))
        frames = 10 if self.args.quick else 40
        for label, stack in (('spin_only', (spin(),)), ('spin_s_expression', (spin(), scale))):
            shapes = (Shape(solid=MODEL_OBJECT, model=model, transforms=stack),)
            timings = []
            for _ in range(2 if self.args.quick else 5):
                scene_plan.cache_clear()
                timings.append(elapsed(lambda: [scene_plan(k, 60., shapes, 'No changes', 8.) for k in range(frames)])[1])
            self.row('motion_path_dense', f'{label}_scene_plan_per_frame', float(np.median(timings)) * 1000 / frames, 'ms')

    def scene_plan_memory(self):
        """P5: traced memory after five 1 s renders of a spinning 18,796-edge model (the audit's fixture)."""
        import gc
        import tracemalloc
        model = model_from_obj(dense_mesh_obj(120, 120), 'Dense 120')[0]
        settings = Settings(shapes=(Shape(solid=MODEL_OBJECT, model=model, transforms=(spin(),)),),
                            source='Single tone', tone_frequency=55.)
        render(np.arange(512) / RATE, settings)  # Route, validation and imports outside the trace.
        self.row('scene_plan_memory', 'model_edges', len(model.edges))
        scene_plan.cache_clear(); gc.collect()
        tracemalloc.start()
        try:
            before = tracemalloc.get_traced_memory()[0]
            _, seconds = elapsed(lambda: [render(second + np.arange(RATE) / RATE, settings) for second in range(5)])
            gc.collect()
            after, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.row('scene_plan_memory', 'retained_after_5s_mib', (after - before) / 2**20, 'MiB')
        self.row('scene_plan_memory', 'peak_mib', (peak - before) / 2**20, 'MiB')
        self.row('scene_plan_memory', 'cache_entries', scene_plan.cache_info().currsize)
        self.row('scene_plan_memory', 'render_5s_traced', seconds, 's')
        scene_plan.cache_clear()

    @staticmethod
    def spiral_drawing(count):
        from phosphor.engine.custom_shape import Drawing
        angle = np.linspace(0., 40. * np.pi, count); radius = np.linspace(.1, .9, count)
        nodes = tuple((k, float(r * np.cos(a)), float(r * np.sin(a))) for k, (a, r) in enumerate(zip(angle, radius)))
        return Drawing(f'Spiral {count}', nodes, tuple((k, k + 1) for k in range(count - 1)))

    def drawing_16k(self):
        """P6: Drawing hashing per lookup, validate() and a 512-sample block of a spinning polyline drawing."""
        from phosphor.engine.custom_shape import CUSTOM_OBJECT
        from phosphor.engine.signal_engine import validate
        repeats = 20 if self.args.quick else 200
        for count in (2000, 8000, 16000):
            drawing = self.spiral_drawing(count)
            hash(drawing)  # A cached hash is computed on first use; time repeated lookups.
            self.row('drawing_16k', f'{count}_hash', float(np.median([elapsed(lambda: hash(drawing))[1]
                                                                      for _ in range(repeats)])) * 1000, 'ms')
            settings = Settings(shapes=(Shape(solid=CUSTOM_OBJECT, custom=drawing, transforms=(spin(),)),),
                                source='Single tone', tone_frequency=55.)
            render(np.arange(512) / RATE, settings)
            self.row('drawing_16k', f'{count}_validate', float(np.median([elapsed(lambda: validate(settings))[1]
                                                                          for _ in range(repeats)])) * 1000, 'ms')
            blocks = [np.arange(start, start + 512) / RATE for start in range(0, RATE, 512)]
            # Consecutive blocks, as playback runs them (a pose frame spans about 1.5 blocks).
            _, seconds = elapsed(lambda: [render(times, settings) for times in blocks])
            self.row('drawing_16k', f'{count}_render_per_512_block', seconds * 1000 / len(blocks), 'ms')

    # P7a: editor and Composer responsiveness ---------------------------------------------
    @staticmethod
    def big_song(count, glide=False, chords=False):
        """count notes (four-voice chords every half beat over 7 octaves) in one long cube clip."""
        from phosphor.engine.composition import Project, Note, Clip
        from tests.unit.golden_scenes import scene
        notes = []
        for k in range(count):
            chord, voice = divmod(k, 4)
            notes.append(Note(chord * .5, .75, 36 + (chord * 5 + voice * 4) % 60, .5 + .5 * (k % 3) / 2))
        if glide:  # One legato glide note in the middle of the song.
            middle = len(notes) // 2
            notes[middle] = replace(notes[middle], glide=.25)
        end = max(n.end for n in notes)
        return Project(notes=tuple(notes), clips=(Clip(0., end + 4., scene('cube')),), chords=chords, velocity=True)

    def song_20k(self):
        """P2: render_song per 512-sample block in a 20,000-note song (mono or chords, with one glide)."""
        from phosphor.engine.composition import render_song
        repeats = 20 if self.args.quick else 100
        for chords in (False, True):
            for glide in (False, True):
                project = self.big_song(20000, glide, chords)
                label = ('chords' if chords else 'mono') + ('_glide' if glide else '')
                machine = Settings()
                start = 10. * RATE
                render_song((start + np.arange(512)) / RATE, project, machine)  # Warm caches, as playback is.
                timings = [elapsed(lambda b=b: render_song((start + b * 512 + np.arange(512)) / RATE, project, machine))[1]
                           for b in range(1, repeats + 1)]
                self.stats('song_20k', f'{label}_block', timings)
        notes = self.big_song(20000).notes
        self.row('song_20k', 'hash_notes', float(np.median([elapsed(lambda: hash(notes))[1] for _ in range(20)])) * 1000, 'ms')
        self.q1_dense()

    # Q1 stress cases (planning/review-q1.md; generators in tools/q1_workloads.py) ----------
    def playback_blocks(self, scenario, metric, project, rate, start=10., repeats=None):
        """Consecutive 10 ms playback blocks (playback_block_frames) from song second start, with
        one ChainState and wait_media=False, as AudioPreview._song_block renders them."""
        from phosphor.audio.audio_preview import playback_block_frames
        from phosphor.engine.composition import render_song
        from phosphor.engine.track_chain import ChainState
        machine, size, state = replace(Settings(), sample_rate=rate), playback_block_frames(rate), ChainState()
        first = round(start * rate)
        def block(b):
            return render_song((first + b * size + np.arange(size)) / rate, project, machine,
                               wait_media=False, chain_state=state)
        block(0)  # Warm caches and the chain, as the first block of playback does.
        repeats = repeats or (30 if self.args.quick else 200)
        self.stats(scenario, metric, [elapsed(lambda b=b: block(b))[1] for b in range(1, repeats + 1)], maximum=True)

    def export_chunks(self, scenario, metric, project, start=10., rate=RATE):
        """One-second chunks as export_song writes them: consecutive, one ChainState, waiting for media."""
        from phosphor.engine.composition import render_song
        from phosphor.engine.track_chain import ChainState
        machine, state = replace(Settings(), sample_rate=rate), ChainState()
        repeats = 3 if self.args.quick else 10
        self.stats(scenario, metric, [elapsed(lambda b=b: render_song(start + b + np.arange(rate) / rate, project, machine,
                                                                      chain_state=state))[1] for b in range(repeats)])

    def q1_dense(self):
        """Q1 note density (song_20k's q1_ metrics): 20,000 seeded 3-4 note chords with velocity over
        8 minutes (2,000 over 48 s with --quick): 10 ms playback blocks at 48 and 96 kHz, an export
        chunk, and the score helpers the editor calls per frame or per playhead move."""
        import q1_workloads as q1
        from phosphor.editor.composer_view import position_text
        from phosphor.engine.composition import clip_settings, cycle_span, song_cycle
        scenario = 'song_20k'
        project = q1.dense_song(self.args.quick)
        self.row(scenario, 'q1_notes', len(project.notes))
        self.row(scenario, 'q1_song_seconds', float(project.seconds(project.end_beats)), 's')
        for rate in (48000, 96000):
            scene_plan.cache_clear()
            self.playback_blocks(scenario, f'q1_block_{rate // 1000}k', project, rate)
        scene_plan.cache_clear()
        self.export_chunks(scenario, 'q1_export_chunk_1s', project)
        repeats = 50 if self.args.quick else 200
        copies = [replace(project) for _ in range(repeats)]  # New objects: end_beats scans again.
        self.stats(scenario, 'q1_end_beats_uncached', [elapsed(lambda p=p: p.end_beats)[1] for p in copies])
        self.stats(scenario, 'q1_end_beats_cached', [elapsed(lambda: project.end_beats)[1] for _ in range(repeats)])
        self.stats(scenario, 'q1_position_text', [elapsed(lambda k=k: position_text(project, 20. + k / 30))[1]
                                                  for k in range(repeats)])
        self.stats(scenario, 'q1_cycle_span_moving', [elapsed(lambda k=k: cycle_span(project, 10. + k / 60))[1]
                                                      for k in range(repeats)])
        machine = Settings()
        self.stats(scenario, 'q1_clip_settings', [elapsed(lambda: clip_settings(project.clips[0], machine, project))[1]
                                                  for _ in range(repeats)])
        cycles = 5 if self.args.quick else 20  # Each playhead move renders a whole cycle (stopped Composer, cycle on).
        self.stats(scenario, 'q1_song_cycle_moving', [elapsed(lambda k=k: song_cycle(project, 10. + k / 60, machine))[1]
                                                      for k in range(cycles)])

    def composer_frames(self, scenario, prefix, project):
        """Stopped-Composer display frames (Qt): render_song calls per 100 ticks and frame cost at a
        fixed playhead, then with the playhead moving 1/60 s per frame (stopped_signal renders anew)."""
        from phosphor.editor import qt_app as q
        with self.window() as w:
            w.set_workspace(q.COMPOSER)
            w.load_project(project, None)
            w.composer_panel.cycle.setChecked(False)
            calls = []
            original = q.render_song
            def counted(*args, **kwargs):
                calls.append(1); return original(*args, **kwargs)
            w.playhead = 10.
            ticks = 100
            with patch('phosphor.editor.qt_app.render_song', counted):
                timings = [elapsed(w.display_frame)[1] for _ in range(ticks)]
            self.row(scenario, f'{prefix}renders_per_100_ticks', len(calls))
            self.stats(scenario, f'{prefix}display_frame', timings)
            timings = []
            for k in range(ticks):
                w.playhead = 10. + (k + 1) / 60
                timings.append(elapsed(w.display_frame)[1])
            self.stats(scenario, f'{prefix}display_moving_playhead', timings, maximum=True)

    def q1_scene(self, scenario, automation):
        """The Q1 complex scene in one Composer clip (24 objects, 6 with --quick): clip_settings,
        scene_plan cold and warm, posed_shapes (the overlay's Qt-free part), 10 ms playback blocks
        at 48 and 96 kHz, then stopped-Composer display frames (Qt)."""
        import q1_workloads as q1
        from phosphor.engine.composition import clip_settings
        from phosphor.engine.signal_engine import posed_shapes, scene_shapes
        project = q1.complex_project(self.args.quick, automation)
        clip, machine = project.clips[0], Settings()
        shapes = clip.settings.shapes
        self.row(scenario, 'objects', len(shapes))
        self.row(scenario, 'transform_steps', sum(len(s.transforms) for s in shapes))
        self.row(scenario, 'automated_values', sum(
            len(s.lanes) + sum(bool(p.points) for t in s.transforms for p in t.params) + bool(s.path_start.points)
            + bool(s.path_trace.points) + sum(bool(v.points) for v in s.transform_variables)
            + sum(bool(v.points) for v in (s.plot.variables if s.plot else ())) for s in shapes) + len(clip.lanes))
        repeats = 20 if self.args.quick else 100
        self.stats(scenario, 'clip_settings', [elapsed(lambda: clip_settings(clip, machine, project))[1] for _ in range(repeats)])
        settings = clip_settings(clip, machine, project)
        planned = scene_shapes(settings)
        def plan(k):
            return scene_plan(k, settings.drawing_hz, planned, settings.travel_mode, settings.travel_speed,
                              settings.motion_time_offset, settings.shape_redraw_count, settings.route_mode, settings.tempo)
        frames = range(600, 600 + (10 if self.args.quick else 60))  # From 10 s at 60 Hz.
        scene_plan.cache_clear()
        self.stats(scenario, 'scene_plan_cold', [elapsed(lambda k=k: plan(k))[1] for k in frames], maximum=True)
        self.stats(scenario, 'scene_plan_warm', [elapsed(lambda k=k: plan(k))[1] for k in frames])
        self.stats(scenario, 'posed_shapes', [elapsed(lambda k=k: posed_shapes(settings.shapes, k / settings.drawing_hz))[1]
                                              for k in frames])
        for rate in (48000, 96000):
            scene_plan.cache_clear()
            self.playback_blocks(scenario, f'render_block_{rate // 1000}k', project, rate)
        scene_plan.cache_clear()
        self.composer_frames(scenario, '', project)

    def q1_complex_scene(self):
        """Q1 complex scene, nothing automated."""
        self.q1_scene('q1_complex_scene', 'static')

    def q1_automated_scene(self):
        """Q1 complex scene with every object variable, transform number, Path Start/Trace, size and
        clip gain on lanes, plus four tempo changes: compare with q1_complex_scene."""
        self.q1_scene('q1_automated_scene', 'all')

    def q1_path_window(self):
        """Time-varying Path Start/Trace (former performance Phase 6) on the Q1 complex scene: compare
        with q1_complex_scene, whose start and trace are the same constants."""
        self.q1_scene('q1_path_window', 'path')

    def q1_audio_tracks(self):
        """Eight audio tracks (4 with --quick), 2-3 automated effects each, pan and gain lanes, three
        16 s clips per track: 10 ms playback blocks at 48 and 96 kHz, the first block after a seek
        (the chain primes itself), cold starts, start and loop warm-up, the warmed first block,
        and a one-second export chunk."""
        import q1_workloads as q1
        from phosphor.audio.audio_preview import AudioPreview, playback_block_frames
        from phosphor.engine.audio_clips import clear_decoded, decoded, processing_tail, warm_chains
        from phosphor.engine.composition import render_song
        from phosphor.engine.track_chain import ChainState
        scenario = 'q1_audio_tracks'
        with tempfile.TemporaryDirectory(prefix='phosphor-bench-q1-audio-') as folder:
            project = q1.audio_project(folder, self.args.quick)
            looped = replace(project, loop=True, loop_start=2., loop_end=96.)
            player = AudioPreview()
            self.row(scenario, 'tracks', len(project.audio_tracks))
            self.row(scenario, 'clips', len(project.audio_clips))
            self.row(scenario, 'effects', sum(len(t.effects) for t in project.audio_tracks))
            self.stats(scenario, 'processing_tail', [elapsed(lambda: processing_tail(project))[1] for _ in range(20)])
            seeks = 10 if self.args.quick else 40
            try:
                for rate in (48000, 96000):
                    label = f'{rate // 1000}k'
                    for clip in project.audio_clips: decoded(clip.path, clip.fingerprint, rate)  # Primed before playback.
                    self.playback_blocks(scenario, f'block_{label}', project, rate)
                    machine, size = replace(Settings(), sample_rate=rate), playback_block_frames(rate)
                    def block(position, state, song=project):
                        first = round(position * rate)
                        return render_song((first + np.arange(size)) / rate, song, machine,
                                           wait_media=False, chain_state=state)
                    state = ChainState()
                    block(10., state)
                    positions = [2. + (7.3 * j) % 40. for j in range(seeks)]
                    self.stats(scenario, f'seek_first_block_{label}',
                               [elapsed(lambda p=p: block(p, state))[1] for p in positions], maximum=True)
                    self.stats(scenario, f'start_first_block_{label}',
                               [elapsed(lambda p=p: block(p, ChainState()))[1] for p in positions], maximum=True)
                    warming, first_blocks = [], []
                    for position in positions:
                        state = ChainState()
                        position = round(position * rate) / rate
                        origins = player._chain_origins(looped, position, rate)
                        warming.append(elapsed(lambda: warm_chains(looped, origins, rate,
                                                                   machine.switch_sweep_ms, state))[1])
                        first_blocks.append(elapsed(lambda: block(position, state, looped))[1])
                    self.stats(scenario, f'warm_start_{label}', warming, maximum=True)
                    self.stats(scenario, f'warmed_first_block_{label}', first_blocks, maximum=True)
                self.export_chunks(scenario, 'export_chunk_1s', project)
            finally:
                clear_decoded()

    def roll_view(self, count, audio_path=None):
        from phosphor.editor.composer_view import TimelineView
        from phosphor.engine.audio_clips import audio_clip_from_file, decoded
        project = self.big_song(count, chords=True)
        if audio_path is not None:
            clip = audio_clip_from_file(audio_path, 0.)
            decoded(clip.path, clip.fingerprint, RATE)
            project = replace(project, audio_clips=(clip,))
        view = TimelineView(); view.resize(1600, 700)
        view.set_project(project, 0)
        return view

    def roll_repaint(self):
        """P1: the note roll's paint (forced full, and the moving playhead of playback).

        The playhead step is set_playhead plus the event loop's paint and flush, as during playback.
        """
        from PySide6.QtGui import QImage
        self.qt()
        import soundfile
        with tempfile.TemporaryDirectory(prefix='phosphor-bench-roll-') as folder:
            wav = Path(folder) / 'tone.wav'
            t = np.arange(60 * RATE) / RATE
            soundfile.write(wav, np.column_stack((np.sin(2 * np.pi * 110 * t), np.sin(2 * np.pi * 165 * t))) * .5, RATE)
            repeats = 10 if self.args.quick else 40
            for count in (2000, 20000):
                view = self.roll_view(count, wav)
                view.scroll = 8.; view.top_pitch = 96
                view.show(); self.loop(200)
                image = QImage(view.size(), QImage.Format.Format_ARGB32_Premultiplied)
                def full():
                    view.update()  # Invalidates any static cache.
                    view.render(image)
                full()
                self.stats('roll_repaint', f'{count}_full_paint', [elapsed(full)[1] for _ in range(repeats)])
                visible = getattr(view, 'visible_notes')
                self.stats('roll_repaint', f'{count}_visible_notes', [elapsed(visible)[1] for _ in range(repeats)])
                timings = []
                for k in range(repeats * 3):
                    view.set_playhead(10. + .02 * k)
                    timings.append(elapsed(self.app.processEvents)[1])
                self.stats('roll_repaint', f'{count}_playhead_step', timings[5:])
                view.close(); view.deleteLater(); self.app.processEvents()

    def muted_outline(self):
        """P3: the editor overlay with a muted dense model (its faint outline, edge by edge before P7a)."""
        with self.window() as w:
            shape = Shape(solid=MODEL_OBJECT, model=self.model(), transforms=(spin(),), muted=True)
            w.scene.settings = replace(w.scene.settings, shapes=(shape,))
            self.row('muted_outline', 'edges', len(self.model().edges))
            w.overlay(0.)
            repeats = 5 if self.args.quick else 30
            self.stats('muted_outline', 'overlay', [elapsed(lambda k=k: w.overlay(k / 60.))[1] for k in range(repeats)])

    def stopped_composer(self):
        """P7: display frames while Composer is stopped: render_song calls per 100 ticks and frame cost."""
        from phosphor.editor import qt_app as q
        with self.window() as w:
            w.set_workspace(q.COMPOSER)
            w.load_project(self.big_song(2000, chords=True), None)
            w.composer_panel.cycle.setChecked(False)
            calls = []
            original = q.render_song
            def counted(*args, **kwargs):
                calls.append(1); return original(*args, **kwargs)
            w.playhead = 1.  # A new playhead: the first of the 100 ticks renders.
            with patch('phosphor.editor.qt_app.render_song', counted):
                timings = [elapsed(w.display_frame)[1] for _ in range(100)]
            self.row('stopped_composer', 'renders_per_100_ticks', len(calls))
            self.stats('stopped_composer', 'display_frame', timings)
        # Q1: the dense chord song (20,000 notes; 2,000 with --quick), fixed and moving playhead.
        import q1_workloads as q1
        self.composer_frames('stopped_composer', 'q1_dense_', q1.dense_song(self.args.quick))

    def audio_clip_decode(self):
        """P9: decoding a 5-minute 44.1 kHz WAV (resampled to 48 kHz and not), a cold block on the
        audio thread, and file reads per block with 17 inactive clips."""
        import gc
        import tracemalloc
        import soundfile
        from phosphor.engine import audio_clips
        from phosphor.engine.audio_clips import audio_clip_from_file, decoded, clear_decoded
        from phosphor.engine.composition import Project, Clip
        from phosphor.audio.audio_preview import AudioPreview
        from tests.unit.golden_scenes import scene
        seconds = 30 if self.args.quick else 300
        with tempfile.TemporaryDirectory(prefix='phosphor-bench-decode-') as folder:
            wav = Path(folder) / 'long.wav'
            t = np.arange(seconds * 44100) / 44100
            soundfile.write(wav, (np.column_stack((np.sin(2 * np.pi * 110 * t), np.sin(2 * np.pi * 165 * t))) * .5), 44100, subtype='PCM_16')
            del t
            clip = audio_clip_from_file(wav, 0.)
            for rate in (48000, 44100):
                clear_decoded(); gc.collect()
                tracemalloc.start()
                try:
                    _, spent = elapsed(lambda: decoded(clip.path, clip.fingerprint, rate))
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
                self.row('audio_clip_decode', f'decode_{rate}', spent, 's')
                self.row('audio_clip_decode', f'decode_{rate}_peak_mib', peak / 2**20, 'MiB')
            # A cold Play: the first 512-sample block on the audio thread.
            project = Project(clips=(Clip(0., 1000., scene('cube')),), audio_clips=(clip,))
            preview = AudioPreview()
            machine = replace(Settings(), sample_rate=48000)
            clear_decoded()
            _, spent = elapsed(lambda: preview._song_block(np.arange(512) / 48000, machine, (project, 0.)))
            self.row('audio_clip_decode', 'cold_audio_thread_block', spent * 1000, 'ms')
            if hasattr(audio_clips, 'wait_idle'): audio_clips.wait_idle()  # P7a: the worker's queued decode.
            # Seventeen files, none sounding in the block.
            paths = []
            for k in range(17):
                path = Path(folder) / f'short{k}.wav'
                soundfile.write(path, np.full((4800, 2), .1 * (k + 1) / 17), 48000); paths.append(path)
            clips = tuple(replace(audio_clip_from_file(p, 400. + k), start=400. + 4 * k) for k, p in enumerate(paths))
            project = Project(clips=(Clip(0., 1000., scene('cube')),), audio_clips=clips)
            reads = []
            # Count file decodes: P7a's decoder reads through audio_clips._read, older code soundfile.read.
            name = 'phosphor.engine.audio_clips._read' if hasattr(audio_clips, '_read') else 'soundfile.read'
            real = audio_clips._read if hasattr(audio_clips, '_read') else soundfile.read
            def counting(*args, **kwargs):
                reads.append(1); return real(*args, **kwargs)
            from phosphor.engine.composition import render_song
            with patch(name, counting):
                for b in range(10): render_song((b * 512 + np.arange(512)) / 48000, project, machine)
            self.row('audio_clip_decode', 'inactive_17_reads_per_block', len(reads) / 10)
            clear_decoded()

    def wobble_eviction(self):
        """P10: a pose frame on the realtime thread whose Wobble loop the Custom LRU evicted."""
        curves = [((20., 0.), (1000., -3. * k), (20000., -30.)) for k in range(noise_loops.MAX_CUSTOM + 1)]
        def stack(curve):
            step = replace(new_transform(NOISE), noise=NoiseOptions('Custom', 'Wobble', curve=curve))
            return (spin(), step.with_param('intensity', Param(.05)))
        from tests.unit.golden_scenes import scene
        cube = scene('cube')
        settings = replace(cube, shapes=(replace(cube.shapes[0], transforms=cube.shapes[0].transforms + stack(curves[0])),))
        repeats = 1 if self.args.quick else 3
        for realtime in (False, True):
            timings = []
            for _ in range(repeats):
                noise_loops.clear()
                for curve in curves: noise_loops.get(curve)  # The fifth build evicts the first.
                scene_plan.cache_clear()
                noise_loops.set_realtime(realtime)
                try: timings.append(elapsed(lambda: render(np.arange(512) / RATE, settings))[1])
                finally: noise_loops.set_realtime(False)
            label = 'realtime' if realtime else 'export'
            self.row('wobble_eviction', f'{label}_first_block_after_eviction', float(np.median(timings)) * 1000, 'ms')
        noise_loops.clear()

    def output_settings(self):
        """P8: Window.output_settings per display frame with a dense model and a 16,000-node drawing.

        first_call is the cold warm-up (it compiles the drawing's components); 0.71.0's
        Drawing.connected made it take hours for one 16,000-node chain.
        """
        from phosphor.engine.custom_shape import CUSTOM_OBJECT
        with self.window() as w:
            shapes = (Shape(solid=MODEL_OBJECT, model=self.model(), transforms=(spin(),)),
                      Shape(solid=CUSTOM_OBJECT, custom=self.spiral_drawing(16000), position_x=.5))
            w.scene.settings = replace(w.scene.settings, shapes=shapes)
            self.row('output_settings', 'first_call', elapsed(w.output_settings)[1] * 1000, 'ms')
            repeats = 50 if self.args.quick else 300
            self.stats('output_settings', 'call', [elapsed(w.output_settings)[1] for _ in range(repeats)])

    def recovery_write_20k(self):
        """P12: one autosave tick of a 20,000-note project (Astra's fixture, about 1.3 MB).

        sync_write is the old GUI-thread cost (write_recovery + save_project). With the
        0.76.0 worker, window_tick is what Window.autosave_now costs the GUI thread (the
        history commit, the snapshot and the hand-off); worker_write is the background
        write; gui_wake_delay_during_write is the worst lateness of a GUI thread that sleeps
        2 ms at a time (timer slack plus waiting for the GIL while the worker serializes).
        Before 0.76.0 the GUI thread was blocked for all of sync_write.
        """
        from phosphor.engine.composition import COMPOSER, Note, new_project
        from phosphor.storage import autosave
        from phosphor.storage.project_files import save_project
        project = replace(new_project(), notes=tuple(Note(k * .25, .24, 60 + k % 8) for k in range(20000)))
        repeats = 3 if self.args.quick else 7
        with tempfile.TemporaryDirectory(prefix='phosphor-bench-recovery-') as folder:
            root = Path(folder)
            self.stats('recovery_write_20k', 'sync_write', [elapsed(lambda: autosave.write_recovery(
                root/'sync', COMPOSER, lambda path: save_project(path, project)))[1] for _ in range(repeats)])
            self.row('recovery_write_20k', 'file_size',
                     autosave.recovery_path(root/'sync', os.getpid(), COMPOSER).stat().st_size / 1e6, 'MB')
            writer_class = getattr(autosave, 'RecoveryWriter', None)
            if writer_class is not None:
                idle = []
                for _ in range(60):
                    start = time.perf_counter(); time.sleep(.002); idle.append(time.perf_counter() - start - .002)
                self.stats('recovery_write_20k', 'gui_wake_delay_idle', idle, maximum=True)
                writer = writer_class()
                try:
                    submits, gaps, writes = [], [], []
                    for k in range(repeats):
                        revision = replace(project, bpm=100. + k)
                        start = time.perf_counter()
                        job = writer.submit(root/'worker', COMPOSER, revision, None)
                        last, gap = time.perf_counter(), 0.
                        submits.append(last - start)
                        while not job.done.is_set():  # An idle GUI thread waking every 2 ms, as for timers.
                            time.sleep(.002); now = time.perf_counter()
                            gap, last = max(gap, now - last - .002), now
                        writes.append(last - start); gaps.append(gap)
                    self.stats('recovery_write_20k', 'gui_submit', submits)
                    self.stats('recovery_write_20k', 'worker_write', writes)
                    self.stats('recovery_write_20k', 'gui_wake_delay_during_write', gaps, maximum=True)
                finally:
                    writer.close(5.)
            with self.window() as w:
                w.autosave_dir, w.autosave_enabled = root/'window', True
                ticks = []
                for k in range(repeats):
                    w.set_project(replace(project, bpm=100. + k)); w.commit_history()
                    ticks.append(elapsed(w.autosave_now)[1])
                    if w.recovery_writer is not None: w.recovery_writer.flush(30.)
                self.stats('recovery_write_20k', 'window_tick', ticks, maximum=True)
                w.autosave_enabled = False

    @contextmanager
    def renderer(self, preferences, width, height, samples, nv12=False):
        from phosphor.display.video_renderer import VideoRenderer
        renderer = VideoRenderer(preferences, width, height, 60, samples, RATE, nv12=nv12)
        try:
            yield renderer
        finally:
            renderer.close()

    def export_render(self):
        self.qt()
        samples = self.prepare(1 if self.args.quick else 2)
        with self.window() as w:
            for width, height in RESOLUTIONS:
                frames, copies = [], []
                with self.renderer(w.preferences, width, height, samples) as renderer:
                    for k in range(len(renderer.ranges)):
                        image, seconds = elapsed(lambda: renderer.frame(k))
                        frames.append(seconds)
                        _, seconds = elapsed(lambda: bytes(image.constBits()))
                        copies.append(seconds)
                label = f'{width}x{height}'
                self.row('export_render', f'{label}_frame_median', float(np.median(frames)) * 1000, 'ms')
                self.row('export_render', f'{label}_bytes_median', float(np.median(copies)) * 1000, 'ms')
                # 0.57 export path: GPU NV12 pass, readback of the previous frame (GUI thread).
                frames = []
                with self.renderer(w.preferences, width, height, samples, nv12=True) as renderer:
                    if renderer.nv12 is None:
                        self.row('export_render', f'{label}_nv12_frame_median', 'unavailable')
                        continue
                    for k in range(len(renderer.ranges)):
                        _, seconds = elapsed(lambda: renderer.frame_nv12(k))
                        frames.append(seconds)
                    renderer.finish_nv12()
                self.row('export_render', f'{label}_nv12_frame_median', float(np.median(frames)) * 1000, 'ms')

    def export_encode(self):
        from phosphor.engine.video_export import find_ffmpeg, ffmpeg_command
        from phosphor.storage.audio_writer import AudioWriter
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            self.row('export_encode', 'ffmpeg', 'unavailable')
            return
        self.qt()
        with self.window() as w, tempfile.TemporaryDirectory(prefix='phosphor-bench-encode-') as folder:
            audio = Path(folder) / 'audio.wav'
            # Audio input matches the app's MP4 command, including -shortest.
            with AudioWriter(audio, RATE, 'WAV', 24) as writer:
                writer.write(np.zeros((2 * RATE, 2)))
            samples = self.prepare(1 / 60)
            from phosphor.display.export_nv12 import Nv12Canvas
            from phosphor.display.crt_theme import PALETTES
            from phosphor.engine.video_export import CODEC_ARGUMENTS
            for width, height in RESOLUTIONS:
                with self.renderer(w.preferences, width, height, samples) as renderer:
                    image = renderer.frame(0)
                    frame = bytes(image.constBits())
                with self.renderer(w.preferences, width, height, samples, nv12=True) as renderer:
                    nv12 = None
                    if renderer.nv12 is not None:
                        renderer.frame_nv12(0)
                        readback = renderer.finish_nv12()
                        canvas = Nv12Canvas(width, height, renderer.side, PALETTES[w.preferences['palette']]['background'])
                        nv12 = bytes(canvas.place(memoryview(readback.data)))
                # Old rows: RGBA pipe, encoder defaults. *_nv12 rows: the 0.57 export command
                # (GPU NV12 frames, video only, the app's per-codec quality options).
                codecs = [(codec, False) for codec in ('libx264', 'h264_nvenc', 'hevc_nvenc', 'h264_amf', 'h264_qsv',
                                                       'h264_videotoolbox', 'hevc_videotoolbox')]
                codecs += [(codec, True) for codec in CODEC_ARGUMENTS] if nv12 is not None else []
                for codec, packed in codecs:
                    target = Path(folder) / f'{width}-{codec}.mp4'
                    if packed:
                        command = ffmpeg_command(ffmpeg, width, height, 60, None, target, 'MP4', codec, 'nv12')
                    else:
                        command = ffmpeg_command(ffmpeg, width, height, 60, audio, target)
                    if codec != 'libx264' and not packed:
                        i = command.index('-c:v')
                        command[i + 1] = codec
                        i = command.index('-crf')
                        del command[i:i + 2]  # CRF is libx264-specific; hardware uses defaults.
                    # A file avoids stderr pipe deadlocks. A watchdog bounds even
                    # a wedged driver's blocking stdin write, and kills on timeout.
                    with tempfile.TemporaryFile() as stderr:
                        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=stderr,
                                                   stdout=subprocess.DEVNULL)
                        watchdog = threading.Timer(6 if self.args.quick else 12, process.kill)
                        start = time.perf_counter()
                        failure = None
                        try:
                            watchdog.start()
                            for _ in range(120):
                                process.stdin.write(nv12 if packed else frame)
                            process.stdin.close()
                            code = process.wait()
                        except OSError as error:
                            failure = error
                            code = -1
                        finally:
                            watchdog.cancel()
                            if process.poll() is None:
                                process.kill()
                            process.wait()
                            if not process.stdin.closed:
                                try:
                                    process.stdin.close()
                                except OSError:
                                    pass
                        seconds = time.perf_counter() - start
                        stderr.seek(0)
                        # Only ffmpeg's last line (e.g. "Encoder not found"): earlier lines name
                        # temp paths, which contain the user's account name, and the JSON is committed.
                        lines = stderr.read().decode('utf-8', 'replace').strip().splitlines()
                        detail = lines[-1].strip() if lines else ''
                    metric = f'{width}x{height}_{codec}' + ('_nv12' if packed else '')
                    if code or failure:
                        self.row('export_encode', metric, 'unavailable')
                        self.results['export_encode'][metric + '_detail'] = detail or str(failure)
                    else:
                        self.row('export_encode', metric, 120 / seconds, 'fps')
                    target.unlink(missing_ok=True)

    def export_end_to_end(self):
        from PySide6.QtCore import QEventLoop, QTimer
        from phosphor.editor.video_export import VideoExportJob
        from phosphor.engine.video_export import find_ffmpeg
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            self.row('export_end_to_end', 'ffmpeg', 'unavailable')
            return
        self.qt()
        duration = 1.5 if self.args.quick else 3.
        # Geometry/route preparation matches Window.output_settings before export.
        self.song_settings()
        runs = [(width, height, 'Auto', duration, '') for width, height in RESOLUTIONS]
        runs.append((3840, 2160, 'Software', duration, '_software'))
        if not self.args.quick:
            runs.append((3840, 2160, 'Auto', 30., '_30s'))  # Long-song behaviour.
        with self.window() as w, tempfile.TemporaryDirectory(prefix='phosphor-bench-job-') as folder:
            for width, height, encoder, duration, suffix in runs:
                result = []
                timed_out = []
                loop = QEventLoop()  # Not app.exec(): quitting the app releases the editor's views.
                def finished(success, error):
                    result.append((success, error))
                    loop.quit()
                scene_plan.cache_clear()
                start = time.perf_counter()
                job = VideoExportJob(w, ffmpeg, Path(folder) / f'{width}.mp4', w.preferences, width, height, 60,
                                     lambda stop: self.blocks(duration, stop), finished, 'MP4', encoder,
                                     round(duration * RATE))
                timeout = QTimer()
                timeout.setSingleShot(True)
                def cancel():
                    timed_out.append(True)
                    job.cancel()  # Keep event loop alive until job.finish cleans up.
                timeout.timeout.connect(cancel)
                timeout.start(int(30000 + 20000 * duration))
                try:
                    loop.exec()
                finally:
                    timeout.stop()
                seconds = time.perf_counter() - start
                if timed_out or not result or not result[0][0]:
                    raise RuntimeError('Export timed out' if timed_out else
                                       (result[0][1] if result else 'Export event loop stopped early'))
                label = f'{width}x{height}{suffix}'
                self.row('export_end_to_end', f'{label}_wall', seconds, 's')
                self.row('export_end_to_end', f'{label}_wall_per_video_second', seconds / duration, 's/s')
                self.row('export_end_to_end', f'{label}_encoder', job.codec)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true')
    parser.add_argument('--only', help=','.join(SCENARIOS))
    parser.add_argument('--obj', type=Path)
    parser.add_argument('--json', type=Path)
    parser.add_argument('--compare', type=Path)
    args = parser.parse_args()
    names = args.only.split(',') if args.only else list(SCENARIOS)
    if set(names) - set(SCENARIOS):
        parser.error('Unknown scenarios: ' + ', '.join(sorted(set(names) - set(SCENARIOS))))
    before = json.loads(args.compare.read_text(encoding='utf-8')) if args.compare else {}
    llm_warning()
    bench = Benchmark(args, before)
    try:
        bench.qt()  # Report configured API even for a CPU-only subset.
    except Exception as error:
        bench.header['graphics_api'] = f'unavailable: {error}'
    print('  '.join(f'{key}={value}' for key, value in bench.header.items()), flush=True)
    print(f'{"scenario":20s} {"metric":42s} {"value":>12s} {"unit":9s}'
          + ('     ratio' if args.compare else ''))
    for name in names:
        try:
            getattr(bench, name)()
        except Exception as error:
            bench.row(name, 'error', ' '.join(str(error).splitlines()))
    if args.json:
        args.json.write_text(json.dumps(dict(header=bench.header, **bench.results), indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
