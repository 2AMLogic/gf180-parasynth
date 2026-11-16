# Milestones

Six, in order. Each names its **evidence**, its **blockers**, and what it does
**not** claim. No aggregate "quality" number — progress is cells and milestones
moving, not a score going up.

**Coverage is reported alongside results, always.** *"20 passing, 4 failing,
6 without verdicts"* is honest; *"83 % passing"* conceals the missing
verification. A milestone needs **both** sufficient valid coverage **and**
passing critical requirements, or the cheapest route to green is to make rows
unmeasurable.

---

## 1. Reference rig qualified · **IN PROGRESS**

Every comparison downstream is worthless until the rig is known to produce what
it claims. We have already had three instances of it not doing so.

| | |
|---|---|
| ✅ | Diva identified as an **unlicensed demo** — 20 broadband clicks in 360 s — and every Diva number withdrawn |
| ✅ | Parameter **name check at construction**, which caught Surge renaming 259–267 by oscillator type (265 is "Unison Voices" or **"High Cut"**) |
| ✅ | **Run-to-run variance** measured: ours 0.000 pp, Surge 0.000, Mini V3 0.003 |
| ✅ | **Surge works as an FX path** — flat to 0.07 dB above 100 Hz, so Model 72 is not needed |
| ✅ | **Model D renders via REAPER** (`rms 0.346`, 130.14 Hz for note 60) after failing headlessly |
| ✅ | **Waveform mapping settled by measurement** — `model/reference_rigs.py` asked Surge for a saw and was handed a 50 % pulse; the derived mapping and the controls that reject a wrong setup are `docs/surge-waveform-mapping.txt` |
| ⬜ | Qualification as an **automated preflight**, with deliberately-wrong setups proving it rejects — #77, which tracked this, was **closed as not planned** on 2026-09-26, so the row is open and unowned rather than open and tracked |
| ⬜ | Plugin state saved **with the audio**, not a patch name |
<!-- claim: grep="SETTLED BY MEASUREMENT" in=docs/surge-waveform-mapping.txt issue=789 note="row 1: the corrected Surge mapping and its reject controls" -->
<!-- claim: grep="predates #87's corrected Surge waveform mapping" in=docs/saw-alias-2x-results.json issue=789 note="the paragraph below: the pre-#87 rows are withdrawn, not re-rendered" -->

**Does not claim:** that our oscillator comparison figures are trustworthy. The
mapping is fixed; the **re-render is not**. Studies that predate the correction
are withdrawn rather than redone — `docs/saw-alias-2x-results.json` carries its
Surge rows as "the withdrawn flat -60 dB harness artefact".

## 2. Analyser ground truth · **DONE, and it keeps earning**

| | |
|---|---|
| ✅ | Every estimator validated against a **closed-form** signal; six estimator bugs found the day it was written |
| ✅ | Injected defects required to move each property, with a **blindness matrix** showing which properties *cannot* see each defect |
| ✅ | That matrix found a real hole: a **uniform 30 % cutoff error is invisible to a non-uniformity metric by construction** |

**Does not claim:** that an estimator validated on synthetic signals behaves on
real recordings. Five measurements in one session were wrong before they were
right; all five were caught by a control rather than by inspection.

## 3. Complete instrument integrated · **NOT DONE**

