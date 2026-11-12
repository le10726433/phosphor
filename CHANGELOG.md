# Application changes

## 0.36.1 — Function plots, video input, builder text and route minimizing (November 11, 2026)

- **Function plots:** Input › Monitor input › Function x(t), y(t) plays and draws two
  expressions in Python maths syntax: t in seconds, s as the 0–1 cycle position at a
  set frequency, and up to 8 variables with sliders. A maths-only whitelist means
  scene and preset files cannot run code. Built-in presets, plus your own saved in
  function-presets.json. This replaces the 40 ms X(t), Y(t) time traces.
- **Video input:** Input › Monitor input › Video file (phosphor CRT) plays a video
  with sound, drawn as a 60 Hz monochrome scanline raster through the phosphor
  display, so the chosen phosphor's decay, beam profile and glow apply. Decoding uses
  Qt Multimedia's FFmpeg backend, with seek, volume, loop and raster lines.
- **Shape builder:** a Text tool places typed text as ordinary strokes. Per selected
  shape, Bold width traces the outline of the strokes fattened to that width, and
  Extrude depth adds front and back copies along Z joined at the corners.
- **Route minimizing:** Preferences › Signal output › Route minimizing plans each
  shape's beam route: Off (the original routes, sample-exact), Shortest jumps, or
  Shortest total path (the default). A plan is used only if it is shorter. **Existing
  scenes sound different unless it is set to Off.**
- **Appearance:** Phosphor grain and Beam shimmer sliders (default 0, unchanged look).
- **Layout:** the INPUT/SCENE/MIX controls sit to the right of the viewport. New
  scenes start in Fully shaped audio.
- **Files:** scene files are now version 10 and store the function plot and builder
  styles; version 8 files from 0.32.6 still open. Shape files are version 5.
- **Fixes:** an over-long number in a function no longer stops the app from
  starting, and functions reject extra arguments.

## 0.32.6 — Bloom, decay editor, device-rate audio and X(t)/Y(t) sweep (November 10, 2026)

- **Phosphor:** glow is now bloom of the energy buffer, continuous around sharp corners.
  Preferences shows a decay preview, editable by dragging or typing; editing a preset
  saves it as a Custom profile. Phosphor intensity reaches 64×, and a Phosphor
  brightness slider sits under the scope. Grid lines can be turned off.
- **Audio:** playback, Live MIDI and macOS system capture run at the output device's
  rate. WAV exports stay at 48 kHz. Live MIDI blocks scale to about 5 ms above 48 kHz,
  and harmonics are summed with Horner's rule, so held chords keep up with real time.
- **System audio:** an X(t), Y(t) sweep on the phosphor display, with X, Y or Both
  triggers, rising or falling edge, trace separation and a 0.1–2000 ms scan window.
- **Rotation:** seconds per revolution is removed; X/Y/Z spin is the only rotation and
  new objects start still. Spin, input gain and intensity use finer-near-zero sliders.
- **Breaking:** scene files are version 8 only. Scenes saved by 0.31.0 are rejected.
- **Fixes:** a resize crash on Direct3D 11; a crash on quit after using the shape
  builder; fullscreen toggles rewriting the preferences file.
- **Layout:** the app is now the `phosphor` package, with `tests/`, `tools/`, `docs/`,
  `native/` and `requirements/` folders. `app.py` is the only entry point.

## 0.31.0 — first public release (November 9, 2026)

- phosphor is now free software under the GNU GPL v3 or later, published with a
  fresh history. NOTICE.md lists the third-party software in the packaged apps.
- No music is bundled. The Howl's Moving Castle transcription and the A Cruel Angel's
  Thesis MIDI file are removed. A new **MIDI file** source plays the top melodic line
  of any `.mid` file the user opens, starting at the file's own tempo. Scenes store
  the file's path (scene version 7) and find a moved file beside the scene. Scenes
  that used a removed song open with the C-major scale.
- GitHub Actions tests and builds the macOS DMG and Windows zip on every push, and
  attaches both to a draft release for each `v*` tag. Release assets are named
  `phosphor-<version>-macos-arm64.dmg` and `phosphor-<version>-windows-x64.zip`.
- The macOS app now declares macOS 15 as its minimum, matching the bundled PySide6
  bindings; before, it would offer to launch on macOS 13 and 14 and then fail.
- Both packages include README, LICENSE and NOTICE.
- The repository is reorganized for publication: the app is at the top level, the
  app guide is GUIDE.md, and the private history snapshots and agent notes are omitted.

## 0.30.1-dev — Windows portable build (November 9, 2026)

- build_windows_app.py packages the Qt editor with PyInstaller as a portable folder,
  `.win-build/dist/phosphor/phosphor.exe`, and a zip of it to distribute. It uses the
  existing `.qt-deps`/`.audio-deps`, adds a multi-size icon, and prunes unused Qt
  modules (and anything linking them) down to 206 MB, 76 MB zipped.
- A packaged Windows app stores preferences in %APPDATA%\phosphor (app_paths.py from
  0.30.0-dev); source runs keep `.user`, so a source checkout's saved preferences do
  not carry over to the packaged app.
- The executable is unsigned, so SmartScreen asks on first launch.
- Validated from the packaged folder on Windows 11 / RTX 4070: Direct3D 11 rendering
  with the phosphor display and Gaussian beam, bundled fonts and logo, readiness-file
  launch in 3.5 s, playback with 0 underruns, and a clean close. Live MIDI from the
  package is untested (the rtmidi backend and PortAudio DLLs are bundled).

## 0.30.0-dev — macOS app bundle (November 9, 2026)

- build_macos_app.py packages the Qt editor as an ad-hoc signed phosphor.app, with
  an optional DMG. It bundles the system-audio capture helper prebuilt, adds the
  app icon, and prunes unused Qt frameworks (435 MB to 180 MB).
- A packaged app stores preferences in ~/Library/Application Support/phosphor;
  source runs keep `.user`. app_paths.py holds both and the Cmd+N relaunch command.
- Windows is unaffected: source runs use the same `.user` paths and launch command.
- Validated on Apple M5 / macOS 26.6 from the bundle: Metal rendering, playback with
  0 underruns, Cmd+N's readiness-file launch. 189 unit tests and the offscreen editor
  smoke test pass from source. Live MIDI and system capture from the bundle are untested.

## 0.29.1-dev — Gaussian energy at every beam speed (November 9, 2026)

- 0.28.3-dev matched Gaussian to Hard edge only where the beam moves less than the
  1 px core per sample. Faster beams, and any speed at Beam-speed brightness 0, lost
  energy: on Metal, a fast beam at brightness 1 measured 0.28-0.31x Hard edge, and
  brightness 0 measured 0.18-0.37x. The core's level now follows Hard edge's formula
  for the full thickness, scaled by thickness over core width, so energy matches at
  any speed and brightness (within 2% in unit tests).
