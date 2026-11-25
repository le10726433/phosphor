"""Sandbox undo (U1) without a QApplication: snapshots, labels, merging, caps and memory."""
from dataclasses import replace
import sys
import tracemalloc
import unittest

from phosphor.editor.history import (History, SceneState, describe_scene, saved_value, with_live,
                                     heavy_payloads, LIVE_FIELDS, STRUCTURAL, SCENE_HISTORY_BUDGET)
from phosphor.engine.signal_engine import Settings, Shape
from phosphor.engine.transforms import new_transform, NoiseOptions, MAP, NOISE, ROTATE, SPIN
from phosphor.engine.function_plot import FunctionPlot, PARAMETRIC_OBJECT
from phosphor.engine.custom_shape import Drawing, CUSTOM_OBJECT
from phosphor.engine.model_object import MODEL_OBJECT
from phosphor.engine.signal_engine import Param
from tests.unit.golden_scenes import dense_model


def scene(*shapes, **values):
    return SceneState(Settings(shapes=shapes, **values), 'drawing', 0 if shapes else -1)


class SceneLabelTests(unittest.TestCase):
    def setUp(self):
        self.cube = Shape(uid='a')
        self.base = scene(self.cube, Shape(uid='b', solid='Tetrahedron'))

    def edit(self, **values):
        shapes = (replace(self.cube, **values), *self.base.settings.shapes[1:])
        return replace(self.base, settings=replace(self.base.settings, shapes=shapes))

    def label(self, after): return describe_scene(self.base, after)

    def test_objects(self):
        s = self.base.settings
        self.assertEqual(self.label(replace(self.base, settings=replace(s, shapes=(*s.shapes, Shape())))), 'Add object')
        self.assertEqual(self.label(replace(self.base, settings=replace(s, shapes=s.shapes[:1]))), 'Delete object')
        self.assertEqual(self.label(replace(self.base, settings=replace(s, shapes=s.shapes[::-1]))), 'Reorder objects')
        for values, label in (
                (dict(position_x=.2), 'Move object'), (dict(shape_size=.5), 'Resize object'),
                (dict(solid='Octahedron'), 'Change shape type'), (dict(solid='Text', text='HI'), 'Change shape type'),
                (dict(muted=True), 'Mute object'), (dict(stack_bypassed=True), 'Bypass stack'),
                (dict(path_trace=Param(.5)), 'Edit path'), (dict(path_start=Param(.2)), 'Edit path'),
                (dict(text='NEW'), 'Edit text'), (dict(lanes=(('shape_size', ((0., .5, 'linear'),)),)), 'Edit automation'),
                (dict(custom=Drawing(nodes=((0, 0., 0.), (1, 1., 0.)), edges=((0, 1),))), 'Edit drawing'),
                (dict(model=dense_model(4, 4)), 'Edit model'), (dict(plot=FunctionPlot()), 'Edit equations')):
            with self.subTest(label=label): self.assertEqual(self.label(self.edit(**values)), label)
        muted = self.edit(muted=True)
        self.assertEqual(describe_scene(muted, self.base), 'Unmute object')

    def test_transforms(self):
        rotate, noise, map_step = new_transform(ROTATE), new_transform(NOISE), new_transform(MAP)
        self.base = self.edit(transforms=(rotate, noise, map_step)); self.cube = self.base.settings.shapes[0]
        cases = (
            ((rotate, noise, map_step, new_transform(SPIN)), 'Add transform'),
            ((rotate, map_step), 'Remove transform'),
            ((noise, rotate, map_step), 'Reorder transforms'),
            ((replace(rotate, enabled=False), noise, map_step), 'Bypass transform'),
            ((replace(rotate, params=(replace(rotate.params[0], value=33.), *rotate.params[1:])), noise, map_step), 'Edit rotate'),
            ((rotate, replace(noise, noise=NoiseOptions('Pink', noise.noise.rate)), map_step), 'Edit Noise'),
            ((rotate, noise, replace(map_step, params=(replace(map_step.params[0], expression='y'), *map_step.params[1:]))), 'Edit Map'))
        for stack, label in cases:
            with self.subTest(label=label): self.assertEqual(self.label(self.edit(transforms=stack)), label)
        self.assertEqual(describe_scene(self.edit(transforms=(replace(rotate, enabled=False), noise, map_step)), self.base), 'Enable transform')

    def test_scene_settings_and_mode(self):
        s = self.base.settings
        for values, label in ((dict(bpm=90.), 'Change tempo'), (dict(source='Single tone'), 'Change source'),
                              (dict(drawing_gain=.3), 'Change level'), (dict(scale_root=50), 'Change scale'),
                              (dict(tone_frequency=220.), 'Edit scene settings')):
            with self.subTest(label=label):
                self.assertEqual(self.label(replace(self.base, settings=replace(s, **values))), label)
        self.assertEqual(self.label(replace(self.base, mode='music')), 'Change listening mode')

    def test_structural_labels_never_merge(self):
        self.assertTrue({'Add object', 'Delete object', 'Reorder objects', 'Add transform', 'Remove transform'} <= STRUCTURAL)


