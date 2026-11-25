"""Best-guess chord labels and sounding pitches for the Composer roll (no Qt)."""
from functools import lru_cache

from phosphor.audio.score_source import NOTE_NAMES
from phosphor.engine.composition import chord_voices


CHORD_SHAPES = (
    ('', (0, 4, 7)), ('m', (0, 3, 7)), ('5', (0, 7)),
    ('dim', (0, 3, 6)), ('aug', (0, 4, 8)), ('sus2', (0, 2, 7)),
    ('sus4', (0, 5, 7)), ('6', (0, 4, 7, 9)), ('m6', (0, 3, 7, 9)),
    ('7', (0, 4, 7, 10)), ('maj7', (0, 4, 7, 11)), ('m7', (0, 3, 7, 10)),
    ('m(maj7)', (0, 3, 7, 11)), ('dim7', (0, 3, 6, 9)),
    ('m7b5', (0, 3, 6, 10)), ('7sus4', (0, 5, 7, 10)),
    ('add9', (0, 2, 4, 7)), ('9', (0, 2, 4, 7, 10)),
    ('maj9', (0, 2, 4, 7, 11)), ('m9', (0, 2, 3, 7, 10)),
)
EXTRA_NAMES = {1: 'b9', 2: '9', 3: '#9', 4: '3', 5: '11',
               6: '#11', 8: 'b13', 9: '13', 10: '7', 11: 'maj7'}


def chord_name(pitches):
    """Name the closest shape, preserving the lowest pitch as the bass."""
    return _chord_name(tuple(sorted(pitches)))


@lru_cache(maxsize=512)
def _chord_name(pitches):
    classes = {p % 12 for p in pitches}
    if len(classes) < 2: return ''
    bass = pitches[0] % 12
    candidates = []
    for root in sorted(classes):
        relative = {(p - root) % 12 for p in classes}
        for priority, (suffix, intervals) in enumerate(CHORD_SHAPES):
            shape = set(intervals)
            missing, extra, matched = shape - relative, relative - shape, shape & relative
            if not missing <= {7} or len(matched) < 2 or len(extra) > 2: continue
            # A perfect fifth beside a shape with an altered fifth (dim, aug, m7b5) is not
            # that shape: another root or shape names it. (0.65.1: this raised KeyError in
            # the roll's paintEvent, which crashed playback on macOS.)
            if 7 in extra: continue
            if matched == {0, 7} and suffix != '5': continue
            score = (len(extra), len(missing), root != bass, priority, root)
            name = NOTE_NAMES[root] + suffix
            if extra:
                prefix = '' if shape & {10, 11} or suffix.startswith('add') else 'add'
                name += '(' + prefix + ','.join(EXTRA_NAMES[i] for i in sorted(extra)) + ')'
            if root != bass: name += '/' + NOTE_NAMES[bass]
            candidates.append((score, name))
    return min(candidates)[1] if candidates else ''


def sounding_pitches(notes, beat, voices=None):
    """Sorted allocated pitches at beat, excluding glide partners and stolen voices."""
    voices = chord_voices(tuple(notes)) if voices is None else voices
    # Vectorized (the roll asks on every display frame during playback): the same comparisons.
    sounding = (voices.start <= beat) & (beat < voices.end) & (beat < voices.steal)
    return tuple(sorted(int(pitch) for pitch in voices.pitch[sounding]))
