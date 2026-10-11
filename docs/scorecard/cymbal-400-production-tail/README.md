# #400 — does the 1–2.5 kHz energy/decay discrepancy exist on the current production CY path?

**Verdict: NO VERDICT (REFUSED) on both properties, both windows.** No defect is confirmed and none is refuted.
The named missing capability is not a missing corpus or runtime (both were present for this run); it is
**the qualified mid-band instrument's own leak gate: the 1–2.5 kHz band of CY5025 is within 0.1 dB of the
6 dB skirt-leak margin on both the production render and the Fischer recording**, so neither side's M band can
be separated from the 3.45 kHz band's skirt by the instrument as frozen. Each side fails a *different* gate:
production fails the energy gate (5.96 dB) and the Fischer recording fails the decay (EDT-window) gate (5.92 dB).
Nothing was loosened to get a number.

No candidate was rendered or promoted. #369 still owns model → RTL → decoded I2S/image delivery.

## What was frozen before measuring (`frozen.json`)

Order, stated because it is checkable in the history: the first freeze (`7e5f524`) preceded any measurement. The
probe then changed twice after review (`1b54ab3`, raw values kept; the side-attribution fix after the Judge's
review), and each change required a re-freeze, because `measure` refuses on a changed probe hash. The first
re-freeze was committed **together with** `result.json` (`8c604da`), not before it. The current `frozen.json`
was re-frozen and committed **on its own, before** the current `result.json` was produced. Every re-freeze
changed only `source_commit` and the probe's sha256 — until this one, which also **extended the pinned
closure** (below); no threshold, window, setting, budget or corpus hash has ever moved.

| item | value |
|---|---|
| source | git commit + sha256 of the **dependency closure of the executed path**, derived by importing the probe, rendering one CY and listing every repo module the interpreter loaded: `cymbal_low_tail.py`, `cymbal_mid.py`, `cymbal_bands.py`, `run_case.py`, `model/drums_fx.py` and its imports `model/modal_fixed.py`, `audition/dsp.py`, `model/fixed.py`, `model/voice_fx.py`, the preparation dependencies `model/audio_measure.py` and `tools/preparation_contract.py`, and the probe. `measure` refuses if any changed since |
| source tree | `freeze` and `measure` **refuse a working tree with uncommitted changes to tracked files**: a dirty tree is not any commit's source, and a hash list cannot see a file outside its closure. `result.json` records the commit that actually executed as `measured_at_commit`, so attribution is stated, not implied |
| reference | Fischer CY5025 (`cy8/CY5025.WAV`), sha256 of all 25 `cy8/CY*.WAV` and the corpus git commit; `measure` refuses on any mismatch |
| production path | `run_case.render_drum_solo("CY")`, the kit that ships, through the register interface, `prepare`d. The CY initialization/command stream is **not frozen as data**: it is pinned **indirectly** by the closure hashes — `tools/run_case.py` builds the register writes and `model/drums_fx.py` renders them through `model/modal_fixed.py`, `audition/dsp.py`, `model/fixed.py` and `model/voice_fx.py`, all now pinned; and the tree rule pins everything else the interpreter loads. Any change to any of these makes `measure` refuse until re-frozen |
| knob support | **none**: `render_drum_solo` has no CY TONE/DECAY parameter, so only the D14A anchor CY5025 renders. The other 24 settings are REFUSED (recorded in `result.json`); `kit_at` and candidates are not substituted. A test fails if the signature gains knobs |
| windows | 1.5 s and 2.0 s, never mixed |
| thresholds | `run_case.TOLERANCE_POLICY`: energy 3 dB, time 50 % — imported, **no new dial** (nothing to register in `tools/sensitivity.py`) |
| Hh1 | production declared **without** Hh1 (`model/drums_fx.py`: "Hh1 is the documented omission"), reference with — an unchecked-at-runtime declaration, stated here |
| candidate budget | 0 |
| preservation set | D15A, D16A, OH00–OH75 (untouched: the probe only reads) |

Two different "M" bands are used and never mixed: energy = `cymbal_mid` `M25` (891–2818 Hz), decay =
`cymbal_low_tail` `M` (891–2828 Hz) against `Ln`.

## Result (`result.json`, produced by `measure`, 37 s wall, one production render)

Re-measured after the closure/boundary fixes below: **every verdict, value and reason is identical** to the
previous run; the record adds `measured_at_commit` (the commit that actually executed) and nothing else changed.

