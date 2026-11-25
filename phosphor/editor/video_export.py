"""Video choices and a timer-driven GUI renderer with a separate pipe worker."""
from collections import deque
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import os
import uuid

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox, QDoubleSpinBox,
    QDialogButtonBox, QProgressDialog, QMessageBox)
import numpy as np
from phosphor.engine.video_export import (find_ffmpeg, ffmpeg_command, mux_command, choose_encoder,
    ENCODER_CHOICES, SOFTWARE_CODEC)
from phosphor.display.crt_theme import PALETTES
from phosphor.display.export_nv12 import Nv12Canvas
from phosphor.display.video_renderer import VideoRenderer
from phosphor.storage.audio_writer import AudioWriter, soundfile


def require_ffmpeg(parent, preference_path):
    executable = find_ffmpeg(preference_path)
    if executable is None:
        QMessageBox.information(parent, 'Video export needs ffmpeg',
            'Install ffmpeg from https://ffmpeg.org/download.html, then add it to PATH or '
            'set Preferences > Export > ffmpeg location. Use Test to check the installation.')
    return executable


class VideoExportDialog(QDialog):
    def __init__(self, parent, duration, composer):
        super().__init__(parent); self.setWindowTitle('Export video')
        layout = QVBoxLayout(self); form = QFormLayout(); layout.addLayout(form)
        self.resolution = QComboBox()
        for w, h in ((1080, 1080), (1920, 1080), (3840, 2160)):
            self.resolution.addItem(f'{w} × {h}', (w, h))
        self.fps = QComboBox(); self.fps.addItems(['30', '60'])
        self.container = QComboBox(); self.container.addItems(['MP4 / AAC 320k', 'MKV / FLAC lossless'])
        if soundfile is None:
            self.container.model().item(1).setEnabled(False)
            self.container.setToolTip('Install soundfile for lossless FLAC audio in MKV.')
        self.encoder = QComboBox(); self.encoder.addItems(ENCODER_CHOICES)
        self.encoder.setToolTip('Auto: the encoder measured fastest on this platform (NVIDIA NVENC on Windows, '
                                'x264 on macOS). Hardware: NVENC, AMD AMF, Intel Quick Sync or VideoToolbox. '
                                'Software: x264. A missing or failing hardware encoder falls back to x264.')
        self.duration = QDoubleSpinBox(); self.duration.setRange(.01, 86400); self.duration.setDecimals(2)
        self.duration.setValue(duration); self.duration.setSuffix(' seconds'); self.duration.setEnabled(not composer)
        for title, widget in (('Resolution', self.resolution), ('Frame rate', self.fps), ('Container / audio', self.container), ('Encoder', self.encoder), ('Whole song' if composer else 'Duration', self.duration)):
            form.addRow(title, widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)


CODEC_NAMES = {'libx264': 'x264', 'h264_nvenc': 'NVIDIA NVENC', 'h264_amf': 'AMD AMF',
               'h264_qsv': 'Intel Quick Sync', 'h264_videotoolbox': 'VideoToolbox'}
QUEUED_FRAMES = 3  # Bounded: about 21 MB of 4K NV12 frames between renderer and encoder.


