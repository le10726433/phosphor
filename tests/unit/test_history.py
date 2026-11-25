"""Composer history without a QApplication."""
from dataclasses import replace
import unittest

from phosphor.editor.history import History, State, describe
from phosphor.engine.composition import new_project, Note


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.project = new_project()
        self.a = State(self.project)
        self.b = State(replace(self.project, bpm=130), 0, 2)
        self.c = State(replace(self.project, bpm=140))
        self.h = History(); self.h.reset(self.a)

    def test_push_undo_redo_and_selection(self):
        self.assertIsNone(self.h.undo()); self.assertIsNone(self.h.redo())
        self.assertFalse(self.h.dirty)
        self.h.push('Change tempo', self.b)
        self.assertEqual(self.h.undo_label, 'Change tempo')
        self.assertTrue(self.h.can_undo); self.assertTrue(self.h.dirty)
        self.assertIs(self.h.undo(), self.a)
        self.assertEqual(self.h.redo_label, 'Change tempo')
        self.assertIs(self.h.redo(), self.b)
        self.assertEqual(self.h.state.selected_object, 2)

    def test_branch(self):
        self.h.push('B', self.b); self.h.undo(); self.h.push('C', self.c)
        self.assertFalse(self.h.can_redo); self.assertIsNone(self.h.redo())

    def test_cap(self):
        for i in range(250): self.h.push(str(i), State(replace(self.project, bpm=i)))
        count = 0
        while self.h.can_undo: self.h.undo(); count += 1
        self.assertEqual(count, 199)
        self.assertTrue(self.h.dirty)  # Saved entry was evicted.

    def test_coalescing(self):
        self.h.push('First', self.b, key=1, now=1.)
        self.h.push('Second', self.c, key=1, now=1.399)
        self.assertEqual(self.h.undo_label, 'First')
        self.assertIs(self.h.undo(), self.a)
        self.h.redo()
        self.h.push('Different', self.b, key=2, now=1.4)
        self.h.push('Later', self.c, key=2, now=1.801)
        self.assertEqual(self.h.undo_label, 'Later')
        self.assertIs(self.h.undo(), self.b)
        self.assertEqual(self.h.undo_label, 'Different')

    def test_none_key_does_not_merge(self):
        self.h.push('B', self.b, now=0)
        self.h.push('C', self.c, now=.1)
        self.assertIs(self.h.undo(), self.b)

    def test_different_key_and_later_push(self):
        self.h.push('B', self.b, key='slider', now=0)
        self.h.push('C', self.c, key='tempo', now=.1)
        self.assertIs(self.h.undo(), self.b)
        self.h.reset(self.a)
        self.h.push('B', self.b, key='slider', now=0)
        self.h.push('C', self.c, key='slider', now=.401)
        self.assertIs(self.h.undo(), self.b)

    def test_saved_entry(self):
        self.h.push('B', self.b, key=1, now=0); self.h.mark_saved()
        self.assertFalse(self.h.dirty)
        self.h.push('C', self.c, key=1, now=.1)
        self.assertTrue(self.h.dirty)
        self.h.undo(); self.assertFalse(self.h.dirty)
        self.h.undo(); self.assertTrue(self.h.dirty)
        self.h.redo(); self.assertFalse(self.h.dirty)
        self.h.redo(); self.assertTrue(self.h.dirty)
        self.h.reset(self.a); self.assertFalse(self.h.dirty)
        self.assertFalse(self.h.can_undo); self.assertFalse(self.h.can_redo)

    def test_describe(self):
        p = self.project; clip = p.clips[0]
        cases = [
            (replace(p, bpm=130), 'Change tempo'),
            (replace(p, loop=True), 'Change loop'),
            (replace(p, loop_start=1), 'Change loop'),
            (replace(p, loop_end=8), 'Change loop'),
            (replace(p, clips=p.clips * 2), 'Add clip'),
            (replace(p, clips=()), 'Delete clip'),
            (replace(p, clips=(replace(clip, start=1),)), 'Move clip'),
            (replace(p, clips=(replace(clip, length=8),)), 'Resize clip'),
            (replace(p, clips=(replace(clip, lanes=(('drawing_gain', ((0., .5, 'linear'),)),)),)), 'Edit automation'),
            (replace(p, clips=(replace(clip, settings=replace(clip.settings, drawing_gain=.3)),)), 'Edit scene'),
            (replace(p, clips=(replace(clip, mode='music'),)), 'Edit scene'),
            (replace(p, notes=(Note(0, 1, 60),)), 'Edit notes'),
            (replace(p, clips=(replace(clip, name='Other'),)), 'Rename clip'),
            (replace(p, clips=(replace(clip, local_time=False),)), 'Edit'),
        ]
        for after, label in cases:
            with self.subTest(label=label): self.assertEqual(describe(p, after), label)
        self.assertEqual(describe(p, replace(cases[6][0], bpm=130)), 'Change tempo')


if __name__ == '__main__': unittest.main()
