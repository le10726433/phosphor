# 12. Reference

Quick reference: keys and mouse gestures for the whole app, limits, files and where
phosphor keeps them, a glossary, and troubleshooting. Composer's own gesture tables are in
[Composer › Reference](08-composer.md#812-reference-mouse-wheel-and-keys).

## 12.1 Keyboard shortcuts

| Action | macOS | Windows |
|---|---|---|
| New instance (a second phosphor window) | ⌘N | Ctrl+N |
| Open scene / project | ⌘O | Ctrl+O |
| Save scene / project | ⌘S | Ctrl+S |
| Save … as | ⇧⌘S | Ctrl+Shift+S |
| Undo (Composer and Sandbox; shape builder keeps its own history) | ⌘Z | Ctrl+Z |
| Redo (Composer and Sandbox; shape builder keeps its own history) | ⇧⌘Z | Ctrl+Y or Ctrl+Shift+Z |
| Full screen (the app window) | ⌃⌘F | F11 |
| Trace only | ⇧⌘F | Ctrl+Shift+F |
| Leave full screen, Trace only or the full-screen trace | Escape | Escape |
| Play / Stop (Composer and Sandbox) | Space | Space |
| Go to start / loop start | Home | Home |
| Stop at song end (Composer) | End | End |
| Zoom to song (Composer) | ⌘0 | Ctrl+0 |

These are the default keys. Change them in **Preferences › Controls › Keyboard
shortcuts** (search, change, reset one or all, export and import; saved on this computer,
with separate macOS and Windows defaults; see [2.5](02-interface.md#preferences--controls)).
Open **Help › Keyboard shortcuts…** for the searchable list of the keys in use and the
gestures, including Composer and the shape builder. The window stays open while you work.
On compact Mac keyboards, fn+Left / fn+Right provide Home / End. Copy, Cut, Paste,
Delete and Return depend on the focused editor; Return applies a typed value.

The File menu's open and save items follow the workspace: **Open scene…** in the Sandbox,
**Open project…** in Composer, and so on.

### Composer note roll

These shortcuts act with the note roll focused. Pitch zoom follows the Zoom time
modifier selected in Preferences › Controls.

| Action | macOS | Windows |
|---|---|---|
| Select all notes | ⌘A | Ctrl+A |
| Transpose selected notes one semitone (scale degree with Snap to scale) | ↑ / ↓ | ↑ / ↓ |
| Duplicate selected notes | ⌘D | Ctrl+D |
| Split notes at playhead | ⌘E | Ctrl+E |
| Join touching selected notes of the same pitch | ⌘J | Ctrl+J |
| Extend selected notes to the next chord group | ⌘L | Ctrl+L |
| Quantize selected notes… | ⌘U | Ctrl+U |
| Copy and move selected notes | ⌥drag note | Alt+drag note |
| Stamp a chord | Click in Chord draw mode | Click in Chord draw mode |
| Zoom pitch rows over the pitch names | ⌃wheel | Ctrl+wheel |
| Transpose selected notes one octave | ⇧↑ / ⇧↓ | Shift+↑ / Shift+↓ |
| Move selected notes one Snap step (Off: quarter beat) | ← / → (also with ⇧) | ← / → (also with Shift) |
| Draw note / set length | Click / drag empty space | Click / drag empty space |
| Select only that note | Click note | Click note |
| Move selected notes / resize note | Drag note / right edge | Drag note / right edge |
| Add notes touched by selection box | ⇧drag empty space | Shift+drag empty space |
| Add or remove note from selection | ⇧click | Shift+click |
| Delete note / Paste at playhead | Right-click note / empty space | Right-click note / empty space |

See [Note tools](08-composer.md#note-tools) for the menu tools and settings.

### Objects list

These keys act only while the Objects list has focus. In the Composer timeline,
M still adds a marker and S still splits a clip.

| Action | macOS | Windows |
|---|---|---|
| Toggle mute on selected objects | M | M |
| Toggle solo on selected objects | S | S |
| Unmute all objects | ⌥M | Alt+M |

## 12.2 Mouse and wheel

Wheel modifiers below apply by default. Change Composer wheel actions and builder zoom
in **Preferences › Controls › Scroll & trackpad**, with separate mouse wheel and trackpad
sensitivities; labels name the physical keys.

| Where | Gesture | Result |
|---|---|---|
| Monitor (Sandbox) | Drag an object's centre / the selected corner | Move / resize it |
| Monitor | Hover the top-right corner | **Pop out** and **Full screen** buttons |
| Any slider | Double-click | Reset to its default |
| A transform, Path or equation-variable number | Right-click | **Static** / **Time varying…** (an expression), and in Composer **Automate** / **Remove automation** |
| Position, Size, Output level, Original blend | Right-click | **Automate** / **Remove automation** (Composer) |
| Objects row or Transform step | Right-click | **Automate On/Off** / **Remove On/Off automation** (Composer) |
| Objects row | Right-click › **Replace** | Change the type of that object, or of every selected object, keeping place, transforms and automation ([4.14](04-objects-and-scenes.md#414-replace-an-objects-type)) |
| Clip on the Scenes track | Right-click | Clip menu, including **Mute clip** |
| Clip on the Scenes track | Drag / Shift-click | Move or reorder (swap past a neighbour's middle) / add to the block a drag moves |
| INSPECT CYCLE panel | Esc or **✕ Close inspector** | Turn the inspector off and return to the previous panel |
| Velocity lane | Right-click › **Collapse velocity lane** / **Expand velocity lane** | A thin strip / full height (saved) |
| Composer edge (the faint line above the timeline) | Drag | Resize the timeline |
| Side panel edge (the line beside the panel) | Drag / double-click | Resize the panel (280–900 px, kept on this computer) / restore the default width |
| Timeline (trackpad) | Two-finger drag / pinch / Option + pinch (Alt on Windows) | Move time and pitch with the fingers / zoom time around the pointer / row or block height |
| Timeline | Shift + wheel / Control + wheel (Ctrl on Windows) | Move in time / zoom time |
| Note or transform roll | Command + wheel (Alt on Windows) | Row or block height |
| Shape builder canvas | Wheel or pinch / middle-drag or two-finger drag | Zoom / pan |

The builder's tools and gestures are in [The shape builder](05-shape-builder.md); the
timeline's are in [Composer](08-composer.md#812-reference-mouse-wheel-and-keys).

## 12.3 Expression names

| Name | Meaning | Where |
|---|---|---|
| `t` | Seconds on the scene's clock (clip-local in Composer) | Everywhere expressions are allowed |
| `p` | Beats since the clip's time origin, following Composer tempo changes; `t × tempo / 60` at constant tempo (in the Sandbox too) | Everywhere |
| `s` | Position 0–1 along the object (its route, or the equation's curve) | Parametric equations/variables and transforms; unavailable in recurrences |
| `x`, `y`, `z` | The point's coordinates before this step | Transform fields only (Map's X′, Y′, Z′ included) |
| `x`, `y`, `z` | Previous sequence point (n−1) | Recurrence equations |
| `x[n-k]`, `y[n-k]`, `z[n-k]` | Earlier sequence point; literal integer k from 1 through Order | Recurrence equations only |
| `n`, `N` | Current step index and total Steps, including initial rows | Recurrence equations only |
| user variables | Equation variables | That object's x, y and z equations |
| object variables | **Scene › Transform › Variables**: the object's own numbers, evaluated once per pose frame | That object's transform fields (Map's formulas included) and Path Start/Trace |
| `pi`, `tau`, `e` | Constants (`tau` = 2π) | Everywhere |

Functions and operators: see [Parametric equations](06-parametric-equations.md). Angles in
transforms are in degrees; trigonometric functions take radians (`sin(tau*s)` is one cycle).

In a [Recurrence equation](04-objects-and-scenes.md#410-recurrence-equation), `t`
and `p` are constant for one sequence; variable expressions use only `t` and `p`.
Other subscripts are unavailable. Order is 1–8, Steps 16–4096, and at most eight
variables are allowed. Divergent sequences park at the last finite point and show
a warning with the step index.

## 12.4 Limits

| Item | Limit |
|---|---|
| Composer tempo | 20–400 BPM; at most 256 tempo changes |
| Composer meter | default 4/4; at most 256 meter changes; 1–16 beats per bar; beat unit 2, 4, 8 or 16 |
| Clips / notes | up to 512 clips, 20,000 notes; notes A0–C8 (MIDI 21–108), monophonic |
| Audio tracks / clips | up to 32 tracks, 256 audio clips in all, 6 hours each; track and clip gain 0–2; track pan −1 to 1, phase −180° to 180°, time delay −20 to 20 ms; track names up to 64 characters |
| Automation gain | −1 to 1, multiplies the number; at most two nodes at one beat |
| Transforms per object | see [Transforms](07-transforms.md) |
| Parametric variables | see [Parametric equations](06-parametric-equations.md) |
| Object variables (Transform › Variables) | 8 per object; see [Transforms](07-transforms.md#78-variables) |
| Saved transforms | 200, names up to 40 characters; see [Transforms](07-transforms.md#79-saved-transforms) |
| Text | see [Objects and scenes](04-objects-and-scenes.md) |
| Shape builder drawing | 16,384 nodes, 32,768 segments, 65,536 points |
| SVG import | 16 MB, 500,000 source points |
| 3D model import (OBJ, STL, glTF, GLB) | 512 MB, 2,000,000 faces (including glTF line segments); over 10,000 triangles the import dialog suggests simplifying |
| Time-view window | 0.1–2000 ms |
| Monitor signal gain | 0–32× (display only) |
| Output | samples beyond ±1 are clipped; **Mix › Output level** scales the drawing |
| Exports | stereo WAV or FLAC; 44.1/48/96/192 kHz; 16/24-bit PCM, or WAV 32-bit float; left = X, right = Y |

## 12.5 Files

| File | What it holds |
|---|---|
| `*.scene.json` | A Sandbox scene: objects, transforms, equations, imported drawings and models (including baked animation), source settings, mix. Scene version 24; versions 11–24 open. Settings an object's type does not use (after **Replace**) are saved as its parked settings; an object's own variables are saved with it. |
| `*.project.json` | A Composer project: tempo, loop, notes, every clip with its whole scene, automation and optional colour, and the audio tracks (name, gain, pan, phase and its mode, their pan and phase lanes, mute, collapse) with their audio clips, which link to their files. Project version 11; versions 1–11 open (before 10, the audio clips open on one track, **Audio 1**; before 11, tracks have no pan or phase). |
| `*.shape.json` | A reusable shape-builder drawing (**Save shape…** / **Load shape…**). |
| `*.wav` | Exports: stereo, left = X, right = Y. |
| `*.mid` | MIDI files to play (MIDI file source), import into Composer notes, export from notes, and Live MIDI recordings. |

Opening a file made by an older version can change it slightly, always with a notice: gains
beyond ±1 are clamped, and an equation variable named `p` is renamed `p_` (because `p` is
now the tempo parameter).

### Where phosphor keeps your settings

| File | Contents |
|---|---|
| `preferences.json` | Appearance (colours, beam, phosphor, decay, brightness), wheel and trackpad controls, the side panel's width, export defaults and metronome level (0–1, default 0.3; Preferences › Controls) |
| `shortcuts.json` | The keyboard shortcuts you changed (Preferences › Controls › Keyboard shortcuts); the others keep their defaults |
| `signal-preferences.json` | Signal output settings |
| `function-presets.json` | Your saved parametric equation presets |
| `recurrence-presets.json` | Your saved recurrence equation presets |
| `transform-presets.json` | Your saved transforms: steps and stacks with the variables they read (Transform › Add › Saved) |
| `workspace.json` | The workspace to open next time (Composer or Sandbox) |
| `monitor-window.json` | The pop-out monitor's position and whether it was open |
| `recent-files.json` | The last 10 scenes and last 10 projects for File › Open Recent; Clear Menu clears the current workspace's list |
| `autosave/` | Recovery copies for dirty Composer projects and Sandbox scenes, with original-path sidecars and per-process locks; written every 120 seconds |

They live in the user data folder:

| Running phosphor | Folder |
|---|---|
| From the source folder (`app.py`) | `.user` beside `app.py` |
| Packaged app on macOS | `~/Library/Application Support/phosphor` |
| Packaged app on Windows | `%APPDATA%\phosphor` |

Set the environment variable `PHOSPHOR_USER_DIR` to use another folder. Monitor input and
device choices are not saved: phosphor always starts on **Generated scene / MIDI**.

## 12.6 Glossary

| Term | Meaning |
|---|---|
| **X–Y mode** | An oscilloscope mode where the left input moves the beam sideways and the right input up and down, so a stereo signal draws a picture. |
| **Scene** | Objects, their transforms and path windows, a source and a mix: one picture-making setup. |
| **Object** | One drawable thing in a scene: a solid, a 2D shape, text, an equation, a custom drawing or an import. |
| **Route** | The order in which the beam visits an object's lines. |
| **Travel** | The beam's path between objects or strokes. It is visible: phosphor has no blanking channel. |
| **Cycle** | One complete trace of the scene. With a note playing, the scene is traced once per cycle of the note's frequency. |
| **Path window** | **Start** and **Trace**: which part of an object's route is drawn. |
| **Pose frame** | One update of the transforms (**Mix › Rotation updates (Hz)** times a second). |
| **Transform stack** | The ordered list of transforms applied to one object. |
| **Object variable** | A number of one object (**Transform › Variables**) that its transform steps and path window read by name. |
| **Source** | What supplies the pitch: Scale, MIDI file, Single tone or Live MIDI (Sandbox); the note roll (Composer). |
| **Clip** | A scene placed on Composer's timeline. |
| **Lane** | An automation line for one number of a clip; its **gain** multiplies the number. |
| **Parked** | Kept aside by **Replace**: settings and variable lanes of a type the object is not now. They come back when it is that type again. |
| **Reference** | A lane's first node's gain, shown as a dotted line; nodes snap to it. |
| **Phosphor** | The screen coating that glows after the beam passes; its decay sets how long a trace lingers. |
| **Time view** | X(t) and Y(t) drawn against time, like a dual-trace oscilloscope. |
| **Trigger** | The zero crossing that starts each time-view sweep, so a repeating wave stands still. |

## 12.7 Troubleshooting

| Problem | Try |
|---|---|
| No sound | Press **Play**. In the Sandbox, check **Input › Monitor input** is **Generated scene / MIDI** and the scene has an object. In Composer, a note must be sounding under a clip that has an object. Check **Mix › Output level** and **Listening mode**. See **About › Audio troubleshooting** for device problems. |
| The picture is empty or tiny | Add an object (new scenes are empty). Check **Size**, **Path › Trace** (0 hides an object), **Mix › Output level**, and **Appearance › Monitor › Signal gain**. |
| Clicks, dropouts, "Underruns" rising | Close other audio-heavy programs; very dense imports and many objects cost more per sample. **About › Audio troubleshooting** lists device settings. |
| The trace is a blur | Lower the decay (**Appearance › Phosphor/Decay**), or slow the Spin. |
| A field shows an error | The expression uses a name or function that is not allowed, or has a syntax error; the message says which. |
| An automated number does not move in the Sandbox | Expected: the Sandbox has no timeline; it uses the first node's gain. |
| System audio is unavailable or silent on a Mac | Grant **System Audio Recording** permission (System Settings › Privacy & Security), then stop and start the capture. It needs macOS 14.2 or later. |
| The pop-out window is lost | Click the placeholder in the main window, or **Appearance › Monitor › Pop out** to toggle it. |

## 12.8 Version

This manual describes phosphor 0.81.0-dev (November 21, 2026). The full list of changes is
in `CHANGELOG.md` in the app folder.
