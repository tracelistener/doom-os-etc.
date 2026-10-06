/* Wave Lab v8 (RAM build): v3-budget's 17 recipes exactly as in v3 (raw
 * integer DUTY, naive Sync/CZRes), plus per-(wave, DUTY) DC removal and
 * perceived-loudness matching (wave_level.h, generated at build time).
 * Raw recipe output is bit-identical to v3-budget at every DUTY setting.
 * No mutable state; the module runs from unused SDRAM.
 */
#include "wave_tables.h"
#include "wave_level.h"

typedef unsigned int u32;
typedef unsigned char u8;
#define INV_TAU 0.15915494309189533577f

#ifdef WAVE_HOST
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((used))
#endif

static float clamp(float x, float lo, float hi) { return x < lo ? lo : x > hi ? hi : x; }
static float absf(float x) { return x < 0 ? -x : x; }
static float frac(float x) { return x - (int)x; } /* all callers nonnegative */
static float lerp(float a, float b, float t) { return a + t*(b - a); }
/* Negative exponent only: fractional lookup + IEEE normal power of two.
 * Bounded, no libm calls, and values below minimum normal are inaudible. */
static float exp2_negative(float x) {
    if (x <= -126) return 0;
    if (x >= 0) return 1;
    int n = (int)x;
    float t = x-n;
    if (t < 0) { --n; t += 1; }
    float pos = t*256;
    int i = (int)pos;
    /* Small negative inputs can round to t==1. Preserve the endpoint. */
    if (i >= 256) { ++n; i = 0; pos = 0; }
    union { u32 bits; float value; } scale;
    scale.bits = (u32)(n+127) << 23;
    return scale.value * (exp2_fraction[i] + (pos-i)*(exp2_fraction[i+1]-exp2_fraction[i]));
}
static float sine(float p) {
    p -= (int)p;
    if (p < 0) p += 1;
    /* A tiny negative argument can round to exactly 1 after the addition.
     * Wrap again before indexing, rather than read past sine_table[2048]. */
    if (p >= 1) p = 0;
    float x = p * 2048;
    int i = (int)x;
    float t = x - i;
    return sine_table[i] + t * (sine_table[i + 1] - sine_table[i]);
}
static float cosine(float p) { return sine(p + 0.25f); }
static float tanh_(float x) {
    float a = absf(x);
    if (a >= 10) return x < 0 ? -1 : 1;
    float pos = a*102.4f;
    int i = (int)pos;
    if (i >= 1024) return x < 0 ? -1 : 1;
    float y = tanh_table[i] + (pos-i)*(tanh_table[i+1]-tanh_table[i]);
    return x < 0 ? -y : y;
}
static float hash11(u32 x) {
    x = (x ^ (x >> 16)) * 0x45d9f3bU;
    x = (x ^ (x >> 16)) * 0x45d9f3bU;
    x ^= x >> 16;
    return (float)x * (2.0f / 4294967295.0f) - 1;
}

/* DUTY position in the integer-percent coefficient rows. Row i+1 is read only
 * when t > 0, which implies i <= 99, so no table is ever read past row 100. */
typedef struct { int i; float t; } duty_pos;
static duty_pos duty_at(float pct) {
    pct = clamp(pct, 0, 100);
    duty_pos at;
    at.i = (int)pct;
    at.t = pct - at.i;
    if (at.i >= 100) { at.i = 100; at.t = 0; }
    return at;
}

static float lp_bank(float p, duty_pos at, float dt, int odd) {
    float previous = 0, current = sine(p), two_c = 2 * cosine(p), y = 0;
    int nmax = (int)clamp(0.45f / dt, 1, 64);
    const float *gains = lp_gains[at.i + (odd ? 101 : 0)];
    if (at.t > 0) {
        const float *next = gains + 64;  /* row i+1 of the same bank */
        float y1 = 0;
        for (int n = 1; n <= nmax; n++) {
            y += current * gains[n-1];
            y1 += current * next[n-1];
            float following = two_c * current - previous;
            previous = current; current = following;
        }
        return lerp(y, y1, at.t);
    }
    for (int n = 1; n <= nmax; n++) {
        y += current * gains[n-1];
        float next = two_c * current - previous;
        previous = current; current = next;
    }
    return y;
}

