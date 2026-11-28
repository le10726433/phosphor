"""GUI-free phosphor model: preset decay constants and additive energy deposits.

Display only; nothing here touches audio. Decay times are approximate visual
presets for each phosphor family, not measured tube data.
"""
from functools import lru_cache
import math
import numpy as np
from phosphor.display.qt_geometry import BEAM_VERTEX, segment_quads

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
QUALITY_LOW, QUALITY_MEDIUM, QUALITY_HIGH, QUALITY_ULTRA = 'Low', 'Medium', 'High', 'Ultra'
GRAPHICS_QUALITIES = (QUALITY_LOW, QUALITY_MEDIUM, QUALITY_HIGH, QUALITY_ULTRA)
DEFAULT_GRAPHICS_QUALITY = QUALITY_HIGH
POWER_LAW = .35
P7 = 'P7 blue / long yellow'
P7_CASCADE = .18
GRID_SIMPLE, GRID_GRATICULE = 'Simple', 'Graticule'
GRID_STYLES = (GRID_SIMPLE, GRID_GRATICULE)
DEFAULT_GRID_STYLE = GRID_GRATICULE
REFERENCE_SIDE = 720.
REFERENCE_FRAME = 1/60
MIN_SIGMA, MAX_SIGMA = 1., 24.  # Physical pixels; below 1 the thin core's aliasing shows.
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


def view_scale(width, height):
    """Scope logical pixels per reference pixel, independent of device pixel ratio."""
    return max(min(width, height), 1.)/REFERENCE_SIDE


def new_sample_count(frames, factor, available):
    """Display points to deposit this frame, so overlapping windows are not counted twice."""
    if frames <= 0 or available < 2:
        return 0
    return min(available-1, int(round(frames*factor)))


def spot_deposit(points, lengths, width, level, *, view_scale=1.):
    """Round spots for segments too short to rasterize; held samples merge and add energy."""
    held = lengths < STATIONARY_LENGTH
    if width <= 0 or not held.any():
        return np.empty(0, dtype=BEAM_VERTEX)
    # A slowly drifting beam starts a new spot each half width instead of smearing one.
    travel = np.floor(np.cumsum(lengths/view_scale)/(width/view_scale/2))
    first = held & np.concatenate(([True], ~held[:-1] | (travel[1:] != travel[:-1])))
    starts = np.flatnonzero(first)
    # Moving segments between groups end a group, so only held segments are counted.
    group = np.cumsum(first)-1
    members = held & (group >= 0)
    counts = np.bincount(group[members], minlength=len(starts))
    centers = np.column_stack([np.bincount(group[members], points[:-1][members, axis], len(starts)) for axis in (0, 1)])/counts[:, None]
    per_sample = ENERGY_SCALE/max(width/view_scale, .5)
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


def stroke_deposit(points, width, scale, strength, spot_width=None, energy_width=None, drawn=None, *, view_scale=1.):
    """Stroke whose colour is energy in R and G; zero alpha makes the blend additive.

    energy_width lets a narrow stroke carry the energy of a wider one, as the
    Gaussian core does for the trace thickness it is blurred out to. drawn, one flag
    per segment, blanks the beam on the others (a sweep's retrace).
    Points and widths are logical pixels; levels use reference pixels via view_scale.
    """
    energy_width = width if energy_width is None else energy_width
    points = np.asarray(points, dtype=np.float32)
    if len(points) < 2:
        return np.empty(0, dtype=BEAM_VERTEX)
    a, b = points[:-1], points[1:]
    delta = b - a
    every_length = np.hypot(delta[:, 0], delta[:, 1])
    if drawn is None:
        spots = spot_deposit(points, every_length, width if spot_width is None else spot_width, scale, view_scale=view_scale)
        keep = every_length >= STATIONARY_LENGTH
    else:
        drawn = np.asarray(drawn, dtype=bool)
        spots = spot_deposit(points, np.where(drawn, every_length, BLANKED_LENGTH), width if spot_width is None else spot_width, scale, view_scale=view_scale)
        keep = drawn & (every_length >= STATIONARY_LENGTH)
    if not keep.any():
        return spots
    kept_lengths = every_length[keep]
    # A sample's energy spreads over its footprint, so overlapping wide strokes sum to 1/speed.
    eff_lengths = np.maximum(kept_lengths/view_scale, max(energy_width/view_scale, .5))
    levels = np.clip(np.rint(scale*ENERGY_SCALE/eff_lengths**strength*(energy_width/width)), 0, 255)
    positive = levels > 0
    if not positive.any():
        return spots
    if not positive.all():
        indices = np.flatnonzero(keep)[positive]
        vertices = segment_quads(a[indices], b[indices], delta[indices], every_length[indices], width)
        levels = levels[positive]
    else:
        vertices = segment_quads(a[keep], b[keep], delta[keep], kept_lengths, width)
    result = np.empty(len(vertices), dtype=BEAM_VERTEX)
    result['x'], result['y'] = vertices[:, 0], vertices[:, 1]
    result['r'] = result['g'] = np.repeat(levels, 6)
    result['b'] = result['a'] = 0
    return np.concatenate((result, spots)) if len(spots) else result


