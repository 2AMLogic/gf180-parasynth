# #369 step 5: the tone stage's measured tilt, applied — the strike is solved, and what is left is not a filter

Step 4 (`../tone-stage/README.md`) measured the tone stage off W14b Figure 9 and deliberately did **not** apply it,
because three of the six numbers it produces are 9–18 dB uncertain. What *is* resolved there is each band's **shape**
— one RC high-pass cascaded with one RC low-pass — and #396 asked exactly one question with it:

> **Does applying the tone stage's measured tilt, and nothing else, close candidate 2's +24.0 dB residual climb at
> CY5025?**

**It does: +24.0 dB → +6.6 dB in the strike window, and candidate 3 is the first render in this chain with no
1/3 octave more than 6 dB from the 808 during the strike.** It is still **not promoted**, for a reason the same
measurement isolates and which is the more useful half of this step: **the part of the 1–2.5 kHz error that
remains is time-dependent, and no revision — including the shipped kit — has ever moved it.** A static filter
cannot be the answer to it.

## 1. The prediction, stated before the render

`model/cymbal_candidate.py`'s revision-3 docstring was written and committed **before** the candidate was rendered,
and it predicts the outcome numerically:

> *removing the differentiator's zero tilts the low band's response, relative to revision 2 and to its own level at
> its 3.175 kHz calibration centre, by +10.0 dB at 1 kHz and −13.4 dB at 20 kHz (decay band, at its 10 kHz centre:
> +19.4 and −4.0 dB), so revision 2's +24.0 dB residual climb should mostly close.*

That is **23.4 dB of tilt removed per band**, and the measured residual climb closed by **17.4 dB** (+24.0 → +6.6).
Same sign, same order, 6 dB short of the per-band arithmetic — which is what three re-levelled bands summing into
one residual should do, and is why the docstring says it "has to be confirmed by the render, not assumed" rather
than claiming the per-band figure as the answer.

## 2. What revision 3 is

