"""Object Replace (R1, 0.80.0): carry-over, parked type data and parked variable lanes."""
from dataclasses import replace
import json
import unittest

import numpy as np

from phosphor.engine import object_replace as r
from phosphor.engine.automation import parked_lane, parked_lanes, live_lanes
from phosphor.engine.composition import Project, Clip, Note, clip_settings
from phosphor.engine.custom_shape import Drawing, CUSTOM_OBJECT
from phosphor.engine.function_plot import FunctionPlot, Variable, PARAMETRIC_OBJECT
from phosphor.engine.lane_access import automatable, blocks, get_points, set_points, label, static_value, is_parked
from phosphor.engine.model_object import MODEL_OBJECT
from phosphor.engine.recurrence import RecurrencePlot, RECURRENCE_OBJECT
from phosphor.engine.signal_engine import Settings, Shape, render, validate, posed_shapes
from phosphor.engine.transforms import Param, new_transform, ROTATE, SPIN
from phosphor.engine.vector_text import TEXT_OBJECT, DEFAULT_TEXT
from phosphor.storage.app_files import SCENE_VERSION, scene_document, scene_from_document
from phosphor.storage.project_files import project_document, project_from_document

LANE = ((0., .5, 'linear'), (4., 1., 'linear'))
OTHER = ((0., 1., 'hold'), (2., -.5, 'linear'))
TRIANGLE = Drawing('Triangle', ((10, -.6, -.4), (20, .6, -.4), (30, 0, .6)), ((10, 20), (20, 30), (30, 10)))


def plot(**lanes):
    """a and b equations; lanes gives a variable its points."""
    return FunctionPlot('a*sin(tau*s)', 'b*cos(tau*s)',
                        (Variable('a', 2., 0., 5., points=lanes.get('a', ())), Variable('b', 3., 0., 5., points=lanes.get('b', ()))))


def placed(shape):
    """shape with everything Replace must carry over: placement, path window, mute, bypass,
    a two-step stack with lanes, and the placement and On/Off lanes."""
    rotate = new_transform(ROTATE).with_param('z', Param(30., '', LANE))
    spin = replace(new_transform(SPIN), on=Param(1., '', ((0., 1., 'hold'), (1., 0., 'hold'))))
    return replace(shape, uid='abc', position_x=.3, position_y=-.2, shape_size=.7, rotation_z=10., spin_y=.25,
                   path_start=Param(.1, '', LANE), path_trace=Param(.8), muted=True, stack_bypassed=True,
                   transforms=(rotate, spin), lanes=(('on', ((0., 1., 'hold'), (2., 0., 'hold'))), ('position_x', LANE)))


def carried(shape):
    return {key: getattr(shape, key) for key in ('uid', 'position_x', 'position_y', 'shape_size', 'rotation_z', 'spin_y',
                                                  'path_start', 'path_trace', 'muted', 'stack_bypassed', 'transforms', 'motion')} | \
        {'lanes': live_lanes(shape.lanes)}


def clip_of(*shapes):
    return Clip(0., 8., Settings(shapes=shapes), 'drawing')


def round_trip(settings, notices=None):
    document = json.loads(json.dumps(scene_document(settings, 'drawing', '')))
    return scene_from_document(document, notices=notices)[0], document


