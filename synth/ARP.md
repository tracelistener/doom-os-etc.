# Sound Generator v9.9.2: arpeggiator — 2026-10-07

Current web patcher release: **v9.9.2-arp** = [v9.6-fixes](SCALES.md) plus an
arpeggiator after the MC-101's, in the Sound Generator VALUE menu. APP1:
2,984,592 bytes; SHA-256
`aab582ff45906a4497e59bebabeb4b3dee034f3e94a9dc287aa810ecc809608f`. APP0 is
unchanged stock. The exact v9.9.2 build is not yet hardware-tested; see
[hardware status](#hardware-status). CPU headroom remains unmeasured.

## Using it

Five items follow SCALE NOTE OCT OFST ENV TUNE in the VALUE menu. Use them
like the others: move to the item, enter edit, turn.

| Item | Values |
|---|---|
| ARP | OFF, UP, DOWN, UP&DN, RAND (no immediate repeats), ORDER (the order you pressed the keys), CHORD (all held notes every step) |
| RATE | 1/4, 1/4T, 1/8, 1/8T, 1/16 (default), 1/16T, 1/32 of a beat |
| A.OCT | -3..+3: also play the held notes that many octaves below/above (CHORD moves an octave per step) |
| HOLD | OFF, ON: latch. The arp keeps going after you let go; the next key after letting go of everything starts a new chord. HOLD OFF with nothing held stops it. |
| BPM | Read-only: the tempo the arp plays at, e.g. `140.0`; `!` + the raw value when it is not a usable tempo (20–300 BPM) |

- With ARP on, pads and MIDI IN no longer start voices themselves: they add and
  remove notes from the arp's list (up to 16, keeping each key's velocity).
- Each step starts on a free voice (or takes the oldest one with v9.5's 2.8 ms
  quick fade) and is released half a step later, so the ENV preset shapes every
  step and release tails overlap across the four voices. When all four voices
  are still ringing, the oldest tail is faded half a step early so the next step
  lands on time.
- Turning ARP on stops notes you were playing directly; turning it off stops the
  arp. ARP OFF behaves exactly like v9.6.
- Leaving the Sound Generator page stops the arp and forgets its notes; nothing
  plays when you come back until you press a key.
- Settings are not saved: ARP OFF, 1/16, 0, HOLD OFF at power-on.

## Tempo

The arp follows the tempo that BPM-synced samples in the current bank follow,
read with the SP's own code (nothing in it is patched):

- the PROJECT BPM when the tempo is set to PROJECT;
- the bank's own BPM when it is set to BANK, for the bank on the pads (the bank
  the tempo screen's BANK buttons pick and TAP TEMPO sets);
- an active tempo override;
- the MIDI clock's tempo when synced to external clock;
- a playing pattern's tempo.

It reads the tempo when you press a key, change an arp setting, or the Sound
Generator screen updates. The arp runs free from the first key: it is not
locked to the pattern sequencer's beat grid.

