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
};
layout(binding = 1) uniform sampler2D energy;
layout(binding = 2) uniform sampler2D bloom;

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
    if (bloomGain > 0.0) {
        // Glow is the energy spread by a wide kernel: continuous around any corner.
        vec4 b = texture(bloom, qt_TexCoord0);
        fastEnergy += bloomGain * unpackFiltered(b.rg);
        slowEnergy += bloomGain * unpackFiltered(b.ba);
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
    vec3 light = fastColor * fast + slowColor * slow;
    // Very bright regions saturate toward white, as an overdriven phosphor does.
    float hot = 1.0 - exp(-(fastEnergy + slowEnergy * slowGain) * exposure * 0.08);
    light = mix(light, vec3(1.0), 0.7 * hot * hot);
    fragColor = vec4(min(light, vec3(1.0)), 0.0) * qt_Opacity;
}
