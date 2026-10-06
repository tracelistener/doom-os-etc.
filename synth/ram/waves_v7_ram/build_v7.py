"""Build Wave Lab v7 (RAM, OS pool untouched): v6 + three fixes from hardware testing.

1. Stuck note on OCT change. The poly dispatcher released a voice only when
   both the note and the pad (source) matched. Holding a pad while changing
   OCT makes its note-off carry the new note, so nothing matched and the voice
   hung. Now it releases exact matches first, as before. If none matched, it
   releases that pad's voice (one voice, never one already fading out). That
   mirrors Roland's own note-off, which falls back to the source.
2. Stock Pulse is 6 dB quieter, the same way as Noise1/Noise2 (RAM gate halves the
   stock oscillator's sample; live and export).
3. New waves are matched by perceived (A-weighted) loudness to stock Sine
   instead of plain RMS, peak-capped as before. Square-like Logic, bright Fold
   and Sync come down; darker waves come up.
Layout identical to v6: wave code at 0x83F80000 in unused SDRAM, OS pool as v4.

    python waves_v7_ram/build_v7.py          (from the sp404mk2 folder)
"""
import ctypes
import json
import math
import shutil
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for folder in ('waves_v3_ram', 'waves_v4_ram', 'waves_v5_ram', 'waves_v6_ram'):
    sys.path.insert(0, str(ROOT / folder))
import build_ram as v3  # noqa: E402
import build_v4 as v4b  # noqa: E402
import build_v5 as v5b  # noqa: E402
import build_v6 as v6b  # noqa: E402

v4b.HERE = v6b.HERE = HERE            # reuse the v4/v6 pipeline on this folder's sources
OUT = ROOT / 'firmware' / 'doom-poly-waves-v7-ram'
v4 = v3.v4
load, call, jump = v3.load, v3.call, v3.jump
C4 = 440 * 2 ** ((60 - 69) / 12)


def a_weight(f):
    f = np.asarray(f, dtype=float)
    f2 = f * f
    ra = (12194 ** 2 * f2 ** 2) / ((f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2)) * (f2 + 12194 ** 2))
    one = (12194 ** 2 * 1e12) / ((1e6 + 20.6 ** 2) * math.sqrt((1e6 + 107.7 ** 2) * (1e6 + 737.9 ** 2)) * (1e6 + 12194 ** 2))
    return ra / one


