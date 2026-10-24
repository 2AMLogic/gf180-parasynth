# verify_voice full-set failure on R1: in domain or not? (#354)

**Answer: outside R1's qualified domain.** The only scenario that diverges when run on its own is the all-maximum
`extremes` register image, and the release host cannot send that image. The `audition` failures are that divergence,
carried through the bench's continuing voice. All three audition sequences pass bit-exact when run alone. So does an
in-domain stress test of every R1 preset at the live controllers' limits.

The RTL is R1's published `voice_dp.v` (`67d59af7`, the same on `origin/main` `c9fbc6c`), under Icarus, with
`--osc2x --filter2x` on the build box.

## 1. Where the full-set count comes from

The bench plays every scenario on **one continuing voice** (`verify_voice.generate`). A divergence in internal state
therefore persists into later scenarios. Splitting the published run (`split-b1.json`):

| run | samples differing | verdict |
|---|---|---|
| `--only audition,extremes` (as in #354) | 5,581 / 121,040 | FAIL: reproduced exactly |
| `--only extremes` | 617 / 5,840 | FAIL |
| `--only audition` | **0 / 115,200** | **PASS** |
| `--only audition --pulse2x` | **0 / 115,200** | **PASS** |

In the combined run, 04-lead-glide shows 4,143 differing samples and 07-growl-bass 821. By 08-self-osc-whistle the
carried state has died away, and it shows 0.

## 2. Which `extremes` scenario diverges on its own

`tools/verify_voice_scoped.py --extremes-index I` runs each extremes scenario from reset through the unchanged bench
(`x0`–`x6`):

| # | scenario | alone |
|---|---|---|
| 0 | the all-maximum control image | **FAIL**: 387 / 960 samples |
| 1 | all-zero image | PASS |
| 2 | negative span, k / ogain / vol at maximum | PASS |
| 3 | rate = 0 | PASS |
| 4 | d_dec = 0 | PASS |
| 5 | a_inc = 0 | PASS |
| 6 | k = gain = ogain = 0 | PASS |

In scenario 0, the first divergence is in the ladder's 19-bit word at frame 62: model −262,119, RTL −262,144 (the
rail). The final output then rails with **opposite signs** at frame 203: model +32,767, RTL −32,768. The model's ladder
word reaches the rail too (headroom 0 dB).

## 3. Why scenario 0 is outside the domain

`verify_voice_scoped.py --classify-full` (`classify.json`) checks each full-set scenario against `qualified_domain` and
against the host's own conversions:

- `check_patch` (the waveform sets);
- `check_inc` (every programmed increment);
- k/gain/ogain must equal `ladder_regs` of the patch's resonance within CC71's 0–1, at a release preset's drive;
- volume at most CC7's 0.9;
- mixer weight at most 1.0.

Scenario 0 is excluded four times over:

