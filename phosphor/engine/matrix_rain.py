"""Matrix digital-rain vector object: scrolling, swapping multi-script glyphs.

Each glyph is authored on the same 4x6 grid as vector_text.GLYPHS so every script
renders crisply on a stereo X-Y oscilloscope without third-party font extraction.
Both Swap speed (text_speed) and Fall speed (fall_speed) are Param fields, so they
support static sliders, time-varying expressions, and Composer automation lanes.
"""
from dataclasses import dataclass, replace
import math
import numpy as np

from phosphor.engine.vector_text import GLYPHS as LATIN_GLYPHS
from phosphor.engine.transforms import Field, Param, _value
from phosphor.engine.automation import validate_points

MATRIX_OBJECT = 'Matrix'
MAX_SAMPLE_LENGTH = 128
MAX_MATRIX_COLUMNS = 10
MAX_MATRIX_ROWS = 10

# Compact 4x6 single/multi-stroke vector glyphs for Katakana / Kanji-inspired rain.
JAPANESE_GLYPHS = {
    'ア': '06 46 34|24 21 10',
    'イ': '46 13|24 20',
    'ウ': '26 25|05 45 43 20|05 03',
    'エ': '06 46|26 20|00 40',
    'オ': '05 45|36 30 21|35 01',
    'カ': '05 45 41 30|26 22 00',
    'キ': '05 45|03 43|26 20',
    'ク': '26 04|25 45 32 00',
    'ケ': '16 03|14 44|34 20',
    'コ': '06 46 40 00',
    'サ': '05 45|16 12|36 32 10',
    'シ': '05 14|03 12|10 43',
    'ス': '06 46 00|23 40',
    'セ': '04 45 33|16 11 41',
    'ソ': '06 14|46 10',
    'タ': '16 04|15 45 10|13 32',
    'チ': '36 05|03 43|25 22 10',
    'ツ': '06 14|26 24|46 10',
    'テ': '06 46|04 44|24 21 10',
    'ト': '16 10|14 42',
    '日': '06 46 40 00 06|03 43',
    '月': '16 46 40 31|16 12 00|14 44|12 42',
    '火': '05 13|45 33|26 22 00|22 40',
    '水': '26 20 11|04 14 01|45 23 40',
}

GREEK_GLYPHS = {
    'Α': '00 26 40|12 32',
    'Β': '00 06 36 45 44 33 03|33 42 41 30 00',
    'Γ': '00 06 46',
    'Δ': '00 26 40 00',
    'Ε': '46 06 00 40|03 33',
    'Ζ': '06 46 00 40',
    'Η': '00 06|40 46|03 43',
    'Θ': '10 01 05 16 36 45 41 30 10|03 43',
    'Λ': '00 26 40',
    'Ξ': '06 46|13 33|00 40',
    'Π': '00 06 46 40',
    'Σ': '46 06 23 00 40',
    'Φ': '26 20|11 02 04 15 35 44 42 31 11',
    'Ψ': '26 20|06 03 22 43 46',
    'Ω': '00 10 02 05 16 36 45 42 30 40',
}

ALL_GLYPHS = {**LATIN_GLYPHS, **JAPANESE_GLYPHS, **GREEK_GLYPHS}

LANGUAGE_POOLS = {
    'Japanese (Katakana/Kanji)': tuple(JAPANESE_GLYPHS),
    'English': tuple('ABCDEFGHIJKLMNOPQRSTUVWXYZ'),
    'Digits': tuple('0123456789'),
    'Greek': tuple(GREEK_GLYPHS),
    'Hex / Binary': tuple('0123456789ABCDEF'),
    'Custom': tuple('01'),
}
MATRIX_LANGUAGES = tuple(LANGUAGE_POOLS)

TEXT_SPEED_FIELD = Field('text_speed', 'Swap speed (Hz)', 4.0, 0.0, 60.0, curve=True)
FALL_SPEED_FIELD = Field('fall_speed', 'Fall speed (rows/s)', 1.2, -20.0, 20.0, curve=True)
MATRIX_FIELDS = (TEXT_SPEED_FIELD, FALL_SPEED_FIELD)


