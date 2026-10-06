/* Wave Lab's 17 phase/DUTY recipes, adapted to freestanding Cortex-M7.
 * No allocation, mutable global state or sample/project storage.
 * Generated sine and scanned-table data are supplied by compile_waves.py.
 */
#include "wave_tables.h"

typedef unsigned int u32;
typedef unsigned char u8;
#define TAU 6.2831853071795864769f
#define INV_TAU 0.15915494309189533577f

static float clamp(float x, float lo, float hi) { return x < lo ? lo : x > hi ? hi : x; }
static float absf(float x) { return x < 0 ? -x : x; }
static float frac(float x) { return x - (int)x; } /* all callers nonnegative */
static float exp2_(float x) { return ((float (*)(float))0x800d309b)(x); }
static float sine(float p) {
    p -= (int)p;
    if (p < 0) p += 1;
    float x = p * 2048;
    int i = (int)x;
    float t = x - i;
    return sine_table[i] + t * (sine_table[i + 1] - sine_table[i]);
}
static float cosine(float p) { return sine(p + 0.25f); }
static float tanh_(float x) {
    float a = absf(x);
    if (a > 10) return x < 0 ? -1 : 1;
    float e = exp2_(-2.88539008178f * a);
    float y = (1 - e) / (1 + e);
    return x < 0 ? -y : y;
}
static float hash11(u32 x) {
    x = (x ^ (x >> 16)) * 0x45d9f3bU;
    x = (x ^ (x >> 16)) * 0x45d9f3bU;
    x ^= x >> 16;
    return (float)x * (2.0f / 4294967295.0f) - 1;
}
static float lp_bank(float p, float d, float dt, int odd) {
    float previous = 0, current = sine(p), two_c = 2 * cosine(p), y = 0;
    int nmax = (int)clamp(0.45f / dt, 1, 64);
    float nc = exp2_(0.2f + 5.2f * d);
    for (int n = 1; n <= nmax; n++) {
        if (!odd || (n & 1)) {
            float x = n / nc, a = 1 - x*x, b = x / 1.4f;
            y += current / (n * (a*a + b*b));
        }
        float next = two_c * current - previous;
        previous = current; current = next;
    }
    return y;
}

/* Public raw function is retained for ARM-vs-original-JS parity tests. */
__attribute__((used))
float wave_eval(u32 type, float p, float d, float dt, float r) {
    p = clamp(p, 0, 0.99999994f);
    d = clamp(d, 0, 1);
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
    case 20: return (1 - p) * cosine(exp2_(4*d) * p);
    case 21: return sine(frac(exp2_(3*d) * p));
    case 22: return sine(0.25f * (1 + 6*d) * sine(p));
    case 23: {
        float g = 1 + 14*d*d, b = 0.35f, t0 = tanh_(g*b);
        float n1 = tanh_(g*(1+b)) - t0, n2 = t0 - tanh_(g*(b-1));
        return (tanh_(g*(sine(p)+b)) - t0) / (n1 > n2 ? n1 : n2);
    }
    case 24: return lp_bank(p, d, dt, 0);
    case 25: return lp_bank(p, d, dt, 1);
    case 26: {
        float pos = d*4;
        int i = (int)pos;
        if (i > 3) i = 3;
        float t = pos-i, y = 0;
        for (int j = 0; j < 9; j++) {
            int h = organ_h[j];
            if (h*dt > 0.45f) continue;
            float g = organ_regs[i][j]*(1-t) + organ_regs[i+1][j]*t;
            if (g > 0) y += g*sine(h*p);
        }
        return y;
    }
    case 27: {
        float pos = d*4;
        int i = (int)pos;
        if (i > 3) i = 3;
        float t = pos-i, tsec = p/(dt*48000), y = 0.3f*sine(p);
        for (int k = 0; k < 3; k++) {
            float f = vowels[i][k] * exp2_(vowel_ratio_log2[i][k]*t);
            if (f > 21600) continue;
            float env = exp2_(-4.53236014183f * vowel_bw[k]*tsec);
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

/* Voice state stays owned by the original renderer. No double-field writes. */
__attribute__((used))
float wave_output(const u8 *state) {
    u32 type = state[0x70];
    float p = (float)*(const double *)(state + 0x18);
    float dt = (float)*(const double *)(state + 0x20);
    float r = (float)*(const double *)(state + 0x40);
    float d = (float)*(const int *)(state + 0x48) * 0.01f;
    float y = wave_eval(type, p, d, dt, r);
    /* Fixed headroom for harmonic sums; preserve stock LEVEL and envelope,
     * not the browser's RMS matching / DC blocker / output saturation chain. */
    if (type == 24 || type == 25 || type == 26) y *= 0.16f;
    if (type == 27) y *= 0.46f;
    return clamp(y, -1, 1);
}

static const char names[17][7] = {
    "FM1:1", "FM1:2", "FM1:7", "CZSaw", "CZSqr", "CZRes", "Sync",
    "Fold", "Drive", "LPSaw", "LPSqr", "Organ", "Vowel", "Table",
    "Logic", "Metal", "Grit"
};
__attribute__((used))
void wave_name(u32 type, char *target) {
    if (type < 15 || type > 31) { target[0] = '-'; target[1] = 0; return; }
    const char *source = names[type-15];
    do { *target++ = *source; } while (*source++);
}
