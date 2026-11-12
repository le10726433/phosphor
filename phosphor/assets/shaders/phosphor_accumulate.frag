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
    float shimmer;   // 0 = off: beam shot noise on newly deposited energy.
    float seed;      // Changes every step, so the noise is new each frame.
    vec2 viewSize;   // Item size in points.
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

float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void main() {
    vec4 old = texture(previous, qt_TexCoord0);
    vec4 added = texture(deposit, qt_TexCoord0);
    // A Gaussian deposit arrives packed in RG; a hard-edged one is plain 8-bit.
    float blurred = (floor(added.r * 255.0 + 0.5) + added.g) / 255.0 * depositGain;
    float addFast = packedDeposit > 0.5 ? blurred : added.r;
    float addSlow = packedDeposit > 0.5 ? blurred : added.g;
    if (shimmer > 0.0) {
        // Only new energy varies, so persistence averages it out on steady traces and
        // it shows as shimmer on faint, fast strokes. The mean deposit is unchanged.
        vec2 cell = floor(qt_TexCoord0 * viewSize) + vec2(seed * 37.0, seed * 91.0);
        float n = 1.0 + shimmer * 1.8 * (hash12(cell) - 0.5);
        addFast *= n;
        addSlow *= n;
    }
    float fast = max(0.0, unpack(old.rg) * fastKeep + addFast - STEP);
    float slow = max(0.0, unpack(old.ba) * slowKeep + addSlow - STEP);
    fragColor = vec4(pack(fast), pack(slow));
}
