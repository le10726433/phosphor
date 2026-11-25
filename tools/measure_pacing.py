"""Native frame pacing in the running editor (performance Phase 5, audit D7 and B11).

Opens the real editor window with the app's graphics setup (D3D11 on Windows, Metal on
macOS, 4x MSAA), lets the housekeeping timer and vsync drive frames as in normal use, and for
each scenario records a stopped phase and a playing phase with the same report the app's
--diagnose-playback uses (playback_diagnostics.PlaybackDiagnostics):
- presentation: swaps of the active view (frames that reached the screen; B11);
- preparation_starts: Window.display_frame starts;
- sample preparation and geometry upload times (CPU only, no GPU completion);
- audio synthesis, write waits and underruns.

Playing phases open the default audio output device and play the scene, as
--diagnose-playback does. Keep the machine otherwise idle and the window visible, unminimized
and on one screen. Every run uses fresh temporary preferences.

    python tools/measure_pacing.py --json pacing.json
    python tools/measure_pacing.py --only composer_20k,map_twist --stopped 3 --playing 5
    python tools/measure_pacing.py --only composer_20k --simulate-rate 192000 --playing 30

--simulate-rate plays through a simulated output device (phosphor.audio.simulated_output) that
consumes frames at that rate's real-time cadence instead of the default device, so rates the
interface can't open (176.4 and 192 kHz here) can be measured through the same audio thread.
"""
import atexit
import os
import tempfile

# Must precede _path, which imports phosphor and resolves storage paths.
_user = tempfile.TemporaryDirectory(prefix='phosphor-pacing-user-')
os.environ['PHOSPHOR_USER_DIR'] = _user.name
atexit.register(_user.cleanup)
import _path  # noqa: E402,F401

import argparse
from dataclasses import replace
import json
from pathlib import Path
import platform
import subprocess
import sys
from types import SimpleNamespace

import numpy as np

SCENARIOS = ('cube', 'dense_model', 'muted_dense_model', 'animated_model', 'noise_wobble', 'map_twist',
             'composer_2k', 'composer_20k', 'audio_clips', 'audio_tracks', 'audio_effects', 'live_midi',
             'q1_dense_automated', 'q1_audio_tracks')


def big_song(count, chords=True):
    """The bench's song (bench_performance.big_song): four-voice chords every half beat."""
    from phosphor.engine.composition import Project, Note, Clip
    from tests.unit.golden_scenes import scene
    notes = []
    for k in range(count):
        chord, voice = divmod(k, 4)
        notes.append(Note(chord * .5, .75, 36 + (chord * 5 + voice * 4) % 60, .5 + .5 * (k % 3) / 2))
    end = max(n.end for n in notes)
    return Project(notes=tuple(notes), clips=(Clip(0., end + 4., scene('cube')),), chords=chords, velocity=True)


