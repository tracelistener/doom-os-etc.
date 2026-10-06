"""Experimental v4: polyphonic UI, lifecycle, transposition and chord export.

Build only from exact stock 5.52, preserving v3 files. Reserve a larger prefix
of the OS pool for new code and independent export states. Append a relocated
scatter table with one copy row AFTER zero-initialization, rather than using
unproven empty resource bytes. APP0 is never changed.
"""
from __future__ import annotations

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import patch_poly as v2
import patch_poly_v3 as v3

STOCK = v2.STOCK
OUTPUT = os.path.join(v2.ROOT, "firmware", "poly-test-v4", "SP404MKII_APP1.bin")
PATCHED_SHA256 = "47100c173dec7a87d42d6b8022f1319332b5f293f70b70d1eb32d67ff131c35d"
NEW_POOL_BASE = 0x8353D82C
CODE_BASE, CODE_SIZE = 0x8353B000, 0x1000
EXPORT_BASE, EXPORT_COUNT = 0x8353C000, 0x8353C700
VOICE_SIZE = 0x1B0

NOTE_ENGINE = CODE_BASE
PARAM_ENGINE = CODE_BASE + 0x180
FREQ_ENGINE = CODE_BASE + 0x240
TUNE_RESET = CODE_BASE + 0x400
RELEASE_ENGINE = CODE_BASE + 0x440
CAPTURE_INIT = CODE_BASE + 0x500
LED_MATCH = CODE_BASE + 0x550
CAPTURE = CODE_BASE + 0x600
EXPORT_ENGINE = CODE_BASE + 0x780
EXPORT_SAMPLE = CODE_BASE + 0xA00
OSC_GATE = CODE_BASE + 0xB00
EXPORT_STOCK = CODE_BASE + 0xB40
ANY_ACTIVE = CODE_BASE + 0xBC0

def asm(source, address):
    return v2.assemble(source, address)

def load(reg, value):
    return f"movw {reg}, #{value & 0xffff}\nmovt {reg}, #{value >> 16}\n"

def call(target):
    return load("r12", target | 1) + "blx r12\n"

def jump(target):
    return load("r12", target | 1) + "bx r12\n"

def next_voice(reg, index, label):
    return f"""
        adds {index}, #1
        cmp {index}, #4
        beq {label}
        cmp {index}, #1
        bne {label}_add
        {load(reg, v2.CLONE_BASE)}
        b {label}_ready
    {label}_add:
        add.w {reg}, {reg}, #0x1b0
    {label}_ready:
    """

def note_source():
    # Clear an older export snapshot only on a new keyboard note-on. Note-off
    # matches the original event note/source, not the transposed sounding pitch.
    source = f"""
        cmp r2, #0
        beq note_entry
        {load('r0', EXPORT_COUNT)}
        mov.w r12, #0
        str.w r12, [r0]
    note_entry:
    """ + v2.NOTE_ASM
    # Stock release begins at -200 (~2x gain); normalize to a non-boosted fade.
    old = """    add sp, #8

note_off_next:"""
    new = """    add sp, #8
    ldrb r0, [r8, #1]
    cbz r0, note_off_next
    movs r0, #0
    str r0, [r8, #0x4c]

note_off_next:"""
    if old not in source:
        raise ValueError("v2 note-off source changed")
    return source.replace(old, new)

def param_source():
    # v3 broadcasting remains intact; FREQ gets interval-preserving semantics.
    return f"""
        cmp r1, #0x7c
        bne normal_param
        {jump(FREQ_ENGINE)}
    normal_param:
    """ + v3.PARAM_ASM

