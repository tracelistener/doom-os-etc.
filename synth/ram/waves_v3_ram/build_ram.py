"""RAM-resident build of the v3-budget 17-wave Sound Generator (2026-10-06).

Same C source and lookup tables as firmware/doom-poly-waves-v3-budget, but the
module is linked for SDRAM and copied there at boot. No Sound Generator code
or table is fetched from QSPI flash while audio runs. That matters because the
SP erases and reprograms its own flash at runtime (the settings sector at
0x603FF000, see analysis/WAVE_V1_CLAUDE_AUDIT.md).

Base: firmware/doom-poly-v4-releasefix (release-click fix already applied).
Placement: v1's hardware-booted layout, a copy row after zero-init at
0x8353E000, with the OS pool base moved up past the module.
Writes firmware/doom-poly-waves-v3-ram/. Nothing is flashed or uploaded.

    python waves_v3_ram/build_ram.py          (from the sp404mk2 folder)
"""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / '.poly_vendor'))
sys.path.insert(0, str(HERE.parents[1] / 'vendor'))
import patch_poly as v2  # noqa: E402  (archived v2-v4 sources, unchanged)
import patch_poly_v4 as v4  # noqa: E402

BASE = ROOT / 'firmware' / 'doom-poly-v4-releasefix' / 'SP404MKII_APP1.bin'
BASE_SHA = '1b295c877f61c445f761a8a607ddb4f34327ba0875de002ebedb0a6d9cf0e18e'
APP0 = ROOT / 'firmware' / 'v552' / 'sp404mk2_sys_v552' / 'SP404MKII_APP0.bin'
APP0_SHA = '3e35ff30137f741a715566e205384f9b434ca7ac3847e247bad3f0c9c8a2bd23'
SOURCES = {'waves.c': 'bdff9225abc5303700e4cb541952c5f39606473a69cf090466bd21d63300dcac',
           'wave_tables.h': '8d59eb235dc4dcb69906f91f17547ebfe9bc2c2f71e9002162f3995a9bdfb2b9'}
# The same sources linked at v3-budget's flash address must reproduce its module.
FLASH_LINK, FLASH_MODULE_SHA = 0x60334C00, 'af20e766bf9344abeec79e5f5453b7898439c8bede738ee1dcf1cfe28baf217e'
ZIG = ROOT / '.wave_toolchain' / 'ziglang' / 'zig.exe'
OUT = ROOT / 'firmware' / 'doom-poly-waves-v3-ram'

FLASH = 0x60080000
COPY, ZERO = 0x600800E4, 0x60080100
CODE_BASE = 0x8353E000                 # v1's SDRAM placement (booted on hardware)
LINK_BASE = CODE_BASE + 0x400
OSC_GATE, NAME_GATE, FREQ_LABEL = CODE_BASE, CODE_BASE + 0x100, CODE_BASE + 0x180
OS_POOL_END = 0x8383A30C
TYPES = ['FM1:1', 'FM1:2', 'FM1:7', 'CZSaw', 'CZSqr', 'CZRes', 'Sync', 'Fold', 'Drive',
         'LPSaw', 'LPSqr', 'Organ', 'Vowel', 'Table', 'Logic', 'Metal', 'Grit']
LINKER = """ENTRY(wave_output)
SECTIONS {{
    . = {base:#x};
    .text : {{ KEEP(*(.text.wave_eval)) KEEP(*(.text.wave_name)) *(.text*) }}
    .rodata : ALIGN(16) {{ *(.rodata*) }}
    .data : ALIGN(16) {{ *(.data*) }}
    .bss : ALIGN(16) {{ *(.bss*) *(COMMON) }}
    /DISCARD/ : {{ *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }}
}}
"""
load, call, jump = v4.load, v4.call, v4.jump


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(data, digest, what):
    if sha(data) != digest:
        raise ValueError(f'{what}: unexpected SHA-256 {sha(data)}')


def asm(source, address):
    return v2.assemble(source, address)


