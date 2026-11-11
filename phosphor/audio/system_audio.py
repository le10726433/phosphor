"""Native macOS / Windows system-audio input for visualization only."""
from collections import deque
import threading
import sys
import time
import numpy as np

API_LOCK = threading.Lock()


def backend():
    try:
        import pyaudiowpatch
        return pyaudiowpatch
    except ImportError as error:
        raise RuntimeError('Install system-audio support: python -m pip install -r requirements.txt') from error


class SystemAudio:
    STALE_SECONDS = .25

    def __init__(self, module=None, clock=time.monotonic):
        self.module = module
        self.clock = clock
        self.thread = None
        self.stop_event = threading.Event()
        self.history = deque(maxlen=16)
        self.ready = False
        self.error = ''
        self.device_name = ''
        self.sample_rate = 48000
        self.channels = 2
        self.frames = self.status_events = 0
        self.peak = self.peak_max = 0.
        self.last_sample = float('-inf')

    @staticmethod
    def devices(module=None):
        if sys.platform == 'darwin' and module is None:
            from phosphor.audio.macos_system_audio import MAC_SOURCE
            return [MAC_SOURCE]
        with API_LOCK, (module or backend()).PyAudio() as manager:
            return [str(info['name']) for info in manager.get_loopback_device_info_generator()
                    if info.get('isLoopbackDevice') and info['maxInputChannels'] > 0]

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, name=None):
        self.stop()
        self.history.clear()
        self.error = self.device_name = ''
        self.frames = self.status_events = 0
        self.peak = self.peak_max = 0.
        self.last_sample = float('-inf')
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, args=(name,), daemon=True)
        self.thread.start()

    def _run(self, name):
        try:
            if sys.platform == 'darwin' and self.module is None:
                from phosphor.audio.macos_system_audio import capture_native
                capture_native(self,name)
                return
            module = self.module or backend()
            with API_LOCK, module.PyAudio() as manager:
                if name is None:
                    info = manager.get_default_wasapi_loopback()
                else:
                    matches = [info for info in manager.get_loopback_device_info_generator() if info['name'] == name]
                    if len(matches) != 1:
                        raise ValueError('Playback device changed or is ambiguous. Refresh devices and select again.')
                    info = matches[0]
                if not info.get('isLoopbackDevice') or info['maxInputChannels'] < 1:
                    raise ValueError('Select a WASAPI playback loopback device. Microphone capture is not supported.')
                self.device_name = str(info['name'])
                self.channels = int(info['maxInputChannels'])
                self.sample_rate = int(info['defaultSampleRate'])
                if self.sample_rate <= 0: raise ValueError('Playback device has an invalid sample rate.')
                if self.stop_event.is_set(): return

                def callback(data, count, timing, status):
                    try:
                        block = np.frombuffer(data, dtype=np.float32).reshape(-1, self.channels)
                        if not np.isfinite(block).all(): raise ValueError('Capture returned invalid audio samples.')
                        # Preserve L/R samples. Mono is duplicated; surround uses
                        # its first two channels without a synthetic downmix.
                        stereo = np.repeat(block, 2, axis=1) if self.channels == 1 else block[:, :2]
                        self.accept_samples(stereo)
                        if status: self.status_events += 1
                        return (None, module.paContinue)
                    except Exception as error:
                        self.error = str(error)
                        return (None, module.paAbort)

                with manager.open(format=module.paFloat32, channels=self.channels,
                        rate=self.sample_rate, input=True, output=False,
                        input_device_index=int(info['index']), frames_per_buffer=1024,
                        stream_callback=callback) as stream:
                    self.ready = True
                    while not self.stop_event.wait(.05):
                        if not stream.is_active():
                            raise RuntimeError(self.error or 'Capture stopped. The playback device may have disconnected; refresh and restart.')
        except Exception as error:
            self.error = str(error)
        finally:
            self.ready = False

    def accept_samples(self, stereo):
        stereo = np.asarray(stereo,dtype=np.float32)
        if stereo.ndim != 2 or stereo.shape[1] != 2 or not np.isfinite(stereo).all():
            raise ValueError('Capture returned invalid audio samples.')
        if not len(stereo): return
        stamp = self.clock()
        self.history.append((stamp,stereo.copy()))
        self.peak = float(np.max(np.abs(stereo)))
        self.peak_max = max(self.peak_max,self.peak)
        self.frames += len(stereo)
        self.last_sample = stamp

    def samples(self, count):
        now = self.clock()
        if not self.ready:
            return np.zeros((count, 2), dtype=np.float32)
        # Some WASAPI endpoints stop callbacks during silence. Expire old blocks
        # instead of leaving the last sound frozen on screen.
        blocks, available = [], 0
        for stamp, block in reversed(tuple(self.history)):
            if now-stamp > self.STALE_SECONDS: break
            blocks.append(block); available += len(block)
            if available >= count: break
        if not blocks: return np.zeros((count, 2), dtype=np.float32)
        result = np.concatenate(blocks[::-1])[-count:]
        if len(result) < count:
            result = np.pad(result, ((count-len(result),0),(0,0)))
        return result

    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=2)
        if self.running: raise RuntimeError('System audio capture is still stopping. Try again shortly.')
        self.ready = False
        self.history.clear()
