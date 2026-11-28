"""Capture the app's control surfaces or compare two captures pixel by pixel.

    python tools/style_snapshots.py --out DIR [--palette NAME]   # at the current QT_SCALE_FACTOR
    python tools/style_snapshots.py --out DIR --style Modern --mode Light
    python tools/style_snapshots.py --compare BEFORE AFTER       # pixel comparison of two folders

Each image is a QWidget.grab() of one surface: every side panel and each of its tabs, the
application bar, the Composer panel (a project with notes, an audio clip and a track with
effects), both Library pages and the Preferences dialog's tabs. The GPU
monitor is not grabbed (the style never changes the scope picture). Run natively from
oscilloscope_music/; set QT_SCALE_FACTOR=1.5 for the larger scale. Choose new output and fixture
folders for the first run. Subsequent runs reuse marked folders and replace top-level output PNGs.
"""
if __package__:
    from . import _path
else:
    import _path
import argparse
import json
import math
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


MARKER = '.phosphor-style-snapshots'
SURFACES = frozenset({
    'app_bar', 'composer', 'browser', 'track_effects',
    'panel_scene', 'panel_scene_0_0', 'panel_scene_object', 'panel_scene_transform',
    'panel_mix', 'panel_signal_output', 'panel_appearance',
    *(f'panel_appearance_0_{i}' for i in range(5)),
    *(f'preferences_{i}' for i in range(3)),
    *(f'preferences_0_0_{i}' for i in range(4)),
    *(f'preferences_2_0_{i}' for i in range(2)),
    'sandbox_app_bar', 'sandbox_input', 'sandbox_mix',
})


def marked_folder(path):
    path = Path(path)
    marker = path / MARKER
    try:
        path.mkdir(parents=True)
    except FileExistsError:
        if (path.is_symlink() or path.is_junction() or not path.is_dir()
                or marker.is_symlink() or not marker.is_file()):
            raise ValueError(f'Refusing unmarked or linked folder: {path}')
    else:
        marker.touch(exist_ok=False)
    return path


def check_target(path):
    # Never follow links from a marked directory into unrelated user files.
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
        raise ValueError(f'Unsafe snapshot target: {path}')


def prepare_capture(out, folder):
    out, folder = Path(out), Path(folder)
    if out.resolve().is_relative_to(folder.resolve()) or folder.resolve().is_relative_to(out.resolve()):
        raise ValueError('Output and fixture folders must be separate')
    marked_folder(folder)
    marked_folder(out)
    loops = marked_folder(folder / 'Loops')
    manifest_path = out / 'snapshots.json'
    for target in (folder / 'tone.wav', loops / 'beat.wav', manifest_path):
        check_target(target)
    # An interrupted run must never pass comparison with an older manifest.
    manifest_path.write_text('{}', encoding='utf-8')
    for png in out.glob('*.png'):
        if png.is_file() or png.is_symlink():
            png.unlink()
    return out, folder


def manifest(folder):
    if not folder.is_dir():
        raise ValueError(f'Missing snapshot folder: {folder}')
    data = json.loads((folder / 'snapshots.json').read_text(encoding='utf-8'))
    filenames = {p.name for p in folder.iterdir() if p.suffix.lower() == '.png' and p.is_file()}
    expected = {f'{surface}.png' for surface in SURFACES}
    if filenames != expected:
        raise ValueError(f'{folder}: missing {sorted(expected - filenames)}, '
                         f'unexpected {sorted(filenames - expected)}')
    if not isinstance(data, dict) or data.get('surfaces') != sorted(SURFACES):
        raise ValueError(f'{folder}: manifest does not match the PNG files')
    scale = data.get('scale')
    if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
        raise ValueError(f'{folder}: invalid snapshot scale {scale!r}')
    # Manifests from ST1 predate style/mode and always describe Classic, which has no mode.
    data.setdefault('style', 'Classic')
    if data['style'] in ('Classic', 'Vintage'):
        data['mode'] = data['requested_mode'] = ''
    else:
        # mode is the resolved Dark or Light; requested_mode what was asked for (System too).
        if data.get('mode') not in ('Dark', 'Light'):
            raise ValueError(f'{folder}: unresolved snapshot mode {data.get("mode")!r}')
        data.setdefault('requested_mode', data['mode'])
    return data


