"""Qt-free horizontal timeline framing, in beats and pixels."""

SCALE_RANGE = (2., 400.)  # Pixels per beat, shared with wheel/button zoom.
FIT_MARGIN = 24.


def zoom_fit(beat_range, width, gutter=64., margin=FIT_MARGIN,
             min_scale=SCALE_RANGE[0], max_scale=SCALE_RANGE[1]):
    """Return (first visible beat, pixels per beat) for a nonnegative range.

    Reserve a pixel margin at each end. At beat zero the left margin is
    necessarily lost: the timeline never scrolls before the song. Degenerate
    ranges use maximum zoom; tiny views remain finite. If minimum zoom cannot
    fit a long range, show its start with the same margin.
    """
    if not 0 < min_scale <= max_scale or margin < 0:
        raise ValueError('Invalid zoom limits or margin')
    start, end = sorted(max(0., float(beat)) for beat in beat_range)
    room = max(1., width - gutter - 2 * margin)
    scale = max_scale if end == start else min(max_scale, max(min_scale, room / (end - start)))
    return max(0., start - margin / scale), scale
