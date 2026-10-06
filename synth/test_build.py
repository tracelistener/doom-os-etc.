"""DOOM/poly composition invariants and v4 regressions on the combined image.

DOOM_STOCK_APP1 must point to the owner's stock 5.52 APP1. No hardware boot.
"""
import os
from pathlib import Path
import struct
import unittest

import build
from build import v2, v4
import test_patch_poly_v4 as regression
import test_patch_poly_v3 as harness
from unicorn import UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE, Uc
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2


def stock_image():
    return Path(os.environ["DOOM_STOCK_APP1"]).read_bytes()


class Composition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock = stock_image()
        cls.doom, cls.image = build.build(cls.stock)
        cls.poly = v4.build(cls.stock)

    def test_deterministic(self):
        self.assertEqual(build.build(self.stock), (self.doom, self.image))

    def test_exact_inputs_only(self):
        with self.assertRaises(ValueError):
            build.build(self.stock[:-1])
        for index in (0x100, len(self.doom) - 1):
            damaged = bytearray(self.doom)
            damaged[index] ^= 1
            with self.assertRaises(ValueError):
                build.compose(self.stock, damaged, self.poly)
        with self.assertRaises(ValueError):
            build.compose(self.stock, self.doom, self.poly[:-1])

    def test_preserves_all_other_doom_bytes_and_flash_addresses(self):
        allowed = set(range(0x80, 0x88))
        for start, end in build.HOOKS:
            off = build.offset(self.doom, start, end - start)
            allowed.update(range(off, off + end - start))
        changed = {i for i, (a, b) in enumerate(zip(self.doom, self.image)) if a != b}
        self.assertTrue(changed)
        self.assertTrue(changed <= allowed)
        # Includes all directly executed appended DOOM code and resources.
        self.assertEqual(self.image[len(self.stock):len(self.doom)], self.doom[len(self.stock):])

    def test_all_synth_hook_spans_and_injected_code_equal_original_v4(self):
        for start, end in build.HOOKS + [(v2.NOTE_WRAPPER, 0x20000), (v4.CODE_BASE, v4.CODE_BASE + v4.CODE_SIZE)]:
            self.assertEqual(build.runtime_bytes(self.image, start, end - start),
                             build.runtime_bytes(self.poly, start, end - start))

    def test_scatter_layout_and_pool_ownership(self):
        before, after = build.scatter(self.doom), build.scatter(self.image)
        self.assertEqual(len(after), 14)
        for original, row in zip(before, after):
            if row[1] != 0x802E7800:
                self.assertEqual(original, row)
            else:
                self.assertEqual(row[:2], original[:2])
                self.assertEqual(row[3], original[3])
                self.assertEqual(row[1] + row[2], v4.NEW_POOL_BASE)
        self.assertEqual(after[-2][1:], [v2.NOTE_WRAPPER, 0x400, build.COPY])
        self.assertEqual(after[-1][1:], [v4.CODE_BASE, v4.CODE_SIZE, build.COPY])
        self.assertGreaterEqual(after[-2][0] - v2.FLASH, len(self.doom))
        self.assertLess(v4.EXPORT_COUNT + 4, v4.NEW_POOL_BASE)
        a, b = struct.unpack_from("<2I", self.image, 0x80)
        self.assertEqual(b + 0x80, len(self.image))
        self.assertEqual((a + 0x80) % 16, 0)

    def test_browser_overlay_round_trip_and_corruption_rejection(self):
        overlay = build.make_overlay(self.doom, self.image, "5.52+poly-v4")
        self.assertEqual(build.apply_container(overlay, self.doom), self.image)
        bad = bytearray(overlay)
        bad[-1] ^= 1
        with self.assertRaises(ValueError):
            build.apply_container(bad, self.doom)
        with self.assertRaises(ValueError):
            build.apply_container(overlay, self.stock)

    def test_optional_fm_is_guarded_and_only_two_spans_change(self):
        image = build.with_fm(self.image)
        allowed = set()
        for address, code in [(build.fm.FM_CODE_ADDRESS, build.fm.FM_CODE), (build.fm.FM_LABEL_ADDRESS, build.fm.FM_LABEL_CODE)]:
            off = build.offset(self.image, address, len(code))
            allowed.update(range(off, off + len(code)))
            self.assertEqual(image[off:off + len(code)], code)
        changed = {i for i, (a, b) in enumerate(zip(self.image, image)) if a != b}
        self.assertTrue(changed <= allowed)
        self.assertEqual(len(image), len(self.image))
        with self.assertRaises(ValueError):
            build.with_fm(image)

    def test_real_startup_dispatch_loads_both_new_rows_after_zero(self):
        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(v2.FLASH, (len(self.image) + 4095) & ~4095)
        uc.mem_write(v2.FLASH, self.image)
        uc.mem_map(0, 0x20000)
        uc.mem_map(0x83530000, 0x10000)
        rows = build.scatter(self.image)
        handlers = {row[3] for row in rows}
        seen = []
        running_copy = False

        def dispatch(machine, address, size, unused):
            nonlocal running_copy
            if address == 0x60080062:
                running_copy = False
            if address == 0x60080622:
                machine.emu_stop()
            elif address in handlers and not running_copy:
                args = [machine.reg_read(reg) for reg in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
                seen.append(args + [address])
                if args[1] in (v2.NOTE_WRAPPER, v4.CODE_BASE):
                    running_copy = True
                else:
                    machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))

        uc.hook_add(UC_HOOK_CODE, dispatch)
        uc.emu_start(0x60080055, 0x60080622, count=30000)
        self.assertEqual(uc.reg_read(UC_ARM_REG_PC), 0x60080622)
        self.assertEqual(seen, rows)
        for load, address, size, _ in rows[-2:]:
            self.assertEqual(bytes(uc.mem_read(address, size)), self.image[load - v2.FLASH:load - v2.FLASH + size])


