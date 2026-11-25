"""Shared panel and control sizes (UI2). Qt-free, so preferences can validate against them.

The side panels stretch their controls to fill the width while text keeps one size: the
widths here are minimums, never fixed sizes. The styles track (ST1) reads its spacing from
the same place.
"""
PANEL_RANGE = (280, 900)  # Side panel width a drag on its dividing line allows (pixels).
PANEL_DEFAULT = 400       # The default side panel width; double-clicking the line restores it.
CAPTION_WIDTH = 104       # Inline captions beside a control: this wide when there is room, so rows line up.
ENTRY_WIDTH = 95          # A slider's number field's usual width; it widens with the panel.
SLIDER_STRETCH, ENTRY_STRETCH = 3, 1  # How a slider row shares extra width between slider and field.
SPLIT_HANDLE = 9          # Grab zone of a dividing line (the line itself is 1 px in its middle).


def panel_width(value):
    """A saved panel width, or the default for anything that is not a whole number in range."""
    return value if type(value) is int and PANEL_RANGE[0] <= value <= PANEL_RANGE[1] else PANEL_DEFAULT


BROWSER_RANGE = (220, 640)  # The browser panel's width a drag on its dividing line allows (AW4).
BROWSER_DEFAULT = 280


def browser_width(value):
    """A saved browser width, or the default for anything that is not a whole number in range."""
    return value if type(value) is int and BROWSER_RANGE[0] <= value <= BROWSER_RANGE[1] else BROWSER_DEFAULT


CAPTION_MIN = 64          # Narrowest an inline caption gets in a narrow panel before it elides.
ENTRY_MIN = 64            # Narrowest a slider's number field gets.
SPIN_MIN = 72             # Narrowest a number box beside a caption gets (it widens with the panel).

