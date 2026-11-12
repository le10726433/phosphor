# phosphor — Audio to Shape

For key features, the program structure, how shapes are generated and how the
phosphor screen is simulated, see the [project README](README.md). This guide
holds detailed notes by feature; version headings mark when each was added.

The running app icon uses the green, amber, cyan or white logo matching
Appearance. On macOS this updates the running Dock icon; a pinned Python launcher
may still show Python's icon while phosphor is closed. The capture helper keeps
its existing bundle identifier/path, with phosphor as its new display name.

## Function plots, video input, builder text and route minimizing — 0.36.1

The INPUT/SCENE/MIX controls sit to the right of the viewport. New scenes start in
**Fully shaped audio**; scene files keep their own listening mode.

**Function plots.** Set **INPUT → Monitor input** to **Function x(t), y(t)** and edit
the two expressions in the **X(t), Y(t)** tab. They use Python maths syntax: `t` is
seconds, `s` is the 0–1 cycle position at the **s frequency**, and you can add up to
8 variables, each with a slider. `sin`, `cos`, `tau` and the other `math` functions
are available, each taking an exact number of arguments. Only maths is accepted, so
a scene or preset file cannot run code. Output above ±1 is clipped and **MIX →
Output level** scales it; WAV export is not offered for function plots yet. Scenes
save the functions. **Save as preset…** and **Delete saved…** manage your presets,
kept in `function-presets.json` in the user folder.

**Video input.** **INPUT → Monitor input → Video file (phosphor CRT)** opens a video
and plays it with sound. Each display step deposits one 60 Hz field of scanlines
(240, 480, 576 or 720 lines) into the phosphor energy buffer, so the phosphor's
decay, the beam profile, glow, grain and shimmer all apply. With the phosphor display
off, the video uses the palette colour. Decoding is Qt Multimedia's FFmpeg backend,
and frames stay on the GPU. The picture is letterboxed in the square viewport; there
is no rolling scan band or interlace yet.

**Builder text, bold and extrude.** In the shape builder (**Build shape…**), the **Text** tool
takes typed text and a size; click the canvas to place it. Letters become ordinary
strokes, centred on the click and selected, so they can be moved, edited, bolded or
extruded. With strokes selected, the panel shows **Bold width** and **Extrude depth**
in place of the single-node controls:

- **Bold width** traces the outline of the strokes fattened to that width, with round
  caps and joins. Overlaps between joined strokes are removed, so an H or E is one
  closed outline; letters with holes need one travel link per hole.
- **Extrude depth** adds front and back copies at ±depth/2 along Z, joined at the
  corners. Turn or spin the shape in Transform to see it.

Styles follow their shapes through moves, joins, copy/paste and Undo. Bold plus
extrude can make a route several times longer, so high source pitches undersample
it; lower pitches or thinner shapes trace cleanly. Shape files are version 5, and
scene files version 10.

**Route minimizing.** **Preferences → Signal output → Route minimizing** plans each
shape's own beam route, for builder drawings, text, imported models and built-in
solids:

- **Off** keeps the original routes, sample for sample.
- **Shortest jumps** keeps how each connected part is traced and visits separate
  parts nearest-first.
- **Shortest total path** (the default) retraces or jumps between odd-degree points,
  whichever is shorter, and traces the result in one circuit.

A plan is used only if it is shorter, and jumps count as travel for **MIX → Faster
travel**. The builder's dashed lines show the jumps of the selected mode. This is a
machine preference, not saved in scenes, and it changes the sound of existing scenes
unless set to Off.

**Grain and shimmer.** **Preferences → Phosphor grain** adds a screen-fixed grain and
**Beam shimmer** varies new light from frame to frame. Both default to 0, the
unchanged look, and never affect audio.

## Shape builder — first usability review

Open **SCENE → Objects → Build shape…**. This adds a new custom object; selecting
an existing custom object changes the button to **Edit shape…**. The first
milestone is a 2D line drawing workspace inside the main window.

- **Polyline:** click successive points, Enter or Finish stroke to finish, or
  click the starting node to close. **Line** finishes after two clicks.
- **Arc:** click start, a point on the curve, then end. Dragging any of these
  defining points recomputes the circular arc. Collinear points cannot form an
  arc; the builder retains the first two points so you can retry the end point.
- **Semicircle:** click the two diameter endpoints. The new curve is selected;
  **Flip side** changes its bulge. Later, Select and click its stroke to flip it.
- **Circle:** click center, then a circumference point. Select its stroke to edit
  Radius numerically; its two defining points remain editable.
- **Spline:** click successive points the smooth curve passes through, then
  Enter to finish or click the first point to close. Select and drag its points
  to reshape it. Deleting a spline knot retains the remaining curve when there
  are enough points; deleting an arc/semicircle defining point removes that curve.
- **Select:** click/drag a node, or select a line. Dragging commits on release.
  Edit selected-node X/Y numerically; Delete removes the selection. Detach
  selected line separates one incident line from a shared node.
- Drag a stroke away from its nodes to move the whole connected shape. Drag
  empty canvas to box-select connected shapes touched by the rectangle; Shift
  adds to an existing selection. Drag a selected stroke to move selected shapes
  together. Right-click for Copy/Cut/Paste; Cmd+C/X/V on Mac or Ctrl+C/X/V on
  Windows work while the canvas has focus. Pasted copies are independent and
  preserve their internal joins and curves. Delete/Cut removes selected shapes;
  clicking a node still lets you edit or delete just that node.
- Hold **Shift** while moving nodes or shapes to lock horizontal/vertical motion
  to the dominant axis. Shift also constrains each newly placed point relative
  to the previous point in all drawing tools. Snapping respects this axis lock.
