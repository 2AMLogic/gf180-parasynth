# #369 cymbal, the inter-band balance (#396) — decomposed, bounded, and REFUSED, with the blocking factor measured

> **Step numbering.** PR #410 ("step 6: the 1–2.5 kHz decay qualified") was open and unmerged while this was
> written; it and #418 ("step 6/7", `low-tail/`) have both **since merged ahead of it** (`f3797ad2` and `8fc1c6d`,
> 2026-09-28), so `../README.md` carries steps 6 and 6/7 and this file still deliberately carries no step number.
> The reason is no longer that #410 is open — it is that all three were measured **in parallel**: none uses
> another's result, so numbering this one 7 or 8 would assert a sequence that does not exist. They agree.
> #410/#411 name *"its level rule anchoring each band to the shipped kit's energy in one 1/3 octave"* as the prime
> suspect for the mid band, and §1 below measures that rule against the circuit for all three bands; #418 goes
> further in the same direction, finding that **no** inter-band balance reaches the 808's 1–2.5 kHz energy, which
> is a sharper form of this step's refusal rather than a contradiction of it. #410's own Judge-driven correction
> of four prose numbers is confined to `mid-band/README.md` and `tools/cymbal_mid.py`'s docstrings; **none of
> those four values is quoted anywhere in this directory or in `../README.md`**, so nothing here restates them and
> the merge reverts none of them. §4 records what each merge did — and did not do — to this step's record.

