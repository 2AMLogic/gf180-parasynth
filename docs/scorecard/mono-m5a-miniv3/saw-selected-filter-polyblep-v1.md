# PolyBLEP control on the selected M5A signal path

This control compares the selected 2× oscillator and causal, headroom-preserving
reconstructed 2× ladder at cutoff 14,073 Hz and drive 0.75. The enabled baseline
is the first run in [`saw-selected-filter-cutoff-sweep-v1.json`](saw-selected-filter-cutoff-sweep-v1.json);
the disabled run is [`saw-selected-filter-polyblep-off-v1.json`](saw-selected-filter-polyblep-off-v1.json).

Disabling PolyBLEP raises final foldback excess from 0 to 20.126 dB at MIDI 84
and from 0 to 23.528 dB at MIDI 96. Absolute alias-band power rises by 25.60
and 28.10 dB. Worst-partial shape error also worsens from 7.771 to 7.863 dB
and from 8.846 to 9.709 dB. Gain remains effectively unchanged. Keep PolyBLEP
enabled; the earlier single-rate-ladder measurements are not needed to reach
this conclusion.

This is a model stage control, not integrated RTL evidence. Reproduce with:

```sh
python tools/measure_m5a_signal_path.py --cutoff 14073 --drive 0.75 \
  --disable-polyblep --out build/scorecard/saw-selected-filter-polyblep-off-v1.json
```
