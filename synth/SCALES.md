# v9.6 candidates and v9.5 audit — 2026-10-07

The web patcher now ships [v9.9.2](ARP.md): v9.6-fixes plus an arpeggiator,
built on the exact v9.6-fixes image below. v9.6-fixes hardware testing is still
pending.
No flashing or unrelated AUTO TRIG changes. Original firmware and frozen v9.5
sources are preserved. The scale-only candidate leaves its module byte-exact;
the newer fixes candidate changes only the two declared control-path hook spans.

## v9.6-fixes release (parent of v9.9.2)

Both bugs below are now fixed, together with all 34 scales:

- FREQ computes the intersection of all held voices' allowable transpose
  intervals first, then applies one delta to the chord. If a MIDI chord is
  wider than 84 semitones, no interval-preserving fit exists: FREQ leaves it
  unchanged. Its original MIDI pitches and key-release ownership are retained.
  Ordinary fitting chords transpose up to their shared bounds; fading voices
  remain untouched. Idle FREQ values are clamped to -36..48.
- After existing stolen-note and source-ownership checks, the pad note-off
  fallback now passes the held voice's original pitch (+0x19c) to the stock
  release routine. It no longer passes an out-of-range recalculated pitch.
  This covers both extreme OCT directions and ROOT/SCALE changes.

All **59 emulator checks pass**: the 32 original behavioral checks, 13 scale
checks and 14 voice-fix checks. Additional coverage includes 572 FREQ cases,
256 out-of-range releases covering every pad and voice slot, MIDI ownership
after transpose, stolen-pad note-offs, and FREQ-transposed pad releases. The
original failure reproducer still fails as expected on unchanged v9.5.
Rebuilding in a second fresh folder produced byte-identical APP1.

Use the pair in `firmware/doom-poly-waves-v9.6-fixes/`, not the preceding
`v9.6-scales/` pair. APP1: 2,971,568 bytes; SHA-256:
`1b2d50e0cfe20b11bad9cdef6945ec24a14a60e772e56a02dd5478331e275cc9`.
APP0 is unchanged stock (hash below). Hardware testing remains pending.

The extra module is 2,028 bytes at 0x83fa1000. Only 59 existing-image bytes
change from v9.5, within declared scale/header/fix spans. FREQ entry at
0x83f810da jumps to `fixed_freq_event`; the guarded four-byte branch at
0x83f80fd8 loads the stored pitch, reproduces displaced call setup and rejoins
at 0x83f80fe2. Oscillators, envelopes, REC and steal/fade routines are unchanged.

```powershell
$env:PYTHONPATH = "$PWD\.poly_vendor"
python repo/doom-os-etc/synth/build_scales.py firmware/doom-poly-waves-v9.5-ram/SP404MKII_APP1.bin --zig .wave_toolchain/ziglang/zig.exe --out firmware/doom-poly-waves-v9.6-fixes-new --fixes
python repo/doom-os-etc/synth/test_voice_fixes.py firmware/doom-poly-waves-v9.6-fixes-new
python repo/doom-os-etc/synth/test_scales.py firmware/doom-poly-waves-v9.6-fixes-new
python repo/doom-os-etc/synth/test_scales_regression.py firmware/doom-poly-waves-v9.6-fixes-new
```

The earlier scale-only candidate and audit are retained below for provenance.

### Public fork reproduction and browser publication

Use the fork's pinned Python dependencies and Zig 0.13.0, and supply your own
unmodified official 5.52 firmware pair. No full Roland firmware is distributed.

```sh
python -m pip install -r synth/requirements.txt
python synth/build_v9.py /path/to/stock/SP404MKII_APP1.bin --zig /path/to/zig --out out/v9.5-parent
python synth/build_scales.py out/v9.5-parent/SP404MKII_APP1.bin --zig /path/to/zig --out out/v9.6 --fixes
python synth/publish_v9_6.py /path/to/stock/SP404MKII_APP1.bin --candidate out/v9.6/SP404MKII_APP1.bin
```

The last command verifies exact source/module/output hashes and generates
`waves-data.js` plus `synth/v9_6_manifest.json`. It does not push to GitHub or
flash a device. The overlay applies to the same frozen browser base as v9.5.
For publication guards, set `DOOM_STOCK_APP1` and `DOOM_V96_APP1` to absolute
paths, then run `python -m unittest discover -s synth -p test_v9_6.py -v`
and `node synth/test_browser.cjs /path/to/stock/SP404MKII_APP1.bin`.

## New Sound Generator scales

34 choices total: the original Chrom, Major, Minor, Dorian, Phrygian and two
pentatonics keep their IDs. 27 new choices follow them in the SCALE menu:

Lydian, Mixolydian, Locrian, Minor Blues, Bebop Minor, Harmonic Minor,
Melodic Minor, Major Blues, Bebop Major, Altered, Whole Tone,
Diminished Whole-Half, Diminished Half-Whole, Gypsy Minor, Romanian Minor,
Spanish 8 Notes, Bhairav, Marva, Purvi, Todi, Arabic, Egyptian, Chinese,
Pelog, Hirajoshi, Miyakobushi and Ryukyu.

