"""Linked audio media, decoding and sample-clock mixing (no Qt)."""
from collections import OrderedDict
from dataclasses import dataclass, replace
import hashlib
import logging
import math
from pathlib import Path
import re
from threading import Condition, Event, Lock, Thread
import time

import numpy as np
import soundfile

from phosphor.engine.automation import gain_at, validate_points, MAX_POINTS
from phosphor.engine.track_chain import prime_samples, process_track, warm, window_margins
from phosphor.engine.track_effects import MAX_EFFECTS, effect_tail_seconds, validate_effect
from phosphor.engine.track_mix import MAX_DELAY_MS, track_chain_for

ROUTINGS = ('stereo', 'mono', 'x', 'y')
MAX_AUDIO_CLIPS = 256
MAX_AUDIO_TRACKS = 32
TRACK_NAME_LIMIT = 64
MAX_AUDIO_SECONDS = 6 * 3600.
GAIN_RANGE = (0., 2.)
AUDIO_FILE_TYPES = ('.wav', '.flac', '.ogg', '.mp3', '.aif', '.aiff')
GAIN_LANE_SHAPES = ('linear', 'hold')
LANE_CLOCK = (1., 0.)  # gain_at reads the lane in seconds from the clip start.
PEAK_BLOCK = 256  # Samples per waveform peak.


@dataclass(frozen=True)
class AudioTrack:
    """One Composer audio track's header. Its clips refer to its index in the project."""
    name: str = 'Audio 1'
    gain: float = 1.
    muted: bool = False
    collapsed: bool = False
    pan: float = 0.
    pan_points: tuple = ()
    phase_mode: str = 'true'
    phase: float = 0.
    delay_ms: float = 0.
    phase_points: tuple = ()
    effects: tuple = ()
    effects_bypass: bool = False


TRACK_LANE_SECONDS = 300000.  # MAX_BEATS at the slowest tempo (20 BPM): any song second.


def _validate_lane(points, label):
    """A track lane (AW2): (song seconds, value 0..1, 'linear'|'hold') points, in time order."""
    if not isinstance(points, tuple) or len(points) > MAX_POINTS:
        raise ValueError(f'{label}: use at most {MAX_POINTS} nodes.')
    previous, same = -1., 0
    for point in points:
        if not isinstance(point, tuple) or len(point) != 3 or point[2] not in GAIN_LANE_SHAPES:
            raise ValueError(f'{label} nodes need seconds, a value and a linear or hold segment.')
        seconds, value = point[:2]
        if not all(type(n) is int or (type(n) is float and math.isfinite(n)) for n in (seconds, value)):
            raise ValueError(f'{label} nodes need finite numbers.')
        same = same + 1 if seconds == previous else 1
        if not 0 <= seconds <= TRACK_LANE_SECONDS or seconds < previous or same > 2:
            raise ValueError(f'{label}: node times must not decrease within 0–{TRACK_LANE_SECONDS:g} s; at most two at one time.')
        if not 0 <= value <= 1: raise ValueError(f'{label} node values must be 0–1.')
        previous = seconds


def validate_audio_track(track):
    if not isinstance(track, AudioTrack): raise ValueError('Invalid audio track.')
    if not isinstance(track.name, str) or not track.name.strip() or len(track.name) > TRACK_NAME_LIMIT:
        raise ValueError('Audio track names need 1–64 characters.')
    if type(track.gain) not in (int, float) or not GAIN_RANGE[0] <= track.gain <= GAIN_RANGE[1]:
        raise ValueError('Audio track gain must be 0–2.')
    if type(track.muted) is not bool: raise ValueError('Audio track mute must be on or off.')
    if type(track.collapsed) is not bool: raise ValueError('Audio track collapse must be on or off.')
    if track.phase_mode not in ('true', 'delay'):
        raise ValueError('Audio track phase mode must be true or delay.')
    for name, value, limit in (('pan', track.pan, 1.), ('phase', track.phase, 180.),
                               ('delay in ms', track.delay_ms, MAX_DELAY_MS)):
        if type(value) not in (int, float) or not -limit <= value <= limit:
            raise ValueError(f'Audio track {name} must be between {-limit:g} and {limit:g}.')
    _validate_lane(track.pan_points, 'Audio track pan lane')
    _validate_lane(track.phase_points, 'Audio track phase lane')
    if not isinstance(track.effects, tuple) or len(track.effects) > MAX_EFFECTS:
        raise ValueError(f'Use a tuple of at most {MAX_EFFECTS} audio track effects.')
    if type(track.effects_bypass) is not bool: raise ValueError('Invalid audio track effects bypass.')
    for effect in track.effects: validate_effect(effect)
    return track


