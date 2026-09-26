# D12A clap burst/tail balance: reproduction and diagnosis

This folder is diagnostic only. No change was made to the drum model, the
scorer or `docs/scorecard/results/D12A.json`. The candidate renders are register
images built inside `tools/clap_d12a_probe.py` and then discarded. They were not
promoted.

| file | what |
|---|---|
| `probe.json` | every number below, written by `tools/clap_d12a_probe.py` |
| `probe-run.log` | that run's console output and exit status |
| `tests-run_all.json` / `.log` | `tools/run_all.py` verdicts: probe tests, `make reference-integration`, the known-answer CLI |
| `start-red.log` | the probe's closed-form tests run against mutated scorer estimators (`tools/clap_d12a_start_red.py`) |

## 1. Basis (frozen)

- Tree: `origin/main` at `022bd7f` plus this branch's probe files only. The
  scorer inputs are hashed in `probe.json` → `basis.scorer_inputs`, and
  `model/drums_fx.py` is `4a0177412ff464a3`, the same file the committed record
  was rendered from.
- Reference: Fischer corpus at `85fbecf1bec3`. All 116 files match
  `tr808-fischer-85fbecf.sha256`, with 0 mismatches, and the probe refuses to run
  on any mismatch. `cp8/CP.WAV` is `376429bb81cb48d1…`. **It is the only clap
  recording in the corpus**, so the reference's own strike-to-strike variability
  cannot be measured.
- Measurement: `run_case.py` `DRUM_PLAN["CP"]`. The burst/tail ratio is
  `_early_late_db(0.030, 0.200)`.

## 2. Reproduction: exact

`run_case.run_drum_case` on the current tree reproduces every committed field
exactly: value, reference, error, tolerance and valid for all three metrics,
plus the windowing and diagnostics blocks. There are 0 mismatches. The only
difference is the `purpose` key, which was added to records after this one was
written.

| metric | ours | reference | error | tol | verdict |
|---|---|---|---|---|---|
| Burst timing | 18.8542 ms | 35.805 | −16.95 | 17.90 | pass (margin 0.95 ms) |
| burst/tail ratio | 11.2831 dB | −2.9726 | **+14.2557** | 3.0 | **fail** |
| decay (T20) | 101.094 ms | 198.99 | −97.90 | 99.50 | pass (margin 1.6 ms) |

Audio: the committed record wrote no WAV (`--no-audio`), so it carries no audio
hash to compare against. The render is deterministic: two renders were
identical, and the probe's override renderer at the default registers is
bit-identical to `render_drum_solo`. Our render's hashes are now recorded:
float64 `03684093c3d559b9…` and 16-bit PCM `5a68e7f84a0d4f6b…`.

## 3. Segmentation: consistent, and not the cause

- **Onset.** Both sides are cut at the same 2 %-of-peak crossing, with t = 0 set
  1 ms before it. Moving the threshold to 1 % or 5 % changes neither side's
  ratio in the third decimal. The reference's onset at sample 8 is handled by
  manufactured lead, as `windowing` records.
- **Split sweep** (`segmentation.split_sweep`). The error is at least +4.75 dB for
  every split from 20 to 80 ms, and it is +4.75 dB at 80 ms, which is after
  every burst on both sides. A different boundary would not remove the failure.
- **What the 30 ms split means on this machine.** The reference's burst train is
  not "three bursts in 30 ms". At 1 ms resolution it shows three short bursts
  starting at about 1, 12 and 24 ms, each with an amplitude τ of about 3–4 ms.
  These are followed by a **fourth, sustained event from about 31 ms**. It
  plateaus near −9 dB until about 42 ms, then decays with τ ≈ 18–24 ms, and it
  is the loudest part of the whole clap. The 30 ms split therefore puts that
  final burst in the "tail" window of the reference. Our model has three
  decreasing 4 ms bursts ending by about 25 ms, and nothing but the tail after
  30 ms.

## 4. Absolute energies: the late side is weak, not the burst loud

The Fischer set pinned LEVEL at maximum, so cross-recording gain means nothing.
Original-gain energies (dB re FS²·s) are recorded in `probe.json` but cannot be
compared. The ratio has no scale, so "which side is wrong" depends on an anchor.
Two anchors were used, and the conclusion is drawn only where they agree.

| window | ours, peak-norm. dB | ref, peak-norm. dB | ours − ref |
|---|---|---|---|
| 0–30 ms (bursts) | −27.24 | −30.42 | +3.18 |
| 30–200 ms ("tail") | −38.53 | −27.45 | **−11.08** |
| 30–50 ms | −40.51 | −28.40 | **−12.11** |
| 50–200 ms | −42.89 | −34.52 | −8.37 |
| 80–200 ms | −48.33 | −42.38 | −5.95 |

