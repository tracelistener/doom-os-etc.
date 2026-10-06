"""Release candidate regressions; no scheduler/peripherals or hardware timing."""
from pathlib import Path
import sys
import unittest

import build
import patch_release
import test_build

WORKSPACE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent / "ram" / "testing"))
from wave_single_note import Note, STOCK
from test_patch_poly_v3 import VOICES, s32
import test_patch_poly_v3 as harness

BASE = WORKSPACE / "firmware/doom-poly-v4/SP404MKII_APP1.bin"
CANDIDATE = WORKSPACE / "firmware/doom-poly-v4-releasefix/SP404MKII_APP1.bin"


class Geometry(unittest.TestCase):
    def test_only_two_owned_slots_changed(self):
        old = BASE.read_bytes()
        new = patch_release.patch(old)
        self.assertEqual(new, CANDIDATE.read_bytes())
        self.assertEqual(new, patch_release.patch(old))
        allowed = set()
        for start, end in patch_release.SLOTS:
            off = build.offset(old, start, end - start)
            allowed.update(range(off, off + end - start))
        changes = {i for i, (a, b) in enumerate(zip(old, new)) if a != b}
        self.assertTrue(changes)
        self.assertLessEqual(changes, allowed)
        self.assertEqual(len(new), len(old))
        self.assertEqual(build.scatter(old), build.scatter(new))
        self.assertEqual(old[:0x88], new[:0x88])
        app0 = BASE.with_name("SP404MKII_APP0.bin").read_bytes()
        self.assertEqual(app0, CANDIDATE.with_name("SP404MKII_APP0.bin").read_bytes())
        build.require_hash(app0, build.APP0_SHA, "APP0")
        with self.assertRaises(ValueError):
            patch_release.patch(new)
        with self.assertRaises(ValueError):
            patch_release.patch(old[:-1])


class ReleaseEmulation(test_build.CombinedEmulation):
    @classmethod
    def setUpClass(cls):
        cls.image = patch_release.patch(BASE.read_bytes())
        cls.regions = harness.regions(cls.image)

    def test_transposed_note_off_real_note_handler_matches_original_identity(self):
        self.real_preview = True
        self._param(harness.FREQ, 25)
        self._call(build.v2.NOTE_WRAPPER, r1=self.notes[2], r2=0, r3=1, stack_arg=12)
        self.assertEqual([self.uc.mem_read(st + 1, 1) for st in VOICES],
                         [b"\0", b"\0", b"\1", b"\0"])
        self.assertEqual(s32(self.uc, VOICES[2] + 0x4C), -200)

    def test_twenty_real_press_release_cycles_keep_level_and_free_voice(self):
        self.real_preview, self.real_render = True, True
        self._call(build.v2.INIT_ALL_WRAPPER)
        self._param(harness.LEVEL, 100)
        for _ in range(20):
            velocity = self._call(harness.GETTER, r0=build.v2.SINGLETON, r1=harness.LEVEL)
            self.assertEqual(velocity, 100)
            self._call(build.v2.NOTE_WRAPPER, r1=33, r2=velocity, stack_arg=3)
            self._call(build.v2.NOTE_WRAPPER, r1=33, r2=0, r3=1, stack_arg=3)
            self.assertEqual(s32(self.uc, VOICES[0] + 0x4C), -200)
            for _ in range(7):  # 448 samples, allowing the stock 400-sample tail.
                self._call(build.v2.RENDER_WRAPPER, r1=0x20001000, r2=64, r3=14)
            self.assertEqual(self.uc.mem_read(VOICES[0], 2), b"\0\0")
            self.assertEqual(s32(self.uc, VOICES[0] + 0x198), 10000)

    def test_all_stop_starts_four_stock_fades_and_is_idempotent(self):
        self._call(build.v4.RELEASE_ENGINE)
        self.assertEqual([s32(self.uc, st + 0x4C) for st in VOICES], [-200] * 4)
        # A second stop must not restart a fade already in progress.
        for st in VOICES:
            harness.put32(self.uc, st + 0x4C, -100)
        self._call(build.v4.RELEASE_ENGINE)
        self.assertEqual([s32(self.uc, st + 0x4C) for st in VOICES], [-100] * 4)

    def test_release_all_handles_clone_only_activity_and_is_idempotent(self):
        self.uc.mem_write(VOICES[0], b"\0\0")
        for st in VOICES:
            self.uc.mem_write(st + 2, b"\1")
        self._call(build.v2.RELEASE_ALL_WRAPPER)
        for st in VOICES:
            self.assertEqual(self.uc.mem_read(st + 2, 1), b"\0")
        for st in VOICES[1:]:
            self.assertEqual(self.uc.mem_read(st + 1, 1), b"\1")
            self.assertEqual(s32(self.uc, st + 0x4C), -200)
            self.assertEqual(s32(self.uc, st + 0x19C), 1000)
            self.assertEqual(s32(self.uc, st + 0x1AC), -1)
            harness.put32(self.uc, st + 0x4C, 80)
        self._call(build.v2.RELEASE_ALL_WRAPPER)
        self.assertEqual([s32(self.uc, st + 0x4C) for st in VOICES[1:]], [80] * 3)


class StockReleaseParity(unittest.TestCase):
    def test_entire_hold_and_release_equal_stock_half_gain(self):
        for kind in range(15):
            with self.subTest(kind=kind):
                old = Note(STOCK, False).run(kind, hold_blocks=24, tail_blocks=10)
                new = Note(str(CANDIDATE), True).run(kind, hold_blocks=24, tail_blocks=10)
                self.assertEqual(new[2], old[2])
                for reference, corrected in zip(old[:2], new[:2]):
                    self.assertEqual(corrected, [value >> 1 for value in reference])

    def test_all_stop_audio_tail_matches_normal_note_off(self):
        for kind in (0, 8, 11):
            with self.subTest(kind=kind):
                normal = Note(str(CANDIDATE), True)
                stopped = Note(str(CANDIDATE), True)
                for synth in (normal, stopped):
                    synth.setup(kind)
                    synth.note(24, 127)
                    for _ in range(24):
                        synth.block()
                normal.note(24, 0)
                stopped.call(build.v4.RELEASE_ENGINE)
                for _ in range(10):
                    self.assertEqual(stopped.block(), normal.block())


if __name__ == "__main__":
    unittest.main()
