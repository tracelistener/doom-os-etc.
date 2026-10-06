"""Estimate per-sample CPU cost of every Sound Generator type (Claude audit, 2026-10-06).

Runs the real oscillator entry (0x80007700 -> wave gate -> stock body or wave
module) of an APP1 image in Unicorn and weights the executed instructions with
Cortex-M7 FPU timings. Read-only: loads a local image, writes nothing.

Usage (workspace root, PYTHONPATH not needed):
    python analysis/wave_cpu_cost.py [APP1.bin]
    python analysis/wave_cpu_cost.py --divs [APP1.bin]

Model: 1 cycle/instruction, VDIV/VSQRT.F32 14, .F64 30, VMLA/VMLS 2, VMRS 2,
taken call/return 3, stock random() stubbed at 20 cycles. It is OPTIMISTIC:
no cache misses, bus stalls, branch mispredicts or interrupts. Budget is
600 MHz / 48 kHz = 12,500 cycles per output frame for the WHOLE CPU.
"""
import math
from pathlib import Path
import os
import random
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, '.poly_vendor'))
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE  # noqa: E402
from unicorn.arm_const import (UC_ARM_REG_APSR, UC_ARM_REG_C1_C0_2, UC_ARM_REG_FPEXC,  # noqa: E402
                               UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_S0,
                               UC_ARM_REG_SP)
from capstone import Cs, CS_ARCH_ARM, CS_MODE_MCLASS, CS_MODE_THUMB  # noqa: E402

FLASH = 0x60080000
COPY, DECOMP = 0x600800E4, 0x60080088
SINGLETON = 0x80249800
STACK, RET = 0x30010000, 0x3001F000
NAMES = ['Sine', 'Sine1', 'Sine2', 'Cos', 'Cos1', 'Cos2', 'Saw', 'Saw+', 'Saw2', 'Tri', 'Tri2',
         'Pulse', 'Pulse+', 'Noise1', 'Noise2', 'FM1:1', 'FM1:2', 'FM1:7', 'CZSaw', 'CZSqr',
         'CZRes', 'Sync', 'Fold', 'Drive', 'LPSaw', 'LPSqr', 'Organ', 'Vowel', 'Table', 'Logic',
         'Metal', 'Grit']
BUDGET = 600e6 / 48000
NOTES = {'C2': 12, 'C4': 24, 'A4': 33, 'C6': 48}   # internal note 33 = A4 = 440 Hz


def decompress(src, outlen):
    o = bytearray()
    i = 0
    while len(o) < outlen:
        tok = src[i]; i += 1
        lit = tok & 7
        if lit == 0:
            lit = src[i]; i += 1
        cpy = tok >> 4
        if cpy == 0:
            cpy = src[i]; i += 1
        n = lit - 1
        if n > 0:
            o += src[i:i + n]; i += n
        if tok & 8:
            off = src[i]; i += 1
            start = len(o) - off
            for k in range(cpy + 2):
                o.append(o[start + k])
        else:
            o += b'\0' * cpy
    return bytes(o[:outlen])