> **THE TONE TERM IS NOW RESOLVED, AND THIS FILE CARRIES THE RE-DERIVED NUMBERS (#420).** As approved, this step
> *bounded* the tone term from W14b Figure 9 (§1's `tone ±` column read 3.68 / 6.95 / 0.02 dB at each band's own
> calibration third, 7.4 / 13.9 / 0.04 dB wide). **#390/#417 resolved that network instead of bounding it** — SN
> p.13's R/C values around VR4 solved by nodal analysis, `tools/tone_stage_schematic.py`, reproducing Figure 9's
> own digitised k = 1.0 curves to 0.001–0.013 dB rms with one shared free parameter. #420 emitted that solution as
> `../sn-p13-vr4.json`, pointed the `schematic-vr4` precondition at it, and re-derived §1 from it. Everything
> below is the re-derived version; what the figure route said is kept beside it, because #396 *excludes* that
> route and an exclusion has to stay a measurement.
>
> **The verdict is not superseded, and the re-derivation is the strongest test it has had.** This step's binding
> factor is the **VCA drives**, and it predicted — before #390 was finished — that *VR4's network alone would not
> unblock item 1*. Resolving VR4 could have falsified that by shrinking the gap. It **grew** it: +9.85 → **+10.13
> dB** (decay) and +38.23 → **+39.79 dB** (short), against a tone bound that fell from 21.3 dB to **0.07 dB**. So
> the prediction held in the direction that could have broken it, and the refusal stands on one remaining absent
> input rather than two.

Step 5 (`../candidate3/README.md`, PR #402) applied the tone stage's measured **tilt** and left the **balance** — the
three bands' levels relative to one another — where it was. #396 then asked for the balance, preferring
**SN p.13's R/C values around VR4** over any further fitting of W14b Figure 9.

**This step does not resolve the balance. It measures why not, and the answer is not the one the issue expected.**

- #396 (and reference §18) put the obstacle at **9–18 dB of figure-reading uncertainty** on Ht1 and Ht2.
- That was the wrong **frequency**: evaluated where each band's level is actually set, Figure 9's own bound is
  **7.4 dB** on the low band at its 3175 Hz calibration third, not 18.0 dB, and **0.04 dB** on the short band
  because 10079 Hz is inside Ht3's plotted range.
- It is now the wrong **quantity** as well. #390/#417 solved the network instead of bounding it, so the tone term
  is a circuit value and its uncertainty is that solution's own residual: **0.008 / 0.008 / 0.067 dB**, three
  orders of magnitude inside the 3.0 dB tolerance a band split has to be known to. *That number is the
  **solution's** uncertainty, not the tone stage's — §1's tolerance paragraph carries the unit-to-unit term
  separately, and it is 45–619× larger. Do not quote one for the other (#425).*
- What is actually blocking the balance is a factor **neither figure carries**: the three swing VCAs' drive levels.
  Against the shipped-kit level rule the model currently uses, the filters-plus-tone balance alone demands
  **+10.1 dB on the decay band and +39.8 dB on the short band**. A 39.8 dB residual is not a 9–18 dB figure
  problem, and resolving the figure's half **grew** it rather than shrinking it.

`tools/cymbal_band_balance.py` is the instrument; `tools/test_cymbal_band_balance.py` is its known answers and the
controls that must fail. `--check` passes with 5/5 injected controls turning a named property red and one control
asserted blind-by-construction and verified blind.

## 1. The decomposition

Each band's level, **at the frequency its level is set** (`cymbal_candidate_eval.CENTRE`: the low band's third at
3175 Hz, both high bands' at 10079 Hz), with the LEVEL differentiator's corner at 18972 Hz (W14b Fig. 10). The
`tone` column is the **solved** VR4 network (`../sn-p13-vr4.json`), not Figure 9's extrapolated fit:

| band | centre | BP peak | BP shape | HP pass | tone | tone ± | LEVEL | total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| low | 3175 Hz | +22.95 | −3.00 | +0.00 | −41.04 | 0.0038 | −15.65 | **−35.79** |
| decay | 10079 Hz | +24.10 | −12.88 | +6.03 | −29.86 | 0.0042 | −6.57 | **−18.33** |
| short | 10079 Hz | +24.10 | −12.88 | +8.86 | −36.57 | 0.0336 | −6.57 | **−9.58** |

**What the `tone ±` column is made of, and what it is not.** Half-width = this family's *largest* disagreement with
Figure 9's own digitised k = 1.0 curve (0.0035 / 0.0040 / 0.0324 dB) plus the move over ±3σ on the one parameter
the solution fits (σ(α) = 3.3 × 10⁻⁵, worth 0.0003–0.0013 dB). It does **not** carry component tolerance: SN p.13's
printed nominal values are taken as exact, because nothing in this repository measures a real TR-808's VR4 network
and an invented tolerance would be a claim rather than a reading.

**#425 asked whether that exclusion could be closed. It is now measured, it stays out of `tone ±` on purpose, and
it is three orders of magnitude larger than the column it is not in.** The two are answers to different questions:
`tone ±` asks *"is the network SN p.13 prints solved?"*, for which the printed values are the **definition** of the
thing rather than an uncertainty in it; component tolerance asks *"how far does a built unit sit from that
print?"*. Summing them would make this table's verdict depend on which physical 808 is meant, so the second is
carried beside the first, named, and cited.

**The class, and where it is not.** The pinned service-notes scan prints no tolerance anywhere in its 16 pages —
p.1 says the full parts list is a *separate* document; p.8 carries the only "UNLESS OTHERWISE SPECIFIED" legend
and it covers semiconductors alone; p.13, the page the values were read off, prints values and designators only;
p.16's PARTS LIST enumerates **one** resistor and **three** capacitors, none of them in this network, and lists
VR4 as `EVH-LWAD25B24 20K (B) CY tone`, resistance and taper only. That search is committed
(`tone_stage_schematic.SN_PRINTS_NO_TOLERANCE`) and is why the class cannot come from there. The class that **is**
citable is **W14a §11: "the voice circuits featured ±20% capacitors and ±5% resistors"** — verified against the
DAFx-14 PDF itself rather than against this repository's transcription of it, and already the source of
`docs/tr808-reference.md` §1.7's ±10 % f0 and ±50 % Q. **VR4's own tolerance is not covered by it** (a pot is not
a fixed resistor) and is printed nowhere, so it is carried as an explicit `None` with its lever reported beside
the bound, not folded into the resistor class.

| what | low (Ht1 @ 3175) | decay (Ht2 @ 10079) | short (Ht3 @ 10079) |
|---|---:|---:|---:|
| `tone ±` (this network, solved) | 0.0038 | 0.0042 | 0.0336 dB |
| lever: 1 % on all 13, in quadrature | 0.134 | 0.126 | 0.138 dB |
| **W14a class, unit to unit, rss** | **1.697** | **1.763** | **1.765 dB** |
| same, every part adversarial at once | 3.289 | 2.605 | 2.399 dB |
| VR4, uncited (lever per 1 %) | 0.019 | 0.016 | 0.050 dB |

Four things follow, and the first is the reason this was worth its own issue:

- **The excluded term dwarfs the carried one — by 45× to 619×, depending on the band.** `tone ±` is *not* the tone
  stage's total uncertainty, and a reader who treats 0.0038 dB as "the tone term is exact" is wrong by more than
  two orders of magnitude. **It still changes no verdict**: §2's refusal is bound by the +39.79 dB VCA-drive gap,
  an order of magnitude above even the unit term, which is why this column collapsing did not and could not
  unblock the balance.
- **On the band *ratios* this table actually reads, the term is smaller than any per-band number**: **0.90 dB**
  (decay − low) and **1.07 dB** (short − low), rss half-widths. A ratio is not two independent draws — C90, the
  shunt at the node all three families share, is the largest lever on each and largely cancels in a difference
  (1.70 dB of the low band's own spread becomes 0.26 dB of decay − low). The propagation has to be done on the
  *difference*, not on the two bands separately: `relative_db` adds the two bands' widths, which for this term
  would give 6.9 dB where the correlated answer is 2.1 dB — a 3× over-statement. As widths, the way the 3.0 dB a
  band split must be known to is stated: **1.80 dB and 2.14 dB, inside it by 1.4–1.7×** — not the 45–400× that
  `tone ±` alone suggests.
- **A tolerance claim only has to be defensible about a few parts.** C90 is in all three dominant sets; four parts
  (C90, R112, R119, VR4) carry 99 % of Ht3's lever at 10079 Hz, and the low band needs seven for 95 %.
- **The lever is a first-order quantity, not a ceiling.** Re-solving the network exactly at the gradient-sign
  corner gives 1.0002–1.0017× the linear sum at 1 % and 1.0012–1.0092× at 5 %: curvature puts the exact corner
  *above* it. The first version of the test asserted the comfortable direction and went red.

`tools/tone_stage_schematic.py --sensitivity` prints the per-component table (`../sn-p13-vr4.json` →
`bands.<band>.at_hz.<f>.sensitivity`), the cited term beside it, both identities that check the table (impedance
scaling: R → kR with C → C/k leaves the answer exactly unchanged, so the resistors' and capacitors'
log-sensitivities must sum to the same number; frequency scaling: the capacitors' sum must equal d(dB)/d(ln f),
computed by perturbing the *frequency*), and a two-defect control matrix showing neither identity is decorative —
a 100× units bug satisfies the first exactly and is caught only by the second. `tolerance_bound_db` REFUSES
(`ToleranceUncited`) for a part silently absent from a class, and reports one explicitly declared uncited.

> **Wrong before it was right, twice, and both are recorded where the numbers are.** (1) The first version of this
> paragraph concluded *no* tolerance class was citable, on the strength of the 16-page service-notes search above.
> The search was real and its conclusion was still wrong: it covered **one** of the four sources this repository
> has already read, and §1.7 of `docs/tr808-reference.md` had carried the class the whole time. An absence is only
> as strong as the corpus it was searched over, and a "nothing found" finding has to name that corpus. (2) The
> first-order lever was asserted to be a ceiling; it is not.

Two things that should be said rather than smoothed over now the column has collapsed:

- For the low and decay bands the residual is **carried outward**. Figure 9 plots Ht1 only to 564 Hz and Ht2 only
  to 1.64 kHz, so their agreement is measured there and used at 3175 / 10079 Hz. What *is* independently checkable
  at those frequencies is the weaker statement that both nodal values fall inside Figure 9's own (7.4 / 13.9 dB
  wide) extrapolation bounds, which they do.
- The short band is the one place the two routes meet at the frequency the balance uses, and there the nodal value
  lands **0.016 dB outside** Figure 9's 0.041 dB-wide bound. That reads as a bound marginally too tight rather than
  a disagreement between methods — the excursion is smaller than the solution's own 0.032 dB residual against the
  same digitised curve — but it is asserted with a floor *and* a ceiling in
  `test_the_short_bands_bound_is_the_one_place_both_routes_can_be_compared`, so it cannot grow either way in silence.

For comparison, the route #396 excludes, at the same three frequencies:

| band | figure tone | figure tone ± | solved − figure |
|---|---:|---:|---:|
| low | −39.52 | 3.68 | −1.52 |
| decay | −28.62 | 6.95 | −1.24 |
| short | −36.61 | 0.02 | +0.04 |

| factor | where it comes from |
|---|---|
| band-pass peak | W14b Fig. 4 (`tools/werner_fig4.py`), whose digitiser is gated on SN p.13's R56–R59, C13–C16 — **resolved** |
| high-pass pass band | W14b Fig. 4, same gate — **resolved** |
| tone stage | SN p.13's VR4 network solved by nodal analysis (`tools/tone_stage_schematic.py`, emitted as `../sn-p13-vr4.json`) — **resolved** (#390/#417, applied by #420). `tone ±` above is that solution's own residual, not an extrapolation spread. Figure 9's bounded reading is kept beside it as the route #396 excludes, and `figure_route_gains()` still refuses on it |
| LEVEL differentiator | W14b Fig. 10 — common to all three bands, so it cancels in a ratio; its *frequency dependence* does not, because the bands are levelled at different frequencies |
| **VCA drive** | **ABSENT.** Three envelope generators and three swing VCAs (Q16/Q17/Q18, reference §10) sit between the band-passes and the high-passes. No W14b figure plots them and no artifact in this repository carries them. |

Relative to the low band:

| band | circuit | bound | shipped-kit rule (candidate 3) | gap | gap on the figure route |
|---|---:|---|---:|---:|---:|
| low | +0.00 | [−0.0077, +0.0077] | +0.00 | +0.00 | +0.00 |
| decay | **+17.46** | [+17.45, +17.47] | +7.33 | **+10.13** | +9.85 |
| short | **+26.21** | [+26.17, +26.25] | −13.58 | **+39.79** | +38.23 |

**The short-band gap is +39.79 dB, and the refusal's rationale survives it.** The argument this step's verdict
rests on is "38 dB is not a 9–18 dB figure problem". Re-deriving it from the resolved network moved it to 39.8 dB
— *away* from the tone term, not towards it — and simultaneously collapsed the tone term's own uncertainty from a
21.3 dB widest propagated bound to 0.07 dB. Both movements are in the conservative direction for a refusal, and
the falsifying outcome was available: had VR4's network carried the missing 38 dB, this table would show it.

The gap is what the **one** remaining absent precondition has to account for: the VCA drive ratios, **and** the
shipped kit's own band-gain errors (it has no Hh1 at all and routes the short band through the closed hat's
11.7 kHz high-pass). Nothing available here separates those two — saying which is which is exactly what the VCA
half of the schematic would do.

## 2. The refusal, and why it is not a decree

`balance_gains()` REFUSES on this tree and names the missing precondition with the path it would live at. As
approved it named **two**; #390/#417 supplied the first and #420 wired it in, so today it names exactly one:

```
REFUSED: the inter-band balance is not applicable: vca-drive absent
(docs/scorecard/cymbal-369/vca-drive.json) -- needed for the three envelope
generators' and swing VCAs' peak drive (Q16/Q17/Q18), which no W14b figure plots
```

That the message *shrank* is itself asserted, not just observed:
`test_the_vca_drive_precondition_refuses_on_this_tree_and_schematic_vr4_no_longer_does` requires the refusal to
name `vca-drive` and **not** to name `schematic-vr4`, and the gate's `refusal-live` property carries the same
clause. A refusal that went on naming a resolved input would be reporting a state of the repository that had
stopped being true — which is the failure mode this whole file exists to avoid, one level up.

Three things keep that from being an opinion compiled into a function:

- **It lifts.** Hand the tool a complete precondition set and it answers. `refusal-live` asserts both directions on
  every run, and the control `PRECOND_ALWAYS_OK` turns it red. An unsatisfiable gate is worse than no gate.
- **It has now lifted once for real, on one of its four preconditions.** `schematic-vr4` went from ABSENT to
  PRESENT the moment `../sn-p13-vr4.json` appeared, exactly as this section said it would, with no change to the
  refusal machinery. That is the strongest available evidence that the remaining refusal is a state rather than a
  decree: the same code reported a different answer when the repository changed under it.
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
from Figures 4 and 10, from SN p.13's solved VR4 network, and (for the excluded route) from Figure 9 — so it is a
genuine independent check rather than a fit being scored against itself.

**The prediction above is quoted exactly as it was committed, with the figure-route gaps it was made from
(+9.85 / +38.23).** #420 re-derived those to +10.13 / +39.79 and the ablation was re-rendered on them (§4's fourth
merge paragraph). Restating the prediction in the new numbers after the fact would destroy the only thing a
prediction is for, so it is left alone and the re-render is scored against it as written.

## 4. Result — the prediction holds, and the assumption is refuted

`balance-ablation.json`, rendered from a **clean tree** (`sources_dirty: false`). The prediction in §3 was committed
first and the ablation rendered after it, and the render has been reproduced identically from a later clean commit —
every field of `balance-ablation.json` except the recorded commit hash is byte-identical between the two runs.

**Reproduced a third time after `main` moved under the branch, and the record deliberately NOT re-anchored.** #412
(#388, the rimshot) changed `model/drums_fx.py` — `PEAK_RSG 0.343 → 0.7728` and a new `RS_LO_X_ATT = 3` on the path
word into `M_RS1` — so `main` was brought in with a **merge** rather than a rebase: a rebase would have rewritten
`cf2741e8` out of the history and left the record's `"commit"` pointing at a hash that no longer exists.
`tools/probes/balance_input_invariance.py` then measured, rather than argued, whether the drift reaches this
measurement: exactly two register addresses move in the whole image (`E_RSG+1` and `P_RS1X`), `drums_fx`'s own
`preset_writes` attributes both to CL and RS alone, and no CY address or CY envelope-peak register moves. The render
was repeated on the merged tree (clean, `cdff9ba`) to check that rather than trust it: **194 of 194 fields identical,
the recorded commit hash the only difference** — `preservation` for RS and CL included, which is the pair whose own
registers did move and which move on both the shipped and the candidate side. So the record still describes the tree
that ships, and `"commit": "cf2741e8…"` is left as rendered.

**`main` moved a second time, for #410, and that one is checked too rather than waved past.** #410 (cymbal step 6)
merged while this PR was in review, so `main` was brought in with a **second merge** for the same reason — `cf2741e8`
has to stay reachable, and `git merge-base --is-ancestor cf2741e8 HEAD` still says it is. #410's diff is five files:
`mid-band/README.md`, `mid-band/mid-band.json`, `../README.md`, `tools/cymbal_mid.py` and
`tools/test_cymbal_mid.py`. None of them is an input this step reads — `cymbal_band_balance` reads
`werner-fig4.json`, `werner-fig9.json`, `sn-p13-vr4.json` and `candidate3/candidate3.json` plus
`model/cymbal_candidate.py`, and the ablation renders through `model/drums_fx.py`; `cymbal_mid` is a *measurement*
module that nothing in this step imports. But "I read the diff and it looked unrelated" is the cheap internal check
`CLAUDE.md` names, so the same probe was run across this merge as across #412's:
`tools/probes/balance_input_invariance.py --baseline-rev ce80871` reports **0 register addresses moved** in the whole
image — no sound owns a moved address, no address is unattributed, no CY envelope-peak register moves, and
`cy_render_inputs_identical: true`. The ablation was therefore **not** re-rendered for #410: there is nothing for it
to have changed, and re-rendering to produce a hash that differs only in its own commit field would be the opposite
of evidence. `"commit": "cf2741e8…"` stands.

**And a third time, for #417 (#390, the tone-stage schematic) and #418 (#369 step 7, the low tail), by the same
rule.** A third merge, again not a rebase. Between them those two PRs add `tools/tone_stage_schematic.py`,
`tools/cymbal_low_tail.py`, `tools/cymbal_m_origin.py`, their tests, `low-tail/`, `m-origin.json` and prose in
`tone-stage/README.md`, `../README.md` and `docs/tr808-reference.md` — no `model/` file at all, so no render input.
Measured rather than asserted again: `balance_input_invariance.py --baseline-rev 5281540` reports **0 register
addresses moved**, nothing unattributed, no CY envelope-peak register moved, `cy_render_inputs_identical: true`.
So the ablation is not re-rendered for these either. **What #417 *does* change is the prose, not the render:** it
resolves the tone term this step only bounded, which is flagged at the top of this file, in §1's factor table and
in §5's second bullet, and left for **#420** rather than back-fitted here.

**And a fourth time, for #420 — where the rule finally said re-render, and the probe is the reason we know why.**
Applying #390's nodal solution moves §1's tone column, which moves `gap_db`, which is an input to `rebalance()` and
therefore to the ablation's own amp registers. So the same question as the three merges above got the opposite
answer, and the important part is that the two answers come from two different instruments looking at two different
things:

- `balance_input_invariance.py --baseline-rev fb638f4` again reports **0 register addresses moved** — correctly.
  Its question is whether *another commit's `model/drums_fx.py`* changes a render input, and #420 touches no
  `model/` render code at all.
- The input that moved is **upstream of `drums_fx`**, in the amps `rebalance()` computes, which that probe does not
  and cannot see. Measured directly instead of inferred: `M_CYH1` 0.014140 → **0.011815**, `M_CYHI` 0.126937 →
  **0.109543** (−1.56 and −1.28 dB), `M_CYH3B` unchanged at the Q0.16 ceiling, `E_short` unchanged at the envelope
  peak ceiling. `test_the_committed_ablation_used_the_gaps_this_tool_computes` went red on exactly this, which is
  what it is for.

So the ablation **was** re-rendered, from a clean tree (`sources_dirty: false`) at `82d03b1`, and the tables below
are the new render. `cf2741e8` remains reachable — this was a merge, never a rebase — but the record's `"commit"`
now names the tree it actually describes, because a record pinned to a commit whose amps it no longer uses would be
precisely the drift this file keeps warning about. The verdict did not move: H−L went from 24.38 to **25.07**,
i.e. **further** from the 808's 8.16, which is the direction resolving the tone term predicted.

The solver's anchor came out at **−35.38 dB** (was −33.82): the low band drops 35.4 dB and the decay band 25.3 dB,
while the short band rises 4.4 dB against candidate 3, because the short band's envelope peak register is the
ceiling that binds. The ratios are the claim; that common scale is not — **except in one respect the CY path makes,
recorded here rather than left implied.** The anchor leaves `env_gain` at 1.0 for the low and decay bands but
**3.0864** for the short band: its amp register is already at the Q0.16 ceiling, so the rest of that band's gain
lands on its envelope peak (0.324 → the 1.0 ceiling), which is **+9.79 dB more excitation into `NL_SWING`** for that
band alone. The swing VCA is not linear, so the ablation moved the short band's operating point inside that
nonlinearity as well as the three band ratios. It does not change the verdict — the direction is conservative for a
refusal — but it is the same class of confound as the sibling one §5 discloses (H's internal mix moved, EDT10
147 → 59 ms), and a sentence that claimed neutrality for a common scale on a nonlinear path would be claiming more
than the path allows.

| | 808 CY5025 | shipped | candidate 3 | **balance ablation** |
|---|---:|---:|---:|---:|
| H − L | **8.16 dB** | 10.82 | 12.09 | **25.07** |
| H EDT10 | **147.8 ms** | 151.3 | 147.2 | **58.8** |
| H late T20 | **432 ms** | *refused* | 363 | 326 |
| Ln EDT10 | **591 ms** | 598 | 578 | 644 |

1/3-octave residual against the 808 (dB; positive = we have more), summarised the same three ways
`../candidate3/README.md` §4 uses:

| window | | shipped | candidate 3 | **balance ablation** |
|---|---|---:|---:|---:|
| 0–50 ms | tilt | −22.5 | **+6.6** | +9.0 |
| | worst | 15.4 | **5.7** | **18.0** |
| | n>6 dB | 6/14 | **0/14** | **8/14** |
| 50–300 ms | tilt | −17.3 | +14.3 | +19.3 |
| | worst | **9.4** | 11.6 | 17.3 |
| | n>6 dB | **2/14** | 7/14 | 9/14 |
| 300–1000 ms | tilt | −9.9 | +16.2 | +21.3 |
| | worst | **8.8** | 10.5 | 14.5 |
| | n>6 dB | **1/14** | 6/14 | 10/14 |

**Preservation still passes.** All 15 non-CY sounds render bit-identically to the shipped kit on the 19-mode
layout, hats included (`balance-ablation.json` → `preservation`, all `true`). The ablation moves only the three CY
band levels, and the record proves it.

**The prediction was right in sign and order, and 4.9 dB steep.** It said H−L would go from 12.09 to *roughly*
+30 dB; it went to **25.07**. That is the same direction and about the same size of over-prediction as candidate 3's
per-band arithmetic (which was 6 dB steep), and for the same reason: three re-levelled bands summing into one
measure do not move by the per-band figure. On the figure-route gaps it was made from, the same prediction scored
5.7 dB steep (H−L 24.38); re-deriving the tone term moved the outcome 0.69 dB **towards** the prediction, which
the prediction did not claim and which is worth exactly as much as that.

**Wrong-then-right, recorded because that is the rate a reader calibrates on:** this step's predicted-then-measured
pair is 1 of 1 — the prediction's *direction* was confirmed and its *magnitude* was 4.9 dB out, caught by the render
rather than by inspection. No measurement in this step was wrong before it was right. The ablation has now been
rendered **twice**: once on the figure-route gaps as approved, and once on #420's re-derived gaps. That second
render was not a correction of a wrong number — the first was right for the inputs it had — it is a re-measurement
forced by an input that genuinely moved, and both are reported rather than the second quietly replacing the first.

## 5. Verdict

**The balance is REFUSED, and the refusal is now measured rather than argued. Nothing reaches RTL. R1 is unchanged.
`model/cymbal_candidate.py` keeps the shipped-kit level rule.**

- **#396's item 1 (apply the balance) cannot be done honestly, and the reason is not the one the issue names.**
  Applying every factor that IS resolved, with the only unmeasured factor held at unity, puts the band split
  **16.9 dB from the 808** where the rule it replaced is 3.9 dB from it, and turns candidate 3's clean strike
  window (0 of 14 thirds outside ±6 dB, the chain's one clear win over the shipped kit) into 8 of 14. Every
  summary measure gets worse in every window.
- **#396's item 2 (derive it from the schematic) is the only route, and HALF OF IT IS NOW DONE.**
  `docs/scorecard/cymbal-369/sn-p13-vr4.json` exists: #390/#417 landed `tools/tone_stage_schematic.py`, which reads
  VR4's R/C values off a SHA-256-pinned scan of SN p.13 and solves the network, and #420 emitted that solution as
  the named artifact and re-derived §1's tone column from it. The precondition the tool named went from ABSENT to
  PRESENT with no change to the refusal machinery, which is what §2's "it lifts the moment it appears" claimed and
  is now the one place in this chain where that claim has been tested against reality rather than against a
  synthetic precondition set. The remaining refusal is on `vca-drive` alone.
- **The schematic needed to supply two things, not one — and that is now confirmed, not predicted.** VR4's network
  resolves the tone term. It does **not** resolve the VCA drives, and this step's measurement said those were the
  larger term. Re-deriving on the solved network is the test of that claim, and the claim passed in the direction
  that could have broken it: the short-band gap **grew** from 38.2 to **39.8 dB** while the tone term's own widest
  propagated bound **fell** from 21.3 dB to **0.07 dB**, and the ablation's H−L error went from 16.2 to 16.9 dB.
  Had VR4 turned out to carry the 38 dB, this bullet would have been wrong and the gap would have closed.
- **An indication, not a measurement, for whoever digitises the VCA half:** with H dominated by the short band
  after the ablation, an H−L error of +16.9 dB is consistent with the short band's VCA drive sitting **roughly
  17–23 dB below** what equal drive assumes, relative to the low band. It is stated as an indication because H−L
  is a two-band energy ratio and the ablation also moved the *mix within* H (H EDT10 collapsed from 147 to 59 ms
  as the short band took over from the decay band), so the number confounds a level with a mix. It is a sanity
  check to run against a real schematic value, not a substitute for one.
- **#396's item 3 (the TONE knob law) is deferred, and its blocker did not exist as an issue until now: #413.**
  See §6.

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
acceptance item 3 of #396, the listening pack's other 24 settings, and `gate-379`'s §2 selection argument was
tracked by nothing. **Filed now as #413**, with the diagnosis, the repair's acceptance bar and a must-fail control
for the saturation the render currently has. The four citations above are left for #413 to correct rather than
rewritten here, so the filing is visible in one place.

Figure 9 itself is the second half of the knob-law problem and is not repairable: it marks only k = 1.0 and gives
no k value for the other four members of each family, so **no TONE position except fully-open is readable from it**
(`tools/werner_fig9.py --check`, which asserts the marker identification and reports that in two of the three
sub-plots the marked curve is not the topmost one).

## Files

- `tools/cymbal_band_balance.py` — the decomposition, the bounds, the preconditions, the refusal, and the ablation's
  level solver. `--report` / `--check` / `--json`. Both tone routes live here side by side: `schematic_tone_term`
  (the solved network, what the balance uses) and `figure_tone_term` (Figure 9, the route #396 excludes).
- `tools/tone_stage_schematic.py` — #390/#417's nodal solution, and `--emit` writes `../sn-p13-vr4.json` from it.
- `../sn-p13-vr4.json` — the `schematic-vr4` precondition: the solved transmission per band at the frequencies the
  balance evaluates, with the pinned scan's SHA-256, the fitted wiper fraction and its σ, the per-family residuals,
  the five shared poles, and the SHA-256 of the Figure 9 artifact α was fitted against.
- `tools/test_cymbal_band_balance.py` — 39 tests: closed-form normalisation known answers, cross-checks against
  Figure 9's *digitised points* rather than its fit, the tone term pinned to `tone_stage_schematic`'s own output
  and the committed artifact pinned to the module that emits it, the refusal asserted in both directions, the level
  solver's ratios and its register refusal, and the 5-control properties × defects matrix with one control blind by
  construction.
- `balance.json` — the decomposition, both routes' bounds, the preconditions and the gate's own output.
- `balance-ablation.json` — the render: levels, preservation, band measures and 1/3 octaves, with commit and dirty
  flag.
- Reproduce: `python3 tools/tone_stage_schematic.py --emit` (≈10 s), then
  `python3 tools/cymbal_band_balance.py --check --json docs/scorecard/cymbal-369/balance/balance.json`
  (≈2 s), then `python3 tools/cymbal_candidate_eval.py --variant balance --out
  docs/scorecard/cymbal-369/balance/balance-ablation.json` (≈4.5 min).
