"""Qt-free Markdown section index and literal manual search."""
from dataclasses import dataclass
from pathlib import Path
import re


def heading_anchor(text, used):
    """GitHub-style heading slug, with chapter-local duplicate suffixes."""
    base = re.sub(r'[^\w -]', '', text.lower()).replace(' ', '-')
    slug = base
    number = 0
    while slug in used:
        number += 1
        slug = f'{base}-{number}'
    used.add(slug)
    return slug


def plain_text(text):
    text = re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', text)
    return re.sub(r'[*_`~]', '', text).strip()


@dataclass(frozen=True)
class Section:
    chapter_file: Path
    chapter_title: str
    heading: str
    anchor: str
    text: str


@dataclass(frozen=True)
class SearchResult:
    section: Section
    snippet: str


def index_chapter(chapter_file, markdown):
    """Index ATX and setext headings; omit comments and fenced code entirely."""
    markdown = re.sub(r'<!--.*?-->', '', markdown, flags=re.S)
    lines = []
    fence = None
    for line in markdown.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            continue
        lines.append(line)
    headings = []
    used = set()
    body = []
    current = None
    title = Path(chapter_file).stem

    def finish():
        if current:
            headings.append((current[0], current[1], ' '.join(' '.join(body).split())))

    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.match(r'^ {0,3}(#{1,6})(?:\s+|$)(.*)$', line)
        heading = None
        level = 0
        if match:
            heading = re.sub(r'\s+#+\s*$', '', match[2])
            level = len(match[1])
        elif line.strip() and i + 1 < len(lines) and re.fullmatch(r' {0,3}(=+|-+)\s*', lines[i + 1]):
            heading = line.strip()
            level = 1 if lines[i + 1].lstrip().startswith('=') else 2
            i += 1
        if heading is not None:
            finish()
            heading = plain_text(heading)
            if level == 1 and not headings:
                title = heading
            current = (heading, heading_anchor(heading, used))
            body = []
        elif current:
            body.append(plain_text(line))
        i += 1
    finish()
    return tuple(Section(Path(chapter_file), title, heading, anchor, text)
                 for heading, anchor, text in headings)


def build_index(manual_dir):
    return tuple(section for path in sorted(Path(manual_dir).glob('*.md'))
                 for section in index_chapter(path, path.read_text(encoding='utf-8')))


def search(index, query, snippet_length=140):
    query = query.strip()
    if not query:
        return []
    results = []
    for section in index:
        heading_match = re.search(re.escape(query), section.heading, re.I)
        text = section.heading if heading_match else section.text
        match = re.search(re.escape(query), text, re.I)
        if match is None:
            continue
        start = max(0, match.start() - snippet_length // 3)
        end = min(len(text), max(start + snippet_length, match.end()))
        snippet = ('…' if start else '') + text[start:end] + ('…' if end < len(text) else '')
        results.append((not bool(heading_match), SearchResult(section, snippet)))
    results.sort(key=lambda item: item[0])  # Stable chapter/section order within ranks.
    return [result for _, result in results]