- Where that level would pass 8 bits, at low beam-speed brightness on thick traces,
  the core widens instead of clipping, and the blur narrows so the width is unchanged.
- Validation on Metal / Apple M5 at 2x, thickness 3 and 6, slow and fast beams at
  brightness 0 and 1: Gaussian/Hard energy 0.83-1.24x (screen readings at minimum
  intensity are coarse); before, 0.18-0.37x. Width at half maximum unchanged.
- Validation on Direct3D 11 / Windows (RTX 4070, 1x, 4x multisampling): 199 unit
  tests, check_core.py and all ten native smokes passed. Accumulated energy read
  linearly through a diagnostic display shader, at two exposures, for thickness 3
  and 6, brightness 0 and 1, and beams of 0.58 and 6.9 px per sample: Gaussian/Hard
  energy 0.95-1.00x, peak 0.83-0.91x, width at half maximum 3.0-3.25 px (Hard
  2.75-3.0) at thickness 3 and 5.75-6.0 px (Hard 5.5-5.75) at thickness 6; nothing
  clipped. The 0.95x case is thickness 3 with a fast beam at brightness 1, where
  8-bit levels are low. Display scaling above 1x was not tested on Windows.

## 0.29.0-dev — A Cruel Angel's Thesis source (November 9, 2026)

- Add A Cruel Angel's Thesis as a built-in source, from a user-supplied piano MIDI
  file bundled in assets/scores. The app plays its top melodic line, because one
  phase drives the beam and chords would distort the figure. The file's tempo
  changes are kept; Tempo 126 gives the original speed (4:02.8).
- Built-in scores are now a list in score_source.py: Howl's Moving Castle is
  unchanged, and further MIDI scores need only a file and a MIDI_SCORES entry.
- Validated on Windows: the melody opens C5 Eb5 F5 Eb5 F5 F5 F5 Bb5 Ab5; 770 events
  including 21 rests. Playback rendering is chunk-consistent, source and processed
  WAV export and scene round trip work, and the editor's Source list selects it.
  The first selection reads the file on the GUI thread, about 155 ms. Not yet run
  on macOS.

## 0.28.4-dev — Phosphor intensity up to 20 (November 9, 2026)

- Phosphor intensity now ranges from 0.1 to 20, as the user requested, leaving room
  to brighten the corrected Gaussian beam. The default stays 5; saved values are kept.

## 0.28.3-dev — Gaussian beam energy and Windows validation (November 9, 2026)

- Fix the Gaussian beam carrying about 3.2 times Hard edge's energy. The thin core
  already deposits the full energy and the blur is normalized, so the extra gain
  added in 0.23.0-dev counted it twice. It also brightened glow by the same factor.
  Gaussian now keeps Hard edge's total energy, with a peak about 0.9 of Hard edge's.
  Because Gaussian is the default, the default trace is dimmer than in 0.23.0-dev
  to 0.28.2-dev; raise Phosphor intensity to compensate.
- Measured on Direct3D 11 at thickness 3 by reading accumulated energy linearly
  through a diagnostic display shader: total energy 3.22x Hard edge before, 1.02x
  after; peak 2.87x before, 0.87x after; width at half maximum 3.0 px both times.
  Glow adds about 10% to the peak and 12-17% to total energy at default settings.
- Windows validation of 0.28.2-dev on Direct3D 11: 192 unit tests and all ten native
  smokes passed under 4x multisampling; Gaussian blur, spots and both shaders work.
  Real launch/quit, quit during playback and Live MIDI (WASAPI exclusive, 0
  underruns, longest callback gap 5.7 ms) passed. A user's real OBJ imported cleanly
  and saved as scene version 6. A user's icon SVG of 50 filled outlines cannot fit
  the 128-node limit (it needs at least 150); a generated SVG within limits imports.

## 0.28.2-dev — No garbage-collection stutter during playback (November 9, 2026)

- Fix short, irregular stutters in live playback that were absent from recordings.
  A full Python garbage collection took about 10 ms and usually ran on the audio
  callback thread, whose budget is 2.67 ms at 128 frames. The app now collects and
  freezes long-lived objects after startup and before audio starts, so later
  collections stay small. Shared code: Windows is affected the same way.
- Validation on Metal / Apple M5 by replaying a user's 155 s MIDI recording in real
  time with their scene and preferences: before, a 9.64 ms full collection inside
  the callback delivered one block 5.76 ms late in 60 s; after, no full collections,
  no late blocks, longest collection under 1 ms.

## 0.28.1-dev — Click-free voice stealing (November 9, 2026)

- Fix clicks during dense Live MIDI playing. When a ninth note arrived, the oldest
  voice was removed instantly, often mid-cycle at full level. Stolen voices now fade
  out over 5 ms and no longer count toward the eight-voice limit, so new notes still
  start at once. The voice count shows only active voices. Shared code: Windows is
  affected the same way.
- Validation on Metal / Apple M5 under app.exec, Original audio, sustain pedal and
  four-note chords: 45 jumps over three times the normal step before, 0 after; the
  largest fell from 0.224 to 0.015. No underruns, and Core Audio kept at least 2.1 ms
  of spare buffer. Callback deadlines were not the cause of the clicks.

## 0.28.0-dev — SVG import and per-axis spin (November 9, 2026)

- Add File > Import SVG…. Paths, lines, polylines, polygons, rectangles, circles
  and ellipses become an editable custom shape, with transforms applied and hidden
  or defined-only content ignored. Fills, colours, text and images are not imported.
- Curves are flattened and simplified to fit 128 nodes and 256 lines. The app reports
  simplification, parts too small to keep, and unsupported elements.
- Add X, Y and Z spin to Transform, from -10 to 10 revolutions per second. Spin adds
  to the view rotation, and Reset view clears it. Scenes using spin are version 6.
- Faster travel now also speeds the links between separate parts of custom shapes,
  so saved multi-part custom shapes in that mode change timing.
- Put Transform's sliders beside their labels so the tab fits 1000x700 without a
  scrollbar. The seconds-per-revolution hint is now a tooltip.
- Validation on Metal / Apple M5 with generated SVG files, including curves, compact
  arcs, nested transforms and spin. Direct3D 11 is not yet tested.

## 0.27.0-dev — 3D model import prototype (November 9, 2026)

- Add File > Import 3D model… and a 3D model object type for Wavefront OBJ files.
  Models are stored in the scene as a see-through wireframe and use the same
  rotation, view angle, size, position and motion controls as the built-in solids.
- Import welds duplicate vertices and keeps open edges and creases of at least 30
  degrees, up to the 256 sharpest edges. Separate parts are joined by connectors
  that follow Faster travel. Scenes containing a model use version 6; other scenes
  keep their existing format.
- Smooth models have few sharp edges, so their result is poor and the app says so.
  Hidden-line removal and materials are not included.
