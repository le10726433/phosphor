// Cross-platform High-Speed Compute BVH Path Tracer for Phosphor C1-170 3D Lab Bench
// Compiles natively to Metal (macOS) and Direct3D 12 / Vulkan (Windows) via wgpu.

const PI: f32 = 3.14159265359;
const INV_PI: f32 = 0.31830988618;
const INTERNAL_FLAG: u32 = 0x80000000u;

const MAT_FLAG_CRT_SCREEN: u32 = 1u;
const MAT_FLAG_CRT_GLASS: u32 = 2u;
const MAT_FLAG_LAMP_BULB: u32 = 4u;
const MAT_FLAG_FLUOR_PANEL: u32 = 8u;
const MAT_FLAG_LAMP_LINER: u32 = 16u;
const MAT_FLAG_ALPHA_CUTOUT: u32 = 32u;
const MAT_FLAG_UV_CLAMP: u32 = 64u;
const MAT_FLAG_LED_GREEN: u32 = 128u;
const MAT_FLAG_LED_AMBER: u32 = 256u;

struct BvhNode {
    bmin: vec3<f32>,
    left_or_first: u32,
    bmax: vec3<f32>,
    count_or_right: u32,
};

struct GpuTri {
    v0: vec3<f32>,
    mat_id: u32,
    e1: vec3<f32>,
    uv0x: f32,
    e2: vec3<f32>,
    uv0y: f32,
    n0: vec3<f32>,
    uv1x: f32,
    n1: vec3<f32>,
    uv1y: f32,
    n2: vec3<f32>,
    uv2x: f32,
    uv2y: f32,
    pad0: f32,
    pad1: f32,
    pad2: f32,
};

struct GpuMat {
    base_color: vec3<f32>,
    roughness: f32,
    emissive: vec3<f32>,
    metalness: f32,
    clearcoat: f32,
    clearcoat_rough: f32,
    ior: f32,
    tex_id: u32,
    uv_scale_u: f32,
    uv_scale_v: f32,
    flags: u32,
    normal_tex_id: u32,
};

struct Uniforms {
    cam_pos: vec3<f32>,
    fov_tan: f32,
    cam_right: vec3<f32>,
    aspect: f32,
    cam_up: vec3<f32>,
    aperture: f32,
    cam_fwd: vec3<f32>,
    focus_dist: f32,

    lamp_pos: vec3<f32>,
    lamp_radius: f32,
    crt_origin: vec3<f32>,
    crt_power: f32,
    crt_u: vec3<f32>,
    lamp_on: f32,
    crt_v: vec3<f32>,
    fluor_on: f32,
    crt_n: vec3<f32>,
    show_grid: f32,

    width: u32,
    height: u32,
    frame_idx: u32,
    spp: u32,
    exposure: f32,
    max_bounces: u32,
    ambient_gain: f32,
    ema_floor: f32,

    lamp_dir: vec3<f32>,
    pad1: f32,
};

@group(0) @binding(0) var<uniform> u: Uniforms;
@group(0) @binding(1) var<storage, read> nodes: array<BvhNode>;
@group(0) @binding(2) var<storage, read> tris: array<GpuTri>;
@group(0) @binding(3) var<storage, read> mats: array<GpuMat>;
@group(0) @binding(4) var tex_array: texture_2d_array<f32>;
@group(0) @binding(5) var tex_sampler: sampler;
@group(0) @binding(6) var crt_tex: texture_2d<f32>;
@group(0) @binding(7) var<storage, read_write> accum: array<vec4<f32>>;
@group(0) @binding(8) var<storage, read_write> gbuf: array<vec4<f32>>;
@group(0) @binding(9) var<storage, read_write> albedo_buf: array<vec4<f32>>;
@group(0) @binding(10) var crt_irr_tex: texture_2d<f32>;

// --- PCG Hash RNG ---
fn pcg_next(state: ptr<function, u32>) -> u32 {
    let old = *state;
    *state = old * 747796405u + 2891336453u;
    let word = ((old >> ((old >> 28u) + 4u)) ^ old) * 277803737u;
    return (word >> 22u) ^ word;
}

fn rand01(state: ptr<function, u32>) -> f32 {
    return f32(pcg_next(state)) * (1.0 / 4294967296.0);
}

fn srgb_to_linear(c: vec3<f32>) -> vec3<f32> {
    return pow(max(c, vec3<f32>(0.0)), vec3<f32>(2.2));
}

fn make_onb(n: vec3<f32>) -> mat3x3<f32> {
    let up = select(vec3<f32>(1.0, 0.0, 0.0), vec3<f32>(0.0, 1.0, 0.0), abs(n.y) < 0.92);
    let t = normalize(cross(up, n));
    let b = cross(n, t);
    return mat3x3<f32>(t, b, n);
}

