# M5B Mini V3 reference and mapping

This freezes and scores the M5B lower-note bright-lead reference before any
sound tuning. It is a **software-synth reference**, not a recording of a
physical Minimoog. The first complete M5B score is a valid **fixed-model
failure**; it is not integrated RTL evidence.

The capture used Arturia Mini V3 3.12.0.3422 under DawDreamer 0.8.3 at 48 kHz
and a pinned 16-sample host block. Each dry segment contains MIDI 72 and 84,
with separate saw and pulse segments, a 4.6 s phrase, and 2.8 s after the last
note-off. Oscillator 1 is set to the measured 8-foot range; other sound sources
and effects are disabled. The raw audio is not normalized.

| Wave | MIDI | Measured pitch | Waveform classification | Raw RMS | Attack 10–90% | Release T20 | Tail at end |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| Saw | 72 | 523.2889 Hz (+0.125 cents) | saw, verified | −19.12 dBFS | 7.71 ms | 1222.35 ms | −283.9 dB |
| Saw | 84 | 1046.5889 Hz (+0.143 cents) | saw, verified | −19.19 dBFS | 3.21 ms | 1243.25 ms | −283.8 dB |
| Pulse | 72 | 523.2889 Hz (+0.125 cents) | 48.0% duty, verified | −14.34 dBFS | 7.85 ms | 1245.67 ms | −288.7 dB |
| Pulse | 84 | 1046.5889 Hz (+0.143 cents) | 47.9% duty, verified | −14.32 dBFS | 2.85 ms | 1247.52 ms | −288.7 dB |

The open-filter setting was calibrated from self-oscillation at **14,072.86 Hz**
with three identical 16-sample-block measurements. The wrong-block control at
512 samples measured 14,494.80 Hz, a 421.93 Hz separation, and was caught. The
current cutoff-calibration wrong-then-right rate is **1/4 measurements**. A
separate 40-second Mini V3 integrity render had zero unprompted transient
events; its injected-click control detected all five clicks.

The first report draft misstated the final release interval as 2.9 s. Auditing
the event timeline against the audio caught the 0.1 s error; the corrected
case definition and manifest use 2.8 s. The waveform data were unchanged.
The artifact audit also found that the repository-wide `*.wav` ignore rule
had omitted the raw recording from the first commit. The final capture commit
tracks the WAV alongside its hash-bearing manifest.
The host also logs `attempt to map invalid URI` for the plugin bundle. The
capture records this warning; all parameter readbacks, waveform classifications,
finite/non-silent audio checks, and the integrity control succeeded.

## Legacy model score (preserved)

The original score used the older filter path and an actual 29% model pulse.
The reference pulse itself is near 48%; the earlier report incorrectly
described the model output as matching it. The exact legacy audio and score are
preserved in [`legacy-model-v1.wav`](legacy-model-v1.wav) and
[`legacy-score-v1.json`](legacy-score-v1.json). The selected profile can
reproduce that score with `engine="legacy"`.

| Property | Result | Limit | Status |
| --- | ---: | ---: | --- |
| Pitch | −0.14826 cents | 1 cent | pass |
| Harmonic shape | 23.21602 dB | 1 dB | fail |
| Foldback energy | 18.61984 dB excess | 3 dB | fail |
| Envelope attack | +6.08333 ms | 5 ms | fail |
| Envelope release | −35.25 ms | 125 ms | pass |
| Gain | +2.87502 dB | 3 dB | pass |
| Clipping | 0% | 0.01% | pass |

This score is retained as the legacy baseline, not the selected engine result.

## Selected M5A engine configuration

M5A and M5B now use the same explicit `selected` engine profile: 2× oscillator,
rate-converted 2× filter with headroom preserved and causal state, 20 kHz saw
cutoff and −0.45428 dB saw correction, `g_exact=False`, and `k_comp=True`. The
`pulse29` control name maps to the candidate `pulse479` output; the rendered
model duty is 47.90%. M5B retains its own frozen envelope calibration. This is
model-only evidence; it does not claim RTL verification.

| Property | Selected result | Limit | Status |
| --- | ---: | ---: | --- |
| Pitch | −0.14825 cents | 1 cent | pass |
| Harmonic shape | 5.96219 dB | 1 dB | fail |
| Foldback energy | 8.84954 dB excess | 3 dB | fail |
| Envelope attack | +6.06250 ms | 5 ms | fail |
| Envelope release | −30.00 ms | 125 ms | pass |
| Gain | +2.70905 dB | 3 dB | pass |
| Clipping | 0% | 0.01% | pass |

Both saw notes pass their per-note foldback limit (1.45 dB at MIDI 72 and
1.91 dB at MIDI 84); pulse remains the aliasing failure. The selected profile
substantially reduces the worst harmonic error, but changes three previously
passing pulse partials: MIDI 72 h5 (−0.24 to −2.45 dB), h9 (−0.84 to −3.97 dB),
and h12 (+0.30 to −4.31 dB). The existing incremental non-regression gate
rejects promotion on those regressions. Keep the complete signed per-note
partial vector; do not promote or tune automatically from the aggregate alone.

The selected report with full engine configuration and provenance is
[`results/M5B.json`](../results/M5B.json). Reproduce it with
`python3 tools/run_case.py M5B --results build/m5b-score-final`; a nonzero exit
is expected because the valid measured candidate fails four properties.
Retain the signed partial diagnostics when selecting further pulse or saw
changes. The attack result is close to the boundary; compare attack context
before tuning it.

Reproduce the score with:

```sh
python3 tools/run_case.py M5B --results build/m5b-score-final
```

The command exits nonzero because this measured candidate fails the case
tolerances. That is a valid result, not a missing verdict. The injected
`MONO_PITCH_UP_25_CENTS` control also produces a valid changed score (24.88 dB
worst normalized distance, with pitch as the worst property); `REF_MISSING`
refuses with a no-verdict as required.

The frozen artifacts are [raw audio](m5b-miniv3-raw.wav) and
[capture manifest](manifest.json). WAV SHA-256:
`ee77ef2180c91e2f6628339872589e0dcafb9344a30288b4a2da86274a919a29`.
The manifest binds the audio hash to the measurement commit and source-file
hashes. Recreate it with:

```sh
python3 tools/measure_mono_m5a_reference.py --case M5B \
  --out docs/scorecard/mono-m5b-miniv3
```
