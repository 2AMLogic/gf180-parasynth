# #369 step 10: the TONE knob on a rendered candidate — the law is not the blocker, the balance is, and now there is a number for it

Step 9 (`../tone-knob/README.md`) took the TONE knob from VR4's wiper — `alpha = TONE/100` off the pot's own
"20K(B)" linear-taper marking, nothing fitted to the recordings — and ended by naming exactly one next question:

> **Does a candidate driven by this law track the 808 as TONE moves?** That is a kit change and needs a render, the
> frozen development/confirmation split, and the hats' preservation set — a separate increment of #369.

This is that increment. It contains one repair, one negative, and one blocker localised to a measured number.

**Nothing is promoted. Nothing reaches RTL. R1 is unchanged.**

## 0. Summary, in the order a reader should not skip

| | result |
|---|---|
| **A repair, found on the way in** | Revision 3's **low-band** tone realisation is **4.06–4.72 dB** off the nodal solution that replaced Figure 9, at every TONE position, against the **3.0 dB** bound revision 3 itself declared. Repaired with one discrete section — the network's own 4219 Hz pole — at **0.56–0.88 dB**. |
| **The question, answered negatively** | The rendered anchored H − L moves **+1.13 dB** across the whole knob where the machine moves **+7.3 dB**. Monotone, right sign, inside the pre-render bracket — and pinned to the bracket's decay-dominated **lower edge** at every position. |
| **The sharper half, in a decay** | H's own EDT10 moves **2.9 %** across the knob where the machine's moves a factor of **1.4–2.4**. No inter-band balance can manufacture a decay, so this is the statement that does not depend on #396's open half. |
| **The mechanism, measured not guessed** | Inside H the short band sits **10.59 dB below** the decay band, and the **low band leaks into H 6.02 dB above the short band**. TONE's 51 dB of authority is on a band holding 6 % of H. |
| **The one-number requirement, REFUSED** | "Raise the short band by X dB" is **not computable** from separate band renders: linear superposition misses the render's own curve by **1.26 dB** against a 0.5 dB bound, and overpredicts the knob's swing by **2.3×** at TONE 100. The swing VCAs clip, so the bands are not independent. |
| **Preservation** | **Passes completely.** 0 registers outside the cymbal's own change at any TONE position; all 16 other sounds bit-identical at the anchor; **OH and CH bit-identical at every one of the five TONE positions**. |

Everything is model evidence. `tools/cymbal_tone_nodal.py` (arithmetic, 2.4 s) and `tools/cymbal_tone_render.py`
(the render, ≈12 min) with their tests; `tone-render.json` is the record.

## 1. The repair: revision 3's low band was validated against a window that cannot see it

Step 5 put the tone stage into the bank and measured its realisation against **W14b Figure 9's per-band 2-pole
window fit**, inside a 3.0 dB bound stated in advance: 0.45 / 1.41 / 1.35 dB. Steps #390/#417 then **replaced**
that extrapolation with a nodal solution of the actual network off SN p.13 (`tools/tone_stage_schematic.py`), which
is what `docs/tr808-reference.md` §10 now records. **Nobody re-measured the realisation against the target that
replaced its own.** This step did, and the same construction, the same bound, the same active ranges:

| band | active range | rev 3 vs Figure 9 | rev 3 vs **nodal** | bound |
|---|---|---:|---:|---|
| low | 2.0–8.0 kHz | 0.45 dB | **4.06–4.72 dB** | 3.0 — **fails at all five TONE positions** |
| decay | 5.0–16.0 kHz | 1.41 dB | 1.55–1.77 dB | 3.0 — passes |
| short | 6.3–16.0 kHz | 1.35 dB | 1.31–1.34 dB | 3.0 — passes |