class ReplaceTest(unittest.TestCase):
    def test_round_trip_restores_equations_variables_and_lanes(self):
        # Cube → Parametric → Text → Parametric: the equations, variables and their lanes return.
        cube = placed(Shape())
        parametric = r.replaced(cube, PARAMETRIC_OBJECT)
        self.assertEqual(parametric.plot, FunctionPlot())  # Never Parametric: Add-object defaults.
        parametric = replace(parametric, plot=plot(a=LANE, b=OTHER))
        text = r.replaced(parametric, TEXT_OBJECT)
        self.assertEqual(text.text, r.ADD_TEXT)
        self.assertEqual(parked_lanes(text.lanes), {'a': LANE, 'b': OTHER})
        self.assertFalse(any(v.points for v in text.plot.variables))  # Parked equation: lanes moved aside.
        back = r.replaced(replace(text, text='HI'), PARAMETRIC_OBJECT)
        self.assertEqual(back.plot, parametric.plot)
        self.assertEqual(back.lanes, parametric.lanes)
        self.assertEqual(back.text, 'HI')
        self.assertEqual(r.replaced(back, TEXT_OBJECT).text, 'HI')  # Parked text comes back too.
        self.assertEqual(r.replaced(back, PARAMETRIC_OBJECT), back)  # Same type: unchanged.

    def test_lanes_and_transforms_carried_over(self):
        shape = placed(Shape())
        for kind in (PARAMETRIC_OBJECT, RECURRENCE_OBJECT, TEXT_OBJECT, 'Square (2D)', 'Octant sphere'):
            with self.subTest(kind=kind):
                result = r.replaced(shape, kind)
                self.assertEqual(result.solid, kind)
                self.assertEqual(carried(result), carried(shape))
        custom = r.replaced(shape, CUSTOM_OBJECT, custom=TRIANGLE)
        self.assertEqual((custom.custom, carried(custom)), (TRIANGLE, carried(shape)))

    def test_same_name_variables_carried_between_equations(self):
        shape = replace(Shape(solid=PARAMETRIC_OBJECT, plot=plot(a=LANE, b=OTHER)))
        recurrence = RecurrencePlot('a*sin(n*0.1)', 'c*cos(n*0.1)', variables=(Variable('a', 1., 0., 1.5), Variable('c', 4., 0., 9.)))
        shape = replace(shape, recurrence=recurrence)  # Parked from an earlier Recurrence.
        result = r.replaced(shape, RECURRENCE_OBJECT)
        a, c = result.recurrence.variables
        self.assertEqual((a.value, a.points), (1.5, LANE))  # Value kept, clamped to the new range; lane kept.
        self.assertEqual((c.value, c.points), (4., ()))
        self.assertEqual([n for n, _ in result.lanes], [parked_lane('b')])  # b is parked.
        clip = clip_of(result)
        self.assertEqual(label(clip, ('parked', 0, 'b')), 'Variable b (parked: Parametric)')
        self.assertEqual(static_value(clip, ('parked', 0, 'b')), 3.)
        # And back: a's lane and value come with it, b's lane returns, c is parked (no lane: nothing).
        result = replace(result, recurrence=replace(result.recurrence, variables=(replace(a, value=1.), c)))
        back = r.replaced(result, PARAMETRIC_OBJECT)
        self.assertEqual([(v.name, v.value, v.points) for v in back.plot.variables], [('a', 1., LANE), ('b', 3., OTHER)])
        self.assertEqual(back.lanes, ())

    def test_a_same_name_variable_added_while_parked_takes_the_lane_back(self):
        # Parametric (b automated) › Recurrence parks b's lane; a b added to the recurrence has no
        # lane of its own, so Replace back gives the Parametric b its parked lane.
        shape = r.replaced(Shape(solid=PARAMETRIC_OBJECT, plot=plot(b=OTHER)), RECURRENCE_OBJECT)
        self.assertEqual(shape.lanes, ((parked_lane('b'), OTHER),))
        shape = replace(shape, recurrence=replace(shape.recurrence, variables=(Variable('b', 2., 0., 5.),)))
        back = r.replaced(shape, PARAMETRIC_OBJECT)
        self.assertEqual([(v.name, v.value, v.points) for v in back.plot.variables], [('a', 2., ()), ('b', 2., OTHER)])
        self.assertEqual(back.lanes, ())
        orphan = Shape(lanes=((parked_lane('q'), OTHER),))  # No parked equation declares q.
        self.assertEqual(label(clip_of(orphan), ('parked', 0, 'q')), 'Variable q (parked)')
        self.assertEqual(static_value(clip_of(orphan), ('parked', 0, 'q')), 1.)

    def test_model_and_custom_need_input_unless_parked(self):
        cube = Shape()
        self.assertTrue(r.needs_input(cube, CUSTOM_OBJECT) and r.needs_input(cube, MODEL_OBJECT))
        with self.assertRaises(ValueError): r.replaced(cube, CUSTOM_OBJECT)
        with self.assertRaises(ValueError): r.replaced(cube, MODEL_OBJECT)
        custom = r.replaced(cube, CUSTOM_OBJECT, custom=TRIANGLE)
        parked = r.replaced(custom, 'Cube')
        self.assertEqual(parked.custom, TRIANGLE)
        self.assertFalse(r.needs_input(parked, CUSTOM_OBJECT))
        other = replace(TRIANGLE, name='Other')
        self.assertEqual(r.replaced(parked, CUSTOM_OBJECT, custom=other).custom, TRIANGLE)  # The parked one comes back.

    def test_new_shape_matches_add_object(self):
        from phosphor.engine.object_replace import new_shape
        self.assertEqual(new_shape(TEXT_OBJECT), Shape(solid=TEXT_OBJECT, text='HELLO'))
        self.assertEqual(new_shape(PARAMETRIC_OBJECT), Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot()))
        self.assertEqual(new_shape(RECURRENCE_OBJECT), Shape(solid=RECURRENCE_OBJECT, recurrence=RecurrencePlot()))
        self.assertEqual(new_shape('Cube'), Shape())
        self.assertEqual(r.parked_fields(Shape()), {})
        self.assertEqual(r.parked_fields(Shape(text=DEFAULT_TEXT)), {})


