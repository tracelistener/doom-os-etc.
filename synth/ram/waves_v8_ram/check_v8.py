"""Portable copy of the original 13 v8 emulation checks. See synth/V8.md.

v8 = v7 with the grit back (raw DUTY on every new wave, no PolyBLEP). Checks:
1. Layout as v7: the OS pool is identical to v4, the wave code in unused SDRAM, and the boot copy is real.
2. Sampled FP32 wave math bit-identical to v3, Sync and CZRes included.
3. DUTY is raw: no history (no smoother). The DUTY flicker grit and the Sync/CZRes
   aliasing measure like v3 again, not like v7.
4. Kept from v7: perceived loudness, Pulse/Noise -6 dB, exact stock waves,
   the OCT stuck-note fix, zero flash access.
Real firmware code in Unicorn; no RTOS, peripherals or timing. Not a hardware test.
"""
import argparse
import json
from pathlib import Path
import math
import os
import random
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
parser = argparse.ArgumentParser(description='Original v8 emulator checks; no RTOS/peripherals/timing validation.')
for key in ('stock', 'v8', 'v7', 'v3', 'base'):
    parser.add_argument('--'+key, type=Path, required=True)
args = parser.parse_args()
os.environ['DOOM_STOCK_APP1'] = str(args.stock)
sys.path.insert(0, str(Path(HERE).parent / 'testing'))
from wave_single_note import Note, STOCK  # noqa: E402
from wave_cpu_cost import Bench, SINGLETON, STACK  # noqa: E402
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE, UC_HOOK_MEM_READ  # noqa: E402
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,  # noqa: E402
                               UC_ARM_REG_R2, UC_ARM_REG_S0, UC_ARM_REG_S1, UC_ARM_REG_S2,
                               UC_ARM_REG_S3, UC_ARM_REG_SP)

V8, V7, V3, BASE = map(str, (args.v8, args.v7, args.v3, args.base))
MAN = json.loads(args.v8.with_name('manifest.json').read_text())
MAN3 = json.loads(args.v3.with_name('manifest.json').read_text())
FLASH, COPY = 0x60080000, 0x600800E4
CODE_BASE = MAN['code_base']
VOICES = [SINGLETON, 0x8353A310, 0x8353A4C0, 0x8353A670]
EXPORT_BASE, EXPORT_COUNT = 0x8353C000, 0x8353C700
NAMES = ['Sine', 'Sine1', 'Sine2', 'Cos', 'Cos1', 'Cos2', 'Saw', 'Saw+', 'Saw2', 'Tri', 'Tri2', 'Pulse',
         'Pulse+', 'Noise1', 'Noise2', 'FM1:1', 'FM1:2', 'FM1:7', 'CZSaw', 'CZSqr', 'CZRes', 'Sync',
         'Fold', 'Drive', 'LPSaw', 'LPSqr', 'Organ', 'Vowel', 'Table', 'Logic', 'Metal', 'Grit']
failures = []


def check(ok, label):
    print(('PASS ' if ok else 'FAIL ') + label, flush=True)
    if not ok:
        failures.append(label)


def scatter(image):
    a, b = struct.unpack_from('<2I', image, 0x80)
    return [list(struct.unpack_from('<4I', image, i)) for i in range(a + 0x80, b + 0x80, 16)]


def u32(uc, address):
    return struct.unpack('<I', bytes(uc.mem_read(address, 4)))[0]


def bits(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def a_weight(f):
    f = np.asarray(f, dtype=float)
    f2 = f * f
    ra = (12194 ** 2 * f2 ** 2) / ((f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2)) * (f2 + 12194 ** 2))
    one = (12194 ** 2 * 1e12) / ((1e6 + 20.6 ** 2) * math.sqrt((1e6 + 107.7 ** 2) * (1e6 + 737.9 ** 2)) * (1e6 + 12194 ** 2))
    return ra / one


