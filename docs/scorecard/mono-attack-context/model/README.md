# Matched attack context: unchanged model versus Mini V3

All 12 model conditions completed in [Linux CI](https://github.com/2AMLogic/gf180-parasynth/actions/runs/35666927724). The 36 frozen Mini V3 renders came from #193. MIDI timing, target note, envelope calibration and sound controls are fixed; only preceding-note history varies within each matched-time pair.

| Wave | History before target | Model attack change | Reference attack change |
| --- | --- | ---: | ---: |
| saw | repeat84_gap3p4 vs delayed84_at4p1 | +0.000 ms | -5.188 ms |
| saw | from72_gap3p4 vs delayed84_at4p1 | -0.021 ms | -5.083 ms |
| saw | repeat84_gap5 vs delayed84_at5p7 | -0.208 ms | -4.979 ms |
| pulse | repeat84_gap3p4 vs delayed84_at4p1 | +0.000 ms | -5.083 ms |
| pulse | from72_gap3p4 vs delayed84_at4p1 | +0.000 ms | -5.104 ms |
| pulse | repeat84_gap5 vs delayed84_at5p7 | +0.042 ms | -4.938 ms |

A preceding note shortens the measured reference attack by 4.94–5.19 ms. The unchanged model changes by −0.21 to +0.04 ms. It does not reproduce this history dependence under the tested conditions. This supports investigating envelope restart/state behavior before making a global attack adjustment; it does not establish the plugin's internal cause.

The selected baseline uses saw 2×, causal reconstructed filter 2×, the 20 kHz saw setting and effective 47.9% pulse at base oscillator rate. **Pulse 2× from #192 is not selected.** No DSP setting or acceptance threshold changed. These are model measurements, not RTL or hardware playback.

Reproduce the measurements from the committed audio without rendering:

```sh
python3 tools/verify_attack_context_model.py
python3 -m pytest -q tools/test_verify_attack_context_model.py
```

`report.json` binds all 12 WAV hashes, production source commit and source hashes, reference report, envelope calibration and complete timing data. `ci-import.json` records publication checks. All attack/release crossings and six history contrasts reproduce exactly on macOS; five original local WAVs match Linux byte-for-byte. One held RMS value differs by 5.55e-17 between platforms; only that diagnostic allows 1e-14 relative rounding noise. Sound limits and timing reproduction remain unchanged.

The verifier refuses a one-sample timing mutation, changed contrast, missing row, changed audio digest, and invalid timing. **Wrong-then-right: zero sound measurement corrections; one publication precondition corrected** (exact dictionary equality rejected the one-ulp RMS difference). The prior reference qualification record remains in the parent report.