| | |
|---|---|
| ✅ | Complete Minimoog voice merged — noise, oscillator-3 modulation, full waveform set; `rtl-sketch/verify_voice.py --set full` is a named nightly job against `model/voice_fx.py` |
| ✅ | Complete 808 built — **all 16 sounds on 11 circuits, 102 acceptance tests under STRICT** (`docs/drum-verification.md` §10.7) |
| ✅ | The 808 is **on `main`** — `drum_kit.v`, `drum_dp.v`, `drum_regs.v`, `modal_dp.v` and their bench are in the tree, not on a branch |
| ✅ | **`synth_top.v` instantiates the real engine** — `drum_regs` + `drum_kit`, and no `drum_section_placeholder` is left in the RTL (contract 17.23 closed) |
| ✅ | **RTL reproduces the complete kit bit-exactly** — `verify_drums.py` compares `drum_kit.v` against `model/drums_fx.py` with no tolerance over the Appendix G kit's eleven circuits, and 9 of 9 injected defects plus write jitter turn it red. It is a named nightly job, so `main` is re-checked nightly rather than graded from this line |
| ⬜ | Playable access: the FPGA needs its SPI host, **including the timed writes the kick attack and tom pitch movement depend on** |
<!-- claim: grep="rtl-sketch/verify_voice\.py --set full" in=.github/workflows/nightly.yml issue=789 note="row 1: the voice's full-set bench runs nightly" -->
<!-- claim: grep="^\| `model/test_808_acceptance\.py`, `TR808_STRICT=1` \| \*\*102 passed\*\*" in=docs/drum-verification.md issue=789 note="row 2: the 102 STRICT acceptance tests" -->
<!-- claim: grep="^module drum_kit" in=rtl-sketch/drum_kit.v issue=789 note="row 3: the engine is a file in this tree" -->
<!-- claim: grep="^\s*drum_kit #\(" in=rtl-sketch/synth_top.v issue=789 note="row 4: the shipping top level instantiates it" -->
<!-- claim: absent="^\s*(module\s+)?drum_section_placeholder" in=rtl-sketch/*.v,fpga/rtl/*.v issue=789 note="row 4: no placeholder module or instantiation is left. The pattern is anchored so the header COMMENT in synth_top.v saying the placeholder is gone does not satisfy it -- an unanchored absent= on this string is STALE on this tree, which is the control for this marker" -->
<!-- claim: grep="rtl-sketch/verify_drums\.py" in=.github/workflows/nightly.yml issue=789 note="row 5: the kit's bit-exactness bench is a named nightly job" -->
<!-- claim: grep="^\| `verify_drums` negative controls \| 9 of 9 caught" in=docs/drum-verification.md issue=789 note="row 5: the bench is shown to be able to fail" -->
<!-- claim: grep="3 tom pitch drops" in=fpga/reports/control_path.txt issue=789 note="row 6: the timed writes the open row names are already bit-exact IN SIMULATION at the chip's pins" -->

**Branch-local success and integrated completion are separate status fields**,
and conflating them is how "the 808 is complete" became misleading.

**What keeps the last row open, and what does not.** The host half exists and is
bit-exact *in simulation* at the chip's pins: `fpga/spi_host.py` drives 288
DR 0007 revision 2 transactions — 11 strikes, 3 BD attack windows, 3 tom pitch
drops — and every one of 8 378 decoded I2S periods matches
`model/synth_top_model.py` with no tolerance (`fpga/reports/control_path.txt`).
What this pass did **not** trace is that same path on real hardware, so the row
stays open rather than being graded from the simulation.

## 4. Acceptance cases measured · **DESIGNED, POPULATED IN PART**

Scorecard design in #75: five cell states, per-sound and per-property, never
aggregated, every row naming a **specific** target.

**It is no longer empty.** The case list is `docs/scorecard/cases.csv` and
per-case results are committed under `docs/scorecard/results/`. **How many cases
have a valid measurement is deliberately not restated here** — it is the
generated board in `README.md`, which moves on its own; a number copied into
this line would be the next claim to outlive its evidence.
<!-- claim: grep="^case_id,family,subject,batch,split" in=docs/scorecard/cases.csv issue=789 note="the case list exists with its header contract" -->
<!-- claim: grep="\"case_id\": \"D01A\"" in=docs/scorecard/results/D01A.json issue=789 note="and at least one case carries a committed result, so NOT POPULATED was false" -->

**Three axes kept separate**, because merging them is how a scorecard starts
lying:

- **reference fidelity** — how closely we reproduce a named reference
- **digital quality** — aliasing, noise, numerical behaviour
- **functional correctness** — bit-exact model ↔ RTL

*Lower aliasing can be an improvement without being more faithful to any
reference. Bit-exact RTL agreement establishes neither musical property.*

**Blocked on milestone 1**, for reference-comparison cells only. Cases for
functional correctness and digital quality can be built now.

## 5. Critical discrepancies resolved · **IN PROGRESS**

| discrepancy | status |
|---|---|
| **Aliasing 19–32 dB worse**, worsening with pitch | still the largest known defect. Milestone 1's mapping bug no longer holds the magnitudes provisional, but the pre-correction Surge rows are **withdrawn, not re-rendered** |
| **2× oversampling made aliasing worse** (−42.7 → −33.1 dB) | **addressed for the saw, not for the rest.** With the 31-tap half-band decimation FIR, 2× *buys* 17.3–23.9 dB instead of losing it; the gap to Mini V3 closes and the gap to Surge XT narrows 15–19 dB and is still large. The other waveforms are unchanged because nothing was applied to them — `docs/saw-alias-2x.md` |
| Excitation shape — machine 41.2 % of first 4 ms in 80–150 Hz vs our 22.3 % | measured, not built |
| Integer-hertz cutoff registers — **53.7 cents/LSB at 30 Hz** | reaches the control interface; needs coordination |
<!-- claim: grep="\"worst\": 17.31" in=docs/saw-alias-2x-results.json issue=789 note="row 2: the committed run behind 17.3-23.9 dB" -->
<!-- claim: grep="narrowed by 15" in=docs/saw-alias-2x.md issue=789 note="row 2: the Surge XT gap is narrowed, not closed" -->

## 6. Held-out cases and regression controls · **NOT STARTED**

Tolerances chosen **before** tuning to pass them, with settings held out so
matching the calibration examples does not become the objective.

---

## What this dashboard refuses to do

- **Produce one number.** There is no "synth quality" score and there will not be.
- **Let coverage hide.** A milestone with high pass rates and low coverage is not
  advancing.
- **Merge the three axes.** See milestone 4.
- **Treat "no verdict" as harmless.** It is not evidence we are wrong; it *is*
  missing verification, and it is counted as such.