fn sample_cosine_hemi(n: vec3<f32>, rng: ptr<function, u32>) -> vec3<f32> {
    let r1 = rand01(rng);
    let r2 = rand01(rng);
    let phi = 2.0 * PI * r1;
    let sr = sqrt(r2);
    let local = vec3<f32>(cos(phi) * sr, sin(phi) * sr, sqrt(max(0.0, 1.0 - r2)));
    return normalize(make_onb(n) * local);
}

fn sample_ggx_half(n: vec3<f32>, alpha: f32, rng: ptr<function, u32>) -> vec3<f32> {
    let r1 = rand01(rng);
    let r2 = rand01(rng);
    let a2 = alpha * alpha;
    let cos_theta = sqrt(max(0.0, (1.0 - r2) / max(1e-6, 1.0 + (a2 - 1.0) * r2)));
    let sin_theta = sqrt(max(0.0, 1.0 - cos_theta * cos_theta));
    let phi = 2.0 * PI * r1;
    let local = vec3<f32>(cos(phi) * sin_theta, sin(phi) * sin_theta, cos_theta);
    return normalize(make_onb(n) * local);
}

fn ggx_d(ndoth: f32, alpha: f32) -> f32 {
    let a2 = alpha * alpha;
    let denom = ndoth * ndoth * (a2 - 1.0) + 1.0;
    return a2 / max(1e-6, PI * denom * denom);
}

fn ggx_g1(ndotx: f32, alpha: f32) -> f32 {
    let k = (alpha + 1.0) * (alpha + 1.0) * 0.125;
    return ndotx / max(1e-6, ndotx * (1.0 - k) + k);
}

fn schlick_fresnel(cos_t: f32, f0: vec3<f32>) -> vec3<f32> {
    return f0 + (vec3<f32>(1.0) - f0) * pow(clamp(1.0 - cos_t, 0.0, 1.0), 5.0);
}

fn hit_aabb(bmin: vec3<f32>, bmax: vec3<f32>, ro: vec3<f32>, inv_d: vec3<f32>, tmax: f32) -> f32 {
    let t0 = (bmin - ro) * inv_d;
    let t1 = (bmax - ro) * inv_d;
    let tmin3 = min(t0, t1);
    let tmax3 = max(t0, t1);
    let tnear = max(max(tmin3.x, tmin3.y), max(tmin3.z, 0.0));
    let tfar = min(min(tmax3.x, tmax3.y), min(tmax3.z, tmax));
    return select(1e30, tnear, tfar >= tnear);
}

struct HitInfo {
    t: f32,
    tri_idx: u32,
    bu: f32,
    bv: f32,
};

fn sample_tex_layer(tex_id: u32, uv: vec2<f32>, uv_scale: vec2<f32>, clamp_uv: bool) -> vec4<f32> {
    var tuv = uv * uv_scale;
    if (clamp_uv) {
        tuv = clamp(tuv, vec2<f32>(0.001), vec2<f32>(0.999));
    } else {
        tuv = fract(tuv);
    }
    return textureSampleLevel(tex_array, tex_sampler, tuv, i32(tex_id - 1u), 0.0);
}

fn trace_ray(ro: vec3<f32>, rd: vec3<f32>, t_max_init: f32) -> HitInfo {
    var hit: HitInfo;
    hit.t = t_max_init;
    hit.tri_idx = 0xffffffffu;
    hit.bu = 0.0;
    hit.bv = 0.0;

    let inv_d = 1.0 / select(rd, vec3<f32>(1e-7), abs(rd) < vec3<f32>(1e-7));
    var stack: array<u32, 34>;
    var sp: i32 = 1;
    stack[0] = 0u;

    while (sp > 0) {
        sp -= 1;
        let node = nodes[stack[sp]];
        if (hit_aabb(node.bmin, node.bmax, ro, inv_d, hit.t) >= hit.t) {
            continue;
        }
        if ((node.count_or_right & INTERNAL_FLAG) == 0u) {
            let first = node.left_or_first;
            let cnt = node.count_or_right;
            for (var i = 0u; i < cnt; i += 1u) {
                let tid = first + i;
                let tri = tris[tid];
                let h = cross(rd, tri.e2);
                let a = dot(tri.e1, h);
                if (abs(a) < 1e-7) { continue; }
                let f = 1.0 / a;
                let s = ro - tri.v0;
                let bu = f * dot(s, h);
                if (bu < 0.0 || bu > 1.0) { continue; }
                let q = cross(s, tri.e1);
                let bv = f * dot(rd, q);
                if (bv < 0.0 || bu + bv > 1.0) { continue; }
                let t = f * dot(tri.e2, q);
                if (t > 0.02 && t < hit.t) {
                    let mat = mats[tri.mat_id];
                    if ((mat.flags & MAT_FLAG_ALPHA_CUTOUT) != 0u && mat.tex_id > 0u) {
                        let bw = 1.0 - bu - bv;
                        let uv = vec2<f32>(tri.uv0x, tri.uv0y) * bw
                               + vec2<f32>(tri.uv1x, tri.uv1y) * bu
                               + vec2<f32>(tri.uv2x, tri.uv2y) * bv;
                        let alpha = sample_tex_layer(mat.tex_id, uv, vec2<f32>(mat.uv_scale_u, mat.uv_scale_v), true).a;
                        if (alpha < 0.35) { continue; }
                    }
                    hit.t = t;
                    hit.tri_idx = tid;
                    hit.bu = bu;
                    hit.bv = bv;
                }
            }
        } else {
            let left = node.left_or_first;
            let right = node.count_or_right & 0x7fffffffu;
            let dl = hit_aabb(nodes[left].bmin, nodes[left].bmax, ro, inv_d, hit.t);
            let dr = hit_aabb(nodes[right].bmin, nodes[right].bmax, ro, inv_d, hit.t);
            if (dl < dr) {
                if (dr < hit.t && sp < 33) { stack[sp] = right; sp += 1; }
                if (dl < hit.t && sp < 33) { stack[sp] = left; sp += 1; }
            } else {
                if (dl < hit.t && sp < 33) { stack[sp] = left; sp += 1; }
                if (dr < hit.t && sp < 33) { stack[sp] = right; sp += 1; }
            }
        }
    }
    return hit;
}

