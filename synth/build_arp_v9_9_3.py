"""Build v9.9.3: use the active sample bank, as the normal TEMPO SEL screen does.

The v9.9.2 source (arp.c) and its CLI remain available for exact reproduction.
Uses the same nine hooks and v9.6-fixes parent as v9.9.2; APP0 remains stock.
"""
import argparse
import json
from pathlib import Path

import build_arp as builder

HERE = Path(__file__).resolve().parent
REVISION = 'v9.9.3-arp'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('v96', type=Path)
    ap.add_argument('--zig', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    if any((args.out / name).exists() for name in ('SP404MKII_APP1.bin', 'SP404MKII_APP0.bin', 'manifest.json')):
        raise ValueError('refusing to overwrite firmware or manifest')
    base = args.v96.read_bytes()
    app0 = args.v96.with_name('SP404MKII_APP0.bin').read_bytes()
    if builder.sha(app0) != builder.APP0_SHA:
        raise ValueError('expected stock APP0 beside the input')
    parent = json.loads(args.v96.with_name('manifest.json').read_text(encoding='utf-8'))
    image, info = builder.build(base, parent, (args.out / 'build').resolve(), args.zig.resolve(),
                                source_path=HERE / 'arp_v9_9_3.c', revision=REVISION)
    info['tests'] = 'pending: synth/test_arp.py, test_arp_regression.py, test_voice_fixes.py and test_bank_tempo.py'
    (args.out / 'SP404MKII_APP1.bin').write_bytes(image)
    (args.out / 'SP404MKII_APP0.bin').write_bytes(app0)
    (args.out / 'manifest.json').write_text(json.dumps(info, indent=2, sort_keys=True) + '\n', encoding='ascii', newline='\n')
    print(f'{REVISION} APP1: {len(image)} bytes; SHA-256 {info["sha256"]}')


if __name__ == '__main__':
    main()