- Peak anchor: the late window is 11.1 dB short and the early window is 3.2 dB
  hot. Anchored on the first 30 ms, the late window is 14.3 dB short and the
  early window is 0 dB off. **Under both anchors the late side accounts for most
  of the error (≥ 11 dB). At most 3.2 dB is attributable to the bursts.**
- **Counterfactual window swaps** (`decomposition`). Giving ours the reference's
  30–50 ms energy reduces the error from 14.26 to **3.98 dB**. Giving it the
  reference's 50–200 ms energy reduces it only to 9.28 dB. So the missing final
  burst is the dominant term, and the weak tail is the secondary one.
- **Tail proper.** On 80–200 ms, where neither side has bursts, the reference's
  amplitude τ is **80.2 ms** and ours is **44.9 ms**, with fit residuals of 2.5
  and 1.2 dB. The fit is validated on synthetic exponentials to within 0.01 ms.
  The model's 47 ms is the R348·C138 component estimate. The machine measures
  about 80 ms.

**Diagnosis.** The error comes from the late envelope. About 10 dB of the
14.26 dB is the missing slow final burst of the burst VCA, which starts at about
31 ms and so sits in the reference's late window. Most of the rest is the tail's
time constant (45 ms against 80 ms). The bursts before 30 ms are within about
3 dB. The segmentation is consistent across both sides.

## 5. Sensitivity (diagnostic renders)

The output path is `tanh(noise) · (env_burst + env_tail)`, which is linear in
the envelopes. The probe asserts that burst-only plus tail-only equals the full
render to within 2 LSB. The tail-peak predictions made from the component
renders **before** each render matched the measured ratio to 3 decimals: 9.782,
8.498 and 14.741 dB.

| perturbation | ratio (err) | burst span | T20 |
|---|---|---|---|
| baseline | 11.28 (+14.26) | 18.85 ok | 101.1 ok |
| tail peak ×2 | 8.50 (+11.47) | 17.02 **FAIL** | 108.1 ok |
| tail τ 47→90 ms | 6.81 (+9.79) | 18.33 ok | 199.4 ok |
| 4 strikes, period 511 | 6.49 (+9.47) | 31.94 ok | 84.5 **FAIL** |
| 4 strikes p511 + tail τ 90 ms, peak 0.33 | 3.37 (+6.35) | 31.92 ok | 200.7 ok |
| final-burst τ 20 ms alone | 2.42 (+5.39) | 31.94 ok | 72.7 **FAIL** |
| final-burst τ 38.5 ms alone | 0.41 (+3.38) | 56.12 **FAIL** | 96.1 **FAIL** |

No combination of registers that leaves the final burst unchanged gets inside
6 dB. The final burst alone fixes most of the ratio but drops T20 out of
tolerance, because the curve falls faster once the late energy ends. It needs the
measured tail τ alongside it.

## 6. Known-answer control

`synthetic_clap()` builds a sign-alternating signal, so x² equals the envelope
squared exactly. It has re-struck exponential bursts and an exponential tail, and
its window energies are closed-form geometric sums. It is run through the
scorer's own `prepare()` and `_early_late_db` at 44.1 and 48 kHz, on 8 signals
that include a 4-burst train whose tail starts after the split. Every error was
≤ 1e-5 dB against a 0.01 dB tolerance.

Every one of four estimator mutants is killed by at least one signal:
- energy → amplitude
- split 30 → 40 ms
- 48 kHz arithmetic applied on a 44.1 kHz file
- t = 0 at the peak instead of the onset

An injected **signal** defect (tail ×2) moves the ratio by −5.9903 dB, which is
exactly its closed form, and it is caught against the baseline expectation.
Start-red: the closed-form tests fail 8 of 8 against mutated scorer estimators
(`start-red.log`).

## 7. Nuisance variation, and a finding about the passes

The LFSR runs freely, so moving the strike by whole frames changes only the
noise under the envelope. This was done over 8 offsets.

| config | ratio dB | ratio pass | burst span ms | span pass | T20 ms | T20 pass |
|---|---|---|---|---|---|---|
| baseline | 10.19 … 11.28 | 0/8 | 18.67 … 20.00 | 8/8 | 88.9 … 101.1 | **3/8** |
| C1 final τ 30 ms | −0.53 … 0.61 | 3/8 | 32.0 … 56.1 | 5/8 | 122.7 … 135.2 | 8/8 |
| C2 final τ 38.5 ms | −1.31 … −0.23 | 8/8 | 39.4 … 73.7 | **2/8** | 124.8 … 136.8 | 8/8 |
| C3 final τ 45 ms | −1.80 … −0.74 | 8/8 | 39.4 … 78.8 | **2/8** | 129.5 … 141.9 | 8/8 |

- **The committed "decay passes narrowly" holds for 3 of 8 noise realisations.**
  The committed single strike is one of those three. That pass is a property of
  one noise phase, not of the model.
