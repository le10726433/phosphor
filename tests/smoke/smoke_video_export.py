"""Native frame fidelity always runs; optional ffmpeg integration.

NV12 tolerance (0.57.0): the GPU pass must equal export_nv12.rgba_to_nv12 of the RGBA
oracle (VideoRenderer.frame) exactly; against ffmpeg's own rgba -> nv12 of the old
full frame, luma |diff| <= 1, chroma |diff| <= 16 with at most 0.01% of chroma samples
above 8 and mean chroma |diff| < 0.05 (ffmpeg filters chroma across 2x2 block edges,
the shader takes the block mean; sharp beam edges on Metal reach 12 on a few samples).
"""
import _path  # noqa: F401
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PySide6.QtGui import QColor, QSurfaceFormat
from PySide6.QtWidgets import QApplication
from phosphor.editor.qt_platform import configure_graphics
from phosphor.editor import video_export as ui
from phosphor.display.video_renderer import VideoRenderer
from phosphor.display.crt_theme import PALETTES
from phosphor.engine.composition import Project, Clip, Note, render_song
from phosphor.engine.signal_engine import Settings, Shape
from phosphor.engine import video_export as engine_export
from phosphor.engine.video_export import find_ffmpeg
from phosphor.display.export_nv12 import rgba_to_nv12, Nv12Canvas
from phosphor.storage.app_files import DEFAULT_PREFERENCES

configure_graphics()
surface = QSurfaceFormat(); surface.setSamples(4); QSurfaceFormat.setDefaultFormat(surface)
app = QApplication([])
preferences = dict(DEFAULT_PREFERENCES, show_grid=False, crt_scanlines=False)
settings = Settings(shapes=(Shape(spin_z=.7, rotation_x=20.),))
song = Project(bpm=120., notes=(Note(0., 4., 57),), clips=(Clip(0., 4., settings),))
samples = render_song(np.arange(96000)/48000, song, settings)
background = QColor(PALETTES[preferences['palette']]['background'])
rgb = np.array([background.red(), background.green(), background.blue()])


def pixels(image):
    return np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine()//4, 4)[:, :image.width(), :3].copy()


def check_picture(data):
    # The green palette must contain a coloured beam, plus untouched background.
    assert np.any((data[:, :, 1].astype(int) > data[:, :, 0].astype(int)+20) & (data[:, :, 1] > 40)), 'No trace-coloured pixels'
    assert np.any(np.all(data == rgb, axis=2)), 'No background pixels'


with VideoRenderer(preferences, 320, 320, 30, samples, 48000) as renderer:
    frames = [pixels(renderer.frame(k)) for k in range(4)]
    for frame in frames: check_picture(frame)
    assert all(np.any(a != b) for a, b in zip(frames, frames[1:])), 'Consecutive frames are identical'
with VideoRenderer(preferences, 1920, 1080, 30, samples, 48000) as renderer:
    wide = pixels(renderer.frame(0))
    assert wide.shape == (1080, 1920, 3)
    assert np.all(wide[:, :420] == rgb) and np.all(wide[:, 1500:] == rgb), 'Letterbox bands are not background'
    check_picture(wide[:, 420:1500])
    # A square render must be pasted unchanged in the centre of the wide frame.
    with VideoRenderer(preferences, 1080, 1080, 30, samples, 48000) as square:
        np.testing.assert_array_equal(wide[:, 420:1500], pixels(square.frame(0)))

def nv12_frames(width, height, count):
    with VideoRenderer(preferences, width, height, 30, samples, 48000, nv12=True) as renderer:
        if renderer.nv12 is None: return None
        frames = []
        for k in range(count + 1):
            readback = renderer.frame_nv12(k) if k < count else renderer.finish_nv12()
            if readback is not None: frames.append(np.frombuffer(memoryview(readback.data), np.uint8).copy())
        return frames


def rgba_frames(width, height, count):
    with VideoRenderer(preferences, width, height, 30, samples, 48000) as renderer:
        frames = []
        for k in range(count):
            image = renderer.frame(k)
            frames.append(np.frombuffer(image.constBits(), np.uint8).reshape(height, width, 4).copy())
        return frames


