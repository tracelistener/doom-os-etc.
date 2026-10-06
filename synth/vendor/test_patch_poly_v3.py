"""v3 tests: static layout plus Unicorn runs of the real stock setter.

The emulator loads every initialized region of the v3 APP1 image (ITCM code,
the SDRAM code/data image and its RW data), so the parameter wrapper executes
the unmodified Roland setter, its exp2f helper and the stock LEVEL getter.
Only the preview start routine and the voice renderer are stubbed.
"""

import math
import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, ".poly_vendor"))

import patch_poly as v2  # noqa: E402
import patch_poly_v3 as v3  # noqa: E402
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_THUMB, Uc  # noqa: E402
from unicorn.arm_const import (  # noqa: E402
    UC_ARM_REG_C1_C0_2,
    UC_ARM_REG_FPEXC,
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_R3,
    UC_ARM_REG_SP,
)

FLASH = 0x60080000
STACK_TOP = 0x30008000
RETURN = 0x30010000
PREVIEW = 0x800D0668
GETTER = 0x80020450
RENDER = 0x0001CB84
VOICES = [v2.SINGLETON] + [v2.CLONE_BASE + i * 0x1B0 for i in range(3)]
TYPE, FREQ, LEVEL, PADLEN, DUTY, BALANCE, TUNE, ENV, PREVIEW_ID = (
    0x7B, 0x7C, 0x7D, 0x7E, 0x7F, 0x80, 0x81, 0x82, 0x7A)


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
            o += b"\x00" * cpy
    return bytes(o[:outlen])


def regions(image):
    """Initialized (runtime address, bytes) regions from APP1's scatter table."""
    rel0, rel1 = struct.unpack_from("<2I", image, 0x80)
    rows = [struct.unpack_from("<4I", image, off) for off in range(0x80 + rel0, 0x80 + rel1, 16)]
    copy_fn = rows[0][3]
    others = sorted({r[3] for r in rows} - {copy_fn})
    kinds = {copy_fn: "copy", others[0]: "decompress", others[1]: "zero"}
    out = []
    for load, dst, size, fn in rows:
        kind = kinds[fn]
        if kind == "copy":
            out.append((dst, image[load - FLASH: load - FLASH + size]))
        elif kind == "decompress":
            out.append((dst, decompress(image[load - FLASH:], size)))
    return out


def u32(uc, a):
    return struct.unpack("<I", uc.mem_read(a, 4))[0]


def s32(uc, a):
    return struct.unpack("<i", uc.mem_read(a, 4))[0]


def f64(uc, a):
    return struct.unpack("<d", uc.mem_read(a, 8))[0]


def put32(uc, a, v):
    uc.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))


def putf64(uc, a, v):
    uc.mem_write(a, struct.pack("<d", v))


class V3Static(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(v3.STOCK, "rb") as h:
            cls.stock = h.read()
        cls.v2img = v2.build(cls.stock)
        cls.v3img = v3.build(cls.stock)

    def test_deterministic_and_hash(self):
        self.assertEqual(self.v3img, v3.build(self.stock))
        self.assertEqual(v2.sha256(self.v3img), v3.PATCHED_SHA256)

    def test_only_v3_ranges_differ_from_v2(self):
        self.assertEqual(len(self.v3img), len(self.v2img))
        diff = {i for i, (a, b) in enumerate(zip(self.v2img, self.v3img)) if a != b}
        allowed = set(range(v3.itcm_file(v2.RENDER_WRAPPER), v3.itcm_file(v3.RENDER_LIMIT)))
        allowed |= set(range(v3.itcm_file(v3.PARAM_WRAPPER), v3.itcm_file(v3.PARAM_LIMIT)))
        hook = v2.new_high_file(v3.PARAM_HOOK)
        allowed |= set(range(hook, hook + 18))
        self.assertTrue(diff)
        self.assertTrue(diff <= allowed, sorted(hex(i) for i in diff - allowed)[:10])

    def test_hook_targets_wrapper(self):
        from capstone import CS_ARCH_ARM, CS_MODE_MCLASS, CS_MODE_THUMB, Cs
        hook = v2.new_high_file(v3.PARAM_HOOK)
        blob = self.v3img[hook: hook + 18]
        self.assertEqual(blob, v3.param_hook_bytes())
        ins = [(i.mnemonic, i.op_str) for i in Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS).disasm(blob, v3.PARAM_HOOK)]
        self.assertEqual(ins, [
            ("mov", "r1, r8"), ("mov", "r2, r6"), ("mov", "r3, r7"),
            ("movw", "r0, #0x%x" % ((v3.PARAM_WRAPPER | 1) & 0xFFFF)),
            ("movt", "r0, #%d" % ((v3.PARAM_WRAPPER | 1) >> 16)),
            ("blx", "r0"), ("nop", ""),
        ])


