"""Capture the user manual's screenshots from the real app (docs/manual/images).

Run natively from oscilloscope_music/ with the displays awake:
    python tools/manual_screenshots.py            # every image
    python tools/manual_screenshots.py eq-        # only names starting with eq-

Each image comes from a real window: widgets are grabbed with QWidget.grab(), and every
GPU monitor (a QQuickWindow inside a window container, which a widget grab leaves
blank) is grabbed with grabWindow() and painted at its place, so other windows covering
the app do not matter. Audio never starts: the stopped preview animates the scenes.
User files are not touched: workspace and pop-out state go to a temporary folder, and
the preferences used are the defaults with a fixed palette.

Equation images (eq-<id>.png) come from the hidden example specs in the manual's
chapters (<!-- example: {...} --> with "kind": "equation").
"""
import _path  # noqa: F401  (makes the phosphor package importable)
import json
import re
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QSurfaceFormat
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from phosphor.editor import qt_app as q
from phosphor.editor import composer_view as cv
from phosphor.engine.automation import gain_at  # noqa: F401  (keeps lane helpers importable)
from phosphor.engine.composition import COMPOSER, SANDBOX, Clip, Note, Project
from phosphor.engine.custom_shape import Drawing, Curve, text_drawing
from phosphor.engine.function_plot import FunctionPlot, Variable, PARAMETRIC_OBJECT
from phosphor.engine.lane_access import set_points
from phosphor.engine.scene_path import INSTANT_TRAVEL
from phosphor.engine.signal_engine import Settings, Shape
from phosphor.engine.transforms import Param, new_transform
from phosphor.storage import project_files

HERE = Path(__file__).resolve().parents[1]
MANUAL = HERE/'docs'/'manual'
IMAGES = MANUAL/'images'
ONLY = sys.argv[1] if len(sys.argv) > 1 else ''
SPEC = re.compile(r'<!--\s*example:\s*(\{.*?\})\s*-->')
FENCE = re.compile(r'^```.*?^```', re.S | re.M)


def composite(widget):
    """The widget as drawn on screen, GPU monitors included.

    Only monitor hosts (widgets with a QQuickWindow `view`) that are visible inside
    `widget` are painted: matching windows by screen position alone picked up hidden
    hosts (the closed shape builder's preview) and other top-level windows.
    """
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
    ratio = image.devicePixelRatio() or 1.
    painter = QPainter(image)
    hosts = [widget] + widget.findChildren(QWidget)
    for host in hosts:
        view = getattr(host, 'view', None)
        if not isinstance(view, QQuickWindow) or not host.isVisible() or not view.isExposed(): continue
        frame = view.grabWindow(); frame.setDevicePixelRatio(ratio)
        painter.drawImage(QRect(host.mapTo(widget, QPoint(0, 0)) if host is not widget else QPoint(0, 0), view.size()), frame)
    painter.end()
    return image


def save(name, image):
    if ONLY and not name.startswith(ONLY): return
    if isinstance(image, QWidget): image = composite(image)
    path = IMAGES/f'{name}.png'
    if not image.save(str(path)): raise RuntimeError(f'Could not save {path}')
    print('saved', path.relative_to(HERE))


def settle(window, ms=900):
    """Let the timer tick so the phosphor builds up a steady picture."""
    QTest.qWait(ms)


def bare(window, name):
    """Save the monitor alone, without editing overlays (handles, selection box, axis marker)."""
    with patch.object(q.Window, 'overlay', lambda self, *args: (setattr(self.host.scope, 'overlays', []), setattr(self, 'handles', []))):
        settle(window, 250); save(name, window.main_host)


def scene(window, settings, selected=0):
    window.sandbox_scene.settings = settings
    window.sandbox_scene.selected = selected if settings.shapes else -1
    window.sync(); settle(window, 300)


def modal(capture_name, opener):
    """Open a modal dialog, capture it once shown, then close it."""
    def grab():
        dialog = QApplication.activeModalWidget()
        if dialog is None: QTimer.singleShot(100, grab); return
        QTest.qWait(250); save(capture_name, dialog); dialog.reject()
    QTimer.singleShot(300, grab); opener()