- Validation on Metal / Apple M5: quad, triangulated, multi-part and dense OBJ
  files, transforms and scene round trip. Direct3D 11 is not yet tested.

## 0.26.0-dev — New defaults (November 9, 2026)

- New scenes start on Faster travel at 64x. Saved scenes keep their own travel
  settings, and the engine's built-in default stays No changes.
- Appearance now defaults to the P31 green phosphor and the Gaussian beam.
  Saved preferences keep their values; Defaults in Preferences applies these.

## 0.25.0-dev — Fast connectors between letters (November 9, 2026)

- In Faster travel, the connector moves between letters and between a letter's
  separate strokes now run at Travel speed, like moves between shapes. Letters
  are drawn at their normal speed, and the route and geometry are unchanged, so
  transforms still act on the whole string. Other travel modes keep their timing.
- Validation: at 8x, connectors in HELLO fall from 12.7% to 1.8% of the path
  time; compared on Metal / Apple M5. Direct3D 11 is not yet tested.

## 0.24.0-dev — Stationary beam spots (November 9, 2026)

- Samples that do not move now deposit a round spot, so corner dwell shows as a
  bright point and a stationary beam leaves a dot. Held samples merge and add
  energy; a slowly drifting beam starts a new spot each half width. Glow bands
  and both beam profiles get spots. Preview only: no audio change.
- During playback or preview, a silent signal now shows a dot where the beam
  rests. When nothing new is played, nothing is deposited.
- Each frame's deposit is still capped at full level, so added dwell stops
  brightening after a few samples at typical widths.
- Validation on Metal / Apple M5 at 2x, thickness 3: corner-to-edge ratio rose
  from 1.63 to 3.90 (Hard, 0 to 24 held samples) and 1.19 to 3.00 (Gaussian).
  Dots fade to the clean frame. Direct3D 11 is not yet tested.

## 0.23.0-dev — Gaussian beam profile prototype (November 9, 2026)

- Add Preferences > Appearance > Beam profile: Hard edge (default) or Gaussian,
  for the phosphor display. Line-trace mode stays hard-edged. Preview only: no
  audio, scene or export change.
- Gaussian draws a thin core and blurs each frame's deposited energy on the GPU in
  two passes, packed as 16-bit fixed point so faint tails do not band. Trace
  thickness is the full width at half maximum; sigma is limited to 1-8 physical
  pixels, and a gain keeps peak energy equal to the hard edge. CRT glow bands are
  still deposited, and are blurred too.
- Validation on Metal / Apple M5 at 2x: full width at half maximum measured 6 px
  at thickness 3 and 12 px at thickness 6, fitted sigma 2.69/5.23 (target
  2.55/5.10). With eight silent MIDI voices, frame timing, geometry time and
  underruns were unchanged from Hard edge. Direct3D 11 is not yet tested.

## 0.22.0-dev — Phosphor display on by default (November 8, 2026)

- Phosphor display now defaults to Palette colour, at the user's request after
  native checks on Direct3D 11 and Metal. A saved Off stays Off; preference files
  from before the setting existed adopt the new default. Restore Defaults selects
  Palette colour. If the shaders cannot load, the app keeps the line trace.
- Unit tests and all ten native smokes passed on Direct3D 11 with factory-default
  preferences. This default change was not rerun on Metal.

## 0.21.6-dev — Windows validation of 0.21.5-dev (November 8, 2026)

- No functional changes. Fix 5 test errors on Windows: a test simulating macOS
  was the first to import sounddevice, so PortAudio resolved for the wrong platform.
- Direct3D 11 at 144 Hz with 0.21.5-dev: real startup and normal close exited 0
  with phosphor active; fitted slow decay P7 0.79 s (preset 0.7 s), P1 0.14 s
  (preset 0.12 s, 4 samples). Live MIDI reported 5.7 ms with 0 underruns.

## 0.21.5-dev — Afterglow at any display refresh rate (November 8, 2026)

- Fix phosphor afterglow decaying too fast on displays above 60 Hz. The feedback
  layers ran at the display rate (about 96 fps on a 120 Hz Mac) while decay was
  computed per 16 ms app tick, so extra frames decayed and re-deposited again.
  The buffer now advances exactly once per tick through scheduleUpdate.
- Validation on Metal / Apple M5, 120 Hz: fitted P7 slow decay 0.733 s (preset
  0.7 s), P1 0.124 s (preset 0.12 s). Direct3D 11 is not yet retested.

## 0.21.4-dev — Lower Live MIDI latency on macOS (November 8, 2026)

- Live MIDI on macOS now uses a Core Audio callback stream, keeping 128 frames.
  Reported output latency on MacBook Pro Speakers fell from 61.96 ms (blocking)
  to 19.29 ms; about 18 ms of that is the device itself. The blocking stream
  remains as a fallback. Built-in playback is unchanged.
- Validation on Metal / Apple M5 under app.exec, silent output, eight voices
  retriggered every 100 ms, with phosphor Off, P31 and P7: 0 underruns, 48 kHz
  held, no callback gap over 5.01 ms, synthesis maximum 2.53 ms of a 2.67 ms
  block. Reported latency is not a measured key-to-sound time. Physical MIDI
  latency and recording during callback playback remain untested.

## 0.21.3-dev — Clean exit with the phosphor layer (November 8, 2026)

- Fix a segfault on every native quit, introduced with the phosphor layer. Qt
  destroyed its effect sources and their source items in an unsafe order inside
  ~QQuickWindow. Closing the window now clears those references first.
- Do not delete the layer with shiboken6.delete: that also crashed. Its Python
  wrappers stay alive until the process ends.
- Validation on Metal / Apple M5: repeated native launch/quit, playback and quit
  with a phosphor preset active all exit 0. Direct3D 11 is not yet retested.

## 0.21.2-dev — Phosphor intensity range (November 8, 2026)

- Phosphor intensity now ranges from 0.1 to 10, with a default of 5, as the user
  requested. Saved preferences keep their existing value until changed.

## 0.21.1-dev — Phosphor display in the real window (November 8, 2026)

- Fix the trace disappearing when a Phosphor display preset was selected. The
  app window uses 4x multisampling, where floating-point layers silently render
  empty on Direct3D 11. Layers are now 8-bit, with energy packed by the shaders
  as 16-bit fixed point. Each frame's deposit is capped at 1.0; energy can still
  build above that across frames, up to 64.
- 0.21.0-dev tests skipped qt_app.main, so they ran without multisampling.
  smoke_phosphor.py now matches the real startup format.

## 0.21.0-dev — Phosphor energy buffer (November 8, 2026)

- Add Preferences > Appearance > Phosphor display: a floating-point GPU buffer
  that accumulates each newly played sample, decays with fast and slow time
  constants, and tone-maps to light. Presets: Palette colour, P31, P1 and P7.
  Decay times are approximate visual presets, not measured tube data.
- Off by default until checked on Metal; Off keeps the existing line trace. If
  the shaders cannot load, the app also keeps the line trace.
