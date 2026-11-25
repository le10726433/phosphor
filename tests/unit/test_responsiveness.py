"""Audit P7a: editor and Composer responsiveness (planning/agent_recommendations.md).

Caches and their invalidation (roll paint, prepared scores, the stopped Composer's signal,
decoded media), the note index, the batched muted outline, decode misses (silence plus the
worker) against always-synchronous export, and swap-based frame pacing.
"""
from dataclasses import replace
import os
from pathlib import Path
import random
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import soundfile

from phosphor.engine import audio_clips as a
from phosphor.engine import composition
from phosphor.engine.composition import Project, Clip, Note, render_song, export_song
from phosphor.engine.signal_engine import Settings
from phosphor.engine.route_plan import SHORTEST_PATH
from tests.unit.golden_scenes import scene

RATE = 48000
MACHINE = Settings(sample_rate=RATE)


def song(count=400, glide=False, chords=False, velocity=True, bpm=120., tempo_changes=()):
    rng = random.Random(count)
    notes = [Note(round(rng.uniform(0, count / 8), 3), round(rng.uniform(.1, 2.), 3), rng.randint(40, 90),
                  round(rng.uniform(.2, 1.), 3)) for _ in range(count)]
    if glide: notes[count // 2] = replace(notes[count // 2], glide=min(.2, notes[count // 2].length))
    end = max(n.end for n in notes)
    return Project(bpm=bpm, notes=tuple(notes), clips=(Clip(0., end + 4., scene('cube')),), chords=chords,
                   velocity=velocity, tempo_changes=tempo_changes)


# Prepared scores (P2) -----------------------------------------------------------------------
class PreparedScoreTests(unittest.TestCase):
    def setUp(self):
        composition.clear_prepared(); self.addCleanup(composition.clear_prepared)

    def blocks(self, project, count=6, start=3.):
        return [render_song(start + (b * 512 + np.arange(512)) / RATE, project, MACHINE) for b in range(count)]

    def fresh(self, project, **kwargs):
        composition.clear_prepared()
        return self.blocks(project, **kwargs)

    def test_one_build_per_score_and_samples_equal_a_fresh_build(self):
        for chords in (False, True):
            for glide in (False, True):
                with self.subTest(chords=chords, glide=glide):
                    project = song(glide=glide, chords=chords)
                    expected = self.fresh(project)
                    composition.clear_prepared()
                    builds = composition.prepared_builds
                    actual = self.blocks(project)
                    self.assertEqual(composition.prepared_builds - builds, 1)
                    for x, y in zip(actual, expected): np.testing.assert_array_equal(x, y)

    def test_edits_tempo_velocity_undo_and_project_switch_invalidate(self):
        project = song(glide=True)
        self.blocks(project)
        builds = composition.prepared_builds
        # An edit is a new notes tuple; undo brings back the old (still cached) one.
        notes = list(project.notes); notes[3] = replace(notes[3], pitch=notes[3].pitch + 1)
        edited = replace(project, notes=tuple(notes))
        for changed in (edited, replace(project, bpm=97.), replace(project, tempo_changes=((8., 140.),)),
                        replace(project, velocity=False), song(300)):
            with self.subTest(changed=changed is edited):
                actual = self.blocks(changed)
                self.assertEqual(composition.prepared_builds - builds, 1)
                builds = composition.prepared_builds
                expected = self.fresh(changed)
                for x, y in zip(actual, expected): np.testing.assert_array_equal(x, y)
                builds = composition.prepared_builds
        composition.clear_prepared()
        self.blocks(project); self.blocks(edited)
        builds = composition.prepared_builds
        self.blocks(project); self.blocks(edited)  # Undo, redo: both scores are still prepared.
        self.assertEqual(composition.prepared_builds, builds)

    def test_entries_hold_their_notes(self):
        project = song(50)
        self.blocks(project, 1)
        entry = next(iter(composition._prepared.values()))
        self.assertIs(entry[0], project.notes)  # Held: its id can't be reused while cached.


# Note index (P1) ----------------------------------------------------------------------------
class RollTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def view(self, project, width=900, height=520):
        from phosphor.editor.composer_view import TimelineView
        view = TimelineView(); view.resize(width, height); view.set_project(project, 0)
        self.addCleanup(lambda: (view.close(), view.deleteLater(), self.app.processEvents()))
        return view


class NoteIndexTests(RollTestCase):
    def test_queries_equal_the_full_scans(self):
        from PySide6.QtCore import QRectF
        from phosphor.editor.composer_view import GUTTER
        rng = random.Random(7)
        project = song(1500, chords=True)
        # Long notes that start left of the view, zero-ish widths and duplicates.
        notes = project.notes + (Note(0., 150., 60), Note(30., .001, 61), Note(30., .001, 61))
        view = self.view(replace(project, notes=notes))
        for _ in range(60):
            view.scroll = rng.uniform(0, 200); view.scale = rng.choice((1.5, 7., 28., 140.))
            view.top_pitch = rng.randint(60, 108); view.row = rng.choice((5, 9, 24))
            room = QRectF(GUTTER, view.roll_top, view.width() - GUTTER, view.note_bottom() - view.roll_top)
            self.assertEqual(view.visible_notes(),
                             [(i, n) for i, n in enumerate(notes) if view.note_rect(n).intersects(room)])
            self.assertEqual([i for i, _ in view.velocity_items()],
                             [i for i, n in enumerate(notes) if GUTTER <= view.x_of(n.start) <= view.width()])
            for _ in range(20):
                x, y = rng.uniform(GUTTER, view.width()), rng.uniform(view.roll_top, view.note_bottom() - 1)
                beats, pitch = view.beats_at(x), view.pitch_at(y)
                expected = next(((i, view.x_of(n.end) - x <= 6) for i in range(len(notes) - 1, -1, -1)
                                 for n in (notes[i],) if n.pitch == pitch and n.start <= beats < n.end), (None, False))
                self.assertEqual(view.note_hit(x, y), expected)

    def test_index_follows_the_notes_tuple(self):
        view = self.view(song(100))
        index = view.note_index()
        self.assertIs(view.note_index(), index)
        view.set_project(song(120), 0)
        self.assertIsNot(view.note_index(), index)

    def test_sounding_pitches_equal_the_loop(self):
        from phosphor.engine.chord_names import sounding_pitches
        project = song(600, chords=True)
        voices = composition.chord_voices(project.notes)
        for beat in np.linspace(-1, 80, 300):
            expected = tuple(sorted(int(p) for s, e, p, st in zip(voices.start, voices.end, voices.pitch, voices.steal)
                                    if s <= beat < e and beat < st))
            self.assertEqual(sounding_pitches(project.notes, beat, voices), expected)


# Roll paint cache (P1) ----------------------------------------------------------------------
class RollPaintTests(RollTestCase):
    def setUp(self):
        a.clear_decoded(); self.addCleanup(a.clear_decoded)

    def image(self, view, cached=True):
        from PySide6.QtGui import QImage
        view.cache_static = cached
        image = QImage(view.size(), QImage.Format.Format_ARGB32_Premultiplied); image.fill(0)
        view.render(image)
        view.cache_static = True
        return image

    def assert_same_as_full(self, view, label):
        cached = self.image(view)
        full = self.image(view, cached=False)
        self.assertTrue(cached == full, f'{label}: the cached roll differs from a full repaint')

    def project_with_audio(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'tone.wav'
        t = np.arange(RATE * 4) / RATE
        soundfile.write(path, np.column_stack((np.sin(2 * np.pi * 3 * t), np.cos(2 * np.pi * 2 * t))) * .8, RATE)
        clip = a.audio_clip_from_file(path, 2.)
        a.decoded(clip.path, clip.fingerprint, RATE)
        # Overlapping notes on one row and more than 8 voices (dropped voices are drawn).
        notes = song(200, chords=True).notes + (Note(4., 2., 70), Note(4.5, 2., 70)) + tuple(
            Note(6., 1., 48 + 2 * k) for k in range(10))
        return replace(song(200, chords=True), notes=notes, audio_clips=(clip,))

    def test_cached_roll_equals_a_full_repaint(self):
        from PySide6.QtCore import QRectF
        from phosphor.editor.composer_view import TRANSFORM_ROLL, NOTE_ROLL
        view = self.view(self.project_with_audio())
        view.top_pitch = 84
        self.assert_same_as_full(view, 'initial')
        for beat in (0., 3.3, 4.6, 6.2, 9.99):
            view.set_playhead(beat)
            self.assert_same_as_full(view, f'playhead {beat}')
        view.set_playhead(view.beats_at(view.width() - 25))  # The pill flips left near the right edge.
        self.assert_same_as_full(view, 'pill flipped')
        view.scroll = 2.5; view.update(); self.assert_same_as_full(view, 'scroll')
        view.note_selection = {3, 4, 5}; view.update(); self.assert_same_as_full(view, 'selection')
        view.selection_rect = QRectF(200, 150, 120, 60); view.update(); self.assert_same_as_full(view, 'box select')
        view.selection_rect = None
        view.selected_audio = 0; view.update(); view.set_meter(.7); self.assert_same_as_full(view, 'meter')
        view.set_meter(1.4); self.assert_same_as_full(view, 'clip lamp')
        view.set_roll(TRANSFORM_ROLL); self.assert_same_as_full(view, 'transform roll')
        view.set_roll(NOTE_ROLL); view.set_audio_gain_lane(True); self.assert_same_as_full(view, 'gain lane')
        view.colors = dict(view.colors, background='#101820'); self.assert_same_as_full(view, 'palette')

    def test_playhead_moves_reuse_the_static_layer(self):
        view = self.view(song(2000, chords=True))
        self.image(view)
        paints = view.static_paints
        for k in range(10):
            view.set_playhead(2. + .05 * k)
            self.assertTrue(view._static_valid, 'set_playhead repaints regions only')
        self.image(view)
        self.assertEqual(view.static_paints, paints)

    def test_lane_paint_and_hit_test_convert_nodes_once(self):
        from PySide6.QtCore import QPointF
        from phosphor.editor.composer_view import TRANSFORM_ROLL
        points = ((0., .2, 'hold'), (1., -.4, 'linear'), (2., .1, 'curve', -.6, .8), (3., .7, 'hold'))
        clip = Clip(0., 4., Settings(), lanes=(('drawing_gain', points),))
        view = self.view(Project(clips=(clip,)))
        view.set_roll(TRANSFORM_ROLL)
        with patch.object(view, 'node_points', wraps=view.node_points) as convert:
            self.image(view, cached=False)
            convert.assert_called_once()
            rect, lane = convert.call_args.args
            self.assertEqual(lane, points)
            convert.reset_mock()
            view.segment_at(rect, points, -100., -100.)
            convert.assert_called_once()
        nodes = view.node_points(rect, points)
        expected = [[nodes[0], QPointF(nodes[1].x(), nodes[0].y()), nodes[1]], nodes[1:3]]
        curve = []
        for n in range(24):
            u = n / 23
            gain = (1-u)**3*.1 + 3*(1-u)**2*u*(-.6) + 3*(1-u)*u*u*.8 + u**3*.7
            curve.append(QPointF(nodes[2].x() + u*(nodes[3].x()-nodes[2].x()), view.gain_y(rect, gain)))
        for i, polyline in enumerate([*expected, curve]):
            self.assertEqual(view.segment_polyline(rect, points, i, nodes), polyline)

    def test_edit_while_stopped_preserves_scroll_and_seek_still_follows(self):
        project = song(200, chords=True)
        view = self.view(project)
        view.scroll = 12.
        view.set_project(replace(project, notes=project.notes[1:]), 0)
        view.set_playhead(view.playhead)
        self.assertEqual(view.scroll, 12.)
        view.set_playhead(2.)
        self.assertEqual(view.scroll, 1.)

    def test_edits_tempo_undo_and_project_switch_redraw_the_static_layer(self):
        project = song(300, chords=True)
        view = self.view(project)
        self.image(view)
        edited = replace(project, notes=project.notes[1:])
        for label, change in (('edit', lambda: view.set_project(edited, 0)),
                              ('undo', lambda: view.set_project(project, 0)),
                              ('tempo', lambda: view.set_project(replace(project, tempo_changes=((4., 90.),)), 0)),
                              ('switch', lambda: view.set_project(song(100), 0)),
                              ('resize', lambda: view.resize(700, 400)),
                              ('zoom', lambda: view.zoom(1.5))):
            with self.subTest(label):
                paints = view.static_paints
                change()
                self.image(view)
                self.assertEqual(view.static_paints, paints + 1)
                self.assert_same_as_full(view, label)


# Muted outline (P3) -------------------------------------------------------------------------
class OutlineTests(unittest.TestCase):
    def test_batched_edges_equal_one_stroke_per_edge(self):
        from phosphor.display.qt_geometry import stroke_edges, stroke_triangles
        from phosphor.engine.model_object import model_from_obj
        from tests.fixtures.dense_mesh import dense_mesh_obj
        rng = np.random.default_rng(3)
        points = rng.normal(size=(50, 2)) * 300
        points[7] = points[8]  # A zero-length edge is dropped, as before.
        model = model_from_obj(dense_mesh_obj(rings=12, segments=12), 'Dense')[0]
        cases = {'disconnected': [(0, 1), (5, 9), (20, 3)], 'repeated': [(0, 1), (0, 1), (1, 0)],
                 'degenerate': [(7, 8), (8, 7), (2, 3)], 'empty': [],
                 'dense': [(a % 50, b % 50) for a, b in model.edges]}
        for label, edges in cases.items():
            with self.subTest(label):
                old = [stroke_triangles(points[[a, b]], .7) for a, b in edges]
                old = np.concatenate(old) if old else np.empty((0, 2), dtype=np.float32)
                new = stroke_edges(points, edges, .7)
                self.assertEqual(new.dtype, old.dtype)
                np.testing.assert_array_equal(new, old)


# The stopped Composer's signal (P7) ---------------------------------------------------------
class StoppedSignalTests(unittest.TestCase):
    def test_one_render_while_nothing_changes(self):
        from phosphor.editor import qt_app
        project = song(50)
        w = SimpleNamespace(playhead=1.25, project=project)
        w.output_project = lambda: w.project
        settings = MACHINE
        with patch.object(qt_app, 'render_song', wraps=qt_app.render_song) as render:
            first = qt_app.Window.stopped_signal(w, settings)
            for _ in range(100):
                # output_settings builds a new Settings every frame: equal values are a hit.
                self.assertIs(qt_app.Window.stopped_signal(w, replace(settings)), first)
            self.assertEqual(render.call_count, 1)
            self.assertFalse(render.call_args.kwargs['wait_media'])
            changes = (('seek', lambda: setattr(w, 'playhead', 2.)),
                       ('edit', lambda: setattr(w, 'project', replace(project, bpm=100.))),
                       ('undo', lambda: setattr(w, 'project', project)),
                       ('solo', lambda: setattr(w, 'output_project', lambda copy=replace(project): copy)),
                       ('media', a.clear_decoded))
            for label, change in changes:
                with self.subTest(label):
                    calls = render.call_count
                    change()
                    qt_app.Window.stopped_signal(w, settings); qt_app.Window.stopped_signal(w, settings)
                    self.assertEqual(render.call_count, calls + 1)
            calls = render.call_count
            for field, value in (('sample_rate', 44100), ('corner_dwell_us', 20.), ('shape_redraw_count', 2),
                                 ('switch_sweep_ms', 5.), ('route_mode', SHORTEST_PATH)):
                qt_app.Window.stopped_signal(w, replace(settings, **{field: value}))
            self.assertEqual(render.call_count, calls + 5)
        np.testing.assert_array_equal(first, np.clip(render_song(1.25 + np.arange(round(.04 * RATE)) / RATE,
                                                                 project, settings), -1, 1))


# Decoded media (P9) -------------------------------------------------------------------------
class DecodeTests(unittest.TestCase):
    def setUp(self):
        a.clear_decoded(); self.addCleanup(a.clear_decoded)
        self.addCleanup(a.wait_idle, 10)
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.paths = []
        for k in range(3):
            path = self.root / f'tone{k}.wav'
            t = np.arange(RATE) / RATE
            soundfile.write(path, np.column_stack((np.sin(2 * np.pi * (200 + 50 * k) * t),) * 2) * .4, RATE)
            self.paths.append(path)
        self.clips = tuple(a.audio_clip_from_file(path, 4. * k) for k, path in enumerate(self.paths))
        self.project = Project(clips=(Clip(0., 16., scene('cube')),), audio_clips=self.clips)

    def test_a_miss_without_waiting_is_silent_and_queues_the_decode(self):
        clip = self.clips[0]
        gate = threading.Event()
        real = a._read
        def read(path, rate):
            gate.wait(10); return real(path, rate)
        times = np.arange(4096) / RATE
        with patch.object(a, '_read', read):  # The worker holds the decode until the gate opens.
            self.assertIsNone(a.decoded(clip.path, clip.fingerprint, RATE, wait=False))
            silent = render_song(times, self.project, MACHINE, wait_media=False)
            gate.set()
            self.assertTrue(a.wait_idle(10))
        np.testing.assert_array_equal(silent, render_song(times, replace(self.project, audio_clips=()), MACHINE))
        self.assertIsNotNone(a.decoded(clip.path, clip.fingerprint, RATE, wait=False))
        heard = render_song(times, self.project, MACHINE, wait_media=False)
        a.clear_decoded()
        np.testing.assert_array_equal(heard, render_song(times, self.project, MACHINE))  # Waits: the same.

    def test_export_always_decodes_synchronously(self):
        warm = self.root / 'warm.wav'; cold = self.root / 'cold.wav'
        a.preload(self.project, RATE)
        export_song(warm, self.project, MACHINE, 0., 10.)
        a.clear_decoded()
        with patch.object(a, 'decoded', wraps=a.decoded) as lookup:
            export_song(cold, self.project, MACHINE, 0., 10.)
        self.assertTrue(lookup.call_args_list and all(call.args[3] is True for call in lookup.call_args_list))
        self.assertEqual(warm.read_bytes(), cold.read_bytes())

    def test_the_audio_thread_never_decodes(self):
        from phosphor.audio.audio_preview import AudioPreview
        readers = []
        real = a._read
        def read(*args):
            readers.append(threading.get_ident()); return real(*args)
        with patch.object(a, '_read', read):
            signal, _, _ = AudioPreview()._song_block(np.arange(512) / RATE, MACHINE, (self.project, 0.))
            self.assertTrue(a.wait_idle(10))
        self.assertNotIn(threading.get_ident(), readers)
        self.assertEqual(len(readers), 1)  # The first clip, by the worker.

    def test_prime_waits_for_clips_at_the_start_and_queues_the_rest(self):
        gate = threading.Event()
        real = a._read
        def read(path, rate):
            if Path(path).name != 'tone0.wav': gate.wait(10)
            return real(path, rate)
        with patch.object(a, '_read', read):
            a.prime(self.project, RATE, 0.)
            clip = self.clips[0]
            self.assertIsNotNone(a.decoded(clip.path, clip.fingerprint, RATE, wait=False))
            far = self.clips[2]
            self.assertIsNone(a.decoded(far.path, far.fingerprint, RATE, wait=False))
            gate.set()
            self.assertTrue(a.wait_idle(10))
        self.assertIsNotNone(a.decoded(far.path, far.fingerprint, RATE, wait=False))

    def test_prepare_drops_requests_of_an_earlier_project(self):
        gate = threading.Event()
        real = a._read
        def read(path, rate):
            gate.wait(10); return real(path, rate)
        with patch.object(a, '_read', read):
            a.prepare(self.project, (RATE, 44100))
            other = replace(self.project, audio_clips=self.clips[2:])
            a.prepare(other, (RATE,))
            with a._lock: queued = list(a._queue)
            # Only the new project's file is still waiting (the worker may already hold one old key).
            self.assertEqual(queued, [(self.clips[2].path, self.clips[2].fingerprint, RATE)])
            gate.set()
            self.assertTrue(a.wait_idle(10))

    def test_cache_is_bounded_in_bytes_and_keeps_the_newest(self):
        size = RATE * 2 * 4  # One second of float32 stereo.
        with patch.object(a, 'DECODE_CACHE_BYTES', int(size * 1.5)):
            for clip in self.clips: a.decoded(clip.path, clip.fingerprint, RATE)
            self.assertEqual([k[0] for k in a._cache], [self.clips[2].path])
            self.assertEqual(a.cached_bytes(), size)
        with patch.object(a, 'DECODE_CACHE_BYTES', 10):
            clip = self.clips[0]
            with self.assertLogs(a.__name__, 'WARNING'):
                samples = a.decoded(clip.path, clip.fingerprint, RATE)
            self.assertEqual(samples.nbytes, size)
            self.assertIs(samples, a.decoded(clip.path, clip.fingerprint, RATE))
            self.assertEqual(list(a._cache), [(clip.path, clip.fingerprint, RATE)])
            self.assertEqual(a.cached_bytes(), size)

    def test_float32_reads_equal_float64_rounded(self):
        rng = np.random.default_rng(5)
        data = np.clip(rng.normal(scale=.4, size=(9000, 2)), -1, 1)
        for kind, subtype in (('WAV', 'PCM_16'), ('WAV', 'PCM_24'), ('WAV', 'PCM_32'), ('WAV', 'FLOAT'),
                              ('WAV', 'DOUBLE'), ('FLAC', 'PCM_16'), ('FLAC', 'PCM_24')):
            for channels in (1, 2, 3):
                with self.subTest(kind=kind, subtype=subtype, channels=channels):
                    path = self.root / f'x.{kind.lower()}'
                    frames = data if channels == 2 else data[:, :1] if channels == 1 else np.column_stack((data, data[:, 0]))
                    soundfile.write(path, frames, 44100, subtype=subtype, format=kind)
                    old, _ = soundfile.read(path, dtype='float64', always_2d=True)
                    old = np.repeat(old, 2, axis=1) if old.shape[1] == 1 else old[:, :2]
                    new = a._read(str(path), 44100)
                    np.testing.assert_array_equal(new, old.astype('float32'))
                    self.assertFalse(new.flags.writeable)
                    self.assertTrue(new.flags.c_contiguous)

    def test_media_generation_changes_when_a_decode_lands(self):
        before = a.media_generation()
        a.decoded(self.clips[0].path, self.clips[0].fingerprint, RATE)
        self.assertNotEqual(a.media_generation(), before)


# Frame pacing (B11) -------------------------------------------------------------------------
class PacingTests(unittest.TestCase):
    def test_pacing_reports_count_median_p95_p99_and_the_clock(self):
        from phosphor.audio.playback_diagnostics import pacing, timing_stats
        stamps = np.cumsum([1 / 60] * 50 + [.1] + [1 / 60] * 49)
        result = pacing(stamps, 'swaps', skip=0)
        self.assertEqual((result['clock'], result['count']), ('swaps', 99))
        self.assertAlmostEqual(result['median_interval_ms'], 1000 / 60)
        self.assertGreater(result['p99_interval_ms'], result['median_interval_ms'])
        self.assertGreater(result['p95_interval_ms'], 0)
        self.assertEqual(pacing([], 'swaps'), dict(clock='swaps', count=0))
        self.assertIn('p99', timing_stats([1, 2, 3]))

    def test_diagnostics_present_swaps_beside_preparation_starts(self):
        from collections import deque
        from phosphor.audio.playback_diagnostics import PlaybackDiagnostics
        audio = SimpleNamespace(frames=0, underruns=0, synthesis_ms=deque(), write_wait_ms=deque(), error='')
        w = SimpleNamespace(frame_times=deque(), swap_times=deque(), tick_times=deque(), prep_ms=deque(),
                            host=SimpleNamespace(scope=SimpleNamespace(upload_ms=deque())), audio=audio,
                            playback_ticks=0, fresh_playback_windows=0)
        diagnostics = PlaybackDiagnostics(w)
        diagnostics.begin('playing')
        w.frame_times.extend(np.arange(60) / 60)  # Preparation kept time...
        w.swap_times.extend(np.arange(6) / 6)  # ...while only 6 frames reached the screen.
        diagnostics.end()
        phase = diagnostics.phases['playing']
        self.assertEqual(phase['presentation']['frames'], 6)
        self.assertEqual(phase['preparation_starts']['frames'], 60)


if __name__ == '__main__':
    unittest.main()