def torus_obj(path, major=24, minor=10):
    import math
    lines = []
    for i in range(major):
        for j in range(minor):
            u, v = 2*math.pi*i/major, 2*math.pi*j/minor
            r = .6 + .25*math.cos(v)
            lines.append(f'v {r*math.cos(u):.5f} {.25*math.sin(v):.5f} {r*math.sin(u):.5f}')
    for i in range(major):
        for j in range(minor):
            a = i*minor + j; b = ((i+1) % major)*minor + j
            c = ((i+1) % major)*minor + (j+1) % minor; d = i*minor + (j+1) % minor
            lines.append(f'f {a+1} {b+1} {c+1} {d+1}')
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def examples():
    for chapter in sorted(MANUAL.glob('*.md')):
        text = FENCE.sub('', chapter.read_text(encoding='utf-8'))  # Format samples in code blocks are not examples.
        for match in SPEC.finditer(text):
            yield chapter.name, json.loads(match.group(1))


def equation_settings(spec):
    variables = tuple(Variable(v['name'], float(v['value']), float(v['low']), float(v['high']), v.get('expression', ''))
                      for v in spec.get('variables', ()))
    plot = FunctionPlot(spec['x'], spec['y'], variables)
    return Settings(source='Single tone', tone_frequency=110., travel_speed=INSTANT_TRAVEL,
                    shapes=(Shape(solid=PARAMETRIC_OBJECT, plot=plot),))


