/* Sound Generator v9.9.3: an arpeggiator (after the MC-101's), on top of v9.6.
 *
 * Five items join the Sound Generator VALUE menu after SCALE NOTE OCT OFST
 * ENV TUNE, used the same way (move to the item, enter edit, turn):
 *   ARP    OFF UP DOWN UP&DN RAND ORDER CHORD
 *   RATE   1/4 1/4T 1/8 1/8T 1/16 1/16T 1/32 of a beat, at the tempo
 *          BPM-synced samples in the current bank follow (project or bank BPM)
 *   A.OCT  -3..+3: also play the held notes that many octaves below/above
 *   HOLD   OFF ON: latch, the arp keeps playing after you let go; the next
 *          key after letting go of everything starts a new chord
 *   BPM    read-only: the tempo the arp plays at ("!" + raw when unusable)
 * With ARP on, pads and MIDI IN no longer start voices themselves: they add
 * and remove notes from the arp's list. The arp runs in the audio loop
 * (checked every 64-frame block), starts each step on a free voice (or takes
 * the oldest one with the 2.8 ms quick fade) and releases it half a step
 * later, so the ENV preset shapes every step and release tails overlap.
 * ARP OFF is v9.6 exactly: note events go straight through.
 */
#include "arp_links.h"      /* v9.5/v9.6 symbol addresses, written by the builder */

typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;

#define EXPORT __attribute__((used))

#define SINGLETON 0x80249800u
#define CLONE_BASE 0x8353A310u
#define VOICE_SIZE 0x1B0u
#define PAGE (*(volatile short *)0x80245880u)
#define SG_PAGE 0x1E
#define NV 4
#define MAXN 16
#define MIDI_SRC 0x100
#define LO_NOTE (-36)
#define HI_NOTE 48

typedef void (*start_fn)(volatile u8 *voice, int on);
typedef void (*voice_fn)(volatile u8 *voice);
typedef int (*void_fn)(void);
typedef int (*tempo_fn)(u32 object);
typedef int (*pad_tempo_fn)(int pad);
typedef int (*param_fn)(u32 object, int parameter, int index);
typedef void (*note_fn)(u32 self, int note, int vel, int r3, int source);
typedef void (*midi_fn)(u32 self, int note, int vel, int r3);
#define STOCK_START ((start_fn)0x800D0669u)       /* on: start the note in +0x5c; off: stock release */
#define STOCK_RESET ((voice_fn)0x80132341u)
#define DISPATCH_OFF ((void_fn)0x800DED09u)
#define DISPATCH_ON ((void_fn)0x800DECF9u)
#define RELEASE_ALL ((void_fn)0x0001FE01u)        /* the all-stop (v4 release engine) */
#define TEMPO_GET ((tempo_fn)0x800D5641u)         /* the SP's current-tempo function (BPM x100) */
#define NOTE_EVENT ((note_fn)(NOTE_EVENT_ADDRESS | 1))
#define MIDI_EVENT ((midi_fn)(MIDI_EVENT_ADDRESS | 1))
#define ENV_QUICK ((volatile u8 *)ENV_QUICK_ADDRESS)
#define ENV_FAST ((volatile u8 *)ENV_FAST_ADDRESS)

enum { OFF, UP, DOWN, UPDOWN, RAND, ORDER, CHORD, MODES };
#define RATES 7

/* ---- settings (VALUE menu) ---- */
EXPORT volatile int arp_mode;                    /* OFF */
EXPORT volatile int arp_rate = 4;                /* 1/16 */
EXPORT volatile int arp_oct;                     /* 0 */
EXPORT volatile int arp_hold;                    /* OFF */
EXPORT volatile int arp_bpm100 = 12000;          /* until the screen reads the real tempo */
EXPORT volatile u8 arp_dirty;                    /* a value changed: redraw the screen */

/* ---- held notes, in press order; changed by the pad/MIDI tasks with dispatch off ---- */
EXPORT volatile int held_count, down_count;
EXPORT volatile signed char held_note[MAXN];
EXPORT volatile u8 held_vel[MAXN], held_down[MAXN];
EXPORT volatile u16 held_src[MAXN];

/* ---- engine state (audio task only) ---- */
EXPORT volatile int arp_run;                     /* playing */
EXPORT volatile float arp_pos, arp_step;         /* samples into the step, step length */
EXPORT volatile int arp_index, arp_last;
EXPORT volatile u8 arp_gated[NV];                /* started by this step, not released yet */
EXPORT volatile u8 arp_pending[NV];              /* fading out, then this note starts */
EXPORT volatile u8 arp_freed[NV];                /* faded early so the next step finds it free */
EXPORT volatile u8 arp_prepared;                 /* this step's voices for the next one are freed */
EXPORT volatile signed char arp_pend_note[NV];
EXPORT volatile u8 arp_pend_vel[NV];
EXPORT volatile u32 arp_age[NV], arp_serial, arp_rng = 0x2545F491u;
EXPORT volatile u32 arp_steps;                   /* steps played (tests, debugging) */

