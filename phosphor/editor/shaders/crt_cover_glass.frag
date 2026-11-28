VARYING vec3 v_localPos;
VARYING vec2 v_uv;

void MAIN()
{
    vec2 uv = v_uv;
    vec3 nrm = normalize(NORMAL);
    vec3 viewDir = normalize(VIEW_VECTOR);
    float ndotv = clamp(dot(nrm, viewDir), 0.0, 1.0);

    // Physical Schlick Fresnel for curved soda-lime CRT glass (IOR ~ 1.52 -> F0 = 0.043)
    float fresnel = 0.043 + 0.957 * pow(1.0 - ndotv, 4.2);

    // Smooth hood-pocket perimeter vignette (soft shadow where curved glass meets deep bezel)
    vec2 edgeDist = min(uv, 1.0 - uv);
    float hoodShadow = 1.0 - smoothstep(0.0, 0.065, edgeDist.x) * smoothstep(0.0, 0.065, edgeDist.y);

    // Analytic resolution-independent etched CRT graticule (8x8 divisions + 5x center subdivisions)
    float gridMask = 0.0;
    float majorMask = 0.0;
    if (showGrid > 0.5) {
        // Inset slightly so lines stay cleanly inside the glass aperture
        vec2 gUv = uv;
        float inside = step(0.015, gUv.x) * step(gUv.x, 0.985) * step(0.015, gUv.y) * step(gUv.y, 0.985);
        vec2 dUv = max(fwidth(gUv), vec2(0.00045));

        // 8x8 division grid lines
        vec2 cell = gUv * 8.0;
        vec2 distGrid = abs(fract(cell + 0.5) - 0.5) / (8.0 * dUv);
        float lineX = 1.0 - smoothstep(0.45, 1.35, distGrid.x);
        float lineY = 1.0 - smoothstep(0.45, 1.35, distGrid.y);
        // Hide outer border lines (0 and 8)
        float innerX = step(0.06, gUv.x) * step(gUv.x, 0.94);
        float innerY = step(0.06, gUv.y) * step(gUv.y, 0.94);
        float divLines = max(lineX * innerX, lineY * innerY);

        // Center crosshair axes
        vec2 distCenter = abs(gUv - 0.5) / dUv;
        float axisX = 1.0 - smoothstep(0.65, 1.65, distCenter.x);
        float axisY = 1.0 - smoothstep(0.65, 1.65, distCenter.y);
        majorMask = max(axisX, axisY);

        // Fine 0.2-division sub-ticks along both center axes (40 subdivisions total)
        vec2 subCell = gUv * 40.0;
        vec2 distSub = abs(fract(subCell + 0.5) - 0.5) / (40.0 * dUv);
        float tickOnH = (1.0 - smoothstep(0.42, 1.25, distSub.x)) * (1.0 - smoothstep(0.0085, 0.0115, abs(gUv.y - 0.5)));
        float tickOnV = (1.0 - smoothstep(0.42, 1.25, distSub.y)) * (1.0 - smoothstep(0.0085, 0.0115, abs(gUv.x - 0.5)));
        float ticks = max(tickOnH, tickOnV);

        gridMask = clamp(max(divLines * 0.72, max(majorMask, ticks * 0.88)) * inside, 0.0, 1.0);
    }

    // Sample live CRT phosphor right behind the etched groove for subtle edge-lit internal glass scatter
    vec2 crtUv = vec2(uv.x, 1.0 - uv.y);
    vec3 crtCol = texture(crtTex, clamp(crtUv, 0.01, 0.99)).rgb;
    vec3 crtGlow = max(crtCol - crtBgColor.rgb * 1.15, vec3(0.0));
    // Wide neighbor check so phosphor just adjacent to an etched line also illuminates the groove
    vec3 crtNear = max(texture(crtTex, clamp(crtUv + vec2(0.008, 0.0), 0.01, 0.99)).rgb - crtBgColor.rgb * 1.15, vec3(0.0))
                 + max(texture(crtTex, clamp(crtUv - vec2(0.008, 0.0), 0.01, 0.99)).rgb - crtBgColor.rgb * 1.15, vec3(0.0))
                 + max(texture(crtTex, clamp(crtUv + vec2(0.0, 0.008), 0.01, 0.99)).rgb - crtBgColor.rgb * 1.15, vec3(0.0))
                 + max(texture(crtTex, clamp(crtUv - vec2(0.0, 0.008), 0.01, 0.99)).rgb - crtBgColor.rgb * 1.15, vec3(0.0));
    vec3 edgeLit = (crtGlow * 0.65 + crtNear * 0.16) * crtPower;

    // Subtle soda-lime glass green edge tint + dark etched grooves + perimeter hood shadow
    // Rendered with One / OneMinusSrcAlpha (premultiplied): BASE_COLOR.a attenuates background phosphor,
    // while PBR specular + ReflectionProbe reflections add cleanly at full physical brightness!
    float glassAlpha = clamp(0.02 + fresnel * 0.14 + hoodShadow * 0.34, 0.0, 0.55);
    float grooveAlpha = gridMask * mix(0.46, 0.64, clamp(ambientLevel, 0.0, 1.0));
    float finalAlpha = clamp(max(glassAlpha, grooveAlpha), 0.0, 0.82);

    // Subtle procedural micro-roughness variation so glass feels slightly aged, never sterile CGI
    float microNoise = fract(sin(dot(floor(uv * 320.0), vec2(12.9898, 78.233))) * 43758.5453);
    vec3 sodaLimeTint = vec3(0.012, 0.026, 0.020) * (0.35 + fresnel * 1.6);
    vec3 grooveColor = vec3(0.008, 0.014, 0.011);
    vec3 baseRgb = mix(sodaLimeTint, grooveColor, gridMask) * (1.0 - hoodShadow * 0.7);

    BASE_COLOR = vec4(baseRgb, finalAlpha);
    ROUGHNESS = mix(0.055 + microNoise * 0.025, 0.36, gridMask);
    METALNESS = 0.0;
    SPECULAR_AMOUNT = mix(0.88 * reflectionGain, 0.25, gridMask);

    // Etched graticule catches faint ambient edge light + live phosphor trace scatter
    vec3 ambientEtch = vec3(0.055, 0.082, 0.068) * (0.15 + 0.85 * ambientLevel) * (0.35 * gridMask + 0.65 * majorMask);
    vec3 phosphorEtch = edgeLit * (0.45 * gridMask + 0.30 * majorMask);
    // Subtle anti-reflective curved-glass rim glint scaled by room brightness
    vec3 rimGlint = vec3(0.04, 0.065, 0.055) * pow(1.0 - ndotv, 5.0) * (0.12 + 0.88 * ambientLevel);
    EMISSIVE_COLOR = ambientEtch + phosphorEtch + rimGlint;
}

void SPECULAR_LIGHT()
{
    // Suppress direct pinpoint PointLight/SpotLight mirror dots on the curved cover glass
    // (keeps proxy crtPhosphorLight and bezel bounce lights from reflecting as fake dots on-screen)
    // while preserving full IBL_PROBE / ReflectionProbe curved room & window reflections.
}
