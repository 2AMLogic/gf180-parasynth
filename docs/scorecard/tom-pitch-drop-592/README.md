# #592: the toms' diode pitch drop against the Fischer takes

**No sound changes ship here.** The diode-drop law (`TOM_DROP_*`), the kit, the RTL and the image are unchanged. This directory holds a
phase-aware measurement, its controls, a preregistration and the result of running it. The pre-registered result is **REFUSED: not
identifiable**:

- only 3 of the 7 development conditions produced a measurement, and the frozen rule needs 4;
- with no development fit there was nothing to confirm, so the 8 reserved conditions were **left unread**;
- independent confirmation was REFUSED in the preregistration, before any measurement, because every tom take in this corpus has
  prior exposure (section 2).

Engine: `origin/main` at `6d5b335`. Corpus: `tidalcycles/sounds-tr808-fischer` at `85fbecf`. That is one TR-808, with each of the 15 tom
takes hash-checked against the full sha256 in `prereg.json`. `~/dev/refs` does not exist on this loom worker, so the takes were read from
`/tmp/fischer-592`. Integrity comes from the hash check at the point of use, not from that path.

## Sound summary

| | what was measured | status |
|---|---|---|
| **Shipped staircase** (our engine, unity tuning, accent 1.0) | onset excess **E 0.076 / 0.070 / 0.067** (LT/MT/HT, i.e. 127 / 117 / 112 cents), relaxation **tau 14.7 / 15.7 / 16.3 ms**. The law asks for E 0.060 and a continuous tau of 20 ms. The realised staircase reads higher and faster: it uses interval-mean steps and then holds at f0 (1 + E e^-3) after 60 ms, so it never returns to f0. The render carries the production host's staircase on every route (`carry` in `describe-development.json`). | measured on our model, independent of the corpus |
| **808 development takes** (post-hoc description, not a verdict) | LT50 E 0.027 (47 cents), tau 55 ms. LT75 E 0.047 (80 cents), tau 51 ms. MT50 E 0.024 (41 cents), tau 34 ms. HT75 E 0.049 (82 cents), tau 22 ms. LT25, HT25 and HT50 were REFUSED on route disagreement. | 3 usable for the fit (HT75 has no tuning reference, because HT50 refused); **not a confirmed effect** |
| **Discrepancy, 808 minus ours** (the 3 measured rows) | onset excess **-81 / -63 / -76 cents** (u ~35 cents). tau ratio **x3.7 / x3.3 / x2.2** (ln 1.32 / 1.20 / 0.77, u 0.43). The 808 starts lower and relaxes 2-4x slower. On the two continuous exponential fits, its excess crosses above ours at **20.7 ms (LT50), 13.1 ms (LT75) and 31.3 ms (MT50)**, i.e. about 13-31 ms, from t = ln(E_ours/E_808) / (1/tau_ours - 1/tau_808) with the record's fitted E and tau. That uses the exponential fit for ours; the realised staircase instead holds at f0 (1 + E e^-3) after 60 ms. |  measured on 3 development rows; **not confirmed** |
| **Production repair** | none proposed. A magnitude change would not be identifiable here even if confirmed: the Fischer accent is undocumented and the law's E scales with accent (prereg `adequacy.accent_confound`). | **not identifiable** |

**Re-reading #558's "74 vs 35 cents"** (post-hoc). The gate's first frame read the 808's LT50 at 74 cents and ours at 35. The phase-aware
estimator reads them in the *opposite* order at onset: 47 cents for the 808 against 127 for ours. The 808's drop is smaller at onset and
lasts longer. That gate figure was not a drop measurement, which is the premise of this issue.

## 1. The estimator (`tools/probes/tom_pitch_drop_fischer.py`)

The estimator separates onset phase from a drop by reading the drop as phase **curvature**. With f(t) = f_inf (1 + E exp(-t/tau)), the
phase is psi + 2 pi f_inf t - 2 pi f_inf E tau exp(-t/tau). The onset phase sits inside the free constant psi, so a constant-pitch tone of
any onset phase has no curvature.

Two independent routes must agree, or the condition is REFUSED:

- **A: waveform.** The unfiltered samples are fitted by variable projection, with H1..H3 locked to theta(t). Amplitudes and phases are
  solved linearly at every step, so the onset phase is never assumed.
