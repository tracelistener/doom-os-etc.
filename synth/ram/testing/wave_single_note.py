"""Render one complete Sound Generator pad note (note-on, hold, note-off) through
the real firmware code of several APP1 images and compare the bus output."""
import os, struct, sys, random
from pathlib import Path
from wave_cpu_cost import Bench, SINGLETON, STACK, RET
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import *

STOCK = os.environ['DOOM_STOCK_APP1']
BUS = 0x30004000
FRAMES, CH = 64, 14

def doom_image():
    os.environ.setdefault('DOOM_STOCK_APP1', STOCK)
    import build
    return build.apply_container(build.patch_container(), Path(STOCK).read_bytes())

class Note:
    def __init__(self, path_or_bytes, poly):
        self.b = Bench(path_or_bytes)
        self.uc = self.b.uc
        self.poly = poly
        self.rng = random.Random(1234)
        self.uc.hook_add(UC_HOOK_CODE, self.hook)
        # random() feeds only phase-wrap bookkeeping; stub it identically for every image
        self.uc.hook_add(UC_HOOK_CODE, lambda uc, a, s, _: (uc.reg_write(UC_ARM_REG_R0, self.rng.getrandbits(31)),
                         uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))) if a == 0x800D314E else None,
                         begin=0x800D314E, end=0x800D3150)

    def hook(self, uc, addr, size, _):
        if addr in (0x800DED08, 0x800DECF8, 0x800D31EE):   # critical section enter/exit, task delay
            uc.reg_write(UC_ARM_REG_R0, 0)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def call(self, fn, r0=0, r1=0, r2=0, r3=0, stack0=0):
        uc = self.uc
        uc.reg_write(UC_ARM_REG_SP, STACK)
        uc.mem_write(STACK, struct.pack('<I', stack0 & 0xFFFFFFFF))
        for reg, v in ((UC_ARM_REG_R0, r0), (UC_ARM_REG_R1, r1), (UC_ARM_REG_R2, r2), (UC_ARM_REG_R3, r3)):
            uc.reg_write(reg, v & 0xFFFFFFFF)
        uc.reg_write(UC_ARM_REG_LR, RET | 1)
        uc.emu_start(fn | 1, RET, count=5_000_000)
        assert uc.reg_read(UC_ARM_REG_SP) == STACK, 'unbalanced stack'
        return uc.reg_read(UC_ARM_REG_R0)

    def setup(self, kind, level=127, env=0):
        st, uc = SINGLETON, self.uc
        self.call(0x80132340, st)                   # stock voice reset
        p = lambda off, v: uc.mem_write(st + off, struct.pack('<i', v))
        uc.mem_write(st, b'\0\0\0\0')
        p(0x08, 0); p(0x0C, 0); p(0x10, env); p(0x48, 50); p(0x60, 4400); p(0x180, 3)
        uc.mem_write(st + 0x70, bytes([kind, kind]))
        p(0x74, level * 100); p(0x198, level * 100)
        for c in range(3):                          # idle clones
            uc.mem_write(0x8353A310 + c * 0x1B0, b'\0\0\0\0')

    def note(self, note, vel):
        if self.poly:
            self.call(0x1FC00, 0, note, vel, 0, stack0=5)       # v2/v4 note dispatcher
        else:
            self.call(0x80132428, SINGLETON, note, vel, 0, stack0=5)

    def block(self):
        self.uc.mem_write(BUS, bytes(FRAMES * CH * 4))
        if self.poly:
            self.call(0x1FD80, 0, BUS, FRAMES, CH)
        else:
            self.call(0x1CB84, SINGLETON, BUS, FRAMES, CH)
        raw = struct.unpack('<%di' % (FRAMES * CH), bytes(self.uc.mem_read(BUS, FRAMES * CH * 4)))
        return [raw[f * CH + 0] for f in range(FRAMES)], [raw[f * CH + 1] for f in range(FRAMES)]

    def run(self, kind, note=24, hold_blocks=40, tail_blocks=12, level=127, env=0):
        self.setup(kind, level, env)
        self.note(note, level)
        L, R = [], []
        for _ in range(hold_blocks):
            l, r = self.block(); L += l; R += r
        off_at = len(L)
        self.note(note, 0)
        for _ in range(tail_blocks):
            l, r = self.block(); L += l; R += r
        return L, R, off_at