**The nodal route is not the suspect, and that is asserted rather than argued.** The short band is the one path
Figure 9 draws across the whole audio band (Ht3, 20 Hz–20 kHz); over its active range the two routes agree to
**0.026 dB** (`nodal-grounded`, bound 0.5). Over the low band's active range they disagree by **4.61 dB**
(`fig9-window-blind`, which must exceed the same 0.5 dB or the finding is not a finding); peak-to-peak over that
same range the two routes span **5.59 dB** (`fig9-window-blind.span_db`).
<!-- claim: test=tools/test_cymbal_tone_writeup_figures.py::test_every_quoted_figure_is_the_tool_s_own_output issue=429 why="all three figures on this line were transcribed, not computed: 0.10/5.62 against a tool that returns 0.026/4.61" -->
Figure 9 plots Ht1 only
over **121–564 Hz**, on a 4 dB tall axis; its fitted low-pass pole is **589.5 Hz** and the network's dominant
in-band pole is at **4219 Hz**. A window a decade below the band cannot see it.

**Why the decay band survives and the low band does not** — and this is the useful part, because it is revision 3's
own argument working in one place and failing in the other. Revision 3 dropped both the tone low-pass pole and the
LEVEL differentiator's zero, on the grounds that over the band both are in their asymptotic ∓6 dB/octave regions and
cancel. The decay band's active range is 5–16 kHz, **entirely above** 4219 Hz, so the argument holds there exactly.
The low band's is 2–8 kHz, which **straddles** the pole, so it does not hold at all: the nodal target falls 5.9 dB
from 2 to 8 kHz where Figure 9's read it as flat to 0.46 dB.

### The realisation, chosen by enumeration over the circuit and not by fitting

