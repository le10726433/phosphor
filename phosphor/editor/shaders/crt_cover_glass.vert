VARYING vec3 v_localPos;
VARYING vec2 v_uv;

void MAIN()
{
    v_localPos = VERTEX;
    v_uv = UV0;
}
