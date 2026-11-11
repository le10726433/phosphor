#version 440
// Bloom input: box-averages factor x factor energy texels into one low-resolution
// texel, keeping fast (RG) and slow (BA) energy apart, packed against BLOOM_MAX.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 energyTexel;
    float factor;
};
layout(binding = 1) uniform sampler2D energy;

const float MAX_ENERGY = 64.0;
const float BLOOM_MAX = 16.0;  // Bloom is spread out, so a smaller range buys precision.
const int MAX_FACTOR = 8;

float unpack(vec2 packed) {
    return (floor(packed.x * 255.0 + 0.5) + packed.y) / 255.0 * MAX_ENERGY;
}

vec2 pack(float value) {
    float scaled = clamp(value / BLOOM_MAX, 0.0, 0.99998) * 255.0;
    float high = floor(scaled);
    return vec2(high / 255.0, scaled - high);
}

void main() {
    float fast = 0.0;
    float slow = 0.0;
    float start = -(factor - 1.0) * 0.5;
    for (int i = 0; i < MAX_FACTOR; ++i) {
        if (float(i) >= factor) break;
        for (int j = 0; j < MAX_FACTOR; ++j) {
            if (float(j) >= factor) break;
            vec4 e = texture(energy, qt_TexCoord0 + energyTexel * vec2(start + float(i), start + float(j)));
            fast += unpack(e.rg);
            slow += unpack(e.ba);
        }
    }
    float count = factor * factor;
    fragColor = vec4(pack(fast / count), pack(slow / count));
}
