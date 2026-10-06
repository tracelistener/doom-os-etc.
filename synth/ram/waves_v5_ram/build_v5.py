"""Build Wave Lab v5 (RAM): v4-ram + PolyBLEP (Sync, CZRes) + stock type-range fixes.

Same two-stage build as v4 (host-measured DC/loudness tables, then the SP
module at 0x8353E400, laid out like v3-ram), plus guarded one-instruction
fixes in stock code that still capped Sound Generator features at TYPE 14:
  - parameter getter: FREQ and DUTY read back as 0 for TYPE 15+ (the FREQ
    display did not follow the pads),
  - envelope setup at note start, preview start and chord export skipped
    TYPE 15+ (ENV had no effect on the new waves),
  - START/END "loop-length export" toggle was refused for TYPE 15+.

    python waves_v5_ram/build_v5.py          (from the sp404mk2 folder)
"""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / 'waves_v3_ram'))
sys.path.insert(0, str(ROOT / 'waves_v4_ram'))
import build_ram as v3  # noqa: E402
import build_v4 as v4b  # noqa: E402

v4b.HERE = HERE           # reuse the v4 pipeline on this folder's sources
OUT = ROOT / 'firmware' / 'doom-poly-waves-v5-ram'

# (address, stock instruction, replacement). All are same-size and guarded.
EXTRA = [
    # Parameter getter 0x80020450: FREQ (0x7c) and DUTY (0x7f) for every TYPE the range allows.
    (0x80020478, 'cmp r1, #0xe', 'cmp r1, #0x1f'),
    (0x800204aa, 'cmp r1, #4', 'cmp r1, #0x15'),
    # Envelope setup: TYPE 6..31 take the normalized-phase path (new waves use that phase).
    (0x800d0740, 'cmp r2, #9', 'cmp r2, #0x1a'),      # note start (0x800d0668)
    (0x8015c14c, 'cmp r1, #9', 'cmp r1, #0x1a'),      # SG page preview start
    (0x8013287e, 'cmp r2, #9', 'cmp r2, #0x1a'),      # chord/pad export
    # START/END loop-length export flag (+0x04): allow TYPE 15..31; still not Sine2/Cos2.
    (0x8015c5fc, 'cmp r1, #0xe', 'cmp r1, #0x1f'),
    (0x8015c602, 'movw r3, #0x7fdb', 'mvn r3, #0x24'),
    (0x8015c614, 'cmp r1, #0xe', 'cmp r1, #0x1f'),
    (0x8015c620, 'movw r2, #0x7fdb', 'mvn r2, #0x24'),
    (0x8015ca9c, 'cmp r1, #0xe', 'cmp r1, #0x1f'),
    (0x8015caa2, 'movw r3, #0x7fdb', 'mvn r3, #0x24'),
    (0x8002b958, 'cmp r2, #0xe', 'cmp r2, #0x1f'),
    (0x8002b964, 'movw r6, #0x7fdb', 'mvn r6, #0x24'),
    (0x8002b970, 'cmp r2, #0xe', 'cmp r2, #0x1f'),
    (0x8002b978, 'movw r2, #0x7fdb', 'mvn r2, #0x24'),
]


def apply_extra(image):
    out = bytearray(image)
    for address, before, after in EXTRA:
        old, new = v3.asm(before, address), v3.asm(after, address)
        if len(old) != len(new):
            raise ValueError(f'size change at {address:#x}')
        off = v3.file_offset(image, address, len(old))
        if bytes(out[off:off + len(old)]) != old:
            raise ValueError(f'unexpected stock bytes at {address:#x}: {bytes(out[off:off + len(old)]).hex()}')
        out[off:off + len(old)] = new
    return bytes(out)


