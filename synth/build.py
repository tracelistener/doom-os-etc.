"""Compose DOOM OS ED5E with the verified poly-test-v4 sources.

Preserve all DOOM OS XIP flash addresses: never insert bytes into its image.
Requires the owner's exact stock 5.52 APP1 and keystone-engine. No APP0 edits.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE / "vendor"))
import patch_poly as v2
import patch_poly_v3 as v3
import patch_poly_v4 as v4
import patch_fm as fm

DOOM_SHA = "16c0b62a1a7f385be00d1cf4222180788be1ae29bb2f280a46835a56a055afa7"
APP0_SHA = "3e35ff30137f741a715566e205384f9b434ca7ac3847e247bad3f0c9c8a2bd23"
COPY = 0x600800E4
ZERO = 0x60080100

# Guard complete instruction spans, including bytes unchanged by v4. Applying
# just byte differences could silently mix DOOM and stock instructions.
HOOKS = [
    (0x1C766, 0x1C76A), (0x1CBE8, 0x1CBEC),
    (0x80002268, 0x8000226C), (v3.PARAM_HOOK, v3.PARAM_HOOK + 18),
    (0x8015C122, 0x8015C134), (0x8015C4F0, 0x8015C4FC),
    (0x8015BFEA, 0x8015C02A), (0x8015CA8E, 0x8015CA9A),
    (0x8015C684, 0x8015C696), (0x80028B98, 0x80028BAE),
    (0x801327B8, 0x801327C4), (0x80007700, 0x8000770C),
] + v4.RELEASE_SPANS


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require_hash(data, expected, name):
    if sha(data) != expected:
        raise ValueError(f"{name}: unsupported SHA-256 {sha(data)}")


def scatter(image):
    a, b = struct.unpack_from("<2I", image, 0x80)
    a += 0x80
    b += 0x80
    if not (0 <= a < b <= len(image)) or (b - a) % 16:
        raise ValueError("invalid scatter table")
    return [list(struct.unpack_from("<4I", image, i)) for i in range(a, b, 16)]


def offset(image, address, size):
    return fm.runtime_to_file_offset(image, address, size)


def runtime_bytes(image, address, size):
    off = offset(image, address, size)
    return bytes(image[off:off + size])


def patch_container():
    text = (REPO / "doomos-patcher.js").read_text(encoding="utf-8")
    match = re.search(r'window\.DOOMOS_PATCH = "([A-Za-z0-9+/=]+)";', text)
    if not match:
        raise ValueError("DOOM OS patch container not found")
    return base64.b64decode(match[1], validate=True)


def apply_container(container, source):
    if container[:8] != b"DOOMPTCH" or struct.unpack_from("<H", container, 8)[0] != 1:
        raise ValueError("unsupported container")
    count = struct.unpack_from("<H", container, 10)[0]
    for i in range(count):
        at = 12 + 96 * i
        if container[at + 16:at + 48].hex() != sha(source):
            continue
        target = container[at + 48:at + 80].hex()
        size, n, records, read = struct.unpack_from("<4I", container, at + 80)
        out = bytearray(size)
        out[:min(len(source), size)] = source[:size]
        for j in range(n):
            pos, length, where = struct.unpack_from("<3I", container, records + 12 * j)
            inline = where == 0xFFFFFFFF
            origin, start = (container, read) if inline else (source, where)
            if pos + length > size or start + length > len(origin):
                raise ValueError("container record out of bounds")
            out[pos:pos + length] = origin[start:start + length]
            if inline:
                read += length
        require_hash(out, target, "container output")
        return bytes(out)
    raise ValueError("unsupported container source")


def compose(stock, doom, poly):
    require_hash(stock, v2.STOCK_SHA256, "stock 5.52 APP1")
    require_hash(doom, DOOM_SHA, "DOOM OS ED5E")
    require_hash(poly, v4.PATCHED_SHA256, "poly-test-v4")
    rows = scatter(doom)
    if rows != scatter(stock):
        raise ValueError("DOOM scatter layout differs from the supported stock layout")
    # Account for every changed byte in every pre-existing copy region.
    # The only other v4 changes are its table and newly injected regions.
    for _, runtime, size, handler in scatter(stock):
        if handler != COPY:
            continue
        before = runtime_bytes(stock, runtime, size)
        after = runtime_bytes(poly, runtime, size)
        for i, (a, b) in enumerate(zip(before, after)):
            if a != b and not any(start <= runtime + i < end for start, end in HOOKS):
                raise ValueError(f"unaccounted v4 edit at {runtime + i:#x}")
    out = bytearray(doom)
    for start, end in HOOKS:
        before = runtime_bytes(stock, start, end - start)
        if runtime_bytes(doom, start, end - start) != before:
            raise ValueError(f"DOOM hook conflict at {start:#x}..{end:#x}")
        replacement = runtime_bytes(poly, start, end - start)
        off = offset(doom, start, end - start)
        out[off:off + end - start] = replacement
    # Stock ITCM ends at 0x1fc00. Both images must leave the new region free.
    for _, runtime, size, handler in rows:
        if runtime < 0x20000 and runtime + size > v2.NOTE_WRAPPER:
            raise ValueError("ITCM wrapper region is already occupied")
    zi = [row for row in rows if row[1] == 0x802E7800 and row[3] == ZERO]
    if len(zi) != 1 or zi[0][1] + zi[0][2] != v2.OLD_OS_POOL_BASE:
        raise ValueError("unexpected SDRAM pool ownership")
    zi[0][2] = v4.NEW_POOL_BASE - zi[0][1]
    # Copy after zero-init. Unlike the standalone v4 builder, do not expand
    # the existing ITCM payload: that would relocate all DOOM XIP code.
    for address, size in [(v2.NOTE_WRAPPER, v2.INSERT_SIZE), (v4.CODE_BASE, v4.CODE_SIZE)]:
        out.extend(b"\0" * (-len(out) % 16))
        load = v2.FLASH + len(out)
        out.extend(runtime_bytes(poly, address, size))
        rows.append([load, address, size, COPY])
    table_at = len(out)
    for row in rows:
        out.extend(struct.pack("<4I", *row))
    struct.pack_into("<2I", out, 0x80, table_at - 0x80, len(out) - 0x80)
    return bytes(out)


def with_fm(image):
    out = bytearray(image)
    fm.patch_at(out, fm.FM_CODE_ADDRESS, fm.FM_CODE)
    fm.patch_at(out, fm.FM_LABEL_ADDRESS, fm.FM_LABEL_CODE)
    return bytes(out)


def make_overlay(source, target, label):
    """Small hash-guarded overlay in the original browser applier's format."""
    records, payload = [], bytearray()
    i = 0
    while i < len(target):
        if i < len(source) and source[i] == target[i]:
            i += 1
            continue
        start = i
        while i < len(target) and (i >= len(source) or source[i] != target[i]):
            i += 1
        records.append((start, i - start, 0xFFFFFFFF))
        payload.extend(target[start:i])
    header = b"DOOMPTCH" + struct.pack("<2H", 1, 1)
    entry = label.encode("ascii").ljust(16, b"\0")
    if len(entry) != 16:
        raise ValueError("overlay label too long")
    entry += bytes.fromhex(sha(source)) + bytes.fromhex(sha(target))
    entry += struct.pack("<4I", len(target), len(records), 108, 108 + 12 * len(records))
    container = header + entry + b"".join(struct.pack("<3I", *r) for r in records) + payload
    if apply_container(container, source) != target:
        raise ValueError("overlay round-trip failed")
    return bytes(container)


