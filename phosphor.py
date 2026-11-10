"""GUI-free phosphor model: preset decay constants and additive energy deposits.

Display only; nothing here touches audio. Decay times are approximate visual
presets for each phosphor family, not measured tube data.
"""
import math
import numpy as np
from qt_geometry import BEAM_VERTEX, stroke_triangles

PHOSPHOR_OFF = 'Off (line trace)'
PHOSPHOR_PALETTE = 'Palette colour'
PHOSPHOR_P31 = 'P31 green'
PHOSPHORS = {
    PHOSPHOR_OFF: None,
    PHOSPHOR_PALETTE: dict(fast=.008, slow=.05, slow_gain=.2),
    'P31 green': dict(fast=.008, slow=.05, slow_gain=.2, fast_color=(.30, 1., .42), slow_color=(.30, 1., .42)),
    'P1 yellow-green': dict(fast=.015, slow=.12, slow_gain=.35, fast_color=(.45, 1., .25), slow_color=(.42, .95, .22)),
    'P7 blue / long yellow': dict(fast=.02, slow=.7, slow_gain=.25, fast_color=(.45, .6, 1.), slow_color=(.85, .85, .2)),
}
BEAM_HARD = 'Hard edge'
BEAM_GAUSSIAN = 'Gaussian'
BEAM_PROFILES = (BEAM_HARD, BEAM_GAUSSIAN)
MIN_SIGMA, MAX_SIGMA = 1., 8.  # Physical pixels; below 1 the thin core's aliasing shows.
FWHM_TO_SIGMA = 1/(2*math.sqrt(2*math.log(2)))
ENERGY_SCALE = 150.
STATIONARY_LENGTH = .05  # Screen pixels per segment; shorter slivers cover no pixel centre.
_spot_angles = np.linspace(0, 2*np.pi, 13)
# Unit-radius twelve-sided fan, as triangles around the origin.
SPOT_TRIANGLES = np.array([point for a, b in zip(_spot_angles[:-1], _spot_angles[1:])
                           for point in ((0., 0.), (np.cos(a), np.sin(a)), (np.cos(b), np.sin(b)))], dtype=np.float32)
EXPOSURE = 1.2
SLOW_REFERENCE = .05
MAX_PENDING_VERTICES = 300000


def new_sample_count(frames, factor, available):
    """Display points to deposit this frame, so overlapping windows are not counted twice."""
    if frames <= 0 or available < 2:
        return 0
    return min(available-1, int(round(frames*factor)))


def spot_deposit(points, lengths, width, level):
    """Round spots for segments too short to rasterize; held samples merge and add energy."""
    held = lengths < STATIONARY_LENGTH
    if width <= 0 or not held.any():
        return np.empty(0, dtype=BEAM_VERTEX)
    # A slowly drifting beam starts a new spot each half width instead of smearing one.
    travel = np.floor(np.cumsum(lengths)/(width/2))
    first = held & np.concatenate(([True], ~held[:-1] | (travel[1:] != travel[:-1])))
    starts = np.flatnonzero(first)
    # Moving segments between groups end a group, so only held segments are counted.
    group = np.cumsum(first)-1
    members = held & (group >= 0)
    counts = np.bincount(group[members], minlength=len(starts))
    centers = np.column_stack([np.bincount(group[members], points[:-1][members, axis], len(starts)) for axis in (0, 1)])/counts[:, None]
    per_sample = ENERGY_SCALE/max(width, .5)
    levels = np.clip(np.rint(level*per_sample*counts), 0, 255)
    keep = levels > 0
    centers, levels = centers[keep], levels[keep]
    result = np.empty(len(centers)*len(SPOT_TRIANGLES), dtype=BEAM_VERTEX)
    offsets = SPOT_TRIANGLES*width/2
    result['x'] = np.repeat(centers[:, 0], len(offsets))+np.tile(offsets[:, 0], len(centers))
    result['y'] = np.repeat(centers[:, 1], len(offsets))+np.tile(offsets[:, 1], len(centers))
    result['r'] = result['g'] = np.repeat(levels, len(offsets))
    result['b'] = result['a'] = 0
    return result


