# Preview benchmark — 0.3.0 vs 0.3.1

Measured November 6, 2026 on this Windows workspace with `benchmark_viewport.py`:
40 frames, an 1180×820 app window, one cube, and 1,920 samples per sharp trace.
Both versions receive the same precomputed stereo scale samples through their
audio-history interface. No hardware audio or MIDI devices are used by the
benchmark. Timings include Python/Tk construction and `update_idletasks` painting,
with a 30 Hz target cadence and no accumulated catch-up frames. They do not
include physical monitor/compositor latency. Version-specific frame timings and
the lower-rate glow are allowed to behave normally.

| CRT effects | Version | Median frame | Mean frame | 95th percentile | Canvas items |
| --- | --- | ---: | ---: | ---: | ---: |
| Off | Original 0.3.0 | 6.73 ms | 7.45 ms | 9.60 ms | 34 |
| Off | Optimized 0.3.1 | 2.68 ms | 3.95 ms | 10.17 ms | 18 |
| On | Original 0.3.0 | 60.38 ms | 59.66 ms | 82.55 ms | 214 |
| On | Optimized 0.3.1 | 10.27 ms | 10.86 ms | 19.29 ms | 18 |

This is about 5.9× lower median frame cost with effects on. The new glow has
different presentation characteristics: it is rasterized at one-third spatial
resolution and updated at up to 15 Hz. The sharp trace retains all samples at
the normal viewport cadence. There is no audio decimation or waveform rewrite.

A separate actual audio-device check ran the optimized GUI and CRT effects with
eight synthetic MIDI voices and stereo WAV/MIDI recording for roughly two
seconds: 95,232 frames submitted at 48 kHz, zero reported audio underruns, and all
three recording files finalized. This is a short smoke test, not a latency or
long-duration guarantee, and does not verify physical controller key handling.

The original and optimized applications retain their own test suites. The
recovered pre-CRT edition passed 39 tests; original CRT 40; optimized edition
45. The optimized tests check cache reuse, full sharp-trace sample count,
effect-independent audio samples, actual output reuse during playback, separate
preference migration, background MIDI health checks and frozen snapshot hashes.

## Windows Qt experiment — November 7, 2026

PySide6/Qt 6.10.3 selected **Direct3D11 / NVIDIA GeForce RTX 4070**, confirmed
through `QQuickWindow.rhi().driverInfo()`. This verifies hardware adapter selection
on this PC, not on the intended LattePanda or another Windows machine.

At 1180×820, with 1,920 sharp-trace samples:

| Qt run | Sample preparation median / p95 | Geometry upload median / p95 |
| --- | ---: | ---: |
| Cube simulation, glow off, 6 seconds | 0.84 / 0.99 ms | 0.40 / 0.52 ms |
| Cube + octant sphere, flute score, glow on, actual audio, 8 seconds | 0.04 / 1.08 ms | 0.90 / 1.40 ms |

The audio run reuses the playback worker's signal, hence its lower median sample
preparation time. It submitted 315,392 stereo frames, with zero reported underruns
or audio errors. A separate ten-second cube/glow playback run submitted 409,600
frames with zero reported underruns. Resizing, all solid types, integer rotation,
palette changes and toggling empty/nonempty glow geometry were exercised in a
separate eight-second run. The final MSAA-enabled multi-shape screenshot was
visually inspected. These are smoke checks, not long-duration performance claims.

Observed frame-interval medians were about 14 ms, with p95 about 21 ms; total
frame counts correspond to roughly 60 updates/second. Timer/vsync scheduling makes
`1000 / median interval` an inappropriate FPS estimate here. Rendering pauses
when minimized, and timing buffers retain at most 4,096 entries.

For context, rerunning the existing CPU replay benchmark in this session gave
9.01 ms median / 15.42 ms p95 with CRT effects, and 2.25 / 3.96 ms without.
**Do not derive a speedup ratio from these figures.** CPU replay measures complete
Tk construction/painting; Qt upload measures CPU geometry preparation/copying and
excludes GPU completion. Input timing, viewport area, glow appearance and cadence
also differ. This establishes a working low-cost GPU submission path; a matched
end-to-end benchmark is needed before choosing it as the full editor's replacement.

The engine, synth, playback and recording modules were not modified. All 48 tests
pass, including three new geometry/buffer-contract tests. The separate frozen
CPU ZIP passed its original 45 tests using its own bundled runtime and a relocated
imports/Tk-startup check.

## Full Qt editor — 0.5.0

The editor uses the same GPU item with four additional geometry layers for shape
handles/bounds and the RGB XYZ key. On the RTX 4070, an eight-second exercise with
audio, two shapes, rotation/placement/size changes and a palette change submitted
315,392 audio frames with zero reported underruns. Median sample/overlay CPU
preparation was 0.82 ms (p95 1.42 ms), geometry upload 0.46 ms (p95 0.69 ms).
Upload excludes GPU completion and is not an end-to-end Tk speed comparison.