- **Snap + join** highlights endpoints within a fixed screen-pixel radius.
  Connected endpoints share one node. Option/Alt temporarily bypasses snapping;
  crossings do not automatically create joins.
- **Undo/Redo** covers completed gestures and numeric edits. Escape cancels an
  unfinished stroke or drag. Scroll zooms; middle-drag pans; Fit frames the drawing.
- Enable **Live audition** to update the playing scene after completed edits.
  Otherwise use Apply to scene. Play audio uses the current source or Live MIDI;
  the output preview includes all scene objects. A new custom object is added
  alongside existing objects. Remove unwanted objects back in the scene editor.
- **Save shape… / Load shape…** store reusable `.shape.json` files. Ordinary
  scene saving includes applied custom geometry. Back to scene prompts about
  unapplied edits; Discard keeps the last applied/auditioned scene state.

Empty drafts cannot replace a playable scene: deleting every line keeps the last
applied shape until another completed drawing is applied. Open/branched drawings
retrace edges. Dotted links between disconnected strokes represent visible stereo
travel, not blanking. Drawings are limited to 128 nodes and 256 segments, including
curves. Duplicate scene objects edit independently. Line-only custom scenes use
version 2; curve scenes use version 3 and curve shape files use version 2. Older
app versions cannot open curve files. Built-in/text-only scenes retain version 1.
Saved curves contain defining points, not tessellation. Output tessellation is
deterministic, independent of zoom, and bounded to 4096 derived segments with a
local-coordinate chord-error target of 0.0015 units. Smooth curve subdivisions are
not treated as extra hardware-dwell corners. Timeline editing is deferred until
explicitly requested again. More tools await the next usability review.

## Motion paths inside the shape builder

Draw the guide and its follower shapes **in the same drawing**. In Select mode,
click a connected path and enable **Use as motion path**. Only that selected path
changes role; other shapes remain drawable. Branching or disconnected guides
prompt for editing before conversion. Guides display dashed and never enter audio.

Select the follower shapes (Shift-click or box-select), **without the guide**, then
click **Assign motion…** in the builder inspector. Choose a guide, velocity in
scene units/second, endpoint behavior and optional direction following. Assignment
preserves the shapes' positions and their offset from the guide's starting point;
it does not snap the group center onto the guide.

- Closed guides loop. Open guides default to **Back and forth**; **Stop at end**
  holds the final pose. Starting audio or applying changed assignments restarts
  motion. Playback/export share the pose evaluator; offline export starts at zero.
- **Follow path direction** defaults off. When enabled, changes in heading rotate
  the rigid group and its offset relative to the guide start. Zero angle offset
  preserves the initial orientation; an explicit Angle offset adds rotation.
  Open-path reversals turn the group 180 degrees.
- The construction canvas shows **rest positions** for predictable editing. The
  output preview shows animation. Enable **Live audition** or use Apply to scene
  to publish completed edits. The whole assembly stays one scene object.
- Uncheck the selected guide's role to make it drawable and pause its followers
  at their rest poses. Assignments are retained; rechecking resumes them. Use
  **Remove motion from selection** to detach selected followers permanently.
- Moving a guide and its followers together preserves their relative offset
  without doubling the translation. Undo/Redo includes roles and assignments.
  Clipboard copies containing both retain motion with independent IDs; followers
  copied alone become static geometry because their guide was not copied.

Builder-motion scenes use version 5 and reusable shape files version 4. Previous
files, including legacy scene-level motion assignments, remain readable. New
assignments are made in the builder; the old scene Motion button is removed.
New Qt windows still start with a stationary Tilting circle. Timeline editing
remains explicitly deferred.

## Shape redraw (experimental)

Validated by the user on macOS on November 8, 2026, with automated native checks
also passing on Windows. This feature remains experimental and disabled by default.

In **Preferences → Signal output**, enable **Shape redraw** and enter the total
draws per shape (1–32). With multiple drawable shapes, each completes that many
passes before traveling to the next. A single drawable shape is unchanged at
every setting; motion guides do not count. Connected builder components count
as drawable shapes. Count 1 and the disabled checkbox preserve existing behavior.

Repeats fit into the same source phase cycle, keeping source note timing and
motion timing unchanged. Each trace receives fewer samples and shaped timbre can
change. Generated playback, Live MIDI shaping and processed exports use the same
setting. Original/source audio and system capture remain unchanged. Apply saves
the machine-specific preference; Cancel restores the last applied values. It is
separate from scenes and appearance. Very complex scenes may need fewer repeats;
if later edits exceed the detail budget, the status reports Redraw inactive and
playback uses one pass until count or complexity is reduced.

The current **0.32.6** Qt editor supports **macOS / Metal** and
**Windows / Direct3D 11**. Use Python 3.12. macOS system-audio monitoring uses
native Core Audio taps (macOS 15 or later for this build); Windows retains WASAPI loopback.

For macOS, follow [the development guide](docs/MACOS_DEVELOPMENT.md), or:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements/requirements-macos.lock.txt
./LaunchMac.command
```

For a first Windows installation, see the
[beginner guide](README.md#first-time-installation-on-windows). Quick reference:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python app.py
```

The selected graphics backend appears beneath the viewport. This is a source
development build; a notarized standalone Mac app is not packaged yet.

## Fullscreen, time traces and corner dwell — 0.11.0-dev

**View → Fullscreen** fills the display while keeping controls. **View → Trace-only
fullscreen** hides controls, menus/status, editing handles and axes for immersion.
The grid, trace and CRT appearance remain as configured. Press **Escape** to
restore the previous view; if that was fullscreen, Escape again exits fullscreen.