static volatile u8 *voice(int i) {
    return (volatile u8 *)(i ? CLONE_BASE + (u32)(i - 1)*VOICE_SIZE : SINGLETON);
}
static int clamp(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }
static void copy(char *out, const char *s) { do { *out++ = *s; } while (*s++); }

/* ---- held-note list (pad and MIDI tasks) ---- */

static void remove_at(int k) {
    for (int j = k; j + 1 < held_count; j++) {
        held_note[j] = held_note[j + 1];
        held_vel[j] = held_vel[j + 1];
        held_down[j] = held_down[j + 1];
        held_src[j] = held_src[j + 1];
    }
    held_count = held_count - 1;
}

static void key_on(int src, int note, int vel) {
    if ((u32)(note - LO_NOTE) > (u32)(HI_NOTE - LO_NOTE)) return;   /* stock ignores these too */
    DISPATCH_OFF();
    if (arp_hold && down_count == 0) held_count = 0;      /* latched chord: start a new one */
    int k = 0;
    while (k < held_count && !(held_src[k] == src && held_note[k] == note)) k++;
    if (k == held_count) {
        if (held_count < MAXN) {
            held_src[k] = (u16)src;
            held_note[k] = (signed char)note;
            held_down[k] = 0;
            held_count = held_count + 1;
        } else k = -1;
    }
    if (k >= 0) {
        held_vel[k] = (u8)vel;
        if (!held_down[k]) { held_down[k] = 1; down_count = down_count + 1; }
    }
    DISPATCH_ON();
}

static void key_off(int src, int note) {
    DISPATCH_OFF();
    int k = -1;
    for (int j = 0; j < held_count; j++)                   /* the exact key first */
        if (held_down[j] && held_src[j] == src && held_note[j] == note) { k = j; break; }
    if (k < 0 && src < 16)                                 /* a pad let go after OCT/ROOT moved */
        for (int j = 0; j < held_count; j++)
            if (held_down[j] && held_src[j] == src) { k = j; break; }
    if (k >= 0) {
        held_down[k] = 0;
        down_count = down_count - 1;
        if (!arp_hold) remove_at(k);
    }
    DISPATCH_ON();
}

EXPORT volatile int arp_tempo_raw;               /* last value the SP's tempo function reported */
EXPORT volatile int arp_tempo_bank;              /* the bank it was asked about, -1 = none */

/* Read the active sample bank with project parameter 0, as the normal
 * TEMPO SEL screen does at 0x80105078. The pad-selection dialog's bank
 * (0x82dffc88 + 0x2bc, parameter 0x7a) is independent and can remain on bank A
 * at 90 BPM while the active bank changes. v9.9.2 incorrectly used that field.
 * Then use the stock sample tempo resolver for the active bank's first pad;
 * its PROJECT/BANK, override, external MIDI clock and pattern rules stay intact. */
#define PAD_TEMPO ((pad_tempo_fn)0x80047DD1u)
#define PROJECT_GET ((param_fn)0x800DDA39u)
#define PROJECT_OBJECT 0x82E009D0u
#define BANKS 10

/* Fallback, for a bank number out of range (never seen): the SP's tempo
 * function (0x800d5640) on a stand-in object. +0x101 set would return that
 * object's own running tempo (+0x7c) and +0x20c < 1 the REC BPM setting (what
 * the plain call says in the Sound Generator: v9.9's fixed 90); with neither it
 * returns the project/bank tempo, the bank taken from the last selected pad
 * (v9.9.1: 90 in BANK mode when that pad sat in another bank). */
EXPORT volatile u32 arp_tempo_probe[0x210/4];

static void refresh_tempo(void) {
    int bank = PROJECT_GET(PROJECT_OBJECT, 0, -1), t;
    if ((u32)bank < BANKS) t = PAD_TEMPO(bank*16);
    else {
        volatile u8 *probe = (volatile u8 *)arp_tempo_probe;
        probe[0x101] = 0;
        *(volatile int *)(probe + 0x20C) = 1;
        t = TEMPO_GET((u32)probe);
        bank = -1;
    }
    arp_tempo_bank = bank;
    arp_tempo_raw = t;
    if (t >= 2000 && t <= 30000) arp_bpm100 = t;
}

