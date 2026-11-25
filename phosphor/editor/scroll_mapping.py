"""Wheel, trackpad and pinch arithmetic shared by the Composer and Builder."""
import math


SENSITIVITY_RANGE = (.25, 4.)
DEFAULT_WHEEL_SENSITIVITY = 1.
DEFAULT_TRACKPAD_SENSITIVITY = 1.
SENSITIVITY_KEYS = ('wheel_sensitivity', 'trackpad_sensitivity')
NOTCH = 120
PINCH_RANGE = (.5, 2.)


def validated_sensitivity(value, default):
    """Return the saved sensitivity if it is a number within the preference limits."""
    low, high = SENSITIVITY_RANGE
    return float(value) if type(value) in (int, float) and low <= value <= high else default


def is_trackpad(pixel_dx, pixel_dy, touchpad_device=False, scroll_phase=False):
    """Recognize a trackpad by its device, scroll phase or pixel deltas."""
    return bool(touchpad_device or scroll_phase or pixel_dx or pixel_dy)


def wheel_notches(angle, sensitivity):
    """Convert a wheel angle delta to a signed number of notches."""
    return angle / NOTCH * sensitivity


def trackpad_pixels(pixel, sensitivity):
    """Scale a system-provided pixel delta linearly."""
    return pixel * sensitivity


def pinch_factor(value, sensitivity):
    """Convert a magnification delta to a bounded zoom factor for one event."""
    if not math.isfinite(value):
        return 1.
    low, high = PINCH_RANGE
    return min(high, max(low, 1. + value * sensitivity))


def zoom_about(anchor, offset, old_scale, new_scale):
    """Return the view offset that keeps the content under the pointer fixed."""
    return offset + anchor / old_scale - anchor / new_scale


class Accumulator:
    """Carry fractional steps forward until the gesture stops or reverses."""

    def __init__(self):
        self.reset()

    @property
    def remainder(self):
        """The signed fraction carried into the next event."""
        return self._remainder

    def reset(self):
        """Discard any remaining fraction."""
        self._remainder = 0.

    def add(self, amount):
        """Return completed steps, starting a fresh fraction on reversal."""
        if not math.isfinite(amount):
            return 0
        if amount < 0 < self._remainder or self._remainder < 0 < amount:
            self.reset()
        total = self._remainder + amount
        steps = math.trunc(total)
        self._remainder = total - steps
        return steps