def read_elf(data, link_base, limit):
    """Fixed-address allocated sections and the three exported symbols."""
    if data[:7] != b'\x7fELF\x01\x01\x01' or struct.unpack_from('<H', data, 18)[0] != 40:
        raise ValueError('expected little-endian ARM ELF32')
    shoff = struct.unpack_from('<I', data, 32)[0]
    shsize, count, _ = struct.unpack_from('<3H', data, 46)
    sections = [struct.unpack_from('<10I', data, shoff + i * shsize) for i in range(count)]
    syms, blocks, end = {}, [], link_base
    for name, kind, flags, addr, off, size, link, info, align, entsize in sections:
        if flags & 2 and size:
            if kind == 8:
                raise ValueError('mutable BSS is not allowed in the waveform module')
            if not link_base <= addr < limit or addr + size > limit:
                raise ValueError('unexpected allocated ELF address')
            blocks.append((addr, data[off:off + size]))
            end = max(end, addr + size)
        if kind == 2:
            strings = sections[link]
            text = data[strings[4]:strings[4] + strings[5]]
            for at in range(off, off + size, entsize):
                n, value = struct.unpack_from('<2I', data, at)
                label = text[n:].split(b'\0')[0].decode('ascii')
                if label in ('wave_eval', 'wave_output', 'wave_name'):
                    syms[label] = value
    if set(syms) != {'wave_eval', 'wave_output', 'wave_name'}:
        raise ValueError('wave exports missing')
    out = bytearray(end - link_base)
    for addr, block in blocks:
        out[addr - link_base:addr - link_base + len(block)] = block
    return bytes(out), syms


def compile_module(link_base, limit, folder):
    if subprocess.check_output([str(ZIG), 'version'], text=True).strip() != '0.13.0':
        raise ValueError('reproducible build requires Zig 0.13.0')
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'waves.ld').write_text(LINKER.format(base=link_base), encoding='ascii', newline='\n')
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(HERE / '.zig-cache'),
               ZIG_LOCAL_CACHE_DIR=str(HERE / '.zig-cache'))
    args = [str(ZIG), 'cc', '-target', 'thumb-freestanding-eabihf', '-mcpu=cortex_m7+vfp4d16',
            '-mfpu=vfpv4-d16', '-mfloat-abi=hard', '-O2', '-ffreestanding',
            '-fno-builtin', '-fno-stack-protector', '-ffp-contract=off',
            '-ffunction-sections', '-fdata-sections',
            '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
            '-nostdlib', '-Wl,--no-undefined', '-Wl,--build-id=none',
            '-Wl,-e,wave_output', '-Wl,-T,' + str(folder / 'waves.ld'), '-I', str(HERE),
            str(HERE / 'waves.c'), '-o', str(folder / 'waves.elf')]
    subprocess.run(args, check=True, cwd=HERE, env=env)
    blob, symbols = read_elf((folder / 'waves.elf').read_bytes(), link_base, limit)
    (folder / 'waves.bin').write_bytes(blob)
    (folder / 'symbols.json').write_text(json.dumps(symbols, indent=2, sort_keys=True) + '\n')
    return blob, symbols


def scatter(image):
    a, b = struct.unpack_from('<2I', image, 0x80)
    return [list(struct.unpack_from('<4I', image, i)) for i in range(a + 0x80, b + 0x80, 16)]


def file_offset(image, address, size):
    for src, dst, n, handler in scatter(image):
        if handler == COPY and dst <= address and address + size <= dst + n:
            return src - FLASH + address - dst
    raise ValueError(f'{address:#x} is not in a copied region')


