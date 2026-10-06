"""Instruction-level oscillator and full polyphony regressions on the wave image."""
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import unittest

import build
import patch_waves as waves
import test_build
import test_release
from unicorn import UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE, Uc
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2
from unicorn.arm_const import UC_ARM_REG_S0, UC_ARM_REG_S1, UC_ARM_REG_S2, UC_ARM_REG_S3

HERE = Path(__file__).resolve().parent


def image_and_info():
    base = Path(os.environ['DOOM_POLY_APP1']).read_bytes()
    module, symbols = waves.module_info(Path(os.environ.get('WAVE_MODULE', HERE.parent/'build'/'waves-v3-budget')))
    return waves.build_image(base, module, symbols)


def float_bits(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


class WaveEmulation(test_release.ReleaseEmulation):
    @classmethod
    def setUpClass(cls):
        cls.image, cls.info = image_and_info()
        cls.regions = test_build.harness.regions(cls.image)

    def setUp(self):
        super().setUp()
        # The superclass maps the complete image in flash. There is no wave
        # RAM copy, expanded zero row or extra SDRAM allocation in v2.

    def raw(self, type, p, d, dt, r):
        for reg, value in zip((UC_ARM_REG_S0,UC_ARM_REG_S1,UC_ARM_REG_S2,UC_ARM_REG_S3), (p,d,dt,r)):
            self.uc.reg_write(reg, float_bits(value))
        self._call(self.info['symbols']['wave_eval'], r0=type)
        return struct.unpack('<f', struct.pack('<I', self.uc.reg_read(UC_ARM_REG_S0)))[0]

    def test_all_17_math_recipes_against_original_wave_lab_js(self):
        source = os.environ['WAVE_LAB_HTML']
        result = subprocess.check_output(['node', str(HERE/'reference_waves.cjs'), source], text=True)
        cases = json.loads(result)
        self.assertEqual(len(cases['ids']), 17)
        worst = {}
        for case in cases['cases']:
            with self.subTest(**{k:case[k] for k in ('type','p','d','dt')}):
                got = self.raw(case['type'], case['p'], case['d'], case['dt'], case['r'])
                self.assertTrue(math.isfinite(got))
                delta = abs(got-case['value'])
                worst[case['type']] = max(worst.get(case['type'], 0), delta)
                # FP32 + 2048-point linear sine interpolation; LP recurrence
                # amplifies its tiny cosine error across up to 64 harmonics.
                tolerance = 0.00005
                self.assertLessEqual(delta, tolerance)
        print('Wave math maximum absolute error by type:', worst)

    def test_cached_recipes_match_every_integer_duty_percent(self):
        source = os.environ['WAVE_LAB_HTML']
        result = subprocess.check_output(['node',str(HERE/'reference_waves.cjs'),source,'--all-duty'],text=True)
        cached = (20,21,23,24,25,26,27)
        worst = {}
        for case in json.loads(result)['cases']:
            if case['type'] not in cached:
                continue
            got = self.raw(case['type'],case['p'],case['d'],case['dt'],case['r'])
            delta = abs(got-case['value'])
            worst[case['type']] = max(worst.get(case['type'],0),delta)
            with self.subTest(kind=case['type'],duty=round(case['d']*100),phase=case['p'],dt=case['dt']):
                self.assertTrue(math.isfinite(got))
                self.assertLessEqual(delta,0.00005)
        print('All 101 DUTY values, cached recipe maximum errors:',worst)

    def test_new_type_range_freq_range_duty_range_and_real_setter(self):
        low, high = 0x30001000, 0x30001004
        for state in test_build.harness.VOICES:
            for type in range(15,32):
                self.uc.mem_write(state+0x70, bytes([type,type]))
                for pid, bounds in [(0x7B,(0,31)),(0x7C,(-36,48)),(0x7F,(0,100))]:
                    self._call(0x80132658, r0=state,r1=pid,r2=low,r3=high)
                    self.assertEqual((test_build.harness.s32(self.uc,low),test_build.harness.s32(self.uc,high)),bounds)
        for type in range(15,32):
            self._voices()
            for state in test_build.harness.VOICES:
                self.uc.mem_write(state+0x70, bytes([type,type]))
            for duty in (0,50,100):
                self._param(test_build.harness.DUTY,duty)
                self.assertEqual([test_build.harness.s32(self.uc,st+0x48) for st in test_build.harness.VOICES],[duty]*4)

    def test_value_names_preserve_stock_and_append_new_labels(self):
        target = 0x30002000
        expected = ['Sine','Sine1','Sine2','Cos','Cos1','Cos2','Saw','Saw+','Saw2','Tri','Tri2','Pulse','Pulse+','Noise1','Noise2'] + waves.TYPES
        for type,name in enumerate(expected):
            self.uc.mem_write(build.v2.SINGLETON+0x70,bytes([type,type]))
            self.uc.mem_write(target,b'?'*32)
            self._call(0x80131C48,r0=build.v2.SINGLETON,r1=target,r2=0x7B)
            got = bytes(self.uc.mem_read(target,32)).split(b'\0')[0].decode('ascii')
            self.assertEqual(got,name)
        self.uc.mem_write(build.v2.SINGLETON+0x70,bytes([32,32]))
        self._call(0x80131C48,r0=build.v2.SINGLETON,r1=target,r2=0x7B)
        self.assertEqual(bytes(self.uc.mem_read(target,4)),b'---\0')

    def test_freq_label_is_noise_only_for_original_two_noise_types(self):
        target = 0x30002000
        for type in range(32):
            self.uc.mem_write(build.v2.SINGLETON+0x70,bytes([type,type]))
            self._call(0x80131B00,r0=build.v2.SINGLETON,r1=target,r2=0x7C)
            got = bytes(self.uc.mem_read(target,16)).split(b'\0')[0].decode('ascii')
            if type not in (13,14):
                self.assertEqual(got,'Freq')
            else:
                self.assertEqual(got,'CycleFreq')

    def test_each_new_type_runs_real_oscillator_updates_phase_and_reads_level(self):
        state = test_build.harness.VOICES[0]
        self.real_preview = True
        self.oscillator_values = None
        for type in range(15,32):
            self._voices()
            self.uc.mem_write(state+0x70,bytes([type,type]))
            self.uc.mem_write(state+0x18,struct.pack('<d',0.271))
            self.uc.mem_write(state+0x40,struct.pack('<d',-0.7))
            # Settled gain: normal original oscillator, no gate/fade suppression.
            test_build.harness.put32(self.uc,state+0x74,10000)
            test_build.harness.put32(self.uc,state+0x198,10000)
            test_build.harness.put32(self.uc,state+0x4C,200)
            got = self._call(0x80007700,r0=state)
            got = got if got < 0x80000000 else got-0x100000000
            dt = test_build.harness.f64(self.uc,state+0x20)
            raw = self.raw(type,0.271,0.5,dt,-0.7)
            gain = 0.16 if type in (24,25,26) else 0.46 if type == 27 else 1
            expected = int(max(-1,min(1,raw*gain))*32767*100/127)
            self.assertLessEqual(abs(got-expected),2)
            self.assertAlmostEqual(test_build.harness.f64(self.uc,state+0x18),0.271+dt,places=12)
            self.assertEqual(test_build.harness.s32(self.uc,state+0x198),10000)

    def test_every_new_type_renders_four_live_voices_without_mutating_level(self):
        h = test_build.harness
        self.real_render = True
        self.real_preview = True
        for type in range(15,32):
            self._voices()
            for state in h.VOICES:
                self.uc.mem_write(state+0x70,bytes([type,type]))
            self.uc.mem_write(0x20001000,b'\0'*(64*14*4))
            before = [h.f64(self.uc,st+0x18) for st in h.VOICES]
            self._call(build.v2.RENDER_WRAPPER,r1=0x20001000,r2=64,r3=14,count=4000000)
            data = bytes(self.uc.mem_read(0x20001000,64*14*4))
            self.assertNotEqual(data,b'\0'*len(data))
            for index,state in enumerate(h.VOICES):
                expected = before[index] + 64*h.f64(self.uc,state+0x20)
                self.assertAlmostEqual(h.f64(self.uc,state+0x18),expected%1,places=11)
                self.assertEqual(h.s32(self.uc,state+0x198),9000+index)

    def test_every_new_type_full_chord_export_writes_pcm_and_preserves_live_state(self):
        h = test_build.harness
        self.real_preview, self.filesystem = True, True
        self.uc.mem_map(0x80BC0000,0x20000)
        for type in range(15,32):
            self._voices()
            for state in h.VOICES:
                self.uc.mem_write(state+0x70,bytes([type,type]))
            self._call(build.v4.CAPTURE_INIT)
            h.put32(self.uc,h.VOICES[0]+0x17C,7)
            h.put32(self.uc,h.VOICES[0]+0x180,-8)
            h.put32(self.uc,h.VOICES[0]+8,0)
            before = [bytes(self.uc.mem_read(st,0x1B0)) for st in h.VOICES]
            self.file_writes.clear(); self.file_headers.clear()
            self._call(0x801327B8,count=4000000)
            pcm = b''.join(data for pointer,data in self.file_writes if pointer==0x80BCD208)
            self.assertTrue(pcm)
            self.assertNotEqual(pcm,b'\0'*len(pcm))
            self.assertEqual(self.file_headers,[(2,len(pcm))])
            self.assertEqual(h.u32(self.uc,build.v4.EXPORT_COUNT),0)
            for i,state in enumerate(h.VOICES):
                expected = bytearray(before[i])
                if i==0: expected[0x17C:0x180] = b'\xff'*4
                self.assertEqual(bytes(self.uc.mem_read(state,0x1B0)),bytes(expected))

    def test_stock_audio_uses_untouched_v4_gate_and_matches_old_pcm(self):
        baseline = test_build.CombinedEmulation('test_four_real_notes_allocate_and_fifth_is_dropped_without_mutation')
        test_build.CombinedEmulation.setUpClass()
        baseline.setUp()
        h, size = test_build.harness, 64*14*4
        visited = []
        def entry(machine,address,length,unused):
            if address == build.v4.OSC_GATE:
                visited.append(machine.reg_read(UC_ARM_REG_R0))
            if address == self.info['symbols']['wave_output'] & ~1:
                self.fail('stock type called custom C renderer')
        self.uc.hook_add(UC_HOOK_CODE,entry)
        for case in (self,baseline):
            case.real_render = case.real_preview = True
        # Stock 8-10 use VSEL, unsupported by this Unicorn version. Those
        # dispatch/code bytes are structurally guarded instead; do not fake
        # a full dynamic stock-wave sweep.
        for type in (*range(8),11,12,13,14):
            for case in (self,baseline):
                case._voices()
                for state in h.VOICES:
                    case.uc.mem_write(state+0x70,bytes([type,type]))
            for block in range(16):
                visited.clear()
                for case in (self,baseline):
                    case.uc.mem_write(0x20001000,bytes(size))
                    case._call(build.v2.RENDER_WRAPPER,r1=0x20001000,r2=64,r3=14,count=4000000)
                self.assertEqual(set(visited),set(h.VOICES))
                self.assertEqual(bytes(self.uc.mem_read(0x20001000,size)),bytes(baseline.uc.mem_read(0x20001000,size)))

    def test_sequential_type_changes_settle_all_four_voices(self):
        h = test_build.harness
        self.real_render = self.real_preview = True
        self._voices()
        for type in (*range(15,32),14,0,31,15):
            self._param(h.TYPE,type)
            for block in range(7):
                self.uc.mem_write(0x20001000,bytes(64*14*4))
                self._call(build.v2.RENDER_WRAPPER,r1=0x20001000,r2=64,r3=14,count=4000000)
            self.assertEqual([self.uc.mem_read(st+0x70,1)[0] for st in h.VOICES],[type]*4)


class WaveLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Path(os.environ['DOOM_POLY_APP1']).read_bytes()
        cls.image, cls.info = image_and_info()

    def test_deterministic_and_hash_guarded(self):
        self.assertEqual(image_and_info()[0],self.image)
        module,symbols=waves.module_info(Path(os.environ.get('WAVE_MODULE',HERE.parent/'build'/'waves-v3-budget')))
        with self.assertRaises(ValueError):
            waves.build_image(self.base[:-1],module,symbols)

    def test_only_documented_hook_bytes_changed_in_existing_doom_poly(self):
        allowed=set()
        for addr,size in self.info['hooks']:
            off=build.offset(self.base,addr,size)
            allowed.update(range(off,off+size))
        changes={i for i,(a,b) in enumerate(zip(self.base,self.image)) if a!=b}
        self.assertTrue(changes<=allowed)

    def test_boot_table_zero_rows_and_pool_are_exactly_unchanged(self):
        rows=build.scatter(self.image)
        self.assertEqual(len(rows),14)
        self.assertEqual(rows,build.scatter(self.base))
        self.assertEqual(self.image[0x80:0x88],self.base[0x80:0x88])
        start,end=struct.unpack_from('<2I',self.base,0x80)
        self.assertEqual(self.image[start+0x80:end+0x80],self.base[start+0x80:end+0x80])
        self.assertEqual(self.info['pool_base'],build.v4.NEW_POOL_BASE)
        self.assertEqual(build.runtime_bytes(self.image,0x80002260,0x24),build.runtime_bytes(self.base,0x80002260,0x24))
        self.assertEqual(self.info['execution'],'flash-xip')
        self.assertGreaterEqual(self.info['code_file'],len(self.base))
        self.assertEqual(self.info['code_file']+build.v2.FLASH,waves.CODE_BASE)
        overlay=build.make_overlay(self.base,self.image,'5.52+poly+waves')
        self.assertEqual(build.apply_container(overlay,self.base),self.image)

    def test_original_v4_gate_and_stock_oscillator_body_are_preserved(self):
        for start,end in ((build.v4.OSC_GATE,build.v4.EXPORT_STOCK),(0x8000770C,0x80007F40)):
            self.assertEqual(build.runtime_bytes(self.image,start,end-start),build.runtime_bytes(self.base,start,end-start))

    def test_real_scatter_dispatch_copies_all_added_regions_after_zero(self):
        uc=Uc(UC_ARCH_ARM,UC_MODE_THUMB)
        uc.mem_map(build.v2.FLASH,(len(self.image)+4095)&~4095)
        uc.mem_write(build.v2.FLASH,self.image)
        uc.mem_map(0,0x20000)
        uc.mem_map(0x83530000,0x30000)
        rows=build.scatter(self.image)
        handlers={row[3] for row in rows}
        seen=[]
        running_copy=False
        def dispatch(machine,address,size,unused):
            nonlocal running_copy
            if address==0x60080062: running_copy=False
            if address==0x60080622: machine.emu_stop()
            elif address in handlers and not running_copy:
                args=[machine.reg_read(reg) for reg in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2)]
                seen.append(args+[address])
                if args[1] in (build.v2.NOTE_WRAPPER,build.v4.CODE_BASE):
                    running_copy=True
                else:
                    if args[1]==0x802E7800:
                        machine.mem_write(build.v2.OLD_OS_POOL_BASE,b'\0'*(self.info['pool_base']-build.v2.OLD_OS_POOL_BASE))
                    machine.reg_write(UC_ARM_REG_PC,machine.reg_read(UC_ARM_REG_LR))
        uc.hook_add(UC_HOOK_CODE,dispatch)
        uc.emu_start(0x60080055,0x60080622,count=500000)
        self.assertEqual(uc.reg_read(UC_ARM_REG_PC),0x60080622)
        self.assertEqual(seen,rows)
        for load,address,size,_ in rows[-2:]:
            self.assertEqual(bytes(uc.mem_read(address,size)),self.image[load-build.v2.FLASH:load-build.v2.FLASH+size])


if __name__=='__main__':
    unittest.main()