- **B: zero crossings.** The signal goes through an order-2 low-pass at 3 fc. The half-cycle count is fitted linearly in (a, f_inf, c,
  constant zig-zag, decaying zig-zag) on a tau grid.

The two routes share only the onset and window rules.

**Known answers** (`knownanswer.json`, 195 cases built from closed-form tones and scipy resonators, with no DrumsFx). Coverage is 6
frequencies (82-205 Hz) x 4 onset phases x 6 drops (E 0 to 0.15, tau 10-35 ms) at 44.1 and 48 kHz. A harsh family adds H2 at -30 dB,
H3 at -40 dB and a -20 dB noise burst.

- **195 / 195 pass the frozen bounds**, and no zero-drop case reads as DROP.
- 14 cases are correct refusals outside the declared observable domain (fewer than 4 half-cycles within 2 tau).
- Worst error over DROP reads: |E| 0.009, |ln tau| 0.11.
- The gate's own fixture, a band-pass resonator with no pitch change, reads > 30 cents in the gate (re-asserted in the test file). Here
  it reads **NO-DROP** for all three numerators at all five f0/tau pairs.

**Uncertainty.** Every corpus figure carries u = sqrt(se^2 + floor^2). The floors are E 0.015 and ln tau 0.30, the worst errors over 252
harsh-family reads, measured before any corpus read. On that family the white-residual se alone understates the error by up to 13x.

**Stated blind spot** (rule 8, form 2). A second partial 3 % below the fundamental at -10 dB makes both routes agree on NO-DROP for a real
E 0.06 drop (`test_known_blind_spot_a_close_partial_can_mask_a_drop`).

### Controls (`controls.json`): clean, then each mutant, then clean again