def gate_sources(symbols):
    # Identical to v3-budget's gates; only their address differs (SDRAM, not flash).
    return [
        (OSC_GATE, NAME_GATE, f'''
            {load('r12', 0x80132AF3)}
            cmp lr, r12
            beq original_gate
            ldrb.w r1, [r0, #0x70]
            subs r1, #15
            cmp r1, #16
            bhi original_gate
            push {{r4-r6, lr}}
            vpush {{d8-d12}}
            mov r4, r0
            {call(symbols['wave_output'])}
            {jump(0x80007774)}
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
    require(base, BASE_SHA, 'DOOM/poly-v4 release-fix base')
    code = bytearray(0x400)
    for start, end, source in gate_sources(symbols):
        blob = asm(source, start)
        if start + len(blob) > end:
            raise ValueError('wave bridge overflow')
        code[start - CODE_BASE:start - CODE_BASE + len(blob)] = blob
    code.extend(module)
    code.extend(b'\0' * (-len(code) % 16))
    # The stock pool base is 12 mod 32; keep that alignment past the module.
    pool_base = ((CODE_BASE + len(code) + 31) & ~31) + 12
    if pool_base >= OS_POOL_END:
        raise ValueError('wave module exceeds OS pool')
    out = bytearray(base)
    patches = []

    def patch(address, expected, replacement):
        off = file_offset(base, address, len(expected))
        if bytes(base[off:off + len(expected)]) != expected or len(replacement) != len(expected):
            raise ValueError(f'unexpected bytes or size at {address:#x}')
        out[off:off + len(expected)] = replacement
        patches.append([address, len(replacement)])

    for address, before, after in [
        (0x8013269C, 'movs r0, #14', 'movs r0, #31'),     # TYPE range max
        (0x801326A4, 'cmp r0, #14', 'cmp r0, #31'),       # FREQ range guard
        (0x801326C6, 'cmp r1, #5', 'cmp r1, #22'),        # DUTY range: types 10..31
        (0x80019FC6, 'cmp r0, #4', 'cmp r0, #21'),        # setter accepts DUTY: 10..31
        (0x80131CCC, 'cmp r2, #14', 'cmp r2, #31'),       # FREQ value text
        (0x80131D9E, 'cmp r1, #4', 'cmp r1, #21'),        # DUTY value text
        (0x80131BAA, 'cmp r0, #4', 'cmp r0, #21'),        # DUTY label
        (0x80132A36, 'cmp r0, #15', 'cmp r0, #32'),       # export period guard
        (0x80002268, f'movw r0, #{v4.NEW_POOL_BASE & 0xFFFF}', f'movw r0, #{pool_base & 0xFFFF}'),
        (0x80002270, f'movt r0, #{v4.NEW_POOL_BASE >> 16}', f'movt r0, #{pool_base >> 16}'),
    ]:
        patch(address, asm(before, address), asm(after, address))
    for address, end, target, before in [
        (0x80007700, 0x8000770C, OSC_GATE, asm(jump(v4.OSC_GATE), 0x80007700) + b'\0\xbf'),
        (0x80131C80, 0x80131C8C, NAME_GATE,
         asm('ldrb.w r0, [r0, #0x70]\ncmp r0, #14\nbhi #0x80131cb4\ntbh [pc, r0, lsl #1]', 0x80131C80)),
        (0x80131B7C, 0x80131B88, FREQ_LABEL,
         asm('ldrb.w r0, [r0, #0x70]\ncmp r0, #13\nbhs #0x80131c0c\nmovs r0, #0\nstrb r0, [r1, #4]', 0x80131B7C)),
    ]:
        replacement = asm(jump(target), address)
        replacement += b'\0\xbf' * ((end - address - len(replacement)) // 2)
        patch(address, before, replacement)
    rows = scatter(base)
    zi = [row for row in rows if row[1] == 0x802E7800]
    if len(zi) != 1 or zi[0][3] != ZERO or zi[0][1] + zi[0][2] != v4.NEW_POOL_BASE:
        raise ValueError('unexpected poly-v4 zero-init row')
    zi[0][2] = pool_base - zi[0][1]
    # Copy after the zero rows (appended last), exactly like v1 and v4.
    out.extend(b'\0' * (-len(out) % 16))
    code_file = len(out)
    out.extend(code)
    rows.append([FLASH + code_file, CODE_BASE, len(code), COPY])
    table_at = len(out)
    for row in rows:
        out.extend(struct.pack('<4I', *row))
    struct.pack_into('<2I', out, 0x80, table_at - 0x80, len(out) - 0x80)
    return bytes(out), {'code_base': CODE_BASE, 'code_size': len(code), 'code_file': code_file,
                        'pool_base': pool_base, 'pool_bytes': OS_POOL_END - pool_base,
                        'pool_bytes_stock': OS_POOL_END - v2.OLD_OS_POOL_BASE,
                        'pool_bytes_v4': OS_POOL_END - v4.NEW_POOL_BASE,
                        'hooks': patches, 'symbols': symbols}


README = """DOOM/poly-v4 + 17 Wave Lab waves, RAM build (v3-ram) -- {date}
================================================================

The v3-budget wave build, with all Sound Generator code in RAM.
Built by the Claude audit session from waves_v3_ram/build_ram.py.

What it is
- Base: doom-poly-v4-releasefix (your working v4 + the release-click fix).
- All 15 stock waves + 17 Wave Lab waves (TYPE 15..31), four voices, DUTY as
  timbre, shared params, pad LEDs, chord REC. Noise1/2 label fixed.
- Wave math is identical to v3-budget (same waves.c and lookup tables, checked
  by hash). The only difference is where it runs: copied to SDRAM at boot,
  not executed from flash. The SP erases and rewrites its own flash at
  runtime (the settings sector), and code running from flash during that
  erase can crash. Here no synth code or table lives in flash.

Trade-off
- The OS memory pool is {pool_kib:.1f} KB smaller than v4 ({pool_pct:.1f}% of stock).
  v1 took 49 KB more than v4 and ran fine; this takes about twice that. If anything
  memory-heavy misbehaves (loading big projects, resampling, exporting),
  report it.

CPU (optimistic emulator model, 4 voices): LPSaw/LPSqr ~20%, Vowel ~16%,
Organ ~12%, everything else 6-8%. v1's LPSaw was 127%.

Files (use BOTH, together):
  SP404MKII_APP1.bin  {app1_size:,} bytes  SHA-256 {app1_sha}
  SP404MKII_APP0.bin  {app0_size:,} bytes  SHA-256 {app0_sha}  (Roland stock)

Status: verified in emulation (see waves_v3_ram/test_ram.py). NOT yet run on
hardware. Keep the official 5.52 update to roll back.

Suggested first test
1. Boot, play a sample pad, open Sound Generator.
2. Sine, one pad: hold, release (should fade, no click).
3. FM1:1 and Fold, sweep DUTY. Then LPSaw at one voice, then 2 and 4 voices.
4. Turn TYPE through the whole list with a note held (this froze v1).
5. Chord REC once.
"""


def main():
    for name, digest in SOURCES.items():
        require((HERE / name).read_bytes(), digest, name)
    flash_blob, _ = compile_module(FLASH_LINK, 0x60400000, HERE / 'build' / 'flash-check')
    require(flash_blob, FLASH_MODULE_SHA, 'reproduced v3-budget flash module')
    module, symbols = compile_module(LINK_BASE, OS_POOL_END, HERE / 'build' / 'ram')
    if len(module) != len(flash_blob):
        raise ValueError('RAM and flash links differ in size')
    base = BASE.read_bytes()
    image, info = build_image(base, module, symbols)
    app0 = APP0.read_bytes()
    require(app0, APP0_SHA, 'stock APP0')
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    (OUT / 'SP404MKII_APP1.bin').write_bytes(image)
    (OUT / 'SP404MKII_APP0.bin').write_bytes(app0)
    info.update(revision='v3-ram', status='hardware-unverified', execution='sdram',
                base_sha256=BASE_SHA, sha256=sha(image), size=len(image),
                module_sha256=sha(module), flash_module_sha256=FLASH_MODULE_SHA,
                sources=SOURCES, types={str(i + 15): n for i, n in enumerate(TYPES)})
    (OUT / 'manifest.json').write_text(json.dumps(info, indent=2, sort_keys=True) + '\n')
    shrink = info['pool_bytes_v4'] - info['pool_bytes']
    (OUT / 'README.txt').write_text(README.format(
        date='2026-10-06', pool_kib=shrink / 1024,
        pool_pct=100 * info['pool_bytes'] / info['pool_bytes_stock'],
        app1_size=len(image), app1_sha=sha(image), app0_size=len(app0), app0_sha=sha(app0)),
        encoding='utf-8')
    print(f'{OUT}: APP1 {len(image)} bytes SHA-256 {sha(image)}')
    print(f'module {len(module)} bytes at {LINK_BASE:#x}; pool base {info["pool_base"]:#x}, '
          f'{shrink} bytes less than v4')


if __name__ == '__main__':
    main()
