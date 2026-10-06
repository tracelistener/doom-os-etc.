"""Generate lookup data and compile the oscillator C source using Zig 0.13.0.

Only generated build outputs are written. No dependencies installed here.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess

HERE = Path(__file__).resolve().parent
LINK_BASE = 0x60334C00


def table_header():
    def cfloat(x):
        # Hex floats preserve the nearest IEEE single precision value.
        x = struct.unpack('<f', struct.pack('<f', x))[0]
        return float(x).hex() + 'f'

    def array(name, values, shape):
        flat = [cfloat(x) for x in values]
        if len(shape) == 1:
            body = ',\n'.join(','.join(flat[i:i+8]) for i in range(0, len(flat), 8))
        else:
            body = ',\n'.join('{' + ','.join(flat[i:i+shape[1]]) + '}' for i in range(0, len(flat), shape[1]))
        return 'static const float ' + name + ''.join(f'[{n}]' for n in shape) + ' = {\n' + body + '\n};\n'

    header = '// Generated from the local Wave Lab recipes.\n'
    header += array('sine_table', [math.sin(2*math.pi*i/2048) for i in range(2049)], [2049])
    frames = []
    for frame in range(32):
        u = frame/31
        center, width = 1 + 23*u*u, 0.8 + 3*u
        values = []
        for i in range(256):
            th = 2*math.pi*i/256
            v = 0.6*math.sin(th)
            for h in range(2, 33):
                a = math.exp(-((h-center)**2)/(2*width*width))/math.sqrt(h)
                v += a*math.sin(h*th + (h%3)*0.7*u)
            # Browser Float32Array rounds before computing its normalization.
            values.append(struct.unpack('<f', struct.pack('<f', v))[0])
        peak = max(1e-9, max(abs(v) for v in values))
        frames += [v/peak for v in values]
    header += array('scanned_table', frames, [32, 256])
    header += 'static const int organ_h[9] = {1,3,2,4,6,8,10,12,16};\n'
    regs = ['008000000','886000000','888000000','888800000','888888888']
    header += array('organ_regs', [10**(-3*(8-int(ch))/20) if ch != '0' else 0 for s in regs for ch in s], [5, 9])
    vowels = [[730,1090,2440],[530,1840,2480],[270,2290,3010],[570,840,2410],[300,870,2240]]
    header += array('vowels', sum(vowels, []), [5, 3])
    header += array('vowel_ratio_log2', [math.log2(vowels[i+1][k]/vowels[i][k]) for i in range(4) for k in range(3)], [4, 3])
    header += array('vowel_amp', [1,0.55,0.3], [3])
    header += array('vowel_bw', [80,90,120], [3])
    # SP DUTY is an integer 0..100. Compute parameter-only work offline,
    # never in the real-time harmonic loop. These are immutable flash data.
    lp = []
    for odd in (False, True):
        for duty in range(101):
            nc = 2**(0.2 + 5.2*duty/100)
            for n in range(1,65):
                x = n/nc
                gain = 1/(n*((1-x*x)**2 + (x/1.4)**2))
                lp.append(0 if odd and n%2 == 0 else gain)
    header += array('lp_gains', lp, [202,64])
    drive, organ, formants, scales = [], [], [], []
    for duty in range(101):
        d = duty/100
        g = 1 + 14*d*d
        t0 = math.tanh(g*0.35)
        norm = max(math.tanh(g*1.35)-t0, t0-math.tanh(-g*0.65))
        drive += [g,t0,1/norm]
        pos = d*4
        i = min(3,int(pos))
        t = pos-i
        gains = [10**(-3*(8-int(ch))/20) if ch != '0' else 0 for s in regs for ch in s]
        organ += [gains[i*9+j]*(1-t) + gains[(i+1)*9+j]*t for j in range(9)]
        formants += [vowels[i][k]*(vowels[i+1][k]/vowels[i][k])**t for k in range(3)]
        scales += [2**(4*d),2**(3*d)]
    header += array('drive_params', drive, [101,3])
    header += array('organ_gains', organ, [101,9])
    header += array('formant_freq', formants, [101,3])
    header += array('cz_scales', scales, [101,2])
    header += array('tanh_table', [math.tanh(10*i/1024) for i in range(1025)], [1025])
    header += array('exp2_fraction', [2**(i/256) for i in range(257)], [257])
    return header


def read_elf(data):
    """Extract only fixed-address allocated sections and exported ARM symbols."""
    if data[:7] != b'\x7fELF\x01\x01\x01':
        raise ValueError('expected little-endian ELF32')
    if struct.unpack_from('<H', data, 18)[0] != 40:
        raise ValueError('expected ARM ELF')
    shoff = struct.unpack_from('<I', data, 32)[0]
    shsize, count, strings_index = struct.unpack_from('<3H', data, 46)
    sections = [struct.unpack_from('<10I', data, shoff+i*shsize) for i in range(count)]
    syms = {}
    end = LINK_BASE
    blocks = []
    for name, kind, flags, addr, off, size, link, info, align, entsize in sections:
        if flags & 2 and size:
            if kind == 8:
                raise ValueError('mutable BSS is not allowed in the waveform module')
            if not LINK_BASE <= addr < 0x60400000 or addr + size > 0x60400000:
                raise ValueError('unexpected allocated ELF address')
            blocks.append((addr, data[off:off+size]))
            end = max(end, addr+size)
        if kind == 2:
            strings = sections[link]
            text = data[strings[4]:strings[4]+strings[5]]
            for at in range(off, off+size, entsize):
                n, value, _, _, _, _ = struct.unpack_from('<3IBBH', data, at)
                label = text[n:].split(b'\0')[0].decode('ascii')
                if label in ('wave_eval','wave_output','wave_name'):
                    syms[label] = value
    if set(syms) != {'wave_eval','wave_output','wave_name'}:
        raise ValueError('wave exports missing')
    out = bytearray(end-LINK_BASE)
    for addr, block in blocks:
        out[addr-LINK_BASE:addr-LINK_BASE+len(block)] = block
    return bytes(out), syms


def compile_module(zig, out):
    version = subprocess.check_output([str(zig),'version'],text=True).strip()
    if version != '0.13.0':
        raise ValueError('reproducible build requires Zig 0.13.0, found ' + version)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'wave_tables.h').write_text(table_header(), encoding='ascii')
    # No libc, soft-float helpers, host startup code or external unresolved symbols.
    # VFPv4-D16 is a conservative subset of the M7's FPv5-D16. It also
    # avoids optional FPv5 conditional-select instructions unsupported by
    # our instruction-level emulator. The hard-float ABI remains identical.
    args = [str(zig), 'cc', '-target', 'thumb-freestanding-eabihf', '-mcpu=cortex_m7+vfp4d16',
            '-mfpu=vfpv4-d16', '-mfloat-abi=hard', '-O2', '-ffreestanding',
            '-fno-builtin', '-fno-stack-protector', '-ffp-contract=off',
            '-ffunction-sections', '-fdata-sections',
            '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
            '-nostdlib', '-Wl,--no-undefined', '-Wl,--build-id=none',
            '-Wl,-e,wave_output',
            '-Wl,-T,' + str(HERE / 'waves.ld'), '-I', str(out),
            str(HERE / 'waves.c'), '-o', str(out / 'waves.elf')]
    subprocess.run(args, check=True, cwd=HERE.parent)
    blob, symbols = read_elf((out / 'waves.elf').read_bytes())
    (out / 'waves.bin').write_bytes(blob)
    (out / 'symbols.json').write_text(json.dumps(symbols, sort_keys=True, indent=2) + '\n', encoding='ascii')
    provenance = {'zig':version,'target':'cortex_m7+vfp4d16 / eabihf',
                  'module_sha256':hashlib.sha256(blob).hexdigest(),
                  'sources':{name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in ('waves.c','waves.ld')},
                  'tables_sha256':hashlib.sha256((out/'wave_tables.h').read_bytes()).hexdigest()}
    (out / 'provenance.json').write_text(json.dumps(provenance,sort_keys=True,indent=2)+'\n',encoding='ascii')
    print(f'Wave module: {len(blob)} bytes, {symbols}')
    return blob, symbols


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zig', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=HERE.parent / 'build' / 'waves-v3-budget')
    args = parser.parse_args()
    compile_module(args.zig.resolve(), args.out.resolve())
