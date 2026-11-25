"""Generated Composer workloads shared by the Q1 benchmark and pacing runs (planning/review-q1.md).

Every input is built in code from fixed seeds and sizes; the audio workload writes its WAVs to a
folder the caller owns (a temporary one). quick=True builds the smaller version used by --quick.
"""
from dataclasses import replace
from pathlib import Path
from random import Random

import numpy as np

from phosphor.engine.composition import Clip, Note, Project
from phosphor.engine.function_plot import Variable
from phosphor.engine.model_object import MODEL_OBJECT
from phosphor.engine.signal_engine import Settings, Shape
from phosphor.engine.transforms import MAP, NOISE, PERSPECTIVE, SCALE, SPIN, Param, new_transform
from tests.unit.golden_scenes import dense_model, scene

AUTOMATION = ('static', 'path', 'all')


def dense_beats(quick):
    """Song length in beats: 960 (8 minutes at 120 BPM), or 96 for --quick."""
    return 96. if quick else 960.


def dense_song(quick, project=None):
    """20,000 notes (2,000 for --quick) over dense_beats: seeded 3- and 4-note chords with
    velocity variation, chords and velocity on. project supplies the scene clip (and tempo
    changes); by default one golden cube clip. Its first clip is stretched over the notes."""
    count, beats = (2000 if quick else 20000), dense_beats(quick)
    spacing = beats * 3.5 / count  # Chords average 3.5 notes.
    rng = Random(410)
    notes = []
    group = 0
    while len(notes) < count:
        root = rng.randrange(42, 66)
        intervals = (0, 4, 7, 12) if rng.random() < .5 else (0, 3, 7)
        for interval in intervals[:count - len(notes)]:
            notes.append(Note(group * spacing, spacing * .9, root + interval, round(rng.uniform(.45, 1.), 3)))
        group += 1
    if project is None:
        project = Project(clips=(Clip(0., beats, scene('cube')),))
    end = max(n.end for n in notes)
    return replace(project, notes=tuple(notes), chords=True, velocity=True,
                   clips=(replace(project.clips[0], length=end),))


def lane(rng, nodes, span):
    """nodes linear points over span beats (or seconds, for audio-clip gain lanes), .35 to .95."""
    return tuple((span * i / (nodes - 1), round(rng.uniform(.35, .95), 4), 'linear') for i in range(nodes))


def complex_project(quick, automation='static', beats=96.):
    """One Composer clip of beats length with 24 objects (6 for --quick): cubes, parametric
    equations, a recurrence, text, a generated dense model and a custom drawing, each with a
    five-step stack (Scale, Spin, Map, Noise, Perspective) and an object variable read by Map.

    automation: 'static' (no lanes or expressions on the automatable values), 'path' (Path
    Start/Trace vary over time by expression, the old Phase 6 item; nothing else) or 'all'
    (lanes with 128 nodes, 16 for --quick, on every object variable, every automatable
    transform parameter, Path Start/Trace, the object size and both clip gains, plus four
    tempo changes). The base geometry is identical in all three.
    """
    if automation not in AUTOMATION: raise ValueError(automation)
    rng = Random(411)
    nodes = 16 if quick else 128
    automated = automation == 'all'

    def points():
        return lane(rng, nodes, beats)

    def number(value):
        return Param(value, points=points() if automated else ())

    model_size = 8 if quick else 16
    lorenz = scene('lorenz').shapes[0]
    bases = (Shape(), scene('parametric').shapes[0],
             replace(lorenz, recurrence=replace(lorenz.recurrence, steps=128 if quick else 512)),
             Shape(solid='Text', text='Q1'), Shape(solid=MODEL_OBJECT, model=dense_model(model_size, model_size)),
             scene('builder_curve').shapes[0])
    shapes = []
    for i in range(6 if quick else 24):
        base = bases[i % len(bases)]
        scale = new_transform(SCALE)
        for axis in 'xyz':
            scale = scale.with_param(axis, number(1.))
        stack = (scale, new_transform(SPIN).with_param('y', number(.15)),
                 new_transform(MAP).with_param('x', Param(0., 'x+warp*sin(2*y)')),
                 new_transform(NOISE).with_param('intensity', number(.025)),
                 new_transform(PERSPECTIVE).with_param('distance', number(4.)))
        start, trace = number(.25), number(.85)
        if automation == 'path':
            start, trace = Param(.25, '.25+.1*sin(t)'), Param(.85, '.85+.1*cos(t)')
        plot = base.plot
        if automated and plot:
            plot = replace(plot, variables=tuple(replace(v, expression='', points=points()) for v in plot.variables))
        shapes.append(replace(base, uid=f'q1-{i}', position_x=(i % 6 - 2.5) * .28,
                              position_y=(i // 6 - 1.5) * .35, shape_size=.13, transforms=stack,
                              transform_variables=(Variable('warp', .18, 0., 1., points=points() if automated else ()),),
                              plot=plot, path_start=start, path_trace=trace,
                              lanes=(('shape_size', points()),) if automated else ()))
    settings = Settings(shapes=tuple(shapes), source='Single tone', tone_frequency=55.)
    gains = tuple((key, points()) for key in ('drawing_gain', 'music_gain')) if automated else ()
    tempo = tuple((round(beats * f, 3), bpm) for f, bpm in ((.08, 100.), (.25, 144.), (.5, 108.), (.83, 120.)))
    return Project(notes=(Note(0., beats, 55),), clips=(Clip(0., beats, settings, lanes=gains),),
                   tempo_changes=tempo if automated else ())


def audio_project(folder, quick):
    """Eight audio tracks (4 for --quick) with 2-3 effects each, every effect with one automated
    parameter, an automated pan lane per track, and three consecutive 16 s clips per track with
    their own gain lanes. One generated 44.1 kHz stereo WAV per track is written into folder."""
    import soundfile
    from phosphor.engine.audio_clips import AudioTrack, audio_clip_from_file
    from phosphor.engine.track_effects import new_effect, with_lane
    rng = Random(412)
    tracks, clips = [], []
    parameters = (('filter', 'cutoff'), ('saturate', 'drive'), ('delay', 'mix'),
                  ('compressor', 'threshold'), ('bitcrush', 'bits'))
    nodes = 16 if quick else 128
    t = np.arange(16 * 44100) / 44100
    for i in range(4 if quick else 8):
        effects = []
        for j in range(2 + i % 2):
            kind, field = parameters[(i + j) % len(parameters)]
            effects.append(with_lane(new_effect(kind), field, lane(rng, nodes, 96.)))
        tracks.append(AudioTrack(f'Q1 track {i+1}', pan_points=lane(rng, nodes, 96.), effects=tuple(effects)))
        path = Path(folder) / f'q1-track-{i}.wav'
        frequency = 88. + i * 17.
        soundfile.write(path, .06 * np.column_stack((np.sin(2*np.pi*frequency*t),
                                                     np.cos(2*np.pi*(frequency+11)*t))), 44100)
        source = audio_clip_from_file(path, 0.)
        for start in (0., 32., 64.):  # Clip starts in beats (16 s at 120 BPM), gain lanes in local seconds.
            clips.append(replace(source, start=start, track=i, gain_points=lane(rng, nodes, 16.)))
    return Project(clips=(Clip(0., 96., Settings()),), audio_tracks=tuple(tracks),
                   audio_clips=tuple(sorted(clips, key=lambda c: (c.start, c.track))))
