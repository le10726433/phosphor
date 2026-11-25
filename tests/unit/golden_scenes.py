"""Small, deterministic audio snapshots shared by the CLI and regression tests.

No Qt or audio device imports. Scene factories are lazy so each test builds only
its own scene. The window starts and sample rate are part of the golden format.
"""
from dataclasses import replace
from functools import lru_cache

import numpy as np

from phosphor.engine.signal_engine import Settings, Shape, render
from phosphor.engine.transforms import new_transform, SPIN, PERSPECTIVE, MOTION_PATH, NOISE, Param, NoiseOptions, resting_tilt_step
from phosphor.engine.model_object import MODEL_OBJECT, model_from_obj
from phosphor.engine.function_plot import PRESETS as PLOTS, Variable, PARAMETRIC_OBJECT
from phosphor.engine.recurrence import PRESETS as RECURRENCES, RECURRENCE_OBJECT
from phosphor.engine.custom_shape import Drawing, Curve, CUSTOM_OBJECT
from phosphor.engine.route_plan import SHORTEST_PATH
from phosphor.engine.composition import Project, Clip, Note, render_song
from tests.fixtures.dense_mesh import dense_mesh_obj
from phosphor.engine.switch_sweep import DEFAULT_SWEEP_MS

RATE = 48000
WINDOW_STARTS = tuple(k * (4. - 128 / RATE) / 31 for k in range(32))
SCENES = ('cube', 'dense_model', 'text', 'parametric', 'lorenz',
          'lorenz_dynamic', 'oscillator', 'travel', 'builder_curve', 'composer',
          'chord_triad', 'chord_glide', 'chord_steal', 'animated_model', 'audio_clip', 'velocity_chords', 'velocity_line', 'pink_noise', 'wobble', 'automated_mute')


def spin():
    return new_transform(SPIN).with_param('y', Param(.25))


def dynamic_lorenz():
    return replace(RECURRENCES['Lorenz attractor'],
                   y='y+0.01*(x*(r-(z*30+25))-y)',
                   variables=(Variable('r', 28., 0., 60., expression='28+6*sin(t)'),))


@lru_cache(maxsize=4)
def dense_model(rings=24, segments=24):
    return model_from_obj(dense_mesh_obj(rings=rings, segments=segments), 'Dense benchmark')[0]


# Machine settings per scene (signal preferences); the rest render with Settings().
MACHINES = {'automated_mute': Settings(switch_sweep_ms=DEFAULT_SWEEP_MS)}


