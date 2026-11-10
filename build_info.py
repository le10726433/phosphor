VERSION = '0.31.0'
PROJECT_DATE = 'November 6, 2026'
BUILD_DATE = 'November 9, 2026'
TRANSPARENCY = ('Created by Mike Le with AI-assisted development using OpenAI Codex. '
                'AI contributed to software design, code, tests, and documentation.')
INSTRUCTIONS = '''Fullscreen, time traces and corner dwell
View > Fullscreen keeps the controls. View > Trace-only fullscreen hides controls,
menus, status and editing overlays; Escape restores the previous view.
The X(t), Y(t) tab shows both raw channels against time over the recent 40 ms.
Freeze holds those plots without stopping playback or capture. Display gain and
interpolation do not change these raw plots.
Preferences > Signal output > Hardware corner dwell adjusts actual generated
X/Y samples (0-500 microseconds per sharp turn, default 0). Source cycle timing
is preserved by speeding edge travel; total dwell is capped at 25% of a cycle.
High notes/many corners may shorten holds. At 48 kHz one sample is 20.83 us.
Use Fully shaped audio or blend 0 to inspect holds. More dwell brightens corners
and can change timbre; it does not remove DAC filtering or channel phase errors.
This hardware preference is separate from scenes and Appearance. Captured system
audio and original/source audio are never modified by corner dwell.

Control panel and text objects
Drag the divider to resize the left control panel. INPUT has Source and Live MIDI
sections; SCENE has Objects and Transform sections to keep controls readable.
In SCENE > Objects, choose Add text, type the text, and press Enter. Text traces
A-Z, numbers and punctuation; lowercase is displayed as capitals. Maximum 64
characters. Use Transform to position, resize or rotate it. Add text starts with
rotation stopped. Text saves with scenes and affects generated audio and exports.
Connections between strokes/letters are visible because stereo has no blanking.
Long text and high notes share fewer samples per stroke, reducing detail.

macOS development preview
On macOS the viewport uses Metal. Use Command+N/O/S/Shift+S for scenes.
Generated scenes, audio output, MIDI and WAV export use the shared engine.
On macOS, choose Monitor input > System audio > START VISUALIZATION.
Allow System Audio Recording when macOS asks, then stop/restart capture.
All system audio is mixed to stereo in memory; no microphone or screen images
are captured, and captured audio is never played back or saved. Requires macOS
14.2 or later. Windows retains WASAPI playback-device selection.

Getting started (Windows system-audio capture)
In the Qt editor, for computer playback visualization set Monitor input to System audio, choose
the playback device your other app uses, and press START VISUALIZATION. This is
input-only: no shaping, recording or audio output. Visualization input gain
scales the display only (0–32x, default 1x); double-click its slider to reset.
It never changes playback or captured samples.
Beam-speed brightness in Preferences > Appearance dims fast parts of the trace
and keeps slow parts bright, like a real tube; 0 restores the even Classic trace.
Phosphor display accumulates light over time and fades it like a chosen tube
coating. Off keeps the line trace. Phosphor intensity works like a scope's
intensity knob. Appearance settings never change audio, scenes or exports.
Use Preferences > Appearance > Display interpolation for Off,
Cubic 4x or Cubic 8x smoothing. This adds display points, not audio information;
the native capture rate stays unchanged. Off is the default. Apply/OK saves the
choice; Cancel restores the last applied choice. It is not saved in scene files.

Qt Preferences also provides trace thickness (0.5–6 px), glow size (0–24 px
radius), glow intensity (0–3x) and glow decay (0–2 seconds) sliders with numeric
entry and double-click defaults. Enable GPU trace glow for glow effects. Decay
0 means no afterimage; positive values fade previous glow traces over that time.
These are persistent preview settings only, independent of audio and scene files.

Mono duplicates to X/Y; surround shows the first L/R pair. Grid/glow/palette still apply. Silence
clears the trace; if the device changes, stop, refresh and restart capture.
Monitor/device choices are temporary; scene files keep your generated settings.
Switch back to Generated scene / MIDI to resume editing (playback stays stopped).

Use the INPUT tab to choose C-major scale, MIDI file or Live MIDI, then press PLAY
AUDIO. MIDI file asks for a .mid file and plays its top melodic line (one phase
drives the beam, so chords would distort the figure). Tempo starts at the file's
own tempo; change it to play faster or slower. Scenes remember the file's path. SCENE contains shapes, view angles and rotation period;
MIX contains travel, output level, blend, listening modes and exports.
Original audio plays the source synth; Fully shaped audio plays the geometry-derived
signal. Processed output crossfades them using Original blend.

Scenes and shapes
Add a shape, select its type, or Duplicate/Remove a selected instance. Select in
the list or click a numbered center dot. Drag the dot to move; drag its corner to
resize. Shapes may extend beyond the output range and deliberately clip.
X/Y/Z sliders orient each shape in integer degrees. Seconds per revolution is
logarithmic; far right or typing inf disables rotation. Double-click a slider
thumb to restore its default. The XYZ key is a preview-only overlay.

Travel between shapes
No changes preserves the default scene path. Faster travel shortens connection
time; Nearest-shape ordering shortens connections; Retrace existing edges places
some travel along shape edges. These are stereo-only strategies, not true beam
blanking, and connecting lines can remain visible on a physical oscilloscope.

Live MIDI
Connect the controller or MIDI interface, Refresh, choose the input, then Connect
and Play audio preview. Select All channels or a particular channel and a synth
preset. There are eight voices total. MIDI input gain 4–8x enlarges quiet shapes.
Velocity, CC7 volume, pitch bend (+/-2 semitones), CC64 sustain, CC66 sostenuto and
CC1 octave alternation are supported. Panic clears sounding notes and pedal holds.
Chord voices are shaped and mixed, which can distort the scene geometry.
On Windows, Live MIDI uses WASAPI exclusive mode for the lowest delay, so other
apps cannot play through that output device until you stop audio. If the device
refuses, it falls back to shared mode; the status line shows the mode in use.

Recording and export
Record performance in Live MIDI saves matching WAV, MIDI and scene/event metadata
JSON files. Stop recording finalizes them while you keep playing. The WAV captures
the selected listening mode. Stopping audio, opening a scene, or exiting finalizes
an active recording. Export source/processed WAV renders built-in source sequences.
Output is stereo 48 kHz, left=X and right=Y. Over-range output clips at +/-1.

Files and shortcuts
Ctrl+S saves a scene; Ctrl+Shift+S saves a copy; Ctrl+O opens a scene. Scene files
contain geometry, source, tempo, synth/mix/travel settings and input-device choice.
They do not contain appearance preferences and do not reconnect hardware or start
audio automatically. A title-bar asterisk marks unsaved scene changes.
Ctrl+N prompts to save the current scene, launches a fresh app process, and closes
the old instance after the new window is ready. Cancel keeps the current window.

Preferences
Choose Green monochrome, Amber or Cyan, plus grid divisions per axis (2–64).
CRT scanlines and lightweight trace glow have separate switches. JetBrains Mono
styles the interface. The sharp preview retains every displayed audio sample;
Qt Quick draws the grid, trace, glow and editing overlays using Direct3D 11.
The adapter name appears below the viewport. Audio and recording are unaffected.
Apply saves preferences for subsequent launches. Preferences affect
the interface only, never audio, scene data, or WAV/MIDI output.
'''