- **INC_RANGE**: increment 16,777,215 (above Nyquist; #247's territory).
- **Ladder words**: k 131,071, gain 1,048,575 and ogain 1,048,575 are not the conversion of any resonance. CC71 programs at most k 65,536.
- **Volume**: 65,535 > CC7's 0.9.
- **Mixer**: weights 65,535 > 1.0.

The three audition sequences are also outside the domain on their own terms:

- all three use waveform sets R1 does not ship (**WAVES**);
- their drives (1.5, 3.6, 0.5) are no preset's;
- 08-self-osc-whistle's resonance of 1.06 is above CC71's range.

That does not change the finding above: they pass when run alone.

The classifier does not check envelope words, so three envelope-edge scenarios show as IN. All of them pass alone.
First version of the classifier: it required the resonance to be a CC step (r/127), which rejected the default
preset's own 0.62. Wrong-then-right 1; corrected before any verdict was drawn.

## 4. In-domain stress

`verify_voice_scoped.py --domain` runs 12 scenarios, 144,000 frames:

- each R1 preset (`default`, `m5a-saw`, `m5a-pulse`);
- CC71 resonance 0 and 1.0 through the host's own `ladder_regs`;
- CC74 cutoff 40 Hz and 8 kHz;
- CC7 volume 0.9;
- the playable range's end notes plus 36/84/96;
- the preset's glide between them, a retrigger and a release.

Every patch and note is checked by `qualified_domain` before rendering, and the run refuses otherwise.

| config | result | worst ladder word (model) | margin to the 19-bit rail |
|---|---|---:|---:|
| OSC2X FILTER2X (R1) | **PASS**, 144,000 frames, every sample and tap | 76,903 | **10.65 dB** |
| + PULSE2X | **PASS**, 144,000 frames | 65,286 | 12.07 dB |
| control `--inject KEFF` | **caught** (comparison failed as required) | — | — |

In-domain play keeps the ladder at least 10.6 dB below the rail where scenario 0 diverges.

## 5. What stays open (#354 is not closed by this)

- **The out-of-domain divergence is real.** At the ladder's 19-bit rail, model and RTL saturate differently, and the output then rails with opposite signs. Which side is right against the contract is not established here.
- **It is also the only thing keeping `--set full` red.** Next step: capture the ladder stage words (`y19` and its inputs) at frames 55–65 of scenario 0 in both model and RTL, find the first differing operation, and fix whichever side violates the contract. Add the exact image as a permanent injection, per verification-rules rule 5.
- **Scope.** `verify_voice.py`'s docstring now states its scope and this known out-of-domain failure. The gate's verdict logic is unchanged, so a full-set FAIL is still reported as FAIL.

## 6. First wrong operation, repair and control (after the batch above)

**Layer: RTL.** In `ladder_dp_n.v`, `xg <= xg_full[SW:0]` holds (x · gain) >> 11 in 25 bits. The ports (17-bit x,
20-bit gain) span −33,554,400..33,553,888, which needs 26 bits. The slice comes before `sat_s`, so the product
**wraps** and flips the sign of the ladder's input. The model computes it exactly and saturates. A known-answer
width test (`tools/test_ladder_xg_width.py`) confirms the intended range independently of both implementations.

**Reachable only outside the domain.** At most 272,630 of gain × 65,535 of |x| < 2^35. The all-maximum image uses
gain 2^20 − 1.

**Repair and control.** The repair (26-bit `xg`, and `INJECT_BUG_LADDER_XG25` keeping R1's width as a control) is
PR #364, against the R2 candidate. R1's bytes are unchanged. With the repair:

- `verify_voice --set full`: **PASS, 442,596 frames**.
- `--only audition,extremes --pulse2x`: PASS, 121,040 frames.
- The all-maximum image alone: PASS.
- The control reproduces R1's first divergence exactly and is caught.
- `verify_ladder`: PASS.

The musical audition mismatches were this wrap, carried by the continuing voice. With the repair they pass in the
chain, so the full set's continuity is kept rather than reset around.

**Model unchanged.** No sound score is stale.

## 7. Closure on the current tree (2026-10-11, #354)

Sections 1–5 describe **R1's pinned bytes** (`67d59af7`), which stay as they are by design. Section 6 is the repair
(#364). This section re-measures the **current tree** (`origin/main` `dcc534bd` plus this branch, which changes no
RTL). It ran on a Loom dispatch worker under Icarus **13.0**, Python 3.12, with one simulation at a time; the
historical runs used Icarus 14 on the build box. Every run below uses `--osc2x --filter2x`.

| run | result | receipt |
|---|---|---|
| all-maximum image alone (`verify_voice_scoped --extremes-index 0`) | **PASS**, 960 frames | `current-x0-clean.log` |
| the same, `-DINJECT_BUG_LADDER_XG25` (R1's 25-bit `xg`) | **FAIL, caught for its reason**: first divergence y19 at frame 62 (model −262,119, RTL −262,144), 387 samples, output rails with opposite signs at frame 203 | `current-x0-xg25-control.{log,json}` |
| `--only audition,extremes`, continuing voice | **PASS**, 121,040 frames, every sample and tap | `current-ae-clean.log` |
| the same, `--pulse2x` | **PASS**, 121,040 frames | `current-ae-clean-p2x.log` |
| the same chain, `-DINJECT_BUG_LADDER_XG25` | **FAIL**: 5,581 samples, of which 04-lead-glide 4,143, 07-growl-bass 821 and 08-self-osc-whistle 0 | `current-ae-xg25.log` |

**What this establishes.** Putting R1's width back into today's tree, and changing nothing else, reproduces R1's
published chain count exactly: 5,581 samples, the same per-sequence split, and the same first divergence. The
`audition` mismatches in #354 were therefore all this one wrap, carried by the continuing voice. No part of them is a
separate defect driven by musical input. With the repair, the chain passes with continuity kept. Nothing was reset
around the failure.

**What this does not establish.**

- **Not the full set.** `--set full` (442,596 frames) in both pulse configurations is a build-box run, and this host is
  not the box. The figure in section 6 is the last full-set measurement, against #364's head, not against this commit.
- **Not R1 PULSE2X with the mutant.** R1's PULSE2X count of 649 was not re-derived under the mutant.
- **Not #247.** The above-Nyquist glide question is a separate issue and is not touched here.
- **Not the build box's Icarus 14.** These values are from Icarus 13.

**Now gated.** The clean image runs in `make verify` as `verify_voice_scoped.py --extremes-index 0 --osc2x
--filter2x`. The mutant runs in `make controls` with `--expect-fail --expect-first y19@62:387`. A bare
`--expect-fail` accepts any failure, so a define that stopped reaching the ladder, combined with an unrelated
breakage, would still read as "caught". `--expect-first` passes only on R1's recorded signature. Its matcher has
pure-Python tests, `tools/test_verify_voice_scoped_expect.py`, and they include that defeating input: an earlier,
upstream divergence. Mutating the matcher to name the downstream tap turns two of those tests red.

**Defect-class search** (`git grep` on `origin/main`, for a product slice taken before saturation,
`<= *_full[…]`): it finds only the `xg` registers.

- `ladder_dp_n.v` is repaired, with the 25-bit control kept.
- The scalar `ladder_dp.v` keeps a 25-bit `xg`. Its `x_in` is 16-bit, so 2^15 · (2^20 − 1) >> 11 = 16,777,200 < 2^24,
  and it cannot wrap.
- The other wide results, `out_full` and `u_full`, go through `sat_o` / `sat_s`. The only exception is `u_full`'s own
  `INJECT_BUG` control.

**Wrong-then-right in this pass: 0 measurements.** One run gave no verdict. The first attempt at the three chain runs went in one backgrounded command. The
harness stopped it at its 600 s background limit during the mutant run. The two clean results had completed and were
kept. The mutant was re-run alone. Separately, one 7 s `--extremes-index 0` run overlapped the mutant chain on this
host. Both results are recorded as they ran.
