# Experimental poly-test-v4 integration

This fork combines **DOOM OS 0.6.1-alpha ED5E** with our **poly-test-v4** Sound
Generator patch. It is not an upstream DOOM OS release. The original local
build was made without hardware validation. The upstream `checksums.txt` and archived
releases still describe upstream builds only.

Update: the user reported this preceding DOOM/poly-v4 build working. The
separate [17-wave extension](WAVES.md) adds types 15–31 to it, with its own
overlay/hash/test image. The current [v9.5 RAM build](V9.md) keeps v8's 17 waves
and raw-grit sound, adds volume/filter envelopes, voice stealing, scale pads and
four-voice MIDI IN, and fixes REC silence and clicks and the stale TYPE and FREQ
displays. The owner reported v9.1 working on hardware on 2026-10-07; the v9.5
hardware confirmation is pending and CPU headroom is still unmeasured. Archived
poly-v4 and v8 sources remain available.

Current publication: [v9.9.2](ARP.md) is [v9.6](SCALES.md) (the v9.5
waves/envelopes/stealing, 34 scales, FREQ bounds and out-of-range pad note-off
fixes) plus an arpeggiator in the Sound Generator VALUE menu. The web patcher
now emits v9.9.2, with 67 emulator checks passing on the exact build; v9.9.2
hardware testing and real-time CPU headroom remain pending. The v9.6 and v9.5
builders remain for reproduction.

## What is included

- Four live Sound Generator voices. V9 steals the oldest fading voice, else
  the oldest held voice, when a fifth note is played; stolen note-offs are ignored.
  Since v9.5 the stolen voice fades out in 2.8 ms before the new note starts.
  Since v9.4, MIDI IN plays the same four voices.
- Scale pads (v9.4): with a SCALE other than Chrom, the pads play only scale
  notes, one scale step apart, on the Sound Generator page.
- Nine custom timed ENV presets, four with a resonant low-pass/filter envelope.
- Shared TYPE, LEVEL, DUTY (where the oscillator supports it), BALANCE, TUNE and
  ENV; FREQ transposes the chord while preserving intervals within bounds.
- Held-pad LED matching, including duplicate pitches, and clone-aware stop/reset.
- REC snapshots and renders the held chord using the stock export/file routines.
  This records a static chord, not a live performance.
- Fixed half gain per voice. Four loud voices can still clip; start at a modest LEVEL.

The base build keeps Saw2 and the stock oscillator list. FM2 is **not** enabled
by default. The optional CLI `--fm` build replaces Saw2 with the already tested
fixed two-operator FM2 oscillator; it is not a new oscillator slot or a ZEN-Core
port, and its DUTY control does not change the fixed modulation index.

This does not make normal sample pads or DOOM patterns into live synth pads.
DOOM OS sequencer/mixer interaction, MIDI, hardware timing and long-session
stability still need device testing.

## Our online or local browser patcher

1. Back up samples/projects/settings and keep the original Roland 5.52 update.
2. Open [our patcher](https://tracelistener.github.io/doom-os-etc./), or open
   **this fork's `index.html` locally**. The upstream patcher does not contain
   this fork's additions.
3. Supply the **unmodified stock 5.52 `SP404MKII_APP1.bin`**, not our v4 binary
   or an existing DOOM image.
4. Select “Sound Generator v9.9.2” for the combined build.
   This single option is OFF by default. It includes both polyphony and all
   17 new waves with v8's raw-grit sound, envelopes and voice stealing. Unselected, it
   produces the exact original ED5E image instead.
5. Patch and download. Stock, DOOM and combined SHA-256 checks are performed
   locally; the browser does not upload your firmware.

APP0 stays **stock and unchanged**. Keep both files from a single generated
output folder together when preparing a test update. Do not mix modified
versions or replace the originals in your rollback folder. Flashing is a
separate user action; no device was updated by these scripts.

## Reproduce from source

The public DOOM OS repository contains a released binary patch container,
not its underlying C source. This is a guarded composition of that container
and the local synth patch sources, not a recompile of DOOM OS itself.

Use Python 3.12, install the pinned test/build dependencies in a virtual
environment, and provide your own official firmware:

```sh
python -m pip install -r synth/requirements.txt
python synth/build.py /path/to/stock/SP404MKII_APP1.bin --web
# Optional separate output replacing Saw2:
python synth/build.py /path/to/stock/SP404MKII_APP1.bin --fm
```

Outputs default to ignored `out/doom-poly-v4/`; optional FM output goes to
`out/doom-poly-v4-fm/`. If a stock APP0 is beside the input, its hash is checked
and it is copied without alteration. `--web` regenerates `synth-data.js`.
It contains only the small overlay, not a complete firmware image.
Do not use `--fm --web` unless you intend to include the additional optional
overlay in the published data; the current page selects upstream DOOM or combined v9.5.
Use [the v9.9.2 source and publication steps](ARP.md) for the current overlay
(built on [v9.6](SCALES.md)); the v9.6 and v9.5 source builds remain available
for reproduction/rollback.

For tests (PowerShell; use an absolute path):

```powershell
$env:DOOM_STOCK_APP1 = 'C:\path\to\stock\SP404MKII_APP1.bin'
python -m unittest discover -s synth -p test_build.py -v
node synth/test_browser.cjs $env:DOOM_STOCK_APP1
```

There are 40 composition and ARM instruction-level tests, including the real
stock synth setter, oscillator, export loop (simulated filesystem), hook
continuations, and real startup scatter dispatch for the new copy sections.
The JS test runs the shipped applier and page handlers with a minimal DOM;
it is not a visual browser or full hardware emulator test.

## Layout and compatibility

The standalone v4 image inserts bytes inside stock APP1. Doing that to DOOM
would move its directly executed flash code and break absolute addresses.
Instead this builder:

1. Validates the exact stock, ED5E and standalone v4 hashes.
2. Guards each complete synth hook span against stock bytes; rejects conflicts.
3. Transfers v4 hooks by their **runtime** address, not their original file offset.
4. Reserves the same `0x3520`-byte prefix of the stock OS memory pool as v4
   (`0x8353a30c` to `0x8353d82c`), retaining its end and alignment.
5. Appends copy sections for ITCM `0x1fc00..0x20000` and SDRAM
   `0x8353b000..0x8353c000`, then a relocated 14-row scatter table. The copy
   sections run after zero-initialization.

All original DOOM flash offsets and all non-synth-hook bytes stay intact.
The pool is 13,600 bytes smaller; device-load compatibility is not proven by
the tests. A scan of DOOM's appended code found no literal or adjacent
MOVW/MOVT address references into the reserved SDRAM prefix. That is useful
negative evidence, not proof that every computed access is safe.

The vendored v2/v3/v4 sources and v3/v4 regression harnesses are copied from
our existing SP firmware workspace without modifying the originals. Their
standalone default paths assume that original layout; use `synth/build.py`
and `synth/test_build.py` here instead. The optional FM source is copied from
our existing FM test. No Roland binaries, plugin licenses, samples, project
backups or third-party CLI research logs are checked into this fork.

## First device test

With a verified backup and rollback update available, test boot and basic
sample playback first. Then test Sound Generator with one voice, four held
notes, LEDs, DUTY/LEVEL/TUNE/FREQ, repeated presses/releases and all-stop.
Hold a chord while entering REC to capture it, then follow the normal
destination workflow and audition the resulting sample. Finally test the
DOOM sequencer/mixer independently and while previewing the synth, using
moderate levels. Report any hang, stuck note, LED mismatch, clipping or
export failure. Do not assume a passing isolated synth test proves DOOM
sequencer compatibility.
