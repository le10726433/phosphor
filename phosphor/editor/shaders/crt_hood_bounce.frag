VARYING vec3 v_localPos;
VARYING vec3 v_localNrm;
VARYING vec3 v_worldPos;

void MAIN()
{
    vec2 tiledUv = UV0 * 1.5;
    vec3 albedo = texture(albedoMap, tiledUv).rgb;
    float rough = texture(roughnessMap, tiledUv).r;

    // Screen-space cotangent frame normal mapping so the CRT hood retains the exact ABS micro-grain of chassisPlasticMat
    vec3 mapN = texture(normalMap, tiledUv).xyz * 2.0 - 1.0;
    vec3 dp1 = dFdx(v_worldPos);
    vec3 dp2 = dFdy(v_worldPos);
    vec2 duv1 = dFdx(tiledUv);
    vec2 duv2 = dFdy(tiledUv);
    vec3 dp2perp = cross(dp2, NORMAL);
    vec3 dp1perp = cross(NORMAL, dp1);
    vec3 T = dp2perp * duv1.x + dp1perp * duv2.x;
    vec3 B = dp2perp * duv1.y + dp1perp * duv2.y;
    float invMax = inversesqrt(max(dot(T, T), dot(B, B)) + 1e-6);
    vec3 worldPerturbed = normalize(NORMAL + (T * mapN.x + B * mapN.y) * invMax * 0.26);
    NORMAL = worldPerturbed;

    // Subtle deep-pocket contact AO right where the hood walls meet the recessed curved CRT glass
    float pocketAo = mix(0.68, 1.0, smoothstep(0.0, 5.5, v_localPos.z));

    BASE_COLOR = vec4(albedo * pocketAo, 1.0);
    ROUGHNESS = clamp(rough * 0.90, 0.45, 0.96);
    METALNESS = 0.0;
    SPECULAR_AMOUNT = 0.22;

    // Physically grounded 13-tap area-light irradiance sampled directly from the live CRT phosphor texture.
    // CRT active glass spans [-74, +74] in X and [-69, +69] in Y at z = 2.2 in crtBay local space.
    vec3 nrm = normalize(v_localNrm);
    vec2 crtHalf = vec2(74.0, 69.0);
    vec2 nearestXY = clamp(v_localPos.xy, -crtHalf * 0.94, crtHalf * 0.94);
    vec3 toCenter = vec3(nearestXY - v_localPos.xy, 2.2 - v_localPos.z);
    float distSq = dot(toCenter, toCenter) + 290.0;
    vec3 dir = normalize(toCenter);

    // Only inner bevel faces angled toward the glowing CRT tube receive area-light bounce
    float facing = max(0.0, dot(nrm, dir));
    // Restrict strictly to the inner bevel wall (not the outer exterior shell)
    float innerMask = smoothstep(89.0, 73.0, max(abs(v_localPos.x), abs(v_localPos.y) * (74.0 / 69.0)));

    vec2 baseUv = clamp(nearestXY / (2.0 * crtHalf) + 0.5, 0.03, 0.97);
    // Flip V to match QtQuick Texture sourceItem orientation
    baseUv.y = 1.0 - baseUv.y;

    // 13-tap dual-ring soft-kernel area integration across the live CRT screen
    vec2 off1 = vec2(0.075, 0.075);
    vec2 off2 = vec2(0.165, 0.165);
    vec3 crtSum = vec3(0.0);
    crtSum += texture(crtTex, clamp(baseUv, 0.02, 0.98)).rgb * 0.18;
    crtSum += texture(crtTex, clamp(baseUv + vec2( off1.x,  0.0   ), 0.02, 0.98)).rgb * 0.09;
    crtSum += texture(crtTex, clamp(baseUv + vec2(-off1.x,  0.0   ), 0.02, 0.98)).rgb * 0.09;
    crtSum += texture(crtTex, clamp(baseUv + vec2( 0.0,     off1.y), 0.02, 0.98)).rgb * 0.09;
    crtSum += texture(crtTex, clamp(baseUv + vec2( 0.0,    -off1.y), 0.02, 0.98)).rgb * 0.09;
    crtSum += texture(crtTex, clamp(baseUv + vec2( off1.x,  off1.y), 0.02, 0.98)).rgb * 0.065;
    crtSum += texture(crtTex, clamp(baseUv + vec2(-off1.x,  off1.y), 0.02, 0.98)).rgb * 0.065;
    crtSum += texture(crtTex, clamp(baseUv + vec2( off1.x, -off1.y), 0.02, 0.98)).rgb * 0.065;
    crtSum += texture(crtTex, clamp(baseUv + vec2(-off1.x, -off1.y), 0.02, 0.98)).rgb * 0.065;
    crtSum += texture(crtTex, clamp(baseUv + vec2( off2.x,  0.0   ), 0.02, 0.98)).rgb * 0.05;
    crtSum += texture(crtTex, clamp(baseUv + vec2(-off2.x,  0.0   ), 0.02, 0.98)).rgb * 0.05;
    crtSum += texture(crtTex, clamp(baseUv + vec2( 0.0,     off2.y), 0.02, 0.98)).rgb * 0.05;
    crtSum += texture(crtTex, clamp(baseUv + vec2( 0.0,    -off2.y), 0.02, 0.98)).rgb * 0.05;

    // Subtract the unlit CRT glass backing color so only actual luminous phosphor trace casts bounce
    vec3 traceLight = max(crtSum - crtBgColor.rgb * 1.15, vec3(0.0));
    float atten = 540.0 / distSq;
    vec3 rawBounce = traceLight * albedo * facing * innerMask * atten * bounceStrength * crtPower;
    // Soft-knee compress below glowHDRMinimumValue (1.35) so hood bounce never triggers HDR bloom flashing
    vec3 bounce = rawBounce / (vec3(1.0) + rawBounce * 0.82);

    EMISSIVE_COLOR = bounce;
}
