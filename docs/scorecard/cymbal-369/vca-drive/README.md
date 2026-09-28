# #369 step 11: the three swing VCAs, read off SN p.13 — there are not three drives, and the one term there is cannot close the gap

Step 10 (`../tone-render/README.md`) ended by naming exactly one next question, and this is that question:

> **What sets the three swing VCAs' drive levels?** Every route to the inter-band balance now terminates on the
> same unmeasured quantity — #396 refused on it, step 8's clipper sweep could not reach the 808's joint box
> without it, and step 10 §4 cannot even state a requirement for it because the clipping it induces is what
> breaks superposition. The artifact that would resolve it is SN p.13's own resistor network around Q16/Q17/Q18,
> read the way `tools/tone_stage_schematic.py` read VR4's — by nodal analysis off a hash-pinned scan, with
> `--verify-source` and a refusal, not by fitting a drive to a recording.

**No kit, RTL, model or scorer change. R1 is unchanged and no candidate is promoted.** Nothing already committed
under `docs/scorecard/cymbal-369/` moves. This increment is a schematic read, its qualification, and one
correction to `docs/tr808-reference.md` §10 and `tools/werner_fig4.py`.

## 0. Summary, in the order a reader should not skip

| | result |
|---|---|
| **The question's premise is wrong, and that is the finding** | There are not three drive levels to read. The three stages are **component-identical** — same 0.022 µF coupling cap, same **2 MΩ series** base bias from B1 (no ground leg, so the same Ic and the same gm), same 100 Ω emitter degeneration, same series diode — and **Q16 and Q17 hang on the same node**, IC3 pin 7, with nothing between them. Their signal drives are equal by construction, not by approximation. |
| **The one element that differs** | the collector load, from each band's own envelope reservoir down to its VCA output node: **R94 39 kΩ** (short), **R90 33 kΩ** (DECAY), **R104 22 kΩ** (low). |
| **The number** | **+4.97 dB** (short) and **+3.52 dB** (DECAY) relative to the low band; **+8.72 / +7.26 dB** as an upper bound that loads the low band with Hh1's own measured input impedance and leaves the high bands unloaded. |
| **Against #396's gap** | the short band's gap is **+38.23 dB**. Closing it inside the VCA needs a collector load of **1.79 MΩ**; the schematic prints **39 kΩ**, which is **33.26 dB** short. **The missing factor is not in the cymbal's VCA section.** |
| **The half that does NOT support the headline** | the DECAY band's gap is +9.85 dB and the upper bound supplies +7.26 dB — within 2.6 dB. The conclusion is carried by the short band alone, and this file says so rather than averaging the two. |
| **The external known answer that qualifies the read** | the two band-passes' **input networks** (C10 0.0033 µF + R52 33 kΩ; C11 0.001 µF + R55 22 kΩ) appear in no prior document here and are what set the filters' absolute gain. Solving both filters with them gives **+22.96 / +24.11 dB**, against W14b Figure 4's digitised **+22.95 / +24.10** — **0.01 dB**, from a different artifact by a different author who never saw these four parts. |
| **Correction** | §10's two band-pass rows carried each other's reference designators. No value or f0 moves; the label a reader would use to find the part on the board was wrong. Details in §5. |

Everything here is arithmetic against a hash-pinned scan: `tools/cymbal_vca_drive.py` (2.4 s) with
`tools/test_cymbal_vca_drive.py` (46 tests, 2.3 s). `vca-drive.json` is the record.

## 1. The read

`--verify-source <sn.pdf>` asserts the file's SHA-256 is `SN_PDF_SHA256` — **the same pin
`tools/tone_stage_schematic.py` carries for VR4**, asserted equal in the tests so the two modules cannot drift
onto different printings — and then re-renders the three crop boxes every value below was read off. It REFUSES
(exit 3) when the file is absent or its hash does not match; the scan is a ~6 MB third-party download and is
deliberately not a test dependency. `--require-source` makes the check binding for a caller who wants it to be,
and it is reachable from `main()` — the defect step 9 found in its own `--require-corpus` is not repeated, and
there is a test that runs the CLI both ways on the same tree to prove it.

| crop | dpi | what it carries |
|---|---|---|
| `vca-hi` | 600 | Q16, its bias chain, R95, D5/C36/C47, R94 and the C38/R87/C37 reservoir; Q17's base network and D7/C40/R88/C39/R90 |
| `vca-lo` | 600 | Q17's emitter, Q18 and its bias, D12, R105/C45/R104, and Hh1's own C48/C59/R124/R127 on Q25 |
| `bandpass` | 400 | the six-square summing bus, both bridged-T band-passes **with their input networks**, and IC3 pins 1/7 feeding C46 and C42/C44 |

