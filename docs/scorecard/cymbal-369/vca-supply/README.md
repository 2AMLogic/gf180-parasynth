# #369 step 12 (#432): the three envelope generators' peak collector voltages, read off SN p.13

Step 11 (`../vca-drive/README.md`) closed the swing VCAs' *signal* side and left exactly one per-band freedom
open inside the cymbal's VCA section:

> **What are the three envelope generators' peak collector voltages?** All three reservoirs charge from Q19
> through their own diode (D6, D7, D8), which is why the plausible shape is "equal at the peak, different only
> in decay" — but each sits behind a different smoothing network, so it has to be derived rather than assumed.

**No kit, RTL, model or scorer change. R1 is unchanged and no candidate is promoted.** This increment is a
schematic read, its qualification, and one correction to `docs/tr808-reference.md` §10 and §1.3.

## 0. Summary, in the order a reader should not skip

| | result |
|---|---|
| **The hypothesis is half right** | The three reservoirs (**C38, C40, C41 — all 1 µF**) ARE equal at the peak: **0.009 V spread** against a **0.252 V** bound derived from the diodes' own forward-drop spread at their own load currents (which differ by two orders of magnitude — that is why the bound is not zero). |
| **The three COLLECTOR ceilings are NOT equal** | **12.79 V (short) / 4.94 V (low) / 3.97 V (DECAY)** at a 14 V trigger and unit duty — **+8.26 dB and −1.91 dB relative to the low band** — and they peak **1 / 120 / 19 ms** apart. Only the short band's collector load (R94) hangs on its own reservoir; the DECAY band's hangs behind R88 33 kΩ / C39 0.47 µF and a divider, the low band's behind Q20's follower and R105 33 kΩ / C45 2.2 µF. |
| **Against #396's gap** | the short band's ceiling, at its MOST FAVOURABLE duty in the 0.25–1.0 bracket, is **31.5 dB below** the +39.79 dB the balance needs. Combined with step 11's signal-side result, **no path through the swing VCA section reaches the gap.** |
| **A correction to the reference, found the same way step 11 found the designator swap** | §10 recorded VR2 (2 MΩ(B)) in **parallel** with R93 470 kΩ. The scan prints them in **series** — R93 runs from the reservoir down to VR2's pin 3, and VR2's wiper (pin 2) is strapped to that same pin 3. Read the reference's way, DECAY's minimum is a dead short across C41 and the low band's reservoir never charges; read the scan's way the timing resistance is 470 k–2.47 M and never zero. The external known answer below rejects the reference's reading and accepts the scan's. |
| **And that correction relocates the DECAY knob** | VR2/C41 feeds the **low** band's collector supply directly through Q20 and R105, and reaches the middle (DECAY) band only through R92/R89/R91. So the DECAY knob's predicted effect lands mostly on the **low** band (×4.0 span) and only partly on the middle band (×5.2) — which is what the Fischer recordings already said (`../README.md` §1: "the low band's own decay tracks DECAY... the recordings contradict §10") and what §10 denied until this step. |
| **The external known answer** | Roland's own chart (SN p.14, 350→1200 ms, span 3.43) and two independent Fischer-recording measurements (low band Ln EDT10 span 3.20, high-bands late T20 span 4.36) — none of which used this schematic read. Predicted spans land at 4.04 (low) and 5.23 (DECAY), inside a ×1.6 bound derived from the three external sources' own mutual disagreement (1.36, rounded up), swept over duty 0.25–1.0 and beta 100–400. |

Everything here is arithmetic (a backward-Euler transient with piecewise-linear diodes) against the same
hash-pinned scan step 11 used: `tools/cymbal_vca_drive.py` extends step 11's module rather than adding a new
one, and `tools/test_cymbal_vca_drive.py` grows from step 11's 46 tests to 90 — 19 new test functions (35
collected cases after parametrization) plus this step's own defects widening several of step 11's shared
parametrized controls. `vca-supply.json` is the machine-readable record, written by `--json-supply`, sibling to
step 11's `vca-drive.json` rather than replacing it — the two questions (signal drive, collector supply) are
independently falsifiable and step 11's numbers do not move.

## 1. The read

