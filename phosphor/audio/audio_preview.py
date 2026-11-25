"""Blocking audio output on a worker thread; Tk never runs on the audio thread."""
from collections import deque
from pathlib import Path
import math
import sys
import threading
import time
from dataclasses import replace
import numpy as np
from phosphor.engine.signal_engine import render, select_signal
from phosphor.engine import audio_clips, noise_loops, switch_sweep
from phosphor.engine.composition import render_song, song_seconds
from phosphor.engine.track_chain import ChainState
from phosphor.engine.metronome import mix_clicks, mix_clicks_beats
from phosphor.engine.tempo_map import TempoMap, Meter
from phosphor.audio.midi_synth import MIDI_SOURCE
from phosphor.audio.live_midi import LiveMidi
from phosphor.audio.playback_history import PlaybackHistory
from phosphor.audio import timer_resolution
from phosphor.audio import output_devices
# The output-rate preference: FOLLOW_DEVICE (0) or one of OUTPUT_RATES, capped at 192 kHz.
from phosphor.storage.signal_preferences import FOLLOW_DEVICE, OUTPUT_RATES  # noqa: F401


def windows_midi_profiles(sd, device=None):
    """Windows Live MIDI output choices, lowest latency first.

    MME is the system default here and adds roughly 90 ms. Shared WASAPI is
    limited by the Windows mixer (about 22 ms). Only exclusive WASAPI in callback
    mode reaches Mac-class latency; it holds the device while Live MIDI plays.
    `device` is the chosen WASAPI output (S2); None uses the default WASAPI output.
    """
    profiles = []
    if device is None: device = wasapi_default_output(sd)
    if device is not None:
        frames = LIVE_BLOCK
        profiles += [dict(label='WASAPI exclusive', callback=True, device=device, blocksize=frames, latency='low',
                          extra_settings=sd.WasapiSettings(exclusive=True)),
                     dict(label='WASAPI shared', device=device, blocksize=frames, latency='low')]
    return profiles+[dict(label='System default', blocksize=PLAYBACK_BLOCK, latency=.03)]


# Block sizes in stream profiles, resolved for the rate the stream opens at (AudioPreview.start).
LIVE_BLOCK, PLAYBACK_BLOCK = 'live', 'playback'
BLOCK_SECONDS = .01


def playback_block_frames(rate):
    """Playback block: 10 ms at any rate (P7c; was 512 frames at every rate).

    Synthesis cost is mostly per block and every block waits for the GIL behind the GUI thread,
    so a fixed 512 frames halved the time each block had at 96 kHz (5.3 ms) and quartered it at
    192 kHz (2.7 ms): Composer fell to 0.24-0.77 of real time at a 144 Hz display. 10 ms is the
    Windows audio engine's own period (480 frames at 48 kHz, close to the old 512) and about
    Core Audio's default 512-frame I/O buffer at 44.1/48 kHz. It stays under a 60 Hz frame, and
    PlaybackHistory's clock keeps the viewport smooth between blocks at any display rate. Live
    MIDI keeps its own smaller blocks (live_block_frames).
    """
    return max(64, round(rate*BLOCK_SECONDS))


def stream_block_frames(blocksize, rate):
    if blocksize == LIVE_BLOCK: return live_block_frames(rate)
    if blocksize == PLAYBACK_BLOCK: return playback_block_frames(rate)
    return blocksize


def live_block_frames(rate):
    """Live MIDI block: 128 frames up to 48 kHz, about 5 ms above it.

    Synthesis cost is mostly per block, so 128 frames at 96 kHz (1.3 ms) cannot keep
    up once chords are held: it measured 78% of real time on Windows, with no underruns
    reported, against 99.95% at 512 frames, and 77.9% inside the editor on Apple M5.
    Used for both Windows WASAPI and macOS Core Audio Live MIDI streams.
    """
    if rate <= DEFAULT_RATE: return 128
    return 1 << math.ceil(math.log2(rate*.005))


def wasapi_default_output(sd):
    """Index of the default WASAPI output device, or None without one."""
    api = next((api for api in sd.query_hostapis() if 'WASAPI' in api['name']), None)
    device = -1 if api is None else api['default_output_device']
    return device if device >= 0 else None


