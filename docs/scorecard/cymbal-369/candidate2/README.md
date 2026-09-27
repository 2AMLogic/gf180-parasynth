# #369 step 3: Hh2, Hh3 and the level stage resolved from the circuit — and the candidate still overcorrects

Step 2 (`../candidate/README.md`) ended with a named next step: *"The two remaining unknowns are circuit
values, not tuning knobs — Hh2's corner and Q, and the LEVEL buffer's differentiator corner. Both come from the
schematic. They are to be read and derived exactly as Hh1 was, then the candidate rebuilt with those values fixed
before rendering."*

Both are now read, from a source the reference already cites but had not been mined: **W14b Figure 4 plots all five
of the cymbal's filter responses, and Figure 10 plots the level stage's.** Those figures are vector graphics, so the
curve coordinates sit in the PDF to full precision. `tools/werner_fig4.py` reads them out.

This document reports two things: the values (section 1, a result that stands on its own) and what the candidate
rebuilt on them measures (section 3, **a second negative**).

## 1. The values

| | was, in the kit / candidate 1 | now, from W14b | fit residual |
|---|---|---|---|
| **Hh2** | 10.5 kHz Q 2.5, *carried because the reference did not resolve it* | **2-pole high-pass, 8839 Hz, Q 1.00**, pass band +6.03 dB | 0.007 dB rms |
| **Hh3** | 2-pole 10.5 kHz Q 2.5 **+ 1-pole at the same corner** (#102's assumption) | **2-pole 10323 Hz Q 5.64 + 1-pole high-pass at 5195 Hz**, pass band +8.86 dB | 0.008 dB rms |
| **LEVEL** | "a +6 dB/oct rising slope", modelled as a pure `(1 − z⁻¹)` | **a single-pole differentiator, corner 18972 Hz** | 0.02 dB rms |

Three further numbers that were not available before, all from the same figure:

- **The band-passes' peak gains**: Hbp1 (3.45 kHz) **+22.95 dB**, Hbp2 (7.1 kHz) **+24.10 dB**.
- **The high-passes' pass-band gains**: Hh1 **0 dB** (unity, as W14b eq. 16 requires), Hh2 **+6.03 dB**, Hh3 **+8.86 dB**.
- So the **filter chain alone** puts the decay band **+7.2 dB** and the short band **+10.0 dB** above the low band.
  These are recorded as constants in `model/cymbal_candidate.py` and **not applied** — see section 4.

### Why these can be believed

The digitiser is gated on answers it did not produce. Three of the five curves are fixed by resistors and
capacitors on SN p.13, through the same textbook formulae the reference already uses for Hh1, and a fourth by W14b
§9's own prose:

| curve | known, independently | digitiser reads | error |
|---|---|---|---|
| Hbp1 | 3450 Hz, Q 6 (R56/R57/C13/C14, bridged-T) | 3437 Hz, Q 6.02 | 0.37 %, 0.3 % |
| Hbp2 | 7100 Hz, Q 6 (R58/R59/C15/C16, bridged-T) | 7095 Hz, Q 6.07 | 0.07 %, 1.2 % |
| Hh1 | 2500 Hz, Q 0.97, unity pass band (R124/R127/C48/C59) | 2506 Hz, Q 0.96, +0.00 dB | 0.23 %, 0.5 % |
| Hh3 | "resonance at the corner frequency (around 10500 Hz)" | peak at 10424 Hz | 0.7 % |

Hh1 is the strongest of these: a 2-pole high-pass model fits the plotted curve to **0.008 dB rms**, which is the
figure being redrawn essentially exactly, not merely consistently.

**Hh3 is genuinely third-order, and the third pole is located, not assumed.** A 2-pole model of the same curve
leaves 0.717 dB rms against the 3-pole model's 0.008 dB, so the structure is distinguishable from the data. The
converse control is in the tests: fitting the 3-pole model to a synthesised 2-pole section pushes the extra pole out
of band instead of inventing one.

**The evidence is committed** as `../werner-fig4.json`, so the gate runs without the paper (the PDF is not
redistributable and CI has no network). `tools/werner_fig4.py --from-pdf --json …` re-derives it and refuses to
write if decimation moves any known answer.

### What this closes in the reference

`docs/tr808-reference.md` §18's open item **"CY high-pass #2/#3 exact corners"** is closed, and its
inference is **corrected**: it read the machine's 9–13 kHz shoulder as evidence that "the ≈10.5 kHz stage behaves
like a band-pass, since … a high-pass at that corner cannot make that shape". It can. A high-pass with **Q 5.64**
at 10.3 kHz peaks 15 dB above its own asymptote and is still 11 dB down from that peak at 20 kHz — because only
0.95 of an octave of spectrum remains above the corner. The shoulder is a resonant high-pass seen through a
too-short window of spectrum, not a band-pass.

### Wrong-then-right, twice, both caught by controls

1. **The y axis was calibrated on the tick *labels*.** A label's `Td` is a glyph baseline, about 1.4 dB below the
   tick it names. The grid lines are the tick positions; the labels only supply the values.
2. **A 2-pole band-pass least-squares fit reads Hbp1 as Q 3.07** against the schematic's 6.0. The band-passes are
   bridged-T sections with a different numerator, so f0 and Q come from the peak and the −3 dB bandwidth. Had the
   known-answer gate not been external, "Q 3.07" would have been reported as a reading of the figure.

And a third, from the controls themselves: **two of the nine controls did not fail when first written.** The gate
was blind to a uniform 2 dB axis offset — exactly the error in (1) — and a 20 % error in the dB scale passed at the
original Q tolerance. Both holes are closed (Hh1's absolute unity pass band is now a gate item; Q_TOL 0.20 → 0.10,
against measured errors of 0.3 % and 1.2 %). A known-answer test nobody has seen fail is not a control.

## 2. What the candidate is now

`model/cymbal_candidate.py`, revision 2. Structure and bank unchanged from candidate 1 — 19 modes, 24 paths,
`N_NUMS` 11 — with only Hh2's and Hh3's coefficients moved to the values above. **The level rule is deliberately
unchanged**, so this step asks one question.

**Preservation: all 15 non-CY sounds render bit-identically to the shipped kit** on the 19-mode layout, hats
included (D15A, D16A, OH). `candidate2.json` → `preservation`.

## 3. The result at CY5025 — negative

1/3-octave energy against the 808 (dB; positive = we have more):

| window | 1.0 | 1.26 | 1.59 | 2.0 | 2.5 | 3.2 | 4.0 | 5.0 | 6.3 | 8.0 | 10 | 12.7 | 16 | 20 kHz |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| shipped, 0–50 ms | +15.4 | +11.2 | +10.4 | +9.1 | +9.4 | +0.8 | −2.3 | −2.9 | −2.0 | +2.9 | −2.3 | −2.2 | +0.2 | −7.0 |
| candidate 1, 0–50 ms | −14.4 | −12.2 | −6.8 | −3.2 | +4.4 | — | — | −7.9 | −8.8 | — | — | +3.3 | +8.1 | +6.1 |
| **candidate 2, 0–50 ms** | **−15.3** | **−13.8** | −8.0 | −4.5 | +2.8 | −0.9 | −1.4 | −7.9 | −5.9 | +0.2 | −3.5 | +2.2 | **+8.6** | **+8.7** |
| candidate 2, 50–300 ms | −20.4 | −20.4 | −17.6 | −14.0 | −7.2 | −6.5 | −0.5 | −8.2 | −7.2 | +0.4 | −0.5 | +4.6 | +8.8 | +9.6 |
| candidate 2, 300–1000 ms | −21.1 | −19.8 | −16.1 | −14.0 | −7.2 | −4.8 | +4.2 | +0.2 | −3.4 | +3.2 | +2.9 | +6.7 | +10.7 | +11.7 |

| | H−L | H EDT10 | Ln EDT10 |
|---|---:|---:|---:|
| 808 CY5025 | 8.16 dB | 148 ms | 591 ms |
| shipped | 10.82 dB | 151 ms | 598 ms |
| candidate 1 | 8.64 dB | 162 ms | 542 ms |
| candidate 2 | 10.60 dB | 156 ms | 549 ms |

**Verdict: not taken forward. Nothing reaches RTL. R1 is unchanged.**

**What this eliminates, which is the point of running it.** Candidate 1's post-mortem left three suspects for its
overcorrection: Hh2's carried corner, Hh3's assumed pole placement, and the level-stage differentiator. All three
are now circuit values rather than guesses, and **the overcorrection barely moved** — 1 kHz went from −14.4 to
−15.3 dB, and the top octave from +6.1 to +8.7 dB. So **the filter values are not the cause.** The suspect list for
the ~15 dB shortfall below 2.5 kHz no longer contains any of them.

The level stage in particular is now exonerated by measurement rather than by argument: across 2–20 kHz the real
circuit tilts **+16.6 dB**, and a discrete `(1 − z⁻¹)` at 48 kHz tilts **+17.4 dB**. The digital stand-in is 0.8 dB
steep over the whole cymbal band — it cannot account for a 15 dB error, and candidate 1's post-mortem was wrong to
name it.

> **Answered, in `../tone-stage/README.md` (step 4).** The hypothesis below is **confirmed in shape and in
> size**: every tone-stage path is a 2-pole band-pass peaking at 274–972 Hz, so each costs its band about
> −20 dB from 1 kHz to 20 kHz, against the LEVEL stage's +16.6 dB that this candidate applies alone. The
> residual below climbs +24.0 dB over the same span. What Figure 9 does *not* give is the inter-band
> balance — it plots Ht1 and Ht2 on 4 dB and 3 dB tall axes, so neither appears anywhere near 3.45 kHz.

## 4. The next question, and it is one question

**The tone stage is the only block of §10 that this model does not have at all**, and it is now the only remaining
candidate for the low-frequency shortfall.

W14b §10 describes it as "a highly-interconnected passive network of resistors and capacitors", giving three
*fifth-order* transfer functions Ht1, Ht2, Ht3 from each band to the output — "far too lengthy to print", and the
companion site that was to hold their coefficients **returns 404** (confirmed again on 2026-09-27; the reference's
§0 already records this). But **W14b Figure 9 plots all three families**, and it is in the same PDF, as the same
kind of vector XObject (`obj 39`), so the same instrument reaches it. It needs one extension — Figure 9 is three
sub-plots in one figure, and `calibrate()` assumes a single axes box. **Filed as #390.**

The physical argument for looking there: the tone stage is a *passive RC* network, so its paths are low-pass in
character, and a low-pass path from the low band to the output would partially cancel the LEVEL buffer's rising
slope. That is the right shape and roughly the right size for the missing 15 dB. It is a hypothesis, stated before
the measurement, and it is the next thing to measure.

A second, separate question, not to be merged with it: **the circuit's own inter-band gains** (+7.2 dB decay,
+10.0 dB short, from section 1) versus the current rule of matching each band to the shipped kit's 1/3 octave. The
constants are in `model/cymbal_candidate.py` as `HH2_PASS_DB` / `HH3_PASS_DB` / `BP_PEAK_DB` and are **not
applied** — moving the levels in the same step as the filter shapes would have made neither answerable, and it is
why candidate 2 changed only the filters.

Still blocking any claim about knob tracking: the knob-law render (`test_discrimination.kit_at`) is not the
instrument (`../README.md` §4) and needs its own labelled repair before selection across the 9 development settings
is meaningful.

## Files

- `tools/werner_fig4.py` — the digitiser. `--check` is the known-answer gate; `--fit` the filter values; `--level`
  the level stage; `--from-pdf --json` re-derives the evidence.
- `tools/test_werner_fig4.py` — 23 tests, 9 of them controls that must fail.
- `../werner-fig4.json` — the digitised curves and the level stage, so the gate runs offline.
- `candidate2.json` — levels, preservation, band measures and 1/3 octaves, with commit and dirty flag.
- **Listening pack**: `/tmp/gf180-listen/cymbal-candidate2/` on the laptop — 808, shipped, 808, candidate at
  CY5025, loudness-matched, built with `tools/make_cymbal_pack.py`.