The 40 ms X(t), Y(t) time traces were removed in 0.36.1; that tab now edits a
function plot. For system audio, the **X(t), Y(t) sweep** display draws both channels
against time on the phosphor screen.

**Preferences → Signal output → Hardware corner dwell** sets a nominal hold at
sharp turns, from **0–500 microseconds**, initially **0**. It affects actual generated
X/Y playback, MIDI shaping and processed WAV exports, so you can tune the physical
scope. It does not process captured system audio or original/source audio.
The hardware profile persists separately from scenes and appearance; Apply saves,
Cancel restores the last applied setting, and Defaults/double-click reset to zero.

Dwell preserves source-cycle/note timing by speeding the remaining edge travel.
Total holds are capped at **25% of each cycle**; high notes or complex routes may
receive less dwell than requested. Smooth curve subdivisions do not receive holds
unless the projected route makes a sharp turn. At 48 kHz, samples are **20.83 µs**
apart; very short holds may receive no sample. Use Fully shaped audio or blend 0
when calibrating. Envelopes, animation and overlapping MIDI voices can still move
the resulting coordinate during a nominal vertex hold.

Your corner-settling reasoning is useful, but dwell is not a universal sharpening
filter: it can brighten corners, change timbre, and faster edges may worsen rounding.
It cannot remove DAC filtering, AC coupling or X/Y phase mismatch. Keep it at zero
for the original waveform and tune against the physical oscilloscope.

## Responsive control panel and text — 0.10.0-dev

Drag the divider between the control panel and viewport to change panel width.
INPUT now has **Source** and **Live MIDI** sections. SCENE has **Objects** and
**Transform** sections. The controls fit without panel scrollbars at the supported
1000×700 minimum window size; labels wrap and dropdowns fit the available width.
Long lists or exceptional error content can still use scrolling when necessary.

To trace text, open **SCENE → Objects → Add text**, type into **Text to trace**,
then press **Enter** or leave the entry to apply. Use **Transform** to position,
resize and rotate the selected text. New text starts with rotation stopped.
Text objects save with scenes and can be duplicated or mixed with other shapes.

The original stroke alphabet supports up to **64 characters**: A–Z, digits,
spaces and standard punctuation; lowercase renders as capitals. Blank text is
allowed. These are actual continuous X–Y paths, including travel between letters
and disconnected strokes. A stereo scope has no third-channel blanking, so
connecting strokes remain visible. Text affects generated audio, live MIDI
shaping, processed WAV export and recordings. It is not overlaid on system audio.
Long strings and high notes leave fewer samples per stroke and may lose detail.
Use short text/lower notes for the clearest results. Text authoring is in the Qt
editor; the retained CPU/Tk editor has no text-entry controls.

## macOS system-audio monitoring — 0.9.0-dev

Requires macOS 15 or later and Xcode Command Line Tools for the local helper build.
The helper is already built in this workspace. No virtual audio driver or extra
Python dependency is needed.

1. Restart the app, then select **Monitor input → System audio — visualization only**.
2. Keep **All system audio (Core Audio)** and press **START VISUALIZATION**.
3. Approve the macOS System Audio Recording prompt, then stop/restart visualization
   if permission was granted while capture was starting. Play audio in another app.
4. Use **STOP VISUALIZATION** to release capture. Return to **Generated scene / MIDI**
   to resume editing and normal app audio playback.

If permission was denied, open **System Settings → Privacy & Security → Screen &
System Audio Recording** (the wording varies by macOS), allow **phosphor
Audio Capture** or the app macOS attributes the launch to, and restart capture.
Microphone permission is not needed. Permission must be granted by the user.

This uses an input-only private Core Audio tap, mixing output apps into stereo.
It does not capture microphone input or screen images, mute apps, change the
system output device, install a driver, save captured audio, or feed audio back
to the speakers. Device selection is an all-system source on macOS; Windows
retains per-playback-endpoint selection. Gain, interpolation and CRT effects are
preview-only. Source/scene/MIDI controls and recording/WAV export stay disabled
while monitoring. Protected content and app/driver restrictions may yield silence.

See [macOS development](docs/MACOS_DEVELOPMENT.md) for implementation and validation.

## GPU editor and rollback

### Trace and glow preferences — 0.7.0

**Preferences → Appearance** includes four live-preview sliders with numeric
entry and double-click defaults:

| Control | Range | Default |
| --- | --- | --- |
| Trace thickness | 0.5–6 px | 1 px |
| Glow size | 0–24 px radius beyond the sharp trace | 3 px |
| Glow intensity | 0–3× | 1× |
| Glow decay time | 0–2 seconds | 0 (no afterimage) |

Enable **GPU trace glow** for glow controls to affect the preview. Positive decay
keeps fading afterimages of earlier traces; each fades to zero over the selected
time. Decay is time-based and uses up to eight cached GPU snapshots, not an
unbounded frame history. Grid, handles and XYZ axes do not leave afterimages.
Changing viewport size, source/mode or stroke style clears old snapshots.

Apply/OK saves these preferences; Cancel restores the last applied values and
Defaults restores the values above. Older preferences migrate to the previous
appearance with no afterimages. These controls affect both generated and system
audio previews in Qt, never playback, capture samples, exports or scene files.
The retained CPU fallback preserves these preference values but does not render
the new Qt effects. All 62 unit/regression tests and native preference/decay checks
pass.

### System-audio visualization — 0.6.0

**Optional interpolation (0.6.2):** open **Preferences → Appearance → Display
interpolation** and choose **Off** (default), **Cubic 4x**, or **Cubic 8x**.
Changes preview immediately; Apply/OK persists the choice and Cancel restores the
last applied choice. Older preference files migrate to Off. This preference is
independent of scene files. Since 0.20.0-dev it also applies to generated scenes.

