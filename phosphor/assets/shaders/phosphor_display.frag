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
    float fast = 1.0 - exp(-fastEnergy * exposure);
    float slow = 1.0 - exp(-slowEnergy * exposure * slowGain);
    vec3 light = fastColor * fast + slowColor * slow;
    // Very bright regions saturate toward white, as an overdriven phosphor does.
    float hot = 1.0 - exp(-(fastEnergy + slowEnergy * slowGain) * exposure * 0.08);
    light = mix(light, vec3(1.0), 0.7 * hot * hot);
    fragColor = vec4(min(light, vec3(1.0)), 0.0) * qt_Opacity;
}