- Phosphor intensity sets exposure. Preview only: no audio, scene or export change.
- Each sample's energy spreads over its footprint, so brightness follows beam
  speed. Stationary samples, such as corner dwell, do not yet deposit a spot.
  The builder preview keeps the line trace.
- Fix beam-speed brightness not drawing after starting at 0 (Classic).
- Native Direct3D 11 only. New smoke_phosphor.py covers accumulation, decay, Off
  restore and resize. Tick preparation measured median about 2.5 ms, was 1.5 ms.

## 0.20.0-dev — Beam-speed trace brightness (November 8, 2026)

- The core trace now dims with beam speed, as energy per pixel falls on a real
  tube. Preferences > Appearance > Beam-speed brightness defaults to 1; 0 uses
  the previous even trace. Preview only: no audio, scene or export change.
- Display interpolation (renamed from System-audio display interpolation) now
  also applies to generated scenes. It stays Off by default.
- Glow strokes are unchanged: their overlap already builds up where the beam is
  slow. Afterimages (Glow decay above 0) still use even brightness.
- Native Direct3D 11 captures of identical frozen signals confirmed fast travel
  lines fade. Geometry upload measured median 1.15 ms against 0.98 ms Classic at
  1920 points. Contrast on smooth shapes is limited because slow parts cannot
  exceed full brightness; that needs an energy buffer. Metal is not yet tested.

## 0.19.2-dev — Arrow image fallback (November 8, 2026)

- If the number-field arrow images cannot be written to the temp folder, show
  plain bars on working buttons instead of stopping the app from starting.

## 0.19.1-dev — Spin box arrows (November 8, 2026)

- Fix number-field arrows on Windows. The Windows 11 style drew them side by
  side while the text field still covered one, so real clicks missed that arrow.
  QTest clicks sent straight to the spin box could not show this. Arrows are now
  explicit stylesheet controls, stacked on the right, and dim at a range limit.
- Verified with real OS mouse clicks on MIDI gain and Grid density at 100%
  display scaling. smoke_responsive_text.py now checks that the text field does
  not overlap either arrow. The shared stylesheet needs a native Mac look.
- The user confirmed on Windows that 0.19.0-dev Live MIDI feels greatly reduced
  in latency with their controller.

## 0.19.0-dev — Windows Live MIDI low-latency output (November 8, 2026)

- Windows Live MIDI now uses WASAPI exclusive mode through a 128-frame callback
  stream, replacing the system default MME output with 512-frame blocking writes.
  Exclusive mode holds the output device while Live MIDI audio runs. If the device
  refuses, it falls back to shared WASAPI, then to the previous settings. The
  status line shows the mode. Mac, built-in playback and exports are unchanged.
- Measured on the development PC (AudioBox USB 96, RTX 4070) in the real editor
  event loop, eight held voices, with device output forced silent: stream-reported
  latency 5.7 ms, was 32.0 ms as reported by MME (whose listed low-latency
  figure is 90 ms). Shared WASAPI reports 22 ms. No underruns were reported in
  12 s; longest gap between callbacks was 8.9 ms, above the 5.7 ms buffer, so an
  isolated click cannot be ruled out without a listening test.
- These are driver-reported figures, not controller-to-speaker measurements.
  Audible output and a physical MIDI controller have not been tested.

## 0.18.1-dev — Windows validation of 0.18.0-dev (November 8, 2026)

- No functional changes. Records Windows evidence for commit `8915f28` and
  updates the Shape redraw tooltip, which said Windows validation was pending.
- Windows 11, CPython 3.12.4, PySide6 6.10.3, Direct3D 11 on NVIDIA GeForce
  RTX 4070. Full discovery: 120 tests, 118 passed, 0 failed, 2 skipped (native
  Mac helper and POSIX-pipe transport).
- All nine native Qt smokes exited 0: editor, responsive/text, view modes,
  shape, curve, selection, motion paths, drawing motion and signal output.
  Three `--exercise` launch/close cycles exited 0 without audio.
- Not tested on Windows: audible playback and output underruns, the Ctrl+N
  new-instance handoff, physical MIDI latency and oscilloscope calibration.
  This run is not evidence for Metal or Core Audio; Mac results remain the
  user's November 8 validation of 0.18.0-dev.

## 0.18.0-dev — Shape redraw signal preference (November 8, 2026)

- Shape redraw is **experimental**, disabled by default. The user validated this
  build on Mac on November 8; Windows validation remains pending. Publish it on
  a separate development branch for cross-platform review before merging.
- Add Shape redraw checkbox and total-draws integer (1–32) in Signal output.
  Each drawable shape completes N passes before traveling to the next. Single
  drawable shapes and count 1 retain exact existing samples; guides do not count.
- Repeat routes inside the same source phase cycle. Source note timing remains
  unchanged; fewer samples per trace and changed shaped timbre are possible.
  Apply consistently to generated playback, MIDI shaping and processed exports;
  original/source audio and system capture remain unchanged.
- Keep redraw in machine signal preferences, separate from scenes/appearance.
  Migrate older corner-dwell-only profiles; support Apply/Cancel/Defaults. Limit
  expanded paths to a bounded detail budget. If later scene edits exceed it,
  keep playback at one pass and show a Redraw inactive status until corrected.
- Validation: 109 core tests plus signal-preference and view/editor integration
  checks passed offscreen; user Mac validation confirmed. Windows and quantitative
  physical-scope redraw calibration remain outstanding.

## 0.17.1-dev — Construction X/Y axes (November 8, 2026)

- Add palette-matched horizontal X and vertical Y axes through the construction
  origin, with X/Y labels and a zero marker. They follow pan/zoom, stay behind
  editable geometry and never enter audio or saved shape data.

## 0.17.0-dev — Motion assignment inside the builder (November 8, 2026)

- Motion path is now a per-connected-path role in a drawing. Select a path and
  toggle its checkbox; select follower shapes and use Assign motion in the same
  builder. Remove the scene-level Motion button from the new workflow.
- Guides, shapes and assignments stay in one reusable assembly. Assignment
  preserves rest positions and offset from the guide start. Follow direction
  uses change from initial heading, with optional angle offset; groups stay rigid.
- Retain velocity, closed looping, open bounce/stop and completed-edit audition.
  Construction shows editable rest positions; output previews animation. Turning
  a guide back into an object pauses followers and retains bindings for resuming.
- Preserve node-based identity through edits, Undo/Redo and portable files.
  Clipboard copies containing guides and followers remap IDs independently;
  followers copied without their guide become independent static geometry.
- Compile rest geometry once and apply bounded rigid transforms at pose time.
  Guides are excluded from stereo geometry. Scene version 5 / shape version 4
  store builder motion; previous file formats and legacy scene motion still load.