// Fast shadow ray (early-exit, ignores transparent CRT glass and light emitter shells)
fn is_occluded(ro: vec3<f32>, rd: vec3<f32>, max_dist: f32) -> bool {
    let inv_d = 1.0 / select(rd, vec3<f32>(1e-7), abs(rd) < vec3<f32>(1e-7));
    var stack: array<u32, 34>;
    var sp: i32 = 1;
    stack[0] = 0u;
    while (sp > 0) {
        sp -= 1;
        let node = nodes[stack[sp]];
        if (hit_aabb(node.bmin, node.bmax, ro, inv_d, max_dist) >= max_dist) {
            continue;
        }
        if ((node.count_or_right & INTERNAL_FLAG) == 0u) {
            let first = node.left_or_first;
            let cnt = node.count_or_right;
            for (var i = 0u; i < cnt; i += 1u) {
                let tri = tris[first + i];
                let mat = mats[tri.mat_id];
                if ((mat.flags & (MAT_FLAG_CRT_GLASS | MAT_FLAG_LAMP_BULB | MAT_FLAG_FLUOR_PANEL | MAT_FLAG_CRT_SCREEN | MAT_FLAG_LAMP_LINER)) != 0u) {
                    continue;
                }
                let h = cross(rd, tri.e2);
                let a = dot(tri.e1, h);
                if (abs(a) < 1e-7) { continue; }
                let f = 1.0 / a;
                let s = ro - tri.v0;
                let bu = f * dot(s, h);
                if (bu < 0.0 || bu > 1.0) { continue; }
                let q = cross(s, tri.e1);
                let bv = f * dot(rd, q);
                if (bv < 0.0 || bu + bv > 1.0) { continue; }
                let t = f * dot(tri.e2, q);
                if (t > 0.08 && t < max_dist) {
                    return true;
                }
            }
        } else {
            let left = node.left_or_first;
            let right = node.count_or_right & 0x7fffffffu;
            let dl = hit_aabb(nodes[left].bmin, nodes[left].bmax, ro, inv_d, max_dist);
            let dr = hit_aabb(nodes[right].bmin, nodes[right].bmax, ro, inv_d, max_dist);
            if (dl < dr) {
                if (dr < max_dist && sp < 33) { stack[sp] = right; sp += 1; }
                if (dl < max_dist && sp < 33) { stack[sp] = left; sp += 1; }
            } else {
                if (dl < max_dist && sp < 33) { stack[sp] = left; sp += 1; }
                if (dr < max_dist && sp < 33) { stack[sp] = right; sp += 1; }
            }
        }
    }
    return false;
}

fn eval_environment(rd: vec3<f32>) -> vec3<f32> {
    let up = clamp(rd.y * 0.5 + 0.5, 0.0, 1.0);
    if (u.fluor_on > 0.5) {
        let base = mix(vec3<f32>(0.10, 0.09, 0.08), vec3<f32>(0.34, 0.38, 0.42), up);
        let softbox = pow(max(0.0, dot(rd, normalize(vec3<f32>(-0.35, 0.72, 0.55)))), 14.0) * 1.4
                    + pow(max(0.0, dot(rd, normalize(vec3<f32>( 0.42, 0.68, 0.55)))), 14.0) * 1.1;
        return (base + vec3<f32>(0.92, 0.96, 1.0) * softbox) * u.ambient_gain;
    } else if (u.lamp_on > 0.5) {
        let cool_night = mix(vec3<f32>(0.010, 0.014, 0.022), vec3<f32>(0.028, 0.044, 0.074), up);
        let window_hint = pow(max(0.0, dot(rd, normalize(vec3<f32>(0.65, 0.42, 0.58)))), 16.0) * 0.16;
        return (cool_night + vec3<f32>(0.24, 0.40, 0.68) * window_hint) * u.ambient_gain;
    }
    return vec3<f32>(0.001, 0.0014, 0.0018);
}

