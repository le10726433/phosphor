"""AW1b: audio-track processing through render_song, playback-style blocks and export."""
from dataclasses import dataclass, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile

from phosphor.engine import audio_clips as a, track_chain
from phosphor.engine.audio_clips import AudioTrack
from phosphor.engine.composition import Project, export_song, render_song, song_seconds
from phosphor.engine.signal_engine import Settings
from phosphor.engine.track_chain import ChainState, FeedbackEcho, OnePoleLowpass, RightDelay, TrackChain

RATE = 48000
MACHINE = Settings(sample_rate=RATE, switch_sweep_ms=0.)


@dataclass(frozen=True)
class Saturate:
    """A nonlinear causal stage: tells gain-before-chain from gain-after."""
    drive: float = 3.
    prime_seconds = 0.

    def new_state(self, rate): return None

    def process(self, block, state, rate): return np.tanh(self.drive * block), state


class ChainedTrackTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        t = np.arange(RATE) / RATE  # One second each, generated in memory (no fixtures).
        clips = []
        for i, freq in enumerate((220., 330.)):
            path = root / f'tone{i}.wav'
            noise = np.random.default_rng(i).uniform(-.05, .05, len(t))
            soundfile.write(path, np.column_stack((.3*np.sin(2*np.pi*freq*t) + noise, .2*np.cos(2*np.pi*freq*t) - noise)),
                            RATE, subtype='FLOAT')
            clips.append(replace(a.audio_clip_from_file(path, 0.), track=i))
        self.root = root
        self.project = Project(audio_tracks=(AudioTrack('A'), AudioTrack('B', gain=.5)), audio_clips=tuple(clips))
        self.times = np.arange(9600) / RATE
        self.chain = TrackChain(windowed=(RightDelay(.002),), causal=(OnePoleLowpass(900.), FeedbackEcho(.01, .4)))
        a.clear_decoded()
        self.addCleanup(a.clear_decoded)

    def render(self, times, chains=None, state=None, project=None):
        return render_song(times, project or self.project, MACHINE, chains=chains, chain_state=state)

    def test_empty_chains_mix_exactly_as_before(self):
        plain = self.render(self.times)
        self.assertTrue(np.array_equal(plain, self.render(self.times, chains={})))
        self.assertTrue(np.array_equal(plain, self.render(self.times, chains={0: TrackChain(), 1: TrackChain()})))
        with patch.object(a, 'project_chains', return_value={}) as chains:
            self.assertTrue(np.array_equal(plain, self.render(self.times)))
            chains.assert_called()

    def test_other_tracks_are_untouched(self):
        chains = {0: self.chain}
        both = self.render(self.times, chains, ChainState())
        only_b = replace(self.project, audio_tracks=(replace(self.project.audio_tracks[0], muted=True), self.project.audio_tracks[1]))
        only_a = replace(self.project, audio_tracks=(self.project.audio_tracks[0], replace(self.project.audio_tracks[1], muted=True)))
        self.assertTrue(np.allclose(both, self.render(self.times, chains, ChainState(), only_a) + self.render(self.times, project=only_b), atol=1e-12))
        self.assertFalse(np.allclose(self.render(self.times, project=only_a), self.render(self.times, chains, ChainState(), only_a)))

    def test_playback_blocks_equal_one_render(self):
        chains = {0: self.chain, 1: TrackChain(causal=(OnePoleLowpass(2000.),))}
        whole = self.render(self.times, chains, ChainState())
        for size in (480, 441, 1, 3000):
            state = ChainState()
            blocks = [self.render(self.times[i:i+size], chains, state) for i in range(0, len(self.times), size)]
            self.assertTrue(np.array_equal(whole, np.concatenate(blocks)), size)

    def test_gain_and_meter_follow_the_processed_track(self):
        chains = {1: self.chain}
        peaks = [0., 0.]
        out = render_song(self.times, self.project, MACHINE, chains=chains, chain_state=ChainState(), track_peaks=peaks)
        muted = replace(self.project, audio_tracks=(replace(self.project.audio_tracks[0], muted=True), self.project.audio_tracks[1]))
        self.assertAlmostEqual(peaks[1], float(np.abs(self.render(self.times, chains, ChainState(), muted)).max()), places=12)
        self.assertTrue(np.all(np.isfinite(out)))

    def test_export_equals_contiguous_playback(self):
        chains = {0: self.chain}
        path = self.root / 'out.wav'
        with patch.object(a, 'project_chains', return_value=chains):
            export_song(path, self.project, MACHINE, 0., float(self.project.beats(.5)), format='WAV', depth=32)
            state = ChainState()
            count = round(.5 * RATE)
            times = np.arange(count) / RATE
            playback = np.concatenate([self.render(times[i:i+480], None, state) for i in range(0, count, 480)])
        exported, rate = soundfile.read(path, dtype='float64')
        self.assertEqual(rate, RATE)
        self.assertTrue(np.allclose(exported, np.clip(playback, -1, 1), atol=1e-7))  # 32-bit float WAV.

    def test_seek_primes_and_stateless_matches_a_fresh_state(self):
        chains = {0: self.chain}
        later = self.times[4800:5280]
        self.assertTrue(np.array_equal(self.render(later, chains), self.render(later, chains, ChainState())))
        state = ChainState()
        self.render(self.times[:480], chains, state)
        jumped = self.render(later, chains, state)  # A seek: primed, not continued.
        self.assertTrue(np.array_equal(jumped, self.render(later, chains, ChainState())))

    def test_track_gain_drives_the_chain(self):
        project = replace(self.project, audio_tracks=(replace(self.project.audio_tracks[0], gain=.25), replace(self.project.audio_tracks[1], muted=True)))
        unity = replace(project, audio_tracks=(replace(project.audio_tracks[0], gain=1.), project.audio_tracks[1]))
        chains = {0: TrackChain(causal=(Saturate(),))}
        dry = self.render(self.times, project=unity)
        self.assertTrue(np.allclose(self.render(self.times, chains, project=project), np.tanh(3 * .25 * dry), atol=1e-12))

    def test_silent_clips_leave_the_output_untouched(self):
        project = replace(self.project, audio_tracks=(replace(self.project.audio_tracks[0], gain=.5), self.project.audio_tracks[1]))
        late = np.arange(480) / RATE + 5.  # After both clips: nothing sounds.
        output = np.full((480, 2), -0.)
        a.mix_audio_clips(output, late, project, RATE, peaks=[0., 0.])
        self.assertEqual(output.tobytes(), np.full((480, 2), -0.).tobytes())

    def test_empty_render_keeps_the_peaks(self):
        peaks = [.5, .25]
        for chains in (None, {0: self.chain}):
            out = render_song(np.array([]), self.project, MACHINE, chains=chains, chain_state=ChainState(), track_peaks=peaks)
            self.assertEqual(out.shape, (0, 2))
        self.assertEqual(peaks, [.5, .25])

    def test_history_before_the_song_is_silent(self):
        chains = {0: TrackChain(windowed=(RightDelay(1 / RATE),))}
        only_a = replace(self.project, audio_tracks=(self.project.audio_tracks[0], replace(self.project.audio_tracks[1], muted=True)))
        out = self.render(np.array([.75 / RATE]), chains, project=only_a)
        self.assertEqual(out[0, 1], 0.)

    def test_export_carries_memory_across_its_blocks(self):
        chains = {0: TrackChain(causal=(FeedbackEcho(.3, .6),))}  # The tail rings past each 1 s block.
        path = self.root / 'long.wav'
        seconds = 2.5
        with patch.object(a, 'project_chains', return_value=chains):
            export_song(path, self.project, MACHINE, 0., float(self.project.beats(seconds)), format='WAV', depth=32)
            reference = self.render(np.arange(round(seconds * RATE)) / RATE, None, ChainState())
        exported, _ = soundfile.read(path, dtype='float64')
        self.assertGreater(np.abs(reference[round(1.2 * RATE):, 1]).max(), .01)  # The tail is audible past 1 s.
        self.assertTrue(np.allclose(exported, np.clip(reference, -1, 1), atol=1e-7))

    def test_playback_with_count_in_equals_export(self):
        from phosphor.audio.audio_preview import AudioPreview, playback_block_frames
        chains = {0: self.chain}
        frames = playback_block_frames(RATE)
        settings = MACHINE
        count_in = 4 * frames / RATE
        blocks = 30
        with patch.object(a, 'project_chains', return_value=chains):
            a.prime(self.project, RATE, 0.)  # As start() does before the stream runs.
            player = AudioPreview(); player.sample_rate = RATE
            player.composer = (self.project, 0.)
            player.parameters = (settings, 'mixed')
            player.count_in_seconds = count_in
            played = []
            for _ in range(blocks):
                block = player._next_block(frames); player._publish_block(block, 0., False); played.append(block)
            played = np.concatenate(played)
            song = played[round(count_in * RATE):]
            reference = self.render(np.arange(len(song)) / RATE, None, ChainState())
        self.assertTrue(np.all(played[:round(count_in * RATE)] == 0))
        skip = round(.005 * RATE)  # Playback's start ramp.
        np.testing.assert_allclose(song[skip:], np.clip(reference, -1, 1).astype('float32')[skip:], atol=1e-6)

    def test_duplicate_names_never_swap_memory(self):
        tracks = (AudioTrack('Same'), AudioTrack('Same', gain=.5))
        project = replace(self.project, audio_tracks=tracks)
        swapped = replace(project, audio_tracks=tracks[::-1],
                          audio_clips=tuple(replace(c, track=1 - c.track) for c in project.audio_clips))
        chains = {0: self.chain, 1: self.chain}
        # Same-named tracks are keyed by position and files: after a reorder no key carries over,
        # so each restarts its memory rather than inheriting the other's.
        before = {a.chain_key(project, t) for t in (0, 1)}
        after = {a.chain_key(swapped, t) for t in (0, 1)}
        self.assertEqual(len(before), 2)
        self.assertFalse(before & after)

    def test_failed_and_pending_media(self):
        clip = self.project.audio_clips[0]
        with patch.object(a, 'decoded', return_value=None), patch.object(a, 'decode_failed', return_value=True):
            self.assertIsNone(a._add_clip(lambda: None, self.times, self.project, 0, RATE, 0., False, False))
        with patch.object(a, 'decoded', return_value=None), patch.object(a, 'decode_failed', return_value=False):
            self.assertEqual(a._add_clip(lambda: None, self.times, self.project, 0, RATE, 0., False, False),
                             (clip.path, clip.fingerprint, RATE))
            self.assertIsNone(a._add_clip(lambda: None, self.times, self.project, 0, RATE, 0., False, True))
        self.assertTrue(clip.path)

    def test_reach_matches_the_sample_rounding(self):
        chain = TrackChain(windowed=(RightDelay(.51 / RATE), RightDelay(.51 / RATE)), causal=(OnePoleLowpass(900., .001),))
        before, after = a.chain_reach(chain, RATE)
        self.assertGreaterEqual(round(before * RATE), 1 + 1 + 48)
        self.assertGreaterEqual(round(after * RATE), 0)

    def test_incomplete_history_retries_once_when_decode_finishes(self):
        # The clip sounds only in the prime. Later blocks never request it in their dry rows.
        clip = replace(self.project.audio_clips[0], duration=.1)
        project = replace(self.project, audio_clips=(clip,))
        chain = TrackChain(causal=(OnePoleLowpass(900., 96 / RATE),))
        samples = np.ones((RATE, 2), dtype='float32')
        arrival_times = .1 + (48 + np.arange(16)) / RATE
        with patch.object(a, 'decoded', return_value=samples):
            reference = self.render(arrival_times, {0: chain}, project=project)
        self.assertTrue(reference.any())
        for outcome in ('ready', 'failed', 'still pending'):
            state = ChainState()
            a.clear_decoded()
            with self.subTest(outcome=outcome), patch.object(a, '_request'), \
                    patch.object(track_chain, '_process_causal', wraps=track_chain._process_causal) as process:
                for i in range(7):
                    if i == 1:
                        with a._lock: a._store(('unrelated.wav', '', RATE), samples)
                    if i == 3 and outcome != 'still pending':
                        with a._lock:
                            a._store((clip.path, clip.fingerprint, RATE), samples if outcome == 'ready' else None)
                    times = .1 + (16 * i + np.arange(16)) / RATE
                    out = render_song(times, project, MACHINE, chains={0: chain}, chain_state=state, wait_media=False)
                    prefixes = sum(len(call.args[1]) == 96 for call in process.call_args_list)
                    arrived = i >= 3 and outcome != 'still pending'
                    self.assertEqual(prefixes, 1 + int(arrived))
                    self.assertEqual(bool(state._primed), arrived)
                    if outcome == 'ready' and i == 3:
                        self.assertTrue(np.array_equal(out, reference))

    def test_prime_waits_for_playable_clips_on_muted_causal_tracks(self):
        first, other = self.project.audio_clips
        tracks = tuple(replace(track, muted=True) for track in self.project.audio_tracks)
        project = replace(self.project, audio_tracks=tracks, audio_clips=(
            first, other, replace(first, gain=0., path='zero'), replace(first, muted=True, path='muted')))
        with patch.object(a, 'project_chains', return_value={0: self.chain}), \
                patch.object(a, 'decoded') as decoded, patch.object(a, 'prepare'):
            a.prime(project, RATE, .1)
        decoded.assert_called_once_with(first.path, first.fingerprint, RATE)

    def test_start_warms_eight_tracks_through_first_wrap(self):
        from phosphor.audio.audio_preview import AudioPreview
        from phosphor.audio.simulated_output import SimulatedDevice
        tracks = tuple(AudioTrack(str(i)) for i in range(8))
        clips = tuple(replace(self.project.audio_clips[0], track=i) for i in range(8))
        project = replace(self.project, audio_tracks=tracks, audio_clips=clips, loop=True,
                          loop_start=.2, loop_end=.40123)
        start = float(project.seconds(project.loop_end)) - 34.3 / RATE
        chains = {t: TrackChain(causal=(FeedbackEcho(.0001, .4, .001),)) for t in range(8)}
        for count_in_frames in (0, 9):
            player = AudioPreview()
            player.device = SimulatedDevice(RATE)
            player.composer = (project, start)
            player.count_in_seconds = count_in_frames / RATE
            with self.subTest(count_in=count_in_frames), patch.object(a, 'project_chains', return_value=chains), \
                    patch.object(player, '_run'):
                player.start(MACHINE, 'mixed')
                player.thread.join()
                cold = ChainState()
                self.assertEqual(len(player.chain_state._primed), 16)
                for frame in (0, 32):
                    elapsed = (frame + np.arange(32)) / RATE - count_in_frames / RATE
                    positions = song_seconds(project, start, np.maximum(0., elapsed))
                    reference = self.render(positions, state=cold, project=project)
                    with patch.object(track_chain, '_initial_states', side_effect=AssertionError('cold prime')):
                        actual = render_song(positions, project, MACHINE, chain_state=player.chain_state, wait_media=False)
                    self.assertTrue(np.array_equal(actual, reference))
                    self.assertTrue(actual.any())

    def test_changed_switch_sweep_at_wrap_matches_cold_prime(self):
        clip = self.project.audio_clips[0]
        project = replace(self.project, loop=True, loop_start=.204, loop_end=.4, audio_clips=(
            replace(clip, duration=.1, muted=True), replace(clip, start=.2)))
        chains = {0: TrackChain(causal=(OnePoleLowpass(100., .01),))}
        a.preload(project, RATE)
        start = float(project.seconds(project.loop_end)) - 16 / RATE
        positions = song_seconds(project, start, np.arange(48) / RATE)
        state = ChainState()
        with patch.object(a, 'project_chains', return_value=chains):
            a.warm_chains(project, (start, positions[16]), RATE, 0., state)
            dry = render_song(positions, project, MACHINE, chain_state=ChainState())
            machine = replace(MACHINE, switch_sweep_ms=5.)
            expected = render_song(positions, project, machine, chain_state=ChainState())
            self.assertNotEqual(expected[16:].tobytes(), dry[16:].tobytes())
            actual = render_song(positions, project, machine, chain_state=state, wait_media=False)
            self.assertEqual(actual.tobytes(), expected.tobytes())

    def test_thread_setup_failure_closes_started_stream(self):
        from phosphor.audio.audio_preview import AudioPreview
        from phosphor.audio.simulated_output import SimulatedDevice
        for failure in ('constructor', 'start'):
            player = AudioPreview()
            player.device = SimulatedDevice(RATE)
            with self.subTest(failure=failure), patch('phosphor.audio.audio_preview.threading.Thread') as thread:
                target = thread if failure == 'constructor' else thread.return_value.start
                target.side_effect = RuntimeError('thread setup')
                with self.assertRaisesRegex(RuntimeError, 'thread setup'):
                    player.start(MACHINE, 'mixed')
                self.assertTrue(player.device.streams)
                for stream in player.device.streams:
                    self.assertIsNotNone(stream.origin)
                    self.assertTrue(stream.closed)
                    self.assertFalse(stream.active)
                self.assertFalse(player.running)

    def test_removed_chain_slots_are_dropped_on_project_change(self):
        state = ChainState()
        self.render(self.times[:16], {0: self.chain, 1: self.chain}, state)
        self.assertEqual(len(state._slots), 2)
        changed = replace(self.project, audio_tracks=(self.project.audio_tracks[0], AudioTrack('Renamed')))
        self.render(self.times[16:32], {0: self.chain}, state, changed)
        self.assertEqual(set(state._slots), {a.chain_key(changed, 0)})
        empty = replace(changed, audio_clips=())
        self.render(self.times[32:48], {}, state, empty)
        self.assertFalse(state._slots)

    def test_state_follows_a_reordered_track(self):
        chains = {0: self.chain, 1: self.chain}
        swapped = replace(self.project, audio_tracks=self.project.audio_tracks[::-1],
                          audio_clips=tuple(replace(c, track=1 - c.track) for c in self.project.audio_clips))
        swapped_chains = {0: self.chain, 1: self.chain}
        state = ChainState()
        first = self.render(self.times[:4800], chains, state)
        reordered = self.render(self.times[4800:], swapped_chains, state, swapped)
        whole = self.render(self.times, chains, ChainState())
        self.assertTrue(np.allclose(np.concatenate((first, reordered)), whole, atol=1e-12))


if __name__ == '__main__':
    unittest.main()
