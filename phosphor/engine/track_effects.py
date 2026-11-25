"""Composer audio effects. Numeric automation uses absolute one-millisecond intervals."""
from dataclasses import dataclass, replace
from functools import cached_property
import math

import numpy as np
from scipy.signal import lfilter

from phosphor.engine.automation import gain_at

CONTROL_SECONDS = .001
MAX_EFFECTS = 8


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    low: float | None
    high: float | None
    default: float | str
    scale: str = 'linear'
    unit: str = ''
    choices: tuple | None = None


@dataclass(frozen=True)
class EffectSpec:
    kind: str
    label: str
    fields: tuple[Field, ...]


def _mode(*choices):
    return Field('mode', 'Mode', None, None, choices[0], choices=choices)


EFFECTS = {spec.kind: spec for spec in (
    EffectSpec('filter', 'Filter', (
        _mode('low-pass', 'high-pass', 'band-pass'),
        Field('cutoff', 'Cutoff', 20, 20000, 1000, 'log', 'Hz'),
        Field('resonance', 'Resonance', .5, 12, .707, 'log', 'Q'))),
    EffectSpec('saturate', 'Saturate', (
        Field('drive', 'Drive', 0, 36, 6, unit='dB'), Field('mix', 'Mix', 0, 1, 1))),
    EffectSpec('bitcrush', 'Bitcrush', (
        Field('bits', 'Bits', 1, 16, 8), Field('rate', 'Rate', 100, 48000, 8000, 'log', 'Hz'),
        Field('mix', 'Mix', 0, 1, 1))),
    EffectSpec('delay', 'Delay', (
        _mode('normal', 'ping-pong'), Field('time', 'Time', 1, 2000, 250, 'log', 'ms'),
        Field('feedback', 'Feedback', 0, .95, .35), Field('mix', 'Mix', 0, 1, .35))),
    EffectSpec('compressor', 'Compressor', (
        _mode('compress', 'limit'), Field('threshold', 'Threshold', -48, 0, -18, unit='dB'),
        Field('ratio', 'Ratio', 1, 20, 4, 'log'), Field('attack', 'Attack', .1, 100, 5, 'log', 'ms'),
        Field('release', 'Release', 10, 1000, 100, 'log', 'ms'), Field('makeup', 'Makeup', 0, 24, 0, unit='dB'))),
)}


def to_lane(field, value):
    if field.choices: raise ValueError(f'{field.label} has no automation lane.')
    if field.scale == 'log':
        return math.log(value / field.low) / math.log(field.high / field.low)
    return (value - field.low) / (field.high - field.low)


def from_lane(field, v):
    if field.choices: raise ValueError(f'{field.label} has no automation lane.')
    if field.scale == 'log':
        return field.low * (field.high / field.low) ** v
    return field.low + v * (field.high - field.low)


@dataclass(frozen=True)
class AudioEffect:
    kind: str
    values: tuple = ()
    lanes: tuple = ()
    bypass: bool = False


def _spec(kind):
    if not isinstance(kind, str) or kind not in EFFECTS:
        raise ValueError(f'Unknown audio effect kind {kind!r}.')
    return EFFECTS[kind]


def new_effect(kind):
    return AudioEffect(_spec(kind).kind)


def _field(effect, name):
    for field in _spec(effect.kind).fields:
        if field.name == name: return field
    raise ValueError(f'Unknown {effect.kind} field {name!r}.')


def effect_value(effect, name):
    return dict(effect.values).get(name, _field(effect, name).default)


def effect_lane(effect, name):
    if _field(effect, name).choices: raise ValueError(f'{name} has no automation lane.')
    return dict(effect.lanes).get(name, ())


def _check_value(field, value):
    valid = (value in field.choices if field.choices else
             type(value) in (int, float) and field.low <= value <= field.high)
    if not valid: raise ValueError(f'Invalid {field.label} value {value!r}.')


def _check_lane(field, points):
    from phosphor.engine.audio_clips import _validate_lane
    if field.choices: raise ValueError(f'{field.label} has no automation lane.')
    _validate_lane(points, field.label)


def _updated(effect, key, name, value, default):
    entries = dict(getattr(effect, key))
    entries[name] = value
    ordered = tuple((f.name, entries[f.name]) for f in _spec(effect.kind).fields
                    if f.name in entries and (f.name != name or value != default))
    return replace(effect, **{key: ordered})


def with_value(effect, name, value):
    field = _field(effect, name)
    _check_value(field, value)
    return _updated(effect, 'values', name, value, field.default)


def with_lane(effect, name, points):
    _check_lane(_field(effect, name), points)
    return _updated(effect, 'lanes', name, points, ())