Interpolation inserts smooth cubic points between the original samples without
changing them or joining the end of a live trace to its start. At 48 kHz, a 40 ms
window has 1,920 original points; 4x displays 7,677 points and 8x displays 15,353.
These are synthetic display points, not higher-rate captured audio or recovered
detail. Cubic interpolation can overshoot; the viewport may clip it. Higher
settings require more rendering work. Capture, playback and the raw peak meter
remain unchanged. Generated audio is never interpolated, only its display.

In **INPUT**, set **Monitor input → System audio — visualization only**. Select
the playback device used by your music/browser app (or keep the Windows default),
then press **START VISUALIZATION** and play audio in that other application.
**STOP VISUALIZATION** releases capture. Stop before changing devices; Refresh
playback devices updates the WASAPI output endpoints. The active endpoint, native
sample rate/channel count, input peak and capture status appear in the sidebar.

**Input gain** scales the displayed trace from **0–32×**, with
numeric entry and a default of **1×**. Double-click the slider to reset it. This
is a temporary preview control: it does not change playback, captured samples,
scene settings or the raw input peak meter. Enlarged traces may clip at the viewport.

This mode sends captured L/R samples to the GPU X–Y trace. It opens an
input-only loopback stream, never an audio output. No shaping, playback gain/blend,
resampling, recording, audio export, or microphone capture is performed. Mono
duplicates onto X/Y; multichannel endpoints display their first two channels.
Silence clears the trace rather than freezing old samples. Changing the Windows
default device does not move an already running capture; stop and restart it.

Scene/mix controls and generation controls are disabled while monitoring system
audio; palette, grid and glow remain preview effects. Switching back to
**Generated scene / MIDI** restores editing with playback stopped. Monitor and
device selection are temporary and are not written into scene files; saving a
scene still saves the underlying generated scene. No captured audio is saved.

