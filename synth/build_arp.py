"""Build Sound Generator v9.9.2 (arpeggiator) from the exact v9.6-fixes APP1.

    python synth/build_arp.py out/v9.6/SP404MKII_APP1.bin --zig /path/to/zig --out out/v9.9.2

The v9.6-fixes manifest.json and stock APP0 must sit beside the input APP1
(as build_scales.py writes them). Needs Zig 0.13.0 and keystone/capstone on
PYTHONPATH. No network, native DLL execution or device flashing; refuses to
overwrite existing outputs.

The arp module (arp.c plus small bridges) goes in a new boot copy row at
0x83fa2000, after v9.6's scale module and below 0x83ff0000. Existing code
changes only at these sites, each checked against its exact v9.6 instructions:
  0x800c19f4  VALUE-menu cursor (setting 0x83) range 0..5 -> 0..10
  0x800bbc98  item names: 6..10 = ARP RATE A.OCT HOLD BPM
  0x80123be6  SG screen: value text for items 6..10 (BPM is read-only)
  0x80123ce0  SG screen update: read the tempo, redraw after an arp change
  0x8015c7f4  VALUE -: items 6..10
  0x8015c85c  VALUE +: items 6..10
  0x0001fd84  render loop (every 64 frames): the arp clock
  0x8353b000  pad notes (NOTE_ENGINE) -> arp_note_event (-> v9.5 note_event when ARP is OFF)
  0x8005a698  MIDI IN -> arp_midi_event (-> v9.5 midi_event when ARP is OFF)
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess

import capstone

HERE = Path(__file__).resolve().parent
PARENT_SHA = '1b2d50e0cfe20b11bad9cdef6945ec24a14a60e772e56a02dd5478331e275cc9'   # v9.6-fixes
APP0_SHA = '3e35ff30137f741a715566e205384f9b434ca7ac3847e247bad3f0c9c8a2bd23'
FLASH, COPY = 0x60080000, 0x600800E4
MODULE_BASE = 0x83FA2000
LINK_BASE = MODULE_BASE + 0x240               # bridges first
LIMIT = 0x83FF0000
BRIDGE = {'name': 0x000, 'draw': 0x060, 'minus': 0x0C0, 'plus': 0x110, 'poll': 0x160, 'tick': 0x1B0}
LAST_ITEM = 10                                  # VALUE-menu items 6..10 are the arp's
EXPORTS = ('arp_tick', 'arp_note_event', 'arp_midi_event', 'arp_item_name', 'arp_item_text',
           'arp_item_step', 'arp_poll')
STATE = ('arp_mode', 'arp_rate', 'arp_oct', 'arp_hold', 'arp_bpm100', 'arp_dirty', 'held_count', 'arp_freed',
         'down_count', 'arp_tempo_raw', 'arp_tempo_bank', 'arp_tempo_probe', 'held_note', 'held_vel', 'held_down', 'held_src',
         'arp_run', 'arp_pos', 'arp_step', 'arp_index', 'arp_gated', 'arp_pending', 'arp_age', 'arp_steps')
MENU = {'items': ['ARP', 'RATE', 'A.OCT', 'HOLD', 'BPM'],
        'ARP': ['OFF', 'UP', 'DOWN', 'UP&DN', 'RAND', 'ORDER', 'CHORD'],
        'RATE': ['1/4', '1/4T', '1/8', '1/8T', '1/16', '1/16T', '1/32'],
        'A.OCT': [-3, 3], 'HOLD': ['OFF', 'ON'], 'BPM': 'read-only: the project/bank tempo the arp follows',
        'gate': 0.5}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def assemble(text, at):
    from keystone import Ks, KS_ARCH_ARM, KS_MODE_LITTLE_ENDIAN, KS_MODE_THUMB
    return bytes(Ks(KS_ARCH_ARM, KS_MODE_THUMB | KS_MODE_LITTLE_ENDIAN).asm(text, at)[0])


def load(reg, value):
    return f'movw {reg}, #{value & 0xFFFF}\nmovt {reg}, #{value >> 16}\n'


def call(target):
    return load('r12', target | 1) + 'blx r12\n'


def jump(target):
    return load('r12', target | 1) + 'bx r12\n'


def scatter(image):
    a, b = struct.unpack_from('<2I', image, 0x80)
    return [list(struct.unpack_from('<4I', image, i)) for i in range(a + 0x80, b + 0x80, 16)]


def file_offset(image, address, size):
    for src, dst, n, handler in scatter(image):
        if handler == COPY and dst <= address and address + size <= dst + n:
            return src - FLASH + address - dst
    raise ValueError(f'{address:#x} not in a copy row')


MD = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)


def listing(code, address):
    return [f'{i.mnemonic} {i.op_str}'.strip() for i in MD.disasm(code, address)]


def checked_asm(source, address):
    """Assemble, then insist every direct branch hits a target named in the source or a local label
    (keystone once turned beq.w #0x8015bfa6 into beq.w #0x800b8036)."""
    code = assemble(source, address)
    named = {int(t, 16) for t in re.findall(r'#(0x[0-9a-fA-F]+)', source)}
    for ins in MD.disasm(code, address):
        if ins.mnemonic.startswith(('b', 'cb')) and ins.op_str.split(', ')[-1].startswith('#'):
            target = int(ins.op_str.split('#')[-1], 16)
            if target not in named and not address <= target < address + len(code):
                raise ValueError(f'{address:#x}: branch at {ins.address:#x} to {target:#x}')
    return code


def read_module(data, link_base, limit):
    """Allocated ELF sections (BSS as zeros, copied at boot) and every global symbol."""
    if data[:7] != b'\x7fELF\x01\x01\x01' or struct.unpack_from('<H', data, 18)[0] != 40:
        raise ValueError('expected little-endian ARM ELF32')
    shoff = struct.unpack_from('<I', data, 32)[0]
    shsize, count, _ = struct.unpack_from('<3H', data, 46)
    sections = [struct.unpack_from('<10I', data, shoff + i * shsize) for i in range(count)]
    syms, blocks, end = {}, [], link_base
    for name, kind, flags, addr, off, size, link, info, align, entsize in sections:
        if flags & 2 and size:
            if not link_base <= addr < limit or addr + size > limit:
                raise ValueError('unexpected allocated ELF address')
            blocks.append((addr, bytes(size) if kind == 8 else data[off:off + size]))
            end = max(end, addr + size)
        if kind == 2:
            strings = sections[link]
            text = data[strings[4]:strings[4] + strings[5]]
            for at in range(off, off + size, entsize):
                n, value, _, sym_info = struct.unpack_from('<3IB', data, at)
                label = text[n:].split(b'\0')[0].decode('ascii')
                if sym_info >> 4 == 1 and label:
                    syms[label] = value
    out = bytearray(end - link_base)
    for addr, block in blocks:
        out[addr - link_base:addr - link_base + len(block)] = block
    return bytes(out), syms


def compile_module(parent_symbols, folder, zig):
    folder.mkdir(parents=True, exist_ok=True)
    links = ''.join(f'#define {key}_ADDRESS {parent_symbols[name] & ~1:#x}u\n' for key, name in
                    (('NOTE_EVENT', 'note_event'), ('MIDI_EVENT', 'midi_event'),
                     ('ENV_QUICK', 'env_quick'), ('ENV_FAST', 'env_fast')))
    (folder / 'arp_links.h').write_text(links, encoding='ascii', newline='\n')
    keep = ' '.join(f'KEEP(*(.text.{name}))' for name in EXPORTS)
    (folder / 'arp.ld').write_text(f'''ENTRY(arp_tick)
SECTIONS {{
 . = {LINK_BASE:#x};
 .text : {{ {keep} *(.text*) }}
 .rodata : ALIGN(16) {{ *(.rodata*) }}
 .data : ALIGN(16) {{ *(.data*) }}
 .bss : ALIGN(16) {{ *(.bss*) *(COMMON) }}
 /DISCARD/ : {{ *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }}
}}
''', encoding='ascii', newline='\n')
    cache = folder / 'cache'
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(cache), ZIG_LOCAL_CACHE_DIR=str(cache))
    if subprocess.check_output([str(zig), 'version'], text=True).strip() != '0.13.0':
        raise ValueError('needs Zig 0.13.0')
    source = folder / 'arp.c'                       # compile LF-normalized source
    source.write_bytes((HERE / 'arp.c').read_bytes().replace(b'\r\n', b'\n'))
    subprocess.run([str(zig), 'cc', '-target', 'thumb-freestanding-eabihf', '-mcpu=cortex_m7+vfp4d16',
                    '-mfloat-abi=hard', '-O2', '-ffunction-sections', '-fdata-sections', '-nostdlib',
                    '-Wl,--no-undefined', '-Wl,--build-id=none', '-Wl,-e,arp_tick', '-Wl,-T,' + str(folder / 'arp.ld'),
                    '-I', str(folder), str(source), '-o', str(folder / 'arp.elf')],
                   check=True, env=env)
    code, symbols = read_module((folder / 'arp.elf').read_bytes(), LINK_BASE, LIMIT)
    missing = [n for n in EXPORTS + STATE if n not in symbols]
    if missing:
        raise ValueError(f'module exports missing: {missing}')
    return code, {n: symbols[n] for n in sorted(set(EXPORTS + STATE))}