class V3Emulation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(v3.STOCK, "rb") as h:
            cls.image = v3.build(h.read())
        cls.regions = regions(cls.image)

    def setUp(self):
        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(0x00000000, 0x20000)
        uc.mem_map(0x20000000, 0x10000)
        uc.mem_map(0x20200000, 0x100000)
        uc.mem_map(0x80000000, 0x300000)
        uc.mem_map(0x83530000, 0x10000)
        uc.mem_map(0x30000000, 0x20000)
        for dst, blob in self.regions:
            if dst < 0x20000 or 0x20000000 <= dst < 0x20300000 or 0x80000000 <= dst < 0x80300000:
                uc.mem_write(dst, blob)
        uc.reg_write(UC_ARM_REG_C1_C0_2, uc.reg_read(UC_ARM_REG_C1_C0_2) | (0xF << 20))
        uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        self.uc = uc
        self.preview_calls = []
        self.setter_calls = []
        self.render_calls = []
        uc.hook_add(UC_HOOK_CODE, self._hook)
        self._voices()

    def _hook(self, uc, address, size, _):
        if address == PREVIEW:
            self.preview_calls.append((uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1)))
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        elif address == v3.SETTER:
            self.setter_calls.append((uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1)))
        elif address == RENDER:
            self.render_calls.append(uc.reg_read(UC_ARM_REG_R0))
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def _call(self, fn, r0=0, r1=0, r2=0, r3=0):
        uc = self.uc
        uc.reg_write(UC_ARM_REG_SP, STACK_TOP)
        for reg, val in ((UC_ARM_REG_R0, r0), (UC_ARM_REG_R1, r1), (UC_ARM_REG_R2, r2), (UC_ARM_REG_R3, r3)):
            uc.reg_write(reg, val & 0xFFFFFFFF)
        uc.reg_write(UC_ARM_REG_LR, RETURN | 1)
        uc.emu_start(fn | 1, RETURN, count=200000)
        return uc.reg_read(UC_ARM_REG_R0)

    def _param(self, pid, value):
        self.setter_calls.clear()
        self._call(v3.PARAM_WRAPPER, r0=v3.PARAM_WRAPPER | 1, r1=pid, r2=value, r3=0)

    def _voices(self):
        """Four held Pulse voices on a chord, each with its own phase/velocity/source."""
        uc = self.uc
        self.notes = [21, 25, 28, 33]          # A3 C#4 E4 A4 in generator units (33 = A4)
        for i, st in enumerate(VOICES):
            uc.mem_write(st, b"\x01\x00")
            put32(uc, st + 0x08, 0)              # balance
            put32(uc, st + 0x0C, 0)              # bus
            put32(uc, st + 0x10, 0)              # env
            put32(uc, st + 0x48, 50)             # duty
            put32(uc, st + 0x4C, 200)            # no fade running
            put32(uc, st + 0x60, 4400)           # tune 440.0
            uc.mem_write(st + 0x70, bytes([11, 11]))  # Pulse, nothing pending
            put32(uc, st + 0x74, 9000 + i)
            put32(uc, st + 0x198, 9000 + i)
            put32(uc, st + 0x180, 3)
            # set pitch through the real stock setter (FREQ case) on this state only
            self._call(v3.SETTER, r0=st, r1=FREQ, r2=self.notes[i])
            putf64(uc, st + 0x18, 0.1 * (i + 1))   # phase 0..1
            putf64(uc, st + 0x30, 0.7 * (i + 1))   # radian phase
            put32(uc, st + 0x19C, self.notes[i])
            put32(uc, st + 0x1A4, 90 + i)
            put32(uc, st + 0x1AC, 10 + i)
        self.preview_calls.clear()

    def _snapshot(self, st):
        uc = self.uc
        return {
            "note": s32(uc, st + 0x5C), "inc": f64(uc, st + 0x20), "rinc": f64(uc, st + 0x38),
            "phase": f64(uc, st + 0x18), "rphase": f64(uc, st + 0x30),
            "cur": s32(uc, st + 0x19C), "vel": s32(uc, st + 0x1A4), "src": s32(uc, st + 0x1AC),
            "active": bytes(uc.mem_read(st, 2)),
        }

    def test_pitch_setup_matches_firmware_formula(self):
        for st, n in zip(VOICES, self.notes):
            want = 440.0 * 2 ** ((n - 33) / 12) / 48000
            self.assertAlmostEqual(f64(self.uc, st + 0x20) / want, 1.0, places=5)

    def test_duty_reaches_every_voice_and_keeps_pitch_and_phase(self):
        before = [self._snapshot(st) for st in VOICES]
        self._param(DUTY, 30)
        self.assertEqual([c[0] for c in self.setter_calls], VOICES)
        for st, b in zip(VOICES, before):
            self.assertEqual(s32(self.uc, st + 0x48), 30)
            self.assertEqual(self._snapshot(st), b)

    def test_tune_retunes_each_voice_from_its_own_note(self):
        before = [self._snapshot(st) for st in VOICES]
        self._param(TUNE, 4320)
        incs = []
        for st, n, b in zip(VOICES, self.notes, before):
            self.assertEqual(s32(self.uc, st + 0x60), 4320)
            want = 432.0 * 2 ** ((n - 33) / 12) / 48000
            self.assertAlmostEqual(f64(self.uc, st + 0x20) / want, 1.0, places=5)
            incs.append(f64(self.uc, st + 0x20))
            after = self._snapshot(st)
            for key in ("note", "phase", "rphase", "cur", "vel", "src", "active"):
                self.assertEqual(after[key], b[key], key)
        self.assertEqual(len(set(incs)), 4, "chord collapsed to fewer pitches")

    def test_type_fades_held_voices_and_skips_releasing_tails(self):
        uc = self.uc
        uc.mem_write(VOICES[2] + 1, b"\x01")          # voice 2 is releasing
        put32(uc, VOICES[2] + 0x4C, 50)              # partway through its release
        uc.mem_write(VOICES[3], b"\x00\x00")          # voice 3 is free
        self._param(TYPE, 6)                          # Saw
        for st in VOICES[:2]:                         # held: fade out, then switch
            self.assertEqual(uc.mem_read(st + 0x70, 2), bytes([11, 6]))
            self.assertEqual(s32(uc, st + 0x4C), -200)
            self.assertEqual(s32(uc, st + 0x48), 50)
        self.assertEqual(uc.mem_read(VOICES[2] + 0x70, 2), bytes([11, 11]))
        self.assertEqual(s32(uc, VOICES[2] + 0x4C), 50)
        self.assertEqual(uc.mem_read(VOICES[3] + 0x70, 2), bytes([6, 6]))  # free: switches now
        self.assertEqual(s32(uc, VOICES[3] + 0x4C), 0)
        self.assertEqual([c[0] for c in self.setter_calls], [VOICES[0], VOICES[1], VOICES[3]])

    def test_type_into_noise_applies_stock_duty_default_per_voice(self):
        self._param(TYPE, 13)
        for st in VOICES:
            self.assertEqual(s32(self.uc, st + 0x48), 100)
            self.assertEqual(self.uc.mem_read(st + 0x71, 1), bytes([13]))

    def test_level_sets_targets_without_velocity_feedback(self):
        self._param(LEVEL, 64)
        for i, st in enumerate(VOICES):
            self.assertEqual(s32(self.uc, st + 0x198), 6400)
            self.assertEqual(s32(self.uc, st + 0x74), 9000 + i)   # active voices ramp in the renderer
        for _ in range(20):                                     # pad path reads LEVEL back as velocity
            self.assertEqual(self._call(GETTER, r0=v2.SINGLETON, r1=LEVEL), 64)

    def test_balance_and_env_reach_every_voice(self):
        self._param(BALANCE, -20)
        self._param(ENV, 7)
        for st in VOICES:
            self.assertEqual(s32(self.uc, st + 0x08), -20)
            self.assertEqual(s32(self.uc, st + 0x10), 7)

    def test_freq_preview_and_pad_length_stay_on_voice_zero(self):
        clones = [self._snapshot(st) for st in VOICES[1:]]
        self._param(FREQ, 10)
        self.assertEqual(self.setter_calls, [(v2.SINGLETON, FREQ)])
        self.assertEqual(s32(self.uc, v2.SINGLETON + 0x5C), 10)
        self.assertEqual([self._snapshot(st) for st in VOICES[1:]], clones)

        self._param(PREVIEW_ID, 1)
        self.assertEqual(self.preview_calls, [(v2.SINGLETON, 1)])

        self._param(PADLEN, 9)
        self.assertEqual(s32(self.uc, v2.SINGLETON + 0x180), 9)
        for st in VOICES[1:]:
            self.assertEqual(s32(self.uc, st + 0x180), 3)

    def test_unknown_ids_never_touch_clones(self):
        clones = [bytes(self.uc.mem_read(st, 0x1B0)) for st in VOICES[1:]]
        self._param(0x83, 5)
        self._param(0x79, 5)
        self.assertEqual([bytes(self.uc.mem_read(st, 0x1B0)) for st in VOICES[1:]], clones)

    def test_render_shares_the_bus_and_visits_all_voices(self):
        put32(self.uc, v2.SINGLETON + 0x0C, 2)
        self._call(v2.RENDER_WRAPPER, r1=0x20001000, r2=64, r3=14)
        self.assertEqual(self.render_calls, VOICES)
        for st in VOICES:
            self.assertEqual(s32(self.uc, st + 0x0C), 2)


if __name__ == "__main__":
    unittest.main()