Both stages are now *in* the model as real poles, and the arithmetic of putting them in **removes** structure rather
than adding it. `tools/cymbal_tone_realisation.py` decides this and states the error of every simplification per
1/3 octave, from the two committed artifacts, **before** the render — and REFUSES outside a 3.0 dB bound stated in
advance (`docs/audio-distance-metrics.md`'s board tolerance for band tilt):

| band | tone poles (Fig. 9) | realisation | active range | shape error | bound |
|---|---|---|---|---:|---|
| low | 127.7 / 589.5 Hz | **no section** — the tone low-pass pole and the 18972 Hz LEVEL differentiator cancel over the band | 2.0–8.0 kHz (7 thirds) | **0.45 dB** at 8.0 kHz | 3.0 |
| decay | 609.9 / 1549.0 Hz | **no section** — same cancellation | 5.0–16.0 kHz (6 thirds) | **1.41 dB** at 16.0 kHz | 3.0 |
| short | 406.0 / 1511.2 Hz | **exactly, and for free**: Hh3's 1-pole stage has an unused second pole slot, so its `a2` carries the 1511.2 Hz tone pole (`a1` 0.820521) | 6.3–16.0 kHz (5 thirds) | **1.35 dB** at 16.0 kHz | 3.0 |

Both stages are −6 and +6 dB/octave asymptotes over the cymbal's band, so on the low and decay bands Hh1's and Hh2's
numerators go **back** from `HP3 = (1 − z⁻¹)³` to `HP = (1 − z⁻¹)²`. This is not "no tilt": it is the measured net of
two real stages, and the deviation of treating it as flat is the table above.

**The bound is discriminating, not decorative.** The gate's historical control `CAND2_LEVEL_ONLY` is the realisation
that actually shipped as candidate 2 and measured a negative; it **fails** the 3.0 dB bound in all three bands. Five
further injected controls each turn a *different* named property red, and one is asserted blind by construction and
verified to stay blind (`tools/cymbal_tone_realisation.py --check`).

**Budget — it went down, not up.** 19 modes, 24 paths, `N_NUMS` 11: unchanged from revision 2. And **no mode of
revision 3 carries numerator code 3**, so the `HP3` decode that revisions 1 and 2 would have forced into
`modal_dp.v` is **no longer needed**. The operator's +31 % drum-area allowance (the 17th mode padding the bank to 32)
is already spent by revision 1's structure and is not increased here.

**Not applied, deliberately, exactly as in revision 2:** the inter-band **balance** (`TONE_K1[*]["peak_db"]`,
9–18 dB uncertain; `HH2_PASS_DB` / `HH3_PASS_DB` / `BP_PEAK_DB`), and the **TONE knob law** (Figure 9 identifies
only k = 1.0). One question at a time.

## 3. Preservation — passes

**All 15 non-CY sounds render bit-identically to the shipped kit** on the 19-mode layout, hats included (D15A, D16A,
OH). `candidate3.json` → `preservation`, all `true`. The rest of the kit is untouched and R1 is unchanged.

## 4. The result at CY5025

1/3-octave energy against Fischer CY5025 (dB; positive = we have more). Reproduced from a clean tree by this session:
every field of `candidate3.json` re-derived identically (`levels`, `preservation`, `bands`, `thirds`).

| window | 1.0 | 1.26 | 1.59 | 2.0 | 2.5 | 3.2 | 4.0 | 5.0 | 6.3 | 8.0 | 10 | 12.7 | 16 | 20 kHz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| shipped, 0–50 ms | **+15.4** | **+11.2** | **+10.4** | **+9.1** | **+9.4** | +0.8 | −2.3 | −2.9 | −1.9 | +2.9 | −2.3 | −2.2 | +0.2 | **−7.0** |
| candidate 2, 0–50 ms | **−15.3** | **−13.8** | **−8.0** | −4.5 | +2.8 | −0.9 | −1.4 | **−7.9** | −5.9 | +0.2 | −3.5 | +2.1 | **+8.6** | **+8.7** |
| candidate 3, 0–50 ms | −2.0 | −4.2 | +0.0 | +1.5 | +5.5 | −1.4 | −3.0 | −4.3 | −2.9 | +2.1 | −3.6 | +0.1 | +5.7 | +4.6 |
| candidate 3, 50–300 ms | **−8.8** | **−11.6** | **−10.0** | **−8.8** | −4.7 | **−6.7** | −2.0 | **−6.4** | −4.2 | +2.4 | −0.8 | +2.7 | **+6.0** | +5.5 |
| candidate 3, 300–1000 ms | **−9.5** | **−10.5** | **−8.3** | **−8.3** | −3.7 | −3.5 | +4.2 | −1.5 | −2.7 | +3.6 | +1.5 | +4.8 | **+8.4** | **+6.7** |

(Bold marks a 1/3 octave outside ±6 dB. The `candidate 3, 0–50 ms` row has none — that is the headline.)

Summarised three ways, none of them an averaged score — **tilt** is the residual at 20 kHz minus the residual at
1 kHz (the quantity #396 asked about), **worst** is the largest \|residual\| over 1–20 kHz, and **n>6** counts the
1/3 octaves outside ±6 dB. Tilt is differenced before rounding, so it will not always equal the difference of the
two rounded entries in the table above (0–50 ms shipped: −7.03 − (+15.43) = −22.46):

| window | | shipped | candidate 1 | candidate 2 | **candidate 3** |
|---|---|---:|---:|---:|---:|
| 0–50 ms | tilt | −22.5 | +20.5 | +24.0 | **+6.6** |
| | worst | 15.4 | 14.4 | 15.3 | **5.7** |
| | n>6 dB | 6/14 | 7/14 | 6/14 | **0/14** |
| 50–300 ms | tilt | −17.3 | +26.7 | +30.0 | **+14.3** |
| | worst | **9.4** | 19.0 | 20.4 | 11.6 |
| | n>6 dB | **2/14** | 8/14 | 10/14 | 7/14 |
| 300–1000 ms | tilt | −9.9 | +31.2 | +32.8 | **+16.2** |
| | worst | **8.8** | 20.7 | 21.1 | 10.5 |
| | n>6 dB | **1/14** | 8/14 | 8/14 | 6/14 |

Band measures:

| | H−L | H EDT10 | H late T20 | Ln EDT10 |
|---|---:|---:|---:|---:|
| **808 CY5025** | **8.16 dB** | **147.8 ms** | **432 ms** | **591 ms** |
| shipped | 10.82 | 151.3 | *refused* | 598 |
| candidate 1 | 8.64 | 162.4 | *refused* | 542 |
| candidate 2 | 10.60 | 155.9 | *refused* | 549 |
| candidate 3 | 12.09 | **147.2** | 363 | 578 |

Candidate 3 has the closest H EDT10 of any render (0.6 ms from the 808), and is the only one of the four whose high
band's late decay is **measurable at all** rather than refused as non-exponential — the 808's is measurable too, so
this is the first render whose high band has the 808's single-slope late character. Its H−L, however, moves **away**
from the 808 (8.16 target; 10.82 shipped → 12.09), which is expected and is not a defect of this step: the level
rule anchors each band to the *shipped kit's* energy in one 1/3 octave, and the shipped kit's own H−L is 2.7 dB
wrong. **No candidate built on that rule can fix the band balance**, and the balance is precisely what step 4 bounded
at 9–18 dB and refused to assert.

## 5. Verdict

**Not promoted. Nothing reaches RTL. R1 is unchanged.**

- **The question #396 asked is answered, positively.** The strike window's tilt closed from +24.0 to +6.6 dB and its
  worst 1/3 octave from 15.3 to 5.7 dB — against the *shipped* kit's 15.4 dB, so this is the first candidate in the
  chain that is better than what ships, on the defect the operator's A/B named ("below 2.5 kHz the strike has
  9–15 dB too much energy", `../README.md` §3).
- **The tail windows are still worse than shipped** (worst 11.6 and 10.5 dB against 9.4 and 8.8), and the sign has
  flipped: where shipped had a 1–2 kHz *excess* after the attack, candidate 3 has a 8–12 dB *deficit*. Halving
  candidate 2's error is not the same as being right, and a candidate that is worse than the shipped kit in two of
  three windows does not ship.
- **Acceptance item 3 (accuracy across all 25 TONE/DECAY settings) is not claimed and cannot yet be tested at all**,
  for the reason already recorded in `../README.md` §4 and filed as **#371**: the knob-law render is not the
  instrument. The candidate renders at one fixed setting, so the frozen confirmation set (16 settings, including
  CY2500) cannot be rendered against it. This is a blocker on the *instrument*, not a missing measurement.

## 6. The finding that matters more than the candidate

Candidate 3's 1–2.5 kHz residual is near zero during the strike and 8–12 dB negative afterwards. **A static filter
change cannot do that** — it moves every time window by the same amount. So the 1–2.5 kHz discrepancy separates into
a static part and a time-dependent part, and differencing the residual *along time* isolates the second, because a
difference of residuals cancels every static term (ours and the 808's alike):

**How much more our 1/3 octave falls than the 808's does, strike → 50–300 ms (dB; negative = we decay faster):**

| render | 1.0 | 1.26 | 1.59 | 2.0 | 2.5 | 3.2 | 4.0 | 5.0 | 6.3 | 8.0 | 10 | 12.7 | 16 | 20 kHz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| shipped | −6.0 | −7.5 | −9.7 | −10.8 | −10.7 | −6.0 | +1.0 | −2.8 | −1.6 | +0.3 | +3.4 | +2.6 | −0.7 | −0.9 |
| candidate 2 | −5.1 | −6.6 | −9.6 | −9.5 | −10.0 | −5.6 | +0.9 | −0.3 | −1.3 | +0.2 | +2.9 | +2.4 | +0.2 | +0.8 |
| candidate 3 | −6.8 | −7.4 | −10.0 | −10.4 | −10.2 | −5.4 | +1.1 | −2.0 | −1.3 | +0.3 | +2.9 | +2.6 | +0.3 | +0.9 |

**Across 1–2.5 kHz the three renders agree to within 1.7 dB while reading −5.1 to −10.8 dB** (worst spread 1.64 dB, at
1 kHz), and they differ from each other by **30.7 dB** of static residual in that same 1 kHz third during the strike
(shipped +15.4 against candidate 2's −15.3). So this quantity is measuring something no revision has touched.
It is not blind: at 5.04 kHz the same quantity spreads **7.3 dB** across the three renders on the
strike → 300–1000 ms difference (+0.8 / +8.1 / +2.8), so it does move when a revision changes what decays there.

**The conclusion, and it is structural rather than a tuning direction:** the cymbal's remaining 1–2.5 kHz error is in
the **envelopes or the source**, not in the filter magnitudes — and therefore **not** in the inter-band balance
either, since a balance is also static. The balance is what step 4 bounded at 9–18 dB and what #396 left open; this
says resolving it would not fix the tail. The shipped kit has had this same 5–11 dB decay error all along; its +11 dB
strike excess was masking it.

### Evidence strength of section 6 — read this before quoting it

This is **not** a qualified decay measurement and must not be reported as one. The qualified decay measures are
`cymbal_bands.measure()`'s EDT10 and late T20, which have known-answer tests, controls and refusals, and which are
defined on L / Ln / H only — **none of the three covers 1–2.5 kHz**. The table above is an *energy ratio between two
fixed windows*, derived by arithmetic from the frozen `thirds()` instrument:

- **What it can support**: that the 1–2.5 kHz discrepancy has a time-dependent component, and that its size is
  invariant across three renders whose static responses differ by up to 30 dB.
- **What it cannot distinguish**: a faster single exponential from a different two-slope mix from an onset-alignment
  difference inside the 0–50 ms window. It also inherits `thirds()`'s window edges, and its own floor is a couple of
  dB (at 10 kHz, where H EDT10 agrees to 0.6 ms, it still reads +2.9).
- **Adding a 1–2.5 kHz band to `cymbal_bands.BANDS` would change the frozen instrument mid-selection**, which
  plan098's rules forbid — a rubric fix is a separate, reviewed change. So it was not done here, and qualifying this
  quantity is filed instead.

## 7. Listening pack

`tools/make_cymbal_pack.py --out <dir>` (committed in step 2, unchanged, and it reads the *current*
`model/cymbal_candidate.py`, so it renders revision 3 with no edit). Built this session at
`/tmp/gf180-listen/cymbal-candidate3/`:

```
CY5025-AB-808-shipped-808-candidate.wav   808, shipped, 808, candidate — 0.25 s gaps
CY5025-808.wav  CY5025-shipped.wav  CY5025-candidate.wav        the matched single hits
```

Each hit is matched to the 808's K-weighted loudness (BS.1770 K filter, gated to within 20 dB of its loudest 50 ms
block), then all three scaled together to −1 dBFS peak. **Listening only; the measurements decide promotion.** Other
TONE/DECAY settings are not in the pack because they cannot be rendered — see §5, #371.

## 8. The next question, and it is one question

**What gives the 808's 1–2.5 kHz a component that survives 5–11 dB longer than ours, and what does our model not
have that produces it?** Filed as **#400**. It needs two things in order:

1. **A qualified decay measurement covering 1–2.5 kHz** — an extension to `cymbal_bands.py` with its own
   known-answer tests and controls, reviewed as a rubric change, so §6's indication becomes evidence or is refuted.
2. Then the structural candidates, tested against the circuit rather than fitted: the low band's VCA envelope shape
   (§10's "fixed, medium" RC against what the recordings show, `../README.md` §1 already records that the machine
   contradicts §10 on which band DECAY moves), the six-square source's low partials, and the AC coupling of #165/#152.

Still open from earlier steps and **not** on the path to the tail defect, per §6: the inter-band balance (#396's
deferred half, 9–18 dB bounded) and the TONE knob law. §6 **reprioritises #396** rather than blocking it — resolving
the balance would not fix the tail. Still blocking any knob claim, and therefore acceptance item 3 entirely: **#371**.

## Files

- `candidate3.json` — levels, preservation, all band measures and 1/3 octaves, with commit and dirty flag.
  Re-derived identically by this session from a clean tree.
- `tools/cymbal_tone_realisation.py` — the realisation and its per-1/3-octave error, `--report` / `--check` / `--json`.
- `tools/test_cymbal_tone_realisation.py` — the tests, including the controls that must fail.
- `model/cymbal_candidate.py` — revision 3, with the prediction of §1 in its docstring.
- Reproduce: `python3 tools/cymbal_candidate_eval.py --out <path>` (≈4.5 min), then
  `python3 tools/make_cymbal_pack.py --out <dir>` (≈1.5 min).
