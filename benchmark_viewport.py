"""Compare UI construction AND Tk painting using a reproducible stereo replay.

Run in a desktop session. No MIDI device is opened and no sound is played.
Example: python benchmark_viewport.py --program path/to/another/app/folder
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--program', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--frames', type=int, default=40)
    parser.add_argument('--output', type=Path)
    options = parser.parse_args()
    sys.path.insert(0, str(options.program.resolve()))
    import numpy as np
    import tkinter as tk
    from app import App
    from app_files import DEFAULT_PREFERENCES
    from score_source import SCALE_NAME
    from signal_engine import render

    class ReplayThread:
        def is_alive(self):
            return True

    user = Path(__file__).resolve().parent / '.user'
    user.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=user) as folder:
        root = tk.Tk()
        app = App(root, preferences_path=Path(folder) / 'preferences.json')
        root.after(200, root.quit)
        root.mainloop()
        for timer in root.tk.call('after', 'info'):
            root.after_cancel(timer)
        root.after = lambda *args, **kwargs: None
        app.source.set(SCALE_NAME)
        settings = app.settings()
        prepared = [render(np.arange((frame+1)*1600, (frame+1)*1600+2048)/48000, settings)[2].astype('float32')
                    for frame in range(options.frames)]
        app.audio.thread = ReplayThread()
        app.audio.parameters = (settings, app.mode.get())
        results = dict(frame_count=options.frames, viewport='1180x820 app window, one cube, 1920 displayed samples',
                       input='Same precomputed stereo scale samples in both versions; audio devices unused.')
        try:
            for effects in (False, True):
                preferences = dict(DEFAULT_PREFERENCES)
                if 'crt_effects' in preferences:
                    preferences['crt_effects'] = effects
                else:
                    preferences.update(crt_scanlines=effects, crt_glow=effects)
                app.apply_preferences(preferences)
                root.update_idletasks()
                durations, builds, paints = [], [], []
                for frame, signal in enumerate(prepared):
                    app.audio.history.clear()
                    app.audio.history.extend(signal[index:index+512] for index in range(0, 2048, 512))
                    app.audio.frames = (frame+1)*1600+2048
                    start = time.perf_counter()
                    app.tick()
                    middle = time.perf_counter()
                    root.update_idletasks()
                    end = time.perf_counter()
                    durations.append((end-start)*1000)
                    builds.append((middle-start)*1000)
                    paints.append((end-middle)*1000)
                    time.sleep(max(0, 1/30-(time.perf_counter()-start)))
                results['effects_on' if effects else 'effects_off'] = dict(
                    median_ms=round(float(np.median(durations)), 2), mean_ms=round(float(np.mean(durations)), 2),
                    p95_ms=round(float(np.percentile(durations, 95)), 2),
                    build_median_ms=round(float(np.median(builds)), 2), paint_median_ms=round(float(np.median(paints)), 2),
                    canvas_items=len(app.canvas.find_all()))
        finally:
            app.audio.thread = None
            app.shutdown_audio()
            root.update_idletasks()
            root.destroy()
    text = json.dumps(results, indent=2) + '\n'
    if options.output:
        options.output.write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
