# The TR-808's variation from itself, measured — and the part that cannot be

**One deliverable, issue #111: how much does a real TR-808 differ from itself,
per metric, so that every tolerance on the scorecard can be compared against
something measured.**

Instrument: [`tools/measure_repeatability.py`](../tools/measure_repeatability.py),
validated by [`tools/test_measure_repeatability.py`](../tools/test_measure_repeatability.py).
Numbers: [`bd-repeatability-results.json`](bd-repeatability-results.json).

```
.venv/bin/python tools/measure_repeatability.py --all --json docs/bd-repeatability-results.json
#   exit 2
```

Exit 2 is the headline. Read on for what exit 2 covers and what was measured
anyway.

---

## 1. The premise was wrong: there are no repeated takes

`refaudio/README.md` and #111 both state that the 808 From Mars clean bass drum
is **24 settings × 6 takes = 144 files**, and that it is *"the only place in the
corpus where the same machine plays the same thing more than once."*

**It is not. The trailing `01`…`06` is the TONE knob.** The grid is
2 chains × 2 accents × 6 DECAY × 6 TONE = 144, with **no take axis in it at
all.**

Three independent lines of evidence, and the recordings are asked before the
file names are.

**What the recordings do across `01`→`06`,** over all twelve Digital
decay × accent groups:

| metric | span across `01`→`06` | Spearman ρ vs index |
|---|--:|--:|
| band energy 200–2000 Hz | **+3.97 dB** | **+1.00 in 12 of 12 groups** |
| early/body energy | **+0.89 dB** | +1.00 in 11 of 12 |
| Pitch trajectory (f0) | +0.003 Hz (0.006 %) | — |
| decay T20 | +0.19 ms (0.5 %) | — |

One component carries **65–96 %** of the between-recording variance in every
group, and its loading is monotone in the trailing index. A high band that
climbs 4 dB while f0 and T20 sit still is the bass drum's **TONE** control
mixing in the attack pulse. Under a null of random ordering P(ρ = +1) is 1/720
per group; twelve of twelve is 10⁻³⁴.

**What the vendor says,** in `catalog.json`'s own notes for the pack:

> A = No Accent, B = Accent, C = More Accent
> Bass Drum / Clean — *"Multi-Sampled Levels of 808 **Decay and Tone** at 2
> accent levels"*

**What the naming convention does elsewhere.** The voices with no knob to sweep
— Cowbell, Rim Shot, Claves — are 2 accents × 2 chains = **four** clean files
each and carry **no trailing number**. The congas, *"2 accent levels at 11
tunings"*, carry `01`…`11`. The snare, *"levels of tone and snappy at 3 accent
levels"*, is 2 × 3 × 6 × 6 = 216, exactly its clean count. **The trailing number
is a knob index wherever it appears.**

So the take-to-take question, as #111 asks it, is **REFUSED**: there is nothing
to take a spread over.

### The one place it could still be answered, and why it was not

`808_loops_from_mars.zip` has a **`WAV/03. Bass Drum/4x4`** folder — 27
bass-drum-only 4/4 loops, up to 16 bars, in which the same machine strikes the
same setting four to sixty-four times **inside one continuous take**. That is
better evidence than six edited takes would have been, because the setting
demonstrably cannot move mid-loop.