@dataclass(frozen=True)
class AudioClip:
    start: float
    path: str
    name: str
    size: int
    fingerprint: str
    duration: float
    offset: float = 0.
    gain: float = 1.
    fade_in: float = 0.
    fade_out: float = 0.
    routing: str = 'stereo'
    missing: bool = False  # Runtime only: never saved.
    gain_points: tuple = ()  # Gain lane (seconds from the clip start, 0..1, 'linear'|'hold'); () = 1.
    source_duration: float | None = None  # Seconds in the file; None = offset + duration (older projects).
    muted: bool = False  # Saved (project version 9): silent, like a muted scene clip.
    track: int = 0


def source_end(clip):
    """Seconds into the file where it ends, the limit for trimming the clip's end out."""
    return clip.offset + clip.duration if clip.source_duration is None else clip.source_duration


def fingerprint(path):
    size = Path(path).stat().st_size
    digest = hashlib.sha256(f'{size}\n'.encode())
    with open(path, 'rb') as stream:
        digest.update(stream.read(min(size, 1024 * 1024)))
        if size > 1024 * 1024:
            stream.seek(-1024 * 1024, 2)
            digest.update(stream.read(1024 * 1024))
    return digest.hexdigest()


def audio_clip_from_file(path, start):
    try:
        path = Path(path).resolve()
        info = soundfile.info(str(path))
        if info.frames <= 0 or info.samplerate <= 0:
            raise ValueError('The audio file is empty.')
        clip = AudioClip(start, str(path), path.name, path.stat().st_size,
                         fingerprint(path), info.frames / info.samplerate,
                         source_duration=info.frames / info.samplerate)
        validate_audio_clip(clip)
        return clip
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError(f'Could not read audio file: {error}') from error


def validate_audio_clip(clip):
    from phosphor.engine.composition import MAX_BEATS, _finite
    if not isinstance(clip, AudioClip): raise ValueError('Invalid audio clip.')
    values = {key: _finite(getattr(clip, key), 'Audio clip ' + key.replace('_', ' '))
              for key in ('start', 'duration', 'offset', 'gain', 'fade_in', 'fade_out')}
    if not 0 <= values['start'] <= MAX_BEATS: raise ValueError('Audio clip start exceeds the beat limit.')
    if values['offset'] < 0: raise ValueError('Audio clip offset must be non-negative.')
    if not 0 < values['duration'] <= MAX_AUDIO_SECONDS: raise ValueError('Audio clip duration must be positive and at most six hours.')
    if not GAIN_RANGE[0] <= values['gain'] <= GAIN_RANGE[1]: raise ValueError('Audio clip gain must be 0–2.')
    if any(not 0 <= values[key] <= values['duration'] for key in ('fade_in', 'fade_out')):
        raise ValueError('Audio clip fades must be between zero and its duration.')
    if type(clip.track) is not int or clip.track < 0: raise ValueError('Invalid audio clip track.')
    if clip.routing not in ROUTINGS: raise ValueError('Invalid audio clip routing.')
    if not isinstance(clip.name, str) or len(clip.name) > 255: raise ValueError('Audio clip name must be at most 255 characters.')
    if not isinstance(clip.path, str): raise ValueError('Audio clip path must be text.')
    if type(clip.size) is not int or clip.size < 0: raise ValueError('Audio clip size must be a non-negative integer.')
    if not isinstance(clip.fingerprint, str) or (clip.fingerprint and not re.fullmatch('[0-9a-f]{64}', clip.fingerprint)):
        raise ValueError('Invalid audio clip fingerprint.')
    if type(clip.missing) is not bool: raise ValueError('Audio clip missing must be on or off.')
    if type(clip.muted) is not bool: raise ValueError('Audio clip mute must be on or off.')
    if clip.source_duration is not None:
        length = _finite(clip.source_duration, 'Audio clip source duration')
        if not 0 < length <= MAX_AUDIO_SECONDS or values['offset'] + values['duration'] > length * (1 + 1e-9) + 1e-9:
            raise ValueError('Audio clip offset and duration must lie inside its file.')
    _validate_lane(clip.gain_points, 'Audio clip gain lane')
    return clip