class Pacing:
    def __init__(self, args):
        self.args = args
        self.results = {}
        from PySide6.QtGui import QSurfaceFormat
        from PySide6.QtWidgets import QApplication
        from phosphor.editor import qt_app as q
        self.q = q
        q.configure_graphics()  # The order main() uses.
        surface = QSurfaceFormat(); surface.setSamples(4); QSurfaceFormat.setDefaultFormat(surface)
        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        self.app.setQuitOnLastWindowClosed(False)
        q.prepare_app_fonts()
        self.media = tempfile.TemporaryDirectory(prefix='phosphor-pacing-media-')

    def loop(self, seconds):
        """A local QEventLoop, never app.exec(): quitting would release the editor's views."""
        from PySide6.QtCore import QEventLoop, QTimer
        loop = QEventLoop(); timer = QTimer(); timer.setSingleShot(True)
        timer.timeout.connect(loop.quit); timer.start(round(seconds * 1000))
        try: loop.exec()
        finally: timer.stop()

    def model(self, rings, segments):
        from phosphor.engine.model_object import model_from_obj
        from tests.fixtures.dense_mesh import dense_mesh_obj
        return model_from_obj(dense_mesh_obj(rings, segments), f'Dense {rings}')[0]

    def sandbox(self, w, shapes):
        w.scene.settings = replace(w.scene.settings, shapes=shapes, source='Single tone', tone_frequency=55.)
        w.sync()

    def setup(self, name, w):
        """Puts the window in the scenario's state; returns a short description."""
        from phosphor.engine.signal_engine import Shape
        from phosphor.engine.model_object import MODEL_OBJECT
        from phosphor.engine.transforms import new_transform, PERSPECTIVE, NOISE, MAP, Param, NoiseOptions, with_map_preset
        from tests.unit.golden_scenes import spin, scene
        q = self.q
        if name == 'cube':
            self.sandbox(w, scene('cube').shapes)
            return 'golden cube scene (Sandbox), 55 Hz tone'
        if name in ('dense_model', 'muted_dense_model', 'map_twist'):
            model = self.model(120, 120)
            stack = (spin(), new_transform(PERSPECTIVE))
            if name == 'map_twist': stack = (spin(), with_map_preset(new_transform(MAP), 'Twist'))
            self.sandbox(w, (Shape(solid=MODEL_OBJECT, model=model, transforms=stack, muted=name == 'muted_dense_model'),)
                         + ((scene('cube').shapes[0],) if name == 'muted_dense_model' else ()))
            what = {'dense_model': 'Spin + Perspective', 'muted_dense_model': 'muted, Spin + Perspective, plus an audible cube',
                    'map_twist': 'Spin + Map (Twist preset)'}[name]
            return f'dense model 120x120 ({len(model.edges)} edges), {what} (Sandbox)'
        if name == 'animated_model':
            settings = scene('animated_model')
            self.sandbox(w, settings.shapes)
            model = settings.shapes[0].model
            return (f'animated glTF walk cycle (golden animated_model; {len(model.edges)} edges, '
                    f'{model.animation.count} frames) (Sandbox)')
        if name == 'noise_wobble':
            model = self.model(60, 60)
            step = replace(new_transform(NOISE), noise=NoiseOptions('Pink', 'Wobble')).with_param('intensity', Param(.05))
            self.sandbox(w, (Shape(solid=MODEL_OBJECT, model=model, transforms=(spin(), step)),))
            return f'dense model 60x60 ({len(model.edges)} edges), Spin + Noise (Pink, Wobble, 0.05) (Sandbox)'
        if name == 'live_midi':
            from phosphor.audio.midi_synth import MIDI_SOURCE
            self.sandbox(w, scene('cube').shapes)
            w.scene.settings = replace(w.scene.settings, source=MIDI_SOURCE); w.sync()
            return 'Live MIDI on the golden cube, a held four-note chord (Sandbox; needs --simulate-rate or a device)'
        if name in ('q1_dense_automated', 'q1_audio_tracks'):
            # Q1 stress cases (planning/review-q1.md), generated by tools/q1_workloads.py at full size.
            import q1_workloads as q1
            if name == 'q1_dense_automated':
                project = q1.dense_song(False, q1.complex_project(False, 'all', q1.dense_beats(False)))
            else:
                project = q1.audio_project(self.media.name, False)
            w.set_workspace(q.COMPOSER)
            w.composer_panel.cycle.setChecked(False)  # No cycle audition while stopped.
            w.load_project(project, None)
            w.seek_song(10.)
            from phosphor.engine import audio_clips
            if project.audio_clips and hasattr(audio_clips, 'wait_idle'): audio_clips.wait_idle(120.)
            if name == 'q1_dense_automated':
                return (f'Composer, {len(project.notes)} notes (3-4 note chords, velocity) over the Q1 complex scene '
                        f'({len(project.clips[0].settings.shapes)} objects, five-step stacks, every variable, transform '
                        'number, Path Start/Trace and gain on 128-node lanes, four tempo changes), playhead 10 s')
            return (f'Composer, Q1 audio: {len(project.audio_tracks)} tracks, '
                    f'{sum(len(t.effects) for t in project.audio_tracks)} automated effects, pan and gain lanes, '
                    f'{len(project.audio_clips)} 16 s WAV clips, no scene, playhead 10 s')
        if name in ('composer_2k', 'composer_20k', 'audio_clips', 'audio_tracks', 'audio_effects'):
            count = 20000 if name == 'composer_20k' else 2000
            project = big_song(count)
            if name == 'audio_clips':
                project = replace(project, audio_clips=self.audio_clips())
            elif name in ('audio_tracks', 'audio_effects'):
                from phosphor.engine.audio_clips import AudioTrack
                tracks = tuple(AudioTrack(n, g) for n, g in (('Drums', 1.), ('Bass', .8), ('Pad', .5), ('Lead', 1.2)))
                if name == 'audio_effects':  # AW3: two effects per track, one filter cutoff automated over the song.
                    from phosphor.engine.track_effects import new_effect, with_lane
                    sweep = with_lane(new_effect('filter'), 'cutoff', ((0., .2, 'linear'), (60., .9, 'linear')))
                    chains = ((sweep, new_effect('compressor')), (new_effect('filter'), new_effect('saturate')),
                              (new_effect('delay'), new_effect('filter')), (new_effect('bitcrush'), new_effect('delay')))
                    tracks = tuple(replace(t, effects=chain) for t, chain in zip(tracks, chains))
                clips = tuple(replace(c, track=i % 4) for i, c in enumerate(self.audio_clips(4)))
                project = replace(project, audio_tracks=tracks, audio_clips=clips)
            w.set_workspace(q.COMPOSER)
            w.composer_panel.cycle.setChecked(False)  # No cycle audition while stopped.
            w.load_project(project, None)
            w.seek_song(10.)
            from phosphor.engine import audio_clips
            if project.audio_clips and hasattr(audio_clips, 'wait_idle'): audio_clips.wait_idle(120.)
            if name == 'audio_effects':
                return ('Composer, 2000 notes, the audio_tracks scenario with two effects on each track (automated filter + '
                        'compressor, filter + saturate, delay + filter, bitcrush + delay), playhead 10 s')
            if name == 'audio_tracks':
                return ('Composer, 2000 notes in one cube clip, 4 audio tracks (gains 1, .8, .5, 1.2) '
                        'with 4 sounding 60 s WAV clips + 17 short inactive clips, playhead 10 s')
            extra = ', 3 sounding 60 s WAV clips + 17 short inactive clips' if name == 'audio_clips' else ''
            return f'Composer, {count} notes (chords, velocity) in one cube clip{extra}, playhead 10 s'
        raise ValueError(name)

    def audio_clips(self, sounding=3):
        """Long 60 s stereo WAVs (44.1 kHz: playback resamples) sounding from 0 s, and 17 short
        48 kHz clips far after the playhead (P9's inactive-file fixture)."""
        import soundfile
        from phosphor.engine.audio_clips import audio_clip_from_file
        folder = Path(self.media.name)
        clips = []
        t = np.arange(60 * 44100) / 44100
        for k in range(sounding):
            path = folder / f'long{k}.wav'
            if not path.exists():
                soundfile.write(path, np.column_stack((np.sin(2*np.pi*(110+55*k)*t), np.sin(2*np.pi*(165+55*k)*t))) * .2, 44100)
            clips.append(audio_clip_from_file(path, 0.))
        for k in range(17):
            path = folder / f'short{k}.wav'
            if not path.exists(): soundfile.write(path, np.full((4800, 2), .1 * (k + 1) / 17), 48000)
            clips.append(replace(audio_clip_from_file(path, 0.), start=4000. + 4 * k))
        return tuple(clips)

    def run(self, name):
        from phosphor.audio.playback_diagnostics import PlaybackDiagnostics, pacing
        q = self.q
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=q.SANDBOX))
        if self.args.simulate_rate:
            from phosphor.audio.simulated_output import SimulatedDevice
            w.audio.device = SimulatedDevice(self.args.simulate_rate)
        if self.args.rate: w.audio.requested_rate = self.args.rate  # The output-rate preference.
        q.PACE_FOR_AUDIO = not self.args.no_pacing
        if self.args.block_ms:
            from phosphor.audio import audio_preview
            audio_preview.BLOCK_SECONDS = self.args.block_ms / 1000
        try:
            if self.args.display_rate:  # 'Match monitor' (the default) or '60 Hz'.
                w.apply_preferences(dict(w.preferences, display_rate=self.args.display_rate))
            if self.args.glow_size is not None:  # Bloom cost (UI2): Appearance › Beam › Glow size, glow on.
                w.apply_preferences(dict(w.preferences, crt_glow=True, glow_size=self.args.glow_size))
            w.resize(*self.args.size); w.show(); w.raise_(); w.activateWindow()
            self.loop(1.)
            w.backend_info()
            description = self.setup(name, w)
            q.settle_garbage()
            self.loop(self.args.warm)
            diagnostics = PlaybackDiagnostics(w)
            readouts = {}
            def phase(label, seconds):
                deferred = w.deferred_frames
                diagnostics.begin(label); self.loop(seconds)
                pace = pacing(w.swap_times, 'swaps', skip=0)
                prep = pacing(w.frame_times, 'display_frame starts', skip=0)
                diagnostics.end()
                data = diagnostics.phases[label]
                data['swap_pacing'], data['preparation_pacing'] = pace, prep
                # Blocking writes may report no underruns while audio falls behind: compare frames to wall time.
                data['audio_realtime_ratio'] = (data['audio_frames'] / (data['seconds'] * w.audio.sample_rate)
                                                if w.audio.running else None)
                readouts[label] = w.backend_label.text()  # The status-bar readout (last 1 s of new frames presented).
                data['content_swap_pacing'] = pacing(w.content_swap_times, 'first swap after each display_frame', skip=0)
                data['deferred_frames'] = w.deferred_frames - deferred  # Waits for a starving audio stream.
            phase('stopped', self.args.stopped)
            if name == 'live_midi':
                # As connecting a MIDI input does, without a port: start Live MIDI audio, hold a chord.
                import mido
                w.audio.start(replace(w.output_settings(), motion_time_offset=0.), w.scene.mode)
                for note in (48, 55, 64, 71): w.audio.midi.receive(mido.Message('note_on', note=note, velocity=100))
            else:
                w.play()
            self.loop(self.args.warm)
            if not w.audio.running: raise RuntimeError('Playback did not start: ' + (w.audio.error or 'unknown'))
            phase('playing', self.args.playing)
            sample_rate, block = w.audio.sample_rate, w.audio.block_frames
            output_mode = w.audio.output_mode or ('simulated' if self.args.simulate_rate else 'default device')
            w.play() if w.workspace == q.COMPOSER else w.audio.stop()
            w.shutdown_audio()
            report = diagnostics.report()
            report.pop('preferences')
            report.update(description=description, backend=w.backend, readouts=readouts,
                          device_sample_rate=sample_rate, device_block_frames=block, output_mode=output_mode,
                          simulated=bool(self.args.simulate_rate), pacing_for_audio=not self.args.no_pacing,
                          rate_notice=w.audio.rate_notice,
                          display_rate=w.preferences['display_rate'], phosphor=w.preferences['phosphor'],
                          glow_size=w.preferences['glow_size'], crt_glow=w.preferences['crt_glow'],
                          workspace=w.workspace)
            self.print(name, report)
            return report
        finally:
            w.shutdown_audio(); w.timer.stop()
            w.release_views(); w.force_close = True; w.close()
            self.app.processEvents()
            from phosphor.engine import audio_clips
            audio_clips.clear_decoded()

    @staticmethod
    def print(name, report):
        for label, data in report['phases'].items():
            swaps = data['swap_pacing']; frames = data['presentation']['intervals_ms']
            prep = data['sample_preparation_ms']
            synth = data['audio_synthesis_ms']; ratio = data['audio_realtime_ratio']
            audio = (f"synthesis median {synth.get('median', 0):6.2f} p95 {synth.get('p95', 0):6.2f} ms, "
                     f"real time {ratio:.2f} | " if ratio is not None else '')
            print(f"{name:18s} {report['device_sample_rate']/1000:5.1f}k/{report['device_block_frames']:<4d} {label:8s} swaps {swaps.get('fps', 0):6.1f} fps  interval median "
                  f"{frames.get('median', 0):6.2f} p95 {frames.get('p95', 0):6.2f} p99 {frames.get('p99', 0):6.2f} "
                  f"max {frames.get('maximum', 0):7.2f} ms | prep starts {data['preparation_pacing'].get('fps', 0):6.1f} fps | "
                  f"prep median {prep.get('median', 0):6.2f} p95 {prep.get('p95', 0):6.2f} ms | "
                  f"{audio}underruns {data['audio_underruns']} | deferred {data.get('deferred_frames', 0)} | "
                  f"readout '{report['readouts'][label]}'", flush=True)


