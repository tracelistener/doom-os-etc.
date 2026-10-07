/* Sound Generator v9: real envelopes, a resonant low-pass with its own
 * envelope, and voice stealing for the four-voice engine.
 *
 * ENV keeps its ten values. OFF is exactly stock (no envelope, the stock
 * ~8 ms release). Roland's nine attack-only shapes A..C2, which last at most
 * one waveform cycle, are replaced by the presets in env_presets.h.
 *
 * env_stage replaces the stock ENV step at the end of the oscillator, so it
 * covers live voices, preview and REC export. A note start is seen through the
 * stock ENV setup, which writes 0 to state+0x18c on every start path.
 * Per-voice state lives in this module's .bss, zeroed by the boot copy.
 */
#include "env_presets.h"

typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;

#define EXPORT __attribute__((used))

#define SINGLETON 0x80249800u
#define CLONE_BASE 0x8353A310u
#define EXPORT_BASE 0x8353C000u
#define EXPORT_COUNT 0x8353C700u
#define VOICE_SIZE 0x1B0u
#define MARK 0x3F800000u            /* state+0x18c once a start has been seen */
#define DECLICK 0.98963f            /* 2 ms at 48 kHz */
#define SILENT 1e-4f                /* -80 dB: the release is over */

enum { IDLE, ATTACK, DECAY, SUSTAIN, RELEASE, FAST };

typedef struct {
    float amp, famp, ic1, ic2, a1, a2, a3, declick, last;
    u8 stage, fstage, preset, tick;
} env_t;

EXPORT env_t env_slot[8];              /* 0..3 live voices, 4..7 REC export voices */
EXPORT volatile u8 env_restart[8];     /* a stolen voice restarts its envelope */
EXPORT volatile u8 env_fast[8];        /* all-stop: let the stock fade finish now */
EXPORT u32 env_age[4];                 /* note-on order, for choosing what to steal */
EXPORT u32 env_serial;
EXPORT volatile int stolen_source[8], stolen_note[8];
EXPORT volatile u8 stolen_valid[8];
EXPORT u32 stolen_next;

static float absf(float x) { return x < 0 ? -x : x; }

/* 2^x for |x| < 100: exponent bits plus a fraction polynomial. */
static float exp2_(float x) {
    if (x < -100) return 0;
    if (x > 100) x = 100;
    int n = (int)x;
    float t = x - n;
    if (t < 0) { --n; t += 1; }
    float p = 1 + t*(0.693147180f + t*(0.240226507f + t*(0.0555041087f +
              t*(0.00961812911f + t*0.00133335581f))));
    union { u32 bits; float value; } scale;
    scale.bits = (u32)(n + 127) << 23;
    return p * scale.value;
}

/* tan(w) for 0 <= w <= 0.45*pi, Pade [5/4]. */
static float tan_(float w) {
    float w2 = w*w;
    return w*(945 - 105*w2 + w2*w2) / (945 - 420*w2 + 15*w2*w2);
}

/* Identity below 0.6 of full scale, then a smooth knee that never passes 1. */
static float soft(float y) {
    float a = absf(y);
    if (a <= 0.6f) return y;
    float u = (a - 0.6f)*2.5f;
    float s = 0.6f + 0.4f*u/(1 + u);
    return y < 0 ? -s : s;
}

static int slot_of(u32 a) {
    if (a == SINGLETON) return 0;
    u32 d = a - CLONE_BASE;
    if (d < 3*VOICE_SIZE && d % VOICE_SIZE == 0) return 1 + (int)(d/VOICE_SIZE);
    d = a - EXPORT_BASE;
    if (d < 4*VOICE_SIZE && d % VOICE_SIZE == 0) return 4 + (int)(d/VOICE_SIZE);
    return -1;
}

static int clamp16(float y) {
    if (y > 32767) return 32767;
    if (y < -32768) return -32768;
    return (int)y;
}