def a_level(x):
    x = np.asarray(x, dtype=float)
    x = x - x.mean()
    spec = np.fft.rfft(x * np.hanning(len(x)))
    f = np.fft.rfftfreq(len(x), 1 / 48000)
    return 10 * math.log10(max(float(np.sum(np.abs(spec * a_weight(np.maximum(f, 1))) ** 2)), 1e-30))


def inharmonic_db(x, f0, sr=48000):
    x = np.asarray(x, dtype=float)
    spec = np.abs(np.fft.rfft((x - x.mean()) * np.blackman(len(x)))) ** 2
    df = sr / len(x)
    harm = np.zeros_like(spec, dtype=bool)
    k = 1
    while k * f0 < sr / 2:
        c = k * f0 / df
        harm[max(0, int(c) - 4):int(c) + 5] = True
        k += 1
    harm[:5] = False
    return 10 * math.log10(max(spec[~harm][5:].sum(), 1e-30) / max(spec[harm].sum(), 1e-30))


class Watched(Note):
    def __init__(self, path):
        super().__init__(path, True)
        self.flash = set()
        self.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, _: self.flash.add(a), begin=0x60000000, end=0x603FFFFF)
        self.uc.hook_add(UC_HOOK_MEM_READ, lambda uc, acc, a, s, v, _: self.flash.add(a),
                         begin=0x60000000, end=0x603FFFFF)

    def held(self, kind, note=24, duties=(50,), samples_each=4096, warm=8):
        self.setup(kind)
        self.uc.mem_write(SINGLETON + 0x48, struct.pack('<i', duties[0]))
        self.note(note, 127)
        for _ in range(warm):
            self.block()
        out = []
        for d in duties:
            self.uc.mem_write(SINGLETON + 0x48, struct.pack('<i', d))
            for _ in range(max(1, samples_each // 64)):
                out += self.block()[0]
        return out


def voices(n):
    return [(n.uc.mem_read(v, 1)[0], n.uc.mem_read(v + 1, 1)[0]) for v in VOICES]


# 1. Layout ------------------------------------------------------------------------------
base, v8 = open(BASE, 'rb').read(), open(V8, 'rb').read()
rows_base, rows_v8 = scatter(base), scatter(v8)
allowed = set(range(0x80, 0x88))
for address, size in MAN['hooks']:
    src, dst = next((r[0], r[1]) for r in rows_base if r[3] == COPY and r[1] <= address < r[1] + r[2])
    allowed.update(range(src - FLASH + address - dst, src - FLASH + address - dst + size))
changed = {i for i in range(len(base)) if base[i] != v8[i]}
check(changed <= allowed and not (set(range(0x023D28, 0x023D34)) & changed)
      and rows_v8[:-1] == rows_base and rows_v8[-1][1:] == [CODE_BASE, MAN['code_size'], COPY],
      f'layout as v7: {len(changed)} bytes of existing code changed, v4 scatter rows intact + one copy row')
uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
uc.mem_map(FLASH, (len(v8) + 0xFFF) & ~0xFFF)
uc.mem_write(FLASH, v8)
uc.mem_map(0x83530000, 0x10000)
uc.mem_map(0x83F00000, 0x100000)
handlers = {r[3] for r in rows_v8}
seen, running = [], [False]


def dispatch(machine, address, size, _):
    if address == 0x60080062:
        running[0] = False
    if address == 0x60080622:
        machine.emu_stop()
    elif address in handlers and not running[0]:
        args = tuple(machine.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        seen.append(args + (address,))
        if args[1] in (0x8353B000, CODE_BASE):
            running[0] = True
        else:
            machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))


uc.hook_add(UC_HOOK_CODE, dispatch)
uc.emu_start(0x60080055, 0x60080622, count=400000)
last = rows_v8[-1]
check(seen == [tuple(r) for r in rows_v8]
      and bytes(uc.mem_read(CODE_BASE, last[2])) == v8[last[0] - FLASH:last[0] - FLASH + last[2]],
      f'real boot loader copies the wave code to {CODE_BASE:#x} (unused SDRAM)')
pn, pools = Note(V8, True), []


def mpl(machine, address, size, _):
    if address == 0x800D2DF2:
        t = machine.reg_read(UC_ARM_REG_R0)
        pools.append((u32(machine, t + 0x14), u32(machine, t + 8)))
        machine.reg_write(UC_ARM_REG_R0, len(pools))
        machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))
    elif address == 0x800D2BB8:
        machine.reg_write(UC_ARM_REG_R0, 0)
        machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))


