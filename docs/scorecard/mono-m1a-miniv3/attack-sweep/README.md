# M1A amp-attack sweep of the selected patch

> **Superseded for 10, 4 and 5 ms** by the v3 known-answer qualification and
> rescore in [`../attack-qualification/`](../attack-qualification/README.md):
> the v1/v2 attack readings below carry errors of several ms on realistic
> signals, and every reference fit remains unqualified under v3.

Base patch `m1a-gain-minus4db-v2`. **Only `amp[0]` (amplitude attack, seconds)
changes.** The reference, estimator, engine, tolerances, note sequence and every
other patch field stay fixed. The grid, the attack-pass rule and the preservation
criteria were committed in [`grid.json`](grid.json) (4020bfe) before any point
was rendered. The instrument is `tools/sweep_m1a_attack.py` and its gates are
tested in `tools/test_sweep_m1a_attack.py`. Evidence is **model only**.

The control point, 10 ms, is the selected patch itself. It reproduces the
promoted record's audio (`628a6a31…`) and its property vector exactly, or the
summary refuses.

Raw error = model − reference, 10–90 % attack in ms. The screening limit is 5 ms
on every event. **None of these readings is a grade.** Under the two-sided rule
(`mono_m1a_score.attack_fit_qualified`, analysis `m1a-envelope-score-v2`), the
attack is **unqualified** at every grid point, because the reference fits for
events 1 and 2 are outside the estimator's validated domain, and the reference
is fixed. History is the PR #195 contrast: event 3 minus event 1 (the same
MIDI 36, re-struck after 43 versus struck from silence), shown as model / reference.

| amp attack | MIDI 36 | MIDI 43 | MIDI 36 (repeat) | raw worst | history m / r | attack state | preserved | model fit in domain | class |
|---:|---:|---:|---:|---:|---:|:--:|:--:|:--:|:--:|
| 0.25 ms | −1.37 | +0.00 | −0.84 | −1.37 | +0.02 / −0.51 | unqualified | yes | OUT | — |
| 0.5 ms | −1.37 | +0.11 | −0.71 | −1.37 | +0.15 / −0.51 | unqualified | yes | OUT | — |
| 1 ms | −1.37 | +0.46 | −0.42 | −1.37 | +0.44 / −0.51 | unqualified | yes | OUT | — |
| 2 ms | −1.37 | +1.21 | +0.20 | −1.37 | +1.06 / −0.51 | unqualified | yes | OUT | — |
| 3 ms | −1.37 | +2.03 | +0.83 | +2.03 | +1.69 / −0.51 | unqualified | yes | OUT | — |
| 4 ms | +0.78 | +2.97 | +2.17 | +2.97 | +0.88 / −0.51 | unqualified | yes | in | exploratory |
| 5 ms | +2.36 | +2.66 | +2.85 | +2.85 | −0.02 / −0.51 | unqualified | yes | in | exploratory |
| 6 ms | +3.86 | +5.14 | +3.24 | +5.14 | −1.13 / −0.51 | unqualified | yes | in | — |
| 8 ms | +6.99 | +6.69 | +6.18 | +6.99 | −1.32 / −0.51 | unqualified | yes | in | — |
| 10 ms (control) | +5.78 | +8.11 | +7.87 | +8.11 | +1.58 / −0.51 | unqualified | yes | in | — |

Every other property is bit-identical at every grid point: Pitch −0.05059 cents
(pass), Harmonic shape 19.82331 dB (fail), Envelope release +8.47917 ms (pass),
Gain −1.04361 dB (pass), Clipping 0 % (pass), Filter envelope unqualified. No
per-note harmonic pass is lost, and every per-note gain stays inside 3 dB. The
attack changes only the note onset, and the pitch, harmonic and gain windows sit
0.3–0.55 s after it.

## Conclusion: sensitivity only, no candidate

**No grid point is a candidate.** An attack comparison needs a qualified fit on
both sides, and M1A's reference has one on the repeated MIDI 36 only. The
MIDI 36 reference fit is p = 3 (outside the qualified {0.5, 1, 2}). The MIDI 43
reference fit is p = 4 on the fit's 32-sample search minimum.

**4 ms and 5 ms are EXPLORATORY settings.** Their model-side fits are inside the
domain, and their raw readings fall inside the 5 ms limit against the current
reference readings. That makes them worth re-measuring once the estimator is
qualified. It is not evidence that either passes. Harmonic shape fails
independently in any case.

What the sweep does show: the raw model readings respond to the setting and
cross the 5 ms limit between 5 and 6 ms against the current reference readings.
No other property moves.

## What is unknown

- **The reference's true 10–90 % attack on MIDI 36 (first note) and MIDI 43.** A
  fit outside the validated shapes, or on the search minimum, gives no
  information about the true value. It does not bound it either.
- **The estimator's accuracy on this model audio**, which carries a filter-envelope
  transient at the onset that the known-signal suite does not include. The
  model's raw readings are not monotone in the setting (MIDI 36: 8 ms → 8.63 ms,
  10 ms → 7.42 ms). No independent calibration of the size of that error exists
  here. The ramp of the amplitude envelope alone is not one, because the output
  is filtered.
- **Why the model fit at 0.25–3 ms sits on the search minimum at p = 4** for the
  first MIDI 36.

Closing these is the attack-measurement qualification task. It is out of scope
here. No envelope or estimator code was changed.

Reproduce (ten independent renders, then the summary, which applies the scorer's
two-sided rule to each point's recorded fits):

```sh
python3 tools/run_all.py --timeout 7200 "python3 tools/sweep_m1a_attack.py point 0.25" … "python3 tools/sweep_m1a_attack.py point 10"
python3 tools/sweep_m1a_attack.py summarize
```

The WAVs go to `build/m1a-attack-sweep/`. Each point's JSON records its SHA-256,
patch, commit and full measurements. The points were scored by
`m1a-envelope-score-v1`, and the summary applies the v2 rule from the fits they
recorded.

**Wrong-then-right: 3.** (1) The first summary reported seven candidates
(0.25–5 ms). Five were withdrawn because the model fit sat on the search minimum.
(2) The remaining two were then called candidates while their reference fits
were out of domain. Review caught this, and they are now exploratory. (3) An
earlier draft claimed that a floor hit bounds the true reference attack
(≤ 0.27 ms) and that the model readings carry a ±2 ms uncertainty. Neither is
established, and both are withdrawn.
