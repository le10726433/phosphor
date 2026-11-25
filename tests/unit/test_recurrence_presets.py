import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from phosphor.engine.function_plot import Variable
from phosphor.engine.recurrence import PRESETS, RecurrencePlot
from phosphor.storage.function_presets import FUNCTION_STORE, PRESETS as FUNCTION_PRESETS
from phosphor.storage.recurrence_presets import (RECURRENCE_STORE, delete_recurrence_preset,
                                                 read_recurrence_presets, save_recurrence_preset)


class RecurrencePresetTests(unittest.TestCase):
    def test_save_read_replace_delete(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'recurrence-presets.json'
            self.assertEqual(read_recurrence_presets(path), {})
            plot = RecurrencePlot('x[n-1] + a', 'y[n-2]', '0.5', order=2,
                                  initial=((0., 1., 0.), (.5, .5, 0.)), steps=64, draw='points',
                                  variables=(Variable('a', .25, 0., 1.),))
            save_recurrence_preset('  Mine  ', plot, path)
            self.assertEqual(read_recurrence_presets(path), {'Mine': plot})
            save_recurrence_preset('Other', RecurrencePlot(), path)
            self.assertEqual(list(read_recurrence_presets(path)), ['Mine', 'Other'])
            changed = replace(plot, steps=128)
            save_recurrence_preset('Mine', changed, path)
            self.assertEqual(read_recurrence_presets(path)['Mine'], changed)
            self.assertEqual(list(delete_recurrence_preset('Mine', path)), ['Other'])

    def test_names_and_damaged_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'recurrence-presets.json'
            for name in ('', ' ', 'x'*41, next(iter(PRESETS))):
                with self.assertRaises(ValueError): save_recurrence_preset(name, RecurrencePlot(), path)
            # A recurrence built-in name is free for parametric presets, and the reverse.
            self.assertEqual(FUNCTION_STORE.preset_name(next(iter(PRESETS))), next(iter(PRESETS)))
            self.assertEqual(RECURRENCE_STORE.preset_name(next(iter(FUNCTION_PRESETS))), next(iter(FUNCTION_PRESETS)))
            for document in ({'format': 'phosphor-function-presets', 'version': 1, 'presets': []},
                             {'format': 'phosphor-recurrence-presets', 'version': 2, 'presets': []},
                             {'format': 'phosphor-recurrence-presets', 'version': 1, 'presets': [{'name': 'A'}]},
                             {'format': 'phosphor-recurrence-presets', 'version': 1,
                              'presets': [{'name': 'A', 'recurrence': {'x': '0'}}]}):
                path.write_text(json.dumps(document), encoding='utf-8')
                with self.assertRaises(ValueError): read_recurrence_presets(path)

    def test_store(self):
        self.assertIs(RECURRENCE_STORE.builtins, PRESETS)
        self.assertEqual(RECURRENCE_STORE.path.name, 'recurrence-presets.json')
        self.assertEqual(FUNCTION_STORE.path.name, 'function-presets.json')


if __name__ == '__main__':
    unittest.main()
