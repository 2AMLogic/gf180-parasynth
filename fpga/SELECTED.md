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

Build the ULX3S 25F candidate:

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
new model-only pulse experiment.