- Validation: 105 core tests and eight offscreen integration checks passed,
  including no-jump offsets, scaled-parent velocity, group movement, files,
  clipboard, disabled-role resume, audition snapshots and 1000×700 layout.
  Local eight-voice, 128-frame synthesis measured median 1.00 ms / p95 1.25 ms.
  Native builder-motion review and Windows validation remain pending.
- Timeline editing remains explicitly deferred.
## 0.16.1-dev — Shared font initialization (November 8, 2026)

- Load bundled JetBrains Mono before constructing any editor window, including
  native smoke-test windows. Reuse the setup in the normal launcher and avoid
  missing-font alias substitution in validation runs.
- Tighten Scene section spacing and compact its object list so the bundled font
  also fits the 1000×700 text/transform layout without a panel scrollbar.
- Native Mac 0.16.0 Circle/motion and selection/clipboard checks both passed on
  November 8; their only reported font warning was caused by test initialization.

## 0.16.0-dev — Motion guides, Circle and sharp marquee (November 8, 2026)

- Draw the selection rectangle in a separate flat-color QSG layer: dashed,
  0.8 px (half the 1.6 px normal construction stroke), with no glow/blur or fill.
- Add Circle: click center, then circumference; edit its numeric radius or
  defining points. Saved circles remain analytic and use bounded tessellation.
- Add Use as motion path conversion and a scene Motion dialog for guide/group
  assignment, velocity in scene units/second, open-path bounce (default) or stop,
  and optional tangent-following with angle offset. Closed guides always loop.
  Disconnected/branching guides are rejected with an edit prompt.
- Motion runs continuously in the preview and restarts on Play or Apply motion.
  Group arrangements remain rigid. Follow direction defaults off; when enabled,
  open-path reversals turn 180°. Guides are excluded from stereo geometry.
  Reverting a guide to an object pauses its followers and retains assignments.
- Playback and export use the same sample/pose evaluator; offline export starts
  motion at zero. Existing Mac low-latency MIDI buffering remains unchanged.
- New Qt editor instances start with a stationary Tilting circle. Motion/Circle
  scenes use version 4; their reusable shape assets use version 3. Legacy formats
  remain readable and unchanged when these features are absent.
- Validation: 99 core tests and seven offscreen Qt checks passed. Native Mac
  Circle/motion/selection rendering review and Windows checks remain pending.
- Timeline editing remains deferred until explicitly requested again.
## 0.15.0-dev — Connected shape selection and constrained movement (November 8, 2026)

- In Select mode, dragging a stroke moves its entire connected shape without
  changing relative node positions. Dragging a node remains a node edit. Multiple
  selected connected shapes move together as one undoable completed gesture.
- Drag empty canvas to box-select connected shapes touched by the box; Shift
  adds to the selection. Highlight selected strokes and show the selection box.
- Add right-click Copy/Cut/Paste and platform-standard shortcuts. Clipboard
  geometry retains analytic curves and internal joins; pasted copies get new
  node IDs. Keyboard pastes are offset; context-menu paste centers at the click.
- Shift constrains node/group drags and point placement to the dominant axis.
  Snapping cannot pull a constrained point off-axis. Existing Option/Alt snap
  bypass and completed-edit Live audition remain available.
- Validation: 93 core tests and six offscreen integration checks passed. Native
  Mac selection/clipboard review and Windows checks remain pending.
- Timeline remains explicitly deferred.
## 0.14.0-dev — Curves, splines and semicircles (November 8, 2026)

- Add three-point arcs (start, through point, end), two-endpoint semicircles with
  Flip side, and interpolating centripetal Catmull–Rom splines. Drag or numerically
  edit defining points; curves retain editable definitions in files.
- Show curved previews during construction, select curves by their actual path,
  and support snap/join, detach, delete, Undo/Redo and completed-edit Live audition.
  Spline point deletion removes a knot when a valid spline remains.
- Derive fixed-error, bounded tessellation independent of zoom. Preserve exact
  knots and shared nodes, deterministic routing and source phase timing. Smooth
  curve tessellation does not become artificial corner-dwell vertices.
- Curve shape files use version 2; curve scenes version 3. Existing line-only
  shape/scene and built-in/text formats remain unchanged and continue to load.
- Validation: 90 core tests and five offscreen integration checks passed. Native
  Mac curve rendering/input and Windows checks remain pending. Local eight-voice
  128-frame spline synthesis measured median 0.95 ms / p95 1.04 ms; this is not
  a hardware latency or underrun measurement.
- Timeline editing is deferred until the user explicitly requests it again.

## 0.13.0-dev — Shape builder, first review milestone (November 8, 2026)

- Add an in-window 2D line/polyline constructor with Back to scene, Select/drag,
  endpoint snap-and-join (Option/Alt bypass), numeric node edits, detach/delete,
  Undo/Redo, zoom/pan and Fit. A stroke or drag is one undoable edit.
- Live audition sends completed immutable edits through the existing audio/MIDI
  engine. Unfinished gestures stay local. An empty drawing leaves the last
  playable scene unchanged, with an explicit notice. The output preview shows
  the actual scene signal, including other existing objects.
- Save/load reusable .shape.json drawings. Custom scenes use format version 2;
  scenes containing only built-ins/text retain version 1 and legacy sample data.
  Duplicate instances edit independently in this milestone.
- Bounded line graphs (128 nodes/256 lines) compile to cached deterministic
  routes. Closed Eulerian components trace once; other paths retrace edges.
  Disconnected strokes have visible travel, shown dashed in construction.
- Timeline, curves, groups and more complex tools await the next usability gate.
- Validation: 84 core tests and all four offscreen Qt smoke checks passed.
  Native Mac constructor rendering/input and Windows validation remain pending.

## 0.12.2-dev — Mac Live MIDI buffering (November 8, 2026)

- Use 128-frame blocks and the device's low-latency output setting for Mac Live
  MIDI, replacing 512 frames and a requested 30 ms latency. Preserve Windows
  and built-in playback profiles; retain a full 40 ms preview history.
- Warm preset coefficient caches before playback. Show MIDI block size and
  device-reported latency in the status line. This is not a measurement of
  controller-to-speaker or controller-to-screen latency.
- Native controller latency and underruns require a desktop/hardware check.
- Validation: 79 core tests passed, including platform-specific stream profiles,
  event processing with 128-frame blocks and full preview retention. Offscreen
  editor integration passed. Native MIDI latency improvement is not yet measured.

## 0.12.1-dev — SOUND LAB header (November 8, 2026)

- Keep the original palette logo and add SOUND LAB beside it as live Courier
  monospaced text, using each palette's trace color. No other names or artwork
  change; the running application icon retains the original logo.

## 0.12.0-dev — phosphor branding (November 8, 2026)

- Rename the application and current documentation to phosphor. Replace the
  header title and palette caption with the supplied palette-specific logo.
- Apply the selected logo to the running app's window/Dock icon; Appearance
  previews, Apply and Cancel use the same palette path. Package artwork in assets.