Two new crop boxes on the same page (`SN_PDF_PAGE = 13`), rendered by the same `verify_source()` step 11 uses,
under the same `--verify-source` / `--require-source` refusal:

| crop | dpi | what it carries |
|---|---|---|
| `env-q19` | 600 | the attack buffer Q19 (R84 10 k base, R85/R86/C35 supply decoupling) and the D6/D7/D8 common-anode bus off its **emitter** — the arrow points away from the base, so Q19 is an NPN follower and the three reservoirs are *charged*, not discharged, through it |
| `env-q20` | 600 | Q20 (the second follower), R91/R92/R89, R93 470 k **in series** with VR2 2 M(B) (wiper strapped to its own pin 3), C41 1 µF, and the low band's R105/C45/R104 chain down to D12 |

### The envelope netlist, by node (reference designators exactly as printed)

| node | what it is |
|---|---|
| E | Q19's emitter — the common anode of D6/D7/D8 |
| A | C38 1 µF, the **short** band's reservoir. R94 leaves it directly for the VCA |
| B | C37 2.2 µF, behind R87 22 k: a shunt lag on A, not a supply |
| S | the short band's VCA output node (D5's anode) |
| P | C40 1 µF, the **DECAY** band's reservoir |
| Q | C39 0.47 µF, behind R88 33 k — R90 leaves **this** node, not P |
| Z | the DECAY band's VCA output node (D11's anode) |
| X | R89 10 k to ground, fed by R91 from Z and R92 from Q20's emitter |
| F | C41 1 µF, the **low** band's reservoir AND Q20's base |
| V | the top of VR2, reached from F through R93 — series, not parallel |
| G | Q20's emitter |
| W | C45 2.2 µF, behind R105 33 k — R104 leaves **this** node |
| Y | the low band's VCA output node (D12's anode) |

| designator | value | designator | value |
|---|---:|---|---:|
| R87 | 22 kΩ | R89 | 10 kΩ |
| R94 | 39 kΩ | R92 | 33 kΩ |
| R88 | 33 kΩ | R93 | 470 kΩ |
| R90 | 33 kΩ | R105 | 33 kΩ |
| R91 | 33 kΩ | R104 | 22 kΩ |
| C38 | 1 µF | C40 | 1 µF |
| C37 | 2.2 µF | C39 | 0.47 µF |
| C41 | 1 µF | C45 | 2.2 µF |

VR2 is 2 MΩ, taper B, wiper strapped to pin 3 — so the two-terminal element the pot contributes is
`wiper_position * 2 MΩ`, in series with R93, never zero.

## 2. The model: a backward-Euler transient, not a fit

The swing VCA's collector supply is the envelope voltage (reference §1.3), so the question is a **transient**
circuit problem, not a static one: a 1 ms trigger (S1.1 / SN p.5 Fig. 7, p.14) charges Q19's emitter through
`max(v_trig − V_BE, 0)`, D6/D7/D8 (each `V_f` + a small on-resistance, blocking reverse current so the reservoirs
cannot discharge back into Q19 once the pulse ends) charge the three reservoirs, and Q20 (a second NPN follower
on C41) draws a real base-current discharge path off the low band's own reservoir.

