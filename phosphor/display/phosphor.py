"""GUI-free phosphor model: preset decay constants and additive energy deposits.

Display only; nothing here touches audio. Decay times are approximate visual
presets for each phosphor family, not measured tube data.
"""
import math
import numpy as np
from phosphor.display.qt_geometry import BEAM_VERTEX, stroke_triangles

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
PHOSPHOR_CUSTOM = 'Custom'
PHOSPHOR_NAMES = (*PHOSPHORS, PHOSPHOR_CUSTOM)
# Editable decay profile: seconds, afterglow brightness relative to the flash, #rrggbb.
PROFILE_RANGES = dict(flash_time=(.001, 1.), afterglow_time=(.001, 5.), afterglow_level=(0., 1.))
PROFILE_COLORS = ('flash_color', 'afterglow_color')
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


BLANKED_LENGTH = 1e6  # Stands in for a blanked segment's length, so it is never a held spot.


def stroke_deposit(points, width, scale, strength, spot_width=None, energy_width=None, drawn=None):
    """Stroke whose colour is energy in R and G; zero alpha makes the blend additive.

    energy_width lets a narrow stroke carry the energy of a wider one, as the
    Gaussian core does for the trace thickness it is blurred out to. drawn, one flag
    per segment, blanks the beam on the others (a sweep's retrace).
    """
    energy_width = width if energy_width is None else energy_width
    points = np.asarray(points, dtype=np.float32)
    if len(points) < 2:
        return np.empty(0, dtype=BEAM_VERTEX)
    every_length = np.linalg.norm(points[1:]-points[:-1], axis=1)
    drawn = np.ones(len(every_length), dtype=bool) if drawn is None else np.asarray(drawn, dtype=bool)
    spots = spot_deposit(points, np.where(drawn, every_length, BLANKED_LENGTH), width if spot_width is None else spot_width, scale)
    vertices = stroke_triangles(points, width)
    if not len(vertices):
        return spots
    visible = drawn[every_length > 1e-6]
    lengths = every_length[every_length > 1e-6]
    moving = np.repeat(lengths >= STATIONARY_LENGTH, 6)
    # A sample's energy spreads over its footprint, so overlapping wide strokes sum to 1/speed.
    lengths = np.maximum(lengths, max(energy_width, .5))
    levels = scale*ENERGY_SCALE/lengths**strength*(energy_width/width)
    levels = np.repeat(np.clip(np.rint(levels), 0, 255), 6)
    keep = (levels > 0) & moving & np.repeat(visible, 6)
    result = np.empty(int(np.count_nonzero(keep)), dtype=BEAM_VERTEX)
    result['x'], result['y'] = vertices[keep, 0], vertices[keep, 1]
    result['r'] = result['g'] = levels[keep]
    result['b'] = result['a'] = 0
    return np.concatenate((result, spots))


def deposit_vertices(points, width, strength, glow_bands=(), spot_width=None, energy_width=None, drawn=None):
    """Core plus lower-energy glow bands, all following the same beam-speed strength."""
    parts = [stroke_deposit(points, band, scale, strength, drawn=drawn) for band, scale in glow_bands]
    parts.append(stroke_deposit(points, width, 1., strength, spot_width, energy_width, drawn))
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


BLOOM_PEAK = .06  # Bloom energy beside the trace, as a fraction of the trace's own energy.
BLOOM_SIGMA = .5  # Of the glow radius: the bloom fades to about 13% at glow_size.


def bloom_gain(thickness, sigma, intensity):
    """Gain that keeps the bloom's peak at BLOOM_PEAK*intensity of the trace energy.

    A line of energy E and width t blurred by sigma peaks at E*t/(sqrt(2*pi)*sigma),
    so size spreads the glow without dimming it. Thickness and sigma are physical px.
    """
    return intensity*BLOOM_PEAK*math.sqrt(2*math.pi)*sigma/max(thickness, .1)


def bloom_uniforms(enabled, thickness, glow_size, intensity, pixel_ratio, width, height):
    """Shader inputs for glow drawn as bloom of the energy buffer, or bloom switched off.

    The blur runs on a texture downsampled by bloomFactor, chosen so the low-resolution
    sigma stays a few texels: wide glow costs about the same as narrow glow.
    """
    if not enabled or intensity <= 0 or glow_size <= 0 or width <= 0 or height <= 0:
        return dict(bloomOn=False, bloomGain=0.)
    sigma = max(.5, glow_size*BLOOM_SIGMA*pixel_ratio)
    factor = 1 if sigma < 3 else 2 if sigma < 8 else 4 if sigma < 24 else 8
    pixels = (max(1, round(width*pixel_ratio)), max(1, round(height*pixel_ratio)))
    size = tuple(max(1, math.ceil(extent/factor)) for extent in pixels)
    return dict(bloomOn=True, bloomGain=bloom_gain(thickness*pixel_ratio, sigma, intensity),
                bloomSigma=sigma/factor, bloomFactor=float(factor), bloomSize=size,
                energyTexel=(1/pixels[0], 1/pixels[1]),
                bloomTexelX=(1/size[0], 0.), bloomTexelY=(0., 1/size[1]))


