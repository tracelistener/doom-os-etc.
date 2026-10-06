"""Build Wave Lab v6: v5 sound, placed in unused SDRAM; OS pool untouched; quieter noise.

v6 differs from v5 only in layout, plus one sound change:
- The gates and wave module are linked at 0x83F80000 and copied there at boot
  by the stock scatter loader (one appended copy row, like the stock code).
  0x83F7A424..0x83FF0000 sits between the firmware's fixed 7.25 MB buffer
  (0x8383A424..0x83F7A424) and the non-cacheable region (MPU region 9,
  0x83FF0000). No stock or DOOM code forms an address in it.
  It is cacheable and executable (MPU region 8).
- The OS memory pool, the zero-init rows and the pool registration are left
  exactly as in the working DOOM/poly-v4 image (no RAM is taken from them).
- Stock Noise1/Noise2 are 6 dB quieter. The RAM gate calls the stock
  oscillator for them and halves the returned sample, live and on export alike.
Everything else (17 waves, DUTY flicker filter/glide, loudness/DC tables, PolyBLEP,
getter/ENV/START-END fixes, release-click fix, Noise label) is as in v5.

    python waves_v6_ram/build_v6.py          (from the sp404mk2 folder)
"""
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for folder in ('waves_v3_ram', 'waves_v4_ram', 'waves_v5_ram'):
    sys.path.insert(0, str(ROOT / folder))
import build_ram as v3  # noqa: E402  (assembler helpers, ELF reader, guards)
import build_v4 as v4b  # noqa: E402  (two-stage level-table measurement)
import build_v5 as v5b  # noqa: E402  (stock type-gate fixes)

v4b.HERE = HERE
OUT = ROOT / 'firmware' / 'doom-poly-waves-v6-ram'
CODE_BASE = 0x83F80000
LINK_BASE = CODE_BASE + 0x400
OSC_GATE, NAME_GATE, FREQ_LABEL = CODE_BASE, CODE_BASE + 0x100, CODE_BASE + 0x180
BIG_BUFFER_END, NONCACHE = 0x83F7A424, 0x83FF0000
load, call, jump = v3.load, v3.call, v3.jump
v4 = v3.v4


def compile_arm():
    folder = HERE / 'build' / 'ram'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'waves.ld').write_text(v3.LINKER.format(base=LINK_BASE), encoding='ascii', newline='\n')
    subprocess.run([str(v3.ZIG), 'cc', '-target', 'thumb-freestanding-eabihf',
                    '-mcpu=cortex_m7+vfp4d16', '-mfpu=vfpv4-d16', '-mfloat-abi=hard', *v4b.COMMON,
                    '-nostdlib', '-Wl,--no-undefined', '-Wl,--build-id=none', '-Wl,-e,wave_output',
                    '-Wl,-T,' + str(folder / 'waves.ld'), '-I', str(HERE), str(HERE / 'waves.c'),
                    '-o', str(folder / 'waves.elf')], check=True, cwd=HERE, env=v4b.ZIG_ENV)
    elf = (folder / 'waves.elf').read_bytes()
    blob, symbols = v3.read_elf(elf, LINK_BASE, NONCACHE)
    symbols['wave_core'] = v4b.elf_symbol(elf, 'wave_core')
    symbols['slots'] = v4b.elf_symbol(elf, 'slots')
    (folder / 'waves.bin').write_bytes(blob)
    return blob, symbols