/* r4 = voice state, r5 = sample after LEVEL and the stock fades. */
EXPORT int env_stage(u8 *s, int x) {
    int i = slot_of((u32)s);
    if (i < 0) return (short)x;
    env_t *e = &env_slot[i];
    volatile u32 *mark = (volatile u32 *)(s + 0x18C);
    int fresh = *mark == 0, steal = 0;
    if (fresh || env_restart[i]) {
        steal = !fresh;                         /* only a stolen voice was sounding */
        u32 p = *(volatile u32 *)(s + 0x10);
        env_restart[i] = 0;
        env_fast[i] = 0;
        *mark = MARK;
        e->preset = p <= 9 ? (u8)p : 0;
        e->stage = e->fstage = ATTACK;
        e->amp = e->famp = 0;
        e->ic1 = e->ic2 = 0;
        e->tick = 0;
        if (fresh) e->declick = 0;
    }
    float y;
    if (e->preset == 0) {
        if (!steal && e->declick == 0) { e->last = (float)x; return (short)x; }  /* stock OFF */
        y = (float)x;
    } else {
        const preset_t *P = &presets[e->preset];
        int hold = 0;
        if (!s[0]) {
            /* Inactive after a release: the stock fade ended the voice this sample.
             * The REC exporter also clears its own voice's active flag but keeps
             * rendering it, so an inactive voice that was never released plays on. */
            if (e->stage >= RELEASE) e->stage = IDLE;
        } else if (s[1]) {                      /* released, or all-stop */
            e->fstage = RELEASE;
            if (env_fast[i]) {
                if (e->stage != IDLE) e->stage = FAST;
            } else if (e->stage != FAST) {
                if (e->stage < RELEASE) e->stage = RELEASE;
                hold = 1;
            }
        }
        switch (e->stage) {
        case ATTACK:
            e->amp = 1.3f - (1.3f - e->amp)*P->ra;
            if (e->amp >= 1) { e->amp = 1; e->stage = DECAY; }
            break;
        case DECAY:
            e->amp = P->sus + (e->amp - P->sus)*P->rd;
            if (absf(e->amp - P->sus) < 1e-5f) { e->amp = P->sus; e->stage = SUSTAIN; }
            break;
        case RELEASE:
            e->amp *= P->rr;
            break;
        default:                                /* SUSTAIN; FAST holds, the stock fade finishes it */
            break;
        }
        /* While our release runs, keep the stock 400-sample fade at its start
         * (gain 399/400). When it is silent, let the stock code end the voice. */
        if (hold) *(volatile int *)(s + 0x4C) = e->amp > SILENT ? -200 : 199;
        if (P->filter) {
            switch (e->fstage) {
            case ATTACK:
                e->famp = 1.3f - (1.3f - e->famp)*P->fra;
                if (e->famp >= 1) { e->famp = 1; e->fstage = DECAY; }
                break;
            case DECAY:
                e->famp = P->fsus + (e->famp - P->fsus)*P->frd;
                if (absf(e->famp - P->fsus) < 1e-5f) { e->famp = P->fsus; e->fstage = SUSTAIN; }
                break;
            case RELEASE:
                e->famp *= P->frr;
                break;
            default:
                break;
            }
            if ((e->tick++ & 15) == 0) {        /* cutoff follows the note and the filter envelope */
                float inc = (float)*(volatile double *)(s + 0x20);
                if (!(inc > 1e-5f && inc < 0.5f)) inc = 261.6256f/48000;
                float rc = inc*exp2_(P->base + P->amount*e->famp);
                if (rc < 0.0006f) rc = 0.0006f;
                if (rc > 0.45f) rc = 0.45f;
                float g = tan_(3.14159265f*rc);
                float a1 = 1/(1 + g*(g + P->k));
                e->a1 = a1;
                e->a2 = g*a1;
                e->a3 = g*e->a2;
            }
            /* Zavalishin's trapezoidal state-variable filter, low-pass output. */
            float v3 = (float)x*P->drive - e->ic2;
            float v1 = e->a1*e->ic1 + e->a2*v3;
            float v2 = e->ic2 + e->a2*e->ic1 + e->a3*v3;
            e->ic1 = 2*v1 - e->ic1;
            e->ic2 = 2*v2 - e->ic2;
            y = soft(v2*P->gain*e->amp*(1/32767.f))*32767;
        } else {
            y = (float)x*e->amp;
        }
    }
    if (steal) e->declick = e->last - y;       /* continue from the stolen note's last sample */
    if (e->declick != 0) {
        y += e->declick;
        e->declick *= DECLICK;
        if (absf(e->declick) < 0.5f) e->declick = 0;
    }
    e->last = y;
    return clamp16(y);
}