def color_hex(rgb):
    return '#'+''.join(f'{round(min(1., max(0., channel))*255):02x}' for channel in rgb)


def preset_profile(name, trace_color):
    """A preset as an editable profile. Its afterglow level is the gain the display uses:
    the preset's slow gain scaled back for long decay, which piles up more energy."""
    preset = PHOSPHORS[name]
    palette = hex_color(trace_color)
    return dict(flash_time=preset['fast'], afterglow_time=preset['slow'],
                afterglow_level=preset['slow_gain']*min(1., SLOW_REFERENCE/preset['slow']),
                flash_color=color_hex(preset.get('fast_color', palette)),
                afterglow_color=color_hex(preset.get('slow_color', palette)))


DEFAULT_CUSTOM_PROFILE = preset_profile(PHOSPHOR_P31, '#000000')


def validated_profile(value):
    if not isinstance(value, dict) or set(value) != set(PROFILE_RANGES)|set(PROFILE_COLORS):
        raise ValueError('Unsupported custom phosphor profile.')
    for key, (low, high) in PROFILE_RANGES.items():
        number = value[key]
        if type(number) not in (int, float) or not math.isfinite(number) or not low <= number <= high:
            raise ValueError(f'{key.replace("_", " ")} must be between {low} and {high}.')
    for key in PROFILE_COLORS:
        color = value[key]
        if not isinstance(color, str) or len(color) != 7 or color[0] != '#' or any(c not in '0123456789abcdefABCDEF' for c in color[1:]):
            raise ValueError(f'{key.replace("_", " ")} must be a #rrggbb colour.')
    return {key: float(value[key]) for key in PROFILE_RANGES} | {key: value[key].lower() for key in PROFILE_COLORS}


def phosphor_profile(name, trace_color, custom):
    """The decay profile the display uses for a phosphor choice, or None for the line trace."""
    if name == PHOSPHOR_CUSTOM:
        return validated_profile(custom)
    return preset_profile(name, trace_color) if PHOSPHORS.get(name) else None


def phosphor_uniforms(profile, elapsed, intensity):
    """Shader inputs for a decay profile; decay stays correct at any frame interval."""
    elapsed = max(0., elapsed)
    return dict(fastKeep=math.exp(-elapsed/profile['flash_time']), slowKeep=math.exp(-elapsed/profile['afterglow_time']),
                slowGain=profile['afterglow_level'], exposure=EXPOSURE*intensity,
                fastColor=hex_color(profile['flash_color']), slowColor=hex_color(profile['afterglow_color']))


PREVIEW_ENERGY = .5  # Energy one pass of the beam leaves on a pixel, for the decay preview.
HOT_WHITE = .08  # Matches phosphor_display.frag.


def decay_preview(profile, intensity, times):
    """Light after one beam pass, as phosphor_display.frag tone-maps it.

    Returns the flash and afterglow brightness and the displayed colour at each time.
    Grain and shimmer are mean-1 noise across pixels and frames, so the preview shows
    the average pixel and does not model them.
    """
    times = np.asarray(times, dtype=float)
    exposure = EXPOSURE*intensity
    fast_energy = PREVIEW_ENERGY*np.exp(-times/profile['flash_time'])
    slow_energy = PREVIEW_ENERGY*np.exp(-times/profile['afterglow_time'])
    level = profile['afterglow_level']
    flash = 1-np.exp(-fast_energy*exposure)
    afterglow = 1-np.exp(-slow_energy*exposure*level)
    light = (np.outer(flash, hex_color(profile['flash_color']))
             + np.outer(afterglow, hex_color(profile['afterglow_color'])))
    hot = 1-np.exp(-(fast_energy+slow_energy*level)*exposure*HOT_WHITE)
    light = light+(1-light)*(.7*hot*hot)[:, None]
    return flash, afterglow, np.minimum(light, 1.)


def afterglow_level_at(profile, intensity, brightness):
    """Inverse of decay_preview's afterglow at its own decay time, for dragging the curve."""
    exposure = EXPOSURE*intensity*PREVIEW_ENERGY*math.exp(-1)
    brightness = min(max(brightness, 0.), .9999)
    return min(1., max(0., -math.log(1-brightness)/exposure))
