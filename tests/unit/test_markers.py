import copy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from phosphor.storage.project_files import PROJECT_VERSION
from phosphor.engine.composition import (Project, Note, new_project, MAX_BEATS, add_marker, move_marker,
    rename_marker, delete_marker, validate_project)
from phosphor.engine import clipboard
from phosphor.storage.project_files import (project_document, project_from_document,
    save_project, read_project)
from phosphor.storage.project_files import PROJECT_VERSION


class MarkerTests(unittest.TestCase):
    def test_clipboard_leaves_markers_in_place(self):
        source = replace(new_project(), markers=((0., 'Source'),), notes=(Note(0., 1., 60),))
        target = replace(new_project(), markers=((4., 'Target'),))
        for copy, paste in ((clipboard.copy_notes, clipboard.paste_notes),
                            (clipboard.copy_clips, clipboard.paste_clips)):
            payload = clipboard.from_text(clipboard.to_text(copy(source, [0])))
            pasted, _ = paste(target, payload, 20.)
            self.assertEqual(pasted.markers, target.markers)
            self.assertEqual(source.markers, ((0., 'Source'),))

    def test_add_move_rename_delete(self):
        original = Project()
        project = add_marker(add_marker(original, 8., 'Verse'), 2., 'Intro')
        self.assertEqual(project.markers, ((2., 'Intro'), (8., 'Verse')))
        project = move_marker(project, 0, 12.)
        self.assertEqual(project.markers, ((8., 'Verse'), (12., 'Intro')))
        project = rename_marker(project, 1, 'Outro')
        self.assertEqual(project.markers[1], (12., 'Outro'))
        self.assertEqual(delete_marker(project, 0).markers, ((12., 'Outro'),))
        self.assertEqual(original.markers, ())

    def test_validation(self):
        validate_project(Project(markers=((0., ''), (MAX_BEATS, 'x' * 64))))
        validate_project(Project(markers=((0., 'A'),) * 256))
        invalid = [[], ((-1., 'bad'),), ((MAX_BEATS + 1, 'bad'),),
                   ((float('nan'), 'bad'),), ((float('inf'), 'bad'),),
                   ((True, 'bad'),), (('1', 'bad'),), ((1., None),),
                   ((1., 'x' * 65),), ((2., 'B'), (1., 'A')),
                   ((0., 'A'),) * 257, ((1.,),), ([1., 'A'],)]
        for markers in invalid:
            with self.subTest(markers=repr(markers)[:80]), self.assertRaises(ValueError):
                validate_project(replace(Project(), markers=markers))
        for operation in (lambda: add_marker(Project(), -1.),
                          lambda: move_marker(Project(markers=((0., 'A'),)), 0, MAX_BEATS + 1),
                          lambda: rename_marker(Project(markers=((0., 'A'),)), 0, 'x' * 65),
                          lambda: add_marker(Project(markers=((0., 'A'),) * 256), 1.)):
            with self.assertRaises(ValueError): operation()

    def test_file_round_trip_and_v2(self):
        project = Project(markers=((0., 'Intro'), (8.5, 'Chorus — reprise')))
        document = project_document(project)
        self.assertEqual(document['version'], PROJECT_VERSION)
        self.assertEqual(project_from_document(document), project)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'markers.project.json'
            save_project(path, project)
            self.assertEqual(read_project(path), project)
        legacy = project_document(Project())
        legacy['version'] = 2
        del legacy['chords']
        del legacy['velocity']
        self.assertEqual(project_from_document(legacy).markers, ())
        legacy['version'] = 1
        self.assertEqual(project_from_document(legacy).markers, ())
        self.assertEqual(project_from_document(project_document(Project())).markers, ())
        for markers in ('bad', [[2., 'B'], [1., 'A']], [[1., 'x' * 65]], [[1.]]):
            bad = copy.deepcopy(document); bad['markers'] = markers
            with self.assertRaises(ValueError): project_from_document(bad)