fn eval_bsdf(n: vec3<f32>, v: vec3<f32>, l: vec3<f32>, albedo: vec3<f32>, rough: f32, metal: f32, cc: f32, cc_rough: f32) -> vec3<f32> {
    let ndotl = max(0.0, dot(n, l));
    let ndotv = max(1e-4, dot(n, v));
    if (ndotl <= 0.0) { return vec3<f32>(0.0); }
    let h = normalize(v + l);
    let ndoth = max(0.0, dot(n, h));
    let vdoth = max(0.0, dot(v, h));

    let f0 = mix(vec3<f32>(0.04), albedo, metal);
    let F = schlick_fresnel(vdoth, f0);
    let a = max(0.05, rough * rough);
    let D = ggx_d(ndoth, a);
    let G = ggx_g1(ndotl, a) * ggx_g1(ndotv, a);
    let spec = (D * G * F) / max(1e-5, 4.0 * ndotl * ndotv);
    let kd = (vec3<f32>(1.0) - F) * (1.0 - metal);
    var brdf = kd * albedo * INV_PI + spec;

    if (cc > 0.01) {
        let ca = max(0.04, cc_rough * cc_rough);
        let cF = schlick_fresnel(vdoth, vec3<f32>(0.04)).x;
        let cD = ggx_d(ndoth, ca);
        let cG = ggx_g1(ndotl, ca) * ggx_g1(ndotv, ca);
        let cspec = (cD * cG * cF) / max(1e-5, 4.0 * ndotl * ndotv);
        brdf = brdf * (1.0 - cc * cF) + vec3<f32>(cc * cspec);
    }
    return brdf * ndotl;
}

fn crt_graticule_mask(uv: vec2<f32>) -> vec2<f32> {
    if (u.show_grid < 0.5) { return vec2<f32>(0.0); }
    let inside = step(0.015, uv.x) * step(uv.x, 0.985) * step(0.015, uv.y) * step(uv.y, 0.985);
    let cell = uv * 8.0;
    let dgrid = abs(fract(cell + 0.5) - 0.5);
    let inner_x = step(0.06, uv.x) * step(uv.x, 0.94);
    let inner_y = step(0.06, uv.y) * step(uv.y, 0.94);
    let lx = 1.0 - smoothstep(0.008, 0.024, dgrid.x);
    let ly = 1.0 - smoothstep(0.008, 0.024, dgrid.y);
    let divs = max(lx * inner_x, ly * inner_y);

    let dc = abs(uv - 0.5);
    let ax = 1.0 - smoothstep(0.0014, 0.0036, dc.x);
    let ay = 1.0 - smoothstep(0.0014, 0.0036, dc.y);
    let major = max(ax, ay);

    let sub = abs(fract(uv * 40.0 + 0.5) - 0.5);
    let th = (1.0 - smoothstep(0.035, 0.09, sub.x)) * (1.0 - smoothstep(0.008, 0.011, dc.y));
    let tv = (1.0 - smoothstep(0.035, 0.09, sub.y)) * (1.0 - smoothstep(0.008, 0.011, dc.x));
    let ticks = max(th, tv);

    return vec2<f32>(clamp(max(divs * 0.68, max(major, ticks * 0.85)) * inside, 0.0, 1.0), major * inside);
}

fn sample_crt_crisp(uv: vec2<f32>) -> vec3<f32> {
    let cuv = clamp(vec2<f32>(uv.x, 1.0 - uv.y), vec2<f32>(0.005), vec2<f32>(0.995));
    return srgb_to_linear(textureSampleLevel(crt_tex, tex_sampler, cuv, 0.0).rgb);
}

fn sample_crt_irradiance(uv: vec2<f32>) -> vec3<f32> {
    let cuv = clamp(vec2<f32>(uv.x, 1.0 - uv.y), vec2<f32>(0.02), vec2<f32>(0.98));
    return srgb_to_linear(textureSampleLevel(crt_irr_tex, tex_sampler, cuv, 0.0).rgb);
}

