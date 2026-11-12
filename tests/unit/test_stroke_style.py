import tempfile
import unittest
from pathlib import Path

import numpy as np

from phosphor.engine.custom_shape import Curve, Drawing, compiled, paths, text_drawing
from phosphor.engine.stroke_style import Style, _distance, bold_outline, corner_indices, set_style, style_of

# An H: stems split at the crossbar, so three strokes meet at two nodes.
H = Drawing('H', ((0, 0., 0.), (1, 0., 1.), (2, 0., .5), (3, .6, 0.), (4, .6, 1.), (5, .6, .5)),
            ((0, 2), (2, 1), (3, 5), (5, 4), (2, 5)))


def degrees(edges, count):
    return np.bincount(np.asarray(edges).ravel(), minlength=count)


class BoldOutlineTests(unittest.TestCase):
    def test_outline_lies_on_the_union_boundary(self):
        lines = list(paths(H))
        pieces = bold_outline(lines, .12)
        starts = np.vstack([p[:-1] for p in lines]); ends = np.vstack([p[1:] for p in lines])
        distance = _distance(np.vstack(pieces), starts, ends)
        self.assertGreater(distance.min(), .06*.97)  # Nothing inside the fattened strokes.
        self.assertLess(distance.max(), .06*1.03)    # Nothing away from them.

    def test_joined_strokes_trace_one_closed_outline(self):
        vertices, edges, route, travel = compiled(set_style(H, {0}, bold=.12))
        self.assertEqual(travel, ())
        self.assertFalse((degrees(edges, len(vertices)) % 2).any())  # One Eulerian loop.
        self.assertEqual(route[0], route[-1])
        np.testing.assert_array_equal(vertices[:, 2], 0.)

    def test_letters_need_travel_only_for_their_holes(self):
        for letter, holes in (('H', 0), ('E', 0), ('T', 0), ('K', 0), ('S', 0), ('O', 1), ('A', 1), ('B', 2), ('8', 2)):
            drawing = text_drawing(letter, .3)
            styled = set_style(drawing, set(range(len(drawing.segments))), bold=.04)
            self.assertEqual(len(compiled(styled)[3]), holes, letter)

    def test_closed_curves_get_inner_and_outer_rings(self):
        ring = Drawing('ring', ((0, 0., 0.), (1, .5, 0.)), (), (Curve('Circle', (0, 1)),))
        vertices, _, _, travel = compiled(set_style(ring, {0}, bold=.1))
        radii = np.hypot(vertices[:, 0], vertices[:, 1])
        self.assertAlmostEqual(radii.min(), .45, 3); self.assertAlmostEqual(radii.max(), .55, 3)
        self.assertEqual(len(travel), 1)

    def test_too_detailed_outlines_are_refused(self):
        drawing = text_drawing('WWWWWWWWWWWW', 2.)
        with self.assertRaisesRegex(ValueError, 'too detailed'):
            bold_outline(list(paths(drawing)), 2e-4)


class ExtrudeTests(unittest.TestCase):
    def test_front_and_back_copies_joined_at_corners(self):
        square = Drawing('sq', ((0, 0., 0.), (1, 1., 0.), (2, 1., 1.), (3, 0., 1.)), ((0, 1), (1, 2), (2, 3), (3, 0)))
        vertices, edges, route, travel = compiled(set_style(square, {0}, depth=.4))
        self.assertEqual(len(vertices), 8); self.assertEqual(len(edges), 12)  # A cube's wireframe.
        self.assertEqual(sorted(set(vertices[:, 2])), [-.2, .2])
        self.assertEqual(travel, ())

    def test_bold_and_extrude_combine(self):
        vertices, edges, _, travel = compiled(set_style(set_style(H, {0}, bold=.12), {0}, depth=.3))
        front, back = vertices[vertices[:, 2] > 0], vertices[vertices[:, 2] < 0]
        self.assertEqual(len(front), len(back)); np.testing.assert_allclose(front[:, :2], back[:, :2])
        self.assertEqual(travel, ())

    def test_corner_detection(self):
        self.assertEqual(corner_indices(np.array([[0, 0], [1, 0], [1, 1]], float)), [0, 1, 2])
        circle = np.column_stack((np.cos(np.linspace(0, 2*np.pi, 65)), np.sin(np.linspace(0, 2*np.pi, 65))))
        self.assertEqual(corner_indices(circle), [0])  # Smooth rings are tied once.