class Bench:
    def __init__(self, path):
        image = path if isinstance(path, bytes) else Path(path).read_bytes()
        rel0, rel1 = struct.unpack_from('<2I', image, 0x80)
        rows = [struct.unpack_from('<4I', image, off) for off in range(0x80 + rel0, 0x80 + rel1, 16)]
        uc = self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        for base, size in ((0x0, 0x20000), (0x20000000, 0x80000), (0x20200000, 0x100000),
                           (0x80000000, 0x4000000), (0x30000000, 0x20000), (0x60000000, 0x400000)):
            uc.mem_map(base, size)
        uc.mem_write(FLASH, image[:0x380000])
        for load, dst, size, fn in rows:
            if fn == COPY:
                uc.mem_write(dst, image[load - FLASH:load - FLASH + size])
            elif fn == DECOMP:
                uc.mem_write(dst, decompress(image[load - FLASH:], size))
        uc.reg_write(UC_ARM_REG_C1_C0_2, uc.reg_read(UC_ARM_REG_C1_C0_2) | (0xF << 20))
        uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        uc.mem_write(RET, b'\x00\xbf\x00\xbf')
        self.md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
        self.cache = {}
        self.stats = {}
        uc.hook_add(UC_HOOK_CODE, self.hook)

    def decode(self, addr, size):
        key = (addr, size)
        if key not in self.cache:
            ins = next(self.md.disasm(bytes(self.uc.mem_read(addr, size)), addr), None)
            self.cache[key] = (ins.mnemonic, ins.op_str) if ins else ('?', '')
        return self.cache[key]

    @staticmethod
    def cost(mn, op):
        if mn.startswith(('vdiv', 'vsqrt')):
            return 30 if '.f64' in mn else 14
        if mn.startswith(('vmla', 'vmls', 'vnmla', 'vnmls')):
            return 4 if '.f64' in mn else 2
        if '.f64' in mn and mn.startswith(('vmul', 'vadd', 'vsub')):
            return 2
        if mn.startswith('vmrs'):
            return 2
        if mn.split('.')[0] in ('bl', 'blx', 'bx') or (mn.startswith('pop') and 'pc' in op):
            return 3
        return 1

    def hook(self, uc, addr, size, _):
        st = self.stats
        if addr == 0x800D314E:   # stock random() veneer: hardware-dependent, stub it
            st['cyc'] = st.get('cyc', 0) + 20
            uc.reg_write(UC_ARM_REG_R0, random.getrandbits(31))
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
            return
        mn, op = self.decode(addr, size)
        st['n'] = st.get('n', 0) + 1
        st['cyc'] = st.get('cyc', 0) + self.cost(mn, op)
        if mn.startswith('vdiv'):
            st['div'] = st.get('div', 0) + 1
        if mn.startswith('vsel'):   # FPv5 VSEL is missing from Unicorn 2.1.4: emulate it
            regs = [r.strip() for r in op.split(',')]
            nzcv = uc.reg_read(UC_ARM_REG_APSR) >> 28
            n, z, v = (nzcv >> 3) & 1, (nzcv >> 2) & 1, nzcv & 1
            take = {'gt': z == 0 and n == v, 'ge': n == v, 'eq': z == 1, 'vs': v == 1}[mn[4:6]]
            src = regs[1] if take else regs[2]
            uc.reg_write(UC_ARM_REG_S0 + int(regs[0][1:]), uc.reg_read(UC_ARM_REG_S0 + int(src[1:])))
            uc.reg_write(UC_ARM_REG_PC, (addr + size) | 1)

    def voice(self, kind, note, duty=50):
        """Settled, active voice with no fade, increments as the stock tail computes them."""
        f = 440.0 * 2 ** ((note - 33) / 12)
        w = self.uc.mem_write
        w(SINGLETON + 0x00, b'\x01\x00')
        w(SINGLETON + 0x18, struct.pack('<d', 0.13))
        w(SINGLETON + 0x20, struct.pack('<d', f / 48000))
        w(SINGLETON + 0x30, struct.pack('<d', 0.8))
        w(SINGLETON + 0x38, struct.pack('<d', 2 * math.pi * f / 48000))
        w(SINGLETON + 0x40, struct.pack('<d', 0.4))
        w(SINGLETON + 0x48, struct.pack('<i', duty))
        w(SINGLETON + 0x4C, struct.pack('<i', 200))
        w(SINGLETON + 0x5C, struct.pack('<i', note))
        w(SINGLETON + 0x60, struct.pack('<i', 4400))
        w(SINGLETON + 0x70, bytes([kind, kind]))
        w(SINGLETON + 0x74, struct.pack('<i', 100))
        w(SINGLETON + 0x198, struct.pack('<i', 100))

    def run(self, kind, note, samples=256):
        self.voice(kind, note)
        self.stats.clear()
        uc = self.uc
        for _ in range(samples):
            uc.reg_write(UC_ARM_REG_SP, STACK)
            uc.reg_write(UC_ARM_REG_LR, RET | 1)
            uc.reg_write(UC_ARM_REG_R0, SINGLETON)
            uc.emu_start(0x80007700 | 1, RET, count=2_000_000)
        return {k: v / samples for k, v in self.stats.items()}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    path = args[0] if args else os.path.join(ROOT, 'firmware', 'doom-poly-waves-v1', 'SP404MKII_APP1.bin')
    bench = Bench(path)
    print(f'{os.path.basename(os.path.dirname(path))}: budget {BUDGET:.0f} cycles per 48 kHz frame (whole CPU)')
    if '--divs' in sys.argv:
        for kind in (0, 23, 24, 25, 26, 27):
            cells = []
            for label, note in NOTES.items():
                r = bench.run(kind, note, 64)
                cells.append(f"{label}: {r.get('div', 0):5.1f} vdiv {r['n']:6.0f} instr")
            print(f'{NAMES[kind]:<6} ' + ' | '.join(cells))
        return
    print(f"{'type':>4} {'name':<7}" + ''.join(f'{k:>24}' for k in NOTES))
    for kind in range(len(NAMES)):
        cells = []
        for note in NOTES.values():
            cyc = bench.run(kind, note)['cyc']
            cells.append(f'{cyc:6.0f}c {100 * cyc / BUDGET:5.1f}%/v {400 * cyc / BUDGET:6.1f}%x4')
        print(f'{kind:>4} {NAMES[kind]:<7}' + ''.join(f'{c:>24}' for c in cells), flush=True)


if __name__ == '__main__':
    main()
