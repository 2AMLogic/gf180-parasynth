# M1A amp-attack sweep of the selected patch

Base patch `m1a-gain-minus4db-v2`. **Only `amp[0]` (amplitude attack, seconds)
changes.** The reference, estimator, engine, tolerances, note sequence and every
other patch field stay fixed. The grid, the attack-pass rule and the preservation
criteria were committed in [`grid.json`](grid.json) (4020bfe) before any point
was rendered. The instrument is `tools/sweep_m1a_attack.py` and its gates are
tested in `tools/test_sweep_m1a_attack.py`. Evidence is **model only**.

The control point, 10 ms, is the selected patch itself. It reproduces the
promoted record's audio (`628a6a31…`) and its property vector exactly, or the
summary refuses.

Error = model − reference, 10–90 % attack in ms. The screening limit is 5 ms on
every event. History is the PR #195 contrast: event 3 minus event 1 (the same
MIDI 36, re-struck after 43 versus struck from silence), shown as model / reference.

| amp attack | MIDI 36 | MIDI 43 | MIDI 36 (repeat) | worst | history m / r | attack | preserved | model fit in domain |
|---:|---:|---:|---:|---:|---:|:--:|:--:|:--:|
| 0.25 ms | −1.37 | +0.00 | −0.84 | −1.37 | +0.02 / −0.51 | pass | yes | **OUT** |
| 0.5 ms | −1.37 | +0.11 | −0.71 | −1.37 | +0.15 / −0.51 | pass | yes | **OUT** |
| 1 ms | −1.37 | +0.46 | −0.42 | −1.37 | +0.44 / −0.51 | pass | yes | **OUT** |
| 2 ms | −1.37 | +1.21 | +0.20 | −1.37 | +1.06 / −0.51 | pass | yes | **OUT** |
| 3 ms | −1.37 | +2.03 | +0.83 | +2.03 | +1.69 / −0.51 | pass | yes | **OUT** |
| **4 ms** | +0.78 | +2.97 | +2.17 | +2.97 | +0.88 / −0.51 | **pass** | yes | in |
| **5 ms** | +2.36 | +2.66 | +2.85 | +2.85 | −0.02 / −0.51 | **pass** | yes | in |
| 6 ms | +3.86 | +5.14 | +3.24 | +5.14 | −1.13 / −0.51 | fail | yes | in |
| 8 ms | +6.99 | +6.69 | +6.18 | +6.99 | −1.32 / −0.51 | fail | yes | in |
| 10 ms (control) | +5.78 | +8.11 | +7.87 | +8.11 | +1.58 / −0.51 | fail | yes | in |

The attack setting leaves every other property bit-identical at every grid point:
Pitch −0.05059 cents (pass), Harmonic shape 19.82331 dB (fail), Envelope release
+8.47917 ms (pass), Gain −1.04361 dB (pass), Clipping 0 % (pass), Filter envelope
unqualified. No per-note harmonic pass is lost and every per-note gain stays inside
3 dB. The attack changes only the note onset, and the pitch, harmonic and gain
windows sit 0.3–0.55 s after it.

## Conclusion: two candidates, not promoted

The candidates are **`+amp_attack=4ms`** and **`+amp_attack=5ms`**, each with
this complete vector: Pitch −0.05059 cents pass · Harmonic shape 19.82331 dB
**fail** · Envelope attack +2.96689 ms (4 ms) or +2.85 ms (5 ms) pass · Release
+8.47917 ms pass · Gain −1.04361 dB pass · Clipping 0 % pass · Filter envelope
unqualified. That is 5 passing, 1 failing and 1 unqualified. **Harmonic shape
still fails, so the case is still a valid fail.** A better attack is a component
improvement, not a pass for the whole case.

## Why 0.25–3 ms are refused even though they pass the gate

`attack_fit` was qualified on shapes p ∈ {0.5, 1, 2} and spans of 0.5–20 ms. It
also searches p = 3 and 4, and its shortest span is 32 samples. That makes its
smallest reportable 10–90 % time **0.667 ms × (0.9^¼ − 0.1^¼) = 0.2745 ms**, which
is a floor, not a measurement. At 0.25–3 ms the MIDI 36 model fit sits on that
floor (p = 4, 0.27 ms) at every setting. At 3 ms the amplitude envelope alone
gives 2.4 ms 10–90 %, so the reading is about 2.1 ms low. The sweep now refuses a
gate pass read from an out-of-domain fit.

Even inside the domain, readings of the model depart from the known amplitude
ramp (0.8 × attack) by −1.06 to +2.23 ms, and the MIDI 36 reading is not
monotone (8 ms → 8.63, 10 ms → 7.42). That is more than the 0.50 ms
known-signal bound. The likely cause is the unchanged 4 ms filter-envelope
transient on the onset. So treat single readings as uncertain to about ±2 ms.
The 4 and 5 ms passes keep about 2 ms of margin.

**A finding that predates this work, about the reference side.** Two of the three
reference attack fits fall outside the qualified domain. The MIDI 36 fit is
p = 3 (1.64 ms). The MIDI 43 fit is p = 4 at exactly the 0.2745 ms floor, so it
is censored ("≤ 0.27 ms"). The published M1A attack property, +8.11 ms, rests
on those readings. The 4 and 5 ms passes do not depend on them: they still hold
for any true reference attack between 0 and about 5 ms on every event. The
M1A attack property itself needs that domain gap closed in `attack_fit`, which
is shared estimator code. This sweep does not touch it.

Reproduce (ten independent renders, then the gated summary):

```sh
python3 tools/run_all.py --timeout 7200 "python3 tools/sweep_m1a_attack.py point 0.25" … "python3 tools/sweep_m1a_attack.py point 10"
python3 tools/sweep_m1a_attack.py summarize
```

The WAVs go to `build/m1a-attack-sweep/`. Each point's JSON records its SHA-256,
patch, commit and full measurements.

**Wrong-then-right: 1.** The first summary reported seven candidates
(0.25–5 ms). A plausibility check against the known amplitude ramp found the
estimator floor, and five of the seven were withdrawn.