- Correct Howl's Moving Castle and migrate the old spelling when opening scenes.
  Audio samples and existing repository paths/scene identifiers are preserved.
- Retain the capture helper's bundle identifier and on-disk path for continuity;
  its display name and privacy description now use phosphor.
- Validation: 77 core tests and all three offscreen Qt integration checks passed,
  including palette artwork selection, unchanged audio and legacy scene loading.
  Native Dock icon appearance still requires a desktop review.

## 0.11.3-dev — Mac validation test correction (November 8, 2026)

- Allow native macOS fullscreen animations to finish between synthetic Escape
  presses in the view-mode smoke check; retain all restoration assertions.
- Native Mac editor and responsive/text checks passed. OLED GPU readback passed
  (black background, white trace, round circle). Escape restoration failed with
  the original 30 ms test waits; the native rerun with transition waits passed.
- Correct the editor smoke test to compare exported samples using effective
  output settings, including saved hardware corner dwell. Application audio
  behavior is unchanged; real user calibration remains untouched.
- Mac validation: 76 core tests passed. Offscreen and native desktop editor,
  responsive/text and view-mode checks passed, including OLED GPU readback,
  fullscreen/Escape restoration, signal preferences/export and raw time traces.
  The native checks ran through the desktop launcher because the agent shell
  cannot create a native window (no screens available).

## 0.11.2-dev — OLED white and immersive grid (November 8, 2026)

- Add OLED white in Appearance: pure black background, white trace/controls,
  and a dim neutral gray grid. Preferences persist independently of scenes.
- Extend the native Quick viewport and grid to the full trace-only fullscreen
  area. Grid cells stay square and the trace retains equal X/Y pixel scale;
  normal editing returns to its centered square viewport on Escape.
- Signal samples, playback, exports and capture are unchanged. Windows validation:
  85 tests passed, 2 Mac-specific skips; all three native editor smoke checks
  passed, including OLED GPU readback and circle aspect ratio. Native Mac
  fullscreen review remains pending.

## 0.11.1-dev — Windows integration validation (November 8, 2026)

- Keep the native Core Audio POSIX-pipe transport test on supported platforms;
  explicitly skip it on Windows, where pipe select can abort the interpreter.
  Windows production capture continues to use WASAPI. Portable packet/routing
  tests remain enabled on Windows.
- Native Windows Qt editor, responsive/text, and fullscreen/time-trace/signal
  preference integration checks passed with the shared Direct3D 11 renderer.
  This Windows run does not revalidate native Metal or Core Audio behavior.

## 0.11.0-dev — immersive view, time traces and hardware corner dwell (November 7, 2026)

- Add View → Fullscreen and Trace-only fullscreen. Immersive mode hides the
  sidebar, menus/status, handles/axes and cursor while retaining the shared square
  GPU viewport and appearance. Escape works in the native Quick window and
  restores the prior normal/fullscreen state and panel proportions.
- Add a left-panel X(t), Y(t) tab with stacked, read-only raw-channel plots over
  the current 40 ms window, including native-rate captured audio. Freeze affects
  only these plots. Visualization gain/interpolation are deliberately excluded.
- Add Preferences → Signal output → Hardware corner dwell, default 0, range
  0–500 µs per sharp turn. This is an explicitly audio-affecting hardware profile,
  separate from Appearance and scene JSON. Apply saves locally; Cancel restores
  the last applied profile. Generated preview/playback, MIDI shaping, processed
  exports and recording metadata use the setting; source/original audio and
  system-audio capture do not change.
- Redistribute source phase between edge travel and holds at projected turns of
  at least 30°, rather than every curve tessellation point. Keep source phase
  cycles/note timing and cap total holds at 25% of each cycle, shortening effective
  dwell for high notes/complex routes. Remaining edge travel speeds up. Dwell 0
  retains existing samples. At 48 kHz holds are limited by 20.83 µs sample spacing;
  envelopes/rotation and polyphonic sums can still move a nominal held vertex.
- Dwell may brighten corners and change timbre; faster edges can worsen rounding,
  and this does not undo DAC filtering, AC coupling or channel phase mismatch.
  Quantitative physical-scope calibration remains hardware-specific. The user
  reported that the new build works on their Mac after desktop testing.
- Pass 75 core tests and Qt editor, responsive/text and view-mode integration,
  covering dwell caps/phase periods, curve handling, generated/prepared/MIDI chunk
  consistency, original audio/source WAV identity, profile persistence/isolation,
  fullscreen/Escape restoration, export setting propagation and raw/frozen traces.

## 0.10.0-dev — responsive controls and vector text objects (November 7, 2026)

- Refresh the cross-platform repository README, Mac setup/validation guide and
  root handoff for publication, preserving the beginner Windows setup guide.

- Replace the fixed-width left panel with a resizable divider (340–640 logical
  pixels), wrapping labels, shrinkable dropdowns and compact placement rows.
  Divide INPUT into Source/Live MIDI and SCENE into Objects/Transform so controls
  fit at 1000×700, 1220×840 and 1600×1000 without panel scrollbars. Horizontal
  scrolling is disabled; vertical fallback remains for exceptional content.
- Add SCENE → Objects → Add text and a text entry committed with Enter or focus
  loss. Use original vector stroke lettering for A–Z, numbers and punctuation,
  with lowercase displayed as capitals and a 64-character limit. Blank text is
  permitted. New text objects start stationary and support duplicate, remove,
  selection, placement, size and XYZ rotation like other objects.
- Integrate text geometry into shared synthesis, MIDI, travel planning and WAV
  export. Route disconnected strokes with actual visible travel and retrace edges;
  no fake blanking or display-only lettering. Long strings/high notes have the
  existing samples-per-cycle fidelity limitations. Existing solid samples stay
  unchanged. Preserve text in scene data; old scenes need no migration, and
  non-text scene objects retain their existing serialized fields.
- Pass 70 core tests, Qt editor integration and responsive/text UI checks,
  including all glyph routes, normalization, scene roundtrips, independent
  duplication, chunk consistency, travel strategies and invalid/blank input.
  Inspect control and vector-route previews. Native Mac review remains necessary
  for the revised panel and new text object; no post-change Metal run is claimed.

## 0.9.0-dev — native macOS system-audio monitoring (November 7, 2026)

- Enable visualization-only system-audio monitoring on macOS 14.2+ using a native
  Core Audio stereo process tap. Capture all system output apps in a private,
  unmuted tap/private tap-only aggregate without microphone or screen capture,
  changing default devices, virtual drivers, playback or recording.
- Build a local ad-hoc signed capture helper with NSAudioCaptureUsageDescription.
  Keep generated binaries out of source. Core Audio's callback writes only to a
  bounded lock-free ring; a separate loop sends framed float32 stereo samples
  through a pipe to the existing bounded Python preview history.
