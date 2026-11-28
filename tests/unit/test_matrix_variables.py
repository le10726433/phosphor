"""Matrix rain speed expressions may read the object's variables (T5): such a scene saves and
reopens (scene, project, clipboard), and rename / remove-variable treat them like step expressions."""
from dataclasses import replace
import json
import unittest

import numpy as np

from phosphor.engine import clipboard
from phosphor.engine.composition import Clip, Project, empty_settings
from phosphor.engine.function_plot import Variable
from phosphor.engine.matrix_rain import MATRIX_OBJECT, MatrixConfig, validate_matrix
from phosphor.engine.signal_engine import Settings, Shape, render, validate
from phosphor.engine.transforms import Param, renamed_variable, variables_in_use
from phosphor.storage.app_files import scene_document, scene_from_document
from phosphor.storage.project_files import project_document, project_from_document


def matrix_shape(text='k*2', fall='', name='k'):
    config = MatrixConfig(text_speed=Param(4., text), fall_speed=Param(1.2, fall))
    return Shape(solid=MATRIX_OBJECT, matrix=config, transform_variables=(Variable(name, 2., 0., 5.),))


class MatrixVariableTests(unittest.TestCase):
    def test_scene_with_a_variable_speed_saves_and_reopens(self):
        settings = Settings(shapes=(matrix_shape(fall='k/4 + 0.5'),))
        validate(settings)
        render(np.arange(480) / 48000., settings)
        document = json.loads(json.dumps(scene_document(settings, 'drawing', '')))
        reopened, _, _ = scene_from_document(document)
        self.assertEqual(reopened.shapes[0].matrix, settings.shapes[0].matrix)
        self.assertEqual(reopened.shapes[0].transform_variables, settings.shapes[0].transform_variables)

    def test_project_and_clipboard_round_trip(self):
        clip = Clip(0., 4., replace(empty_settings(), shapes=(matrix_shape(),)), name='Rain')
        project = Project(clips=(clip,))
        again = project_from_document(json.loads(json.dumps(project_document(project))))
        self.assertEqual(again.clips[0].settings.shapes[0].matrix, clip.settings.shapes[0].matrix)
        payload = clipboard.from_text(clipboard.to_text(clipboard.copy_clips(project, (0,))))
        pasted, _ = clipboard.paste_clips(project, payload, 8.)
        self.assertEqual(pasted.clips[-1].settings.shapes[0].matrix, clip.settings.shapes[0].matrix)

    def test_unknown_names_are_still_refused_on_read(self):
        document = scene_document(Settings(shapes=(matrix_shape(),)), 'drawing', '')
        document['shapes'][0].pop('transform_variables')
        with self.assertRaisesRegex(ValueError, "k"):
            scene_from_document(document)
        with self.assertRaises(ValueError):
            MatrixConfig.read(MatrixConfig(text_speed=Param(4., 'q')).document(), ('k',))
        self.assertEqual(MatrixConfig.read(MatrixConfig(text_speed=Param(4., 'k')).document(), ('k',)).text_speed.expression, 'k')

    def test_parked_matrix_data_reading_a_removed_variable_stays_readable(self):
        """Parked Matrix data (object Replace) is not validated against the variables, as
        signal_engine.validate skips it, so removing the variable never breaks the file."""
        document = scene_document(Settings(shapes=(matrix_shape(),)), 'drawing', '')
        item = document['shapes'][0]
        item['solid'] = 'Cube'; item['parked'] = {'matrix': item.pop('matrix')}; item.pop('transform_variables')
        reopened, _, _ = scene_from_document(json.loads(json.dumps(document)))
        self.assertEqual(reopened.shapes[0].matrix.text_speed.expression, 'k*2')

    def test_rename_rewrites_matrix_speeds_and_remove_sees_them_used(self):
        shape = matrix_shape(fall='1 + k')
        self.assertEqual(variables_in_use(shape), {'k'})
        renamed = renamed_variable(shape, 'k', 'rate')
        self.assertEqual((renamed.matrix.text_speed.expression, renamed.matrix.fall_speed.expression), ('rate*2', '1 + rate'))
        self.assertEqual(renamed.matrix.text_speed.value, shape.matrix.text_speed.value)
        validate(Settings(shapes=(renamed,)))
        unused = matrix_shape(text='')
        self.assertIs(renamed_variable(unused, 'k', 'w').matrix, unused.matrix)  # Untouched config stays the same object.
        self.assertEqual(variables_in_use(matrix_shape(text='')), set())
        removed = replace(shape, transform_variables=())
        with self.assertRaises(ValueError): validate(Settings(shapes=(removed,)))
        with self.assertRaises(ValueError): validate_matrix(removed.matrix, ())


if __name__ == '__main__':
    unittest.main()
