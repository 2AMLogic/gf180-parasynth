# Selected Mono FPGA checkpoint

`m5a-saw` and `m5a-pulse` carry the measured M5A patch, current envelope
encoding, and explicit `OSC2X=1 FILTER2X=1` requirement. The saw uses 20 kHz
cutoff and −0.45428 dB correction. Pulse uses control code 7 (`pulse29`),
which emits 47.90% duty in this configuration. Oscillator 2× still applies
only to saw; the separate pulse-oversampling experiment is not selected here.

The host previously carried only the first five waveform codes and could
not load `pulse29`. The named-preset test exposed that `KeyError` before the
host was changed to use the voice's full waveform-code table. The test now
asserts code 7 in all three emitted waveform writes.

The [recorded host run](reports/selected/preset-play.json) exercises MIDI
72/84/96 for both presets. Both produce 3.73 seconds of model audio with zero
samples at the output rail. The preset, host, and build-result checks passed
46 tests. Saved register images and audio are in `fpga/reports/selected/`.

Render a named preset through the host's SPI schedule and selected integer
chip model, including three seconds for the final release:

```sh
python3 fpga/play.py --preset m5a-saw --keys 72,84,96 --pattern none --bars 1 --out build/m5a-saw-play.wav
python3 fpga/play.py --preset m5a-pulse --keys 72,84,96 --pattern none --bars 1 --out build/m5a-pulse-play.wav
python3 fpga/selected_preset.py m5a-pulse --out build/m5a-pulse-registers.json
```

These commands render a model. They do not establish physical-board audio.
M5A's stored complete SPI→I²S simulation is the reused core correctness
evidence; all core source files and ROMs are checked byte-for-byte against
that simulation's commit before a build. The FPGA wrapper is outside that
simulation's scope.

Build the ULX3S 85F candidate (the selected default target):

```sh
make -C fpga candidate
# Or select a locally built router explicitly:
python3 fpga/build_selected.py --nextpnr /path/to/nextpnr-ecp5
```

The Python runner records each tool's own exit status and fresh artifact
hash in `build/fpga-selected/report.json`. Missing tools refuse. Nonzero
tool exits, stale output, and missing artifacts cannot report success.
Synthesis-only output is explicitly `SYNTHESIZED`; it does not claim a
bitstream or a timing pass. The selected configuration does not include the
separately verified pulse 2× candidate from #192.

## Fresh device-fit result

The selected baseline synthesized successfully, but placement on the 25F
failed: 30,803 / 24,288 logic cells and 104 / 28 multipliers. No bitstream or
timing pass is claimed for that device. See `reports/selected/build-25k.json`
and its placement log. The unchanged baseline has now built and met timing on 85F;
that is a different hardware target, not evidence of a 25F fit.

Reproduce placement without repeating the 824-second synthesis only when
all source hashes, configuration and saved netlist bytes still match:

```sh
python3 fpga/build_selected.py --device 85k --out build/fpga-selected-85k --from-synthesis build/fpga-selected/report.json --nextpnr /path/to/nextpnr-ecp5
```

The resume guard was tested red before implementation. Modified source,
configuration, or netlist bytes refuse reuse. Four build-runner tests pass.

## Linux build evidence

`fpga-selected-85f` runs the same selected baseline on Ubuntu 24.04 with the
2026-09-21 OSS CAD Suite archive pinned by SHA-256. It preserves the complete
build directory (stage reports, logs, timing JSON, configuration and bitstream)
even when a stage fails. The archive checksum is verified before extraction;
tampering and archive traversal are covered by controls. Tool versions and
archive identity are saved alongside the build artifacts.

This moves the long 85F build to a dedicated runner without changing DSP or
claiming that a bitstream proves live playback. `VOICE_PULSE_2X` remains absent.
The earlier local route was interrupted after the Linux run started. Its
partial log and explicit NO-VERDICT record are retained under
`fpga/reports/selected/`; it supplies no timing pass or bitstream.

The completed Linux artifact is published with `fpga/publish_selected.py`. It
checks the recorded stage artifacts, source/ROM/constraint hashes and reused
SPI→I²S core simulation. A timing pass requires a nonempty report covering the
actual 725/59 MHz core clock, achieved frequency at least the constraint, and
resource use within the selected device. A missing clock, wrong clock, missed
timing, NaN frequency, overfull device or absent paths is rejected. The seven
clock/resource controls started red against an unimplemented gate.

```sh
python3 fpga/publish_selected.py /path/to/downloaded-selected-85f --workflow-url https://github.com/2AMLogic/gf180-parasynth/actions/runs/35666476657 --out /tmp/selected-85f-publication
```

A failed build still preserves raw reports and compressed complete logs, with
an explicit nonpassing publication state. A qualified bitstream additionally
requires all three build stages to pass. Neither state establishes live control
or captured physical line output.

## Completed Linux run

See [the recorded 85F result](reports/selected/linux-85f/README.md) and its
hash-bound publication record. Physical playback remains unverified.

The published 85F baseline achieves **12.886930 MHz** against **12.288183 MHz** reported required. Use the pinned historical bitstream linked above; current source changes are explicitly recorded separately. The publication quantization repair required no routing rerun.