def bridges(sym):
    """Small assembly glue at MODULE_BASE; each re-creates the stock instructions it displaces."""
    at = {k: MODULE_BASE + v for k, v in BRIDGE.items()}
    src = {}
    # Item names (setting 0x83's value text), entered from 0x800bbc98: r4 = text buffer, r5 = value.
    src['name'] = ('cmp r5, #5\nbls stock\n' + f'cmp r5, #{LAST_ITEM}\n' + 'bhi dflt\nmov r0, r5\nmov r1, r4\n' +
                   call(sym['arp_item_name']) + jump(0x800BBCC2) +            # add sp, #0x68; pop {r4, r5, r7, pc}
                   'dflt:\n' + jump(0x800BBDC4) +
                   'stock:\n' + load('r12', 0x800BBCA6) + 'ldrh.w r0, [r12, r5, lsl #1]\n'
                   'add.w r12, r12, r0, lsl #1\norr r12, r12, #1\n' + load('r0', 0x800BBCA6) + 'bx r12\n')
    # SG screen value text, entered from 0x80123be6: the displaced draw call, then the 6-way switch.
    src['draw'] = (call(0x800D4FA0) + 'cmp r6, #5\nbls stock\n' + f'cmp r6, #{LAST_ITEM}\n' + 'bhi done\nmov r0, r6\n'
                   'add r1, sp, #0x28\n' + call(sym['arp_item_text']) + 'done:\n' + jump(0x80123C5A) +
                   'stock:\n' + load('r12', 0x80123BF2) + 'ldrb.w r0, [r12, r6]\n'
                   'add.w r12, r12, r0, lsl #1\norr r12, r12, #1\nbx r12\n')
    # VALUE -/+ in edit mode, entered from 0x8015c7f4 / 0x8015c85c: r0 = cursor, r4 = keyboard.
    for key, table, step in (('minus', 0x8015C7FE, 'mvn r1, #0\n'), ('plus', 0x8015C866, 'movs r1, #1\n')):
        src[key] = ('cmp r0, #5\nbls stock\n' + f'cmp r0, #{LAST_ITEM}\n' + 'bhi unhandled\n' + step +
                    call(sym['arp_item_step']) +
                    jump(0x8015CC84) +                                        # movs r0, #1; add sp; pop
                    'unhandled:\n' + jump(0x8015C5D8) +
                    'stock:\n' + load('r12', table) + 'ldrh.w r0, [r12, r0, lsl #1]\n'
                    'add.w r12, r12, r0, lsl #1\norr r12, r12, #1\nbx r12\n')
    # SG screen update, entered at its start (0x80123ce0) with r0 = screen.
    src['poll'] = ('push {r4, r5, r6, lr}\nmov r4, r0\n' + call(sym['arp_poll']) + 'cbz r0, nodraw\n'
                   'mov r0, r4\n' + call(0x800D0018) + 'nodraw:\n' + load('r5', 0x82E01144) + jump(0x80123CEC))
    # Render wrapper, called from 0x1fd84 (blx): the arp clock, then the displaced instructions.
    src['tick'] = ('push {r0, r1, r2, r3, r12, lr}\nmov r0, r2\n' + call(sym['arp_tick']) +
                   'pop {r0, r1, r2, r3, r12, lr}\nmov r4, r1\nmov r5, r2\nmov r6, r3\n'
                   'movw r7, #0x9800\nbx lr\n')
    blob = bytearray(LINK_BASE - MODULE_BASE)
    for key, text in src.items():
        code = checked_asm(text, at[key])
        nxt = min([v for v in at.values() if v > at[key]] + [LINK_BASE])
        if at[key] + len(code) > nxt:
            raise ValueError(f'bridge {key} too long ({len(code)} bytes)')
        blob[at[key] - MODULE_BASE:at[key] - MODULE_BASE + len(code)] = code
    return bytes(blob), at