/* REC: one frame of the held-chord snapshot, as v4's mixer did (each voice
 * halved, summed, saturated once), entered from the stock exporter's loop
 * with its frame counter (r10, from 1), loop limit (r11) and the frame-count
 * word at [sp+4]. A recording fades over its last 10 ms, so a sustained sound
 * no longer stops dead at the end of the pad. The exception is a seamless
 * raw-wave loop: ENV OFF, Pad Length >= 1 and START/END on (+0x04, on by
 * default), which the stock exporter trims to whole cycles. That stays as
 * rendered. With an ENV preset the sound changes over time, so it fades. */
typedef int (*osc_fn)(volatile u8 *state);
#define STOCK_OSC ((osc_fn)0x80007701u)
#define FADE_FRAMES 480

EXPORT int export_mix(int pos, int fp, int limit) {
    u32 count = *(volatile u32 *)EXPORT_COUNT;
    int sum = 0;
    for (u32 i = 0; i < count; i++)
        sum += STOCK_OSC((volatile u8 *)(EXPORT_BASE + i*VOICE_SIZE)) >> 1;
    if (sum > 32767) sum = 32767;
    if (sum < -32768) sum = -32768;
    int last = limit < fp - 1 ? limit + 1 : fp;     /* the stock loop's final frame */
    int left = last - pos;                          /* frames still to come after this one */
    int fade = last/4 < FADE_FRAMES ? last/4 : FADE_FRAMES;    /* very short pads: last quarter */
    if (fade < 1) fade = 1;
    if (left < 0) left = 0;
    volatile u8 *base = (volatile u8 *)EXPORT_BASE;
    int loop = *(volatile int *)(base + 0x10) == 0          /* ENV OFF: a raw waveform */
               && *(volatile int *)(base + 0x180) >= 1 && base[4];
    if (!loop && left < fade)
        sum = (int)((float)sum*(float)(left + 1)/(float)fade);
    return sum;
}

/* Value text for ENV (parameter 0x82). Out-of-range values write nothing, as stock. */
EXPORT void env_name(u32 value, char *target) {
    if (value > 9) return;
    const char *n = preset_names[value];
    int k = 0;
    do { target[k] = n[k]; } while (n[k++]);
}

/* ---- Note dispatcher: v7's behaviour, plus stealing instead of dropping. ---- */

typedef void (*voice_fn)(volatile u8 *voice);
typedef void (*note_fn)(volatile u8 *voice, int note, int vel, int r3, int source);
typedef int (*dispatch_fn)(void);
#define STOCK_RESET ((voice_fn)0x80132341u)
#define STOCK_NOTE ((note_fn)0x80132429u)
#define DISPATCH_OFF ((dispatch_fn)0x800DED09u)   /* tk_dis_dsp: the audio task cannot run */
#define DISPATCH_ON ((dispatch_fn)0x800DECF9u)

static volatile u8 *voice(int i) {
    return (volatile u8 *)(i ? CLONE_BASE + (u32)(i - 1)*VOICE_SIZE : SINGLETON);
}
static int field(volatile u8 *v, u32 off) { return *(volatile int *)(v + off); }

/* A stolen note's pad or key is usually still held. Its note-off must not
 * release whatever now sounds, so it is remembered and swallowed. */
static void stolen_add(int source, int note) {
    u32 j = stolen_next++ & 7;
    stolen_valid[j] = 0;
    stolen_source[j] = source;
    stolen_note[j] = note;
    stolen_valid[j] = 1;
}
static int stolen_take(int source, int note) {
    for (int j = 0; j < 8; j++)
        if (stolen_valid[j] && stolen_source[j] == source && stolen_note[j] == note) {
            stolen_valid[j] = 0;
            return 1;
        }
    return 0;
}
/* A new note-on from a pad means that pad was let go; its OCT-shifted
 * note-off may not have matched. MIDI keys are cleared by exact note. */
static void stolen_forget(int source, int note) {
    for (int j = 0; j < 8; j++)
        if (stolen_valid[j] && stolen_source[j] == source &&
            (stolen_note[j] == note || (u32)source <= 15))
            stolen_valid[j] = 0;
}

/* ---- FREQ readout and knob: they follow the latest note, not voice 0. ----
 * With long releases voice 0 often stays busy fading, so new notes land on
 * the other voices; a readout tied to voice 0 then froze on a fading note. */

EXPORT volatile u8 display_slot;        /* voice of the latest note (or of an idle FREQ change) */

EXPORT volatile u8 *display_voice(void) { return voice(display_slot & 3); }

/* Getter for FREQ (parameter 0x7c): the knob steps from this value. */
EXPORT int freq_get(volatile u8 *state) {
    if (state[0x71] > 31) return 0;
    return field(display_voice(), 0x5C);
}

