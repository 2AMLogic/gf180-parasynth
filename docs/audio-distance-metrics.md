# Audio distance metrics: should one enter our measurement loop, and in what role

**One deliverable: whether any learned or spectral audio-distance metric should
enter this project's measurement loop, and in exactly what role.**

Instrument: [`tools/probes/audio_distance_floor.py`](../tools/probes/audio_distance_floor.py).
Numbers: [`audio-distance-metrics-results.json`](audio-distance-metrics-results.json).
Every figure tagged **[measured]** below came out of that probe, on this
repository's own signals, **measured at `ba14af2` — after #132's repaired
measurement path**, and reproducible with:

```sh
.venv/bin/python tools/probes/audio_distance_floor.py --json out.json
#   exit 0
```

> **Known stale (#533).** Every `mel_dac` figure below that came from a Fischer
> file (44.1 kHz; E2, E3, E4, E6, E7, E9) was measured with the filterbank built
> for 48 kHz, because `distances()` did not pass the sample rate. Measured size
> on the `bd8/BD5050.WAV` 1-sample floor: 0.011947 (as printed) vs 0.011846
> (true rate), about 0.9 %; 0.1-0.6 % on the other anchors spot-checked. The
> code is fixed (`sr` is now a required argument), but **the tables and
> `audio-distance-metrics-results.json` have NOT been regenerated**: the
> reference corpus was not on the host that fixed it, so no number here has
> moved yet. Our own renders (`dx.SR`) were never affected. `mss_*` and
> `mrstft` columns do not use the mel filterbank and are unaffected.

It was drafted against `c752145` and re-measured in full when #132 landed,
because #132 changes the `prepare()` this probe depends on. **Every conclusion
survived; two got stronger.** What changed is in
[§6](#6-the-alignment-problem-which-gates-everything-else).

---

## The answer

**Adopt none of them as a target. Adopt one as a guard, narrowly, and only
after the alignment problem below is solved.**

The short reason, and it is measured rather than argued: on our own bass drum,
a multi-scale spectral distance reads **0.307** for an f0 error the size of the
TR-808's own session-to-session spread — a difference we have already decided
is *not* a defect — and **0.336 to 0.406** for the tom pitch-drop defect that
ships today at ×1.7 against hardware measured at ×1.06–×1.24. **The defect and
the floor are the same number**, and on two of the four distances three of the
four defect rungs land *below* the floor. The board's own per-property
estimator separates the same five renders cleanly and monotonically, 43.9 Hz
down to −0.97 Hz.

Worse, all four distances **rank the tom defect wrongly**: every one of them
puts ×1.14 *further* from the shipped ×1.7 than ×1.06 is, which is backwards.
That is Turian & Henry's published result reproduced on our signals, on the
exact defect we most need to see.

**The guard role, by contrast, is confirmed and by a wide margin.** Three
defects a fixed-point drum machine can actually have — a −40 dBFS 12 kHz tone,
a 6-bit requantisation, a non-decaying tail noise floor — **pass every one of
the board's per-property BD metrics** with an order of magnitude to spare, and
the DAC multi-scale mel distance flags them at **71×, 238× and 356×** its own
floor ([§7](#7-the-guard-hypothesis-tested)). Our per-property coverage is three
properties per voice, and that is a real hole of exactly this shape.

**One precondition gates the whole adopt row, and it is not met today:**
2 ms of onset disagreement reads 0.373 on the tom render, squarely inside the
defect's own 0.336–0.406 range, and the misalignment sweep is non-monotone
([§6](#6-the-alignment-problem-which-gates-everything-else)).

The recommendation table is [§9](#9-recommendation).

---

## 0. What was measured, what was read, and what was refused

This document's central risk is the repository's central failure — confident
claims with no grounding — so the three categories are kept apart and tagged.

| tag | meaning |
|---|---|
| **[measured]** | computed by `tools/probes/audio_distance_floor.py` on our signals, this session |
| **[sourced]** | from a paper fetched and read this session; the link is in [§10](#11-sources) |
| **[inference]** | my reasoning from the above. Not established. |

### Three refusals, stated rather than filled in

**`torch` is not installed in this venv and no embedding checkpoint is on this
host.** So every number for OpenL3, VGGish, CLAP, EnCodec or CDPAM in
[§4](#4-learned-embeddings) would have been recalled, not measured. None is
given. [§4](#4-learned-embeddings) is sourced-and-inferred throughout and says
so.

**The fourteen frozen Surge clips are not cached on this host.**
`tools/refprofile.py --list` reports all fourteen as `(NOT CACHED HERE)`, which
is the correct outcome on a host without the plugins — so nothing here is
measured on the Filters material, and every measurement below is on drums.

**The board's own BD reference has no readable decay, so the sweeps are taken
on a different file.** #132's truncation guard refuses `schroeder_t20` on
`bd8/BD5050.WAV` — *"the record ends before the decay does: only 845 ms follow
the −25 dB point and a T20 of 537 ms needs 1074 ms after it"* — and D01A's
`decay` metric is a stated no-verdict on `main` for exactly that reason. The
decay sweep here perturbs the signal's **own** measured T20 by a stated
percentage, so it had nothing to aim at. The probe now asserts that
precondition at the point of use, **exits 2, and prints the files whose T20 the
current estimator does accept**; the sweeps are taken on `bd8/BD2550.WAV`
(T20 408.3 ms), which passes. **[measured]**

**There is no second recording session of the reference on this host.**
`/tmp/tr808-ref` holds the Fischer set; the From Mars packs whose current and
legacy editions gave `docs/bd-repeatability-measurement.md` its
session-to-session numbers are not here. So the reference-side floor in
[§2.3](#23-the-floor-that-decides-it) is **constructed** — the measured
per-property spread (2.74 % f0, 1.30 % T20, 0.159 dB band split) pushed through
the distance — and not a distance measured between two real sessions. It is
labelled as constructed wherever it is used.

### The estimator was ground-truthed before it was used, and it caught two defects

`E0` checks each distance against a closed-form answer before any signal is
touched: identical inputs must read exactly 0, and a pure ×2 gain must read
exactly 1.0 (relative L1, spectral convergence) and exactly ln 2 = 0.693147
(every log-magnitude form). **[measured]**

**It refused once, and a second defect walked straight past it** — which is
the more useful half of the story:

1. **A mel filterbank with empty bands.** At 48 kHz a 2048-point FFT with DAC's
   320 mel bands puts several low bands entirely between two FFT bins, so their
   response is identically zero, both signals clamp to the log floor, and those
   bands contribute exactly 0 for *any* input. A pure ×2 gain read **0.6247
   instead of 0.6931** — a 10 % under-report with no defect present. Empty
   bands are now dropped.
2. **A unit error the gate could not catch**, recorded because that is the more
   useful failure. `audio_measure.schroeder_t20` returns **seconds**; the decay
   perturbation divided by 1000 and so aimed at a 0.54 ms decay instead of
   537 ms, a rate of 37 000 dB/s. The sweep printed `1e128` and then `NaN`. The
   ground-truth gate is on the *metric* and this bug was in the *signal*, so
   the gate passed and the sweep was nonsense. It was caught by reading the
   output. **A gate on the estimator does not cover the stimulus.**

After both fixes the gate passes, with a stated residual: the log floor
(−100 dB of peak, part of the definition) moves the ×2 answer by
**2.3 × 10⁻⁶** for log-MSS and **1.4 × 10⁻⁵** for the DAC mel loss. That is the
clamp's own contribution and it is four orders of magnitude below anything
reported here.

### Determinism, which removes one whole objection

Two renders of one patch through our integer model are **bit-identical**, and
every distance between them is **exactly 0.0**. **[measured]**

So "synths are noisy, exact comparison is impossible" is simply false for our
side. It is also false as a general claim about plugins: `docs/reference-integrity.md`
records Surge Type 1 and our model at **0.0000 pp** run-to-run spread on the
self-oscillation measurement, against 0.006–0.010 pp for Mini V3, whose
analogue-variation modelling is not quite deterministic. **[sourced, internal]**

**Where non-determinism does exist it is a per-plugin property to be measured,
not a reason to lower the resolution of every metric.** **[inference]** Nothing
below is limited by our own render's repeatability; the limits are all on the
reference side or in the metric.

---

## 1. Why most of this literature is not about our problem

**Our setting is paired and per-property.** One rendered sound against one
named reference recording, measured on f0, Schroeder T20, band-energy splits,
attack time and partial balance, each normalised by its own tolerance, worst
reported. We need to catch **one wrong kick drum**.

**FAD and its relatives are distributional.** Kilgour et al. introduce FAD as
a **reference-free** metric that adapts FID to audio, validated against
artificial distortions and reported as correlating with human perception at
r = 0.52 against SDR's 0.39. **[sourced]** Fréchet distances compare the
Gaussian fits of two *populations* of embeddings; a population of one has no
covariance, so the quantity is undefined, not merely noisy. **[inference]**

Gui et al. then show FAD is "hampered by sample size bias, poor choice of audio
embeddings, or the use of biased or low-quality reference sets", and propose
extrapolating towards infinite sample size to remove that bias. **[sourced]**
Their one genuinely relevant contribution is **per-song FAD**, which they
report "can be useful to identify outlier samples and predict perceptual
quality". **[sourced]** That is the closest thing in the distributional
literature to a per-item score — and note what it is *for*: finding outliers in
a generated set, given a reference *distribution*. We have a reference
*recording*, not a distribution, and no set to find an outlier in.

**So the distributional branch is out on a definitional ground, not an
empirical one.** **[inference]** It is not that FAD would score badly on our
task; it is that our task does not have the two populations FAD's definition
requires. Nothing below revisits it.

That leaves paired distances, which is the rest of this document.

---

## 2. What a multi-scale spectral distance actually reads on our signals

Four distances, implemented from their published definitions, all over the six
FFT sizes {2048, 1024, 512, 256, 128, 64} that DDSP and the Turian–Henry
benchmark both use:

| | what it is | what a value means |
|---|---|---|
| `mss_l1` | L1 on **linear** magnitude, divided by the reference's own L1 norm | 1.0 = "wrong by as much as the reference contains" |
| `mss_log` | mean \|Δ log magnitude\|, in nats | 0.693 = a factor of two (6.02 dB) averaged over every bin |
| `mrstft` | Yamamoto et al.'s form: spectral convergence + log-magnitude L1 | sum of the two above; the form most codebases ship |
| `mel_dac` | the Descript Audio Codec's multi-scale mel L1 at its published windows and bin counts | nats, as `mss_log` |

### 2.1 The floor is alignment, and it is enormous

One **real** TR-808 bass drum recording against **itself**, moved by k samples
and zero-padded back. A 4-sample head trim cannot be the machine; whatever the
distance reads here, it is reading the editor. This is the control that
convicted the band-split metric in `docs/bd-repeatability-measurement.md`, run
against a new metric. **[measured]**

| shift | | `mss_l1` | `mss_log` | `mrstft` | `mel_dac` |
|---|--:|--:|--:|--:|--:|
| 1 sample | 0.023 ms | 0.0047 | 0.0066 | 0.0123 | 0.0114 |
| 4 samples | 0.091 ms | 0.0184 | 0.0175 | 0.0399 | 0.0383 |
| 16 samples | 0.363 ms | 0.0714 | 0.0420 | 0.1294 | 0.1070 |
| 48 samples | 1.088 ms | 0.2005 | 0.0733 | 0.3143 | 0.2070 |
| 96 samples | 2.177 ms | 0.3568 | 0.0957 | 0.5189 | 0.2986 |
| 240 samples | 5.442 ms | **0.5234** | 0.1213 | **0.7076** | 0.3978 |
| 480 samples | 10.884 ms | *0.3176* | *0.0984* | *0.5516* | *0.2878* |

Three things fall out of this table, and each one is on its own sufficient to
disqualify a whole-file spectral distance as a target.

**The alignment floor is larger than every defect measured in this document.**
`model/audio_measure.py`'s own docstring states that `onsets` positions are
"good to about 10 ms and no better, because the Hilbert transform is not causal
and puts a precursor ahead of every strike", and `run_case.prepare` aligns by a
2 %-of-peak threshold crossing with a 1 ms lead — not a cross-correlation, and
not sample-accurate between two different instruments. At **5.4 ms** of
misalignment `mss_l1` reads 0.523, which is **43 % of the entire bass-drum-
versus-snare-drum distance** (1.229, [§2.6](#26-the-ceiling-and-the-exchange-rate)) — for two copies
of one recording. **[measured]**

**It is not monotone.** 10.9 ms of misalignment reads *less* than 5.4 ms, on
all four distances (italicised row). So the value cannot even be read as "more
is worse" in the one variable that dominates it. **[measured]**

**A defect must be compared against the floor on its own signal, and that is
done in [§2.4](#24-the-tom-pitch-drop--the-decisive-test) rather than borrowed
from this table.** **[inference]**

### 2.2 Single-property sweeps

The same real recording, perturbed one property at a time. Each perturbation is
analytic, not fitted: the decay change is a closed-form extra exponential
solved from the signal's own measured T20; the tilt is an exact dB of gain
above 200 Hz, zero-phase; the gain is a scalar. **[measured]**

**Decay (T20) error**

| error | `mss_l1` | `mss_log` | `mrstft` | `mel_dac` |
|---|--:|--:|--:|--:|
| 1.30 % *(the machine's own spread)* | 0.0138 | 0.0102 | 0.0199 | 0.0403 |
| 2.5 % | 0.0265 | 0.0208 | 0.0394 | 0.0776 |
| 5 % | 0.0530 | 0.0468 | 0.0839 | 0.1552 |
| 10 % | 0.1061 | 0.1145 | 0.1877 | 0.3090 |
| 25 % | 0.2673 | 0.3917 | 0.5685 | 0.7335 |
| 50 % | 0.5413 | 0.8570 | 1.1921 | 1.2843 |

Decay is the one property where a spectral distance behaves well: monotone,
close to linear in the error over the range that matters, and a 5 % error
(0.0530 on `mss_l1`) sits comfortably above the machine's own 1.30 % (0.0138).
**A 5 % decay error is resolvable — by a factor of 3.9 over the machine's own
decay spread.** **[measured]**

**Band tilt (partial imbalance), and the band-weighting trap**

| tilt above 200 Hz | `mss_l1` | `mss_log` | `mrstft` | `mel_dac` |
|---|--:|--:|--:|--:|
| 0.159 dB *(the machine's own spread)* | 0.0006 | 0.0028 | 0.0039 | 0.0113 |
| 1 dB | 0.0036 | 0.0180 | 0.0256 | 0.0631 |
| 3 dB *(the board's tolerance)* | 0.0123 | 0.0702 | 0.0960 | 0.1918 |
| **8 dB** | **0.0456** | 0.3266 | 0.4230 | 0.5622 |

**An 8 dB partial imbalance reads 0.0456 on `mss_l1` — less than a 0.4 dB
broadband gain change, and about the same as a 10-sample (0.2 ms) editing
shift.** **[measured]** Linear-magnitude MSS on a bass drum is dominated by the
~50 Hz fundamental, so an 8 dB error everywhere above 200 Hz is nearly
invisible to it. `mel_dac` reads 0.562 for the same signal and sees it
perfectly well — a factor of **12** between the two conventions on one signal.

**Which of the four you pick decides whether the metric can see a partial
imbalance at all, and there is no way to pick without already knowing which
property you are hunting.** **[inference]** That is not a tuning detail; it is
the per-property question reappearing inside the metric that was supposed to
replace it.

**f0, moved alone**

Resampling a recording moves pitch *and* scales every time constant by the
inverse, so a sweep taken that way cannot say which one the distance responded
to. This sweep instead retunes **our own bass drum through the model**, which
moves f0 and nothing else. **[measured]**

| f0 error | | `mss_l1` | `mss_log` | `mrstft` | `mel_dac` |
|---|---|--:|--:|--:|--:|
| **+2.74 %** *(the machine's own spread)* | 46.8 cents | **0.3065** | **0.0492** | **0.3856** | **0.2643** |
| +5.95 % | 100 cents | 0.3572 | 0.0648 | 0.4785 | 0.3542 |
| +10.0 % *(the board's tolerance)* | 165 cents | 0.3833 | 0.0682 | 0.5068 | 0.3707 |
| +26.0 % | 400 cents | 0.4794 | 0.0807 | 0.5888 | 0.4363 |
| **+100 %** *(an octave)* | 1200 cents | **0.7481** | **0.1061** | **0.8348** | **0.5334** |

Read the first and last rows together. **A pitch error the size of the
machine's own session-to-session wander reads 41 % of what a full octave error
reads.** A 36-fold increase in the pitch error buys a 2.4-fold increase in the
distance. **[measured]** This is the compressive, saturating response Turian &
Henry describe as "at great distances it has no sense of pitch orientation
whatsoever (a vanishing gradient)". **[sourced]**

### 2.3 The floor that decides it

A distance is usable only if the error we care about is larger than the
distance's floor. The TR-808's own session-to-session spread is measured:
**2.74 % f0, 1.30 % T20, 0.159 dB band split**
(`docs/bd-repeatability-measurement.md`). Pushing each through the sweeps above
and taking the worst gives the **constructed reference floor** — the value a
whole-clip distance will read between our render and the reference when our
render is *correct* and the reference simply came from a different session.

| | `mss_l1` | `mss_log` | `mrstft` | `mel_dac` |
|---|--:|--:|--:|--:|
| from 1.30 % T20 | 0.0138 | 0.0102 | 0.0199 | 0.0403 |
| from 0.159 dB band | 0.0006 | 0.0028 | 0.0039 | 0.0113 |
| **from 2.74 % f0** | **0.3065** | **0.0492** | **0.3856** | **0.2643** |
| **constructed floor (worst)** | **0.307** | **0.049** | **0.386** | **0.264** |

**[measured, on a constructed stimulus]** — the perturbations are exact, the
2.74 / 1.30 / 0.159 figures are measured elsewhere in this repository, but this
is not a distance taken between two real recording sessions, because no second
session is on this host.

**f0 dominates the floor by a factor of twenty-two.** A whole-clip spectral
distance against a real recording is, to first order, an f0 comparator with a
very coarse scale — and 2.74 % of f0 is a difference the board deliberately
passes, since the frequency tolerance is 10 %.

### 2.4 The tom pitch drop — the decisive test

`spec/NUMERIC-CONTRACT.md` §15.7.1 ships the toms' diode pitch drop at **×1.7**.
`docs/tom-pitch-drop-measurement.md` measured 99 files of real hardware and
found **×1.063 unaccented, ×1.140 at Accent, ×1.236 at More Accent** — the
shipped value is 3 to 11 times too large and lies outside the range of every
file. This is the largest open drum defect in the project and it is **a pitch
error**, which is precisely where the literature says spectral distances are
weakest.

Our LT rendered through the real model at each ratio, with nothing else
changed, against the shipped ×1.7. **[measured]**

| vs shipped ×1.7 | true error | `mss_l1` | `mss_log` | `mrstft` | `mel_dac` |
|---|---|--:|--:|--:|--:|
| *floor: same patch twice* | — | **0.0000** | 0.0000 | 0.0000 | 0.0000 |
| *floor: 1-sample shift* | — | 0.0062 | 0.0036 | 0.0113 | 0.0067 |
| *constructed reference floor* ([§2.3](#23-the-floor-that-decides-it)) | — | *0.307* | *0.049* | *0.386* | *0.264* |
| ×1.236 More Accent | smallest | 0.3358 | 0.0379 | 0.4657 | 0.2100 |
| ×1.140 Accent | ↓ | 0.3991 | 0.0612 | 0.5634 | 0.3499 |
| ×1.063 unaccented | ↓ | 0.3894 | 0.0401 | 0.5463 | 0.2336 |
| ×1.000 no drop | largest | 0.4064 | 0.0443 | 0.5671 | 0.2635 |

**Every one of the four distances gets the ranking wrong.** The true ordering of
error magnitude is ×1.236 < ×1.140 < ×1.063 < ×1.000. **All four report ×1.140
as *further* from the shipped ×1.7 than ×1.063 is**, which is backwards
(0.3991 > 0.3894, 0.0612 > 0.0401, 0.5634 > 0.5463, 0.3499 > 0.2336). And two
of them — `mss_log` and `mel_dac` — additionally report ×1.000, the largest
possible error with no drop at all, as *closer* than ×1.140 (0.0443 < 0.0612,
0.2635 < 0.3499). **[measured]**

**Not one of the four separates the defect from the floor by a usable margin,
and two of them do not separate it at all.** Against the constructed reference
floor: **[measured]**

| | floor | defect range | verdict |
|---|--:|--:|---|
| `mss_l1` | 0.307 | 0.336 – 0.406 | clears, by **1.10× to 1.33×** |
| `mss_log` | 0.049 | 0.038 – 0.061 | **three of four rungs below the floor** |
| `mrstft` | 0.386 | 0.466 – 0.567 | clears, by **1.21× to 1.47×** |
| `mel_dac` | 0.264 | 0.210 – 0.350 | **three of four rungs below the floor** |

The best of the four clears its own floor by 10 % on the smallest rung; the
other two spend three rungs out of four *underneath* it. For comparison,
`docs/bd-repeatability-measurement.md` records that the *tightest* ratio
anywhere on the board is the f0 tolerance at **3.7×** the machine's spread, and
calls that the one with no headroom.

**What the board's own estimator does on the same five renders**, using
`run_case._pitch_drop("LT")` unmodified:

| render | Pitch drop (Hz) |
|---|--:|
| ×1.70 shipped | **43.87** |
| ×1.236 | 14.15 |
| ×1.140 | 8.02 |
| ×1.063 | 3.44 |
| ×1.000 | −0.97 |

Monotone, in hertz, directly comparable with a frequency tolerance, and it says
*which property is wrong*. **[measured]** There is no version of this comparison
in which the spectral distance is the better instrument for this defect.

### 2.5 Windowing rescues the ranking — and gives the game away

The drop lives in the first 60 ms of a 1200 ms render, so a whole-clip distance
averages it against 1140 ms in which nothing is wrong. Restricting each distance
to the first 60 ms: **[measured]**

| vs shipped ×1.7, first 60 ms | `mss_l1` | `mss_log` | `mrstft` | `mel_dac` |
|---|--:|--:|--:|--:|
| *floor: 1-sample shift, same window* | 0.0070 | 0.0196 | 0.0279 | 0.0167 |
| ×1.236 | 0.4594 | 0.1566 | 0.6797 | 0.4720 |
| ×1.140 | 0.4928 | 0.1633 | 0.7168 | 0.5074 |
| ×1.063 | 0.5694 | 0.1883 | 0.8107 | 0.5884 |
| ×1.000 | 0.5960 | 0.1869 | 0.8366 | 0.5973 |

**Three of the four are now monotone across all four rungs** — `mss_l1`,
`mrstft` and `mel_dac` — at 28–88× their own alignment floor in this window.
Only `mss_log` still inverts, at the last rung. **[measured]** *(This is one of
the two results that got stronger under #132: before it, only `mss_l1` was
monotone here.)*

**This is the finding that settles the role question.** The distance becomes a
usable instrument exactly when you tell it *which 60 ms window* the defect
lives in — that is, when you already have the per-property knowledge the scalar
was supposed to make unnecessary. A windowed, band-limited, property-targeted
spectral distance is not an alternative to the scorecard; it is another entry
on it, with a worse unit. **[inference]**

### 2.6 The ceiling, and the exchange rate

**The ceiling.** Different voices of the same machine, so the largest value the
metric plausibly produces on this material: **[measured]**

| | `mss_l1` | `mss_log` | `mrstft` | `mel_dac` |
|---|--:|--:|--:|--:|
| BD vs SD | 1.229 | 0.698 | 1.666 | 1.888 |
| BD vs CH | 1.242 | 0.564 | 1.570 | 1.905 |
| LT vs MT | 0.690 | 0.196 | 0.894 | 0.518 |

So on `mss_l1`, the usable band between "the machine repeating itself" (0.307)
and "a completely different drum" (1.229) is a factor of **4.0** — and the tom
defect and an octave error both land inside the lower third of it.

**The exchange rate.** For each distance, the broadband **gain** error that
reads the same value as a **5 % decay** error: **[measured]**

| | `mss_l1` | `mss_log` | `mrstft` | `mel_dac` |
|---|--:|--:|--:|--:|
| value of a 5 % decay error | 0.0530 | 0.0468 | 0.0839 | 0.1552 |
| gain error reading the same | **0.448 dB** | 1.734 dB | **0.587 dB** | 1.716 dB |

`run_case.prepare` **peak-normalises both sides**, because the Fischer set's
levels are not the machine's — so level is deliberately outside what the board
scores, and a few tenths of a dB of residual level difference between a
peak-normalised render and a peak-normalised recording is entirely ordinary.
**A scalar that cannot tell a 0.45 dB level residual from a 5 % decay error
cannot drive a design loop**, because the two have opposite correct responses:
one is to be ignored, the other fixed. **[inference]**

---

## 3. The literature agrees, and says why

### 3.1 The result that decides the pitch case

Turian & Henry, *I'm Sorry for Your Loss: Spectrally-Based Audio Distances Are
Bad at Pitch* (2020), measures the pitch distance between two stationary
sinusoids and asks only whether each distance's **gradient points the right
way** — a rank assumption, not a calibration. Table 1, at three resolutions
(analytic ε→0; fine ±30 cents / ±2 dB; coarse ±600 cents / ±10 dB), over 1000
trials, worst-case 95 % CI ±0.032: **[sourced]**

| distance | ω ±ε | ω ±30 c | ω ±600 c | A ±ε | A ±2 dB | A ±10 dB |
|---|--:|--:|--:|--:|--:|--:|
| Spectrogram | 0.617 | 0.574 | 0.695 | 0.535 | 0.518 | 0.514 |
| log(Spectrogram) | 0.548 | 0.541 | 0.679 | 0.607 | 0.577 | 0.615 |
| Mel | 0.511 | 0.479 | 0.580 | 0.564 | 0.544 | 0.551 |
| MFCC | 0.532 | 0.603 | 0.648 | 0.593 | 0.931 | 0.999 |
| **MSS** | **0.771** | **0.905** | **0.978** | **0.550** | **0.530** | **0.531** |
| log₂(Spectral Centroid) | 0.515 | 1.000 | 1.000 | 0.460 | 0.503 | 0.518 |
| nsynth wavenet | 0.588 | 0.862 | 0.873 | 0.938 | — | — |
| vggish | 0.536 | 0.652 | 0.595 | 0.636 | — | — |
| openl3 | 0.594 | 0.989 | 0.507 | 0.480 | — | — |
| wav2vec 2.0 Large (LV-60) | 0.727 | 0.831 | 0.682 | 0.738 | — | — |

The paper's own framing: MSS "mitigates the poor behavior of the
spectrogram-based pitch distance", with "the pitch-perturbed gradient correctly
oriented 77 % of the time"; but "MSS has poor analytic level gradients (55 %)";
and the general behaviour is that a spectral distance "at great distances has no
sense of pitch orientation whatsoever (a vanishing gradient), and it locks-in
with extreme confidence at fine pitch distances". **[sourced]**

**Three consequences for us, and note that MSS is the *best* row in that table
on pitch.** **[inference]**

- **77 % of gradients correctly oriented is a catastrophic number for a
  scorecard, even though it is a respectable one for a loss.** A training loss
  is summed over millions of steps and millions of examples; a wrong sign 23 %
  of the time still descends. Our scorecard reads **one** number for **one**
  case and a human acts on it. A 23 % chance the sign is backwards is not a
  noisy instrument, it is an instrument that will send someone to fix the wrong
  thing roughly one case in four.
- **The task in that table is two stationary sinusoids.** Ours is an
  inharmonic, fast-decaying percussive one-shot with a pitch that *sweeps*
  during the first 60 ms. Nothing in the paper suggests the numbers improve
  when you make the signal harder. Our [§2.4](#24-the-tom-pitch-drop--the-decisive-test)
  measures the harder version and finds 4 rank errors out of 4.
- **The level column is the exchange-rate problem, independently found.** MSS
  scores 0.550 / 0.530 / 0.531 — near-random — at ordering *amplitude*
  differences, which is the same fact our [§2.6](#26-the-ceiling-and-the-exchange-rate) measures as
  "a 5 % decay error and a 0.4 dB gain error read the same number".

One caveat the paper states itself: **octave equivalence is explicitly out of
scope.** Appendix A.1 models only frequency-ratio distance and excludes the
helical dimension where octaves wrap, calling it "an open question".
**[sourced]** So the paper does not license a claim about octave errors
specifically; our [§2.2](#22-single-property-sweeps) measures one directly
(octave = 0.748 against a 2.74 % floor of 0.307) and that number stands on its
own.

### 3.2 Codec losses are the same family, and they are training objectives

Three independently-developed neural codecs arrived at the same construction.
**[sourced]**

| | reconstruction loss | scales |
|---|---|---|
| **EnCodec** | L1 + L2 over mel-spectrograms at several time scales, 64 mel bins | window 2^i, hop 2^i/4, i ∈ {5…11} (32–2048) |
| **DAC / Improved RVQGAN** | L1 on mel-spectrograms | windows [32, 64, 128, 256, 512, 1024, 2048] with mel bins [5, 10, 20, 40, 80, 160, 320] |
| **Parallel WaveGAN** | multi-resolution STFT: spectral convergence + log-STFT magnitude | multiple resolutions (the abstract states the combination; exact sizes are in the full paper, not on the page fetched) |

DAC states the rationale directly — "multi-scale spectral losses encourage
modeling of frequencies in multiple time-scales", and "using the lowest hop
size of 8 improves modeling of very quick transients that are especially common
in the music domain". **[sourced]**

**These are objectives, not instruments, and the difference is not
rhetorical.** **[inference]** They are designed so the *gradient* is useful
under a specific condition our setting does not meet: in codec training the two
signals are the **same recording**, sample-aligned by construction, and the loss
never has to survive a 5 ms onset disagreement. [§2.1](#21-the-floor-is-alignment-and-it-is-enormous)
measures what happens when it does. None of the three papers characterises the
*value's* floor, reports a resolution, or claims the value is interpretable —
because for their purpose none of that is needed. Using one as a scorecard
metric imports a function that was never asked to have the property we need.

**The DAC mel configuration is worth keeping anyway**, for a different reason:
it is the one place in that family where the low bands get very small windows
(32 samples, 5 mel bins), which is what made `mel_dac` the only distance in
[§2.2](#22-single-property-sweeps) that sees an 8 dB partial imbalance. It is a
better-designed *representation* than plain linear MSS, and that is independent
of whether the scalar built on it is usable. **[inference]**

### 3.3 Discriminators are where the interesting idea is

EnCodec's MS-STFT discriminator is five sub-discriminators at STFT window
lengths [2048, 1024, 512, 256, 128], each a 2-D convolution over the complex
spectrogram with dilations 1, 2, 4 in time; its stated purpose is a "perceptual
loss term" that reduces artifacts. **[sourced]** DAC adds a multi-period
discriminator with periods [2, 3, 5, 7, 11] and a **multi-band** multi-scale
STFT discriminator at windows [2048, 1024, 512] with band limits
[0.0, 0.1, 0.25, 0.5, 0.75, 1.0], on the stated grounds that "splitting the
STFT into sub-bands slightly improves high frequency prediction and mitigates
aliasing artifacts, since the discriminator can learn discriminative features
about a specific sub-band". **[sourced]**

**The coordinator's observation is right and is the most interesting idea in
this survey: a discriminator is a device for *finding where two signals differ*,
which is the shape of a blind-spot detector rather than a target.**
**[inference]** A discriminator's output is not a scalar you minimise — it is a
map over time and frequency (and, with sub-banding, over frequency band
explicitly) of where the two signals are separable. That is diagnostic
information of exactly the kind a scalar distance destroys.

**But three things must be said against adopting one here, and I have not seen
them addressed anywhere.** **[inference]**

1. **A discriminator must be trained, and trained against something.** These
   are trained adversarially against a generator on large corpora. We have one
   reference recording per voice. A discriminator trained to separate "our BD"
   from "the 808's BD" on a sample size of one will separate them on the
   recording chain, the edit point and the converter, which is what
   `docs/bd-repeatability-measurement.md` already showed is five sixths of the
   apparent difference on a band split.
2. **Its output has no floor we could state**, for the same reason a learned
   embedding's does not ([§4](#4-learned-embeddings)), and this document's
   whole argument is that an uncharacterised floor is disqualifying.
3. **There is a non-learned version of the same idea that we can have today**,
   and it is [§7](#7-the-guard-hypothesis-tested)'s construction: take the
   *residual* between the two signals' multi-scale spectrograms and report
   **where** it is largest in time and frequency, rather than its norm. That
   keeps the diagnostic content and throws away the scalar. It needs no
   training, no checkpoint and no corpus. **I did not build it** — it is out of
   scope for this deliverable — but it is the concrete thing I would build
   before training anything.

---

## 4. Learned embeddings

**Nothing in this section is measured.** `torch` is not installed in this venv
and no checkpoint is on this host, so every number would be recalled. What
follows is sourced or inferred, tagged as such, and deliberately short.

**What the one relevant measurement says.** In the Turian–Henry table
([§3.1](#31-the-result-that-decides-the-pitch-case)), the learned embeddings are
*not* better than MSS on pitch: openl3 0.594 analytic and **0.507 at ±600 cents
— chance** — while scoring 0.989 at ±30 cents; vggish 0.536 / 0.652 / 0.595;
wav2vec 2.0 Large 0.727 / 0.831 / 0.682. **[sourced]** OpenL3's pattern is the
worst possible shape for us: near-perfect at fine pitch differences, at chance
at coarse ones. A metric that is right about 30 cents and coin-flipping about
600 cents cannot be read at all without already knowing which regime you are in.

**What a paired embedding distance would add that we lack.** **[inference]**
Genuinely: sensitivity to *timbral* properties nobody has written an estimator
for. Our BD case measures f0, an early/body energy ratio and a T20. A render
that matched all three and still sounded wrong — wrong noise character, wrong
transient shape, a spurious tone — would pass. An embedding trained on a large
audio corpus encodes many such distinctions implicitly. That is a real gap and
it is the honest case for this family.

**What it fails to detect, and why it cannot be fixed by choosing a better
model.** **[inference]** An embedding distance of 0.3 does not say whether the
decay or the pitch is wrong. That is not a limitation of current embeddings; it
is what a projection to a fixed vector *is*. Everything the scorecard does — per
property, own tolerance, own units, worst reported, coverage stated separately —
is destroyed by the projection. `docs/scorecard/README.md` already refuses to
average milliseconds against cents against decibels; an embedding distance is
that average, performed by a network, with the weights unstated.

**CDPAM is the most relevant member of the family and still does not fit.**
It is a full-reference, pairwise metric trained on human triplet comparisons,
which is exactly our shape. But its own abstract states the problem it was built
to fix in its predecessor: DPAM "does not generalize well outside the range of
perturbations on which it was trained", and CDPAM improves generalisation
without claiming to remove the dependence. **[sourced]** Its evaluation is over speech
and speech-processing perturbations — the paper's own applications are speech
synthesis and enhancement — so a synthesiser parameter error is outside the
range it was built and checked on. **[inference; I did not read its dataset
list, only the abstract]** **A 5 % T20 error on an
analogue bass drum is not in that range, and we would have no way to find out
that it was not, because the metric returns a number either way.** **[inference]**
That is the failure mode `model/audio_measure.py`'s docstring is built around:
"an estimator that always produces a plausible number".

**Cost, if we wanted one anyway.** **[inference]** A PyTorch dependency, a model
checkpoint that must be hashed and frozen like `refprofile/profile.json` is
(otherwise a silent upstream weight change moves every historical result), a
resample to the model's rate, and a floor characterisation of its own — the
whole of [§2](#2-what-a-multi-scale-spectral-distance-actually-reads-on-our-signals)
repeated. On a repository whose stated failure mode is un-validated estimators,
that is a large bill for a scalar that cannot name a property.

---

## 5. Where practice is contested or absent

**There is no accepted paired distance for percussive one-shots, and the two
camps do not overlap.** **[inference, from the sources in §11]**

- The **reconstruction camp** (DDSP, Parallel WaveGAN, EnCodec, DAC) uses
  paired multi-scale spectral losses constantly — on signals that are
  sample-aligned by construction, for gradients, with no claim about the value.
- The **evaluation camp** (FAD and its successors) produces interpretable,
  validated-against-humans numbers — for *populations*, not pairs.

Nobody in either camp is solving "is this one rendered kick drum the same as
that one recorded kick drum", which is our problem. The closest thing is
full-reference perceptual metrics: DPAM/CDPAM **[sourced]**, and the
speech-quality lineage PESQ / ViSQOL **[not sourced this session — named from
background knowledge, and not relied on by any claim here]**. These are paired
and validated, but on speech-and-codec degradations rather than on synthesis
parameter errors.

**Do not read this as "the field has not got round to it yet."** **[inference]**
The absence is structural: a paired distance needs the two signals to be
*corresponding*, and two recordings of "the same drum" from different sessions
are not corresponding at the sample level. That is the alignment floor of
[§2.1](#21-the-floor-is-alignment-and-it-is-enormous), and it is why the
property-wise approach exists.

### Three things I was asked about and could not source

**The retracted ICLR blog-track post.** I fetched the ICLR 2023 blog-post index
and it lists no post on audio distances, audio similarity or spectral losses,
and none marked retracted or withdrawn. **WebSearch is exhausted for this
session (200/200)**, so I could not search for it by title or author, and I have
not found it by URL guessing. **I have not seen it, and I am not reconstructing
its bibliography from memory.** If it exists its reading list is probably worth
more than this section; the person to ask is the author.

**AMUSE.** I could not identify it. Several unrelated things carry that
acronym. Nothing is claimed about it here.

**ESPnet-Codec / VERSA.** Confirmed to exist: *ESPnet-Codec: Comprehensive
Training and Evaluation of Neural Codecs for Audio, Music, and Speech* (2024)
introduces **VERSA**, described as a standalone toolkit providing "a
comprehensive evaluation of codec performance over 20 audio evaluation
metrics". **[sourced]** **I could not obtain the list of those twenty metrics**
— it is not on the abstract page — so I am not naming them. As a *catalogue* of
what the codec community currently measures it is probably the single best
starting point for anyone extending this survey, and that is all I can say
about it from evidence.

---

## 6. The alignment problem, which gates everything else

[§2.1](#21-the-floor-is-alignment-and-it-is-enormous) measured the shift floor
on a bass drum. Here it is on the **tom render the defect actually lives on**,
so the comparison is same-signal: **[measured]**

| shift | | `mss_l1` | `mss_log` | `mrstft` | `mel_dac` |
|---|--:|--:|--:|--:|--:|
| 1 sample | 0.021 ms | 0.0062 | 0.0036 | 0.0113 | 0.0067 |
| 4 samples | 0.083 ms | 0.0243 | 0.0078 | 0.0383 | 0.0212 |
| 16 samples | 0.333 ms | 0.0928 | 0.0156 | 0.1331 | 0.0627 |
| 48 samples | 1.000 ms | 0.2501 | 0.0319 | 0.3387 | 0.1321 |
| **96 samples** | **2.000 ms** | **0.3730** | 0.0444 | **0.4903** | 0.1857 |
| 240 samples | 5.000 ms | *0.2208* | *0.0300* | *0.3755* | *0.1411* |
| 480 samples | 10.000 ms | 0.3519 | 0.0404 | 0.5176 | 0.1854 |

**Two milliseconds of onset disagreement reads 0.373 on `mss_l1`. The entire
tom pitch-drop defect reads 0.336 to 0.406.** On the same signal, with the same
metric — the misalignment lands *inside* the defect's own range. **[measured]**
And the sweep is again non-monotone: 5 ms reads *less* than 2 ms, and 10 ms
reads less than 2 ms too.

**This is a precondition, and it is not currently met.** **[inference]**
`run_case.prepare` aligns by a 2 %-of-peak threshold crossing with a 1 ms lead.
That is adequate for the board's estimators, every one of which is a frequency,
a time *interval*, or a ratio — all invariant to a shifted origin.
A whole-file spectral distance is invariant to none of them. Between our render
and a real recording, the true alignment disagreement is at least the
difference between a digital onset and a 1994 converter's rise, and
`audio_measure.py` puts its own onset estimator at "about 10 ms and no better".

**Before any spectral distance could be read at all, alignment would have to be
done by cross-correlation to sample accuracy and the residual reported on the
record** — and on two *different instruments* playing the same nominal sound,
it is not obvious that a sample-accurate alignment even exists to be found.
That is an open question, not a solved engineering step. **[inference]**

### What #132 already fixed, and what it did not

This document was drafted against `c752145` and re-measured against `ba14af2`
(#132, *"the repaired measurement path"*), which landed while it was being
written. #132 changes `run_case.prepare` to **guarantee** `required_lead_samples(sr)
+ 1 ms` of true silence before the strike **on both sides** — at least 10 ms, or
20 × the band-pass `padlen`, whichever is larger. Its own commit records what it
was fixing: the reference side was getting 0.11–1.18 ms of lead while our
renders, which begin at exact digital silence, got none.

**That removes a systematic lead asymmetry of up to 1.18 ms between our side and
the reference side.** Read against the table above, a 1 ms shift is worth 0.25 on
`mss_l1` for the tom render. So before #132, a spectral distance taken between
our render and a reference would have carried up to that much of pure apparatus
offset — comparable with the entire tom defect. **[measured, against §6's own
table]**

**What #132 does not do, and what a spectral distance would still need.**
**[inference]** It pins both sides to the same 2 %-of-peak crossing with the same
lead; it does not cross-correlate, and the crossing itself does not *correspond*
between a digital render, whose first sample above threshold is exact, and a
recorded analogue strike through a 1994 converter. The residual is unmeasured.
Every per-property estimator on the board is invariant to it — they are
frequencies, intervals and ratios. A whole-file spectral distance is invariant to
none of them, so the residual would have to be measured and bounded before any
such distance could be read, and that work does not exist.

### And a second precondition: the reference has a noise floor and we do not

Our renders lead with exact digital silence and decay to exact zero. The Fischer
recordings are a 1994 converter's output and do neither — `run_case.prepare`
already has to subtract their DC from the pre-onset region, and
`docs/bd-repeatability-measurement.md` found a 4-sample head trim moving the
shipped band split by 0.568 dB.

One real recording against **itself** with everything below a threshold zeroed —
a change that removes no voice and is inaudible: **[measured]**

| gate | `mss_l1` | `mss_log` | `mrstft` | `mel_dac` |
|---|--:|--:|--:|--:|
| below −60 dBFS of peak | 0.0040 | 0.0812 | 0.0819 | **0.2512** |
| below −50 dBFS | 0.0192 | 0.3498 | 0.3545 | 0.7982 |
| below −40 dBFS | 0.0599 | 0.7105 | 0.7260 | 1.3373 |
| tail after 1.0 s zeroed | 0.0148 | 0.0621 | 0.0759 | 0.4334 |

**Removing inaudible material below −60 dBFS moves the DAC mel loss by 0.251 —
as much as the entire constructed machine-repeatability floor (0.264).**
**[measured]** A decaying one-shot spends most of its duration below −60 dB of
its own peak, and a log-domain distance weights every bin equally, so the metric
is substantially reading the region where the drum is already over.

`mss_l1` is almost immune (0.0040), for the same reason it is almost blind to an
8 dB partial imbalance: linear magnitude is dominated by the loudest content.
**The linear form is robust to the noise floor and blind to the high band; the
log forms see the high band and are dominated by the noise floor. There is no
setting of this knob that is right for both.** **[inference]**

---

## 7. The guard hypothesis, tested

Everything above argues against a spectral distance as a **target**. The
coordinator's counter-position — *"if every per-property metric passes and a
spectral distance is large, we are missing a property"* — is a different claim
and deserves its own experiment rather than an opinion.

So: three defects a fixed-point drum machine can actually have, injected into
our own BD render, with **the board's real `DRUM_PLAN["BD"]` estimators run on
each** and each distance reported against its own floor. **[measured]**

| injected defect | `mss_l1` | `mss_log` | `mrstft` | `mel_dac` | board's 3 metrics |
|---|--:|--:|--:|--:|---|
| *floor: 1-sample shift* | 0.0041 | 0.0035 | 0.0082 | 0.0085 | — |
| −40 dBFS 12 kHz tone *(clock/LFO feedthrough)* | 0.0844 | 0.1750 | 0.2095 | **0.6071** | **all pass** |
| 6-bit requantisation *(a narrowed word)* | 0.2828 | 1.4337 | 1.4705 | **2.0287** | **all pass** |
| −45 dBFS tail noise after 250 ms | 0.4004 | 2.7550 | 2.7786 | **3.0329** | **all pass** |

What the board reads on those same three signals — worst case across all nine
measurements is **0.138 of tolerance**: **[measured]**

| defect | Pitch trajectory | early/body energy | decay |
|---|---|---|---|
| 12 kHz tone | 49.42 → 49.42 Hz, **0.000** | −10.372 → −10.372 dB, **0.000** | 334.7 → 357.8 ms, **0.138** |
| 6-bit | 49.42 → 49.42 Hz, **0.000** | −10.372 → −10.377 dB, **0.002** | 334.7 → 339.6 ms, **0.029** |
| tail noise | 49.42 → 49.42 Hz, **0.000** | −10.372 → −10.372 dB, **0.000** | 334.7 → 348.5 ms, **0.082** |

*(each figure is the error divided by that metric's own tolerance; ≤ 1 passes)*

**The guard hypothesis is confirmed, and by a wide margin.** All three defects
pass every per-property metric with an order of magnitude to spare, and every
distance flags all three: **21× to 99×** its floor for linear `mss_l1`, and
**49× to 778×** for the log-domain forms. Two of them
(requantisation, tail noise) read *above the BD-versus-snare-drum ceiling* —
the metric's way of saying "this is not the same instrument". **[measured]**

**Three qualifications, and they shape the recommendation rather than reversing
it.** **[inference]**

1. **The defect class is exactly the one [§6](#6-the-alignment-problem-which-gates-everything-else)
   warns about.** All three injections are *additive, broadband and present in
   the quiet parts* — which is the same property that makes an inaudible −60 dB
   gate read 0.251. The guard is sensitive to added stuff. It is not, on this
   evidence, sensitive to a wrong *parameter*: §2.4 is the same family of
   measurement on a parameter error and it fails.
2. **Some of the headroom is the board's loose time tolerance.** The decay
   tolerance here is 167 ms, 50 % of 335 ms, which
   `docs/bd-repeatability-measurement.md` already identifies as having "an order
   of magnitude of unused room". A tighter decay tolerance would have caught
   part of the 12 kHz case on its own.
3. **This is a self-comparison and the real guard would not be.** Both sides are
   our own deterministic render, so the floor is 0.0086. Used as designed —
   our render against the reference recording — the defect's contribution rides
   on top of a baseline that already includes the constructed reference floor
   (0.264 on `mel_dac`), and distances do not add. Even pessimistically
   treating the floor as additive, the 12 kHz tone clears it by 2.3× and the
   other two by 7.7–11.5×, so the conclusion survives; but **the guard's threshold
   must be set against a measured our-versus-reference baseline, not against
   the self-comparison floor measured here.** That baseline is not measured in
   this document.

---

## 8. The role question, answered

**A target is out, and not on the balance of evidence — on four independent
disqualifications, any one of which is sufficient.** **[measured, §§2, 6]**

1. It ranks our largest open drum defect **wrongly**, on all four distance
   variants ([§2.4](#24-the-tom-pitch-drop--the-decisive-test)).
2. Its floor against a real recording is dominated by an f0 difference the
   board deliberately passes, and the defect sits at that floor
   ([§2.3](#23-the-floor-that-decides-it)).
3. It is non-monotone in onset misalignment, and 2 ms of misalignment reads
   larger than the whole defect ([§6](#6-the-alignment-problem-which-gates-everything-else)).
4. It cannot distinguish a 5 % decay error from a 0.4 dB level residual, and
   the board peak-normalises both sides, so that residual is always present
   ([§2.6](#26-the-ceiling-and-the-exchange-rate)).

And a fifth reason that is not measured but is the one I would argue hardest.
**[inference]** `docs/scorecard/README.md` already refuses to average
milliseconds against cents against decibels, and requires every distance to be
normalised by *its own* tolerance with the worst reported. A spectral distance
is precisely that forbidden average, performed inside an FFT, with the weighting
set by whichever magnitude convention someone picked. Adopting it as a target
would not be adding a metric to the board; it would be repealing the board's
central rule. If it were then optimised against, the result is `#99`/`#100`'s
two-judges problem at its limit: a second judge whose verdict nobody can
interpret and whose disagreement with the first cannot be adjudicated, because
the second judge cannot say what it is disagreeing about.

**A guard is in, and [§7](#7-the-guard-hypothesis-tested) is the reason.** The
argument for it is not that the scalar is good — it is that *our per-property
coverage is deliberately narrow*. The BD case measures three properties. A
render that matches all three and still has a 12 kHz tone in it passes today,
and a guard catches it at 71× its floor. That is a real hole, and it is exactly
the shape of hole a repository whose failure mode is "internal consistency is
cheap to check" should expect to have.

**The guard's contract, stated so it cannot quietly become a target.**
**[inference]**

- It is **never normalised by a tolerance, never summed into the case's worst,
  and never rendered on the board as a distance.** It has one output:
  `blind-spot: <voice> differs at <distance> against a baseline of <b>` — a
  flag, in the diagnostics block, next to the provenance.
- It **cannot make a case pass or fail.** A case's verdict comes from the
  per-property metrics. The guard's job is to say *look again*, and the correct
  response to it firing is **to write a new per-property estimator** for
  whatever it found, not to tune anything until the guard goes quiet.
- **Any change made in response to the guard invalidates the guard as evidence
  for that change** — same logic as the scorecard's holdout rule, that a
  holdout case which has guided a change has become development data.
- It reports **`no verdict`, not a number**, whenever its preconditions fail:
  no sample-accurate alignment, or a reference whose noise floor has not been
  characterised. Per `docs/scorecard/README.md`, an invalid measurement has no
  distance, not zero distance.

**And the thing worth building instead of adopting any scalar at all.**
**[inference]** [§3.3](#33-discriminators-are-where-the-interesting-idea-is)
argues that a discriminator's value is that it is a *map* of where two signals
differ. The non-learned version of that needs no checkpoint, no corpus and no
training: take the multi-scale spectral **residual** and report **where** it is
largest, in time and in frequency band — "the disagreement is in 4–8 kHz,
between 200 and 400 ms" — and no norm at all. That output is actionable in a way
that `0.3` is not, it names a property to go and measure, and it cannot be
optimised against because it is not a number. I did not build it; it is the
recommendation I would make for the next issue.

---

## 9. Recommendation

| metric | what it would catch that we miss today | what it costs | verdict |
|---|---|---|---|
| **`mel_dac` (DAC multi-scale mel, log) as a blind-spot guard** | additive/broadband defects invisible to our three-per-voice property list: spurious tones, requantisation noise, a non-decaying noise floor — measured at **71×, 238× and 356× its own floor**, all passing every board metric ([§7](#7-the-guard-hypothesis-tested)) | ~90 lines of numpy, no new dependency; a measured our-versus-reference baseline per voice; a gate or window to keep it out of the sub-−60 dB region ([§6](#6-the-alignment-problem-which-gates-everything-else)) | **adopt as guard only** — diagnostics block, never a tolerance, never in the case's worst, and firing means *write a new estimator*, not *tune until quiet* |
| **`mss_l1` (linear multi-scale spectral)** | little. Nearly blind to an 8 dB partial imbalance (0.0456, less than a 0.45 dB level change — `mel_dac` reads 12× more on the same signal), and the only distance that survives the reference's noise floor | same as above | **reject** — the one form robust to [§6](#6-the-alignment-problem-which-gates-everything-else)'s second precondition is the one blind to the errors we care about |
| **any multi-scale spectral distance as a scorecard target** | nothing it catches survives its floor: ranks the tom defect wrongly on all four variants; floor 0.307 against a defect of 0.336–0.406, with three of four rungs *below* the floor on two of the metrics; 2 ms of misalignment lands inside the defect's own range | would repeal the board's own rule against averaging across units | **reject** — four independent disqualifications, [§8](#8-the-role-question-answered) |
| **A learned paired distance (CDPAM, OpenL3/VGGish/CLAP cosine)** | genuinely: timbral properties nobody has written an estimator for. This is a real gap | torch; a frozen, hashed checkpoint (a silent upstream weight change moves every historical result); a full floor characterisation, i.e. all of [§2](#2-what-a-multi-scale-spectral-distance-actually-reads-on-our-signals) repeated. **Unmeasurable on this host** | **reject for now** — not refuted, *unmeasured*. OpenL3 is at chance (0.507) on coarse pitch ordering ([§3.1](#31-the-result-that-decides-the-pitch-case)) and CDPAM's own abstract concedes the family generalises poorly outside its training perturbations, which do not include synth parameter errors |
| **FAD / MMD / any distributional metric** | nothing — it is not defined on our inputs. A population of one has no covariance | — | **reject** — definitional, not empirical ([§1](#1-why-most-of-this-literature-is-not-about-our-problem)) |
| **A trained discriminator (MPD / MS-STFT / sub-band CQT)** | in principle, *where* two signals differ — a map, not a scalar | adversarial training against a corpus we do not have; one reference recording per voice; an uncharacterisable floor | **reject as built** — but see the row below, which is the same idea without the training |
| **A multi-scale spectral *residual map* (no norm)** — **not built, recommended next** | names the time and frequency band of a disagreement — "4–8 kHz, 200–400 ms" — which points at a property to go and measure | small; no checkpoint, no corpus, no training | **build this instead** — it keeps everything [§3.3](#33-discriminators-are-where-the-interesting-idea-is) says is valuable about a discriminator and throws away the scalar that makes it dangerous |

### The one precondition that gates the adopt row

**None of this is usable until alignment is solved and asserted.**
[§6](#6-the-alignment-problem-which-gates-everything-else) measures 2 ms of
onset disagreement reading larger than our largest defect, non-monotonically.
The guard must therefore:

1. align by cross-correlation to sample accuracy and **write the residual onto
   the record**, and
2. **refuse — `no verdict`, not a number** — when the residual exceeds a stated
   bound, or when the reference's noise floor has not been characterised for
   that voice.

A guard that answers when it cannot is worse than one that is absent, because
its output looks exactly like data.

---

## 10. This session's wrong-then-right rate

Per `CLAUDE.md`, so a reader can calibrate any single figure above. **Five
results in this session were wrong before they were right**, every one caught
by a control or by reading output, and none by inspection:

| what | wrong | right | caught by |
|---|---|---|---|
| DAC mel filterbank, empty low bands | ×2 gain read 0.6247 | 0.69313 (ln 2) | the E0 ground-truth gate |
| decay perturbation, seconds read as ms | sweep read 1e128, then NaN | 0.0138–0.541 | reading the sweep; **the gate passed**, because the bug was in the stimulus, not the metric |
| "pre-onset noise floor" field | −3.6 dBFS | not a noise floor at all — `prepare` had already trimmed to 1 ms before onset | noticing the number was implausible |
| every number in the first draft | measured at `c752145`, before #132 changed the `prepare()` the probe depends on | re-measured at `ba14af2`; all conclusions held, two strengthened | rebasing onto `main` and re-running **before** reporting, not after |
| the whole probe, post-rebase | died with a traceback on `bd8/BD5050.WAV` | REFUSES with exit 2 and names the files that work | #132's truncation guard, which is a control I did not write |

The second is the useful one: a ground-truth gate on the estimator does not
cover the stimulus, and it looked exactly like a passing run.

The fourth is the one worth generalising. **A survey measured against a
worktree is measured against a stale premise the moment `main` moves**, and
`main` moves several times an hour here. #132 changed `prepare()` — the
alignment step this entire document is about — while the document was being
written. Had it been reported without the re-run, every table would have been
honestly produced and quietly wrong.

---

## 11. Sources

Fetched and read this session. **WebSearch was exhausted (200/200) before this
task began**, so everything here was reached by direct URL; anything I could not
reach by URL is listed in [§5](#5-where-practice-is-contested-or-absent) as not
sourced rather than recalled.

- Joseph Turian, Max Henry (2020). [*I'm Sorry for Your Loss: Spectrally-Based Audio Distances Are Bad at Pitch*](https://arxiv.org/abs/2012.04572). arXiv:2012.04572. (Table 1 read from the [ar5iv full text](https://ar5iv.labs.arxiv.org/html/2012.04572).)
- Jesse Engel, Lamtharn Hantrakul, Chenjie Gu, Adam Roberts (2020). [*DDSP: Differentiable Digital Signal Processing*](https://arxiv.org/abs/2001.04643). arXiv:2001.04643.
- Ryuichi Yamamoto, Eunwoo Song, Jae-Min Kim (2019). [*Parallel WaveGAN: A fast waveform generation model based on generative adversarial networks with multi-resolution spectrogram*](https://arxiv.org/abs/1910.11480). arXiv:1910.11480.
- Alexandre Défossez, Jade Copet, Gabriel Synnaeve, Yossi Adi (2022). [*High Fidelity Neural Audio Compression*](https://arxiv.org/abs/2210.13438) (EnCodec). arXiv:2210.13438. (Loss and MS-STFT discriminator configuration from the [ar5iv full text](https://ar5iv.labs.arxiv.org/html/2210.13438).)
- Rithesh Kumar, Prem Seetharaman, Alejandro Luebs, Ishaan Kumar, Kundan Kumar (2023). [*High-Fidelity Audio Compression with Improved RVQGAN*](https://arxiv.org/abs/2306.06546) (Descript Audio Codec). arXiv:2306.06546. (Mel-loss and discriminator configuration from the [ar5iv full text](https://ar5iv.labs.arxiv.org/html/2306.06546).)
- Kevin Kilgour, Mauricio Zuluaga, Dominik Roblek, Matthew Sharifi (2018). [*Fréchet Audio Distance: A Metric for Evaluating Music Enhancement Algorithms*](https://arxiv.org/abs/1812.08466). arXiv:1812.08466.
- Azalea Gui, Hannes Gamper, Sebastian Braun, Dimitra Emmanouilidou (2023). [*Adapting Frechet Audio Distance for Generative Music Evaluation*](https://arxiv.org/abs/2311.01616). arXiv:2311.01616.
- Pranay Manocha, Zeyu Jin, Richard Zhang, Adam Finkelstein (2021). [*CDPAM: Contrastive learning for perceptual audio similarity*](https://arxiv.org/abs/2102.05109). arXiv:2102.05109.
- Joseph Turian *et al.* (2022). [*HEAR: Holistic Evaluation of Audio Representations*](https://arxiv.org/abs/2203.03022). arXiv:2203.03022. *(Fetched; the abstract page carries no result I could quote, so nothing is claimed from it beyond its existence and scope.)*
- Jiatong Shi *et al.* (2024). [*ESPnet-Codec: Comprehensive Training and Evaluation of Neural Codecs for Audio, Music, and Speech*](https://arxiv.org/abs/2409.15897). arXiv:2409.15897. *(Confirmed to introduce the VERSA toolkit with "over 20 audio evaluation metrics"; the list of those metrics is not on the abstract page and is not reproduced here.)*
- [ICLR 2023 blog-post track index](https://iclr-blogposts.github.io/2023/blog/) — fetched; contains no post on audio distances, audio similarity or spectral losses, and none marked retracted.

### Internal, and load-bearing

- [`docs/bd-repeatability-measurement.md`](bd-repeatability-measurement.md) — the 2.74 % / 1.30 % / 0.159 dB machine floor, and the editing-noise control this document reuses.
- [`docs/tom-pitch-drop-measurement.md`](tom-pitch-drop-measurement.md) — ×1.063 / ×1.140 / ×1.236 against the shipped ×1.7.
- [`docs/scorecard/README.md`](scorecard/README.md) — per-property distance, own tolerance, worst reported, coverage stated separately; and "an invalid measurement has no distance, not zero distance".
- [`docs/reference-integrity.md`](reference-integrity.md) — run-to-run spread by plugin: 0.0000 pp for ours and Surge, 0.006–0.010 pp for Mini V3.
- [`model/audio_measure.py`](../model/audio_measure.py) — onsets "good to about 10 ms and no better"; the refusal contract every estimator here imitates.
