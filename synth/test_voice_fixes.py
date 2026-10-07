"""Regression coverage for both v9.5 bugs, against real candidate ARM code.

Run from sp404mk2: python repo/doom-os-etc/synth/test_voice_fixes.py <candidate-folder>
No device I/O or firmware writes. Parent failure repros remain in audit_v95_edges.py.
"""
import argparse
import itertools
import json
from pathlib import Path
import random
import struct
from audit_v95_edges import harness

def main(folder):
    h=harness(); man=json.loads((folder/'manifest.json').read_text())
    assert man['freq_bounds_fix'] and man['pad_noteoff_range_fix']
    m=h['Fast'](str(folder/'SP404MKII_APP1.bin')); checks=[]
    def check(ok,label):
        print(('PASS ' if ok else 'FAIL ')+label,flush=True)
        checks.append(bool(ok)); assert ok,label
    def midi(n,v=100): m.call(0x8005a680,n,v,0)
    def knob(value): m.call(h['SYM']['freq_event'],h['SINGLETON'],0x7c,value,0)
    def field(v,off): return struct.unpack('<i',bytes(m.uc.mem_read(v+off,4)))[0]
    def pitches(): return [field(v,0x5c) for v in h['VOICES'] if m.uc.mem_read(v,1)[0]]
    m.setup(6); h['keyboard'](m,0)
    midi(12); midi(127); before=pitches(); knob(48)
    check(before==[-36,79] and pitches()==before,'MIDI 12+127 + FREQ: over-wide chord unchanged, not [-67,48]')
    midi(12,0); midi(127,0); m.render(20)
    check(not any(v[0] for v in m.voices()),'over-wide MIDI chord still releases normally')
    # Every slot ordering: widest chord is a no-op; fitting chord shares exactly
    # one delta from the intersection, without clamping individual notes.
    cases=0
    for notes in itertools.permutations((12,48,96,127)):
        for value in (-36,0,48):
            m.setup(6)
            for n in notes: midi(n)
            before=pitches(); knob(value); assert pitches()==before,(notes,value,pitches())
            cases+=1
    rng=random.Random(9602)
    for _ in range(500):
        notes=rng.sample(range(12,128),rng.randrange(1,5)); value=rng.randrange(-36,49)
        m.setup(6)
        for n in notes: midi(n)
        before=pitches(); low=-36-min(before); high=48-max(before)
        delta=0 if low>high else min(high,max(low,value-before[-1]))
        knob(value); assert pitches()==[p+delta for p in before],(notes,value,before,pitches(),delta)
        if low<=high: assert all(-36<=p<=48 for p in pitches())
        cases+=1
    check(True,f'{cases} FREQ cases: slot-order independent, exact interval preservation, fitting chords inside bounds')
    for value in (-100,-36,0,48,100):
        m.setup(6); knob(value)
        assert field(h['SINGLETON'],0x5c)==min(48,max(-36,value))
    check(True,'idle FREQ obeys limits, including invalid direct caller values')
    # Reproduce the real keyboard pitch change that failed in v9.5.
    m.setup(6); h['keyboard'](m,1,0,2,0); pad=12
    on=h['pad_maths'](m,pad)[0]; m.key(on,100,pad); m.render(1)
    h['keyboard'](m,1,0,-3,0); off=h['pad_maths'](m,pad)[1][0]
    m.key(off,0,pad)
    check((on,off)==(17,-43) and m.voices()[0][:2]==(1,1),'Major pad 13, OCT +2 -> -3: out-of-range release now starts')
    m.render(20)
    check(not m.voices()[0][0],'formerly stuck pad reaches silence and frees its voice')
    # Both out-of-range directions, every physical source and every voice slot;
    # held neighbors and MIDI owners must not be touched by the fallback.
    cases=0
    for source in range(16):
        for slot in range(4):
            for off in (-120,-37,49,144):
                m.setup(6,env=3)
                order=[s for s in range(16) if s!=source][:3]; order.insert(slot,source)
                for i,s in enumerate(order): m.key(12+i*4,100,s)
                m.render(1); m.key(off,0,source)
                assert [v[1] for v in m.voices()]==[int(i==slot) for i in range(4)],(source,slot,off,m.voices())
                cases+=1
    check(True,f'{cases} out-of-range note-offs: both directions, every pad and every voice; only matching owner releases')
    m.setup(6); m.key(24,100,12); midi(60); midi(72); m.render(1); m.key(-120,0,12)
    check([v[1] for v in m.voices()[:3]]==[1,0,0],'OCT/scale-shifted pad release does not release MIDI voices')
    midi(60,0); midi(72,0); m.render(20)
    check(not any(v[0] for v in m.voices()),'mixed pad/MIDI owners all free after their own note-offs')
    m.setup(6)
    for n in (60,64,67,72): midi(n)
    knob(40)
    for n in (60,64,67,72): midi(n,0)
    m.render(20)
    check(not any(v[0] for v in m.voices()),'MIDI key ownership survives FREQ transpose of a fitting four-note chord')
    # Steal then change keyboard range: old physical pad release must not kill
    # the replacement. Other pads must remain independently releasable.
    m.setup(6)
    for s,n in ((12,24),(13,28),(14,31),(15,35)): m.key(n,100,s); m.render(1)
    m.key_live(38,100,8); before=m.voices(); m.key(-120,0,12)
    check(m.voices()==before,'out-of-range note-off from stolen pad cannot release its replacement')
    for s in (13,14,15,8): m.key(144,0,s)
    m.render(20)
    check(not any(v[0] for v in m.voices()),'remaining pads release after steal even when recalculated pitches exceed limits')
    # FREQ retune affects current pitch, but release still uses stored pad note.
    m.setup(6); m.key(24,100,12); m.render(1); knob(48); m.key(-120,0,12); m.render(20)
    check(not m.voices()[0][0],'pad note-off works after FREQ transpose as well as out-of-range OCT change')
    check(not m.flash,'both fixed control paths execute/read RAM only')
    print(f'ALL {len(checks)} VOICE-FIX CHECKS PASS (emulation, not hardware).')

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('folder',type=Path)
    main(ap.parse_args().folder)