def build(stock):
    require_hash(stock, v2.STOCK_SHA256, "stock 5.52 APP1")
    doom = apply_container(patch_container(), stock)
    poly = v4.build(stock)
    return doom, compose(stock, doom, poly)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock", type=Path)
    parser.add_argument("--out", type=Path, default=REPO / "out" / "doom-poly-v4")
    parser.add_argument("--fm", action="store_true", help="also produce optional FM2 (replaces Saw2)")
    parser.add_argument("--web", action="store_true", help="regenerate browser overlay data")
    args = parser.parse_args()
    stock = args.stock.read_bytes()
    doom, merged = build(stock)
    outputs = {"poly": merged}
    overlays = {"poly": make_overlay(doom, merged, "5.52+poly-v4")}
    if args.fm:
        outputs["fm"] = with_fm(merged)
        overlays["fm"] = make_overlay(merged, outputs["fm"], "5.52+poly-v4+FM")
    # APP0 is only copied, with its independent stock digest checked first.
    app0 = args.stock.with_name("SP404MKII_APP0.bin")
    app0_bytes = app0.read_bytes() if app0.exists() else None
    if app0_bytes is not None:
        require_hash(app0_bytes, APP0_SHA, "stock APP0")
    for name, image in outputs.items():
        folder = args.out if name == "poly" else args.out.with_name(args.out.name + "-fm")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SP404MKII_APP1.bin").write_bytes(image)
        if app0_bytes is not None:
            (folder / "SP404MKII_APP0.bin").write_bytes(app0_bytes)
        print(f"{folder}: {len(image)} bytes, SHA-256 {sha(image)}")
    if args.web:
        info = {"base": DOOM_SHA, "v4": v4.PATCHED_SHA256,
                "experimental": True,
                "outputs": {name: {"sha256": sha(image), "size": len(image)} for name, image in outputs.items()}}
        js = "// Generated by synth/build.py --web; do not hand-edit. No complete firmware images.\n"
        js += "window.DOOMOS_SYNTH = " + json.dumps(info, sort_keys=True) + ";\n"
        js += "window.DOOMOS_SYNTH_PATCHES = " + json.dumps({key: base64.b64encode(value).decode("ascii") for key, value in overlays.items()}, sort_keys=True) + ";\n"
        (REPO / "synth-data.js").write_text(js, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
