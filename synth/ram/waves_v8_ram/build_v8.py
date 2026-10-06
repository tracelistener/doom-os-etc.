"""Build Wave Lab v8 (RAM, OS pool untouched): all the v1-v3 grit back.

v8 = v7 with the two grit-removing changes undone:
- No DUTY smoother: every new wave takes the stock integer DUTY directly, as in
  v1-v3. Every step, and knob jitter at rest, lands at once.
- No PolyBLEP on Sync/CZRes: their naive cycle-restart step (aliasing grit) is back.
The raw wave math is bit-identical to v3 at every DUTY setting. Kept from v7:
perceived-loudness and DC tables, Pulse and Noise1/2 at -6 dB, the stuck-note
fix on OCT change, FREQ display/ENV/START-END on new waves, the release-click
fix, and the wave code in unused SDRAM with the OS pool exactly as in v4.

    python waves_v8_ram/build_v8.py          (from the sp404mk2 folder)
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / 'waves_v7_ram'))
import build_v7 as v7b  # noqa: E402  (also sets up the v3-v6 pipeline modules)

v7b.v4b.HERE = v7b.v6b.HERE = HERE
OUT = ROOT / 'firmware' / 'doom-poly-waves-v8-ram'
v3, v4b, v5b, v6b, v4 = v7b.v3, v7b.v4b, v7b.v5b, v7b.v6b, v7b.v4


def compile_arm():
    folder = HERE / 'build' / 'ram'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'waves.ld').write_text(v3.LINKER.format(base=v6b.LINK_BASE), encoding='ascii', newline='\n')
    subprocess.run([str(v3.ZIG), 'cc', '-target', 'thumb-freestanding-eabihf',
                    '-mcpu=cortex_m7+vfp4d16', '-mfpu=vfpv4-d16', '-mfloat-abi=hard', *v4b.COMMON,
                    '-nostdlib', '-Wl,--no-undefined', '-Wl,--build-id=none', '-Wl,-e,wave_output',
                    '-Wl,-T,' + str(folder / 'waves.ld'), '-I', str(HERE), str(HERE / 'waves.c'),
                    '-o', str(folder / 'waves.elf')], check=True, cwd=HERE, env=v4b.ZIG_ENV)
    elf = (folder / 'waves.elf').read_bytes()
    blob, symbols = v3.read_elf(elf, v6b.LINK_BASE, v6b.NONCACHE)
    symbols['wave_core'] = v4b.elf_symbol(elf, 'wave_core')
    (folder / 'waves.bin').write_bytes(blob)
    return blob, symbols


README = """DOOM/poly-v4 + 17 Wave Lab waves, v8 (RAM, no memory taken) -- 2026-10-06
==========================================================================

Built by the Claude audit session (waves_v8_ram/build_v8.py). v7 with all the
grit put back:

- Every new wave takes DUTY raw again, exactly as in v1-v3: each DUTY step and
  the knob's tiny jitter land immediately (the crunchy grit). The v4 DUTY
  smoother is gone.
- Sync and CZRes are back to their naive restart (no PolyBLEP): their gritty
  aliasing is back.
- The wave math is bit-identical to v3 at every DUTY setting.

Kept from v7: perceived-loudness matching and DC removal on the new waves,
Pulse and Noise1/2 6 dB quieter, the stuck-note fix on OCT change, FREQ display
/ ENV / START-END on new waves, the release-click fix, and the wave code in unused
SDRAM with the OS pool exactly as in your working v4.

Files (use BOTH, together):
  SP404MKII_APP1.bin  {app1_size:,} bytes  SHA-256 {app1_sha}
  SP404MKII_APP0.bin  {app0_size:,} bytes  SHA-256 {app0_sha}  (Roland stock)

Status: verified in emulation (waves_v8_ram/test_v8.py). NOT run on hardware.
Roll back with doom-poly-waves-v7-ram, doom-poly-v4-releasefix or Roland 5.52.
"""


def main():
    v3.require((HERE / 'wave_tables.h').read_bytes(), v4b.TABLES_SHA, 'wave_tables.h')
    v4b.write_level_header([[(0.0, 1.0)] * 101 for _ in range(17)])
    levels, _ = v7b.measure(v4b.host_dll())
    level_sha = v4b.write_level_header(levels)
    module, symbols = compile_arm()
    base = v3.BASE.read_bytes()
    image, info = v6b.build_image(base, module, symbols)
    image = v5b.apply_extra(image)
    image, note_size = v7b.patch_note_engine(image)
    app0 = v3.APP0.read_bytes()
    v3.require(app0, v3.APP0_SHA, 'stock APP0')
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    (OUT / 'SP404MKII_APP1.bin').write_bytes(image)
    (OUT / 'SP404MKII_APP0.bin').write_bytes(app0)
    info['hooks'] += [[a, len(v3.asm(b, a))] for a, b, _ in v5b.EXTRA]
    info['hooks'].append([v4.NOTE_ENGINE, v4.PARAM_ENGINE - v4.NOTE_ENGINE])
    info.update(revision='v8-ram', status='hardware-unverified', execution='sdram-gap',
                symbols=symbols, base_sha256=v3.BASE_SHA, sha256=v3.sha(image), size=len(image),
                module_sha256=v3.sha(module), wave_level_sha256=level_sha,
                waves_c_sha256=v3.sha((HERE / 'waves.c').read_bytes()), tables_sha256=v4b.TABLES_SHA,
                extra_fixes=[[hex(a), b, c] for a, b, c in v5b.EXTRA],
                duty='raw (stock integer DUTY, no smoothing)', polyblep=[],
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
    print(f'{OUT}: APP1 {len(image)} bytes SHA-256 {v3.sha(image)}')


if __name__ == '__main__':
    main()