class ParkedLaneTest(unittest.TestCase):
    def parked_shape(self):
        return r.replaced(Shape(solid=PARAMETRIC_OBJECT, plot=plot(a=LANE)), 'Cube')

    def test_lane_access_lists_edits_and_removes_parked_keys(self):
        clip = clip_of(self.parked_shape())
        key = ('parked', 0, 'a')
        self.assertIn(key, automatable(clip))
        self.assertNotIn(('variable', 0, 'a'), automatable(clip))
        self.assertTrue(is_parked(key))
        self.assertEqual([(b.key, b.title, b.keys) for b in blocks(clip)], [(('parked', 0), 'Cube › Parked variables', (key,))])
        self.assertEqual(get_points(clip, key), LANE)
        self.assertEqual(label(clip, key), 'Variable a (parked: Parametric)')
        edited = set_points(clip, key, OTHER)
        self.assertEqual(get_points(edited, key), OTHER)
        removed = set_points(edited, key, ())
        self.assertNotIn(key, automatable(removed))
        self.assertEqual(removed.settings.shapes[0].lanes, ())
        with self.assertRaises(ValueError): set_points(removed, key, LANE)  # No new parked lanes.

    def test_evaluation_and_baking_skip_parked_lanes(self):
        shape = self.parked_shape()
        plain = replace(shape, lanes=())
        self.assertEqual([replace(s, lanes=()) for s in posed_shapes((shape,), 1.25)], list(posed_shapes((plain,), 1.25)))
        times = np.arange(2048) / 48000.
        np.testing.assert_array_equal(render(times, Settings(shapes=(shape,))), render(times, Settings(shapes=(plain,))))
        baked = clip_settings(clip_of(shape), Settings(), 120.)
        self.assertIs(baked.shapes[0], shape)  # Only parked lanes: nothing is baked.
        self.assertEqual(clip_settings(clip_of(replace(shape, lanes=(('position_x', LANE), *shape.lanes))), Settings(), 120.)
                         .shapes[0].lane_clock, (.5, 0.))

    def test_validation(self):
        shape = self.parked_shape()
        validate(Settings(shapes=(shape,)))
        for name in ('1a', 't', 'for'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate(Settings(shapes=(replace(shape, lanes=((parked_lane(name), LANE),)),)))
        guide = Shape(solid=CUSTOM_OBJECT, uid='g', custom=replace(TRIANGLE, motion_path=True), lanes=shape.lanes)
        validate(Settings(shapes=(guide,)))  # Parked lanes are no live lanes on a motion path.
        with self.assertRaises(ValueError):
            validate(Settings(shapes=(replace(guide, lanes=(('position_x', LANE),)),)))


class StorageTest(unittest.TestCase):
    def test_parked_data_saved_and_loaded(self):
        shape = placed(Shape(solid=PARAMETRIC_OBJECT, plot=plot(a=LANE)))
        shape = r.replaced(r.replaced(r.replaced(shape, TEXT_OBJECT), CUSTOM_OBJECT, custom=TRIANGLE), RECURRENCE_OBJECT)
        settings = Settings(shapes=(shape,))
        loaded, document = round_trip(settings)
        item = document['shapes'][0]
        self.assertEqual(document['version'], SCENE_VERSION)
        self.assertEqual(set(item['parked']), {'text', 'custom', 'plot'})
        self.assertEqual(item['parked']['text'], r.ADD_TEXT)
        self.assertIn(parked_lane('a'), item['lanes'])
        self.assertNotIn('custom', item)
        self.assertEqual(loaded.shapes[0], shape)
        # Through a Composer project too (scenes are embedded documents).
        project = Project(notes=(Note(0., 8., 57),), clips=(Clip(0., 8., settings, 'drawing'),))
        reopened = project_from_document(json.loads(json.dumps(project_document(project))))
        self.assertEqual(reopened.clips[0].settings.shapes[0], shape)

    def test_parked_lane_without_a_parked_equation_is_dropped_with_a_notice(self):
        shape = Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot(), lanes=((parked_lane('b'), OTHER),))
        validate(Settings(shapes=(shape,)))  # Valid in a session; a reopened file drops it.
        notices = []
        loaded, _ = round_trip(Settings(shapes=(shape,)), notices)
        self.assertEqual(loaded.shapes[0].lanes, ())
        self.assertEqual(notices, ['Parked automation of variable b dropped: no parked equation declares it.'])

    def test_older_scene_opens_unchanged(self):
        shapes = (placed(Shape()), Shape(solid=TEXT_OBJECT, text='OLD'),
                  Shape(solid=PARAMETRIC_OBJECT, plot=plot(a=LANE)), Shape(solid=CUSTOM_OBJECT, custom=TRIANGLE))
        document = json.loads(json.dumps(scene_document(Settings(shapes=shapes), 'drawing', '')))
        self.assertFalse(any('parked' in item for item in document['shapes']))  # Nothing parked: no new field.
        document['version'] = 22
        loaded = scene_from_document(document)[0]
        self.assertEqual(loaded.shapes, shapes)
        self.assertEqual(scene_document(loaded, 'drawing', '')['shapes'], document['shapes'])
        # Version 22 files cannot carry parked data or parked lanes.
        for change in ({'parked': {'text': 'X'}}, {'lanes': {parked_lane('a'): [[0., 1., 'linear']]}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                scene_from_document(dict(document, shapes=[dict(document['shapes'][0], **change)]))

    def test_invalid_parked_documents_are_refused(self):
        document = json.loads(json.dumps(scene_document(Settings(shapes=(Shape(solid=TEXT_OBJECT, text='T'),)), 'drawing', '')))
        for parked in ({}, {'text': 'X'}, {'colour': 1}, []):
            with self.subTest(parked=parked), self.assertRaises(ValueError):
                scene_from_document(dict(document, shapes=[dict(document['shapes'][0], parked=parked)]))


if __name__ == '__main__':
    unittest.main()