def freq_source():
    # Find a common delta bounded by all held notes' stock range [-36, 48].
    # Leave +19c intact: it identifies the original pad/MIDI note for note-off.
    return f"""
        push.w {{r4-r10, lr}}
        mov r4, r2
        mov r5, r3
        {load('r7', v2.SINGLETON)}
        ldr r0, [r7, #0x5c]
        sub.w r6, r4, r0
        movs r8, #0
    bound_loop:
        ldrb r0, [r7]
        cbz r0, bound_next
        ldrb r0, [r7, #1]
        cbnz r0, bound_next
        ldr r1, [r7, #0x5c]
        mvn r0, #35
        subs r0, r0, r1
        cmp r6, r0
        it lt
        movlt r6, r0
        rsb.w r0, r1, #48
        cmp r6, r0
        it gt
        movgt r6, r0
    bound_next:
        {next_voice('r7', 'r8', 'bound_done')}
        b bound_loop
    bound_done:
        {load('r7', v2.SINGLETON)}
        movs r8, #0
    transpose_loop:
        ldrb r0, [r7]
        cbz r0, transpose_idle
        ldrb r0, [r7, #1]
        cbnz r0, transpose_next
        ldr r2, [r7, #0x5c]
        add r2, r6
        b transpose_apply
    transpose_idle:
        cmp r8, #0
        bne transpose_next
        mov r2, r4
    transpose_apply:
        mov r0, r7
        movs r1, #0x7c
        mov r3, r5
        {call(v3.SETTER)}
    transpose_next:
        {next_voice('r7', 'r8', 'transpose_done')}
        b transpose_loop
    transpose_done:
        pop.w {{r4-r10, pc}}
    """

def tune_source():
    return f"""
        push {{r4, lr}}
        movs r1, #0x81
        movw r2, #4400
        movs r3, #0
        {call(PARAM_ENGINE)}
        pop {{r4, pc}}
    """

def release_source():
    # All stop paths are idempotent. Clear preview mode and source matching for
    # every state, including idle voice zero when clones alone are sounding.
    return f"""
        push {{r4-r6, lr}}
        {load('r4', v2.SINGLETON)}
        movs r5, #0
    release_loop:
        movs r0, #0
        strb r0, [r4, #2]
        ldrb r1, [r4]
        cbz r1, release_next
        ldrb r1, [r4, #1]
        cbnz r1, release_next
        str r0, [r4, #0x4c]
        movs r0, #1
        strb r0, [r4, #1]
        movs r0, #0xff
        strb.w r0, [r4, #0x194]
        ldr r0, [r4, #0x74]
        str.w r0, [r4, #0x198]
        movw r0, #1000
        str.w r0, [r4, #0x19c]
        mov.w r0, #-1
        str.w r0, [r4, #0x1ac]
    release_next:
        {next_voice('r4', 'r5', 'release_done')}
        b release_loop
    release_done:
        {load('r0', 0x802496F0)}
        movw r1, #0xffff
        strh r1, [r0]
        pop {{r4-r6, pc}}
    """

def capture_init_source():
    return f"""
        push {{r4, lr}}
        {call(CAPTURE)}
        {call(v2.INIT_ALL_WRAPPER)}
        pop {{r4, pc}}
    """

def led_source():
    # r0 physical pad [0,15], r1 its untransposed note. Return r1 for a held
    # matching source (or pitch for a non-pad source); otherwise stock sentinel.
    return f"""
        push {{r4-r6, lr}}
        mov r4, r0
        mov r5, r1
        {load('r2', v2.SINGLETON)}
        movs r6, #0
    led_loop:
        ldrb r0, [r2]
        cbz r0, led_next
        ldrb r0, [r2, #1]
        cbnz r0, led_next
        ldr.w r0, [r2, #0x1ac]
        cmp r0, r4
        beq led_found
        cmp r0, #15
        bls led_next
        ldr.w r0, [r2, #0x19c]
        cmp r0, r5
        beq led_found
    led_next:
        {next_voice('r2', 'r6', 'led_none')}
        b led_loop
    led_none:
        movw r0, #1000
        pop {{r4-r6, pc}}
    led_found:
        mov r0, r5
        pop {{r4-r6, pc}}
    """

