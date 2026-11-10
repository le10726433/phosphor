"""Blocking audio output on a worker thread; Tk never runs on the audio thread."""
from collections import deque
from pathlib import Path
import sys
import threading
import time
import numpy as np
from signal_engine import render, select_signal
from midi_synth import MIDI_SOURCE
from live_midi import LiveMidi
from playback_history import PlaybackHistory

# Local dependency install for this workspace; ordinary venv installs work too.
local_dependencies = Path(__file__).parent / '.audio-deps'
if local_dependencies.is_dir():
    sys.path.append(str(local_dependencies))


def windows_midi_profiles(sd):
    """Windows Live MIDI output choices, lowest latency first.

    MME is the system default here and adds roughly 90 ms. Shared WASAPI is
    limited by the Windows mixer (about 22 ms). Only exclusive WASAPI in callback
    mode reaches Mac-class latency; it holds the device while Live MIDI plays.
    """
    profiles = []
    try:
        api = next(api for api in sd.query_hostapis() if 'WASAPI' in api['name'])
        device = api['default_output_device']
        if device >= 0:
            profiles += [dict(label='WASAPI exclusive', callback=True, device=device, blocksize=128, latency='low',
                              extra_settings=sd.WasapiSettings(exclusive=True)),
                         dict(label='WASAPI shared', device=device, blocksize=128, latency='low')]
    except StopIteration:
        pass
    return profiles+[dict(label='System default', blocksize=AudioPreview.BLOCK_FRAMES, latency=.03)]


