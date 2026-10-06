"""Release-only correction for the exact DOOM/poly-v4 image.

Leave archived v4 sources and browser overlays unchanged. No new waves, pool
changes, or audio-loop instrumentation. Local hardware-unverified candidate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import build

BASE_SHA = "5ef3642a238d157f0176547dc22d11d46f445072e3aa0ae47879ac304e42cae3"
SLOTS = ((build.v4.NOTE_ENGINE, build.v4.PARAM_ENGINE),
         (build.v4.RELEASE_ENGINE, build.v4.CAPTURE_INIT))


def corrected_sources():
    # Keep instruction addresses in NOTE_ENGINE stable. The original handler
    # already wrote -200; remove only the erroneous subsequent overwrite.
    old = "    movs r0, #0\n    str r0, [r8, #0x4c]"
    source = build.v4.note_source()
    if source.count(old) != 1:
        raise ValueError("unexpected archived note-off source")
    # str through high register r8 is 32-bit: replace 2+4 bytes with 3 NOPs.
    note = source.replace(old, "    nop\n    nop\n    nop")
    if len(build.v4.asm(note, build.v4.NOTE_ENGINE)) != len(build.v4.asm(source, build.v4.NOTE_ENGINE)):
        raise ValueError("note-off correction changed instruction addresses")
    # All-stop doesn't call the original note handler, so initialize its fade
    # explicitly. r0 was zero for preview clearing, and becomes one immediately
    # after this store; changing it here does not alter other lifecycle fields.
    old = "        str r0, [r4, #0x4c]"
    source = build.v4.release_source()
    if source.count(old) != 1:
        raise ValueError("unexpected archived all-stop source")
    stop = source.replace(old, "        mvn r0, #199\n" + old)
    return note, stop


def patch(image: bytes) -> bytes:
    build.require_hash(image, BASE_SHA, "DOOM/poly-v4 release-fix input")
    out = bytearray(image)
    original = (build.v4.note_source(), build.v4.release_source())
    for (start, end), before, after in zip(SLOTS, original, corrected_sources()):
        size = end - start
        expected = build.v4.asm(before, start).ljust(size, b"\0")
        if build.runtime_bytes(image, start, size) != expected:
            raise ValueError(f"unexpected release slot at {start:#x}")
        code = build.v4.asm(after, start)
        if len(code) > size:
            raise ValueError("release correction exceeds owned code slot")
        off = build.offset(image, start, size)
        out[off:off + size] = code.ljust(size, b"\0")
    return bytes(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    image = patch(args.base.read_bytes())
    app0 = args.base.with_name("SP404MKII_APP0.bin").read_bytes()
    build.require_hash(app0, build.APP0_SHA, "unchanged APP0")
    if args.out.exists():
        raise ValueError("refusing to overwrite an existing candidate directory")
    args.out.mkdir(parents=True)
    (args.out / "SP404MKII_APP1.bin").write_bytes(image)
    (args.out / "SP404MKII_APP0.bin").write_bytes(app0)
    manifest = {"status": "hardware-unverified", "base_sha256": BASE_SHA,
                "app1_sha256": build.sha(image), "app0_sha256": build.sha(app0),
                "size": len(image), "changes": "two release-code slots only",
                "new_waves": False, "cpu_timing_instrumentation": False}
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
