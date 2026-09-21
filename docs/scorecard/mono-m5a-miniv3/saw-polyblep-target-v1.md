# M5A saw PolyBLEP control at the target drive

The M5A patch uses ladder drive 0.75. This paired 2× oscillator experiment
uses the frozen reference, cutoff 14,073 Hz, drive 0.75, and all other patch
controls unchanged. The sole intervention disables the PolyBLEP correction
before the same 2× decimator. The probe left the ladder at its default
single-rate configuration, so this is not evidence for the selected causal,
headroom-preserving 2× ladder path. The metric windows and stage probes are
identical between runs.

Disabling PolyBLEP does not improve the target patch. At MIDI 84/96, final
worst-partial error changes from 7.823/8.921 dB to 7.916/9.436 dB. Final
foldback excess changes from 12.085/15.478 dB to 21.499/24.878 dB, and absolute
alias-band power rises by 9.42/9.40 dB. Gain is unchanged within 0.005 dB.

On the single-rate ladder diagnostic, disabling PolyBLEP increases aliasing at
the M5A drive. The production-path configuration must be rerun before treating
this as a conclusion about the shipped filter candidate. This is model-only
evidence and does not change the RTL candidate.

Reproduce the baseline and challenger with:

```sh
python tools/measure_m5a_signal_path.py --cutoff 14073 --drive 0.75 \
  --out build/scorecard/saw-polyblep-on-target-v1.json
python tools/measure_m5a_signal_path.py --cutoff 14073 --drive 0.75 \
  --disable-polyblep --out build/scorecard/saw-polyblep-off-target-v1.json
```

Both reports bind to source commit `eb2c22680b79ffe67f9f487c8fbcd33d8ea8b521`:
[PolyBLEP enabled](saw-polyblep-on-target-v1.json), [disabled](saw-polyblep-off-target-v1.json).