def capture_source():
    return f"""
        push.w {{r4-r8, lr}}
        {load('r4', v2.SINGLETON)}
        {load('r5', EXPORT_BASE)}
        movs r6, #0
        movs r7, #0
    capture_loop:
        ldrb r0, [r4]
        cbz r0, capture_next
        ldrb r0, [r4, #1]
        cbnz r0, capture_next
        mov r0, r4
        mov r1, r5
        movs r2, #108
    copy_loop:
        ldr r3, [r0], #4
        str r3, [r1], #4
        subs r2, #1
        bne copy_loop
        adds r6, #1
        add.w r5, r5, #0x1b0
    capture_next:
        {next_voice('r4', 'r7', 'capture_done')}
        b capture_loop
    capture_done:
        {load('r0', EXPORT_COUNT)}
        str r6, [r0]
        mov r0, r6
        pop.w {{r4-r8, pc}}
    """

def export_source():
    return f"""
        push.w {{r4-r8, lr}}
        {load('r4', v2.SINGLETON)}
        {load('r5', EXPORT_BASE)}
        {load('r6', EXPORT_COUNT)}
        ldr r7, [r6]
        cbnz r7, export_have_snapshot
        {call(CAPTURE)}
        mov r7, r0
        cbnz r7, export_have_snapshot
        mov r0, r4
        mov r1, r5
        movs r2, #108
    export_copy_fallback:
        ldr r3, [r0], #4
        str r3, [r1], #4
        subs r2, #1
        bne export_copy_fallback
        movs r7, #1
    export_have_snapshot:
        str r7, [r6]
        ldr.w r0, [r4, #0x17c]
        str.w r0, [r5, #0x17c]
        ldr.w r0, [r4, #0x180]
        str.w r0, [r5, #0x180]
        ldr r0, [r4, #8]
        str r0, [r5, #8]
        movs r8, #0
    export_prepare:
        ldrb.w r0, [r4, #0x70]
        strb.w r0, [r5, #0x70]
        strb.w r0, [r5, #0x71]
        ldr r0, [r4, #0x48]
        str r0, [r5, #0x48]
        ldr r0, [r4, #0x60]
        str r0, [r5, #0x60]
        ldr r0, [r4, #0x10]
        str r0, [r5, #0x10]
        movs r0, #0
        strh r0, [r5]
        strb r0, [r5, #3]
        ldr.w r0, [r5, #0x198]
        str r0, [r5, #0x74]
        mov r0, r5
        movs r1, #1
        {call(0x800D0668)}
        adds r8, #1
        add.w r5, r5, #0x1b0
        cmp r8, r7
        blo export_prepare

        movs r0, #1
        strb r0, [r4, #3]
        {load('r0', EXPORT_BASE)}
        {call(EXPORT_STOCK)}
        mov r8, r0
        {load('r5', EXPORT_BASE)}
        ldr.w r0, [r5, #0x17c]
        str.w r0, [r4, #0x17c]
        movs r0, #0
        str r0, [r6]
        strb r0, [r4, #3]
        {load('r5', v2.CLONE_BASE)}
        strb r0, [r5, #3]
        strb.w r0, [r5, #0x1b3]
        strb.w r0, [r5, #0x363]
        mov r0, r8
        pop.w {{r4-r8, pc}}
    """

def sample_source():
    # Accumulate in 32 bits, then saturate ONCE. Pan/file channel handling stays
    # in the stock exporter, whose settings now apply to the entire chord.
    return f"""
        push.w {{r4-r8, lr}}
        {load('r4', EXPORT_BASE)}
        {load('r0', EXPORT_COUNT)}
        ldr r5, [r0]
        movs r6, #0
        cbz r5, sample_done
    sample_loop:
        mov r0, r4
        {call(0x80007700)}
        asrs r0, r0, #1
        add r6, r0
        add.w r4, r4, #0x1b0
        subs r5, #1
        bne sample_loop
    sample_done:
        ssat r0, #16, r6
        pop.w {{r4-r8, pc}}
    """

def oscillator_gate_source():
    # The stock exporter has one oscillator BL at 0x80132aee. Redirect only
    # that return address to the chord mixer. Other oscillator callers enter
    # the original function with its displaced (PC-independent) prologue.
    return f"""
        {load('r12', 0x80132AF3)}
        cmp lr, r12
        bne stock_oscillator
        {jump(EXPORT_SAMPLE)}
    stock_oscillator:
        push {{r4-r6, lr}}
        vpush {{d8-d12}}
        mov r4, r0
        ldrb.w r0, [r0, #0x70]
        {jump(0x8000770C)}
    """