**The swing VCA's average load.** The stage switches at the band-pass frequency (≥ 3.45 kHz), two to three
orders of magnitude faster than every envelope time constant here, so each reservoir sees only the VCA's
*average* current. A switch that clamps its collector to `V_CLAMP` (the stage's lower edge) for a fraction
`duty` of each cycle and floats for the rest draws the same average current as a fixed resistance
`R_load*(1/duty − 1) + R_SAT`. **`duty` is not printed on the schematic**, so every number in this step is
reported across a bracket (0.25, 0.5, 1.0) rather than chosen, and the beta of Q19/Q20 (100–400) is swept the
same way.

Two formulations, for the reason step 11's band-pass carries two: the short band's reservoir (C38) touches only
two resistors (R87 to B, R94 to S) and nothing else in the cymbal reaches it, so its own modes are a
hand-checkable 3×3 eigenvalue problem. The MNA transient's deep tail is required to reproduce the slowest of
them to 3 % (`test_the_transient_reproduces_the_closed_form_short_band_modes`), and a control perturbs one
formulation only to show the agreement is not two names for one code path.

## 3. The answer

### 3a. Equal at the reservoir — confirmed

| band | reservoir | peak voltage (14 V trigger) | load current at the peak |
|---|---|---:|---:|
| short | C38 | 12.791 V | 0.879 mA |
| low | C41 | 12.800 V | 0.008 mA |
| DECAY | C40 | 12.796 V | 0.363 mA |

Spread **0.0087 V**, bound **0.2521 V** (`n·Vt·ln(I_hi/I_lo)` at the currents the transient itself reports,
which differ by two orders of magnitude — the reason the bound is not zero). `MISMATCHED_D8` (D8's forward
drop +0.4 V) is the paired negative: it pushes the spread past the same bound.

### 3b. Not equal at the collector — refuted, and by a lot

| band | collector ceiling (14 V trigger, duty 1.0) | dB re low | peaks at |
|---|---:|---:|---:|
| short | 12.791 V | **+8.26 dB** | 1.0 ms |
| low | 4.943 V | 0.00 dB | 120.4 ms |
| DECAY | 3.967 V | **−1.91 dB** | 18.8 ms |

Across the whole duty bracket (0.25–1.0), the short band's ceiling stays **+4.02 … +8.26 dB** above the low
band's and the DECAY band's stays **−4.39 … −1.91 dB** below it — both signs hold at every duty, which is what
makes this a statement about the network rather than about a chosen operating point. The low band's ceiling
peaks **≥ 20×** later than the short band's (120 ms vs 1 ms), because C45 sits behind R105 and C38 sits behind
nothing.

**Against #396's short-band gap (+39.79 dB):** even at the ceiling's most favourable duty, the short band's
advantage is **8.26 dB**, leaving a **31.5 dB** margin against the +20 dB bound this chain uses throughout —
i.e. the collector-supply side cannot close the gap either. Combined with step 11's signal-side term (+4.97 dB
chain / +8.71 dB upper bound), **no element inside the swing VCA section reaches the balance's short-band gap.**

### 3c. The DECAY knob is not where the reference put it