A separate normal Qt-event-loop check played eight synthetic MIDI voices while
recording 137,216 stereo frames; WAV/MIDI/JSON finalized with zero reported
underruns. This verifies the software input path and audio device, not physical
controller operation. Native mouse events verified movement and resizing,
integer entry/reset, preferences Apply/Cancel without scene changes, scene
save/open/cancel, and actual Ctrl+N startup readiness before the old window closed.
All 51 unit/regression tests pass. No audio DSP modules were modified.

## System-audio monitor — 0.6.0

A five-second native Qt run selected the AudioBox USB 96 WASAPI loopback endpoint
at 48 kHz stereo. It captured 188,416 frames with no capture errors or status
events, while the app's output engine submitted zero frames. Direct3D11 remained
on the RTX 4070. Median capture-history preparation was 0.054 ms (p95 0.074 ms);
geometry submission was 0.97 ms (p95 1.26 ms), excluding GPU completion.
This verifies device capture/lifecycle, not an end-to-end latency measurement.

Four new tests verify native-rate input-only stream options, bit-preserving L/R
delivery including out-of-range floats, mono/surround selection, bounded buffers,
stale silence, invalid input aborts, device rejection and cleanup. All 55 tests
pass. Native UI checks verify that capture bypasses `render()`, displays the raw
samples without overlays, never starts output/record/export, and leaves the
generated scene unchanged when toggling monitoring. Existing audio DSP is intact.

## Optional display interpolation — 0.6.2

A 3.5-second native Qt check replayed a synthetic stereo curve at Cubic 8x with
glow enabled: 15,353 display points from 1,920 original samples, 207 rendered
frames, 0.85 ms median preparation and 5.12 ms median geometry submission on this
PC. Submission excludes GPU completion. Every original sample was retained
exactly and the source buffer stayed unchanged; the output engine submitted zero
frames. This was a preview replay, not a higher-rate hardware capture.

All 59 regression tests pass. Native preferences checks verify persistent Apply,
Cancel restoration and both cubic levels through the GPU path. Off remains the
default, and existing preference files migrate without changing audio or scenes.

## Adjustable phosphor glow — 0.7.0

History is bounded to eight snapshots (two cached glow geometries per snapshot).
Emission is limited to 30 Hz and further spaced for longer decay times. Fading
updates material opacity, without rebuilding or copying old geometry each frame.
Default decay is zero, preserving the previous nonpersistent appearance.

A 2.4-second native GPU replay with Cubic 4x, a 2 px trace, 6 px glow radius,
1.5× intensity and 0.5-second decay rendered 135 frames and submitted no audio.
Twelve history layers still had visible geometry shortly after the input went
silent; all expired after the decay interval. Median geometry submission over
the replay (including its silent portion) was 1.45 ms, excluding GPU completion.
This is a lifecycle/decay check, not a general performance guarantee.

All 62 regression tests pass. Native preferences tests cover numeric edits,
double-click reset, persistence and Cancel; scene data and audio DSP stay intact.

## macOS playback burst diagnosis — 0.8.3-dev

Native report on Apple M5 / Metal, one cube, 710×710 logical viewport at 2× pixel
ratio, 120 Hz display, glow and scanlines disabled:

| Phase | Presentation FPS | GUI updates | Fresh playback windows | Audio write wait p95 |
| --- | ---: | ---: | ---: | ---: |
| Stopped before | 60.37 | 181 | — | — |
| Playing | 59.99 | 180 | 46 | 80.50 ms |
| Stopped after | 60.00 | 180 | — | — |

There were zero audio underruns. Audio synthesis median/p95 was 0.47/1.49 ms;
geometry upload median/p95 while playing was 0.92/1.57 ms. The display slowdown
came from selecting the latest submitted block only when burst writes completed.
Smaller synthesis blocks did not prevent host API buffering bursts.

Version 0.8.3 introduces a clocked reader over a larger bounded submitted-audio
history. A fake device accepting eight 512-sample writes per ~85 ms burst
reproduced 40 fresh windows across 188 UI ticks with the old reader. The new
reader delivered 188/188 fresh cursors with the same simulated device stalls.
Presentation FPS remained ~62 in both offscreen cases. Audio generation and
recording samples are unchanged, and no synthetic display interpolation is used.
The cursor uses monotonic time plus reported output latency; precise acoustic
synchronization is not calibrated. The native post-fix 0.8.3-dev rerun confirmed 60.02 presentation FPS during
playback and 179 fresh audio windows across 179 playback GUI ticks (100%).
Stopped presentation rates were 60.21 FPS before and 59.94 FPS after. There were
zero audio underruns or errors. CoreAudio write waits remained bursty at 80.52 ms
p95, confirming that the clocked history reader removes the display dependency
on those write completions. Reported output latency was 443.29 ms; this is a
device/driver estimate, not a calibrated acoustic measurement. The initial
measurement was short and used one cube with glow/scanlines disabled.

## Native macOS system audio — 0.9.0-dev

A 20-second native desktop test on Apple M5 / Metal captured 912,384 stereo frames
at 48 kHz from the all-system Core Audio tap. Maximum raw peak was 0.229, with no
capture errors or dropped callback blocks. The application submitted zero audio
playback frames and reported no output errors. The user confirmed visualization
worked. This verifies input-only capture with non-silent external audio after
privacy approval; it is a short run, not a long-duration or route-change test.
