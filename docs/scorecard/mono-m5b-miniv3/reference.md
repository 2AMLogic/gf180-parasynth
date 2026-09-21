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

## First model score

The complete phrase was scored against the seven required M5B properties using
`production-2x-hold`, the `pulse29` control (which produces the separately
measured reference duty near 48%), and no normalization. The run is bound to
source commit `53c7466`, this reference audio and manifest, and analysis version
`m5b-score-v1`. The full per-event diagnostics and provenance are in
[`results/M5B.json`](../results/M5B.json).

| Property | Result | Limit | Status |
| --- | ---: | ---: | --- |
| Pitch | −0.14826 cents | 1 cent | pass |
| Harmonic shape | 23.21602 dB | 1 dB | fail |
| Foldback energy | 18.61984 dB excess | 3 dB | fail |
| Envelope attack | +6.08333 ms | 5 ms | fail |
| Envelope release | −35.25 ms | 125 ms | pass |
| Gain | +2.87502 dB | 3 dB | pass |
| Clipping | 0% | 0.01% | pass |

The score localizes the next sound work: pulse harmonic shape and foldback are
the largest misses; saw harmonic shape and aliasing also remain outside the
limits. Attack is just beyond tolerance. Pitch, release, gain, and clipping
already pass. Keep the per-wave and per-note signed partial diagnostics in the
JSON when selecting a pulse or saw change; the phrase aggregate alone can hide
a regression in one segment.

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