Degrees were visually checked against the supplied MC-101 update PDF, page 9,
and independently transcribed into a test oracle. For example, Roland's
Bebop Minor row is C D Eb E F G A Bb. This implementation follows that row,
not a differently named eight-note scale from another source. Only scale
degrees are borrowed; this does not port the MC-101 synth or its fingerboard
layouts. [scales.json](scales.json) contains full names and degrees; eight-character
screen labels are used for new choices.

Use the existing Sound Generator SCALE control to select them. ROOT and OCT
work as before. Pads walk consecutive scale notes, anchored at pad 9 using
v9.5's behavior. Sparse scales span more octaves; pitches outside the existing
pad-note range (-36..48 in internal pitch units) do not start a voice.
Outside the Sound Generator page, added scale IDs act as Chrom and the menu
range remains 0..6. No new save/load persistence has been established.

## Confirmed v9.5 bugs — not fixed by this scale-only candidate

1. **Wide MIDI chord + FREQ violates its own lower bound.** Send MIDI notes
   12 and 127, then set FREQ to 48. Voice pitches change from [-36,79] to
   [-67,48]. The sequential bounds loop in `freq_event` cannot fit that
   115-semitone interval into its 84-semitone target range: a later voice's
   constraint undoes an earlier one's. The stock setter accepts -67.
   Workaround: avoid transposing such wide MIDI chords with FREQ.
   A fix needs a defined policy for chords already wider than the target range;
   simply adding another per-voice clamp would change intervals.
2. **Out-of-range pad note-off can leave a held note stuck.** On Major, OCT +2,
   hold physical pad 13, move OCT to -3, then release it. Computed pitch changes
   17 -> -43. `note_event` finds the same physical source, but passes the new
   out-of-range note into the stock note function, which ignores it. The voice
   stays held. This was reproduced in published v9.5 without the added scales.
   Workaround: release pads before large OCT/scale/root changes, or use all-stop
   if a note sticks. A targeted future fix should release by the voice's stored
   original pitch, with regression coverage for stolen notes and MIDI ownership.

These are emulator-confirmed edge cases, not confirmed causes of earlier
hardware crashes. AUTO TRIG opening Looper remains unresolved and is not
changed here. Raw DUTY grit and four-loud-voice clipping are existing documented
behaviors, not new scale failures.

## Validation

- Published v9.5: all 32 existing emulator checks pass.
- Candidate: all 32 behavioral checks pass again; only test layout expectations
  are adapted for the separate scale copy row. No behavioral checks are skipped.
- 13 additional scale checks pass, including 26,112 independent pad-pitch cases,
  native note-on/off spans, LED helpers, full native label formatting and SCALE
  +/- controls, bounds, unchanged legacy behavior, off-page fallback, invalid
  pads, note ownership after in-range changes, and the real boot copy routine.
- The scale module adds 1,452 bytes at 0x83fa1000, beyond the original module's
  end (0x83fa09c0), below 0x83ff0000. Existing copy rows and OS pool are unchanged.
  Only eight declared scale hook spans and the scatter-table header change in
  existing bytes. There is no new oscillator/audio-loop work.

Not tested: hardware, screen clipping, full UI/RTOS/peripherals, real-time CPU
headroom, persistence, long-session stability. Passing these checks does not
establish full-device equivalence or certify a safe flash.

## Reproduce in the existing sp404mk2 workspace

Use Python 3.12, existing `.poly_vendor` dependencies and Zig 0.13.0. Builders
refuse to overwrite firmware outputs. These tests reuse the existing workspace's
`waves_v9_ram/test_v9.py` and `analysis` harnesses; they are not standalone fork tests.

```powershell
$env:PYTHONPATH = "$PWD\.poly_vendor"
python repo/doom-os-etc/synth/audit_v95_edges.py
python repo/doom-os-etc/synth/build_scales.py firmware/doom-poly-waves-v9.5-ram/SP404MKII_APP1.bin --zig .wave_toolchain/ziglang/zig.exe --out firmware/doom-poly-waves-v9.6-scales-new
python repo/doom-os-etc/synth/test_scales.py firmware/doom-poly-waves-v9.6-scales-new
python repo/doom-os-etc/synth/test_scales_regression.py firmware/doom-poly-waves-v9.6-scales-new
```

Current local candidate: `firmware/doom-poly-waves-v9.6-scales/`.
APP1 is 2,970,992 bytes; SHA-256:
`6723e29218bdf141c11703079fb261457fb111eee908a79a2b86a5fcdb4b74f5`.
APP0 remains the stock companion:
`3e35ff30137f741a715566e205384f9b434ca7ac3847e247bad3f0c9c8a2bd23`.
Keep the pair together and retain the original v9.5/stock rollback pairs.
