# Shape constructor and musical timeline — working design

Created November 8, 2026. Status: discussion draft, not an implementation spec.

## Current workflow — builder-local motion 0.17.0-dev

The user clarified that guides and follower assignment belong inside the shape
builder. A selected connected path has its own Motion path checkbox; the whole
custom drawing is no longer the role boundary. Select follower components and
use Assign motion in the same builder. Shapes retain their rest positions and
offset from the guide start. Relative heading change rotates the rigid group
and its offset, so Follow direction with zero angle offset has no initial jump.

Drawing stores immutable per-path guide roles and node-based follower bindings.
Disabling a guide makes it drawable and pauses its followers without deleting
assignments. Geometry stays in one scene assembly. Construction shows rest poses;
the output preview animates completed snapshots. Existing velocity, endpoint,
direction, audition, reset and sample/export behavior is retained. Node edits,
Undo/Redo, independent clipboard remapping and scene/asset persistence carry the
metadata. A whole-assembly translation also moves stored attachment references,
preventing double displacement. Copies without a guide are static geometry.

New files use scene version 5 and shape version 4; legacy scene motion still
plays. The old scene Motion button is removed from the new authoring workflow.
105 core tests and eight offscreen checks passed; native Mac builder-motion and
Windows checks are pending. Timeline editing remains explicitly deferred.

## Motion guides and Circle — 0.16.0-dev

Approved as a standalone feature, with timeline editing still explicitly deferred:
convert object drawings to motion guides through a checkbox, assign one or more
scene objects as a rigid group, and choose velocity in scene units per second.
Closed guides loop; open guides default to back-and-forth, with stop-at-end as an
option. Motion runs continuously in preview and restarts on Play or Apply motion.
Optional Follow path direction is off by default and has an angle offset;
reversals turn 180 degrees. Guides must be continuous and unbranched; conversion
prompts for editing otherwise. Toggling a guide back to an object pauses its
followers without discarding assignments. Circle creation is center then radius,
with a numeric radius inspector. New Qt instances use a stationary Tilting circle.

Motion and group anchors are immutable scene data. Pose evaluation is shared by
preview, playback and export, separate from musical phase. GUID references bind
objects to guides; static world routes and smooth curve tangents are cached.
Guide overlays never enter audio; immersive mode hides them. New features use
scene version 4 and reusable shape version 3, preserving older readers' clear
version rejection and current support for all previous formats.

Selection marquees are sharp dashed flat-color triangles at 0.8 px, half the
normal construction stroke, in a dedicated QSG layer without effects.

## Connected movement and selection — 0.15.0-dev

Approved: dragging a stroke moves its entire connected shape; dragging nodes
edits nodes. Box selection selects connected components touched by the box,
Shift-box adds selection, and selected components can move/copy/cut/paste
together. Clipboard copies preserve curve definitions and internal shared nodes;
paste remaps IDs and creates independent geometry. Right-click and standard
platform keyboard shortcuts provide clipboard operations. Each completed edit
is undoable and obeys the existing audition boundary.

Shift constrains node/group drags and point placement in every drawing tool to
the dominant horizontal/vertical axis. Snapping cannot override the constraint.
Timeline remains deferred until explicitly requested again.

## Current scope — curves 0.14.0-dev; timeline explicitly deferred

