"""A simulated audio output device for measuring playback pacing without the hardware (P7c).

`SimulatedDevice(rate)` stands in for the `sounddevice` module: give it to
`AudioPreview.device` and playback opens its streams here instead of on a real device. The
stream consumes frames at the rate's real-time cadence on a high-resolution clock, so the same
`AudioPreview._run` / `_callback` code runs, with the same GIL behaviour (the blocking write
sleeps without the GIL, as PortAudio's does), and underruns are reported as a device would:
- blocking streams: a write that arrives after the buffer ran dry reports an underflow, and the
  frames the device played as silence are lost (the stream does not slow down);
- callback streams: a callback that finishes after its block was due reports an underflow on
  the next call.

Used by `tools/measure_pacing.py --simulate-rate` for 176.4 and 192 kHz, which this machine's
interface can't open. Python 3.11+ sleeps on a high-resolution waitable timer on Windows, so
`time.sleep` waits to about a millisecond here, not the 15.6 ms timer tick.
"""
import math
import threading
import time
from types import SimpleNamespace

import numpy as np


class PortAudioError(Exception):
    """Raised like sounddevice.PortAudioError when a simulated device refuses a rate."""


class SimulatedOutputStream:
    def __init__(self, owner, samplerate, channels=2, dtype='float32', blocksize=0, latency=.1,
                 callback=None, clock=time.perf_counter, sleep=time.sleep, device=None, **ignored):
        if samplerate not in owner.rates:
            raise PortAudioError(f'Invalid sample rate {samplerate} (simulated device: '
                                 + ', '.join(f'{r / 1000:g}' for r in owner.rates) + ' kHz)')
        self.owner, self.device = owner, device  # device: the index asked for (WASAPI profiles pass one).
        self.samplerate = int(samplerate)
        self.channels = channels
        self.blocksize = int(blocksize) or 512
        latency = {'low': .01, 'high': .1}.get(latency, latency)
        # The device buffer: at least one block, as PortAudio rounds its host buffer up.
        self.capacity = max(self.blocksize, math.ceil(float(latency) * self.samplerate))
        self.latency = self.capacity / self.samplerate
        self.callback = callback
        self.clock, self.sleep = clock, sleep
        self.origin = None  # Clock time of frame 0: start().
        self.written = 0  # Frames the device has accepted (blocking) or requested (callback).
        self.underflows = 0
        self.writes = 0
        self.closed = False
        self.active = False
        self._thread = None
        self._stop = threading.Event()
        owner.streams.append(self)

    def played(self):
        """Frames the device has consumed by now."""
        return 0 if self.origin is None else max(0, int((self.clock() - self.origin) * self.samplerate))

    def start(self):
        self.active = True
        if self.callback is not None:
            # The device starts playing once its first buffer is full.
            self.origin = self.clock() + self.latency
            self._stop.clear()
            self._thread = threading.Thread(target=self._pump, daemon=True, name='simulated-audio')
            self._thread.start()
        else:
            # PortAudio primes a blocking output stream with a buffer of silence when it starts.
            self.origin = self.clock()
            self.written = self.capacity

    def write(self, data):
        """Blocking write: waits for room in the buffer; True if the buffer ran dry first."""
        if not self.active: raise PortAudioError('Stream is stopped')
        count = len(data)
        self.writes += 1
        underflow = False
        played = self.played()
        if self.written < played:
            # Ran dry: the device played silence meanwhile, and that time is gone.
            underflow = True
            self.underflows += 1
            self.written = played
        while self.written + count - played > self.capacity:
            self.sleep((self.written + count - self.capacity - played) / self.samplerate)
            played = self.played()
        self.written += count
        return underflow

    def _pump(self):
        """Callback mode: ask for each block when the device needs it, on a precise clock."""
        output = np.zeros((self.blocksize, self.channels), dtype='float32')
        late = False
        while not self._stop.is_set():
            # Each block is requested one buffer ahead of when it plays.
            due = self.origin + (self.written - self.capacity) / self.samplerate
            wait = due - self.clock()
            if wait > 0: self.sleep(wait)
            if self._stop.is_set(): break
            status = SimpleNamespace(output_underflow=late)
            self.callback(output, self.blocksize, None, status)
            self.written += self.blocksize
            # Late if it finished after the previous blocks in the buffer had all played.
            late = self.played() > self.written - self.blocksize
            if late:
                # The device played silence until now; this block plays from here.
                self.underflows += 1
                self.written = self.played() + self.blocksize

    def stop(self):
        if self.callback is not None:
            self._stop.set()
            if self._thread is not None and self._thread is not threading.current_thread(): self._thread.join(1)
        elif self.active and self.origin is not None:
            # Drain what was written, as PortAudio's stop does.
            remaining = (self.written - self.played()) / self.samplerate
            if remaining > 0: self.sleep(min(remaining, 1.))
        self.active = False

    def close(self):
        if self.active: self.stop()
        self.closed = True


class SimulatedDevice:
    """The part of the sounddevice module AudioPreview uses, for one simulated output device.

    `rates` are the rates it accepts (default: only `rate`, like an exclusive-mode device), so
    the output-rate preference's fallback can be tested too. Its host API carries 'WASAPI' in
    its name so Windows code paths treat it as the default WASAPI output.
    """
    PortAudioError = PortAudioError

    def __init__(self, rate, rates=None, name='Simulated output', clock=time.perf_counter, sleep=time.sleep):
        self.rate = int(rate)
        self.rates = tuple(int(r) for r in (rates or (rate,)))
        self.name = name
        self.clock, self.sleep = clock, sleep
        self.streams = []

    def query_hostapis(self):
        return ({'name': 'Simulated (WASAPI-compatible)', 'default_output_device': 0},)

    def query_devices(self, device=None, kind=None):
        return {'name': self.name, 'default_samplerate': float(self.rate), 'max_output_channels': 2,
                'hostapi': 0}

    @staticmethod
    def WasapiSettings(**options):
        return SimpleNamespace(**options)

    def OutputStream(self, **options):
        options.setdefault('clock', self.clock); options.setdefault('sleep', self.sleep)
        return SimulatedOutputStream(self, **options)
