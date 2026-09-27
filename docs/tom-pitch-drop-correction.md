# The toms' pitch drop, corrected

**One deliverable: the toms' pitch drop set to its measured value, with the
before and after pitch trajectories measured against the real machine.**

This is the first sound change of the project. Everything before it in this line
was measurement: [`tom-pitch-drop-measurement.md`](tom-pitch-drop-measurement.md)
(#110) measured the drop from 99 clean-digital tom files and 66 conga files of a
real TR-808 and changed no coefficient. This document changes the coefficient.

**It is a fit, not a tune.** `TOM_DROP_RATIO` is the fitted law evaluated at a
stated reference setting (×1.060), close to but not identical with that
setting's own measured median (×1.054) — grounded in hardware, not tuned to
improve a score. Every comparison below is against a named recording of that
hardware; none is against ×1.7, and none is against a scorecard number. #99 —
which blocks anything fitted to a metric — does not reach this, and the board
movement in §6 is reported as a **consequence**, not as the objective.

---

## 1. What changed

| | before | after |
|---|---|---|
| magnitude | `TOM_DROP_RATIO = 1.7` | `TOM_DROP_RATIO = 1.060` |
| accent | `min(max(accent, 0), 1)` — a clamp | `max(0, accent − A0)` — a threshold |
| tuning | ignored | `exp(G · (f0/f0_nominal − 1))` |
| position | one law for tom and conga | separate thresholds and slopes |
| step hold | the interval's **left edge** | the interval's **mean** |
| relaxation | `exp(−3t/60 ms)`, 6 steps | **unchanged** |

```
excess = (TOM_DROP_RATIO − 1) · max(0, accent − A0)/(1 − A0_tom)
                              · exp(G · (f0/f0_nominal − 1))
```

`TOM_DROP_RATIO` is still the single knob that scales the whole sweep. What
changed is that it now names a **stated** setting — accent 1.0, the TUNING pot
at its centre, the TOM position of the circuit — instead of an unqualified
maximum, and that the number is the fitted law evaluated at that setting
(×1.060), against that same setting's own measured median (×1.054), rather
than inferred.

The measurement, restated so this page stands alone. Onset f0 ÷ settled f0,
median over 11 TUNING positions × 3 voices:

| accent | n | median | range | τ |
|---|--:|--:|---|--:|
| no accent | 23 | **×1.063** | ×1.040 – ×1.094 | 13.0 ms |
| accent | 33 | **×1.140** | ×1.085 – ×1.272 | 24.5 ms |
| more accent | 33 | **×1.236** | ×1.169 – ×1.344 | 33.1 ms |

**×1.7 occurs in none of the 99 files.** The largest drop anywhere is ×1.344.

---

## 2. Four faults, not one

**1. The magnitude** was 3× too large at the loudest hit measured and 11× too
large at an unaccented one.

**2. The clamp.** `min(max(accent, 0), 1)` handed the *full* sweep to an
unaccented hit — which is most of what an 808 plays — where the machine does
×1.06. Correcting the magnitude alone would have left the accent curve wrong at
the bottom. The measured excesses at the three recorded accent levels are
**0.054 / 0.143 / 0.239**: a straight line that does **not** pass through the
origin. Germanium diodes do not conduct below a drive, so the law has a
threshold, and below it a hit does not sweep at all.

**3. The TUNING pot**, which the shipped sequence ignored entirely. LT at *More
Accent* runs ×1.169 at 82 Hz and ×1.325 at 101 Hz — the excess nearly doubles
across the pot. §4's circuit reading predicts a dependence; its direction and
size here are the measurement's, and the fitted slope is
**3.58 ± 0.14** per unit `f0/f0_centre` for the tom position.

**4. The step hold** — found only because the trajectories were measured rather
than the scalars. See §4. The law reads back exactly; the staircase that
delivered it read back **22–38 % high**.

### The tom and the conga are not one law, and it is not about frequency

`HT` and `LC` are **both nominally 185 Hz**, on the same bridged-T with a
capacitor switched (§4, SW8). Unaccented, their measured excesses are **0.061
and 0.0055** — eleven times apart at the same pitch. So the drop cannot be a
function of f0, and a law that made it one would be wrong here and nowhere
else. The tuning term is therefore normalised to each **position's** own
nominal, and the conga position carries its own threshold (1.064 against the
tom's 0.670) and its own tuning slope (7.46 ± 0.09 against 3.58 ± 0.14).

The conga constants are a forced consequence of a shared code path, not a
second deliverable: the congas are the same circuit and `hit_writes` sweeps
them through the same sequence. Left on the tom law they would have swept 12×
too far at no accent.

---

## 3. The accent map is an assumption, and it is labelled one

The pack ships **A = No Accent, B = Accent, C = More Accent** — ordered, and
calibrated to no scalar. The model's accent is continuous. The map used is:

| level | model accent | why |
|---|--:|---|
| A | 1.0 | the model's plain hit (`'x'` in `drums_fx.pattern`); the 808's step with the accent bit off |
| B | 1.4 | the model's accented hit (`'X'`) |
| C | 2.0 | the model's legal maximum; the 808's ACCENT pot at maximum, the common trigger's 14 V end (§1.1) |

`model/tom_drop_fit.py --accent-map` re-runs the whole fit under any other map.
What that sensitivity shows matters more than the choice:

| map (A,B,C) | excess at accent 1.0 |
|---|--:|
| 1.0, 1.2, 2.0 | 0.079 |
| 1.0, 1.4, 2.0 *(shipped)* | 0.060 |
| 1.0, 1.5, 2.0 | 0.053 |
| 1.0, 1.4, 1.8 | 0.053 |
| 0.8, 1.0, 1.4 | 0.126 |

**Every map tried lands between 0.053 and 0.126.** The shipped 0.70 is outside
all of them, by 5–13×. The map is a real assumption about a real unknown, and it
cannot rescue ×1.7.

---

## 4. The fourth fault: a staircase that sat above its own law

The corrected law was measured back through `tom_pitch_probe` and read **25–70 %
high**. The law was not the problem. A continuous `exp(−3t/60 ms)` carrying an
excess of 0.060 / 0.133 / 0.242 comes back out of the probe at **0.0598 / 0.1323
/ 0.2409** — the instrument recovers it to better than 0.4 %.

The six-step host sequence was. A coefficient written at `t_i` is held until
`t_{i+1}`, so writing `exp(−3 t_i / T)` holds the curve's **highest** value
across the whole interval and the staircase sits above the law everywhere. The
first step held the full excess for 10 ms, where the law has already relaxed to
0.61 of it.

Measured against the continuous law over 0–120 ms at excess 0.060:

| hold | rms error in f0 | max error | probe reads | inflation |
|---|--:|--:|--:|--:|
| left edge (was) | 0.00546 | 0.02357 | ×1.0827 | **+37.8 %** |
| interval midpoint | 0.00292 | 0.01327 | ×1.0655 | +9.2 % |
| **interval mean (now)** | **0.00291** | **0.01278** | ×1.0661 | **+10.2 %** |

Half the trajectory error, at the **same six steps and the same fourteen
writes** — so nothing in §15.7.1's write count or `fpga/link_budget.py` moves.
The mean of `exp(−3t/T)` over one step of `T/S` is
`(S/3)(e^(−3i/S) − e^(−3(i+1)/S))` in closed form; the last write is the
endpoint, because nothing is held after it.

This fault was invisible to a scalar and invisible to a comparison against
×1.7. It was visible in a trajectory.

---

## 5. Held out

The law was derived from the merged per-file table, and then four splits were
run in which it never saw the rows it was tested on
(`model/tom_drop_fit.py`, `docs/tom-pitch-drop-law.json`). Errors are in excess
units, where the shipped law's error was **0.56**.

| held out | train | n | bias | rms | worst |
|---|---|--:|--:|--:|--:|
| an entire accent level (**B**) | A + C | 33 | −0.018 | 0.029 | 0.085 |
| half the TUNING positions | odd positions | 40 | −0.003 | 0.018 | 0.058 |
| an entire voice (**MT**) | LT + HT | 31 | +0.019 | 0.022 | 0.035 |
| both ends of the pot | positions 03–09 | 32 | −0.002 | 0.024 | 0.068 |

Worst held-out bias **0.019**, about **30× smaller** than the error being
corrected. The pot-ends split is the one that matters most for a law with an
exponential in it: trained on the middle seven positions it extrapolates to both
ends with a bias of −0.002.

**And one hold-out nobody arranged.** The correction is derived entirely from
the *808 From Mars* corpus. The scorecard's tom cases score against the
**Fischer TR-808 s/n 103852** recordings — a different machine, a different
engineer, a different decade, reached by a different path. Nothing in §6 was
used to derive anything in §1.

---

## 6. Before and after, against the machine

`model/tom_drop_compare.py` renders the model, rate-converts it to the
recordings' 44.1 kHz and puts **both sides through the same estimator**
(`model/tom_pitch_probe.py`, #110's gated instrument). Every reference column
is a named *808 From Mars* clean-digital member. **Nothing here is compared
against ×1.7.**

Two preconditions are asserted rather than assumed, every run:

- every reference member is REFUSED unless it is byte-for-byte the size
  `refaudio/index/808-from-mars.tsv` lists (the index's own SHA-256 matches the
  one #110 recorded, `d148c98a…`);
- the 48 kHz → 44.1 kHz conversion is put through **three known drops** first
  and REFUSES if any moves by more than 1 % of its excess. It moves ×1.06,
  ×1.24 and ×1.70 by **0.01 %, 0.06 % and 0.23 %**.

Onset excess (f0 ÷ settled f0, minus 1) over 20 settings — three voices × three
accent levels at the pot centre, plus both pot ends at *More Accent*, plus the
three congas:

| | before | after |
|---|--:|--:|
| **bias** | **+0.7113** | **+0.0208** |
| rms | 0.7188 | 0.0302 |
| worst | 0.8398 | 0.0682 |
| trajectory rms, fraction of f0 | 0.076 – 0.179 | 0.0007 – 0.075 |

Per setting, onset ratio — machine, then model before, then model after:

| voice | level | pot | machine | before | after | after err |
|---|---|--:|--:|--:|--:|--:|
| LT | Accent | 06 | ×1.1320 | ×1.9120 | **×1.1366** | +0.005 |
| LT | More Accent | 06 | ×1.2216 | ×1.9127 | **×1.2348** | +0.013 |
| LT | More Accent | 01 | ×1.1689 | ×1.9846 | **×1.1981** | +0.029 |
| LT | More Accent | 11 | ×1.3249 | ×1.8873 | **×1.3676** | +0.043 |
| MT | No Accent | 06 | ×1.0436 | ×1.8834 | **×1.0758** | +0.032 |
| MT | Accent | 06 | ×1.1251 | ×1.8834 | **×1.1632** | +0.038 |
| MT | More Accent | 06 | ×1.2217 | ×1.8838 | **×1.2864** | +0.065 |
| MT | More Accent | 01 | ×1.1738 | ×1.8776 | **×1.2257** | +0.052 |
| MT | More Accent | 11 | ×1.3442 | ×1.8504 | **×1.3537** | +0.009 |
| HT | No Accent | 06 | ×1.0633 | ×1.8142 | **×1.0700** | +0.007 |
| HT | Accent | 06 | ×1.1515 | ×1.8387 | **×1.1514** | −0.000 |
| HT | More Accent | 06 | ×1.2615 | ×1.8391 | **×1.2783** | +0.017 |
| HT | More Accent | 01 | ×1.2027 | ×1.8555 | **×1.2095** | +0.007 |
| HT | More Accent | 11 | ×1.3324 | ×1.8305 | **×1.3369** | +0.005 |
| LC | No Accent | 06 | ×1.0044 | ×1.8416 | **×1.0000** | −0.004 |
| LC | Accent | 06 | ×1.0612 | ×1.8420 | **×1.1294** | +0.068 |
| MC | No Accent | 06 | ×1.0029 | ×1.8196 | **×1.0000** | −0.003 |
| MC | Accent | 06 | ×1.0643 | ×1.8207 | **×1.0881** | +0.024 |
| HC | No Accent | 06 | ×1.0047 | ×1.8055 | **×0.9999** | −0.005 |
| HC | Accent | 06 | ×1.0586 | ×1.8068 | **×1.0741** | +0.016 |

*(LT at No Accent is absent because the reference row refuses: it is one of
#110's nine `REFUSED` accent-A rows, and a row that claims nothing cannot be a
comparator.)*

**Three things only a trajectory shows, and a scalar would have hidden.**

1. **The old model's curve is flat across accent.** MT reads ×1.8834 at accent
   1.0 and ×1.8838 at accent 2.0. That is the `min(max(accent,0),1)` clamp,
   visible as a shape.
2. **It read ×1.88, not ×1.70.** The left-edge staircase inflated even the
   inferred ratio by 11 %.
3. **MT is the worst voice after the change** (+0.032 to +0.065) — and MT is
   exactly the voice the fit held out, where the hold-out predicted +12.9 %.
   The residual was forecast before it was seen.

---

## 7. Board delta

Sixteen drum cases re-run against the **Fischer TR-808 s/n 103852** corpus —
a different machine from the one the correction was derived from.
`tools/run_case.py D01A … D16A --refs /tmp/tr808-ref`.

### The deliverable

| case | voice | reference | before | after | tol | before | after |
|---|---|--:|--:|--:|--:|--:|--:|
| `D03A` | LT | 1.95 Hz | 43.87 Hz | **2.50 Hz** | 8.86 | 4.73 | **0.06** |
| `D05A` | MT | 1.94 Hz | 75.53 Hz | **3.87 Hz** | 13.52 | 5.44 | **0.14** |
| `D07A` | HT | 3.20 Hz | 92.30 Hz | **5.89 Hz** | 18.76 | 4.75 | **0.14** |

Pitch drop goes from **4.7–5.4 × tolerance to 0.06–0.14 ×**, against hardware
that had no part in deriving the law.

Read the size honestly: `pitch_drop_hz` states its own floor at **about 2 Hz**,
and the references read 1.95 / 1.94 / 3.20 Hz. Two of the three references are
*at* that floor, so what this now says is "the model's sweep is as small as the
machine's, and both are near the limit of what this estimator resolves" — which
is the correct outcome and is not the same as agreement to 0.5 Hz.

### Every case that moved, and every case that did not

| case | before | after | worst metric before → after |
|---|--:|--:|---|
| `D03A` low tom | fail 4.73 | fail **6.04** | Pitch drop → **body spectrum** |
| `D05A` mid tom | fail 5.44 | fail **5.46** | Pitch drop → **body spectrum** |
| `D07A` high tom | fail 4.75 | fail **4.51** | Pitch drop → **body spectrum** |
| `D04A` low conga | no verdict | no verdict | invalid: decay (unchanged) |
| `D06A` mid conga | fail 1.39 | fail **3.99** | body spectrum → body spectrum |
| `D08A` high conga | **pass 0.97** | **fail 3.64** | — → **body spectrum** |
| the other ten | — | — | **identical to the last decimal** |

`D01A`, `D02A`, `D09A`–`D16A` did not move at all. That is the control on the
whole change: `AMP_TOM` was re-balanced and only the six tom/conga positions
changed, exactly as intended.

**`D08A` is a regression and is reported as one.** The high conga passed at 0.97
— inside tolerance by 3 % — and now fails at 3.64.

---

## 8. What is still wrong

### The toms and congas are short of high-band energy, and the wrong sweep was hiding it

`body spectrum` moved against us on all six:

| case | reference | before | after | already failing before? |
|---|--:|--:|--:|---|
| `D03A` LT | −22.53 dB | −36.57 | −40.64 | yes, 4.68 |
| `D05A` MT | −21.84 dB | −31.99 | −38.23 | yes, 3.38 |
| `D07A` HT | −23.23 dB | −30.30 | −36.78 | yes, 2.36 |
| `D04A` LC | −29.76 dB | −36.35 | −42.18 | yes, 2.20 |
| `D06A` MC | −27.42 dB | −31.59 | −39.39 | yes, 1.39 |
| `D08A` HC | −31.36 dB | −34.26 | −42.27 | no, 0.97 |

All six were **already** short of energy above the split — by 3 to 14 dB —
before anything here was touched, and five of six were already failing. The
×1.7 sweep was manufacturing **4 to 8 dB** of high-band energy, and that
spurious energy was partially masking a separate, larger and pre-existing
deficiency. Removing it did not create the fault; it stopped paying for it.

What the deficiency is, is not settled here and this document does not claim
it. Two candidates are on record and neither has been measured: §4's
**pink-noise rumble** (`toms only`, τ ≈ 85 ms, low-passed at ≈ 400 Hz), which
§15.5 records as not implemented — but the congas show the same shape and §4
says congas have no noise — and the harmonic content a nonlinear VCA puts on a
ring that our single 2-pole mode emits as a pure sinusoid.

### The congas' settled pitch, also uncovered

| case | reference | before | after |
|---|--:|--:|--:|
| `D04A` LC | 200.43 Hz | 192.21 (0.41) | **185.04 (0.77)** |
| `D06A` MC | 281.09 Hz | 291.20 (0.36) | **280.00 (0.04)** |
| `D08A` HC | 412.43 Hz | 415.83 (0.08) | **400.00 (0.30)** |

The model's congas now settle on `TOM_PRESET`'s chart frequencies — 185 / 280 /
400 Hz — because the sweep no longer lifts them. The Fischer machine reads
**200 / 282 / 412**, which is the offset `docs/tr808-reference.md` already
recorded (`+8 / +1 / +3 %`, inside §1.7's ±10 %). The ×1.7 sweep was still
decaying inside the measurement window and had been accidentally compensating
it on LC and HC. All three remain inside tolerance; this is a pre-existing
offset made visible, not a new one. **It is the same shape as the body-spectrum
finding: a wrong sweep was paying a different bill.**

### Deviations knowingly left in the law

- **τ is fixed at 20 ms and the machine's is not.** Measured τ is
  **13.0 / 24.5 / 33.1 ms** at the three accent levels; `exp(−3t/60 ms)` is
  20 ms at all three. The exponential shape and the 60 ms span are the parts of
  §15.7.1 that survived contact with hardware (88 of 89 rows beat a linear
  ramp), so they are unchanged, and making τ accent-dependent is a separate
  change with its own evidence to assemble.
- **The six-step hold still reads about 10 % high** on the excess, down from
  22–38 %. Removing the rest needs more steps, which moves the write count and
  the link budget.
- **`LC` at *Accent* is the worst row** at +0.068, where the law predicts
  ×1.129 and the machine does ×1.061. The conga threshold is fitted to one
  level — the pack records congas at A and B only — so it has no held-out
  accent level, and that is stated rather than papered over.
- **The accent map is an assumption** (§3). It is the largest unquantified term
  in this change.

---

## 9. Provenance

- Branch `sound-tom-pitch-drop`, rebased onto `origin/main` `24937ca`.
- **Law** from `docs/tom-pitch-drop-results.json` (#110, merged), itself from
  `808-from-mars.zip` SHA-256 `f567c676…`, index SHA-256 `d148c98a…`. The index
  hash this branch reads matches the one #110 recorded, byte for byte, and every
  one of the 165 cached members matches its indexed size.
- **`tools/refaudio_fetch.py` still exits 2 on this host** (`REFAUDIO_SSH`
  unset) and is unmodified. The reference audio was reached through the cache
  `tools/refaudio_local.py` filled, and `tom_drop_compare.py` re-asserts the
  per-member size check at the point of use, refusing otherwise.
- Commands:

  ```sh
  python model/tom_drop_fit.py --json docs/tom-pitch-drop-law.json
  python model/tom_drop_compare.py --stage before --json docs/tom-pitch-drop-before.json
  python model/tom_drop_compare.py --stage after  --json docs/tom-pitch-drop-after.json
  python model/drums_fx_render.py --balance
  python -m pytest model/test_drums_fx.py            # 43 passed
  python tools/run_case.py D01A .. D16A --refs /tmp/tr808-ref
  ```

- Held-out splits, per-cell fits and the accent-map sensitivity:
  `docs/tom-pitch-drop-law.json`. Per-setting trajectories, both stages:
  `docs/tom-pitch-drop-before.json`, `docs/tom-pitch-drop-after.json`.
- Both instruments carry their validation cases in
  `model/test_tom_drop_law.py`, including the refusals demonstrated red and the
  test that locks `drums_fx`'s shipped constants to the committed law.
- The trajectories, plotted and browsable:
  <https://claude.ai/artifact/PaJS6CCUpSour1MjkxkGTe>

### Wrong-then-right rate for this change

Three results were wrong before they were right, all caught by a control rather
than by inspection:

1. The corrected law measured **25–70 % high** on first contact with the
   recordings. The cause was the staircase's left-edge hold (§4), not the law —
   established by putting the *continuous* law through the same probe, where it
   came back to 0.4 %.
2. Six of twenty rows **refused** after the correction, as `only 0 clean
   periods after the pulse`. The cause was the model's exactly-zero tail
   reporting a −400 dBFS floor to a probe that measures its floor from the last
   30 ms. Trimming the dead tail fixed it; no sample inside the ring was
   touched.
3. The kit's tom levels were **61 % hot** after the change, caught by
   `test_kit_voices_sit_at_the_chart_levels` rather than by listening: the ×1.7
   sweep had been detuning the resonator while the pulse was still in it and
   costing the toms 38–44 % of their ring. `--balance` re-run, procedure
   unchanged.
