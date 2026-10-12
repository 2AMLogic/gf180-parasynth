# The cymbal knob renderer: shipped structure (#413) with the RATE exponent (#656)

Part of #369. Records in this directory:

| file | produced by | what it holds |
|---|---|---|
| `knob-render.json` | `python3 tools/cymbal_tone_render.py --knob-render --out docs/scorecard/cymbal-369/knob-render/knob-render.json` | anchor, ownership, 25 settings, hats, the DECAY gate, two controls, the RATE table |
| `late-t20-localise.json` | `python3 tools/probes/cymbal_late_t20.py localise --out …` | paired interventions on the late-T20 refusals that the rate fix does not reach |
| `confirmation.json` | `python3 tools/probes/cymbal_late_t20.py confirm --out …` | the gate's rule on conditions it never saw |

All three were produced from committed trees. Each one carries its `commit` and `sources_dirty: false`. None was hand-edited. `knob-render.json` is from `61217e5`. Tests: `tools/test_cymbal_tone_render.py`, `model/test_drum_rate_exponent.py`.

## What #656 changed (the sound first)

**DECAY 7.5 and 10.0 now move the low band.** Before, the cymbal's DECAY law asked E_CYL for about 0.92 s and 1.16 s at those two positions. The Q0.16 rate register's slowest decay is rate 1 = 1.3653 s, so both positions got that same envelope. With a 3-bit exponent on RATE/FRATE (contract revision 18), every ask is realised to within 0.15 %:

| DECAY | E_CYL asked | old code → realised | new code → realised | E_CYD asked | new realised |
|---|---|---|---|---|---|
| 0.0 | 0.2205 s | 6 → 0.2275 (+3.2 %) | `0x70318` → 0.2206 (+0.06 %) | 0.0662 | 0.0662 |
| 2.5 (anchor) | 0.4551 | 3 → 0.4551 | 3 → 0.4551 (nothing written) | 0.1365 | 0.1365 |
| 5.0 | 0.6897 | 2 → 0.6827 (−1.0 %) | `0x700FD` → 0.6908 (+0.15 %) | 0.2069 | 0.2068 |
| 7.5 | 0.9243 | **1 → 1.3653 (+47.7 %, floor)** | `0x700BD` → 0.9247 (+0.04 %) | 0.2773 | 0.2774 |
| 10.0 | 1.1589 | **1 → 1.3653 (+17.8 %, floor)** | `0x70097` → 1.1574 (−0.13 %) | 0.3476 | 0.3474 |

The asks are the anchor's realised tau times the DECAY network ratio, not the fitted constant times the ratio. The shipped kit's E_CYL and E_CYD were fitted to the recording through their Q0.16 codes, so the anchor sound is 0.4551 / 0.1365 s and the knob scales that. The constant-based asks are in the record as `nominal_asked_tau_s`.

Measured effect at TONE 5.0, from `knob-render.json` rows:

- **L EDT10**: 276 / 573 / 881 / 1201 / 1524 ms. Under #413's record it was 287 / 573 / 869 / **1799 / 1810**, a 0.6 % step from 7.5 to 10.0. It is now 27 %.
- **L late T20** (frozen instrument): 551 / 1233 / 1915 / 2543 / 3114 ms, qualified at all five and rising. Under #413 it was REFUSED at 7.5 and 10.0 on this row. On TONE 0.0 it qualified but reversed, 3653.91 → 3648.81 ms.

## The DECAY gate (`--knob-render` exit)

The clean properties must PASS on every TONE row: `distinct-renders`, `h-edt10-rises`, `l-edt10-rises`, `h-edt10-direction-matches-808`, **`late-t20` (band L)** and **`decay-taus-realised`**. A refusal is never a pass. The gate refuses outright if its gated-band list is empty or names a band nobody measured.

| TONE | late-t20 (L) | L refused at | H (reported, ungated) |
|---|---|---|---|
| 0.0 | **PASS** 546 / 1219 / 1909 / 2560 / 3156 ms | — | REFUSED at 5.0, 7.5, 10 |
| 2.5 | **PASS** 548 / 1223 / 1912 / 2556 / 3146 | — | REFUSED at 2.5, 5.0, 7.5, 10 |
| 5.0 | **PASS** 551 / 1233 / 1915 / 2543 / 3114 | — | REFUSED at 2.5, 5.0, 7.5, 10 |
| 7.5 | **REFUSED** | 10.0 (residual 1.8 dB) | REFUSED at 5.0, 7.5, 10 |
| 10.0 | **REFUSED** | 5.0, 7.5, 10.0 (2.6, 2.9 dB at 7.5 / 10) | REFUSED at 5.0, 7.5, 10 |

`decay-taus-realised` passes on every row (worst 0.15 %). The other four properties pass on every row. **The command therefore exits 1.** That is correct: two rows have a real defect, and it is not the rate register (next section).

**#656 acceptance item 2 is met on TONE 0.0, 2.5 and 5.0 for band L. It is not met on TONE 7.5 and 10.0 (L), and not met anywhere for H.**

## What remains, and what causes it (paired interventions, `late-t20-localise.json`)

Following the curation, the refusals left after the floor is gone are not attributed to the floor. Each was intervened on one variable at a time:

