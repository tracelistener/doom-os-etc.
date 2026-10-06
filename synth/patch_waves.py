"""Append all 17 Wave Lab types to the tested DOOM/poly-v4 layout.

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

HERE = Path(__file__).resolve().parent
BASE_SHA = '5ef3642a238d157f0176547dc22d11d46f445072e3aa0ae47879ac304e42cae3'
CODE_BASE = 0x8353E000
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
    code = bytearray(0x400)
    jump, call, load = build.v4.jump, build.v4.call, build.v4.load
    sources = [
        (OSC_GATE, NAME_GATE, f'''
            {load('r12', 0x80132AF3)}
            cmp lr, r12
            bne direct_voice
            {jump(build.v4.EXPORT_SAMPLE)}
        direct_voice:
            push {{r4-r6, lr}}
            vpush {{d8-d12}}
            mov r4, r0
            ldrb.w r0, [r4, #0x70]
            cmp r0, #15
            blo stock_voice
            cmp r0, #31
            bhi stock_voice
            mov r0, r4
            {call(symbols['wave_output'])}
            {jump(0x80007774)}
        stock_voice:
            ldrb.w r0, [r4, #0x70]
            {jump(0x8000770C)}
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
    # Original pool base was 12 mod 32; retain that alignment, reserve all code.
    pool_base = ((CODE_BASE + len(code) + 31) & ~31) + 12
    if pool_base >= build.v2.OS_POOL_END:
        raise ValueError('wave module exceeds OS pool')
    out = bytearray(base)
    patches = []

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
        (0x80002268, 'movw r0, #0xd82c', f'movw r0, #{pool_base & 0xffff}'),
        (0x80002270, 'movt r0, #0x8353', f'movt r0, #{pool_base >> 16}'),
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
    rows = build.scatter(base)
    zi = next(row for row in rows if row[1] == 0x802E7800)
    if zi[1] + zi[2] != build.v4.NEW_POOL_BASE or zi[3] != build.ZERO:
        raise ValueError('unexpected poly-v4 zero-init row')
    zi[2] = pool_base - zi[1]
    out.extend(b'\0' * (-len(out) % 16))
    code_file = len(out)
    out.extend(code)
    rows.append([build.v2.FLASH + code_file, CODE_BASE, len(code), build.COPY])
    table_at = len(out)
    for row in rows:
        out.extend(struct.pack('<4I', *row))
    struct.pack_into('<2I', out, 0x80, table_at-0x80, len(out)-0x80)
    return bytes(out), {'pool_base': pool_base, 'code_base': CODE_BASE,
                        'code_size': len(code), 'hooks': patches, 'symbols': symbols}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('--module', type=Path, default=HERE.parent / 'build' / 'waves')
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
    print(f'Wave code {len(module)} bytes, new pool {info["pool_base"]:#x}; types 15..31')


if __name__ == '__main__':
    main()