def export_stock_source():
    return f"""
        push.w {{r4-r11, lr}}
        sub sp, #4
        vpush {{d8-d11}}
        sub sp, #8
        {jump(0x801327C4)}
    """

def any_active_source():
    return f"""
        {load('r1', v2.SINGLETON)}
        movs r2, #0
    active_loop:
        ldrb r0, [r1]
        cbnz r0, active_found
        {next_voice('r1', 'r2', 'active_none')}
        b active_loop
    active_none:
        movs r0, #0
    active_found:
        bx lr
    """

# Exact stock spans replaced by absolute calls, with continuation left intact.
RELEASE_SPANS = [(0x80053FF8, 0x8005401C), (0x800DD044, 0x800DD068),
                 (0x8015C654, 0x8015C676), (0x8015C9CA, 0x8015C9F8),
                 (0x8015CB68, 0x8015CB96)]
CODE_SLOTS = [(NOTE_ENGINE, PARAM_ENGINE, note_source),
              (PARAM_ENGINE, FREQ_ENGINE, param_source),
              (FREQ_ENGINE, TUNE_RESET, freq_source),
              (TUNE_RESET, RELEASE_ENGINE, tune_source),
              (RELEASE_ENGINE, CAPTURE_INIT, release_source),
              (CAPTURE_INIT, LED_MATCH, capture_init_source),
              (LED_MATCH, CAPTURE, led_source),
              (CAPTURE, EXPORT_ENGINE, capture_source),
              (EXPORT_ENGINE, EXPORT_SAMPLE, export_source),
              (EXPORT_SAMPLE, OSC_GATE, sample_source),
              (OSC_GATE, EXPORT_STOCK, oscillator_gate_source),
              (EXPORT_STOCK, ANY_ACTIVE, export_stock_source),
              (ANY_ACTIVE, CODE_BASE + CODE_SIZE, any_active_source)]

