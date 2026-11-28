// 4-Pass Demodulated Edge-Preserving A-Trous Wavelet Denoiser + Firefly Clamp + ACES Filmic
// Uses unperturbed geometric normals + depth + SPP-adaptive luminance weighting so even
// 1-4 SPP interactive frames look smooth & grain-free while silkscreen text & CRT stay crisp!

struct DenoiseParams {
    width: u32,
    height: u32,
    step_size: u32,
    is_final: u32,
    exposure: f32,
    fluor_on: f32,
    denoise_on: f32,
    spp_count: f32,
};

@group(0) @binding(0) var<uniform> p: DenoiseParams;
@group(0) @binding(1) var<storage, read> src_buf: array<vec4<f32>>;
@group(0) @binding(2) var<storage, read> gbuf: array<vec4<f32>>;
@group(0) @binding(3) var<storage, read> albedo_buf: array<vec4<f32>>;
@group(0) @binding(4) var<storage, read_write> dst_buf: array<vec4<f32>>;
@group(0) @binding(5) var out_img: texture_storage_2d<rgba8unorm, write>;

fn aces_tonemap(x: vec3<f32>) -> vec3<f32> {
    let a = 2.51;
    let b = 0.03;
    let c = 2.43;
    let d = 0.59;
    let e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), vec3<f32>(0.0), vec3<f32>(1.0));
}

fn linear_to_srgb(c: vec3<f32>) -> vec3<f32> {
    return pow(clamp(c, vec3<f32>(0.0), vec3<f32>(1.0)), vec3<f32>(1.0 / 2.2));
}

fn luma(c: vec3<f32>) -> f32 {
    return dot(c, vec3<f32>(0.2126, 0.7152, 0.0722));
}

@compute @workgroup_size(8, 8)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
    if (gid.x >= p.width || gid.y >= p.height) {
        return;
    }
    let idx = gid.y * p.width + gid.x;
    let c_raw = src_buf[idx].rgb;
    let g0 = gbuf[idx];
    let alb0 = albedo_buf[idx];

    var result = c_raw;

    if (p.denoise_on > 0.5 && g0.w > 0.0) {
        let n0 = g0.xyz;
        let z0 = g0.w;
        let a0 = max(alb0.rgb, vec3<f32>(0.05));
        let max_x = i32(p.width) - 1;
        let max_y = i32(p.height) - 1;

        var irr0 = c_raw / a0;

        // Pass 0 (step_size == 1): 3x3 neighborhood firefly rejection kills isolated Monte Carlo spikes
        if (p.step_size == 1u) {
            var n_sum = vec3<f32>(0.0);
            var n_cnt = 0.0;
            for (var fy: i32 = -1; fy <= 1; fy += 1) {
                for (var fx: i32 = -1; fx <= 1; fx += 1) {
                    if (fx == 0 && fy == 0) { continue; }
                    let sx = clamp(i32(gid.x) + fx, 0, max_x);
                    let sy = clamp(i32(gid.y) + fy, 0, max_y);
                    let sidx = u32(sy) * p.width + u32(sx);
                    let gs = gbuf[sidx];
                    if (gs.w > 0.0 && dot(n0, gs.xyz) > 0.85 && abs(z0 - gs.w) < max(z0 * 0.06, 3.0)) {
                        let as_rgb = max(albedo_buf[sidx].rgb, vec3<f32>(0.05));
                        n_sum += src_buf[sidx].rgb / as_rgb;
                        n_cnt += 1.0;
                    }
                }
            }
            if (n_cnt >= 3.0) {
                let n_avg = n_sum / n_cnt;
                let max_allowed = max(luma(n_avg) * 2.6, 0.35);
                let l_cur = luma(irr0);
                if (l_cur > max_allowed) {
                    irr0 *= (max_allowed / max(l_cur, 1e-5));
                }
            }
        }

        let l0 = luma(irr0);
        // Adaptive luminance sigma: stronger smoothing at low interactive SPP (1-12), tighter as SPP converges
        let noise_scale = clamp(2.8 / sqrt(max(p.spp_count, 1.0)), 0.38, 2.8);
        let sigma_l = max(0.14 + l0 * 0.55, 0.12) * noise_scale;

        let kernel = array<f32, 3>(1.0, 0.62, 0.28);
        var sum_irr = irr0;
        var sum_w = 1.0;
        let step = i32(p.step_size);

        for (var dy: i32 = -2; dy <= 2; dy += 1) {
            let sy = clamp(i32(gid.y) + dy * step, 0, max_y);
            let ky = kernel[abs(dy)];
            for (var dx: i32 = -2; dx <= 2; dx += 1) {
                if (dx == 0 && dy == 0) { continue; }
                let sx = clamp(i32(gid.x) + dx * step, 0, max_x);
                let kx = kernel[abs(dx)];
                let sidx = u32(sy) * p.width + u32(sx);

                let gs = gbuf[sidx];
                if (gs.w <= 0.0) { continue; }

                let ndot = max(0.0, dot(n0, gs.xyz));
                let w_n = pow(ndot, 16.0);
                let dz = abs(z0 - gs.w) / max(z0 * 0.04, 2.0);
                let w_z = exp(-dz * dz);

                let as_rgb = max(albedo_buf[sidx].rgb, vec3<f32>(0.05));
                let irrs = src_buf[sidx].rgb / as_rgb;
                let dl = abs(l0 - luma(irrs)) / sigma_l;
                let w_l = exp(-dl * dl * 0.65);

                let w = kx * ky * w_n * w_z * w_l;
                sum_irr += irrs * w;
                sum_w += w;
            }
        }
        result = (sum_irr / max(sum_w, 1e-5)) * a0;
    }

    dst_buf[idx] = vec4<f32>(result, 1.0);

    if (p.is_final > 0u) {
        var ldr = aces_tonemap(result * p.exposure);
        let screen_uv = (vec2<f32>(f32(gid.x), f32(gid.y)) + 0.5) / vec2<f32>(f32(p.width), f32(p.height));
        let vdist = length((screen_uv - 0.5) * vec2<f32>(1.35, 1.0));
        let vig = 1.0 - smoothstep(0.48, 0.96, vdist) * select(0.24, 0.12, p.fluor_on > 0.5);
        ldr = linear_to_srgb(ldr * vig);
        textureStore(out_img, vec2<i32>(i32(gid.x), i32(gid.y)), vec4<f32>(ldr, 1.0));
    }
}
