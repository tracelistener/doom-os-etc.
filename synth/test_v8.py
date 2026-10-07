"""V8 publication guards and exhaustive integer-DUTY math samples; no device."""
import base64
import json
import os
from pathlib import Path
import re
import struct
import sys
import unittest

import build
import build_v8

sys.path.insert(0, str(Path(__file__).resolve().parent / 'ram' / 'testing'))
from wave_single_note import Note
from unicorn.arm_const import UC_ARM_REG_S0, UC_ARM_REG_S1, UC_ARM_REG_S2, UC_ARM_REG_S3


def bits(value):
    return struct.unpack('<I', struct.pack('<f', value))[0]


class V8Publication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(os.environ.get('DOOM_V8_APP1',
            build.REPO / 'out' / 'doom-poly-waves-v8-ram' / 'SP404MKII_APP1.bin'))
        cls.image = cls.folder.read_bytes()
        cls.info = json.loads(cls.folder.with_name('manifest.json').read_text())

    def test_exact_v8_target_and_stock_app0(self):
        build.require_hash(self.image, build_v8.EXPECTED_SHA, 'v8 target')
        self.assertEqual(len(self.image), build_v8.EXPECTED_SIZE)
        build.require_hash(self.folder.with_name('SP404MKII_APP0.bin').read_bytes(),
                           build.APP0_SHA, 'stock APP0')

    def test_source_and_metadata_guards(self):
        info = build_v8.verified_sources()
        self.assertEqual(info['revision'], 'v8-ram')
        self.assertEqual(info['execution'], 'sdram-gap')
        self.assertEqual(info['polyblep'], [])
        self.assertEqual(info['status'], 'hardware-unverified')
        self.assertTrue(info['pool_unchanged_from_v4'])
        self.assertEqual(info['symbols'], self.info['symbols'])

    def test_archived_v8_overlay_roundtrip(self):
        stock = Path(os.environ['DOOM_STOCK_APP1']).read_bytes()
        _, base = build.build(stock)
        # The live page now ships v9.1; v8 remains an archived reproducible build.
        overlay = build.make_overlay(base, self.image, '5.52+waves-v8')
        self.assertEqual(build.apply_container(overlay, base), self.image)

    def test_publish_rejects_corrupt_inputs_without_overwriting_data(self):
        destination = build.REPO / 'waves-data.js'
        before = destination.read_bytes()
        stock = Path(os.environ['DOOM_STOCK_APP1']).read_bytes()
        _, base = build.build(stock)
        for source, target in ((stock, self.image), (base, self.image[:-1])):
            with self.assertRaises(ValueError):
                build_v8.publish_overlay(source, target, self.info)
        self.assertEqual(destination.read_bytes(), before)

    def test_pool_scatter_and_code_gap(self):
        stock = Path(os.environ['DOOM_STOCK_APP1']).read_bytes()
        _, base = build.build(stock)
        old, rows = build.scatter(base), build.scatter(self.image)
        self.assertEqual(rows[:-1], old)
        self.assertEqual(rows[-1][1], 0x83f80000)
        self.assertLessEqual(rows[-1][1]+rows[-1][2], 0x83ff0000)
        self.assertEqual(self.info['pool_base'], build.v4.NEW_POOL_BASE)

    def test_each_type_and_every_integer_duty_matches_v3_raw_sample(self):
        reference = Path(os.environ['DOOM_V3_RAM_APP1'])
        reference_info = json.loads(reference.with_name('manifest.json').read_text())
        v8, v3 = Note(self.image, True), Note(str(reference), True)
        # One deterministic phase/pitch/random tuple per TYPE/DUTY pair;
        # adds 1,717 guaranteed combinations to the original random sample.
        for kind in range(15, 32):
            for duty in range(101):
                p, dt, random_value = ((duty*37+kind*11) % 1009)/1009, 0.00545, 0.37
                for reg, value in zip((UC_ARM_REG_S0,UC_ARM_REG_S1,UC_ARM_REG_S2,UC_ARM_REG_S3),
                                      (p,duty,dt,random_value)):
                    v8.uc.reg_write(reg,bits(value))
                v8.call(self.info['symbols']['wave_core'],kind)
                # Match original v3's FP32 integer-percent conversion exactly.
                f32_duty = struct.unpack('<f',struct.pack('<f',duty))[0]
                f32_factor = struct.unpack('<f',struct.pack('<f',0.01))[0]
                for reg, value in zip((UC_ARM_REG_S0,UC_ARM_REG_S1,UC_ARM_REG_S2,UC_ARM_REG_S3),
                                      (p,f32_duty*f32_factor,dt,random_value)):
                    v3.uc.reg_write(reg,bits(value))
                v3.call(reference_info['symbols']['wave_eval'],kind)
                with self.subTest(kind=kind,duty=duty):
                    self.assertEqual(v8.uc.reg_read(UC_ARM_REG_S0),v3.uc.reg_read(UC_ARM_REG_S0))


if __name__ == '__main__':
    unittest.main()
