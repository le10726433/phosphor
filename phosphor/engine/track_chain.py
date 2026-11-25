"""Per-track stereo processing at arbitrary song times.

A windowed processor declares nonnegative history_seconds and lookahead_seconds.
At the given rate, round each margin up to H and L samples. Its pure
apply(window, rate) takes H+n+L stereo rows and returns the n processed middle
rows. Each processor consumes its own margins, leaving later processors theirs.
Margins may also be functions of rate. A processor declaring uses_times receives
apply(window, rate, times), with the absolute song seconds of every input row.

A causal processor declares nonnegative prime_seconds and provides new_state(rate)
and process(block, state, rate) -> (out, state). With uses_times, process also
receives the absolute song times as a fourth argument, including during priming.
Its state_key describes the state layout across live edits (default: processor type).
Output has the input shape.
Carrying state across any split must produce bitwise-identical output.
"""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from math import ceil, expm1, isfinite, pi

import numpy as np

MAX_PRIME_SECONDS = 2.0


@dataclass(frozen=True)
class TrackChain:
    windowed: tuple = ()
    causal: tuple = ()

    def __bool__(self):
        return bool(self.windowed or self.causal)


@dataclass
class _Slot:
    rate: float
    origin: float
    counter: int
    signature: tuple
    states: list
    last_time: float
    last_output: np.ndarray | None
    pending: set


class ChainState:
    """Per-track memory carried from one render call to the next, playback and export."""

    def __init__(self):
        self._slots = {}
        self._context = None
        self._primed = OrderedDict()
        self.prime_limit = 8

    def reset(self):
        self._slots.clear()
        self._primed.clear()
        self._context = None
        self.prime_limit = 8

    def use_context(self, context, active_keys=None):
        if self._context is not context:
            self._primed.clear()
            keep = set(active_keys) if active_keys is not None else self._slots.keys()
            for key, slot in list(self._slots.items()):
                if key not in keep or slot.pending:
                    del self._slots[key]
                else:
                    slot.last_output = None
            self._context = context

    def _remember(self, key, states):
        self._primed[key] = states
        self._primed.move_to_end(key)
        while len(self._primed) > self.prime_limit:
            self._primed.popitem(last=False)


def _seconds(value, name):
    if not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return value


def _process_causal(processors, block, states, rate, times):
    for i, processor in enumerate(processors):
        args = (times,) if getattr(processor, 'uses_times', False) else ()
        block, states[i] = processor.process(block, states[i], rate, *args)
    return block


def window_margins(processor, rate):
    def samples(value, name):
        seconds = value(rate) if callable(value) else value
        return ceil(_seconds(seconds, name) * rate)

    return (samples(processor.history_seconds, "history_seconds"),
            samples(processor.lookahead_seconds, "lookahead_seconds"))


def prime_samples(chain, rate):
    requested = sum(ceil(_seconds(p.prime_seconds, "prime_seconds") * rate) for p in chain.causal)
    return min(requested, ceil(MAX_PRIME_SECONDS * rate))


def _run_end(times, end, origin, counter, rate, tolerance):
    chunk = 256
    while end < len(times):
        rows = times[end:end + chunk]
        expected = origin + (counter + np.arange(len(rows))) / rate
        off = np.flatnonzero(np.abs(rows - expected) > tolerance)
        if off.size:
            return end + int(off[0])
        end += len(rows)
        counter += len(rows)
        chunk = min(chunk * 2, 65536)
    return end


def _window_reader(chain, dry_at, rate):
    margins = [window_margins(p, rate) for p in chain.windowed]
    history = sum(h for h, _ in margins)
    lookahead = sum(l for _, l in margins)

    def windowed(origin, first, count):
        indices = np.arange(first - history, first + count + lookahead)
        row_times = origin + indices / rate
        block = dry_at(row_times)
        for processor, (before, after) in zip(chain.windowed, margins):
            if getattr(processor, 'uses_times', False):
                block = processor.apply(block, rate, row_times)
            else:
                block = processor.apply(block, rate)
            row_times = row_times[before:len(row_times) - after]
        return block, row_times

    return windowed, lookahead


def _prime_key(chain, key, origin, rate, sweep):
    cache_key = (key, origin, rate, sweep, (tuple(chain.windowed), tuple(chain.causal)))
    try:
        hash(cache_key)
    except TypeError:
        return None
    return cache_key


def _prime_length(chain, rate, origin, lookahead, silent_before):
    if silent_before is not None and origin + lookahead / rate <= silent_before:
        return 0
    return prime_samples(chain, rate)


def _initial_states(chain, windowed, origin, prime, rate):
    states = [p.new_state(rate) for p in chain.causal]
    if prime:
        prefix, row_times = windowed(origin, -prime, prime)
        _process_causal(chain.causal, prefix, states, rate, row_times)
    return states


def warm(chain, dry_at, origins, rate, state, key=0, context=None, incomplete=None, silent_before=None, sweep=0.):
    """Cache complete causal histories without advancing the live sample clock."""
    state.use_context(context)
    windowed, lookahead = _window_reader(chain, dry_at, rate)
    for origin in origins:
        prime = _prime_length(chain, rate, origin, lookahead, silent_before)
        cache_key = _prime_key(chain, key, origin, rate, sweep)
        if prime and cache_key is not None and cache_key not in state._primed:
            states = _initial_states(chain, windowed, origin, prime, rate)
            if incomplete is None or not incomplete():
                state._remember(cache_key, states)


