"""Build a four-voice Sound Generator test image from stock 5.52 APP1.

This is intentionally a first-stage test:

* stock oscillators only (FM2 is not included);
* four simultaneous voices, with a fifth note dropped rather than stolen;
* fixed -6 dB level per voice;
* the original voice plus three cloned 0x1b0-byte state blocks.

The patch grows the existing ITCM copy region by 0x400 bytes and inserts the
dispatcher/render/lifecycle wrappers there. It reserves 0x520 bytes at the
front of the firmware's own 3 MiB OS memory pool by moving that pool's base
forward and extending the preceding zero-init scatter row by the same amount.
"""

from __future__ import annotations

import hashlib
import os
import struct
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VENDOR = os.path.join(ROOT, ".poly_vendor")
if VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)

try:
    from keystone import KS_ARCH_ARM, KS_MODE_LITTLE_ENDIAN, KS_MODE_THUMB, Ks
except ImportError as exc:  # pragma: no cover - setup failure, not patch logic
    raise SystemExit(
        "Keystone is required. Install it with:\n"
        "  python -m pip install keystone-engine --target .poly_vendor"
    ) from exc


FLASH = 0x60080000
STOCK_SHA256 = "4a3d67711e14dcc97d50249a4eee7dd6df0251a2f37757cbbe2556c233730d80"
PATCHED_SHA256 = "1d632cc5ce2b0847136bde7f1f61e1ab790ebba996dd0eda4ca52f5bd784163f"

STOCK = os.path.join(
    ROOT, "firmware", "v552", "sp404mk2_sys_v552", "SP404MKII_APP1.bin"
)
OUTPUT = os.path.join(ROOT, "firmware", "poly-test-v2", "SP404MKII_APP1.bin")

# The stock ITCM copy is file 0x000af0 -> runtime 0x00000400, size 0x1f800.
# Inserting 0x400 bytes at its end grows it exactly to the 0x00020000 ITCM edge.
INSERT_FILE = 0x202F0
INSERT_SIZE = 0x400
NEXT_PAYLOAD_FILE = 0x20300
ITCM_NEW_SIZE = 0x1FC00

NOTE_WRAPPER = 0x0001FC00
RENDER_WRAPPER = 0x0001FD80
RELEASE_ALL_WRAPPER = 0x0001FE00
INIT_ALL_WRAPPER = 0x0001FE80
GAIN_WRAPPER = 0x0001FF00

SINGLETON = 0x80249800
CLONE_BASE = 0x8353A310
OLD_OS_POOL_BASE = 0x8353A30C
NEW_OS_POOL_BASE = 0x8353A82C
OS_POOL_END = 0x8383A30C
RESERVED_SIZE = NEW_OS_POOL_BASE - OLD_OS_POOL_BASE  # 0x520