typedef void (*setter_fn)(volatile u8 *voice, int id, int value, int flags);
#define STOCK_SETTER ((setter_fn)0x80019E31u)

/* FREQ knob: v4's interval-keeping transpose of every held voice, measured
 * from the note on the screen. With nothing held, voice 0 takes the value. */
EXPORT void freq_event(u32 self, int id, int value, int flags) {
    (void)self; (void)id;
    int delta = value - field(display_voice(), 0x5C);
    for (int i = 0; i < 4; i++) {               /* every held note stays inside -36..48 */
        volatile u8 *v = voice(i);
        if (v[0] && !v[1]) {
            int n = field(v, 0x5C);
            if (n + delta < -36) delta = -36 - n;
            if (n + delta > 48) delta = 48 - n;
        }
    }
    int held = -1;
    for (int i = 0; i < 4; i++) {
        volatile u8 *v = voice(i);
        if (!v[0]) {
            if (i == 0) STOCK_SETTER(v, 0x7C, value, flags);
        } else if (!v[1]) {
            STOCK_SETTER(v, 0x7C, field(v, 0x5C) + delta, flags);
            if (held < 0 || env_age[i] > env_age[held]) held = i;
        }
    }
    volatile u8 *d = display_voice();
    if (!d[0] || d[1]) display_slot = held >= 0 ? (u8)held : 0;   /* show a note that moved */
}

/* Voice for a new note (pad or MIDI): an idle one first (voice 0 first; an
 * idle clone takes voice 0's settings), else the oldest fading voice, else the
 * oldest held one. Returns the index, | STOLEN when it was sounding, or -1. */
#define STOLEN 0x100
static int claim(void) {
    for (int i = 0; i < 4; i++) {
        volatile u8 *v = voice(i);
        if (v[0]) continue;
        if (i) {
            volatile u32 *dst = (volatile u32 *)v, *src = (volatile u32 *)SINGLETON;
            *(volatile u16 *)v = 0;
            for (int w = 1; w < 108; w++) dst[w] = src[w];
            dst[0] = src[0] & 0xFFFF0000u;
        }
        /* A TYPE change made while this voice was fading never swapped its
         * sounding type; a new note always starts on the selected one. */
        if (v[0x70] != v[0x71]) v[0x70] = v[0x71];
        STOCK_RESET(v);
        env_age[i] = ++env_serial;
        display_slot = (u8)i;
        return i;
    }
    int best = -1;
    for (int pass = 0; pass < 2 && best < 0; pass++)
        for (int i = 0; i < 4; i++) {
            volatile u8 *v = voice(i);
            if (v[0] && (pass ? !v[1] : v[1]) && (best < 0 || env_age[i] < env_age[best]))
                best = i;
        }
    if (best < 0) return -1;
    volatile u8 *v = voice(best);
    if (v[3]) return -1;                        /* REC export owns it; stock ignores the note */
    if (!v[1] && field(v, 0x1AC) != -1)         /* a held pad note: swallow its note-off later */
        stolen_add(field(v, 0x1AC), field(v, 0x19C));
    u8 type = ((volatile u8 *)SINGLETON)[0x71];
    DISPATCH_OFF();
    if (v[1]) {                                 /* stop the fade, keep the voice sounding */
        *(volatile int *)(v + 0x4C) = 200;
        v[1] = 0;
    }
    if (v[0x70] != type || v[0x71] != type) {   /* fading voices missed TYPE changes */
        v[0x71] = type;
        v[0x70] = type;
        *(volatile int *)(v + 0x48) = *(volatile int *)(SINGLETON + 0x48);
    }
    env_restart[best] = 1;                      /* the declick covers the switch */
    DISPATCH_ON();
    env_age[best] = ++env_serial;
    display_slot = (u8)best;
    return best | STOLEN;
}