| band | poles the bank carries | extra DC zero | new sections | shape error, per TONE position |
|---|---|---|---:|---|
| low | **4219.0 Hz** (the network's own top pole) | no | **1** | 0.73 / 0.75 / 0.70 / 0.56 / 0.88 |
| decay | none — the two stages really do cancel | no | 0 | 1.77 / 1.77 / 1.76 / 1.71 / 1.55 |
| short | **4219.0 Hz**, in M_CYH3B's free slot | no (keeps the LEVEL zero) | 0 | 1.75 / 1.77 / 1.78 / 1.78 / 1.79 |

The candidate set is **42 members per band**: every subset of the network's own five pole frequencies that fits the
band's free slots, times the two DC-zero options. Three things make that an enumeration rather than an optimisation,
and each is a named property with a bound:

- **`poles-from-network`** — every pole is one of `tone_stage_schematic.poles_hz`'s own, to **0.0 Hz**. Revision 3's
  short-band pole, 1511.2 Hz, is **not**: the network's nearest is 1625.4 Hz. It is an artifact of the same 2-pole
  window fit.
- **`poles-in-band`** — a pole more than one octave outside the band's active range is in its asymptotic region
  there and is not distinguishable from any other such pole. Over the short band's 6.3–16 kHz the network's 130.0,
  488.6, 713.5 and 1625.4 Hz poles read **1.269, 1.277, 1.286 and 1.357 dB** — a 0.09 dB spread. Choosing among
  them by that spread would be fitting dressed as enumeration, which is the trap #102 records for Q. The rule is
  stated before the choice, and it always admits the empty realisation so it can never make a band unrealisable on
  its own.
- **Cost, then faithfulness, then error.** Ordering is: inside the bound, then cost (new sections plus the `HP3`
  decode revision 3 removed), then worst-case error, then fewest poles. **Faithfulness breaks ties at equal cost,
  not the other way round** — which is why the short band takes the network's pole into its free slot.

**Following the rule costs accuracy on the short band and is taken anyway.** 1511.2 Hz reads **1.34 dB** and
4219.0 Hz reads **1.79 dB** — the structurally correct value is 0.45 dB *worse* by the same metric. Both are inside
the bound. A value that reads better and is not in the circuit is not the one to keep.

**`one-register-set`: 0.171 dB, against a 0.25 dB bound.** The network's top pole moves **4132.2 → 4712.0 Hz** across
the five TONE codes (14 %; the ideal full rotation α = 0 → 1, which no code selects, gives 4132.1 → 4715.1 Hz —
`top_pole_hz.codes_hz` and `.rotation_hz`). Letting each pole track alpha buys **0.171 dB** at most: the low band
reads 0.878 dB with its pole fixed against 0.707 dB tracking, and the other two bands gain nothing. That is 68 % of
the bound and it is the whole of what fixing the poles costs, so **TONE still needs no coefficient rewrite** — one
register set covers all five positions, and the RTL deadlines do not have to carry a per-position write.
<!-- claim: test=tools/test_cymbal_tone_writeup_figures.py::test_every_quoted_figure_is_the_tool_s_own_output issue=429 why="this paragraph read 0.00 dB, and it is the paragraph that justifies not realising the knob's shape change" -->

### Budget, counted exactly — and the margin is now zero

**19 → 20 modes, 24 → 25 paths, `N_NUMS` 11.** No mode carries numerator code 3, so `modal_dp.v` still needs no
`HP3` decode (revision 3's saving is preserved).

**Mode 19 is the last mode the register map can address, and the reason is not area.** Contract 15.1 puts `A_RESET`
at 0xFF and `DrumsFx.write` decodes it **before** the mode range, so mode 19's `num` register **is** 0xFF and can
never be written. That is harmless here and only here, because 19 ≥ `N_NUMS` = 11 and `ModalFx.step` reads `num`
only below `N_NUMS` — which is also *why* the low band's new section must have numerator RAW rather than a DC zero.
**A 21st mode has no address at all.** The operator's accepted +31 % drum-area allowance (padding the bank to 32)
covers the area; it does not cover the map. `test_a_second_new_section_would_not_be_addressable` asserts that the
margin is exactly zero, and `test_revision_4_never_writes_the_reset_address` asserts no TONE position writes 0xFF.

## 2. The prediction, written into the model before the render

`model/cymbal_candidate.py`'s revision-4 docstring was committed before `cymbal_tone_render.py` was run. Because the
inter-band balance is #396's open half, the prediction is a **bracket** rather than a curve: L follows the low band,
and H lies between the decay band's shift and the short band's, so the anchored H − L must fall inside

| TONE | 0 | 25 | 50 | 75 | 100 |
|---|---|---|---|---|---|
| bracket | [−49.4, −0.03] | [−4.4, −0.06] | 0.00 | [+0.2, +3.2] | [+0.8, +9.9] |
| **the 808's own** | −2.3 | −1.4 | 0.00 | +1.5 | +5.1 |

The bracket contains the machine's own curve at all 25 settings (`test_the_bracket_brackets_the_808`) — if it had
not, the circuit's per-band levels would already be refuted and no render would have been worth starting. It is
2.9–4.3 dB wide at TONE 25 and 75, against the machine's 7.3 dB span, so it is not vacuous there
(`test_the_bracket_is_not_vacuous_in_the_middle`).

And the prediction says **where a miss would point**: a miss at TONE 0 or 100, where the bracket is 49 and 9 dB
wide, is a *balance* result; a miss in the middle is a *TONE-law* result.

## 3. The render

Anchored at TONE 50 (the D14A anchor's own code), 20-mode bank, level rule unchanged from step 5 — each band matched
to the **shipped kit's** same band in one 1/3 octave. Nothing here reads a recording.

**Anchored H − L, dB relative to TONE 50:**

| | TONE 0 | 25 | 50 | 75 | 100 | span |
|---|---:|---:|---:|---:|---:|---:|
| **ours (candidate 4)** | **−0.14** | **−0.10** | 0.00 | **+0.22** | **+0.99** | **1.13** |
| bracket's lower edge | −0.03 | −0.06 | 0.00 | +0.18 | +0.80 | |
| 808, DECAY 0 | −2.31 | −1.44 | 0.00 | +1.53 | +5.03 | 7.34 |
| 808, DECAY 25 | −2.72 | −1.41 | 0.00 | +1.70 | +5.37 | 8.09 |
| 808, DECAY 50 | −2.28 | −1.57 | 0.00 | +1.38 | +5.46 | 7.74 |
| 808, DECAY 75 | −2.29 | −1.29 | 0.00 | +1.60 | +4.86 | 7.15 |
| 808, DECAY 100 | −2.27 | −1.35 | 0.00 | +1.63 | +5.01 | 7.28 |

Worst deviation **4.47 dB** against a 3.0 dB bound, at CY1050 — a **development** setting, so this is not a
confirmation failure being reported as a development pass. It misses on the development set and on the confirmation
set alike, and every one of the 25 settings is in §6 below.

**The shape of the miss is the diagnosis.** Our curve does not merely fall short; it sits **0.02–0.19 dB above the
bracket's lower edge at every position**, and that edge is by construction *"H is entirely the decay band"*. The
knob's 51 dB of authority over the short band buys 0.2 dB of H − L because the short band is not in H.

**The DECAY confound is bounded, not assumed.** The render sits at the shipped kit's one DECAY; the 808's anchored
H − L agrees across all five of its DECAY columns to **0.601 dB** (worst case DECAY 50 against DECAY 75 at TONE 100;
the columns' own spans run 7.14–8.09 dB), which is a seventh of the deviation being reported. So this comparison
does not need the DECAY law that step 9 explicitly did not supply.
<!-- claim: test=tools/test_cymbal_tone_render.py::test_the_808s_decay_columns_agree_to_what_the_writeup_says issue=429 why="both numbers were read off the rounded table above rather than computed: 0.5 for 0.601, 7.15 for 7.14" -->

**H's own EDT10, ratio to TONE 50** — and this is the half no balance can fake, because it is a decay:

| | TONE 0 | 25 | 50 | 75 | 100 |
|---|---:|---:|---:|---:|---:|
| **ours** | **1.009** | **1.002** | 1.000 | **0.997** | **0.971** |
| 808, DECAY 0 | 1.086 | 1.117 | 1.000 | 0.947 | 0.760 |
| 808, DECAY 25 | 1.237 | 1.125 | 1.000 | 0.939 | 0.749 |
| 808, DECAY 50 | 1.270 | 1.180 | 1.000 | 0.913 | 0.662 |
| 808, DECAY 75 | 1.381 | 1.185 | 1.000 | 0.860 | 0.640 |
| 808, DECAY 100 | 1.361 | 1.209 | 1.000 | 0.807 | 0.578 |

Ours moves **2.9 %**; the machine moves a factor of **1.4–2.4**, and it does so in the right direction in all five
columns. The property `h-edt-falls-with-tone` *passes* — ours does fall, and does rise at TONE 0 — so the **sign**
is right and only the **size** is wrong. That distinction matters: the mechanism is present and starved, not absent.

**Ln's EDT10 is TONE-invariant to 0.03 %** against the machine's ±3 %, which is the property `ln-edt-tone-invariant`
and is the one thing here that agrees closely. It is also the weakest evidence in the table, because in this model
the low band's envelope cannot move with TONE — it is a check that the leakage from the vanishing short band does
not disturb it, not a check of a mechanism.

## 4. The mechanism, rendered rather than inferred

`cc.band_only` switches two of the three VCA paths off, so each band's own energy inside the frozen analysis bands
is a **measurement of the same kit with two thirds of it muted** — not an inference from the sum:

| band | share of L | share of H |
|---|---:|---:|
| low | −0.00 dB | **−6.14 dB** |
| decay | −32.09 dB | −1.57 dB |
| short | −53.59 dB | **−12.16 dB** |

- **The short band sits 10.59 dB below the decay band inside H.** It holds about 6 % of H's energy. TONE's whole
  51 dB of authority acts on that 6 %.
- **The low band leaks into H 6.02 dB *above* the short band's contribution.** So H − L partly tracks the low band's
  own TONE gain on both sides of the ratio, where it cancels. This is not the analysis filter's skirt alone: the
  low band's own chain is 40 dB down at 7 kHz, and the swing VCA is a hard nonlinearity, so the likely carrier is
  the low band's clipping harmonics landing in 6–14 kHz. **That is not isolated here** and is the first item in §7.
- L is the low band, as designed (the other two are 32 and 54 dB down).

### The one-number requirement for #396 — REFUSED, and the refusal is the finding

The obvious deliverable from the table above is *"the short band needs +X dB relative to the other two"*, solved by
mixing the three rendered bands. `tools/cymbal_tone_render.py` implements exactly that
(`required_short_band_offset`, with a known-answer test that recovers a planted 12.0 dB offset to the 0.25 dB grid
step) **and gates it on a precondition it does not meet**:

| | TONE 0 | 25 | 50 | 75 | 100 |
|---|---:|---:|---:|---:|---:|
| linear mix of the three rendered bands | −0.29 | −0.21 | 0.00 | +0.40 | **+2.24** |
| the render itself | −0.14 | −0.10 | 0.00 | +0.22 | **+0.99** |
| error | −0.15 | −0.11 | 0.00 | +0.18 | **+1.26** |

**1.26 dB against a 0.5 dB bound stated in advance, on a quantity whose entire measured range is 1.13 dB.** So the
requirement is not reported. The cause is in §10 already: the three **swing VCAs** clip, so the bands do not
superpose and a band's contribution depends on what is beside it. Worse for the naive fix, the linear model
**overpredicts** the knob's swing by 2.3× at TONE 100 — clipping is actively suppressing TONE's authority, so
raising the short band's drive would change its own clipping as well as its level.

This is the same wall step 8 (`../vca-clip/README.md`) hit from the other side, and it is why "just apply the
resolved balance" has now failed to be a computable instruction **three** times: #396's refusal (the VCA drives are
unmeasured), step 8's joint constraint (`n_balances_in_808_box` is 0), and this.

## 5. Preservation — passes, and two ways

Acceptance item 4, checked with two complementary instruments because neither alone is enough:

- **Exact, all five TONE positions.** The set of registers the candidate writes that differ from the shipped image
  on the same layout contains **0** addresses outside the cymbal's own six modes, five paths and three envelopes.
  `test_cy_owned_addresses_excludes_every_shared_register` asserts that M_HATBP, M_CHHP, M_OHHP, E_CH and E_OH are
  **not** claimed as the cymbal's, so this check cannot pass by defining the hats' registers as ours.
- **Bit-exact renders.** All **16** non-CY sounds identical to the shipped kit at the anchor, and **OH and CH
  identical at every one of the five TONE positions**. The hats get the per-position treatment because they share
  the 7.1 kHz band-pass and because the shipped kit routes the cymbal's short band through the **closed hat's own**
  high-pass M_CHHP, which the candidate re-routes.

D15A, D16A and OH00–OH75 are therefore unaffected by construction and by measurement. The rest of the kit is
untouched and R1 is unchanged.

## 6. Every one of the 25 settings

Acceptance item 3 says report every setting and say which are development. Ours is one curve (the render has no
DECAY law), so each row repeats it against that setting's own 808 value.

| setting | split | 808 anchored H−L | ours | Δ | 808 H EDT ratio | ours |
|---|---|---:|---:|---:|---:|---:|
| CY0000 | confirmation | −2.31 | −0.14 | +2.16 | 1.086 | 1.009 |
| CY2500 | confirmation | −1.44 | −0.10 | +1.34 | 1.117 | 1.002 |
| CY5000 | development | 0.00 | 0.00 | +0.00 | 1.000 | 1.000 |
| CY7500 | confirmation | +1.53 | +0.22 | −1.30 | 0.947 | 0.997 |
| CY1000 | confirmation | +5.03 | +0.99 | −4.04 | 0.760 | 0.971 |
| CY0025 | confirmation | −2.72 | −0.14 | **+2.58** | 1.237 | 1.009 |
| CY2525 | confirmation | −1.41 | −0.10 | +1.31 | 1.125 | 1.002 |
| CY5025 | development | 0.00 | 0.00 | +0.00 | 1.000 | 1.000 |
| CY7525 | confirmation | +1.70 | +0.22 | −1.48 | 0.939 | 0.997 |
| CY1025 | confirmation | +5.37 | +0.99 | **−4.38** | 0.749 | 0.971 |
| CY0050 | development | −2.28 | −0.14 | +2.13 | 1.270 | 1.009 |
| CY2550 | development | −1.57 | −0.10 | +1.46 | 1.180 | 1.002 |
| CY5050 | development | 0.00 | 0.00 | +0.00 | 1.000 | 1.000 |
| CY7550 | development | +1.38 | +0.22 | −1.15 | 0.913 | 0.997 |
| CY1050 | development | +5.46 | +0.99 | **−4.47** | 0.662 | 0.971 |
| CY0075 | confirmation | −2.29 | −0.14 | +2.15 | 1.381 | 1.009 |
| CY2575 | confirmation | −1.29 | −0.10 | +1.19 | 1.185 | 1.002 |
| CY5075 | development | 0.00 | 0.00 | +0.00 | 1.000 | 1.000 |
| CY7575 | confirmation | +1.60 | +0.22 | −1.38 | 0.860 | 0.997 |
| CY1075 | confirmation | +4.86 | +0.99 | −3.87 | 0.640 | 0.971 |
| CY0010 | confirmation | −2.27 | −0.14 | +2.13 | 1.361 | 1.009 |
| CY2510 | confirmation | −1.35 | −0.10 | +1.25 | 1.209 | 1.002 |
| CY5010 | development | 0.00 | 0.00 | +0.00 | 1.000 | 1.000 |
| CY7510 | confirmation | +1.63 | +0.22 | −1.41 | 0.807 | 0.997 |
| CY1010 | confirmation | +5.01 | +0.99 | −4.03 | 0.578 | 0.971 |

The worst three misses are all at TONE 100 and one of them (CY1050) is a development setting. **The development set
and the confirmation set agree**, which is what "the deviation is structural, not a selection artifact" means here.

For reference, the absolute H − L: ours reads 11.41 / 11.45 / **11.56** / 11.78 / 12.54 dB against the 808 CY5025's
**8.16**. At the anchor revision 4 is 11.56 where revision 3 was 12.09 — 0.53 dB closer, which is a by-product of
the low-band repair and not a result this step selected on.

## 7. What this step forbids, and the next question

**Forbidden to the next step: reading the balance off this render by a linear mix.** §4 measured that the mix is
1.26 dB wrong on a 1.13 dB quantity. Any "+X dB on the short band" derived that way is not a circuit fact and not
even a valid model fact.

**Also forbidden: treating the low band's 24 % presence inside H as settled.** It is measured, it is larger than the
short band's contribution, and its *cause* is not isolated — analysis-filter skirt, the low band's own chain, and
the swing VCA's clipping harmonics are all candidates, and step 8 already showed that the analysis filter reads
+3.67 and +9.49 dB high on the two bands peaking at 7.1 kHz. It needs a band-only spectrum, not an assertion.

**The next question, and it is one question:**

> **What sets the three swing VCAs' drive levels?** Every route to the inter-band balance now terminates on the same
> unmeasured quantity — #396 refused on it, step 8's clipper sweep could not reach the 808's joint box without it,
> and §4 above cannot even state a requirement for it because the clipping it induces is what breaks superposition.
> The artifact that would resolve it is SN p.13's own resistor network around Q16/Q17/Q18, read the way
> `tools/tone_stage_schematic.py` read VR4's — by nodal analysis off a hash-pinned scan, with `--verify-source` and
> a refusal, not by fitting a drive to a recording.

Still open and unchanged by this step: #396 (the inter-band balance), #413 (the knob-law render is not the
instrument — this step does **not** close it, because it renders the *candidate* and not `kit_at`), #400's tail
question, the DECAY knob's own law, and the listening pack at other settings.

## 8. Wrong-then-right rate: 4, three caught by controls and one by the gate

Published because that rate is how a reader calibrates any single figure above.

| # | what was wrong | what caught it |
|---|---|---|
| 1 | `test_tone_mainly_attenuates_the_third_band` asserted the short band's span exceeds **10×** the others'. The measured ratio is **6.2** (51.1 dB against 8.3 and 7.4), so the first gate was unsatisfiable — CLAUDE.md's "worse than no gate" | running the gate before committing it. The test now asserts 5× **and** that the other two spans are non-zero, because §7 of the reference says the controls are non-orthogonal and a test that let them be zero would be wrong in the other direction |
| 2 | **the sub-chain accounting double-counted Hh3's own pole.** The first version of `cymbal_tone_nodal` put the 5195 Hz pole into *both* `band_chain_db` and the realisation under test, and read revision 3's short band at **1.55 dB** where step 5 recorded 1.35 | `test_rev3_against_figure_9_reproduces_step_5s_recorded_figure`, written as a binding to step 5's committed figures rather than as a fresh measurement. Without that binding the number would have looked perfectly plausible |
| 3 | **the enumeration picked a 130 Hz pole for the short band**, because the cost ordering broke ties by error and four sub-2 kHz network poles read within 0.09 dB of each other over 6.3–16 kHz. That is fitting by enumeration, and it beat the structurally correct 4219 Hz pole by 0.5 dB | printing the full ranked table before trusting the winner. `POLE_MARGIN_OCT` and the `poles-in-band` property now exclude it, and `test_asymptotic_poles_are_inadmissible_for_the_band_they_cannot_shape` pins the 0.09 dB spread that is the reason |
| 4 | `model/cymbal_candidate.TONE_GAIN_DB` was transcribed from a 2-decimal print and disagreed with the tool by **0.003 dB** | `assert_realisation_is_the_validated_one`, which REFUSED the render at its first invocation rather than rendering a gain table the model and the tool disagreed about. This is the one caught by the gate rather than by a control, and it is the cheap kind — but the expensive kind has the same shape |

Number 2 is the one worth dwelling on: it is a **correct instrument in a wrong state**, this project's recurring
failure mode, and the only thing that found it was a test asserting agreement with an *earlier step's committed
number* rather than re-deriving it. Number 3 is the one that would have changed what a reader believed — it would
have shipped a 130 Hz pole with a better error figure and no circuit behind it.

**And one thing that was right first time and should not be counted as evidence of care:** the preservation result.
It passes because `candidate_kit` touches only the cymbal's own registers, which was true of revision 3 as well.

## Files

- `tone-render.json` — the whole record: precondition, levels, per-TONE band measures, all 25 settings' comparison,
  band shares, the superposition check, preservation, commit and dirty flag. The dirty flag is `false` and the
  commit is an ancestor of `HEAD` whose tree carries revision 4, so every figure below is reproducible from this
  history — which was **not** true of the record this one replaces (#429).
<!-- claim: test=tools/test_cymbal_tone_render.py::test_the_render_record_is_reproducible_from_a_commit_in_this_history issue=429 -->
<!-- claim: test=tools/test_cymbal_tone_render.py::test_no_committed_scorecard_record_was_produced_from_a_dirty_tree issue=429 why="generalised to every scorecard record carrying the two provenance fields" -->
- `tools/cymbal_tone_nodal.py` / `tools/test_cymbal_tone_nodal.py` — the realisation, 10 properties, 7 injected
  defects, 4 blindness assertions, 34 tests. `--report` / `--check` / `--json`.
- `tools/cymbal_tone_render.py` / `tools/test_cymbal_tone_render.py` — the render and its verdict, with the verdict's
  own controls (a planted 4 dB miss, a flat curve, a reversed curve).
- `model/cymbal_candidate.py` — revision 4, with §2's prediction in its docstring, committed before the render.
- Reproduce: `python3 tools/cymbal_tone_nodal.py --check` (2.4 s), then
  `python3 tools/cymbal_tone_render.py --out <path>` (≈12 min).