/* Raw recipe at DUTY `pct` percent (0..100, may be fractional while gliding).
 * Exported so tests can compare exact integer DUTY rows with v3. */
EXPORT float wave_core(u32 type, float p, float pct, float dt, float r) {
    p = clamp(p, 0, 0.99999994f);
    float d = clamp(pct, 0, 100) * 0.01f;
    duty_pos at = duty_at(pct);
    dt = clamp(dt, 0.00000001f, 0.5f);
    switch (type) {
    case 15: return sine(p + INV_TAU * 7*d*d * sine(p));
    case 16: return sine(p + INV_TAU * 5*d*d * sine(2*p));
    case 17: return sine(p + INV_TAU * 2.5f*d*d * sine(7*p));
    case 18: {
        float m = 0.5f - 0.49f*d;
        float w = p < m ? 0.5f*p/m : 0.5f + 0.5f*(p-m)/(1-m);
        return -cosine(w);
    }
    case 19: {
        float m = 0.5f - 0.48f*d;
        float h = p < 0.5f ? p : p - 0.5f;
        float base = p < 0.5f ? 0 : 0.5f;
        return cosine(base + 0.5f * clamp(h / m, 0, 1));
    }
    case 20: case 21: {
        float scale = cz_scales[at.i][type - 20];
        if (at.t > 0) scale = lerp(scale, cz_scales[at.i + 1][type - 20], at.t);
        return type == 20 ? (1 - p) * cosine(scale * p) : sine(frac(scale * p));
    }
    case 22: return sine(0.25f * (1 + 6*d) * sine(p));
    case 23: {
        const float *c = drive_params[at.i];
        float g = c[0], t0 = c[1], norm = c[2];
        if (at.t > 0) {
            g = lerp(g, c[3], at.t);
            t0 = lerp(t0, c[4], at.t);
            norm = lerp(norm, c[5], at.t);
        }
        return (tanh_(g*(sine(p)+0.35f)) - t0)*norm;
    }
    case 24: return lp_bank(p, at, dt, 0);
    case 25: return lp_bank(p, at, dt, 1);
    case 26: {
        const float *gains = organ_gains[at.i];
        float y = 0;
        for (int j = 0; j < 9; j++) {
            int h = organ_h[j];
            if (h*dt > 0.45f) continue;
            float g = gains[j];
            if (at.t > 0) g = lerp(g, gains[j + 9], at.t);
            if (g > 0) y += g*sine(h*p);
        }
        return y;
    }
    case 27: {
        const float *freq = formant_freq[at.i];
        float tsec = p/(dt*48000), y = 0.3f*sine(p);
        for (int k = 0; k < 3; k++) {
            float f = freq[k];
            if (at.t > 0) f = lerp(f, freq[k + 3], at.t);
            if (f > 21600) continue;
            float env = exp2_negative(-4.53236014183f * vowel_bw[k]*tsec);
            float att = clamp(tsec*f*2, 0, 1);
            y += vowel_amp[k]*env*att*sine(f*tsec);
        }
        return y;
    }
    case 28: {
        float fpos = d*31;
        int f0 = (int)fpos, f1 = f0 < 31 ? f0+1 : 31;
        float ft = fpos-f0, x = p*256;
        int i0 = (int)x, i1 = (i0+1)&255;
        float xt = x-i0;
        float a = scanned_table[f0][i0]*(1-xt) + scanned_table[f0][i1]*xt;
        float b = scanned_table[f1][i0]*(1-xt) + scanned_table[f1][i1]*xt;
        return a*(1-ft) + b*ft;
    }
    case 29: {
        int k = 2 + (int)(d*7 + 0.5f);
        return ((p < 0.5f) != (frac(k*p) < 0.5f)) ? 1 : -1;
    }
    case 30: {
        int steps = 2 + (int)(62*d*d + 0.5f);
        return hash11((u32)(int)(p*steps)*7919U + (u32)steps*104729U);
    }
    case 31: {
        r = clamp(r, -1, 1);
        float w = 0.5f + 0.45f*d*r;
        return (p < w ? 1 : -1) * (1 - 0.35f*d*absf(r));
    }
    default: return 0;
    }
}