- **The Burst timing estimator is not qualified for a clap with a slow final
  burst.** The estimator is `envelope_bursts` with a 2 dB dip on a 4 ms RMS
  envelope over 0–120 ms. Once any candidate supplies the final burst, it counts
  noise fluctuations inside that burst as extra bursts, and the span wanders
  from 32 to 79 ms with the noise phase. The reference is a single realisation
  of a signal of the same shape, so its 35.8 ms is subject to the same
  fluctuation, and it cannot be repeated from this corpus.

## 8. Proposed next change (one; not implemented)

**Mechanism: the burst VCA's final strike decays slowly.** On the machine,
Fig. 13's sawtooth oscillator stops mid-ramp and the last ramp completes
(tr808-reference §7). The change adds a fourth strike (`bursts=3, period=511`,
which places strikes at 0, 10.6, 21.3 and 31.9 ms) and a **host-sequenced write
of `E_CPBURST`'s RATE at the fourth strike's frame**. That is the same kind of
host write as `bd_attack_writes` and `tom_pitch_drop_writes`, and it needs no
change to the block. The tail τ is set to its **measured 80 ms**. That is a
coupled precondition, not a candidate dimension: §5 shows that without it the
final burst breaks T20.

Frozen candidates, differing only in final-strike τ:

- **C1** 30 ms
- **C2** 38.5 ms (C144·R365)
- **C3** 45 ms

**Stated tradeoff.** The model's fourth strike is (13/16)³ = 0.54 of the first,
because `BURST_C` is fixed. On the reference it is the loudest. The candidates
therefore make up the energy with a τ 1.3–2.5 times the reference's measured
18–24 ms. That is an energy match with the wrong shape, and it has to be
accepted as a product decision or replaced by a structural final-strike level.
The structural option is a block change and is outside this bound.

**Preservation requirements.** Each is judged as a pass-rate over the same 8
noise offsets, never on one strike, and must be at least the baseline's rate:

- burst timing at 8/8
- decay T20 at 3/8, which C1–C3 all exceed at 8/8
- accent 0.5 / 1 / 2: the ratio moves monotonically as it does now, and there
  are 0 rail samples at accent 2 (candidates peak at 0.390 FS against the
  baseline's 0.389)
- MA, which shares circuit 6, is unchanged: the rate write is emitted only when
  CP is the selected sound
- the burst RATE is restored at every CP hit frame, since a register written at
  the fourth strike otherwise persists into the next hit's first three bursts.
  The probe renders one hit, so it does not exercise this, and a two-hit test
  is required
- every other voice is bit-identical

Promotion further needs exact RTL agreement with a mid-sound envelope RATE
write, and release binding.

**Before/after test.** Run `tools/run_case.py D12A` plus
`tools/clap_d12a_probe.py` (nuisance block) on baseline and candidate.

- The target is a burst/tail pass-rate of 8/8, against 0/8 now.
- The preservation criteria are the pass-rates listed above.
- The control is the known-answer suite plus the tail ×2 injection, which must
  stay green and caught.

### Blocker, and where this stops

**Measurement qualification fails for a required preservation property.**
Burst timing cannot certify any candidate that implements this mechanism. The
estimator is qualified at best 2/8 on C2 and C3, the two candidates that fix the
ratio, and the reference's own 35.8 ms is a single realisation of the same
fragile reading. Following plan075 Milestone 3 and T7, this task stops here and
records the blocker. It does not start an estimator project.

The one discriminating measurement that would unblock it is the burst-timing
estimator run on a synthetic clap with a known strike schedule and a slow
noise-carrier final burst, across noise realisations. That run either qualifies
the estimator in this regime or confirms the refusal. It belongs to the scorer
owner, as a versioned rubric decision, not to this sound task.

## Wrong-then-right, this session (7)

These were caught by controls or refusals, not by inspection.

1. The synthetic 4-burst known-answer signal overlapped its own tail, and read
   4.7 dB "wrong". The fault was in the generator. It now refuses overlap.
2. The tail ×2 injection was first expected to move the ratio by −6.02 dB. The
   true value is −5.99 dB, because a sliver of the last burst lies past the
   split. The closed form is now used.
3. The original-gain energies indexed before sample 0 of the reference: the
   onset is at sample 8, the trim is 44 samples, and the lead is manufactured.
   The probe crashed rather than reporting.
4. `tail_fit`'s time-constant line was written wrongly once and overwritten
   before use.
5. Known-answer mutants were first required to be caught on every signal. The
   "48 kHz" mutant is the identity on a 48 kHz signal. The criterion is now
   mutation-kill: each mutant must be caught by at least one signal.
6. The committed decay pass was taken as a property of the model. It holds for
   only 3 of 8 noise realisations.
7. One exploratory render showed "Burst timing 31.9 ms, pass" with three
   strikes. The estimator was counting a noise dip as a fourth burst, which is
   the qualification failure in §7.