The user confirmed the line builder works, then requested curves, splines and
semicircles. Approved interactions: interpolating splines through clicked points,
three-point arcs and semicircles from diameter endpoints with Flip side.
These tools now retain analytic definitions, editable shared nodes, Undo/Redo
and completed-edit Live audition. Splines use centripetal Catmull–Rom interpolation
([parameterization reference](https://www.cemyuksel.com/research/catmullrom_param/)).
Curve tessellation is derived/cached with a fixed local-coordinate error target
of 0.0015 and a 4096-segment total budget. It is independent of zoom and preserves
defining knots. Curved scenes use version 3 and curved shape assets version 2;
older line-only and built-in/text formats remain compatible.

The user explicitly instructed: defer timeline editing until they say otherwise.
All timeline sections below remain future design notes, not approval to implement.
Next gate: native Mac curve checks and user usability review. Automated Windows
native checks passed for 0.18.0-dev on November 8, 2026 (see CHANGELOG 0.18.1-dev).

## First milestone approved and implemented for review — 0.13.0-dev

The user approved an in-window construction workspace, endpoint snap-and-join
by default, and a Live audition toggle that publishes completed edits. The first
implementation covers line/polyline drawing, node/line selection, node movement,
numeric coordinates, detach/delete, Undo/Redo, reusable shape save/load and custom
scene/audio integration. Construction and output both use native Qt Quick/RHI.
The live scene only receives immutable completed snapshots; unfinished gestures
and editor camera state stay local. Empty drafts leave the last playable scene
in place. New custom shapes add scene instances; duplicates edit independently.

Closed Eulerian components trace once; other components retrace edges. Explicit
straight travel connects disconnected components. Geometry is bounded to 128
nodes/256 lines and derived routing is cached. Custom scenes use version 2;
legacy built-in/text scenes retain version 1. This is an inline geometry adapter,
not yet a linked asset library or timeline/project format.

Review gate: native Mac rendering/input and hands-on usability before adding
curves, primitives or timeline functionality. Windows native validation remains
required before publication. See README.md for controls and limitations.

This document collects the next major direction for phosphor. Recording
an idea here does not approve implementation. Refine the open decisions together
before building; preserve the current working editor and release history.

## Requested direction

- A native shape constructor inside the application.
- Basic line, arc, and n-sided polygon tools, with additional useful drawing tools.
- Endpoint snapping so connected geometry is convenient to draw and edit.
- A GUI arrangement that accommodates construction and object transforms.
- A timeline for programming a visual sequence alongside a MIDI sequence,
  using familiar music-production concepts.

Everything below is a proposal unless identified as existing behavior or a user
request. Tool details, file formats, routing and timeline scope remain adjustable.

## Proposed workflow

1. Create or select a reusable shape in a shape library.
2. Edit its lines and curves in a dedicated construction view.
3. Place instances in a scene and set position, size, orientation and pivot.
4. Place scene/object clips alongside MIDI clips on a shared musical timeline.
5. Animate properties with keyframes and audition synchronized sound and X–Y output.
6. Save the project; export a deterministic stereo render and MIDI as appropriate.

A useful first milestone is drawing a connected custom outline and playing it
through the existing engine. Timeline development should follow once editable
geometry and its audio routing are stable.

## Shape construction tools

| Tool | Proposed interaction / purpose | Suggested stage |
| --- | --- | --- |
| Select / move | Select objects, segments or nodes; box-select and multi-select | First |
| Line | Click endpoints; numerical length/angle entry | First |
| Polyline | Continue connected segments; Enter finishes, close to starting node | First |
| Arc | Three-point arc: start, intermediate point, end; optional center/radius mode | First |
| Regular n-sided polygon | Choose side count, center, radius and rotation; retain editable parameters | First |
| Rectangle / square | Corner-to-corner creation; constrain aspect ratio | First |
| Circle / ellipse | Center/radii construction; keep analytic parameters | First |
| Node edit | Move endpoints, split segments, join/detach nodes, delete segments | First |
| Transform | Move, rotate, uniform/nonuniform scale, mirror; numeric entry and pivot | First |
| Duplicate / group | Reuse geometry and transform selections together | First |
| Cubic Bézier / pen | Smooth editable curves with tangent handles | Later |
| Align / distribute | Arrange multiple selections precisely | Later |
| Fillet / chamfer | Round or bevel a selected corner | Later |
| Freehand / simplify | Convert a drawn gesture into bounded-complexity curves | Later |
| SVG import | Limited documented vector subset; validate complexity and transforms | Later |

Start with a 2D construction plane. Existing 3D solids remain available as
parametric objects, and a custom flat drawing can rotate in 3D in the scene.
A full 3D mesh editor, extrusion and perspective drawing are separate possible
extensions, not implied first-version requirements. Stroke thickness is a preview
style; ordinary X–Y audio describes a trajectory, not a filled or thick surface.

## Snapping and connected endpoints

Proposed default: endpoint snapping enabled, with a visible target highlight and
a temporary bypass modifier. Optional grid, midpoint, center and angle snapping
can be toggled independently. Use a screen-pixel snap radius, with deterministic
nearest-target selection, so zoom does not make snapping unusably strict or loose.
Exact keyboard modifiers and default radius remain open.

Distinguish two operations:

- **Snap placement:** align a point with a target without permanently constraining it.
- **Join endpoints:** make two segment endpoints reference one shared node, so
  moving that node keeps the connection intact.

For connecting strokes within one shape, propose snap-and-join as the default,
with an explicit detach operation. Do not silently weld separate object instances
or create cross-object constraints. Snapping at a crossing should not split/join
intersecting segments unless requested. Show ambiguous targets and offer cycling.

Moving an arc endpoint must have a defined outcome: preserve its three-point
construction and recompute the circle, or preserve center/radius and constrain
motion. Prefer explicit arc handles and a displayed construction mode. Tangent
continuity is an optional constraint, not an automatic consequence of joining.

Provide Undo/Redo across creation, joining, node movement, parameter edits and
transforms. Treat a drag as one undoable action. Cancel should restore the state
before an unfinished tool operation.

## Proposed interface arrangement

- **Library / objects panel:** built-in solids, custom shape assets, text and scene
  instances. Rename, duplicate, group and show selection hierarchy.
- **Central workspace:** switch between Construct and Output preview. Construction
  has rulers/grid, zoom/pan, nodes and tool handles; output remains faithful to
  generated samples. Consider split view later.
- **Inspector:** context-sensitive dimensions, arc/polygon parameters, node data,
  transforms and pivot. Separate object pose from the editor camera/view.
- **Bottom timeline:** collapsible/resizable tracks, transport, musical ruler,
  loop range, playhead and clip/keyframe editors.
- **Existing audio controls:** retain source/MIDI/synth/mix access without forcing
  users into the timeline for a quick live performance or system-audio preview.

Retain CRT palettes including OLED white, persistent appearance preferences,
trace-only fullscreen and reachable controls. Construction overlays must never
enter audio. Reuse shared native Qt Quick/RHI rendering on Windows and macOS;
new native UI behavior requires testing on both platforms.

## Geometry model and current architecture

Today, `signal_engine.Shape` describes a built-in solid or text plus transforms.
`shape_geometry()` supplies vertices, edges and a route; `planned_path()` projects
instances and `scene_path.py` joins their closed routes into one trajectory.
`qt_scene.Scene` holds current settings and version-1 scene serialization.
There is no editable segment graph, project timeline or MIDI clip editor yet.

Proposed evolution:

- **Shape asset:** stable ID, named local-coordinate nodes and typed segments
  (line/arc/curve), primitive parameters and subpaths. Keep curves analytic in
  saved data; derive tessellation for output. Shared endpoint IDs encode joins.
- **Scene instance:** stable ID, asset reference, placement, scale, orientation
  and pivot. Duplicates can share an asset; Make unique creates an independent
  copy. Explicitly choose linked versus independent duplication in the UI.
- **Built-in adapters:** expose existing solids/text through the same geometry
  interface without converting them destructively to editable node data.
- **Render plan:** a derived, bounded representation of vertices/routes and
  traversal information, cached by geometry/pose/calibration changes.
- **Project:** assets, scene instances, tracks/clips, automation and musical timing.
  Keep IDs stable across save/load, undo and clip references.

Do not replace the current engine in one step. First add a custom-geometry
adapter beside existing shape types. Preserve current default samples and scene
loading. Editable geometry is the source of truth; tessellated points and optimized
routes are derived data. Freeze validated snapshots for playback; never mutate
geometry underneath the audio worker or do unbounded routing in its real-time path.

## Oscilloscope-specific routing decisions

A drawing is not yet a playable continuous trajectory. Open strokes, branches
and disconnected components need an explicit traversal plan, including the
return from the final point to the first point of each audio cycle.

- Preserve intentional edges and curve direction; retain all connected components.
- Display travel/retrace segments differently in construction/routing preview,
  but preserve them in output wherever physically required.
- Offer manual stroke order/direction and a deterministic automatic route.
- Optimize travel/retracing only within a documented work budget; cache results.
- A joined endpoint need not be a smooth tangent; preserve sharp corners.
- Tessellate curves to a defined error/complexity limit, with stable results
  across playback chunks and offline export.
- Keep musical phase/cycle timing independent of viewport zoom or frame rate.
- Account for high notes and complex paths sharing few samples. Preview warnings
  can show traversal length, corners and samples per musical cycle.
- Keep hardware corner dwell as separate signal calibration. Smooth curves
  should not acquire dwell at every tessellation point.

Existing stereo-only output cannot truly blank travel. The user's Tektronix 2245A
has an external blanking input, but hardware blanking was explicitly deferred.
A native constructor
must not promise invisible pen-up moves or disconnected-looking output that the
hardware cannot reproduce. Physical AC coupling can also remove position offsets.

## Musical timeline

Propose a shared bars/beats ruler with time signature and BPM. Begin with one
constant tempo per project and 4/4 as an editable default, pending agreement.
Support common musical grid divisions and triplets; snap can be disabled.
Tempo maps and time-signature changes are later extensions unless prioritized.

| Track / lane | Proposed content |
| --- | --- |
| MIDI | Note clips with pitch, duration, velocity and channel; piano-roll editing |
| MIDI controllers | Pitch bend, sustain and CC lanes, preserving current synth mappings |
| Visual scene / object | Clips controlling instance presence and referencing reusable assets/scenes |
| Automation | Position, size and XYZ orientation; later supported source/mix parameters |
| Audio reference | Optional later imported audio aligned to the same ruler; scope and processing undecided |

Transport should support play/stop, seek, loop, recording, metronome/count-in
options, and a clearly separate live-input mode. A metronome is for monitoring;
it must not leak into exported X–Y signals unless deliberately requested.
Use musical terminology: BPM, quarter/eighth notes, measures, beats and ticks.

Store musical event positions in beats/ticks and define conversion to sample
positions. Use the audio/sample clock as the playback authority, not GUI timers.
Seeking should evaluate the same timeline state as uninterrupted playback.
Define note/controller reconstruction, sustain reset and loop-boundary handling
before promising arbitrary seeks with MIDI. Specify interpolation (step/linear
first), shortest-path versus continuous rotation, keyframe override precedence,
and behavior before the first/after the last keyframe.

Preserve current polyphonic behavior initially: each note shapes the whole active
scene, then voices are mixed. Per-track/per-note shape assignments are an open
creative option that would require explicit routing and output-headroom rules.
Visual clip overlap also needs a policy: combine active instances versus exclusive
scene replacement. Start with one explicit policy, not ambiguous blending.

A project render must use the same event/automation evaluator as live playback.
MIDI recording should place incoming timestamped events onto the musical timeline;
define how device latency and count-in are handled. External MIDI clock, plugin
hosting and a complete DAW mixer are outside the suggested first milestone.

## Files, compatibility and preferences

Propose a versioned project format for assets/timeline while retaining current
`.scene.json` files as static scenes. Import an old scene as a static project
without altering its geometry/sound. Decide whether a project is JSON with
external media references or a portable ZIP container with relative media paths.
Missing media should be reported and relinkable, never silently substituted.

Keep aesthetic preferences separate from scene/project contents. Keep hardware
calibration machine-specific; reproducible export may additionally need an
explicit calibration snapshot in export metadata, not hidden preference changes.
Save atomically, validate finite values and bounded geometry/event counts, and
plan autosave recovery and Undo/Redo separately from saved-file dirty state.

## Suggested delivery stages and review gates

1. **Agree on scope and mockup:** construction view, inspector, snap semantics,
   and how the timeline fits. Review before changing the working editor.
2. **Editable line graph:** line/polyline, nodes, endpoint snap/join/detach,
   numeric edits, Undo/Redo, save/load, and one custom shape through existing audio.
3. **Parametric tools:** arcs, polygons, circles/ellipses, groups and transforms;
   review curve fidelity, routing and high-note behavior on a physical scope.
4. **Timeline transport:** shared musical clock, MIDI file import/clips, a basic
   piano roll, loop/seek and one visual clip lane. Constant tempo initially.
5. **Automation and recording:** transform keyframes, MIDI event recording,
   controller lanes and repeatable offline render.
6. **Later expansion:** tempo maps, SVG/Bézier/freehand, media containers,
   per-track shape routing and other agreed features.

Each stage should remain runnable on its own, have a source checkpoint, and pass
Windows checks plus native Mac checks where affected. Compare chunked and full
renders, MIDI timing, save/load compatibility, undo integrity and bounded routing
cost. Keep real-time playback/export off the GUI thread and viewport performance
independent of the amount of editing history.

## Decisions to discuss next

1. Is a 2D construction workspace with 3D scene rotation sufficient initially?
2. Should ordinary endpoint snapping also join nodes by default, as proposed?
3. Is three-point arc creation preferable to center/start/end as the default?
4. Should MIDI file import/playback precede full piano-roll authoring?
5. Should visual clips combine active objects or switch between complete scenes?
6. Should every MIDI track shape the whole scene initially, or does instrument-to-
   shape assignment belong in the first timeline version?
7. Which properties need initial automation: position/size/XYZ only, or also
   text content, visibility, output gain and source/blend?
8. Should this stay a focused oscilloscope composition tool, with audio reference
   support later, or should multitrack audio editing be a long-term goal?

## Discussion log

- November 8, 2026: user proposed native line/arc/n-sided-polygon construction,
  snapping endpoints, a revised shape/transform GUI, and a musical timeline tied
  to MIDI sequences. Initial architecture and staged proposals recorded here.
