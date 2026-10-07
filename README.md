# Tracelistener's SP-404MKII Sound Generator mod

**[Open the synth patcher](https://tracelistener.github.io/doom-os-etc./)**

Sound Generator **v9.5** adds four live voices, 17 new waveforms, nine
volume/filter-envelope presets and scale pads to the SP-404MKII. Firmware processing happens
locally in your browser; nothing is uploaded. Select the single Sound Generator
option to include the mod. It is OFF by default; unchecked generates the base
build without these synth additions.

## What this mod adds

- Four live Sound Generator voices with shared controls and held-pad LEDs.
- 17 added waves: FM1:1, FM1:2, FM1:7, CZSaw, CZSqr, CZRes, Sync, Fold,
  Drive, LPSaw, LPSqr, Organ, Vowel, Table, Logic, Metal and Grit.
- Nine timed ENV presets; four also have a resonant low-pass/filter envelope.
- Scale pads: with a SCALE set, the 16 pads play only scale notes, so one
  bank spans two to three octaves.
- MIDI IN on all four voices. When all four are busy, a new note takes one
  after a 2.8 ms fade-out, reducing chord-swap clicks in emulator tests.
- Held-chord REC with fades at the end and when REC starts, voice
  stealing and fixes for release, OCT note-off, FREQ/ENV/START-END, the FREQ
  readout and stale TYPE selection/display.
- Loudness/DC matching and quieter Pulse/Noise, while retaining raw DUTY
  jitter and Sync/CZRes aliasing grit. Stock waves, including Saw2, remain.

This does not turn normal sample pads or patterns into live synth pads.
No ZEN-Core/MC-101 engine is included.

## Sound Generator envelopes (v9.5)

In Sound Generator, select **ENV** and choose a preset. Play a new note after
changing ENV: each voice takes its preset when the note starts. These replace
the nine original non-OFF ENV shapes and work with stock and added waveforms,
four-voice playback and held-chord REC.

| ENV | What it does |
|---|---|
| OFF | Stock behavior; no added volume or filter envelope. |
| Pluck | Fast attack, short decay to silence. |
| Perc | Fast attack, longer decay to silence. |
| Keys | Fast attack, decay to a quieter held level, release tail. |
| Pad | Soft attack, full held level, long release. |
| Swell | Slower fade-in and longer release than Pad. |
| Bass | Fast attack with a closing resonant low-pass filter and short release. |
| Acid | Fast attack, stronger resonance/filter drive and very short release. |
| Sweep | Quick volume attack with a slowly opening resonant low-pass filter. |
| FPad | Soft volume attack, slow filter movement and long release. |

Bass, Acid, Sweep and FPad have **both volume and filter envelopes**; the
other five non-OFF presets shape volume only. The filter tracks note pitch.
Release tails continue after note-off, while all-stop still uses the short
stock fade. ENV is a preset selector, not separate ADSR/filter parameter knobs.
REC renders the held chord with its selected envelope, not a live performance.
See [the complete v9.5 notes](synth/V9.md).

## Scale pads (v9.4)

Set **SCALE** to anything other than Chrom and the 16 pads play only notes of
that scale, one scale step per pad, from the bottom row (lowest) to the top
row (highest), left to right. Pad 9 plays the first scale note at or above its
usual note. One bank then spans two octaves and a step with a 7-note scale, or
three octaves with a pentatonic one. The pad lights keep their meaning (root,
below and above the root). Chrom, NOTE, OCT and the other screens work as
before. MIDI IN now plays all four voices, and a held MIDI note lights the pad
with the same pitch.

## How to install

Use this fork's web patcher, which includes the new synth options:

[Open the patcher](https://tracelistener.github.io/doom-os-etc./)

You can also download this repository as a ZIP file (under the Code button), unzip it and open index.html in your browser. Just follow the instructions on the page.

### Manual installation

1. Download the 5.52 system update from [Roland's support page](https://www.roland.com/global/support/by_product/sp-404mk2/updates_drivers/) and unzip it.
2. Open [our patcher](https://tracelistener.github.io/doom-os-etc./) and load the stock `SP404MKII_APP1.bin`. Select Sound Generator for v9.5: four voices, 17 new waves, envelopes and scale pads. The patcher only accepts official 5.52 and checks the result against [the fork's checksums](synth/checksums.txt).
3. Download the patched `SP404MKII_APP1.bin` and use it with the unchanged official 5.52 `SP404MKII_APP0.bin` for your test update. Back up first and keep your known-good update for rollback.
4. Insert the card, hold **SHIFT** while you turn the SP on, and press the **VALUE** knob when the update menu appears.
5. When the update is done, turn the SP off and on again.

To go back to the original firmware, just run the updater with the original Roland firmware.

## Status

**v9.5** (2026-10-07) passes all 32 emulator checks and its source rebuilds
byte-for-byte to the published image; hardware confirmation is pending.
It addresses a click the v9.4 hardware test found when swapping a note
of a full chord. The previous build, v9.1, was reported working on hardware on
2026-10-07 (test coverage unspecified). CPU headroom remains unmeasured. All
135 publication/regression checks and the browser patcher test pass. This
remains experimental; four loud voices can clip.

See [the v9.5 build and test notes](synth/V9.md),
[the wave list](synth/WAVES.md) and [integration notes](synth/README.md).
Report synth bugs with the build, waveform, ENV preset, number of held notes
and steps to reproduce. Preserve your backups and known-good firmware pair.

## Disclaimer

Unofficial; not affiliated with Roland or Klangfeld Labs. Firmware modification
is at your own risk. Synth additions are by tracelistener; the underlying
DOOM OS 0.6.1 ED5E base is by Quintus Oostendorp / Klangfeld Labs under the
MIT License ([LICENSE](LICENSE)), retained unchanged.