It is **the one 808 pack of the three not present on this host** (80 of the
catalogue's 89 packs are). Whoever has it should run `--audit` on those loops
first — a vendor who copy-pasted one strike would show cross-correlation 1.000
— and then the real take-to-take number is a short job.

---

## 2. The estimator, measured before the machine

An estimator with its own scatter measures itself. Three controls ran first.

| control | result |
|---|---|
| **determinism** — six runs of one array | bit-identical |
| **ground truth** — synthetic BD, closed-form answer | f0 exact; T20 within **0.008 %** of ln(10)·τ |
| **editing noise** — one *real* recording, six copies differing only by where the vendor's editor cut the head and tail | below |

**The editing-noise floor convicts the apparatus.** A 4-sample (0.09 ms) change
in the head trim cannot be the machine:

| metric | span over six editor-trim variants |
|---|--:|
| Pitch trajectory | 0.0004 Hz (0.0008 %) |
| decay T20 | 0.0003 ms |
| early/body energy | 0.064 dB |
| attack | 0.091 ms |
| **body spectrum (as shipped)** | **0.568 dB** |
| **body spectrum (#101's fix applied)** | **0.0005 dB** |

That 0.568 dB is **19 % of its own 3.0 dB tolerance, from the head trim alone**
— #101's `sosfiltfilt` edge, found here independently of the conga work, on a
different voice. With 10 ms of silence in front of the strike the same span is
**1100× smaller**.

**#118, demonstrated more starkly than #118 states it.** Cutting a record whose
true T20 is 1192 ms down to 400 ms: `schroeder_t20` reports **282 ms — a −76 %
error — and its `tail_db` guard reads −43 dB against a −35 dB requirement, so it
does not refuse.** The length probe added here fires at −11.2 %. On the
unaltered corpus that probe reads **−0.00 % at every decay position**, so these
files are *not* truncation-limited and their T20 can be trusted.

---

## 3. What the corpus can answer: the same machine, recorded twice

Samples From Mars recorded this machine twice — the current edition and the
superseded legacy edition, indexed as a second session. The two editions
cross-correlate at **0.9960**, so they are two recordings and not one re-pressed.

**The DECAY letters do not correspond between sessions,** so most settings
cannot be compared at all:

| | current T20 | legacy T20 | apart |
|---|--:|--:|--:|
| **Decay A** | **38.86 ms** | **38.32 ms** | **1.4 %** |
| Decay B | 87.48 | 98.32 | 12.4 % |
| Decay C | 282.29 | 220.53 | 21.9 % |
| Decay D | 539.82 | 395.84 | 26.7 % |
| Decay E | 1193.71 | 608.51 | 49.0 % |
| Decay F | 2249.77 | 719.26 | 68.0 % |

One letter agreeing to 1.4 % while the rest diverge monotonically is what a knob
against its **end stop** looks like — the one position reproducible between
sessions without calibration. Everything below is measured there, across the six
TONE positions.

| metric | session A | session B | \|diff\| | vs estimator floor |
|---|--:|--:|--:|--:|
| **f0** | 50.612 Hz | 49.203 Hz | **1.385 Hz (2.74 %)** | 3900× |
| **decay T20** | 38.750 ms | 38.242 ms | **0.505 ms (1.30 %)** | 2000× |
| **early/body energy** | 0.972 dB | 0.566 dB | **0.421 dB** | 6.6× |
| **band split, #101-corrected** | −11.502 dB | −11.617 dB | **0.159 dB** | **0.28×** |
| band split, as shipped | −11.973 dB | −11.124 dB | 0.855 dB | — |
| **attack** | 6.168 ms | 6.081 ms | **0.079 ms (1.28 %)** | **0.88×** |

**Two of these are not measurements of the machine.** The shipped band split and
the attack both move *less* between two recording sessions than they move when
the file is trimmed by four samples. Their numbers describe the apparatus.

**f0 is the cleanly attributable one.** The TR-808 bass drum offers LEVEL, TONE
and DECAY and **no tuning control**, so no knob-setting error can enter it. The
tool bounds what little could: dƒ₀/dln(T20) is +0.179 Hz and the two sessions'
T20 differ by 1.38 %, so **at most 0.0025 Hz of the 1.445 Hz difference is knob
position — 0.17 % of it.**

---

## 4. Every tolerance, beside what it has to beat

A tolerance has to sit **above** what the apparatus does on its own and above
what the machine does on its own, and **below** what the machine's own knobs do
— otherwise it cannot tell two settings apart.

| tolerance | value | machine | apparatus | mach ÷ appar | tol ÷ mach | tol ÷ knob travel |
|---|--:|--:|--:|--:|--:|--:|
| energy ratio — band split | 3.0 dB | 0.159 dB | 0.568 dB | **0.28** | 18.9× | 0.19 |
| energy ratio — early/body | 3.0 dB | 0.421 dB | 0.064 dB | 6.6 | 7.1× | 0.17 |
| frequency — f0 | 5.06 Hz (10 %) | 1.385 Hz | 0.0004 Hz | 3948 | **3.7×** | **1.38** |
| time — decay | 19.4 ms (50 %) | 0.505 ms | 0.0003 ms | 2003 | 38.3× | 0.009 |
| time — attack | 3.08 ms (50 %) | 0.079 ms | 0.091 ms | **0.88** | 38.8× | 0.20 |

"Knob travel" is the union over both accent settings — everything the machine's
own controls do to that metric across the whole 6 decay × 6 tone grid, twice.

### The four findings

**No tolerance on the board is finer than the machine's own floor.** The conga
failures at 1.036 and 1.052 (#106) are **not** explained by machine spread: the
machine moves 0.16 dB on a band split and the tolerance is 3.0 dB, nineteen
times that. #111's suspicion that *"3 dB is finer than the machine's spread"* is
**refuted** for this voice. The ±10 % f0 sensitivity the conga agent measured —
2.46/2.71/1.54 dB of body spectrum — is a statement about f0 *authority over the
metric*, not about how far f0 actually wanders, and this says f0 wanders 2.74 %,
not 10 %.

**Two metrics are dominated by the apparatus, not the machine.** The shipped
band split (mach ÷ appar = **0.28**) and the attack (**0.88**). Fixing #101
moves the band split from 0.855 dB of session-to-session difference to
**0.159 dB** — five sixths of what looked like the machine was the window edge.
**Until #101 lands, no band-split result on the board is a measurement of
anything the TR-808 did.** That is nineteen drum results.

**The f0 tolerance is the one with no headroom, in both directions.** It is only
**3.7×** the machine's own spread — the tightest ratio on the board, and it
could not be tightened below about 3 % without becoming scoring noise. And it is
**1.4× everything the machine's own controls can do to the bass drum's f0**
(both accents, both grids, f0 spans 50.55–54.21 Hz = 3.66 Hz total; the
tolerance is 5.06 Hz). **On the bass drum, a ±10 % f0 tolerance cannot
distinguish any two settings of the machine** — it is the only tolerance on the
board wider than the travel of the thing it is meant to score. It is simultaneously too loose to discriminate and too close to the
noise to tighten. This is the tolerance to revisit, not the 3 dB one.

> **Revisited, #127.** The bass drum's f0 tolerance is now an absolute
> **2.370 Hz** (`run_case.F0_DISCRIMINATION_BAND`), not 10 % of the reference.
> The two bounds in the paragraph above are the two bounds it is derived from —
> the floor is `session_to_session` `abs_diff_max` (1.5345 Hz; the largest
> observed pair rather than the 1.3852 Hz median this section quotes, so that
> no observed pair of recordings of the same machine can fail) and the ceiling
> is `knob_travel["both accents"]` `grid_span` (3.6612 Hz).
> <!-- claim: test=tools/test_run_case.py::test_f0_discrimination_band_matches_the_measurement -->
> The point taken in
> that band is their geometric mean, the unique value whose two ratio margins
> are equal — 1.54× either way — so no fraction is chosen by hand.
> <!-- claim: test=tools/test_run_case.py::test_f0_discrimination_tolerance_is_equidistant_from_both_failures -->
> At 4.76 %
> of D01A's 49.78 Hz reference it sits above the ~3 % this section says f0
> could not be tightened past without becoming scoring noise; the derivation
> was not fitted to that limit and agrees with it.
>
> Both directions are carried as controls: the new bound fails the machine's
> own grid extremes, 3.66 Hz apart, which the 10 % rule passed
> <!-- claim: test=tools/test_run_case.py::test_POSITIVE_CONTROL_new_f0_check_separates_settings_the_old_one_could_not -->
> and it still passes a difference the size of the measured session spread,
> so it scores the instrument and not the recording session.
> <!-- claim: test=tools/test_run_case.py::test_NEGATIVE_CONTROL_new_f0_check_passes_the_machines_own_session_spread -->
>
> The root cause was the *shape*, not the number: reference 1.7's ±10 % is a
> **unit-to-unit** component spread, and the reference on the other side of a
> scorecard comparison is a recording of **one** unit. `LINE_SEARCH_FRAC`
> keeps the ±10 % for exactly that reason — a search window *does* need the
> unit-to-unit band.
>
> **Only the bass drum, because only the bass drum has both numbers.** The
> three other f0 cases on the 10 % rule (D04A/D06A/D08A) were checked, not
> merely left alone: their tuning pot spans ±10 % of nominal, so their travel
> is ~20 % against a 10 % tolerance — half the travel, not 1.38× it.
> <!-- claim: test=tools/test_run_case.py::test_the_other_f0_cases_were_CHECKED_not_merely_left_alone -->
> Their
> session-to-session floor is unmeasured, so the floor half of a
> discrimination band cannot be derived for them and none was invented. **The
> general question this section raises — whether the same shape defect recurs
> elsewhere on the board — is still open**, and it closes one voice at a time,
> by measuring one voice at a time.

**The 50 % time tolerance is 38× the machine's floor** and about six tenths of
one of the vendor's six DECAY steps. It has an order of magnitude of unused
room.

---

## 5. What this does not establish

**It bounds one machine.** Every real-808 recording reachable here descends from
one unit, and nominally different "808" sets cross-correlate at 1.000 — the same
events re-pressed. **Unit-to-unit is the larger term and there is no data for it
here at all.** These are floors. A tolerance already tighter than a floor is
definitely wrong; one wider than it is merely unproven.

**Session-to-session is not take-to-take.** It is larger, and it is the more
relevant quantity for a scorecard that compares a render against one recorded
take. The take-to-take number is now measured — see §5a — and it is smaller, as
the upper-bound argument required.

**It bounds the bass drum.** BD repeatability is not cymbal repeatability. It
suggests an order of magnitude for the rest and no more.

**Whether the legacy edition is the same physical unit is undocumented.** Same
vendor, same machine per the catalogue's own description, but not asserted by
anyone. If it is a second unit, the 2.74 % f0 figure is *unit-to-unit* and the
take-to-take floor is smaller still. Either way it is an **upper bound** on
take-to-take, which is the direction every conclusion above relies on.

**Two dB metrics carry a chain confound.** The two sessions' console and
converter chains differ (the current one is documented as API 1608 → Apogee
Symphony MKII; the legacy one is not documented at all). Frequencies and times
are chain-invariant; the dB numbers are upper bounds.

## 5a. Take-to-take, from the 4x4 loops — 2026-10-03, tool commit `80ebf0e`

*Issue #111, unblocked by the operator's 2026-10-02 rulings (the library is
already owned; a private S3 working copy exists). One unit's take-to-take
spread. It says nothing about unit-to-unit variation, which remains the larger
term and remains unmeasured.*

**Source.** `808_loops_from_mars.zip` fetched with `tools/refaudio_s3.py`
(`REFAUDIO_S3`, profile `batch-runner-submit`); the whole archive hashed to the
SHA-256 in `refaudio/catalog.json` before a byte was extracted. Audio was held
in gitignored `refaudio/cache/` and deleted afterwards; **only derived metrics
are committed** (`docs/bd-repeatability-results.json`, key `take_to_take`).
Reproduce: `tools/measure_repeatability.py --loops --json
docs/bd-repeatability-results.json`.

**Audit (passed — not REFUSED).** 27 loops in `4x4/`. The audit does not trust
the folder name: onsets are detected and counted, and a loop is admitted only if
it holds **one strike per beat** across the whole file (spread-blind: strikes
are never compared to each other to decide admission). 13 loops pass; 14 are
patterns, double-time or layered loops and are excluded with the reason
recorded. Among the 13, **no consecutive pair of strikes is a duplicate**: after
integer alignment and best gain every pair leaves a residual far above 24-bit
quantisation (residual rms 0.2–2 % of signal on the 10 loops that hold one
setting; correlation 0.9993–1.0000, **not** a bit-identical 1.000). A copy-pasted
strike *is* flagged by the same code (control below), so the audit could have
refused and did not.

**Measure.** One setting per loop, strikes sliced from 8 samples before each
onset to the next onset (one beat), first strike of each loop dropped (decided
beforehand: it alone follows silence). Estimators are the board's own, imported.
"Take-to-take" below is the **median |difference| between two strikes of one
loop**, median across loops — the same statistic as the session-to-session floor
in §2/§3, so the ratio is like for like.

| metric | take-to-take | session-to-session | take ÷ session | scorecard tol | tol ÷ take | loops read |
|---|--:|--:|--:|--:|--:|--:|
| f0 | 0.0014 Hz | 1.385 Hz | 0.001 | 5.06 Hz | 3,700× | 12 of 13 |
| band split (padded) | 0.0073 dB | 0.159 dB | 0.046 | 3.0 dB | 410× | 12 of 13 |
| early/body energy | 0.0153 dB | 0.421 dB | 0.036 | 3.0 dB | 196× | 12 of 13 |
| attack | 0.0113 ms | 0.079 ms | 0.143 | 3.08 ms | 272× | 12 of 13 |
| decay T20 | 0.0199 ms | 0.506 ms | 0.039 | 19.4 ms | 974× | **2 of 13** |

**What it says.** Strike to strike, this machine is nearly deterministic: every
metric's take-to-take spread is 0.1 %–14 % of the session-to-session figure, so
the session numbers were indeed an upper bound, and a loose one. **No scorecard
tolerance is finer than take-to-take variation** — the tightest ratio is 196×.
The earlier suspicion that the board's tolerances are scoring noise is refuted
at the take level as well as the session level. Combined with §3, the machine's
variation that a tolerance has to absorb is dominated by session/unit
differences, not by the strike.

**Where the numbers should not be over-read.**

- **Decay is 2 loops, not 13.** A one-beat slice cuts the tail of every
  long-decay strike, and `schroeder_t20`'s own tail guard (correctly, #118)
  declines to read them. Ten of the twelve readable loops yielded no decay at all
  (counted as refused, never replaced by a number) and Tite yielded 16 of 31
  strikes. The decay figure rests on Fluid and Tite only.
- **Attack is sample-quantised and partly constructed.** Slices are aligned at
  each strike's 2 % crossing, which is also how the attack estimator finds its
  origin, so six loops read attack spread of exactly 0, four read 1 sample (0.0227 ms),
  Goosed 3 samples, and Tite (alternating strikes) 0.70 ms. The figure is an upper bound on resolution, not a
  measurement of the machine's attack jitter.
- **Several take-to-take figures sit at or near the estimator's own floor**
  (`--self-test` editing noise: f0 0.00035 Hz span, band split 0.0005 dB). f0
  (4×) and band split (16×) are above it; do not read the take-to-take figure as
  more precise than the estimator.
- **Three admitted loops are not one setting.** Alternate, Tite and Trio have
  consecutive-strike correlation 0.96–0.99 (alternating or accented strikes); Punch's
  first strike differs from the rest and its slices could not be read at all
  (every metric refused). The table's median across loops is robust to them, but
  they are in the per-loop data, not hidden.
- **The audit rules out bit-level copy-paste, not every re-use.** A vendor who
  arranged one recorded strike and then re-amped or re-recorded the whole loop
  would pass this audit and show exactly this kind of tiny residual. Nothing in
  the file distinguishes that from a machine that is just that repeatable; the
  vendor's notes do not say. If it happened, the true take-to-take spread is
  *larger* than reported, so the "no tolerance is finer than the machine"
  conclusion — which needs an upper bound on tolerance-to-machine ratio, i.e. a
  lower bound on the spread — is the claim to weaken, not strengthen.
- **Controls carried** (`--loops` refuses to report if any fails; also in
  `tools/test_measure_repeatability.py`): a pasted loop is flagged 100 %
  duplicate; independent-noise strikes are flagged 0 %; all 16 onsets are found;
  a double-time pattern is refused; a 3 Hz f0 difference injected into every other strike is measured as
  3.002 Hz and a clean loop as 0.00003 Hz. **Wrong-then-right:** the first
  version of the alignment used a raw dot product and aligned *identical* strikes
  tens of samples off, so the pasted-loop control read as a performance (residual
  1.4 %); caught by that control, not by inspection, and fixed by normalising.
  The audit's first form also compared every strike to strike 0, which read two
  pasted-looking loops as varied because strike 0 alone follows silence; it now
  uses consecutive pairs.

---

## Provenance

`808-from-mars.zip` (SHA-256 verified against `refaudio/catalog.json`), `808_loops_from_mars.zip` (§5a; SHA-256 verified, fetched with `tools/refaudio_s3.py`) and
`808_from_mars_legacy.zip`, `…/01. Bass Drum/Clean/Digital/`, 144 + 144 files,
each member's size checked against the committed `refaudio/index/`. Fetched with
`tools/refaudio_local.py`. No audio is committed.
