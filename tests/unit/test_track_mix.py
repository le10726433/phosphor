"""AW2 track processors, song-time automation and project integration."""
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

import numpy as np

from phosphor.engine import audio_clips, audio_tracks, clipboard
from phosphor.engine.audio_clips import AudioClip, AudioTrack
from phosphor.engine.composition import Project, render_song
from phosphor.engine.signal_engine import Settings
from phosphor.engine.track_chain import ChainState, TrackChain, process_track, window_margins
from phosphor.engine.track_mix import MAX_DELAY_MS, Pan, TimeDelay, TruePhase, from_lane, to_lane, track_chain_for
from phosphor.storage.project_files import PROJECT_VERSION, project_document, project_from_document


POINTS = ((0., .25, 'hold'), (.003, 1., 'linear'), (.017, 0., 'linear'))


def signal(times):
    x = np.sin(2000 * np.pi * times)
    return np.column_stack((x, x))


def render(processor, times, rate, split=False):
    chain = TrackChain((processor,))
    state = ChainState()
    chunks = np.split(times, [1, 17, 101, 777]) if split else [times]
    return np.concatenate([process_track(chain, signal, t, rate, state) for t in chunks])


class ProcessorTests(unittest.TestCase):
    def test_lane_units_and_pan_law(self):
        for kind, limit in [('pan', 1.), ('phase', 180.), ('delay', MAX_DELAY_MS)]:
            for v in (0., .25, .5, .75, 1.):
                self.assertEqual(from_lane(kind, v), (2 * v - 1) * limit)
                self.assertEqual(to_lane(kind, from_lane(kind, v)), v)
        samples = np.array([[.75, -.5], [-0., 0.], [.2, .3]])
        self.assertEqual(Pan(0.).apply(samples, 48000).tobytes(), samples.tobytes())
        for p in (-1., -.5, .5, 1.):
            out = Pan(p).apply(samples, 48000)
            far = int(p < 0)
            np.testing.assert_array_equal(out[:, 1-far], samples[:, 1-far])
            np.testing.assert_allclose(out[:, far], samples[:, far] * np.sin((1-abs(p))*np.pi/2))
        times = np.array([1., 2., 3.])
        pan = Pan(1., ((1., 0., 'linear'), (3., 1., 'hold')))
        np.testing.assert_array_equal(pan.apply(np.ones((3, 2)), 48000, times),
                                      [[1., 0.], [1., 1.], [0., 1.]])
        times = .00037 + np.arange(900) / 48000
        pan = Pan(-1., POINTS)
        self.assertEqual(render(pan, times, 48000, True).tobytes(),
                         render(pan, times, 48000).tobytes())

    def test_true_phase_quadrature_and_inversion(self):
        for rate in (44100, 48000, 192000):
            with self.subTest(rate=rate):
                times = .037 + np.arange(rate // 20) / rate
                phase = TruePhase(90.)
                out = render(phase, times, rate)
                radius = int(phase.taps_seconds * rate / 2)
                self.assertEqual(window_margins(phase, rate), (radius, radius))
                np.testing.assert_array_equal(out[:, 0], signal(times)[:, 0])
                np.testing.assert_allclose(out[:, 1], np.cos(2000*np.pi*times), atol=1e-3, rtol=0)
                np.testing.assert_allclose(render(TruePhase(180.), times, rate)[:, 1],
                                           -out[:, 0], atol=1e-12, rtol=0)
        self.assertEqual(window_margins(TruePhase(90.), 1000), (20, 20))  # 40 ms taps at 1 kHz: 20 each side.
        bass = .5 + np.arange(4800) / 48000  # 80 Hz: a bass note still turns by 90 degrees (manual §8).
        tone = lambda t: np.column_stack((np.sin(160*np.pi*t), np.sin(160*np.pi*t)))
        turned = process_track(TrackChain(windowed=(TruePhase(90.),)), tone, bass, 48000)
        np.testing.assert_allclose(turned[:, 1], np.cos(160*np.pi*bass), atol=1e-3, rtol=0)
        times = np.arange(2200) / 48000
        degrees = np.deg2rad(np.interp(times, [0., .03], [-180., 180.]))
        phase = TruePhase(17., ((0., 0., 'linear'), (.03, 1., 'linear')))
        whole = render(phase, times, 48000)
        np.testing.assert_allclose(whole[:, 1], np.sin(2000*np.pi*times + degrees), atol=1e-3, rtol=0)
        np.testing.assert_allclose(render(phase, times, 48000, True), whole, atol=1e-12, rtol=0)

    def test_delay_integer_fractional_and_automated(self):
        rate = 1000
        window = np.random.default_rng(712).normal(size=(80, 2))
        rows = np.arange(20, len(window))
        for ms in (20., -3., 0., 4.25, -.5):
            ch = int(ms >= 0)
            expected = window[rows].copy()
            k = int(abs(ms))
            frac = abs(ms) - k
            expected[:, ch] = (1 - frac) * window[rows-k, ch] + frac * window[rows-k-1, ch]
            np.testing.assert_allclose(TimeDelay(ms).apply(window, rate), expected, atol=1e-15, rtol=0)
        times = np.arange(len(window)) / rate
        delay = TimeDelay(7., ((.02, 0., 'hold'), (.04, 1., 'hold')))
        expected = window[rows].copy()
        expected[:20, 0] = window[:20, 0]
        expected[20:, 1] = window[20:60, 1]
        np.testing.assert_array_equal(delay.apply(window, rate, times), expected)
        times = .00013 + np.arange(1100) / 44100
        for delay in (TimeDelay(-1.37), TimeDelay(8., POINTS)):
            self.assertEqual(render(delay, times, 44100, True).tobytes(),
                             render(delay, times, 44100).tobytes())


class TrackModelTests(unittest.TestCase):
    def test_build_active_processors_in_order(self):
        for track, stages in [
                (AudioTrack(), ()), (AudioTrack(delay_ms=4.), ()),
                (AudioTrack(phase_mode='delay', phase=90.), ()),
                (AudioTrack(pan=.2), (Pan,)), (AudioTrack(pan_points=POINTS), (Pan,)),
                (AudioTrack(phase=45.), (TruePhase,)), (AudioTrack(phase_points=POINTS), (TruePhase,)),
                (AudioTrack(phase_mode='delay', delay_ms=2.), (TimeDelay,)),
                (AudioTrack(pan=.2, phase_mode='delay', phase_points=POINTS), (Pan, TimeDelay)),
                (AudioTrack(pan=.2, phase=45.), (Pan, TruePhase))]:
            chain = track_chain_for(track)
            self.assertEqual(tuple(map(type, chain.windowed)), stages)
            chains = audio_clips.project_chains(Project(audio_tracks=(AudioTrack(), track)))
            self.assertEqual(chains, {1: chain} if stages else {})

    def test_invalid_fields(self):
        for key, invalid in [('pan', [False, 1.01, -1.01, float('nan')]),
                             ('phase', [181., -181., '90']), ('phase_mode', [None, 'unknown']),
                             ('delay_ms', [-21., 21., float('inf')]),
                             ('pan_points', [[(0., .5, 'hold')], ((0., -.1, 'hold'),)]),
                             ('phase_points', [((0., .5, 'curve', .5, .5),), ((-1., .5, 'hold'),)])]:
            for value in invalid:
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, key.split('_')[0]):
                    audio_clips.validate_audio_track(AudioTrack(**{key: value}))

    def test_storage_versions_clipboard_and_duplicate(self):
        track = AudioTrack('Mixed', .8, True, True, -.5, POINTS, 'delay', 120., -4.25, POINTS)
        project = Project(audio_tracks=(track, AudioTrack()))
        doc = json.loads(json.dumps(project_document(project)))
        self.assertEqual(doc['version'], PROJECT_VERSION)
        self.assertEqual(project_from_document(doc), project)
        default = doc['audio_tracks'][1]
        self.assertEqual(default, {'name': 'Audio 1', 'clips': []})
        old = dict(doc, version=10, audio_tracks=[default])
        self.assertEqual(project_from_document(old).audio_tracks, (AudioTrack(),))
        for key in ('pan', 'pan_points', 'phase_mode', 'phase', 'delay_ms', 'phase_points'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'version 11'):
                project_from_document(dict(old, audio_tracks=[dict(default, **{key: doc['audio_tracks'][0][key]})]))
        pasted, _ = clipboard.paste_tracks(Project(), clipboard.copy_tracks(project, [0]))
        self.assertEqual(pasted.audio_tracks, (track,))
        copied, (i,) = audio_tracks.duplicate_tracks(project, [0])
        self.assertEqual(copied.audio_tracks[i], replace(track, name='Mixed copy'))

    def test_edits_and_undo_labels(self):
        project = Project(audio_tracks=(AudioTrack('A'), AudioTrack('B')))
        for edit, value, field, label in [(audio_tracks.set_pan, -.5, 'pan', 'pan'),
                                          (audio_tracks.set_phase_mode, 'delay', 'phase_mode', 'phase mode'),
                                          (audio_tracks.set_phase, 45., 'phase', 'phase'),
                                          (audio_tracks.set_delay, 3.5, 'delay_ms', 'delay')]:
            changed = edit(project, [0, 1], value)
            self.assertEqual(changed.audio_tracks, tuple(replace(t, **{field: value}) for t in project.audio_tracks))
            self.assertEqual(audio_tracks.describe(project, changed), 'Change audio track ' + label)
        for kind in ('pan', 'phase'):
            changed = audio_tracks.set_lane(project, 1, kind, POINTS)
            self.assertEqual(changed.audio_tracks, (project.audio_tracks[0], AudioTrack('B', **{kind+'_points': POINTS})))
            self.assertEqual(audio_tracks.describe(project, changed), f'Change audio track {kind} lane')
        with self.assertRaises(ValueError): audio_tracks.set_lane(project, 0, 'delay', POINTS)

    def test_render_song_pan_and_byte_exact_default(self):
        rate = 48000
        times = np.arange(512) / rate
        dry = np.random.default_rng(391).uniform(-.5, .5, (len(times), 2))
        clip = AudioClip(0., 'tone.wav', 'tone', 0, '', len(times)/rate)
        project = Project(audio_tracks=(AudioTrack(),), audio_clips=(clip,))
        machine = Settings(sample_rate=rate, switch_sweep_ms=0.)
        with patch.object(audio_clips, 'decoded', return_value=dry):
            plain = render_song(times, project, machine)
            self.assertEqual(plain.tobytes(), dry.tobytes())
            self.assertEqual(plain.tobytes(), render_song(times, project, machine, chains={}).tobytes())
            panned = audio_tracks.set_pan(project, [0], .25)
            out = render_song(times, panned, machine)
            np.testing.assert_array_equal(out, dry * [np.sin(.75 * np.pi / 2), 1.])