README = """DOOM/poly-v4 + 17 Wave Lab waves, RAM build v5 -- 2026-10-06
=============================================================

Built by the Claude audit session (waves_v5_ram/build_v5.py). Everything in
v4-ram (RAM-only synth code, DUTY flicker/zipper fix, equal loudness, DC
removal, release-click fix, Noise1/2 label), plus:

- FREQ display follows the pads on the new waves. Stock code read FREQ (and
  DUTY) back as 0 for TYPE 15+, so the screen never updated.
- ENV now works on the new waves. Stock note start skipped envelope setup
  for TYPE 15+.
- START/END (loop-length export) can be switched on for the new waves.
- Sync and CZRes: PolyBLEP smooths the jump at each cycle restart, which
  removes most of their aliasing grit. Logic, Metal and Grit stay raw.

Trade-off: OS memory pool {pool_kib:.1f} KB smaller than v4 ({pool_pct:.1f}% of stock).

Files (use BOTH, together):
  SP404MKII_APP1.bin  {app1_size:,} bytes  SHA-256 {app1_sha}
  SP404MKII_APP0.bin  {app0_size:,} bytes  SHA-256 {app0_sha}  (Roland stock)

Status: verified in emulation (waves_v5_ram/test_v5.py). NOT run on hardware.
Roll back with doom-poly-waves-v4-ram, doom-poly-v4-releasefix or Roland 5.52.

Quick test
1. New wave, play different pads: FREQ on screen should change with each pad.
2. ENV A/B/C on FM1:1 or LPSaw: should shape notes like on stock Saw.
3. Sync at DUTY 50 and CZRes: noticeably less gritty, especially high notes.
4. START/END on a new wave, then REC: exported sample should loop cleanly.
"""


def main():
    v3.require((HERE / 'wave_tables.h').read_bytes(), v4b.TABLES_SHA, 'wave_tables.h')
    v4b.write_level_header([[(0.0, 1.0)] * 101 for _ in range(17)])
    levels, report = v4b.measure(v4b.host_dll())
    level_sha = v4b.write_level_header(levels)
    for kind, duty, mean, rms, peak, gain, final in report:
        if kind in (20, 21):
            print(f'type {kind} duty {duty:3}: mean {mean:6.3f} rms {rms:5.3f} peak {peak:5.3f} gain {gain:5.2f}')
    module, symbols = v4b.compile_arm()
    base = v3.BASE.read_bytes()
    image, info = v3.build_image(base, module, {k: symbols[k] for k in ('wave_eval', 'wave_output', 'wave_name')})
    image = apply_extra(image)
    app0 = v3.APP0.read_bytes()
    v3.require(app0, v3.APP0_SHA, 'stock APP0')
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    (OUT / 'SP404MKII_APP1.bin').write_bytes(image)
    (OUT / 'SP404MKII_APP0.bin').write_bytes(app0)
    info['symbols'] = symbols
    info['hooks'] += [[a, len(v3.asm(b, a))] for a, b, _ in EXTRA]
    info.update(revision='v5-ram', status='hardware-unverified', execution='sdram',
                base_sha256=v3.BASE_SHA, sha256=v3.sha(image), size=len(image),
                module_sha256=v3.sha(module), wave_level_sha256=level_sha,
                waves_c_sha256=v3.sha((HERE / 'waves.c').read_bytes()), tables_sha256=v4b.TABLES_SHA,
                extra_fixes=[[hex(a), b, c] for a, b, c in EXTRA],
                polyblep=['Sync', 'CZRes'],
                duty={'hold_samples': 2400, 'immediate_steps': 3, 'glide_per_sample': 0.002},
                loudness={'target_rms': v4b.TARGET_RMS, 'peak_max': v4b.PEAK_MAX,
                          'gain_max': v4b.GAIN_MAX, 'reference_midi': v4b.REF_NOTES},
                types={str(i + 15): n for i, n in enumerate(v3.TYPES)})
    (OUT / 'manifest.json').write_text(json.dumps(info, indent=2, sort_keys=True) + '\n')
    shrink = info['pool_bytes_v4'] - info['pool_bytes']
    (OUT / 'README.txt').write_text(README.format(
        pool_kib=shrink / 1024, pool_pct=100 * info['pool_bytes'] / info['pool_bytes_stock'],
        app1_size=len(image), app1_sha=v3.sha(image), app0_size=len(app0), app0_sha=v3.sha(app0)),
        encoding='utf-8')
    print(f'{OUT}: APP1 {len(image)} bytes SHA-256 {v3.sha(image)}')
    print(f'module {len(module)} bytes; pool base {info["pool_base"]:#x}, {shrink} bytes less than v4')


if __name__ == '__main__':
    main()