| | reference (superseded 2026-09-30) | this read |
|---|---|---|
| VR2 vs R93 | parallel | **series** (R93 to VR2's pin 3, wiper strapped to pin 3) |
| timing resistance | 0 – 380 kΩ | **470 kΩ – 2.47 MΩ, never zero** |
| C41's tail | "up to ≈0.38 s" | **0.47 s – 2.47 s** |

Read the reference's way, DECAY at minimum shorts C41 to ground and the low band's reservoir never charges
(`envelope_peaks` on `VR2_PARALLEL_NOT_SERIES` gives **< 3 V** where the correct reading gives **12.8 V**) — not
a design a synth would ship. Read the scan's way, the predicted span (τ at DECAY max / τ at DECAY min) is:

| | predicted span | external span | bound (×1.6) |
|---|---:|---:|---|
| low band | **4.04** | 3.20 (Fischer Ln EDT10) | 2.00 – 5.12 |
| DECAY band | **5.23** | 4.36 (Fischer late T20) | 2.73 – 6.98 |
| longer of the two (chart proxy) | **4.04** | 3.43 (Roland's own chart) | 2.14 – 5.49 |
| short band | **1.00** | — (not on VR2 at all) | asserted and verified blind, tol 0.01 |

All three land inside the bound, at every duty (0.25–1.0) and every beta (100–400) in the bracket — 9
combinations per span, all passing (`test_the_span_known_answer_survives_both_unknowns`). The bound itself is
derived, not chosen: it is the three external sources' own mutual disagreement (max/min = 1.36), rounded up to
1.6, and a control (`test_the_span_bound_is_the_external_sources_own_disagreement`) pins that derivation so the
bound cannot be quietly widened to admit a number.

**And the relocation matters**, because it is exactly what the Fischer recordings already said and reference
§10 denied: `../README.md` §1 records "the low band's own decay tracks DECAY... the recordings contradict
§10['s 'DECAY changes only the middle band's RC']." VR2/C41 reaches the low band directly (through Q20 and
R105) and the DECAY band only through R92/R89/R91 — so the low band's span (4.04) is the larger of the two
observable effects, matching the direction the recordings show.

## 4. Why the read can be trusted

**Known answer 1 (reservoir equality): the bound is derived from the transient's own reported currents, not
assumed.** The two orders of magnitude of spread between D6's ~0.9 mA and D8's ~0.008 mA is what makes 0.25 V a
real bound rather than an arbitrary tolerance, and `MISMATCHED_D8` demonstrates the bound can fail.

**Known answer 2 (DECAY span): three sources outside this repository's own model, stated before any derived
number.**
- Roland's own chart, SN p.14: 350 → 1200 ms, span 3.43.
- Fischer, low band (Ln EDT10, measured off the 808 recordings by `tools/cymbal_bands.py` in an earlier,
  independent step): 400 → 1280 ms, span 3.20.
- Fischer, high bands (late T20, same tool, same independence): 250 → 1090 ms, span 4.36.

None of the three knew this schematic read existed. Agreement inside a bound derived from their own mutual
spread (not fitted to make this module pass) is the qualifying check.

**Known answer 3 (the closed-form check): the short band's own eigenvalues.** C38 touches exactly two
resistors and nothing else in the cymbal reaches it, so a reader can write its three-node modes by hand; the
MNA transient's tail reproduces the slowest one (161.8 ms) to 3 %.

### Controls, and what each one is for

Six new injected defects, every one a component value or a wire, never a bound:

| defect | turns red |
|---|---|
| `VR2_PARALLEL_NOT_SERIES` (the reference's own former reading) | `env-decay-band-ceiling-below-the-low-band`, `env-decay-knob-span-low` |
| `WRONG_C41` (1 µF → 0.1 µF) | `env-decay-band-ceiling-below-the-low-band`, `env-decay-knob-span-chart`, `env-decay-knob-span-decay` |
| `FAST_SMOOTHING` (collapses R88/R105 to 100 Ω, so all three bands look like the short band) | `env-ceilings-peak-at-different-times`, `env-collector-peaks-are-not-equal` |
| `NO_SMOOTHING_CAPS` (C39/C45/C37 → 1 pF) | `env-ceilings-peak-at-different-times`, `env-decay-band-ceiling-below-the-low-band` |
| `MISMATCHED_D8` (D8's V_f +0.4 V) | `env-reservoirs-equal-at-the-peak` |
| `R89_OPEN` (10 k → 10 MΩ) | `env-decay-band-ceiling-below-the-low-band` |

`SWAP_BP_CAPS`'s existing blindness assertion (step 11) is widened rather than left as-is: a filter defect
cannot move an envelope property either, since the two sections share no component, so it is now checked
against all nine envelope properties as well as step 11's own. Three new blindness assertions are
collector-supply-specific: `VR2_PARALLEL_NOT_SERIES` must leave the **short** band's span exactly at 1.0 (its
reservoir is not on VR2 at all), and `VR2_PARALLEL_NOT_SERIES`, `WRONG_C41` and `FAST_SMOOTHING` must each
leave step 11's filter and drive properties untouched — a wire or component change inside the envelope section
cannot move a band-pass.

## 5. What this step does and does not settle

**Settled:** the swing VCA's **upper** edge — the ceiling the collector node reaches when the stage cuts off,
i.e. the level a band can never exceed. Both halves of "equal at the peak, different only in decay" are now
measured rather than assumed, and one half (equal at the collector) is refuted.

**Not settled, named rather than left implicit:**
- **The swing VCA's lower edge.** Reference §1.3's "clips wildly between the envelope voltage and a lower edge
  that is itself a function of the envelope" needs the transistor's large-signal model and the base drive
  amplitude; neither is read here.
- **The conduction duty**, bracketed (0.25–1.0) rather than chosen, and every number reported across it.
- **The two high bands' own high-pass input loading.** Hh1's input network is read (step 11), so the low
  band's loaded collector impedance is exact; Hh2's and Hh3's loading is not modelled here either, for the same
  reason step 11 left it as a bound rather than an estimate.

## 6. The correction to `docs/tr808-reference.md`

§10's controls paragraph and its own VR2/R93/C41 table row both said "DECAY changes only the middle band's RC"
and "2 MΩ ‖ R93 470 kΩ ... up to ≈0.38 s". Both are corrected in place, with the superseded wording preserved
inside a `was "..."` quotation rather than silently overwritten
(`test_the_reference_section_10_row_states_the_series_pair` gates the row; the paragraph is prose and is not
separately gated beyond the module's own numbers matching it). §1.3's own paragraph gains the collector-supply
finding, replacing the "not resolved here" sentence step 11 left behind.

## 7. Wrong-then-right rate for this step

| # | what was wrong | what caught it |
|---|---|---|
| 1 | the first pass fitted the swing's 1/e time constant on the raw **node** voltage rather than the swing above `V_CLAMP`. A trace that settles at the clamp reports tens of seconds for a network whose real tail is ~140 ms | `env_tau_ms`'s own docstring now states this; the closed-form short-band eigenvalue (161.8 ms) caught the discrepancy before the span known answer was even run |
| 2 | the DECAY span control was written against the reference's parallel reading before the series correction was confirmed, and passed only because DECAY-at-minimum's dead short happened to still produce *some* finite span | re-running `test_the_reference_says_parallel_and_the_scan_says_series` after the correction, which asserts the mechanism (reservoir voltage at DECAY minimum) rather than only the span number |

## 8. What this step forbids, and what is left in the chain

**Forbidden to the next step: treating the collector supply as a free per-band level.** It is not free —
the reservoirs are equal by construction and the collector ceilings are set entirely by the printed smoothing
networks (R87/C37, R88/C39/divider, R105/C45), not by anything a candidate could choose.

**Also forbidden: quoting the DECAY span against reference §10's superseded row.** `docs/tr808-reference.md`
now carries the series correction; any future citation of "up to ≈0.38 s" or "changes only the middle band's
RC" is citing the pre-2026-09-30 text.

With this step, **the cymbal's VCA section is fully read on both the signal side (step 11) and the collector
supply side (this step), and neither closes #396's short-band gap.** The balance's own missing factor is
therefore outside the VCA section entirely — consistent with step 8's clipper sweep (which could not reach the
808's joint box at any drive) and step 10's rendered finding (a linear mix of the three bands misses the
render's own curve because the swing VCAs clip, but no per-band VCA level fixes it).

Still open and unchanged by this step: #396 (the inter-band balance itself — both of its `schematic-vr4` and
`vca-drive` preconditions are now measured and neither is what was missing), #413, #400's tail question, and
the listening pack at other settings.

## Files

- `vca-supply.json` — the record: the envelope netlist, the reservoir/collector peaks by duty and VR2 position,
  the DECAY-span computation by beta, every property, the reference correction, and the gate.
- `../../../../tools/cymbal_vca_drive.py` — extended, not replaced: `envelope_transient` / `envelope_peaks` /
  `envelope_peak_spread` / `envelope_spans` / `envelope_properties` / `supply_record`, plus the two new crops
  `env-q19` / `env-q20`. `--json-supply <path>` writes this step's own artifact.
- `../../../../tools/test_cymbal_vca_drive.py` — 19 new test functions for this step (35 collected cases after
  parametrization, of the module's 90 total): 3 known answers external to this repository's model
  (reservoir-spread bound, DECAY span vs Roland's chart and the Fischer recordings, the closed-form short-band
  eigenvalue), 6 injected defects, 3 new blindness assertions (`VR2_PARALLEL_NOT_SERIES`, `WRONG_C41`,
  `FAST_SMOOTHING` — plus `SWAP_BP_CAPS`'s existing blindness list widened to cover this step's own
  properties, since the envelope section shares no component with either band-pass), and the round-trip /
  refusal paths.
- Reproduce: `python3 tools/cymbal_vca_drive.py --check` (gates both steps 11 and 12 together — they share one
  module and one gate). Write this step's own record with
  `python3 tools/cymbal_vca_drive.py --json-supply docs/scorecard/cymbal-369/vca-supply/vca-supply.json`.
  Add `--require-source <sn.pdf>` on a host that has the scan; fetch it as `../vca-drive/README.md` §1 describes.
