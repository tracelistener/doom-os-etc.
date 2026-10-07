/* v9.6 control-path fixes. Original v9.5 audio/steal/envelope routines stay
 * in place. Addresses refer to the hash-guarded v9.5 symbols, not new state.
 */
#include "scale_tables.h"
typedef unsigned int u32;
typedef unsigned char u8;
#define EXPORT __attribute__((used))
#define SINGLETON 0x80249800u
#define CLONES 0x8353A310u
typedef void (*setter_fn)(volatile u8 *,int,int,int);
#define STOCK_SETTER ((setter_fn)0x80019E31u)
#define AGES ((volatile u32 *)ENV_AGE_ADDRESS)
#define DISPLAY (*(volatile u8 *)DISPLAY_SLOT_ADDRESS)
static volatile u8 *voice(int i) {
    return (volatile u8 *)(i ? CLONES+(u32)(i-1)*0x1b0u : SINGLETON);
}
static int pitch(volatile u8 *v) { return *(volatile int *)(v+0x5c); }
static int clamp(int v,int lo,int hi) { return v<lo ? lo : v>hi ? hi : v; }

EXPORT void fixed_freq_event(u32 self,int id,int value,int flags) {
    (void)self; (void)id;
    int low=0, high=0, held=-1;
    /* Intersect all allowed transpose intervals before changing ANY voice.
     * Unlike sequential clamping, no later voice can undo an earlier bound.
     */
    for (int i=0;i<4;i++) {
        volatile u8 *v=voice(i);
        if (v[0] && !v[1]) {
            int n=pitch(v), lo=-36-n, hi=48-n;
            if (held<0) { low=lo; high=hi; }
            else { if (lo>low) low=lo; if (hi<high) high=hi; }
            if (held<0 || AGES[i]>AGES[held]) held=i;
        }
    }
    if (held>=0 && low>high) return; /* over-wide MIDI chord: don't distort it */
    int delta=held<0 ? 0 : clamp(value-pitch(voice(DISPLAY&3)),low,high);
    for (int i=0;i<4;i++) {
        volatile u8 *v=voice(i);
        if (!v[0]) {
            if (!i) STOCK_SETTER(v,0x7c,clamp(value,-36,48),flags);
        } else if (!v[1]) STOCK_SETTER(v,0x7c,pitch(v)+delta,flags);
    }
    volatile u8 *d=voice(DISPLAY&3);
    if (!d[0] || d[1]) DISPLAY=held>=0 ? (u8)held : 0;
}
