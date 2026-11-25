#version 440
// Packs the rendered side x side RGBA picture into NV12 bytes inside an RGBA8 target of
// (side / 4) x (side * 3 / 2) texels, so a readback is the NV12 frame in memory order:
// rows 0..side-1 hold four luma bytes per texel, the rest two U/V pairs per texel.
// BT.601 limited range like ffmpeg's default rgba -> yuv420p; chroma is the 2x2 mean.
// export_nv12.rgba_to_nv12 is the NumPy reference for this shader.
layout(location = 0) out vec4 fragColor;
layout(binding = 1) uniform sampler2D source;

float quantize(float value) { return clamp(floor(value + 0.5), 0.0, 255.0) / 255.0; }

vec3 rgb(ivec2 p) { return texelFetch(source, p, 0).rgb * 255.0; }

float luma(vec3 c) { return quantize(16.0 + dot(c, vec3(0.256788, 0.504129, 0.097906))); }

vec2 chroma(ivec2 block)
{
    ivec2 p = block * 2;
    vec3 c = (rgb(p) + rgb(p + ivec2(1, 0)) + rgb(p + ivec2(0, 1)) + rgb(p + ivec2(1, 1))) * 0.25;
    return vec2(quantize(128.0 + dot(c, vec3(-0.148223, -0.290993, 0.439216))),
                quantize(128.0 + dot(c, vec3(0.439216, -0.367788, -0.071427))));
}

void main()
{
    ivec2 o = ivec2(gl_FragCoord.xy);
    int side = textureSize(source, 0).x;
    if (o.y < side) {
        int x = o.x * 4;
        fragColor = vec4(luma(rgb(ivec2(x, o.y))), luma(rgb(ivec2(x + 1, o.y))),
                         luma(rgb(ivec2(x + 2, o.y))), luma(rgb(ivec2(x + 3, o.y))));
    } else {
        int row = o.y - side;
        fragColor = vec4(chroma(ivec2(o.x * 2, row)), chroma(ivec2(o.x * 2 + 1, row)));
    }
}