| property | window | production | Fischer CY5025 | outcome |
|---|---|---|---|---|
| energy (M25 share) | 1.5 s, 2.0 s | **refused** (production's gate): 1 s window 5.96 dB over skirt leakage (need 6) | qualified: 6.06 dB over, share −16.89 dB | **REFUSED by production** |
| decay (rho M, −10 dB) | 1.5 s, 2.0 s | qualified: EDT window 6.08 dB over leak, rho 0.8696 (1.5 s) / 0.8709 (2.0 s) | **refused** (Fischer's gate): EDT window 5.92 dB over leak (need 6) | **REFUSED by reference** |
| joint | | | | False (requires both DISCREPANT) |
| the other 24 settings | | | | REFUSED: no production knob support |

Each REFUSED record names the side whose gate tripped (`reason` is prefixed `production:` / `reference:`, and
`refused_by` holds both sides' reasons, `null` for a side that qualified). The qualified production rho above is
**not** a measurement of a match or a discrepancy: with no qualified reference rho there is nothing to compare it to.

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

## Controls (`tools/probes/test_cymbal_400_production_tail.py`, 48 tests, all pass)

Written and committed red against a stub (33 failed) before the instrument existed. The five refusal-attribution
controls were committed red against the unlabelled probe (`5b5ff1e`: 5 failed, 33 passed) before the fix. The
closure/boundary/matrix controls below were committed red against the probe as it stood (`ba9deb1`: 9 failed,
39 passed) before theirs.

- known answers: planted `lt.synth` strikes, energy MATCH/decay DISCREPANT, the reverse, joint, null (record vs itself);
  classifier boundaries (2.9 vs 5 dB; 40 % vs 60 %);
- defeating inputs: unprepared record (preparation mismatch), NaN/inf, truncation (REFUSED, measured: "0.500 s
  after t=0"), floor contamination (decay REFUSED, measured: end margin 5.0 dB < 15), leak-only production band
  against a reference with real content (both properties REFUSED, not DISCREPANT);
- invariance: scale, prepended silence (values unchanged); trailing silence is outside the declared domain and
  is asserted to never *change* a verdict (measured: same MATCH/MATCH);
- refusals: corpus missing, corpus hash differs from frozen, unsupported knob settings;
- refusal attribution: a planted reference-only refusal (leak-only reference against a qualifying production)
  must name the reference and not production, for energy and decay; the mirror case must name production; when
  both sides refuse, both reasons are recorded;
- executed mutants: `NO_PREP_ASSERT`; `NO_FINITE_CHECK`, which bypasses the finite guard — the NaN record is
  then **still REFUSED downstream** (energy by `classify`, "a side is missing or non-finite"; decay by
  `cymbal_mid`'s floor gate), so the guard is defence in depth that gives an early, side-named refusal, not the
  only thing stopping the input; the test asserts exactly that, so a downstream change letting NaN reach a
  verdict goes red; and the wrong Hh1 declaration shown to change the leak prediction;
- **properties × defects matrix (rule 4)**, executed, printed by
  `test_control_matrix_energy_decay_x_defects_is_printed_and_no_defect_goes_unseen` and transcribed here —
  MOVED = the property's reported outcome (verdict, values, reason) changed under the defect, BLIND = it did not.
  Every cell moved; a defect that nothing sees fails the suite:

  | defect | energy | decay |
  |---|---|---|
  | `NO_PREP_ASSERT` (unprepared record) | **MOVED**: record-level refusal → **MATCH** (−10.55 vs −8.57 dB) — without the guard the unprepared record is *measured*, and energy reports a match | **MOVED**: record-level refusal → REFUSED (window too short) |
  | `NO_FINITE_CHECK` (NaN sample) | **MOVED** (reason only): early "record is not finite" → `classify`'s "a side is missing or non-finite"; verdict stays REFUSED | **MOVED** (reason only): early guard → the floor/skirt gates; verdict stays REFUSED (the mutant's reason renders `None dB` — the known cosmetic defect of `cymbal_mid`/`cymbal_low_tail`, not this probe) |
  | `WRONG_HH1` (declaration flipped) | **MOVED**: MATCH → REFUSED (predicted leak −13.07 → −11.36 dB puts the band 4.8 dB under the 6 dB margin) | **MOVED**: MATCH → REFUSED (EDT margin 5.04 dB, same cause) |

  The WRONG_HH1 row is the strongest reading in the table: on the synthetic strike the wrong declaration does
  not merely shift a prediction, it flips both verdicts; on the real kit, where the margin sits at 5.96 vs 6 dB,
  it would flip them the other way — a REFUSED turning into a qualified number under an undeclared change.
- **refusal boundary (CLI)**: `run_case.prepare` refuses non-finite / cut-into-strike records with
  `run_case.Refused` and `cymbal_mid` refuses a silent record with `cymbal_bands.Refused` — classes the CLI used
  to let through as a traceback (exit 1). Now `main` normalizes exactly those classes (plus `cymbal_low_tail`'s)
  to `REFUSED / NO VERDICT` (exit 2), with the side named: `prepare`'s message carries the side, and the
  silence refusal is re-raised from `_side` as `reference: silent` / `production: silent`. Controls drive
  `main` end to end for silence, a non-finite render and a reference cut into its strike; a further control
  asserts an implementation error (`ValueError`) still propagates — refusals are normalized **by class**, never
  by catching `Exception`. The CLI controls require a clean committed tree and say so when it is not;
- **freeze closure / source-tree identity**: the render path's model modules and the preparation dependencies
  must be in `SOURCE_FILES` (asserted against the set derived from an actual render); a changed closure file
  and a dirty tracked tree must refuse **before rendering** (the render is monkeypatched to fail the test if
  reached, which is how "before" is proven, not asserted).

Known limitation of the controls: they run on synthetic strikes. The instrument's dynamic range on rho is narrow
(a planted 2.4x tau ratio reads +86 %; shorter-than-reference tails below ~0.15 s are refused), so only the
"production outlasts reference" direction could be planted as DISCREPANT. The reverse direction is covered only
by the classifier unit tests, not end to end.

## Wrong-then-right: 5

1. Planted energy fixture: expected a 4x `a_m` change to read ~12 dB; M25 share saturates (−6.32 → −1.47 dB at 2 → 8). Caught by the test failing. Fixed by calibrating the plant.
2. Planted decay fixture: a 2.4x shorter prod tail read a −48 % rho change (inside the 50 % tolerance) and ≤0.15 s tails refuse. Caught by the test failing; the plant was reversed.
3. First reading of the REFUSED result attributed the refusal to the reference side; the raw values showed it was production (and the reference is also near the boundary). Caught by adding the raw values to the record. This correction was itself only half right — see 4.
4. The corrected report then attributed **both** refusals to production. That holds for energy only: on decay, production qualified (EDT 6.08 dB, rho 0.8696 / 0.8709) and the Fischer recording refused (EDT 5.92 dB), and the README table said the opposite. Root cause: `compare_records` built the reason as `p_reason or r_reason` with no side label, so neither `result.json` nor its author could tell whose gate tripped — the raw-values fix in 3 added evidence but not attribution. Caught by the Judge reading `unqualified_raw` against the table, not by a control. Fixed by side-labelled reasons (`refused_by`) with planted one-sided refusal controls, a re-freeze and a re-measurement.
5. The freeze pinned **six** files, enumerated by hand from the probe's own imports; the render actually executes `model/drums_fx.py`'s imports (`model/modal_fixed.py`, `audition/dsp.py`, `model/fixed.py`, `model/voice_fx.py`, imported lazily inside `render_drum_solo`) and preparation executes `model/audio_measure.py` and `tools/preparation_contract.py` — none pinned, so an edit to the modal bank after freeze would have changed the render under an unchanged guard while `result.json` attributed it to the frozen commit. Caught by the Judge reading the import graph, not executed there and not caught by any control. Fixed by deriving the closure from an actual render's loaded modules, refusing a dirty tracked tree at freeze and measure, recording `measured_at_commit`, and shipping the defeating inputs (changed closure file, dirty tree) as refuse-before-rendering controls.

## Reproduce / run on the build box

```
python3 -m pytest tools/probes/test_cymbal_400_production_tail.py -q
python3 tools/probes/cymbal_400_production_tail.py freeze  --refs $GF180_TR808_REFS --out docs/scorecard/cymbal-400-production-tail/frozen.json
python3 tools/probes/cymbal_400_production_tail.py measure --refs $GF180_TR808_REFS --frozen docs/scorecard/cymbal-400-production-tail/frozen.json --out docs/scorecard/cymbal-400-production-tail/result.json
```
Exit 2 and `REFUSED / NO VERDICT` when the corpus, the frozen record, a source hash or the tree state is
wrong — including the downstream preconditions (`run_case.prepare`'s non-finite / cut-into-strike refusals,
`cymbal_mid`'s silence refusal), which are normalized by class at the CLI boundary; an implementation error
still exits as a traceback. `freeze` and `measure` refuse a dirty tracked tree, so run them from a committed
state.
`make verify` / `make controls` were not run here (shared dispatch host); they are deferred to the build box.

## Suggested separate follow-up (not done here)

A direct production-vs-808 comparison needs a band the leak gate can qualify: either the lower three thirds
(`Mn`, 891–1782 Hz, furthest from the 3.45 kHz peak) under a frozen new gate, or a knob-capable production path
(#413) so that settings with more 1–2.5 kHz content than CY5025 can be rendered. Either is a new frozen
decision, not a retune of this one.
