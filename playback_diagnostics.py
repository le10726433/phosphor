"""Measure actual presentation and processing during stopped/play/stopped phases."""
import time
import os
import numpy as np


def timing_stats(values):
    values = np.asarray(list(values), dtype=float)
    return dict(count=len(values), median=float(np.median(values)),
                p95=float(np.percentile(values,95)), maximum=float(np.max(values))) if len(values) else {}


def presentation_stats(stamps, elapsed):
    stamps = list(stamps)
    return dict(frames=len(stamps), fps=len(stamps)/elapsed if elapsed > 0 else 0,
                intervals_ms=timing_stats(np.diff(stamps)*1000))


class PlaybackDiagnostics:
    def __init__(self, window):
        self.window = window
        self.phases = {}
        self.error = ''

    def begin(self, name):
        w = self.window
        self.name, self.started = name, time.perf_counter()
        w.frame_times.clear(); w.tick_times.clear()
        w.prep_ms.clear(); w.host.scope.upload_ms.clear()
        w.audio.synthesis_ms.clear(); w.audio.write_wait_ms.clear()
        self.audio_frames = w.audio.frames
        self.underruns = w.audio.underruns
        self.playback_ticks = w.playback_ticks
        self.fresh_windows = w.fresh_playback_windows

    def end(self):
        w = self.window
        elapsed = time.perf_counter()-self.started
        self.phases[self.name] = dict(seconds=elapsed,
            presentation=presentation_stats(w.frame_times,elapsed),
            ui_updates=presentation_stats(w.tick_times,elapsed),
            sample_preparation_ms=timing_stats(w.prep_ms),
            geometry_upload_ms=timing_stats(w.host.scope.upload_ms),
            audio_synthesis_ms=timing_stats(w.audio.synthesis_ms),
            audio_write_wait_ms=timing_stats(w.audio.write_wait_ms),
            audio_frames=w.audio.frames-self.audio_frames,
            audio_underruns=w.audio.underruns-self.underruns,
            audio_error=w.audio.error,
            playback_ticks=w.playback_ticks-self.playback_ticks,
            fresh_audio_windows=w.fresh_playback_windows-self.fresh_windows)

    def start_audio(self):
        try: self.window.audio.start(self.window.output_settings(),self.window.scene.mode)
        except Exception as error: self.error = str(error)

    def report(self):
        w = self.window
        return dict(phases=self.phases,error=self.error,
            preferences=dict(w.preferences),source=w.scene.settings.source,
            window_size=[w.width(),w.height()],
            viewport_size=[w.host.scope.width(),w.host.scope.height()],
            device_pixel_ratio=w.host.view.devicePixelRatio(),
            audio_block_frames=w.audio.BLOCK_FRAMES,
            reported_audio_latency_seconds=w.audio.playback.latency,
            render_loop=os.environ.get('QSG_RENDER_LOOP'),
            screen_refresh_hz=w.host.view.screen().refreshRate() if w.host.view.screen() else None,
            note='Presentation events are measured; geometry timing excludes GPU completion.')