def main():
    IMAGES.mkdir(parents=True, exist_ok=True)
    folder = tempfile.TemporaryDirectory(); root = Path(folder.name)
    q.configure_graphics(); surface = QSurfaceFormat(); surface.setSamples(4); QSurfaceFormat.setDefaultFormat(surface)
    app = QApplication([])
    with patch.object(project_files, 'WORKSPACE_PATH', root/'workspace.json'), \
         patch.object(q, 'write_workspace', lambda name: project_files.write_workspace(name, root/'workspace.json')), \
         patch.object(q, 'MONITOR_WINDOW_PATH', root/'monitor-window.json'), \
         patch.object(q.Window, 'error', lambda self, error: (_ for _ in ()).throw(RuntimeError(str(error)))), \
         patch.object(q.Window, 'save_preferences', lambda self: None), \
         patch.object(q.Window, 'save_signal_preferences', lambda self: None), \
         patch.object(q.Window, 'save_brightness', lambda self: None):
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=SANDBOX))
        w.resize(1220, 840); w.show(); w.raise_(); QTest.qWait(400)
        w.apply_preferences(dict(q.DEFAULT_PREFERENCES, palette='Green monochrome', phosphor='P31 green'))
        view = w.main_host.view
        rotate = new_transform('Rotate').with_param('x', Param(20.)).with_param('y', Param(30.))
        spin = new_transform('Spin').with_param('y', Param(.15))
        cube = Shape(solid='Cube', transforms=(rotate, spin))
        base = replace(w.scene.settings, source='Single tone', tone_frequency=110.)

        # Sandbox: the window, the bar, the cube.
        scene(w, replace(base, shapes=(cube,))); w.show_panel(q.PANEL_SCENE); w.scene_sections.setCurrentIndex(0); settle(w)
        save('window-sandbox', w); save('app-bar', w.app_bar); bare(w, 'scope-cube')
        save('panel-scene-objects', w.sidebar)
        w.scene_sections.setCurrentIndex(1); settle(w, 300); save('panel-scene-transform', w.sidebar)
        w.scene_sections.setCurrentIndex(0)

        # Text, equation and model objects.
        text = Shape(solid=q.TEXT_OBJECT, text='PHOSPHOR', shape_size=1.6)
        scene(w, replace(base, shapes=(text,))); settle(w); save('panel-scene-text', w.sidebar); bare(w, 'scope-text')
        rose = FunctionPlot('0.8*cos(3*tau*s)*cos(tau*s)', '0.8*cos(3*tau*s)*sin(tau*s)', (Variable('a', 1., 0., 2.),))
        scene(w, replace(base, travel_speed=INSTANT_TRAVEL, shapes=(Shape(solid=PARAMETRIC_OBJECT, plot=rose),)))
        settle(w); save('panel-scene-parametric', w.sidebar); bare(w, 'scope-parametric')
        obj = root/'torus.obj'; torus_obj(obj)
        scene(w, replace(base, shapes=()))
        w.add_model(str(obj)); w.scene.change('transforms', (new_transform('Rotate').with_param('x', Param(35.)), spin)); w.sync(); settle(w)
        bare(w, 'scope-model')

        # Input panel, one image per source and monitor input.
        scene(w, replace(base, shapes=(cube,))); w.show_panel(q.PANEL_INPUT)
        for name, source in (('panel-input-scale', q.SOURCES[0]), ('panel-input-tone', 'Single tone')):
            w.edit('source', source); settle(w, 300); save(name, w.sidebar)
        from tests.example_midi import example_midi
        midi = example_midi()
        w.scene.change('midi_file', str(midi)); w.edit('source', q.MIDI_FILE_SOURCE); settle(w, 300); save('panel-input-midi-file', w.sidebar)
        with patch.object(q.Window, 'refresh_ports', lambda self: None):
            w.edit('source', q.MIDI_SOURCE); settle(w, 300); save('panel-input-live-midi', w.sidebar)
        w.edit('source', 'Single tone')
        if q.SYSTEM_AUDIO_AVAILABLE:
            w.monitor.setCurrentIndex(q.MONITOR_SYSTEM); w.monitor_changed(q.MONITOR_SYSTEM); settle(w, 600); save('panel-input-system', w.sidebar)
        w.monitor.setCurrentIndex(q.MONITOR_VIDEO); w.monitor_changed(q.MONITOR_VIDEO); settle(w, 300); save('panel-input-video', w.sidebar)
        w.monitor.setCurrentIndex(q.MONITOR_GENERATED); w.monitor_changed(q.MONITOR_GENERATED)

        # Mix, Appearance (five tabs), Signal output.
        w.show_panel(q.PANEL_MIX); settle(w, 300); save('panel-mix', w.sidebar)
        w.show_panel(q.PANEL_APPEARANCE)
        for index, name in enumerate(('display', 'beam', 'phosphor', 'decay', 'monitor')):
            w.appearance_editor.tabs.setCurrentIndex(index); settle(w, 300); save(f'panel-appearance-{name}', w.sidebar)
        w.appearance_editor.tabs.setCurrentIndex(0)
        w.show_panel(q.PANEL_SIGNAL); settle(w, 300); save('panel-signal-output', w.sidebar)
        modal('dialog-preferences', w.show_preferences)

        # Time view, then the pop-out beside the main window.
        scene(w, replace(base, travel_speed=INSTANT_TRAVEL, shapes=(Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot('0.7*sin(tau*s)', '0.7*sin(2*tau*s+pi/4)')),)))
        w.show_panel(q.PANEL_APPEARANCE); w.appearance_editor.tabs.setCurrentIndex(4)
        w.sweep_display.setCurrentIndex(1); w.sweep_separation_control.commit(.4); w.sweep_window_control.commit(20.); settle(w, 1200)
        save('monitor-time-view', w.main_host)
        w.sweep_display.setCurrentIndex(0); w.appearance_editor.tabs.setCurrentIndex(0)
        scene(w, replace(base, shapes=(cube,)))
        w.pop_out(); pop = w.monitor_window
        area = w.screen().availableGeometry(); pop.setGeometry(QRect(area.left()+40, area.top()+120, 480, 480)); settle(w, 1200)
        left, right = composite(w), composite(pop)
        both = QImage(left.width() + right.width() + 24, max(left.height(), right.height()), QImage.Format.Format_ARGB32)
        both.fill(QColor('#202020')); painter = QPainter(both)
        painter.drawImage(0, 0, left); painter.drawImage(left.width() + 24, 60, right); painter.end()
        save('monitor-popout', both); w.pop_in()

        # Shape builder: text strokes and a circle, then a guide with a follower.
        scene(w, replace(base, shapes=(Shape(solid='Tilting circle'),))); w.scene.saved = w.scene.document()
        w.open_builder(); QTest.qWait(200); b = w.builder
        word = text_drawing('PHOSPHOR', .25)
        b.commit(word); b.fit(); b.set_tool('Select'); QTest.qWait(400); save('builder-window', w)
        fixture = Drawing('Motion', ((0, -1., .6), (1, 1., .6), (2, -.15, -.2), (3, .15, -.2), (4, .15, .1), (5, -.15, .1), (6, 0., -.6), (7, .35, -.6)),
                          ((0, 1), (2, 3), (3, 4), (4, 5), (5, 2)), (Curve('Circle', (6, 7)),))
        b.commit(fixture); b.fit(); b.set_tool('Select')
        b.selected = None; b.selected_edge = 0; b.selected_segments = frozenset((0,)); b.refresh(); b.motion_role.setChecked(True)
        b.selected_segments = frozenset((1, 2, 3, 4)); b.refresh()
        if b.assign_button.isEnabled():
            b.assign_button.click(); QTest.qWait(100)
            if b.motion_dialog is not None: b.motion_dialog.speed.setValue(.5); b.motion_dialog.apply(); b.motion_dialog.reject()
        QTest.qWait(400); save('builder-motion', w)
        b.cancel_gesture() if hasattr(b, 'cancel_gesture') else None
        with patch.object(q.QMessageBox, 'question', return_value=q.QMessageBox.StandardButton.Discard):
            w.leave_builder()
        QTest.qWait(200)

        # Composer: three clips, a melody, automation lanes.
        w.set_workspace(COMPOSER); QTest.qWait(300)
        wobble = new_transform('Rotate').with_param('z', Param(45.))
        clips = (Clip(0., 16., replace(base, shapes=(replace(cube, transforms=(wobble, rotate, spin)),)), name='Cube'),
                 Clip(16., 16., replace(base, travel_speed=INSTANT_TRAVEL, shapes=(Shape(solid=PARAMETRIC_OBJECT, plot=rose),)), name='Rose'),
                 Clip(32., 16., replace(base, shapes=(text,)), name='Title'))
        first = set_points(clips[0], ('transform', 0, 0, 'z'), ((0., 0., 'linear'), (8., 1., 'linear'), (12., .4, 'hold'), (16., 1., 'linear')))
        first = set_points(first, ('mix', 'drawing_gain'), ((0., .2, 'linear'), (4., 1., 'linear'), (16., 1., 'linear')))
        melody = [57, 60, 64, 62, 60, 64, 67, 64]
        notes = tuple(Note(float(i*2), 1.75, pitch) for i, pitch in enumerate(melody*3))
        w.set_project(Project(bpm=120., clips=(first, *clips[1:]), notes=notes), dirty=False); w.select_clip(0)
        view_timeline = w.composer_panel.timeline; view_timeline.scroll, view_timeline.scale = 0., 18.
        view_timeline.top_pitch = 72; w.playhead = float(w.project.seconds(6.)); w.vsplit.setSizes([430, 330]); w.show_panel(q.PANEL_SCENE); settle(w, 900)
        view_timeline.set_roll(cv.NOTE_ROLL); QTest.qWait(200)
        save('window-composer', w); save('timeline-note-roll', w.composer_panel)
        view_timeline.set_roll(cv.TRANSFORM_ROLL); view_timeline.lane_scroll = 0; view_timeline.update(); QTest.qWait(300)
        save('timeline-transform-roll', w.composer_panel)
        w.set_workspace(SANDBOX); QTest.qWait(200)

        # Equation gallery from the manual's example specs.
        seen = set()
        for chapter, spec in examples():
            if spec.get('kind') != 'equation' or spec['id'] in seen: continue
            seen.add(spec['id']); name = f"eq-{spec['id']}"
            if ONLY and not name.startswith(ONLY): continue
            try: scene(w, equation_settings(spec)); settle(w, 900); bare(w, name)
            except Exception as error: print(f'{chapter}: example {spec["id"]} failed: {error}', file=sys.stderr)

        w.project_dirty = False; w.sandbox_scene.saved = w.sandbox_scene.document()
        w.release_views(); w.force_close = True; w.close(); app.processEvents()


if __name__ == '__main__':
    main()
