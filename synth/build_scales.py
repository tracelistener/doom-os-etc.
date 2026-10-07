"""Build a separate v9.6 scale candidate (optionally with voice fixes) from v9.5.

No changes to frozen v9.5 sources, original firmware, web payload or GitHub.
The scale module is in a new copy row after the existing v9.5 module, below
0x83ff0000. Hooks cover pad helpers, pad events and SCALE label/range cases.
"""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess

import build
import build_v9
import build_v8

HERE=Path(__file__).resolve().parent
MODULE_BASE=0x83FA1000
LINK_BASE=MODULE_BASE+0x100
LIMIT=0x83FF0000
NAME_SITE=0x800C5CA4
RANGE_SITE=0x800D39FC

def assemble(text,at):
    from keystone import Ks,KS_ARCH_ARM,KS_MODE_THUMB
    return bytes(Ks(KS_ARCH_ARM,KS_MODE_THUMB).asm(text,at)[0])

def jump(target):
    return f'movw r12, #{(target|1)&65535}\nmovt r12, #{target>>16}\nbx r12\nnop\n'

def call(target):
    return f'movw r12, #{(target|1)&65535}\nmovt r12, #{target>>16}\nblx r12\n'

def table():
    rows=json.loads((HERE/'scales.json').read_text())
    assert len(rows)==34 and len({r['name'] for r in rows})==34
    for r in rows:
        assert r['notes']==sorted(set(r['notes'])) and r['notes'][0]==0
        assert all(0<=n<12 for n in r['notes']) and len(r['label'])<=8
    return rows

def compile_module(folder,zig,info,fixes=False):
    # Reuse only the frozen ELF reader (does not compile/run native host code).
    import importlib.util
    spec=importlib.util.spec_from_file_location('_scale_v9_reader',build_v9.RAM/'build_v9.py')
    reader=importlib.util.module_from_spec(spec); spec.loader.exec_module(reader)
    rows=table()
    header=f'#define SCALE_COUNT {len(rows)}\n'
    for key,symbol in [('LEGACY_NOTE','kb_pad_note'),('LEGACY_IN','kb_in_scale'),('LEGACY_REL','kb_pad_rel')]:
        header+=f'#define {key}_ADDRESS {info["symbols"][symbol]|1:#x}u\n'
    for key,symbol in [('ENV_AGE','env_age'),('DISPLAY_SLOT','display_slot')]:
        header+=f'#define {key}_ADDRESS {info["symbols"][symbol]:#x}u\n'
    header+='static const unsigned short scale_masks[SCALE_COUNT] = {'
    header+=','.join(hex(sum(1<<n for n in r['notes'])) for r in rows)+'};\n'
    header+='static const char scale_names[SCALE_COUNT][9] = {'
    header+=','.join(json.dumps(r['label']) for r in rows)+'};\n'
    (folder/'scale_tables.h').write_text(header,encoding='ascii')
    linker=f'''ENTRY(scale_note)
SECTIONS {{
 . = {LINK_BASE:#x};
 .text : {{ KEEP(*(.text.scale_note)) KEEP(*(.text.scale_in)) KEEP(*(.text.scale_rel)) KEEP(*(.text.scale_range)) KEEP(*(.text.scale_name)) KEEP(*(.text.fixed_freq_event)) *(.text*) }}
 .rodata : ALIGN(16) {{ *(.rodata*) }}
 /DISCARD/ : {{ *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }}
}}
'''
    (folder/'scales.ld').write_text(linker,encoding='ascii')
    env=dict(os.environ,ZIG_GLOBAL_CACHE_DIR=str(folder/'cache'),ZIG_LOCAL_CACHE_DIR=str(folder/'cache'))
    subprocess.run([str(zig),'cc','-target','thumb-freestanding-eabihf',
        '-mcpu=cortex_m7+vfp4d16','-mfloat-abi=hard','-O2','-ffunction-sections','-fdata-sections',
        '-nostdlib','-Wl,--no-undefined','-Wl,--build-id=none','-Wl,-T,'+str(folder/'scales.ld'),
        '-I',str(folder),str(HERE/'scales.c'),
        *([str(HERE/'voice_fixes.c')] if fixes else []),
        '-o',str(folder/'scales.elf')],check=True,env=env)
    code,symbols=reader.read_module((folder/'scales.elf').read_bytes(),LINK_BASE,LIMIT)
    needed=('scale_note','scale_in','scale_rel','scale_range','scale_name')
    if fixes: needed+=('fixed_freq_event',)
    symbols={n:symbols[n] for n in needed}
    blob=bytearray(0x100)+code
    # NAME: only new, valid values take the C path; legacy labels untouched.
    name=f'cmp r10, #7\nblo legacy\ncmp r10, #33\nbhi legacy\nmov r0,r10\nmov r1,r8\n'
    name+=call(symbols['scale_name'])+jump(0x800C6758)
    # The patched case overwrites the original TBH. Reproduce its table jump
    # without jumping back into the overwritten instructions.
    name+='legacy:\ncmp r10,#6\nbhi invalid\n'
    name+='movw r12,#0x5cb0\nmovt r12,#0x800c\nldrh.w r0,[r12,r10,lsl #1]\n'
    name+='add.w r12,r12,r0,lsl #1\norr r12,r12,#1\nbx r12\n'
    name+='invalid:\n'+jump(0x800C6758)
    bridge=assemble(name,MODULE_BASE)
    assert len(bridge)<=0x80
    blob[:len(bridge)]=bridge
    bridge=assemble('mov r0,r5\nmov r1,r4\n'+call(symbols['scale_range'])+'pop {r4-r6,pc}',MODULE_BASE+0x80)
    assert len(bridge)<=0x80
    blob[0x80:0x80+len(bridge)]=bridge
    if fixes:
        # Original note_event has finished ownership/stolen-note checks here;
        # r4 is the matched held voice. Use its ORIGINAL pad pitch for release.
        # Then reproduce the displaced stock-call setup and rejoin before its
        # stack argument / tail call. r3 and the physical source in r5 survive.
        bridge=assemble('ldr.w r1,[r4,#0x19c]\nmovs r2,#0\n'
            'movw r12,#0x2429\nmovt r12,#0x8013\nb.w #0x83f80fe2',MODULE_BASE+0xc0)
        assert len(bridge)<=0x40
        blob[0xc0:0xc0+len(bridge)]=bridge
    return bytes(blob),symbols,rows

