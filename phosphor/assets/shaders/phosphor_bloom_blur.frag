#version 440
// One direction of the bloom Gaussian on the low-resolution bloom texture. Unlike the
// beam blur it carries both fast (RG) and slow (BA) energy, packed against BLOOM_MAX.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 texel;
    float sigma;
};
layout(binding = 1) uniform sampler2D source;

const float BLOOM_MAX = 16.0;
const int MAX_RADIUS = 32;

float unpack(vec2 packed) {
    return (floor(packed.x * 255.0 + 0.5) + packed.y) / 255.0 * BLOOM_MAX;
}

vec2 pack(float value) {
    float scaled = clamp(value / BLOOM_MAX, 0.0, 0.99998) * 255.0;
    float high = floor(scaled);
    return vec2(high / 255.0, scaled - high);
}

vec2 energyAt(vec2 uv) {
    vec4 value = texture(source, uv);
    return vec2(unpack(value.rg), unpack(value.ba));
}

void main() {
    vec2 sum = energyAt(qt_TexCoord0);
    float total = 1.0;
    for (int i = 1; i <= MAX_RADIUS; ++i) {
        float x = float(i);
        if (x > 3.0 * sigma) break;
        float weight = exp(-x * x / (2.0 * sigma * sigma));
        sum += weight * (energyAt(qt_TexCoord0 + texel * x) + energyAt(qt_TexCoord0 - texel * x));
        total += 2.0 * weight;
    }
    sum /= total;
    fragColor = vec4(pack(sum.x), pack(sum.y));
}
