"""Arpeggiator emulation suite: real SP-404MKII firmware code in Unicorn, not a hardware test.

    python waves_v97_arp/test_arp.py [CANDIDATE PARENT]              (workspace, from sp404mk2)
    python repo/doom-os-etc/synth/test_arp.py CANDIDATE PARENT        (fork copy, from sp404mk2)

CANDIDATE: folder with the arp build's SP404MKII_APP1.bin and manifest.json (default
firmware/doom-poly-waves-v9.9.2-arp); PARENT: the v9.6-fixes folder it was built on
(default firmware/doom-poly-waves-v9.6-fixes). Needs the sp404mk2 workspace's emulator
harness (waves_v9_ram/test_v9.py and analysis/) and .poly_vendor on PYTHONPATH.

1. Layout: only the 9 declared hook sites change in v9.6-fixes' bytes; one new copy row.
2. ARP OFF is v9.6 exactly: chords, TYPE sweeps, stealing (audio running) and MIDI IN
   produce bit-identical audio and voice state.
3. VALUE menu through the real stock code: cursor range 0..10, item names, value text on
   the SG screen, VALUE -/+ (bounds, stock items 0..5 dispatch unchanged), screen update.
4. Arp engine through the real render loop and pad/MIDI paths: step and gate timing at
   the project BPM without drift, every motif, A.OCT, HOLD/latch, tempo and RATE changes,
   the tempo source (project, the bank on the pads, override, MIDI clock, pattern),
   busy voices taken with the quick fade, page changes, ARP on/off with nothing stuck.
5. CPU per render block; zero flash access.
No RTOS, peripherals, screen or real timing.
"""
import argparse
import json
import os
from pathlib import Path
import struct
import sys

import numpy as np
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1,
                               UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R6, UC_ARM_REG_R9,
                               UC_ARM_REG_SP)


def workspace():
    """The sp404mk2 folder: the one holding the emulator harness, waves_v9_ram/test_v9.py."""
    here = Path(__file__).resolve().parent
    for folder in [here, *here.parents]:
        if (folder / 'waves_v9_ram' / 'test_v9.py').is_file():
            return folder
    raise SystemExit('waves_v9_ram/test_v9.py not found: run inside the sp404mk2 workspace')


