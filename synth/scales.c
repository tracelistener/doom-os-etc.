/* v9.6 scale-only extension. Audio/envelope/voice code stays exact v9.5.
 * Only Sound Generator (page 0x1e) walks added scales. Off that page,
 * extended SCALE values act as Chrom; original choices remain exact v9.5.
 */
#include "scale_tables.h"
typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;
#define EXPORT __attribute__((used))
#define KB 0x80591C48u
#define PAGE (*(volatile short *)0x80245880u)
#define SG 0x1e
#define PAD ((const int *)0x801A9E60u)
typedef int (*pad_fn)(int);
#define LEGACY_NOTE ((pad_fn)LEGACY_NOTE_ADDRESS)
#define LEGACY_IN ((pad_fn)LEGACY_IN_ADDRESS)
#define LEGACY_REL ((pad_fn)LEGACY_REL_ADDRESS)
static int kb(int off) { return *(volatile int *)(KB+off); }
static int mod12(int n) { int d=n%12; return d<0 ? d+12 : d; }

EXPORT int scale_note(int pad) {
    if ((u32)pad>=16) return 0; /* callers normally supply physical pads */
    int s=kb(12);
    if (PAGE!=SG || s<7 || s>=SCALE_COUNT) return LEGACY_NOTE(pad);
    u16 mask=scale_masks[s];
    int step=PAD[pad], root=kb(16), anchor=12*kb(4)-kb(8);
    while (!(mask & (1u<<mod12(anchor-root)))) ++anchor;
    int n=anchor;
    for (;step>0;--step) do ++n; while (!(mask & (1u<<mod12(n-root))));
    for (;step<0;++step) do --n; while (!(mask & (1u<<mod12(n-root))));
    return n;
}
EXPORT int scale_in(int pad) {
    if ((u32)pad>=16) return 0;
    return LEGACY_IN(pad); /* SG: every pad; elsewhere: only stock masks */
}
EXPORT int scale_rel(int pad) {
    if ((u32)pad>=16) return 0;
    int s=kb(12);
    if (PAGE!=SG || s<7 || s>=SCALE_COUNT) return LEGACY_REL(pad);
    return scale_note(pad)-12*kb(4)-kb(16);
}
EXPORT void scale_range(int *lo,int *hi) {
    *lo=0; *hi=PAGE==SG ? SCALE_COUNT-1 : 6;
}
/* Original label case handles IDs 0..6 unchanged, and rejects invalid values.
 * The bridge sends only added, valid indices here. Eight-character labels.
 */
EXPORT void scale_name(u32 s,char *out) {
    const char *name=PAGE==SG ? scale_names[s] : "Chrom";
    do { *out++=*name; } while (*name++);
}