| band / condition | as shipped | low path off | decay path off | short path off | E_CYL headroom (PEAK ×8, att +3, level-neutral) |
|---|---|---|---|---|---|
| H / CY5025 (anchor) | 1.50 dB | **0.27** | — | 1.47 | 1.41 |
| H / CY5010 | 1.77 | **0.29** | — | 1.77 | 1.47 |
| L / CY1075 | 2.58 | — | 2.46 | 2.56 | **0.53** |
| L / CY1010 | 2.94 | — | 2.83 | 2.96 | **0.43** |

(The values are late-segment Schroeder residuals. The instrument refuses above 1.5 dB.)

- **L at TONE 7.5 / 10 is E_CYL's headroom, not crosstalk.** Switching the decay path off barely moves the residual. Running the same envelope 18 dB higher with the path 18 dB lower (same output level) qualifies it. Derived from the arithmetic, not separately measured: PEAK_CYL 0.0864 × TONE gain 0.43 puts the low band's envelope at −28.5 dB re full scale, and its one-LSB floor sits at about tau·SR LSB, −49.6 dB at 1.16 s. **#660.**
- **H is the low band's energy above 6 kHz.** Switching the low path off qualifies H, and headroom helps only 0.1–0.3 dB. H refuses at the anchor itself, which this renderer must leave bit-identical. So no DECAY setting can make H qualify. A gate on H would be red at the anchor, and no control could be seen turning it red. H is therefore reported with its own status and left ungated. **#661.**

## Controls (rule 5: the historical defect as an executing mutant)

| control | what it injects | properties it moved | blind |
|---|---|---|---|
| saturation (#413's) | DECAY 10.0 asks what 7.5 asks | distinct-renders, h-edt10-rises, l-edt10-rises, h-edt10-direction-matches-808, **late-t20** (L 2543 = 2543: plateau → FAIL), **decay-taus-realised** | none |
| **Q0.16 floor (#656's)** | the same knob law through `rate_code(…, exp_bits=0)`: E_CYL on rate 1 at 7.5 and 10.0 | **late-t20** (L REFUSED at 7.5, 10), **decay-taus-realised** (FAIL) | distinct-renders, both EDT10 rises, the 808 direction |

The Q0.16 row's blind column is the point. EDT10 and audio distinctness could not see the floor, because E_CYD still moved. Only the late T20 and the register table saw it, which is why #413's EDT10-only gate passed while the floor was live.

RTL: `INJECT_BUG_DRUM_RATE_Q16` (the datapath ignores `e`) and `INJECT_BUG_DRUM_RATE_REG16` (`drum_regs` truncates the write to 16 bits) are both caught by `verify_drums --short` and at the I2S pins by `verify_synth_top`. Both are listed in `make controls`.

## Confirmation on conditions the gate never saw (`confirmation.json`)

- **TONE 2.5** (a confirmation row in the frozen split), at nine wiper fractions 0, 0.125, …, 1.0, four of them between the knob codes. L late T20 = 548 / 880 / 1223 / 1571 / 1912 / 2231 / 2556 / 2856 / 3146 ms: qualified everywhere and strictly rising.
- **TONE 7.5 with the strike 7919 frames later** (different LFSR and oscillator phases). L late T20 = 562 / 1248 / 1909 / 2506 / REFUSED. This is the same refusal at the same position as the selection grid (#660), and nowhere else.

## The RATE width: a swept dial (`docs/sensitivity/drum-rate-exp-bits.json`)

The plan was committed before any point (`3948a96`). Worst DECAY-ask error at 0..4 exponent bits: 49.23 / 24.89 / 4.978 / 0.312 / 0.0014 %, SENSITIVE as predicted. The frozen 5 % rule (ISO 3382-1 EDT JND) selected **2**. At 2 bits the frozen late-T20 instrument then refused L at CY5010 (1.51 dB, E_CYL +4.72 %), and at 3 bits it qualified (1.38 dB). That refuted the rule's premise that a sub-JND tau error buys nothing measurable, so the selection was **amended to 3**. The record carries the amendment with `stated_before_results: false` and says plainly that the paired evidence is a 0.01 / 0.12 dB threshold crossing, not a margin. Cost: 108 flip-flops (18 envelopes × RATE + FRATE × 3 bits) plus a 3-stage shift on the decay slot. This is stated from the RTL. No synthesis was run.

## Wrong-then-right (this session: 4)

1. The sweep's closed-form check fitted −6..−40 dB as one window. It read the integer decay's low-level truncation bias (3.8–4.6 %) as a closed-form error. It is now split: `sim_check_pct` is ≤ 0.82 % and `tail_slow_pct` is reported.
2. The localisation criterion asked that the short-path-off render NOT qualify. It read H's 1.47 dB (unchanged from 1.50) as a counterexample. It is now residual-based.
3. The width: the frozen rule's 2 bits was refuted by the gate and amended to 3 (above).
4. H's and L's remaining refusals were first read as band crosstalk. The L / TONE 10 rows refuted that (decay path off: 2.94 → 2.83 dB), and the headroom intervention is what moves L.

## Not done here

- The H and L refusals above (#660, #661). Both are sound-structure changes to the shipped CY kit, and both would move the anchor.
- Synthesis area of the wider registers. RTL simulation was run on this host as `verify_drums --short` and `verify_synth_top` (full and `--short`), not as `make verify-full`. `verify_drums` without `--short`, and the rest of `make controls`, belong on the build box.
- The Fischer WAV corpus (`~/dev/refs`) is absent on this host. The record uses the committed `fischer.json`.
