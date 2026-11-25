"""Shared lane geometry needs no QApplication."""
import unittest
import phosphor  # noqa: F401
from PySide6.QtCore import QRectF
from phosphor.editor.lane_strip import LaneStrip, LaneItem, BLOCKS, LINE


class LaneStripTests(unittest.TestCase):
    def setUp(self):
        self.lane = LaneStrip(); self.lane.set_rect(QRectF(64, 100, 400, 60))

    def test_mapping_and_clamping(self):
        for value in (0., .25, .5, .75, 1.):
            self.assertAlmostEqual(self.lane.value_at(self.lane.y_of(value)), value)
        self.assertEqual(self.lane.y_of(1), 104)
        self.assertEqual(self.lane.y_of(0), 156)
        self.assertEqual(self.lane.value_at(-1000), 1)
        self.assertEqual(self.lane.value_at(1000), 0)

    def test_stacked_bars_and_crossing(self):
        items = (LaneItem(1., .2), LaneItem(1., .8), LaneItem(3., .5))
        x_of = lambda beat: 64 + beat * 20
        self.assertEqual(self.lane.bar_at(85, self.lane.y_of(.75), x_of, items), 1)
        self.assertEqual(self.lane.bar_at(84, self.lane.y_of(.25), x_of, items), 0)
        self.assertIsNone(self.lane.bar_at(100, 130, x_of, items))
        self.assertEqual(self.lane.crossed(124, 84, x_of, items), (0, 1, 2))
        self.assertEqual(self.lane.crossed(85, 123, x_of, items), ())

    def test_line_nodes(self):
        lane = LaneStrip(LINE); lane.set_rect(QRectF(64, 100, 400, 60))
        items = (LaneItem(1., 1.), LaneItem(2., .5, hold=True), LaneItem(2.1, .4))
        x_of = lambda beat: 64 + beat * 20
        self.assertEqual(lane.node_at(105, lane.y_of(.5), x_of, items), 1)
        self.assertEqual(lane.node_at(106, lane.y_of(.4), x_of, items), 2)
        self.assertIsNone(lane.node_at(105, lane.y_of(.9), x_of, items))  # Both directions count.
        self.assertIsNone(lane.node_at(300, 130, x_of, items))
        with self.assertRaises(ValueError): LaneStrip('dots')

    def test_blocks(self):
        lane = LaneStrip(BLOCKS)
        items = (LaneItem(1., 1., 2.), LaneItem(3., 0., 5.))
        self.assertEqual(lane.block_at(1., items), 0)
        self.assertIsNone(lane.block_at(2., items))
        self.assertEqual(lane.block_at(4., items), 1)
