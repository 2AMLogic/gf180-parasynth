# #369 step 4: the tone stage, read off W14b Figure 9 — and it is the missing tilt

Step 3 (`../candidate2/README.md`) ended having eliminated every suspect but one. It rebuilt the candidate on
Hh2, Hh3 and the LEVEL stage read from W14b Figures 4 and 10, **and the candidate still overcorrected at CY5025**
— 15.3 dB short at 1 kHz and 8.7 dB long at 20 kHz. Its §4 named the next thing to measure, and #390 carried the
hypothesis, stated before the measurement:

> the tone stage is a *passive RC* network, so its paths are low-pass in character, and a low-pass path from the
> low band to the output would partially cancel the LEVEL buffer's rising slope. That is the right shape and
> roughly the right size for the missing 15 dB.

**That hypothesis is confirmed in shape and in size.** No candidate is built here — the same figure that confirms
it also shows why one cannot yet be built honestly, which is section 4.

## 1. What Figure 9 is, and what the instrument had to learn

W14b §10 gives the tone stage as three *fifth-order* transfer functions `Ht1 = Vtone/Vh1`, `Ht2 = Vtone/Vh2`,
`Ht3 = Vtone/Vh3`, declines to print their coefficients ("far too lengthy to print in this work or to provide
much insight by visual inspection") and points at a companion site that returns 404. But **Figure 9 plots all
three families** for tone control `k ∈ [0.01, 1.0]`, marking the `k = 1.0` member of each with an asterisk, and
it is a vector XObject in the same PDF as Figure 4. `tools/werner_fig9.py` reads it out.

One extension was needed and it is in the shared file: `werner_fig4.calibrate()` assumed a single axes box, one
tick-label set, at least three y labels and a 10 dB tick step. Figure 9 is three stacked sub-plots with 20, 1 and
2 dB steps and only two y labels on two of them. The new `calibrate(..., box=…)` takes the box, windows the label
search to it, reads the dB step off the labels, and accepts two. **Figure 4's and Figure 10's own numbers are
bit-unchanged** — that is the point of putting it there rather than in a copy.

## 2. The answer

Only the `k = 1.0` members are identified (section 3), so these are the tone stage with the TONE knob fully open.

| | plotted range | window height | 2-pole band-pass fit | rms |
|---|---|---|---|---|
| **Ht1** (low band → out) | 121 – 564 Hz | 1.7 dB | f0 **274.4 Hz**, Q **0.383**, peak **−26.44 dB** | 0.002 dB |
| **Ht2** (DECAY band → out) | 562 – 1640 Hz | 1.0 dB | f0 **972.0 Hz**, Q **0.450**, peak **−15.12 dB** | 0.004 dB |
| **Ht3** (short band → out) | **20 Hz – 20 kHz** | **24.1 dB** | f0 **783.3 Hz**, Q **0.409**, peak **−22.09 dB** | 0.050 dB |

Every one of Figure 9's **fifteen** curves is a 2-pole band-pass to ≤ 0.05 dB rms over its plotted range, and
every one reads **Q < 0.5** — two *real* poles, i.e. one RC high-pass cascaded with one RC low-pass, which is what
a passive network builds. Ht3, the one plotted across the whole axis, is a 5th-order function that is
indistinguishable from a 2-pole section over three decades: an extra pole anywhere below 50 kHz raises its
residual by 10–27×.

### The headline: the tone stage cancels the LEVEL stage

| across 2 – 20 kHz | |
|---|---|
| LEVEL buffer (W14b Fig. 10, measured in step 3) | **+16.6 dB** |
| Ht3 at k = 1.0 (W14b Fig. 9, measured here) | **−17.7 dB** |
| net | **−1.1 dB** |

The model has the first of those and **none** of the second. So the model's cymbal is tilted up by about 17 dB
across its own band relative to the machine's, from a stage that was simply absent.

### It is the right size for the defect, band by band

Each path's tilt relative to its own value at 1 kHz, on `tools/cymbal_bands.py`'s 1/3-octave centres
(`tools/werner_fig9.py --fit`), against candidate 2's measured residual at CY5025 (ours − 808, 0–50 ms):

| kHz | 1.0 | 1.26 | 1.59 | 2.0 | 2.5 | 3.2 | 4.0 | 5.0 | 6.3 | 8.0 | 10 | 12.7 | 16 | 20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Ht1** | 0.0 | −1.5 | −3.2 | −5.0 | −6.8 | −8.9 | −10.8 | −12.7 | −14.7 | −16.7 | −18.7 | −20.7 | −22.7 | −24.7 |
| **Ht2** | 0.0 | −0.2 | −0.8 | −1.8 | −2.9 | −4.5 | −6.1 | −7.8 | −9.6 | −11.6 | −13.4 | −15.5 | −17.4 | −19.4 |
| **Ht3** | 0.0 | −0.5 | −1.3 | −2.3 | −3.6 | −5.2 | −6.8 | −8.6 | −10.4 | −12.4 | −14.3 | −16.3 | −18.3 | −20.2 |
| *candidate 2 residual* | *−15.3* | *−13.8* | *−8.0* | *−4.5* | *+2.8* | *−0.9* | *−1.4* | *−7.9* | *−5.9* | *+0.2* | *−3.5* | *+2.2* | *+8.6* | *+8.7* |

The residual climbs **+24.0 dB** from 1 kHz to 20 kHz. The three tone paths fall **19.4 to 24.7 dB** over the same
span. Same sign, same order, and the spread between the three paths is smaller than the defect. Nothing else in
the measured chain has that shape.

**That is a prediction, not a result.** It is arithmetic on transfer functions, and the cymbal's VCAs clip, so it
must be confirmed by a render before anything is claimed. The render is the next step and it is blocked by
section 4.

## 3. Two things the figure gets wrong, and how each was resolved

### The asterisk does not mark the top curve

W14b's caption: "*k = 1.0 responses are marked with an asterisk (∗)*". Taking "the marked one is the topmost" —
the obvious shortcut — is **right once and wrong twice**:

| | k = 1.0 is | peak at k = 1.0 | topmost member's peak |
|---|---|---|---|
| Ht3 | member **1** of 5 | −22.09 dB | −22.09 dB |
| Ht2 | member **5** of 5 | −15.12 dB | −14.46 dB |
| Ht1 | member **5** of 5 | −26.44 dB | −25.55 dB |

The identification is not a judgement call. The asterisk is a 36 pt glyph, so the offset from its `Td` origin to
the peak of the curve it marks is a property of the font and must be **the same vector in all three sub-plots**.
Exactly one of the 125 assignments satisfies that: offset (+7.67, +19.58) pt, agreeing to **1.32 pt**, with the
next best of the 125 at **17.17 pt**. Two spare constraints, not a guess. `MARKER_AGREE_PT = 4.0` sits between
them and is marginal in neither direction.

Getting this wrong would not merely have put Ht1 and Ht2 about 1 dB too loud; it would have said the tone control
makes the first two bands *louder* as it opens, when it makes them quieter.

### The legend prints `Ht3` twice and `Ht1` never

The three legend labels read, top to bottom, **`Ht3`, `Ht2`, `Ht3`**. One of them is wrong and the figure cannot
say which. The tool names the sub-plots from **W14b §10's own prose** instead:

> The primary effect of the tone control is to change the amount of attenuation in the third band. However, the
> center frequencies of all bands and the attenuation of the first and second bands are also somewhat affected.

Measured spread of peak level across each family: **28.7 dB**, 0.7 dB, 0.9 dB, top to bottom. Exactly one band is
moved a lot, and it is the top one, so the top one is the third band and its legend is right. `Ht2`'s legend is
unambiguous. The bottom is `Ht1` by elimination, and the tool prints that it overrode a label rather than
silently renumbering.

Two independent corroborations that the **bottom** sub-plot is the one with bad labels: it titles its x axis
"magnitude (hertz)" where it should read "frequency (hertz)", and its y axis "amplitude (dB)" where the other two
correctly read "magnitude (dB)". Three label errors in one sub-plot, not one error somewhere in three.

## 4. Why no candidate is built here

Figure 9 plots **Ht1 on a 4 dB tall axis and Ht2 on a 3 dB tall axis**. Both curves leave the plot long before the
cymbal's own band: Ht1 at 564 Hz, Ht2 at 1.64 kHz, against band-passes at 3.45 kHz and 7.1 kHz. Everything quoted
for them up there is an **extrapolation**, and `tools/werner_fig9.py` says how far it can be wrong. For each
candidate extra real pole or zero the window cannot distinguish from a plain 2-pole section (residual within 3×):

| at 7.1 kHz | nominal | bound | width |
|---|---|---|---|
| Ht1 | −46.4 dB | −54.5 … −36.5 | **18.0 dB** |
| Ht2 | −25.7 dB | −31.1 … −22.0 | **9.1 dB** |
| Ht3 | −33.7 dB | *measured, not extrapolated* | — |

The **inter-band balance** is exactly what those two numbers decide, so Figure 9 does not determine it. A
candidate that applied the nominal values would be asserting an 18 dB-uncertain number as a circuit fact — the
thing #369 forbids.

A second, independent reason to distrust reading the fits as circuit values: three transfer functions of one
network to one output node share a denominator, so they share poles. The three window fits do not.

| | window fit | its real poles |
|---|---|---|
| Ht1 | f0 274.4 Hz, Q 0.383 | 127.7 and 589.5 Hz |
| Ht2 | f0 972.0 Hz, Q 0.450 | 609.9 and 1549.0 Hz |
| Ht3 | f0 783.3 Hz, Q 0.409 | 406.0 and 1511.2 Hz |

At most one of those can be the network's in-band pair. That is not a contradiction — a 2-pole fit over a 3 dB
window is a local shape, not a pole location — but it is the proof that the Ht1 and Ht2 rows are shape
descriptions and must carry the bound.

**What the extrapolation does have, which is the one known answer available.** Ht3's sub-plot is 36 dB tall, so
the whole procedure can be run on a curve whose answer is known: throw away all but its top 3 dB — the same view
Figure 9 gives of Ht1 and Ht2 — fit, extrapolate, and compare against the part thrown away.

| fitted from Ht3's top 3 dB (283 – 2124 Hz, 62 of its points) | 3.45 kHz | 7.1 kHz |
|---|---|---|
| extrapolated | −28.27 | −33.99 |
| plotted (truth) | −28.06 | −33.65 |
| error | **−0.21** | **−0.34** |

(Off the committed, 6× decimated evidence, which is what `--check` runs on. The undecimated read of the paper
gives +0.18 and +0.32 dB — same size, opposite sign, both well inside the 0.5 dB gate.)

So the *procedure* works to about a third of a dB — **when the true section is a 2-pole band-pass.** A 3 dB window
cannot establish that it is, which is precisely what the 18 dB bound measures. Both facts are true and both are
reported.

### What would unblock it

Three routes, in the order they should be tried:

1. **The schematic.** The reference has `TONE (VR4 20 kΩ)` and nothing else of the tone network; SN p.13's
   resistor and capacitor values around VR4 would give the network by nodal analysis, the same way Hh1 came from
   R124/R127/C48/C59. That is the only route that yields circuit values rather than fitted ones.
2. **The shared denominator as a constraint.** Ht3 is measured in full and its two in-band poles are known.
   Fitting Ht1's and Ht2's windows with those poles *held fixed* and only the numerators free is a much smaller
   problem than the free fits above, and its residual is a test of the shared-denominator assumption rather than
   an assumption itself.
3. **Only the tilt, and not the balance.** The tilt (section 2's table) and the inter-band levels are separable:
   a candidate could apply each band's tone-stage *shape* normalised to 0 dB at 1 kHz and leave the levels on the
   current rule. That answers the one question this measurement does settle, and defers the one it does not.
   It is the cheapest confirmation of the headline and probably the right next step.

**Route 1 was tried (#390, 2026-09-28) and it worked — see section 7.** Route 2 was not needed as a result;
route 3 was already absorbed into #396 before #390 started (see that issue's "Related Open Work").

## 5. What is now closed in the reference

- §10 gains the tone stage: three paths, each a 2-pole band-pass with real poles, with Ht3 measured in full and
  Ht1/Ht2 given with their extrapolation bounds.
- §18's open item *"The tone stage is still open, and is now the cymbal's largest unmodelled block"* becomes
  **partly closed**: the shape is measured and is the missing tilt; the inter-band balance is not, and is
  restated as the narrower open item it now is.
- §10's "what to implement" gains the tilt, and says plainly that the LEVEL stage must not be implemented without
  it — the two are the same magnitude and opposite in sign, and a model with one and not the other is further
  from the machine than a model with neither.

**Superseded by section 7 below (#390): the inter-band balance is no longer open.** §18 now records it as
resolved, not bounded.

## 7. #390: the schematic resolves the balance route 1 could only be hoped for

Route 1 above was attempted by reading SN p.13's voicing board (VG 3116-140) directly: the resistors and
capacitors around VR4 ("CY TONE", printed as 20 kΩ(B), linear taper) and VR6/IC6 ("CY LEVEL"). Two op-amp
outputs (the two 7.1 kHz-band Sallen-Keys, Hh2 and Hh3) and Q25's emitter (Hh1, already used for the R124/R127/
C48/C59 derivation) feed a 4-node passive network: C55/R112/R119 forms one rail, C56/R120 the other, with Q25's
own C58/R123/C57/R121 stage pre-filtering Hh1 before it joins the second rail; VR4 is a balanced bridging
attenuator (its wiper grounded, not a simple divider) splitting attenuation between the two rails; the mix node
is loaded by C90 into IC6's virtual ground.

**The result matches Figure 9 without needing a fudge factor.** Fitting only the pot's wiper fraction (no
per-path gain offset) against Figure 9's own digitised k = 1.0 curves gives a *single* value that fits **all
three families at once** — 707 points across three independently-plotted windows — to 0.001–0.013 dB rms,
including the fully-measured Ht3 curve across three decades (not merely its narrow local window). The network is
independently 5th order (5 capacitors, no cap-only loop — matches W14b's own word, derived without ever seeing
W14b's coefficients) and its three transfer functions share **exactly one pole set**
(128/509/681/1636/4192 Hz): "one network, one denominator" is now an algebraic fact, not an argument from
plausibility.

**Two controls rule out coincidence.** Swapping which op-amp rail is Hh2 vs. Hh3, or moving Hh1's pre-filter
onto the other rail, degrades the fit 20–100×. A wrong circuit does not accidentally land this close.

**The resolved balance**, read directly off the solved network at the cymbal's own corners (no extrapolation
needed — the network covers the whole audio band), relative to Ht3:

| | 3.45 kHz | 7.1 kHz |
|---|---|---|
| Ht2 − Ht3 | +7.61 dB | +6.98 dB |
| Ht1 − Ht3 | −13.93 dB | −18.11 dB |

Both Ht1 and Ht2's absolute values also fall inside Figure 9's own (much wider) extrapolation bounds at 7.1 kHz
— −51.8 dB inside [−54.5, −36.5] for Ht1, −26.7 dB inside [−31.1, −22.0] for Ht2 — an independent cross-check
the schematic route did not have to pass to be usable, and did.

Route 2 (the shared-denominator constrained fit) turned out not to be needed: route 1 resolved the balance
outright rather than merely narrowing the bound, so there is nothing left for a constrained refit to add. This
also means the "at most one of Ht1/Ht2/Ht3's window-fit pole pairs is the network's real pair" caveat in
section 2 is now explained rather than merely observed: the true network has *five* poles, and each narrow
window's 2-pole fit is a local approximation dominated by whichever two of those five sit nearest that window.

**What this does and does not change for #396.** #396 was scoped to apply the tone stage's *tilt* only, levels
untouched, and that scope is unaffected. The balance above is available for whichever candidate revision picks
up the levels — #390 does not build that candidate (out of scope by its own acceptance criteria) and does not
alter `model/cymbal_candidate.py`.

### The read is pinned to a source someone else can re-open

"SN p.13" is a citation, not evidence: nothing in the first pass let a reader check that the twelve component
values were read off the page correctly, or even off the same printing. So the scan is now pinned by **SHA-256**
with its page number and the two crop boxes (400 dpi and 500 dpi, coordinates recorded) that every value came
from. `tools/tone_stage_schematic.py --verify-source <sn.pdf>` re-renders exactly those crops after checking the
hash, and **REFUSES with exit 3** on a missing file or a mismatch rather than answering from a different
printing — a plausible-looking page 13 from another revision is precisely the failure that would not announce
itself. The PDF is a ~6 MB third-party download and is deliberately **not** a test dependency: the tests cover
the refusal paths, and `make verify` still needs no network.

Re-reading against that crop (2026-09-28) confirmed all twelve values, VR4's wiper-to-ground wiring, and Q25's
emitter as Ht1's source — and settled one thing the fit could only choose:

**The rail assignment is confirmed by the schematic, independently of the fit.** The two controls above show
that a swapped assignment fits 20–100× worse, which is evidence *from Figure 9*. The scan gives it a second,
unrelated derivation: the top rail's op-amp has a **three**-capacitor input network (C49 .0033, C53 .001,
C54 .001) and the bottom rail's has **two** (C51 .001, C52 .001), which is exactly the 3rd-order/2nd-order split
already recorded for Hh3/Hh2 in `docs/tr808-reference.md` §10 — and the bottom op-amp is the one wired to VR2
**"CY DECAY"**, which is Hh2's band by definition. Two independent routes, same answer.

### A test that could not run looked exactly like a test that passed

The claim "five capacitors, no cap-only loop, therefore fifth-order with one shared denominator" is the most
structural thing in this derivation — it is what makes each narrow window's 2-pole fit a *local approximation of
a known object* rather than a competing model. It was tested only under `sympy`, and **no workflow in this
repository installs `sympy`** (they install `numpy scipy pytest`, plus `pyyaml`). The test therefore *skipped* in
CI, where a skip is reported beside passes and is read as one.

It is now derived a second way, with numpy/scipy only, so it runs wherever the suite runs:

- the network is rebuilt as a plain `(G + sC)` pencil over **seven** nodes, splitting each series R-C branch at
  its own internal node, rather than folding it into `Z = R + 1/(sC)` by hand as `solve_vtone` does;
- "fifth-order" becomes a **count** of finite generalised eigenvalues of `(-G, C)`, not an assertion about a
  polynomial's degree: 128.31 / 509.09 / 681.37 / 1635.75 / 4191.51 Hz;
- "one network, one denominator" becomes **structural**: neither `G` nor `C` is a function of which source is
  driven, so all three paths share the pencil and hence the poles by construction;
- the two formulations agree to **~1e-14 dB**, which turns `solve_vtone`'s hand elimination from an assumed step
  into a checked one. Injecting an R119/R129 swap into one of them alone diverges by **2.62 dB**, so that
  agreement test is not vacuous.

The `sympy` test is kept as a third, symbolic witness and still skips where `sympy` is absent — it is now
corroboration rather than the only thing standing behind the claim.

Evidence and code: `tools/tone_stage_schematic.py`, `tools/test_tone_stage_schematic.py`. Component values and
the full nodal-analysis derivation are documented in the module's own docstring and in
`docs/tr808-reference.md` §10/§18.

## 6. Wrong-then-right, four times

1. **Every curve in the top sub-plot stopped at 13.5 kHz.** The containment test that selects a sub-plot's own
   polylines used a 1e-6 pt tolerance; MATLAB's exporter rounds to 1/60 pt in the 0.1-scaled space it emits, so a
   point exactly on the right-hand axis reads 1e-5 pt outside it. The last 0.17 of a decade of all five curves —
   including the 20 kHz end of the one number this step is about — was silently dropped. Nothing in the figure
   looked wrong; the curves simply ended. `werner_fig4.BOX_TOL = 0.05` and a comment that says why.
2. **The first `--check` crashed instead of refusing.** The Ht1/Ht3 swap control put a 121–564 Hz curve where the
   extrapolation control expected a full-range one, and `max()` over an empty list raised `ValueError`. A crash
   in a gate is not a verdict; `truncation_control` now REFUSES with the reason, and the control passes for the
   right reason rather than by exception.
3. **The schematic route shipped with an uncheckable citation.** The twelve component values were tagged
   "SN p.13" and nothing more, so no reader could confirm they had been read correctly, or off the same printing.
   The values turned out to be right — re-reading them against a hash-pinned scan changed none of them — but
   *that they were right was luck from the reader's point of view*, because there was no way to tell. Pinning the
   SHA-256, page and crop boxes is the fix; the re-read also produced a genuinely new result (the schematic
   confirms the rail assignment independently of the fit), which is the usual pattern: the check that was skipped
   because "the answer is already known" is the one that had something left to say.
4. **The load-bearing structural test was gated on a package CI does not install.** "Fifth-order, one shared
   denominator" was asserted only under `sympy`, so in CI it *skipped*. A skip sits in the report next to passes
   and is read as one, which makes a skipped check of a central claim worse than an absent one — the same shape
   as the unsatisfiable-gate problem `CLAUDE.md` warns about, arriving from the opposite direction. Now derived
   with numpy/scipy as an eigenvalue count, cross-checked against a second node formulation. **Worth assuming
   this recurs elsewhere in this suite**: the general check is "does this test run in CI, or only here?".

## Files

- `tools/werner_fig9.py` — the digitiser. `--check` is the gate, `--fit` the transfer functions and the tilt
  table, `--from-pdf --json` re-derives the evidence.
- `tools/test_werner_fig9.py` — 29 test functions (31 cases), **14 of them controls that must fail**. The five
  that run the gate each trip a different item: `passive`, `one-x`, `marker`, `prose`, `extrap`.
- `../werner-fig9.json` — the digitised curves, so the gate runs with no paper and no network.
- `tools/werner_fig4.py` — `calibrate()` now takes a box. Figure 4's and Figure 10's numbers are unchanged.
- `tools/tone_stage_schematic.py` (#390) — the SN p.13 nodal analysis. `--check` is the gate, `--report` the
  resolved balance.
- `tools/test_tone_stage_schematic.py` (#390) — known-answer tests against Figure 9's digitised curves, plus
  wrong-rail-assignment and component-perturbation controls.