NOTE_ASM = r"""
    push.w {r4-r11, lr}
    sub sp, #4
    mov r4, r1
    mov r5, r2
    mov r6, r3
    ldr r7, [sp, #40]
    cmp r5, #0
    beq.w note_off

    movw r8, #0x9800
    movt r8, #0x8024
    movs r9, #0
find_free:
    ldrb r0, [r8]
    cmp r0, #0
    beq.w selected
    adds r9, #1
    cmp r9, #4
    beq.w done
    cmp r9, #1
    bne add_state
    movw r8, #0xa310
    movt r8, #0x8353
    b find_free
add_state:
    add.w r8, r8, #0x1b0
    b find_free

selected:
    movw r10, #0x9800
    movt r10, #0x8024
    cmp r8, r10
    beq reset_voice

    movs r0, #0
    strh r0, [r8]
    add.w r0, r10, #4
    add.w r1, r8, #4
    movs r2, #107
copy_loop:
    ldr r3, [r0], #4
    str r3, [r1], #4
    subs r2, #1
    bne copy_loop
    ldr r3, [r10]
    bfc r3, #0, #16
    str r3, [r8]

reset_voice:
    mov r0, r8
    movw r12, #0x2341
    movt r12, #0x8013
    blx r12

    sub sp, #8
    str r7, [sp]
    mov r0, r8
    mov r1, r4
    mov r2, r5
    mov r3, r6
    movw r12, #0x2429
    movt r12, #0x8013
    blx r12
    add sp, #8

    b done

note_off:
    movw r8, #0x9800
    movt r8, #0x8024
    movs r9, #0
note_off_loop:
    ldrb r0, [r8]
    cmp r0, #0
    beq note_off_next
    ldr.w r0, [r8, #0x19c]
    cmp r0, r4
    bne note_off_next
    ldr.w r0, [r8, #0x1ac]
    cmp r0, r7
    bne note_off_next

    sub sp, #8
    str r7, [sp]
    mov r0, r8
    mov r1, r4
    movs r2, #0
    mov r3, r6
    movw r12, #0x2429
    movt r12, #0x8013
    blx r12
    add sp, #8

note_off_next:
    adds r9, #1
    cmp r9, #4
    beq done
    cmp r9, #1
    bne note_off_add
    movw r8, #0xa310
    movt r8, #0x8353
    b note_off_loop
note_off_add:
    add.w r8, r8, #0x1b0
    b note_off_loop

done:
    add sp, #4
    pop.w {r4-r11, pc}
"""


RENDER_ASM = r"""
    push.w {r4-r8, lr}
    mov r4, r1
    mov r5, r2
    mov r6, r3

    movw r7, #0x9800
    movt r7, #0x8024
    mov r0, r7
    mov r1, r4
    mov r2, r5
    mov r3, r6
    bl #0x0001cb84

    movw r7, #0xa310
    movt r7, #0x8353
    movs r8, #3
render_clones:
    mov r0, r7
    mov r1, r4
    mov r2, r5
    mov r3, r6
    bl #0x0001cb84
    add.w r7, r7, #0x1b0
    subs r8, #1
    bne render_clones
    pop.w {r4-r8, pc}
"""


RELEASE_ALL_ASM = r"""
    push.w {r4-r6, lr}
    movw r4, #0x9800
    movt r4, #0x8024
    movs r5, #0
release_loop:
    mvn r0, #0xc7
    str r0, [r4, #0x4c]
    movs r0, #1
    strb r0, [r4, #1]
    movs r0, #0xff
    strb.w r0, [r4, #0x194]
    ldr r0, [r4, #0x74]
    str.w r0, [r4, #0x198]
    movw r0, #1000
    str.w r0, [r4, #0x19c]

    adds r5, #1
    cmp r5, #4
    beq release_done
    cmp r5, #1
    bne release_add
    movw r4, #0xa310
    movt r4, #0x8353
    b release_loop
release_add:
    add.w r4, r4, #0x1b0
    b release_loop
release_done:
    movw r0, #0x96f0
    movt r0, #0x8024
    mov.w r1, #-1
    strh r1, [r0]
    pop.w {r4-r6, pc}
"""


INIT_ALL_ASM = r"""
    push {r4, lr}
    movw r4, #0x9800
    movt r4, #0x8024
    mov r0, r4
    movw r12, #0x2341
    movt r12, #0x8013
    blx r12

    movw r4, #0xa310
    movt r4, #0x8353
    movs r0, #0
    strh r0, [r4]
    add.w r4, r4, #0x1b0
    strh r0, [r4]
    add.w r4, r4, #0x1b0
    strh r0, [r4]
    pop {r4, pc}
"""


# Keep the LEVEL/current/target fields pristine: the stock pad path reads
# target / 100 back as its next velocity. Attenuate the signed audio sample
# once, between the oscillator and additive bus mix, instead.
GAIN_ASM = r"""
    push {r4, lr}
    bl #0x0001007a
    asrs r0, r0, #1
    pop {r4, pc}
"""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def assemble(source: str, address: int) -> bytes:
    ks = Ks(KS_ARCH_ARM, KS_MODE_THUMB | KS_MODE_LITTLE_ENDIAN)
    encoded, _ = ks.asm(source, address)
    return bytes(encoded)


