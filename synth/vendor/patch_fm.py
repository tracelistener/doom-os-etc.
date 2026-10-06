#!/usr/bin/env python3
"""Build the stock-5.52 SP-404MKII FM oscillator test firmware.

The patch replaces Sound Generator type 8 (Saw2) with a fixed two-operator
phase-modulation voice:

    output = sin(carrier_phase + 2 * sin(2 * carrier_phase))

Only APP1 is changed. APP0 (the encrypted BMC firmware) is never read or
written. The patch is deliberately in-place, so the APP1 file size and its
scatter-load table stay unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path


FLASH_BASE = 0x60080000
STOCK_552_SHA256 = "4a3d67711e14dcc97d50249a4eee7dd6df0251a2f37757cbbe2556c233730d80"

# Runtime 0x80007af8, the original Saw2 case inside the per-sample oscillator.
# This is Thumb-2/VFP code assembled for that exact address. It evaluates the
# modulator with the firmware's existing sinf veneer, evaluates the carrier,
# then branches into the stock level-scaling path at 0x80007774.
FM_CODE_ADDRESS = 0x80007AF8
FM_CODE = bytes.fromhex(
    "94ed0c0b"  # vldr d0, [r4, #0x30]
    "b7eec00b"  # vcvt.f32.f64 s0, d0
    "30ee000a"  # vadd.f32 s0, s0, s0       ; modulator ratio = 2
    "cbf082fb"  # bl 0x800d320c             ; sinf
    "b0ee408a"  # vmov.f32 s16, s0
    "94ed0c0b"  # vldr d0, [r4, #0x30]
    "b7eec00b"  # vcvt.f32.f64 s0, d0
    "b0ee001a"  # vmov.f32 s2, #2.0         ; modulation index = 2
    "08ee010a"  # vmla.f32 s0, s16, s2
    "cbf076fb"  # bl 0x800d320c             ; sinf
    "28e6"      # b 0x80007774               ; stock scale/level path
)

# Runtime 0x80132040, the type-8 value-to-text case. The remaining stock
# instructions store r0 and return, producing the NUL-terminated label FM2.
FM_LABEL_ADDRESS = 0x80132040
FM_LABEL_CODE = bytes.fromhex(
    "0020"      # movs r0, #0
    "e070"      # strb r0, [r4, #3]
    "44f64650"  # movw r0, #0x4d46
    "c0f23200"  # movt r0, #0x32             ; r0 = 0x00324d46 = "FM2\0"
)

# Exact stock bytes prevent accidentally applying this address-specific patch
# to another firmware or over a different modification.
EXPECTED = {
    FM_CODE_ADDRESS: bytes.fromhex(
        "b5ee000abfee001ab7ee002a38ee000a30ee011a"
        "b4ee420ab6ee082af1ee10fa31fe000ab4ee428a"
        "f1ee10fa0cdab6ee"
    ),
    FM_LABEL_ADDRESS: bytes.fromhex("0020207146f25310c3f27720206002b0"),
}


def sha256(data: bytes | bytearray) -> str:
    return hashlib.sha256(data).hexdigest()


def scatter_rows(image: bytes | bytearray):
    """Yield (load_address, runtime_address, size, handler) rows."""
    rel0, rel1 = struct.unpack_from("<2I", image, 0x80)
    start, end = 0x80 + rel0, 0x80 + rel1
    if not (0 <= start <= end <= len(image)) or (end - start) % 16:
        raise ValueError("invalid APP1 scatter-load table")
    for offset in range(start, end, 16):
        yield struct.unpack_from("<4I", image, offset)


def runtime_to_file_offset(image: bytes | bytearray, address: int, size: int) -> int:
    """Map an address in a copy-loaded APP1 region back to its file offset."""
    rows = list(scatter_rows(image))
    if not rows:
        raise ValueError("empty APP1 scatter-load table")
    copy_handler = rows[0][3]
    for load, runtime, region_size, handler in rows:
        if handler == copy_handler and runtime <= address and address + size <= runtime + region_size:
            offset = load - FLASH_BASE + address - runtime
            if offset < 0 or offset + size > len(image):
                break
            return offset
    raise ValueError(f"runtime address 0x{address:08x} is not in a copy-loaded APP1 region")


def patch_at(image: bytearray, address: int, replacement: bytes) -> int:
    offset = runtime_to_file_offset(image, address, len(replacement))
    expected = EXPECTED[address]
    found = bytes(image[offset : offset + len(expected)])
    if found != expected:
        raise ValueError(
            f"stock bytes differ at runtime 0x{address:08x} "
            f"(file 0x{offset:x}); expected {expected.hex()}, found {found.hex()}"
        )
    image[offset : offset + len(replacement)] = replacement
    return offset


def build(source: Path, destination: Path) -> tuple[str, list[tuple[int, int, int]]]:
    image = bytearray(source.read_bytes())
    digest = sha256(image)
    if digest != STOCK_552_SHA256:
        raise ValueError(
            "input is not the stock Roland 5.52 SP404MKII_APP1.bin: "
            f"SHA-256 is {digest}"
        )

    changes = []
    for address, code in ((FM_CODE_ADDRESS, FM_CODE), (FM_LABEL_ADDRESS, FM_LABEL_CODE)):
        offset = patch_at(image, address, code)
        changes.append((address, offset, len(code)))

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(image)
    return sha256(image), changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="stock 5.52 SP404MKII_APP1.bin")
    parser.add_argument("destination", type=Path, help="output SP404MKII_APP1.bin")
    args = parser.parse_args()

    digest, changes = build(args.source, args.destination)
    print(f"wrote {args.destination}")
    print(f"SHA-256 {digest}")
    for address, offset, size in changes:
        print(f"patched runtime 0x{address:08x} / file 0x{offset:x}: {size} bytes")
    print("Sound Generator type 8 is now FM2 (ratio 2:1, index 2).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