/* NOTE_ENGINE (pads) and the MIDI-in dispatcher come here first. */
EXPORT void arp_note_event(u32 self, int note, int vel, int r3, int source) {
    if (!arp_mode) { NOTE_EVENT(self, note, vel, r3, source); return; }
    if (vel) { refresh_tempo(); key_on(source & 0xFF, note, vel); }
    else key_off(source & 0xFF, note);
}

EXPORT void arp_midi_event(u32 self, int note, int vel, int r3) {
    if (!arp_mode) { MIDI_EVENT(self, note, vel, r3); return; }
    if ((u32)note < 12) return;                            /* as the stock MIDI note function */
    if (vel) { refresh_tempo(); key_on(MIDI_SRC, note - 48, vel); }
    else key_off(MIDI_SRC, note - 48);
}

/* ---- the VALUE menu: items 6..10 ---- */

static const char *const ITEM_NAMES[5] = {"ARP", "RATE", "A.OCT", "HOLD", "BPM"};
static const char *const MODE_NAMES[MODES] = {"OFF", "UP", "DOWN", "UP&DN", "RAND", "ORDER", "CHORD"};
static const char *const RATE_NAMES[RATES] = {"1/4", "1/4T", "1/8", "1/8T", "1/16", "1/16T", "1/32"};

EXPORT void arp_item_name(int item, char *out) {
    copy(out, ITEM_NAMES[clamp(item - 6, 0, 4)]);
}

/* Unsigned decimal, at least `width` digits. Returns the end of the string. */
static char *number(char *out, u32 v, int width) {
    char tmp[12];
    int n = 0;
    do { tmp[n++] = (char)('0' + v % 10); v /= 10; } while (v || n < width);
    while (n) *out++ = tmp[--n];
    *out = 0;
    return out;
}

EXPORT void arp_item_text(int item, char *out) {
    switch (item) {
    case 6: copy(out, MODE_NAMES[clamp(arp_mode, 0, MODES - 1)]); break;
    case 7: copy(out, RATE_NAMES[clamp(arp_rate, 0, RATES - 1)]); break;
    case 8: {                                              /* as OCT: "+1", "0", "-1" */
        int o = clamp(arp_oct, -3, 3);
        if (o) { out[0] = o > 0 ? '+' : '-'; out[1] = (char)('0' + (o > 0 ? o : -o)); out[2] = 0; }
        else { out[0] = '0'; out[1] = 0; }
        break;
    }
    case 9: copy(out, arp_hold ? "ON" : "OFF"); break;
    default: {                                             /* BPM: what the SP's tempo function says now */
        refresh_tempo();
        int t = arp_tempo_raw;
        if (t >= 2000 && t <= 30000) {                     /* in use by the arp: "140.0" */
            char *end = number(out, (u32)t/100, 1);
            end[0] = '.';
            number(end + 1, (u32)(t % 100)/10, 1);
        } else {                                           /* not a tempo the arp accepts: raw */
            out[0] = '!';
            if (t < 0) { out[1] = '-'; number(out + 2, (u32)-t, 1); }
            else number(out + 1, (u32)t, 1);
        }
        break;
    }
    }
}

EXPORT void arp_item_step(int item, int dir) {
    switch (item) {
    case 6: {
        int m = clamp(arp_mode + dir, 0, MODES - 1);
        if (m == arp_mode) break;
        /* Notes played straight to the voices would never get their note-off
         * once the arp takes the events: stop them when the arp comes on. */
        if (!arp_mode) RELEASE_ALL();
        DISPATCH_OFF();
        if (!arp_mode || !m) { held_count = 0; down_count = 0; }
        arp_mode = m;
        DISPATCH_ON();
        refresh_tempo();
        break;
    }
    case 7: arp_rate = clamp(arp_rate + dir, 0, RATES - 1); break;
    case 8: arp_oct = clamp(arp_oct + dir, -3, 3); break;
    case 9: {
        int h = clamp(arp_hold + dir, 0, 1);
        if (h == arp_hold) break;
        DISPATCH_OFF();
        arp_hold = h;
        if (!h)                                            /* unlatch: keep only keys still down */
            for (int k = held_count - 1; k >= 0; k--)
                if (!held_down[k]) remove_at(k);
        DISPATCH_ON();
        break;
    }
    default: break;
    }
    arp_dirty = 1;
}

/* The SG screen's update: read the tempo, and redraw after a menu change. */
EXPORT int arp_poll(void) {
    refresh_tempo();
    if (!arp_dirty) return 0;
    arp_dirty = 0;
    return 1;
}

/* ---- the engine (audio task, from the render loop) ---- */

