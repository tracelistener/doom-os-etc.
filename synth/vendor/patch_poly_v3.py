"""Four-voice Sound Generator test v3: v2 plus shared parameter control.

Builds on the hardware-confirmed v2 image (patch_poly.build) and adds one
feature layer:

* Sound Generator parameter edits are applied to every voice, not only the
  singleton. The stock setter (0x80019e30) still runs on the singleton first,
  exactly as before. For TYPE, LEVEL, DUTY, BALANCE, TUNE and ENV it then runs
  again on each clone, so every voice keeps its own note, phase, velocity,
  source ID and envelope progress. TUNE recomputes each voice's increments
  from that voice's own note, so a held chord stays a chord.
* FREQ (the pitch itself), PREVIEW and PAD LENGTH stay singleton-only.
* A TYPE change skips clones that are already releasing. The stock type
  switch restarts the fade counter, which would bump a dying tail back up.
* The output bus is not set through the setter (the REMAIN button writes
  state+0x0c directly), so the render wrapper copies the singleton's bus to
  the clones once per audio block.

Everything else, including all v2 code, addresses and hooks, is unchanged.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import patch_poly as v2  # noqa: E402

ROOT = v2.ROOT
STOCK = v2.STOCK
OUTPUT = os.path.join(ROOT, "firmware", "poly-test-v3", "SP404MKII_APP1.bin")
PATCHED_SHA256 = "132520641c960de0909f26c6d3cc80366f53ff54e4e65f6a3baacb194f2fce49"

SETTER = 0x80019E30
PARAM_HOOK = 0x800DD26C          # 18-byte setter call block inside the global SetParam
PARAM_HOOK_STOCK = "49f600004146c8f2240032463b463cf7d9fd"
PARAM_WRAPPER = 0x0001FF20       # free tail of the v2 ITCM block, after the gain wrapper
PARAM_LIMIT = 0x00020000
RENDER_LIMIT = v2.RELEASE_ALL_WRAPPER

# Bit (id - 0x7a) set = broadcast to clones.
# 0x7b TYPE, 0x7d LEVEL, 0x7f DUTY, 0x80 BALANCE, 0x81 TUNE, 0x82 ENV.
# Not broadcast: 0x7a PREVIEW, 0x7c FREQ (pitch), 0x7e PAD LENGTH (export only).
SHARED_PARAM_MASK = (1 << 1) | (1 << 3) | (1 << 5) | (1 << 6) | (1 << 7) | (1 << 8)


# Same as v2's render wrapper, plus: copy the singleton's output bus (+0x0c)
# into the three clones before rendering.
RENDER_ASM = r"""
    push.w {r4-r8, lr}
    mov r4, r1
    mov r5, r2
    mov r6, r3

    movw r7, #0x9800
    movt r7, #0x8024
    ldr r0, [r7, #0xc]
    movw r1, #0xa310
    movt r1, #0x8353
    str r0, [r1, #0xc]
    str.w r0, [r1, #0x1bc]
    str.w r0, [r1, #0x36c]

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


# Entered from the global SetParam with r1 = parameter id, r2 = clamped value,
# r3 = flags (r0 = this wrapper's own address, ignored).
PARAM_ASM = r"""
    push.w {r4-r8, lr}
    mov r4, r1
    mov r5, r2
    mov r6, r3

    movw r0, #0x9800
    movt r0, #0x8024
    movw r12, #0x9e31
    movt r12, #0x8001
    blx r12

    sub.w r0, r4, #0x7a
    cmp r0, #8
    bhi param_done
    movw r1, #0x1ea
    lsr.w r1, r1, r0
    tst.w r1, #1
    beq param_done

    movw r7, #0xa310
    movt r7, #0x8353
    mov.w r8, #3
param_loop:
    cmp r4, #0x7b
    bne param_apply
    ldrb r0, [r7]
    cbz r0, param_apply
    ldrb r0, [r7, #1]
    cbnz r0, param_next
param_apply:
    mov r0, r7
    mov r1, r4
    mov r2, r5
    mov r3, r6
    movw r12, #0x9e31
    movt r12, #0x8001
    blx r12
param_next:
    add.w r7, r7, #0x1b0
    subs.w r8, r8, #1
    bne param_loop
param_done:
    pop.w {r4-r8, pc}
"""


def itcm_file(runtime: int) -> int:
    return runtime + 0x6F0


def param_hook_bytes() -> bytes:
    return v2.assemble(
        f"""
            mov r1, r8
            mov r2, r6
            mov r3, r7
            movw r0, #{(PARAM_WRAPPER | 1) & 0xffff}
            movt r0, #{(PARAM_WRAPPER | 1) >> 16}
            blx r0
            nop
        """,
        PARAM_HOOK,
    )


def build(stock: bytes) -> bytes:
    data = bytearray(v2.build(stock))
    if v2.sha256(data) != v2.PATCHED_SHA256:
        raise ValueError("v2 base image does not match the hardware-tested v2 build")

    # The render slot must still hold exactly v2's render wrapper.
    v2_render = v2.assemble(v2.RENDER_ASM, v2.RENDER_WRAPPER)
    off = itcm_file(v2.RENDER_WRAPPER)
    if bytes(data[off : off + len(v2_render)]) != v2_render:
        raise ValueError("render slot does not contain the v2 render wrapper")
    render = v2.assemble(RENDER_ASM, v2.RENDER_WRAPPER)
    if v2.RENDER_WRAPPER + len(render) > RENDER_LIMIT:
        raise ValueError(f"render wrapper is 0x{len(render):x} bytes and overflows its slot")
    if any(data[off + len(v2_render) : itcm_file(RENDER_LIMIT)]):
        raise ValueError("render slot tail is not empty")
    data[off : off + len(render)] = render

    # The parameter wrapper goes into the still-empty tail after the gain wrapper.
    gain_end = v2.GAIN_WRAPPER + len(v2.assemble(v2.GAIN_ASM, v2.GAIN_WRAPPER))
    if PARAM_WRAPPER < gain_end:
        raise ValueError("parameter wrapper would overlap the gain wrapper")
    param = v2.assemble(PARAM_ASM, PARAM_WRAPPER)
    if PARAM_WRAPPER + len(param) > PARAM_LIMIT:
        raise ValueError(f"parameter wrapper is 0x{len(param):x} bytes and overflows ITCM")
    poff = itcm_file(PARAM_WRAPPER)
    if any(data[itcm_file(gain_end) : itcm_file(PARAM_LIMIT)]):
        raise ValueError("ITCM tail after the gain wrapper is not empty")
    data[poff : poff + len(param)] = param

    # Route the generator's setter call through the wrapper.
    hoff = v2.new_high_file(PARAM_HOOK)
    v2.expect(data, hoff, PARAM_HOOK_STOCK, "generator setter call block")
    hook = param_hook_bytes()
    if len(hook) != 18:
        raise ValueError(f"unexpected parameter hook size: {len(hook)}")
    data[hoff : hoff + len(hook)] = hook

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
    print(f"SHA-256: {v2.sha256(patched)}")
    print("Four-voice test v3: v2 plus shared parameter control.")


if __name__ == "__main__":
    main()
