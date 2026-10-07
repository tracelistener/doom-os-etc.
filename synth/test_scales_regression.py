"""Run the unchanged v9.5 behavioral suite against a scale candidate.

Only adapt the suite's image path and layout assertions for the extra copy row
and declared scale hooks. No audio, envelope, MIDI or stealing checks are skipped.
The original test file is never edited. Run from the sp404mk2 workspace.
"""
import argparse
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]

def main(folder):
    source=ROOT/'waves_v9_ram/test_v9.py'
    text=source.read_text(encoding='utf-8')
    def replace(old,new):
        nonlocal text
        assert text.count(old)==1,old
        text=text.replace(old,new)
    replace("FOLDER = W + 'doom-poly-waves-v9.5-ram' + os.sep",
            'FOLDER = '+repr(str(folder.resolve())+'\\'))
    replace("for address, size in MAN['v9_hooks']:",
            "for address, size in MAN['v9_hooks'] + MAN['scale_hooks']:")
    replace("rows9[:-1] == rows8[:-1]", "rows9[:-2] == rows8[:-1]")
    replace("and rows9[-1] == [FLASH + code_file, CODE_BASE, MAN['code_size'], COPY]",
            "and rows9[-2] == [FLASH + code_file, CODE_BASE, MAN['code_size'], COPY]\n"
            "      and rows9[-1] == [FLASH + MAN['scale_code_file'], MAN['scale_code_base'], MAN['scale_code_size'], COPY]")
    replace("args[1] in (0x8353B000, CODE_BASE)",
            "args[1] in (0x8353B000, CODE_BASE, MAN['scale_code_base'])")
    replace('last = rows9[-1]','last = rows9[-2]')
    replace("'one copy row into unused SDRAM'","'original v9.5 copy row plus separate scale row into unused SDRAM'")
    exec(compile(text,str(source),'exec'),{'__file__':str(source),'__name__':'__main__'})

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('folder',type=Path)
    main(ap.parse_args().folder)
