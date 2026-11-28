VARYING vec3 v_localPos;
VARYING vec3 v_localNrm;
VARYING vec3 v_worldPos;

void MAIN()
{
    v_localPos = VERTEX;
    v_localNrm = normalize(NORMAL);
    v_worldPos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
}
