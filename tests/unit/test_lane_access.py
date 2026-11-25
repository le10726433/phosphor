import unittest
from dataclasses import replace

from phosphor.engine import lane_access as lanes
from phosphor.engine.lane_edit import shift_lane
from phosphor.engine.composition import Clip, Project, validate_project
from phosphor.engine.custom_shape import Drawing, CUSTOM_OBJECT
from phosphor.engine.function_plot import PRESETS, PARAMETRIC_OBJECT
from phosphor.engine.signal_engine import Shape, Settings
from phosphor.engine.transforms import FIELDS, new_transform, Param


POINTS = ((0., -1., 'hold'), (4., 1., 'linear'))  # The full gain range, ±1.
SWITCH = ((0., 1., 'hold'), (2., 0., 'hold'), (6., 1., 'hold'))  # On/Off lanes: 0/1 holds.


def points_for(key):
    return SWITCH if lanes.is_switch(key) else POINTS


class LaneAccessTests(unittest.TestCase):
    def test_shift_all_lane_kinds(self):
        keys = [('transform', 0, 0, 'z'), ('path', 0, 'path_trace'),
                ('variable', 1, 'k'), ('shape', 2, 'shape_size'),
                ('mix', 'drawing_gain'), ('mix', 'music_gain')]
        clip = self.clip
        for key in keys:
            clip = lanes.set_points(clip, key, POINTS)
        self.assertIs(lanes.shift_clip_lanes(clip, 0.), clip)
        self.assertIs(lanes.shift_clip_lanes(self.clip, 1.), self.clip)
        for offset in (2., -2., -10.):
            with self.subTest(offset=offset):
                shifted = lanes.shift_clip_lanes(clip, offset)
                validate_project(Project(clips=(shifted,)))
                for key in lanes.automatable(clip):
                    self.assertEqual(lanes.get_points(shifted, key),
                                     shift_lane(POINTS, offset) if key in keys else ())
                    self.assertEqual(lanes.static_value(shifted, key),
                                     lanes.static_value(clip, key))
                restored = shifted
                for key in keys:
                    restored = lanes.set_points(restored, key, POINTS)
                self.assertEqual(restored, clip)
                for key in keys:
                    self.assertIs(lanes.get_points(clip, key), POINTS)

    def setUp(self):
        self.clip = Clip(8., 16., Settings(shapes=(
            Shape(transforms=(new_transform('Rotate').with_param('z', Param(30., 't')),
                              new_transform('Mirror'), new_transform('Noise'))),
            Shape(solid=PARAMETRIC_OBJECT, plot=PRESETS['Rose (k petals)']),
            Shape(solid='Text', text='A'), Shape(solid='Text', text='B'), Shape(),
        )))

    def test_order_and_exclusions(self):
        expected = []
        for i in range(5):
            expected.append(('on', i))
            expected.extend(('shape', i, name) for name in ('position_x', 'position_y', 'shape_size'))
            expected.extend(('path', i, name) for name in ('path_start', 'path_trace'))
            if i == 0:
                expected.extend(('transform', 0, 0, axis) for axis in 'xyz')
                expected.extend((('on', 0, 0), ('on', 0, 1)))
                expected.extend(('transform', 0, 2, name) for name in ('intensity', 'speed', 'detail'))
                expected.append(('on', 0, 2))
            if i == 1: expected.append(('variable', 1, 'k'))
        expected.extend((('mix', 'drawing_gain'), ('mix', 'music_gain')))
        self.assertEqual(lanes.automatable(self.clip), expected)

    def test_round_trip_validation_and_identity(self):
        for key in lanes.automatable(self.clip):
            with self.subTest(key=key):
                self.assertEqual(lanes.get_points(self.clip, key), ())
                updated = lanes.set_points(self.clip, key, points_for(key))
                self.assertIs(lanes.get_points(updated, key), points_for(key))
                validate_project(Project(clips=(updated,)))
                self.assertEqual(lanes.set_points(updated, key, ()), self.clip)
                if key[0] != 'mix': self.assertIs(updated.lanes, self.clip.lanes)
                if key[0] == 'mix':
                    self.assertIs(updated.settings, self.clip.settings)
                else:
                    i = key[1]
                    for j, shape in enumerate(self.clip.settings.shapes):
                        if j != i: self.assertIs(updated.settings.shapes[j], shape)
                    old, new = self.clip.settings.shapes[i], updated.settings.shapes[i]
                    for name in vars(old):
                        changed = {'shape': 'lanes', 'path': key[-1], 'transform': 'transforms',
                                   'variable': 'plot', 'on': 'lanes' if len(key) == 2 else 'transforms'}[key[0]]
                        if name != changed: self.assertIs(getattr(new, name), getattr(old, name))
                    if key[0] == 'on' and len(key) == 3:
                        for j, step in enumerate(old.transforms):
                            if j != key[2]: self.assertIs(new.transforms[j], step)
                            else: self.assertEqual(replace(new.transforms[j], on=step.on), step)
                    if key[0] == 'transform':
                        for j, step in enumerate(old.transforms):
                            if j != key[2]: self.assertIs(new.transforms[j], step)
                            else:
                                for field, before, after in zip(
                                        FIELDS[step.kind],
                                        step.params, new.transforms[j].params):
                                    if field.name != key[3]: self.assertIs(after, before)
                self.assertEqual(lanes.get_points(self.clip, key), ())

    def test_invalid_keys_and_points(self):
        invalid = [(), ('mix', 'bad'), ('shape', -1, 'position_x'),
                   ('shape', 50, 'shape_size'), ('shape', True, 'position_x'),
                   ('transform', 0, 1, 'x'), ('transform', 0, 2, 'missing'),
                   ('transform', 0, 0, 'bad'), ('variable', 0, 'k'),
                   ('variable', 1, 'missing'), ('path', 0, 'bad'), ['mix', 'drawing_gain']]
        for key in invalid:
            for accessor in (lanes.get_points, lanes.label, lanes.static_value):
                with self.subTest(key=key, accessor=accessor), self.assertRaises(ValueError):
                    accessor(self.clip, key)
            with self.assertRaises(ValueError): lanes.set_points(self.clip, key, ())
        for points in (list(POINTS), ((0., 3., 'linear'),), ((1., 1., 'ease'),),
                       ((2., 1., 'hold'), (1., 1., 'linear'))):
            with self.assertRaises(ValueError): lanes.set_points(self.clip, ('mix', 'music_gain'), points)

    def test_labels_and_static_values(self):
        cases = [ (('shape', 0, 'position_x'), 'Position X', 0.),
                  (('shape', 0, 'position_y'), 'Position Y', 0.),
                  (('shape', 0, 'shape_size'), 'Size', 1.),
                  (('path', 0, 'path_start'), 'Path Start', 0.),
                  (('path', 0, 'path_trace'), 'Path Trace', 1.),
                  (('transform', 0, 0, 'z'), 'Rotate Z (°)', 30.),
                  (('variable', 1, 'k'), 'Variable k', 4.),
                  (('mix', 'drawing_gain'), 'Output level', .55),
                  (('mix', 'music_gain'), 'Original blend', 0.)]
        for key, title, value in cases:
            with self.subTest(key=key):
                self.assertEqual(lanes.label(self.clip, key), title)
                self.assertEqual(lanes.static_value(lanes.set_points(self.clip, key, POINTS), key), value)

    def test_blocks(self):
        self.assertEqual(lanes.blocks(self.clip), ())
        keys = [('shape', 0, 'position_x'), ('path', 0, 'path_trace'),
                ('transform', 0, 0, 'z'), ('transform', 0, 2, 'intensity'),
                ('variable', 1, 'k'), ('shape', 2, 'shape_size'),
                ('path', 3, 'path_start'), ('shape', 4, 'position_y'), ('mix', 'music_gain')]
        clip = self.clip
        for key in reversed(keys): clip = lanes.set_points(clip, key, POINTS)
        blocks = lanes.blocks(clip)
        self.assertEqual([b.key for b in blocks], [lanes.block_of(k) for k in keys])
        self.assertEqual([b.title for b in blocks], [
            'Cube #1 › Placement', 'Cube #1 › Path', 'Cube #1 › 1. Rotate',
            'Cube #1 › 3. Noise', 'Parametric equation › Equation',
            '"A" › Placement', '"B" › Path', 'Cube #5 › Placement', 'Mix'])
        for block in blocks:
            self.assertEqual(block.keys, tuple(k for k in lanes.automatable(clip)
                                               if lanes.block_of(k) == block.key))
        self.assertEqual(lanes.blocks(self.clip, include=(('transform', 0, 0),)),
                         (blocks[2],))
        duplicate = replace(self.clip.settings.shapes[3], text='A')
        clip = replace(self.clip, settings=replace(self.clip.settings,
                       shapes=(*self.clip.settings.shapes[:3], duplicate)))
        self.assertEqual([b.title for b in lanes.blocks(clip, include=(('path', 2), ('path', 3)))],
                         ['"A" #3 › Path', '"A" #4 › Path'])

    def test_motion_guide_and_defaults(self):
        guide = Shape(solid=CUSTOM_OBJECT, custom=Drawing(motion_path=True),
                      transforms=(new_transform('Rotate'),))
        clip = replace(self.clip, settings=Settings(shapes=(guide,)))
        self.assertEqual(lanes.automatable(clip), [
            ('path', 0, 'path_start'), ('path', 0, 'path_trace'),
            *(('transform', 0, 0, axis) for axis in 'xyz'),
            ('mix', 'drawing_gain'), ('mix', 'music_gain')])
        with self.assertRaises(ValueError): lanes.set_points(clip, ('shape', 0, 'position_x'), POINTS)
        self.assertEqual(lanes.default_points(self.clip), ((0., 1., 'linear'), (16., 1., 'linear')))
        self.assertEqual(lanes.default_points(replace(self.clip, length=100000.))[-1][0], 100000.)


if __name__ == '__main__':
    unittest.main()