| injection | zero drop, any phase | non-zero drop recovered | route disagreement refused | staircase carried | wrong hash refused | confirmation disjoint | verdict |
|---|---|---|---|---|---|---|---|
| stub (always NO-DROP) | BLIND | MOVED | MOVED | MOVED | BLIND | BLIND | CAUGHT |
| fixed-onset-phase (phase assumed, so an offset reads as a drop) | MOVED | MOVED | MOVED | MOVED | BLIND | BLIND | CAUGHT |
| first-frame (the gate's kind of reading) | MOVED | MOVED | MOVED | MOVED | BLIND | BLIND | CAUGHT |
| omit-host-writes (render without the tom bend) | BLIND | BLIND | BLIND | MOVED | BLIND | BLIND | CAUGHT |
| confirm-pools-dev | BLIND | BLIND | BLIND | BLIND | BLIND | MOVED | CAUGHT |
| no-hash-check | BLIND | BLIND | BLIND | BLIND | MOVED | BLIND | CAUGHT |
| no-agreement-check | BLIND | BLIND | MOVED | BLIND | BLIND | BLIND | CAUGHT |

"Caught" means three things held: the clean run held every property, the mutant ran to a verdict on every property, and its declared
property moved.

**Start red** (`start-red-stub.log`). The whole test file was run against the stub (`TPD592_INJECT=stub`): **79 failed, 69 passed,
1 skipped**. The tests that passed under the stub were the zero-drop, judge and staircase tests, which a stub that answers NO-DROP cannot
fail. The stub is BLIND to them, and the matrix row above shows that.

**Defeating inputs for the new guards:**

| guard | defeating input | committed as |
|---|---|---|
| silence | NaN | `test_refuses_nonfinite_before_silence` |
| too-short | the record padded with digital silence | `test_refuses_too_short_and_padding_cannot_rescue_it` |
| route agreement | a beating second partial | `beating_tone`; the `no-agreement-check` mutant |
| hash | a tampered take | `wrong_hash_refused` |
| carry | omitted host writes | the `omit-host-writes` mutant |
| confirmation | development rows pooled into confirmation | the `confirm-pools-dev` mutant |
| confirm-once | a record that already exists | `test_confirm_runs_once` |

**Not defeated, and a finding** (section 4): the agreement rule's *class* test.

## 2. Preregistration (`prereg.json`, commit `8b31320`, before any take was measured)

Before that commit, the only operation performed on the corpus was hashing the takes.

| split | takes | prior exposure |
|---|---|---|
| development | LT25 LT50 LT75 MT50 HT25 HT50 HT75 | The 25/75 takes were #558's selection set. The 50 takes were #558's diagnosis targets: the "74 cents" is LT50's first gate frame. |
| confirmation (reserved) | LT00 LT10 MT00 MT25 MT75 MT10 HT00 HT10 | These were #558's numerator confirmation set. Their gate pitch_shape against the shipped engine is published. |

- **Independent confirmation was REFUSED up front.** The corpus has one take per TUNING setting from one unit. Every tom take's gate
  pitch_shape, which is a pitch-trajectory distance, has already been read. So the reserved set is labelled "reserved, previously exposed".
  It is never labelled "untouched".
- **No take was used to fit the shipped law.** That law came from 808 From Mars.
- **No take had been read for E or tau before this work.**
- **Accent.** The Fischer accent is undocumented and ours is fixed at 1.0. No accent claim is made. A confirmed magnitude discrepancy would
  be reported as "not identifiable (accent confound)", never as a justified `TOM_DROP_RATIO` repair.
- **Rules.**
  - A fit needs at least 4 usable development rows.
  - Confirmation needs at least 5 usable rows and at least 75 % of them to agree on the sign of the discrepancy, to resolve beyond 2u,
    and to be predicted better by the development offset than by the shipped law.
  - The thresholds are 5 cents (the gate's JND) and a tau ratio of 1.25.

## 3. Result

`development.json` (measured at `b6d216e` from a clean tree, committed in `bd33dbe`):

| take | class | 808 E (cents) / tau | ours E (cents) / tau, same tuning | d cents (u) | ln tau ratio (u) |
|---|---|---|---|---|---|
| LT25 | REFUSED: route disagreement (tau A 105 vs B 74 ms) | | | | |
| LT50 | MEASURED | 0.0272 (46.5) / 54.7 ms | 0.0764 (127.4) / 14.7 ms | -80.9 (35.0) | +1.32 (0.43) |
| LT75 | MEASURED, r 1.053 | 0.0472 (79.9) / 50.5 ms | 0.0860 (142.9) / 15.3 ms | -62.9 (34.5) | +1.20 (0.43) |
| MT50 | MEASURED | 0.0238 (40.7) / 33.8 ms | 0.0696 (116.5) / 15.7 ms | -75.8 (35.1) | +0.77 (0.43) |
| HT25, HT75 | REFUSED: no tuning reference (HT50 refused) | | | | |
| HT50 | REFUSED: no tuning reference (record reason). HT50 is its own tuning reference, and its 808 estimate is refused on route disagreement (`describe-development.json`) | | | | |

- **`fit.json`** (committed in `41f5ffb`, before `confirm` ran): **REFUSED, not identifiable**, because 3 usable development rows are fewer than 4.
- **`confirmation.json`**: magnitude **REFUSED**, relaxation **REFUSED** (no development fit). **None of the 8 reserved takes was read.**
  This procedure was added after the development result, in commit `b8c1fb0`. It is the conservative
  reading of "evaluate once": with no hypothesis, measuring the reserved takes would spend them without testing anything. A successor can
  still use them as reserved conditions for this estimator. That does not make them fresh; see section 2.

**Changes to the probe after the development measurement** (deviations from the preregistered code). An earlier version of this
README said the estimator was unchanged apart from `b8c1fb0`. That was wrong. Three commits changed `tools/probes/tom_pitch_drop_fischer.py`
after `development.json` was measured at `b6d216e`:

| commit | change | effect on a committed record |
|---|---|---|
| `b8c1fb0` | `confirm` leaves the reserved rows unread when the development fit is REFUSED; adds the post-hoc `describe` | none: it only governs `confirmation.json`, which was produced by it |
| `1a697f1` | `knownanswer`/`controls` exit non-zero on any failed case or uncaught mutant; the confirmation leak check sees supplied development rows instead of filtering them out first | none: identical re-run below |
| `8fedbf4` | route A keeps only successful, finite optimizer results (an unconverged lower-cost start can no longer supply the parameters); route B REFUSES `fit-failed` when its bounded refinement does not converge; `fit` and `judge_confirmation` bind every row to `prereg.split_of`, its label and its take path | none: identical re-run below |

So `knownanswer.json`, `controls.json` (both at `8b31320`), `development.json` (`b6d216e`) and `fit.json` were all produced by
earlier code than the code that ships. **Re-run at `8fedbf4`**, the last commit to touch the probe, from a clean tree, into
`rerun-8fedbf4/` (`knownanswer`, `controls`, `measure --split development`, `fit`; `confirm` was not re-run and no reserved take
was read). `tools/probes/tpd592_rerun_compare.py` compares each pair field by field, floats exactly as serialised, excluding only
the top-level `provenance` block, and refuses if it cannot see an injected 1e-9 relative change to one value:

| record | committed at | re-run at `8fedbf4` |
|---|---|---|
| `knownanswer.json` | `8b31320` | IDENTICAL: 195 / 195, 14 out-of-domain refusals, max abs E 0.008969, max abs ln tau 0.110528, 0 zero-drop read as DROP |
| `controls.json` | `8b31320` | IDENTICAL: clean and clean-again hold every property, 7 / 7 mutants CAUGHT, same matrix |
| `development.json` | `b6d216e` | IDENTICAL: same classes and reasons on all 7 rows, same E and tau to the last serialised digit |
| `fit.json` | `bd33dbe` (committed in `41f5ffb`) | IDENTICAL: REFUSED, not-identifiable, 3 usable development rows < 4 |

No recorded number changed, so the wrong-then-right count in section 4 is unchanged by the code changes. The Judge independently
re-estimated the 7 development takes at `8fedbf4` and got the same result.
- **`describe-development.json`** (post-hoc, development takes only): both routes' figures for every development take, including the
  refused ones (the 808 column in the sound summary). HT75 measures cleanly (E 0.049, tau 22 ms) but has no tuning ratio.

**Measured effect versus mechanism hypothesis.** Kept apart, per rule 7.

- **Measured** (3 development rows, not confirmed): the 808's drop is smaller at onset and relaxes about 2-4x slower than the shipped
  staircase.
- **Hypothesis** (`mechanism=inferred`, not tested): the 808's drop relaxes with the ring's own amplitude rather than on a fixed schedule.
  Its tau tracks about 0.55-0.6x each tom's amplitude decay (LT 50-55 ms against tau_a 87.6 ms, MT 34 against 57.7, HT75 22 against 41.7).
  That would fit the reference's "amplitude-dependent and gradual". Nothing here measured the amplitude dependence.

## 4. Wrong-then-right: 7 before the corpus, 1 found on it and not yet right

All 7 pre-corpus errors were caught by the known answers or by our own render, not by inspection:

1. **Route A band-pass (0.55-1.85 fc).** LT's E 0.15 read 0.095-0.12, depending on onset phase.
2. **High-pass edge at 0.25 fc.** A sin-phase onset's DC content became a slow offset that read as curvature: E 0.15 -> 0.12 at sin phase,
   while cos phase was unaffected. Fixed by removing route A's filter and fitting a DC term.
3. **H2 at -35 dB.** It biased E by up to 0.011 (82 Hz, E 0.04). Fixed with H1..H3 locked to theta in A and zig-zag columns in B.
4. **White-residual se.** It understated the harsh-family error by up to 13x (e 0.013, se 0.001). Fixed with the uncertainty floor.
5. **The carry check.** It required the staircase tone itself to pass route agreement. A six-step staircase is not an exponential, so
   our own HT staircase was refused. Fixed by comparing route by route.
6. **Agreement tolerance of 0.006.** That was tighter than the stated uncertainty, and it refused our shipped HT staircase (A 0.065 vs
   B 0.075). Set to 0.015, the floor.
7. **Detection on se alone.** It called a 0.3-cent deterministic drift in our *no-drop* render significant, so the render was refused as
   tau-at-bound. Fixed with an absolute floor E_MIN_DETECT of 0.003 (~5 cents).

**Not yet right, found on the development takes.** The agreement rule compares route *classes* before route *values*.

- On HT50 the two routes agree on E to 0.0006 (A 0.0310, B 0.0304) and on tau to 0.5 ms. Route B's se of 0.017, however, puts it under
  the detection threshold, so B reads NO-DROP and the take is refused.
- HT25 is the same case.
- HT50's refusal then removes the tuning reference for HT25 and HT75.
- That rule alone accounts for 3 of the 4 refused development rows.

It is not corrected here, because the estimator is frozen by `prereg.json`. A successor should treat an under-powered NO-DROP as compatible
when the other route's E lies inside its E_upper, pre-register that change, and re-run development before reading the reserved takes.

**That rule may not rescue HT25.** HT50's routes agree on tau (19.1 against 19.6 ms), but HT25's do not: A 15.7 ms, B 25.7 ms
(`describe-development.json`), ln(25.7/15.7) = 0.49, which is above `AGREE_LN_TAU` 0.35. Whether HT25 is rescued depends on whether
tau is still compared when route B is NO-DROP. The successor must decide that, and state it, when it pre-registers. It must not be
decided after seeing which choice rescues the take.

**Wrong in this record, caught in review, not by a control.** Three statements in earlier versions of this README were wrong. None of
them was a measured number from the estimator.

1. The sound summary said the 808's excess "crosses above ours at roughly 20-25 ms". Recomputed from the record's fitted E and tau,
   the crossings are 20.7 / 13.1 / 31.3 ms (LT50 / LT75 / MT50). Only LT50 fell inside the stated range.
2. Section 3 said the estimator was unchanged apart from `b8c1fb0`. Two later commits, `1a697f1` and `8fedbf4`, changed the probe.
   They are now disclosed, and the records were re-run at `8fedbf4` with identical results (section 3).
3. Section 6 gave a test count of 150. That count was stale after those two commits added tests (section 6 has the current count).

## 5. Class search (`git grep` on `origin/main` at `6d5b335`)

**Onset-frequency excess read as a real drop:**

- `tools/probes/tom_conga_gate.py` `diagnose` prints first-frame cents. It is labelled diagnostic, and its known answer pins the artefact.
- `tools/test_bd_pitch_predeclaration.py::test_onset_phase_alone_cannot_satisfy_the_rule_at_declared_knobs` already guards BD's
  `glide_cents` against it.
- `model/tom_pitch_probe.py`, the From Mars instrument behind the shipped law, uses same-direction zero crossings. Those are
  phase-insensitive, so it is not this class.
- No other instance was found.

**Scheduling horizon read as a time constant:**

- `fpga/link_budget.py`, `fpga/fixtures.py`, `fpga/spi_host.py`, `fpga/verify_fixture.py` and `fpga/test_spi_host.py` use
  `TOM_DROP_MS` as a schedule width, which is correct.
- `model/drums_fx.py:854` and its provenance entry (`:1343`) compare the From Mars tau of 24.5 ms with the *continuous* law's 20 ms. The
  realised staircase reads about 15-16 ms by this estimator. That is a related instance, law-as-shipped, and the comment was not edited
  (read-only here).
- `test_horizon_is_not_the_time_constant` now pins the realised tau below TOM_DROP_MS / 3.

## 6. What was and was not run

- **Run on this loom worker:**
  - At `8fedbf4` (the probe's final code), `tools/probes/test_tom_pitch_drop_fischer.py` with `GF180_TR808_FISCHER=/tmp/fischer-592`:
    164 passed (164 collected).
  - `tools/probes/test_tom_conga_gate.py` without the corpus: 28 passed, 4 skipped.
  - `tools/probes/test_tpd592_rerun_compare.py`: 7 passed.
  - The `knownanswer`, `controls`, `measure`, `fit`, `confirm` and `describe` commands as recorded. Each record carries its commit, a
    clean-tree check at the start and end, the prereg sha256, the argv, and the Python/numpy/scipy versions. `knownanswer`,
    `controls`, `measure` and `fit` were re-run at `8fedbf4` into `rerun-8fedbf4/` (section 3).
- **Known pre-existing issue on `origin/main`, not addressed here.** With `GF180_TR808_FISCHER` pointing at a tom-only corpus,
  `tools/probes/test_tom_conga_gate.py` fails 3 tests with `FileNotFoundError: mc8/MC50.WAV` instead of skipping them, because the
  conga tests skip on the environment variable rather than on each take. That is why the count above is without the corpus.
- **Not run:** `make verify`, `make controls` and `make verify-full`. No RTL or decoded-output runs.
  - They belong on the pinned build box, which this worker has no access to.
  - Nothing in the model, RTL or image changed.
  - Their absence is a missing verdict, not evidence about this record.
- **No production dial is proposed,** so nothing is registered under `docs/sensitivity/registry.json`.
