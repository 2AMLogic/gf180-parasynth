# #369 step 6: the inter-band balance — decomposed, bounded, and REFUSED, with the blocking factor measured

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

## 4. Result

*(filled in from the render; see §5 for the verdict.)*

## 5. Verdict

*(filled in after the render.)*

## Files

- `tools/cymbal_band_balance.py` — the decomposition, the bounds, the preconditions, the refusal, and the ablation's
  level solver. `--report` / `--check` / `--json`.
- `tools/test_cymbal_band_balance.py` — known answers and the controls that must fail.
- `balance.json` — the evidence record.
- Reproduce: `python3 tools/cymbal_band_balance.py --check --json docs/scorecard/cymbal-369/balance/balance.json`,
  then `python3 tools/cymbal_candidate_eval.py --variant balance --out <path>` (≈4.5 min).