def validate_effect(effect):
    if not isinstance(effect, AudioEffect) or type(effect.bypass) is not bool:
        raise ValueError('Invalid audio effect or bypass flag.')
    fields = _spec(effect.kind).fields
    for entries, check in ((effect.values, _check_value), (effect.lanes, _check_lane)):
        if not isinstance(entries, tuple): raise ValueError('Effect fields must be a tuple.')
        previous = -1
        for entry in entries:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise ValueError('Expected an effect field and value.')
            name, value = entry
            field = _field(effect, name)
            index = fields.index(field)
            if index <= previous: raise ValueError('Duplicate or unordered effect fields.')
            check(field, value)
            if value == (() if check is _check_lane else field.default):
                raise ValueError(f'Omit default {field.label} entries.')
            previous = index
    return effect


def _bound(effect, name, extreme):
    bound = min if extreme == 'low' else max
    values = [effect_value(effect, name)]
    field = _field(effect, name)
    values.extend(from_lane(field, v) for _, v, _ in effect_lane(effect, name))
    return bound(values)


def effect_tail_seconds(effect):
    if effect.kind == 'filter':
        decay = 6.9 * _bound(effect, 'resonance', 'high') / (math.pi * _bound(effect, 'cutoff', 'low'))
        return min(2., max(.05, decay))
    if effect.kind != 'delay': return 0.
    time = _bound(effect, 'time', 'high') / 1000
    feedback = _bound(effect, 'feedback', 'high')
    repeats = 1 if feedback == 0 else 1 + math.floor(math.log(.001) / math.log(feedback))
    return min(10., time * repeats)


def _filter(block, state, rate, times, mode, cutoff, resonance):
    omega = 2 * math.pi * min(cutoff, .45 * rate) / rate
    c = math.cos(omega)
    alpha = math.sin(omega) / (2 * resonance)
    if mode == 'band-pass': b = (alpha, 0, -alpha)
    elif mode == 'high-pass': b = ((1+c)/2, -(1+c), (1+c)/2)
    else: b = ((1-c)/2, 1-c, (1-c)/2)
    return lfilter(b, (1+alpha, -2*c, 1-alpha), block, axis=0, zi=state)


def _saturate(block, state, rate, times, drive, mix):
    g = 10 ** (drive / 20)
    return block + mix * (np.tanh(g * block) / math.tanh(g) - block), state


def _bitcrush(block, state, output_rate, times, bits, rate, mix):
    step = 2 / (2 ** round(bits))
    wet = np.rint(np.clip(block, -1, 1) / step) * step
    indices = np.floor(times * rate)
    if rate < output_rate:
        first = np.r_[True, indices[1:] != indices[:-1]]
        if indices[0] == state[0]: wet[0] = state[1]
        wet = wet[np.maximum.accumulate(np.where(first, np.arange(len(block)), 0))]
    return block + mix * (wet - block), (indices[-1], wet[-1].copy())


def _delay(block, state, rate, times, mode, time, feedback, mix):
    """Ring stores input plus feedback. The output adds mix times the delayed ring."""
    ring, cursor = state
    distance = max(1, round(time * rate / 1000))
    out = np.empty_like(block)
    for start in range(0, len(block), distance):
        dry = block[start:start + distance]
        write = (cursor + np.arange(len(dry))) % len(ring)
        wet = ring[(write - distance) % len(ring)]
        out[start:start + len(dry)] = dry + mix * wet
        if mode == 'ping-pong':
            store = feedback * wet[:, ::-1]
            store[:, 0] += .5 * (dry[:, 0] + dry[:, 1])
        else:
            store = dry + feedback * wet
        ring[write] = store
        cursor = (cursor + len(dry)) % len(ring)
    return out, (ring, cursor)


def _compressor(block, state, rate, times, mode, threshold, ratio, attack, release, makeup):
    """Ramp from the previous gain toward a target derived from the last completed peak."""
    previous, peak, start_gain, end_gain = state
    index = math.floor(times[0] / CONTROL_SECONDS)
    ceiling = 10 ** (threshold / 20)
    if index != previous:
        target = 1. if peak <= ceiling else (ceiling / peak) ** (1 if mode == 'limit' else 1 - 1/ratio)
        tau_ms = attack if target < end_gain else release
        start_gain = end_gain
        end_gain += (target - end_gain) * -math.expm1(-1000 * CONTROL_SECONDS / tau_ms)
        peak = 0.
    peak = max(peak, float(np.abs(block).max()))
    fraction = np.clip((times - index * CONTROL_SECONDS) / CONTROL_SECONDS, 0, 1)
    gain = start_gain + fraction * (end_gain - start_gain)
    if mode == 'limit':
        # One shared safety gain, even on a transient, preserves the stereo figure.
        amplitude = np.abs(block).max(axis=1)
        gain = np.minimum(gain, ceiling / np.where(amplitude > 0, amplitude, 1))
    boost = 10 ** (makeup / 20)
    out = block * (gain * boost)[:, None]
    if mode == 'limit':
        limit = 10 ** ((threshold + makeup) / 20)
        np.clip(out, -limit, limit, out=out)
    return out, (index, peak, start_gain, end_gain)


