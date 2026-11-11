# phosphor macOS development — 0.17.0-dev

The Qt editor uses native Metal rendering on macOS and retains Direct3D11 on
Windows. A standalone, ad-hoc signed app can be built; see Standalone app bundle below.

## Requirements and setup

- Apple Silicon, macOS 15+ (PySide6 6.10 is built for 15; the app bundle declares it).
  Native system-audio monitoring needs Core Audio taps (14.2+), so 15 covers it.
- Python 3.12 and the pinned PySide6 6.10.3. Do not use Python 3.15 with this pin.
- Xcode Command Line Tools to compile the small native capture helper.
- A writable checkout: development preferences and build products are source-local.

From the repository root:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements/requirements-macos.lock.txt
./LaunchMac.command
```

Alternatively:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements/requirements-macos.lock.txt
./LaunchMac.command
```

`requirements/requirements-macos.lock.txt` records the versions used on the Mac development
machine. Build a fresh environment after moving the checkout or switching
architecture; do not copy Windows or Rosetta dependency directories into it.
The development run used native arm64 Python 3.12.16 on macOS 26.6.2 with Xcode
27.1. The system Python and global packages need not be modified.

The label beneath the viewport should report Metal and the actual graphics
adapter. Qt uses the native macOS menu bar and Command+N/O/S/Shift+S scene shortcuts.
Audio is stopped on startup. The retained CPU/Tk editor is not the supported Mac
launch path and has no text-authoring controls.

## Controls and vector text

Drag the divider to adjust the left panel width. INPUT has Source and Live MIDI
sections; SCENE has Objects and Transform sections. Layout tests cover 1000×700,
1220×840 and 1600×1000, plus panel widths of 340–520 logical pixels. Labels wrap;
vertical scrolling remains available for exceptional content rather than hiding it.

Use **SCENE → Objects → Add text**, type a short word, and press Enter. Text supports
up to 64 characters: A–Z, digits, spaces and standard punctuation. Lowercase renders
as capitals. Transform controls position, size and rotation; new text starts
stationary. Text saves with scenes and uses the actual synthesis/MIDI/export path.
Travel between letters/strokes stays visible because stereo X–Y has no blanking.
Long text and high notes reduce the available samples per stroke.

## Native system-audio visualization

Choose **Monitor input → System audio (visualization)**, keep **All system audio
(Core Audio)**, and press **START VISUALIZATION**. Play music in another application.

macOS must grant System Audio Recording permission. Approve the system prompt,
then stop/restart capture after granting access. If denied, allow **phosphor Audio Capture**, or the launching app named by macOS, under **System Settings
→ Privacy & Security → Screen & System Audio Recording**. Microphone permission
is not required. Permission approval must be performed by the user.

The native Objective-C helper lives in `native/macos_capture/`. Build it explicitly with:

```sh
.venv/bin/python -m phosphor.audio.build_macos_capture
```

It is also built on demand when capture starts. The local helper is ad-hoc signed
and includes `NSAudioCaptureUsageDescription`; it is not Developer ID signed or
notarized. Compiled products stay ignored under `.native/`.

`SystemAudio` routes Mac capture through `phosphor/audio/macos_system_audio.py`. The helper creates
a private, unmuted process tap on the default output device's first stream, without
mixdown, so it captures at the device rate (96 kHz included). If that stream is not
stereo it falls back to a stereo global tap, which Core Audio mixes at 48 kHz. A
private tap-only aggregate input is set to the tap's rate, and the helper reports
the aggregate's actual rate. `OscilloscopeAudioCapture --describe` prints the tap
kind and rates and counts delivered frames for 2 s without writing audio out. It excludes its own and the parent process's Core Audio objects when
available. It does not open a microphone or hardware audio output, change default
devices, capture screen images, install drivers, write captured audio files or
send audio over a network. Normal playback in other apps continues.

A bounded lock-free ring separates the hardware callback from pipe writes. Framed
float32 stereo samples pass through an anonymous pipe to a bounded Python history.
The tap's sample rate and sample values are preserved. Display gain, interpolation
and CRT effects are preview-only. Recording/export and generated audio are blocked
while monitoring. Protected content or app/driver restrictions may yield silence.