def deposit_vertices(points, width, strength, glow_bands=(), spot_width=None, energy_width=None, drawn=None, *, view_scale=1.):
    """Core plus lower-energy glow bands, all following the same beam-speed strength."""
    core = stroke_deposit(points, width, 1., strength, spot_width, energy_width, drawn, view_scale=view_scale)
    if not glow_bands:
        return core
    parts = [stroke_deposit(points, band, scale, strength, drawn=drawn, view_scale=view_scale) for band, scale in glow_bands]
    parts.append(core)
    return np.concatenate(parts)


@lru_cache(maxsize=64)
def hex_color(value):
    return tuple(int(value[index:index+2], 16)/255 for index in (1, 3, 5))


def gaussian_beam(thickness, pixel_ratio, strength=1., *, view_scale=1.):
    """Core width (logical px) and blur sigma (physical px).

    Thickness is already scaled to logical pixels; view_scale converts the core's
    energy calculation back to reference pixels. Trace thickness is the beam's full
    width at half maximum. The core is drawn thin
    and carries the energy of the full thickness (stroke_deposit's energy_width), and
    the blur preserves it, so Gaussian matches Hard edge at any beam speed or
    beam-speed brightness. The core widens where its level would pass 8 bits, and the
    blur narrows so the core's own width does not broaden the profile.
    """
    target = min(MAX_SIGMA, max(MIN_SIGMA, thickness*pixel_ratio*FWHM_TO_SIGMA))
    # The slowest beam has the highest level: ENERGY_SCALE/max(thickness, .5)**strength.
    reference = thickness/view_scale
    unclipped = reference*ENERGY_SCALE/(255*max(reference, .5)**strength)*view_scale
    core = min(thickness, max(min(thickness, view_scale), unclipped))
    # A box of width w adds w**2/12 to the variance of the profile.
    sigma = max(MIN_SIGMA, math.sqrt(max(0., target**2-(core*pixel_ratio)**2/12)))
    return core, sigma


def beam_uniforms(profile, thickness, pixel_ratio, width, height, strength=1., *, view_scale=1.):
    """Shader inputs for the beam profile; a hard edge bypasses the blur passes."""
    if profile != BEAM_GAUSSIAN or width <= 0 or height <= 0:
        return dict(gaussian=False, depositGain=1.)
    _, sigma = gaussian_beam(thickness, pixel_ratio, strength, view_scale=view_scale)
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


# phosphor_bloom_down.frag box-averages at most this many texels per axis (MAX_FACTOR).
# bloom_uniforms and halo_uniforms never choose a larger downsampling factor.
BLOOM_DOWN_MAX_FACTOR = 32


def bloom_uniforms(enabled, thickness, glow_size, intensity, pixel_ratio, width, height, *, view_scale=1.):
    """Shader inputs for glow drawn as bloom of the energy buffer, or bloom switched off.

    The blur runs on a texture downsampled by bloomFactor, chosen so the low-resolution
    sigma stays a few texels: wide glow costs about the same as narrow glow.
    Thickness and glow_size are already scaled logical pixels. Compute gain in
    reference units too, so its thickness floor cannot dim small scopes.
    """
    if not enabled or intensity <= 0 or glow_size <= 0 or width <= 0 or height <= 0:
        return dict(bloomOn=False, bloomGain=0.)
    sigma = max(.5, glow_size*BLOOM_SIGMA*pixel_ratio)
    factor = 1 if sigma < 3 else 2 if sigma < 8 else 4 if sigma < 24 else 8 if sigma < 48 else 16 if sigma < 96 else 32
    pixels = (max(1, round(width*pixel_ratio)), max(1, round(height*pixel_ratio)))
    size = tuple(max(1, math.ceil(extent/factor)) for extent in pixels)
    return dict(bloomOn=True, bloomGain=bloom_gain(thickness*pixel_ratio/view_scale, sigma/view_scale, intensity),
                bloomSigma=sigma/factor, bloomFactor=float(factor), bloomSize=size,
                energyTexel=(1/pixels[0], 1/pixels[1]),
                bloomTexelX=(1/size[0], 0.), bloomTexelY=(0., 1/size[1]))


