# Windows Qt GPU prototype — 0.4.0

**Update, 0.5.0:** the user reviewed this prototype and approved implementation.
`python app.py` and `LaunchQt.bat` now launch the full Qt editor.
Its multi-shape editor, unrestricted position/size dragging, XYZ overlays,
sliders with numeric entry/reset, MIDI setup/recording, scene lifecycle, menus,
shortcuts and persistent preferences are implemented. `python app.py --cpu` keeps
the Tk fallback; `python tools/qt_preview.py` keeps this small benchmark. The sections
below describe the original 0.4.0 prototype and its limitations, not the new full
editor. The reviewed source remains tagged `oscilloscope-v0.4.0-reviewed`.

This is a separate viewport experiment, not yet a replacement for the full Tk
editor. Run `app.py` for the existing menus, shape dragging, scene saving,
MIDI-device selection, recording, exports and full preferences.

## Launch in VS Code

From this folder, using the project's Python environment:

```powershell
python -m pip install -r requirements/requirements-qt.txt
python tools/qt_preview.py
```

`LaunchQt.bat` uses `.venv\Scripts\python.exe` when available, otherwise `python`.
This workspace also supports private `.qt-deps` and `.audio-deps` directories;
dependencies are not committed. The Qt dependency is pinned to PySide6 6.10.3.
The launcher currently rejects other operating systems. macOS implementation and
packaging require Mike's future approval.

## What works in this stage

- Actual stereo signal preview through Qt Quick's Direct3D 11 scene graph.
- GPU-drawn grid, scanlines and three triangle-stroke trace/glow layers; four
  samples per pixel requested for antialiasing. No Pillow/Tk raster trace is
  uploaded as the viewport. The graphics backend and adapter appear below it.
- The same CPU audio/DSP engine and playback worker as the existing app. Audio
  remains left=X, right=Y, 48 kHz; preview style never changes generated samples.
- Both built-in sources, BPM, listening mode, output/blend, all shape types,
  logarithmic rotation period with a stopped endpoint, and integer XYZ controls.
- Read existing scene files, including multi-shape scenes and their positions,
  sizes, view angles and travel settings. Opening starts neither sound nor MIDI.
  Editing the shape type, period or XYZ controls replaces an imported multi-shape
  scene with a single solid; a notice in the sidebar explains this limitation.
- Initial palette/grid/effect settings are read from the existing preferences.
  Prototype palette/glow adjustments are temporary and do not write preferences.

The full scene editor, drag handles, axes, menus, shortcuts, numeric inputs beside
sliders and recording UI have not yet been ported. Saved MIDI scenes can be
previewed silently and can reconnect their saved device when Play is explicitly
pressed; device selection and recording still belong in the full CPU app.

## Implementation boundaries

Qt draws the triangles and composites the UI on the selected graphics adapter.
Python/NumPy still synthesize audio, project geometry and build/upload the stroke
vertices. This does not move the audio engine to the GPU. The `basic` Qt render
loop keeps Python scene-graph callbacks on the GUI thread for this first stage;
Direct3D still performs rendering. A software adapter can be selected by Windows
on another machine, so check the displayed adapter before assuming acceleration.

The geometry attribute wrapper and nodes are retained for their native lifetimes;
Qt owns their geometry/material resources. Repeated silent points produce no
degenerate triangles. Trace geometry is clipped by the viewport, not clamped in
the audio data. Metrics use bounded buffers and rendering pauses when minimized.

Reference: [Qt Quick scene graph](https://doc.qt.io/qt-6/qtquick-visualcanvas-scenegraph.html).

## Reproduce the checks

```powershell
python -m unittest discover -s tests/unit -t .
python tools/qt_preview.py --seconds 10 --play --glow --report .user/qt-play.json --screenshot .user/qt-play.png
python tools/qt_preview.py --seconds 8 --exercise --report .user/qt-exercise.json
python tools/qt_preview.py --seconds 8 --no-glow --report .user/qt-no-glow.json
python tools/qt_preview.py --scene path/to/example.scene.json
python tools/benchmark_viewport.py --frames 40 --output .user/cpu-benchmark.json
```

Timed runs exit automatically. `--exercise` changes shapes, integer X rotation,
palette, glow and window size every half second to exercise resource reuse.
Reports separate CPU sample preparation, geometry construction/upload and observed
frame intervals. Upload time does **not** include GPU completion; frame intervals
include timer scheduling/vsync. They cannot establish an end-to-end speedup ratio
against Tk's full paint benchmark. Results are recorded in PERFORMANCE.md.

## Frozen rollback package

Before Qt work, CPU version 0.3.1 was archived from commit `c213455`, tag
`oscilloscope-v0.3.1-cpu-stable`, as:

`../releases/OscilloscopeMusic-CPU-0.3.1-Windows-x64.zip`

Extract the complete ZIP to a writable folder and run `Launch.cmd`. It bundles a
private Python 3.12.4 x64 runtime, Tcl/Tk, pinned dependency packages, font licenses,
source and the two older source snapshots. It needs no Python installation or
network access. Hardware audio/MIDI drivers may still be needed on another PC.
Generated audio, personal preferences/recordings and all Qt code are excluded.
A file-hash manifest is inside; an adjacent `.zip.sha256` verifies the archive.
All 45 original tests passed using the bundled runtime, and imports/Tk startup
were verified again after extracting to a different folder.

The Qt editor has since replaced the prototype as the main app.