### The three stages

| | short | DECAY | low |
|---|---|---|---|
| transistor | Q16 | Q17 | Q18 |
| fed from | **IC3 pin 7** | **IC3 pin 7** | IC3 pin 1 |
| coupling cap | C42 0.022 µF | C44 0.022 µF | C46 0.022 µF |
| base bias (series, to B1) | R96 1M + R97 1M | R100 1M + R99 1M | R102 1M + R103 1M |
| emitter | R95 100 Ω | R98 100 Ω | R101 100 Ω |
| series diode | D5 | D11 | D12 |
| **collector load** | **R94 39 kΩ** | **R90 33 kΩ** | **R104 22 kΩ** |
| reservoir | C38 1 µF via D6, smoothed R87 22 k + C37 2.2 µF | C40 1 µF via D7, smoothed R88 33 k + C39 0.47 µF | via R105 33 k from the Q20 envelope, smoothed C45 2.2 µF |

The source side is a passive summing node as well: six 120 kΩ resistors (R35, R37, R39, R46, R48, R50) from the
six HD14584 Schmitt squares into one bus with R53 1 kΩ to ground, and **both** band-passes tap that one node.
There is no per-band trim there either.

### Why the base bias being a *series pair* is load-bearing

This is the thing I read wrong first (§6, wrong-then-right 2). A 1M/1M **divider** to ground fixes each base's
*voltage*, and the three stages' collector currents would then differ with Vbe. Two 1 MΩ in **series** from B1
with no ground leg fixes each base's *current*, so all three carry the same Ic, the same r_e, and the same gm —
which is exactly the precondition that makes the collector-load ratio the *whole* inter-band term rather than a
leading term. `test_the_base_bias_is_a_series_pair_not_a_divider` pins it at 2 MΩ, not 500 kΩ.

## 2. The answer, under two conventions, both reported

`chain_db` is the collector-load ratio alone. It is the term to multiply into
`cymbal_band_balance.band_filter_db`, which already carries each band's high-pass separately, so the load must
not be counted twice.

`bound_db` loads the low band's 22 kΩ with **Hh1's own measured input impedance** (40.6 kΩ at the low band's
level centre, 3175 Hz → 14.30 kΩ in parallel) and leaves the two high bands **unloaded**. A passive load can only
reduce |Z|, so this is an **upper bound on the ratio, not an estimate of it** — and the upper bound is the right
thing to argue against, because the headline is that the term is too *small*.

| re low | short | DECAY |
|---|---:|---:|
| collector-load ratio (`chain_db`) | **+4.97 dB** | **+3.52 dB** |
| upper bound (`bound_db`) | **+8.72 dB** | **+7.26 dB** |
| #396's gap (`../balance/balance.json`) | **+38.23 dB** | **+9.85 dB** |
| margin | **29.5 dB** | 2.6 dB |

### The same statement with no gain model in it at all

| band | gap | collector load it would need | printed | short by |
|---|---:|---:|---:|---:|
| short | +38.23 dB | **1.79 MΩ** | R94 **39 kΩ** | **33.26 dB** |
| DECAY | +9.85 dB | 68.4 kΩ | R90 33 kΩ | 6.33 dB |

The short band's row needs no assumption about gm, r_e, loading or clipping: it is the resistance the schematic
would have to print against the resistance it does print.

**And the DECAY row is why this file does not claim more than it has.** 6.3 dB is within the range a loading
treatment could plausibly move, so the DECAY band's gap is *not* refuted here. The conclusion — the balance's
missing factor is not in the VCA section — rests on the short band, which is also the band the gap is largest on
and the band step 10 §4 measured as sitting 10.59 dB below the decay band inside H.

## 3. Why the read can be trusted: an external known answer on its newest part

The read's only genuinely new content is the two band-passes' **input networks**. Nothing in
`docs/tr808-reference.md`, `tools/werner_fig4.py`, `tools/cymbal_band_balance.py` or `model/cymbal_candidate.py`
has ever carried C10/R52/C11/R55, and they are what set each filter's **absolute** gain — f0 and Q do not depend
on them at all.

| | computed from this read | W14b Fig. 4, digitised (`cc.BP_PEAK_DB`) | Δ |
|---|---:|---:|---:|
| 3.45 kHz band-pass peak | **+22.96 dB** | +22.95 | **0.01** |
| 7.1 kHz band-pass peak | **+24.11 dB** | +24.10 | **0.01** |