Technically: v9.9.2 reads setting 0x7a of the pad-select state at `0x82dffc88`
(the bank; the stock getter's case is a plain load of `+0x2bc`) and calls the
stock function at `0x80047dd0` with the bank's first pad. The sample voices
call that function with their own pad for BPM sync. v9.9.1 instead used the
stock current-tempo function on a stand-in object, which takes the bank from
the last selected pad: with the tempo set to BANK it read 90 when that pad sat
in another bank. That path remains only as a fallback for an out-of-range bank
number. Called plainly, that function returns the REC BPM in the Sound
Generator (v9.9's fixed 90).

## Hardware status

| Build | Owner's hardware report (2026-10-07) |
|---|---|
| v9.7 | Menu labels fit, ARP on stops held notes, step timing right. The tempo did not follow PROJECT/BANK, and notes resumed when re-entering the Sound Generator after leaving it (fixed in v9.8). |
| v9.8 | Arp still not following the tempo. With the tempo at PROJECT 140, BPM-synced samples read 70. v9.8 hooked the SP's current-tempo function; v9.9 removed that hook. Whether the 70 persists has not been re-checked. |
| v9.9 | BPM item read a fixed 90: the REC BPM (fixed in v9.9.1). |
| v9.9.1 | BPM item matched TAP TEMPO with the tempo set to PROJECT; read 90 with BANK (fixed in v9.9.2). |
| v9.9.2 | Not yet tested. |

## How it is built

`build_arp.py` compiles `arp.c` with Zig 0.13.0 into one new boot copy row:
12,740 bytes at `0x83fa2000`, after v9.6's scale module and below
`0x83ff0000` (bridges first, then the C module). Existing code changes only at
nine sites, each checked against its exact v9.6 instructions (83 bytes,
including the scatter-table header):

| Address | Change |
|---|---|
| `0x800c19f4` | VALUE-menu cursor (setting 0x83) range 0..5 -> 0..10 |
| `0x800bbc98` | item names: 6..10 = ARP RATE A.OCT HOLD BPM |
| `0x80123be6` | SG screen value text for items 6..10 |
| `0x80123ce0` | SG screen update: read the tempo, redraw after an arp change |
| `0x8015c7f4` / `0x8015c85c` | VALUE -/+ on items 6..10 (BPM is read-only) |
| `0x0001fd84` | render loop, every 64 frames: the arp clock |
| `0x8353b000` | pad notes -> `arp_note_event` (v9.5's `note_event` when ARP is OFF) |
| `0x8005a698` | MIDI IN -> `arp_midi_event` (v9.5's `midi_event` when ARP is OFF) |

The bridges re-create the stock dispatch they displace for items 0..5. Voices
start through the stock note start (`0x800d0668`) after the same voice-0 clone
copy as v9.5's stealing.

### Public fork reproduction and browser publication

Use the fork's pinned Python dependencies and Zig 0.13.0, and supply your own
unmodified official 5.52 firmware pair. No full Roland firmware is distributed.

```sh
python -m pip install -r synth/requirements.txt
python synth/build_v9.py /path/to/stock/SP404MKII_APP1.bin --zig /path/to/zig --out out/v9.5-parent
python synth/build_scales.py out/v9.5-parent/SP404MKII_APP1.bin --zig /path/to/zig --out out/v9.6 --fixes
python synth/build_arp.py out/v9.6/SP404MKII_APP1.bin --zig /path/to/zig --out out/v9.9.2
python synth/publish_v9_9_2.py /path/to/stock/SP404MKII_APP1.bin --candidate out/v9.9.2/SP404MKII_APP1.bin
```

The last command verifies exact source/module/output hashes and generates
`waves-data.js` plus `synth/v9_9_2_manifest.json`. It does not push to GitHub
or flash a device. The overlay applies to the same frozen browser base as v9.5
and v9.6. For publication guards, set `DOOM_STOCK_APP1` and `DOOM_V992_APP1`
to absolute paths, then run
`python -m unittest discover -s synth -p test_v9_9_2.py -v` and
`node synth/test_browser.cjs /path/to/stock/SP404MKII_APP1.bin`.

## Tests

67 Unicorn checks pass on the exact v9.9.2 image, run from the sp404mk2
workspace that holds the emulator harness (`waves_v9_ram/test_v9.py`):

```powershell
$env:PYTHONPATH = "$PWD\.poly_vendor"
python repo/doom-os-etc/synth/test_arp.py firmware/doom-poly-waves-v9.9.2-arp firmware/doom-poly-waves-v9.6-fixes
python repo/doom-os-etc/synth/test_arp_regression.py firmware/doom-poly-waves-v9.9.2-arp
python repo/doom-os-etc/synth/test_voice_fixes.py firmware/doom-poly-waves-v9.9.2-arp
```

- `test_arp.py` (21): layout (only the nine sites change, one new copy row);
  ARP OFF = v9.6 with bit-identical audio and voice state (chords, TYPE sweep, a
  steal with audio running, MIDI chord); the VALUE menu through the real stock
  code; step and gate timing at 120 BPM without drift; every motif, A.OCT,
  HOLD; tempo and RATE changes; busy voices; page changes; ARP on/off with
  nothing left sounding; MIDI IN; the tempo source (PROJECT, the bank on the
  pads rather than the last selected pad's bank, override, MIDI clock,
  pattern); CPU per block (19 instructions with ARP OFF, ~85 while playing,
  against ~50,000 for the four voices); zero flash access.
- `test_arp_regression.py` (32): the unchanged v9.5 suite, adapted only for the
  scale and arp copy rows and hooks, including the real boot loader.
- `test_voice_fixes.py` (14): v9.6's FREQ bounds and pad note-off fixes.

v9.9.2's tempo check fails on v9.9.1, which read the last selected pad's bank.
Rebuilding from the fork's sources produces the identical APP1. These are
emulator checks of real firmware code, without the RTOS, screen or real
timing; they are not a hardware test.
