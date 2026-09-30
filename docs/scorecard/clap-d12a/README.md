# D12A clap burst/tail balance: reproduction and diagnosis

This folder is diagnostic only. No change was made to the drum model, the
scorer or `docs/scorecard/results/D12A.json`. The experiment renders (C1–C3)
are register images built inside `tools/clap_d12a_probe.py` and then discarded.
**They are development experiments on development data, not candidates for
promotion** (see §8).

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

## 4. Window energies: late energy is deficient under the stated normalisation

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
  of the error (≥ 11 dB).** Both anchors are internal to each recording: each
  side is normalised to its own peak or its own first 30 ms. This is **not** an
  absolute calibration. It does not show that our early (burst) amplitude is
  correct, only that, relative to each side's own peak, the late energy is
  deficient and the early energy differs by at most about 3 dB.
- **Counterfactual window swaps** (`decomposition`). Giving ours the reference's
  30–50 ms energy reduces the error from 14.26 to **3.98 dB**. Giving it the
  reference's 50–200 ms energy reduces it only to 9.28 dB. So the missing final
  burst is the dominant term, and the weak tail is the secondary one.
- **Tail proper.** On 80–200 ms, where neither side has bursts, the reference's
  amplitude τ is **80.2 ms** and ours is **44.9 ms**, with fit residuals of 2.5
  and 1.2 dB. The fit is validated on synthetic exponentials to within 0.01 ms.
  The model's 47 ms is the R348·C138 component estimate. The machine measures
  about 80 ms.

**Diagnosis (scoped).** Under the stated normalisation (each side to its own
peak; Fischer pinned LEVEL at maximum, so there is no absolute level), the ratio
error is carried by deficient late energy. About 10 dB of the
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

| config (C1–C3: development experiments) | ratio dB | ratio pass | burst span ms | span pass | T20 ms | T20 pass |
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
  envelope over 0–120 ms. Once any experiment supplies the final burst, it counts
  noise fluctuations inside that burst as extra bursts, and the span wanders
  from 32 to 79 ms with the noise phase. The reference is a single realisation
  of a signal of the same shape, so its 35.8 ms is subject to the same
  fluctuation, and it cannot be repeated from this corpus.

## 8. Proposed next step (one; not implemented)

**Mechanism under test: the burst VCA's final strike carries the late energy.** On the machine,
Fig. 13's sawtooth oscillator stops mid-ramp and the last ramp completes
(tr808-reference §7). The change adds a fourth strike (`bursts=3, period=511`,
which places strikes at 0, 10.6, 21.3 and 31.9 ms) and a **host-sequenced write
of `E_CPBURST`'s RATE at the fourth strike's frame**. That is the same kind of
host write as `bd_attack_writes` and `tom_pitch_drop_writes`, and it needs no
change to the block. The tail τ is set to its **measured 80 ms**. That is a
coupled precondition, not a candidate dimension: §5 shows that without it the
final burst breaks T20.

**Development experiments (development data, not promotion candidates)**,
differing only in final-strike τ:

- **C1** 30 ms
- **C2** 38.5 ms (C144·R365)
- **C3** 45 ms

These compensate for a weak final strike by extending its decay. They are
evidence that the late window is where the ratio lives, not a sound to ship.

**Implementation constraint (found in review, confirmed in source).**
- The envelope reduces each internal re-strike to 13/16 of the previous strike:
  `strike <- (strike * BURST_C) >> 16` in `EnvFx.frame` (`model/drums_fx.py`), and
  `mul_a <= strike`, `mul_b <= BURST_C` in `rtl-sketch/drum_dp.v`. The fourth
  strike is therefore (13/16)³ ≈ 54 % of the first. On the reference it is the
  loudest.
- PEAK is read only when the envelope FIRES (`e_fired ? e_peak` in
  `drum_dp.v`; `level <- peak * accent` on fire in the model). **Writing PEAK
  mid-note does not change the stored strike level.** No register sequence can
  raise the final strike.
- RATE, by contrast, is read on every decay step in both the model and
  `drum_dp.v` (`mul_b <= ... e_rate`), so the host rate rewrite used by C1–C3
  does take effect mid-note in both. It was checked by reading the source only:
  **exact RTL agreement for a mid-note RATE write has not been simulated.**