/* Raw function kept for parity tests: d in 0..1, as in v1-v3. */
EXPORT float wave_eval(u32 type, float p, float d, float dt, float r) {
    return wave_core(type, p, clamp(d, 0, 1) * 100, dt, r);
}

/* Remove the recipe's DC and match loudness across waves and DUTY settings.
 * wave_level[type-15][duty] = {mean, gain}; gain is RMS-matched and peak-capped. */
static float level_fix(u32 type, float pct, float y) {
    if (type < 15 || type > 31) return y;
    duty_pos at = duty_at(pct);
    const float *row = wave_level[type - 15][at.i];
    float mean = row[0], gain = row[1];
    if (at.t > 0) {
        mean = lerp(mean, row[2], at.t);
        gain = lerp(gain, row[3], at.t);
    }
    return (y - mean) * gain;
}

/* Voice state stays owned by the original renderer. No double-field writes. */
EXPORT float wave_output(const u8 *state) {
    u32 type = state[0x70];
    float p = (float)*(const double *)(state + 0x18);
    float dt = (float)*(const double *)(state + 0x20);
    float r = (float)*(const double *)(state + 0x40);
    /* v8: DUTY is used raw, exactly as the stock waves and v1-v3 do: every step
     * (and knob jitter) lands at once. That stepping is the waves' grit. */
    int duty = *(const int *)(state + 0x48);
    float pct = (float)(duty < 0 ? 0 : duty > 100 ? 100 : duty);
    return clamp(level_fix(type, pct, wave_core(type, p, pct, dt, r)), -1, 1);
}

static const char names[17][7] = {
    "FM1:1", "FM1:2", "FM1:7", "CZSaw", "CZSqr", "CZRes", "Sync",
    "Fold", "Drive", "LPSaw", "LPSqr", "Organ", "Vowel", "Table",
    "Logic", "Metal", "Grit"
};
EXPORT void wave_name(u32 type, char *target) {
    if (type < 15 || type > 31) { target[0] = '-'; target[1] = 0; return; }
    const char *source = names[type-15];
    do { *target++ = *source; } while (*source++);
}

#ifdef WAVE_HOST
/* Build-time render of one period (n phases) for the perceptual-loudness table. */
EXPORT void wave_host_render(u32 type, float pct, float dt, float r, int n, float *out) {
    for (int j = 0; j < n; j++) out[j] = wave_core(type, (j + 0.5f) / n, pct, dt, r);
}
#endif

#ifdef WAVE_HOST
/* Build-time statistics for wave_level.h: mean, RMS about the mean and the
 * peak |y - mean| over n evenly spaced phases (and the given r values). */
EXPORT void wave_host_stats(u32 type, float pct, float dt, int n, const float *rs, int nr,
                            float mean_in, int use_mean, float *out) {
    double sum = 0, sum2 = 0, peak = 0;
    long count = 0;
    double mean = mean_in;
    if (!use_mean) {
        for (int k = 0; k < nr; k++)
            for (int j = 0; j < n; j++) { sum += wave_core(type, (j + 0.5f) / n, pct, dt, rs[k]); count++; }
        mean = sum / count;
    }
    count = 0;
    for (int k = 0; k < nr; k++)
        for (int j = 0; j < n; j++) {
            double v = wave_core(type, (j + 0.5f) / n, pct, dt, rs[k]) - mean;
            sum2 += v * v;
            if (v > peak) peak = v;
            if (-v > peak) peak = -v;
            count++;
        }
    out[0] = (float)mean;
    out[1] = (float)(sum2 / count > 0 ? __builtin_sqrt(sum2 / count) : 0);
    out[2] = (float)peak;
}
#endif
