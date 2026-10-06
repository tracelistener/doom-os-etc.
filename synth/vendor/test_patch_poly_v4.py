"""v4 regressions: actual setters/oscillators/export loop, simulated file I/O.

This is instruction-level validation, not an emulator boot or hardware test.
"""
import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import patch_poly as v2
import patch_poly_v3 as v3
import patch_poly_v4 as v4
import test_patch_poly_v3 as harness
from test_patch_poly_v3 import (
    VOICES, DUTY, FREQ, TUNE, TYPE, PREVIEW_ID, PADLEN,
    STACK_TOP, RETURN, u32, s32, f64, put32,
)
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
    UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
    UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9,
    UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_SP,
)

class V4Static(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(v4.STOCK, "rb") as handle:
            cls.stock = handle.read()
        cls.base = v3.build(cls.stock)
        cls.image = v4.build(cls.stock)

    def test_reproducible_exact_input_only(self):
        self.assertEqual(self.image, v4.build(self.stock))
        self.assertEqual(v2.sha256(self.image), v4.PATCHED_SHA256)
        bad = bytearray(self.stock)
        bad[0x123] ^= 1
        with self.assertRaises(ValueError):
            v4.build(bytes(bad))

    def test_copy_row_is_after_zero_init_and_owned_pool_prefix(self):
        a, b = struct.unpack_from("<2I", self.image, 0x80)
        rows = [struct.unpack_from("<4I", self.image, off)
                for off in range(a + 0x80, b + 0x80, 16)]
        self.assertEqual(len(rows), 13)
        self.assertEqual(b + 0x80, len(self.image))
        zi = next(row for row in rows if row[1] == 0x802E7800)
        self.assertEqual(zi[1] + zi[2], v4.NEW_POOL_BASE)
        self.assertEqual(rows[-1][1:3], (v4.CODE_BASE, v4.CODE_SIZE))
        self.assertEqual(rows[-1][3], rows[0][3])
        self.assertLess(v4.EXPORT_BASE + 4 * v4.VOICE_SIZE, v4.EXPORT_COUNT)
        self.assertLess(v4.EXPORT_COUNT + 4, v4.NEW_POOL_BASE)
        self.assertEqual(v4.NEW_POOL_BASE % 32, v2.OLD_OS_POOL_BASE % 32)
        # Model ordered scatter zero/copy operations in the newly reserved area.
        memory = bytearray(v4.NEW_POOL_BASE - v2.OLD_OS_POOL_BASE)
        for load, dst, size, fn in rows:
            if dst == 0x802E7800:
                memory[:] = b"\0" * len(memory)
            elif dst == v4.CODE_BASE:
                start = dst - v2.OLD_OS_POOL_BASE
                source = load - v2.FLASH
                memory[start:start + size] = self.image[source:source + size]
        for start, end, factory in v4.CODE_SLOTS:
            blob = v4.asm(factory(), start)
            off = start - v2.OLD_OS_POOL_BASE
            self.assertEqual(memory[off:off + len(blob)], blob)
            self.assertLessEqual(start + len(blob), end)

    def test_only_documented_existing_bytes_change(self):
        allowed = set(range(0x80, 0x88))
        for address in (v2.NOTE_WRAPPER, v3.PARAM_WRAPPER, v2.RELEASE_ALL_WRAPPER):
            off = v3.itcm_file(address)
            allowed.update(range(off, off + 10))
        spans = v4.RELEASE_SPANS + [
            (0x80002268, 0x8000226C), (0x8015C684, 0x8015C696),
            (0x8015CA8E, 0x8015CA9A), (0x8015BFEA, 0x8015C01C),
            (0x80028B98, 0x80028BAE), (0x801327B8, 0x801327C4),
            (0x80007700, 0x8000770C),
        ]
        for start, end in spans:
            allowed.update(range(v2.new_high_file(start), v2.new_high_file(end)))
        diffs = {i for i, (a, b) in enumerate(zip(self.base, self.image)) if a != b}
        self.assertTrue(diffs)
        self.assertTrue(diffs <= allowed, sorted(hex(i) for i in diffs - allowed)[:10])
        # The compressed initialized data and stock scatter table stay intact.
        self.assertEqual(self.image[0x267600:len(self.base)], self.base[0x267600:])
        self.assertEqual(self.image[0x62C:0x6EC], self.base[0x62C:0x6EC])

    def test_real_boot_scatter_dispatch_uses_relocated_table_and_copies_code(self):
        from unicorn import UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE, Uc
        uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(v2.FLASH, (len(self.image) + 4095) & ~4095)
        uc.mem_write(v2.FLASH, self.image)
        uc.mem_map(0x83530000, 0x10000)
        rel0, rel1 = struct.unpack_from("<2I", self.image, 0x80)
        rows = [struct.unpack_from("<4I", self.image, off)
                for off in range(0x80 + rel0, 0x80 + rel1, 16)]
        handlers = {row[3] for row in rows}
        seen = []
        running_copy = False
        def dispatch(machine, address, size, unused):
            nonlocal running_copy
            if address == 0x60080062:
                running_copy = False
            if address == 0x60080622:
                machine.emu_stop()  # End of scatter, before application boot.
            elif address in handlers and not running_copy:
                args = tuple(machine.reg_read(reg) for reg in
                             (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
                seen.append(args + (address,))
                if args[1] == v4.CODE_BASE:
                    running_copy = True
                else:
                    # Established stock rows are modeled; new code uses the
                    # real ROM copy handler. No peripheral initialization here.
                    machine.reg_write(UC_ARM_REG_PC, machine.reg_read(UC_ARM_REG_LR))
        uc.hook_add(UC_HOOK_CODE, dispatch)
        uc.emu_start(0x60080055, 0x60080622, count=20000)
        self.assertEqual(uc.reg_read(UC_ARM_REG_PC), 0x60080622)
        self.assertEqual(seen, rows)
        code_file = rows[-1][0] - v2.FLASH
        self.assertEqual(bytes(uc.mem_read(v4.CODE_BASE, v4.CODE_SIZE)),
                         self.image[code_file:code_file + v4.CODE_SIZE])

class V4Emulation(harness.V3Emulation):
    @classmethod
    def setUpClass(cls):
        with open(v4.STOCK, "rb") as handle:
            cls.image = v4.build(handle.read())
        from test_patch_poly_v3 import regions
        cls.regions = regions(cls.image)

    def setUp(self):
        self.oscillator_values = None
        self.oscillator_states = []
        self.real_preview = False
        self.real_render = False
        self.mock_export = False
        self.filesystem = False
        self.fs_open_failure = False
        self.fs_alloc_failure = False
        self.file_writes = []
        self.file_headers = []
        self.export_seen = []
        super().setUp()
        # Stock software RNG state lies in zero-init SDRAM outside the v3 map.
        # Initialize its two ring pointers so phase-wrap code can run normally.
        self.uc.mem_map(0x80590000, 0x10000)
        put32(self.uc, 0x80591488, 0x805913AC)
        put32(self.uc, 0x8059148C, 0x80591428)
        for dst, blob in self.regions:
            if dst == v4.CODE_BASE:
                self.uc.mem_write(dst, blob)

    def _return(self, value=0):
        self.uc.reg_write(UC_ARM_REG_R0, value & 0xFFFFFFFF)
        self.uc.reg_write(UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def _hook(self, uc, address, size, user):
        if address == 0x800D0668 and self.real_preview:
            return  # Execute original preview/start routine, including envelopes.
        if address == harness.RENDER and self.real_render:
            return
        if address in (0x800DED08, 0x800DECF8):
            self._return()  # Firmware critical-section entry/exit, no MMIO here.
        elif address == 0x80007700 and self.oscillator_values is not None:
            # Let the export LR gate run, but stub its subsequent per-voice calls.
            if uc.reg_read(UC_ARM_REG_LR) != 0x80132AF3:
                state = uc.reg_read(UC_ARM_REG_R0)
                self.oscillator_states.append(state)
                self.assertEqual(uc.reg_read(UC_ARM_REG_SP) % 8, 0)
                self._return(self.oscillator_values.get(state, 0))
        elif address == v4.EXPORT_STOCK and self.mock_export:
            state = uc.reg_read(UC_ARM_REG_R0)
            self.export_seen.append((state, u32(uc, v4.EXPORT_COUNT),
                                     [s32(uc, v4.EXPORT_BASE + i * 0x1B0 + 0x5C)
                                      for i in range(u32(uc, v4.EXPORT_COUNT))]))
            put32(uc, state + 0x17C, -1)
            self._return(123)
        elif self.filesystem and address == 0x800DD9B8:
            pointer, length = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)
            self.file_writes.append((pointer, bytes(uc.mem_read(pointer, length))))
            self._return(length)
        elif self.filesystem and address == 0x800DEC60:
            self._return(3 if not self.fs_open_failure and
                         uc.reg_read(UC_ARM_REG_R1) == 0x602 else -1)
        elif self.filesystem and address == 0x800E1176:
            self._return(0 if self.fs_alloc_failure else 0x30018000)
        elif self.filesystem and address == 0x800C6B88:
            self.file_headers.append((uc.reg_read(UC_ARM_REG_R2), uc.reg_read(UC_ARM_REG_R3)))
            self._return()
        elif self.filesystem and address in (
            0x800E1190, 0x800D60C8, 0x800D0030, 0x800D08E0,
            0x800E25B8, 0x800D9440, 0x800DEBD8, 0x800CFB20,
        ):
            self._return()
        else:
            super()._hook(uc, address, size, user)

    def _call(self, fn, r0=0, r1=0, r2=0, r3=0, stack_arg=0, count=200000):
        uc = self.uc
        uc.reg_write(UC_ARM_REG_SP, STACK_TOP)
        put32(uc, STACK_TOP, stack_arg)
        for reg, val in ((UC_ARM_REG_R0, r0), (UC_ARM_REG_R1, r1),
                         (UC_ARM_REG_R2, r2), (UC_ARM_REG_R3, r3)):
            uc.reg_write(reg, val & 0xFFFFFFFF)
        uc.reg_write(UC_ARM_REG_LR, RETURN | 1)
        uc.emu_start(fn | 1, RETURN, count=count)
        self.assertEqual(uc.reg_read(UC_ARM_REG_PC), RETURN, "instruction budget exhausted")
        self.assertEqual(uc.reg_read(UC_ARM_REG_SP), STACK_TOP, "unbalanced stack")
        return uc.reg_read(UC_ARM_REG_R0)

    def _voices(self):
        super()._voices()
        # A genuinely allocated one-note voice has no stock previous-note stack.
        for state in VOICES:
            put32(self.uc, state + 0x1A0, 1000)
            put32(self.uc, state + 0x1A8, 0)

    def test_freq_preview_and_pad_length_stay_on_voice_zero(self):
        # Override v3's FREQ expectation: transpose, don't collapse the chord.
        before = [self._snapshot(st) for st in VOICES]
        self._param(FREQ, 10)
        for st, note, snap in zip(VOICES, (10, 14, 17, 22), before):
            self.assertEqual(s32(self.uc, st + 0x5C), note)
            self.assertEqual(s32(self.uc, st + 0x19C), snap['cur'])
            self.assertEqual(s32(self.uc, st + 0x1AC), snap['src'])
            self.assertEqual(f64(self.uc, st + 0x18), snap['phase'])
            want = 440.0 * 2 ** ((note - 33) / 12) / 48000
            self.assertAlmostEqual(f64(self.uc, st + 0x20) / want, 1, places=5)
        self._param(PREVIEW_ID, 1)
        self.assertEqual(self.preview_calls, [(v2.SINGLETON, 1)])
        self._param(PADLEN, 9)
        self.assertEqual(s32(self.uc, v2.SINGLETON + 0x180), 9)
        self.assertEqual([s32(self.uc, st + 0x180) for st in VOICES[1:]], [3] * 3)

    def test_freq_clamps_common_interval_and_preserves_releasing_tail(self):
        self._param(FREQ, 48)
        self.assertEqual([s32(self.uc, st + 0x5C) for st in VOICES], [36, 40, 43, 48])
        self._param(FREQ, -36)
        self.assertEqual([s32(self.uc, st + 0x5C) for st in VOICES], [-36, -32, -29, -24])
        self.uc.mem_write(VOICES[2] + 1, b"\1")
        before = self._snapshot(VOICES[2])
        self._param(FREQ, -30)
        self.assertEqual(self._snapshot(VOICES[2]), before)

    def test_tune_reset_actual_hook_retunes_every_voice(self):
        self._param(TUNE, 4320)
        # Execute precisely the patched page block, ending before unrelated UI.
        from unicorn import UC_HOOK_CODE
        self.uc.hook_add(UC_HOOK_CODE, lambda uc, a, n, _: uc.reg_write(UC_ARM_REG_PC, RETURN)
                         if a == 0x8015C696 else None)
        self._call(0x8015C684)
        for st, note in zip(VOICES, self.notes):
            self.assertEqual(s32(self.uc, st + 0x60), 4400)
            want = 440 * 2 ** ((note - 33) / 12) / 48000
            self.assertAlmostEqual(f64(self.uc, st + 0x20) / want, 1, places=5)

    def test_release_all_handles_clone_only_activity_and_is_idempotent(self):
        self.uc.mem_write(VOICES[0], b"\0\0")
        for st in VOICES:
            self.uc.mem_write(st + 2, b"\1")
        self._call(v2.RELEASE_ALL_WRAPPER)
        for st in VOICES:
            self.assertEqual(self.uc.mem_read(st + 2, 1), b"\0")
        for st in VOICES[1:]:
            self.assertEqual(self.uc.mem_read(st + 1, 1), b"\1")
            self.assertEqual(s32(self.uc, st + 0x4C), 0)
            self.assertEqual(s32(self.uc, st + 0x19C), 1000)
            self.assertEqual(s32(self.uc, st + 0x1AC), -1)
            put32(self.uc, st + 0x4C, 80)
        self._call(v2.RELEASE_ALL_WRAPPER)
        self.assertEqual([s32(self.uc, st + 0x4C) for st in VOICES[1:]], [80] * 3)

    def test_every_patched_release_block_runs_with_preserved_continuation(self):
        from unicorn import UC_HOOK_CODE
        for start, end in v4.RELEASE_SPANS:
            with self.subTest(hook=hex(start)):
                self._voices()
                # The c654 block follows the original state/metadata setup.
                hook = self.uc.hook_add(UC_HOOK_CODE, lambda uc, a, n, _, stop=end:
                                        uc.reg_write(UC_ARM_REG_PC, RETURN) if a == stop else None)
                self._call(start)
                self.uc.hook_del(hook)
                self.assertEqual([self.uc.mem_read(st + 1, 1) for st in VOICES], [b"\1"] * 4)

    def test_leds_match_each_held_pad_not_only_first_pitch(self):
        self._param(FREQ, 25)  # Original note identity still determines its pad.
        for index, st in enumerate(VOICES):
            self.assertEqual(self._call(v4.LED_MATCH, r0=10 + index,
                                        r1=self.notes[index]), self.notes[index])
        self.assertEqual(self._call(v4.LED_MATCH, r0=1, r1=self.notes[0]), 1000)
        self.uc.mem_write(VOICES[2] + 1, b"\1")
        self.assertEqual(self._call(v4.LED_MATCH, r0=12, r1=self.notes[2]), 1000)
        self.uc.mem_write(VOICES[0], b"\0\0")
        self.assertEqual(self._call(v4.LED_MATCH, r0=13, r1=self.notes[3]), self.notes[3])

    def test_led_duplicate_pitch_sources_are_independent(self):
        put32(self.uc, VOICES[1] + 0x19C, self.notes[0])
        self.assertEqual(self._call(v4.LED_MATCH, r0=10, r1=self.notes[0]), self.notes[0])
        self.assertEqual(self._call(v4.LED_MATCH, r0=11, r1=self.notes[0]), self.notes[0])
        self.uc.mem_write(VOICES[0] + 1, b"\1")
        self.assertEqual(self._call(v4.LED_MATCH, r0=10, r1=self.notes[0]), 1000)
        self.assertEqual(self._call(v4.LED_MATCH, r0=11, r1=self.notes[0]), self.notes[0])

    def test_actual_led_hook_preserves_stock_pad_helper_and_comparison_inputs(self):
        from unicorn import UC_HOOK_CODE
        seen = []
        def hook(uc, address, size, unused):
            if address == 0x80030E98:
                seen.append(uc.reg_read(UC_ARM_REG_R0))
                self._return(123)
            elif address == 0x80028BAE:
                uc.reg_write(UC_ARM_REG_PC, RETURN)
        self.uc.hook_add(UC_HOOK_CODE, hook)
        for pad, note, expected in [(13, self.notes[3], self.notes[3]), (2, 0, 1000)]:
            self.uc.reg_write(UC_ARM_REG_R4, pad)
            self.uc.reg_write(UC_ARM_REG_R6, note)
            self._call(0x80028B98)
            self.assertEqual(self.uc.reg_read(UC_ARM_REG_R5), expected)
            self.assertEqual(self.uc.reg_read(UC_ARM_REG_R0), 123)
        self.assertEqual(seen, [13, 2])

    def test_actual_preview_stop_hook_handles_clone_only_and_idle(self):
        from unicorn import UC_HOOK_CODE
        destinations = []
        def hook(uc, address, size, unused):
            if address in (0x8015C01C, 0x8015C138):
                destinations.append(address)
                uc.reg_write(UC_ARM_REG_PC, RETURN)
        self.uc.hook_add(UC_HOOK_CODE, hook)
        self.uc.mem_write(VOICES[0], b"\0\0")
        self._call(0x8015BFEA)
        self.assertEqual(destinations, [0x8015C01C])
        self.assertEqual([self.uc.mem_read(st + 1, 1) for st in VOICES[1:]], [b"\1"] * 3)
        self._call(v2.INIT_ALL_WRAPPER)
        self._call(0x8015BFEA)
        self.assertEqual(destinations[-1], 0x8015C138)

    def test_capture_compacts_held_notes_and_reset_keeps_snapshot(self):
        self.uc.mem_write(VOICES[0], b"\0\0")
        self.uc.mem_write(VOICES[2] + 1, b"\1")
        self._call(v4.CAPTURE_INIT)
        self.assertEqual(u32(self.uc, v4.EXPORT_COUNT), 2)
        self.assertEqual([s32(self.uc, v4.EXPORT_BASE + i * 0x1B0 + 0x5C)
                          for i in range(2)], [self.notes[1], self.notes[3]])
        self.assertEqual([self.uc.mem_read(st, 2) for st in VOICES], [b"\0\0"] * 4)

    def test_actual_rec_hook_captures_before_reset_and_new_note_invalidates(self):
        from unicorn import UC_HOOK_CODE
        hook = self.uc.hook_add(UC_HOOK_CODE, lambda uc, a, n, _:
                                uc.reg_write(UC_ARM_REG_PC, RETURN)
                                if a == 0x8015CA9A else None)
        self._call(0x8015CA8E)
        self.uc.hook_del(hook)
        self.assertEqual(u32(self.uc, v4.EXPORT_COUNT), 4)
        self.assertEqual([self.uc.mem_read(st, 2) for st in VOICES], [b"\0\0"] * 4)
        self._call(v2.NOTE_WRAPPER, r1=33, r2=100, stack_arg=3)
        self.assertEqual(u32(self.uc, v4.EXPORT_COUNT), 0)

    def test_export_sample_half_gain_signed_sum_and_single_saturation(self):
        put32(self.uc, v4.EXPORT_COUNT, 4)
        states = [v4.EXPORT_BASE + i * 0x1B0 for i in range(4)]
        for samples, want in [([1000] * 4, 2000), ([-1001] * 4, -2004),
                              ([32767] * 4, 32767), ([-32768] * 4, -32768),
                              ([32767, 32767, -32768, -32768], -2)]:
            with self.subTest(samples=samples):
                self.oscillator_values = dict(zip(states, samples))
                self.oscillator_states.clear()
                value = self._call(v4.EXPORT_SAMPLE)
                self.assertEqual(value, want & 0xFFFFFFFF)
                self.assertEqual(self.oscillator_states, states)

    def test_export_uses_private_states_and_clears_consumed_snapshot(self):
        self.real_preview, self.mock_export = True, True
        self._call(v4.CAPTURE)
        put32(self.uc, VOICES[0] + 0x17C, 7)
        before = [bytes(self.uc.mem_read(st, 0x1B0)) for st in VOICES]
        self.assertEqual(self._call(0x801327B8), 123)
        self.assertEqual(self.export_seen, [(v4.EXPORT_BASE, 4, self.notes)])
        self.assertEqual(u32(self.uc, v4.EXPORT_COUNT), 0)
        self.assertEqual(s32(self.uc, VOICES[0] + 0x17C), -1)
        for i, st in enumerate(VOICES):
            after = bytearray(self.uc.mem_read(st, 0x1B0))
            old = bytearray(before[i])
            if i == 0:
                old[0x17C:0x180] = b"\xff" * 4
            self.assertEqual(after, old, "export mutated live pitch/phase/lifecycle")

    def test_full_stock_export_loop_writes_chord_pcm_and_restores_locks(self):
        self.real_preview, self.filesystem = True, True
        self.uc.mem_map(0x80BC0000, 0x20000)
        self._call(v4.CAPTURE_INIT)
        put32(self.uc, VOICES[0] + 0x17C, 7)
        put32(self.uc, VOICES[0] + 0x180, -8)  # Shortest period setting.
        put32(self.uc, VOICES[0] + 8, 0)      # Stereo, centered.
        states = [v4.EXPORT_BASE + i * 0x1B0 for i in range(4)]
        self.oscillator_values = dict(zip(states, [1000, 2000, 3000, 4000]))
        self._call(0x801327B8, count=2000000)
        pcm = b"".join(data for pointer, data in self.file_writes if pointer == 0x80BCD208)
        self.assertTrue(pcm)
        # Roland's internal export payload is big-endian (stock REVSH helper).
        samples = struct.unpack(">" + "h" * (len(pcm) // 2), pcm)
        self.assertEqual(set(samples), {5000})
        self.assertEqual(len(self.oscillator_states), len(samples) // 2 * 4)
        self.assertEqual(self.file_headers, [(2, len(pcm))])
        self.assertEqual(u32(self.uc, v4.EXPORT_COUNT), 0)
        self.assertEqual(s32(self.uc, VOICES[0] + 0x17C), -1)
        self.assertEqual([self.uc.mem_read(st + 3, 1) for st in VOICES], [b"\0"] * 4)

    def test_real_oscillator_gate_retains_single_voice_and_independent_phase(self):
        # Original oscillator runs, including math and envelope state, through
        # the gate's displaced prologue. Calling outside export is NOT mixed.
        self.real_preview = True
        for i, st in enumerate(VOICES):
            self.uc.mem_write(st, b"\0\0")
            self._call(0x800D0668, r0=st, r1=1)
        before = [f64(self.uc, st + 0x18) for st in VOICES]
        self._call(0x80007700, r0=VOICES[0])
        self.assertGreater(f64(self.uc, VOICES[0] + 0x18), before[0])
        self.assertEqual([f64(self.uc, st + 0x18) for st in VOICES[1:]], before[1:])

    def test_stock_export_multichunk_stereo_and_mono_pcm(self):
        self.real_preview, self.filesystem = True, True
        self.uc.mem_map(0x80BC0000, 0x20000)
        states = [v4.EXPORT_BASE + i * 0x1B0 for i in range(4)]
        self.oscillator_values = dict.fromkeys(states, -1000)
        for balance, channels in [(0, 2), (51, 1), (-51, 1)]:
            with self.subTest(balance=balance):
                self._voices()
                self._call(v4.CAPTURE_INIT)
                put32(self.uc, VOICES[0] + 0x17C, 7)
                put32(self.uc, VOICES[0] + 0x180, -3)
                put32(self.uc, VOICES[0] + 8, balance)
                self.file_writes.clear()
                self.file_headers.clear()
                self.oscillator_states.clear()
                self._call(0x801327B8, count=5000000)
                chunks = [data for pointer, data in self.file_writes if pointer == 0x80BCD208]
                self.assertGreater(len(chunks), 1)
                self.assertEqual(len(chunks[0]), 8192)
                pcm = b"".join(chunks)
                samples = struct.unpack(">" + "h" * (len(pcm) // 2), pcm)
                self.assertEqual(set(samples), {-2000})
                self.assertEqual(self.file_headers, [(channels, len(pcm))])
                self.assertEqual(len(self.oscillator_states), len(samples) // channels * 4)

    def test_export_sample_runs_four_real_oscillators_with_distinct_pitches(self):
        self.real_preview = True
        self._call(v4.CAPTURE)
        states = [v4.EXPORT_BASE + i * 0x1B0 for i in range(4)]
        for st in states:
            self.uc.mem_write(st, b"\0\0")
            self._call(0x800D0668, r0=st, r1=1)
        before_live = [bytes(self.uc.mem_read(st, 0x1B0)) for st in VOICES]
        for _ in range(10):
            self._call(v4.EXPORT_SAMPLE)
        phases = [f64(self.uc, st + 0x18) for st in states]
        self.assertEqual(len(set(phases)), 4)
        for st, phase in zip(states, phases):
            self.assertAlmostEqual(phase, 10 * f64(self.uc, st + 0x20), places=10)
        self.assertEqual([bytes(self.uc.mem_read(st, 0x1B0)) for st in VOICES], before_live)

    def test_stock_export_error_paths_clear_snapshot_and_restore_locks(self):
        self.real_preview, self.filesystem = True, True
        self.uc.mem_map(0x80BC0000, 0x20000)
        for fail_open, fail_alloc, destination in [(True, False, 7), (False, True, 7),
                                                  (False, False, -1)]:
            with self.subTest(open=fail_open, alloc=fail_alloc, destination=destination):
                self.fs_open_failure, self.fs_alloc_failure = fail_open, fail_alloc
                self._voices()
                self._call(v4.CAPTURE_INIT)
                put32(self.uc, VOICES[0] + 0x17C, destination)
                put32(self.uc, VOICES[0] + 0x180, -8)
                self._call(0x801327B8, count=1000000)
                self.assertEqual(u32(self.uc, v4.EXPORT_COUNT), 0)
                self.assertEqual([self.uc.mem_read(st + 3, 1) for st in VOICES], [b"\0"] * 4)

    def test_transposed_note_off_real_note_handler_matches_original_identity(self):
        self.real_preview = True
        self._param(FREQ, 25)
        self._call(v2.NOTE_WRAPPER, r1=self.notes[2], r2=0, r3=1, stack_arg=12)
        self.assertEqual([self.uc.mem_read(st + 1, 1) for st in VOICES],
                         [b"\0", b"\0", b"\1", b"\0"])
        self.assertEqual(s32(self.uc, VOICES[2] + 0x4C), 0)

    def test_twenty_real_press_release_cycles_keep_level_and_free_voice(self):
        self.real_preview, self.real_render = True, True
        self._call(v2.INIT_ALL_WRAPPER)
        self._param(harness.LEVEL, 100)
        for _ in range(20):
            velocity = self._call(harness.GETTER, r0=v2.SINGLETON, r1=harness.LEVEL)
            self.assertEqual(velocity, 100)
            self._call(v2.NOTE_WRAPPER, r1=33, r2=velocity, stack_arg=3)
            self.assertEqual(self.uc.mem_read(VOICES[0], 2), b"\1\0")
            self._call(v2.NOTE_WRAPPER, r1=33, r2=0, r3=1, stack_arg=3)
            for _ in range(4):
                self._call(v2.RENDER_WRAPPER, r1=0x20001000, r2=64, r3=14)
            self.assertEqual(self.uc.mem_read(VOICES[0], 2), b"\0\0")
            self.assertEqual(s32(self.uc, VOICES[0] + 0x198), 10000)

    def test_wrappers_preserve_callee_saved_registers(self):
        regs = [UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11]
        for fn, kwargs in [(v4.LED_MATCH, dict(r0=10, r1=self.notes[0])),
                           (v4.CAPTURE, {}), (v4.TUNE_RESET, {}),
                           (v4.FREQ_ENGINE, dict(r2=25)), (v4.RELEASE_ENGINE, {})]:
            for i, reg in enumerate(regs):
                self.uc.reg_write(reg, 0x12340000 + i)
            self._call(fn, **kwargs)
            self.assertEqual([self.uc.reg_read(reg) for reg in regs],
                             [0x12340000 + i for i in range(8)])

if __name__ == "__main__":
    unittest.main()