_RUN = dict(filter=_filter, bitcrush=_bitcrush, saturate=_saturate, delay=_delay, compressor=_compressor)


@dataclass(frozen=True)
class _Processor:
    effect: AudioEffect
    uses_times = True

    @cached_property
    def delay_bucket(self):
        field = _field(self.effect, 'time')
        time = effect_value(self.effect, 'time')
        lane_max = max((v for _, v, _ in effect_lane(self.effect, 'time')), default=0.)
        # Compare each value in its own units so a log-lane round trip at 125 ms
        # cannot push the delay into the 250 ms bucket.
        return next(ms for ms in (62.5, 125, 250, 500, 1000, 2000)
                    if time <= ms and lane_max <= to_lane(field, ms))

    @cached_property
    def state_key(self):
        e = self.effect
        if e.kind == 'delay': return e.kind, effect_value(e, 'mode'), self.delay_bucket
        if e.kind == 'filter': return e.kind, effect_value(e, 'mode')
        return (e.kind,)

    @cached_property
    def prime_seconds(self):
        e = self.effect
        if e.kind == 'delay': return min(4., effect_tail_seconds(e))
        if e.kind == 'compressor': return min(1., .005 * _bound(e, 'release', 'high'))
        if e.kind == 'bitcrush': return min(.01, 1 / _bound(e, 'rate', 'low'))
        if e.kind == 'filter': return .05
        return effect_tail_seconds(e)

    def tail_seconds(self, rate):
        return effect_tail_seconds(self.effect)

    def new_state(self, rate):
        # Keep pristine state through leading silence, including a partial control segment.
        return None

    def _active_state(self, rate):
        kind = self.effect.kind
        if kind == 'filter': return np.zeros((2, 2)), None, None, None
        if kind == 'delay':
            length = math.ceil(self.delay_bucket * rate / 1000) + math.ceil(CONTROL_SECONDS * rate)
            return np.zeros((length, 2)), 0
        if kind == 'bitcrush': return None, None
        if kind == 'compressor': return None, 0., 1., 1.
        return ()

    def process(self, block, state, rate, times):
        from phosphor.engine.track_mix import LANE_CLOCK
        if state is None:
            first = 0
            if not len(block) or not block[0].any():
                audible = np.any(block != 0, axis=1)
                if not audible.any(): return block, None
                first = audible.argmax()
            state = self._active_state(rate)
            if first:
                out = block.copy()
                out[first:], state = self.process(block[first:], state, rate, times[first:])
                return out, state
        e = self.effect
        run = _RUN[e.kind]
        fields = EFFECTS[e.kind].fields
        values = {f.name: f.default for f in fields}
        values.update(e.values)
        if not e.lanes and e.kind != 'compressor':
            if e.kind == 'filter':
                out, zi = run(block, state[0], rate, times, **values)
                return out, (zi, None, values['cutoff'], values['resonance'])
            return run(block, state, rate, times, **values)
        indices = np.floor(times / CONTROL_SECONDS)
        starts = np.r_[0, np.flatnonzero(indices[1:] != indices[:-1]) + 1]
        clock = indices[starts] * CONTROL_SECONDS
        automated = {name: from_lane(_field(e, name), gain_at(points, LANE_CLOCK, clock))
                     for name, points in e.lanes}
        out = np.empty_like(block)
        for i, (start, end) in enumerate(zip(starts, np.r_[starts[1:], len(block)])):
            values.update((name, float(series[i])) for name, series in automated.items())
            if e.kind == 'filter':
                zi, previous, cutoff, resonance = state
                index = indices[start]
                if previous != index:
                    if not np.isfinite(zi).all() or np.any(np.abs(zi) > 1e4):
                        zi.fill(0.)
                    zi[np.abs(zi) < 1e-30] = 0.
                    if cutoff is not None and 'cutoff' in automated:
                        values['cutoff'] = min(cutoff * 2**.5, max(cutoff / 2**.5, values['cutoff']))
                    if resonance is not None and 'resonance' in automated:
                        values['resonance'] = min(resonance * math.exp(.25),
                                                  max(resonance / math.exp(.25), values['resonance']))
                    cutoff, resonance = values['cutoff'], values['resonance']
                values.update(cutoff=cutoff, resonance=resonance)
                out[start:end], zi = run(block[start:end], zi, rate, times[start:end], **values)
                state = zi, index, cutoff, resonance
                continue
            out[start:end], state = run(block[start:end], state, rate, times[start:end], **values)
        return out, state


def effect_processor(effect):
    return _Processor(effect)