def measure(lib):
    """levels[type][duty] = (mean, gain); gain matches A-weighted level to a unit stock Sine at C4."""
    lib.wave_host_render.argtypes = [ctypes.c_uint, ctypes.c_float, ctypes.c_float, ctypes.c_float,
                                     ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
    n = v4b.N_PHASES
    buf = (ctypes.c_float * n)()
    stats = (ctypes.c_float * 3)()
    dts = [440 * 2 ** ((m - 69) / 12) / 48000 for m in v4b.REF_NOTES]
    dt4 = C4 / 48000
    target = math.sqrt(0.5) * float(a_weight(C4))           # stock Sine, full scale, at C4
    k = np.arange(n // 2 + 1)
    weights = a_weight(np.maximum(k * C4, 1.0)) ** 2 * ((k >= 1) & (k * C4 < 24000))
    levels, report = [], []
    for kind in range(15, 32):
        rs = [i / 10 - 1 for i in range(21)] if kind == 31 else [0.0]
        rarr = (ctypes.c_float * len(rs))(*rs)
        row = []
        for duty in range(101):
            lib.wave_host_stats(kind, duty, dt4, n, rarr, len(rs), 0, 0, stats)
            mean = stats[0]
            peak = 0.0
            for dt in dts:
                lib.wave_host_stats(kind, duty, dt, n, rarr, len(rs), mean, 1, stats)
                peak = max(peak, stats[2])
            power = 0.0
            for r in rs:
                lib.wave_host_render(kind, duty, dt4, r, n, buf)
                spectrum = np.fft.rfft(np.frombuffer(buf, dtype=np.float32) - mean) / n
                power += float(np.sum(2 * np.abs(spectrum) ** 2 * weights))
            aw = math.sqrt(power / len(rs))
            gain = min(target / max(aw, 1e-9), v4b.PEAK_MAX / max(peak, 1e-6), v4b.GAIN_MAX)
            row.append((mean, gain))
            if duty in (0, 50, 100):
                report.append((kind, duty, gain, min(1.0, gain * aw / target)))
        levels.append(row)
    return levels, report


def gate_sources(symbols):
    """v6's gates; the quiet path now covers stock Pulse (11) as well as Noise1/2 (13, 14)."""
    sources = v6b_gate_sources(symbols)
    osc = sources[0]
    text = osc[2].replace('''            ldrb.w r1, [r0, #0x70]
            subs r2, r1, #13
            cmp r2, #1
            bls noise_voice''', '''            ldrb.w r1, [r0, #0x70]
            cmp r1, #11
            beq noise_voice
            subs r2, r1, #13
            cmp r2, #1
            bls noise_voice''')
    if text == osc[2]:
        raise ValueError('v6 gate source changed')
    return [(osc[0], osc[1], text)] + sources[1:]


v6b_gate_sources = v6b.gate_sources
v6b.gate_sources = gate_sources


def note_sources():
    """(release-fix note engine as in the base image, v7 note engine with source fallback)."""
    base = v4.note_source().replace('    movs r0, #0\n    str r0, [r8, #0x4c]', '    nop\n    nop\n    nop')
    head = base[:base.index('note_off:')]
    note_off = '''note_off:
    mov.w r10, #0
    mov.w r11, #0
note_off_pass:
    movw r8, #0x9800
    movt r8, #0x8024
    movs r9, #0
note_off_loop:
    ldrb r0, [r8]
    cmp r0, #0
    beq note_off_next
    ldr.w r0, [r8, #0x1ac]
    cmp r0, r7
    bne note_off_next
    cmp.w r11, #0
    bne by_source
    ldr.w r0, [r8, #0x19c]
    cmp r0, r4
    bne note_off_next
    b release_voice
by_source:
    ldrb r0, [r8, #1]
    cmp r0, #0
    bne note_off_next
release_voice:
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
    add.w r10, r10, #1
    cmp.w r11, #0
    bne done
note_off_next:
    adds r9, #1
    cmp r9, #4
    beq note_off_end
    cmp r9, #1
    bne note_off_add
    movw r8, #0xa310
    movt r8, #0x8353
    b note_off_loop
note_off_add:
    add.w r8, r8, #0x1b0
    b note_off_loop
note_off_end:
    cmp.w r10, #0
    bne done
    cmp.w r11, #0
    bne done
    mov.w r11, #1
    b note_off_pass

done:
    add sp, #4
    pop.w {r4-r11, pc}
'''
    return base, head + note_off


def patch_note_engine(image):
    before, after = note_sources()
    size = v4.PARAM_ENGINE - v4.NOTE_ENGINE
    old, new = v3.asm(before, v4.NOTE_ENGINE), v3.asm(after, v4.NOTE_ENGINE)
    if len(new) > size:
        raise ValueError(f'note engine {len(new)} bytes exceeds its {size}-byte slot')
    off = v3.file_offset(image, v4.NOTE_ENGINE, size)
    if image[off:off + size] != old.ljust(size, b'\0'):
        raise ValueError('note engine slot is not the release-fixed v4 code')
    out = bytearray(image)
    out[off:off + size] = new.ljust(size, b'\0')
    return bytes(out), len(new)


README = """DOOM/poly-v4 + 17 Wave Lab waves, v7 (RAM, no memory taken) -- 2026-10-06
==========================================================================

Built by the Claude audit session (waves_v7_ram/build_v7.py). Same layout and
everything else as v6, plus:

- Stuck-note fix: holding a pad while changing OCT (or anything that changes
  the pad's note) no longer leaves the note hanging. Exact note+pad matches
  release first, as before; if none match, that pad's own voice is released.
- Stock Pulse is 6 dB quieter (like Noise1/Noise2), live and on export.
- New waves are matched by perceived loudness (A-weighted, like your ears)
  to stock Sine, instead of plain RMS. Logic, Fold and Sync come down; darker
  waves come up. Peak-capped so nothing clips.

Files (use BOTH, together):
  SP404MKII_APP1.bin  {app1_size:,} bytes  SHA-256 {app1_sha}
  SP404MKII_APP0.bin  {app0_size:,} bytes  SHA-256 {app0_sha}  (Roland stock)

Status: verified in emulation (waves_v7_ram/test_v7.py). NOT run on hardware.
Roll back with doom-poly-waves-v6-ram, doom-poly-v4-releasefix or Roland 5.52.
"""


def main():
    v3.require((HERE / 'wave_tables.h').read_bytes(), v4b.TABLES_SHA, 'wave_tables.h')
    v4b.write_level_header([[(0.0, 1.0)] * 101 for _ in range(17)])
    levels, report = measure(v4b.host_dll())
    level_sha = v4b.write_level_header(levels)
    print('wave duty  gain  level-vs-sine(A)')
    for kind, duty, gain, rel in report:
        print(f'{kind:4} {duty:4} {gain:5.2f} {20 * math.log10(max(rel, 1e-6)):6.1f} dB')
    module, symbols = v6b.compile_arm()
    base = v3.BASE.read_bytes()
    image, info = v6b.build_image(base, module, symbols)
    image = v5b.apply_extra(image)
    image, note_size = patch_note_engine(image)
    app0 = v3.APP0.read_bytes()
    v3.require(app0, v3.APP0_SHA, 'stock APP0')
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    (OUT / 'SP404MKII_APP1.bin').write_bytes(image)
    (OUT / 'SP404MKII_APP0.bin').write_bytes(app0)
    info['hooks'] += [[a, len(v3.asm(b, a))] for a, b, _ in v5b.EXTRA]
    info['hooks'].append([v4.NOTE_ENGINE, v4.PARAM_ENGINE - v4.NOTE_ENGINE])
    info.update(revision='v7-ram', status='hardware-unverified', execution='sdram-gap',
                symbols=symbols, base_sha256=v3.BASE_SHA, sha256=v3.sha(image), size=len(image),
                module_sha256=v3.sha(module), wave_level_sha256=level_sha,
                waves_c_sha256=v3.sha((HERE / 'waves.c').read_bytes()), tables_sha256=v4b.TABLES_SHA,
                extra_fixes=[[hex(a), b, c] for a, b, c in v5b.EXTRA], polyblep=['Sync', 'CZRes'],
                quiet_stock_types={'11': 'Pulse', '13': 'Noise1', '14': 'Noise2'}, quiet_gain_db=-6.02,
                loudness={'match': 'A-weighted level of a full-scale stock Sine at C4',
                          'peak_max': v4b.PEAK_MAX, 'gain_max': v4b.GAIN_MAX},
                note_off='exact note+source first, else one non-releasing voice of that source',
                note_engine_bytes=note_size, sdram_gap=['0x83f7a424', '0x83ff0000'],
                types={str(i + 15): n for i, n in enumerate(v3.TYPES)})
    (OUT / 'manifest.json').write_text(json.dumps(info, indent=2, sort_keys=True) + '\n')
    (OUT / 'README.txt').write_text(README.format(
        app1_size=len(image), app1_sha=v3.sha(image), app0_size=len(app0), app0_sha=v3.sha(app0)),
        encoding='utf-8')
    print(f'{OUT}: APP1 {len(image)} bytes SHA-256 {v3.sha(image)}; note engine {note_size}/384 bytes')


if __name__ == '__main__':
    main()
