"""Focused read-only v9.5 edge-case audit; runs ARM firmware in Unicorn."""
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parents[3]
SOURCE=ROOT/'waves_v9_ram/test_v9.py'

def harness():
    text=SOURCE.read_text(encoding='utf-8')
    ns={'__file__':str(SOURCE),'__name__':'v95_edge_harness'}
    exec(compile(text.split('# 1. Layout')[0],str(SOURCE),'exec'),ns)
    helpers=text[text.index('def keyboard('):text.index('m, m3 = Fast(V9), Fast(V93)')]
    ns.update(KB=0x80591C48,PAGE_ID=0x80245880,SG=0x1e)
    exec(compile(helpers,str(SOURCE),'exec'),ns)
    return ns

def audit():
    h=harness(); m=h['Fast'](h['V9']); m.setup(6)
    h['keyboard'](m,0)
    for n in (12,127): m.call(0x8005A680,n,100,0)
    pitches=lambda:[struct.unpack('<i',bytes(m.uc.mem_read(v+0x5c,4)))[0]
                    for v in h['VOICES'] if m.uc.mem_read(v,1)[0]]
    before=pitches()
    m.call(h['SYM']['freq_event'],h['SINGLETON'],0x7c,48,0)
    after=pitches()
    print('Wide MIDI chord + FREQ:',before,'->',after,flush=True)
    assert before==[-36,79] and after==[-67,48]
    assert after[0]<-36
    print('CONFIRMED: FREQ bounds loop permits -67 (below documented -36 lower limit).')
    m.setup(6); h['keyboard'](m,1,0,2,0)
    pad=12; on=h['pad_maths'](m,pad)[0]
    m.key(on,100,pad); m.render(1)
    h['keyboard'](m,1,0,-3,0)
    off=h['pad_maths'](m,pad)[1][0]
    m.key(off,0,pad); m.render(20)
    assert off < -36 and m.voices()[0][:2]==(1,0)
    print(f'CONFIRMED: held pad {pad+1}, Major, OCT +2 -> -3; note-off {on} -> {off} ignored; voice remains held.')
    return before,after

if __name__=='__main__': audit()
