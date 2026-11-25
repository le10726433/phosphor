"""Pure manual indexing/search; no Qt or writable app data required."""
from pathlib import Path
import unittest

from phosphor.editor.manual_search import build_index, index_chapter, search
from phosphor.storage.app_paths import APP_DIR


class ManualSearchTests(unittest.TestCase):
    def test_headings_anchors_and_sections(self):
        sections = index_chapter(Path('chapter.md'), '''# Chapter
Introduction.
## 2.1 **Main** [window](other.md) ###
Window body.
## Repeat
First.
## Repeat
Second.
Setext title
------------
Last.
''')
        self.assertEqual([s.heading for s in sections],
                         ['Chapter', '2.1 Main window', 'Repeat', 'Repeat', 'Setext title'])
        self.assertEqual([s.anchor for s in sections],
                         ['chapter', '21-main-window', 'repeat', 'repeat-1', 'setext-title'])
        self.assertEqual(sections[1].chapter_file, Path('chapter.md'))
        self.assertTrue(all(s.chapter_title == 'Chapter' for s in sections))
        self.assertEqual(sections[2].text, 'First.')
        self.assertEqual(sections[3].text, 'Second.')

    def test_fences_and_hidden_examples_are_excluded(self):
        sections = index_chapter('chapter.md', '''# Visible
Before.
```markdown
## Fake
secret_code
````
~~~
# Also fake
secret_tilde
~~~
<!-- example: {
"text": "secret_example"
} -->
After.
''')
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].text, 'Before. After.')
        for query in ('Fake', 'secret_code', 'secret_tilde', 'secret_example'):
            self.assertEqual(search(sections, query), [])

    def test_case_insensitive_ranking_and_literal_query(self):
        sections = index_chapter('chapter.md', '''# Chapter
Body mentions SEARCH and a.b.
## Search controls
Other text.
## Later
Another search.
''')
        results = search(sections, 'sEaRcH')
        self.assertEqual([r.section.heading for r in results],
                         ['Search controls', 'Chapter', 'Later'])
        self.assertEqual(len(search(sections, 'a.b')), 1)
        self.assertEqual(search(sections, 'aXb'), [])
        self.assertEqual(search(sections, '   '), [])
        self.assertEqual(search(sections, 'missing'), [])

    def test_snippet_surrounds_first_match(self):
        sections = index_chapter('chapter.md', '# Title\n' + 'before ' * 40
                                 + 'Needle nearby context ' + 'after ' * 40 + 'needle')
        snippet = search(sections, 'needle')[0].snippet
        self.assertIn('Needle nearby context', snippet)
        self.assertTrue(snippet.startswith('…'))
        self.assertTrue(snippet.endswith('…'))
        self.assertLessEqual(len(snippet), 142)
        self.assertEqual(snippet.lower().count('needle'), 1)

    def test_bundled_manual_index(self):
        sections = build_index(APP_DIR / 'docs' / 'manual')
        results = search(sections, 'RUNNING PHOSPHOR')
        self.assertTrue(results)
        section = results[0].section
        self.assertEqual(section.chapter_file.name, '01-introduction.md')
        self.assertEqual(section.anchor, '13-running-phosphor')
        self.assertTrue(section.chapter_title)


if __name__ == '__main__':
    unittest.main()