class CombinedEmulation(regression.V4Emulation):
    @classmethod
    def setUpClass(cls):
        _, cls.image = build.build(stock_image())
        cls.regions = harness.regions(cls.image)

    def setUp(self):
        super().setUp()
        # DOOM's existing flash trampolines must remain executable too.
        self.uc.mem_map(v2.FLASH, (len(self.image) + 4095) & ~4095)
        self.uc.mem_write(v2.FLASH, self.image)

    def test_four_real_notes_allocate_and_fifth_is_dropped_without_mutation(self):
        self.real_preview = True
        self._call(v2.INIT_ALL_WRAPPER)
        self._param(harness.LEVEL, 75)
        for index, note in enumerate((21, 25, 28, 33)):
            self._call(v2.NOTE_WRAPPER, r1=note, r2=75, stack_arg=10 + index)
        for index, (state, note) in enumerate(zip(harness.VOICES, (21, 25, 28, 33))):
            self.assertEqual(self.uc.mem_read(state, 2), b"\1\0")
            self.assertEqual(harness.s32(self.uc, state + 0x19C), note)
            self.assertEqual(harness.s32(self.uc, state + 0x1AC), 10 + index)
        before = [bytes(self.uc.mem_read(state, 0x1B0)) for state in harness.VOICES]
        self._call(v2.NOTE_WRAPPER, r1=36, r2=75, stack_arg=14)
        self.assertEqual([bytes(self.uc.mem_read(state, 0x1B0)) for state in harness.VOICES], before)

    def test_duplicate_pitch_real_note_off_releases_only_matching_pad(self):
        self.real_preview = True
        self._call(v2.INIT_ALL_WRAPPER)
        for source in (7, 8):
            self._call(v2.NOTE_WRAPPER, r1=33, r2=75, stack_arg=source)
        self._call(v2.NOTE_WRAPPER, r1=33, r2=0, r3=1, stack_arg=8)
        self.assertEqual(self.uc.mem_read(harness.VOICES[0], 2), b"\1\0")
        self.assertEqual(self.uc.mem_read(harness.VOICES[1], 2), b"\1\1")
        before = [bytes(self.uc.mem_read(state, 0x1B0)) for state in harness.VOICES]
        self._call(v2.NOTE_WRAPPER, r1=33, r2=0, r3=1, stack_arg=99)
        self.assertEqual([bytes(self.uc.mem_read(state, 0x1B0)) for state in harness.VOICES], before)


if __name__ == "__main__":
    unittest.main()