class StyleDataTests(unittest.TestCase):
    def test_styles_follow_shapes_through_edits(self):
        two = H.paste(H, (2., 0.))[0]
        styled = set_style(two, {0}, bold=.1, depth=.2)
        self.assertEqual(style_of(styled, {0}).bold, .1); self.assertIsNone(style_of(styled, {5}))
        moved = styled.translate({0}, (.3, .3))
        self.assertEqual(moved.styles, styled.styles)
        copy, selection = styled.paste(styled.subset({0, 1, 2, 3, 4}), (0., 3.))
        self.assertEqual(style_of(copy, selection).depth, .2)
        cleared = set_style(styled, {0}, bold=0., depth=0.)
        self.assertEqual(cleared.styles, ())

    def test_joining_shapes_keeps_one_style(self):
        two = H.paste(H, (2., 0.))[0]
        styled = set_style(set_style(two, {0}, bold=.1), {5}, depth=.5)
        from phosphor.engine.drawing_motion import reconcile
        joined = reconcile(styled.move(6, 0., 0., join=0))  # A node of the second H onto the first.
        self.assertEqual(len(joined.styles), 1); self.assertEqual(joined.styles[0].bold, .1)

    def test_documents_round_trip_and_reject_bad_values(self):
        styled = set_style(H, {0}, bold=.12, depth=.3)
        self.assertEqual(Drawing.read(styled.document()), styled)
        self.assertNotIn('styles', H.document())
        for bad in (dict(nodes=[99], bold=.1, depth=0.), dict(nodes=[0], bold=-1, depth=0.),
                    dict(nodes=[0], bold=0., depth=0.), dict(nodes=[0], bold=10**400, depth=0.),
                    dict(nodes=[0], bold='1', depth=0.), dict(nodes=[0], bold=.1)):
            with self.assertRaises(ValueError, msg=bad): Drawing.read(dict(H.document(), styles=[bad]))

    def test_scene_files_store_styles(self):
        from phosphor.engine.signal_engine import Settings, Shape
        from phosphor.engine.custom_shape import CUSTOM_OBJECT
        from phosphor.storage.app_files import atomic_json, read_scene, scene_document
        styled = set_style(H, {0}, bold=.12, depth=.3)
        settings = Settings(shapes=(Shape(solid=CUSTOM_OBJECT, custom=styled),))
        document = scene_document(settings, 'drawing', '')
        self.assertEqual(document['version'], 10)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'styled.scene.json'; atomic_json(path, document)
            self.assertEqual(read_scene(path)[0].shapes[0].custom, styled)


class TextDrawingTests(unittest.TestCase):
    def test_text_becomes_centred_strokes(self):
        drawing = text_drawing('Hi!', .6)
        points = np.array([n[1:] for n in drawing.nodes])
        self.assertAlmostEqual(np.ptp(points[:, 1]), .6)
        np.testing.assert_allclose((points.min(axis=0)+points.max(axis=0))/2, 0., atol=1e-12)
        self.assertEqual(drawing.name, 'Hi!')
        self.assertEqual(len(compiled(drawing)[3]), 3)  # H, I and ! (two strokes) apart.

    def test_text_limits(self):
        for text, message in (('   ', 'Type'), ('aé', 'Use letters'), ('8'*40, 'nodes')):
            with self.assertRaisesRegex(ValueError, message): text_drawing(text)
        with self.assertRaises(ValueError): text_drawing('A', 0.)
        with self.assertRaisesRegex(ValueError, 'too large'): text_drawing('W'*20, 20.)


if __name__ == '__main__':
    unittest.main()