def stroke_deposit(points, width, scale, strength, spot_width=None, energy_width=None):
    """Stroke whose colour is energy in R and G; zero alpha makes the blend additive.

    energy_width lets a narrow stroke carry the energy of a wider one, as the
    Gaussian core does for the trace thickness it is blurred out to.
    """
    energy_width = width if energy_width is None else energy_width
    points = np.asarray(points, dtype=np.float32)
    if len(points) < 2:
        return np.empty(0, dtype=BEAM_VERTEX)
    every_length = np.linalg.norm(points[1:]-points[:-1], axis=1)
    spots = spot_deposit(points, every_length, width if spot_width is None else spot_width, scale)
    vertices = stroke_triangles(points, width)
    if not len(vertices):
        return spots
    lengths = every_length[every_length > 1e-6]
    moving = np.repeat(lengths >= STATIONARY_LENGTH, 6)
    # A sample's energy spreads over its footprint, so overlapping wide strokes sum to 1/speed.
    lengths = np.maximum(lengths, max(energy_width, .5))
    levels = scale*ENERGY_SCALE/lengths**strength*(energy_width/width)
    levels = np.repeat(np.clip(np.rint(levels), 0, 255), 6)
    keep = (levels > 0) & moving
    result = np.empty(int(np.count_nonzero(keep)), dtype=BEAM_VERTEX)
    result['x'], result['y'] = vertices[keep, 0], vertices[keep, 1]
    result['r'] = result['g'] = levels[keep]
    result['b'] = result['a'] = 0
    return np.concatenate((result, spots))


def deposit_vertices(points, width, strength, glow_bands=(), spot_width=None, energy_width=None):
    """Core plus lower-energy glow bands, all following the same beam-speed strength."""
    parts = [stroke_deposit(points, band, scale, strength) for band, scale in glow_bands]
    parts.append(stroke_deposit(points, width, 1., strength, spot_width, energy_width))
    return np.concatenate(parts)


def hex_color(value):
    return tuple(int(value[index:index+2], 16)/255 for index in (1, 3, 5))


def gaussian_beam(thickness, pixel_ratio, strength=1.):
    """Core width (logical px) and blur sigma (physical px).

    Trace thickness is the beam's full width at half maximum. The core is drawn thin
    and carries the energy of the full thickness (stroke_deposit's energy_width), and
    the blur preserves it, so Gaussian matches Hard edge at any beam speed or
    beam-speed brightness. The core widens where its level would pass 8 bits, and the
    blur narrows so the core's own width does not broaden the profile.
    """
    target = min(MAX_SIGMA, max(MIN_SIGMA, thickness*pixel_ratio*FWHM_TO_SIGMA))
    # The slowest beam has the highest level: ENERGY_SCALE/max(thickness, .5)**strength.
    unclipped = thickness*ENERGY_SCALE/(255*max(thickness, .5)**strength)
    core = min(thickness, max(min(thickness, 1.), unclipped))
    # A box of width w adds w**2/12 to the variance of the profile.
    sigma = max(MIN_SIGMA, math.sqrt(max(0., target**2-(core*pixel_ratio)**2/12)))
    return core, sigma


def beam_uniforms(profile, thickness, pixel_ratio, width, height, strength=1.):
    """Shader inputs for the beam profile; a hard edge bypasses the blur passes."""
    if profile != BEAM_GAUSSIAN or width <= 0 or height <= 0:
        return dict(gaussian=False, depositGain=1.)
    _, sigma = gaussian_beam(thickness, pixel_ratio, strength)
    return dict(gaussian=True, sigma=sigma, depositGain=1.,
                texelX=(1/(width*pixel_ratio), 0.), texelY=(0., 1/(height*pixel_ratio)))


def phosphor_uniforms(name, trace_color, glow_decay, elapsed, intensity):
    """Shader inputs for a preset; decay stays correct at any frame interval."""
    preset = PHOSPHORS[name]
    slow = max(preset['slow'], glow_decay/2) if name == PHOSPHOR_PALETTE else preset['slow']
    palette = hex_color(trace_color)
    elapsed = max(0., elapsed)
    return dict(fastKeep=math.exp(-elapsed/preset['fast']), slowKeep=math.exp(-elapsed/slow),
                # Longer decay piles up more energy, so scale it back to a common level.
                slowGain=preset['slow_gain']*min(1., SLOW_REFERENCE/slow), exposure=EXPOSURE*intensity,
                fastColor=preset.get('fast_color', palette), slowColor=preset.get('slow_color', palette))