def main(candidate, parent):
    ROOT = str(workspace())
    HARNESS = os.path.join(ROOT, 'waves_v9_ram', 'test_v9.py')
    g = {'__file__': HARNESS, '__name__': 'arp_harness'}
    exec(compile(open(HARNESS, encoding='utf-8').read().split('# 1. Layout')[0], HARNESS, 'exec'), g)
    Fast, STACK, RET, VOICES, SINGLETON = g['Fast'], g['STACK'], g['RET'], g['VOICES'], g['SINGLETON']
    RELEASE_ALL, envelope = g['RELEASE_ALL'], g['envelope']

    V97 = str(candidate / 'SP404MKII_APP1.bin')
    V96 = str(parent / 'SP404MKII_APP1.bin')
    MAN = json.loads((candidate / 'manifest.json').read_text(encoding='utf-8'))
    SYM = MAN['arp_symbols']
    KB, PAGE_ID, SG, SETTINGS = 0x80591C48, 0x80245880, 0x1E, 0x82E01144
    PAD_TEMPO, TEMPO_GET = 0x80047DD0, 0x800D5640           # stock: tempo for a pad's samples; current tempo
    PADSEL, CUR_BANK = 0x82DFFC88, 0x82DFFF44                # pad-select state; its setting 0x7a (the bank)
    BUF, OBJ = 0x30014000, 0x30018000
    FLASH, COPY = 0x60080000, 0x600800E4
    failures, machines = [], []

    def check(ok, label):
        print(('PASS ' if ok else 'FAIL ') + label, flush=True)
        if not ok:
            failures.append(label)

    def u32(m, address):
        return struct.unpack('<I', bytes(m.uc.mem_read(address, 4)))[0]

    def i32(m, address):
        return struct.unpack('<i', bytes(m.uc.mem_read(address, 4)))[0]

    def put(m, address, value, fmt='<i'):
        m.uc.mem_write(address, struct.pack(fmt, value))

    def get(m, name, fmt='<i'):
        return struct.unpack(fmt, bytes(m.uc.mem_read(SYM[name], struct.calcsize(fmt))))[0]

    def setv(m, name, value, fmt='<i'):
        put(m, SYM[name], value, fmt)

    def text(m, address, n=16):
        return bytes(m.uc.mem_read(address, n)).split(b'\0')[0].decode('latin-1')

    def stub(m, address, value=0, log=None):
        """Make a firmware function return `value` at once (optionally recording r0..r3)."""
        def hook(uc, a, s, _):
            if log is not None:
                log.append(tuple(uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)))
            uc.reg_write(UC_ARM_REG_R0, value(uc) if callable(value) else value)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        return m.uc.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)

    def run_from(m, start, regs, stops, sp=STACK - 0x400, count=400000):
        """Execute from `start` until one of `stops` is reached; returns that address (or None)."""
        uc, hit = m.uc, []
        handles = [uc.hook_add(UC_HOOK_CODE, lambda u, a, s, _, t=t: (hit.append(t), u.reg_write(UC_ARM_REG_PC, RET | 1)),
                               begin=t, end=t) for t in stops]
        uc.reg_write(UC_ARM_REG_SP, sp)
        uc.reg_write(UC_ARM_REG_LR, RET | 1)
        for reg, value in regs.items():
            uc.reg_write(reg, value & 0xFFFFFFFF)
        uc.emu_start(start | 1, RET, count=count)
        for h in handles:
            uc.hook_del(h)
        return hit[0] if hit else None

    class Arp:
        """A v9.7 machine on the SG page that logs every stock note start/release with its time."""

        def __init__(self, path=V97, kind=0, env=1, bpm100=12000):
            self.m = m = Fast(path)
            machines.append(m)
            m.setup(kind, env=env)
            put(m, PAGE_ID, SG, '<h')
            for off, value in ((0, 1), (4, 2), (8, 0), (0xC, 0), (0x10, 0), (0x14, 0)):
                put(m, KB + off, value)
            self.t, self.log = 0, []
            if 'arp_bpm100' in SYM and path == V97:
                setv(m, 'arp_bpm100', bpm100)
            for site in (TEMPO_GET, PAD_TEMPO):                                      # tempo functions
                stub(m, site, lambda uc: bpm100 if path != V97 else get(m, 'arp_bpm100'))

            def start(uc, a, s, _):
                v, on = uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1)
                if v in VOICES:
                    self.log.append((self.t, VOICES.index(v), on, i32(m, v + 0x5C), i32(m, v + 0x74) // 100))
            m.uc.hook_add(UC_HOOK_CODE, start, begin=0x800D0668, end=0x800D0668)

        def blocks(self, n):
            out = []
            for _ in range(n):
                out += self.m.block()[0]
                self.t += 64
            return np.array(out, dtype=float)

        def ms(self, millis):
            return self.blocks(int(round(millis * 48 / 64)))

        def starts(self, since=0):
            return [(t, i, note, vel) for t, i, on, note, vel in self.log if on and t >= since]

        def releases(self, since=0):
            return [(t, i) for t, i, on, note, vel in self.log if not on and t >= since]

        def menu(self, item, direction):
            """VALUE -/+ on item 6..9 through the real stock handler bridge."""
            m = self.m
            site = 0x8015C7F4 if direction < 0 else 0x8015C85C
            return run_from(m, site, {UC_ARM_REG_R0: item, UC_ARM_REG_R4: KB}, (0x8015CC84, 0x8015C5D8))

        def set(self, mode=None, rate=None, oct=None, hold=None):
            for item, name, want in ((6, 'arp_mode', mode), (7, 'arp_rate', rate), (8, 'arp_oct', oct),
                                     (9, 'arp_hold', hold)):
                while want is not None and get(self.m, name) != want:
                    self.menu(item, 1 if want > get(self.m, name) else -1)

        def voices(self):
            return [(self.m.uc.mem_read(v, 1)[0], self.m.uc.mem_read(v + 1, 1)[0]) for v in VOICES]

    # 1. Layout -----------------------------------------------------------------------------------------
    v96, v97 = open(V96, 'rb').read(), open(V97, 'rb').read()
    P96 = json.loads((parent / 'manifest.json').read_text(encoding='utf-8'))

    def scatter(image):
        a, b = struct.unpack_from('<2I', image, 0x80)
        return [list(struct.unpack_from('<4I', image, i)) for i in range(a + 0x80, b + 0x80, 16)]

    def file_offset(image, address, size):
        for src, dst, n, handler in scatter(image):
            if handler == COPY and dst <= address and address + size <= dst + n:
                return src - FLASH + address - dst
        raise ValueError(hex(address))

    allowed = set(range(0x80, 0x88))
    for address, size in MAN['arp_hooks']:
        off = file_offset(v96, address, size)
        allowed.update(range(off, off + size))
    changed = {i for i in range(len(v96)) if v96[i] != v97[i]}
    rows96, rows97 = scatter(v96), scatter(v97)
    new = rows97[-1]
    module = v97[new[0] - FLASH:new[0] - FLASH + new[2]]
    check(changed <= allowed and rows97[:-1] == rows96 and new[1:] == [0x83FA2000, MAN['arp_code_size'], COPY]
          and 0x83FA2000 >= P96['scale_code_base'] + P96['scale_code_size'] and 0x83FA2000 + new[2] <= 0x83FF0000
          and len(MAN['arp_hooks']) == 9,
          f'v9.6 bytes change only at the 9 arp hook sites ({len(changed)} bytes); one new copy row, '
          f'{new[2]:,} bytes at 0x83fa2000 after the scale module, below 0x83ff0000')
    m = Fast(V97)
    machines.append(m)
    check(bytes(m.uc.mem_read(0x83FA2000, new[2])) == module and get(m, 'arp_mode') == 0 and get(m, 'arp_rate') == 4
          and get(m, 'arp_bpm100') == 12000 and get(m, 'held_count') == 0,
          'module loads with its settings at power-on: ARP OFF, RATE 1/16, A.OCT 0, HOLD OFF, empty note list')

    # 2. ARP OFF = v9.6 ---------------------------------------------------------------------------------

    def session(path):
        """Chord, TYPE sweep, a steal with audio running, MIDI chord, releases: audio and voice state."""
        n = Fast(path)
        machines.append(n)
        n.setup(24, env=4)
        put(n, PAGE_ID, SG, '<h')
        out = []
        for src, note in ((1, 21), (2, 25), (3, 28), (4, 33)):
            n.key(note, 100, src)
            out += list(n.render(3))
        for kind in (0, 31, 11, 6):
            n.call(0x80019E30, SINGLETON, 0x7B, kind)
            out += list(n.render(6))
        waited, _ = n.key_live(36, 110, 5)                     # steals with the audio running
        out += list(waited) + list(n.render(10))
        for note in (60, 64, 67):
            n.live(0x8005A680, note, 90, 0)
            out += list(n.render(4))
        for src, note in ((2, 25), (3, 28), (4, 33), (5, 36)):
            n.key(note, 0, src)
            out += list(n.render(3))
        for note in (60, 64, 67):
            n.live(0x8005A680, note, 0, 0)
        out += list(n.render(120))
        state = [bytes(n.uc.mem_read(v, 0x1B0)) for v in VOICES]
        return np.array(out), state

    a96, s96 = session(V96)
    a97, s97 = session(V97)
    check(np.array_equal(a96, a97) and s96 == s97,
          f'ARP OFF is v9.6 exactly: chord, TYPE sweep, a steal with audio running, MIDI chord and releases '
          f'give bit-identical audio ({len(a97)} samples) and voice state')

    # 3. VALUE menu --------------------------------------------------------------------------------------
    LO, HI = BUF, BUF + 4
    ranges = {}
    for path in (V96, V97):
        m = Fast(path)
        machines.append(m)
        m.uc.mem_write(LO, bytes(8))
        m.call(0x800C1780, SETTINGS, 0x83, LO, HI, stack0=0)
        ranges[path] = (i32(m, LO), i32(m, HI))
    check(ranges[V96] == (0, 5) and ranges[V97] == (0, 10), f'VALUE-menu cursor range {ranges[V96]} -> {ranges[V97]}')

    def item_name(m, value):
        m.uc.mem_write(BUF, b'?' * 24)
        m.call(0x800BBA08, SETTINGS, BUF, 0x83, value, stack0=0)
        return bytes(m.uc.mem_read(BUF, 24))

    m6, m7 = Fast(V96), Fast(V97)
    machines += [m6, m7]
    names = [item_name(m7, v).split(b'\0')[0].decode() for v in range(11)]
    same = all(item_name(m6, v) == item_name(m7, v) for v in list(range(6)) + [11, 12, 255])
    check(names == ['SCALE', 'NOTE', 'OCT', 'OFST', 'ENV', 'TUNE', 'ARP', 'RATE', 'A.OCT', 'HOLD', 'BPM'] and same,
          'item names (real stock value-text function): ' + ' '.join(names) + '; 0..5 and invalid values unchanged')

    def screen_value(m, cursor):
        """SG screen from the patched value switch: our text in its buffer, or the stock case reached."""
        sp = STACK - 0x800
        m.uc.mem_write(sp, bytes(0x80))
        stub(m, 0x800D4FA0)                                    # the displaced draw call: nothing to draw on
        cases = (0x80123BF8, 0x80123C0C, 0x80123C30, 0x80123C44, 0x80123CA0, 0x80123CB4)
        hit = run_from(m, 0x80123BE6, {UC_ARM_REG_R6: cursor, UC_ARM_REG_R4: OBJ, UC_ARM_REG_R9: KB},
                       cases + (0x80123C5A,), sp=sp)
        return hit, text(m, sp + 0x28)

    m = Fast(V97)
    machines.append(m)
    shown = {}
    for mode in range(7):
        setv(m, 'arp_mode', mode)
        shown.setdefault(6, []).append(screen_value(m, 6)[1])
    for rate in range(7):
        setv(m, 'arp_rate', rate)
        shown.setdefault(7, []).append(screen_value(m, 7)[1])
    for octave in range(-3, 4):
        setv(m, 'arp_oct', octave)
        shown.setdefault(8, []).append(screen_value(m, 8)[1])
    for hold in (0, 1):
        setv(m, 'arp_hold', hold)
        shown.setdefault(9, []).append(screen_value(m, 9)[1])
    stock_cases = [screen_value(m, c)[0] for c in range(6)]
    past = screen_value(m, 11)[0]
    check(shown[6] == ['OFF', 'UP', 'DOWN', 'UP&DN', 'RAND', 'ORDER', 'CHORD']
          and shown[7] == ['1/4', '1/4T', '1/8', '1/8T', '1/16', '1/16T', '1/32']
          and shown[8] == ['-3', '-2', '-1', '0', '+1', '+2', '+3'] and shown[9] == ['OFF', 'ON']
          and stock_cases == [0x80123BF8, 0x80123C0C, 0x80123C30, 0x80123C44, 0x80123CA0, 0x80123CB4]
          and past == 0x80123C5A,
          'SG screen value text: ' + ' / '.join(' '.join(v) for v in shown.values()) +
          '; SCALE..TUNE reach their stock cases; cursor 11 skips as stock')

    m = Fast(V97)
    machines.append(m)
    released = []
    stub(m, 0x0001FE00, 0, released)                          # count all-stops
    stub(m, TEMPO_GET, 12000)                                  # tempo (the pad object is not built without a boot)
    stub(m, PAD_TEMPO, 12000)
    steps, exits = [], []
    for item, name, lo, hi in ((6, 'arp_mode', 0, 6), (7, 'arp_rate', 0, 6), (8, 'arp_oct', -3, 3), (9, 'arp_hold', 0, 1)):
        seen = [get(m, name)]
        for direction in [1] * 10 + [-1] * 15 + [1] * 3:
            exits.append(run_from(m, 0x8015C85C if direction > 0 else 0x8015C7F4,
                                  {UC_ARM_REG_R0: item, UC_ARM_REG_R4: KB}, (0x8015CC84, 0x8015C5D8)))
            seen.append(get(m, name))
        steps.append((name, min(seen), max(seen), seen[-1]))
    stock = [run_from(m, site, {UC_ARM_REG_R0: c, UC_ARM_REG_R4: KB}, targets + (0x8015CC84, 0x8015C5D8))
             for site, targets in ((0x8015C7F4, (0x8015C80C, 0x8015CBCC, 0x8015CBF4, 0x8015CC04, 0x8015CC14, 0x8015CC28)),
                                   (0x8015C85C, (0x8015C874, 0x8015CC40, 0x8015CC8C, 0x8015CC9C, 0x8015CCCC, 0x8015CCDC)))
             for c in range(6)]
    outside = run_from(m, 0x8015C85C, {UC_ARM_REG_R0: 11, UC_ARM_REG_R4: KB}, (0x8015CC84, 0x8015C5D8))
    check([s[1:] for s in steps] == [(0, 6, 3), (0, 6, 3), (-3, 3, 0), (0, 1, 1)]
          and set(exits) == {0x8015CC84} and len(released) == 2
          and stock == [0x8015C80C, 0x8015CBCC, 0x8015CBF4, 0x8015CC04, 0x8015CC14, 0x8015CC28,
                        0x8015C874, 0x8015CC40, 0x8015CC8C, 0x8015CC9C, 0x8015CCCC, 0x8015CCDC]
          and outside == 0x8015C5D8,
          'VALUE -/+ on ARP/RATE/A.OCT/HOLD step within 0..6, 0..6, -3..+3, OFF..ON and stop at the ends; '
          'switching ARP on stops directly played notes (all-stop); SCALE..TUNE dispatch as stock')

    m = Fast(V97)
    machines.append(m)
    redraws = []
    stub(m, 0x800D0018, 0, redraws)
    stub(m, TEMPO_GET, 9050)
    stub(m, PAD_TEMPO, 9050)
    put(m, CUR_BANK, 0)
    stub(m, 0x800D94D8, 0)
    stub(m, 0x800D9F20, 0)
    for off, value in ((0, 1), (4, 2), (8, 0), (0xC, 0), (0x10, 0), (0x14, 0)):
        put(m, KB + off, value)
    m.uc.mem_write(OBJ, bytes(0x100))
    for off, value in ((0xA8, 1), (0xAC, 2)):
        put(m, OBJ + off, value)
    m.call(0x80123CE0, OBJ)
    quiet = len(redraws)
    setv(m, 'arp_dirty', 1, '<B')
    m.call(0x80123CE0, OBJ)
    check(quiet == 0 and len(redraws) == 1 and redraws[0][0] == OBJ and get(m, 'arp_dirty', '<B') == 0
          and get(m, 'arp_bpm100') == 9050,
          'SG screen update reads the project tempo (90.50 BPM) and redraws once after an arp change, '
          'not otherwise')

    # 4. Engine ------------------------------------------------------------------------------------------
    STEP = 6000                                               # 1/16 at 120 BPM

    def on_times(a, since=0):
        return [t for t, i, n, v in a.starts(since)]

    a = Arp()
    a.set(mode=1)
    for src, note in ((1, 24), (2, 28), (3, 31)):
        a.m.key(note, 100, src)
    a.blocks(int(40 * STEP / 64) + 2)
    st = a.starts()
    times = [t for t, i, n, v in st]
    gaps = np.diff(times)
    rel = a.releases()
    gate = [next(r for r, i in rel if r > t and i == v) - t for t, v, n, _ in st[:-1]]
    drift = times[39] - 39 * STEP
    check(len(st) >= 40 and [n for t, i, n, v in st[:9]] == [24, 28, 31] * 3 and set(gaps) <= {5952, 6016}
          and abs(drift) <= 64 and all(2944 <= x <= 3072 for x in gate) and {v for *_, v in st} == {100},
          f'UP 1/16 at 120 BPM: steps every {gaps.mean():.1f} samples (one render block of jitter, '
          f'drift {drift} after 39 steps), gate {min(gate)}-{max(gate)} samples, pad velocity kept')
    voices_used = {i for t, i, n, v in st}
    check(len(voices_used) >= 2, f'steps rotate across voices {sorted(voices_used)} so release tails overlap')

    def sequence(mode, keys=((1, 24), (2, 28), (3, 31)), steps=10, octave=0, env=1):
        s = Arp(env=env)
        s.set(mode=mode, oct=octave)
        for src, note in keys:
            s.m.key(note, 100, src)
        s.blocks(int(steps * STEP / 64))
        return s

    seq = {}
    for mode, name in ((2, 'DOWN'), (3, 'UP&DN'), (5, 'ORDER')):
        s = sequence(mode, keys=((1, 28), (2, 31), (3, 24)))
        seq[name] = [n for t, i, n, v in s.starts()][:8]
    r = sequence(4, steps=60)
    rand = [n for t, i, n, v in r.starts()]

    def by_step(starts, step=STEP):
        """CHORD notes that had to take a ringing voice start a few blocks later: group by step."""
        groups = {}
        for t, i, n, v in starts:
            groups.setdefault((t + step // 4) // step, []).append(n)
        return groups

    chord = by_step(sequence(6, steps=4).starts())
    check(seq['DOWN'] == [31, 28, 24, 31, 28, 24, 31, 28] and seq['UP&DN'] == [24, 28, 31, 28, 24, 28, 31, 28]
          and seq['ORDER'] == [28, 31, 24, 28, 31, 24, 28, 31]
          and set(rand) == {24, 28, 31} and all(x != y for x, y in zip(rand, rand[1:]))
          and [sorted(v) for v in chord.values()][:3] == [[24, 28, 31]] * 3,
          f'motifs: DOWN {seq["DOWN"][:3]}, UP&DN {seq["UP&DN"][:5]}, ORDER (pressed 28 31 24) {seq["ORDER"][:3]}, '
          f'RAND never repeats ({len(rand)} steps), CHORD plays all three each step')

    up1 = [n for t, i, n, v in sequence(1, octave=1, steps=7).starts()][:7]
    dn2 = [n for t, i, n, v in sequence(1, octave=-2, steps=10).starts()][:10]
    top = [n for t, i, n, v in sequence(1, keys=((1, 40), (2, 45)), octave=2, steps=6).starts()][:6]
    ch1 = by_step(sequence(6, octave=1, steps=3).starts())
    check(up1 == [24, 28, 31, 36, 40, 43, 24] and dn2 == [0, 4, 7, 12, 16, 19, 24, 28, 31, 0]
          and top == [40, 45, 40, 45, 40, 45] and [sorted(v) for v in ch1.values()][:2] == [[24, 28, 31], [36, 40, 43]],
          f'A.OCT: +1 {up1[:6]}, -2 starts at {dn2[0]}, notes past the top of the range are skipped, '
          f'CHORD moves an octave per step')

    h = Arp()
    h.set(mode=1)
    h.m.key(24, 100, 1)
    h.m.key(28, 100, 2)
    h.blocks(int(4 * STEP / 64))
    h.m.key(24, 0, 1)
    h.m.key(28, 0, 2)
    t0 = h.t
    h.blocks(int(4 * STEP / 64))
    stopped = not h.starts(t0 + 64) and all(not a_ or r for a_, r in h.voices()) and not get(h.m, 'arp_run')
    h.set(hold=1)
    h.m.key(24, 100, 1)
    h.m.key(28, 100, 2)
    h.blocks(int(2 * STEP / 64))
    h.m.key(24, 0, 1)
    h.m.key(28, 0, 2)
    t1 = h.t
    h.blocks(int(4 * STEP / 64))
    latched = sorted({n for t, i, n, v in h.starts(t1)})
    h.m.key(31, 100, 3)                                        # a new key after letting go: new chord
    t2 = h.t
    h.blocks(int(4 * STEP / 64))
    fresh = sorted({n for t, i, n, v in h.starts(t2 + 64)})
    h.m.key(31, 0, 3)
    h.set(hold=0)                                              # unlatch with nothing down: stops
    t3 = h.t
    h.blocks(int(4 * STEP / 64))
    check(stopped and latched == [24, 28] and fresh == [31] and not h.starts(t3 + 64) and get(h.m, 'held_count') == 0,
          'letting go stops the arp (HOLD OFF); HOLD ON keeps a released chord going, the next key starts a '
          'new chord, HOLD OFF with nothing held stops it')

    k = Arp()
    k.set(mode=1, rate=4)
    k.m.key(24, 100, 1)
    k.blocks(int(3 * STEP / 64))
    setv(k.m, 'arp_bpm100', 6000)                              # 60 BPM: steps double
    t0 = k.t
    k.blocks(int(4 * 2 * STEP / 64))
    slow = np.diff(on_times(k, t0))
    k.set(rate=6)                                              # 1/32 at 60 BPM = one 1/16 at 120
    t1 = k.t
    k.blocks(int(6 * STEP / 64))
    fast = np.diff(on_times(k, t1))
    check(set(slow[1:]) <= {11968, 12032} and set(fast[1:]) <= {5952, 6016},
          f'tempo and RATE changes apply from the next step: 60 BPM 1/16 = {slow[1:].mean():.0f} samples, '
          f'1/32 = {fast[1:].mean():.0f}')

    b = Arp(env=4)                                             # Pad: 1.4 s release tails fill all voices
    b.set(mode=1, rate=6)
    for src, note in ((1, 24), (2, 28), (3, 31), (4, 35)):
        b.m.key(note, 100, src)
    freed = 0
    for _ in range(int(24 * 3000 / 64)):
        b.blocks(1)
        freed += any(bytes(b.m.uc.mem_read(SYM['arp_freed'], 4)))
    late = [t - n_ * 3000 for n_, (t, i, note, v) in enumerate(b.starts())]
    for src, note in ((1, 24), (2, 28), (3, 31), (4, 35)):
        b.m.key(note, 0, src)
    b.blocks(int(3 * 48000 / 64))
    check(len(late) >= 24 and all(0 <= x < 64 for x in late) and freed > 20 and all(not a_ for a_, r in b.voices())
          and bytes(b.m.uc.mem_read(MAN['symbols']['env_quick'], 4)) == bytes(4) and get(b.m, 'arp_run') == 0,
          f'with all four voices still ringing (Pad, 1/32), the oldest tail is faded half a step ahead '
          f'({freed} blocks), so every one of {len(late)} steps lands on the grid (max {max(late)} samples late, '
          f'one block); afterwards every voice ends, none left reserved')

    p = Arp()
    p.set(mode=1, hold=1)
    p.m.key(24, 100, 1)                                        # one latched note...
    p.m.key(28, 100, 2)                                        # ...and one key still held
    p.m.key(24, 0, 1)
    p.blocks(int(2 * STEP / 64))
    put(p.m, PAGE_ID, 0x05, '<h')                              # another page: the arp stops and forgets
    t0 = p.t
    p.blocks(int(3 * STEP / 64))
    away = p.starts(t0 + 64)
    forgot = get(p.m, 'held_count') == 0 and get(p.m, 'down_count') == 0
    put(p.m, PAGE_ID, SG, '<h')                                # back: nothing plays by itself
    t1 = p.t
    p.blocks(int(3 * STEP / 64))
    silent = not p.starts(t1) and all(not a_ or r for a_, r in p.voices())
    p.m.key(31, 100, 3)                                        # a new key plays at once
    t2 = p.t
    p.blocks(int(2 * STEP / 64))
    fresh = sorted({n for t, i, n, v in p.starts(t2)})
    check(not away and forgot and silent and fresh == [31],
          'leaving the Sound Generator page stops the arp and forgets its notes (latched or held, whose '
          'note-offs go to the other page); coming back, nothing plays until a new key')

    g = Fast(V97)                                              # the tempo the arp follows, and its readout
    machines.append(g)
    TEMPO, PADOBJ, VT, PAD_GETTER = 0x82E0B71C, 0x82E009D0, 0x30015100, 0x800D56D8
    EXT_SYNC, EXT_BPM = 0x80592245, 0x80245B38                 # MIDI clock sync flag; the clock's BPM (float)
    params, current_pad, asked = {}, [5], []                   # the last selected pad, 5, is in bank A
    put(g, PADOBJ, VT)                                         # the project/pad parameter object's vtable
    put(g, VT + 8, PAD_GETTER | 1, '<I')                       # its parameter getter, answered from `params`
    stub(g, PAD_GETTER, lambda uc: (asked.append(uc.reg_read(UC_ARM_REG_R1)), params.get(uc.reg_read(UC_ARM_REG_R1), 0))[1])
    stub(g, 0x800E26D0, lambda uc: current_pad[0])             # current pad (the fallback's bank)
    stub(g, 0x800D94D8, 9000)                                  # REC BPM: 90, as on the owner's SP
    g.uc.mem_write(TEMPO, bytes(0x210))                        # the real tempo object, as in the SG: its
    put(g, TEMPO + 0x20C, 0)                                   # answer there would be the REC BPM
    BANKS = {0xB: 0, 0xA: 14000, 0x21: 9000, 0x23: 9600, 0x2A: 17250}
    readout, bank_asked = {}, {}
    for label, values, bank, override, clock, pattern in (
            ('project', {**BANKS, 0xB: 1}, 2, 0, None, None),
            ('bank C', BANKS, 2, 0, None, None),              # v9.9.1 read bank A here (the last pad's bank)
            ('bank J', BANKS, 9, 0, None, None),
            ('override', {**BANKS, 0xB: 1}, 2, 12345, None, None),
            ('MIDI clock', {**BANKS, 0xB: 1}, 2, 0, 128.5, None),
            ('pattern', BANKS, 2, 0, None, 11000),
            ('no bank', BANKS, -1, 0, None, None),            # out of range: the old path, the last pad's bank
            ('too slow', {0xB: 1, 0xA: 50}, 0, 0, None, None)):
        params.clear()
        params.update(values)
        put(g, CUR_BANK, bank)
        put(g, PADOBJ + 0x50, 1 if override else 0)
        put(g, PADOBJ + 0x90, override)
        g.uc.mem_write(EXT_SYNC, bytes([1 if clock else 0]))
        put(g, EXT_BPM, clock or 0.0, '<f')
        g.uc.mem_write(TEMPO + 0x39, bytes([1 if pattern else 0]))
        g.uc.mem_write(TEMPO + 0x101, bytes([1 if pattern else 0]))
        put(g, TEMPO + 0x7C, pattern or 0)
        setv(g, 'arp_bpm100', 12000)
        del asked[:]
        readout[label] = (screen_value(g, 10)[1], get(g, 'arp_bpm100'))
        bank_asked[label] = [a for a in asked if 0x21 <= a <= 0x2A]
    g.uc.mem_write(TEMPO + 0x39, bytes(1))
    g.uc.mem_write(TEMPO + 0x101, bytes(1))
    stock_says = g.call(TEMPO_GET, TEMPO)                      # what the stock call reports in the SG
    before = (get(g, 'arp_mode'), get(g, 'arp_rate'), get(g, 'arp_oct'), get(g, 'arp_hold'))
    turned = [run_from(g, site, {UC_ARM_REG_R0: 10, UC_ARM_REG_R4: KB}, (0x8015CC84, 0x8015C5D8))
              for site in (0x8015C7F4, 0x8015C85C)]
    after = (get(g, 'arp_mode'), get(g, 'arp_rate'), get(g, 'arp_oct'), get(g, 'arp_hold'))
    pick = Fast(V97)                                           # setting 0x7a through the stock getter
    machines.append(pick)
    put(pick, CUR_BANK, 7)
    bank_setting = pick.call(0x800E26D0, PADSEL, 0x7A, 0)
    untouched = all(bytes(g.uc.mem_read(a, n)) == bytes(Fast(V96).uc.mem_read(a, n))
                    for a, n in ((TEMPO_GET, 0x96), (PAD_TEMPO, 0x9C), (0x800E2970, 8)))
    check(readout == {'project': ('140.0', 14000), 'bank C': ('96.0', 9600), 'bank J': ('172.5', 17250),
                      'override': ('123.4', 12345), 'MIDI clock': ('128.5', 12850), 'pattern': ('110.0', 11000),
                      'no bank': ('90.0', 9000), 'too slow': ('!50', 12000)}
          and bank_asked['bank C'] == [0x23] and bank_asked['bank J'] == [0x2A] and bank_asked['no bank'] == [0x21]
          and stock_says == 9000 and bank_setting == 7 and CUR_BANK == PADSEL + 0x2BC and untouched
          and turned == [0x8015CC84] * 2 and before == after,
          'the arp follows the tempo BPM-synced samples in the bank on the pads follow, through the SP\'s own '
          'untouched code: PROJECT BPM (140.0), or that bank\'s BPM in BANK mode (bank C 96.0, bank J 172.5 - '
          'not the last selected pad\'s bank A, 90, as v9.9.1), an override (123.4), the MIDI clock (128.5), a '
          'playing pattern (110.0); never the REC BPM (90) the plain call reports in the SG; the read-only BPM '
          'item shows it, raw "!50" when unusable')

    o = Arp(env=4)
    o.m.key(24, 100, 1)                                        # played directly (ARP OFF)
    o.blocks(8)
    direct = o.voices()[0]
    o.set(mode=1)                                              # ARP on: the direct note is stopped
    o.blocks(int(0.05 * 48000 / 64))
    was_released = o.voices()[0][1] == 1 or o.voices()[0][0] == 0
    o.m.key(24, 0, 1)                                          # its note-off now goes to the arp: harmless
    o.m.key(28, 100, 2)
    o.blocks(int(2 * STEP / 64))
    o.set(mode=0)                                              # ARP off while playing: stops
    o.m.key(28, 0, 2)
    o.blocks(int(2.5 * 48000 / 64))
    quiet = all(not a_ for a_, r in o.voices())
    o.m.key(31, 100, 3)                                        # direct play again (v9.6 path)
    o.blocks(4)
    back = o.voices()[0] == (1, 0) and i32(o.m, VOICES[0] + 0x1AC) == 3
    check(direct == (1, 0) and was_released and quiet and back and get(o.m, 'held_count') == 0,
          'ARP on stops notes played directly, ARP off stops the arp; nothing is left sounding and pads play '
          'directly again')

    d = Arp()
    d.set(mode=1)
    for note in (60, 64):
        d.m.live(0x8005A680, note, 80, 0)                      # MIDI IN through the real dispatcher
    d.blocks(int(4 * STEP / 64))
    midi_notes = [n for t, i, n, v in d.starts()][:4]
    midi_vel = {v for t, i, n, v in d.starts()}
    for note in (60, 64):
        d.m.live(0x8005A680, note, 0, 0)
    t0 = d.t
    d.blocks(int(3 * STEP / 64))
    check(midi_notes == [12, 16, 12, 16] and midi_vel == {80} and not d.starts(t0 + 64),
          'MIDI IN feeds the arp (keys 60 64 -> notes 12 16, velocity kept) and its note-offs stop it')

    # 5. CPU, flash ----------------------------------------------------------------------------------------
    from unicorn import UC_HOOK_CODE as _HC  # noqa: E402,F811

    def tick_cost(m, blocks=40):
        counted = [0]
        h = m.uc.hook_add(_HC, lambda uc, a_, s, _: counted.__setitem__(0, counted[0] + 1),
                          begin=SYM['arp_tick'] & ~1, end=0x83FA3FFF)
        for _ in range(blocks):
            m.block()
        m.uc.hook_del(h)
        return counted[0] / blocks

    idle = Arp()
    off_cost = tick_cost(idle.m)
    busy = Arp()
    busy.set(mode=1)
    for src, note in ((1, 24), (2, 28), (3, 31), (4, 35)):
        busy.m.key(note, 100, src)
    busy.blocks(2)
    on_cost = tick_cost(busy.m, 400)
    check(off_cost < 20 and on_cost < 400,
          f'arp work per 64-frame block: {off_cost:.0f} instructions with ARP OFF, {on_cost:.0f} on average while '
          f'playing (the four voices cost ~50,000)')
    check(not any(m.flash for m in machines), f'zero flash fetches/reads ({len(machines)} machines)')

    print('\nALL PASS' if not failures else f'\n{len(failures)} FAILED: {failures}')
    return 1 if failures else 0


if __name__ == '__main__':
    firmware = workspace() / 'firmware'
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('candidate', type=Path, nargs='?', default=firmware / 'doom-poly-waves-v9.9.2-arp')
    ap.add_argument('parent', type=Path, nargs='?', default=firmware / 'doom-poly-waves-v9.6-fixes')
    args = ap.parse_args()
    sys.exit(main(args.candidate.resolve(), args.parent.resolve()))