class SceneSnapshotTests(unittest.TestCase):
    def test_live_fields_are_ignored_and_kept(self):
        a = scene(Shape())
        live = replace(a.settings, motion_time_offset=3., corner_dwell_us=20., route_mode='Nearest', switch_sweep_ms=5.)
        self.assertEqual(saved_value(a), saved_value(replace(a, settings=live)))
        self.assertNotEqual(saved_value(a), saved_value(replace(a, settings=replace(a.settings, bpm=99.))))
        self.assertEqual(saved_value(a), saved_value(replace(a, selected=-1)))  # Selection is not saved.
        restored = with_live(replace(a.settings, bpm=99.), live)
        self.assertEqual((restored.bpm, restored.motion_time_offset, restored.route_mode), (99., 3., 'Nearest'))
        self.assertIs(with_live(a.settings, a.settings), a.settings)
        self.assertIn('tempo', LIVE_FIELDS); self.assertNotIn('bpm', LIVE_FIELDS); self.assertNotIn('shapes', LIVE_FIELDS)

    def test_amend_keeps_step_label_and_saved_token(self):
        a = scene(Shape(), Shape(solid='Octahedron'))
        b = replace(a, settings=replace(a.settings, bpm=90.))
        h = History(); h.reset(a); h.push('Change tempo', b); h.mark_saved()
        h.amend(replace(b, selected=1))
        self.assertEqual((h.undo_label, h.state.selected, h.dirty), ('Change tempo', 1, False))
        self.assertIs(h.undo(), a); self.assertEqual(h.redo().selected, 1)

    def test_drag_steps_merge_by_key_and_time(self):
        a = scene(Shape()); h = History(); h.reset(a)
        states = [replace(a, settings=replace(a.settings, shapes=(Shape(position_x=i/10),))) for i in range(1, 6)]
        for i, state in enumerate(states): h.push('Move object', state, key=('field', 'Move object'), now=i*.1)
        self.assertEqual(len(h), 2)
        self.assertIs(h.state, states[-1]); self.assertIs(h.undo(), a)

    def test_history_cap(self):
        a = scene(Shape()); h = History(weigh=heavy_payloads, budget=SCENE_HISTORY_BUDGET); h.reset(a)
        for i in range(250): h.push('Move object', replace(a, settings=replace(a.settings, shapes=(Shape(position_x=i),))))
        self.assertEqual(len(h), 200)

    def test_byte_budget_drops_oldest_distinct_models(self):
        models = [dense_model(8+i, 8) for i in range(6)]
        one = heavy_payloads(scene(Shape(solid=MODEL_OBJECT, model=models[0])))[id(models[0])]
        h = History(weigh=heavy_payloads, budget=int(2.5*one)); a = scene(); h.reset(a)
        for model in models: h.push('Add object', scene(Shape(solid=MODEL_OBJECT, model=model)))
        self.assertLessEqual(h.weight, 2.5*one*1.3)
        self.assertLess(len(h), 7); self.assertGreaterEqual(len(h), 2)
        self.assertIs(h.state.settings.shapes[0].model, models[-1])
        # The same model shared by many entries is counted once.
        shared = History(weigh=heavy_payloads, budget=int(1.5*one)); shared.reset(scene(Shape(solid=MODEL_OBJECT, model=models[0])))
        for i in range(50):
            shared.push('Move object', scene(Shape(solid=MODEL_OBJECT, model=models[0], position_x=i/100)))
        self.assertEqual(len(shared), 51)

    def test_weight_estimate_is_close_to_cpython_size(self):
        model = dense_model(); estimate = heavy_payloads(scene(Shape(solid=MODEL_OBJECT, model=model)))[id(model)]
        actual = sys.getsizeof(model.vertices) + sys.getsizeof(model.edges)
        actual += sum(sys.getsizeof(v) + sum(map(sys.getsizeof, v)) for v in model.vertices)
        actual += sum(sys.getsizeof(e) for e in model.edges)  # Small edge ints are cached by CPython.
        self.assertLess(abs(estimate - actual) / actual, .5)

    def test_dense_model_snapshots_are_cheap(self):
        """200 placement edits on a dense model keep one model alive; each entry costs a
        Settings, a shapes tuple and a Shape (measured, not estimated)."""
        model = dense_model(64, 64)
        base = scene(Shape(solid=MODEL_OBJECT, model=model), Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot()),
                     Shape(solid=CUSTOM_OBJECT, custom=Drawing(nodes=((0, 0., 0.), (1, 1., 0.)), edges=((0, 1),))))
        h = History(weigh=heavy_payloads, budget=SCENE_HISTORY_BUDGET); h.reset(base)
        tracemalloc.start()
        try:
            before = tracemalloc.take_snapshot()
            state = base
            for i in range(300):
                shapes = (replace(state.settings.shapes[0], position_x=i/1000), *state.settings.shapes[1:])
                state = replace(state, settings=replace(state.settings, shapes=shapes))
                h.push('Move object', state, now=float(i))
            grown = sum(stat.size_diff for stat in tracemalloc.take_snapshot().compare_to(before, 'filename'))
        finally:
            tracemalloc.stop()
        self.assertEqual(len(h), 200)
        self.assertLess(grown / 200, 4096)  # Bytes per entry; the model alone is several megabytes.
        self.assertEqual(h.weight, sum(heavy_payloads(base).values()))


if __name__ == '__main__': unittest.main()