DEFAULT_RATE = 48000
RATE_RANGE = (8000, 384000)


def device_rate(sd, device=None):
    """The output device's own rate, so the system does not resample generated audio.

    Core Audio keeps a device at its nominal rate and converts any stream opened at
    another rate; Windows shared mode likewise runs at the mixer rate. MME, the Windows
    default API, reports 44100 whatever the device runs at, so a stream with no
    explicit device takes the rate WASAPI reports for the default output.
    """
    try:
        if device is None and sys.platform == 'win32':
            device = wasapi_default_output(sd)
            if device is None: return DEFAULT_RATE
        info =sd.query_devices(device, 'output') if device is not None else sd.query_devices(kind='output')
        rate = int(round(float(info['default_samplerate'])))
    except Exception:
        return DEFAULT_RATE
    return rate if RATE_RANGE[0] <= rate <= RATE_RANGE[1] else DEFAULT_RATE


class _ClosedStream:
    """Stands in for a stream that could not be reopened (S2): stopping and closing do nothing,
    and a write raises the reason, which ends playback with it on the status line."""
    active = False
    latency = .1

    def __init__(self, reason):
        self.reason = reason

    def write(self, data):
        raise RuntimeError(self.reason)

    def start(self): pass
    def stop(self): pass
    def abort(self): pass
    def close(self): pass