def gate_sources(symbols):
    return [
        (OSC_GATE, NAME_GATE, f'''
            {load('r12', 0x80132AF3)}
            cmp lr, r12
            beq original_gate
            ldrb.w r1, [r0, #0x70]
            subs r2, r1, #13
            cmp r2, #1
            bls noise_voice
            subs r1, #15
            cmp r1, #16
            bhi original_gate
            push {{r4-r6, lr}}
            vpush {{d8-d12}}
            mov r4, r0
            {call(symbols['wave_output'])}
            {jump(0x80007774)}
        noise_voice:
            push {{r3, lr}}
            {call(v4.OSC_GATE)}
            asrs r0, r0, #1
            pop {{r3, pc}}
        original_gate:
            {jump(v4.OSC_GATE)}
        '''),
        (NAME_GATE, FREQ_LABEL, f'''
            ldrb.w r0, [r0, #0x70]
            cmp r0, #14
            bhi extended_name
            {load('r12', 0x80131C8C)}
            ldrh.w r0, [r12, r0, lsl #1]
            add.w r12, r12, r0, lsl #1
            orr.w r12, r12, #1
            bx r12
        extended_name:
            cmp r0, #31
            bls known_name
            {jump(0x80131CB4)}
        known_name:
            mov r1, r4
            {call(symbols['wave_name'])}
            {jump(0x80131C78)}
        '''),
        (FREQ_LABEL, CODE_BASE + 0x400, f'''
            ldrb.w r0, [r0, #0x70]
            subs r0, #13
            cmp r0, #1
            bhi pitched_label
            adds r0, #13
            {jump(0x80131C0C)}
        pitched_label:
            movs r0, #0
            strb r0, [r1, #4]
            movw r0, #0x7246
            movt r0, #0x7165
            str r0, [r1]
            bx lr
        '''),
    ]


