VARYING vec2 v_uv;
VARYING vec3 v_worldPos;
VARYING vec3 v_worldNrm;

void MAIN()
{
    vec3 V = normalize(CAMERA_POSITION - v_worldPos);
    vec3 N = normalize(v_worldNrm);

    // Edge-softening facing factor so cone silhouette melts seamlessly into air (zero hard edges)
    float edgeSoft = pow(abs(dot(N, V)), 1.85);
    // Smooth fade from lamp mouth (v=0) down toward bench (v=1)
    float topFade = smoothstep(0.0, 0.09, v_uv.y);
    float lenFade = pow(clamp(1.0 - v_uv.y, 0.0, 1.0), 1.9);

    // Delicate organic dust density variation in the warm tungsten beam
    float dust = 0.88 + 0.12 * sin(v_worldPos.x * 0.07 + v_worldPos.y * 0.05)
                             * cos(v_worldPos.z * 0.08 - v_worldPos.y * 0.04);

    float alpha = edgeSoft * topFade * lenFade * dust * coneIntensity;
    FRAGCOLOR = vec4(coneColor.rgb, clamp(alpha, 0.0, 0.16));
}
