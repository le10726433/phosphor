VARYING vec2 v_uv;
VARYING vec3 v_worldPos;
VARYING vec3 v_worldNrm;

void MAIN()
{
    v_uv = UV0;
    v_worldPos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
    v_worldNrm = normalize(mat3(MODEL_MATRIX) * NORMAL);
}