class AudioPreview:
    # Playback blocks are 10 ms at the stream's rate (playback_block_frames); Live MIDI keeps
    # small blocks for responsiveness. The viewport has its own playback clock because Core Audio
    # may still accept many writes in a burst.
    BLOCK_FRAMES = 512  # Before a stream opens, and for tests that drive blocks by hand.
    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
        # Rate of the open (or last) output stream; settings always carry it.
        self.sample_rate = DEFAULT_RATE
        self._parameters = None
        # Composer playback: (project, song second where playback started), or None for a
        # Sandbox scene. Replaced whole (never mutated), so the audio thread reads it safely.
        self._composer = None
        # Live switch crossfade (mute/solo M3): (old parameters, old composer) waiting for the
        # audio thread, and the one it is playing: (old parameters, old composer, samples done, total).
        self._switch_request = None
        self._switch_fade = None
        self.cycle = False
        self._cycle_pending = (None,)
        self._cycle_current = None
        self._cycle_index = 0
        self._cycle_request = None
        self._cycle_fade = 0
        self._cycle_stop = threading.Event()
        self.preview = False
        self.preview_level = .7
        self.preview_retry_rate = None
        self._preview_samples = None
        self._preview_index = 0
        self._preview_rate = DEFAULT_RATE
        self._preview_fade_end = None
        self.metronome = False
        self.metronome_level = .3
        self.count_in_seconds = 0.
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
        self.output_peak = 0.  # Largest |sample| before the output limit since take_output_peak.
        self.track_peaks = []  # Per audio track, the same (AW1; take_track_peaks).
        self._peak_lock = threading.Lock()  # The audio thread merges, the GUI drains (Codex review).
        self.synthesis_ms = deque(maxlen=4096)
        self.write_wait_ms = deque(maxlen=4096)
        # A stand-in for the sounddevice module (simulated_output.SimulatedDevice), or None.
        self.device = None
        # The output-rate preference: FOLLOW_DEVICE (0) or one of OUTPUT_RATES. When the device
        # refuses it, start() opens at the device rate and says so in rate_notice.
        self.requested_rate = FOLLOW_DEVICE
        self.rate_notice = ''
        # S2: the output device preference (a name, or SYSTEM_DEFAULT), the open stream's device
        # name, and a status-line notice when the chosen one is missing or playback moved.
        self._output_device = output_devices.SYSTEM_DEFAULT
        self.output_name = ''
        self.device_notice = ''
        self._reopen = threading.Event()  # Set by the GUI; the audio thread reopens the stream.
        self._stream_state = None  # (profile label, callback, following the default, default token).
        self._idle_token = None
        # (perf_counter, frames submitted) when a blocking write last waited for room, so the
        # device buffer was full: the anchor of buffered_seconds (P7c display pacing).
        self._full_at = None

    @property
    def output_device(self):
        return self._output_device

    @output_device.setter
    def output_device(self, name):
        """The chosen output (a device name, or SYSTEM_DEFAULT). A change while playing moves
        the open stream to it (S2); otherwise it applies from the next Play."""
        if name == self._output_device: return
        self._output_device = name
        self._device_rate = None
        if self.running: self._reopen.set()

    @property
    def notice(self):
        """The status line's output notes: a refused rate, a missing or changed device."""
        return '  |  '.join(text for text in (self.rate_notice, self.device_notice) if text)

    def _resolve_device(self, sd):
        """(PortAudio index or None for the system default, notice, following the default)."""
        name = self._output_device
        if self.device is not None or not name: return None, '', True
        index = output_devices.find(sd, name)
        if index is None: return None, f'{name} is not connected; playing on the system default.', True
        return index, '', False

    def check_output(self):
        """Called by the GUI about once a second (S2). While playing, asks the audio thread to
        reopen the stream when the system default changed (with System default) or the chosen
        device went away; while stopped, forgets the cached device rate after a change.
        Returns True when it asked for a reopen."""
        if self.device is not None: return False  # A simulated device never changes.
        token = output_devices.default_token()
        state = self._stream_state
        if not self.running or state is None:
            if token != self._idle_token: self._idle_token = token; self._device_rate = None
            return False
        following, opened_token = state[2], state[3]
        if following:
            changed = token is not None and opened_token is not None and token != opened_token
        else:
            changed = output_devices.present(self.output_name) is False
        if changed and not self._reopen.is_set():
            self._reopen.set(); return True
        return False

    def _sounddevice(self):
        if self.device is not None: return self.device
        import sounddevice
        return sounddevice

    @property
    def parameters(self):
        return self._parameters

    @parameters.setter
    def parameters(self, value):
        # Callers pass scene settings; playback always renders at the stream's rate.
        if value is not None:
            settings, mode = value
            if settings.sample_rate != self.sample_rate:
                settings = replace(settings, sample_rate=self.sample_rate)
            value = (settings, mode)
        self._request_switch_fade(self._parameters, value)
        self._parameters = value

    @property
    def composer(self):
        return self._composer

    @composer.setter
    def composer(self, value):
        old = self._composer
        if (old is not None and value is not None and self.running and not self.live and
                switch_sweep.project_signature(old[0]) != switch_sweep.project_signature(value[0])):
            self._switch_request = (self._parameters, old)
        self._composer = value

    def _request_switch_fade(self, old, new):
        """A live Mute, Solo, bypass or power change crossfades from the old render (playback only)."""
        if (old is None or new is None or self._composer is not None or not self.running or self.live or
                self.cycle or self.preview):
            return
        if switch_sweep.signature(old[0].shapes) != switch_sweep.signature(new[0].shapes):
            self._switch_request = (old, None)

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    @property
    def cycle_running(self):
        return self.cycle and self.running

    @property
    def cycle_samples(self):
        """Currently audible immutable buffer (audio thread publishes whole references)."""
        return self._cycle_current

    def set_cycle(self, samples):
        """Publish an owned immutable snapshot in one atomic reference assignment."""
        if samples is not None:
            samples = np.array(samples, dtype='float32', copy=True)
            if samples.ndim != 2 or samples.shape[1] != 2 or not len(samples):
                raise ValueError('Cycle samples must have shape (N, 2), N > 0.')
            samples.setflags(write=False)
        self._cycle_pending = (samples,)

    def _cycle_block(self, count, rate):
        pending = self._cycle_pending  # One coherent request per block.
        output = np.zeros((count, 2), dtype='float32')
        ramp = max(1, round(.005 * rate))
        i = 0
        # Copied in slices between cycle boundaries: a per-sample Python loop is too slow
        # for the audio thread at 96 kHz.
        while i < count:
            if self._cycle_index == 0 and not self._cycle_fade:
                if self._cycle_stop.is_set() or pending is not self._cycle_request:
                    requested = None if self._cycle_stop.is_set() else pending[0]
                    self._cycle_request = pending
                    if requested is None and self._cycle_current is not None:
                        self._cycle_fade = ramp
                    else:
                        self._cycle_current = requested
            current = self._cycle_current
            if current is None: break  # Silence until a buffer is published.
            n = min(count - i, len(current) - self._cycle_index)
            if self._cycle_fade: n = min(n, self._cycle_fade)
            chunk = current[self._cycle_index:self._cycle_index + n]
            fading = bool(self._cycle_fade)
            if fading:
                gains = (self._cycle_fade - 1 - np.arange(n)) / ramp
                output[i:i + n] = chunk * gains[:, None]
                self._cycle_fade -= n
            else:
                output[i:i + n] = chunk
            self._cycle_index = (self._cycle_index + n) % len(current)
            i += n
            if fading and not self._cycle_fade:
                # Faded out: silence until the next buffer, which starts its cycle at 0.
                self._cycle_current = None
                self._cycle_index = 0
        if self._cycle_stop.is_set() and self._cycle_current is None:
            self.stop_event.set()
        return output

    @property
    def preview_running(self):
        return self.preview and self.running

    @property
    def preview_seconds(self):
        return self._preview_index / self._preview_rate

    def start_preview(self, samples, rate, settings, mode):
        if self.running:
            raise RuntimeError('Audio is already running.')
        if rate <= 0 or samples.ndim != 2 or samples.shape[1] != 2:
            raise ValueError('Preview needs stereo samples at a positive rate.')
        self._composer = None
        self.cycle = False
        self._preview_samples = samples
        self._preview_index = 0
        self._preview_rate = rate
        self._preview_gain = self.preview_level
        self._preview_fade_end = None
        self.preview_retry_rate = None
        self.preview = True
        try:
            self.start(settings, mode)
        except Exception:
            self.preview = False
            self._preview_samples = None
            raise

    def _preview_block(self, count, rate):
        output = np.zeros((count, 2), dtype='float32')
        if rate != self._preview_rate:
            self.preview_retry_rate = rate
            self.stop_event.set()
            return output
        start = self._preview_index
        end = len(self._preview_samples)
        if self._cycle_stop.is_set() and self._preview_fade_end is None:
            self._preview_fade_length = min(max(1, round(.005 * rate)), end - start)
            self._preview_fade_end = start + self._preview_fade_length
        if self._preview_fade_end is not None:
            end = self._preview_fade_end
        n = min(count, end - start)
        level = self.preview_level
        gains = np.linspace(self._preview_gain, level, count)[:n]
        self._preview_gain = level
        if n and self._preview_fade_end is not None:
            gains *= (end - start - 1 - np.arange(n)) / self._preview_fade_length
        output[:n] = self._preview_samples[start:start + n] * gains[:, None]
        self._preview_index += n
        if self._preview_index == end:
            self.stop_event.set()
        return output

    def start(self, settings, mode, *, cycle_factory=None):
        if self.running:
            return
        # Import here so simulation/export still work without the audio package.
        try:
            sd = self._sounddevice()
        except ImportError as error:
            raise RuntimeError('Install playback support: python -m pip install -r requirements.txt') from error
        if not self.preview:
            render(np.arange(2) / settings.sample_rate, settings)
        self.live = not self.preview and settings.source == MIDI_SOURCE
        # S2: PortAudio's device list and default are read when it initialises; refresh them
        # (no stream is open here) so playback uses today's default and connected devices.
        output_devices.refresh(sd)
        chosen, device_notice, following = self._resolve_device(sd)
        self._reopen.clear()
        attempts = []
        for profile in self._stream_profiles(sd, chosen):
            native = device_rate(sd, profile.get('device'))
            # The requested rate first, then the device's own: a lower-latency profile at the
            # device rate wins over a slower profile at the requested rate (Live MIDI).
            for rate in dict.fromkeys(r for r in (self.requested_rate, native) if r):
                attempts.append((profile, rate))
        for remaining, (profile, rate) in reversed(list(enumerate(reversed(attempts)))):
            options = dict(profile)
            label, callback = options.pop('label', ''), options.pop('callback', False)
            options['blocksize'] = stream_block_frames(options['blocksize'], rate)
            if callback:
                options['callback'] = self._callback
            stream = None
            started = False
            try:
                stream = sd.OutputStream(samplerate=rate, channels=2, dtype='float32', **options)
                song = self._composer if not (self.live or self.preview) else None
                if song is not None:
                    # Decode the audio clips near the playhead at the stream's rate before it starts.
                    project, begin = song
                    audio_clips.prime(project, rate, begin)
                self.sample_rate = rate
                self._prepare(stream, replace(settings, sample_rate=rate), mode, options['blocksize'], label)
                if song is not None and project.audio_clips:
                    origins = self._chain_origins(project, begin, rate)
                    audio_clips.warm_chains(project, origins, rate, settings.switch_sweep_ms, self.chain_state)
                if self.cycle and cycle_factory is not None and rate != settings.sample_rate:
                    # Prepare at the selected device rate before the first write.
                    self.set_cycle(cycle_factory(replace(settings, sample_rate=rate)))
                # PortAudio starts with its buffer full. Anchor display pacing after any warm-up.
                self._full_at = (time.perf_counter(), 0)
                stream.start()
                started = True
                self.device_notice = device_notice
                self.output_name = output_devices.device_name(sd, options.get('device'))
                self._stream_state = (label, callback, following, output_devices.default_token() if self.device is None else None)
                self.rate_notice = (f'Output rate {self.requested_rate/1000:g} kHz is not available on this device; '
                                    f'playing at {rate/1000:g} kHz.' if self.requested_rate and rate != self.requested_rate else '')
                target = self._wait_for_callback_stream if callback else self._run
                self.thread = threading.Thread(target=target, args=(stream,), daemon=True)
                self.thread.start()
            except Exception:
                self.midi.accepting = False
                self.thread = None
                self._stream_state = None
                if stream is not None:
                    try:
                        if started: stream.stop()
                    finally:
                        stream.close()
                if not remaining:
                    raise
                continue
            return

    def _stream_profiles(self, sd, device=None):
        """Stream options to try in order; `device` is the chosen output's index (S2), None for
        the system default."""
        chosen = {} if device is None else dict(device=device)
        if self.live and sys.platform == 'darwin':
            # Blocking writes add about 42 ms of PortAudio buffering on Core Audio.
            # Above 48 kHz the block grows to about 5 ms, as on Windows: inside the running
            # editor, 128 frames at 96 kHz kept only 78% of real time with chords held
            # (Apple M5), while reporting no underruns.
            return [dict(label='Core Audio callback', callback=True, blocksize=LIVE_BLOCK, latency='low', **chosen),
                    dict(label='Core Audio blocking', blocksize=LIVE_BLOCK, latency='low', **chosen)]
        if self.live and sys.platform == 'win32':
            return windows_midi_profiles(sd, device)
        return [dict(blocksize=PLAYBACK_BLOCK, latency=.03 if self.live else .1, **chosen)]

    def _reopen_stream(self, stream, callback=False):
        """Move playback to the current output on the audio thread (S2): the system default
        after it changed, the chosen device after it was picked, or the default after the
        chosen one went away. Keeps the stream's rate (the new device converts if its own
        differs) and the frame count, so playback continues where it was. When no profile
        opens it returns a _ClosedStream whose writes raise the reason: the old stream is
        closed and PortAudio re-initialised, so it must never be touched again."""
        self._reopen.clear()
        try: stream.abort()  # Drop what the old device still had queued.
        except Exception: pass
        try: stream.close()
        except Exception: pass
        sd = self._sounddevice()
        output_devices.refresh(sd)
        chosen, notice, following = self._resolve_device(sd)
        label = self._stream_state[0] if self._stream_state else ''
        profiles = [p for p in self._stream_profiles(sd, chosen) if bool(p.get('callback')) == callback]
        profiles.sort(key=lambda p: p.get('label', '') != label)  # The same kind of stream first.
        error = None
        for profile in profiles:
            options = dict(profile)
            label, _ = options.pop('label', ''), options.pop('callback', False)
            options['blocksize'] = self.block_frames
            if callback: options['callback'] = self._callback
            try:
                new = sd.OutputStream(samplerate=self.sample_rate, channels=2, dtype='float32', **options)
            except Exception as failure:
                error = failure; continue
            try:
                self.output_latency = float(getattr(new, 'latency', .1))
                self.playback.latency = max(0., self.output_latency); self.playback.origin = None  # Re-anchor the clock.
                self._full_at = (time.perf_counter(), self.frames)
                new.start()
            except Exception as failure:
                error = failure
                try: new.close()
                except Exception: pass
                continue
            self.output_mode = label
            self.output_name = output_devices.device_name(sd, options.get('device'))
            self._stream_state = (label, callback, following, output_devices.default_token() if self.device is None else None)
            self.device_notice = notice or (f'Output moved to {self.output_name}.' if self.output_name else '')
            return new
        return _ClosedStream(f'The output device changed and no device opened at {self.sample_rate/1000:g} kHz'
                             + (f' ({error})' if error else '') + '; press Play again.')

    def _prepare(self, stream, settings, mode, block_frames, label):
        # Runs before stream.start: a callback stream can fire immediately.
        self.block_frames = block_frames
        self.output_mode = label
        self.parameters = (settings, mode)
        self.stop_event.clear()
        self._cycle_stop.clear()
        self._cycle_current = None
        self._cycle_index = self._cycle_fade = 0
        self._cycle_request = None
        self.history = deque(maxlen=max(5,int(np.ceil(.04*settings.sample_rate/block_frames))+1))
        self.output_latency = float(getattr(stream,'latency',.1))
        self.playback.reset(settings.sample_rate, self.output_latency)
        self.error = ''
        self.synthesis_ms.clear(); self.write_wait_ms.clear()
        self.underruns = self.frames = 0
        self.clipped_samples = 0
        self.output_peak = 0.
        self.track_peaks = []
        # Audio-track memory across blocks (AW1b): a seek, a loop or a new start is a
        # discontinuity that the chain detects and primes itself.
        self.chain_state = ChainState()
        self._switch_request = self._switch_fade = None
        if self.live:
            self.midi.reset(settings.sample_rate)

    def _next_block(self, count):
        previous = noise_loops.realtime()
        noise_loops.set_realtime(True)
        try: return self._render_block(count)
        finally: noise_loops.set_realtime(previous)

    def _chain_state(self):
        """The playback ChainState (start() makes a fresh one; tests that drive blocks directly
        get one on first use)."""
        state = getattr(self, 'chain_state', None)
        if state is None: state = self.chain_state = ChainState()
        return state

    def _song_elapsed(self, times, rate):
        return times - round(self.count_in_seconds * rate) / rate

    def _chain_origins(self, project, start, rate):
        origins = [float(song_seconds(project, start, 0.))]
        if project.loop:
            end = float(project.seconds(project.loop_end))
            if start < end:
                # The first wrapped sample may overshoot the loop boundary. Check the frames
                # around the rounded crossing using the same arithmetic as the playback clock.
                crossing = round((end - start) * rate) + round(self.count_in_seconds * rate)
                frames = np.arange(max(0, crossing - 1), crossing + 3)
                elapsed = self._song_elapsed(frames / rate, rate)
                first_wrapped = elapsed[start + elapsed >= end][0]
                origins.append(float(song_seconds(project, start, first_wrapped)))
        return origins

    def _song_block(self, times, settings, composer, meter=False, chain_state=None):
        project, start = composer
        elapsed = self._song_elapsed(times, settings.sample_rate)
        positions = song_seconds(project, start, np.maximum(0., elapsed))
        # Never decodes on the audio thread: a clip whose media isn't ready is silent until the
        # decode worker has it (audit P9). start() primes the clips at the playhead first.
        # The count-in renders the start sample repeatedly and silences it after: meter only blocks
        # that are all song (a block that straddles the start loses at most one 10 ms reading).
        peaks = [0.] * len(project.audio_tracks) if meter and project.audio_tracks and not np.any(elapsed < 0) else None
        signal = render_song(positions, project, settings, wait_media=False, track_peaks=peaks,
                             chain_state=chain_state).astype('float32')
        signal[elapsed < 0] = 0
        if peaks:  # Each track's peak before the output limit (AW1 track meters).
            with self._peak_lock:
                old = self.track_peaks
                self.track_peaks = [max(peak, old[t]) if t < len(old) else peak for t, peak in enumerate(peaks)]
        return signal, elapsed, positions

    def _switch_crossfade(self, signal, times):
        """Blend the start of a block after a live switch from the old state's render."""
        request, self._switch_request = self._switch_request, None
        settings = self._parameters[0]
        total = round(settings.switch_sweep_ms / 1000 * settings.sample_rate)
        if request is not None:
            self._switch_fade = (*request, 0, total) if total > 0 and request[0] is not None else None
        if self._switch_fade is None: return signal
        old_parameters, old_composer, done, total = self._switch_fade
        n = min(len(signal), total - done)
        old_settings, old_mode = old_parameters
        old_settings = replace(old_settings, sample_rate=settings.sample_rate)
        try:
            if old_composer is not None:
                old = self._song_block(times[:n], old_settings, old_composer)[0]
            else:
                drawing, music, mixed = render(times[:n], old_settings)
                old = select_signal(drawing, music, mixed, old_settings, old_mode, clip_output=False).astype('float32')
        except ValueError:
            self._switch_fade = None; return signal
        weight = ((done + 1 + np.arange(n)) / total)[:, None]
        signal = signal.copy()
        signal[:n] = old + weight * (signal[:n] - old)
        self._switch_fade = (old_parameters, old_composer, done + n, total) if done + n < total else None
        return signal

    def _render_block(self, count):
        """Render one playback block without waiting for spectrum workers."""
        settings, mode = self.parameters
        times = (self.frames + np.arange(count)) / settings.sample_rate
        composer = self.composer
        click_positions = None
        if self.preview and not self.live:
            signal = self._preview_block(count, settings.sample_rate)
        elif composer is not None and not self.live:
            signal, elapsed, positions = self._song_block(times, settings, composer, meter=True,
                                                          chain_state=self._chain_state())
            signal = self._switch_crossfade(signal, times)
            project = composer[0]
            click_positions = np.where(elapsed < 0, elapsed, positions)
        elif self.cycle and not self.live:
            signal = self._cycle_block(count, settings.sample_rate)
        else:
            if self.live:
                self.midi.process(self.frames, settings, mode)
                drawing, music, mixed = self.midi.render(times, settings)
            else:
                drawing, music, mixed = render(times, settings)
            signal = select_signal(drawing, music, mixed, settings, mode, clip_output=False).astype('float32')
            if not self.live: signal = self._switch_crossfade(signal, times)
        self.clipped_samples += int(np.count_nonzero(abs(signal) > 1))
        if len(signal): self.output_peak = max(self.output_peak, float(np.abs(signal).max()))  # Before the limit.
        signal = np.clip(signal, -1, 1)
        # Brief startup ramp avoids a hard jump when playback begins.
        if self.frames == 0:
            n = min(len(signal), round(.005 * settings.sample_rate))
            signal[:n] *= np.linspace(0, 1, n)[:, None]
        if click_positions is not None and (self.metronome or self.count_in_seconds):
            # After X/Y generation only: exports never call this monitoring path.
            if project.tempo_changes or project.meter_changes:
                clicks = mix_clicks_beats(np.zeros_like(signal), project.beats(click_positions),
                                         settings.sample_rate, TempoMap(project.bpm, project.tempo_changes),
                                         self.metronome_level,
                                         Meter(project.meter_changes) if project.meter_changes else None)
            else:
                clicks = mix_clicks(np.zeros_like(signal), click_positions, project.bpm,
                                   settings.sample_rate, self.metronome_level)
            if not self.metronome: clicks[elapsed >= 0] = 0
            signal = np.clip(signal + clicks, -1, 1).astype('float32')
        return signal

    def _publish_block(self, signal, submitted_at, underflow):
        settings, mode = self.parameters
        self.playback.publish(self.frames,signal,submitted_at,underflow)
        if self.live and self.midi.capture:
            self.midi.capture.write(self.frames, signal, settings, mode)
        self.history.append(signal)
        self.frames += len(signal)

    def _run(self, stream):
        timer_resolution.begin()  # Released in _finish_stream.
        try:
            while not self.stop_event.is_set():
                if self._reopen.is_set() and self._stream_state is not None: stream = self._reopen_stream(stream)
                processing_started = time.perf_counter()
                signal = self._next_block(self.block_frames)
                writing_started = time.perf_counter()
                self.synthesis_ms.append((writing_started-processing_started)*1000)
                submitted_at = time.monotonic()
                try:
                    underflow = stream.write(signal)
                except Exception:
                    if self.stop_event.is_set() or self._stream_state is None: raise
                    # The device went away (unplugged): continue on the current output (S2).
                    stream = self._reopen_stream(stream)
                    submitted_at = time.monotonic()
                    underflow = stream.write(signal)
                if underflow:
                    self.underruns += 1
                written = time.perf_counter()
                self.write_wait_ms.append((written-writing_started)*1000)
                self._publish_block(signal,submitted_at,underflow)
                if written-writing_started > .0005: self._full_at = (written, self.frames)
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
        timer_resolution.begin()  # Released in _finish_stream.
        try:
            while not self.stop_event.wait(.05):
                if self._stream_state is None:  # Not opened by start(): nothing to reopen.
                    if not stream.active:
                        self.error = 'Audio device stopped unexpectedly.'
                        break
                elif self._reopen.is_set() or not stream.active:
                    # A changed default, a new choice or a lost device (S2): continue elsewhere.
                    stream = self._reopen_stream(stream, callback=True)
                    if isinstance(stream, _ClosedStream):
                        self.error = f'Audio device stopped unexpectedly. {stream.reason}'
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
            try:
                if (self.cycle or self.preview) and not self.error:
                    stream.stop()  # Drain the final ramp-down block before closing.
            finally:
                try: stream.close()
                finally:
                    self._stream_state = None  # Nothing open to move any more (S2).
                    self.preview = False
                    self._preview_samples = None
                    timer_resolution.end()

    def buffered_seconds(self):
        """Seconds of audio queued in a blocking stream's device buffer (an estimate), or None.

        Drift-free: re-anchored each time a write had to wait for room, when the buffer was
        full. Between anchors it drains in real time and fills with each block written. The
        editor waits a little with its next display frame while this runs low, so the audio
        thread gets the GIL first (P7c). Live MIDI's callback streams return None.
        """
        anchor = self._full_at
        if anchor is None or self.live or self.cycle or self.preview or not self.running: return None
        at, frames = anchor
        queued = self.output_latency-(time.perf_counter()-at)+(self.frames-frames)/self.sample_rate
        return min(self.output_latency, max(0., queued))

    def expected_rate(self):
        """The rate the next playback stream will run at: the open stream's, the output-rate
        preference's, or the output device's (queried once), so audio clips can be prepared
        before Play (audit P9)."""
        if self.running: return self.sample_rate
        if self.requested_rate: return self.requested_rate
        if getattr(self, '_device_rate', None) is None:
            try:
                sd = self._sounddevice()
                output_devices.refresh(sd)
                self._device_rate = device_rate(sd, self._resolve_device(sd)[0])
            except Exception:
                self._device_rate = DEFAULT_RATE
        return self._device_rate

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

    def take_track_peaks(self):
        """Each audio track's peak since the last call (a list by track index), then start again."""
        with self._peak_lock: peaks, self.track_peaks = self.track_peaks, []
        return peaks

    def take_output_peak(self):
        """The output's peak since the last call (above 1 when it clipped), then start again."""
        peak, self.output_peak = self.output_peak, 0.
        return peak

    def stop(self):
        if self.cycle_running or self.preview_running:
            self._cycle_stop.set()
            if self.thread is not None: self.thread.join(timeout=1)
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        if self.running:
            raise RuntimeError('Audio device is still stopping; try again shortly.')
        self.preview = False
        self._preview_samples = None
        self.cycle = False
        self._cycle_current = None
        self._cycle_index = self._cycle_fade = 0
        self._cycle_request = None