def process_track(chain, dry_at, times, rate, state=None, key=0, context=None, incomplete=None,
                  silent_before=None, still_pending=None, sweep=0.):
    """Carry causal memory across adjacent runs. Retry when any pending decode resolves."""
    if not isfinite(rate) or rate <= 0:
        raise ValueError("rate must be finite and positive")
    if state is not None:
        state.use_context(context)
    if not chain:
        block = dry_at(times)
        if incomplete is not None:
            incomplete()
        return block

    windowed, lookahead = _window_reader(chain, dry_at, rate)
    structure = (tuple(type(p) for p in chain.windowed),
                 tuple(getattr(p, 'state_key', type(p)) for p in chain.causal))

    times = np.asarray(times, dtype=np.float64)
    if not times.size:
        return np.empty((0, 2), dtype=np.float64)
    tolerance = 0.25 / rate
    holds = np.abs(np.diff(times)) <= tolerance
    counts = None
    last_times = times
    if np.any(holds):
        starts = np.r_[0, np.flatnonzero(~holds) + 1]
        counts = np.diff(starts, append=len(times))
        last_times = times[starts + counts - 1]
        times = times[starts]

    slot = state._slots.get(key) if state is not None else None
    blocks = []
    first = 0
    while first < len(times):
        start = times[first]
        compatible = (slot is not None and slot.rate == rate
                      and (not slot.pending or (still_pending is not None and still_pending(slot.pending)))
                      and slot.signature == structure)
        held = (compatible and slot.last_output is not None
                and abs(start - slot.last_time) <= tolerance)
        pending_prime = None
        continuing = held or (compatible and abs(start - (slot.origin + slot.counter / rate)) <= tolerance)
        if continuing:
            origin, counter, states = slot.origin, slot.counter, slot.states
        else:
            origin, counter = start, 0
            prime = _prime_length(chain, rate, origin, lookahead, silent_before)
            cache_key = _prime_key(chain, key, origin, rate, sweep) if state is not None and prime else None
            cached = state._primed.get(cache_key) if cache_key is not None else None
            if cached is not None and (slot is None or not slot.pending):
                state._primed.move_to_end(cache_key)
                states = deepcopy(cached)
            else:
                states = _initial_states(chain, windowed, origin, prime, rate)
                if cache_key is not None:
                    pending_prime = deepcopy(states)

        if held:
            blocks.append(slot.last_output[None, :])
        count = int(not held)
        end = first + 1
        stop = _run_end(times, end, origin, counter + count, rate, tolerance)
        count += stop - end
        end = stop
        if count:
            block, row_times = windowed(origin, counter, count)
            block = _process_causal(chain.causal, block, states, rate, row_times)
            blocks.append(block)
            counter += count
        pending = incomplete() if incomplete is not None else set()
        if continuing and slot.pending:
            pending.update(slot.pending)
        if state is not None:
            if not pending and pending_prime is not None:
                state._remember(cache_key, pending_prime)
            slot = _Slot(rate, origin, counter, structure, states, last_times[end - 1],
                         blocks[-1][-1].copy(), pending)
            state._slots[key] = slot
        first = end
    out = blocks[0] if len(blocks) == 1 else np.concatenate(blocks)
    return out if counts is None else np.repeat(out, counts, axis=0)


@dataclass(frozen=True)
class RightDelay:
    seconds: float
    lookahead_seconds = 0.0

    @property
    def history_seconds(self):
        return self.seconds

    def apply(self, window, rate):
        history = ceil(self.seconds * rate)
        start = history - round(self.seconds * rate)
        out = window[history:].copy()
        out[:, 1] = window[start:start + len(out), 1]
        return out


@dataclass(frozen=True)
class CentredAverage:
    seconds: float

    @property
    def history_seconds(self):
        return self.seconds

    @property
    def lookahead_seconds(self):
        return self.seconds

    def apply(self, window, rate):
        margin = ceil(self.seconds * rate)
        radius = round(self.seconds * rate)
        n = len(window) - 2 * margin
        width = 2 * radius + 1
        # Sum each row's neighbours in fixed order, even at a block boundary.
        out = np.zeros((n, 2), dtype=np.float64)
        for start in range(margin - radius, margin - radius + width):
            out += window[start:start + n]
        return out / width


@dataclass(frozen=True)
class OnePoleLowpass:
    cutoff_hz: float
    prime_seconds: float = 0.5

    def __post_init__(self):
        if not isfinite(self.cutoff_hz) or self.cutoff_hz < 0:
            raise ValueError("cutoff_hz must be finite and nonnegative")

    def new_state(self, rate):
        return np.zeros(2, dtype=np.float64)

    def process(self, block, state, rate):
        a = -expm1(-2 * pi * self.cutoff_hz / rate)
        out = np.empty_like(block)
        for i, sample in enumerate(block):
            state = state + a * (sample - state)
            out[i] = state
        return out, state


@dataclass(frozen=True)
class FeedbackEcho:
    seconds: float
    feedback: float
    prime_seconds: float = 1.0

    @property
    def state_key(self):
        return type(self), self.seconds  # This processor allocates a delay-sized ring.

    def __post_init__(self):
        _seconds(self.seconds, "seconds")
        if not isfinite(self.feedback):
            raise ValueError("feedback must be finite")

    def new_state(self, rate):
        return np.zeros(max(1, round(self.seconds * rate)), dtype=np.float64), 0

    def process(self, block, state, rate):
        ring, cursor = state
        out = block.copy()
        for i, sample in enumerate(block[:, 1]):
            ring[cursor] = sample + self.feedback * ring[cursor]
            out[i, 1] = ring[cursor]
            cursor = (cursor + 1) % len(ring)
        return out, (ring, cursor)
