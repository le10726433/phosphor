"""Block, seek and loop behaviour of the per-track audio chain."""

import unittest
from unittest.mock import Mock

import numpy as np

from phosphor.engine.track_chain import (
    CentredAverage, ChainState, FeedbackEcho, OnePoleLowpass, RightDelay,
    TrackChain, _run_end, process_track,
)


def dry_at(times, rate):
    # Evaluate on sample indices so reconstructed window margins use the exact
    # same signal as the original grid, including at negative song times.
    index = np.rint(times * rate).astype(np.int64)
    def noise(seed):
        bits = (index + seed).astype(np.uint32)
        bits = (bits ^ (bits >> 16)) * np.uint32(0x45D9F3B)
        bits = (bits ^ (bits >> 16)) * np.uint32(0x45D9F3B)
        return (bits ^ (bits >> 16)) / 2**32 - 0.5

    tone = 0.3 * np.sin(0.071 * index) + 0.2 * np.sin(0.017 * index)
    return np.column_stack((tone + noise(11), tone + noise(37)))


class TrackChainTests(unittest.TestCase):
    rate = 4000

    def test_discontinuous_clock_comparison_work(self):
        class CountedTimes:
            def __init__(self, rows):
                self.rows = rows
                self.compared = 0

            def __len__(self):
                return len(self.rows)

            def __getitem__(self, key):
                chunk = self.rows[key]
                self.compared += len(chunk)
                return chunk

        times = CountedTimes(np.tile([0., 1.], 4096))
        for first, origin in enumerate(times.rows):
            end = _run_end(times, first + 1, origin, 1, self.rate, .25 / self.rate)
            self.assertEqual(end, first + 1)
        self.assertGreater(times.compared, 0)
        self.assertLessEqual(times.compared, 256 * len(times))

    def test_capped_prime_and_skipped_silent_prefix(self):
        chain = TrackChain(causal=(OnePoleLowpass(20, 1.5), OnePoleLowpass(30, 1.5)))
        times = self.grid(100, 4)
        calls = []
        def dry(at):
            calls.append(len(at))
            return np.zeros((len(at), 2))

        state = ChainState()
        process_track(chain, dry, times, self.rate, state, silent_before=times[0])
        self.assertEqual(calls, [4])
        self.assertFalse(state._primed)
        calls.clear()
        process_track(chain, dry, times, self.rate, state, silent_before=0.)
        self.assertEqual(calls, [2 * self.rate, 4])
        self.assertEqual(len(state._primed), 1)

    def grid(self, first, count):
        return np.arange(first, first + count) / self.rate

    def render(self, chain, times, state=None, key=0):
        return process_track(chain, lambda t: dry_at(t, self.rate), times,
                             self.rate, state, key)

    def exact(self, actual, expected):
        self.assertEqual(actual.dtype, np.float64)
        self.assertTrue(np.array_equal(actual, expected))

    def test_empty_chain(self):
        times = self.grid(0, 4)[[2, 2, 3, 0]]
        expected = dry_at(times, self.rate)
        dry = Mock(return_value=expected)
        self.assertFalse(TrackChain())
        self.assertIs(process_track(TrackChain(), dry, times, self.rate), expected)
        self.assertIs(dry.call_args.args[0], times)
        dry.assert_called_once()

    def test_windowed_hand_calculation_and_random_splits(self):
        times = self.grid(-20, int(0.3 * self.rate))
        delay = RightDelay(2.2 / self.rate)
        average = CentredAverage(1.4 / self.rate)
        cuts = np.random.default_rng(192).choice(np.arange(1, len(times)), 22, replace=False)
        for stages in ((delay,), (average,), (delay, average)):
            with self.subTest(stages=stages):
                def delayed(offset):
                    t = times + offset / self.rate
                    signal = dry_at(t, self.rate)
                    if delay in stages:
                        signal[:, 1] = dry_at(t - 2 / self.rate, self.rate)[:, 1]
                    return signal

                expected = delayed(0)
                if average in stages:
                    expected = (delayed(-1) + expected + delayed(1)) / 3
                chain = TrackChain(windowed=stages)
                self.exact(self.render(chain, times), expected)
                for state in (None, ChainState()):
                    chunks = [self.render(chain, t, state) for t in np.split(times, sorted(cuts))]
                    self.exact(np.concatenate(chunks), expected)

    def test_causal_splits(self):
        times = self.grid(7, 1327)
        lp = OnePoleLowpass(75, 0.02)
        echo = FeedbackEcho(0.003, 0.5, 0.03)
        chains = (TrackChain(causal=(lp,)), TrackChain(causal=(echo,)),
                  TrackChain((RightDelay(0.001), CentredAverage(0.0003)), (lp, echo)))
        for chain in chains:
            whole = self.render(chain, times, ChainState())
            self.exact(self.render(chain, times), whole)
            for cuts in (range(1, len(times)), (480, 960), (12, 75, 104, 707, 1326)):
                with self.subTest(chain=chain, cuts=cuts):
                    state = ChainState()
                    chunks = [self.render(chain, t, state) for t in np.split(times, cuts)]
                    self.exact(np.concatenate(chunks), whole)

    def test_window_times_follow_each_stages_margins_across_splits(self):
        class Stamp:
            uses_times = True
            history_seconds = 2 / self.rate
            lookahead_seconds = 1 / self.rate

            def apply(stage, window, rate, times):
                self.assertEqual(times.dtype, np.float64)
                self.assertEqual(len(times), len(window))
                return window[2:-1] + times[2:-1, None]

        chain = TrackChain((CentredAverage(1 / self.rate), Stamp()))
        times = .12345 + self.grid(0, 300)
        expected = self.render(TrackChain((chain.windowed[0],)), times) + times[:, None]
        state = ChainState()
        for block in np.split(np.arange(len(times)), [1, 7, 129]):
            self.exact(self.render(chain, times[block], state), expected[block])

    def test_causal_impulses(self):
        signal = np.zeros((7, 2))
        signal[0] = (1, -1)
        t = self.grid(0, len(signal))
        def apply(processor):
            return process_track(TrackChain(causal=(processor,)), lambda _: signal,
                                 t, self.rate)

        echo = signal.copy()
        echo[2::2, 1] = (-0.5, -0.25, -0.125)
        self.exact(apply(FeedbackEcho(2 / self.rate, 0.5, 0)), echo)
        cutoff = self.rate * np.log(2) / (2 * np.pi)
        expected = 0.5 ** np.arange(1, 8)[:, None] * signal[0]
        np.testing.assert_allclose(apply(OnePoleLowpass(cutoff, 0)), expected,
                                   rtol=1e-14, atol=0)

    def test_half_sample_clock_across_blocks_and_margins(self):
        rate = 48000
        playhead = np.nextafter(0.5 / rate, np.inf)
        times = playhead + np.arange(4800) / rate
        delay, average = RightDelay(1 / rate), CentredAverage(2 / rate)

        def ramp(t):
            index = np.rint(t * rate)
            return np.column_stack((index, -index))

        for chain in (TrackChain((delay,)), TrackChain((average,)),
                      TrackChain((delay, average), (FeedbackEcho(3 / rate, 0.5, 8 / rate),))):
            expected = process_track(chain, ramp, times, rate).tobytes()
            for size in (480, 1):
                with self.subTest(chain=chain, size=size):
                    state = ChainState()
                    blocks = [process_track(chain, ramp, times[i:i + size], rate, state)
                              for i in range(0, len(times), size)]
                    self.assertEqual(np.concatenate(blocks).tobytes(), expected)

    def test_seeks(self):
        times = self.grid(0, 8000)
        # |dry| <= 1, so a settled echo with feedback 1/2 has |y| <= 2.
        # Priming 22 full delay periods attenuates omitted history by 2**-22.
        for proc, bound in ((OnePoleLowpass(50, 0.1), 1e-9),
                            (FeedbackEcho(0.01, 0.5, 0.22), 2 * 0.5**22)):
            with self.subTest(proc=proc):
                chain = TrackChain(causal=(proc,))
                reference = self.render(chain, times)
                state = ChainState()
                self.render(chain, times[:80], state)
                for start in (7000, 2000):
                    seek = times[start:start + 300]
                    out = self.render(chain, seek, state)
                    self.exact(out, self.render(chain, seek))
                    np.testing.assert_allclose(out, reference[start:start + 300],
                                               rtol=0, atol=bound)

    def test_loop_in_block(self):
        chain = TrackChain(causal=(FeedbackEcho(0.003, 0.8, 0.004),))
        state = ChainState()
        self.render(chain, self.grid(100, 730), state)
        tail, head = self.grid(830, 92), self.grid(100, 81)
        out = self.render(chain, np.r_[tail, head], state)
        self.exact(out[:92], self.render(chain, self.grid(100, 822))[730:])
        self.exact(out[92:], self.render(chain, head))
        self.exact(self.render(chain, self.grid(181, 50), state),
                   self.render(chain, self.grid(100, 131))[81:])

    def test_count_in(self):
        calls = []
        def dry(t):
            self.assertEqual(len(np.unique(t)), len(t))
            calls.append(len(t))
            return dry_at(t, self.rate)

        chain = TrackChain((RightDelay(3 / self.rate), CentredAverage(1 / self.rate)),
                           (OnePoleLowpass(40, 15 / self.rate),))
        start = self.grid(0, 105)
        times = np.r_[np.zeros(500), start]
        out = process_track(chain, dry, times, self.rate, ChainState())
        self.assertCountEqual(calls, [len(start) + 5, 15 + 5])
        fresh = self.render(chain, start)
        self.exact(out[:500], np.repeat(fresh[:1], 500, axis=0))
        self.exact(out[500:], fresh)

    def test_count_in_across_calls(self):
        rate = self.rate
        times = self.grid(37, 1440)
        chain = TrackChain(causal=(FeedbackEcho(3 / rate, 0.75, 17 / rate),))
        state = ChainState()
        dry = Mock(side_effect=lambda t: dry_at(t, rate))
        held = np.full(480, times[0])
        expected = self.render(chain, times)
        for _ in range(20):
            out = process_track(chain, dry, held, rate, state)
            self.assertEqual(out.tobytes(), np.repeat(expected[:1], 480, axis=0).tobytes())
        blocks = [process_track(chain, dry, t, rate, state) for t in np.split(times, 3)]
        self.assertEqual(np.concatenate(blocks).tobytes(), expected.tobytes())
        self.assertEqual([len(c.args[0]) for c in dry.call_args_list], [17, 1, 479, 480, 480])

    def test_loop_prime_cache_and_context_identity(self):
        rate = 1024
        times = np.arange(25, 89) / rate
        chain = TrackChain(causal=(OnePoleLowpass(20, 8 / rate),
                                   FeedbackEcho(3 / rate, 0.8, 16 / rate)))
        state = ChainState()
        context = []
        dry = Mock(side_effect=lambda t: dry_at(t, rate))
        fresh = process_track(chain, dry, times, rate)
        dry.reset_mock()
        for _ in range(4):
            out = process_track(chain, dry, times, rate, state, context=context)
            self.assertEqual(out.tobytes(), fresh.tobytes())
        self.assertEqual([len(c.args[0]) for c in dry.call_args_list], [24] + [64] * 4)

        context = []  # Equal value, different identity. The live slot still continues.
        dry.reset_mock()
        later = times + len(times) / rate
        tail = process_track(chain, dry, later, rate, state, context=context)
        whole = process_track(chain, lambda t: dry_at(t, rate), np.r_[times, later], rate)
        self.assertEqual(tail.tobytes(), whole[len(times):].tobytes())
        out = process_track(chain, dry, times, rate, state, context=context)
        self.assertEqual(out.tobytes(), fresh.tobytes())
        self.assertEqual([len(c.args[0]) for c in dry.call_args_list], [64, 24, 64])

    def test_processor_configuration_compatibility(self):
        rate = self.rate
        times = self.grid(0, 128)
        state = ChainState()
        dry = Mock(side_effect=lambda t: dry_at(t, rate))
        for i, (delay, echo, primes) in enumerate(((1, 2, True), (1, 2, False),
                                                   (1, 4, True), (3, 4, False))):
            chain = TrackChain((RightDelay(delay / rate),),
                               (FeedbackEcho(echo / rate, 0.8, 15 / rate),))
            t = times[32 * i:32 * (i + 1)]
            dry.reset_mock()
            out = process_track(chain, dry, t, rate, state)
            self.assertEqual(dry.call_count, 2 if primes else 1)
            if i == 3:
                # The changed window reads the new delay immediately. The four echo taps
                # carry the preceding output through that edit, in write order.
                expected = self.render(TrackChain(chain.windowed), t)
                echo_state = (previous[-4:, 1].copy(), 0)
                expected, _ = chain.causal[0].process(expected, echo_state, rate)
            else:
                expected = self.render(chain, t if primes else times[:64])[-32:]
            self.assertEqual(out.tobytes(), expected.tobytes())
            previous = out

    def test_incomplete_history_retries_and_excludes_prime_from_cache(self):
        rate = self.rate
        times = self.grid(40, 64)
        chain = TrackChain(causal=(FeedbackEcho(3 / rate, 0.75, 17 / rate),))
        state = ChainState()
        lengths = []
        missing = False

        def dry(t):
            nonlocal missing
            lengths.append(len(t))
            if len(lengths) <= 2:
                missing = True
                return np.zeros((len(t), 2))
            return dry_at(t, rate)

        def incomplete():
            nonlocal missing
            result, missing = missing, False
            return {'missing'} if result else set()

        report = Mock(side_effect=incomplete)
        for t in (times, times + len(times) / rate, times, times):
            out = process_track(chain, dry, t, rate, state, incomplete=report)
            if len(lengths) > 2:
                self.assertEqual(out.tobytes(), self.render(chain, t).tobytes())
        self.assertEqual(lengths, [17, 64, 17, 64, 17, 64, 64])
        self.assertEqual(report.call_count, 4)

    def test_rate_change(self):
        chain = TrackChain(causal=(FeedbackEcho(0.002, 0.75, 0.01),))
        state = ChainState()
        for rate in (48000, 96000):
            start = 0 if rate == 48000 else 0.01
            t = start + np.arange(480) / rate
            dry = Mock(side_effect=lambda t: dry_at(t, rate))
            out = process_track(chain, dry, t, rate, state)
            self.assertEqual(dry.call_count, 2)
            self.exact(out, process_track(chain, dry, t, rate))

    def test_continuity_tolerance_within_and_between_calls(self):
        # Binary-exact timestamps exercise the inclusive quarter-sample boundary.
        rate = 1024
        chain = TrackChain(causal=(OnePoleLowpass(10, 8 / rate),))
        for offset, primes in ((-0.25, 1), (0.25, 1), (-0.26, 2), (0.26, 2)):
            times = np.array([0, 1, 2 + offset, 3 + offset]) / rate
            for split in (False, True):
                with self.subTest(offset=offset, split=split):
                    dry = Mock(side_effect=lambda t: dry_at(t, rate))
                    state = ChainState()
                    chunks = np.split(times, [2]) if split else [times]
                    for chunk in chunks:
                        process_track(chain, dry, chunk, rate, state)
                    self.assertEqual(sum(len(c.args[0]) == 8 for c in dry.call_args_list),
                                     primes)

    def test_signature_change_and_reset(self):
        state = ChainState()
        lp = TrackChain(causal=(OnePoleLowpass(25, 0.01),))
        echo = TrackChain(causal=(FeedbackEcho(0.002, 0.75, 0.01),))
        self.render(lp, self.grid(0, 64), state)
        times = self.grid(64, 64)
        self.exact(self.render(echo, times, state), self.render(echo, times))
        state.reset()
        times = self.grid(128, 64)
        self.exact(self.render(echo, times, state), self.render(echo, times))

    def test_track_keys(self):
        chain = TrackChain(causal=(OnePoleLowpass(1, 0),))
        shared = ChainState()
        times = self.grid(0, 256)
        for first in (0, 64, 128, 192):
            t = times[first:first + 64]
            for key, gain in ((0, 1), (1, -2)):
                def dry(t):
                    return gain * dry_at(t, self.rate)

                out = process_track(chain, dry, t, self.rate, shared, key)
                expected = process_track(chain, dry, times, self.rate)
                self.exact(out, expected[first:first + 64])

    def test_invalid_values(self):
        for bad in (-1, np.inf, np.nan):
            for name, chain in (
                ("history_seconds", TrackChain((RightDelay(bad),))),
                ("lookahead_seconds", TrackChain((Mock(history_seconds=0, lookahead_seconds=bad),))),
                ("prime_seconds", TrackChain(causal=(OnePoleLowpass(20, bad),))),
            ):
                with self.subTest(name=name, bad=bad), self.assertRaisesRegex(ValueError, name):
                    self.render(chain, self.grid(0, 1))
        for rate in (0, -1, np.inf, np.nan):
            with self.assertRaisesRegex(ValueError, "rate"):
                process_track(TrackChain(), Mock(), np.zeros(1), rate)

    # Second review (overseer, November 21, 2026).
    def test_prime_cache_is_keyed_by_the_exact_origin(self):
        rate = 1024
        chain = TrackChain(windowed=(RightDelay(1 / rate),), causal=(OnePoleLowpass(50.0, 4 / rate),))
        render = lambda t, state: process_track(chain, lambda u: dry_at(u, rate), t, rate, state, 0, context="c")
        state = ChainState()
        render(np.array([8.49, 9.49]) / rate, state)
        render(np.array([30.0]) / rate, state)  # Move away, then seek back near the cached origin.
        seek = np.array([8.51, 9.51]) / rate
        self.exact(render(seek, state), render(seek, ChainState()))
        for i in range(20):  # The cache stays bounded.
            render(np.array([100.0 + 3 * i]) / rate, state)
        self.assertLessEqual(len(state._primed), 8)

    def test_context_change_rerenders_a_held_sample(self):
        chain = TrackChain(causal=(OnePoleLowpass(300.0, 0.01),))
        start = self.grid(10, 1)
        state = ChainState()
        process_track(chain, lambda t: dry_at(t, self.rate), np.repeat(start, 5), self.rate, state, 0, context="old")
        louder = lambda t: 2 * dry_at(t, self.rate)
        held = process_track(chain, louder, np.repeat(start, 5), self.rate, state, 0, context="new")
        fresh = process_track(chain, louder, start, self.rate, ChainState(), 0, context="new")
        self.exact(held, np.repeat(fresh, 5, axis=0))

    def test_irregular_times_split_the_same_way_in_any_call_pattern(self):
        chain = TrackChain(windowed=(RightDelay(2 / self.rate),), causal=(FeedbackEcho(3 / self.rate, 0.5, 0.01),))
        times = np.arange(24) * 1.2 / self.rate
        whole = self.render(chain, times, ChainState())
        state = ChainState()
        pieces = np.concatenate([self.render(chain, times[i:i + 1], state) for i in range(24)])
        self.assertEqual(whole.tobytes(), pieces.tobytes())

    def test_unhashable_processors_still_render(self):
        class Unhashable:
            __hash__ = None
            history_seconds = lookahead_seconds = 0.0
            prime_seconds = 0.001

            def __eq__(self, other): return type(other) is type(self)
            def apply(self, window, rate): return window * 0.5
            def new_state(self, rate): return 0
            def process(self, block, state, rate): return block * 0.25, state

        times = self.grid(0, 64)
        for chain in (TrackChain(windowed=(Unhashable(),)), TrackChain(causal=(Unhashable(),))):
            state = ChainState()
            a = self.render(chain, times[:32], state)
            b = self.render(chain, times[32:], state)
            self.exact(np.concatenate((a, b)), self.render(chain, times, ChainState()))


if __name__ == "__main__":
    unittest.main()