@dataclass(frozen=True)
class MatrixConfig:
    language_a: str = 'Japanese (Katakana/Kanji)'
    language_b: str = 'English'
    sample_a: str = ''
    sample_b: str = ''
    columns: int = 5
    rows: int = 5
    text_speed: Param = Param(4.0)
    fall_speed: Param = Param(1.2)

    def document(self):
        return {
            'language_a': self.language_a,
            'language_b': self.language_b,
            'sample_a': self.sample_a,
            'sample_b': self.sample_b,
            'columns': self.columns,
            'rows': self.rows,
            'text_speed': self.text_speed.document(),
            'fall_speed': self.fall_speed.document(),
        }

    @classmethod
    def read(cls, value, variable_names=()):
        # variable_names: the owning object's variable names, which the speed expressions may
        # read (as signal_engine.validate passes them); None for parked data, which validate
        # skips too, so a speed reading a since-removed variable never makes a file unreadable.
        if not isinstance(value, dict):
            raise ValueError('Invalid Matrix configuration.')
        allowed = {'language_a', 'language_b', 'sample_a', 'sample_b', 'columns', 'rows', 'text_speed', 'fall_speed'}
        if not set(value) <= allowed:
            raise ValueError('Unsupported Matrix fields.')
        config = cls(
            language_a=value.get('language_a', 'Japanese (Katakana/Kanji)'),
            language_b=value.get('language_b', 'English'),
            sample_a=value.get('sample_a', ''),
            sample_b=value.get('sample_b', ''),
            columns=value.get('columns', 5),
            rows=value.get('rows', 5),
            text_speed=Param.read(value['text_speed'], 'Matrix Swap speed') if 'text_speed' in value else Param(4.0),
            fall_speed=Param.read(value['fall_speed'], 'Matrix Fall speed') if 'fall_speed' in value else Param(1.2),
        )
        validate_matrix(config, variable_names)
        return config


def _glyph_pool(language, sample):
    if sample:
        chars = []
        for ch in sample:
            if ch == ' ':
                continue
            if ch in ALL_GLYPHS:
                chars.append(ch)
            elif ch.upper() in ALL_GLYPHS:
                chars.append(ch.upper())
        if chars:
            return tuple(chars)
    return LANGUAGE_POOLS[language]


def validate_matrix(config, variable_names=()):
    if not isinstance(config, MatrixConfig):
        raise ValueError('Matrix configuration is missing.')
    if config.language_a not in LANGUAGE_POOLS or config.language_b not in LANGUAGE_POOLS:
        raise ValueError('Choose supported Matrix languages.')
    for key in ('sample_a', 'sample_b'):
        text = getattr(config, key)
        if not isinstance(text, str) or len(text) > MAX_SAMPLE_LENGTH:
            raise ValueError(f'Matrix sample strings must be at most {MAX_SAMPLE_LENGTH} characters.')
    if type(config.columns) is not int or not 1 <= config.columns <= MAX_MATRIX_COLUMNS:
        raise ValueError(f'Matrix columns must be an integer from 1 to {MAX_MATRIX_COLUMNS}.')
    if type(config.rows) is not int or not 2 <= config.rows <= MAX_MATRIX_ROWS:
        raise ValueError(f'Matrix rows must be an integer from 2 to {MAX_MATRIX_ROWS}.')
    from phosphor.engine.transforms import compiled_expression
    for field, param in ((TEXT_SPEED_FIELD, config.text_speed), (FALL_SPEED_FIELD, config.fall_speed)):
        if not isinstance(param, Param) or isinstance(param.value, bool) or not isinstance(param.value, (int, float)) or not math.isfinite(param.value):
            raise ValueError(f'Matrix {field.label} must be a finite number.')
        if not field.low <= param.value <= field.high:
            raise ValueError(f'Matrix {field.label} must be between {field.low:g} and {field.high:g}.')
        if not isinstance(param.expression, str):
            raise ValueError(f'Matrix {field.label}: invalid expression.')
        validate_points(param.points, f'Matrix {field.label}')
        if param.expression and variable_names is not None:
            compiled_expression(param.expression, f'Matrix {field.label}', tuple(variable_names))


def _mix(a, b, c):
    """Deterministic 32-bit integer hash for reproducible character selection."""
    x = ((a * 0x9E3779B1) ^ (b * 0x85EBCA77) ^ (c * 0xC2B2AE3D)) & 0xFFFFFFFF
    x = ((x ^ (x >> 16)) * 0x7FEB352D) & 0xFFFFFFFF
    x = ((x ^ (x >> 15)) * 0x846CA68B) & 0xFFFFFFFF
    return x ^ (x >> 16)


