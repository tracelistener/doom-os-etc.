"""Exact current v9.9.3 (arpeggiator) publication guards, standalone within the fork.

Set DOOM_STOCK_APP1 and DOOM_V993_APP1 to supplied input/tested output paths.
No network, device I/O or native DLL execution.
"""
import base64
import json
import os
from pathlib import Path
import re
import unittest
import build
import build_arp
import build_v8
import publish_v9_6
import publish_v9_9_3 as release

class V993Publication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path=Path(os.environ['DOOM_V993_APP1'])
        cls.image=cls.path.read_bytes()
        cls.info=json.loads((release.HERE/'v9_9_3_manifest.json').read_text())
        cls.stock=Path(os.environ['DOOM_STOCK_APP1']).read_bytes()
        _,cls.base=build.build(cls.stock)

    def test_exact_target_and_stock_app0(self):
        build.require_hash(self.image,release.EXPECTED_SHA,'v9.9.3 APP1')
        self.assertEqual(len(self.image),release.EXPECTED_SIZE)
        build.require_hash(self.path.with_name('SP404MKII_APP0.bin').read_bytes(),build.APP0_SHA,'APP0')

    def test_frozen_sources_and_metadata(self):
        self.assertEqual(release.validate(self.image,self.info),self.info)
        self.assertEqual(self.info['revision'],'v9.9.3-arp')
        self.assertEqual(self.info['parent_revision'],'v9.6-fixes')
        self.assertEqual(self.info['parent_sha256'],publish_v9_6.EXPECTED_SHA)
        self.assertEqual(self.info['execution'],'sdram-gap')
        self.assertEqual(self.info['status'],'hardware-unverified')
        self.assertEqual(self.info['hardware_validation']['cpu_headroom'],'unmeasured')
        self.assertIn('not yet tested on hardware',self.info['hardware_validation']['detail'])
        self.assertEqual(self.info['arp_menu']['items'],['ARP','RATE','A.OCT','HOLD','BPM'])
        self.assertEqual(len(self.info['arp_hooks']),9)
        self.assertEqual(len(self.info['scales']),34)
        self.assertTrue(self.info['freq_bounds_fix'] and self.info['pad_noteoff_range_fix'])
        self.assertEqual(self.info['waves_c_sha256'],build_v8.SOURCE_LF_HASHES['waves.c'])

    def test_shipped_overlay_and_metadata(self):
        text=(build.REPO/'waves-data.js').read_text()
        overlay=re.search(r'window\.DOOMOS_WAVES_PATCH = "([A-Za-z0-9+/=]+)";',text)
        self.assertIsNotNone(overlay)
        container=base64.b64decode(overlay[1],validate=True)
        self.assertEqual(build.apply_container(container,self.base),self.image)
        info=re.search(r'window\.DOOMOS_WAVES = (\{.*\});',text)
        self.assertEqual(json.loads(info[1]),self.info)

    def test_corrupt_publication_rejected_before_writing(self):
        destinations=[build.REPO/'waves-data.js',release.HERE/'v9_9_3_manifest.json']
        before=[p.read_bytes() for p in destinations]
        cases=[(self.stock,self.image,self.info),(self.base,self.image[:-1],self.info)]
        for key,value in [('sha256','00'*32),('size',0),('revision','v9.9.1-arp'),
                          ('parent_sha256','00'*32),('scales',[]),('freq_bounds_fix',False),
                          ('pad_noteoff_range_fix',False),('module_sha256','00'*32),
                          ('scale_module_sha256','00'*32),('arp_module_sha256','00'*32),
                          ('arp_c_sha256','00'*32),('arp_hooks',self.info['arp_hooks'][:-1]),
                          ('arp_code_base',0)]:
            cases.append((self.base,self.image,dict(self.info,**{key:value})))
        for base,image,info in cases:
            with self.subTest(revision=info['revision'],size=len(image)):
                with self.assertRaises(ValueError): release.publish_overlay(base,image,info)
        self.assertEqual([p.read_bytes() for p in destinations],before)

    def test_scatter_and_module_gap(self):
        old=build.scatter(self.base); rows=build.scatter(self.image)
        self.assertEqual(rows[:-3],old)
        for row,prefix in zip(rows[-3:],('','scale_','arp_')):
            self.assertEqual(row,[build.v2.FLASH+self.info[prefix+'code_file'],
                self.info[prefix+'code_base'],self.info[prefix+'code_size'],build.COPY])
        self.assertLessEqual(rows[-3][1]+rows[-3][2],rows[-2][1])
        self.assertLessEqual(rows[-2][1]+rows[-2][2],rows[-1][1])
        self.assertEqual(rows[-1][1],build_arp.MODULE_BASE)
        self.assertLessEqual(rows[-1][1]+rows[-1][2],0x83ff0000)
        self.assertEqual(self.info['pool_base'],build.v4.NEW_POOL_BASE)
        self.assertTrue(self.info['pool_unchanged_from_v4'])

    def test_release_hash_in_checksums(self):
        text=(release.HERE/'checksums.txt').read_text()
        self.assertIn(f'DOOM-poly-v4-SoundGenerator-v9.9.3-arp  {release.EXPECTED_SHA}  {release.EXPECTED_SIZE}',text)

if __name__=='__main__': unittest.main()