def build_image(base, module, symbols):
    v3.require(base, v3.BASE_SHA, 'DOOM/poly-v4 release-fix base')
    code = bytearray(0x400)
    for start, end, source in gate_sources(symbols):
        blob = v3.asm(source, start)
        if start + len(blob) > end:
            raise ValueError('wave bridge overflow')
        code[start - CODE_BASE:start - CODE_BASE + len(blob)] = blob
    code.extend(module)
    code.extend(b'\0' * (-len(code) % 16))
    if not BIG_BUFFER_END <= CODE_BASE or CODE_BASE + len(code) > NONCACHE:
        raise ValueError('wave code must sit inside the unused SDRAM gap')
    out = bytearray(base)
    patches = []

    def patch(address, expected, replacement):
        off = v3.file_offset(base, address, len(expected))
        if bytes(base[off:off + len(expected)]) != expected or len(replacement) != len(expected):
            raise ValueError(f'unexpected bytes or size at {address:#x}')
        out[off:off + len(expected)] = replacement
        patches.append([address, len(replacement)])

    for address, before, after in [
        (0x8013269C, 'movs r0, #14', 'movs r0, #31'),
        (0x801326A4, 'cmp r0, #14', 'cmp r0, #31'),
        (0x801326C6, 'cmp r1, #5', 'cmp r1, #22'),
        (0x80019FC6, 'cmp r0, #4', 'cmp r0, #21'),
        (0x80131CCC, 'cmp r2, #14', 'cmp r2, #31'),
        (0x80131D9E, 'cmp r1, #4', 'cmp r1, #21'),
        (0x80131BAA, 'cmp r0, #4', 'cmp r0, #21'),
        (0x80132A36, 'cmp r0, #15', 'cmp r0, #32'),
    ]:
        patch(address, v3.asm(before, address), v3.asm(after, address))
    for address, end, target, before in [
        (0x80007700, 0x8000770C, OSC_GATE, v3.asm(jump(v4.OSC_GATE), 0x80007700) + b'\0\xbf'),
        (0x80131C80, 0x80131C8C, NAME_GATE,
         v3.asm('ldrb.w r0, [r0, #0x70]\ncmp r0, #14\nbhi #0x80131cb4\ntbh [pc, r0, lsl #1]', 0x80131C80)),
        (0x80131B7C, 0x80131B88, FREQ_LABEL,
         v3.asm('ldrb.w r0, [r0, #0x70]\ncmp r0, #13\nbhs #0x80131c0c\nmovs r0, #0\nstrb r0, [r1, #4]', 0x80131B7C)),
    ]:
        replacement = v3.asm(jump(target), address)
        replacement += b'\0\xbf' * ((end - address - len(replacement)) // 2)
        patch(address, before, replacement)
    rows = v3.scatter(base)                         # zero rows and pool untouched
    out.extend(b'\0' * (-len(out) % 16))
    code_file = len(out)
    out.extend(code)
    rows.append([v3.FLASH + code_file, CODE_BASE, len(code), v3.COPY])
    table_at = len(out)
    for row in rows:
        out.extend(struct.pack('<4I', *row))
    struct.pack_into('<2I', out, 0x80, table_at - 0x80, len(out) - 0x80)
    return bytes(out), {'code_base': CODE_BASE, 'code_size': len(code), 'code_file': code_file,
                        'pool_base': v4.NEW_POOL_BASE, 'pool_unchanged_from_v4': True,
                        'hooks': patches}


README = """DOOM/poly-v4 + 17 Wave Lab waves, v6 (RAM, no memory taken) -- 2026-10-06
==========================================================================

Built by the Claude audit session (waves_v6_ram/build_v6.py).

What changed from v5
- Placement, done like the stock waves: the wave code is copied into SDRAM at
  boot by the firmware's own loader, into ~470 KB that nothing in the stock
  firmware or DOOM OS uses (0x83F7A424-0x83FF0000, between a fixed 7.25 MB
  buffer and the non-cacheable area). The OS memory pool is back to exactly
  the size it had in your working v4 build. v3-ram/v4-ram/v5-ram took ~125 KB
  from it.
- Stock Noise1/Noise2 are 6 dB quieter (live and when exported with REC).

Same as v5: all 15 stock waves + 17 new (TYPE 15..31), four voices, DUTY
flicker filter + glide, equal loudness and no DC on the new waves, PolyBLEP on
Sync/CZRes, FREQ display / ENV / START-END working on new waves, release-click
fix, Noise1/2 label fix. New-wave sound is bit-identical to v5.

Files (use BOTH, together):
  SP404MKII_APP1.bin  {app1_size:,} bytes  SHA-256 {app1_sha}
  SP404MKII_APP0.bin  {app0_size:,} bytes  SHA-256 {app0_sha}  (Roland stock)

Status: verified in emulation (waves_v6_ram/test_v6.py). NOT run on hardware.
Roll back with doom-poly-v4-releasefix or Roland 5.52.
"""


def main():
    v3.require((HERE / 'wave_tables.h').read_bytes(), v4b.TABLES_SHA, 'wave_tables.h')
    v4b.write_level_header([[(0.0, 1.0)] * 101 for _ in range(17)])
    levels, _ = v4b.measure(v4b.host_dll())
    level_sha = v4b.write_level_header(levels)
    module, symbols = compile_arm()
    base = v3.BASE.read_bytes()
    image, info = build_image(base, module, symbols)
    image = v5b.apply_extra(image)
    app0 = v3.APP0.read_bytes()
    v3.require(app0, v3.APP0_SHA, 'stock APP0')
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    (OUT / 'SP404MKII_APP1.bin').write_bytes(image)
    (OUT / 'SP404MKII_APP0.bin').write_bytes(app0)
    info['hooks'] += [[a, len(v3.asm(b, a))] for a, b, _ in v5b.EXTRA]
    info.update(revision='v6-ram', status='hardware-unverified', execution='sdram-gap',
                symbols=symbols, base_sha256=v3.BASE_SHA, sha256=v3.sha(image), size=len(image),
                module_sha256=v3.sha(module), wave_level_sha256=level_sha,
                waves_c_sha256=v3.sha((HERE / 'waves.c').read_bytes()), tables_sha256=v4b.TABLES_SHA,
                extra_fixes=[[hex(a), b, c] for a, b, c in v5b.EXTRA], polyblep=['Sync', 'CZRes'],
                noise_gain_db=-6.02, sdram_gap=[hex(BIG_BUFFER_END), hex(NONCACHE)],
                types={str(i + 15): n for i, n in enumerate(v3.TYPES)})
    (OUT / 'manifest.json').write_text(json.dumps(info, indent=2, sort_keys=True) + '\n')
    (OUT / 'README.txt').write_text(README.format(
        app1_size=len(image), app1_sha=v3.sha(image), app0_size=len(app0), app0_sha=v3.sha(app0)),
        encoding='utf-8')
    print(f'{OUT}: APP1 {len(image)} bytes SHA-256 {v3.sha(image)}')
    print(f'module {len(module)} bytes at {LINK_BASE:#x}..{CODE_BASE + info["code_size"]:#x}; pool unchanged')


if __name__ == '__main__':
    main()
