# Can our digital 808 be told apart from a real one?

**Short answer: yes — but for the first time not at ceiling, and now over
all sixteen sounds rather than eight. Six of the sixteen the corpus cannot
adjudicate at all, and for those the only honest output is a refusal.**

The useful part is not that answer. It is *how far* apart, in a unit the
design team can act on, and *which* sounds no freely licensed reference
material can currently settle.

Run it:

```
git clone --depth 1 https://github.com/tidalcycles/sounds-tr808-fischer /tmp/tr808-ref
.venv/bin/python model/discrimination_run.py --refs /tmp/tr808-ref \
    --out docs/img/discrimination --json /tmp/discrimination.json
.venv/bin/python model/discrimination_run.py --refs /tmp/tr808-ref \
    --sounds 16 --features both --json docs/discrimination-results.json
.venv/bin/python model/discrimination_trajectory.py --refs /tmp/tr808-ref
.venv/bin/python -m pytest model/test_discrimination.py \
    model/discrimination_features.py model/discrimination_trajectory.py -q
```

> ### Re-run 2026-09-18 — this section replaces the numbers the scorecard said
> ### must not be quoted
>
> `--sounds 8 --features base` reproduces the published invocation exactly:
> same split hash `6738610a454806f8`, same per-voice distances. Everything
> below was measured on branch `measure-discrimination-current`, clean, at
> `c752145` + this branch's harness commits; corpus
> `tidalcycles/sounds-tr808-fischer` `85fbecf`, 116 WAVs,
> sha256 `e3ad2d77a79cda4a`; sixteen-sound split hash `ab381e78f9f4cc41`.
>
> **The measurement path this study uses does not touch PR #132.**
> `test_discrimination.py` imports nothing from `model/audio_measure.py`: its
> features are its own log-mel/MFCC code and its diagnostics its own
> `measure_tau`, `measure_f0`, `band_share`, `partial_ratio`. **No result here
> routes through `band_energy`, so the windowing fix worth up to 6 dB does not
> apply to any number in this document.** That is a property to re-check, not
> to assume, if the harness ever imports the shared estimators.
>
> **Three of the harness's own numbers were withdrawn by this re-run, and one
> of them affects every accuracy this study has ever published.** See §1.1.

`model/test_discrimination.py` is the machinery and its self-tests,
`model/discrimination_run.py` the reproducible script. The Minimoog half (§8)
is `model/reference_compare.py` + `model/reference_rigs.py`, ground-truthed by
`model/test_reference_compare.py`; `model/moog_probe.py` is the older
settings-independent probe and **two of its published numbers were withdrawn
on 2026-09-18** — see §8.1 before quoting anything it prints.

---

## 1. Scorecard

| | |
|---|---|
| model revision rendered | `c752145` (clean) + this branch's harness commits, model sha `3cf0210cca1aef01`. Eleven circuits, sixteen sounds. |
| reference | Fischer/Technopolis 1994, CC0-1.0 via TidalCycles, real TR-808 **s/n 103852**, individual voice outputs, 16-bit/44.1 kHz |
| corpus | 116 WAVs, sha256 `e3ad2d77a79cda4a`, upstream `85fbecf` |
| unique source recordings | **116** — all sixteen sounds, the whole set (the eight-voice study used 68 of them) |
| unique knob settings | 116 — **exactly one take per setting** |
| law-fitting settings | 54 (knobs at 0.0 / 5.0 / 10.0) |
| held-out settings | **62** (any knob at 2.5 or 7.5) |
| split hash | `ab381e78f9f4cc41` (sixteen sounds); `6738610a454806f8` (the published eight, reproduced) |
| classifier | L2 logistic regression, `C` by grouped inner CV on the fit split only |
| held-out balanced accuracy, arm `ours`, 320 columns | **0.895** [0.83, 0.94] |
| paired ABX | **56 / 62** |
| held-out balanced accuracy, arm `ours`, 511 columns | **0.984** [0.94, 1.00], ABX 62/62 |
| positive controls | all pass (§4) |
| voices with **no possible knob-equivalent** | **6 of 16** — CH, CP, CB, RS, CL, MA |
| verdict | **known defect remains** (BD, SD, CY); **no verdict — underpowered** (LT, MT, HT, LC, MC, HC, OH); **REFUSED — corpus cannot test** (CH, CP, CB, RS, CL, MA) |

**The pooled accuracy is no longer 1.000.** On the study's own 320 columns
the discriminator now misses 6 of 62 paired ABX trials and lands at 0.895.
Every previous run of this study was at ceiling, so this is the first time
the number carries information at all.

---

### 1.1 What this re-run withdrew from the harness itself

**Nothing below was found by inspection; each was found by running the thing
and watching a control fail.**

| withdrawn | what it was | what it is |
|---|---|---|
| **every balanced accuracy, CI and ABX count this study has published** | `discriminate` grouped the inner CV by `hash(c.rec) % (1 << 31)`. Python salts `hash()` on `str` per process, so the folds differed on every run and `C` was chosen by accident. Caught by re-running: pooled `ours` came back **0.868 once and 0.816 the next time**, on a byte-equal split hash and byte-equal feature vectors. | a sha256 group id. Three runs at `PYTHONHASHSEED` 1/2/3 now return 0.868 [0.77,0.94] identically. **The knob-equivalents never moved — a distance has no classifier in it — which is exactly why this survived so long.** |
| **the knob-equivalent's ruler** | `distance_curve` z-scores on the pair of real populations it compares; `ours_distance` z-scores on the real-plus-ours population. Two different `mu`/`sd`, and then one is read off the other as though they shared units. | `voice_scale` freezes both to that voice's real recordings. Both readings are now printed side by side, because the frozen one saturates (§3.1) and the per-comparison one still ranks. |
| **FD-mel on the extended feature set** | the Fréchet construct is not scale-free and is computed on raw columns. With columns in ppm and in cycle counts against the log-mel columns' dB it returned **4.0e10** for `ours` against 5.5e9 for the reference's own subsets. | **REFUSED**, not printed. The `base` rows stand; there is no honest `plus` row. |
| **the jitter bucket's effect sizes** | `effect_sizes` divides by the machine's spread over the *held-out* real recordings, which for every single-knob voice is **two** files. A near-constant column then has a near-zero denominator: the run reported **4747** for the high conga. | the sd floor is resolvable rather than merely non-zero, and the column is refused below it. The permutation importance, measured on held-out accuracy, was never affected and is the statistic quoted in §5b. |

## 2. The split, and the two different experiments

The control law that turns a knob position into register values is fitted on
**FIT_KNOBS = {0.0, 5.0, 10.0}** and then frozen. Everything reported as a
result is measured at **TEST_KNOBS = {2.5, 7.5}**, which the law has never
seen. A two-knob setting is held out if *either* knob is held out, so the 16
held-out bass-drum settings also test the law's separability assumption —
that is a claim about the circuit, not only about interpolation.

That split is what separates two claims which must never be conflated:

| | what the model is given | what a pass shows |
|---|---|---|
| **emulation** | knob positions and note events only; renders blind | the engine reproduces the **instrument** |
| **sound-matching** | the target recording, and a search for parameters | the engine can **reach** that tone |

Results at the fit settings are sound-matching and are never quoted as
emulation. **Every number in §3–§6 is emulation.** The Minimoog work in §8
could only ever have been sound-matching, and is labelled so.

Split hash and per-run revision hashes are written into the `--json` file by
every run (`docs/discrimination-results.json` for the eight-voice
reproduction, `docs/discrimination-results-16.json` for the sixteen).

**The split is unchanged in form and larger in fact**: 54 fit settings and
**62 held out** across sixteen sounds, against 30 and 38 across eight. The
eight-voice split hash `6738610a454806f8` reproduces exactly, so the
before/after in §3.2 compares two runs of one experiment and not two
experiments.

### The knob laws, and where they came from

Which physical quantity each knob moves was measured off the machine at the
three fit positions, not assumed:

| voice | knob | what it actually moves | measured at knobs 0 / 5 / 10 |
|---|---|---|---|
| BD | DECAY | body τ (f0 does **not** move: 50.0 Hz on all 25 files) | 17.5 / 241 / 541 ms |
| BD | TONE | click energy above 300 Hz in the first 10 ms | 1.29 / 1.67 / 1.93 % |
| SD | TONE | ~~body ring, *not* pitch (168/172 Hz throughout)~~ — **WITHDRAWN 2026-09-18**; the two partials' amplitude **ratio**, and neither mode's decay | 0.0015 / 0.0839 / 2.205 (energy, upper over lower) |
| SD | SNAPPY | noise share above 700 Hz | 0.00 / 51.7 / 92.5 % |
| LT | TUNING | f0 | 80.0 / 90.0 / 100.0 Hz |
| MT | TUNING | f0 | 123.3 / 136.7 / 153.3 Hz |
| HT | TUNING | f0 | 170.0 / 186.7 / 213.3 Hz |
| LC | TUNING | f0 | 183.3 / 200.0 / 223.3 Hz |
| MC | TUNING | f0 | 260.0 / 280.0 / 320.0 Hz |
| HC | TUNING | f0 | 376.7 / 413.3 / 466.7 Hz |
| OH | DECAY | envelope τ — **saturates**, and 7.5 is held out | 22.9 / 186 / 219 ms |
| CY | DECAY | envelope τ | 158 / 394 / 510 ms |
| CY | TONE | the two bands' **ratio**, *not* a decay | 0.236 / 0.257 / 0.381 (5–13 kHz over 2–5 kHz) |

Each law is a three-parameter interpolant through exactly those three points
(log link for τ and f0, logit for energy shares). **Six of the sixteen have
no knob — CH, CP, CB, RS, CL and MA** — so they have no law and, see §7, no
possible held-out setting and no knob-equivalent.

> **CY TONE IS A BALANCE, NOT A DECAY, and it is the second voice in this
> study where the distinction had to be made the hard way.** Measured down
> the TONE column the cymbal's single fitted τ runs **464 → 196 ms**, which
> reads exactly like a decay knob and would have been written into a circuit
> whose decay the knob does not touch — the same error withdrawn from the
> snare's TONE law on 2026-09-18, in a different voice.
>
> Three things say it is the balance. The **file lengths the recordist chose
> never move** down TONE (2.50 s at every position) and do move down DECAY
> (1.50 → 4.00 s); the band split moves (2–5 kHz 0.762 → 0.686, 5–13 kHz
> 0.180 → 0.261); and the τ that does move is the weighted mix of a long
> 3.45 kHz band and a short 10.5 kHz one. `tools/probe_new_voice_knobs.py`
> is the measurement.
>
> **A knob that moves the file length the recordist chose is the decay knob.**
> That is the cheapest independent check available on this corpus and it is
> the one that settled which of the two filename codes is which.