- Preserve the native tap rate and sample values. Handle split/invalid packets,
  silence expiration, dropped callback blocks, cancellation and process cleanup.
  Windows retains WASAPI/PyAudioWPatch and injected backend regression tests.
- Expose All system audio (Core Audio) in the UI, with macOS permission guidance;
  gain/interpolation/glow remain display-only, and capture cannot record/export
  audio or start an output stream.
- Pass 66 core tests, native callback/protocol self-tests, macOS platform/packet/
  lifecycle tests and offscreen Qt integration. A sandbox startup returned a Core Audio object
  access error before audio arrived; live desktop capture and privacy approval
  were initially pending.
- Native desktop validation then succeeded: 912,384 stereo frames at 48 kHz,
  nonzero peak (0.229), no capture errors/dropped blocks, and zero app playback
  frames. The user confirmed the system-audio visualization worked.

## 0.8.3-dev — steady playback clock for burst audio writes (November 7, 2026)

- Native Metal diagnosis showed ~60 presentation/UI events per second in all
  phases, but only 46 fresh audio windows over 180 playback UI ticks. CoreAudio
  writes returned in bursts, with ~80.5 ms p95 blocking waits. The renderer was
  repeatedly presenting the same latest block, not missing GPU frames.
- Add a bounded history of submitted audio indexed by sample frame. The Qt
  editor/benchmark now select a 40 ms window using a steady monotonic clock and
  the device's reported output latency. Playback overlays use that same cursor.
  Silence pads startup; the cursor never reads beyond submitted samples. Restart
  clears history and reported underruns re-anchor the presentation estimate.
- Keep synthesis, clipping, actual output and performance recording samples
  unchanged. Display timing uses an estimated device latency, not calibrated
  acoustic synchronization. The retained CPU/Tk path keeps its previous behavior.
- Pass 60 core tests, including burst delivery, verbatim sample selection,
  startup/bounds, retained-history bounds and underrun/restart handling.
- Make the Qt smoke export wait yield the Python GIL explicitly: qWait alone
  starved its export worker with effects off. Keep the same 10-second timeout
  and sample-accurate export checks; offscreen integration passes with yielding.
- A 13-second offscreen comparison with simulated ~80 ms device bursts retained
  ~62 FPS in both versions; fresh playback windows improved from 40/188 to
  188/188. This reproduces the diagnosed publication failure but is not a native
  post-fix Metal/CoreAudio measurement.
- Native post-fix rerun on Apple M5/Metal confirms 179/179 fresh playback windows,
  60.02 presentation FPS, zero audio underruns and no errors. CoreAudio write
  waits remain ~80 ms p95, with smooth sample-clock progression between them.

## 0.8.2-dev — native playback diagnosis (November 7, 2026)

- The 0.8.1 block cadence change did not resolve the user's native slowdown.
  An offscreen stopped/play comparison with paced simulated output kept ~16 ms
  UI intervals in both modes, so it does not reproduce the native problem.
- Add `--diagnose-playback`: a 13-second automated native stopped/playing/stopped
  comparison with three measurement phases and warmup between transitions.
  Record actual presentation/UI event rates, processing/upload timing, audio
  synthesis/write waits, underruns, pixel ratio, refresh rate and preferences.
  Controls are disabled for the diagnostic; no preferences or scene files are
  written. Diagnostic startup errors are captured without a blocking dialog.
- Retain sample generation and viewport effects. This version adds evidence
  collection; it does not claim a fix for the native playback slowdown.

## 0.8.1-dev — playback viewport pacing (November 7, 2026)

- Use 512-frame generated-audio blocks rather than 1024. At 48 kHz, submitted
  sample windows now refresh at 93.75 Hz instead of 46.875 Hz, so a 60 Hz viewport
  no longer repeats windows solely because of audio block cadence. Live MIDI
  retains its existing 512-frame blocks. The 40 ms trace and sample engine stay
  unchanged; worker tests verify contiguous output against the full render.
- Add actual frame interval/FPS and fresh playback-window counts to timed reports
  to distinguish GPU presentation slowdown from audio-window pacing.
- Simulated real-time output, polled 150 times at 60 Hz, improved from 118 distinct
  windows (32 repeats) to 150 distinct windows (zero repeats). This measures
  publication pacing, not native Metal FPS or CoreAudio behavior.
- Pass 55 core tests and offscreen Qt integration. Native playback smoothness
  remains to be confirmed after restarting the already-running app.

## 0.8.0-dev — initial macOS development port (November 7, 2026)

- Select native Metal on macOS and retain Direct3D11 on Windows using a shared
  graphics configuration for the editor and small viewport benchmark.
- Remove Windows-only launch restrictions, use native Command scene shortcuts,
  and provide a Finder-compatible `LaunchMac.command`.
- Disable unsupported macOS system-audio capture in the UI and reject its CLI
  option with a clear explanation; retain Windows WASAPI behavior.
- Record macOS approval in AGENTS.md, document the isolated Python 3.12 setup,
  and preserve exact installed dependency versions. Include `.command` launchers
  in source checkpoints.
- Pass 54 core/platform tests and offscreen Qt integration checks. Native Metal,
  hardware playback and MIDI remain desktop validation tasks; the sandbox has
  no display access. The retained Tk desktop suite is not Mac-verified.

## 0.7.1 — audio troubleshooting help (November 7, 2026)

- Add a third Audio troubleshooting tab in the Qt Help & About window, with a
  direct About-menu entry. Explain Windows endpoint effects, the seven-output
  comparison and Realtek effects-off confirmation, and practical device/trace
  troubleshooting. Include official Microsoft references and distinguish signal
  fidelity from frame rate. No rendering, audio or system settings are changed.

## 0.7.0 — adjustable trace and phosphor glow (November 7, 2026)

- Add persistent Preferences sliders for trace thickness, glow size, intensity
  and decay, with numeric entry, live preview and double-click defaults. Preserve
  the prior appearance when migrating old preferences: 1 px trace, 3 px glow
  radius, 1× intensity, zero decay. Apply/Cancel/Defaults cover all four values.
- Implement time-based fading afterimages with at most eight cached GPU snapshots.
  Upload history geometry only on emission; fade cached materials per frame.
  Keep afterimages behind the live trace and editing overlays, expire them during
  silence, and clear them when glow is disabled or viewport/style/source changes.
- Skip redundant full-interface style rebuilds when adjusting non-palette
  preferences. Preserve Qt-specific numeric preferences in the CPU fallback.
- Verify 62 regression tests, native numeric/reset/persistence checks, and actual
  GPU afterimages remaining after silence then expiring. Audio engines untouched.

## 0.6.2 — optional display interpolation (November 7, 2026)

- Add Preferences → Appearance → System-audio display interpolation: Off by
  default, Cubic 4x or Cubic 8x. Preview immediately, persist on Apply/OK, restore
  the last applied value on Cancel, and migrate older preference files to Off.
