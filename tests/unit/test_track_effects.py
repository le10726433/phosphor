"""Per-track effects, control clocks, storage and full playback/export integration."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from phosphor.engine import audio_clips, audio_tracks, clipboard, track_effects as fx
from phosphor.engine.audio_clips import AudioTrack
from phosphor.engine.composition import Project, export_song, render_song
from phosphor.engine.signal_engine import Settings
from phosphor.engine.track_chain import ChainState, process_track
from phosphor.engine.track_mix import track_chain_for
from phosphor.storage.project_files import project_document, project_from_document
from scipy.signal import lfilter


def effect(kind, **values):
    e = fx.new_effect(kind)
    for name, value in values.items(): e = fx.with_value(e, name, value)
    return e


def apply(e, block, rate):
    processor = fx.effect_processor(e)
    return processor.process(block, processor.new_state(rate), rate, np.arange(len(block))/rate)[0]


def tone(t):
    return np.column_stack((.4 * np.sin(t * 728), .32 * np.cos(t * 1742)))


class EffectTests(unittest.TestCase):
    def test_fast_cutoff_automation_stays_bounded(self):
        rate = 192000
        field = fx.EFFECTS['filter'].fields[1]
        points = tuple((i / 1000, fx.to_lane(field, (20, 750)[i % 2]), 'hold') for i in range(256))
        e = fx.with_lane(effect('filter', resonance=12), 'cutoff', points)
        impulse = np.zeros((256 * 192, 2))
        impulse[0] = .5
        out = apply(e, impulse, rate)
        self.assertTrue(np.isfinite(out).all())
        self.assertLess(np.abs(out).max(), 10)

    def test_effects_keep_pristine_state_through_silence(self):
        rate = 44100
        times = np.arange(123) / rate
        for kind in fx.EFFECTS:
            with self.subTest(kind=kind):
                processor = fx.effect_processor(effect(kind))
                silence = np.zeros((len(times), 2))
                out, state = processor.process(silence, processor.new_state(rate), rate, times)
                np.testing.assert_array_equal(out, silence)
                self.assertEqual(state, processor.new_state(rate))

    def test_delay_storage_and_mode_keys(self):
        rate = 48000
        def delay(ms):
            return fx.effect_processor(effect('delay', time=ms))
        self.assertEqual(delay(10).state_key, delay(62.5).state_key)
        self.assertNotEqual(delay(62.5).state_key, delay(63).state_key)
        field = next(f for f in fx.EFFECTS['delay'].fields if f.name == 'time')
        lane = ((0., fx.to_lane(field, 125), 'hold'),)
        processor = fx.effect_processor(fx.with_lane(effect('delay', time=2), 'time', lane))
        _, (ring, _) = processor.process(np.ones((1, 2)), processor.new_state(rate), rate, np.zeros(1))
        self.assertEqual(len(ring), 6000 + 48)
        self.assertNotEqual(fx.effect_processor(effect('filter')).state_key,
                            fx.effect_processor(effect('filter', mode='high-pass')).state_key)

    def test_serial_tails(self):
        delay = effect('delay', time=500, feedback=0, mix=1)
        track = AudioTrack(effects=(delay, delay))
        self.assertEqual(audio_clips.processing_tail(Project(audio_tracks=(track,))), 1.)
        track = replace(track, phase_mode='delay', delay_ms=20)
        self.assertEqual(audio_clips.processing_tail(Project(audio_tracks=(track,))), 1.02)
        self.assertAlmostEqual(fx.effect_tail_seconds(effect('filter', cutoff=20, resonance=12)),
                               6.9 * 12 / (np.pi * 20))

    def test_nonfinite_float_media_decodes_as_silence(self):
        import soundfile
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'nonfinite.wav'
            soundfile.write(path, [.25, np.nan, np.inf, -np.inf], 48000, subtype='FLOAT')
            clip = audio_clips.audio_clip_from_file(path, 0.)
            samples = audio_clips.decoded(clip.path, clip.fingerprint, 48000)
            np.testing.assert_array_equal(samples, [[.25, .25], [0, 0], [0, 0], [0, 0]])

    def test_oversized_integer_lane_in_project(self):
        doc = project_document(Project(audio_tracks=(AudioTrack(),)))
        for node in ([10**400, .5, 'hold'], [0., 10**400, 'hold']):
            doc['audio_tracks'][0]['effects'] = [{'kind': 'filter', 'lanes': {'cutoff': [node]}}]
            with self.assertRaises(ValueError):
                project_from_document(doc)

    def test_filter_reference(self):
        rate = 8000
        block = tone(np.arange(512)/rate)
        for mode in ('low-pass', 'high-pass', 'band-pass'):
            for cutoff in (800, 20000):
                omega = 2*np.pi*min(cutoff, .45*rate)/rate
                c, alpha = np.cos(omega), np.sin(omega)/(2*.707)
                b = {'low-pass': np.array([1, 2, 1])*(1-c)/2,
                     'high-pass': np.array([1, -2, 1])*(1+c)/2,
                     'band-pass': np.array([alpha, 0, -alpha])}[mode]
                expected = lfilter(b, [1+alpha, -2*c, 1-alpha], block, axis=0)
                np.testing.assert_allclose(apply(effect('filter', mode=mode, cutoff=cutoff), block, rate),
                                           expected, atol=1e-14)

    def test_saturate_and_bitcrush(self):
        rate = 48000
        block = tone(np.arange(514)/rate)
        g = 10**(.3)
        wet = np.tanh(g*block)/np.tanh(g)
        np.testing.assert_allclose(apply(effect('saturate', mix=.3), block, rate), block+.3*(wet-block))
        for hz in (14001, 48000):
            ids = np.floor(np.arange(len(block))/rate*hz)
            first = np.searchsorted(ids, ids) if hz < rate else np.arange(len(block))
            wet = np.round(block[first]*8)/8
            np.testing.assert_array_equal(apply(effect('bitcrush', bits=4, rate=hz, mix=.25), block, rate),
                                          block+.25*(wet-block))

    def test_delay_echoes(self):
        block = np.zeros((64, 2))
        block[0] = [.8, .4]
        for mode in ('normal', 'ping-pong'):
            expected = block.copy()
            for i in range(1, 8):
                if mode == 'normal': expected[i*8] = block[0] * .25 * .5**(i-1)
                else: expected[i*8, (i-1)%2] = .6 * .25 * .5**(i-1)
            np.testing.assert_allclose(apply(effect('delay', time=2, mode=mode, feedback=.5, mix=.25),
                                             block, 4000), expected)

    def test_compressor_curve_and_limit(self):
        rate = 48000
        x = .8 * np.sin(2*np.pi*np.arange(4800)/48)
        block = np.column_stack((x, -.3*x))
        for mode in ('compress', 'limit'):
            out = apply(effect('compressor', mode=mode, threshold=-20, makeup=6, attack=.1), block, rate)
            exponent = 1 if mode == 'limit' else .75
            expected = block * (.1/.8)**exponent * 10**.3
            np.testing.assert_allclose(out[-480:], expected[-480:], atol=3e-5)
            np.testing.assert_allclose(out[:, 1], -.3*out[:, 0], atol=1e-15)
            if mode == 'limit': self.assertLessEqual(abs(out).max(), .1*10**.3)

    def test_irregular_splits_and_lane_mapping(self):
        rate = 44100
        times = .00038 + np.arange(2037)/rate
        for kind, spec in fx.EFFECTS.items():
            e = fx.new_effect(kind)
            for automated in (False, True):
                if automated:
                    for field in spec.fields:
                        if field.choices: continue
                        self.assertAlmostEqual(fx.from_lane(field, fx.to_lane(field, field.default)), field.default)
                        e = fx.with_lane(e, field.name, ((0., .15, 'linear'), (.03, .73, 'hold')))
                with self.subTest(kind=kind, automated=automated):
                    chain = track_chain_for(AudioTrack(effects=(e,)))
                    whole = process_track(chain, tone, times, rate)
                    state = ChainState()
                    parts = np.split(times, [1, 31, 43, 44, 109, 473, 480, 481, 977, 1234, 2012])
                    actual = np.concatenate([process_track(chain, tone, t, rate, state) for t in parts])
                    np.testing.assert_array_equal(actual, whole)

    def test_bypass_and_live_edit(self):
        rate = 4000
        times = np.arange(24)/rate
        e = effect('delay', time=2, feedback=0, mix=.2)
        track = AudioTrack(effects=(e,))
        for bypassed in (replace(track, effects_bypass=True), AudioTrack(effects=(replace(e, bypass=True),))):
            self.assertFalse(track_chain_for(bypassed))
            np.testing.assert_array_equal(process_track(track_chain_for(bypassed), tone, times, rate), tone(times))
        def impulse(t):
            return np.repeat((np.rint(t*rate) == 0)[:, None].astype(float), 2, axis=1)
        for edit, expected in ((fx.with_value(e, 'mix', .7), .7), (effect('saturate'), 0.)):
            state = ChainState()
            process_track(track_chain_for(track), impulse, times[:5], rate, state)
            chain = track_chain_for(AudioTrack(effects=(edit,)))
            actual = process_track(chain, impulse, times[5:], rate, state)
            np.testing.assert_array_equal(actual[3], [expected, expected])

    def test_bounded_work(self):
        rate = 48000
        block = np.ones((480, 2))
        e = fx.new_effect('filter')
        with patch.object(fx, 'lfilter', wraps=lfilter) as calls:
            apply(e, block, rate)
            self.assertEqual(calls.call_count, 1)
            calls.reset_mock()
            apply(fx.with_lane(e, 'cutoff', ((0., .5, 'hold'),)), block, rate)
            self.assertEqual(calls.call_count, 10)

    def test_lane_overrides_static_on_absolute_control_grid(self):
        rate = 48000
        times = .00051 + np.arange(200)/rate
        e = fx.with_lane(effect('saturate', drive=36), 'drive',
                         ((0., 0., 'linear'), (.004, 1., 'hold')))
        dry = tone(times)
        control = np.floor(times/.001)*.001
        g = 10**(36*np.minimum(1., control/.004)/20)
        expected = np.tanh(g[:, None]*dry)/np.tanh(g[:, None])
        np.testing.assert_allclose(process_track(track_chain_for(AudioTrack(effects=(e,))), tone, times, rate),
                                   expected, atol=1e-15)

    def test_seek_prime_depends_on_values_and_uses_song_time(self):
        rate = 8000
        times = .12 + np.arange(48)/rate
        points = ((0., 0., 'linear'), (.2, 1., 'hold'))
        e = fx.with_lane(effect('filter'), 'cutoff', points)
        state = ChainState()
        for resonance in (.6, 8):
            chain = track_chain_for(AudioTrack(effects=(fx.with_value(e, 'resonance', resonance),)))
            actual = process_track(chain, tone, times, rate, state)
            # Construct the entire prime and audible span on the song clock.
            at = times[0] + np.arange(-400, len(times))/rate
            processor = chain.causal[0]
            expected, _ = processor.process(tone(at), processor.new_state(rate), rate, at)
            np.testing.assert_array_equal(actual, expected[-len(times):])

    def test_storage_and_clipboard(self):
        e = fx.with_lane(effect('delay', time=20, mode='ping-pong'), 'feedback', ((.4, .2, 'hold'),))
        self.assertEqual(fx.with_value(e, 'time', 250).values, (('mode', 'ping-pong'),))
        self.assertEqual(fx.with_lane(e, 'feedback', ()).lanes, ())
        track = AudioTrack(effects=(e, replace(effect('saturate'), bypass=True),
                                    effect('compressor', release=125.5)), effects_bypass=True)
        project = Project(audio_tracks=(track,))
        doc = project_document(project)
        self.assertEqual(project_from_document(doc), project)
        payload = clipboard.from_text(clipboard.to_text(clipboard.copy_tracks(project, [0])))
        pasted, _ = clipboard.paste_tracks(Project(), payload)
        self.assertEqual(pasted.audio_tracks, (track,))
        self.assertEqual(audio_tracks.describe(Project(audio_tracks=(AudioTrack(),)), project), 'Change audio track effects')
        for key, value in (('effects', []), ('effects_bypass', False)):
            doc['audio_tracks'] = [{'name': 'T', 'clips': [], key: value}]
            with self.assertRaisesRegex(ValueError, 'version 12'): project_from_document(dict(doc, version=11))
        for bad in ([{'kind': 'unknown'}], [{'kind': 'delay', 'values': {'fb': .35}}],
                    [{'kind': 'delay', 'values': {'feedback': 1}}], [{'kind': 'delay', 'bypass': 1}],
                    [{'kind': 'filter', 'values': {'mode': 'other'}}], [{'kind': 'filter'}]*9,
                    [{'kind': 'filter', 'lanes': {'mode': []}}],
                    [{'kind': 'filter', 'lanes': {'cutoff': [[0, 2, 'hold']]}}],
                    [{'kind': 'filter', 'lanes': {'cutoff': [[0, .5, 'curve']]}}]):
            doc['audio_tracks'] = [{'name': 'T', 'clips': [], 'effects': bad}]
            with self.subTest(bad=bad), self.assertRaises(ValueError): project_from_document(doc)

    def test_playback_matches_whole_song_export_including_echoes(self):
        import soundfile
        rate = 48000
        machine = Settings(sample_rate=rate)
        with TemporaryDirectory() as directory:
            source = Path(directory)/'tone.wav'
            dest = Path(directory)/'export.wav'
            soundfile.write(source, tone(np.arange(4800)/rate), rate, subtype='FLOAT')
            clip = audio_clips.audio_clip_from_file(source, 3.8)
            effects = (effect('filter'), effect('delay', time=30, feedback=.5))
            project = Project(bpm=120, audio_tracks=(AudioTrack(effects=effects),), audio_clips=(clip,))
            export_song(dest, project, machine, depth=32)
            saved, _ = soundfile.read(dest, dtype='float32')
            end = float(project.seconds(project.end_beats))
            self.assertEqual(len(saved), round((end+.05+.30)*rate))
            self.assertGreater(np.abs(saved[round(end*rate):]).max(), .01)
            times = np.arange(len(saved))/rate
            state = ChainState()
            playback = np.concatenate([render_song(times[i:i+480], project, machine, chain_state=state)
                                       for i in range(0, len(times), 480)])
            np.testing.assert_array_equal(playback.astype('float32'), saved)