static float step_samples(void) {
    static const float beats[RATES] = {1.0f, 2.0f/3, 0.5f, 1.0f/3, 0.25f, 1.0f/6, 0.125f};
    int bpm = arp_bpm100;
    if (bpm < 2000 || bpm > 30000) bpm = 12000;
    return 288000000.0f*beats[clamp(arp_rate, 0, RATES - 1)]/(float)bpm;   /* 48 kHz */
}

/* A free voice takes voice 0's settings (as v9.5's take()) and starts the note. */
static void start_voice(int i, int note, int vel) {
    volatile u8 *v = voice(i);
    ENV_QUICK[i] = 0;
    arp_freed[i] = 0;
    if (i) {
        volatile u32 *dst = (volatile u32 *)v, *src = (volatile u32 *)SINGLETON;
        *(volatile u16 *)v = 0;
        for (int w = 1; w < 108; w++) dst[w] = src[w];
        dst[0] = src[0] & 0xFFFF0000u;
    }
    if (v[0x70] != v[0x71]) v[0x70] = v[0x71];
    STOCK_RESET(v);
    *(volatile int *)(v + 0x5C) = note;
    *(volatile int *)(v + 0x74) = vel*100;
    *(volatile int *)(v + 0x198) = vel*100;
    *(volatile int *)(v + 0x19C) = note;                   /* pad lights follow the arp */
    *(volatile int *)(v + 0x1A0) = 1000;
    *(volatile int *)(v + 0x1AC) = -1;                     /* not a pad: pad note-offs never match */
    v[0x194] = 0xFF;                                       /* nor MIDI note-offs */
    STOCK_START(v, 1);
    arp_gated[i] = 1;
    arp_age[i] = ++arp_serial;
}

/* A voice the next step can start on at once (a voice v9.5's stealing has
 * reserved is skipped; the arp's own early-freed voices are fine). */
static int usable(int i) {
    return !voice(i)[0] && !arp_pending[i] && (!ENV_QUICK[i] || arp_freed[i]);
}

static void trigger(int note, int vel) {
    for (int i = 0; i < NV; i++)
        if (usable(i)) { start_voice(i, note, vel); return; }
    int best = -1;                                         /* else the oldest released voice, */
    for (int pass = 0; pass < 2 && best < 0; pass++)       /* then the oldest still gated one */
        for (int i = 0; i < NV; i++) {
            volatile u8 *v = voice(i);
            if (arp_pending[i] || !v[0] || (pass ? !v[1] : v[1])) continue;
            if (best < 0 || arp_age[i] < arp_age[best]) best = i;
        }
    if (best < 0) return;
    volatile u8 *v = voice(best);
    if (!v[1]) { *(volatile int *)(v + 0x4C) = -200; v[1] = 1; }
    ENV_FAST[best] = 1;                                    /* 2.8 ms fade, as a v9.5 steal */
    ENV_QUICK[best] = 1;
    arp_gated[best] = 0;
    arp_pending[best] = 1;
    arp_pend_note[best] = (signed char)note;
    arp_pend_vel[best] = (u8)vel;
}

static void start_pending(void) {
    for (int i = 0; i < NV; i++)
        if (arp_pending[i] && !voice(i)[0]) {
            arp_pending[i] = 0;
            start_voice(i, arp_pend_note[i], arp_pend_vel[i]);
        }
}

static void close_gate(void) {
    for (int i = 0; i < NV; i++)
        if (arp_gated[i]) {
            volatile u8 *v = voice(i);
            if (v[0] && !v[1]) STOCK_START(v, 0);          /* stock release: ENV runs its release */
            arp_gated[i] = 0;
        }
}

/* Half a step ahead: make sure the next step finds `need` voices free, by
 * starting the quick fade on the oldest release tails now rather than making
 * the next step wait for it (which would put it ~4 ms behind the grid). */
static void prepare(int need) {
    int have = 0;
    for (int i = 0; i < NV; i++)
        if (usable(i) || arp_freed[i]) have++;
    while (have < need) {
        int best = -1;
        for (int i = 0; i < NV; i++) {
            volatile u8 *v = voice(i);
            if (v[0] && v[1] && !arp_gated[i] && !arp_pending[i] && !arp_freed[i] &&
                (best < 0 || arp_age[i] < arp_age[best])) best = i;
        }
        if (best < 0) return;
        ENV_FAST[best] = 1;
        ENV_QUICK[best] = 1;
        arp_freed[best] = 1;
        have++;
    }
}