- Insert Catmull–Rom cubic display points while retaining every original sample
  exactly, preserving capture buffers and native sample rate. Interpolate only
  system-audio visualization, before display gain; leave generated audio/scenes
  and playback untouched. Keep the preference outside scene files.
- Verify 59 tests covering sample preservation, sine reconstruction, migration,
  persistence and invalid values; native Qt checks exercise Apply/Cancel, both
  interpolation levels through the GPU viewport, gain/reset and unchanged capture.

## 0.6.1 — system-audio visualization gain (November 7, 2026)

- Add a 0–32× visualization input-gain slider with numeric entry, default 1×,
  and double-click reset. Scale only the displayed system-audio trace, using a
  separate array; preserve captured samples, raw peak metering and playback.
- Keep this control temporary and independent of scene files/generated audio.
  Verify numeric gain, zero/attenuation/amplification, reset, unchanged capture
  buffers/scene and absence of output through the native editor smoke check.

## 0.6.0 — visualization-only Windows system audio (November 7, 2026)

- Add a separate Monitor input selector for Generated scene / MIDI or System
  audio. Enumerate WASAPI playback loopback devices off the GUI thread and offer
  the current Windows default or a named endpoint, with explicit start/stop.
- Display native-rate float L/R directly in the GPU viewport, bypassing synthesis,
  shaping, gain and blending. Mono duplicates X/Y; surround uses the first pair.
  Never open an output stream, microphone, recording file or export for this mode.
- Use a bounded capture buffer, expire stale samples during silence, report
  endpoint/rate/channels/input peak/status, and release capture on scene open,
  monitor change, new-instance handoff or exit. Device changes require restart.
- Disable/hide generation controls and shape/mix tabs, remove editing overlays,
  and block record/export actions while visualizing system audio. Restore the
  original scene when returning; keep monitor choices out of saved scene data.
- Add Windows-only PyAudioWPatch 0.2.12.9. Preserve the pre-capture version as
  `oscilloscope-v0.5.0-before-loopback`; keep DSP and old CPU releases unchanged.
- Verify 55 tests, native UI bypass/restoration, and actual 48 kHz stereo AudioBox
  USB 96 loopback capture: 188,416 frames, zero capture status events/errors,
  zero frames from the app's playback engine. No macOS implementation.

## 0.5.0 — full Windows Qt GPU editor (November 7, 2026)

- Promote the reviewed Direct3D 11 viewport into the main app, using Qt controls
  and INPUT / SCENE / MIX tabs. Keep `app.py --cpu` as the Tk fallback and
  `qt_preview.py` as the small benchmark. Tag the reviewed prototype separately.
- Restore multi-shape add/duplicate/remove/select, per-shape geometry, unrestricted
  move/size dragging, reachable edge handles, integer XYZ controls and preview-only
  RGB axes. Draw all viewport overlays using the shared GPU geometry item.
- Port logarithmic rotation/output sliders with optional numeric input and
  double-click defaults, MIDI device/synth/channel/gain controls, polyphonic
  playback/recording, travel strategies, source and processed WAV exports.
- Port scene save/open/dirty prompts, shortcuts and verified Ctrl+N replacement
  handoff. Keep edits disabled during handoff and preserve the current scene if
  replacement startup fails. CPU fallback Ctrl+N stays in the CPU edition.
- Port persistent three-palette preferences with Apply/Cancel/defaults, grid and
  effects, plus About/instructions. Keep preferences separate from scene files.
- Render WAV exports on a worker and atomically replace the destination. Keep
  source/synth/playback/recording DSP unchanged; separate shared numeric helpers
  from Tk so the Qt entry point does not import Tkinter.
- Verify 51 regression tests, native widget/mouse/preferences/file checks, real
  replacement-window readiness, and eight synthetic MIDI voices with 137,216
  recorded stereo frames, finalized WAV/MID/JSON, and zero reported underruns.
- Preserve the CPU ZIP and older snapshots. Windows only; no macOS implementation.

## 0.4.0 — Windows Qt GPU viewport experiment (November 7, 2026)

- Freeze CPU 0.3.1 as a separate Windows x64 ZIP with private Python/Tcl/Tk,
  pinned packages, fonts/licenses, exact tagged source and older source snapshots.
  Verify its 45 tests and relocated startup; provide SHA256 archive/file manifests.
- Add a separate PySide6/Qt Quick launcher with Direct3D 11 triangle geometry,
  GPU grid/scanlines/trace/glow, retained native resource lifetimes and bounded
  timing metrics. Keep the full CPU editor and DSP/playback source intact.
- Add scene import, built-in source playback, tempo, shape, integer XYZ, rotation,
  listening-mode/mix and temporary palette/glow controls for viewport evaluation.
- Verify actual RTX 4070 adapter selection, audio playback without underruns,
  resize/shape/palette/glow changes, and GPU buffer/degenerate-stroke tests.
- Document the partial UI migration, timing limitations and Windows-only scope.
  No macOS implementation or packaging without future user approval.

## 0.3.1 — preview performance and rollback history (November 6, 2026)

- Cache grid, scanlines and viewport border as a bitmap; rebuild only on size or
  appearance changes. Reuse the sharp trace, selection handles and axis items.
- Replace full-resolution wide vector glow with a raster glow layer at one-third
  spatial resolution, limited to 15 updates/second. Keep every sharp-trace sample.
- Add separate scanline/glow preferences, migrating the previous combined switch.
- Reuse actual audio-worker samples during playback instead of synthesizing a
  second copy in the GUI. Cache settings and update dirty/title state on edits.
- Limit status refresh to 5 Hz, schedule the viewport at a fixed 30 Hz without
  catch-up bursts, and suspend viewport rendering while minimized.
- Move periodic MIDI device discovery off the GUI thread and serialize port API
  operations; ignore stale checks after reconnecting.
- Controlled replay benchmark: CRT-on median frame cost fell from 60.38 ms to
  10.27 ms; CRT-off from 6.73 ms to 2.68 ms. Includes Tk painting, one cube,
  1,920 samples, same replayed stereo sample sequence and window size in both editions.
- Verified 45 tests and a two-second, eight-voice live-audio/recording check with
  CRT effects enabled: zero reported underruns. Audio DSP source is unchanged.
- Preserved separate runnable 0.2.0 pre-CRT and 0.3.0 original-CRT editions,
  source hash manifests, Git checkpoints and release tags. Added a reusable
  viewport benchmark for future regressions.

## 0.3.0 — preserved baseline (November 6, 2026)

- CRT edition with JetBrains Mono, coordinated palettes, scanlines/glow, and
  INPUT / SCENE / MIX tabs, preserved separately before performance work.
- Added scoped Git checkpoint tooling and immutable source snapshots.
- Recovered the 0.2.0 pre-CRT edition from recorded successful session edits;
  all 39 tests passed after recovery. No prior Git commits existed.
