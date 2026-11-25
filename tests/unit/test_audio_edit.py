"""Audio-track edits (A2): move, trim, split, duplicate, fades, gain lane, their undo and storage."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
import soundfile

from phosphor.storage.project_files import PROJECT_VERSION
from phosphor.editor.history import History, State, describe
from phosphor.engine import audio_clips as a
from phosphor.engine import audio_edit as e
from phosphor.engine.composition import Project, export_song, render_song, validate_project
from phosphor.engine.signal_engine import Settings
from phosphor.storage.project_files import project_document, project_from_document, read_project, save_project
from phosphor.storage.project_files import PROJECT_VERSION

RATE = 48000
MACHINE = Settings(sample_rate=RATE)
SECONDS = .5  # Tone length; 120 BPM, so one beat is 24000 samples.


class AudioEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        a.clear_decoded(); self.addCleanup(a.clear_decoded)
        t = np.arange(round(SECONDS * RATE)) / RATE
        self.samples = np.column_stack((.4*np.sin(2*np.pi*220*t), .3*np.cos(2*np.pi*330*t))).astype('float32')
        self.path = self.root / 'tone.wav'
        soundfile.write(self.path, self.samples, RATE, subtype='FLOAT')
        self.clip = a.audio_clip_from_file(self.path, 1.)
        self.project = Project(audio_clips=(self.clip,))
        self.times = np.arange(3 * RATE) / RATE

    def render(self, project, times=None):
        return render_song(self.times if times is None else times, project, MACHINE)

    def test_import_records_the_file_length(self):
        self.assertEqual(self.clip.source_duration, SECONDS)
        self.assertEqual(a.source_end(self.clip), SECONDS)
        self.assertEqual(a.source_end(replace(self.clip, source_duration=None, offset=.1, duration=.2)), .1 + .2)
        with self.assertRaises(ValueError): a.validate_audio_clip(replace(self.clip, duration=SECONDS + .01))

    def test_move_keeps_samples_and_order(self):
        other = replace(self.clip, start=3., name='later')
        project = Project(audio_clips=(self.clip, other))
        moved, index = e.move(project, 0, 4.)
        self.assertEqual(index, 1)
        self.assertEqual([c.name for c in moved.audio_clips], ['later', 'tone.wav'])
        alone, _ = e.move(self.project, 0, 2.)
        np.testing.assert_array_equal(self.render(alone, self.times + .5), self.render(self.project))
        self.assertEqual(e.move(self.project, 0, -3.)[0].audio_clips[0].start, 0.)

    def test_trim_start_keeps_audio_in_song_time(self):
        trimmed, _ = e.trim_start(self.project, 0, 1.25)  # .125 s later = 6000 samples.
        clip = trimmed.audio_clips[0]
        self.assertEqual((clip.start, clip.offset, clip.duration), (1.25, .125, SECONDS - .125))
        before, after = self.render(self.project), self.render(trimmed)
        cut = round(.625 * RATE)
        np.testing.assert_array_equal(after[cut:], before[cut:])
        self.assertFalse(np.any(after[:cut]))
        # Extending out again stops at the file's first sample, and back to the original exactly.
        restored, _ = e.trim_start(trimmed, 0, 0.)
        self.assertEqual(restored.audio_clips[0].offset, 0.)
        self.assertAlmostEqual(restored.audio_clips[0].start, 1.)
        np.testing.assert_array_equal(self.render(restored), before)
        # Never shorter than MIN_SECONDS.
        short, _ = e.trim_start(self.project, 0, 9.)
        self.assertAlmostEqual(short.audio_clips[0].duration, e.MIN_SECONDS)

    def test_trim_end_and_file_limit(self):
        trimmed, _ = e.trim_end(self.project, 0, 1.5)  # .25 s of audio left.
        self.assertEqual(trimmed.audio_clips[0].duration, .25)
        before, after = self.render(self.project), self.render(trimmed)
        end = round(.75 * RATE)
        np.testing.assert_array_equal(after[:end], before[:end]); self.assertFalse(np.any(after[end:]))
        longer, _ = e.trim_end(trimmed, 0, 20.)
        self.assertEqual(longer.audio_clips[0].duration, SECONDS)
        np.testing.assert_array_equal(self.render(longer), before)
        faded = replace(self.clip, fade_in=.4, fade_out=.4)
        clip = e.trim_end(Project(audio_clips=(faded,)), 0, 1.2)[0].audio_clips[0]
        self.assertEqual((clip.fade_in, clip.fade_out), (clip.duration, clip.duration))

    def test_split_is_seamless(self):
        lane = ((0., 1., 'linear'), (.4, .25, 'linear'))
        for clip in (self.clip, replace(self.clip, gain=.8, fade_in=.05, fade_out=.1, gain_points=lane)):
            with self.subTest(clip=clip):
                project = Project(audio_clips=(clip,))
                split, index = e.split(project, 0, 1.5)  # .25 s in: a whole sample.
                self.assertEqual(index, 1)
                left, right = split.audio_clips
                self.assertEqual((left.duration, right.start, right.offset, right.duration), (.25, 1.5, .25, .25))
                self.assertEqual((left.fade_in, left.fade_out, right.fade_in, right.fade_out), (clip.fade_in, 0., 0., clip.fade_out))
                np.testing.assert_allclose(self.render(split), self.render(project), rtol=0, atol=1e-12)
                if not clip.gain_points: np.testing.assert_array_equal(self.render(split), self.render(project))
                else:
                    self.assertEqual(left.gain_points[-1][0], .25); self.assertEqual(right.gain_points[0][0], 0.)
                    self.assertAlmostEqual(right.gain_points[0][1], e.lane_value(lane, .25))
        for beat in (1., 1.0001, 2., 0.5):
            with self.assertRaises(ValueError): e.split(self.project, 0, beat)

    def test_duplicate_follows_and_adds(self):
        copy, index = e.duplicate(self.project, 0)
        self.assertEqual(index, 1)
        self.assertEqual(copy.audio_clips[1], replace(self.clip, start=2.))
        np.testing.assert_array_equal(self.render(copy), self.render(self.project) + self.render(self.project, self.times - .5))
        placed, index = e.duplicate(self.project, 0, 0.)
        self.assertEqual((index, placed.audio_clips[0].start), (0, 0.))

    def test_delete(self):
        project = Project(audio_clips=(self.clip, replace(self.clip, start=2.)))
        self.assertEqual(e.delete(project, 1), (Project(audio_clips=(self.clip,)), 0))
        self.assertEqual(e.delete(self.project, 0), (replace(self.project, audio_clips=()), None))

    def test_fades_gain_and_routing(self):
        faded, _ = e.set_fades(self.project, 0, fade_in=.1)
        faded, _ = e.set_fades(faded, 0, fade_out=9.)
        clip = faded.audio_clips[0]
        self.assertEqual((clip.fade_in, clip.fade_out), (.1, SECONDS))
        self.assertEqual(e.set_fades(faded, 0, fade_in=-1)[0].audio_clips[0].fade_in, 0.)
        self.assertEqual(e.set_gain(self.project, 0, 5.)[0].audio_clips[0].gain, 2.)
        self.assertEqual(e.set_routing(self.project, 0, 'x')[0].audio_clips[0].routing, 'x')
        with self.assertRaises(ValueError): e.set_routing(self.project, 0, 'z')
        # The fade's first sample is silent and it reaches the tone after fade_in.
        out = self.render(faded)
        start = RATE // 2
        np.testing.assert_array_equal(out[start], 0.)
        k = round(.1 * RATE)
        np.testing.assert_array_equal(out[start + k + 1], self.samples[k + 1].astype('float64') * min(1., (len(self.samples) - k - 1) / len(self.samples)))

    def test_gain_lane_mix(self):
        lane = ((0., 1., 'linear'), (.2, 0., 'hold'), (.3, .5, 'linear'))
        project, _ = e.set_gain_points(self.project, 0, lane)
        k = np.arange(len(self.samples))
        expected = np.zeros((len(self.times), 2))
        start = RATE // 2
        gain = a.gain_at(lane, a.LANE_CLOCK, k / RATE)
        expected[start:start + len(k)] = self.samples.astype('float64') * gain[:, None]
        np.testing.assert_array_equal(self.render(project), expected)
        flat, _ = e.set_gain_points(self.project, 0, ((0., 1., 'linear'), (.4, 1., 'linear')))
        np.testing.assert_array_equal(self.render(flat), self.render(self.project))
        for bad in (((0., 1.5, 'linear'),), ((0., .5, 'curve', .1, .2),), ((.2, .5, 'linear'), (.1, .5, 'linear'))):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_project(replace(self.project, audio_clips=(replace(self.clip, gain_points=bad),)))

    def test_gain_lane_point_helpers(self):
        points, index = e.add_gain_node((), SECONDS, .2, .5)
        self.assertEqual((points, index), (((0., 1., 'linear'), (.2, .5, 'linear'), (SECONDS, 1., 'linear')), 1))
        points, index = e.add_gain_node(points, SECONDS, .2, .7)  # Same time: a new gain.
        self.assertEqual((points[1], index), ((.2, .7, 'linear'), 1))
        self.assertEqual(e.move_gain_node(points, 1, 9., -1.)[1], (SECONDS, 0., 'linear'))
        self.assertEqual(e.move_gain_node(points, 1, -1., 2.)[1], (0., 1., 'linear'))
        self.assertEqual(e.set_gain_shape(points, 0, 'hold')[0], (0., 1., 'hold'))
        self.assertEqual(e.delete_gain_node(points, 1), ((0., 1., 'linear'), (SECONDS, 1., 'linear')))
        self.assertEqual(e.delete_gain_node(((0., 1., 'linear'), (1., .5, 'linear')), 0), ())
        shifted = e.shift_points(((0., 0., 'linear'), (.4, 1., 'linear')), -.1)
        self.assertEqual(shifted[0][0], 0.); self.assertAlmostEqual(shifted[0][1], .25); self.assertAlmostEqual(shifted[1][0], .3)
        self.assertEqual(e.lane_value((), 3.), 1.)

    def test_trim_shifts_the_gain_lane(self):
        lane = ((0., 0., 'linear'), (.4, 1., 'linear'))
        project, _ = e.set_gain_points(self.project, 0, lane)
        trimmed, _ = e.trim_start(project, 0, 1.25)
        np.testing.assert_allclose(self.render(trimmed)[round(.625 * RATE):], self.render(project)[round(.625 * RATE):], rtol=0, atol=1e-12)

    def test_each_edit_is_one_undo_step(self):
        history = History(); history.reset(State(self.project))
        edits = (('Move audio clip', lambda p: e.move(p, 0, 2.)),
                 ('Trim audio clip', lambda p: e.trim_start(p, 0, 2.25)),
                 ('Trim audio clip', lambda p: e.trim_end(p, 0, 2.75)),
                 ('Fade audio clip', lambda p: e.set_fades(p, 0, .05, .05)),
                 ('Edit audio gain lane', lambda p: e.set_gain_points(p, 0, e.add_gain_node((), p.audio_clips[0].duration, .1, .5)[0])),
                 ('Change audio clip gain', lambda p: e.set_gain(p, 0, .5)),
                 ('Change audio routing', lambda p: e.set_routing(p, 0, 'mono')),
                 ('Split audio clip', lambda p: e.split(p, 0, 2.5)),
                 ('Duplicate audio clip', lambda p: e.duplicate(p, 1)),
                 ('Delete audio clip', lambda p: e.delete(p, 0)))
        states = [self.project]
        for label, edit in edits:
            project, _ = edit(states[-1])
            self.assertEqual(describe(states[-1], project), label)
            history.push(describe(states[-1], project), State(project), now=len(states)); states.append(project)
        for label, _ in reversed(edits):
            self.assertEqual(history.undo_label, label)
            states.pop(); self.assertEqual(history.undo().project, states[-1])
        self.assertFalse(history.can_undo)
        self.assertEqual(describe(Project(), e.add(Project(), self.clip)[0]), 'Add audio clip')

    def test_storage_roundtrip_and_versions(self):
        clip = replace(self.clip, offset=.1, duration=.3, gain_points=((0., .5, 'hold'), (.2, 1., 'linear')))
        project = Project(audio_clips=(clip,))
        path = self.root / 'song.project.json'
        doc = save_project(path, project)
        self.assertEqual(doc['version'], PROJECT_VERSION)
        self.assertEqual(doc['audio_tracks'][0]['clips'][0]['source_duration'], SECONDS)
        self.assertEqual(read_project(path), project)
        old = project_document(Project(audio_clips=(replace(clip, gain_points=(), source_duration=None),)))
        old['audio_clips'] = old.pop('audio_tracks')[0]['clips']
        self.assertNotIn('gain_points', old['audio_clips'][0]); self.assertNotIn('source_duration', old['audio_clips'][0])
        old['version'] = 7
        self.assertIsNone(project_from_document(old).audio_clips[0].source_duration)
        bad = project_document(project); bad['version'] = 7
        bad['audio_clips'] = bad.pop('audio_tracks')[0]['clips']
        with self.assertRaises(ValueError): project_from_document(bad)

    def test_playback_equals_export_with_edited_clips(self):
        project, _ = e.set_fades(self.project, 0, .05, .1)
        project, _ = e.split(project, 0, 1.5)
        project, _ = e.duplicate(project, 1, 3.)
        project, _ = e.trim_start(project, 2, 3.1)
        project, _ = e.set_gain_points(project, 0, ((0., .2, 'linear'), (.2, 1., 'hold')))
        project, _ = e.set_routing(project, 2, 'y')
        export = self.root / 'export.wav'
        export_song(export, project, MACHINE, format='WAV', depth=32)
        samples, rate = soundfile.read(export, dtype='float32', always_2d=True)
        times = np.arange(len(samples)) / RATE
        np.testing.assert_array_equal(samples, render_song(times, project, MACHINE).astype('float32'))
        rng = np.random.default_rng(7); parts, start = [], 0
        while start < len(times):
            end = start + int(rng.integers(1, 1900)); parts.append(render_song(times[start:end], project, MACHINE)); start = end
        np.testing.assert_array_equal(np.concatenate(parts).astype('float32'), samples)

    def test_waveform_peaks(self):
        self.assertIsNone(a.waveform(str(self.path), self.clip.fingerprint))  # Never reads the file itself.
        a.decoded(str(self.path), self.clip.fingerprint, RATE)
        wave = a.waveform(str(self.path), self.clip.fingerprint)
        self.assertEqual(wave.block_seconds, a.PEAK_BLOCK / RATE)
        self.assertEqual(len(wave.peaks), -(-len(self.samples) // a.PEAK_BLOCK))
        self.assertAlmostEqual(wave.peak, float(np.abs(self.samples).max()), places=6)
        self.assertIs(a.waveform(str(self.path), self.clip.fingerprint), wave)
        a.decoded(str(self.path), self.clip.fingerprint, 44100); a._peaks.clear()
        self.assertEqual(a.waveform(str(self.path), self.clip.fingerprint).block_seconds, a.PEAK_BLOCK / 44100)
        self.assertIsNone(a.waveform(str(self.root / 'absent.wav'), ''))


if __name__ == '__main__':
    unittest.main()