The new Windows-only dependency is `PyAudioWPatch==0.2.12.9`; install the updated
`requirements.txt`. Device discovery/open/close run on workers and capture uses
a bounded in-memory buffer. See the upstream
[WASAPI loopback example](https://github.com/s0d3s/PyAudioWPatch/blob/master/examples/pawp_record_wasapi_loopback.py).

For a timed input-only desktop check (no sound generated by this app):

```powershell
python app.py --system-audio --play --seconds 5 --report .user/system-audio.json
```

The unit/regression suite now contains 62 tests. The native editor smoke check
also verifies raw system-monitor bypass, disabled editing, blocked recording/WAV
export, and restoration of the original scene without marking it dirty.

**0.5.0** promotes the reviewed Qt Quick renderer into the full editor. Both
`python app.py` and `LaunchQt.bat` launch it. INPUT / SCENE / MIX, shape dragging,
XYZ axes, MIDI gain/synth/recording, WAV exports, menus, shortcuts, scene files
and persistent appearance preferences are implemented in Qt. Shape and palette
edits no longer replace imported multi-shape scenes. WAV exports run in a worker
and replace their destination only after rendering finishes; wait for export to
finish before closing the app. Existing scene/preference formats are unchanged.

`python app.py --cpu` launches the retained Tk editor (requires Tkinter). Its
Ctrl+N also stays in the CPU edition. `python tools/qt_preview.py` retains the small
viewport test program. [QT_PROTOTYPE.md](docs/QT_PROTOTYPE.md) describes the original
prototype, the frozen CPU ZIP and current launch commands. The reviewed prototype
is tagged `oscilloscope-v0.4.0-reviewed`; CPU ZIP/tag remain unchanged.

## Desktop menus, scenes, and preferences

**About → Audio troubleshooting** opens a dedicated help tab explaining Windows
audio effects, the controlled Realtek/AudioBox comparison and effects-off retest,
steps for checking enhancements/spatial sound, and small/flat/stuttering traces.
It distinguishes waveform fidelity from rendering speed and includes Microsoft
references. This help does not change any audio or Windows settings.

**File** provides New instance, Open/Save/Save As scene, source/processed WAV
exports, performance recording controls, and Exit. The title shows the current
scene filename, with an asterisk for unsaved configuration changes.

| Shortcut | Action |
| --- | --- |
| Ctrl+N | Prompt to save, launch a fresh app process, then close the current window after the replacement reports ready |
| Ctrl+O | Open a scene, prompting to save unsaved changes |
| Ctrl+S | Save the current scene |
| Ctrl+Shift+S | Save scene as a new file |

New instance always offers Save / Don't Save / Cancel, including for a clean
scene. Cancelling either that prompt or the save-file picker keeps the old window.
Save and launch failures keep the existing scene open. Audio/MIDI are closed and
active recordings finalized before launching the replacement, avoiding device
contention. Scene files use versioned `.scene.json` data containing all shapes,
their positions/sizes/rotations, source, tempo, synth/mix/travel settings, listening
mode and preferred MIDI input name. Opening never starts audio or automatically
connects MIDI. Scene writes are atomic and stopped rotation is JSON `null`.

**Preferences → Appearance** offers three coordinated CRT palettes: **Green
monochrome**, **Amber**, and **Cyan**, plus grid divisions per axis (2–64) and an
separate scanline and GPU trace-glow switches. The preset styles the complete interface;
separate background/trace/grid color pickers and the old light/night toggle are
replaced by these palettes. Edits preview immediately;
Apply/OK persist them to `.user/preferences.json` in this app folder. Cancel
restores the last applied appearance. Preferences are independent of scenes:
opening/saving a scene never changes or stores aesthetic preferences, and changes
to appearance do not mark the scene dirty. Audio/exports are unaffected.

**About** shows Made by Mike Le, the project/build dates and version, the approved
AI transparency statement, and a How to use tab. `phosphor/storage/build_info.py` holds the release
version/build date and in-app instructions; update the build date with a release.

Double-click any slider thumb to reset its default: XYZ=0°, rotation=5 seconds
per turn, output level=0.55. Defaults remain the same after opening a scene or
switching the selected shape. Use the sidebar scrollbar for additional controls.

The interface uses **JetBrains Mono**, bundled with its SIL Open Font License in
`phosphor/assets/fonts`. Qt loads the fonts privately for the app process, without
installing them system-wide.
Font source: [JetBrains/JetBrainsMono](https://github.com/JetBrains/JetBrainsMono).

**INPUT / SCENE / MIX** tabs organize source/MIDI/tempo, geometry/orientation, and
travel/mix/output controls. Playback and recording stay visible below the tabs.
Menus are integrated into the header. The CRT effects are preview-only: they do
not alter audio, exports, or MIDI recordings. Existing appearance files migrate
to the nearest preset by trace color while preserving grid density.

## Performance and version history

The GPU editor targets approximately 60 updates/second, reuses actual playback
samples, and draws the grid, scanlines, trace, glow, drag handles and XYZ key with
Qt Quick triangle geometry. Audio synthesis/projection and vertex preparation
remain on the CPU. Status refreshes at 5 Hz; minimized windows suspend viewport
work. MIDI discovery/health checks and WAV export run off the GUI thread.

The retained CPU version 0.3.1 caches the grid/scanline background and retains the existing canvas
items. The sharp trace still uses every sample in the 40 ms preview window. Glow
is a separate raster effect at one-third resolution, refreshed at up to 15 Hz;
the main preview targets 30 Hz. Status labels refresh at 5 Hz. During playback,
the GUI displays the audio worker's samples and does not synthesize a second
signal. Minimized windows suspend viewport work. MIDI device health checks run
off the GUI thread. These optimizations do not change source synthesis, geometry,
MIDI voices or recorded sample fidelity. Install the updated requirements for
Pillow, which is used for cached preview images.

Run `python -m unittest discover -s tests/unit -t .` for the unit/regression tests.
Run `python tests/smoke/smoke_qt_editor.py` separately in a Windows desktop session for native
GPU/control/drag/reset/preferences/scene integration checks. It uses temporary
files and does not overwrite personal preferences. For timed checks:

```powershell
python app.py --seconds 8 --exercise --play --report .user/qt-editor.json --screenshot .user/qt-editor.png
```

Measured on this Windows workspace with the same stereo replay, one cube and an
1180×820 app window: median full frame cost (including Tk paint) changed from
60.38 ms to 10.27 ms with CRT effects enabled, and from 6.73 ms to 2.68 ms with
effects disabled. Measurements depend on hardware, viewport size and scene;
they are not a guarantee for every setup. See [docs/PERFORMANCE.md](docs/PERFORMANCE.md).

To rerun the repeatable viewport benchmark (it opens a test window, without
playing sound or opening MIDI devices):

```powershell
python tools/benchmark_viewport.py
```

## Live MIDI controller mode

Install the updated requirements in the same Python environment you use to run
the app. MIDI uses Mido with the RtMidi backend. The workspace-local dependency
folder is also supported for this checkout.

1. Connect the controller by USB MIDI, or its DIN output to your interface's MIDI input.
2. Click Refresh in Live MIDI, select its input port, then Connect. Connecting
   selects the Live MIDI source. Select All channels or a specific MIDI channel.
3. Choose a synth preset and click Play audio preview. Keys played before starting
   audio are not queued. Input is monitored in the MIDI status area.
4. Use Original audio for the synth sound, Fully shaped audio for shaped voices,
   or Processed output to use the original blend. Each held voice traces the full
   scene at its own pitch; the voices are then mixed, so chords distort the geometry.
5. Panic / All notes off immediately clears voices and pedal holds. Changing the
   receive channel also clears voices. Changing presets affects new notes.

The 12 presets are sine, sawtooth, square, triangle, variable triangle (35% ramp
width), and pulse (25% duty), each with short and sustained variants. Envelopes
follow the controller's basic settings: 10.5 ms attack, 100 ms hold, 300 ms decay,
sustain 0 or 1, and 300 ms release. The app synthesizes these independently; it
does not copy or require the controller's onboard audio. Oscillators use up to
32 Fourier harmonics below Nyquist, without adding output high/low-pass filters.
Geometry-derived waveforms can still alias as described below.

Eight total voices are supported across channels. The oldest releasing voice is
stolen first, otherwise the oldest voice. Mixing reserves a fixed 1/8 of available
level per voice to avoid clipping and gain changes as notes are added/released;
a single note is therefore quieter than the earlier single-voice test sources.
Use **MIDI input gain (×)** to boost the synthesized input before shaping and
mixing. Range is 0–16×, initially 1×; try 4× or 8× for larger live shapes. At 8×,
a full-velocity single note cancels the fixed 1/8 voice allocation. Velocity,
CC7 and envelopes still affect amplitude. Gain updates notes already sounding;
it affects both shaped and original synth signals, while Output level remains
the final master control. Gain is preserved in recording JSON; raw MIDI note
velocities are unchanged.

Boosting gain spends reserved polyphonic headroom. If the selected live output
exceeds ±1, it is hard-clipped at the output boundary rather than renormalized
or wrapped. A cumulative clipped-channel-sample count appears in the main status
until playback restarts. Lower MIDI gain or Output level if this occurs. The
preview and recorded WAV use those same clipped samples; high gain can flatten
the shape and distort the sound. Zero MIDI gain mutes live voices. Other audio
sources are unaffected. Changing gain abruptly can create an audible transient.

This is eight voices total, rather than eight
per channel as on the controller. Amplitude/velocity also scale scene offsets for
live voices, and live silence produces zero output. The waveform preset changes
the original synth timbre; fully shaped timbre mainly follows the scene geometry.

Supported input messages:

| Message | App behavior |
| --- | --- |
| Note On / Off, including Note On with velocity 0 | Velocity-sensitive voices with release |
| Pitch bend | Full 14-bit input, ±2 semitones, per channel |
| CC7 | Channel volume, including notes already sounding |
| CC1 | Any nonzero value enables played pitch / octave-below alternation; switches every 25 ms (20 full cycles/s), matching the sketch's control tick |
| CC64 | Sustain pedal |
| CC66 | Sostenuto, latching notes held when the pedal is pressed |
| CC120 / 123 / 121 | All sound off / all notes off / reset controllers |
| Program/bank changes | Recorded but do not select GUI synth presets |

The controller sketch's SYNTH-mode preset changes are local and do not send a
corresponding Program Change. Choose app presets in the GUI; no firmware changes
are required. No MIDI output or echo is sent back to the controller. MIDI clock
does not quantize playing or drive a sequencer in this version. BPM affects
recording tempo metadata, not the speed of live playing or the 20 Hz modulation.

### Performance recording

While Live MIDI audio is running, click Record performance and choose a new WAV
filename. It writes three files with the same basename:

- `.wav`: stereo, 48 kHz, 16-bit PCM of the selected listening mode, including
  changes to blend, scene, volume, and view. It records samples submitted to the
  audio device; device underruns or analog output behavior cannot be captured.
- `.mid`: accepted channel MIDI events on the same sample clock, with the tempo
  at recording start. No beat quantization is applied. Events are applied at
  audio-block boundaries (128 samples, about 2.7 ms at 48 kHz, on the low-latency
  streams).
- `.json`: scene/preset/listening-mode changes, initial voice state, and event
  sample positions. Standard MIDI cannot describe this app's geometry processing.

Already sounding notes and current controllers are seeded at recording start.
Their precise oscillator/envelope ages are retained in JSON; another MIDI player's
sound will not reproduce the WAV exactly. Recording end appends pedal-off and
all-sound-off messages so playback does not leave stuck notes. Stop recording
finalizes files while live playing continues. Stopping audio, changing source,
disconnecting, or closing the app finalizes any recording. Existing filenames are
refused to avoid overwriting takes. Output is written incrementally rather than
buffering a complete performance in memory; event metadata remains in memory.

Live audio uses the low-latency 128-frame paths described in the
[project README](README.md#audio-output-paths); actual latency depends on
hardware and drivers. Underruns appear in the main status area. A periodic
port check stops playback/recording if the device disappears from the input list.
Reconnect and restart audio to resume. Current scene editing and recording are
available; automatic playback of recorded MIDI/scene sessions is a future step.
Use the sidebar scrollbar to reach the rest of the controls.

## New transform

This replaces the independent drawing oscillator with audio-driven traversal.
For the test scale, the synthesizer supplies the input waveform, phase, and
amplitude envelope. One source cycle traverses one complete wireframe route:

    output(t) = output_level * envelope(t) * path(frac(source_phase(t)), pose(t))

Distance along the projected path is proportional to phase. The input envelope
controls image size, including note attacks/releases. At a fixed pose this is
periodic waveshaping: the source cycle rate and note timing are retained, but
the spectrum and perceived pitch can change considerably. Rotation also alters
the waveform. This is not a claim of transparent music preservation.

No independent drawing signal is added at the default blend of zero. The optional
original blend crossfades between shaped stereo audio and original stereo audio:

    output = output_level * ((1 - blend) * shaped + blend * original)

`prepare_audio(stereo, sample_rate)` accepts arbitrary finite stereo PCM arrays
in [-1, 1]. It finds the dominant stereo component (avoiding simple L+R phase
cancellation) and uses an FFT analytic signal to extract phase and a globally
normalized amplitude envelope. Pass the resulting `PreparedAudio` as `source`
to `render` or `export_wav`. This is offline analysis, not live input or an output
high/low-pass filter. It does not preserve the original stereo image, and complex
music has no single phase/pitch that can preserve every voice. Global envelope
normalization preserves relative dynamics, not absolute input level. Silent/DC-only
sources become silent shaped output. FFT boundary artifacts are possible.

The GUI offers the synthetic scale, the melody of a MIDI file and Live MIDI;
audio-file loading is a future integration step. Source arrays loop when sampled
beyond their end.

## MIDI file source

Choose **INPUT → Source → MIDI file** and pick a `.mid` or `.midi` file; **Choose…**
changes it later. One phase drives the beam, so the app keeps a single line: the
highest note of each chord, skipping notes that start under a still-held melody
note. In passages where only a bass line plays, the melody follows it. Gaps over
30 ms become rests, and the file's tempo changes are kept. Drum channel 10 is ignored.

**Tempo** starts at the file's first tempo, which plays it at its original speed;
raise or lower it to play faster or slower. The melody uses deterministic,
flute-like additive synthesis with subtle vibrato, duplicated to both channels.
**Export source WAV** renders one pass of the melody at the selected tempo;
**Export processed WAV** renders it through the scene.

Scenes store the MIDI file's path, not its notes (scene version 7). If the file has
moved, a scene still finds a file of the same name in the scene's own folder.
Scenes from earlier builds that used a bundled song open with the C-major scale.
phosphor ships no music.

## Controls

### X, Y and Z view rotation

Each selected shape has three linked degree sliders/number fields, from -180°
to +180°, snapping to whole degrees. Typed fractional angles round to the nearest
whole degree for rendering and normalize in the field on Enter or focus loss.
X tips the view vertically, Y turns it horizontally, and Z rolls it
in the viewport. They are applied in X → Y → Z order, around the shape's own
center, after its existing animated pose. Zero preserves the original view
(including the reference tilt used by 3D solids). Reset selected view rotation
returns all three offsets to zero. They also work on polygons and the tilting
circle, retaining depth until the final orthographic projection.

Shapes are stationary unless an X/Y/Z spin is set; spins add to the manual
offsets. Each instance retains its own angles when selected, duplicated or
removed. These controls affect the generated X–Y trajectory, live audio, processed
WAV exports and recording metadata. The projection remains orthographic; the
controls change orientation rather than lens perspective or camera distance.

A bottom-left XYZ orientation key follows the selected shape's animated pose
and manual rotations: X is red, Y green, Z blue. Dashed portions mark negative
directions; an end-on axis uses a dot (toward viewer) or cross (away). It is a
canvas-only overlay and never enters generated audio, WAV exports, or MIDI/WAV
recordings. Seconds-per-revolution and output-level controls retain their
fractional precision.

### SVG drawings

**File > Import SVG…** adds an SVG's paths and basic shapes as an editable custom
shape. Only outlines are traced: fills, colours, embedded images and SVG text are not
imported, so convert text to outlines first. Art is simplified to fit 128 nodes and
256 lines, and the app reports when detail was lost.

### Spin

The Transform tab has X, Y and Z spin sliders from -10 to 10 revolutions per second,
applied around the viewport axes and added to the view rotation. Faster spin needs a
higher Rotation updates rate to look smooth. Reset view clears spin.

### 3D models

**File > Import 3D model…** adds a Wavefront OBJ file as a see-through wireframe.
Choosing **3D model** under Selected object replaces the selected object instead.
Import keeps open edges and creases of at least 30 degrees, up to the 256 sharpest
edges, because each pass only has about 800 samples at 60 Hz. Hard-edged models
work best; smooth forms have few sharp edges and may look incomplete. The model is
stored in the scene, so the original file is not needed afterwards.

### Travel between shapes

The travel dropdown has four exclusive modes. New scenes start on **Faster travel**
at ×64; saved scenes keep their own setting:

| Mode | Behavior |
| --- | --- |
| No changes | Previous scene trace: closed shape routes in list order, constant path speed including connecting moves. |
| Faster travel | Same geometry and list order, but spends less time on connections. The speed multiplier is adjustable from 1–64×, initially 8×. It is relative to drawing speed, with one complete scene traversal still fitting each source phase cycle. |
| Nearest-shape ordering | Greedily chooses the next nearest shape and an entry vertex, rotating each closed route to start there. Includes the return connection and falls back to the original route if the total connecting distance would be worse. Does not rearrange the editor's list. |
| Retrace existing edges | Keeps list order, chooses the nearest vertex pair for each connection, and follows existing graph edges from the drawing entry back out to its exit vertex. Full wireframe coverage is retained; extra travel on existing edges can increase their brightness. |

These affect actual shape-derived audio for test sources, MIDI voices, playback,
and processed export. Mode/speed changes are included in performance-recording
metadata. Original synth/source audio is unchanged. For one shape, these modes
have no effect, except that Faster travel also speeds the connecting moves between
letters and separate strokes in a text object. They are alternatives, not combined
strategies.

There is no third-channel brightness control and no fake blanking in the preview.
Connecting lines can remain visible. Fast transitions can have too few samples
to draw faithfully, and the DAC/scope bandwidth can round them; audible transients
or timbre changes are possible. Optimization uses the projected full-size pose
at each rotation update; changing poses can switch routes and cause discontinuities.
The preview connects the same output samples the physical scope receives.

### Multiple shapes

The Shapes list contains independent solid instances. **Add** creates another cube;
then choose its type using Selected solid. **Duplicate** copies the selected solid,
size and rotation period with a small horizontal offset where space permits.
**Remove** deletes the selected instance; at least one shape remains in the scene.
Duplicates may use the same solid type or any combination of the available solids.

Select a shape in the list or click its numbered center dot in the viewport.
Its corner handle resizes only that shape; dragging its center moves only that
shape. The seconds-per-revolution field edits the selected instance. Tempo,
source, rotation-update rate, output level and original blend apply to the scene.
Only the selected instance has a selection box and resize handle. Reset affects
the selected shape's position and size. Use the sidebar scrollbar if needed.

All shapes share one continuous scene path: each audio phase cycle visits their
wireframes in list order and returns to the first. Travel between separated shapes
is included, so connecting lines can appear. Signals are not added together,
which would sum coordinates into a different shape rather than display both.
More shapes share the available samples per cycle, reducing detail per shape and
changing the resulting sound. Shape sizes and offsets may extend beyond output
range; the resulting trace clips at the signal boundary. Live playback and processed WAV
export include the entire scene; source WAV and original-audio comparison do not.
Use File → Save scene to preserve the complete scene between sessions.

Drag the yellow center dot to position the solid in two dimensions. Drag the blue
square at the upper-right corner of its selection box to resize it uniformly.
The box uses the solid's bounding sphere, so it remains stable while rotating
and during note attacks. Reset position and size restores the centered default.
Position and enlargement are unrestricted; only a minimum positive size is kept.
You can deliberately move or enlarge shapes beyond the viewport/output boundary.
When a handle would go offscreen, it remains reachable at the canvas edge so you
can bring the shape back. These edge handles are proxies for the offscreen center
or corner; the actual geometry is not moved to fit them.
Preview, playback, recordings and processed exports all hard-clip final audio
coordinates to ±1. No automatic rescaling pulls the scene back inside the bounds.
This intentionally flattens clipped edges and can distort the audible waveform.
Handles are hidden in Original audio comparison. At output level zero, dragging
is disabled. Handles describe the fully shaped component; original blend can
distort the processed trace away from this selection box.

These transforms change audio samples, live playback, and WAV export rather than
just the display. Translation adds a DC offset: a physical scope/output path must
pass DC to reproduce that position; AC-coupled audio hardware can remove it.
The offset remains during silence. Original-audio comparison stays untransformed.
Moving/resizing during playback may create audible transients.

- Selected shape: all five Platonic solids—tetrahedron, cube, octahedron,
  dodecahedron, icosahedron—and three regular 2D face polygons: equilateral
  triangle, square, regular pentagon. Tetrahedron/octahedron/icosahedron use
  triangular faces, cube uses squares, and dodecahedron uses pentagons.
  2D shapes rotate in the X–Y plane and remain flat; 3D shapes retain the
  existing tilted orthographic view. Any combination can share the scene.
- Tilting circle: a 128-segment circular wireframe rotating out of the X–Y plane
  about Y. It starts as a circle, becomes an ellipse, is a vertical line at a
  quarter-turn, and returns through an ellipse to a circle at a half-turn, when
  given a Y spin; the outline repeats twice per revolution. Without spin it is a
  face-on circle.
- Octant sphere: three perpendicular great circles in the XY, XZ, and YZ planes,
  each approximated by 96 segments. Their six shared axis poles are merged into
  graph junctions, forming eight spherical octants. All rings are visible through
  the wireframe, including their rear halves. The continuous route traces every
  ring segment once, without extra internal connecting strokes. It uses the
  tilted 3D rotation view. This is a three-ring wireframe, not a shaded surface.
- Rotation updates: 60 Hz initially. This updates geometry, not audio pitch or
  drawing repetition. Allowed range is 1–240 Hz.
- X/Y/Z spin: revolutions per second about each viewport axis, per shape,
  -10 to 10. The sliders are finer near 0 and coarser toward the ends. Scene
  version 8 removed the former seconds-per-revolution rotation; earlier scene
  files are rejected.
- Tempo: initially quarter note = 120 BPM, adjustable for either source. The scale
  uses C-major C3 through C6 and descending.
  Turnaround endpoints are not doubled: 42 notes / 21 seconds per scale loop.
- Output level: linked linear slider and optional number entry, 0–0.95,
  initially 0.55. Editing either control updates the other and live playback.
- Original blend: 0–1, initially 0 for entirely shaped audio.
- Processed output / Fully shaped audio / Original audio: selects both the audible
  signal and the plotted signal. Comparisons use the same output level.
- Play/Stop audio preview: system default stereo output, restarting at time zero.
- Export processed WAV: always exports processed output, regardless of comparison
  view. 48 kHz, 16-bit PCM, left=X and right=Y. Exports one source loop/performance.
  Its duration may not align with the five-second rotation, so repeating that
  file is not necessarily a seamless animation loop.

Valid edits update playback with a short device-buffer delay; invalid edits keep
the last valid playback settings. Parameter changes can create discontinuities.
Playback runs on a worker thread and closes on exit; device errors appear in the
status. The simulated scope displays recent transmitted samples with 40 ms
persistence and approximate timing, not calibrated audible latency.

## Geometry and sampling limitations

The cube retraces four edges to close its route. Tetrahedron, dodecahedron and
icosahedron retrace every edge in a continuous closed walk. The octahedron and
octant sphere can trace each edge once; 2D polygons and the tilting circle trace
their perimeters once. Retraces can affect
brightness and timbre.
Coordinates are normalized by a fixed bounding-sphere radius to avoid breathing
as the solid rotates. Pose is frozen for each geometry update interval; changes
at update boundaries can create small discontinuities.

At 48 kHz, C3 gives about 367 samples per traversal, C6 only about 46. A complex
solid can have more edge traversals than samples in a high note's cycle. Consecutive
samples then skip corners and the physical scope can show chord shortcuts rather
than the exact wireframe. Waveform harmonics can alias. This prototype deliberately
does not add output filtering, corner smoothing, or oversampling. Higher sample
rates or slower traversal would trade hardware requirements or musical pitch for
geometry fidelity; these are decisions for the next stage.

The preview does not model DAC reconstruction, analog bandwidth, AC coupling, or
channel mismatch. Phosphor persistence is modelled as described in the
[project README](README.md#how-the-phosphor-screen-is-simulated). Original-audio comparison of the mono
scale displays a diagonal line, as expected.

## Verify

```powershell
python -m unittest discover -s tests/unit -t .
```

Tests cover topology/routes, on-edge mathematical samples, independent rotation,
quarter-note timing, chunk consistency, stereo analysis including antiphase and
silence, blending, and audio worker cleanup/error handling.

First review: start with Cube and original blend 0; compare Original audio with
Fully shaped audio. Then try the Platonic solids and 2D polygons through the scale's full range.
`cube_audio_shaped_120bpm.wav` is the updated demo; older stage1 WAV files use
the superseded additive method.

## OLED and immersive grid — 0.11.2-dev

Choose **Preferences → Appearance → OLED white** for pure black backgrounds,
white traces/controls, and a dim gray grid. Apply/OK saves the choice.
**View → Trace-only fullscreen** extends the grid across the entire screen with
square cells; trace proportions remain unchanged (circles stay round). Escape
restores the previous view and normal square editor. These are preview effects.

## Future design discussions

[Shape constructor and musical timeline](docs/SHAPE_BUILDER_AND_TIMELINE.md) collects
the proposed drawing tools, snapping, geometry model, GUI and MIDI sequencing
workflow. It is a discussion draft; these features are not implemented yet.
