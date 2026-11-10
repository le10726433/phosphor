import unittest
from dataclasses import replace
import numpy as np
from custom_shape import Drawing,Curve,compiled
from builder_selection import axis_delta,intersects_box

class SelectionTests(unittest.TestCase):
    def fixture(self):
        return Drawing('Groups',((0,-1,0),(1,0,0),(2,1,0),(3,-1,-1),(4,1,-1)),((0,1),(1,2)),(Curve('Semicircle',(3,4)),))

    def test_connected_translation_and_independent_paste(self):
        d=self.fixture();self.assertEqual(d.connected((0,)),frozenset((0,1)))
        moved=d.translate((1,),(.25,.5))
        np.testing.assert_allclose(np.array(moved.nodes)[:3,1:],np.array(d.nodes)[:3,1:]+[.25,.5])
        self.assertEqual(moved.nodes[3:],d.nodes[3:]);self.assertEqual(moved.curves,d.curves)
        copy=d.subset((0,1,2));pasted,selection=d.paste(copy,(.2,-.2))
        self.assertEqual(len(selection),3);self.assertEqual(len({n[0] for n in pasted.nodes}),10)
        self.assertEqual(pasted.connected((2,)),frozenset((2,3))) # pasted lines inserted before original curve
        self.assertEqual(pasted.without(selection),d)
        self.assertEqual(d.nodes[0],(0,-1,0))
        compiled(pasted)

    def test_constraints_and_rectangles_include_crossing_strokes(self):
        np.testing.assert_array_equal(axis_delta((2,1)),(2,0))
        np.testing.assert_array_equal(axis_delta((1,-2)),(0,-2))
        self.assertTrue(intersects_box(np.array([[-2,0],[2,0]]),(-.1,-.1),(.1,.1)))
        self.assertFalse(intersects_box(np.array([[-2,1],[2,1]]),(-.1,-.1),(.1,.1)))
        self.assertTrue(intersects_box(np.array([[0,0],[0,1]]),(.1,1),(-.1,0)))

    def test_overflow_and_out_of_range_moves_are_atomic(self):
        d=self.fixture()
        with self.assertRaises(ValueError):d.translate((0,),(200,0))
        large=replace(d,nodes=tuple((k,0.,float(k)/10) for k in range(128)),edges=((0,1),),curves=())
        with self.assertRaises(ValueError):large.paste(d,(0,0))
        self.assertEqual(len(large.nodes),128)