That is an external check, not a consistency check: Figure 4 is a plotted curve in a published paper, digitised
by a different tool from a different artifact, and no part of it informed which resistor sits in front of which
op-amp on the board.

**And the blindness that makes it load-bearing is asserted and verified blind.** `DROP_INPUT_NETWORK` replaces
both input networks with a plain 10 kΩ. The peak gain moves **8.6 dB**; f0 and Q **do not move at all**, because
they are set by the bridged-T alone. So f0/Q cannot stand in for the input read, and the peak-gain agreement is
the only thing testing it.

Two further known answers on the rest of the read: both bridged-T centres and Q reproduce §10's 3450 / 7100 Hz
and Q 6 from the components; Hh1 reproduces §10's 2.5 kHz / Q 0.97 from C48/C59/R124/R127 — which matters
because Hh1's input impedance is what the `bound_db` convention depends on.

### Controls, and what each one is for

Seven injected defects, every one a change to a component value or a wire and never to a bound:

| defect | turns red |
|---|---|
| `SWAP_BP_CAPS` | `bp-f0-matches-reference`, `bp-peak-matches-figure4` |
| `DROP_INPUT_NETWORK` | `bp-peak-matches-figure4` **only** (f0/Q asserted blind, verified blind) |
| `UNEQUAL_EMITTER` | `only-collector-load-differs` |
| `SPLIT_HIGH_FEED` | `high-bands-share-one-node` |
| `EQUAL_COLLECTOR_LOADS` | `only-collector-load-differs` |
| `HUGE_SHORT_LOAD` (R94 → 1.8 MΩ) | `required-load-not-printed`, `vca-term-far-below-the-gap` |
| `HH1_WRONG_RATIO` | `hh1-matches-reference` |

`HUGE_SHORT_LOAD` is the paired negative for the headline: it puts the resistor the gap would need onto the
schematic and the two properties that carry the conclusion go red. Without it, "the VCA term is far below the
gap" is a claim nobody has seen fail.

Four blindness assertions are checked as well (a filter defect must not move a VCA property, and vice versa),
and the band-pass transfer function is built twice — once by hand elimination and once as an explicit
`(G + sC) v = b` solve with the op-amp as an ideal nullor. They agree to **2e-14 dB**, and a control perturbs
one formulation only to show that agreement is not two names for one code path.

## 4. Refusals

- **The pinned scan.** Absent or wrong hash ⇒ `SourceUnavailable`, exit 3 (`--verify-source`) or exit 1
  (`--require-source`). A schematic read checked against the wrong printing looks exactly like a checked one.
- **The gap this file is judged against.** `balance_targets()` re-reads `../balance/balance.json` and REFUSES if
  either figure has moved from the +9.85 / +38.23 quoted here. A conclusion quoted against a number that has
  since drifted is this repository's recurring failure, and it is cheap to make impossible.

## 5. The correction: §10's two band-pass rows carried each other's designators

The schematic prints **C13 = C14 = 0.0068 µF with R56/R57 on IC3 pin 1** (3.45 kHz) and
**C15 = C16 = 0.0033 µF with R58/R59 on IC3 pin 7** (7.1 kHz). §10 had it the other way round. **No value and no
f0/Q moves**, because each row already carried the right *capacitance* for its own band and both filters share
560 Ω / 82 kΩ; what was wrong is the label a reader would use to find the part on the board — or to re-read the
scan and "confirm" the wrong component.

`tools/werner_fig4.py`'s `KNOWN` dict was wrong in the other half: its *designators* were right and the
capacitance printed beside them was not (`"3450 Hz … C13=C14 3.3 nF"`, and 3.3 nF on that network is 7117 Hz —
not a self-consistent pair). `f0` and `q` there are hard-coded, so no digitiser gate moves either.

Both are fixed, and the fix is a mechanism rather than a note:
`test_werner_fig4_source_strings_name_components_that_produce_their_own_f0` recomputes f0 from the designators
each string names **and** compares the printed capacitance against this read, and
`test_reference_section_10_names_the_designators_this_read_found` parses the shipped §10 table. Neither half can
silently re-cross.

## 6. Wrong-then-right rate for this step: 3, published because that rate is how a reader calibrates §2