- Consequence: a rate rewrite can only lengthen a strike that is about 5.4 dB
  too quiet, which is why C1–C3 need a τ 1.3–2.5 times the reference's measured
  18–24 ms. A final strike at the reference's relative level **needs an explicit
  implementation**, such as a separate final-strike level or multiplier. That is
  a block change and must be scoped as its own bounded experiment. The
  host-sequenced rate rewrite alone is not a faithful implementation of this
  mechanism.

**Preservation requirements for any later final-strike experiment.** Each is judged as a pass-rate over the same 8
noise offsets, never on one strike, and must be at least the baseline's rate:

- burst timing at 8/8
- decay T20 at 3/8 (the development experiments C1–C3 reached 8/8)
- accent 0.5 / 1 / 2: the ratio moves monotonically as it does now, and there
  are 0 rail samples at accent 2 (C1–C3 peak at 0.390 FS against the
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
`tools/clap_d12a_probe.py` (nuisance block) on the baseline and the experiment.

- The target is a burst/tail pass-rate of 8/8, against 0/8 now.
- The preservation criteria are the pass-rates listed above.
- The control is the known-answer suite plus the tail ×2 injection, which must
  stay green and caught.

### Blocker, and where this stops

**Measurement qualification fails for a required preservation property.**
Burst timing cannot certify any render that has a slow final burst. It passes
at best 2/8 on C2 and C3, the two experiments that fix the ratio, and the reference's own 35.8 ms is a single realisation of the same
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

---

# plan081 B and C (branch `measure/d12a-final-strike`)

All heavy runs were on the build box, and the records are in this folder:

| file | what |
|---|---|
| `burst-timing-qual.json` | B's per-condition statistics |
| `final-strike.json` | C's full record |
| `c-run_all.json` / `.log` | the committed C run at `50214bb` |
| `b-and-first-c-run_all.json` / `.log` | B's verdict run, plus a first C run at `4059834` |

The first C run is identical to the committed one in every number. The only
differences are the programmed-strike lists, which it printed with a doubled
first strike (see wrong-then-right 8), and the basis head.

## 9. B: burst-timing apparatus check. Verdict: UNQUALIFIED for this domain

**Three quantities, kept apart.**

1. **Programmed strike times.** These are the excitation schedule, read from
   model or RTL envelope state. They are exact for our renders and unknown for
   the reference.
2. **Peaks in a noisy envelope.** This is what `_burst_span_ms` returns: the
   first-to-last span of peaks that `envelope_bursts` accepts in a 4 ms RMS
   envelope.
3. **The audible transient's duration or energy distribution.** Not measured
   here.

B asked whether quantity 2 recovers quantity 1's span and count.

**Generator.** It is independent of the detector and of the model: Gaussian
noise (seeded PCG64), a float Butterworth band-pass around 1071 Hz, and a
piecewise re-strike envelope built from a declared strike list. There were 24
realisations per condition, at 44.1 and 48 kHz. Truth is the strike list, never
the peak finder's output.

**Conditions:**
- three short strikes
- four short strikes
- three short strikes plus a sustained final strike with τ 20 ms or τ 40 ms
- three short strikes plus a slow tail and no 4th trigger

**Controls:** missing final strike, missing 2nd strike, final strike delayed
by 8 ms, and an extra strike at 55 ms.

**Budget.** Frozen before the run: |median error| ≤ 2 ms, 95th percentile ≤ 5 ms,
and at most 1 false extra strike in 24.

| detector | result |
|---|---|
| official (2 dB dip) | **fails every condition at both rates**. Baseline-like S3: p95 11 ms, 5–6/24 strikes missed, 2–3/24 extras. Sustained final τ 20 ms: median +12 to +19 ms, 14–15/24 false extras. τ 40 ms: median +29 ms, 20–23/24 extras |
| pre-declared correction (6 dB dip) | fails every condition. It trades extras for misses (up to 22/24 missed) |
| official on a **noise-free** carrier (reference point) | exact everywhere: error ≤ 0.011 ms, 0 extra, 0 missed |

- **The detector reads envelope shapes correctly. Noise is what breaks it.** No
  small correction qualifies, so per the stop rule "Burst timing" is marked
  **unqualified** for noise-excited clap envelopes. No estimator work was
  started.
- **Consequence for preservation.** The baseline's 8/8 "Burst timing pass" was
  produced by an estimator this check shows to be unsuitable. Under plan082
  that pass is **not a preservation obligation**. Timing is reported in C
  below, labelled unqualified, and is not used as a gate.
- **Limitation.** The generator's carrier is un-saturated Gaussian noise. Our
  model's path has a tanh, which lowers the envelope's fluctuation; that may be
  why our renders read more stably (18.7–20.0 ms over 8 offsets) than S3 does.
  The reference's analogue "distorted noise" has unknown crest statistics, so
  model stability does not qualify the detector on the reference either.
- **Narrow follow-up (one).** Timing should be two separate checks, as plan081
  says: exact programmed strike times from model or RTL state (C reports them),
  and a declared acoustic envelope-shape measurement, such as the anchored
  window energies and late-event level and duration below. A replacement peak
  detector is not the follow-up.

## 10. C: one bounded final-strike experiment. Result: L2 selected and confirmed; model improvement, promotion incomplete

**Mechanism.** `FinalStrikeEnv`, a subclass of `EnvFx` that lives only in
`tools/clap_final_strike_experiment.py`, gives the **last** re-strike an
explicit level (`fire_level × L`) and its own decay rate. Every other strike
keeps the shipped 13/16 rule. Precondition: with no final level set, the
subclass renders bit-identically to `render_drum_solo("CP")`. This was
asserted, and it passed.

**Frozen at `4059834`, before any render.**
- Conditions: B0; T (tail τ 80 ms); L1, L2 and L3 (T plus 4 strikes at period
  511 plus a final strike at 0.75, 1.00 and 1.25 of the first, with τ 20 ms);
  C2 archived.
- Development offsets: the #253 set. Fresh offsets:
  `3301 5557 8803 10501 14009 16411 18503 20011`.
- Anchor: each side's own 0–30 ms energy at **fixed gain**. There is no
  per-candidate peak normalisation.
- Selection rule: stated in the tool's docstring.
- **Selection used DEV only.** The fresh set was rendered afterwards, in the
  same process, for B0 and the one selected level only, and no human looked at
  results in between. The fresh set is therefore untouched confirmation data.

**DEV, 8 offsets.** Ratio and decay are qualified; timing is shown but is
unqualified (B).

| condition | ratio pass | decay pass | timing* | median \|ratio err\| dB | anchored − ref: 30–50 / 50–80 / 80–200 dB | late event re early peak, −20 dB duration | peak FS @ acc 1 / 2 | rail |
|---|---|---|---|---|---|---|---|---|
| B0 baseline | 0/8 | 3/8 | 8/8 | 14.16 | −15.06 / −12.20 / −8.69 | −9.2 dB, 74 ms (tail) | 0.243 / 0.389 | 0 |
| T tail-only | 0/8 | 8/8 | 8/8 | 10.32 | −12.93 / −7.86 / −1.03 | −7.8 dB, 122 ms | 0.243 / 0.390 | 0 |
| L1 final 0.75 | 7/8 | 8/8 | 7/8 | 2.59 | **−3.27** / −0.95 / +0.94 | +1.2 dB, 67 ms | 0.243 / 0.390 | 0 |
| **L2 final 1.00** | **8/8** | **8/8** | 7/8 | 0.89 | −1.40 / +0.53 / +1.56 | +3.2 dB, 53 ms | 0.274 / 0.425 | 0 |
| L3 final 1.25 | 8/8 | 7/8 | 7/8 | 0.55 | +0.14 / +1.79 / +2.15 | +4.8 dB, 53 ms | 0.330 / 0.425 | 0 |
| C2 archived | 8/8 | 8/8 | 2/8 | 2.41 | −4.50 / +0.76 / **+4.64** | −0.6 dB, 102 ms | 0.243 / 0.390 | 0 |
| *reference* | — | — | — | — | (anchored: +2.02 / −4.87 / −11.96) | +2.2 dB at 38.8 ms, 46 ms | — | — |

\* Timing is unqualified (section 9) and is not a gate.

- **Programmed strike times, from envelope state:**
  - B0 and T: 0, 10.0, 20.0 ms
  - L1–L3 and C2: 0, 10.646, 21.292, 31.938 ms
- **Accent response** (level-matched ratio at accents 0.5 / 1 / 2): L2 gives
  −2.14 / −2.14 / −1.74 dB, which is flat to a small tanh effect at accent 2,
  as the baseline's is.
- **Selection.**
  - L1 is ineligible: its anchored 30–50 ms window is 3.27 dB short, beyond the
    3 dB limit.
  - L2 and L3 are both eligible. L3's median error is lower by 0.34 dB, which
    is inside the 0.5 dB tie band, so the rule picks the lower level: **L2**.
- **Fresh confirmation (untouched offsets).**

  | condition | ratio | decay | median \|ratio err\| | anchored − ref: 30–50 / 50–80 / 80–200 |
  |---|---|---|---|---|
  | L2 | 8/8 | 8/8 | 0.47 dB | −1.06 / +0.92 / +1.30 dB |
  | B0 on the same offsets | 0/8 | **0/8** | 13.62 dB | −14.51 / −11.86 / −9.24 dB |

  **Confirmed.**
- **Separating the contributions.**
  - The tail alone (T) moves the error by only 3.8 dB and passes 0/8.
  - The explicit final strike supplies the remaining ~9.3 dB.
  - L2 is **not** a long-tail shift: its 80–200 ms window is +1.3 to +1.6 dB of
    the reference, against C2's +4.6 dB.
  - L2's late event lasts 53 ms, close to the reference's 46 ms. C2's lasts
    102 ms, which is the smear C2 used to get its energy.
- **Headroom.** L2 raises the accent-1 peak from 0.243 to 0.274 FS (+1.0 dB) and
  the accent-2 peak from 0.389 to 0.425 FS (+0.8 dB), with 0 rail samples at any
  accent. At accent 2, L3 and L2 peak identically at 0.4247, because the 24-bit
  envelope saturates.
- **Decay.** L2 passes 8/8 on DEV and 8/8 on fresh, against the baseline's 3/8
  and 0/8. The baseline's decay "pass" is fragile, as §7 found; L2's T20
  (109–126 ms) sits inside tolerance on both sets.

**Label: model improvement; promotion incomplete.** Burst timing is
unqualified (§9); the change exists only as an experiment-local `EnvFx`
subclass, with no production model, RTL, image or release binding. The next
step is plan081 D: an explicit final-strike implementation in model and RTL,
repeated hits and stale updates, CP/MA switching, accent and headroom checks,
deadline checks, and I2S proof. That waits for the coordinator.

## Wrong-then-right, B and C (2 more; 9 in total)

8. C's first run reported programmed strikes with a doubled first strike
   (`[0.0, 0.0, 10.0, 20.0]`). The fire frame is already a rise from 0, and the
   code also prepended 0. This was a reporting bug and did not affect any
   render. It was fixed at `50214bb` and re-run, and every other number was
   identical.
9. The first C run used `--jobs 7`, before the coordinator's cap of 4 arrived.
   The committed re-run used 4. Both were on the build box.

## Integration with main (step 0, merge `05b0406`)

- **Why #261 went red.** Its `reference-controls` and `m5a-fast` checks
  refused because `model/audio_measure.py` and `docs/scorecard/cases.csv`
  differed from origin/main. That was the stale-input guard working. The branch
  now merges origin/main (`d396964`) and #253's refreshed head (`a84d5b7`), and
  the guard was not bypassed (no `--allow-stale`).
- **What changed in those inputs, and whether it touches the clap.**
  - `model/audio_measure.py`: **docstring-only** edits to
    `moving_average_envelope` and `spectral_centroid`, from #251. No executable
    line changed.
  - `docs/scorecard/cases.csv`: only rows F2A–F2D, which gain "Playing weight",
    from #238. The D12A row is unchanged.
  - Neither touches the CP estimators or the qualification apparatus.
- **Re-checked on the build box at `05b0406`** (logs in `step0/`, every exit
  status checked):

  | check | result | exit |
  |---|---|---|
  | refprofile restore + tests | pass | 0 |
  | `reference-controls` | 2/2 PASS | 0 |
  | `make verify-fast` | 7/7 PASS: 272 + 163 + 10 + 3 pytest passed, plus 3 tools | 0 |
  | `tools/run_case.py D12A` through the normal runner | metrics identical to the official record in every value, reference, error, tolerance and valid field | 1 (a mismatch result, as expected) |
  | probe tests | pass | 0 |

- **The experiment was not re-run or re-selected.** `final-strike.json` and
  `burst-timing-qual.json` keep their original identities (`50214bb` and
  `4059834`), and their fresh-offset confirmation stands as recorded. The
  unchanged D12A rescore shows the merged measurement inputs do not move the
  clap's values.