def compared(data):
    """What two captures must share: the resolved mode, not the requested one."""
    return {key: value for key, value in data.items() if key != 'requested_mode'}


def load_image(path):
    from PySide6.QtGui import QImage
    return QImage(str(path))


def compare(before, after):
    before, after = Path(before), Path(after)
    try:
        if compared(manifest(before)) != compared(manifest(after)):
            raise ValueError('Snapshot manifests differ')
    except (ValueError, OSError) as error:
        print(error)
        return 1
    differ = 0
    for surface in sorted(SURFACES):
        name = surface + '.png'
        x, y = load_image(before / name), load_image(after / name)
        if x.isNull() or y.isNull():
            print(f'{name}: unreadable image')
            differ += 1
            continue
        if x.size() != y.size():
            print(f'{name}: size {x.width()}x{x.height()} -> {y.width()}x{y.height()}'); differ += 1; continue
        x = x.convertToFormat(x.Format.Format_ARGB32)
        y = y.convertToFormat(y.Format.Format_ARGB32)
        if x == y: continue
        count = sum(1 for j in range(x.height()) for i in range(x.width()) if x.pixel(i, j) != y.pixel(i, j))
        print(f'{name}: {count} pixels differ'); differ += 1
    print(f'{len(SURFACES) - differ}/{len(SURFACES)} identical')
    return 1 if differ else 0