// Low-variance Next Event Estimation:
// - Lamp NEE samples the open circular mouth disk of the spun-metal shade (~95% visible, 4x cleaner!)
// - CRT NEE samples the pre-blurred 16x16 live CRT irradiance texture (eliminates 95% of green speckle!)
// - Tiny indicator LEDs & PWR button evaluated analytically (zero fireflies, zero BVH cost!)
fn sample_direct_lights(p: vec3<f32>, n: vec3<f32>, v: vec3<f32>,
                        albedo: vec3<f32>, rough: f32, metal: f32, cc: f32, cc_rough: f32,
                        bounce: u32, rng: ptr<function, u32>) -> vec3<f32> {
    var direct = vec3<f32>(0.0);
    let orig = p + n * 0.14;

    // On deeper indirect bounces (bounce >= 1), probabilistically evaluate 1 dominant light to double speed!
    let do_lamp = (u.lamp_on > 0.5) && (bounce == 0u || u.fluor_on < 0.5);
    let do_fluor = (u.fluor_on > 0.5);
    let do_crt = (u.crt_power > 0.01) && (bounce == 0u || (u.lamp_on < 0.5 && u.fluor_on < 0.5));

    // 1. Open-mouth Tungsten Architect Lamp Aperture (high visibility -> silky smooth warm pool!)
    if (do_lamp) {
        let onb_l = make_onb(u.lamp_dir);
        let r = sqrt(rand01(rng)) * (u.lamp_radius * 0.72);
        let phi = 2.0 * PI * rand01(rng);
        let lp = u.lamp_pos + onb_l[0] * (r * cos(phi)) + onb_l[1] * (r * sin(phi));
        let to_l = lp - orig;
        let dist2 = dot(to_l, to_l);
        let dist = sqrt(dist2);
        let ldir = to_l / max(dist, 1e-4);
        let cos_cone = dot(-ldir, u.lamp_dir);
        // Smooth physical shade-rim cutoff matching the spun-metal cone opening
        let cone_mask = smoothstep(0.16, 0.56, cos_cone);
        if (dot(n, ldir) > 0.0 && cone_mask > 0.001) {
            if (!is_occluded(orig, ldir, dist - 2.0)) {
                let lamp_flux = vec3<f32>(1.0, 0.56, 0.19) * select(138000.0, 26000.0, u.fluor_on > 0.5);
                let radiance = (lamp_flux * cone_mask) / (dist2 + 2200.0);
                direct += eval_bsdf(n, v, ldir, albedo, rough, metal, cc, cc_rough) * radiance;
            }
        }
    }

    // 2. Overhead Fluorescent Shop Ceiling Softboxes (2 rectangular area lights)
    if (do_fluor) {
        let pick = select(0.0, 1.0, rand01(rng) > 0.5);
        let cx = mix(-140.0, 160.0, pick);
        let rx = (rand01(rng) - 0.5) * 240.0;
        let rz = (rand01(rng) - 0.5) * 65.0;
        let lp = vec3<f32>(cx + rx, 338.0, 90.0 + rz);
        let to_l = lp - orig;
        let dist2 = dot(to_l, to_l);
        let dist = sqrt(dist2);
        let ldir = to_l / max(dist, 1e-4);
        let cos_light = max(0.0, -ldir.y);
        if (dot(n, ldir) > 0.0 && cos_light > 0.01) {
            if (!is_occluded(orig, ldir, dist - 2.0)) {
                let panel_rad = vec3<f32>(0.93, 0.97, 1.04) * 5.2;
                let geom = (2.0 * 240.0 * 65.0 * cos_light) / max(dist2, 220.0);
                direct += eval_bsdf(n, v, ldir, albedo, rough, metal, cc, cc_rough) * panel_rad * geom;
            }
        }
    }

    // 3. Live Curved CRT Phosphor Area Light (samples smooth 16x16 irradiance map -> zero green speckle!)
    if (do_crt) {
        let su = rand01(rng);
        let sv = rand01(rng);
        let lp = u.crt_origin + u.crt_u * (su * 2.0 - 1.0) + u.crt_v * (sv * 2.0 - 1.0);
        let to_l = lp - orig;
        let dist2 = dot(to_l, to_l);
        let dist = sqrt(dist2);
        let ldir = to_l / max(dist, 1e-4);
        let cos_light = dot(-ldir, u.crt_n);
        if (dot(n, ldir) > 0.0 && cos_light > 0.01) {
            if (!is_occluded(orig, ldir, dist - 2.0)) {
                let crt_lin = sample_crt_irradiance(vec2<f32>(su, sv));
                let trace_rad = max(crt_lin - vec3<f32>(0.010, 0.022, 0.014), vec3<f32>(0.0));
                let area = 148.0 * 138.0;
                let gi_gain = select(select(3.8, 5.8, u.lamp_on < 0.5), 1.4, u.fluor_on > 0.5);
                let geom = (area * cos_light) / max(dist2, 200.0);
                direct += eval_bsdf(n, v, ldir, albedo, rough, metal, cc, cc_rough) * trace_rad * geom * gi_gain * u.crt_power;
            }
        }
    }

    // 4. Analytic zero-noise local illumination from tiny amber PWR jewel & active green LEDs (primary bounce only)
    if (bounce == 0u && u.crt_power > 0.1 && u.fluor_on < 0.5) {
        // Amber PWR button at world ~(192, -75, 16)
        let to_pwr = vec3<f32>(192.0, -75.0, 16.0) - orig;
        let d2_pwr = dot(to_pwr, to_pwr);
        if (d2_pwr < 9000.0) {
            let ldir = to_pwr * inverseSqrt(max(d2_pwr, 1.0));
            let att = 320.0 / (d2_pwr + 48.0);
            direct += eval_bsdf(n, v, ldir, albedo, rough, metal, cc, cc_rough) * vec3<f32>(1.0, 0.62, 0.14) * att;
        }
        // Active green BEAM / GRID LEDs at world ~(107, 90, 5)
        let to_led = vec3<f32>(107.0, 90.0, 5.0) - orig;
        let d2_led = dot(to_led, to_led);
        if (d2_led < 6500.0) {
            let ldir = to_led * inverseSqrt(max(d2_led, 1.0));
            let att = 180.0 / (d2_led + 36.0);
            direct += eval_bsdf(n, v, ldir, albedo, rough, metal, cc, cc_rough) * vec3<f32>(0.22, 1.0, 0.40) * att;
        }
    }

    return direct;
}