def halo_uniforms(enabled, width, height, pixel_ratio):
    """Coarse wide-angle faceplate halation pass (High and Ultra tiers)."""
    if not enabled or width <= 0 or height <= 0:
        return dict(haloOn=False, haloTexOn=0.)
    pixels = (max(1, round(width*pixel_ratio)), max(1, round(height*pixel_ratio)))
    factor = 12 if max(pixels) >= 512 else 8
    size = tuple(max(1, math.ceil(extent/factor)) for extent in pixels)
    return dict(haloOn=True, haloTexOn=1., haloFactor=float(factor), haloSigma=6.0,
                haloSize=size, haloTexelX=(1/size[0], 0.), haloTexelY=(0., 1/size[1]))


def quality_uniforms(quality, phosphor_name, vignette, grid_on, grid_style, grid_divisions, bloom_on, bloom_gain_value):
    """Visual simulation uniforms gated by the independent graphics quality tier."""
    if quality == QUALITY_LOW:
        return dict(softSat=0., powerLaw=0., cascade=0., haloGain=0., linearTone=0.,
                    vignette=0., edgeGraticule=0., gridDivisions=float(grid_divisions))
    if quality == QUALITY_MEDIUM:
        return dict(softSat=1., powerLaw=0., cascade=0., haloGain=0., linearTone=0.,
                    vignette=0., edgeGraticule=0., gridDivisions=float(grid_divisions))
    cascade = P7_CASCADE if phosphor_name == P7 else 0.
    has_bloom = bool(bloom_on and bloom_gain_value > 0.)
    if quality == QUALITY_ULTRA:
        return dict(softSat=1., powerLaw=POWER_LAW, cascade=cascade,
                    haloGain=.22 if has_bloom else 0., linearTone=1.,
                    vignette=float(vignette),
                    edgeGraticule=.18 if (grid_on and grid_style == GRID_GRATICULE) else 0.,
                    gridDivisions=float(grid_divisions))
    # High (default)
    return dict(softSat=1., powerLaw=POWER_LAW, cascade=cascade,
                haloGain=.14 if has_bloom else 0., linearTone=1.,
                vignette=float(vignette), edgeGraticule=0.,
                gridDivisions=float(grid_divisions))


def color_hex(rgb):
    return '#'+''.join(f'{round(min(1., max(0., channel))*255):02x}' for channel in rgb)


@lru_cache(maxsize=32)
def _preset_profile(name, trace_color):
    preset = PHOSPHORS[name]
    palette = hex_color(trace_color)
    return dict(flash_time=preset['fast'], afterglow_time=preset['slow'],
                afterglow_level=preset['slow_gain']*min(1., SLOW_REFERENCE/preset['slow']),
                flash_color=color_hex(preset.get('fast_color', palette)),
                afterglow_color=color_hex(preset.get('slow_color', palette)))


def preset_profile(name, trace_color):
    """A preset as an editable profile. Its afterglow level is the gain the display uses:
    the preset's slow gain scaled back for long decay, which piles up more energy."""
    return dict(_preset_profile(name, trace_color))


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


def frame_gain(dt, tau):
    """Normalize post-deposit energy to the 60 Hz reference (stable near zero)."""
    if dt <= 0 or dt == REFERENCE_FRAME:
        return 1.
    def f(x):
        return x / -math.expm1(-x) if x else 1.
    return f(REFERENCE_FRAME/tau)/f(dt/tau)


def tail_keep(tau, elapsed):
    """Scale the capped 60 Hz tail retention to this step's duration."""
    reference = math.exp(-REFERENCE_FRAME/tau)
    return min(.992, max(reference, 1e-5)**.65)**(elapsed/REFERENCE_FRAME)


def phosphor_uniforms(profile, elapsed, intensity, *, decay_elapsed=None):
    """Shader inputs for a decay profile; decay stays correct at any frame interval."""
    elapsed = max(0., elapsed)
    dt = elapsed if decay_elapsed is None else max(0., decay_elapsed)
    # Once STEP alone exhausts both packed channels, additional idle time is immaterial.
    dt = min(dt, 2 * 65025 * REFERENCE_FRAME)
    flash, glow = profile['flash_time'], profile['afterglow_time']
    fast_keep = math.exp(-dt/flash)
    slow_keep = math.exp(-dt/glow)
    transfer = 1-fast_keep
    if dt > REFERENCE_FRAME:
        # Flash transferred early in a long frame has already faded by its end.
        # Sum the 60 Hz exponential cascade in closed form, also for fractional frames.
        f, s = math.exp(-REFERENCE_FRAME/flash), math.exp(-REFERENCE_FRAME/glow)
        spread = ((slow_keep-fast_keep)/(s-f) if abs(s-f) > 1e-9 else
                  (dt/REFERENCE_FRAME)*math.exp(-(dt-REFERENCE_FRAME)/flash))
        transfer = (1-f)*spread
    return dict(fastKeep=fast_keep, slowKeep=slow_keep, cascadeWeight=transfer,
                tailKeep=tail_keep(profile['afterglow_time'], dt),
                fastGain=frame_gain(elapsed, profile['flash_time']),
                slowGain=frame_gain(elapsed, profile['afterglow_time']),
                stepScale=dt/REFERENCE_FRAME,
                idleStepScale=max(0., dt-elapsed)/REFERENCE_FRAME,
                shimmerScale=math.sqrt(elapsed/REFERENCE_FRAME) if elapsed else 1.,
                afterglowGain=profile['afterglow_level'], exposure=EXPOSURE*intensity,
                fastColor=hex_color(profile['flash_color']), slowColor=hex_color(profile['afterglow_color']))