def expect(data: bytes | bytearray, offset: int, expected_hex: str, label: str) -> None:
    expected = bytes.fromhex(expected_hex)
    actual = bytes(data[offset : offset + len(expected)])
    if actual != expected:
        raise ValueError(
            f"{label}: original bytes differ at file 0x{offset:x}: "
            f"expected {expected.hex()}, got {actual.hex()}"
        )


def old_high_file(runtime: int) -> int:
    return 0x21AC0 + (runtime - 0x80000000)


def new_high_file(runtime: int) -> int:
    return old_high_file(runtime) + INSERT_SIZE


def absolute_call(target: int, address: int, trailing_nop: bool = True) -> bytes:
    source = f"""
        movw r0, #{target & 0xffff}
        movt r0, #{target >> 16}
        blx r0
    """
    if trailing_nop:
        source += "\n        nop\n"
    return assemble(source, address)


def build(stock: bytes) -> bytes:
    if sha256(stock) != STOCK_SHA256:
        raise ValueError("Input is not the exact stock Roland 5.52 APP1 image")

    # Original-byte guards, all evaluated before the file is expanded.
    expect(stock, 0x1C766 + 0x6F0, "00f00dfa", "live render call")
    expect(stock, 0x1CBE8 + 0x6F0, "f3f747fa", "renderer oscillator call")
    expect(stock, old_high_file(0x80002268), "4af20c30", "OS pool base")
    expect(
        stock,
        old_high_file(0x8015C122),
        "49f60000c8f22400002331460094d6f77af9",
        "note-on call block",
    )
    expect(
        stock,
        old_high_file(0x8015C4F0),
        "49f60000c8f22400d5f796ff",
        "note-off call block",
    )
    expect(
        stock,
        old_high_file(0x8015BFFA),
        "49f600006ff0c701c8f224004ff47a72c16401214170ff2180f89411416fc0e9661249f2f0604ff0ff31c8f22400018001b0",
        "stock all-notes-off block prefix",
    )
    expect(
        stock,
        old_high_file(0x8015CA8E),
        "49f60000c8f22400d5f753fc",
        "Sound Generator init call",
    )

    note = assemble(NOTE_ASM, NOTE_WRAPPER)
    render = assemble(RENDER_ASM, RENDER_WRAPPER)
    release_all = assemble(RELEASE_ALL_ASM, RELEASE_ALL_WRAPPER)
    init_all = assemble(INIT_ALL_ASM, INIT_ALL_WRAPPER)
    gain = assemble(GAIN_ASM, GAIN_WRAPPER)

    slots = [
        (NOTE_WRAPPER, RENDER_WRAPPER, note, "note wrapper"),
        (RENDER_WRAPPER, RELEASE_ALL_WRAPPER, render, "render wrapper"),
        (RELEASE_ALL_WRAPPER, INIT_ALL_WRAPPER, release_all, "release-all wrapper"),
        (INIT_ALL_WRAPPER, GAIN_WRAPPER, init_all, "init-all wrapper"),
        (GAIN_WRAPPER, 0x00020000, gain, "sample-gain wrapper"),
    ]
    injection = bytearray(INSERT_SIZE)
    for start, limit, blob, label in slots:
        if start + len(blob) > limit:
            raise ValueError(
                f"{label} is 0x{len(blob):x} bytes and exceeds its ITCM slot"
            )
        off = start - NOTE_WRAPPER
        injection[off : off + len(blob)] = blob

    data = bytearray(stock)
    data[INSERT_FILE:INSERT_FILE] = injection

    # Extend the ITCM copy row and shift every later scatter source by 0x400.
    rel0, rel1 = struct.unpack_from("<2I", data, 0x80)
    table_start, table_end = 0x80 + rel0, 0x80 + rel1
    if (table_start, table_end) != (0x62C, 0x6EC):
        raise ValueError("Unexpected scatter table bounds")
    for row in range(table_start, table_end, 16):
        load = struct.unpack_from("<I", data, row)[0]
        if load - FLASH >= NEXT_PAYLOAD_FILE:
            struct.pack_into("<I", data, row, load + INSERT_SIZE)

    if struct.unpack_from("<I", data, 0x63C + 8)[0] != 0x1F800:
        raise ValueError("Unexpected stock ITCM region size")
    struct.pack_into("<I", data, 0x63C + 8, ITCM_NEW_SIZE)

    if struct.unpack_from("<I", data, 0x6CC + 8)[0] != 0x03252B0C:
        raise ValueError("Unexpected stock SDRAM zero-init size")
    struct.pack_into("<I", data, 0x6CC + 8, 0x03252B0C + RESERVED_SIZE)

    # Move the OS pool base past the three voice clones. Its end stays fixed,
    # and the firmware recomputes the reduced length by subtraction.
    pool_patch = assemble("movw r0, #0xa82c", 0x80002268)
    if len(pool_patch) != 4:
        raise ValueError("Unexpected pool-base patch size")
    data[new_high_file(0x80002268) : new_high_file(0x80002268) + 4] = pool_patch

    # Route note events through the low-ITCM dispatcher.
    note_on_patch = assemble(
        f"""
            movs r3, #0
            mov r1, r6
            str r4, [sp]
            movw r0, #{(NOTE_WRAPPER | 1) & 0xffff}
            movt r0, #{(NOTE_WRAPPER | 1) >> 16}
            blx r0
            nop
        """,
        0x8015C122,
    )
    if len(note_on_patch) != 18:
        raise ValueError(f"Unexpected note-on patch size: {len(note_on_patch)}")
    off = new_high_file(0x8015C122)
    data[off : off + len(note_on_patch)] = note_on_patch

    note_off_patch = absolute_call(NOTE_WRAPPER | 1, 0x8015C4F0)
    if len(note_off_patch) != 12:
        raise ValueError(f"Unexpected note-off patch size: {len(note_off_patch)}")
    off = new_high_file(0x8015C4F0)
    data[off : off + len(note_off_patch)] = note_off_patch

    # Route the special stock all-notes-off path through all four states.
    release_patch = absolute_call(RELEASE_ALL_WRAPPER | 1, 0x8015BFFA)
    release_patch += b"\x00\xbf" * ((0x30 - len(release_patch)) // 2)
    if len(release_patch) != 0x30:
        raise ValueError("Unexpected release-all patch size")
    off = new_high_file(0x8015BFFA)
    data[off : off + len(release_patch)] = release_patch

    # Generator reinitialization clears clone activity as well as voice 0.
    init_patch = absolute_call(INIT_ALL_WRAPPER | 1, 0x8015CA8E)
    if len(init_patch) != 12:
        raise ValueError(f"Unexpected init patch size: {len(init_patch)}")
    off = new_high_file(0x8015CA8E)
    data[off : off + len(init_patch)] = init_patch

    # Replace the one stock render call with a four-state render loop.
    render_call = assemble(f"bl #{RENDER_WRAPPER}", 0x0001C766)
    if len(render_call) != 4:
        raise ValueError("Unexpected render-call patch size")
    off = 0x1C766 + 0x6F0
    data[off : off + 4] = render_call

    gain_call = assemble(f"bl #{GAIN_WRAPPER}", 0x0001CBE8)
    if len(gain_call) != 4:
        raise ValueError("Unexpected sample-gain hook size")
    off = 0x1CBE8 + 0x6F0
    data[off : off + 4] = gain_call

    return bytes(data)


def main() -> None:
    source = sys.argv[1] if len(sys.argv) > 1 else STOCK
    output = sys.argv[2] if len(sys.argv) > 2 else OUTPUT
    with open(source, "rb") as handle:
        patched = build(handle.read())
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    with open(output, "wb") as handle:
        handle.write(patched)
    print(f"Wrote {output}")
    print(f"Size: {len(patched)} bytes")
    print(f"SHA-256: {sha256(patched)}")
    print("Sound Generator is now a four-voice stock-oscillator test build.")


if __name__ == "__main__":
    main()