# Each site: its size, the exact v9.6 instructions (capstone text) and the replacement.
def sites(sym, at, parent_symbols):
    note, midi = parent_symbols['note_event'] | 1, parent_symbols['midi_event'] | 1
    return [
        (0x800C19F4, 2, ['movs r0, #5'], f'movs r0, #{LAST_ITEM}\n'),
        (0x800BBC98, 14, ['cmp r5, #5', 'bhi.w #0x800bbdc4', 'addw r0, pc, #6', 'tbh [pc, r5, lsl #1]'],
         jump(at['name']) + 'nop\nnop\n'),
        (0x80123BE6, 12, ['bl #0x800d4fa0', 'cmp r6, #5', 'bhi #0x80123c5a', 'tbb [pc, r6]'],
         jump(at['draw']) + 'nop\n'),
        (0x80123CE0, 12, ['push {r4, r5, r6, lr}', 'movw r5, #0x1144', 'mov r4, r0', 'movt r5, #0x82e0'],
         jump(at['poll']) + 'nop\n'),
        (0x8015C7F4, 10, ['cmp r0, #5', 'bhi.w #0x8015c5d8', 'tbh [pc, r0, lsl #1]'], jump(at['minus'])),
        (0x8015C85C, 10, ['cmp r0, #5', 'bhi.w #0x8015c5d8', 'tbh [pc, r0, lsl #1]'], jump(at['plus'])),
        (0x0001FD84, 10, ['mov r4, r1', 'mov r5, r2', 'mov r6, r3', 'movw r7, #0x9800'], call(at['tick'])),
        (0x8353B000, 10, [f'movw ip, #{note & 0xFFFF:#x}', f'movt ip, #{note >> 16:#x}', 'bx ip'],
         jump(sym['arp_note_event'])),
        (0x8005A698, 10, [f'movw ip, #{midi & 0xFFFF:#x}', f'movt ip, #{midi >> 16:#x}', 'blx ip'],
         call(sym['arp_midi_event'])),
    ]