EXPORT void note_event(u32 self, int note, int vel, int r3, int source) {
    (void)self;
    if (vel != 0) {
        *(volatile u32 *)EXPORT_COUNT = 0;      /* a new note drops an older chord snapshot */
        stolen_forget(source, note);
        if ((u32)(note + 36) > 84) return;      /* the stock note-on ignores these */
        int got = claim();
        if (got < 0) return;
        volatile u8 *v = voice(got & 3);
        STOCK_NOTE(v, note, vel, r3, source);   /* idle voice: start; sounding: retune in place */
        v[0x194] = 0xFF;                        /* not a MIDI note: MIDI note-offs never match it */
        if (got & STOLEN) *(volatile int *)(v + 0x1A0) = 1000;   /* no mono return-to-previous-note */
        return;
    }
    if (stolen_take(source, note)) return;
    int released = 0;
    for (int i = 0; i < 4; i++) {               /* exact note and source first */
        volatile u8 *v = voice(i);
        if (v[0] && field(v, 0x1AC) == source && field(v, 0x19C) == note) {
            STOCK_NOTE(v, note, 0, r3, source);
            ++released;
        }
    }
    if (released) return;
    for (int i = 0; i < 4; i++) {               /* else that source's held voice (OCT moved) */
        volatile u8 *v = voice(i);
        if (v[0] && field(v, 0x1AC) == source && !v[1]) {
            STOCK_NOTE(v, note, 0, r3, source);
            return;
        }
    }
}

/* ---- MIDI IN: the stock route plays voice 0 only; share the four voices. ----
 * The MIDI dispatcher (0x8005a6a0) called the stock MIDI note function on
 * voice 0. That function keeps the MIDI note in +0x194 and releases on it. */

typedef void (*midi_fn)(volatile u8 *voice, int note, int vel, int r3);
#define STOCK_MIDI ((midi_fn)0x80132119u)

EXPORT void midi_event(u32 self, int note, int vel, int r3) {
    (void)self;
    if ((u32)note < 12) return;                 /* the stock function ignores these */
    if (vel != 0) {
        *(volatile u32 *)EXPORT_COUNT = 0;
        int got = claim();
        if (got < 0) return;
        volatile u8 *v = voice(got & 3);
        STOCK_MIDI(v, note, vel, r3);
        *(volatile int *)(v + 0x1AC) = -1;      /* no pad: pad note-offs never match it */
        *(volatile int *)(v + 0x19C) = note - 48;   /* the pad lights show this pitch */
        return;
    }
    for (int i = 0; i < 4; i++) {               /* every voice still holding this key */
        volatile u8 *v = voice(i);
        if (v[0] && v[0x194] == (u8)note) STOCK_MIDI(v, note, 0, r3);
    }
}

/* ---- Scale pads: on the Sound Generator page the 16 pads walk the scale. ----
 * Stock pads are 16 semitones (-4..11 around pad 9) and a scale only silences
 * the pads outside it. Here pad 9 plays the first scale note at or above its
 * stock note and each pad steps one scale note, so a 7-note scale spans over
 * two octaves and a pentatonic three. Chrom, and every other page (chromatic
 * sample mode shares these settings and helpers), keep the stock layout. */

#define KB 0x80591C48u                          /* keyboard: +4 OCT, +8 shift, +0xc SCALE, +0x10 root */
#define PAD_SEMITONE ((const int *)0x801A9E60u)
#define SCALE_MASKS ((const u8 *const *)0x801AB4A4u)
#define PAGE (*(volatile short *)0x80245880u)
#define SG_PAGE 0x1E

static int kb(u32 off) { return *(volatile int *)(KB + off); }
static int degree(int n, int root) { return (n - root + 120) % 12; }

EXPORT int kb_pad_note(int pad) {
    int t = PAD_SEMITONE[pad], n = t + 12*kb(4) - kb(8), scale = kb(0xC);
    if (scale < 1 || scale > 6 || PAGE != SG_PAGE) return n;
    const u8 *mask = SCALE_MASKS[scale - 1];
    int root = kb(0x10);
    n -= t;                                     /* pad 9's stock note */
    while (!mask[degree(n, root)]) n++;
    for (; t > 0; t--) do n++; while (!mask[degree(n, root)]);
    for (; t < 0; t++) do n--; while (!mask[degree(n, root)]);
    return n;
}

/* Pad lights: is this pad playable (stock: in the scale)? */
EXPORT int kb_in_scale(int pad) {
    int scale = kb(0xC);
    if (scale < 1 || scale > 6 || PAGE == SG_PAGE) return 1;
    int n = PAD_SEMITONE[pad] + 12*kb(4) - kb(8);
    return SCALE_MASKS[scale - 1][degree(n, kb(0x10))] != 0;
}

/* Pad lights: the pad's note above the root, without the octave (stock helper). */
EXPORT int kb_pad_rel(int pad) {
    return kb_pad_note(pad) - 12*kb(4) - kb(0x10);
}
