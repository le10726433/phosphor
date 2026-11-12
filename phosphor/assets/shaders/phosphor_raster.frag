#version 440
// Raster beam deposit for video: one 60 Hz CRT field per step. The picture is sampled
// once per scanline (vertical resolution = scanLines) and laid down as a horizontal
// line with a Gaussian vertical profile, so dark gaps separate the lines.
// Output matches the hard-edged deposit: plain 8-bit energy in R (fast) and G (slow).
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float scanLines;   // Visible lines over the screen height.
    float lineWidth;   // Gaussian sigma of a line, as a fraction of the line pitch.
    float gain;        // Energy per unit of linear luminance this step.
};
layout(binding = 1) uniform sampler2D video;

void main() {
    float position = qt_TexCoord0.y * scanLines;
    float line = floor(position);
    // The beam is modulated by the picture at this line's centre only.
    vec3 rgb = texture(video, vec2(qt_TexCoord0.x, (line + 0.5) / scanLines)).rgb;
    // Video is gamma encoded; the beam current follows linear light.
    float luma = dot(pow(rgb, vec3(2.2)), vec3(0.2126, 0.7152, 0.0722));
    float offset = (position - line - 0.5) / max(lineWidth, 0.05);
    float profile = exp(-0.5 * offset * offset);
    float energy = clamp(gain * luma * profile, 0.0, 1.0);
    fragColor = vec4(energy, energy, 0.0, 1.0);
}
