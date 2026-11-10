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
};
layout(binding = 1) uniform sampler2D energy;

const float MAX_ENERGY = 64.0;

float unpack(vec2 packed) {
    return (floor(packed.x * 255.0 + 0.5) + packed.y) / 255.0 * MAX_ENERGY;
}

void main() {
    vec4 e = texture(energy, qt_TexCoord0);
    float fastEnergy = unpack(e.rg);
    float slowEnergy = unpack(e.ba);
    float fast = 1.0 - exp(-fastEnergy * exposure);
    float slow = 1.0 - exp(-slowEnergy * exposure * slowGain);
    vec3 light = fastColor * fast + slowColor * slow;
    // Very bright regions saturate toward white, as an overdriven phosphor does.
    float hot = 1.0 - exp(-(fastEnergy + slowEnergy * slowGain) * exposure * 0.08);
    light = mix(light, vec3(1.0), 0.7 * hot * hot);
    fragColor = vec4(min(light, vec3(1.0)), 0.0) * qt_Opacity;
}