# Decoded media (audit P9) --------------------------------------------------------
# Keyed by (path, fingerprint, rate). The cache is bounded in bytes (and entries, since a failed
# decode is a None entry of no size), preferring the most recently prepared project's files. Lookups that must not
# block (the audio thread, the stopped Composer's display) pass wait=False: a miss returns None,
# heard as silence for that clip, and queues the decode on one worker thread. Everything else
# (export, tests, the relink dialog's progress worker) waits, and a decode already running for
# the same key is waited for rather than repeated.
DECODE_CACHE_BYTES = 2 * 2**30
DECODE_CACHE_ENTRIES = 2 * MAX_AUDIO_CLIPS
_cache = OrderedDict()
_cache_bytes = 0
_array_refs = {}  # id(samples) -> number of cache keys holding it.
_wanted = frozenset()  # Decode keys of the most recently prepared project, in its requested rates.
_lock = Lock()
_generation = 0  # Bumped by clear_decoded: decodes started before it are not stored.
_ready = 0  # Bumped whenever the cache changes, so views and display caches can see new media.
_pending = {}  # key -> Event, set when the decode running for key has finished.
_retry_after = {}  # Memory pressure is transient. Keep export errors distinct from unreadable media.
_queue = []  # Keys waiting for the worker, first first.
_wake = Condition(_lock)
_worker = None
decodes = 0  # Files decoded (tests and the bench count them).


_peaks = OrderedDict()


def clear_decoded():
    global _generation, _cache_bytes, _ready, _wanted
    with _lock:
        _cache.clear()
        _retry_after.clear()
        _array_refs.clear()
        _wanted = frozenset()
        _peaks.clear()
        _queue.clear()
        _cache_bytes = 0
        _generation += 1
        _ready += 1


def media_generation():
    """A number that changes whenever decoded media appear or are cleared."""
    return _ready


def _read(path, rate):
    """The file's first two channels (mono doubled) at `rate`, float32 and read-only.

    Without resampling the file is read as float32 directly: the same values as reading float64
    and rounding (PCM and float files scale by powers of two), at half the peak memory. A
    resampled file is still read and filtered in float64, so its samples stay as they were.
    """
    samples = _read_stereo(path, rate)
    if samples.nbytes > DECODE_CACHE_BYTES:
        logging.getLogger(__name__).warning('%s decoded to %d bytes, exceeding the %d-byte cache limit.',
                                            path, samples.nbytes, DECODE_CACHE_BYTES)
    samples.setflags(write=False)
    return samples