| # | what was wrong | what caught it |
|---|---|---|
| 1 | **I looked for the drives in the wrong place.** The question is phrased as "three drive levels", and a drive trim normally lives at a stage's *input*, so the first pass went looking at the coupling networks. All three are 0.022 µF into identical 2 MΩ / 100 Ω stages and two of them share a node; the per-band element is the **collector load**, on the supply side. | reading the schematic at 600 dpi instead of reasoning about where such a part *usually* goes |
| 2 | **the base bias read as a 1M/1M divider to ground.** That is what a 1M-over-1M pair almost always is, and at 100 dpi the tap looked like it was between them. At 600 dpi the tap is *below* both and there is no ground leg: they are in series, 2 MΩ, fixed base current. This is not cosmetic — it is the precondition that makes the three stages' gm equal and therefore makes §2's ratio exact rather than approximate | re-cropping the same region at 6× the resolution before writing the number down. `test_the_base_bias_is_a_series_pair_not_a_divider` now pins it |
| 3 | **the designator test was vacuous, and it passed on the exact string it was written to catch.** It recomputed f0 from *this module's* values for the designators the string names — so for `"3450 Hz … C13=C14 3.3 nF"` it looked up C13, got **6.8 nF** from the read, computed 3454 Hz, and went green while the text beside it said 3.3 nF | running the control against the pre-fix string before trusting the test. It now checks the printed capacitance too, and both halves are required |

Number 3 is the one worth dwelling on: it is the same shape as #376's crosstalk control — a control that **could
not fail on any input**, passing for a reason that had nothing to do with the thing it was guarding. The only
thing that found it was re-running it against the state it was supposed to reject.

**And one thing that was right first time and is not evidence of care:** the peak-gain agreement with Figure 4.
It was 0.01 dB on the first run. That is a real external check, but it says the *read* is right, not that the
reading *process* was careful.

## 7. What this step forbids, and the next question

**Forbidden to the next step: treating the VCA drives as a free parameter.** They are not free and they are not
three. Any candidate that reaches the 808's band balance by choosing per-band VCA drives is choosing numbers the
schematic prints, and the printed ones are +4.97 / +3.52 dB.

**Also forbidden: satisfying #396's `vca-drive` precondition by dropping a file at
`docs/scorecard/cymbal-369/vca-drive.json`.** `cymbal_band_balance.preconditions()` gates on that path's
**existence** and never reads its contents, so creating it would shorten the refusal list without one drive
number reaching the balance — a false green of exactly the kind this chain exists to avoid. This step therefore
writes to `vca-drive/vca-drive.json`, asserts the mismatch in
`test_this_artifact_is_not_at_the_path_that_would_flip_396s_refusal`, and the gate defect is **#431**.

**The next question, and it is one question:**

> **What are the three envelope generators' peak collector voltages?** That is the per-band freedom this step
> did *not* close, and it is now the only one left inside the cymbal's VCA section. All three reservoirs are
> charged from Q19 through their own diode (D6, D7, D8), which is why the *shape* of the answer is likely to be
> "equal at the peak and different only in decay" — but each sits behind a different smoothing network
> (R87 22 k + C37 2.2 µF; R88 33 k + C39 0.47 µF; R105 33 k + C45 2.2 µF), and the collector's DC operating point
> is what sets where a swing VCA clips. It is the same kind of read as this one and belongs in the same tool.

That question is filed as **#432**, with the components for all three networks in its body.

Still open and unchanged by this step: #396 (the inter-band balance — its `schematic-vr4` precondition artifact
has still never been written, and its `vca-drive` precondition is now measured but deliberately not flipped),
#413, #400's tail question, the DECAY knob's own law, and the listening pack at other settings. **And the
balance's +38.23 dB gap now has one fewer place to live**, which is the whole point of a negative.

## Files

- `vca-drive.json` — the record: the read, the crops, both conventions, the required-load table, every property,
  the gate, the commit and its dirty flag.
- `../../../../tools/cymbal_vca_drive.py` — the instrument. `--report` / `--check` / `--json` /
  `--verify-source` / `--require-source`.
- `../../../../tools/test_cymbal_vca_drive.py` — 46 tests: 6 known answers against artifacts this module did not
  produce, 7 injected defects, 4 blindness assertions, 6 refusal paths, and the two designator gates.
- Reproduce: `python3 tools/cymbal_vca_drive.py --check` (2.4 s). Add
  `--require-source <sn.pdf>` on a host that has the scan; fetch it with
  `curl -sL -o sn.pdf https://ia801906.us.archive.org/9/items/synthmanual-roland-tr-808-service-notes/rolandtr-808servicenotes.pdf`
  (the `archive.org/download/…` route `tone_stage_schematic.py` records returned HTTP 500 on 2026-09-28; the
  bytes are identical and the hash matched on the first try from the node above).