pn.uc.hook_add(UC_HOOK_CODE, mpl, begin=0x800D2BB8, end=0x800D2DF2)
pn.call(0x80002260)
check(pools[0] == (0x8353D82C, 0x8383A30C - 0x8353D82C), f'OS pool exactly as in v4: {pools[0][1]:,} bytes')

# 2. Wave math == v3 ---------------------------------------------------------------------------
w8, w3 = Watched(V8), Watched(V3)
rng = random.Random(5)
mismatch = 0
for _ in range(2040):
    kind, duty = rng.randrange(15, 32), rng.randrange(101)
    p, dt, r = rng.random(), rng.uniform(1e-5, 0.4), rng.uniform(-1, 1)
    for reg, val in zip((UC_ARM_REG_S0, UC_ARM_REG_S1, UC_ARM_REG_S2, UC_ARM_REG_S3), (p, duty, dt, r)):
        w8.uc.reg_write(reg, bits(val))
    w8.call(MAN['symbols']['wave_core'], kind)
    v3_d = float(np.float32(duty) * np.float32(0.01))
    for reg, val in zip((UC_ARM_REG_S0, UC_ARM_REG_S1, UC_ARM_REG_S2, UC_ARM_REG_S3), (p, v3_d, dt, r)):
        w3.uc.reg_write(reg, bits(val))
    w3.call(MAN3['symbols']['wave_eval'], kind)
    mismatch += w8.uc.reg_read(UC_ARM_REG_S0) != w3.uc.reg_read(UC_ARM_REG_S0)
check(mismatch == 0, 'wave math bit-identical to v3 in 2,040 sampled integer-DUTY cases, all 17 types')

# 3. Grit is back -----------------------------------------------------------------------------
w7 = Watched(V7)
f0 = 440 * 2 ** ((24 - 33) / 12)
rows = []
for kind in (15, 18, 21, 22, 23, 24, 25, 26, 27, 28):
    steady = w8.held(kind, duties=[50] * 16, samples_each=480)
    flick8 = w8.held(kind, duties=[50, 51] * 8, samples_each=480)
    flick7 = w7.held(kind, duties=[50, 51] * 8, samples_each=480)
    flick3 = w3.held(kind, duties=[50, 51] * 8, samples_each=480)
    rows.append((kind, flick8 != steady, inharmonic_db(flick7[:4096], f0), inharmonic_db(flick8[:4096], f0),
                 inharmonic_db(flick3[:4096], f0)))
check(all(r[1] for r in rows) and all(abs(g8 - g3) < 3 and g8 > g7 + 6 for _, _, g7, g8, g3 in rows),
      'sampled DUTY flicker grit matches v3 within 3 dB (10 representative waves)')
print('     ' + ', '.join(f'{NAMES[k]} v7 {g7:.0f} / v8 {g8:.0f} / v3 {g3:.0f} dB' for k, _, g7, g8, g3 in rows))

w8.setup(15)
w8.note(24, 127)
for _ in range(4):
    w8.block()
w8.uc.mem_write(SINGLETON + 0x48, struct.pack('<i', 80))
after_step = w8.block()[0][:8]
fresh = Watched(V8)
fresh.setup(15)
fresh.uc.mem_write(SINGLETON + 0x48, struct.pack('<i', 80))
fresh.note(24, 127)
for _ in range(4):
    fresh.block()
