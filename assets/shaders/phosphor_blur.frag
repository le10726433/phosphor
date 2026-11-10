#version 440
// One direction of the Gaussian beam profile. Output is packed as 16-bit fixed point
// because blurred tails are far too faint for an 8-bit layer.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 texel;
    float sigma;
    float packedInput;
};
layout(binding = 1) uniform sampler2D source;

const int MAX_RADIUS = 24;  // Three times the largest sigma in phosphor.py.

float energyAt(vec2 uv) {
    vec4 value = texture(source, uv);
    return packedInput > 0.5 ? (floor(value.r * 255.0 + 0.5) + value.g) / 255.0 : value.r;
}

void main() {
    float sum = energyAt(qt_TexCoord0);
    float total = 1.0;
    for (int i = 1; i <= MAX_RADIUS; ++i) {
        float x = float(i);
        if (x > 3.0 * sigma) break;
        float weight = exp(-x * x / (2.0 * sigma * sigma));
        sum += weight * (energyAt(qt_TexCoord0 + texel * x) + energyAt(qt_TexCoord0 - texel * x));
        total += 2.0 * weight;
    }
    float scaled = clamp(sum / total, 0.0, 0.99998) * 255.0;
    float high = floor(scaled);
    fragColor = vec4(high / 255.0, scaled - high, 0.0, 1.0);
}
