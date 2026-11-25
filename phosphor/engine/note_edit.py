"""Pure immutable note-roll edits; overlaps retain playback's last-note priority."""
from dataclasses import replace
from bisect import bisect_left, bisect_right
import math

from phosphor.engine.composition import PITCH_RANGE, MAX_BEATS


def cut_at_onsets(notes, existing=()):
    """Monophonic recording rule: each new onset ends the previous note."""
    ordered = sorted(notes, key=lambda note: note.start)
    result = []
    for note in ordered:
        if result and result[-1].end > note.start:
            previous = result.pop()
            if previous.start < note.start:
                length = note.start - previous.start
                result.append(replace(previous, length=length, glide=min(previous.glide, length)))
        result.append(note)
    if not existing or not result:
        return tuple(result) if not existing else tuple(existing)
    # Only recording intersections are changed. Unrelated pre-existing overlaps
    # retain the roll's normal last-note playback/resumption behavior.
    onsets = [n.start for n in result]
    kept = []
    for note in existing:
        index = bisect_left(onsets, note.start)
        end = min(note.end, onsets[index]) if index < len(onsets) else note.end
        if end > note.start:
            length = end - note.start
            kept.append(replace(note, length=length, glide=min(note.glide, length)))
    old_onsets = sorted(n.start for n in kept)
    recorded = []
    for note in result:
        index = bisect_right(old_onsets, note.start)
        end = min(note.end, old_onsets[index]) if index < len(old_onsets) else note.end
        recorded.append(replace(note, length=end - note.start))
    return tuple(sorted((*kept, *recorded), key=lambda note: note.start))


def move_notes(notes, indices, semitones=0, beats=0.):
    """Return (notes, moved indices), preserving order, lengths and velocities.

    Like single-note edits, clamp each note at the pitch/time boundaries and keep
    overlapping notes intact: composition.note_line resolves the monophonic voice.
    Stable indices also distinguish otherwise identical notes.
    """
    picked = frozenset(indices)
    if any(type(i) is not int or not 0 <= i < len(notes) for i in picked):
        raise ValueError('Invalid note selection.')
    if type(semitones) is not int or not math.isfinite(beats):
        raise ValueError('Expected integer semitones and finite beats.')
    return tuple(replace(n, start=min(MAX_BEATS, max(0., n.start + beats)),
                         pitch=min(PITCH_RANGE[1], max(PITCH_RANGE[0], n.pitch + semitones)))
                 if i in picked else n for i, n in enumerate(notes)), tuple(sorted(picked))