static void stop(void) {
    close_gate();
    for (int i = 0; i < NV; i++)
        if (arp_pending[i] || arp_freed[i]) {              /* fading for us: let it finish */
            arp_pending[i] = 0;                            /* normally, and free it for */
            arp_freed[i] = 0;                              /* v9.5's claim() again */
            ENV_QUICK[i] = 0;
        }
    arp_prepared = 0;
    arp_run = 0;
}

static u32 rnd(void) {
    u32 x = arp_rng;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    arp_rng = x;
    return x;
}

/* The notes to step through: held notes (sorted, or in press order), then
 * their copies A.OCT octaves away, lowest octave first. */
static int build(int *notes, int *vels, int *base, int *bvels, int *nbase) {
    int nb = 0;
    for (int k = 0; k < held_count && k < MAXN; k++) {
        int n = held_note[k], v = held_vel[k], j = nb;
        if (arp_mode != ORDER) {
            for (int d = 0; d < nb; d++) if (base[d] == n) { j = -1; break; }
            if (j < 0) continue;
            while (j > 0 && base[j - 1] > n) { base[j] = base[j - 1]; bvels[j] = bvels[j - 1]; j--; }
        }
        base[j] = n;
        bvels[j] = v;
        nb++;
    }
    int lo = arp_oct < 0 ? arp_oct : 0, hi = arp_oct > 0 ? arp_oct : 0, count = 0;
    for (int o = lo; o <= hi; o++)
        for (int k = 0; k < nb; k++) {
            int n = base[k] + 12*o;
            if (n >= LO_NOTE && n <= HI_NOTE) { notes[count] = n; vels[count] = bvels[k]; count++; }
        }
    *nbase = nb;
    return count;
}

/* Scratch for play_step: static, not on the audio task's stack (only that task uses them). */
static int notes[MAXN*7], vels[MAXN*7], base[MAXN], bvels[MAXN];

static void play_step(void) {
    int nb;
    int n = build(notes, vels, base, bvels, &nb);
    if (n <= 0) return;
    int step = arp_index++;
    arp_steps++;
    int mode = arp_mode, k;
    if (mode == CHORD) {                                   /* the chord, one octave per step */
        int lo = arp_oct < 0 ? arp_oct : 0, hi = arp_oct > 0 ? arp_oct : 0;
        int o = lo + step % (hi - lo + 1), played = 0;
        for (k = 0; k < nb && played < NV; k++) {
            int x = base[k] + 12*o;
            if (x >= LO_NOTE && x <= HI_NOTE) { trigger(x, bvels[k]); played++; }
        }
        return;
    }
    if (mode == DOWN) k = n - 1 - step % n;
    else if (mode == UPDOWN) {
        int period = n > 1 ? 2*n - 2 : 1, m = step % period;
        k = m < n ? m : period - m;
    } else if (mode == RAND) {
        k = (int)(rnd() % (u32)n);
        if (n > 1 && k == arp_last) k = (k + 1) % n;
    } else k = step % n;                                   /* UP, ORDER */
    arp_last = k;
    trigger(notes[k], vels[k]);
}

/* Every render block (64 frames), before the voices render. */
EXPORT void arp_tick(int frames) {
    if (arp_mode && PAGE != SG_PAGE && held_count) {
        /* Off the Sound Generator page the pads and keys belong to other screens:
         * their note-offs never reach the arp. Forget the notes (the UI tasks
         * change the list with dispatch off, so this cannot cut into an update). */
        held_count = 0;
        down_count = 0;
    }
    if (!arp_mode || PAGE != SG_PAGE || held_count <= 0) {
        if (arp_run) stop();
        return;
    }
    start_pending();
    if (!arp_run) {                                        /* first key: play at once */
        arp_run = 1;
        arp_index = 0;
        arp_last = -1;
        arp_step = step_samples();
        arp_pos = arp_step;
    } else arp_pos = arp_pos + (float)frames;
    if (arp_pos >= arp_step*0.5f) {                        /* gate: half a step */
        close_gate();
        if (!arp_prepared) {
            int need = 1;
            if (arp_mode == CHORD) {                       /* distinct held notes, up to four */
                need = 0;
                for (int k = 0; k < held_count && need < NV; k++) {
                    int dup = 0;
                    for (int j = 0; j < k; j++) if (held_note[j] == held_note[k]) dup = 1;
                    need += !dup;
                }
            }
            prepare(need);
            arp_prepared = 1;
        }
    }
    if (arp_pos >= arp_step) {
        float rest = arp_pos - arp_step;
        arp_step = step_samples();                         /* tempo and RATE apply per step */
        arp_pos = rest < arp_step ? rest : 0;
        close_gate();
        play_step();
        arp_prepared = 0;
    }
}