def make_image(base,blob,symbols,info=None,fixes=False):
    build.require_hash(base,build_v9.EXPECTED_SHA,'exact published v9.5')
    oldrows=build.scatter(base)
    # The boot's broad zero-init precedes the copy rows; only copied content
    # must not overlap. The frozen v9.5 row is retained byte-for-byte.
    for src,dst,size,handler in oldrows:
        if handler==build.COPY and max(dst,MODULE_BASE)<min(dst+size,MODULE_BASE+len(blob)):
            raise ValueError('scale module overlaps existing copied memory')
    if MODULE_BASE+len(blob)>LIMIT: raise ValueError('scale module exceeds SDRAM gap')
    out=bytearray(base); hooks=[]
    sites=[(0x80030C88,symbols['scale_note']),(0x80030FA8,symbols['scale_in']),
           (0x80030E98,symbols['scale_rel']),(NAME_SITE,MODULE_BASE),(RANGE_SITE,MODULE_BASE+0x80)]
    for site,target in sites:
        off=build.offset(base,site,12)
        out[off:off+12]=assemble(jump(target),site)
        hooks.append([site,12])
    # v9.5 pad events call the RAM helpers directly, not their native entry
    # points. Redirect those calls too, preserving all surrounding code.
    from capstone import Cs,CS_ARCH_ARM,CS_MODE_THUMB
    md=Cs(CS_ARCH_ARM,CS_MODE_THUMB)
    for site,symbol in ((0x8015C072,'scale_note'),(0x8015C080,'scale_in'),
                        (0x8015C4D2,'scale_note')):
        off=build.offset(base,site,10)
        ins=list(md.disasm(base[off:off+10],site))
        assert [i.mnemonic for i in ins]==['movw','movt','blx'], (hex(site),ins)
        out[off:off+10]=assemble(call(symbols[symbol]),site)
        hooks.append([site,10])
    fix_hooks=[]
    if fixes:
        # This recipe applies ONLY to exact frozen v9.5; never scan-and-patch
        # an unknown image. Verify the displaced release instructions too.
        site=0x83f80fd8; off=build.offset(base,site,10)
        expected=assemble('movw r12,#0x2429\nmovs r2,#0\nmovt r12,#0x8013',site)
        if base[off:off+10]!=expected: raise ValueError('note-off fallback changed')
        out[off:off+4]=assemble(f'b.w #{MODULE_BASE+0xc0:#x}',site)
        fix_hooks.append([site,4])
        site=info['symbols']['freq_event']&~1; off=build.offset(base,site,12)
        out[off:off+12]=assemble(jump(symbols['fixed_freq_event']),site)
        fix_hooks.append([site,12])
    file=(len(out)+15)&~15
    out+=bytes(file-len(out))+blob
    table_at=(len(out)+15)&~15
    out+=bytes(table_at-len(out))
    rows=oldrows+[[build.v2.FLASH+file,MODULE_BASE,len(blob),build.COPY]]
    for row in rows: out+=struct.pack('<4I',*row)
    struct.pack_into('<2I',out,0x80,table_at-0x80,len(out)-0x80)
    return bytes(out),dict(scale_code_base=MODULE_BASE,scale_code_file=file,
                           scale_code_size=len(blob),scale_symbols=symbols,scale_hooks=hooks,
                           fix_hooks=fix_hooks)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('v95',type=Path)
    ap.add_argument('--zig',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--fixes',action='store_true',help='also fix FREQ bounds and out-of-range pad note-off')
    args=ap.parse_args()
    if any((args.out/name).exists() for name in ('SP404MKII_APP1.bin','SP404MKII_APP0.bin','manifest.json')):
        raise ValueError('refusing to overwrite firmware or manifest')
    base=args.v95.read_bytes(); build.require_hash(base,build_v9.EXPECTED_SHA,'published v9.5')
    app0=args.v95.with_name('SP404MKII_APP0.bin').read_bytes()
    build.require_hash(app0,build.APP0_SHA,'stock APP0')
    if subprocess.check_output([str(args.zig),'version'],text=True).strip()!='0.13.0':
        raise ValueError('requires Zig 0.13.0')
    info=build_v9.verified_sources()
    args.out.mkdir(parents=True,exist_ok=True)
    folder=args.out/'build'; folder.mkdir(exist_ok=True)
    blob,symbols,rows=compile_module(folder.resolve(),args.zig.resolve(),info,args.fixes)
    image,layout=make_image(base,blob,symbols,info,args.fixes)
    info.update(layout,revision='v9.6-fixes' if args.fixes else 'v9.6-scales',sha256=build.sha(image),size=len(image),
                parent_sha256=build_v9.EXPECTED_SHA,scales=rows,
                scales_c_sha256=build.sha((HERE/'scales.c').read_bytes()),
                scales_json_sha256=build.sha((HERE/'scales.json').read_bytes()),
                tests='pending v9.6 scale checks',published=False)
    info['scale_module_sha256']=build.sha(blob)
    if args.fixes:
        info.update(voice_fixes_c_sha256=build.sha((HERE/'voice_fixes.c').read_bytes()),
                    freq_bounds_fix=True,pad_noteoff_range_fix=True,
                    wide_chord_policy='FREQ does not transpose held chords wider than 84 semitones',
                    original_module_sha256=info['module_sha256'],
                    module_sha256=build.sha(image[info['code_file']:info['code_file']+info['code_size']]))
    info['hardware_validation']={'source':'none yet','date':None,'coverage':'none',
        'detail':info['revision']+' candidate has not been tested on hardware','cpu_headroom':'unmeasured'}
    (args.out/'SP404MKII_APP1.bin').write_bytes(image)
    (args.out/'SP404MKII_APP0.bin').write_bytes(app0)
    (args.out/'manifest.json').write_text(json.dumps(info,indent=2)+'\n')
    print(json.dumps(dict(output=str(args.out),sha256=info['sha256'],size=len(image),**layout),indent=2))

if __name__=='__main__': main()
