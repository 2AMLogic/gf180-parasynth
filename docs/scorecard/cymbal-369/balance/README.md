# #369 cymbal, the inter-band balance (#396) — decomposed, bounded, and REFUSED, with the blocking factor measured

> **Step numbering.** PR #410 ("step 6: the 1–2.5 kHz decay qualified") was open and unmerged while this was
> written, so this file deliberately carries no step number. The two are parallel, not sequential, and they agree:
> #410/#411 name *"its level rule anchoring each band to the shipped kit's energy in one 1/3 octave"* as the prime
> suspect for the mid band, and §1 below measures that rule against the circuit for all three bands.

Step 5 (`../candidate3/README.md`, PR #402) applied the tone stage's measured **tilt** and left the **balance** — the
three bands' levels relative to one another — where it was. #396 then asked for the balance, preferring
**SN p.13's R/C values around VR4** over any further fitting of W14b Figure 9.

**This step does not resolve the balance. It measures why not, and the answer is not the one the issue expected.**

- #396 (and reference §18) put the obstacle at **9–18 dB of figure-reading uncertainty** on Ht1 and Ht2.
- Evaluated where each band's level is actually set, that uncertainty is **smaller** than quoted — the low band's
  tone term is **7.4 dB** wide at its own 3175 Hz calibration third, not 18.0 dB, and the short band's is
  **0.04 dB** because 10079 Hz is inside Ht3's plotted range.
- What is actually blocking the balance is a factor **neither figure carries**: the three swing VCAs' drive levels.
  Against the shipped-kit level rule the model currently uses, the filters-plus-tone balance alone demands
  **+9.9 dB on the decay band and +38.2 dB on the short band**. A 38 dB residual is not a 9–18 dB figure problem.

`tools/cymbal_band_balance.py` is the instrument; `tools/test_cymbal_band_balance.py` is its known answers and the
controls that must fail. `--check` passes with 5/5 injected controls turning a named property red and one control
asserted blind-by-construction and verified blind.

## 1. The decomposition

Each band's level, **at the frequency its level is set** (`cymbal_candidate_eval.CENTRE`: the low band's third at
3175 Hz, both high bands' at 10079 Hz), with the LEVEL differentiator's corner at 18972 Hz (W14b Fig. 10):

| band | centre | BP peak | BP shape | HP pass | tone | tone ± | LEVEL | total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| low | 3175 Hz | +22.95 | −3.00 | +0.00 | −39.52 | 3.68 | −15.65 | **−34.27** |
| decay | 10079 Hz | +24.10 | −12.88 | +6.03 | −28.62 | 6.95 | −6.57 | **−17.09** |
| short | 10079 Hz | +24.10 | −12.88 | +8.86 | −36.61 | 0.02 | −6.57 | **−9.62** |

| factor | where it comes from |
|---|---|
| band-pass peak | W14b Fig. 4 (`tools/werner_fig4.py`), whose digitiser is gated on SN p.13's R56–R59, C13–C16 — **resolved** |
| high-pass pass band | W14b Fig. 4, same gate — **resolved** |
| tone stage | W14b Fig. 9 (`tools/werner_fig9.py`) — **bounded**, per-band widths above |
| LEVEL differentiator | W14b Fig. 10 — common to all three bands, so it cancels in a ratio; its *frequency dependence* does not, because the bands are levelled at different frequencies |
| **VCA drive** | **ABSENT.** Three envelope generators and three swing VCAs (Q16/Q17/Q18, reference §10) sit between the band-passes and the high-passes. No W14b figure plots them and no artifact in this repository carries them. |

Relative to the low band:

| band | circuit | bound | shipped-kit rule (candidate 3) | gap |
|---|---:|---|---:|---:|
| low | +0.00 | [−7.36, +7.36] | +0.00 | +0.00 |
| decay | **+17.18** | [+4.94, +26.20] | +7.33 | **+9.85** |
| short | **+24.65** | [+20.38, +27.78] | −13.58 | **+38.23** |

The gap is what the two absent preconditions have to account for: the VCA drive ratios, **and** the shipped kit's own
band-gain errors (it has no Hh1 at all and routes the short band through the closed hat's 11.7 kHz high-pass).
Nothing available here separates those two — saying which is which is exactly what the schematic would do.

## 2. The refusal, and why it is not a decree

`balance_gains()` REFUSES on this tree and names both missing preconditions with the path each would live at:

```
REFUSED: the inter-band balance is not applicable: schematic-vr4 absent
(docs/scorecard/cymbal-369/sn-p13-vr4.json) -- needed for SN p.13's R/C values around VR4 …;
vca-drive absent (docs/scorecard/cymbal-369/vca-drive.json) -- needed for the three envelope
generators' and swing VCAs' peak drive (Q16/Q17/Q18), which no W14b figure plots
```

Two things keep that from being an opinion compiled into a function:

- **It lifts.** Hand the tool a complete precondition set and it answers. `refusal-live` asserts both directions on
  every run, and the control `PRECOND_ALWAYS_OK` turns it red. An unsatisfiable gate is worse than no gate.
- **The excluded route refuses for its own, different reason.** `figure_route_gains()` is the route #396 excludes —
  take Figure 9's fitted `peak_db` values and apply them — and it refuses because the propagated bound is ±7.4 dB
  (low), ±10.6 dB (decay) and ±3.7 dB (short) against the **3.0 dB** board tolerance for a band split
  (`docs/audio-distance-metrics.md`, 19× the machine's own 0.159 dB unit-to-unit band-split spread). The exclusion
  is therefore a measurement, not a decree.

## 3. The prediction, stated before the render

The refusal above is arithmetic on transfer functions. Candidate 3's own post-mortem is the reason that is not
enough: *"the cymbal's VCAs clip … so this is arithmetic on transfer functions and has to be confirmed by the
render, not assumed."* So the resolved factors are applied as a **diagnostic ablation** —
`tools/cymbal_candidate_eval.py --variant balance`, which sets the relative band levels from the circuit's
filters-plus-tone balance and **holds the three VCA drives equal**, that last being the assumption under test.

**Written and committed before the ablation was rendered:**

> The two high bands rise by +9.85 dB (decay) and +38.23 dB (short) relative to the low band. In the H band
> (6–14 kHz) the decay band currently dominates the short band by 21 dB, and after the change the short band
> dominates instead; the H band's energy therefore rises by about **+18 dB** while L (2–5 kHz) is unchanged. So
> **H−L should go from candidate 3's 12.09 dB to roughly +30 dB, against the 808 CY5025's 8.16 dB** — about 22 dB
> away from the machine where candidate 3 is 3.9 dB away.
>
> **If instead H−L lands near 8.16 dB, the equal-VCA-drive assumption is right, the balance is resolvable from the
> two figures alone, and this step's refusal is wrong.** That is the outcome this ablation exists to allow.

The external reference is the Fischer CY5025 recording, and H−L is `cymbal_bands.measure()`'s qualified band split
(eight known-answer tests, its own refusals). It was **not** used to derive any number in §1 — the balance comes
from Figures 4, 9 and 10 — so it is a genuine independent check rather than a fit being scored against itself.

## 4. Result — the prediction holds, and the assumption is refuted

`balance-ablation.json`, rendered from a **clean tree** (`sources_dirty: false`). The prediction in §3 was committed
first and the ablation rendered after it, and the render has been reproduced identically from a later clean commit —
every field of `balance-ablation.json` except the recorded commit hash is byte-identical between the two runs.

The solver's anchor came out at **−33.82 dB**: the low band drops 33.8 dB and the decay band 24.0 dB, while the
short band rises 4.4 dB against candidate 3, because the short band's envelope peak register is the ceiling that
binds. The ratios are the claim; that common scale is not.

| | 808 CY5025 | shipped | candidate 3 | **balance ablation** |
|---|---:|---:|---:|---:|
| H − L | **8.16 dB** | 10.82 | 12.09 | **24.38** |
| H EDT10 | **147.8 ms** | 151.3 | 147.2 | **69.9** |
| H late T20 | **432 ms** | *refused* | 363 | 328 |
| Ln EDT10 | **591 ms** | 598 | 578 | 620 |

1/3-octave residual against the 808 (dB; positive = we have more), summarised the same three ways
`../candidate3/README.md` §4 uses:

| window | | shipped | candidate 3 | **balance ablation** |
|---|---|---:|---:|---:|
| 0–50 ms | tilt | −22.5 | **+6.6** | +8.4 |
| | worst | 15.4 | **5.7** | **17.1** |
| | n>6 dB | 6/14 | **0/14** | **8/14** |
| 50–300 ms | tilt | −17.3 | +14.3 | +19.6 |
| | worst | **9.4** | 11.6 | 17.0 |
| | n>6 dB | **2/14** | 7/14 | 9/14 |
| 300–1000 ms | tilt | −9.9 | +16.2 | +21.6 |
| | worst | **8.8** | 10.5 | 14.8 |
| | n>6 dB | **1/14** | 6/14 | 10/14 |

**Preservation still passes.** All 15 non-CY sounds render bit-identically to the shipped kit on the 19-mode
layout, hats included (`balance-ablation.json` → `preservation`, all `true`). The ablation moves only the three CY
band levels, and the record proves it.

**The prediction was right in sign and order, and 5.7 dB steep.** It said H−L would go from 12.09 to *roughly*
+30 dB; it went to **24.38**. That is the same direction and about the same size of over-prediction as candidate 3's
per-band arithmetic (which was 6 dB steep), and for the same reason: three re-levelled bands summing into one
measure do not move by the per-band figure.

**Wrong-then-right, recorded because that is the rate a reader calibrates on:** this step's predicted-then-measured
pair is 1 of 1 — the prediction's *direction* was confirmed and its *magnitude* was 5.7 dB out, caught by the render
rather than by inspection. No measurement in this step was wrong before it was right; the ablation was rendered
once.

## 5. Verdict

**The balance is REFUSED, and the refusal is now measured rather than argued. Nothing reaches RTL. R1 is unchanged.
`model/cymbal_candidate.py` keeps the shipped-kit level rule.**

- **#396's item 1 (apply the balance) cannot be done honestly, and the reason is not the one the issue names.**
  Applying every factor that IS resolved, with the only unmeasured factor held at unity, puts the band split
  **16.2 dB from the 808** where the rule it replaced is 3.9 dB from it, and turns candidate 3's clean strike
  window (0 of 14 thirds outside ±6 dB, the chain's one clear win over the shipped kit) into 8 of 14. Every
  summary measure gets worse in every window.
- **#396's item 2 (derive it from the schematic) is the only route, and it is REFUSED for a missing input**, not
  declined. `docs/scorecard/cymbal-369/sn-p13-vr4.json` does not exist in this repository and no other artifact
  here carries VR4's R/C values. `tools/cymbal_band_balance.py` names that path, refuses on it, and lifts the
  refusal the moment it appears.
- **The schematic needs to supply two things, not one.** VR4's network resolves the tone term. It does **not**
  resolve the VCA drives, and this step's measurement says those are the larger term: the gap is 38.2 dB in the
  short band against a widest tone bound of 21.3 dB, and the ablation's 16.2 dB H−L error is what remains after
  every resolved factor has been applied. So a digitisation of VR4 alone would not unblock item 1.
- **An indication, not a measurement, for whoever digitises the schematic:** with H dominated by the short band
  after the ablation, an H−L error of +16.2 dB is consistent with the short band's VCA drive sitting **roughly
  16–22 dB below** what equal drive assumes, relative to the low band. It is stated as an indication because H−L
  is a two-band energy ratio and the ablation also moved the *mix within* H (H EDT10 collapsed from 147 to 70 ms
  as the short band took over from the decay band), so the number confounds a level with a mix. It is a sanity
  check to run against a real schematic value, not a substitute for one.
- **#396's item 3 (the TONE knob law) is deferred, and its blocker did not exist as an issue.** See §6.

## 6. The blocker for the TONE knob law, and why it needed filing

Every claim about tracking TONE across the 25 settings is blocked on the same thing: `kit_at`, the knob-law render,
is not the instrument (`../README.md` §4 — it gives H−L of −2.4 dB at CY5025 against the shipped kit's +10.8, and
its DECAY saturates so 7.5 and 10.0 render identically). Four places in this repository cite that blocker as
**"#371"**:

```
tools/make_cymbal_pack.py:79      tools/cymbal_candidate_eval.py:18
../candidate3/README.md:129,189,204
```

**#371 is a merged pull request** — step 1 of this very chain — **not an open issue.** So the blocker that gates
acceptance item 3 of #396, the listening pack's other settings, and `gate-379`'s §2 selection argument was tracked
by nothing. Filed now as a real issue, and the citations above are left for that issue to correct rather than
rewritten here, so the filing is visible in one place.

Figure 9 itself is the second half of the knob-law problem and is not repairable: it marks only k = 1.0 and gives
no k value for the other four members of each family, so **no TONE position except fully-open is readable from it**
(`tools/werner_fig9.py --check`, which asserts the marker identification and reports that in two of the three
sub-plots the marked curve is not the topmost one).

## Files

- `tools/cymbal_band_balance.py` — the decomposition, the bounds, the preconditions, the refusal, and the ablation's
  level solver. `--report` / `--check` / `--json`.
- `tools/test_cymbal_band_balance.py` — 30 tests: closed-form normalisation known answers, cross-checks against
  Figure 9's *digitised points* rather than its fit, the refusal asserted in both directions, the level solver's
  ratios and its register refusal, and the 5-control properties × defects matrix with one control blind by
  construction.
- `balance.json` — the decomposition, the bounds, the preconditions and the gate's own output.
- `balance-ablation.json` — the render: levels, preservation, band measures and 1/3 octaves, with commit and dirty
  flag.
- Reproduce: `python3 tools/cymbal_band_balance.py --check --json docs/scorecard/cymbal-369/balance/balance.json`
  (≈2 s), then `python3 tools/cymbal_candidate_eval.py --variant balance --out
  docs/scorecard/cymbal-369/balance/balance-ablation.json` (≈4.5 min).