class VideoExportJob:
    """prepare() runs off-thread; all Qt Quick work stays on the GUI thread.

    Pipeline (0.57.0): a worker renders audio ahead (prepare may yield blocks; video
    starts after the first one) and writes the audio file; the GUI thread renders frames,
    packs them to NV12 on the GPU and reads each back one frame late (VideoRenderer.
    frame_nv12); a bounded queue feeds the pipe thread, which pads the square into the
    output frame and writes it to a video-only ffmpeg. Encoder backpressure never blocks
    the GUI: a frame waits in self.pending. When video and audio are done, a second
    ffmpeg copies the video and encodes the audio exactly as before. A hardware encoder
    that fails restarts the video with x264. The target is replaced only on success.
    """
    def __init__(self, parent, ffmpeg, target, preferences, width, height, fps, prepare, finished, container='MP4',
                 encoder='Auto', sample_count=None):
        self.target = Path(target); self.preferences = dict(preferences)
        self.width, self.height, self.fps = width, height, fps
        self.finished = finished; self.container = container; self.ffmpeg = ffmpeg
        self.temp = tempfile.TemporaryDirectory(prefix='phosphor-video-')
        self.partial = self.target.with_name('.export-' + uuid.uuid4().hex + self.target.suffix)
        self.audio = Path(self.temp.name) / ('audio.wav' if container == 'MP4' else 'audio.flac')
        self.video = Path(self.temp.name) / 'video.mkv'
        self.frames = queue.Queue(QUEUED_FRAMES); self.ready = queue.Queue(); self.stop = threading.Event()
        self.encoder_done = threading.Event(); self.audio_done = threading.Event(); self.errors = deque(maxlen=80)
        self.process = None; self.renderer = None; self.pending = None; self.k = 0; self.rendered = 0
        self.closed = False; self.rendering = False; self.samples = None; self.available = 0
        self.codec = SOFTWARE_CODEC; self.retried = False; self.canvas = None
        self.encoder_done.set()  # Set whenever no pipe thread runs (start_video clears it).
        self.progress = QProgressDialog('Preparing audio…', 'Cancel', 0, 0, parent)
        self.progress.setWindowTitle('Export video'); self.progress.setMinimumDuration(0)
        self.progress.setAutoClose(False); self.progress.setAutoReset(False)
        self.progress.canceled.connect(self.cancel)
        self.timer = QTimer(parent); self.timer.timeout.connect(self.tick)
        def preparation():
            try:
                self.codec = choose_encoder(ffmpeg, encoder)
                if self.stop.is_set(): return
                result = prepare(self.stop)
                with AudioWriter(self.audio, 48000, 'WAV' if container == 'MP4' else 'FLAC', 24) as writer:
                    if isinstance(result, np.ndarray) or sample_count is None:
                        samples = np.asarray(result) if isinstance(result, np.ndarray) else np.concatenate(list(result))
                        if self.stop.is_set(): return
                        self.samples = samples; self.available = len(samples); self.ready.put(samples)
                        for start in range(0, len(samples), 48000):
                            if self.stop.is_set(): return
                            writer.write(samples[start:start+48000])
                    else:
                        # Streaming: the renderer reads self.samples up to self.available.
                        self.samples = np.zeros((sample_count, 2)); self.ready.put(self.samples); filled = 0
                        for block in result:
                            if self.stop.is_set(): return
                            block = np.asarray(block)
                            self.samples[filled:filled+len(block)] = block; writer.write(block)
                            filled += len(block); self.available = filled
                        if filled != sample_count: raise RuntimeError(f'Audio preparation returned {filled} of {sample_count} samples.')
                self.audio_done.set()
            except Exception as error: self.ready.put(error)
        self.preparation = threading.Thread(target=preparation, daemon=True); self.preparation.start()
        self.timer.start(0)

    def start_video(self):
        """(Re)start the renderer and the video-only encoder at frame 0."""
        if self.renderer is not None: self.renderer.close()
        self.renderer = VideoRenderer(self.preferences, self.width, self.height, self.fps, self.samples, 48000, nv12=True)
        nv12 = self.renderer.nv12 is not None
        self.canvas = (Nv12Canvas(self.width, self.height, self.renderer.side, PALETTES[self.preferences['palette']]['background'])
                       if nv12 else None)
        self.frames = queue.Queue(QUEUED_FRAMES); self.pending = None; self.k = self.rendered = 0
        self.progress.setRange(0, len(self.renderer.ranges))
        self.progress.setLabelText(f'Rendering video ({CODEC_NAMES[self.codec]})…')
        self.errors.clear()
        self.process = subprocess.Popen(ffmpeg_command(self.ffmpeg, self.width, self.height, self.fps, None, self.video,
                'MKV', self.codec, 'nv12' if nv12 else 'rgba'), stdin=subprocess.PIPE, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL)
        self.encoder_done.clear()
        threading.Thread(target=self.encode, args=(self.process, self.frames, self.canvas), daemon=True).start()

    def encode(self, process, frames, canvas):
        """Pipe thread: frames → ffmpeg; then, once the audio is written, the final mux."""
        retry = False
        def drain(stream):
            for line in stream: self.errors.append(line.decode('utf-8', 'replace'))
        try:
            reader = threading.Thread(target=drain, args=(process.stderr,), daemon=True); reader.start()
            try:
                while not self.stop.is_set():
                    try: frame = frames.get(timeout=.1)
                    except queue.Empty: continue
                    if frame is None: break
                    process.stdin.write(frame if canvas is None else canvas.place(memoryview(frame.data)))
                    del frame  # Release the readback (and its frame buffer) before waiting again.
                process.stdin.close()
            except OSError:
                pass  # ffmpeg exited early; its exit code and stderr explain why.
            code = process.wait(); reader.join()
            if self.stop.is_set(): return
            if code:
                if self.codec != SOFTWARE_CODEC and not self.retried:
                    retry = True; self.ready.put(('retry', ''.join(self.errors)[-400:])); return
                raise RuntimeError(''.join(self.errors) or f'ffmpeg exited with {code}')
            while not self.audio_done.wait(.1):
                if self.stop.is_set() or not self.preparation.is_alive(): return
            self.errors.clear()
            self.process = process = subprocess.Popen(mux_command(self.ffmpeg, self.video, self.audio, self.partial, self.container),
                stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL)
            if self.stop.is_set(): process.kill()
            reader = threading.Thread(target=drain, args=(process.stderr,), daemon=True); reader.start()
            code = process.wait(); reader.join()
            if code and not self.stop.is_set(): raise RuntimeError(''.join(self.errors) or f'ffmpeg exited with {code}')
        except Exception as error:
            if not self.stop.is_set(): self.ready.put(error)
        finally:
            if process.poll() is None:
                process.kill(); process.wait()
            if process.stdin and not process.stdin.closed:
                try: process.stdin.close()
                except OSError: pass
            process.stderr.close()
            if not retry: self.encoder_done.set()

    def cancel(self):
        self.stop.set()
        if self.process is not None and self.process.poll() is None: self.process.kill()

    def next_item(self):
        """Render one step; returns bytes/readback for the queue, or None when nothing is ready."""
        renderer = self.renderer; count = len(renderer.ranges)
        if self.rendered < count and renderer.ranges[self.rendered][1] > self.available: return None  # Audio not ready.
        self.rendering = True
        try:
            if renderer.nv12 is None:
                image = renderer.frame(self.rendered); item = bytes(image.constBits())
            else:
                item = renderer.frame_nv12(self.rendered) if self.rendered < count else renderer.finish_nv12()
        finally: self.rendering = False
        self.rendered += 1
        if renderer.notice: self.progress.setLabelText(renderer.notice)
        return item

    def tick(self):
        if self.rendering or self.closed: return
        try:
            if self.stop.is_set():
                if self.encoder_done.is_set() and not self.preparation.is_alive(): self.finish(False)
                return
            if not self.ready.empty():
                value = self.ready.get_nowait()
                if isinstance(value, Exception): raise value
                if isinstance(value, tuple):  # The hardware encoder failed: start over with x264.
                    self.retried = True; self.codec = SOFTWARE_CODEC
                self.start_video()
            if self.renderer is None: return
            count = len(self.renderer.ranges)
            if self.k > count:
                if self.encoder_done.is_set(): self.finish(True)
                return
            if self.pending is None and self.k < count:
                self.pending = self.next_item()  # NV12 frames arrive one step late.
                if self.pending is None: return self.idle(self.rendered < count)
            try: self.frames.put_nowait(self.pending)  # After the last frame, None ends the pipe.
            except queue.Full: return self.idle(True)
            self.idle(False); self.pending = None; self.k += 1; self.progress.setValue(min(self.k, count))
            if self.k == count: self.progress.setLabelText('Finishing video…')
        except Exception as error:
            self.failure = str(error); self.cancel()

    def idle(self, waiting):
        """Poll every millisecond while waiting for audio or the encoder, else run flat out."""
        interval = 1 if waiting else 0
        if self.timer.interval() != interval: self.timer.setInterval(interval)

    def finish(self, success):
        if self.closed: return
        self.closed = True; self.timer.stop(); self.timer.deleteLater()
        try:
            if self.renderer: self.renderer.close()
            if success: os.replace(self.partial, self.target)
        except Exception as error:
            success = False; self.failure = str(error)
        finally:
            try: self.partial.unlink(missing_ok=True); self.temp.cleanup()
            except OSError: pass  # Never leave the editor waiting on a temporary file.
            self.progress.close(); self.progress.deleteLater()
            self.finished(success, getattr(self, 'failure', ''))
