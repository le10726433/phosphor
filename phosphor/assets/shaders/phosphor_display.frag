#version 440
// Tone-maps packed energy to light. Alpha 0 with premultiplied colour adds to the screen.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float exposure;
    float slowGain;
    vec3 fastColor;
    vec3 slowColor;
    float bloomGain;
    float grain;     // 0 = off: phosphor crystals of uneven brightness, fixed to the glass.
    vec2 viewSize;   // Item size in points, so grain keeps its size on high-DPI screens.
    // Append scalars after the aligned vec2; Qt binds these by reflected property name.
    float haloGain;      // 0 = off: wide CRT faceplate glass halation.
    float haloTexOn;     // >0.5 = the coarse halo pass is ready (High, Ultra); else no halo.
    float vignette;      // 0 = flat, >0 = radial glass corner falloff.
    float linearTone;    // >0.5 = linear-light colour mixing and hot-core tone mapping.
    float edgeGraticule; // 0 = off, >0 = subtle edge-lit graticule response (Ultra).
    float gridDivisions; // Divisions per axis for edge-lit graticule.
    float viewScale;     // Logical pixels per reference pixel.
};
layout(binding = 1) uniform sampler2D energy;
layout(binding = 2) uniform sampler2D bloom;
layout(binding = 3) uniform sampler2D halo;

const float MAX_ENERGY = 64.0;
const float BLOOM_MAX = 16.0;

float unpack(vec2 packed) {
    return (floor(packed.x * 255.0 + 0.5) + packed.y) / 255.0 * MAX_ENERGY;
}

// The bloom texture is sampled with linear filtering. The packed value is linear in
// both bytes, so it must not be rounded after filtering.
float unpackFiltered(vec2 packed) {
    return (packed.x * 255.0 + packed.y) / 255.0 * BLOOM_MAX;
}

// Sine-free hash (Dave Hoskins): identical on Metal and Direct3D, no texture.
float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

// Mean-1 brightness factor of the crystal under this point: fine grains plus clumps.
float grainFactor(vec2 uv) {
    vec2 cell = floor(uv * viewSize);
    float n = 0.65 * hash12(cell) + 0.35 * hash12(floor(cell / 3.0) + 71.0);
    return 1.0 + grain * 1.6 * (n - 0.5);
}

void main() {
    vec4 e = texture(energy, qt_TexCoord0);
    float fastEnergy = unpack(e.rg);
    float slowEnergy = unpack(e.ba);
    float scatterFast = 0.0;
    float scatterSlow = 0.0;
    if (bloomGain > 0.0) {
        // Glow is the energy spread by a wide kernel: continuous around any corner.
        vec4 b = texture(bloom, qt_TexCoord0);
        scatterFast = bloomGain * unpackFiltered(b.rg);
        scatterSlow = bloomGain * unpackFiltered(b.ba);
        fastEnergy += scatterFast;
        slowEnergy += scatterSlow;
    }
    if (haloGain > 0.0 && haloTexOn > 0.5) {
        // Wide faceplate glass halation from the dedicated coarse pass. Offset taps of the
        // narrow bloom would draw ghost copies of each line, so no pass means no halo.
        vec4 h = texture(halo, qt_TexCoord0);
        float hf = haloGain * unpackFiltered(h.rg);
        float hs = haloGain * unpackFiltered(h.ba);
        fastEnergy += hf;
        slowEnergy += hs;
        scatterFast += hf;
        scatterSlow += hs;
    }
    if (grain > 0.0) {
        // Applied before tone mapping: grain shows in dim edges and glow, and the
        // saturated core stays smooth, as on a real screen.
        float g = grainFactor(qt_TexCoord0);
        fastEnergy *= g;
        slowEnergy *= g;
    }
    float fast = 1.0 - exp(-fastEnergy * exposure);
    float slow = 1.0 - exp(-slowEnergy * exposure * slowGain);
    float hot = 1.0 - exp(-(fastEnergy + slowEnergy * slowGain) * exposure * 0.08);
    vec3 light;
    if (linearTone > 0.5) {
        // Blend flash, afterglow and incandescent hot-core desaturation in linear light.
        vec3 fastLin = pow(max(fastColor, vec3(0.0)), vec3(2.2));
        vec3 slowLin = pow(max(slowColor, vec3(0.0)), vec3(2.2));
        vec3 lin = fastLin * pow(max(fast, 0.0), 2.2) + slowLin * pow(max(slow, 0.0), 2.2);
        lin = mix(lin, vec3(1.0), 0.7 * hot * hot);
        light = pow(clamp(lin, 0.0, 1.0), vec3(1.0 / 2.2));
    } else {
        light = fastColor * fast + slowColor * slow;
        light = mix(light, vec3(1.0), 0.7 * hot * hot);
    }
    if (edgeGraticule > 0.0 && gridDivisions >= 2.0) {
        // Black faceplate graticule occlusion: etched black lines stay dark over phosphor glow.
        float spacing = min(viewSize.x, viewSize.y) / gridDivisions;
        if (spacing > 4.0) {
            vec2 pos = qt_TexCoord0 * viewSize - 0.5 * viewSize;
            float phase = mod(gridDivisions, 2.0) * 0.5;
            vec2 cell = abs(fract(pos / spacing - phase + 0.5) - 0.5) * spacing;
            float lineDist = min(cell.x, cell.y);
            float axisDist = min(abs(pos.x), abs(pos.y));
            // Half coverage at the drawn widths' half-extents: 0.5 and 0.8 logical px.
            float mask = 1.0 - min(smoothstep(0.0, 1.0, lineDist * viewScale),
                                   smoothstep(0.3, 1.3, axisDist * viewScale));
            light *= (1.0 - mask * min(edgeGraticule * 4.0, 0.85));
        }
    }
    if (vignette > 0.0) {
        vec2 centered = qt_TexCoord0 * 2.0 - 1.0;
        float r2 = dot(centered, centered);
        float vig = clamp(1.0 - vignette * 0.35 * r2 * r2, 0.25, 1.0);
        light *= vig;
    }
    fragColor = vec4(min(light, vec3(1.0)), 0.0) * qt_Opacity;
}