def _read_stereo(path, rate, seconds=None):
    """Writable stereo samples for clip playback or a duration-limited preview."""
    with soundfile.SoundFile(path) as stream:
        native = stream.samplerate
        frames = -1 if seconds is None else max(0, round(seconds * native))
        samples = stream.read(frames=frames, dtype='float32' if native == rate else 'float64',
                              always_2d=True)
    samples = np.repeat(samples, 2, axis=1) if samples.shape[1] == 1 else samples[:, :2]
    np.nan_to_num(samples, copy=False, nan=0., posinf=0., neginf=0.)
    if native != rate and len(samples):
        from scipy.signal import resample_poly
        g = math.gcd(rate, native)
        samples = resample_poly(samples, rate // g, native // g, axis=0)
    return np.ascontiguousarray(samples, dtype='float32')


def _store(key, samples):
    """Insert a new key under the lock. Aliases share one charge until the last key leaves."""
    global _cache_bytes, _ready
    _cache[key] = samples
    _retry_after.pop(key, None)
    if samples is not None:
        refs = _array_refs.get(id(samples), 0)
        if not refs: _cache_bytes += samples.nbytes
        _array_refs[id(samples)] = refs + 1
    while len(_cache) > 1 and (_cache_bytes > DECODE_CACHE_BYTES or len(_cache) > DECODE_CACHE_ENTRIES):
        oldest = next(iter(_cache))
        victim = next((k for k in _cache if k != key and k not in _wanted), oldest)
        old = _cache.pop(victim)
        if old is not None:
            _array_refs[id(old)] -= 1
            if not _array_refs[id(old)]:
                del _array_refs[id(old)]
                _cache_bytes -= old.nbytes
    _ready += 1


def _decode(key, generation, wait):
    global decodes
    path, _, rate = key
    try:
        try: samples = _read(path, rate)
        except MemoryError as error:
            with _lock:
                if generation == _generation and key not in _cache:
                    _retry_after[key] = time.monotonic() + 5.
            if wait:
                raise MemoryError(f'Insufficient memory to decode {path} at {rate} Hz') from error
            return None
        except (OSError, RuntimeError, ValueError): samples = None
        except Exception:
            # Treat unexpected decoder faults as unreadable, avoiding repeated worker failures.
            logging.getLogger(__name__).exception('Could not decode %s', path)
            samples = None
        with _lock:
            if generation == _generation and key not in _cache: _store(key, samples)
            return _cache.get(key, samples)
    finally:
        with _lock:
            decodes += 1
            _pending.pop(key).set()


def decode_failed(path, fingerprint, rate):
    """Whether this file's decode at `rate` finished and failed (cached as unreadable)."""
    key = (path, fingerprint, rate)
    with _lock: return key in _cache and _cache[key] is None


def _still_pending(keys):
    # Both ready and permanently unreadable keys are present in the cache.
    with _lock: return all(key not in _cache for key in keys)


def _backing_off(key):
    deadline = _retry_after.get(key)
    if deadline is None: return False
    if time.monotonic() < deadline: return True
    del _retry_after[key]
    return False


def decoded(path, fingerprint, rate, wait=True):
    """Samples (N, 2) float32 at `rate`, or None if the file can't be read.

    wait=False never reads the file: on a miss it queues the decode and returns None.
    Waiting callers receive MemoryError while a decode is backing off after memory pressure.
    """
    key = (path, fingerprint, rate)
    while True:
        with _lock:
            if key in _cache:
                _cache.move_to_end(key)
                return _cache[key]
            if not wait:
                _request(key); return None
            if _backing_off(key):
                raise MemoryError(f'Insufficient memory to decode {path} at {rate} Hz')
            event = _pending.get(key)
            if event is None:
                if key in _queue: _queue.remove(key)
                _pending[key] = Event()
                generation = _generation
                break
        event.wait()  # Another thread is decoding it; then look again.
    return _decode(key, generation, wait=True)


def _request(key):
    """Called with the lock held: queue a decode for the worker unless it is cached or under way."""
    global _worker
    if key in _cache or key in _pending or key in _queue or _backing_off(key): return
    _queue.append(key)
    if _worker is None or not _worker.is_alive():
        _worker = Thread(target=_work, name='audio-decode', daemon=True)
        _worker.start()
    _wake.notify()


def _work():
    while True:
        with _lock:
            while not _queue: _wake.wait()
            key = _queue.pop(0)
            if key in _cache or key in _pending: continue
            _pending[key] = Event()
            generation = _generation
        _decode(key, generation, wait=False)


def wait_idle(timeout=None):
    """Wait until the worker has nothing queued or running (tests and the bench); False on timeout."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        with _lock:
            if not _queue and not _pending: return True
            events = list(_pending.values())
        if deadline is not None and time.monotonic() >= deadline: return False
        if events: events[0].wait(None if deadline is None else max(0., deadline - time.monotonic()))
        else: time.sleep(.001)  # Queued, and the worker hasn't claimed it yet.


def needed(project):
    """(path, fingerprint) of every file the project plays, in clip order (muted clips last)."""
    clips = [c for c in project.audio_clips if not c.missing and c.gain > 0]
    files = []
    tracks = project.audio_tracks
    for clip in sorted(clips, key=lambda c: c.muted or tracks[c.track].muted or tracks[c.track].gain == 0):
        if (clip.path, clip.fingerprint) not in files: files.append((clip.path, clip.fingerprint))
    return files


def prepare(project, rates):
    """Queue decodes of the project's files at each rate, on the worker; never blocks.

    Requests queued for an earlier project that this one no longer uses are dropped. Muted
    clips are prepared too (after the others), so unmuting never waits for a decode.
    """
    global _wanted
    files = needed(project)
    keys = [(path, fingerprint, rate) for rate in dict.fromkeys(rates) for path, fingerprint in files]
    with _lock:
        _wanted = frozenset(keys[:DECODE_CACHE_ENTRIES])
        wanted = set(keys)
        _queue[:] = [key for key in _queue if key in wanted]
        for key in keys: _request(key)


def preload(project, rate):
    """Decode the project's files at `rate` now (waits); muted clips too, so unmuting never decodes."""
    for path, fingerprint in needed(project): decoded(path, fingerprint, rate)


def prime(project, rate, start, seconds=2.):
    """Wait for the files of clips that sound within `seconds` of song second `start` (and of the
    loop start when looping), so playback that starts or seeks into cold media is not silent;
    queue the rest. Called before an output stream starts, once its rate is known."""
    chains = project_chains(project)
    reach = {t: chain_reach(chain, rate) for t, chain in chains.items() if chain}
    starts = [float(start)]
    if project.loop: starts.append(float(project.seconds(project.loop_start)))
    for clip in project.audio_clips:
        track = project.audio_tracks[clip.track]
        if clip.missing or clip.muted or clip.gain == 0: continue
        chain = chains.get(clip.track)
        if (track.muted or track.gain == 0) and not (chain and chain.causal): continue
        before, after = reach.get(clip.track, (0., 0.))
        spans = [(a - before, a + seconds + after) for a in starts]
        s0 = float(project.seconds(clip.start))
        if any(s0 < b and s0 + clip.duration > a for a, b in spans):
            decoded(clip.path, clip.fingerprint, rate)
    prepare(project, (rate,))


def cached_bytes():
    with _lock: return _cache_bytes


@dataclass(frozen=True)
class Waveform:
    block_seconds: float  # File seconds per peak.
    peaks: np.ndarray  # float32 max |sample| of either channel per block.
    peak: float  # The whole file's peak.


def waveform(path, fingerprint):
    """Peaks for drawing, built once from a decode already in the cache (at any rate).

    Never reads the file: without a cached decode (preload makes one) it returns None, and a
    later call tries again.
    """
    key = (path, fingerprint)
    with _lock:
        if key in _peaks:
            _peaks.move_to_end(key); return _peaks[key]
        source = next(((k[2], v) for k, v in reversed(_cache.items()) if k[:2] == key and v is not None), None)
    if source is None: return None
    rate, samples = source
    if not len(samples): return None
    level = np.abs(samples).max(axis=1)
    level = np.pad(level, (0, -len(level) % PEAK_BLOCK)).reshape(-1, PEAK_BLOCK).max(axis=1).astype('float32')
    level.setflags(write=False)
    result = Waveform(PEAK_BLOCK / rate, level, float(level.max()))
    with _lock:
        _peaks[key] = result
        while len(_peaks) > 32: _peaks.popitem(last=False)
    return result


def alias_decoded(old_path, new_path, fingerprint):
    """Reuse the decodes and peaks of old_path for a byte-identical copy at new_path (no decoding).

    The copy shares the old arrays, so it is not counted again against the byte bound.
    """
    global _ready
    with _lock:
        for key, value in list(_cache.items()):
            if key[:2] == (old_path, fingerprint) and (new_path, fingerprint, key[2]) not in _cache:
                _store((new_path, fingerprint, key[2]), value)
        if (old_path, fingerprint) in _peaks: _peaks[(new_path, fingerprint)] = _peaks[(old_path, fingerprint)]
        while len(_peaks) > 32: _peaks.popitem(last=False)
        _ready += 1


EDGE_SECONDS = 1e-6  # Clips closer than this (song seconds) abut.


@dataclass(frozen=True)
class AudioLayout:
    starts: np.ndarray  # Song seconds in the original summation order.
    ends: np.ndarray
    order: np.ndarray  # Clip indices in start-time order.
    sorted_starts: np.ndarray
    latest_end: np.ndarray  # Latest end among all clips up to this sorted position.
    first_playable: tuple
    muted: tuple

    @classmethod
    def from_project(cls, project):
        starts = project.seconds([clip.start for clip in project.audio_clips])
        ends = starts + [clip.duration for clip in project.audio_clips]
        order = np.argsort(starts, kind='stable')
        sorted_starts = starts[order]
        latest_end = np.maximum.accumulate(ends[order])
        for array in (starts, ends, order, sorted_starts, latest_end): array.setflags(write=False)
        first = [None] * len(project.audio_tracks)
        muted = [[] for _ in project.audio_tracks]
        for i, clip in enumerate(project.audio_clips):
            if clip.muted: muted[clip.track].append(i)
            elif first[clip.track] is None and not clip.missing and clip.gain != 0: first[clip.track] = i
        return cls(starts, ends, order, sorted_starts, latest_end, tuple(first), tuple(map(tuple, muted)))

    def overlapping(self, times, rate):
        if not len(times): return ()
        # Include a sample of slack for rounding; _add_clip decides the exact edges.
        low, high = np.fmin.reduce(times) - 1. / rate, np.fmax.reduce(times) + 1. / rate
        left = self.latest_end.searchsorted(low)
        right = self.sorted_starts.searchsorted(high, side='right')
        candidates = self.order[left:right]
        return np.sort(candidates[self.ends[candidates] >= low])


def muted_edges(project, index):
    """(fade in, fade out): whether audio clip `index` starts where a muted audio clip ends, and
    ends where one starts (a split piece next to a muted piece). Its sound sweeps in or out there."""
    clip = project.audio_clips[index]
    layout = project.audio_layout
    neighbours = layout.muted[clip.track]
    rising = any(abs(layout.ends[i] - layout.starts[index]) < EDGE_SECONDS for i in neighbours if i != index)
    falling = any(abs(layout.starts[i] - layout.ends[index]) < EDGE_SECONDS for i in neighbours if i != index)
    return rising, falling


def _add_clip(target, times, project, index, rate, sweep_ms, any_muted, wait):
    """Add audio clip `index` at song seconds `times`. target() returns (dest, meter or None) and
    is called only once the clip contributes, so silent clips allocate nothing (as before AW1b)."""
    clip = project.audio_clips[index]
    s0 = project.audio_layout.starts[index]
    k = np.rint((times - s0) * rate).astype(np.int64)
    duration = round(clip.duration * rate)
    if not np.any((k >= 0) & (k < duration)): return  # Silent in this block: no decode.
    samples = decoded(clip.path, clip.fingerprint, rate, wait)
    if samples is None:
        key = (clip.path, clip.fingerprint, rate)
        if not wait and not decode_failed(*key): return key
        return
    src = k + round(clip.offset * rate)
    mask = (k >= 0) & (k < duration) & (src < len(samples))
    if not np.any(mask): return
    envelope = np.ones(np.count_nonzero(mask), dtype='float64')
    n_in, n_out = round(clip.fade_in * rate), round(clip.fade_out * rate)
    if any_muted:
        rising, falling = muted_edges(project, index)
        sweep = min(duration, round(sweep_ms / 1000. * rate))
        if rising: n_in = max(n_in, sweep)
        if falling: n_out = max(n_out, sweep)
    if n_in: envelope = np.minimum(envelope, k[mask] / n_in)
    if n_out: envelope = np.minimum(envelope, (duration - k[mask]) / n_out)
    envelope *= clip.gain
    if clip.gain_points: envelope *= gain_at(clip.gain_points, LANE_CLOCK, k[mask] / rate)
    left, right = samples[src[mask]].astype('float64').T
    dest, meter = target()
    if clip.routing != 'stereo': left = right = (left + right) / 2
    if clip.routing != 'y':
        x = envelope * left
        dest[mask, 0] += x
        if meter is not None: meter[mask, 0] += x
    if clip.routing != 'x':
        y = envelope * right
        dest[mask, 1] += y
        if meter is not None: meter[mask, 1] += y


def project_chains(project):
    """Build chains only for tracks with active processing."""
    return {i: chain for i, track in enumerate(project.audio_tracks)
            if (chain := track_chain_for(track))}


def chain_reach(chain, rate):
    """(seconds before, seconds after) of dry audio a chain reads around what it plays at `rate`:
    windowed history plus the causal primes, and the windowed look-ahead, each stage rounded up to
    whole samples as process_track rounds it, plus one sample for the clips' sample rounding."""
    margins = [window_margins(p, rate) for p in chain.windowed]
    before = (sum(h for h, _ in margins)
              + prime_samples(chain, rate) + 1)
    after = sum(l for _, l in margins) + 1
    return before / rate, after / rate


def processing_tail(project):
    """Longest serial ring-out across the parallel tracks, in seconds."""
    tail = 0.
    for track in project.audio_tracks:
        duration = (0. if track.effects_bypass else
                    sum(effect_tail_seconds(e) for e in track.effects if not e.bypass))
        if track.phase_mode == 'delay' and (track.delay_ms or track.phase_points):
            reach = 20. if track.phase_points else abs(track.delay_ms)
            duration += reach / 1000.
        tail = max(tail, duration)
    return min(10., tail)


def chain_key(project, t):
    """The ChainState key of track t: its name while names are unique, so a track's memory follows
    it when tracks are reordered (AW2 may give tracks a stable id); else its position and files, so
    same-named tracks with different audio never inherit each other's memory."""
    names = [track.name for track in project.audio_tracks]
    name = names[t]
    if names.count(name) == 1: return ('name', name)
    return ('index', t, tuple(sorted((c.path, c.fingerprint) for c in project.audio_clips if c.track == t)))


def _playing(clip, track):
    return not (clip.missing or clip.muted or clip.gain == 0 or track.muted or track.gain == 0)


def _active_keys(project, chains):
    if chains is None: chains = project_chains(project)
    for t, chain in chains.items():
        if chain and 0 <= t < len(project.audio_tracks):
            yield chain_key(project, t)


def _chain_inputs(project, t, rate, sweep_ms, any_muted, wait):
    layout = project.audio_layout
    track = project.audio_tracks[t]
    pending = set()

    def dry_at(at):
        at = np.asarray(at, dtype=float)
        buffer = np.zeros((len(at), 2))
        for index in layout.overlapping(at, rate):
            clip = project.audio_clips[index]
            if clip.track != t or clip.missing or clip.muted or clip.gain == 0: continue
            key = _add_clip(lambda: (buffer, None), at, project, index, rate, sweep_ms, any_muted, wait)
            if key is not None: pending.add(key)
        buffer[at < 0] = 0.  # Before the song starts there is no sound, even in a chain's history.
        if track.gain != 1: buffer *= track.gain  # Track gain drives the chain (clip, track gain, pan/phase, effects).
        return buffer
    def incomplete():
        nonlocal pending
        missing, pending = pending, set()
        return missing
    return dict(dry_at=dry_at, incomplete=incomplete, key=chain_key(project, t), context=project,
                silent_before=layout.starts[layout.first_playable[t]], sweep=sweep_ms if any_muted else 0.)


def warm_chains(project, origins, rate, sweep_ms, state):
    """Prepare the playback histories at the first sample and first loop wrap."""
    chains = project_chains(project)
    state.use_context(project, _active_keys(project, chains))
    causal = [t for t, chain in chains.items() if chain.causal and project.audio_layout.first_playable[t] is not None]
    state.prime_limit = max(8, len(causal) * len(origins) + 2)
    any_muted = sweep_ms > 0 and any(project.audio_layout.muted)
    for t in causal:
        inputs = _chain_inputs(project, t, rate, sweep_ms, any_muted, wait=False)
        warm(chains[t], origins=origins, rate=rate, state=state, **inputs)


def mix_audio_clips(output, times, project, rate, sweep_ms=0., wait=True, peaks=None, chains=None, chain_state=None):
    """Add the audio clips at song seconds `times`. Muted clips are silent; with a switch sweep,
    a clip that abuts a muted one fades over the sweep at that edge (as scene clips sweep).

    wait=False (playback, the stopped display) never decodes here: a clip whose media isn't
    decoded yet is silent in this block and its decode is queued (audit P9).

    chains: {track index: track_chain.TrackChain} for tracks with processing (AW1b; AW2 and AW3
    fill it from the tracks). A track with a non-empty chain is rendered on its own, through
    track_chain.process_track with chain_state (the caller's memory across blocks; None renders
    stateless with a fresh prime). Every other track is mixed exactly as before AW1b."""
    if not len(times): return  # Nothing to mix, and no empty meter to take a peak of.
    if chains is None and project.audio_clips: chains = project_chains(project)
    if chain_state is not None:
        chain_state.use_context(project, _active_keys(project, chains))
    if not project.audio_clips: return
    layout = project.audio_layout
    any_muted = sweep_ms > 0 and any(layout.muted)
    chained = {t: chain for t, chain in (chains or {}).items()
               if chain and 0 <= t < len(project.audio_tracks)}
    buffers = {}
    meters = {} if peaks is not None else None
    for index in layout.overlapping(times, rate):
        clip = project.audio_clips[index]
        t = clip.track
        track = project.audio_tracks[t]
        if t in chained or not _playing(clip, track): continue

        def target(t=t, track=track):
            if track.gain != 1:
                if t not in buffers: buffers[t] = np.zeros((len(times), 2))
                return buffers[t], None
            if peaks is not None and t < len(peaks):
                if t not in meters: meters[t] = np.zeros((len(times), 2))
                return output, meters[t]
            return output, None
        _add_clip(target, times, project, index, rate, sweep_ms, any_muted, wait)
    for t in sorted(buffers):
        scaled = project.audio_tracks[t].gain * buffers[t]
        output += scaled
        if peaks is not None and t < len(peaks): meters[t] = scaled
    for t in sorted(chained):
        track = project.audio_tracks[t]
        if not chained[t].causal and (track.muted or track.gain == 0): continue
        if layout.first_playable[t] is None: continue

        inputs = _chain_inputs(project, t, rate, sweep_ms, any_muted, wait)
        scaled = process_track(chained[t], times=times, rate=rate, state=chain_state,
                               still_pending=None if wait else _still_pending, **inputs)
        if track.muted: continue
        output += scaled
        if peaks is not None and t < len(peaks): meters[t] = scaled
    if meters:
        for t, samples in meters.items(): peaks[t] = max(peaks[t], float(np.abs(samples).max()))


def media_folder(project_path):
    """`<project name> media` beside the project file, where Collect media copies the files."""
    project_path = Path(project_path)
    name = project_path.name
    name = name[:-len('.project.json')] if name.endswith('.project.json') else project_path.stem
    return project_path.parent / f'{name} media'


def search_candidates(clip, project_path, relative):
    """Where the automatic search looks, in order: the relative path from the project file, the
    last absolute path, the project's media folder, and the same name in the project's folder."""
    candidates = []
    if project_path is not None:
        folder = Path(project_path).parent
        if relative is not None: candidates.append(folder / relative)
    candidates.append(Path(clip.path))
    if project_path is not None:
        candidates.extend((media_folder(project_path) / clip.name, Path(project_path).parent / clip.name))
    return candidates


def matches(clip, path):
    """Whether the file at path has the clip's size and fingerprint."""
    try:
        path = Path(path)
        return path.is_file() and path.stat().st_size == clip.size and fingerprint(path) == clip.fingerprint
    except (OSError, ValueError):
        return False


def resolve_audio_clip(clip, project_path, relative):
    for candidate in search_candidates(clip, project_path, relative):
        if matches(clip, candidate):
            return replace(clip, path=str(candidate.resolve()), missing=False)
    return replace(clip, missing=True)


def add_audio_clip(project, clip):
    from phosphor.engine.composition import validate_project
    return validate_project(replace(project, audio_clips=tuple(sorted(
        (*project.audio_clips, clip), key=lambda c: (c.start, c.track)))))


def audio_clip_end_beats(project, clip):
    return project.beats(project.seconds(clip.start) + clip.duration)
