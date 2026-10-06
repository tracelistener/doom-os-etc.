"""Append optimized 17-wave candidate with corrected poly-v4 release.

Consumes a locally compiled fixed-address C module; preserves stock types.
Existing DOOM/poly-v4 bytes are guarded. APP0 is never modified.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct

import build
import patch_release

HERE = Path(__file__).resolve().parent
BASE_SHA = '5ef3642a238d157f0176547dc22d11d46f445072e3aa0ae47879ac304e42cae3'
# The failed v1 carved another 49,952 bytes from SDRAM. V2 executes from
# appended flash, like DOOM's existing code, without any boot/pool changes.
CODE_BASE = 0x60334800
LINK_BASE = CODE_BASE + 0x400
OSC_GATE = CODE_BASE
NAME_GATE = CODE_BASE + 0x100
FREQ_LABEL = CODE_BASE + 0x180
TYPES = ['FM1:1','FM1:2','FM1:7','CZSaw','CZSqr','CZRes','Sync','Fold','Drive',
         'LPSaw','LPSqr','Organ','Vowel','Table','Logic','Metal','Grit']


def asm(source, address):
    return build.v4.asm(source, address)


def module_info(folder):
    module = (folder / 'waves.bin').read_bytes()
    symbols = json.loads((folder / 'symbols.json').read_text())
    for label in ('wave_eval','wave_output','wave_name'):
        if not LINK_BASE <= symbols[label] < LINK_BASE + len(module) or not symbols[label] & 1:
            raise ValueError('invalid compiled wave export')
    return module, symbols


def build_image(base, module, symbols):
    build.require_hash(base, BASE_SHA, 'DOOM ED5E + poly-v4')
    released = patch_release.patch(base)
    code = bytearray(0x400)
    jump, call, load = build.v4.jump, build.v4.call, build.v4.load
    sources = [
        (OSC_GATE, NAME_GATE, f'''
            {load('r12', 0x80132AF3)}
            cmp lr, r12
            beq original_gate
            ldrb.w r1, [r0, #0x70]
            subs r1, #15
            cmp r1, #16
            bhi original_gate
            push {{r4-r6, lr}}
            vpush {{d8-d12}}
            mov r4, r0
            {call(symbols['wave_output'])}
            {jump(0x80007774)}
        original_gate:
            {jump(build.v4.OSC_GATE)}
        '''),
        (NAME_GATE, FREQ_LABEL, f'''
            ldrb.w r0, [r0, #0x70]
            cmp r0, #14
            bhi extended_name
            {load('r12', 0x80131C8C)}
            ldrh.w r0, [r12, r0, lsl #1]
            add.w r12, r12, r0, lsl #1
            orr.w r12, r12, #1
            bx r12
        extended_name:
            cmp r0, #31
            bls known_name
            {jump(0x80131CB4)}
        known_name:
            mov r1, r4
            {call(symbols['wave_name'])}
            {jump(0x80131C78)}
        '''),
        (FREQ_LABEL, CODE_BASE + 0x400, f'''
            ldrb.w r0, [r0, #0x70]
            subs r0, #13
            cmp r0, #1
            bhi pitched_label
            adds r0, #13
            {jump(0x80131C0C)}
        pitched_label:
            movs r0, #0
            strb r0, [r1, #4]
            movw r0, #0x7246
            movt r0, #0x7165
            str r0, [r1]
            bx lr
        '''),
    ]
    for start, end, source in sources:
        blob = asm(source, start)
        if start + len(blob) > end:
            raise ValueError('wave bridge overflow')
        code[start-CODE_BASE:start-CODE_BASE+len(blob)] = blob
    code.extend(module)
    code.extend(b'\0' * (-len(code) % 16))
    if CODE_BASE < build.v2.FLASH + len(base):
        raise ValueError('wave flash layout overlaps existing DOOM/poly image')
    if CODE_BASE + len(code) > 0x60400000:
        raise ValueError('wave module exceeds conservative 4 MiB flash prefix')
    out = bytearray(released)
    patches = [[start,end-start] for start,end in patch_release.SLOTS]

    def patch(address, expected, replacement):
        off = build.offset(base, address, len(expected))
        if out[off:off+len(expected)] != expected or len(replacement) != len(expected):
            raise ValueError(f'wave hook conflict/size mismatch at {address:#x}')
        out[off:off+len(expected)] = replacement
        patches.append([address, len(replacement)])

    for addr, before, after in [
        (0x8013269C, 'movs r0, #14', 'movs r0, #31'),
        (0x801326A4, 'cmp r0, #14', 'cmp r0, #31'),
        (0x801326C6, 'cmp r1, #5', 'cmp r1, #22'),
        (0x80019FC6, 'cmp r0, #4', 'cmp r0, #21'),
        (0x80131CCC, 'cmp r2, #14', 'cmp r2, #31'),
        (0x80131D9E, 'cmp r1, #4', 'cmp r1, #21'),
        (0x80131BAA, 'cmp r0, #4', 'cmp r0, #21'),
        (0x80132A36, 'cmp r0, #15', 'cmp r0, #32'),
    ]:
        patch(addr, asm(before, addr), asm(after, addr))
    for address, end, target, before in [
        (0x80007700, 0x8000770C, OSC_GATE,
         asm(jump(build.v4.OSC_GATE), 0x80007700) + b'\0\xbf'),
        (0x80131C80, 0x80131C8C, NAME_GATE,
         asm('ldrb.w r0, [r0, #0x70]\ncmp r0, #14\nbhi #0x80131cb4\ntbh [pc, r0, lsl #1]', 0x80131C80)),
        (0x80131B7C, 0x80131B88, FREQ_LABEL,
         asm('ldrb.w r0, [r0, #0x70]\ncmp r0, #13\nbhs #0x80131c0c\nmovs r0, #0\nstrb r0, [r1, #4]', 0x80131B7C)),
    ]:
        replacement = asm(jump(target), address)
        replacement += b'\0\xbf' * ((end-address-len(replacement))//2)
        patch(address, before, replacement)
    # Keep the complete existing scatter table and its header pointers, all
    # initialized RAM copy rows, and the working v4 OS pool reservation.
    code_file = CODE_BASE - build.v2.FLASH
    out.extend(b'\0' * (code_file-len(out)))
    out.extend(code)
    return bytes(out), {'revision': 'v3-budget-candidate',
                        'status': 'hardware-unverified',
                        'pool_base': build.v4.NEW_POOL_BASE, 'code_base': CODE_BASE,
                        'release_fix': True, 'noise_freq_label_fix': True,
                        'dsp': 'integer-DUTY coefficient tables and bounded exp/tanh lookups',
                        'code_file': code_file, 'execution': 'flash-xip',
                        'code_size': len(code), 'hooks': patches, 'symbols': symbols}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('--module', type=Path, default=HERE.parent / 'build' / 'waves-v3-budget')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--web', action='store_true')
    args = parser.parse_args()
    module, symbols = module_info(args.module)
    base = args.base.read_bytes()
    image, info = build_image(base, module, symbols)
    app0 = args.base.with_name('SP404MKII_APP0.bin').read_bytes()
    build.require_hash(app0, build.APP0_SHA, 'unchanged stock APP0')
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'SP404MKII_APP1.bin').write_bytes(image)
    (args.out / 'SP404MKII_APP0.bin').write_bytes(app0)
    info.update(sha256=build.sha(image), size=len(image), source_sha256=BASE_SHA,
                module_sha256=build.sha(module), types={str(i+15): name for i, name in enumerate(TYPES)})
    (args.out / 'manifest.json').write_text(json.dumps(info, indent=2, sort_keys=True)+'\n')
    if args.web:
        overlay = build.make_overlay(base, image, '5.52+poly+waves')
        # Separate file so the original DOOM/poly data and output remain reproducible.
        text = '// Generated by synth/patch_waves.py --web; experimental, no full firmware.\n'
        text += 'window.DOOMOS_WAVES = ' + json.dumps(info, sort_keys=True) + ';\n'
        text += 'window.DOOMOS_WAVES_PATCH = ' + json.dumps(base64.b64encode(overlay).decode('ascii')) + ';\n'
        (HERE.parent / 'waves-data.js').write_text(text, encoding='ascii', newline='\n')
    print(f'{args.out}: {len(image)} bytes, SHA-256 {build.sha(image)}')
    print(f'Wave code {len(module)} bytes, UNCHANGED pool {info["pool_base"]:#x}; types 15..31; hardware unverified')


if __name__ == '__main__':
    main()
