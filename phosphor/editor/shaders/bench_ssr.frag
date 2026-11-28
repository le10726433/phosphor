float linearizeDepth(float d)
{
    // Hardware [0, 1] depth (Metal & Direct3D 11) with camera clipNear=10, clipFar=2500
    float zNear = 10.0;
    float zFar = 2500.0;
    return (zNear * zFar) / max(1.0, zFar - d * (zFar - zNear));
}

vec3 reconstructViewPos(vec2 uv, float linZ, float aspect, float tanHalfFov)
{
    // QtQuick3D Effect UV: y=0 is top of screen, y=1 is bottom of screen
    vec2 ndc = vec2(uv.x * 2.0 - 1.0, 1.0 - uv.y * 2.0);
    return vec3(ndc.x * aspect * tanHalfFov * linZ, ndc.y * tanHalfFov * linZ, -linZ);
}

void MAIN()
{
    vec4 baseColor = texture(INPUT, INPUT_UV);
    if (ssrStrength <= 0.001) {
        FRAGCOLOR = baseColor;
        return;
    }

    // Bench occupies lower portion of frame; fast early-out for upper wall & scope
    if (INPUT_UV.y < 0.58) {
        FRAGCOLOR = baseColor;
        return;
    }

    float depthC = texture(DEPTH_TEXTURE, INPUT_UV).r;
    if (depthC >= 0.9995) {
        FRAGCOLOR = baseColor;
        return;
    }

    vec2 texSize = vec2(textureSize(INPUT, 0));
    vec2 px = 1.5 / max(texSize, vec2(1.0));
    float aspect = texSize.x / max(1.0, texSize.y);
    float tanHalfFov = tan(radians(max(10.0, camFov) * 0.5));

    float zC = linearizeDepth(depthC);
    float zL = linearizeDepth(texture(DEPTH_TEXTURE, INPUT_UV - vec2(px.x, 0.0)).r);
    float zR = linearizeDepth(texture(DEPTH_TEXTURE, INPUT_UV + vec2(px.x, 0.0)).r);
    float zU = linearizeDepth(texture(DEPTH_TEXTURE, INPUT_UV - vec2(0.0, px.y)).r);
    float zD = linearizeDepth(texture(DEPTH_TEXTURE, INPUT_UV + vec2(0.0, px.y)).r);

    // Reject silhouette edges (red switch box top edge, lamp base rim, cables) via depth continuity
    float maxJump = max(max(abs(zL - zC), abs(zR - zC)), max(abs(zU - zC), abs(zD - zC)));
    float contMask = 1.0 - smoothstep(3.5, 9.0, maxJump);
    if (contMask <= 0.01) {
        FRAGCOLOR = baseColor;
        return;
    }

    // Pick smaller-delta neighbor per axis to reconstruct crisp view-space surface normal
    vec3 pC = reconstructViewPos(INPUT_UV, zC, aspect, tanHalfFov);
    vec3 pL = reconstructViewPos(INPUT_UV - vec2(px.x, 0.0), zL, aspect, tanHalfFov);
    vec3 pR = reconstructViewPos(INPUT_UV + vec2(px.x, 0.0), zR, aspect, tanHalfFov);
    vec3 pU = reconstructViewPos(INPUT_UV - vec2(0.0, px.y), zU, aspect, tanHalfFov);
    vec3 pD = reconstructViewPos(INPUT_UV + vec2(0.0, px.y), zD, aspect, tanHalfFov);

    vec3 dPdx = (abs(zR - zC) < abs(zL - zC)) ? (pR - pC) : (pC - pL);
    vec3 dPdy = (abs(zU - zC) < abs(zD - zC)) ? (pU - pC) : (pC - pD);
    vec3 viewN = normalize(cross(dPdx, dPdy));
    if (viewN.z < 0.0) {
        viewN = -viewN;
    }

    // Strictly isolate horizontal bench & cutting-mat surfaces (viewN.y ~ cos(7.2 deg) = 0.992),
    // rejecting vertical front/side box faces (viewN.y <= 0.15) with zero leakage!
    float horizMask = smoothstep(0.88, 0.96, viewN.y);
    float screenFade = smoothstep(0.60, 0.72, INPUT_UV.y) * smoothstep(0.995, 0.90, INPUT_UV.y);
    float deskMask = horizMask * contMask * screenFade;
    if (deskMask <= 0.005) {
        FRAGCOLOR = baseColor;
        return;
    }

    // Physical Schlick Fresnel weighting for grazing reflections across the lacquered oak & cutting mat
    vec3 V = normalize(-pC);
    float ndotv = clamp(dot(viewN, V), 0.0, 1.0);
    float fresnel = 0.04 + 0.96 * pow(1.0 - ndotv, 3.2);

    // Subtle interleaved gradient noise jitter removes stepping bands along the raymarch
    vec2 pixCoord = INPUT_UV * texSize;
    float jitter = fract(52.9829189 * fract(dot(pixCoord, vec2(0.06711056, 0.00583715))));

    int steps = (highQuality > 0.5) ? 20 : 12;
    float invSteps = 1.0 / float(steps);
    vec3 reflAccum = vec3(0.0);
    float hitWeight = 0.0;

    for (int i = 1; i <= 20; ++i) {
        if (i > steps) break;
        float t = (float(i) - 0.5 + (jitter - 0.5) * 0.75) * invSteps;
        vec2 sampleUv = INPUT_UV - vec2(0.0, t * 0.24);
        if (sampleUv.y < 0.02) break;

        float sampleDepth = texture(DEPTH_TEXTURE, sampleUv).r;
        if (sampleDepth < 0.999) {
            float sampleZ = linearizeDepth(sampleDepth);
            // Accept physically plausible reflections of objects rising above/near the bench contact point
            float dz = sampleZ - zC;
            if (dz >= -12.0 && dz <= 165.0) {
                vec3 sampleCol = texture(INPUT, sampleUv).rgb;
                float luma = dot(sampleCol, vec3(0.2126, 0.7152, 0.0722));
                float distFade = (1.0 - t) * (1.0 - t);
                float depthWeight = smoothstep(-12.0, 2.0, dz) * (1.0 - smoothstep(95.0, 165.0, dz));
                float w = distFade * depthWeight * (0.35 + 0.90 * clamp(luma, 0.0, 1.8));
                reflAccum += sampleCol * w;
                hitWeight += w;
            }
        }
    }

    vec3 outCol = baseColor.rgb;
    if (hitWeight > 0.001) {
        vec3 reflCol = reflAccum / hitWeight;
        float reflStrength = clamp(deskMask * ssrStrength * (0.06 + 0.22 * fresnel), 0.0, 0.22);
        outCol = mix(outCol, outCol + reflCol * 0.78, reflStrength);
    }
    FRAGCOLOR = vec4(outCol, baseColor.a);
}
