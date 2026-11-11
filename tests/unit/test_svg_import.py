"""SVG outline parsing, simplification into drawings, and per-axis spin."""
from pathlib import Path
import tempfile
import unittest
import numpy as np
from phosphor.storage.app_files import atomic_json, read_scene, scene_document
from phosphor.engine.custom_shape import CUSTOM_OBJECT, MAX_EDGES, MAX_NODES, compiled
from phosphor.editor.qt_scene import Scene
from phosphor.engine.scene_path import connector_segments
from phosphor.engine.signal_engine import Settings, Shape, planned_path, projected_solid, validate
from phosphor.engine.svg_import import drawing_from_svg, load_svg, parse_path, parse_svg, simplify

NS = 'xmlns="http://www.w3.org/2000/svg"'


def svg(body):
    return f'<svg {NS}>{body}</svg>'


def ends(data):
    return [(tuple(np.round(p[0], 6)), tuple(np.round(p[-1], 6)), closed) for p, closed in parse_path(data)]


class PathTests(unittest.TestCase):
    def test_lines_relative_and_implicit_commands(self):
        points, closed = parse_path('M1 1 2 1 2 2z')[0]
        np.testing.assert_allclose(points, [[1, 1], [2, 1], [2, 2]])
        self.assertTrue(closed)
        np.testing.assert_allclose(parse_path('m1 1l1 0h2v3')[0][0][-1], [4, 4])

    def test_curves_end_exactly_on_target(self):
        for data, end in (('M0 0C1 1 2 1 3 0', (3, 0)), ('M0 0Q1 1 2 0T4 0', (4, 0)), ('M0 0C0 1 1 1 1 0S2-1 2 0', (2, 0))):
            with self.subTest(data=data):
                np.testing.assert_allclose(parse_path(data)[0][0][-1], end)

    def test_compact_arc_flags_and_circle_radius(self):
        points = parse_path('M0 0a1 1 0 11 2 0 1 1 0 11-2 0z')[0][0]
        np.testing.assert_allclose(points.max(axis=0)-points.min(axis=0), (2, 2), atol=.01)

    def test_multiple_subpaths(self):
        self.assertEqual(len(parse_path('M0 0L1 0M5 5L6 5')), 2)

    def test_bad_data_raises(self):
        for data in ('M0', 'M0 0B1 1', 'M0 0A1 1 0 2 0 1 1'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_path(data)


class DocumentTests(unittest.TestCase):
    def test_shapes_transforms_and_hidden_content(self):
        found, skipped = parse_svg(svg('<g transform="translate(10 0)"><rect width="2" height="1"/></g><g display="none"><line x2="9"/></g>'
                                       '<defs><circle r="5"/></defs><text>a</text><image/>'))
        self.assertEqual(skipped, ('image', 'text'))
        self.assertEqual(len(found), 1)
        np.testing.assert_allclose(found[0][0][0], (10, 0))

    def test_rotate_about_a_point(self):
        found, _ = parse_svg(svg('<line x1="1" y1="0" x2="2" y2="0" transform="rotate(90 1 0)"/>'))
        np.testing.assert_allclose(found[0][0], [[1, 0], [1, 1]], atol=1e-9)

    def test_invalid_documents_are_rejected(self):
        for text in ('not xml', f'<html {NS}/>', svg('<text>a</text>'), svg('<line/>'),
                     '<!DOCTYPE svg [<!ENTITY a "b">]><svg/>'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                drawing_from_svg(text)

    def test_y_axis_points_up(self):
        drawing, _ = drawing_from_svg(svg('<polyline points="0,0 10,0 10,20"/>'))
        self.assertEqual(len(drawing.edges), 2)
        self.assertEqual({(x, y) for _, x, y in drawing.nodes}, {(-.5, 1.), (.5, 1.), (.5, -1.)})

    def test_shared_endpoints_join_and_duplicates_merge(self):
        drawing, _ = drawing_from_svg(svg('<line x2="5"/><line x2="5"/><line x1="5" x2="5" y2="5"/>'))
        self.assertEqual((len(drawing.nodes), len(drawing.edges)), (3, 2))

    def test_load_uses_the_file_name(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'badge.svg'
            path.write_text(svg('<line x2="5"/>'), encoding='utf-8')
            self.assertEqual(load_svg(path)[0].name, 'badge')


class SimplifyTests(unittest.TestCase):
    def test_straight_runs_collapse_and_corners_stay(self):
        points = np.column_stack((np.r_[np.linspace(0, 1, 50), np.ones(50)], np.r_[np.zeros(50), np.linspace(0, 1, 50)]))
        np.testing.assert_allclose(simplify(points, False, .001), [[0, 0], [1, 0], [1, 1]])

    def test_closed_paths_keep_area(self):
        theta = np.linspace(0, 2*np.pi, 200, endpoint=False)
        self.assertGreater(len(simplify(np.column_stack((np.cos(theta), np.sin(theta))), True, .01)), 8)

    def test_dense_art_fits_the_custom_shape_limits(self):
        theta = np.linspace(0, 2*np.pi, 5000)
        wave = ' '.join(f'{np.cos(t)*(100+9*np.sin(40*t)):.3f},{np.sin(t)*(100+9*np.sin(40*t)):.3f}' for t in theta)
        drawing, report = drawing_from_svg(svg(f'<polygon points="{wave}"/>'))
        self.assertLessEqual(len(drawing.nodes), MAX_NODES)
        self.assertLessEqual(len(drawing.edges), MAX_EDGES)
        self.assertTrue(report.simplified)

    def test_many_tiny_parts_report_what_was_dropped(self):
        body = '<rect width="1000" height="1000"/>'+''.join(f'<circle cx="{(i % 30)*33}" cy="{(i//30)*33}" r="1"/>' for i in range(300))
        drawing, report = drawing_from_svg(svg(body))
        self.assertLessEqual(len(drawing.nodes), MAX_NODES)
        self.assertGreater(report.dropped, 0)


class ConnectorTests(unittest.TestCase):
    def test_separate_parts_follow_faster_travel(self):
        drawing, _ = drawing_from_svg(svg('<rect width="2" height="2"/><rect x="10" width="2" height="2"/>'))
        shapes = (Shape(solid=CUSTOM_OBJECT, custom=drawing),)
        route, travel = compiled(drawing)[2], np.array(compiled(drawing)[3])
        steps = np.r_[connector_segments(route, travel), False]
        plain = np.diff(planned_path(0, 60, shapes, 'No changes', 8)[2])
        faster = np.diff(planned_path(0, 60, shapes, 'Faster travel', 8)[2])
        self.assertTrue(steps.any())
        np.testing.assert_allclose(faster[steps], plain[steps]/8)
        np.testing.assert_allclose(faster[~steps], plain[~steps])

    def test_scene_adds_an_editable_shape(self):
        scene = Scene()
        scene.add_drawing(drawing_from_svg(svg('<line x2="5"/>'), 'mark')[0])
        self.assertEqual((scene.shape.solid, scene.shape.custom.name, scene.selected), (CUSTOM_OBJECT, 'mark', 1))


class SpinTests(unittest.TestCase):
    POINT = np.array([[1., .5, .25]])

    def pose(self, seconds, **spin):
        # Text has no built-in tilt, so expected poses are exact axis turns.
        return projected_solid(seconds, Shape('Text', **spin), vertices=self.POINT, include_depth=True)[0]

    def test_each_axis_turns_at_its_rate(self):
        x, y, z = self.POINT[0]
        np.testing.assert_allclose(self.pose(.25, spin_z=1.), (-y, x, z), atol=1e-9)
        np.testing.assert_allclose(self.pose(.25, spin_y=1.), (z, y, -x), atol=1e-9)
        np.testing.assert_allclose(self.pose(.25, spin_x=1.), (x, -z, y), atol=1e-9)

    def test_full_revolutions_return_and_negative_reverses(self):
        np.testing.assert_allclose(self.pose(2., spin_x=.5, spin_y=1.5, spin_z=-2.), self.pose(0), atol=1e-9)
        np.testing.assert_allclose(self.pose(.25, spin_z=-1.), self.pose(.75, spin_z=1.), atol=1e-9)

    def test_spin_adds_to_view_rotation(self):
        moved = projected_solid(.25, Shape('Text', spin_z=1., rotation_z=90.), vertices=self.POINT)
        np.testing.assert_allclose(moved, projected_solid(0, Shape('Text', rotation_z=180.), vertices=self.POINT), atol=1e-9)

    def test_spin_reaches_the_planned_audio_path(self):
        shapes = (Shape(spin_y=1.),)
        first, later = planned_path(0, 60, shapes, 'No changes', 8), planned_path(15, 60, shapes, 'No changes', 8)
        self.assertFalse(np.allclose(first[0], later[0]))

    def test_limits_are_enforced(self):
        validate(Settings(shapes=(Shape(spin_x=10.),)))
        for value in (10.5, -11., float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate(Settings(shapes=(Shape(spin_y=value),)))

    def test_scene_storage_only_changes_when_spin_is_used(self):
        self.assertEqual(scene_document(Settings(), 'mixed', '')['version'], 8)
        self.assertNotIn('spin_x', scene_document(Settings(shapes=(Shape(),)), 'mixed', '')['shapes'][0])
        settings = Settings(shapes=(Shape(spin_z=2.5),))
        document = scene_document(settings, 'mixed', '')
        self.assertEqual(document['version'], 8)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'spin.json'
            atomic_json(path, document)
            self.assertEqual(read_scene(path)[0].shapes, settings.shapes)