Reference: [Apple's Core Audio tap documentation](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps).

## Playback clock and performance

Native diagnosis found that Metal kept presenting around 60 FPS while CoreAudio
accepted generated-audio writes in bursts with ~80 ms waits. Reading only the
latest submitted block made the trace advance roughly 15 times per second.

`phosphor/audio/playback_history.py` now selects a 40 ms window from a larger bounded submitted-
audio history using a monotonic clock and reported output latency. Trace and
editing overlays share that cursor. No synthetic samples or display interpolation
are introduced. The cursor does not read beyond submitted audio; startup uses
silence, restart clears history, and underruns re-anchor the timing estimate.
This is not calibrated acoustic/DAC synchronization. The legacy Tk reader is
unchanged. See [PERFORMANCE.md](PERFORMANCE.md) for measured conditions.

## Tests and native checks

Run from the app folder:

```sh
.venv/bin/python tools/check_core.py
.venv/bin/python tests/smoke/smoke_qt_editor.py --headless
.venv/bin/python tests/smoke/smoke_responsive_text.py --headless
.venv/bin/python tests/smoke/smoke_view_modes.py --headless
```

The current build passes **75 core tests** and offscreen Qt editor/layout/text
integration checks. The native helper self-test verifies planar/interleaved
stereo conversion and packet framing without requesting capture permission.
Tests cover packet splitting, sample/rate preservation, invalid input, cancellation,
transport cleanup, Windows backend fakes, text routes, scenes and travel strategies.

Offscreen rendering uses Software; it does not verify Metal drawing. Core checks
explicitly exclude legacy Tk desktop/viewport classes. Full discovery also runs
those classes and requires a working Tk desktop. Native GUI/Core Audio access may
be unavailable in a restricted shell sandbox even when the desktop app works.

For native UI checks, run the smoke scripts without `--headless`. For diagnostics:

```sh
# Automatically compare stopped → playing → stopped; produces audible output.
.venv/bin/python app.py --diagnose-playback --report .user/playback-comparison.json
# Input-only capture; play external music and approve macOS access first.
.venv/bin/python app.py --system-audio --play --seconds 20 --report .user/mac-system-audio.json
# Geometry, placement and appearance exercise.
.venv/bin/python app.py --seconds 8 --exercise --report .user/mac-render.json --screenshot .user/mac-render.png
```

Native verification completed on Apple M5/Metal:

- Playback: 179/179 GUI ticks received fresh sample windows at 60.02 presentation
  FPS, with zero audio underruns or errors. One cube; glow/scanlines disabled.
- System audio: 912,384 stereo frames at 48 kHz, non-silent peak 0.229, zero capture
  errors/dropped-block events, and zero app-generated playback frames. The user
  confirmed visible system-audio visualization.

These were short runs. The user reported that the fullscreen/time/dwell build works on their Mac.
Quantitative physical-scope calibration remains hardware-specific. Physical MIDI input/recording, prolonged capture, device/route changes and
wider hardware coverage remain separate checks. The Windows native UI has not
been reverified from this Mac; retain its established path and regression tests.

## Standalone app bundle — 0.30.0-dev

`tools/build_macos_app.py` packages the Qt editor as `phosphor.app` with PyInstaller:

```sh
.venv/bin/python -m pip install -r requirements/requirements-macos-build.txt
.venv/bin/python tools/build_macos_app.py          # .mac-build/dist/phosphor.app
.venv/bin/python tools/build_macos_app.py --dmg    # also phosphor-<version>.dmg
```

- The bundle carries Python, Qt, numpy, PortAudio, rtmidi, the assets and the
  prebuilt capture helper (Contents/Helpers). Nothing is compiled at run time.
- Unused Qt frameworks are pruned after PyInstaller, since the PySide6 hook copies
  every QML plugin (QtWebEngine alone is 214 MB). The app is about 180 MB and the
  DMG about 76 MB. The build fails if a kept binary still links a pruned framework.
  The QML in qt_phosphor.py imports only QtQuick; review UNUSED_FRAMEWORKS if that changes.
- A packaged app writes preferences to ~/Library/Application Support/phosphor,
  not `.user` (app_paths.py). Source runs still use `.user`, so the two do not share
  settings. PHOSPHOR_USER_DIR overrides both for testing.
- Cmd+N relaunches the bundle's own executable (app_paths.relaunch_command).
- The Tk `--cpu` fallback is not included.
- The signature is ad hoc. It runs on the Mac that built it. Other Macs will block
  it until it is signed with a Developer ID and notarized, or until the user allows
  it under System Settings > Privacy & Security. System-audio permission is granted
  to the app bundle, separately from Terminal.
- Validated on Apple M5, macOS 26.6: Metal / Apple M5, playback with 0 underruns,
  exercise screenshot, readiness-file relaunch. Live MIDI with a device, and system
  capture from the bundle, are not yet checked.

## Fullscreen, time plots and hardware corner dwell — 0.11.0-dev

View → Fullscreen keeps the controls. Trace-only fullscreen hides UI/overlays and
cursor while reusing the native Quick window; Escape restores the prior view.
Test native Mac fullscreen transitions, another display and Quick-window focus.
The X(t), Y(t) tab renders raw channels from the shared current sample window,
including the capture rate. Freeze affects only that diagnostic plot. Gain and
synthetic display interpolation are excluded.

Signal-output calibration is intentionally separate from Appearance and scenes.
`phosphor/storage/signal_preferences.py` stores `.user/signal-preferences.json`; `output_settings()`
applies the profile to generated Qt playback/export, without mutating Scene.
MIDI performance JSON records the actual calibrated settings. Source WAV and
original/captured audio remain unmodified. The retained CPU/Tk UI does not load
this profile; direct engine callers may set Settings.corner_dwell_us explicitly.

`phosphor/engine/corner_dwell.py` detects projected turns >=30° and reserves equal phase holds,
with a 25% total source-cycle cap. Edge travel uses the remaining phase. Source
frequencies are supplied analytically by the built-in source or MIDI voice;
prepared offline input derives frequency from the complete phase array rather
than audio chunk boundaries. Dwell 0 leaves existing sample values unchanged.
Repeated source-phase boundaries/note timing stay fixed; timbre/perceived pitch
can still change under waveshaping. Actual sample resolution is 20.83 µs at 48 kHz.
Envelope/rotation changes and polyphonic mixing can move a nominal held vertex.

Validation: 75 core tests; existing Qt/editor/layout checks; new fullscreen/Escape,
profile Apply/Cancel/export, source isolation and raw/frozen/native-rate time-plot
checks. The new views were visually inspected offscreen, and the user subsequently
reported the build works on the Mac. No quantitative Tektronix corner-improvement
measurement has been recorded; dwell should be tuned on the physical scope.

## Windows portable build

`tools/build_windows_app.py` is the Windows counterpart, also with PyInstaller:

```
python -m pip install --target .win-build/tools -r requirements/requirements-windows-build.txt
python tools/build_windows_app.py   # .win-build/dist/phosphor/phosphor.exe and phosphor-<version>-windows-x64.zip
```

It takes dependencies from `.qt-deps`/`.audio-deps`. The packaged app keeps
preferences in `%APPDATA%\phosphor`. The executable is unsigned, so SmartScreen
asks on first launch ("More info" → "Run anyway").
