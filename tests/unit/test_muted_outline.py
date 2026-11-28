"""B2 review: the faint outline of a muted or switched-off object draws the stack the audio
plays (audition state, On/Off steps and bypass resolved first) and the topology it builds."""
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from phosphor.engine.signal_engine import Shape, audition_shapes, object_id, posed_shapes
from phosphor.engine.transforms import Param, new_transform, ROTATE, SPIN, GRIDIFY

OFF = ((0., 0., 'hold'),)  # An On/Off lane that is off (unbaked: its first node).
VIEW = (400, 300, 120.)


def spin(off=False):
    step = new_transform(SPIN).with_param('z', Param(1.))
    return replace(step, on=Param(1., points=OFF)) if off else step


def outline(shape, seconds, cache=None, solos=()):
    from phosphor.editor.qt_app import muted_outline
    effective = audition_shapes((shape,), (), solos)
    posed, = posed_shapes(effective, seconds)
    return muted_outline({} if cache is None else cache, seconds, posed, *VIEW)


class MutedOutlineTests(unittest.TestCase):
    def setUp(self):
        self.cube = Shape(solid='Cube', muted=True)
        self.bare = outline(self.cube, 0.)

    def test_a_step_switched_off_by_its_lane_does_not_move_the_outline(self):
        shape = replace(self.cube, transforms=(spin(off=True),))
        cache = {}
        first, second = outline(shape, .1, cache), outline(shape, .3, cache)
        self.assertTrue(np.array_equal(first, self.bare))
        self.assertTrue(np.array_equal(second, self.bare))
        self.assertEqual(len(cache), 1)  # Static: one outline for every pose.
        # The same spin switched on turns the outline and rebuilds it per pose.
        shape = replace(self.cube, transforms=(spin(),))
        cache = {}
        first, second = outline(shape, .1, cache), outline(shape, .3, cache)
        self.assertFalse(np.array_equal(first, second))
        self.assertEqual(len(cache), 2)

    def test_transform_solo_is_applied_before_the_on_off_lane(self):
        rotate = replace(new_transform(ROTATE).with_param('z', Param(30.)), on=Param(1., points=OFF))
        shape = replace(self.cube, transforms=(rotate, spin()))
        solos = ((object_id(shape, 0), 0),)  # Solo the Rotate step, whose lane is off.
        self.assertTrue(np.array_equal(outline(shape, .2, solos=solos), self.bare))

    def test_a_bypassed_stack_draws_the_bare_object_once(self):
        from phosphor.editor import qt_app
        shape = replace(self.cube, transforms=(spin(),), stack_bypassed=True)
        cache = {}
        with patch.object(qt_app, '_outline_geometry', wraps=qt_app._outline_geometry) as built:
            first, second = outline(shape, .1, cache), outline(shape, .3, cache)
        self.assertEqual(built.call_count, 1)
        self.assertTrue(np.array_equal(first, self.bare) and np.array_equal(second, self.bare))

    def test_animated_gridify_copies_appear_and_move(self):
        grid = new_transform(GRIDIFY).with_param('spacing_x', Param(1.2, '1+t'))
        shape = replace(self.cube, transforms=(grid,))
        cache = {}
        first, second = outline(shape, 0., cache), outline(shape, .5, cache)
        self.assertGreaterEqual(len(first), 4*len(self.bare))  # Count X 2 by Count Y 2: four copies (and joins).
        width = lambda tris: np.ptp(tris[:, 0])
        self.assertGreater(width(first), 1.5*width(self.bare))
        self.assertFalse(np.array_equal(first, second))
        self.assertEqual(len(cache), 2)


class WindowMutedOutlineTests(unittest.TestCase):
    """Through Window.overlay, the caller that poses the scene and builds the outline."""
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from phosphor.editor.qt_app import Window, PANEL_SCENE
        self.w = Window(SimpleNamespace(scene=None, ready_file=None, workspace='Sandbox'))
        self.w.timer.stop(); self.w.show_panel(PANEL_SCENE)
        self.w.add_object('Cube')
        self.w.show(); self.app.processEvents()

    def tearDown(self):
        self.w.force_close = True
        self.w.shutdown_audio()
        self.w.scene.mark_saved(); self.w.project_dirty = False
        self.w.release_views(); self.w.close(); self.w.deleteLater(); self.app.processEvents()
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete); self.w = None

    def faint(self, seconds):
        self.w.overlay(seconds)
        layers = [points for points, colour in self.w.host.scope.overlays if colour.alphaF() < .25 and len(points)]
        self.assertEqual(len(layers), 1)
        return layers[0]

    def test_overlay_outline_follows_the_resolved_stack(self):
        w = self.w; w.select_shape(0)
        w.toggle_objects('mute', 0)
        bare = self.faint(.1)
        w.scene.change('transforms', (spin(off=True),))
        self.assertTrue(np.array_equal(self.faint(.1), bare))
        self.assertTrue(np.array_equal(self.faint(.3), bare))
        w.scene.change('transforms', (spin(),))
        self.assertFalse(np.array_equal(self.faint(.1), self.faint(.3)))
        w.scene.change('stack_bypassed', True)
        self.assertTrue(np.array_equal(self.faint(.3), bare))
        grid = new_transform(GRIDIFY).with_param('spacing_x', Param(1.2, '1+t'))
        w.scene.change('stack_bypassed', False); w.scene.change('transforms', (grid,))
        first, second = self.faint(0.), self.faint(.5)
        self.assertGreaterEqual(len(first), 4*len(bare))
        self.assertFalse(np.array_equal(first, second))


if __name__ == '__main__': unittest.main()
