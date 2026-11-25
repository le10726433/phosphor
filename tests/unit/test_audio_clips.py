import hashlib
import json
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile

from phosphor.storage.project_files import PROJECT_VERSION
from phosphor.engine import audio_clips as a
from phosphor.engine.composition import (Project, Clip, Note, render_song, song_cycle,
                                         song_seconds, export_song, validate_project)
from phosphor.engine.signal_engine import Settings, Shape
from phosphor.storage.project_files import (project_document, project_from_document,
                                            save_project, read_project)
from phosphor.storage.project_files import PROJECT_VERSION

RATE = 48000
MACHINE = Settings(sample_rate=RATE)


class AudioClipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        a.clear_decoded()
        self.addCleanup(a.clear_decoded)
        t = np.arange(4800) / RATE
        self.samples = np.column_stack((.3*np.sin(2*np.pi*220*t), .2*np.cos(2*np.pi*330*t))).astype('float32')
        self.path = self.root / 'tone.wav'
        soundfile.write(self.path, self.samples, RATE, subtype='FLOAT')
        self.clip = a.audio_clip_from_file(self.path, 0.)

    def render(self, clip=None, times=None, **kwargs):
        project = Project(audio_clips=(self.clip if clip is None else clip,), **kwargs)
        return render_song(np.arange(4800)/RATE if times is None else times, project, MACHINE)

    def test_fingerprint_small_and_large(self):
        for size in (91, 2*1024*1024+31):
            with self.subTest(size=size):
                path = self.root / 'bytes'
                payload = bytearray(b'a'*size)
                path.write_bytes(payload)
                digest = hashlib.sha256(f'{size}\n'.encode())
                digest.update(payload[:1024*1024])
                if size > 1024*1024: digest.update(payload[-1024*1024:])
                expected = a.fingerprint(path)
                self.assertEqual(expected, digest.hexdigest())
                copy = self.root / 'copy'; shutil.copyfile(path, copy)
                self.assertEqual(a.fingerprint(copy), expected)
                for index in (0, size-1):
                    changed = payload.copy(); changed[index] = 98; path.write_bytes(changed)
                    self.assertNotEqual(a.fingerprint(path), expected)

    def test_decode_stereo_mono_and_resample_frequency(self):
        np.testing.assert_array_equal(a.decoded(str(self.path), self.clip.fingerprint, RATE), self.samples)
        path = self.root / 'mono.wav'
        t = np.arange(44100) / 44100
        soundfile.write(path, .3*np.sin(2*np.pi*440*t), 44100, subtype='FLOAT')
        clip = a.audio_clip_from_file(path, 0)
        native = a.decoded(clip.path, clip.fingerprint, 44100)
        np.testing.assert_array_equal(native[:, 0], native[:, 1])
        samples = a.decoded(clip.path, clip.fingerprint, RATE)
        self.assertEqual(samples.shape, (round(len(t)*RATE/44100), 2))
        self.assertEqual(np.argmax(abs(np.fft.rfft(samples[:, 0]))), 440)

    def test_decode_cache_failures_lru_and_concurrent_access(self):
        with patch.object(a, '_read', wraps=a._read) as read:
            with ThreadPoolExecutor(max_workers=4) as pool:
                values = list(pool.map(lambda _: a.decoded(self.clip.path, self.clip.fingerprint, RATE), range(8)))
            for value in values: np.testing.assert_array_equal(value, self.samples)
            self.assertEqual(read.call_count, 1)  # Concurrent callers wait for the one decode.
            calls = read.call_count
            a.preload(Project(audio_clips=(self.clip,)), RATE)
            self.assertEqual(read.call_count, calls)
            for _ in range(3): self.assertIsNone(a.decoded('absent.wav', '', RATE))
            self.assertEqual(read.call_count, calls+1)
            with patch.object(a, 'DECODE_CACHE_ENTRIES', 16):
                for i in range(20): a.decoded(f'absent{i}.wav', '', RATE)
            self.assertEqual(len(a._cache), 16)
            a.clear_decoded(); self.assertEqual(len(a._cache), 0)

    def test_routing_sample_exact(self):
        left, right = self.samples.astype('float64').T
        mono = (left+right)/2
        for routing, expected in (('stereo', (left, right)), ('mono', (mono, mono)),
                                  ('x', (mono, np.zeros_like(mono))), ('y', (np.zeros_like(mono), mono))):
            with self.subTest(routing=routing):
                np.testing.assert_array_equal(self.render(replace(self.clip, routing=routing)), np.column_stack(expected))

    def test_overlap_filter_matches_full_scan_at_unordered_times(self):
        clips = tuple(replace(self.clip, start=i*.04, gain=(i+1)/128.) for i in range(128))
        project = Project(audio_clips=clips[::2] + clips[1::2], tempo_changes=((1., 85.),))
        start = float(project.seconds(clips[40].start))
        times = start + np.array([.093, 0., -.49/RATE, .01, .04])
        with patch.object(a.AudioLayout, 'overlapping', return_value=range(len(clips))):
            expected = render_song(times, project, MACHINE)
        with patch.object(a, '_add_clip', wraps=a._add_clip) as add:
            actual = render_song(times, project, MACHINE)
        self.assertTrue(np.any(expected))
        np.testing.assert_array_equal(actual, expected)
        self.assertLess(add.call_count, 12)

    def test_decode_fault_completes_worker_and_memory_failure_can_retry(self):
        key = self.clip.path, self.clip.fingerprint, RATE
        with patch.object(a, '_read', side_effect=TypeError('injected')), self.assertLogs(a.__name__, 'ERROR'):
            a.decoded(*key, wait=False)
            self.assertTrue(a.wait_idle(5))
        self.assertNotIn(key, a._pending)
        self.assertTrue(a.decode_failed(*key))
        a.clear_decoded()
        for wait in (False, True):
            a.clear_decoded()
            with self.subTest(wait=wait), patch.object(a.time, 'monotonic', return_value=10.) as clock, \
                    patch.object(a, '_read', side_effect=[MemoryError(), self.samples]) as read:
                if wait:
                    with self.assertRaises(MemoryError): a.decoded(*key)
                else:
                    self.assertIsNone(a.decoded(*key, wait=False))
                    a.wait_idle()
                clock.return_value = 14.
                a.prepare(Project(audio_clips=(self.clip,)), (RATE,))
                self.assertIsNone(a.decoded(*key, wait=False))
                a.wait_idle()
                with self.assertRaises(MemoryError): a.decoded(*key)
                self.assertFalse(a.decode_failed(*key))
                self.assertEqual(read.call_count, 1)
                clock.return_value = 16.
                if not wait:
                    self.assertIsNone(a.decoded(*key, wait=False))
                    a.wait_idle()
                np.testing.assert_array_equal(a.decoded(*key), self.samples)
                self.assertEqual(read.call_count, 2)

    def test_export_rate_survives_a_full_cache_of_prepared_media(self):
        with patch.object(a, 'DECODE_CACHE_BYTES', self.samples.nbytes):
            a.prepare(Project(audio_clips=(self.clip,)), (RATE,))
            self.assertTrue(a.wait_idle(5))
            with patch.object(a, '_read', wraps=a._read) as read:
                for _ in range(3):
                    self.assertIsNotNone(a.decoded(self.clip.path, self.clip.fingerprint, RATE // 2))
                self.assertEqual(read.call_count, 1)

    def test_alias_accounting_and_project_aware_eviction(self):
        path, fp = self.clip.path, self.clip.fingerprint
        def check_bytes():
            arrays = {id(v): v for v in a._cache.values() if v is not None}
            self.assertEqual(a.cached_bytes(), sum(v.nbytes for v in arrays.values()))
        a.decoded(path, fp, RATE)
        with patch.object(a, 'DECODE_CACHE_ENTRIES', 2):
            a.alias_decoded(path, 'a', fp)
            a.alias_decoded(path, 'b', fp)  # Evicts the original, both aliases still hold the array.
            check_bytes()
            self.assertNotIn((path, fp, RATE), a._cache)
            a.decoded(path, 'other', RATE)
            check_bytes()
            a.decoded(path, 'third', RATE)  # Evicts the last alias.
            check_bytes()
        a.clear_decoded()
        a.prepare(Project(audio_clips=(self.clip,)), (RATE,))
        self.assertTrue(a.wait_idle(5))
        with patch.object(a, 'DECODE_CACHE_ENTRIES', 3), patch.object(a, 'DECODE_CACHE_BYTES', self.samples.nbytes*2):
            for alias in ('a', 'b', 'c', 'd'):
                a.alias_decoded(path, alias, fp)
                check_bytes()
            for fingerprint in ('second', 'third', 'fourth'):
                a.decoded(path, fingerprint, RATE)
                check_bytes()
            self.assertIn((path, fp, RATE), a._cache)
            self.assertLessEqual(a.cached_bytes(), a.DECODE_CACHE_BYTES)

    def test_gain_fades_overlap_and_trim_sample_exact(self):
        for fade_in, fade_out in ((.01, .02), (.05, .05), (0., 0.)):
            clip = replace(self.clip, start=.5, offset=.01, duration=.06, gain=.7,
                           fade_in=fade_in, fade_out=fade_out)
            times = .25 + np.arange(-1, 2881)/RATE
            expected = np.zeros((2882, 2))
            k = np.arange(2880)
            env = np.ones(2880)
            if fade_in: env = np.minimum(env, k/round(fade_in*RATE))
            if fade_out: env = np.minimum(env, (2880-k)/round(fade_out*RATE))
            expected[1:-1] = self.samples[480:3360].astype('float64') * (env*.7)[:, None]
            np.testing.assert_array_equal(self.render(clip, times), expected)
            if fade_in: np.testing.assert_array_equal(expected[1], 0)
            if fade_out: self.assertEqual(env[-1], 1/round(fade_out*RATE))

    def test_block_split_seek_and_loop(self):
        project = Project(audio_clips=(replace(self.clip, start=2.),), loop=True, loop_start=2., loop_end=2.2)
        times = np.arange(60000)/RATE
        whole = render_song(times, project, MACHINE)
        rng = np.random.default_rng(51); parts = []; start = 0
        while start < len(times):
            end = start + int(rng.integers(1, 1700))
            parts.append(render_song(times[start:end], project, MACHINE)); start = end
        np.testing.assert_array_equal(np.concatenate(parts), whole)
        np.testing.assert_array_equal(render_song(times[49000:49100], project, MACHINE), self.samples[1000:1100])
        wrapped = song_seconds(project, 0., np.array([1.025, 1.125]))
        np.testing.assert_array_equal(render_song(wrapped, project, MACHINE), np.tile(self.samples[1200], (2, 1)))

    def test_export_float_equals_playback(self):
        project = Project(audio_clips=(replace(self.clip, gain=.7, fade_in=.01),))
        path = self.root / 'export.wav'
        export_song(path, project, MACHINE, end_beats=.2, format='WAV', depth=32)
        samples, rate = soundfile.read(path, dtype='float32', always_2d=True)
        self.assertEqual(rate, RATE)
        np.testing.assert_array_equal(samples, render_song(np.arange(4800)/RATE, project, MACHINE).astype('float32'))

    def test_tempo_moves_start_without_changing_speed(self):
        clip = replace(self.clip, start=4.)
        for changes, start in (((), 2.), (((2., 60.),), 3.)):
            project = Project(audio_clips=(clip,), tempo_changes=changes)
            np.testing.assert_array_equal(render_song(start+np.arange(4800)/RATE, project, MACHINE), self.samples)

    def test_silent_clips_preserve_bits_and_exclusions(self):
        base = Project(clips=(Clip(0., 8., Settings(shapes=(Shape(),))),), notes=(Note(0., 8., 60),))
        times = np.arange(4800)/RATE
        expected = render_song(times, base, MACHINE)
        for clip in (replace(self.clip, gain=0), replace(self.clip, missing=True), replace(self.clip, path='vanished.wav')):
            np.testing.assert_array_equal(render_song(times, replace(base, audio_clips=(clip,)), MACHINE), expected)
        project = replace(base, audio_clips=(self.clip,))
        np.testing.assert_array_equal(render_song(times, project, MACHINE, source_only=True), render_song(times, base, MACHINE, source_only=True))
        np.testing.assert_array_equal(song_cycle(project, .03, MACHINE)[0], song_cycle(base, .03, MACHINE)[0])
        end = Project(audio_clips=(replace(self.clip, start=12., duration=3.),), tempo_changes=((2., 60.),))
        self.assertEqual(end.end_beats, float(end.beats(end.seconds(12.)+3.)))

    def test_add_stable_order_and_validation(self):
        late = replace(self.clip, start=2., name='late')
        project = a.add_audio_clip(a.add_audio_clip(Project(), late), self.clip)
        project = a.add_audio_clip(project, replace(self.clip, name='second'))
        self.assertEqual([c.name for c in project.audio_clips], ['tone.wav', 'second', 'late'])
        for fields in ({'routing': 'bad'}, {'fade_in': -1}, {'fade_out': .2}, {'gain': 3}, {'start': float('nan')},
                       {'offset': -1}, {'duration': 0}, {'size': True}, {'fingerprint': 'A'*64}, {'missing': 1},
                       {'path': None}, {'name': 'a'*256}):
            with self.subTest(fields=fields), self.assertRaises(ValueError): a.validate_audio_clip(replace(self.clip, **fields))
        for clips in ([self.clip], (None,), (self.clip,)*257):
            with self.assertRaises(ValueError): validate_project(Project(audio_clips=clips))
        for path in (self.root / 'absent', self.root / 'empty.wav'):
            if path.name == 'empty.wav': soundfile.write(path, [], RATE, subtype='FLOAT')
            with self.assertRaises(ValueError): a.audio_clip_from_file(path, 0)

    def test_v5_roundtrip_and_v4_compatibility(self):
        project = Project(audio_clips=(self.clip,))
        path = self.root / 'song.project.json'
        doc = save_project(path, project)
        self.assertEqual(doc['version'], PROJECT_VERSION)
        self.assertEqual(doc['audio_tracks'][0]['clips'][0]['path'], 'tone.wav')
        self.assertNotIn('missing', doc['audio_tracks'][0]['clips'][0])
        self.assertEqual(read_project(path), project)
        self.assertEqual(project_from_document(project_document(project)), project)
        old = project_document(Project()); old['version'] = 4; del old['velocity']
        self.assertEqual(project_from_document(old).audio_clips, ())
        old['audio_clips'] = []
        with self.assertRaises(ValueError): project_from_document(old)
        with patch('phosphor.storage.project_files.os.path.relpath', side_effect=ValueError('different drive')):
            doc = project_document(project, self.root)
        self.assertIsNone(doc['audio_tracks'][0]['clips'][0]['path'])
        self.assertEqual(project_from_document(doc, path=path), project)
        for fields in ({'extra': 1}, {'missing': True}, {'path': 123}):
            bad = project_document(project); bad['audio_tracks'][0]['clips'][0].update(fields)
            with self.assertRaises(ValueError): project_from_document(bad)

    def test_all_resolution_steps_and_precedence(self):
        project_path = self.root / 'project' / 'song.project.json'
        project_path.parent.mkdir()
        paths = [project_path.parent / 'relative.wav', self.path,
                 project_path.parent / 'song media' / self.clip.name, project_path.parent / self.clip.name]
        for path in paths:
            path.parent.mkdir(exist_ok=True)
            if path != self.path: shutil.copyfile(self.path, path)
        for candidate in paths:
            found = a.resolve_audio_clip(self.clip, project_path, 'relative.wav')
            self.assertEqual(found.path, str(candidate.resolve())); self.assertFalse(found.missing)
            candidate.unlink()
        missing = a.resolve_audio_clip(self.clip, project_path, None)
        self.assertTrue(missing.missing); self.assertEqual(missing.path, self.clip.path)

    def test_mismatch_notice_and_last_path_survive_reopen(self):
        project_path = self.root / 'song.project.json'
        project = Project(audio_clips=(self.clip,))
        save_project(project_path, project)
        # Same name and size, different contents.
        soundfile.write(self.path, -self.samples, RATE, subtype='FLOAT')
        notices = []; missing = read_project(project_path, notices)
        self.assertEqual(notices, ['Audio file not found: tone.wav'])
        self.assertTrue(missing.audio_clips[0].missing)
        doc = save_project(project_path, missing)
        self.assertEqual(doc['audio_tracks'][0]['clips'][0]['absolute'], self.clip.path)
        self.assertTrue(read_project(project_path).audio_clips[0].missing)
        self.assertTrue(a.resolve_audio_clip(self.clip, None, 'tone.wav').missing)


if __name__ == '__main__': unittest.main()