def build(base, parent, folder, zig):
    if sha(base) != PARENT_SHA or parent['sha256'] != PARENT_SHA or parent['revision'] != 'v9.6-fixes':
        raise ValueError('expected the exact v9.6-fixes APP1 and its manifest')
    code, sym = compile_module(parent['symbols'], folder, zig)
    blob, at = bridges(sym)
    blob += code
    if MODULE_BASE + len(blob) > LIMIT:
        raise ValueError('module does not fit below 0x83ff0000')
    rows = scatter(base)
    for src, dst, n, handler in rows:
        if handler == COPY and max(dst, MODULE_BASE) < min(dst + n, MODULE_BASE + len(blob)):
            raise ValueError('module overlaps copied memory')
    out, hooks = bytearray(base), []
    for address, size, expected, source in sites(sym, at, parent['symbols']):
        off = file_offset(base, address, size)
        found = listing(base[off:off + size], address)
        if found != expected:
            raise ValueError(f'{address:#x}: expected {expected}, found {found}')
        new = checked_asm(source, address)
        if len(new) != size:
            raise ValueError(f'{address:#x}: patch is {len(new)} bytes, site {size}')
        out[off:off + size] = new
        hooks.append([address, size])
    file = (len(out) + 15) & ~15
    out += bytes(file - len(out)) + blob
    table = (len(out) + 15) & ~15
    out += bytes(table - len(out))
    for row in rows + [[FLASH + file, MODULE_BASE, len(blob), COPY]]:
        out += struct.pack('<4I', *row)
    struct.pack_into('<2I', out, 0x80, table - 0x80, len(out) - 0x80)
    image = bytes(out)
    info = dict(parent)
    info.update(revision='v9.9.2-arp', parent_revision=parent['revision'], parent_sha256=PARENT_SHA,
                sha256=sha(image), size=len(image), arp_code_base=MODULE_BASE, arp_code_file=file,
                arp_code_size=len(blob), arp_symbols=sym, arp_bridges=at, arp_hooks=hooks,
                arp_module_sha256=sha(blob),
                arp_c_sha256=sha((HERE / 'arp.c').read_bytes().replace(b'\r\n', b'\n')),
                status='hardware-unverified', published=False, arp_menu=MENU,
                tests='pending: synth/test_arp.py (workspace harness) and synth/test_v9_9_2.py')
    info['hardware_validation'] = {'source': 'none yet', 'date': None, 'coverage': 'none',
                                   'detail': 'v9.9.2-arp candidate has not been tested on hardware',
                                   'cpu_headroom': 'unmeasured'}
    return image, info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('v96', type=Path, help='exact v9.6-fixes SP404MKII_APP1.bin (manifest.json and APP0 beside it)')
    ap.add_argument('--zig', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    if any((args.out / n).exists() for n in ('SP404MKII_APP1.bin', 'SP404MKII_APP0.bin', 'manifest.json')):
        raise ValueError('refusing to overwrite firmware or manifest')
    base = args.v96.read_bytes()
    app0 = args.v96.with_name('SP404MKII_APP0.bin').read_bytes()
    if sha(app0) != APP0_SHA:
        raise ValueError('expected stock APP0 beside the input')
    parent = json.loads(args.v96.with_name('manifest.json').read_text(encoding='utf-8'))
    args.out.mkdir(parents=True, exist_ok=True)
    image, info = build(base, parent, (args.out / 'build').resolve(), args.zig.resolve())
    (args.out / 'SP404MKII_APP1.bin').write_bytes(image)
    (args.out / 'SP404MKII_APP0.bin').write_bytes(app0)
    (args.out / 'manifest.json').write_text(json.dumps(info, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f'v9.9.2-arp APP1: {len(image)} bytes; SHA-256 {info["sha256"]}')


if __name__ == '__main__':
    main()
