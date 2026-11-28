"""Smoke test: P1 editor responsiveness optimizations keep UI state and rendering exact."""
import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from phosphor.editor.qt_platform import configure_graphics
configure_graphics()

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication
from phosphor.editor.qt_app import Window, _HANDLE_RING, _CORNER_BOX, _AXIS_GLYPHS
from phosphor.engine.composition import COMPOSER, Note, Project
from phosphor.engine.signal_engine import Settings, Shape
from phosphor.engine.audio_clips import AudioTrack
from phosphor.engine.track_effects import EFFECTS, new_effect


def pump(app, n=10):
    for _ in range(n):
        app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scale', type=float, default=1.0)
    parser.parse_args()

    app = QApplication.instance() or QApplication([])
    opts = SimpleNamespace(scene=None, workspace=COMPOSER, ready_file=None, skip_recovery=True)
    window = Window(opts)
    try:
        window.show()
        pump(app, 15)

        # 1. Precomputed viewport handle & axis glyph geometry is valid.
        assert len(_HANDLE_RING) > 0 and len(_CORNER_BOX) > 0
        assert len(_AXIS_GLYPHS) == 3 and all(len(g) > 0 for g in _AXIS_GLYPHS)

        # 2. TrackEffectsPanel.refresh() skips redundant chain rebuilds when effects are unchanged,
        # and still rebuilds when effects change.
        kinds = list(EFFECTS)[:2]
        track = AudioTrack(name='Audio 1', effects=(new_effect(kinds[0]),))
        proj = Project(clips=window.project.clips, audio_tracks=(track,), chords=True)
        window.set_project(proj, dirty=False)
        panel = window.open_track_effects(0)
        pump(app, 5)
        assert panel.chain.count() == 1
        first_item = panel.chain.item(0)
        panel.refresh()  # No-op refresh: same QListWidgetItem instance stays in place
        assert panel.chain.item(0) is first_item, 'TrackEffectsPanel rebuilt chain on no-op refresh'

        # Adding an effect must rebuild the chain immediately.
        panel.add_effect(kinds[1])
        pump(app, 5)
        assert panel.chain.count() == 2, f'Expected 2 effects after add_effect, got {panel.chain.count()}'

        # 3. TimelineView.chord_layout() defers recompute during active note drag and updates on release.
        tl = window.composer_panel.timeline
        notes_a = (Note(0.0, 1.0, 60), Note(0.0, 1.0, 64), Note(0.0, 1.0, 67))
        notes_b = (Note(0.5, 1.0, 60), Note(0.5, 1.0, 64), Note(0.5, 1.0, 67))
        window.set_project(Project(clips=window.project.clips, notes=notes_a, chords=True), dirty=False)
        layout_a = tl.chord_layout()
        tl.drag = ('move', 0)
        window.set_project(Project(clips=window.project.clips, notes=notes_b, chords=True), dirty=False)
        assert tl.chord_layout() is layout_a, 'chord_layout recomputed during active note drag'
        tl.drag = None
        layout_b = tl.chord_layout()
        assert layout_b is not layout_a, 'chord_layout did not recompute after note drag release'

        # 4. Muted outline cache populates and reuses cached geometry for static muted shapes.
        window.scene.settings = Settings(shapes=(Shape(solid='Cube', muted=True), Shape(solid='Cube')))
        window.reveal_handles()
        window.overlay(0.25)
        cache = getattr(window, '_muted_outline_cache', None)
        assert cache and len(cache) == 1, f'Expected 1 cached muted outline, got {cache}'
        cached_tris = next(iter(cache.values()))
        window.overlay(1.75)  # Different time, static shape -> must hit cache
        assert len(cache) == 1 and next(iter(cache.values())) is cached_tris

        # 5. Coalesced viewport pointer drag updates shape & spinbox immediately and syncs on release.
        window.scene.settings = Settings(shapes=(Shape(solid='Cube', position_x=0.0, position_y=0.0),))
        window.sync()
        window.reveal_handles()
        window.overlay(0.0)
        cx, cy = window.handles[0][1]
        assert window.pointer('press', QPointF(float(cx), float(cy)))
        sync_calls = 0
        orig_sync = window.sync

        def count_sync():
            nonlocal sync_calls
            sync_calls += 1
            return orig_sync()

        window.sync = count_sync
        try:
            for step in range(1, 11):
                window.pointer('move', QPointF(float(cx) + step * 2, float(cy)))
            assert sync_calls == 0, f'Expected 0 full sync() calls during pointer move, got {sync_calls}'
            assert window.scene.shape.position_x > 0.0, 'Shape position_x did not update during drag'
            assert abs(window.parameters['position_x'].value() - window.scene.shape.position_x) < 1e-3
            window.pointer('release', QPointF(float(cx) + 20, float(cy)))
            assert sync_calls == 1, f'Expected 1 flush sync() on release, got {sync_calls}'
        finally:
            window.sync = orig_sync

        print('smoke_p1_editor_perf OK')
    finally:
        window.force_close = True
        window.close()
        pump(app, 5)


if __name__ == '__main__':
    main()
