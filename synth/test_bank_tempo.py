"""Compare the arp with the real TEMPO SEL screen and native parameter getters.

Run from the sp404mk2 workspace:
    python repo/doom-os-etc/synth/test_bank_tempo.py <candidate-folder>

The fixture supplies stored project/bank state and initialized object vtables.
Only graphics and button-state reads are stubbed: no tempo, bank or parameter
getter is replaced. There is no RTOS, device access or hardware timing test.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import struct

from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R3

ROOT = Path(__file__).resolve().parents[3]
PROJECT, PADSEL, DATA = 0x82E009D0, 0x82DFFC88, 0x82E2CD08
SCREEN, TEXT = 0x30018000, 0x30014000


def main(folder):
    source = ROOT / 'waves_v9_ram/test_v9.py'
    ns = {'__file__': str(source), '__name__': 'bank_tempo_harness'}
    exec(compile(source.read_text(encoding='utf-8').split('# 1. Layout')[0], str(source), 'exec'), ns)
    m = ns['Fast'](str(folder / 'SP404MKII_APP1.bin'))
    symbols = json.loads((folder / 'manifest.json').read_text())['arp_symbols']

    def put(address, value):
        m.uc.mem_write(address, struct.pack('<I', value & 0xFFFFFFFF))

    def string(address):
        return bytes(m.uc.mem_read(address, 80)).split(b'\0')[0].decode('ascii')

    # Production vtables: real project getter 0x800dda38 and pad-selection getter
    # 0x800e26d0. Main-screen bank (project parameter 0) is independent of the
    # pad-selection dialog's parameter 0x7a and the last selected sample's bank.
    put(PROJECT, 0x80220FCC)
    put(PADSEL, 0x8021F69C)
    put(DATA + 0x18, 5)
    texts = []

    def graphics(uc, address, size, context):
        if address in (0x800D4FA0, 0x800D80E0, 0x800CE5B8):
            texts.append(string(uc.reg_read(UC_ARM_REG_R3)))
        uc.reg_write(UC_ARM_REG_R0, 0)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    for address in (0x800D8018, 0x800C2C90, 0x800D80D8, 0x800D80E0,
                    0x800CE548, 0x800CE580, 0x800D4FA0, 0x800CE5B8, 0x800D9F20):
        m.uc.hook_add(UC_HOOK_CODE, graphics, begin=address, end=address)

    def screen_and_arp():
        texts.clear()
        m.call(0x80104F68, SCREEN)  # normal SHIFT + pad 11 TEMPO SEL draw
        assert texts[0] == 'C1:TEMPO SEL', texts
        screen_bpm = int(Decimal(texts[-2]) * 100)
        m.call(symbols['arp_item_text'], 10, TEXT)
        arp_bpm = int(Decimal(string(TEXT)) * 100)
        return screen_bpm, arp_bpm

    def require_match(label):
        expected, actual = screen_and_arp()
        assert actual == expected, f'{label}: TEMPO SEL = {expected / 100:g}, arp = {actual / 100:g} BPM'

    # Hardware report: BANK mode stays at 90 although TEMPO SEL changes.
    put(DATA + 0x14, 2)  # active bank C
    put(PADSEL + 0x2BC, 0)  # stale dialog bank A
    put(DATA + 0x28, 14000)  # BANK mode; project tempo itself is 140
    for bank in range(10):
        put(DATA + 0x58 + bank * 4, (9000 + bank * 1000) << 1)
    require_match('active bank C versus stale dialog bank A')
    assert 'BANK C' in texts
    print('PASS native TEMPO SEL shows BANK C at 110 BPM; arp agrees despite stale bank A at 90', flush=True)

    for active in range(10):
        put(DATA + 0x14, active)
        for dialog in range(10):
            put(PADSEL + 0x2BC, dialog)
            require_match(f'active bank {active}, dialog bank {dialog}')
    print('PASS all 100 active/dialog bank combinations match the native tempo screen', flush=True)

    put(DATA + 0x14, 2)
    put(PADSEL + 0x2BC, 0)
    for bpm in (6500, 9700, 14000, 20100):
        put(DATA + 0x58 + 2 * 4, bpm << 1)
        require_match(f'bank C tempo changed to {bpm}')
    print('PASS changing the active bank tempo updates the arp without changing dialog selection', flush=True)

    for active in range(10):
        put(DATA + 0x14, active)
        put(DATA + 0x28, 13700 | (1 << 16))  # PROJECT mode
        require_match(f'PROJECT mode, active bank {active}')
        assert screen_and_arp() == (13700, 13700)
    print('PASS PROJECT mode remains 137 BPM for all ten active banks', flush=True)
    assert not m.flash, sorted(m.flash)
    print('ALL 4 BANK-TEMPO CHECKS PASS (native screen/getters; emulation, not hardware).')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('folder', type=Path)
    main(ap.parse_args().folder)
