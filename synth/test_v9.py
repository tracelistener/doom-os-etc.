"""Exact v9.4 publication guards; no flashing or native host execution."""
import base64
import json
import os
from pathlib import Path
import re
import unittest

import build
import build_v8
import build_v9


class V9Publication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(os.environ.get('DOOM_V9_APP1',
            build.REPO/'out'/'doom-poly-waves-v9.4-ram'/'SP404MKII_APP1.bin'))
        cls.image = cls.folder.read_bytes()
        cls.info = build_v9.verified_sources()
        cls.stock = Path(os.environ['DOOM_STOCK_APP1']).read_bytes()
        _, cls.base = build.build(cls.stock)

    def test_exact_target_and_stock_app0(self):
        build.require_hash(self.image, build_v9.EXPECTED_SHA, 'v9.4 target')
        self.assertEqual(len(self.image), build_v9.EXPECTED_SIZE)
        build.require_hash(self.folder.with_name('SP404MKII_APP0.bin').read_bytes(),
                           build.APP0_SHA, 'stock APP0')

    def test_source_and_metadata_guards(self):
        self.assertEqual(self.info['revision'], 'v9.4-ram')
        self.assertEqual(self.info['execution'], 'sdram-gap')
        self.assertEqual(self.info['status'], 'hardware-test-in-progress')
        self.assertEqual(self.info['hardware_validation']['coverage'], 'none')
        self.assertEqual(self.info['hardware_validation']['cpu_headroom'], 'unmeasured')
        self.assertEqual(len(self.info['env_presets']), 10)
        self.assertTrue(self.info['pool_unchanged_from_v4'])
        self.assertEqual(self.info['waves_c_sha256'], build_v8.SOURCE_LF_HASHES['waves.c'])

    def test_shipped_overlay_roundtrip_and_metadata(self):
        text = (build.REPO/'waves-data.js').read_text()
        match = re.search(r'window\.DOOMOS_WAVES_PATCH = "([A-Za-z0-9+/=]+)";', text)
        self.assertIsNotNone(match)
        container = base64.b64decode(match[1], validate=True)
        self.assertEqual(build.apply_container(container, self.base), self.image)
        metadata = re.search(r'window\.DOOMOS_WAVES = (\{.*\});', text)
        self.assertEqual(json.loads(metadata[1]), self.info)

    def test_rejects_corrupt_inputs_without_overwriting_data(self):
        destination = build.REPO/'waves-data.js'
        before = destination.read_bytes()
        cases = [(self.stock, self.image, self.info),
                 (self.base, self.image[:-1], self.info)]
        for key, wrong in [('size', 0), ('sha256', '00'*32), ('revision', 'v9.1-ram')]:
            cases.append((self.base, self.image, dict(self.info, **{key: wrong})))
        for source, target, info in cases:
            with self.subTest(info=info['revision'], size=len(target)):
                with self.assertRaises(ValueError):
                    build_v9.publish_overlay(source, target, info)
        self.assertEqual(destination.read_bytes(), before)

    def test_pool_scatter_and_code_gap(self):
        old, rows = build.scatter(self.base), build.scatter(self.image)
        self.assertEqual(rows[:-1], old)
        self.assertEqual(rows[-1][1], self.info['code_base'])
        self.assertEqual(rows[-1][2], self.info['code_size'])
        self.assertLessEqual(rows[-1][1]+rows[-1][2], 0x83ff0000)
        self.assertEqual(self.info['pool_base'], build.v4.NEW_POOL_BASE)
        self.assertEqual(rows[-1][0]-build.v2.FLASH, self.info['code_file'])
        self.assertEqual(rows[-1][3], build.COPY)
        self.assertLessEqual(self.info['code_file']+self.info['code_size'], len(self.image))
        # The builder guards the pre-injection module hash. Gate bridges then
        # replace reserved bytes; exact final APP1 is guarded separately above.


if __name__ == '__main__':
    unittest.main()