class ReviewFixTests(unittest.TestCase):
    """Codex's AW2 review (November 22, 2026)."""

    def test_track_lanes_are_in_seconds_with_their_own_bound(self):
        late = AudioTrack(pan_points=((120000., .5, 'linear'),))  # Beat 40,000 at 20 BPM.
        audio_clips.validate_audio_track(late)
        for bad in (((-1., .5, 'linear'),), ((400000., .5, 'linear'),), ((0., 1.5, 'linear'),),
                    ((1., .5, 'linear'), (0., .5, 'linear')), ((0., .5, 'curve'),)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                audio_clips.validate_audio_track(AudioTrack(pan_points=bad))

    def test_strict_lane_decoding(self):
        document = project_document(Project(audio_tracks=(AudioTrack(pan_points=((0., .5, 'linear'),)),)))
        document['audio_tracks'][0]['pan_points'] = [[0, 2, 'linear']]
        with self.assertRaises(ValueError): project_from_document(document)

    def test_whole_song_export_keeps_the_delay_tail(self):
        self.assertEqual(audio_clips.processing_tail(Project()), 0.)
        tracks = (AudioTrack(phase_mode='delay', delay_ms=-12.5), AudioTrack(phase_mode='delay', delay_ms=3.))
        self.assertAlmostEqual(audio_clips.processing_tail(Project(audio_tracks=tracks)), .0125)
        automated = AudioTrack(phase_mode='delay', phase_points=((0., .5, 'linear'),))
        self.assertAlmostEqual(audio_clips.processing_tail(Project(audio_tracks=(automated,))), .02)
        self.assertEqual(audio_clips.processing_tail(Project(audio_tracks=(AudioTrack(phase=90.),))), 0.)