@compute @workgroup_size(8, 8)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
    if (gid.x >= u.width || gid.y >= u.height) {
        return;
    }

    let pix_idx = gid.y * u.width + gid.x;
    var rng = (gid.x * 1973u) ^ (gid.y * 9277u) ^ ((u.frame_idx + 1u) * 26699u) | 1u;

    var col_accum = vec3<f32>(0.0);
    var g_norm_accum = vec3<f32>(0.0);
    var g_depth_accum = 0.0;
    var g_albedo_accum = vec3<f32>(0.0);
    var g_skip_denoise = 0.0;
    let num_samples = max(1u, u.spp);

    for (var s = 0u; s < num_samples; s += 1u) {
        let jx = rand01(&rng);
        let jy = rand01(&rng);
        let ndc_x = ((f32(gid.x) + jx) / f32(u.width)) * 2.0 - 1.0;
        let ndc_y = 1.0 - ((f32(gid.y) + jy) / f32(u.height)) * 2.0;

        var rd = normalize(u.cam_fwd + u.cam_right * (ndc_x * u.aspect * u.fov_tan) + u.cam_up * (ndc_y * u.fov_tan));
        var ro = u.cam_pos;

        if (u.aperture > 0.001) {
            let focus_pt = ro + rd * u.focus_dist;
            let a_ang = 2.0 * PI * rand01(&rng);
            let a_rad = sqrt(rand01(&rng)) * u.aperture;
            let lens_off = u.cam_right * (cos(a_ang) * a_rad) + u.cam_up * (sin(a_ang) * a_rad);
            ro += lens_off;
            rd = normalize(focus_pt - ro);
        }

        var throughput = vec3<f32>(1.0);
        var radiance = vec3<f32>(0.0);
        var last_specular = true;
        var recorded_primary = false;

        for (var bounce = 0u; bounce < u.max_bounces; bounce += 1u) {
            let hit = trace_ray(ro, rd, 1e20);
            if (hit.tri_idx == 0xffffffffu) {
                radiance += throughput * eval_environment(rd);
                if (!recorded_primary) {
                    g_norm_accum += -rd;
                    g_depth_accum += 2500.0;
                    g_albedo_accum += vec3<f32>(1.0);
                    g_skip_denoise += 1.0;
                    recorded_primary = true;
                }
                break;
            }

            let tri = tris[hit.tri_idx];
            let mat = mats[tri.mat_id];
            let bw = 1.0 - hit.bu - hit.bv;
            let p = ro + rd * hit.t;
            var n_geom = normalize(tri.n0 * bw + tri.n1 * hit.bu + tri.n2 * hit.bv);
            if (dot(rd, n_geom) > 0.0) { n_geom = -n_geom; }

            let uv = vec2<f32>(tri.uv0x, tri.uv0y) * bw
                   + vec2<f32>(tri.uv1x, tri.uv1y) * hit.bu
                   + vec2<f32>(tri.uv2x, tri.uv2y) * hit.bv;

            // --- SPECIAL CASE 1: Deterministic Zero-Speckle Curved Dielectric CRT Cover Glass ---
            if ((mat.flags & MAT_FLAG_CRT_GLASS) != 0u) {
                if (!recorded_primary) {
                    g_norm_accum += n_geom;
                    g_depth_accum += hit.t;
                    g_albedo_accum += vec3<f32>(1.0);
                    g_skip_denoise += 1.0;
                    recorded_primary = true;
                }
                let v_dir = -rd;
                let ndotv = clamp(dot(n_geom, v_dir), 0.0, 1.0);
                let fresnel = 0.043 + 0.957 * pow(1.0 - ndotv, 4.4);
                let grat = crt_graticule_mask(uv);
                let gmask = grat.x;
                let major = grat.y;

                // Smooth hood-edge perimeter vignette
                let ed = min(uv, 1.0 - uv);
                let hood_vig = 1.0 - smoothstep(0.0, 0.055, ed.x) * smoothstep(0.0, 0.055, ed.y);

                // Deterministic transmitted CRT phosphor directly behind the curved glass (ZERO speckle!)
                let crt_lin = sample_crt_crisp(uv);
                let base_glass = vec3<f32>(0.008, 0.020, 0.012);
                let trace_only = max(crt_lin - base_glass, vec3<f32>(0.0));
                let crt_out = base_glass + trace_only * 2.45 * u.crt_power;

                let trans_tint = mix(vec3<f32>(0.96, 0.99, 0.97), vec3<f32>(0.26, 0.32, 0.28), gmask * 0.72)
                               * (1.0 - hood_vig * 0.34);
                let amb_etch = vec3<f32>(0.045, 0.068, 0.055) * select(0.18, 0.85, u.fluor_on > 0.5);
                let etch_light = amb_etch * (0.4 * gmask + 0.6 * major) + trace_only * 0.45 * gmask * u.crt_power;

                radiance += throughput * ((1.0 - fresnel) * crt_out * trans_tint + etch_light);

                // Continue tracing ONLY the Fresnel reflection weighted directly by `fresnel` (zero 1/p spike!)
                let rdir = reflect(rd, n_geom);
                throughput *= fresnel * vec3<f32>(0.90, 0.96, 0.93);
                if (max(throughput.r, max(throughput.g, throughput.b)) < 0.004) {
                    break;
                }
                ro = p + n_geom * 0.10;
                rd = rdir;
                last_specular = true;
                continue;
            }

            // --- SPECIAL CASE 2: Live Curved CRT Phosphor Emitter ---
            if ((mat.flags & MAT_FLAG_CRT_SCREEN) != 0u) {
                if (!recorded_primary) {
                    g_norm_accum += n_geom;
                    g_depth_accum += hit.t;
                    g_albedo_accum += vec3<f32>(1.0);
                    g_skip_denoise += 1.0;
                    recorded_primary = true;
                }
                let crt_lin = sample_crt_crisp(uv);
                let base_glass = vec3<f32>(0.008, 0.020, 0.012);
                let trace_only = max(crt_lin - base_glass, vec3<f32>(0.0));
                radiance += throughput * (base_glass + trace_only * 2.45 * u.crt_power);
                break;
            }

            // --- Standard PBR Surface Evaluation ---
            var albedo = mat.base_color;
            let clamp_uv = (mat.flags & MAT_FLAG_UV_CLAMP) != 0u;
            if (mat.tex_id > 0u) {
                let tcol = sample_tex_layer(mat.tex_id, uv, vec2<f32>(mat.uv_scale_u, mat.uv_scale_v), clamp_uv).rgb;
                albedo *= srgb_to_linear(tcol);
            }

            // IMPORTANT: Write UNPERTURBED smooth geometric normal into gbuf so A-Trous denoiser
            // smoothly filters oak wood, plaster wall & ABS chassis while stopping at real 3D edges!
            var n = n_geom;
            if (mat.normal_tex_id > 0u) {
                let nraw = sample_tex_layer(mat.normal_tex_id, uv, vec2<f32>(mat.uv_scale_u, mat.uv_scale_v), clamp_uv).rgb * 2.0 - 1.0;
                let onb = make_onb(n_geom);
                n = normalize(n_geom + (onb[0] * nraw.x + onb[1] * nraw.y) * 0.22);
            }

            var emit = mat.emissive;
            if ((mat.flags & MAT_FLAG_LAMP_BULB) != 0u) {
                emit = select(vec3<f32>(0.0), mat.emissive, u.lamp_on > 0.5);
                if (u.lamp_on < 0.5) { albedo = vec3<f32>(0.18, 0.17, 0.15); }
            } else if ((mat.flags & MAT_FLAG_LAMP_LINER) != 0u) {
                emit = select(vec3<f32>(0.0), mat.emissive, u.lamp_on > 0.5);
            } else if ((mat.flags & MAT_FLAG_FLUOR_PANEL) != 0u) {
                emit = select(vec3<f32>(0.0), mat.emissive, u.fluor_on > 0.5);
            }

            if (!recorded_primary) {
                g_norm_accum += n_geom;
                g_depth_accum += hit.t;
                g_albedo_accum += max(albedo, vec3<f32>(0.05));
                if (any(emit > vec3<f32>(0.5))) {
                    g_skip_denoise += 1.0;
                }
                recorded_primary = true;
            }

            let is_nee_light = (mat.flags & (MAT_FLAG_LAMP_BULB | MAT_FLAG_FLUOR_PANEL)) != 0u;
            if (any(emit > vec3<f32>(0.0))) {
                if (!is_nee_light || bounce == 0u || last_specular) {
                    var contrib = throughput * emit;
                    // Strictly bound indirect emissive bounce hits to prevent fireflies
                    if (bounce > 0u) {
                        let mc = max(contrib.r, max(contrib.g, contrib.b));
                        if (mc > 1.8) { contrib *= (1.8 / mc); }
                    }
                    radiance += contrib;
                }
                if (is_nee_light) { break; }
            }

            let v_dir = -rd;
            let rough = max(0.06, mat.roughness);
            let metal = mat.metalness;
            let cc = mat.clearcoat;
            let cc_rough = max(0.05, mat.clearcoat_rough);

            // Direct lighting via low-variance NEE
            radiance += throughput * sample_direct_lights(p, n, v_dir, albedo, rough, metal, cc, cc_rough, bounce, &rng);

            if (bounce + 1u >= u.max_bounces) {
                break;
            }

            // Indirect BSDF Sampling
            let ndotv = max(1e-4, dot(n, v_dir));
            let f0 = mix(vec3<f32>(0.04), albedo, metal);
            let f_spec = schlick_fresnel(ndotv, f0);
            let p_cc = clamp(cc * (0.04 + 0.96 * pow(1.0 - ndotv, 5.0)) * 2.0, 0.0, 0.50);
            let spec_luma = max(f_spec.r, max(f_spec.g, f_spec.b));
            let p_spec = (1.0 - p_cc) * clamp(mix(spec_luma, 1.0, metal), 0.05, 0.94);

            let xi = rand01(&rng);
            var next_dir: vec3<f32>;
            if (xi < p_cc) {
                let h = sample_ggx_half(n, cc_rough * cc_rough, &rng);
                next_dir = reflect(-v_dir, h);
                if (dot(next_dir, n_geom) <= 0.0) { break; }
                let vdoth = max(1e-4, dot(v_dir, h));
                let cF = schlick_fresnel(vdoth, vec3<f32>(0.04)).x;
                throughput *= min(vec3<f32>(cc * cF / max(1e-4, p_cc)), vec3<f32>(2.5));
                last_specular = cc_rough < 0.22;
            } else if (xi < p_cc + p_spec) {
                let a = rough * rough;
                let h = sample_ggx_half(n, a, &rng);
                next_dir = reflect(-v_dir, h);
                if (dot(next_dir, n_geom) <= 0.0) { break; }
                let ndotl = max(1e-4, dot(n, next_dir));
                let ndoth = max(1e-4, dot(n, h));
                let vdoth = max(1e-4, dot(v_dir, h));
                let F = schlick_fresnel(vdoth, f0);
                let G = ggx_g1(ndotl, a) * ggx_g1(ndotv, a);
                let weight = (F * G * vdoth) / max(1e-5, ndotv * ndoth * p_spec);
                throughput *= min(weight, vec3<f32>(3.0));
                last_specular = rough < 0.25;
            } else {
                next_dir = sample_cosine_hemi(n_geom, &rng);
                let p_diff = max(0.05, 1.0 - p_cc - p_spec);
                throughput *= (albedo * (1.0 - metal) * (vec3<f32>(1.0) - f_spec)) / p_diff;
                last_specular = false;
            }

            // Russian Roulette after bounce 1
            if (bounce >= 1u) {
                let rr = clamp(max(throughput.r, max(throughput.g, throughput.b)), 0.05, 0.92);
                if (rand01(&rng) > rr) { break; }
                throughput /= rr;
            }

            ro = p + n_geom * 0.12;
            rd = next_dir;
        }

        let max_c = max(radiance.r, max(radiance.g, radiance.b));
        if (max_c > 8.0) {
            radiance *= (8.0 / max_c);
        }
        col_accum += radiance;
    }

    let inv_s = 1.0 / f32(num_samples);
    let sample_avg = col_accum * inv_s;
    let avg_albedo = g_albedo_accum * inv_s;
    let avg_norm = normalize(g_norm_accum + vec3<f32>(0.0, 0.0, 1e-6));
    let avg_depth = g_depth_accum * inv_s;
    let skip_flag = select(1.0, -1.0, g_skip_denoise * inv_s > 0.5);

    var prev = vec3<f32>(0.0);
    var total_n = f32(num_samples);
    var blend_w = 1.0;
    if (u.frame_idx > 0u) {
        let old = accum[pix_idx];
        prev = old.rgb;
        total_n = old.a + f32(num_samples);
        blend_w = max(f32(num_samples) / total_n, u.ema_floor);
        let old_alb = albedo_buf[pix_idx].rgb;
        albedo_buf[pix_idx] = vec4<f32>(mix(old_alb, avg_albedo, blend_w), skip_flag);
    } else {
        albedo_buf[pix_idx] = vec4<f32>(avg_albedo, skip_flag);
    }
    let blended = mix(prev, sample_avg, blend_w);
    accum[pix_idx] = vec4<f32>(blended, total_n);
    gbuf[pix_idx] = vec4<f32>(avg_norm, avg_depth * skip_flag);
}