def scene(name):
    if name == 'automated_mute':
        # Mute/solo M3 with the default sweep. Switch beats are chosen so that snapshot windows fall
        # inside sweeps: the letter goes off at frame 31 (window 4) and back on at frame 54 (window
        # 7); the Spin step goes off at frame 62 (window 8) and on at frame 116 (window 15); the
        # first clip ends into a muted clip during window 23, and the last clip starts during 26.
        on = (('on', ((0., 1., 'hold'), (1.032, 0., 'hold'), (1.799, 1., 'hold'))),)
        spin_step = replace(spin(), on=Param(1., '', ((0., 1., 'hold'), (2.065, 0., 'hold'), (3.865, 1., 'hold'))))
        shapes = (Shape(position_x=-.4, shape_size=.4, transforms=(resting_tilt_step(), spin_step)),
                  Shape(solid='Text', text='A', position_x=.4, shape_size=.4, lanes=on))
        cut, back = 5.93553, 6.7012
        return Project(notes=(Note(0., 8., 58),), clips=(
            Clip(0., cut, Settings(shapes=shapes), 'drawing'),
            Clip(cut, back - cut, Settings(shapes=shapes), 'drawing', muted=True),
            Clip(back, 8. - back, Settings(shapes=shapes), 'drawing')))
    if name in ('pink_noise', 'wobble'):
        wobble = name == 'wobble'
        noise = replace(new_transform(NOISE), noise=NoiseOptions('Pink', 'Wobble' if wobble else 'Sample'))
        noise = noise.with_param('intensity', Param(.1 if wobble else .05)).with_param('speed', Param(8.)).with_param('detail', Param(3.))
        return Settings(shapes=(Shape(transforms=(resting_tilt_step(), spin(), noise)),))
    if name == 'velocity_chords':
        return Project(notes=tuple(Note(0., 4., p, v) for p, v in zip((60, 64, 67), (.3, .65, 1.))) +
                       (Note(4., 4., 62, .5, glide=1.), Note(4., 4., 69, .9)),
                       clips=(Clip(0., 8., replace(scene('cube'), music_gain=.3), 'mixed'),), chords=True, velocity=True)
    if name == 'velocity_line':
        return Project(notes=(Note(0., 2., 60, .25), Note(2., 2., 64, .5, glide=.5),
                              Note(4., 2., 67, .75), Note(6., 2., 72, 1.)),
                       clips=(Clip(0., 8., replace(scene('text'), music_gain=.3), 'mixed'),),
                       tempo_changes=((3., 90.),), velocity=True)
    if name == 'audio_clip':
        # A note makes the scene draw, so the clips are mixed over a moving figure.
        return Project(notes=(Note(0., 8., 57),), clips=(Clip(0., 8., replace(scene('cube'), drawing_gain=.5), 'drawing'),),
                       audio_clips=audio_media())
    if name == 'animated_model':
        return Settings(shapes=(Shape(solid=MODEL_OBJECT, model=animated_model()),))
    if name == 'cube':
        return Settings(shapes=(Shape(transforms=(resting_tilt_step(), spin())),))
    if name == 'dense_model':
        return Settings(shapes=(Shape(solid=MODEL_OBJECT, model=dense_model(), transforms=(
            resting_tilt_step(), spin(), new_transform(PERSPECTIVE), new_transform(MOTION_PATH), new_transform(NOISE))),))
    if name == 'text':
        return Settings(shapes=(Shape(solid='Text', text='HI', path_start=Param(.17), path_trace=Param(.43)),))
    if name == 'parametric':
        plot = replace(PLOTS['Lissajous 3:2'], variables=(
            Variable('phase', 1., 0., 6.3, expression='1+0.5*sin(t)'),))
        return Settings(shapes=(Shape(solid=PARAMETRIC_OBJECT, plot=plot),))
    if name in ('lorenz', 'lorenz_dynamic', 'oscillator'):
        plot = {'lorenz': RECURRENCES['Lorenz attractor'],
                'lorenz_dynamic': dynamic_lorenz(),
                'oscillator': RECURRENCES['Second-order oscillator']}[name]
        return Settings(shapes=(Shape(solid=RECURRENCE_OBJECT, recurrence=plot),))
    if name == 'travel':
        return Settings(shapes=(Shape(position_x=-.4, shape_size=.4, transforms=(resting_tilt_step(),)),
                                Shape(solid='Text', text='A', position_x=.4, shape_size=.4)),
                        travel_mode='Faster travel', travel_speed=12., route_mode=SHORTEST_PATH,
                        corner_dwell_us=40., shape_redraw_count=2)
    if name == 'builder_curve':
        drawing = Drawing('Arc', ((0, -1., 0.), (1, 0., 1.), (2, 1., 0.)),
                          (), (Curve('Arc', (0, 1, 2)),))
        return Settings(shapes=(Shape(solid=CUSTOM_OBJECT, custom=drawing),))
    if name == 'composer':
        return Project(notes=(Note(0., 4., 57), Note(4., 8., 64, glide=1.)),
                       clips=(Clip(0., 4., replace(scene('cube'), music_gain=.2), 'mixed', lanes=(
                           ('drawing_gain', ((0., .3, 'linear'), (4., .8, 'linear'))),)),
                              Clip(4., 8., scene('parametric'), 'drawing', local_time=True)),
                       tempo_changes=((3., 100.),))
    if name == 'chord_triad':
        return Project(notes=(Note(0., 6., 60), Note(1., 5., 64), Note(2., 4., 67)),
                       clips=(Clip(0., 8., replace(scene('cube'), music_gain=.3), 'mixed'),), chords=True)
    if name == 'chord_glide':
        return Project(notes=tuple(Note(0., 4., p) for p in (60, 64, 67)) +
                       tuple(Note(4., 4., p, glide=1.) for p in (57, 60, 64)) + (Note(4., 4., 69),),
                       clips=(Clip(0., 8., scene('text'), 'mixed'),),
                       tempo_changes=((6., 100.),), chords=True)
    if name == 'chord_steal':
        return Project(notes=tuple(Note(0., 6., p) for p in (48, 52, 55, 60, 64, 67, 72, 76)) +
                       (Note(2., 4., 79),), clips=(Clip(0., 8., scene('travel'), 'drawing'),), chords=True)
    raise KeyError(name)


