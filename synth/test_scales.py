"""Scale candidate tests: ARM execution, independent pitch/label oracle and layout.

Run from sp404mk2 with its existing .poly_vendor on PYTHONPATH:
 python repo/doom-os-etc/synth/test_scales.py firmware/doom-poly-waves-v9.6-scales
The PDF is a reference, never executable instructions. No device I/O.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(Path(__file__).resolve().parent))
from audit_v95_edges import harness
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
from unicorn.arm_const import *

# Independently transcribed from the visual table on page 9 of the MC-101 PDF.
# String digits mean pitch classes; A=10, B=11. Keep separate from C generator.
PDF_NOTES=['0123456789AB','024579B','023578A','023579A','013578A','02479','0357A',
 '024679B','024579A','013568A','03567A','0234579A','023578B','023579B',
 '023479','0245789B','013468A','02468A','0235689B','0134679A','023678B',
 '023679A','0134568A','014578B','014679B','014678B','013678B','024568A',
 '0257A','0467B','01378','02378','01578','0457B']
def digest(b): return hashlib.sha256(b).hexdigest()
def s32(n): return n-(1<<32) if n&0x80000000 else n
def put(m,a,v): m.uc.mem_write(a,struct.pack('<I',v&0xffffffff))
def word(m,a): return struct.unpack('<I',bytes(m.uc.mem_read(a,4)))[0]

def main(folder):
    h=harness(); Fast=h['Fast']; v95=Path(h['V9']).read_bytes()
    candidate=folder/'SP404MKII_APP1.bin'; data=candidate.read_bytes()
    man=json.loads((folder/'manifest.json').read_text())
    rows=json.loads((Path(__file__).parent/'scales.json').read_text())
    checks=[]
    def check(ok,label):
        print(('PASS ' if ok else 'FAIL ')+label,flush=True)
        checks.append(bool(ok))
        if not ok: raise AssertionError(label)
    check(man['sha256']==digest(data) and man['parent_sha256']==digest(v95)
          and digest((folder/'SP404MKII_APP0.bin').read_bytes())==
          digest(Path(h['V9']).with_name('SP404MKII_APP0.bin').read_bytes()),
          'exact published v9.5 parent; candidate hash; unchanged stock APP0')
    check([r['notes'] for r in rows]==[[int(c,16) for c in line] for line in PDF_NOTES],
          '34 scales match independently transcribed PDF degrees (including Bebop Minor)')
    allowed=set(range(0x80,0x88))
    for address,size in man['scale_hooks']+man.get('fix_hooks',[]):
        off=h['file_offset'](v95,address,size); allowed.update(range(off,off+size))
    changed={i for i in range(len(v95)) if v95[i]!=data[i]}
    oldrows=h['scatter'](v95); newrows=h['scatter'](data)
    expected=[h['FLASH']+man['scale_code_file'],man['scale_code_base'],man['scale_code_size'],h['COPY']]
    module_range=set(range(man['code_file'],man['code_file']+man['code_size']))
    check(changed<=allowed and newrows==oldrows+[expected]
          and not (changed&module_range)-allowed
          and oldrows[-1][1]+oldrows[-1][2]<=man['scale_code_base']
          and man['scale_code_base']+man['scale_code_size']<=0x83ff0000,
          f'only declared scale/fix hooks and header changed ({len(changed)} bytes); other v9.5 module bytes unchanged')
    uc=Uc(UC_ARCH_ARM,UC_MODE_THUMB)
    uc.mem_map(h['FLASH'],(len(data)+4095)&~4095); uc.mem_write(h['FLASH'],data)
    uc.mem_map(0x83f00000,0x100000)
    seen=[]; running=[False]; handlers={r[3] for r in newrows}
    def boot(u,a,n,_):
        if a==0x60080062: running[0]=False
        if a==0x60080622: u.emu_stop()
        elif a in handlers and not running[0]:
            args=tuple(u.reg_read(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2))
            seen.append(args+(a,))
            if args[1] in (man['code_base'],man['scale_code_base']): running[0]=True
            else: u.reg_write(UC_ARM_REG_PC,u.reg_read(UC_ARM_REG_LR))
    uc.hook_add(UC_HOOK_CODE,boot); uc.emu_start(0x60080055,0x60080622,count=400000)
    check(seen==[tuple(r) for r in newrows] and bytes(uc.mem_read(man['scale_code_base'],expected[2]))==
          data[man['scale_code_file']:man['scale_code_file']+expected[2]],
          'real boot loader copies separate scale module after original copy rows')
    m=Fast(str(candidate)); legacy=Fast(h['V9']); KB=h['KB']; SG=h['SG']
    check(all(rows[k+1]['notes']==[i for i,x in enumerate(bytes(m.uc.mem_read(
          word(m,0x801ab4a4+4*k),12))) if x] for k in range(6)),
          'original six stock scale masks and their IDs preserved')
    # Independent oracle: enumerate a sorted infinite-ish scale, index relative
    # to the first scale note >= the chromatic pad-9 anchor (not a bitmask walk).
    padsteps=struct.unpack('<16i',bytes(m.uc.mem_read(0x801a9e60,64)))
    def oracle(pad,scale,root,octave,shift):
        degrees=rows[scale]['notes']; anchor=12*octave-shift
        pitches=sorted(root+12*o+d for o in range(-20,21) for d in degrees)
        idx=next(i for i,n in enumerate(pitches) if n>=anchor)
        return pitches[idx+padsteps[pad]]
    cases=0
    for scale in range(34):
        for root in range(12):
            for octave,shift in ((-3,12),(0,0),(2,5),(4,-12)):
                h['keyboard'](m,scale,root,octave,shift)
                if scale<7: h['keyboard'](legacy,scale,root,octave,shift)
                for pad in range(16):
                    got=h['pad_maths'](m,pad)
                    if scale<7: want=h['pad_maths'](legacy,pad)
                    else:
                        note=oracle(pad,scale,root,octave,shift)
                        want=[note,(note,0,1,pad),note,1,note-12*octave-root]
                    assert got==want,(scale,root,octave,shift,pad,got,want)
                    cases+=1
    check(True,f'{cases} pad cases: real note-on/off and LED helpers match independent scale oracle; legacy exact')
    for scale in range(34):
        for page in (5,0x1d):
            h['keyboard'](m,scale,9,3,-2,page)
            h['keyboard'](legacy,scale if scale<7 else 0,9,3,-2,page)
            for pad in range(16):
                assert h['pad_maths'](m,pad)==h['pad_maths'](legacy,pad),(scale,page,pad)
    check(True,'outside Sound Generator: original choices exact; added scales safely act as Chrom')
    for name in ('scale_note','scale_in','scale_rel'):
        for pad in (-1,16,255): assert m.call(man['scale_symbols'][name],pad)==0
    check(True,'new helpers safely reject invalid pad indices')
    lo,hi=0x30018000,0x30018004
    for page in (SG,5,0x1d):
        h['keyboard'](m,7,page=page)
        m.call(0x800d35f0,KB,0xf1,lo,hi,stack0=-1)
        assert (word(m,lo),word(m,hi))==(0,33 if page==SG else 6)
    check(True,'real native SCALE parameter range: 0..33 in SG, original 0..6 elsewhere')
    # Real SG +/- event spans, including calls to the native range routine.
    for page in (SG,5):
        limit=33 if page==SG else 6
        for value in range(limit+1):
            for start,step in ((0x8015c80c,-1),(0x8015c874,1)):
                h['keyboard'](m,value,page=page)
                m.uc.reg_write(UC_ARM_REG_R4,KB); m.uc.reg_write(UC_ARM_REG_SP,h['STACK'])
                def finish(u,a,n,_): u.emu_stop()
                handles=[m.uc.hook_add(UC_HOOK_CODE,finish,begin=a,end=a)
                         for a in (0x8015cc66,0x8015c5d8)]
                m.uc.emu_start(start|1,0,count=200000)
                for handle in handles: m.uc.hook_del(handle)
                assert word(m,KB+12)==min(limit,max(0,value+step))
                assert m.uc.reg_read(UC_ARM_REG_SP)==h['STACK']
    check(True,'native SG SCALE +/- controls traverse all 34 values and stop at bounds')
    # Minimal generic parameter object with the firmware's real ROM vtable,
    # getter, validator, formatter and cleanup (not a boot-constructed UI).
    obj,vt,out=0x30018200,0x80220fcc,0x30018400
    for machine in (m,legacy):
        put(machine,obj,vt)
        assert word(machine,vt+8)==0x800dda39 and word(machine,vt+0x70)==0x800ed5f1
    def label(machine,scale,page):
        h['keyboard'](machine,scale,page=page)
        machine.uc.mem_write(out,b'?'*32)
        machine.call(0x800c5168,obj,out,0xf1,scale,stack0=-1)
        raw=bytes(machine.uc.mem_read(out,32))
        assert raw[9:]==b'?'*23,raw
        return raw
    for scale in range(34):
        for page in (SG,5):
            got=label(m,scale,page)
            if scale<7: assert got==label(legacy,scale,page),(scale,page,got)
            else:
                want=(rows[scale]['label'] if page==SG else 'Chrom').encode()+b'\0'
                assert got[:len(want)]==want,(scale,page,got,want)
    for invalid in (-1,34,255): assert label(m,invalid,SG)==label(legacy,invalid,SG)
    check(True,'full native SCALE label function: all 34 labels, legacy exact, invalid values and buffer guards')
    # Note ownership remains tied to physical pad after changing scale/root/OCT.
    m.setup(6,env=3); h['keyboard'](m,19,11,2,5)
    for pad in (0,3,8,15):
        m.key(oracle(pad,19,11,2,5),100,pad); m.render(1)
    h['keyboard'](m,33,0,1,0)
    for pad in (0,3,8,15): m.key(oracle(pad,33,0,1,0),0,pad)
    assert all(v[1]==1 for v in m.voices()),m.voices()
    m.render(600)
    check(all(v[0]==0 for v in m.voices()),'four held pads release after SCALE, ROOT and OCT changes')
    check(not m.flash,'scale paths execute/read RAM, not flash')
    print(f'ALL {len(checks)} SCALE CHECKS PASS (no hardware/timing/UI-layout claim).')

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('folder',type=Path)
    main(ap.parse_args().folder)
