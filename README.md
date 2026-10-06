# Tracelistener's SP-404MKII synth patcher (DOOM OS fork)

<p align="center">
  <img src="assets/doom-os-512.png" alt="DOOM OS">
</p>

DOOM OS is a heavily modified version of the original 5.52 firmware. It adds a graphical pattern
sequencer, a mixer, effect automation, custom shortcuts and more, and improves parts of the original
firmware. You patch it yourself, in your browser, using the official firmware file from Roland.

Upstream base: 0.6.1-alpha, build ED5E (2026-10-04)

This fork adds **experimental four-voice Sound Generator polyphony from our
poly-test-v4 firmware**, plus **17 Wave Lab waveforms as types 15–31**.
Use [our online patcher](https://tracelistener.github.io/doom-os-etc./), or
open this fork's `index.html` locally to select polyphony and/or the waves. The combined
build has passed instruction-level and composition tests, **not hardware
validation for the new waves**. The user reported the preceding DOOM/poly-v4
build working. See [the integration notes](synth/README.md) and
[new-wave build steps and test checklist](synth/WAVES.md).

**[Open our web patcher: DOOM OS + polyphony + 17 waves](https://tracelistener.github.io/doom-os-etc./)**

## Features

* Pattern sequencer with a graphical interface: see and edit your pattern as a grid while it plays
* Piano roll: play melodies in on the pads
* Live recording with a rehearse mode, quantise, swing, fixed velocity and note length
* Eight pattern tools: quantise, swing, shift, double, halve, reverse, humanise, transpose
* Track and pattern settings: mute, solo, volume, swap pad, paste, clear, tempo, pattern length
* Mixer with a live level meter for every track
* Effect automation: record your effect knob moves and edit them step by step
* Zoomed view of four tracks
* Undo and redo, 12 steps
* Help mode: press any control to see what it does
* Custom shortcuts (key combos)
* A DOOM OS tab in the system settings
* Six boot animations

## Improvements

* Safer saving: a pattern is written to a new file first, and the old file is only removed once the new one is complete
* Slightly faster boot time
* Everything from the original firmware still works: DOOM OS doesn't remove or replace anything

## How to install

Use this fork's web patcher, which includes the new synth options:

https://tracelistener.github.io/doom-os-etc./

You can also download this repository as a ZIP file (under the Code button), unzip it and open index.html in your browser. Just follow the instructions on the page.

### Manual installation

1. Download the 5.52 system update from [Roland's support page](https://www.roland.com/global/support/by_product/sp-404mk2/updates_drivers/) and unzip it.
2. Open [our patcher](https://tracelistener.github.io/doom-os-etc./) and load the stock `SP404MKII_APP1.bin`. Leave both synth boxes checked for all 17 new waves. Everything happens in your browser, nothing is uploaded. The patcher only accepts official 5.52 and checks the result against [the fork's checksums](synth/checksums.txt).
3. Download the patched `SP404MKII_APP1.bin` and use it with the unchanged official 5.52 `SP404MKII_APP0.bin` for your test update. Back up first and keep your known-good update for rollback.
4. Insert the card, hold **SHIFT** while you turn the SP on, and press the **VALUE** knob when the update menu appears.
5. When the update is done, turn the SP off and on again.

To go back to the original firmware, just run the updater with the original Roland firmware.

## Main controls

| Control | What it does |
|---|---|
| **REMAIN** + **SUB PAD** | Open DOOM OS (works from any screen) |
| **EXIT** | Close DOOM OS and save your changes |
| Hold **SHIFT** for 3 seconds | Help mode: press any control to see what it does |
| **PATTERN SELECT** | Start / stop |
| Pads 1-16 | Turn steps on or off in the selected row |
| Turn **VALUE** | Select a row |
| Hold **SUB PAD** + tap pads | Choose the sample for a new row |
| Hold a step + **CTRL 1** / **2** / **3** | Velocity / length / timing of that step |
| Hold a step + **SHIFT** + **CTRL 1** | Pitch of that step |
| Hold a step + press **VALUE** | Open the piano roll |
| **HOLD** | Play the pads instead of editing steps |
| **REC** | Press once to rehearse, again to record |
| **PATTERN EDIT** | Pattern tools |
| **RECORD SETTING** | Track panel, pattern panel and mixer |
| **SHIFT** + **PATTERN SELECT** | Undo |
| **REMAIN** + **PATTERN SELECT** | Redo |

The upstream DOOM OS guide is in the [original manual](https://klangfeldlabs.com/doom-os/manual/).
Our added waves and testing instructions are documented in [WAVES.md](synth/WAVES.md).

## Coming next

Song mode, a sample editor, an effects editor, a guided tutorial, and an SDK so you can build your own screens.

## Status

This is an alpha. It has been tested on my own SP-404MK2, but expect some rough edges.
Found a bug? Open an issue and include the version shown on the VERSION tab in *Utility → System*.
Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Disclaimer

This is an unofficial project, not affiliated with or endorsed by Roland. Modifying your firmware is at your own risk and may void your warranty. DOOM OS is named in tribute to MF DOOM. Made by Klangfeld Labs, MIT licensed ([LICENSE](LICENSE)).