@lru_cache(maxsize=1)
def audio_media():
    import atexit
    import shutil
    import tempfile
    from pathlib import Path
    import soundfile
    from phosphor.engine.audio_clips import audio_clip_from_file
    folder = Path(tempfile.mkdtemp(prefix='phosphor-golden-audio-'))
    atexit.register(shutil.rmtree, folder)
    t = np.arange(72000) / 48000
    stereo = folder / 'stereo.wav'
    soundfile.write(stereo, np.column_stack((.3*np.sin(2*np.pi*220*t), .3*np.sin(2*np.pi*330*t))),
                    48000, subtype='FLOAT')
    t = np.arange(88200) / 44100
    mono = folder / 'mono.wav'
    soundfile.write(mono, .25*np.sin(2*np.pi*440*t), 44100, subtype='FLOAT')
    return (replace(audio_clip_from_file(stereo, .5), offset=.1, gain=.8, fade_in=.2, fade_out=.3),
            replace(audio_clip_from_file(mono, 3.), routing='x', gain=.5))


@lru_cache(maxsize=1)
def animated_model():
    # Test-only: "low poly character walk cycle" by crisix8819, CC-BY-4.0.
    from pathlib import Path
    from phosphor.engine.mesh_formats import read_mesh
    source = read_mesh(Path(__file__).parents[1] / 'fixtures/low_poly_character_walk_cycle/scene.gltf').animation_source
    return source.model(0, simplify_to=300, duration=2.)[0]


def snapshot(name):
    """Keep windows separate, exercising the engine's arbitrary-block contract."""
    definition = scene(name)
    drawing, output = [], []
    for start in WINDOW_STARTS:
        times = start + np.arange(128, dtype=np.float64) / RATE
        if isinstance(definition, Project):
            # render_song exposes the selected song signal only. A drawing-mode
            # project gives the corresponding drawing snapshot through the same API.
            machine = MACHINES.get(name, Settings())
            d = render_song(times, replace(definition, clips=tuple(
                replace(c, mode='drawing') for c in definition.clips)), machine)
            o = render_song(times, definition, machine)
        else:
            d, _, o = render(times, definition)
        drawing.append(np.asarray(d, dtype=np.float64))
        output.append(np.asarray(o, dtype=np.float64))
    return dict(drawing=np.stack(drawing), output=np.stack(output))


def assert_snapshot(name, actual, golden):
    # 1e-9 allows last-digit libm differences (Windows NumPy 1.26 vs Mac
    # NumPy 2.5) and the planned Numba path; real regressions are >= 1e-6.
    for channel, values in actual.items():
        expected = golden[f'{name}__{channel}']
        difference = float(np.max(np.abs(values - expected)))
        np.testing.assert_allclose(values, expected, rtol=0, atol=1e-9,
                                   err_msg=f'{name}/{channel}: max abs difference {difference:.12g}')
