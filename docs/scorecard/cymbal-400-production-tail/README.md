# #400 — does the 1–2.5 kHz energy/decay discrepancy exist on the current production CY path?

**Verdict: NO VERDICT (REFUSED) on both properties, both windows.** No defect is confirmed and none is refuted.
The named missing capability is not a missing corpus or runtime (both were present for this run); it is
**the qualified mid-band instrument's own leak gate: the 1–2.5 kHz band of CY5025 is within 0.1 dB of the
6 dB skirt-leak margin on both the production render and the Fischer recording**, so neither side's M band can
be separated from the 3.45 kHz band's skirt by the instrument as frozen. Nothing was loosened to get a number.

No candidate was rendered or promoted. #369 still owns model → RTL → decoded I2S/image delivery.

## What was frozen before measuring (`frozen.json`, committed before `result.json`)

| item | value |
|---|---|
| source | git commit + sha256 of `cymbal_low_tail.py`, `cymbal_mid.py`, `cymbal_bands.py`, `run_case.py`, `model/drums_fx.py` and the probe; `measure` refuses if any changed since |
| reference | Fischer CY5025 (`cy8/CY5025.WAV`), sha256 of all 25 `cy8/CY*.WAV` and the corpus git commit; `measure` refuses on any mismatch |
| production path | `run_case.render_drum_solo("CY")`, the kit that ships, through the register interface, `prepare`d |
| knob support | **none**: `render_drum_solo` has no CY TONE/DECAY parameter, so only the D14A anchor CY5025 renders. The other 24 settings are REFUSED (recorded in `result.json`); `kit_at` and candidates are not substituted. A test fails if the signature gains knobs |
| windows | 1.5 s and 2.0 s, never mixed |
| thresholds | `run_case.TOLERANCE_POLICY`: energy 3 dB, time 50 % — imported, **no new dial** (nothing to register in `tools/sensitivity.py`) |
| Hh1 | production declared **without** Hh1 (`model/drums_fx.py`: "Hh1 is the documented omission"), reference with — an unchecked-at-runtime declaration, stated here |
| candidate budget | 0 |
| preservation set | D15A, D16A, OH00–OH75 (untouched: the probe only reads) |

Two different "M" bands are used and never mixed: energy = `cymbal_mid` `M25` (891–2818 Hz), decay =
`cymbal_low_tail` `M` (891–2828 Hz) against `Ln`.

## Result (`result.json`, produced by `measure`, 36 s, one production render)

| property | window | production | Fischer CY5025 | outcome |
|---|---|---|---|---|
| energy (M25 share) | 1.5 s, 2.0 s | refused: 5.96 dB over skirt leakage (need 6) | 6.06 dB over (passes 1 s gate; EDT window 5.92 dB) | **REFUSED** |
| decay (rho M, −10 dB) | 1.5 s, 2.0 s | refused: EDT window 5.92 dB over leak (need 6) | no qualified value | **REFUSED** |
| joint | | | | False (requires both DISCREPANT) |
| the other 24 settings | | | | REFUSED: no production knob support |

Unqualified raw values are in `result.json` (`detail.unqualified_raw`) so the refusal is diagnosable. They are **not
verdict inputs**: production M25 share −17.25 dB vs reference −16.89 dB looks like a 0.36 dB agreement, but both
sit at the leak boundary, where `cymbal_mid` documents that the number cannot be attributed to a separate
1–2.5 kHz component. Do not quote it as the sound matching.

What the refusal does tell us (measured, not interpreted): at the setting we can render, the real 808's own
1–2.5 kHz content is barely above what its low band's skirt alone would produce. That is why a direct
production-vs-808 comparison is at the edge of this instrument's domain, and why #369's step-5/step-8 numbers
(derived from differences along time, not the leak-gated band) are the only evidence of the tail question so far.
The historical 8–12 dB tail drop remains an unreproduced-on-current-production historical figure.

## Confirmation on untouched conditions

Not available. Production renders only CY5025 (the development setting), so no untouched setting can confirm
anything on this path. A confirmed *mechanism* claim is therefore withheld by construction, and any future
positive finding here would still need a knob-capable production path (#413).

## Stage isolation

Not run: its precondition (DISCREPANT in both windows) was not met. The predictions and their status are
frozen in `frozen.json` (`stage_isolation`), all `unverified`/`inferred`; none is a result.

## Controls (`tools/probes/test_cymbal_400_production_tail.py`, 33 tests, all pass)

Written and committed red against a stub (33 failed) before the instrument existed.

- known answers: planted `lt.synth` strikes, energy MATCH/decay DISCREPANT, the reverse, joint, null (record vs itself);
  classifier boundaries (2.9 vs 5 dB; 40 % vs 60 %);
- defeating inputs: unprepared record (preparation mismatch), NaN/inf, truncation (REFUSED, measured: "0.500 s
  after t=0"), floor contamination (decay REFUSED, measured: end margin 5.0 dB < 15), leak-only production band
  against a reference with real content (both properties REFUSED, not DISCREPANT);
- invariance: scale, prepended silence (values unchanged); trailing silence is outside the declared domain and
  is asserted to never *change* a verdict (measured: same MATCH/MATCH);
- refusals: corpus missing, corpus hash differs from frozen, unsupported knob settings;
- executed mutants: `NO_PREP_ASSERT`, `NO_FINITE_CHECK` (the guard is shown to be the only thing stopping the input),
  and the wrong Hh1 declaration shown to change the leak prediction.

Known limitation of the controls: they run on synthetic strikes. The instrument's dynamic range on rho is narrow
(a planted 2.4x tau ratio reads +86 %; shorter-than-reference tails below ~0.15 s are refused), so only the
"production outlasts reference" direction could be planted as DISCREPANT. The reverse direction is covered only
by the classifier unit tests, not end to end.

## Wrong-then-right: 3

1. Planted energy fixture: expected a 4x `a_m` change to read ~12 dB; M25 share saturates (−6.32 → −1.47 dB at 2 → 8). Caught by the test failing. Fixed by calibrating the plant.
2. Planted decay fixture: a 2.4x shorter prod tail read a −48 % rho change (inside the 50 % tolerance) and ≤0.15 s tails refuse. Caught by the test failing; the plant was reversed.
3. First reading of the REFUSED result attributed the refusal to the reference side; the raw values showed it was production (and the reference is also near the boundary). Caught by adding the raw values to the record.

## Reproduce / run on the build box

```
python3 -m pytest tools/probes/test_cymbal_400_production_tail.py -q
python3 tools/probes/cymbal_400_production_tail.py freeze  --refs $GF180_TR808_REFS --out docs/scorecard/cymbal-400-production-tail/frozen.json
python3 tools/probes/cymbal_400_production_tail.py measure --refs $GF180_TR808_REFS --frozen docs/scorecard/cymbal-400-production-tail/frozen.json --out docs/scorecard/cymbal-400-production-tail/result.json
```
Exit 2 and `REFUSED / NO VERDICT` when the corpus, the frozen record, or a source hash is missing/changed.
`make verify` / `make controls` were not run here (shared dispatch host); they are deferred to the build box.

## Suggested separate follow-up (not done here)

A direct production-vs-808 comparison needs a band the leak gate can qualify: either the lower three thirds
(`Mn`, 891–1782 Hz, furthest from the 3.45 kHz peak) under a frozen new gate, or a knob-capable production path
(#413) so that settings with more 1–2.5 kHz content than CY5025 can be rendered. Either is a new frozen
decision, not a retune of this one.