ffmpeg = find_ffmpeg('')
gpu_nv12 = nv12_frames(1920, 1080, 4)
if gpu_nv12 is None:
    print('SKIP: GPU NV12 pass unavailable on this graphics API; export uses the RGBA path.')
else:
    old = rgba_frames(1920, 1080, 4)
    canvas = Nv12Canvas(1920, 1080, 1080, PALETTES[preferences['palette']]['background'])
    for k, (square, full) in enumerate(zip(gpu_nv12, old)):
        np.testing.assert_array_equal(square, rgba_to_nv12(np.ascontiguousarray(full[:, 420:1500])),
                                      err_msg=f'GPU NV12 differs from the NumPy reference in frame {k}')
        if ffmpeg is not None:
            reference = np.frombuffer(subprocess.run([ffmpeg, '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgba',
                '-s', '1920x1080', '-i', '-', '-pix_fmt', 'nv12', '-f', 'rawvideo', '-'], input=full.tobytes(),
                capture_output=True, check=True, timeout=30).stdout, np.uint8).astype(int)
            frame = canvas.place(square).astype(int); luma = 1920 * 1080
            dy, dc = np.abs(reference[:luma] - frame[:luma]), np.abs(reference[luma:] - frame[luma:])
            assert dy.max() <= 1 and dc.max() <= 16 and (dc > 8).mean() < 1e-4 and dc.mean() < .05, \
                (k, dy.max(), dc.max(), int((dc > 8).sum()), dc.mean())


def export(target, prepare, **options):
    results = []
    job = ui.VideoExportJob(None, ffmpeg, target, preferences, 320, 320, 30,
        prepare, lambda success, error: results.append((success, error)), **options)
    deadline = time.monotonic()+120
    while not results and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.002)
    if not results:
        job.cancel()
        while not results: app.processEvents(); time.sleep(.002)
        raise AssertionError('Video export timed out')
    assert results == [(True, '')], results
    return job


def frame_count(probe, target):
    result = subprocess.run([probe, '-v', 'error', '-select_streams', 'v:0', '-count_frames',
        '-show_entries', 'stream=nb_read_frames,width,height', '-of', 'json', str(target)],
        capture_output=True, text=True, timeout=15, check=True)
    stream = json.loads(result.stdout)['streams'][0]
    assert (stream['width'], stream['height']) == (320, 320)
    return int(stream['nb_read_frames'])


# Patch module names, never Shiboken class methods with MagicMock.
messages = []
class Messages:
    @staticmethod
    def information(parent, title, text): messages.append((title, text))
with patch.object(ui, 'find_ffmpeg', lambda _: None), patch.object(ui, 'QMessageBox', Messages):
    assert ui.require_ffmpeg(None, '') is None
assert 'https://ffmpeg.org' in messages[0][1] and 'Preferences' in messages[0][1]

if ffmpeg is None:
    print('SKIP: ffmpeg not installed; encode/ffprobe integration skipped. Native frame checks passed.')
else:
    probe = shutil.which('ffprobe') or str(Path(ffmpeg).with_name('ffprobe' + Path(ffmpeg).suffix))
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder)/'song.mp4'
        export(target, lambda stop: samples)
        assert frame_count(probe, target) == 60
        # Streamed audio blocks (the app's path): video starts before the audio is complete.
        streamed = Path(folder)/'streamed.mkv'
        export(streamed, lambda stop: (samples[i:i+48000] for i in range(0, len(samples), 48000)),
               container='MKV', encoder='Software', sample_count=len(samples))
        assert frame_count(probe, streamed) == 60
        # A hardware encoder that fails after the probe restarts the video with x264.
        failing = Path(folder)/'fallback.mp4'
        with patch.object(ui, 'choose_encoder', lambda ffmpeg, choice: 'h264_nvenc'),              patch.dict(engine_export.CODEC_ARGUMENTS, {'h264_nvenc': ('-no-such-option', '1')}):
            job = export(failing, lambda stop: samples, encoder='Hardware')
        assert job.retried and job.codec == 'libx264', (job.retried, job.codec)
        assert frame_count(probe, failing) == 60
    print('Video export passed: native frames, square centring, GPU NV12 tolerance, missing-ffmpeg '
          'explanation, MP4, streamed MKV and hardware fallback (60 probed frames each).')
app.processEvents()
