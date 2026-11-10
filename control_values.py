"""Shared numeric conversions for CPU and Qt controls, without GUI imports."""
import math

MIN_ROTATION = .01
MAX_SLIDER_ROTATION = 3600.
FINITE_END = .98
STOP_START = .99


def integer_degrees(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('Rotation degrees must be finite.')
    return math.floor(value + .5) if value >= 0 else math.ceil(value - .5)


def parse_rotation(text):
    text = str(text).strip().lower()
    if text in ('∞', 'inf', '+inf', 'infinity', 'no rotation', 'none', 'off'):
        return None
    value = float(text)
    if not math.isfinite(value) or value < MIN_ROTATION:
        raise ValueError('Rotation must be at least 0.01 seconds, or No rotation / inf.')
    return value


def format_rotation(value):
    return 'No rotation' if value is None else f'{value:.8g}'


def rotation_position(value):
    if value is None:
        return 1.
    return min(FINITE_END, max(0., FINITE_END * math.log(value / MIN_ROTATION)
                               / math.log(MAX_SLIDER_ROTATION / MIN_ROTATION)))


def rotation_value(position):
    if position >= STOP_START:
        return None
    return MIN_ROTATION * (MAX_SLIDER_ROTATION / MIN_ROTATION) ** (min(FINITE_END, max(0., position)) / FINITE_END)