class AudioPreview:
    # Small synthesis blocks retain MIDI responsiveness; the viewport has its own
    # playback clock because CoreAudio may still accept many writes in a burst.
    BLOCK_FRAMES = 512
    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
        self.parameters = None
        self.history = deque(maxlen=5)
        self.playback = PlaybackHistory()
        self.error = ''
        self.underruns = 0
        self.frames = 0
        self.midi = LiveMidi()
        self.live = False
        self.block_frames = self.BLOCK_FRAMES
        self.output_latency = 0.
        self.output_mode = ''
        self.clipped_samples = 0
        self.synthesis_ms = deque(maxlen=4096)
        self.write_wait_ms = deque(maxlen=4096)

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, settings, mode):
        if self.running:
            return
        # Import here so simulation/export still work without the audio package.
        try:
            import sounddevice as sd
        except ImportError as error:
            raise RuntimeError('Install playback support: python -m pip install -r requirements.txt') from error
        render(np.arange(2) / settings.sample_rate, settings)
        self.live = settings.source == MIDI_SOURCE
        if self.live:
            # Avoid the first note paying for the Fourier coefficient cache.
            from midi_synth import coefficients, FAMILIES
            for family in FAMILIES: coefficients(family)
        profiles = self._stream_profiles(sd)
        for remaining, profile in reversed(list(enumerate(reversed(profiles)))):
            options = dict(profile)
            label, callback = options.pop('label', ''), options.pop('callback', False)
            if callback:
                options['callback'] = self._callback
            stream = None
            try:
                stream = sd.OutputStream(samplerate=settings.sample_rate, channels=2, dtype='float32', **options)
                self._prepare(stream, settings, mode, options['blocksize'], label)
                stream.start()
            except Exception:
                self.midi.accepting = False
                if stream is not None:
                    stream.close()
                if not remaining:
                    raise
                continue
            target = self._wait_for_callback_stream if callback else self._run
            self.thread = threading.Thread(target=target, args=(stream,), daemon=True)
            self.thread.start()
            return

    def _stream_profiles(self, sd):
        if self.live and sys.platform == 'darwin':
            # Blocking writes add about 42 ms of PortAudio buffering on Core Audio.
            return [dict(label='Core Audio callback', callback=True, blocksize=128, latency='low'),
                    dict(label='Core Audio blocking', blocksize=128, latency='low')]
        if self.live and sys.platform == 'win32':
            return windows_midi_profiles(sd)
        return [dict(blocksize=self.BLOCK_FRAMES, latency=.03 if self.live else .1)]

    def _prepare(self, stream, settings, mode, block_frames, label):
        # Runs before stream.start: a callback stream can fire immediately.
        self.block_frames = block_frames
        self.output_mode = label
        self.parameters = (settings, mode)
        self.stop_event.clear()
        self.history = deque(maxlen=max(5,int(np.ceil(.04*settings.sample_rate/block_frames))+1))
        self.output_latency = float(getattr(stream,'latency',.1))
        self.playback.reset(settings.sample_rate, self.output_latency)
        self.error = ''
        self.synthesis_ms.clear(); self.write_wait_ms.clear()
        self.underruns = self.frames = 0
        self.clipped_samples = 0
        if self.live:
            self.midi.reset(settings.sample_rate)

    def _next_block(self, count):
        settings, mode = self.parameters
        times = (self.frames + np.arange(count)) / settings.sample_rate
        if self.live:
            self.midi.process(self.frames, settings, mode)
            drawing, music, mixed = self.midi.render(times, settings)
        else:
            drawing, music, mixed = render(times, settings)
        signal = select_signal(drawing, music, mixed, settings, mode, clip_output=False).astype('float32')
        self.clipped_samples += int(np.count_nonzero(abs(signal) > 1))
        signal = np.clip(signal, -1, 1)
        # Brief startup ramp avoids a hard jump when playback begins.
        if self.frames == 0:
            n = min(len(signal), round(.005 * settings.sample_rate))
            signal[:n] *= np.linspace(0, 1, n)[:, None]
        return signal

    def _publish_block(self, signal, submitted_at, underflow):
        settings, mode = self.parameters
        self.playback.publish(self.frames,signal,submitted_at,underflow)
        if self.live and self.midi.capture:
            self.midi.capture.write(self.frames, signal, settings, mode)
        self.history.append(signal)
        self.frames += len(signal)

    def _run(self, stream):
        try:
            while not self.stop_event.is_set():
                processing_started = time.perf_counter()
                signal = self._next_block(self.block_frames)
                writing_started = time.perf_counter()
                self.synthesis_ms.append((writing_started-processing_started)*1000)
                submitted_at = time.monotonic()
                underflow = stream.write(signal)
                if underflow:
                    self.underruns += 1
                self.write_wait_ms.append((time.perf_counter()-writing_started)*1000)
                self._publish_block(signal,submitted_at,underflow)
        except Exception as error:
            self.error = str(error)
        finally:
            self._finish_stream(stream)

    def _callback(self, output, frames, time_info, status):
        if self.stop_event.is_set():
            output.fill(0)
            return
        started = time.perf_counter()
        underflow = bool(status.output_underflow)
        if underflow:
            self.underruns += 1
        try:
            signal = self._next_block(frames)
            output[:] = signal
            self._publish_block(signal,time.monotonic(),underflow)
        except Exception as error:
            output.fill(0)
            self.error = str(error)
            self.stop_event.set()
        self.synthesis_ms.append((time.perf_counter()-started)*1000)

    def _wait_for_callback_stream(self, stream):
        try:
            while not self.stop_event.wait(.05):
                if not stream.active:
                    self.error = 'Audio device stopped unexpectedly.'
                    break
            stream.stop()
        except Exception as error:
            self.error = str(error)
        finally:
            self._finish_stream(stream)

    def _finish_stream(self, stream):
        self.midi.accepting = False
        try:
            self.midi.finish_record()
        except Exception as error:
            self.error = f'Recording finalization failed: {error}'
        finally:
            self.midi.synth.panic()
            stream.close()

    def playback_snapshot(self, count):
        return self.playback.snapshot(count)

    def samples(self, count):
        blocks = tuple(self.history)
        if not blocks:
            return None
        selected, available = [], 0
        for block in reversed(blocks):
            selected.append(block)
            available += len(block)
            if available >= count:
                break
        return np.concatenate(selected[::-1])[-count:]

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        if self.running:
            raise RuntimeError('Audio device is still stopping; try again shortly.')