def snapshots(out, palette, folder, style='Classic', mode='Dark'):
    import numpy as np
    import soundfile
    from PySide6.QtWidgets import QApplication
    from phosphor.editor import qt_app as q
    from phosphor.engine import audio_clips
    from phosphor.engine.audio_clips import AudioTrack
    from phosphor.engine.composition import COMPOSER, Note
    from phosphor.engine.track_effects import new_effect

    if palette not in q.PALETTES:
        raise ValueError(f'Unknown palette: {palette}')
    if style == 'Modern' and 'style_mode' not in q.DEFAULT_PREFERENCES:
        raise ValueError('This app version does not support Modern')
    out, folder = prepare_capture(out, folder)
    q.configure_graphics()
    app = QApplication([]); app.setCursorFlashTime(0)
    tone = .3 * np.sin(2 * np.pi * 220 * np.arange(24000) / 48000)
    soundfile.write(folder / 'tone.wav', np.column_stack((tone, tone)), 48000)
    soundfile.write(folder / 'Loops' / 'beat.wav', tone, 48000)

    def shot(name, widget):
        app.processEvents()
        if not widget.grab().save(str(out / f'{name}.png')):
            raise OSError(f'Failed to save {name}.png')

    def wait_for(predicate, description):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(.02)
        raise TimeoutError(f'Timed out waiting for {description}')

    prefs = dict(q.DEFAULT_PREFERENCES, palette=palette)
    if 'style' in prefs: prefs['style'] = style
    if 'style_mode' in prefs: prefs['style_mode'] = mode
    with (tempfile.TemporaryDirectory() as settings,
          patch.object(q, 'PREFERENCES_PATH', Path(settings) / 'preferences.json'),
          patch.object(q, 'SIGNAL_PREFERENCES_PATH', Path(settings) / 'signal.json'),
          patch.object(q, 'read_preferences', return_value=prefs),
          patch.object(q, 'read_signal_preferences', return_value=dict(q.DEFAULT_SIGNAL_PREFERENCES)),
          patch.object(q, 'write_workspace', lambda *args: None)):
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
        try:
            w.resize(1220, 840); w.show(); w.activateWindow(); app.processEvents(); w.timer.stop()
            clip = audio_clips.audio_clip_from_file(folder / 'tone.wav', 0.)
            track = AudioTrack('Lead', effects=(new_effect('filter'), new_effect('delay')))
            notes = tuple(Note(float(i), .5, 60 + 2 * i) for i in range(6))
            audio_clips.decoded(clip.path, clip.fingerprint, 48000, wait=False)
            wait_for(lambda: audio_clips.waveform(clip.path, clip.fingerprint) is not None, 'Composer waveform')
            w.set_project(replace(w.project, bpm=120., notes=notes, audio_tracks=(track, AudioTrack('Pad')), audio_clips=(clip,)))
            w.commit_history(); app.processEvents()
            w.app_bar.setFocus(); app.processEvents()
            shot('app_bar', w.app_bar)
            shot('composer', w.composer_panel)
            for name in q.PANELS:
                if not w.bar_buttons[name].isEnabled(): continue
                w.show_panel(name); app.processEvents()
                tabs = [t for t in w.panels[name].widget().findChildren(q.QTabWidget) if t.isVisibleTo(w)]
                shot(f'panel_{name.lower().replace(" ", "_")}', w.sidebar)
                for t, tab in enumerate(tabs):
                    for i in range(tab.count()):
                        if not tab.isTabEnabled(i): continue
                        tab.setCurrentIndex(i); app.processEvents()
                        shot(f'panel_{name.lower().replace(" ", "_")}_{t}_{i}', w.sidebar)
                    tab.setCurrentIndex(0)
            w.toggle_browser(True); w.browser.open_folder(folder)
            wait_for(lambda: {'Loops', 'tone.wav'} == {
                w.browser.list.item(i).text() for i in range(w.browser.list.count())
            }, 'browser entries Loops and tone.wav')
            w.app_bar.setFocus(); shot('browser', w.left_panel)
            w.toggle_browser(False)
            fx = w.open_track_effects(0); fx.chain.setCurrentRow(0); app.processEvents()
            w.app_bar.setFocus(); shot('track_effects', w.left_panel)
            w.toggle_browser(False)
            w.add_object('Cube'); w.show_panel(q.PANEL_SCENE); app.processEvents(); w.app_bar.setFocus()
            shot('panel_scene_object', w.sidebar)
            w.scene_sections.setCurrentIndex(1); app.processEvents(); shot('panel_scene_transform', w.sidebar)
            # Preferences: every tab, including nested tab bars.
            w.show_preferences(); dialog = w.preferences_dialog; app.processEvents()
            for i in range(w.preferences_tabs.count()):
                w.preferences_tabs.setCurrentIndex(i); app.processEvents(); shot(f'preferences_{i}', dialog)
                for t, tab in enumerate(w.preferences_tabs.widget(i).findChildren(q.QTabWidget)):
                    for j in range(tab.count()):
                        tab.setCurrentIndex(j); app.processEvents(); shot(f'preferences_{i}_{t}_{j}', dialog)
                    tab.setCurrentIndex(0)
            dialog.close(); app.processEvents()
            # Sandbox: the Input panel and the panels without Composer.
            w.set_workspace(q.SANDBOX); app.processEvents(); w.app_bar.setFocus()
            shot('sandbox_app_bar', w.app_bar)
            for name in (q.PANEL_INPUT, q.PANEL_MIX):
                w.show_panel(name); app.processEvents(); shot(f'sandbox_{name.lower()}', w.sidebar)
            names = sorted(p.stem for p in out.glob('*.png'))
            if names != sorted(SURFACES):
                raise ValueError(f'Unexpected capture surfaces: {sorted(SURFACES.symmetric_difference(names))}')
            if style in ('Classic', 'Vintage'):
                data = dict(surfaces=names, scale=w.devicePixelRatioF(), style=style, mode='', requested_mode='')
            else:
                data = dict(surfaces=names, scale=w.devicePixelRatioF(), style=style,
                            mode=getattr(w.theme, 'mode', '') or mode, requested_mode=mode)
            (out / 'snapshots.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
        finally:
            w.history_timer.stop(); w.timer.stop(); w.force_close = True
            w.shutdown_audio(); w.release_views(); w.close()
    print(f'{len(list(out.glob("*.png")))} snapshots in {out}')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--out'); parser.add_argument('--palette', default='Green monochrome')
    parser.add_argument('--style', choices=('Classic', 'Modern', 'Vintage'), default='Classic')
    parser.add_argument('--mode', choices=('Dark', 'Light', 'System'), default='Dark')
    parser.add_argument('--folder', help='A fixed folder for the browser (its path is shown)')
    parser.add_argument('--compare', nargs=2, metavar=('BEFORE', 'AFTER'))
    args = parser.parse_args()
    if args.compare:
        return compare(*args.compare)
    if not args.out: parser.error('--out or --compare')
    try:
        snapshots(args.out, args.palette, args.folder or Path(tempfile.gettempdir()) / 'phosphor-style-snapshots',
                  args.style, args.mode)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
