"""Deterministic periodic coloured noise, built on one daemon worker."""
from collections import OrderedDict
import threading
import numpy as np

RATE, SECONDS = 48000, 40
N = RATE * SECONDS
SEED = 6781
TARGET_RMS = 1 / np.sqrt(3)
MAX_CUSTOM = 4
MAX_ATTEMPTS = 3  # Builds of one curve before get() gives up (a build that raises, e.g. MemoryError).
BUILTINS = (((20., 0.), (20000., 0.)), ((20., 0.), (20000., -30.)), ((20., 0.), (20000., -60.)))
_lock = threading.Lock()
_cache, _spectra, _pending = OrderedDict(), {}, OrderedDict()
_errors = {}  # curve -> (failed builds, last error)
_worker = None
_generation = 0
_local = threading.local()


def _key(curve): return tuple((float(hz), float(db)) for hz, db in curve)


def build(curve):
    """Shape the same white source in log frequency, then normalize its RMS."""
    nodes = np.asarray(curve, dtype=float)
    white = np.random.Generator(np.random.PCG64(SEED)).standard_normal(N)
    frequencies = np.fft.rfftfreq(N, 1 / RATE)
    db = np.interp(np.log10(np.maximum(frequencies, 1e-20)), np.log10(nodes[:, 0]), nodes[:, 1])
    transformed = np.fft.rfft(white); transformed[0] = 0
    result = np.fft.irfft(transformed * 10 ** (db / 20), n=N)
    result *= TARGET_RMS / np.sqrt(np.mean(result * result))
    result = result.astype(np.float32); result.setflags(write=False)
    return result


def _run():
    global _worker
    while True:
        with _lock:
            if not _pending:
                _worker = None; return
            curve, (event, generation) = next(iter(_pending.items()))
        try:
            table = build(curve)
            with _lock:
                if generation == _generation:
                    _cache[curve] = table; _errors.pop(curve, None)
                    custom = [key for key in _cache if key not in BUILTINS]
                    for key in custom[:-MAX_CUSTOM]:
                        _cache.pop(key); _spectra.pop(key, None)
        except Exception as error:  # Keep the worker alive; get() counts the failure.
            with _lock:
                if generation == _generation:
                    _errors[curve] = (_errors.get(curve, (0, None))[0] + 1, error)
        finally:
            with _lock:
                if _pending.get(curve) == (event, generation): _pending.pop(curve)
                event.set()


def request(curve):
    """Queue a missing curve without blocking the caller."""
    global _worker
    curve = _key(curve)
    with _lock:
        if curve in _cache or curve in _pending: return
        _pending[curve] = (threading.Event(), _generation)
        if _worker is None:
            _worker = threading.Thread(target=_run, name='noise-loops', daemon=True)
            _worker.start()


def get(curve, wait=True):
    """The loop for curve; without wait, None while it is being built (queued if missing).

    Raises ValueError once a curve has failed MAX_ATTEMPTS builds (B2: it used to retry forever).
    """
    curve = _key(curve)
    while True:
        with _lock:
            failed, error = _errors.get(curve, (0, None))
        if failed >= MAX_ATTEMPTS: raise ValueError(f'Noise loop could not be built: {error}')
        request(curve)
        with _lock:
            if curve in _cache:
                _cache.move_to_end(curve); return _cache[curve]
            event = _pending.get(curve, (None, None))[0]
        if not wait: return None
        if event is not None: event.wait()


def missed():
    """Record that this thread went on without a loop it needed (a realtime fallback)."""
    _local.misses = misses() + 1


def misses():
    """This thread's count of fallbacks; caches compare it to never store a fallback result."""
    return getattr(_local, 'misses', 0)


def set_realtime(on): _local.on = bool(on)
def realtime(): return getattr(_local, 'on', False)


def spectrum(curve, wait=False):
    """Measured averaged power in logarithmic bins, normalized to zero dB."""
    curve = _key(curve)
    with _lock:
        if curve in _spectra: return _spectra[curve]
    table = get(curve, wait)
    if table is None: return None
    size = 8192
    starts = np.linspace(0, N-size, 64).astype(int)
    power = np.mean([abs(np.fft.rfft(table[start:start+size] * np.hanning(size)))**2 for start in starts], axis=0)
    frequencies = np.fft.rfftfreq(size, 1 / RATE)
    edges = np.geomspace(20, 20000, 65)
    hz = np.sqrt(edges[:-1]*edges[1:])
    values = [np.mean(power[(frequencies >= a) & (frequencies < b)])
              if np.any((frequencies >= a) & (frequencies < b)) else np.interp(h, frequencies, power)
              for a, b, h in zip(edges[:-1], edges[1:], hz)]
    db = 10*np.log10(np.maximum(values, 1e-30)); db -= db.max()
    with _lock:
        if curve in _cache: _spectra[curve] = (hz, db)
    return hz, db


def clear():
    """Discard cached and queued work; an in-flight build cannot repopulate it."""
    global _generation
    with _lock:
        _generation += 1
        _cache.clear(); _spectra.clear(); _errors.clear()
        for event, _ in _pending.values(): event.set()
        _pending.clear()


def sample_noise(curve, index, sample_rate, wait):
    table = get(curve, wait)
    if table is None: return None
    k = (np.asarray(index, dtype=np.int64) * RATE // sample_rate) % N
    return np.column_stack((table[k], table[(k + N//2) % N])).astype(np.float64)