> **The six TUNING laws were checked before they were used, not assumed from
> LT and HT.** f0 is monotone across the knob on all six and τ is flat to
> 1.05–1.08× over the whole dial, so the LT/HT law form carries over
> unchanged. Ours lands **2–7 % sharp at every held-out position, all six
> high** (§6.5) — a systematic sign, and the one thing these laws get wrong.

> **The SD TONE row was wrong, and it was the fifth instance of this voice's
> recurring error.** "28.5 / 27.4 / 13.6 ms" is one τ fitted to a sum of two
> modes that decay at different rates; fitted separately the machine's modes
> are 29–39 ms and 5–11 ms at *every* TONE position and what moves is their
> ratio, by 31.7 dB. Roland says the same thing ("the output ratio of the
> two", SN p.6). Reproduced from a construction with the decays held fixed in
> `test_discrimination.test_a_single_tau_on_two_modes_reads_a_balance_change_as_a_decay_change`,
> and it mattered: `kit_at` wrote that τ into **both** our body modes, so the
> study was driving our snare wrongly and part of the SD distance it reported
> was its own. Corrected 2026-09-18 (`docs/drum-verification.md` §8.6).

---

## 3. Result — all sixteen sounds

Held out, level-matched, arm `ours`, the study's own 320 columns. Balanced
accuracy with a Clopper-Pearson interval computed over **settings** rather
than over generated comparisons. Two knob-equivalent columns, because the
two rulers disagree and §3.1 is why.

> **§3.3 corrects this table.** Every figure below was measured through
> `condition()`'s acausal high-pass, whose 6-sample pad against a
> 2,400-sample pole put a full-scale pedestal on the attack (#161). The
> repaired pipeline moves BD **2.5 → 4.0**, HT (post-#154) **4.6 → 5.9** and
> CY **8.1 → 8.6** — *further* from the machine, not closer. Read §3.3 before
> quoting anything here.

| sound | circuit | knobs | held-out settings | bal. acc. | 95 % CI | **knob-equiv** (per-comparison) | knob-equiv (frozen ruler) | verdict |
|---|---|---|---|---|---|---|---|---|
| **BD** | BD | TONE, DECAY | 16 | 0.844 | [0.67, 0.95] | **2.5** | 5.2 | known defect remains |
| **SD** | SD | TONE, SNAPPY | 16 | 0.812 | [0.64, 0.93] | **3.4** | 4.7 | known defect remains |
| **CY** | CY | TONE, DECAY | 16 | 1.000 | [0.89, 1.00] | **8.1** | ≥ 10 | known defect remains |
| LC | LT | TUNING | 2 | 0.750 | [0.19, 0.99] | 3.5 | ≥ 10 | no verdict — underpowered |
| MT | MT | TUNING | 2 | 1.000 | [0.40, 1.00] | 5.2 | ≥ 10 | no verdict — underpowered |
| MC | MT | TUNING | 2 | 0.750 | [0.19, 0.99] | 5.4 | ≥ 10 | no verdict — underpowered |
| OH | OH | DECAY | 2 | 1.000 | [0.40, 1.00] | 6.9 | ≥ 10 | no verdict — underpowered |
| HT | HT | TUNING | 2 | 1.000 | [0.40, 1.00] | 7.3 | ≥ 10 | no verdict — underpowered |
| LT | LT | TUNING | 2 | 1.000 | [0.40, 1.00] | ≥ 10 | ≥ 10 | no verdict — underpowered |
| HC | HT | TUNING | 2 | 1.000 | [0.40, 1.00] | ≥ 10 | ≥ 10 | no verdict — underpowered |
| **CH** | CH | — | **0** | — | — | **REFUSED** | REFUSED | corpus cannot test |
| **CP** | CP | — | **0** | — | — | **REFUSED** | REFUSED | corpus cannot test |
| **CB** | CB | — | **0** | — | — | **REFUSED** | REFUSED | corpus cannot test |
| **RS** | CL | — | **0** | — | — | **REFUSED** | REFUSED | corpus cannot test |
| **CL** | CL | — | **0** | — | — | **REFUSED** | REFUSED | corpus cannot test |
| **MA** | CP | — | **0** | — | — | **REFUSED** | REFUSED | corpus cannot test |
| pooled | | | 62 | **0.895** | [0.83, 0.94] | — | — | known defect remains |

**REFUSED is not "we did not get to it".** A knob-equivalent is by
definition a distance measured on that sound's own knob. Six of the sixteen
have no knob, so the corpus holds exactly one recording of each, there is no
held-out setting, there is no yardstick, and there is no number to be had.
The harness asserts this rather than interpolating onto a dial that does not
exist (`test_a_sound_with_no_knob_can_produce_no_knob_equivalent`). **The old
study named three such voices; with eleven circuits there are six.**

### 3.1 The two rulers, and why they disagree

The knob-equivalent reads one distance off another. Until this re-run the two
were z-scored on *different* populations — the yardstick on the pair of real
recordings being compared, the measurement on real-plus-ours — so one was
being read off the other in units it did not share. `voice_scale` freezes
both to that sound's real recordings.

On the frozen ruler **12 of the 16 sounds sit at or past the end of the
dial**, and that is the finding, not a failure of the method:

> **Our difference from the machine is largely not on the machine's knob
> axis at all.** The knob-equivalent's premise — that our error looks like a
> knob move — is what saturates. We differ in directions the real 808's own
> knob never travels, so normalising by the machine's own spread sends the
> ratio off the top.

Both columns are reported. The per-comparison ruler still *ranks* the sounds,
which is what makes it actionable, and the frozen one says how much of that
ranking is an artefact of rescaling. Neither is "the" number, and a
knob-equivalent remains a **per-sound** reading: **SD 3.4 and OH 6.9 are two
readings on two different dials and were never interchangeable.**

### 3.2 Before and after

Against the revision 6/7 partial re-run (`docs/drum-verification.md` §8.6),
same corpus, same split, arm `ours`, per-comparison ruler — the only ruler on
which the two are comparable at all.

| sound | rev 5 (original study) | rev 6/7 partial re-run | **today** | direction |
|---|---|---|---|---|
| **SD** | 8.8 | 3.4 | **3.4** | held |
| **BD** | 3.6 | 2.5 | **2.5** | held |
| **LT** | 7.1 | 6.7 | **≥ 10** | **worse, or at the ruler's ceiling** |
| **OH** | 6.9 | 6.9 | **6.9** | held |
| **HT** | 6.0 | 7.2 | **7.3** | held (worse than rev 5) |
| CY | — | — | **8.1** | first measurement |
| MT / LC / MC / HC | — | — | **5.2 / 3.5 / 5.4 / ≥ 10** | first measurement |
| CH / CP / CB / RS / CL / MA | — | — | **REFUSED** | no knob |

**BD 2.5 and SD 3.4 reproduce the revision 6/7 figures exactly**, to the
tenth, on an independently re-derived law — which is the strongest evidence
available that those two numbers are real and that the harness is stable.

**LT is the one that moved.** Its ours-to-real distance (32.4) is now a hair
*above* its own knob-10 distance (32.3), so the interpolation has nothing to
land on. Read it as "at least 10", not as "much worse than 6.7": a 0.3 %
margin is not a measurement, and the honest statement is that LT is at the
end of the dial and this corpus cannot say how far past it.

**Three of the five old numbers held to the tenth and one is at a ceiling.**
The kit grew from eight sounds to sixteen without moving the five voices that
were already measured — which is the result a contract revision is supposed
to produce and is not always what happens.

### 3.3 The conditioning filter's boundary, and what repairing it moved (#161)

**Every number in §3 above was measured through an acausal high-pass whose
initial condition was a guess.** `condition()` high-passed with
`sosfiltfilt`, which pads **6 samples**. The 20 Hz pole is **0.99715** at
44.1 kHz and **0.99739** at 48 kHz — about **2,400 samples** to settle to
1e-3. Six against 2,400 is not a boundary condition, it is an initial
condition chosen at random, and on a unit impulse at index 0 it answers with a
**full-scale negative pedestal**: second sample **−0.994**, the first 30 ms
integrating to **−342** (−373 at 48 k) against a causal filter's **+0.02**.
`condition()` is inside the classifier's own feature pipeline, so that
pedestal was in every 320-column vector, every interpretable feature and every
knob-equivalent in the table above.

`condition()`'s own docstring warned about this boundary problem and then
reached for the acausal filter anyway.

**It applied to both sides, so some of it cancels — and how much was measured,
not assumed** (`model/condition_boundary.py`, which renders each setting once
and conditions it both ways, so the only thing differing between the two
columns is the boundary). Norms over the 320 columns in log10 power, median
over each sound's settings; `resid` is the part that does **not** cancel in
the paired difference:

| sound | lead ms, machine / ours | ‖Δ‖ machine | ‖Δ‖ ours | resid | paired distance | resid / paired |
|---|---|---|---|---|---|---|
| BD | 0.14 / 0.00 | 0.91 | 0.38 | 0.47 | 5.24 | **9 %** |
| HT | 0.14 / 0.00 | 1.22 | 0.35 | 0.97 | 8.86 | 11 % |
| CY | 0.25 / 0.00 | 0.02 | 5.19 | 5.19 | 39.97 | 13 % |
| SD | 0.14 / 0.00 | 0.40 | 0.85 | 1.22 | 7.63 | 16 % |
| OH | 0.14 / 0.00 | 0.63 | 3.53 | 3.50 | 14.01 | 25 % |
| MC | 0.14 / 0.00 | 0.07 | 0.30 | 0.28 | 0.95 | 29 % |
| LT | 0.14 / 0.00 | 1.84 | 1.00 | 2.83 | 8.85 | 32 % |
| MT | 0.11 / 0.00 | 2.25 | 0.98 | 3.23 | 7.96 | 41 % |
| LC | 0.14 / 0.00 | 0.30 | 0.78 | 0.70 | 0.81 | **86 %** |
| HC | 0.16 / 0.00 | 0.20 | 1.02 | 0.92 | 1.05 | **87 %** |
| RS / CH / CL | 0.14–0.18 / 0.00 | 0.31–1.92 | 5.31–10.13 | 6.93–9.85 | 5.31–8.55 | **102–130 %** |

**Almost nothing cancels.** Read the rows: for CY the machine's vector moves
by 0.02 and ours by 5.19, and the residual is 5.19 — the machine's side
contributes nothing to the cancellation at all. For LT, MT, SD, CB and MA the
residual equals the *sum* of the two sides to two decimals, which is what
near-orthogonal shifts look like. **The `lead ms` column is why**: our render
reaches `condition()` pre-trimmed at its onset by `_render_raw` and the
machine's does not (#160's F2), so a pedestal whose size depends on the first
sample lands on two differently shaped onsets. This is #163's pattern exactly,
and it is the reason "it applies to both sides" was never a defence.

What saves the three adjudicable sounds is not cancellation but **scale**: the
residual is 9–16 % of the distance being reported for BD, SD and CY. Where the
reported distance is small — LC, HC, and the three no-knob sounds — **the
artefact was as large as the difference**.

**The repair.** Cut at the onset **first**, then high-pass **causally from
rest**, on both sides. Cutting first makes the window depend on nothing
outside itself, so the trim asymmetry cannot reach the answer at all — a
stronger property than trimming both sides identically, and the one #103 asks
for: `test_a_measurement_does_not_depend_on_where_the_record_begins` asserts
the 320 columns are unchanged to 1e-9 under 1, 10 and 50 ms of prepended
silence and under a converter's DC-plus-hiss lead. At HEAD, before the repair,
10 ms of silence moved them by 0.069 (0.69 dB).

**The knob-equivalents, re-derived.** `distance_curve` / `ours_distance` /
`knob_equivalent_distance` exactly as `discrimination_run.py` calls them, run
twice off the same renders. The baseline is **legacy at this commit**, not
§3's printed figure: HEAD also carries #154's tom pitch-drop correction, which
moves the renders themselves.

| sound | §3 (#148) | legacy @ HEAD | **repaired** | Δ from the repair | ±1 % on the distance |
|---|---|---|---|---|---|
| **BD** | 2.5 | 2.5 | **4.0** | **+1.4** | 3.9 – 4.1 |
| **SD** | 3.4 | 3.4 | **3.4** | +0.0 | 3.3 – 3.6 |
| **CY** | 8.1 | 8.1 | **8.6** | **+0.5** | 8.4 – 8.8 |
| OH | 6.9 | 6.9 | **6.0** | −0.9 | 5.3 – 6.7 |
| HT | 7.3 | 4.6 | **5.9** | **+1.3** | 5.8 – 6.0 |
| LT | ≥ 10 | 7.0 | **6.8** | −0.2 | 6.6 – 7.0 |
| MT | 5.2 | 4.4 | **3.5** | **−0.9** | 3.5 – 3.6 |
| LC | 3.5 | 2.5 † | **2.5** † | +0.0 | 2.5 |
| MC | 5.4 | 2.5 † | **2.5** † | +0.0 | 2.5 |
| HC | ≥ 10 | 2.5 † | **2.5** † | +0.0 | 2.5 |
| CH CP CB RS CL MA | REFUSED | REFUSED | **REFUSED** | — | no knob, no yardstick |

† pinned at the bottom of the yardstick: the ours-to-real distance is below
the smallest knob step the curve holds, so 2.5 is a floor, not a reading.

**The two columns on the left are two different corrections and must not be
added.** #148 → legacy@HEAD is **#154's**, and it lands on exactly the six
tom/conga sounds #154 re-fitted (LT MT HT LC MC HC) and on **none** of the
other four. That BD 2.5, SD 3.4, CY 8.1 and OH 6.9 reproduce **to the
decimal** through the legacy path is the check that this re-derivation runs
the same code the study runs.

**Three of the moves are outside the ruler's own sensitivity and four are
not.** The last column is the knob-equivalent recomputed at ±1 % of the
measured distance, because a knob-equivalent is read off a four-point
interpolation and its steepness decides what a one-decimal figure is worth.
BD (+1.4), HT (+1.3) and MT (−0.9) are far outside that band and are real
moves; CY (+0.5) is just outside; **OH's −0.9 is inside it** (5.3–6.7 for a
±1 % nudge) and should not be read as a move at all. SD and LT do not move.

**BD, the closest sound we had, was the most flattered.** 2.5 → 4.0 of 10 is a
**+58 %** increase in our distance from the machine on its own dial, and the
underlying distance only moved 5.9 % (17.24 → 18.26) — the knob-equivalent
amplifies it because BD's yardstick is shallow where we land. The repaired
pipeline separates us **further** from the machine on BD, HT and CY. That is
the finding: the old numbers were flattered by an artefact, and nothing was
tuned to preserve them.

**What is not re-derived here.** The balanced accuracies, CIs and ABX counts
in §3 and §4 come from the classifier, and this section measures distances
only. They are re-derived by the full run recorded in §9.

## 4. Controls — without these, none of §3 means anything

Sixteen sounds, arm-by-arm, 2026-09-18.

| control | what it proves | result |
|---|---|---|
| **cross-voice**, real vs real, different voice, same machine, same converter, same afternoon (LT vs LC, HT vs HC, BD vs MT, LT vs MT, LC vs MC) | the pipeline resolves **timbre** with zero provenance cue | **1.000** on all five — PASS |
| **label permutation**, 200 shuffles | calibrates what chance is for this pipeline at this N | mean **0.497** [0.427, 0.573] — PASS, chance is 0.5 |
| **real-vs-real random split** of the 25 real BDs into two pseudo-classes | the pipeline does not manufacture separation from nothing | mean **0.438** [0.309, 0.688] — PASS |
| **large degradations** (τ×4, noise path removed, 4-bit cutoff) | catches gross errors | 0.906 – **1.000** — PASS |
| **graded degradations** (τ×0.75, τ×0.50, snare noise −6 / −12 dB, 6-bit and 5-bit cutoff) | catches errors *near the margin that matters* | 0.906 – 0.992 — PASS |

**The ordering is now visible, which it never was before.** Every degradation
sits **above** `ours` (0.895): deg_decay 1.000, deg_tail75 0.992, deg_tail50
0.960, deg_q5bit 0.941, deg_qcoarse 0.926, deg_q6bit 0.912, deg_nonoise /
deg_snappy6 / deg_snappy12 0.906. When the whole board was at 1.000 that
ordering could not exist; it is the single most useful consequence of the
kit having improved.

**Two things the reader should not over-read.**

`deg_tail75` (0.992) scores **above** `deg_tail50` (0.960) — a milder
degradation separating more than a stronger one. At n = 62 settings the
interval on each is about ±0.04 and the two overlap completely, so this is
noise in the ordering and not an inversion of the physics. It is recorded
rather than smoothed because an unexplained inversion is exactly the shape a
real defect would take, and the next re-run should check whether it persists.

The `deg_nonoise`, `deg_snappy*` arms cover only SD (and CP, MA), so their
n is 32 rather than 124. **Their accuracies are not comparable with the
full-kit arms' and the FD-mel rows built from them are not either** — the
voice balance the Fréchet construct needs is fixed only *within* a row, not
across rows. The published §7 table read them across rows and should not
have.

### What the corpus cannot give: the real-vs-real floor

The number that would make an ours-vs-real accuracy fully interpretable is
the **same-setting real-vs-real** accuracy. Two genuine takes at the same
knobs are not identical — the 808's six hat oscillators free-run, its noise
source is an avalanche diode, components drift. If real separated from real
at 80 %, our 100 % would mean much less.

**That number cannot be computed from any freely licensed 808 material we
could obtain.** Fischer states he recorded many hits of each sound and kept
the one he judged most representative, so the set has exactly **one take per
setting**; the `808*` directories redistributed in tidalcycles/Dirt-Samples
are byte-identical to the same files. This is a real limitation, not a
detail.

What we computed instead is the **separation curve** — real against real at a
known knob distance, one knob at a time, split on the other knob:

| | Δ = 2.5 | Δ = 5.0 | Δ = 7.5 | Δ = 10.0 |
|---|---|---|---|---|
| BD **DECAY** | 1.00 | 1.00 | 1.00 | 1.00 |
| BD **TONE** | **0.50** | **0.50** | 0.75 | 0.75 |
| SD **SNAPPY** | 1.00 | 1.00 | 1.00 | 1.00 |
| SD **TONE** | **0.50** | **0.50** | 0.75 | 0.75 |

This is an **upper bound on the floor**, not the floor: every pair on it
differs by a real knob move, and Δ = 0 is off its left edge. But it is
informative in both directions. The machine's own TONE knob, moved *end to
end*, is separated at only 0.75 — and at one step, at chance. Our renders are
separated at 1.00. **We are further from the real machine than the real
machine's weakest knob can travel across its whole range.**

To get the true floor someone must record multi-take material. 808 From Mars
(≈ $39) has since been purchased, and **it does not supply it**: its bass
drum's 144 clean files are 2 chains × 2 accents × 6 decay × 6 tone, a grid of
settings with no take axis in it, and every other voice is one take per setting
too (`docs/bd-repeatability-measurement.md`). The same vendor's
`808_loops_from_mars.zip` has bass-drum-only 4/4 loops, where one setting is
struck repeatedly inside one continuous take; that is the remaining candidate.
New recordings would serve equally.

---

## 5. What carries the discrimination

Two representations, deliberately, because each misses what the other finds.

### 5a. Interpretable diagnostics, at held-out settings only

The quantities the model was fitted against, measured at settings the fit
never saw. Mean relative error, ours against the machine, over the held-out
settings. Ratios this large are easier to read in dB, so both are given.

| sound | n | largest errors |
|---|---|---|
| **CY** | 16 | energy below 700 Hz **×1710 (+32.3 dB)**, 0.7–5 kHz ×4.5 (+6.5 dB), τ +167 % |
| **HC** | 2 | 0.7–5 kHz **×648 (+28.1 dB)**, above 5 kHz −11 %, attack −22 % |
| **OH** | 2 | energy below 700 Hz **×170 (+22.3 dB)**, 0.7–5 kHz −81 %, attack −81 % |
| **SD** | 16 | 0.7–5 kHz **×29.5 (+14.7 dB)**, above 5 kHz ×9.3 (+9.7 dB), below 700 Hz +32 % |
| **MC** | 2 | 0.7–5 kHz ×7.6 (+8.8 dB), above 5 kHz −59 %, centroid −36 % |
| **BD** | 16 | **τ +169 %**, attack +29 %, above 5 kHz −79 % |
| **LC** | 2 | 0.7–5 kHz +63 %, above 5 kHz −56 %, centroid −17 % |
| **LT** | 2 | 0.7–5 kHz **−100 %**, above 5 kHz −65 %, attack −29 % |
| **MT** | 2 | 0.7–5 kHz **−100 %**, above 5 kHz −51 %, attack −38 % |
| **HT** | 2 | 0.7–5 kHz **−100 %**, above 5 kHz −27 %, centroid −24 % |

**Two opposite defects, and they are on the same circuits.** LT, MT and HT
have **literally nothing** in 0.7–5 kHz — the absent pink-noise path, as
before. Their conga twins LC, MC and HC ride the *same three circuits* and
have **too much** there, up to +28 dB. So the tom/conga pair is not one
error with one sign: whatever supplies that band is missing in the tom
position and over-supplied in the conga position, and a single fix that
raises the band would make the congas worse.

**The BD τ error is +169 % and is the largest single mechanism left on the
adjudicable sounds.** §6's trajectory puts it at 170–302 Hz: ours decays at
−8.9 dB/100 ms where the machine decays at −44.1.

**CY's low-frequency excess is 32 dB** and is the same defect as OH's
(+22.3 dB), one circuit apart — both are broadband excitation reaching the
output below the circuit's own band.

### 5b. General representation, grouped permutation importance

Accuracy drop when a whole bucket is shuffled on held-out clips, arm `ours`,
all sixteen sounds, the study's own 320 columns:

| feature group | accuracy drop |
|---|---|
| MFCC, **attack segment** (0–60 ms) | **+0.122** |
| MFCC, early segment (60–120 ms) | +0.065 |
| MFCC, mid segment (120–180 ms) | +0.050 |
| MFCC, tail segment (180–240 ms) | +0.022 |
| 200–700 Hz, attack segment | +0.017 |
| 2–5 kHz, attack segment | +0.010 |

**The attack still carries most of it**, now over sixteen sounds rather than
eight, and §6's trajectory report says what is in that attack.

---

### 5c. The vocoder-discriminator views, as extra columns

Three deterministic decompositions borrowed from the neural-vocoder
discriminators were added as **feature columns** — no training, no encoder,
no embedding, nothing fitted to the reference — and handed to the same L2
logistic regression and the same knob-equivalent unit.
`model/discrimination_features.py` is the code, with 12 self-tests.

| view | what it is | columns |
|---|---|---|
| **MPD** | multi-period fold, statistics across rows | 48 |
| **JIT** | the dominant partial's own cycle trajectory | 9 |
| **CQT** | constant-Q sub-bands, 6 per octave, 40 Hz–16 kHz | 104 |
| **MS** | six bands at 4, 10 and 25 ms, plus the scale differences | 30 |

**What they changed.** Pooled held-out accuracy **0.895 → 0.984**, ABX
**56/62 → 62/62**. They separate.

**What they say.** After splitting the jitter bucket into *pitch* and
*stability* columns — which had to be done, because `period_ms` and
`ncycles` say what the partial's frequency **is**, which is a tuning error
the study already measures:

| bucket | accuracy drop | |
|---|---|---|
| MFCC, early segment | +0.045 | |
| MFCC, tail segment | +0.026 | |
| MFCC, attack segment | +0.026 | |
| **`jit.period`** | **+0.024** | pitch — a tuning error |
| `cqt.0-200Hz` | +0.018 | **new** |
| `cqt.9000-20000Hz` | +0.017 | **new** |
| `cqt.5000-9000Hz` | +0.014 | **new** |
| `cqt.700-2000Hz` | +0.010 | **new** |
| `ms.scale-difference` | +0.010 | **new** — #109's 4 ms vs 10 ms |
| `cqt.2000-5000Hz` | +0.010 | **new** |
| MFCC, mid segment | +0.008 | |
| **`jit.stability`** | **+0.008** | **oscillator steadiness — #56** |
| … eleven more buckets … | ≤ +0.006 | |
| `mpd.*`, all six strides | **≤ +0.002** | **new, and worth nothing** |

Full list in `docs/discrimination-results-16.json`; 24 buckets are kept.

> **#56 DOES NOT GET A NUMBER FROM THIS, AND THAT IS THE RESULT.**
> "Three stable oscillators do not sound like three analogue ones" would be
> measured by the **stability** columns. `jit.period` — a pitch error — is
> **fourth of twenty-four at +0.024**. `jit.stability` is **twelfth at
> +0.008**, a third of it, below four constant-Q buckets and below the
> multi-scale difference. And the pitch reading is independently
> corroborated: measured directly, all six tuned circuits are 2–7 % sharp at
> every held-out TUNING position (§6.5).
>
> This is a negative result with power behind it, not an absence of
> evidence. The stability columns resolve **0.1 % per-cycle jitter as
> 14 534 ppm against a 20 ppm rate-artefact floor** — a factor of 700 — and
> they carry the control that makes the claim falsifiable at all: two
> *perfectly stable* detuned oscillators beat, and beating reads as jitter
> on any scatter statistic (+2 Hz of static detune reads 17 473 ppm, against
> 14 534 for real jitter). Only the lag-1 autocorrelation of the
> **differenced** phase residual separates the two mechanisms (0.80–1.00 for
> beating and glide, 0.60–0.68 for per-cycle jitter), and
> `test_static_detuning_is_not_reported_as_drift` asserts the size of that
> gap so it cannot quietly close.
>
> What the null does **not** cover: the study's window is 240 ms and the
> probe follows one partial. Oscillator drift over a longer note, or in the
> five oscillators the probe does not lock to, is untested.

**The MPD columns earn nothing and should not be carried.** All six strides
come in at **+0.002 or less**, ranks 18 and 20–23 of 24 — 48 columns for
less than a fifth of what one constant-Q bucket contributes. They are also
not a drift measure and are not read as one: the strides are not
commensurate with any oscillator here (131 Hz at 48 kHz is 366.4 samples),
so a perfectly stable tone already walks from row to row, which
`test_a_fixed_stride_fold_is_not_a_drift_measure` asserts. **The
recommendation is to drop them.** Of the three borrowed views, the
constant-Q ladder and the multi-scale windows pay for themselves and the
multi-period fold does not.

**Promotable to named measurements, with tolerances and floors:**

- **`cqt.0-200Hz`** — the low-band excess. It is the most promotable thing
  here: it is in the top five, it is corroborated by the trajectory report
  on every one of the sixteen sounds, and it has an obvious floor (the
  machine's own level in that band, 41–91 dB under its peak).
- **`ms.scale-difference`** (4 ms against 10 ms) — #109's shape, already a
  named quantity there, now with a per-voice value.
- **`jit.period_ms`** — a per-voice pitch check with a stated tolerance;
  ours is 2–7 % sharp and the sign is systematic.
- **NOT `jit.phasejit_ppm` on its own.** It cannot tell drift from beating
  and must never be quoted without `jit.dphase_ar1` beside it.

**A caution on the deltas, and it is the coordinator's, not a hedge.** Adding
columns changes the ruler as well as the reading: the knob-equivalent is a
distance normalised by that voice's own spread, and both move. The
per-comparison knob-equivalent rose for BD (2.5 → 4.4) and CY (8.1 → 9.8)
and **fell** for SD (3.4 → 2.9), HT (7.3 → 6.7) and LT (≥10 → 7.4). A number
that rises with better features is a finding — it says the old columns could
not see how far away we were — but a number that moves in either direction
on a ruler that also moved is not by itself a newly discovered defect.

## 6. Ranked: what to fix next

Read with §3.1's caveat: the per-comparison knob-equivalent ranks, and it
ranks against **one machine** (§7).

1. **The excitation pulse, and it is now the finding rather than an
   inference.** `docs/discrimination-trajectory.txt` reports, for **all
   sixteen sounds without exception**, broadband energy in the first 30 ms
   that the machine does not have — 41 to 91 dB below the machine's own peak
   in that band, and ours carrying +11 to +67 dB of it. The cymbal is the
   clearest case: at 135 Hz the machine sits at −72.9 dB, 91 dB under its own
   peak, and ours carries −6 dB. That is an impulse striking a resonator
   where the machine uses a pulse shaped over ~10 ms, seen directly rather
   than inferred from a segment importance. **One fix, sixteen sounds.**
2. **BD — 2.5 / 10.** Closest of the three adjudicable sounds, and the
   trajectory says exactly where: the 170–302 Hz attack is **17 to 22 dB
   quiet in the first 30 ms**, and the body then **decays far too slowly** —
   ours −8.9 dB/100 ms at 170 Hz against the machine's −44.1. A quiet, long
   body where the machine has a loud, short one.
3. **SD — 3.4 / 10.** Held at revision 7's figure. The trajectory puts the
   remaining error at 1.3–3.8 kHz in the first 60 ms, +11 to +12 dB, and in
   a tail that decays too slowly (−27.8 dB/100 ms at 479 Hz against −46.5).
4. **CY — 8.1 / 10, and the first measurement of this voice.** The worst of
   the three adjudicable sounds. Two separable defects: **+21 dB at 1.5 kHz**
   where the machine has −18, and a high band that **decays too slowly**
   across 4.8–10.8 kHz (−1.6 to −3.2 dB/100 ms against −5.6 to −7.6). The
   135 Hz excess above is the third.
5. **The six tuned circuits are all ~4 % sharp at the held-out knob
   positions.** Measured directly against the machine at every held-out
   TUNING setting: LT +4.0/+7.1 %, LC +3.5/+4.8, MT +5.3/+4.7, MC +3.7/+2.2,
   HT +3.8/+3.3, HC +5.2/+2.3 — **every one high, none low**. A systematic
   sign like that is a law or a rounding, not noise, and it is cheap to chase.
6. **The congas' and toms' bodies decay too fast and the machine's do not.**
   LT is the extreme: ours −48.7 dB/100 ms at 190 Hz against the machine's
   −8.2. LC, MC, HC and HT are the same sign.
7. **OH's tail is 17–20 dB hot at 7.7–8.6 kHz between 210 and 240 ms** and
   its 1.9–2.2 kHz decays at less than half the machine's rate.

`docfix` was rendered as a second arm and is **not distinguishable from
`ours`** on this corpus: 0.887 against 0.895 on the 320 columns, which is
well inside the interval. The fixes `drum-verification.md` prescribes are
not what is left.

## 7. What this test does **not** say

- **It is not a listening test.** Near-chance classifier accuracy would be an
  automated *screening* result about these evaluators on this corpus. It
  would not be a claim about human indistinguishability — that is a different
  experiment, with listeners, trials and controls we have not run. No such
  claim is made anywhere here.
- **EVERY NUMBER HERE IS A DISTANCE FROM ONE MACHINE, NOT FROM THE 808.**
  Every real-808 recording reachable from here descends from Fischer
  s/n 103852. The nominally different `808*` sets redistributed in
  tidalcycles/Dirt-Samples are byte-identical to these files — re-pressings
  of the same events, cross-correlating at 1.000, not independent takes. So
  "our BD is 2.5 of 10 away" means **2.5 of 10 away from that unit**, with
  its components, its trimmer settings and its 1994 afternoon. A second
  machine would differ from this one by some unknown amount that this corpus
  cannot bound, and the machine-to-machine spread is plausibly a large
  fraction of the distances reported here. Nothing in this document is a
  distance from "a TR-808" as a class, and the ranking in §6 should be read
  as a ranking against one instrument.

- **Six of sixteen sounds cannot be tested at all, and the count grew with
  the kit.** CH, CP, CB, RS, CL and MA have no knob, so the corpus has one
  recording each and there is nothing to hold out. CB is documented as
  *wrong* and this test can neither confirm nor deny it. Seven more —
  LT, MT, HT, LC, MC, HC, OH — have two held-out settings each, and eight is
  the minimum at which any number of correct calls could exclude chance, so
  they get no verdict either. **Only three of sixteen sounds — BD, SD and
  CY — are adjudicable at all, and the sounds most likely to pass are
  exactly the ones the corpus is too small to judge.**
- **Power.** 38 held-out settings bound a chance-performing discriminator
  below 0.61 one-sided; ~270 trials would be needed to bound it at 0.60, and
  the corpus offers 38. Had we measured near-chance, the honest report would
  have been "no verdict — screening inconclusive", not "indistinguishable".
- **The unmatched-level pass is void.** The reference pack is **peak-limited
  to −2.4 dBFS** — 38 of 116 files sit within 1 % of the ceiling and the whole
  set spans 6.0 dB. Level differences therefore carry no information about
  the machine's accent behaviour. Measured, not assumed; the run prints it.
- **FD-mel is reported, and not relied on.** The Fréchet construct behind FAD,
  computed on this module's own 320-d representation (not VGGish — so it is
  *not* FAD and is never called that), with encoder, sample counts, voice
  balance and preprocessing fixed across rows:

  | row | FD-mel |
  |---|---|
  | reference vs `ours` | 67 |
  | reference vs `docfix` | 69 |
  | reference vs graded degradations | 113 – 149 |
  | reference vs large degradations | 160 – 261 |
  | **reference vs reference subsets** | **211** |

  The arm ordering is sensible and `ours` sits well below the reference's own
  subset-to-subset spread. But that spread (211) lands *inside* the range of
  the degraded arms, which means at 25 clips per side FD-mel is dominated by
  sampling noise and has little resolving power here. Comparative only, as
  intended, and weak.

---

## 8. Minimoog: what our ladder measures against three independent emulations

> ### ⚠️ WITHDRAWN 2026-09-18: every u-he Diva number below
>
> Diva was running **unlicensed**. It prints `ERROR: Could not read lic from
> file.` on every instantiation and inserts periodic broadband clicks — 20 in
> a 360 s render, none in the first 167 s, then clusters every ~33 s, each a
> ~0.1 ms burst that raises the 6–20 kHz band by **32–43 dB** while leaving
> the note's own band unchanged. Surge XT over the same test: **zero**.
> `docs/reference-integrity.md` §1 has the evidence.
>
> **Every Diva figure in this section is withdrawn**, including
> **h5 − h3 = −41.2 dB at matched h3**, which has been quoted elsewhere.
> Surge XT and Arturia Mini V3 are unaffected — both showed zero events.
> Read this section as a two-reference study until Diva is licensed.

**Revision 2026-09-18. Revision 1 of this section concluded that no Minimoog
validation was possible and produced none. That conclusion was wrong, and one
of the numbers it rested on was a measurement artefact. Both are corrected
here.**

Revision 1's argument was: the only free hardware corpus (Legowelt's 222 WAVs
from Minimoog #5529) ships no panel settings, and the parameter-labelled
datasets — InverSynth, Sound2Synth, DiffMoog — were rejected because they are
"rendered from *software* synths, so comparing against them would test our
chip against another emulation." The material facts are still true. The
conclusion drawn from them is not.

**Why it is wrong.** A purity standard that admits only a real Model D
produced *zero* validation instead of imperfect validation, and shipped a
filter whose only evidence was that it agreed with our own decision records.
And it gave up the one thing an unlabelled hardware corpus can never
provide: **a software reference can be set to a known patch, and ours set to
the same patch.** That is a controlled experiment. Sound-matching against
222 unlabelled recordings could only ever have been a similarity score.

So: three references, all on this machine, all driven headlessly from Python
through `dawdreamer` (VST3, programmatic parameters, no GUI), all at 48 kHz —
which is our own `SR`, so **nothing in this study is resampled**.

| reference | what it is | what it can be asked |
|---|---|---|
| **Surge XT 1.2.3** — `LP Vintage Ladder`, subtype **Type 2** | **open source.** `sst::filters::VintageLadder::Huov` — Huovilainen's DAFx-04 nonlinear ladder, **the same published model DR 0001 implements** | everything, and its cutoff is commanded *and read back* in Hz, so cutoff accuracy is answerable here and nowhere else |
| **Surge XT 1.2.3** — same filter, subtype **Type 1** | `VintageLadder::RK` — Runge-Kutta 4 integration of the Stilson/Puckette ladder ODE, cubic soft-clip, 4× oversampled | everything |
| **Arturia Mini V3** | a dedicated Minimoog Model D emulation; 2 audio inputs (the Model D's external-input jack), so a known signal can be put through its filter | shape, drive, self-oscillation. Every parameter is a bare 0..1 with no units and no readback, so **commanded-cutoff accuracy is not answerable against it** |
| **u-he Diva** — VCF model `Ladder`, 24 dB | a ladder model in a synth with a strong reputation for analogue accuracy; `Accuracy: divine`, `OfflineAcc: best`, all voice-drift slop zeroed | shape and self-oscillation. **0 audio input channels**, so it is excited by its own white noise against a wide-open reference render, and **drive is not answerable against it at all** |

**What this does and does not establish, and the label goes on every result
below: none of the three is a Minimoog.** Agreeing with them means
"consistent with high-quality emulations", not "sounds like a Minimoog". Two
of them are commercial products whose internals cannot be inspected. The
protocol that would settle the real question is written down —
`docs/moog-recording-protocol.md` — and needs one person with the instrument
and an hour.

Run it:

```
.venv/bin/pip install dawdreamer
.venv/bin/python -m pytest model/test_reference_compare.py -q      # the estimators, first
.venv/bin/python model/reference_compare.py --stage all --devices ours,surge-rk,surge-huov,diva,miniv3
.venv/bin/python model/reference_compare.py --stage peakdrive,bigdrive --devices ...
.venv/bin/python model/reference_compare.py --report --out /tmp/refcmp
```

`model/reference_rigs.py` holds the five rigs (ours, its injected defects, and
the three plugins), `model/reference_compare.py` the measurements and the
report, `model/test_reference_compare.py` their ground truth.

---

### 8.1 The number revision 1 got wrong, and how

Revision 1 published this table and called it 25 dB of structural separation:

| structure | h5 − h3, at 129 / 258 / 516 Hz | as published |
|---|---|---|
| ours (tanh in every stage) | −14.0, −14.1, −14.5 dB | |
| one-tanh (linearised) — negative control | −38.4, −39.1, −40.8 dB | |

**Re-measured on the identical signals with a validated estimator, four of
those six numbers do not exist.** `model/moog_probe.py`'s `harmonics()`
integrates FFT bins around each harmonic with no window and no floor check. A
*rectangular* coherent projection leaks the fundamental sideways at roughly
1/(π·Δbins), which for a half-second record puts a phantom "harmonic" at −55
to −75 dB — precisely the range these h5 values live in. Measured with a
Blackman-Harris window (sidelobes 92 dB down) and a floor probed at four
off-harmonic offsets, `ours` at 258 Hz and `one-tanh` at 258 and 516 Hz have
**no fifth harmonic above their own noise floor at all**, and where h5 does
exist the spread is −25.5 dB, not −14.0.

This is the failure `docs/verification-rules.md` exists about, in the section
that was arguing for the rest of the filter. `audio_measure.harmonic_signature`
replaces it: windowed projection, a floor measured at (k ± 0.3) and
(k ± 0.5)·f0 taking the **largest** of the four, harmonics above Nyquist
returned as `None` rather than 0, and a `drift_db` so that a still-growing
ring is not analysed as a steady one. Its ground truth recovers harmonics at
−60 and −75 dB from a record that is *not* a whole number of periods, to
0.003 dB.

**The second thing revision 1 got wrong: h5 − h3 is not settings-independent.**
It depends strongly on how hard the limit cycle drives the nonlinearity, and
h3 is the measure of that. Against the fixed-point one-tanh control the probe
separates the two structures by **21 dB at res 1.3, 8 dB at res 1.05 and 3 dB
at res 2.0** — because at the top of the range the control's hard input clip
takes over. Quoted without a resonance, the number means nothing. Everything
below is quoted either at a stated resonance or at **matched h3**, which
controls the drive.

---

### 8.2 Method, and the four ways it could have been a gain error

- **Stepped tone, coherent projection** — the measured transfer function, the
  same probe `model/test_moog_acceptance.py` already uses on our filter, at
  the same drive, for all five rigs. Not an impulse response: it would presume
  a linearity that none of these four filters has. Never a spectral centroid.
- **Everything quoted is a ratio** — dB over a passband plateau, dB per
  octave, a harmonic over its own fundamental, a frequency over another
  frequency. A fixed gain difference between two synthesisers cancels out of
  every one of them by construction.
- **48 kHz end to end.** No result can be a resampler.
- **Every estimator is ground-truthed against a closed-form signal before any
  number it produces is quoted** (`model/test_reference_compare.py`, 17
  tests): −24.00 dB/oct recovered exactly from a −24 dB/oct line and *refused*
  on a resonant skirt; the −3 dB corner of four cascaded one-poles against its
  algebraic value 0.434995·f_p; a resonator's peak height, peak frequency and
  Q against their closed forms; harmonics at −60/−75 dB recovered; a pure sine
  under noise reported as **having no third harmonic** rather than as the
  noise level.
- **Start red.** Three deliberately-wrong ladders are carried through the same
  measurements (§8.7). If a broken model landed inside the reference spread on
  a property, that property proves nothing and is reported as proving nothing.
- **Level.** Input levels are referred to each plugin's own full scale, which
  is a matched documented setting (it is the rail) but is *not* the level at
  each filter's input. §8.6 measures where each filter actually starts to
  saturate, which is what makes the drive columns comparable.

---

### 8.3 Surge XT gets its own verdict

Surge is not the same kind of evidence as the other two. Its Vintage Ladder
"Type 2" is an implementation of the *same paper* DR 0001 implements, so a
disagreement is a bug in one of the two, not a difference of modelling taste.
Read from `sst-filters` `include/sst/filters/VintageLadders.h` (the Huov
namespace is mathematically identical at the 1.2.3-era commit `8ea9b8d` and on
`main`, checked), here is every place the two differ **by design**:

| | ours (DR 0001, contract 11.4) | Surge `VintageLadder::Huov` |
|---|---|---|
| topology | four one-poles, `y[s] += g·(tanh(y[s−1]) − tanh(y[s]))` | identical |
| oversampling | 2× (96 kHz) | 2×, input fed at both sub-steps (no zero-stuffing), same |
| feedback tap | `(y3[n−1] + y3[n−2])/2`, half-sample phase compensation | `(stage3 + delay4)/2`, the same |
| **output tap** | `y[3]`, **before** the averaging | `delay[5]`, **after** it |
| arithmetic | integer, Q1.15 signal, 24-bit Q4.20 state | float32 SIMD |
| **tanh** | **16-entry table over [0,4), linear interpolation**, max error **0.0060** | Padé rational, clamped at ±5, max error **1.5e-5** |
| **tuning** | `g = 1 − exp(−2π f / f_os)`, no correction | **`fcr = 1.8730 fc³ + 0.4955 fc² − 0.6490 fc + 0.9988`**, Huovilainen's published tuning polynomial, applied to the exponent |
| resonance law | `k = 4·res`, corrected per cutoff by DR 0006's own measured ROM | `4·res·acr`, `acr = −3.9364 fc² + 1.8409 fc + 0.9968`, Huovilainen's published polynomial |

> **The `fcr` quadratic term is `0.4955`, and `sst-filters` ships `0.4995`.**
> Surge's own comment in `VintageLadders.h` reads `0.4955 * fc2` and the
> constant beside it is *named* `m04955` — but it is *initialised* to
> `0.4995f`, in both the 1.2.3-era commit `8ea9b8d` and on `main`. The cited
> source spells it **`0.4955`**, so the paper's value is 0.4955 and Surge
> ships a typo: its comment and its constant name both agree with the paper
> against its own code.
>
> **This repository implements 0.4955**, in `model/reference_rigs.py`'s
> `OurLadder.fcr` and in the table above. It is recorded here because the next
> person to compare our implementation against Surge's source will find our
> value differing from the code in front of them and reasonably assume we are
> wrong.
>
> **It changes nothing measured.** At a 10 kHz cutoff the two differ by
> 1.7e−4 in an `fcr` of 0.9022 — **0.003 cents**. Every figure in §8.4 stands
> as measured.
>
> Worth the line for its own sake: a reference can be **authoritative about
> its intent and wrong in its artefact**, and the two have to be read
> separately.
| **resonance range** | `res` clamps at 2.0 (the 17-bit `k` register); **res = 1 is the onset at every cutoff** (DR 0006) | `res` clamped to ≤ 0.9925 and reduced further above f_s/3: **it never reaches the onset and cannot self-oscillate** |
| **signal scale into the tanh** | `gain = drive·0.13/0.05 = 2.6`, so full scale is 2.6 in tanh units | `thermal = 1/70`, so full scale is **0.0143** in tanh units |
| gain compensation | `ogain = (2V_T/v_pu)·(1 + 2·res)` at the output | `gComp = 0.5` inside the feedback, on the "Compensated" subtypes only |

Two of those rows decide what Surge can be used for:

**Surge's Huovilainen subtype cannot self-oscillate.** Measured: at resonance
100 % its free ring decays monotonically from −82.5 dB to −127.3 dB over
1.65 s. That is not a defect, it is the `0.9925` clamp doing its job. It means
Surge Type 2 contributes nothing to the self-oscillation fingerprint.

**Surge's Huovilainen subtype does not reach its own nonlinearity at any
usable level.** With `thermal = 1/70`, a full-scale ±1.0 signal presents 0.014
to a `tanh` that is linear to one part in 10⁴ there. Predicted h3 at 0 dBFS:
−101 dB. **Measured: −102 dB.** It first produces −40 dB of third harmonic at
**+18 dBFS** — 18 dB past the rail. Ours reaches that at **−6.2 dBFS**, Mini
V3 at **−5.7 dBFS**, Surge's RK model at **−0.6 dBFS**.

> Our figure here is the **shipped** filter's, re-measured 2026-09-26 from
> `bigdrive-ours` in `docs/reference-compare-results-shipped.json` — the same
> row and the same quantity §8.6 quotes, which said −6.2 dBFS while this line
> still said the pre-DR-0011 −6.6 dBFS (issue #239). The three reference
> values are the frozen file's and are unchanged.

So the honest verdict on Surge: **on the linear structure it is an excellent
reference and we should agree with it exactly. On the nonlinearity it is not a
reference at all — at normal levels it is a linear 4-pole ladder with
Huovilainen's tuning polynomials bolted on.** Our input scaling, which is 27×
hotter, is the one that matches both the physics (a transistor ladder sees a
few hundred mV against 2V_T ≈ 50 mV) and the dedicated Minimoog emulation.

---

### 8.4 Result: the cutoff control does not mean the same thing across its range

> ### Re-run 2026-09-26 — the table below now measures the shipped filter, not revision 8
>
> Every revision of this table through 2026-09-18 measured `ours` against the
> pre-DR-0011 cutoff ROM and reported "7.92 pp against Surge Type 2's 0.62" as
> though it were the shipped filter's number. It was not — DR 0011 shipped the
> fix this section itself derives, below, on 2026-09-18, and the table was
> never re-run against it (issue #239). It is re-run here: every
> `ours`-prefixed row (`ours` plus its controls and probes) is rebuilt against
> the current `model/reference_rigs.OurLadder` by
> `tools/regen_discrimination_ours.py`, whose output is committed at
> `docs/reference-compare-results-shipped.json`. **The spread drops from
> 7.92 pp to 1.26 pp** — matching, independently, the number
> `docs/ladder-rung1-audit.md` already reported from a different instrument (a
> linearised loop, not this stepped-tone harness). What is left is the
> *resonance-dependent offset* this metric cannot see by construction — 105
> cents of travel across the resonance knob, unchanged by this re-run and
> tracked separately in `docs/ladder-rung1-audit.md`.
>
> `docs/reference-compare-results.json` itself is untouched by this re-run.
> Its `ours`-prefixed keys are `model/ladder_headroom.py`'s own deliberate
> revision-8 anchor — `frozen_reference_tracking("ours")`, bound there to the
> name `ours_rev8` and used to compute `drift_before_dr_0011_pp` — and
> `model/test_ladder_headroom.py::test_the_frozen_surge_row_is_not_a_like_for_like_limit_cycle`
> locks its 7.92 pp spread as a historical value. Overwriting that file's
> `ours` keys in place would have silently broken that anchor to fix this
> table, which is why the fresh data lives in a new file instead.
>
> **One caveat this section did not state, which decides how its comparison may
> be read**: §8.3 records that Surge Type 2 cannot self-oscillate, so its
> `tracking` row is a *decaying* resonant ring — 36 dB below ours in level in the
> frozen profile — where ours is a limit cycle. Comparing how much each *drifts*
> is fair; comparing the absolute offsets is not.

Self-oscillation pitch against **commanded** cutoff, at maximum resonance, over
six octaves:

| filter | 100 Hz | 800 Hz | 6400 Hz | **spread** |
|---|---|---|---|---|
| **ours** | −6.13 % | −5.12 % | −5.25 % | **1.26 pp** |
| Surge Type 2 (Huov) | −0.37 % | −0.23 % | +0.25 % | **0.62 pp** |
| Surge Type 1 (RK) | −2.18 % | −2.32 % | −3.46 % | **1.28 pp** |
| Diva *(knob calibrated on f_osc — circular, not evidence)* | +0.10 % | +0.04 % | +0.04 % | 0.09 pp |
| Mini V3 *(same, circular)* | +0.03 % | −0.15 % | −0.24 % | 1.02 pp |

A frequency-*independent* offset is one scale factor and is removable in an
afternoon; the **spread** is the defect. **Post-DR-0011, ours (1.26 pp) sits
between the two Surge models** (0.62 pp Type 2, 1.28 pp Type 1) rather than 6
to 13 times either, which is what the fix derived below (Huovilainen's `fcr`
tuning polynomial) predicts. Contract 17.12's open item (+7.2 % at 10 kHz,
±2 % from 400 Hz to 1.6 kHz) is the symptom this closed. What remains is not
this drift but the 105-cent resonance-dependent offset the admonition above
names — invisible to this metric by construction.

**The rest of this subsection is the historical derivation that produced
DR 0011**, kept for the record rather than re-run: its `ours` column below is
deliberately the pre-fix baseline, not the shipped filter, and the live
number is the table above. Applying `fcr` to our own cutoff lookup — one
multiply in the ROM build, no change to the datapath — and then one constant
scale:

| cutoff | ours | + `fcr` | + `fcr` × 1.030 |
|---|---|---|---|
| 200 Hz | −2.51 % | −2.98 % | **+0.03 %** |
| 800 Hz | −1.41 % | −2.66 % | **+0.31 %** |
| 3 kHz | +1.56 % | −2.61 % | **+0.42 %** |
| 10 kHz | **+6.85 %** | −3.90 % | **−0.89 %** |

**Worst error 6.85 % (115 cents) → 0.89 % (15 cents)**, measured at
res = 1.05. It also flattens the measured −3 dB corner: the corner/commanded
ratio goes from 0.752–0.818 (8.8 % drift) to 0.748–0.775 (3.5 %). The
constant differs with resonance — at maximum resonance the residual offset is
−8 % rather than −2.6 % — so the scale has to be chosen for a stated operating
point, and that choice is a decision record, not a measurement.

**This is the strongest result in the study**: we differ from every reference
in the same direction, the mechanism is identified in the source of a
reference implementing the same paper, and applying the published correction
removes 87 % of the error.

---

### 8.5 Result: the shipped tanh table, not the structure, is what our fifth harmonic measures

> ### Re-run 2026-09-26 — the fingerprint table's `ours` rows are the shipped filter; **the entry-count sweep below it is still revision 8**
>
> This subsection sits between §8.4's and §8.6's freshness banners and used to
> be covered by the single banner they replaced, so it says for itself which of
> its numbers are which (issue #239).
>
> **Fresh (post-DR-0011).** The three `ours`-prefixed rows of the fingerprint
> table immediately below — `ours`, `ours, 256-entry tanh table`, and the
> injected `one-tanh` control — are re-derived from
> `docs/reference-compare-results-shipped.json`, the file `tools/regen_discrimination_ours.py`
> writes and §8.4/§8.6 are built from. No new simulation was needed: the same
> committed `selfosc-*` rows produce them. Seven cells moved, all by ≤0.4 dB:
> `ours` h2 −95.5→−95.9 and h3 −40.0→−40.1; 256-entry h2 −95.9→−95.8, h7
> −105.2→−105.1 and matched −46.0→−45.9; one-tanh h7 −97.2→−96.9 and matched
> −38.7→−38.8. **No conclusion in this subsection changes**, and that is the
> expected result rather than a lucky one: DR 0011 retuned the cutoff ROM, not
> the nonlinearity or the structure, and this fingerprint is a measure of the
> nonlinearity. The reference rows (Surge, Diva, Mini V3) are the frozen
> `docs/reference-compare-results.json`'s and are untouched.
>
> **Still revision 8, deliberately not re-run.** Everything after the
> fingerprint table — the tanh **entry-count sweep** (8…1024 entries at
> res 1.1) and the prose derived from it — is a separate probe this issue did
> not rescope to cover, measured pre-DR-0011 and quoted here as the historical
> derivation. Its own res-1.1 cells drift by the same ≤0.1 dB where they
> overlap the fresh data (16 entries: h5 −70.1 against −70.2 now), and its
> conclusion — *128 entries converges* — is a statement about LUT resolution,
> not about cutoff tuning. Re-run it before quoting it as a shipped-filter
> number.

The self-oscillation fingerprint at each device's own maximum resonance, and
at **matched h3 = −42 dB** (equal drive into each nonlinearity):

| filter | onset | h2 | h3 | h5 | h7 | h5 − h3 | **at matched h3** |
|---|---|---|---|---|---|---|---|
| **ours** | res 1.02 | −95.9 | −40.1 | −63.4 | −62.6 | −23.4 | **−20.4** |
| ours, 256-entry tanh table | res 1.02 | −95.8 | −39.8 | −81.7 | −105.1 | −41.8 | **−45.9** |
| Surge Type 1 (RK) | 0.90 | *< floor* | −50.4 | −100.6 | −138.3 | −50.3 | — |
| Diva Ladder | 0.90 | **−33.9** | −36.0 | −71.4 | −107.1 | −35.3 | **−41.2** |
| Mini V3 | 0.78 | *< floor* | −41.8 | −70.5 | −87.4 | −28.7 | **−28.7** |
| Surge Type 2 (Huov) | **never** | — | — | — | — | — | — |
| *one-tanh — injected defect* | 1.02 | −94.8 | −39.7 | −66.2 | −96.9 | −26.4 | *−38.8* |

Three things come out of this, and only the first is comfortable.

**Our third harmonic sits inside the references' range** (−40.1 against −36.0,
−41.8 and −50.4). h3 is the measure of the nonlinearity's real curvature, and
on it we agree.

**Our fifth harmonic does not, and the excess is our tanh look-up table.**
Rebuilding the identical filter with a 256-entry table instead of the shipped
16 leaves h3 unchanged (−39.8 vs −40.1) and drops **h5 by 18 dB and h7 by
43 dB**. The full sweep, at res 1.1 — **pre-DR-0011, see the banner above**:

| entries | ROM bits | max table error | h3 | h5 |
|---|---|---|---|---|
| 8 | 128 | 0.0233 | −53.9 | −93.1 |
| **16 (shipped)** | **256** | **0.0060** | **−50.8** | **−70.1** |
| 32 | 512 | 0.0015 | −50.9 | −76.7 |
| 64 | 1024 | 0.00067 | −51.1 | −83.4 |
| 128 | 2048 | 0.00067 | −51.2 | −95.1 |
| 256 | 4096 | 0.00067 | −51.2 | −96.1 |
| 1024 | 16384 | 0.00067 | −51.2 | −97.1 |

A 16-segment piecewise-linear `tanh` has 16 corners in its derivative, and the
corners — not the saturation — are what emit the fifth and seventh. **128
entries converges** (2048 ROM bits against 256, a 1792-bit increase: the whole
ladder is 1,917 cells, so this is worth costing rather than guessing at). At
16 entries, the "structural fingerprint" this section was built around is
measuring our LUT resolution.

**The fingerprint does not support DR 0001 on its own.** At matched drive ours
sits at −20.4 dB, the references at −28.7 and −41.2, and **the injected
one-tanh defect at −38.8 — closer to Diva than we are.** A discriminator that
ranks a structure we know is wrong above the one we ship cannot be used to
argue the structure is right. With a 256-entry table ours moves to −45.9,
inside the references' spread, but by then the argument is about the table.
DR 0001 remains supported by circuit derivation; this measurement does not add
to it, and revision 1's claim that it did was resting on the leakage of §8.1.

**A fourth thing, about the references rather than about us.** Diva's ladder
emits h2 at −33.9 dB, *2.1 dB above its own h3*: its nonlinearity is
**asymmetric**, which an odd-symmetric `tanh` cannot be, and which a real
transistor ladder with a mismatched differential pair is. Ours and Mini V3 are
odd-symmetric (h2 below the floor, or −95 dB). This is a genuine disagreement
*among the references*, and it means the target itself is uncertain: at least
one of the two commercial emulations is modelling something the other decided
not to.

---

### 8.6 Result: slope, corner and resonance

> ### Re-run 2026-09-26 — `ours` rows below are the shipped filter, not revision 8
>
> Like §8.4, this subsection's `ours`-prefixed rows were last measured before
> DR 0011 (2026-09-18) and are re-run here against the current
> `model/reference_rigs.OurLadder` by `tools/regen_discrimination_ours.py`
> (issue #239); the reference rows (Surge, Diva, Mini V3) are unchanged,
> reused from the frozen `docs/reference-compare-results.json`. The stopband
> slope and the dropped-pole control barely move — DR 0011 changes the cutoff
> ROM's tuning, not the ladder structure or the pole count, so a
> structure-dependent number is not expected to move and does not. The
> resonant-peak table and the corner ratio **do** move, because both are
> downstream of the cutoff ROM the resonance compensation (DR 0006) is derived
> from.

**Stopband slope.** Ours −21.4 to −21.9 dB/oct over 2.2–7× the measured
corner, fit residual 0.22–0.35 dB. The references over the same band: Surge
Type 2 −19.4 to −21.5, Surge Type 1 −19.2 to −21.4, Diva −18.2 to −21.2,
Mini V3 −17.6 to −21.6. **An ideal analogue 4-pole gives −17.6 dB/oct over
that band** (closed form, in the report's `ideal4p` column) — 24 dB/octave is
the asymptote, not what any 4-pole does two octaves above its corner. So the
24 dB/oct claim holds: ours is the *steepest* of the five, and the injected
dropped-pole control reads −11.7 to −11.8 — still far outside the real
filters' cluster, so the control still starts red.

**−3 dB corner against commanded cutoff.** Nobody's ratio is constant: ours
0.771–0.800 — rising from 100 Hz to a peak near 800 Hz and easing back, not
the pre-DR-0011 monotonic drift, and less than half the span (0.029 against
0.066) — Surge Type 2 0.627→0.566 and Mini V3 0.697→0.730 (drifting the other
way), Diva 0.584–0.697 with no clean trend. The corner is the weaker
discriminator of the two frequency measurements — it moves with resonance and
with the passband reference band — and §8.4's self-oscillation pitch is the
one to read.

**Resonant peak, at 0.9 of each filter's own self-oscillation threshold**,
against input level:

| filter | −60 dBFS | −48 | −36 | −24 | −12 dBFS |
|---|---|---|---|---|---|
| **ours** | 20.7 dB / Q 11.8 | 20.6 / 11.5 | 20.5 / 11.4 | 15.0 / 6.7 | **8.3 / 2.8** |
| ours, 256-entry table | 17.9 / 8.8 | 17.7 / 8.6 | 18.3 / 9.2 | 15.1 / 6.9 | 8.3 / 2.8 |
| Surge Type 2 | 21.9 / 12.7 | 21.9 | 21.9 | 21.9 | **21.9 / 12.7** |
| Surge Type 1 | 22.0 / 12.7 | 22.0 | 22.0 | 22.0 | 22.5 / 13.2 |
| Diva | 17.7 / 9.7 | 18.4 | 19.7 | 20.9 | 20.0 / 10.6 |
| Mini V3 | 14.1 / 3.4 | 14.1 | 13.7 | 14.4 | 16.6 / 7.5 |
| *2-pole — injected defect* | 2.0 / — | 2.1 | 2.1 | 2.0 | 1.6 / — |

**At small signal the five agree**: 22.0, 21.9, 20.7, 17.7, 14.1 dB. Ours now
sits in the middle of the spread, not at its top — DR 0011's cutoff-ROM
retuning moves the resonance-compensation coefficient (DR 0006) derived from
it, and this table is downstream of that.

**With drive we are the only one whose resonance collapses**: −12.3 dB from
−48 to −12 dBFS, against +0.0, +0.5, +1.6 and +2.5 for the four references.
This is the "thickens vs flat-tops" question and the answer is not flattering,
but **it is confounded** and the confound must be stated: at 0.9 of its own
threshold ours reaches Q 11.8 while Mini V3 reaches only Q 3.4, so our
internal signal is roughly three and a half times larger before the
nonlinearity ever sees it. Our *input-referred* saturation threshold
(−6.2 dBFS) agrees with Mini V3's (−5.7 dBFS) to within a dB. What differs is
how much Q each knob buys, which is a resonance-law difference, not a
gain-staging one. **Reported as a measured difference with its confound
named, not as a defect.**

---

### 8.7 The controls: does any of this have power?

Three deliberately-wrong ladders through the identical measurements:

| injected defect | what it must move | measured | ours |
|---|---|---|---|
| **dropped pole** (2 stages) | the slope, the peak | −11.7 to −11.8 dB/oct; peak 2.0–2.1 dB, no Q at any drive | −21.4 to −21.9; 20.7 dB, Q 11.8 |
| **cutoff ROM read 30 % high** | the corner | corner/commanded 0.99–1.04 | 0.771–0.800 |
| **one-tanh** (four linear poles, one saturating element in the feedback — the structure DR 0001 rejected) | the fingerprint | h5 − h3 at matched h3 **−38.8 dB** | **−20.4 dB** |

Each is separated from ours by far more than the spread between the three
references, so the measurements can see a broken filter. The one-tanh control
carries the caveat of §8.1: it separates by 21 dB at res 1.3, 8 dB at 1.05 and
**3 dB at res 2.0**, where its hard input clip dominates — so the structural
probe has power only at a stated resonance, and `model/test_reference_compare.py`
asserts *both* the separation and its disappearance, so that the caveat cannot
quietly stop being true.

`_Variant(stages=4, nonlin='every')` is asserted bit-exact against `LadderFx`,
as in the acceptance suite: a control that has drifted measures its own drift.

---

### 8.8 Ranked: what this says to fix

1. **Apply Huovilainen's `fcr` tuning polynomial to the cutoff ROM** (contract
   17.12). One multiply at ROM-build time, no datapath change. Worst
   self-oscillation tuning error 6.85 % → 0.89 % with one accompanying
   constant; corner-ratio drift 8.8 % → 3.5 %. We are outside all four
   references in the same direction and the fix is published.
2. **Cost a wider `tanh` table.** 16 → 128 entries removes 25 dB of excess
   fifth harmonic and 43 dB of seventh at self-oscillation, for 1792 extra ROM
   bits against a 1,917-cell datapath. Whether that is audible is a separate
   question and should be asked with a listening test, not asserted here;
   whether it is affordable is an area question and should be measured, not
   guessed.
3. **Decide whether the resonance law is right.** Ours buys Q 14 at 0.9 of
   threshold where Mini V3 buys Q 3.4. That is not a defect on any evidence
   here, but it is the mechanism behind the only property on which we behave
   unlike all three references, and nobody has chosen it deliberately.
4. **Nothing here impeaches the 24 dB/octave claim or the −3 dB corner**, and
   the third-harmonic depth at self-oscillation is inside the references'
   range.

---

### 8.9 What is still not established

- **None of this is a Minimoog.** It is three emulations, two of them
  closed. Where they disagree with each other — Diva's asymmetric
  nonlinearity, Mini V3's Q 3.4 against Surge's Q 12.7 at the same fraction
  of threshold — the target is genuinely uncertain and no amount of averaging
  would fix that.
- **Surge Type 2 is a linear reference.** Every nonlinearity result above
  rests on Mini V3, Diva and Surge's RK model, i.e. on two closed products and
  one model of a different paper.
- **Diva's and Mini V3's cutoff scales have no units.** Their tracking rows are
  circular by construction and are printed only so the circularity is visible.
- **Nothing here is a listening test**, and none of it should be reported as
  one. The one measurement that would settle the structure question against
  the real instrument is a few seconds of a Model D's filter self-oscillating
  with the mixer at zero; `docs/moog-recording-protocol.md` §4.1 is how to
  capture it, and `model/moog_probe.py` reads it the day it exists.

---

## 9. Reproducing, and one caveat

### Provenance of the 2026-09-18 re-run

| | |
|---|---|
| tree | branch `measure-discrimination-current`, **clean**. Measured off `main` `c752145`; rebased onto `main` `a57d469` (which contains PR #132, `ba14af2`) and re-verified identical |
| model sha256 (drums_fx + modal_fixed + harness) | `3cf0210cca1aef01` (sixteen-sound run) |
| corpus | `tidalcycles/sounds-tr808-fischer` `85fbecf`, **116** WAVs, sha256 `e3ad2d77a79cda4a` |
| split hash | `ab381e78f9f4cc41` (sixteen); `6738610a454806f8` (the published eight, reproduced byte-for-byte) |
| commands | `model/discrimination_run.py --refs /tmp/tr808-ref --sounds 16 --features both`; `model/discrimination_trajectory.py --refs /tmp/tr808-ref`; `pytest model/test_discrimination.py model/discrimination_features.py model/discrimination_trajectory.py -q` |
| self-tests | **38 passed** (was 12) |
| results | `docs/discrimination-results.json` (eight-voice), `docs/discrimination-results-16.json` (sixteen), `docs/discrimination-run.txt`, `docs/discrimination-trajectory.txt` |

### Provenance of the #161 conditioning re-derivation (§3.3)

| | |
|---|---|
| tree | branch `fix-condition-boundary`, **clean** (`dirty=false`, recorded by the tool itself) |
| commit | `9c1e69fe83d7`, `node/D-drums-bitexact-75-g9c1e69f` |
| model sha256 (drums_fx + modal_fixed + harness) | `c8d273a057a9966f` |
| corpus | `tidalcycles/sounds-tr808-fischer` `85fbecf`, **116** WAVs, sha256 `6e6af82198eb5b46` (sorted relative path then bytes, so the digest does not depend on walk order — a different definition from the `e3ad2d77a79cda4a` above, not a different corpus) |
| split hash | `ab381e78f9f4cc41` — **#148's, unchanged** |
| command | `model/condition_boundary.py --refs /tmp/tr808-ref --json docs/condition-boundary-results.json` |
| self-tests | `pytest model/test_discrimination.py -q` — **23 passed** (was 20; the four new conditioning controls, one of which replaced none) |
| results | `docs/condition-boundary-results.json` |

Both columns come from **one render per setting**, conditioned twice, so
nothing but the boundary condition differs between them. Run twice; identical
to the printed precision.

**Wrong-then-right, this correction: 0 of 4.** All four controls were written
before the repair and all four were red against HEAD
(`test_conditioning_does_not_answer_an_impulse_with_a_pedestal`,
`test_a_measurement_does_not_depend_on_where_the_record_begins`,
`test_the_two_sides_arrive_differently_prepared_and_it_is_recorded`, and
`test_the_published_boundary_is_still_reachable_and_still_wrong`, which keeps
`legacy=True` reproducing the defect so the delta stays a measurement rather
than an assertion). No published figure was tuned; three of the repaired
knob-equivalents are **worse** than the ones they replace.

**Not re-derived by that command:** the trajectory in
`docs/discrimination-trajectory.txt` also runs through `condition()` and is
therefore also affected. It is #152/#160's subject and is not touched here.

**Which measurement path, and PR #132.** PR #132 repairs four estimator
defects in `model/audio_measure.py` and `tools/run_case.py`. It was open when
this re-run started and **merged to `main` as `ba14af2` while it was running**;
the branch has since been rebased onto it and **every number above re-measured
identically** (split hash `6738610a454806f8`, pooled 0.868 [0.77,0.94],
BD 2.5, SD 3.4, OH 6.9, HT 7.3, LT ≥10 — unchanged to the tenth).

That it changes nothing is checkable rather than assumed, and the check is
the reason: PR #132 touches `audio_measure.py`, `run_case.py`,
`tools/probes/` and the scorecard results, and **none of the eleven files
this study uses**.
`model/test_discrimination.py` imports nothing from `model/audio_measure.py`.
Its features are its own log-mel and MFCC code; its diagnostics are its own
`measure_tau`, `measure_f0`, `band_share` and `partial_ratio`. **No number in
this document routes through `band_energy`, so the windowing fix worth up to
6 dB changes none of them.** If the harness is ever made to import the shared
estimators, this paragraph stops being true and every figure needs re-deriving.

**One thing PR #132 does touch, indirectly.** `drums_fx.CY_DECAY_T20`'s note
records that the knob-2.5 cymbal file "is the one file of the five whose
length is shorter than its own decay" and excludes it — which is exactly
#118's truncation defect, a backward integral reporting the cut rather than
the decay. The cymbal DECAY law fitted here does not use a Schroeder T20 at
all; `measure_tau` regresses the log envelope over −3..−27 dB and needs only
27 dB of record, and it reads that file at 258.5 ms, in order with both its
neighbours (158 / **258.5** / 393.6 / 466.0 / 510.4 ms). Recorded because the
two methods disagree about which files are usable, and the drums contract
took the other one.

Nothing in this test was tuned on a held-out setting. The pre-registered
split, the equivalence margin and the minimum-N rule were fixed before any
accuracy was computed, and `model/test_discrimination.py`'s own pytest suite
checks each of them (window shorter than the shortest reference file, features
rate-independent across 44.1 k and 48 k, floor clamp hides a −76 dBFS
converter floor, level matching removes a pure gain, a recording never on both
sides of a split, and that 10-of-20 is reported as an upper bound of 0.68
rather than as "indistinguishable").

Added with the sixteen-sound extension, and each one exists because it caught
something: the eight default sounds' register image is byte-identical under
`kit_with_sounds`, so no previously published arm moved; the stop struck comes
from `SOUND_STOP` and not from `STOP_NAMES.index`, which raised `ValueError`
on LC, MC, HC and MA; all sixteen render non-silent and each shared circuit's
two sounds separate by 1.2–1.5× rms; the extra columns append and never
reorder the original 320; the CV grouping does not move between processes;
a sound with no knob can produce no knob-equivalent; and a frozen ruler makes
the two distances commensurate.
