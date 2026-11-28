"""Render 4 path-traced verification shots of Phosphor's C1-170 3D Lab Bench via wgpu (Metal / DX12).

Run from the app folder (the one with app.py):
    .venv/bin/python tools/capture_pathtrace.py
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PySide6.QtWidgets import QApplication

from phosphor.raytrace.engine import PathTracerEngine, make_demo_crt_image

OUT_DIR = ROOT / '.captures'


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication([])  # Needed only to paint the Qt faceplate/prop textures once at startup

    t0 = time.perf_counter()
    pt = PathTracerEngine(width=1280, height=800)
    print(f'Initialized PathTracerEngine ({pt.backend_name}) in {time.perf_counter() - t0:.2f}s', flush=True)
    print(f"BVH: {pt.scene['stats']}", flush=True)

    # Upload vibrant glowing P31 green Lissajous CRT waveform
    pt.update_crt_texture(make_demo_crt_image(512, kind='lissajous', color=(84, 255, 130)))

    # Interactive low-SPP verification (800x500, 1 SPP and 4 SPP)
    pt.resize(800, 500)
    pt.camera_preset = 0
    pt.lamp_on = True
    pt.fluor_on = False
    pt.exposure = 1.18
    for name, spp in (('pt_00_lamp_1spp', 1), ('pt_00b_lamp_4spp', 4)):
        ts = time.perf_counter()
        pt.render_samples(total_spp=spp, batch_spp=1)
        rgba = pt.read_rgba()
        dt = time.perf_counter() - ts
        out_path = OUT_DIR / f'{name}.png'
        Image.fromarray(rgba, mode='RGBA').save(out_path)
        print(f'Saved {out_path} ({spp} spp @ 800x500 in {dt * 1000.0:.1f} ms -> {spp / max(dt, 1e-4):.1f} spp/s)', flush=True)

    # Full-res 1280x800 high-SPP verification shots
    pt.resize(1280, 800)
    shots = [
        ('pt_01_lamp',             dict(camera_preset=0, lamp_on=True,  fluor_on=False, exposure=1.18), 64),
        ('pt_02_fluorescent',      dict(camera_preset=0, lamp_on=True,  fluor_on=True,  exposure=1.02), 64),
        ('pt_03_dark_crt_gi',      dict(camera_preset=0, lamp_on=False, fluor_on=False, exposure=1.35), 96),
        ('pt_04_controls_closeup', dict(camera_preset=2, lamp_on=True,  fluor_on=True,  exposure=1.05), 64),
    ]

    for name, cfg, spp in shots:
        for k, v in cfg.items():
            setattr(pt, k, v)
        ts = time.perf_counter()
        pt.render_samples(total_spp=spp, batch_spp=8)
        rgba = pt.read_rgba()
        dt = time.perf_counter() - ts
        out_path = OUT_DIR / f'{name}.png'
        Image.fromarray(rgba, mode='RGBA').save(out_path)
        print(f'Saved {out_path} ({spp} spp in {dt:.2f}s -> {spp / max(dt, 1e-4):.1f} spp/s)', flush=True)

    app.quit()


if __name__ == '__main__':
    main()
