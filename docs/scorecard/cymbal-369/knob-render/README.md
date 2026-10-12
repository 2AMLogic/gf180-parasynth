# #413 -- the cymbal knob renderer is now the shipped structure

Part of #369. Record: `knob-render.json`, produced by
`.venv/bin/python tools/cymbal_tone_render.py --knob-render --out docs/scorecard/cymbal-369/knob-render/knob-render.json`
(commit and `sources_dirty` are inside the record; it was regenerated from the committed tree after the
H-EDT10 direction fix, not hand-edited). Tests: `tools/test_cymbal_tone_render.py`, `model/test_discrimination.py`.

## Diagnosis (two faults, two register paths)

1. **TONE** -- `kit_at("CY")` solved `_cy_hi_amp` for the reference recording's own fitted `CY.tone_ratio`: the
   render reproduced the band ratio it was then judged against (circular). Path: the CYHI/CYH mode amplitudes.
2. **DECAY** -- `_cy_decay_scale()` clamped a two-point power law, and the slow end asked E_CYL for a time constant
   longer than the Q0.16 rate register carries (rate 1, tau = 65536/SR = 1.3653 s). Path: E_CYL / E_CYD rate words.
   `rate_register_floor` in the record shows E_CYL at the floor at DECAY 7.5 (asked 1.0155 s) and 10.0 (asked 1.2732 s).

## Repair

`kit_at("CY", ...)` now delegates to `cymbal_candidate.shipped_knob_kit(tone, decay)` (one constructor, no second
private implementation). It reads no `laws`; the anchor writes nothing.

## Results (rendered, 7 s per setting)

| Acceptance item | Result |
|---|---|
| anchor == `render_drum_solo("CY")` bit-identical | PASS |
| writes only cymbal-owned registers, all 25 settings; the 15 other voices unchanged at the anchor | PASS |
| hats / OH / CH shared resources bit-identical at every TONE x DECAY | PASS (all) |
| five DECAY renders pairwise distinct, endpoints distinct | PASS, all 5 TONE rows |
| H and L EDT10 rise with DECAY; H EDT10 direction matches the 808 | PASS, all 5 TONE rows |
| **late-T20 monotone across all five positions** | **NOT MET**: REFUSED by the frozen instrument ("late decay is not one exponential") at the anchor-H and at DECAY 7.5/10 for H, L and Ln; at TONE 00 L (qualified at all five) is **non-monotone, 3653.9 ms at 7.5 vs 3648.8 ms at 10.0 -> FAIL**, kept visible |
| injected saturation control | clean green; mutant (DECAY 10.0 asks what 7.5 asks) RED on `distinct-renders` (75=10), `h-edt10-rises`, `l-edt10-rises`, `h-edt10-direction-matches-808`; no property blind |

The late-T20 failure is a measured fact about the instrument, not a gate defect: the 7.5 -> 10.0 L step is a
0.14 % reversal sitting on the E_CYL rate-register floor. The gate stands on EDT10 (qualified everywhere) and
reports late-T20 only where it qualifies. Requiring it everywhere would be unsatisfiable (verification rule 8);
it is REFUSED, not passed. A real fix needs a wider E_CYL rate range (RTL), out of scope here.

Development vs confirmation: the record labels every setting (`split`); the gate is structural and not tuned on
either set, so CY2500 (a confirmation holdout) is judged by the same untuned gate. No claim about tracking the
808 across TONE/DECAY *accuracy* is made -- that needs the Fischer TONE law (out of scope).
The Fischer WAV corpus (`~/dev/refs`) is absent on this host; the record uses the committed `fischer.json` only.

## Wrong-then-right

Two, both caught by controls: (1) the first H-EDT10 direction gate demanded a direction from 808 steps the 808
itself does not resolve (plateaus < 2 %); caught by the clean-row test, fixed in aff45b1. (2) a citation fix
introduced a typo ("is was") caught on re-read. Prior-session wrong-then-right 2 (nearly reporting a 10 dB band
error through `kit_at`) is the origin of this issue.

## Defect-class search

- `#371` in the four named files: 6 stale occurrences before, 5 repointed to #413; the sixth (gate-379 §2) now says
  "#371, the merged PR" deliberately (historical).
- Reference-fitted laws: `fit_laws` still computes `CY.tone_ratio` (test_discrimination.py ~784) but nothing reads
  it for CY now (test pins this). **`SD.tone_ratio` (snare) is the same shape** (fitted to the recording, line ~728,
  used by `_sd_partial_amp`): not repaired here, flagged as a follow-up.
- Clamped endpoints: the E_CYL/E_CYD rate-register floor is the only one found in the cymbal path (recorded above).
- Remaining `kit_at("CY")` callers: `tools/cymbal_bands.py:229`, `tools/probes/excitation_energy.py:1044`,
  `tools/probes/hihat/hh_probe5.py:364` -- all now delegate; their old numbers through the circular path are
  superseded, not regenerated here.