def build(stock):
    data = bytearray(v3.build(stock))
    if v2.sha256(data) != v3.PATCHED_SHA256:
        raise ValueError("v3 base mismatch")

    def patch_high(address, end, source):
        off, length = v2.new_high_file(address), end - address
        expected = stock[v2.old_high_file(address):v2.old_high_file(end)]
        v2.expect(data, off, expected.hex(), f"v4 hook {address:x}")
        blob = asm(source, address)
        if len(blob) > length or (length - len(blob)) % 2:
            raise ValueError(f"hook {address:x} exceeds its guarded span")
        data[off:off + length] = blob + b"\x00\xbf" * ((length - len(blob)) // 2)

    # Existing ITCM wrappers are now small veneers into the reserved code row.
    for old, target in [(v2.NOTE_WRAPPER, NOTE_ENGINE),
                        (v3.PARAM_WRAPPER, PARAM_ENGINE),
                        (v2.RELEASE_ALL_WRAPPER, RELEASE_ENGINE)]:
        blob = asm(jump(target), old)
        off = v3.itcm_file(old)
        data[off:off + len(blob)] = blob

    for start, end in RELEASE_SPANS:
        patch_high(start, end, call(RELEASE_ENGINE))
    patch_high(0x8015C684, 0x8015C696, call(TUNE_RESET))

    # Capture held voices before stock REC changes keyboard mode and clears them.
    init_off = v2.new_high_file(0x8015CA8E)
    init_v2 = v2.absolute_call(v2.INIT_ALL_WRAPPER | 1, 0x8015CA8E)
    v2.expect(data, init_off, init_v2.hex(), "v2 keyboard-off init hook")
    init_patch = asm(call(CAPTURE_INIT) + "nop", 0x8015CA8E)
    if len(init_patch) != 12:
        raise ValueError("unexpected capture/init hook length")
    data[init_off:init_off + 12] = init_patch

    # The preview/all-stop toggle must notice clone-only activity too. Preserve
    # stock preview-on behavior when idle, and the shared redraw block at c01c.
    # This span already contains the v2 all-stop hook. Guard the exact v3
    # bytes separately rather than pretending the whole span is stock.
    start, end = 0x8015BFEA, 0x8015C01C
    off = v2.new_high_file(start)
    expected = v3.build(stock)[off:v2.new_high_file(end)]
    v2.expect(data, off, expected.hex(), "v3 preview/stop toggle")
    blob = asm(load('r4', v2.SINGLETON) + call(ANY_ACTIVE) +
               "cmp r0, #0\nbeq preview_idle\n" + call(RELEASE_ENGINE) +
               "b preview_done\npreview_idle:\n" + jump(0x8015C138) +
               "preview_done:\n", start)
    if len(blob) > end - start or (end - start - len(blob)) % 2:
        raise ValueError("preview/stop hook exceeds its guarded span")
    data[off:off + end - start] = blob + b"\x00\xbf" * ((end - start - len(blob)) // 2)

    led_hook = ("mov r0, r4\nmov r1, r6\n" + call(LED_MATCH) +
                "mov r5, r0\nmov r0, r4\nbl #0x80030e98\n")
    patch_high(0x80028B98, 0x80028BAE, led_hook)
    # Entry trampolines avoid out-of-range Thumb BLs and do not use code caves.
    # Their displaced prologues contain no PC-relative instructions. Original
    # stock export setup, duration, pan, file I/O and finalization stay intact.
    for address, prologue in [
        (0x801327B8, "push.w {r4-r11, lr}\nsub sp, #4\nvpush {d8-d11}\nsub sp, #8"),
        (0x80007700, "push {r4-r6, lr}\nvpush {d8-d12}\nmov r4, r0\nldrb.w r0, [r0, #0x70]"),
    ]:
        blob = asm(prologue, address)
        v2.expect(stock, v2.old_high_file(address), blob.hex(), "displaced stock prologue")
    patch_high(0x801327B8, 0x801327C4, jump(EXPORT_ENGINE))
    patch_high(0x80007700, 0x8000770C, jump(OSC_GATE))

    # Grow owned storage, retaining the original OS pool's modulo-32 alignment.
    pool_off = v2.new_high_file(0x80002268)
    v2.expect(data, pool_off, "4af62c00", "v3 pool base")
    data[pool_off:pool_off + 4] = asm("movw r0, #0xd82c", 0x80002268)
    rel0, rel1 = struct.unpack_from("<2I", data, 0x80)
    rows = [list(struct.unpack_from("<4I", data, off))
            for off in range(0x80 + rel0, 0x80 + rel1, 16)]
    zi = [row for row in rows if row[1] == 0x802E7800]
    if len(zi) != 1 or zi[0][2] != 0x0325302C:
        raise ValueError("unexpected v3 zero region")
    zi[0][2] += NEW_POOL_BASE - v2.NEW_OS_POOL_BASE
    code = bytearray(CODE_SIZE)
    for start, end, factory in CODE_SLOTS:
        blob = asm(factory(), start)
        if start + len(blob) > end:
            raise ValueError(f"v4 code slot {start:x}: {len(blob):x} bytes overflow")
        code[start - CODE_BASE:start - CODE_BASE + len(blob)] = blob
    data.extend(b"\0" * (-len(data) % 16))
    code_file = len(data)
    data.extend(code)
    table_file = len(data)
    # Runs after the existing zero rows, so zero init cannot erase this code.
    rows.append([v2.FLASH + code_file, CODE_BASE, CODE_SIZE, rows[0][3]])
    for row in rows:
        data.extend(struct.pack("<4I", *row))
    struct.pack_into("<2I", data, 0x80, table_file - 0x80, len(data) - 0x80)
    return bytes(data)

def main():
    source = sys.argv[1] if len(sys.argv) > 1 else STOCK
    output = sys.argv[2] if len(sys.argv) > 2 else OUTPUT
    with open(source, "rb") as handle:
        image = build(handle.read())
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "wb") as handle:
        handle.write(image)
    print(f"Wrote {output}\nSize: {len(image)}\nSHA-256: {v2.sha256(image)}")

if __name__ == "__main__":
    main()