ENERGY_UNIT = 64/65025  # One low-byte quantum in the packed energy buffer.
PREVIEW_ENERGY = .5  # Energy one pass of the beam leaves on a pixel, for the decay preview.
HOT_WHITE = .08  # Matches phosphor_display.frag.


def _decay_step(fast, slow, fast_keep, slow_keep, tail, cascade, steps, power_law=POWER_LAW):
    """One no-deposit accumulate-shader update, before texture quantization."""
    keep = slow_keep + (tail-slow_keep) * (power_law*.45/(1+2*slow))
    bleed = ENERGY_UNIT*steps
    return (np.maximum(0., fast*fast_keep-bleed),
            np.maximum(0., slow*keep+fast*(1-fast_keep)*cascade-bleed))


def _preview_energy(profile, times, quality, name):
    # Follow the mean (unquantized) 60 Hz trajectory, then step each fractional frame.
    simulated = quality in (QUALITY_HIGH, QUALITY_ULTRA)
    power_law = POWER_LAW if simulated else 0.
    cascade = P7_CASCADE if simulated and name == P7 else 0.
    steps = times/REFERENCE_FRAME
    whole = steps.astype(int)
    count = int(whole.max(initial=0)) + 1
    fast, slow = np.empty(count), np.empty(count)
    fast[0] = slow[0] = PREVIEW_ENERGY
    fk = math.exp(-REFERENCE_FRAME/profile['flash_time'])
    sk = math.exp(-REFERENCE_FRAME/profile['afterglow_time'])
    tail = tail_keep(profile['afterglow_time'], REFERENCE_FRAME)
    for i in range(1, count):
        fast[i], slow[i] = _decay_step(fast[i-1], slow[i-1], fk, sk, tail, cascade, 1., power_law)
    part = steps-whole
    return _decay_step(fast[whole], slow[whole], fk**part, sk**part, tail**part, cascade, part, power_law)


def decay_preview(profile, intensity, times, quality=DEFAULT_GRAPHICS_QUALITY, name=PHOSPHOR_CUSTOM):
    """Light after one beam pass, as phosphor_display.frag tone-maps it.

    Returns the flash and afterglow brightness and the displayed colour at each time.
    Grain and shimmer are mean-1 noise across pixels and frames, so the preview shows
    the average pixel and does not model them.
    """
    times = np.asarray(times, dtype=float)
    exposure = EXPOSURE*intensity
    fast_energy, slow_energy = _preview_energy(profile, times, quality, name)
    level = profile['afterglow_level']
    flash = 1-np.exp(-fast_energy*exposure)
    afterglow = 1-np.exp(-slow_energy*exposure*level)
    fast_light = np.outer(flash, hex_color(profile['flash_color']))
    slow_light = np.outer(afterglow, hex_color(profile['afterglow_color']))
    linear = quality in (QUALITY_HIGH, QUALITY_ULTRA)
    light = fast_light**2.2 + slow_light**2.2 if linear else fast_light + slow_light
    hot = 1-np.exp(-(fast_energy+slow_energy*level)*exposure*HOT_WHITE)
    light = light+(1-light)*(.7*hot*hot)[:, None]
    light = np.minimum(light, 1.)
    return flash, afterglow, light**(1/2.2) if linear else light


def afterglow_level_at(profile, intensity, brightness, quality=DEFAULT_GRAPHICS_QUALITY, name=PHOSPHOR_CUSTOM):
    """Inverse of decay_preview's afterglow at its own decay time, for dragging the curve."""
    _, slow = _preview_energy(profile, np.array([profile['afterglow_time']]), quality, name)
    exposure = EXPOSURE*intensity*slow[0]
    if exposure == 0: return profile['afterglow_level']
    if brightness <= 0.: return 0.
    if brightness >= -math.expm1(-exposure): return 1.
    return -math.log1p(-brightness)/exposure