def header(app):
    from PySide6.QtGui import QGuiApplication
    from phosphor.storage.build_info import VERSION
    import PySide6
    try: commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True,
                                 cwd=Path(__file__).parent).stdout.strip()
    except OSError: commit = None
    screen = QGuiApplication.primaryScreen()
    return dict(version=VERSION, commit=commit, platform=platform.platform(), machine=platform.machine(),
                processor=platform.processor(), cpu_count=os.cpu_count(), python=platform.python_version(),
                numpy=np.__version__, pyside6=PySide6.__version__,
                screen=dict(size=[screen.size().width(), screen.size().height()], dpr=screen.devicePixelRatio(),
                            refresh_hz=screen.refreshRate(), count=len(QGuiApplication.screens())))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--only', help=','.join(SCENARIOS))
    parser.add_argument('--stopped', type=float, default=4., help='seconds measured while stopped')
    parser.add_argument('--playing', type=float, default=6., help='seconds measured while playing')
    parser.add_argument('--warm', type=float, default=1.5, help='seconds before each phase')
    parser.add_argument('--size', type=int, nargs=2, default=(1400, 900), metavar=('W', 'H'))
    parser.add_argument('--display-rate', choices=('Match monitor', '60 Hz'),
                        help='the display_rate preference (default: the app default, Match monitor)')
    parser.add_argument('--simulate-rate', type=int, metavar='HZ',
                        help='play through a simulated output device at this rate (e.g. 176400, 192000)')
    parser.add_argument('--rate', type=int, metavar='HZ',
                        help='the output-rate signal preference (default: follow the device)')
    parser.add_argument('--no-pacing', action='store_true',
                        help='turn off display pacing for audio (qt_app.PACE_FOR_AUDIO), to compare')
    parser.add_argument('--glow-size', type=float, help='Glow size preference with GPU trace glow on (default: the app default)')
    parser.add_argument('--block-ms', type=float, help='playback block length in ms (default: the app value, 10), to compare')
    parser.add_argument('--repeat', type=int, default=1, help='runs per scenario (results keyed name, name#2…)')
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError: pass
    names = args.only.split(',') if args.only else list(SCENARIOS)
    if set(names) - set(SCENARIOS): parser.error('Unknown scenarios: ' + ', '.join(sorted(set(names) - set(SCENARIOS))))
    pacing = Pacing(args)
    info = header(pacing.app)
    print('  '.join(f'{k}={v}' for k, v in info.items()), flush=True)
    for name in names:
        for k in range(args.repeat):
            key = name if k == 0 else f'{name}#{k + 1}'
            try:
                pacing.results[key] = pacing.run(name)
            except Exception as error:
                pacing.results[key] = dict(error=' '.join(str(error).splitlines()))
                print(f'{key:18s} ERROR {error}', flush=True)
    first = next((r for r in pacing.results.values() if 'backend' in r), {})
    info.update(backend=first.get('backend'), viewport_size=first.get('viewport_size'),
                device_pixel_ratio=first.get('device_pixel_ratio'), window_size=first.get('window_size'))
    if args.json:
        args.json.write_text(json.dumps(dict(header=info, **pacing.results), indent=2) + '\n', encoding='utf-8')
    pacing.media.cleanup()


if __name__ == '__main__':
    main()
