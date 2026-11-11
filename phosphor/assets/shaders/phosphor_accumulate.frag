#version 440
// Energy buffer update. Fast energy is packed in RG and slow energy in BA as 16-bit
// fixed point: floating-point layers render empty in multisampled Direct3D 11 windows.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float fastKeep;
    float slowKeep;
    float depositGain;
    float packedDeposit;
};
layout(binding = 1) uniform sampler2D previous;
layout(binding = 2) uniform sampler2D deposit;

const float MAX_ENERGY = 64.0;
const float STEP = MAX_ENERGY / 65025.0;  // Removed each frame so faint tails reach zero.

float unpack(vec2 packed) {
    return (floor(packed.x * 255.0 + 0.5) + packed.y) / 255.0 * MAX_ENERGY;
}

vec2 pack(float value) {
    float scaled = clamp(value / MAX_ENERGY, 0.0, 0.99998) * 255.0;
    float high = floor(scaled);
    return vec2(high / 255.0, scaled - high);
}

void main() {
    vec4 old = texture(previous, qt_TexCoord0);
    vec4 added = texture(deposit, qt_TexCoord0);
    // A Gaussian deposit arrives packed in RG; a hard-edged one is plain 8-bit.
    float blurred = (floor(added.r * 255.0 + 0.5) + added.g) / 255.0 * depositGain;
    float addFast = packedDeposit > 0.5 ? blurred : added.r;
    float addSlow = packedDeposit > 0.5 ? blurred : added.g;
    float fast = max(0.0, unpack(old.rg) * fastKeep + addFast - STEP);
    float slow = max(0.0, unpack(old.ba) * slowKeep + addSlow - STEP);
    fragColor = vec4(pack(fast), pack(slow));
}