def _parsed_glyph(char):
    """Cached list of strokes, each a list of (x, y) tuples in 4x6 grid units."""
    spec = ALL_GLYPHS[char]
    return [[(int(pair[0]), int(pair[1])) for pair in stroke.split()] for stroke in spec.split('|')]


_GLYPH_CACHE = {ch: _parsed_glyph(ch) for ch in ALL_GLYPHS}
_PROBE = np.zeros((1, 3))


def matrix_route_at(config, seconds, tempo=120., values=None):
    """Build (vertices (N, 3), edges (E, 2), closed route (R,), connectors (C, 2)) at pose time."""
    t = float(seconds)
    swap_v = _value(TEXT_SPEED_FIELD, config.text_speed, t, _PROBE, None, 'Matrix Swap speed', tempo, values)
    fall_v = _value(FALL_SPEED_FIELD, config.fall_speed, t, _PROBE, None, 'Matrix Fall speed', tempo, values)
    text_speed = float(swap_v) if np.ndim(swap_v) == 0 else float(swap_v.flat[0])
    fall_speed = float(fall_v) if np.ndim(fall_v) == 0 else float(fall_v.flat[0])

    pool_a = _glyph_pool(config.language_a, config.sample_a)
    pool_b = _glyph_pool(config.language_b, config.sample_b)
    combined = pool_a + pool_b
    n_comb = len(combined)

    cols, rows = config.columns, config.rows
    cell_w, cell_h = 6.0, 8.0
    total_w = cols * cell_w
    total_h = rows * cell_h
    scale = 1.8 / max(total_w, total_h, 1.0)

    vertices = []
    drawn_edges = []
    connectors = []
    route = []

    for c in range(cols):
        # Each column gets an organic phase offset and slight speed factor (0.82..1.24x).
        col_hash = _mix(c + 1, 17, 31)
        col_phase = (col_hash & 0xFFFF) / 65536.0 * rows
        col_rate = 0.82 + ((col_hash >> 16) & 0xFF) / 255.0 * 0.42
        fall_pos = t * fall_speed * col_rate + col_phase

        # Serpentine vertical order between adjacent columns keeps travel jumps short.
        row_order = range(rows) if c % 2 == 0 else range(rows - 1, -1, -1)
        cx = (c - (cols - 1) * 0.5) * cell_w - 2.0
        for r in row_order:
            # Scroll downward (y decreases as fall_pos increases), wrapping smoothly in [0, rows).
            slot_y = (r - fall_pos) % rows
            # Identify which logical raindrop row index this cell belongs to.
            drop_id = math.floor((r - fall_pos) / rows)
            # Swap tick advances at text_speed Hz with per-cell phase jitter.
            cell_seed = _mix(c + 3, r + 7, drop_id & 0xFFFF)
            cell_phase = (cell_seed & 0xFF) / 256.0
            swap_tick = int(math.floor(t * text_speed + cell_phase)) if text_speed > 0. else 0
            # Pick character from combined two-language pool (deterministic).
            ch_idx = _mix(c * 131 + r, drop_id & 0xFFFF, swap_tick) % n_comb
            strokes = _GLYPH_CACHE[combined[ch_idx]]

            cy = (rows * 0.5 - slot_y - 0.5) * cell_h - 3.0
            for stroke in strokes:
                start_idx = len(vertices)
                for gx, gy in stroke:
                    vertices.append(((cx + gx) * scale, (cy + gy) * scale, 0.0))
                if route:
                    connectors.append((route[-1], start_idx))
                for k in range(len(stroke) - 1):
                    drawn_edges.append((start_idx + k, start_idx + k + 1))
                route.extend(range(start_idx, start_idx + len(stroke)))

    if not vertices:
        return np.zeros((1, 3)), np.array([[0, 0]], dtype=int), np.array([0, 0], dtype=int), np.empty((0, 2), dtype=int)

    # Close the route back to vertex 0 via a connector jump.
    if route[-1] != route[0]:
        connectors.append((route[-1], route[0]))
        route.append(route[0])

    pts = np.asarray(vertices, dtype=float)
    conn_arr = np.asarray(connectors, dtype=int).reshape(-1, 2) if connectors else np.empty((0, 2), dtype=int)
    edges_arr = np.asarray(drawn_edges + connectors, dtype=int).reshape(-1, 2) if (drawn_edges or connectors) else np.empty((0, 2), dtype=int)
    return pts, edges_arr, np.asarray(route, dtype=int), conn_arr