check(after_step == fresh.block()[0][:8], 'DUTY change lands on the very next sample (no glide, no history)')

alias = []
for kind, duty in ((21, 50), (20, 50)):
    for note in (24, 36):
        alias.append((kind, note, inharmonic_db(w7.held(kind, note, [duty])[:4096], 440 * 2 ** ((note - 33) / 12)),
                      inharmonic_db(w8.held(kind, note, [duty])[:4096], 440 * 2 ** ((note - 33) / 12))))
check(all(a8 > a7 + 8 for *_, a7, a8 in alias), 'Sync/CZRes aliasing grit back (PolyBLEP removed)')
print('     ' + ', '.join(f'{NAMES[k]} n{n}: {a7:.0f}->{a8:.0f} dB' for k, n, a7, a8 in alias))

# 4. Kept from v7 -------------------------------------------------------------------------------
ref = a_level(w8.held(0))
lv = {(k, d): a_level(w8.held(k, duties=[d])) - ref for k in list(range(15, 31)) for d in (0, 50, 100)}
lv[(11, 50)] = a_level(w8.held(11)) - ref
check(all(-2.5 <= v <= 1.0 for v in lv.values()),
      f'perceived loudness vs stock Sine: new waves {min(lv.values()):+.1f}..{max(lv.values()):+.1f} dB, '
      f'Pulse {lv[(11, 50)]:+.1f} dB, Logic {lv[(29, 50)]:+.1f} dB')
sn, cn = Note(STOCK, False), Note(V8, True)
bad = []
for kind in range(15):
    s = sn.run(kind, hold_blocks=20, tail_blocks=10)[0]
    c = cn.run(kind, hold_blocks=20, tail_blocks=10)[0]
    shift = 2 if kind in (11, 13, 14) else 1
    if any(x != (y >> shift) for y, x in zip(s, c)):
        bad.append(kind)
check(not bad, f'stock waves exact: Pulse/Noise stock >> 2, the rest stock >> 1 {bad}')


def scenario(steps):
    n = Note(V8, True)
    n.setup(15)
    for step in steps:
        if step == 'render':
            for _ in range(6):
                n.block()
        else:
            n.call(0x1FC00, 0, step[0], step[1], 0, stack0=step[2])
    return voices(n)


check(scenario([(24, 100, 5), 'render', (36, 0, 5), 'render'])[0][1] == 1
      and [v[1] for v in scenario([(24, 100, 1), (28, 100, 2), 'render', (36, 0, 1), 'render'])[:2]] == [1, 0],
      'OCT stuck-note fix still in place (released pad only)')
w8.setup(0)
w8.note(24, 100)
ok = True
for kind in list(range(15, 32)) + [0, 31, 11, 14, 24]:
    w8.call(0x80019E30, SINGLETON, 0x7B, kind)
    for _ in range(6):
        ok &= all(abs(v) <= 16384 for v in w8.block()[0])
w8.setup(24)
for note in (21, 25, 28, 33):
    w8.note(note, 100)
for _ in range(8):
    ok &= any(w8.block()[0])
check(ok and [w8.uc.mem_read(v, 1)[0] for v in VOICES] == [1, 1, 1, 1], 'TYPE sweep and 4-voice chord render in range')
check(not w8.flash, f'zero flash fetches/reads in every synth path above ({len(w8.flash)})')
bench = Bench(V8)
costs = {k: bench.run(k, 24, 128)['cyc'] for k in (0, 11, 15, 21, 24, 26, 27)}
check(max(costs.values()) < 900, 'optimistic instruction-weighted CPU estimate per voice-sample: ' + ', '.join(f'{NAMES[k]} {v:.0f}' for k, v in costs.items()))

print('\nALL PASS' if not failures else f'\n{len(failures)} FAILED: {failures}')
sys.exit(1 if failures else 0)
