"""Composer clipboard round trips, placement and hostile input checks."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from phosphor.engine import clipboard as cb
from phosphor.engine.composition import (
    Project, Note, Clip, empty_settings, validate_project, MAX_BEATS,
)


class ClipboardTests(unittest.TestCase):
    def roundtrip(self, payload):
        result = cb.from_text(cb.to_text(payload))
        self.assertEqual(result, payload)
        return result

    def test_notes(self):
        project = Project(notes=(Note(8, 2, 64), Note(4, 1, 62), Note(4, 3, 60)))
        payload = self.roundtrip(cb.copy_notes(project, (0, 1, 2)))
        self.assertEqual([n[0] for n in payload['notes']], [0, 0, 4])
        self.assertEqual([n[2] for n in payload['notes']], [60, 62, 64])
        result, indices = cb.paste_notes(project, payload, 20)
        self.assertEqual(indices, (3, 4, 5))
        self.assertEqual([result.notes[i].start for i in indices], [20, 20, 24])
        self.assertEqual(validate_project(result), result)
        result, indices = cb.paste_notes(Project(), payload, MAX_BEATS)
        self.assertTrue(all(result.notes[i].end <= MAX_BEATS for i in indices))
        validate_project(result)
        self.assertEqual(cb.describe(payload), '3 notes')

    def test_clips(self):
        settings = replace(empty_settings(), drawing_gain=.42)
        lane = (('music_gain', ((0., .5, 'hold'), (2., 1., 'linear'))),)
        source = Project(clips=(Clip(3, 4, settings, 'mixed', 'First', False, lane),
                                Clip(9, 2, settings, name='Second')))
        payload = self.roundtrip(cb.copy_clips(source, (1, 0)))
        self.assertEqual([c['offset'] for c in payload['clips']], [0, 6])
        self.assertIsInstance(payload['clips'][0]['scene'], dict)
        result, indices = cb.paste_clips(Project(), payload, 20)
        self.assertEqual(indices, (0, 1))
        self.assertEqual([c.start for c in result.clips], [20, 26])
        self.assertEqual(result.clips[0].settings, settings)
        self.assertEqual(result.clips[0].lanes, lane)
        self.assertFalse(result.clips[0].local_time)
        validate_project(result)
        one = cb.copy_clips(source, (0,))
        target = Project(clips=(Clip(0, 4, settings), Clip(6, 4, settings)))
        for beat, expected in ((5, (5, 1)), (2, (4, 2))):
            result, indices = cb.paste_clips(target, one, beat)
            pasted = result.clips[indices[0]]
            self.assertEqual((pasted.start, pasted.length), expected)
            validate_project(result)
        with self.assertRaisesRegex(ValueError, 'There is no room for the clips there.'):
            cb.paste_clips(target, one, MAX_BEATS)
        self.assertEqual(cb.describe(one), '1 clip')

    def test_nodes(self):
        points = ((2., .5, 'linear'), (4., 1., 'hold'), (7., -.5, 'linear'))
        payload = self.roundtrip(cb.copy_nodes(points, (2, 0, 1)))
        self.assertEqual([p[0] for p in payload['nodes']], [0, 2, 5])
        existing = ((0., 1., 'hold'), (9., 1., 'hold'), (11., 1., 'hold'), (18., 1., 'hold'))
        result, indices = cb.paste_nodes(existing, payload, 8, 12)
        self.assertEqual([p[0] for p in result], [0, 8, 10, 11, 18])
        self.assertEqual(indices, (1, 2))
        result, indices = cb.paste_nodes(existing, payload, 8, 20)
        self.assertEqual([p[0] for p in result], [0, 8, 10, 13, 18])
        self.assertEqual(indices, (1, 2, 3))
        self.assertEqual(cb.describe(payload), '3 automation nodes')
        with self.assertRaises(ValueError):
            cb.paste_nodes(points, payload, 30, 20)

    def test_bad_documents(self):
        note = cb.copy_notes(Project(notes=(Note(0, 1, 60),)), (0,))
        clip = cb.copy_clips(Project(clips=(Clip(0, 4, empty_settings()),)), (0,))
        node = cb.copy_nodes(((0., 1., 'linear'), (2., 0., 'hold')), (0, 1))
        bad = [None, '', '{', '[]', '{"x": NaN}',
               '{"format":1,"format":2}', json.dumps(dict(note, extra=1)),
               json.dumps(dict(note, format='wrong')), json.dumps(dict(note, version=True)),
               json.dumps(dict(note, kind='unknown')), json.dumps(dict(note, notes=[])),
               json.dumps(dict(note, notes=[[True, 1, 60, .5]])),
               json.dumps(dict(note, notes=[[0, 0, 60, .5]])),
               json.dumps(dict(note, notes=[[0, 1, 109, .5]])),
               json.dumps(dict(note, notes=[[0, 1, 60.0, .5]])),
               json.dumps(dict(note, notes=[[0, 1, 60, 2]])),
               json.dumps(dict(note, notes=[[MAX_BEATS, 1, 60, .5]])),
               json.dumps(dict(note, notes=note['notes'] * (cb.MAX_NOTES + 1))),
               json.dumps(dict(node, nodes=list(reversed(node['nodes'])))),
               json.dumps(dict(node, nodes=[[0, 1, 'bad']])),
               json.dumps(dict(node, nodes=[[0, True, 'hold']])),
               json.dumps(dict(node, nodes=[[0, 1, 'hold'], [0, 0, 'hold'], [0, .5, 'hold']])),
               json.dumps(dict(node, nodes=[[1, 1, 'hold']]))]
        for field, value in [('mode', 'bad'), ('name', 'x' * 65), ('local_time', 1),
                             ('offset', -1), ('length', False), ('extra', 1),
                             ('lanes', {'unknown': [[0, 1, 'hold']]}),
                             ('lanes', {'music_gain': []})]:
            item = deepcopy(clip)
            item['clips'][0][field] = value
            bad.append(json.dumps(item))
        item = deepcopy(clip)
        item['clips'][0]['scene']['version'] = -1
        bad.append(json.dumps(item))
        item = deepcopy(clip)
        item['clips'][0]['scene']['settings']['extra'] = 1
        bad.append(json.dumps(item))
        for text in bad:
            with self.subTest(text=str(text)[:120]), self.assertRaises(ValueError):
                cb.from_text(text)
        with patch.object(cb, 'MAX_TEXT', 10), self.assertRaises(ValueError):
            cb.from_text(json.dumps(note))

    def test_selection_and_capacity(self):
        project = Project(notes=(Note(0, 1, 60),), clips=(Clip(0, 4, empty_settings()),))
        for indices in ((), (-1,), (1,), (True,), ('0',), None):
            for copy in (cb.copy_notes, cb.copy_clips):
                with self.subTest(copy=copy, indices=indices), self.assertRaises(ValueError):
                    copy(project, indices)
        payload = cb.copy_notes(project, (0,))
        with patch.object(cb, 'MAX_NOTES', 1), self.assertRaises(ValueError):
            cb.paste_notes(project, payload, 4)
        payload = cb.copy_clips(project, (0,))
        with patch.object(cb, 'MAX_CLIPS', 1), self.assertRaises(ValueError):
            cb.paste_clips(project, payload, 4)
        points = tuple((float(i), 1., 'hold') for i in range(cb.MAX_POINTS))
        payload = cb.copy_nodes(points, (0,))
        with self.assertRaises(ValueError):
            cb.paste_nodes(points, payload, 300, 400)


if __name__ == '__main__':
    unittest.main()
