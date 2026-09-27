# 0023: Each cowbell partial has its own envelope pair

- **Status**: proposed
- **Date**: 2026-09-27
- **Decided by**: Builder agent, issue #107, from `tools/measure_partial_balance.py`
  against the Fischer TR-808 recording `cb8/CB.WAV` (sha256:1468cbd6c75a1f23)

## Context

DR 0010 split the cowbell into two paths, `SQ 4` (800 Hz) and `SQ 5` (540 Hz),
each through its own swing VCA -- but both paths still read the **same** two
envelopes, `E_CBA` (τ 5 ms) and `E_CBB` (τ 100 ms, DR 0010's fit to the
*summed* tail). Two partials under one envelope decay at one rate, so their
balance cannot move over the note.

The machine's does. Read per line, with the trajectory estimator the tool
validates on known damped partials (±1.1 dB; tau to 0.01 %), over 30-400 ms:

| | low line | τ low | high line | τ high | τ low / τ high |
|---|---:|---:|---:|---:|---:|
| machine | 558.35 Hz | **121.4 ms** | 823.70 Hz | **106.6 ms** | 1.139 |
| ours, revision 14 | 540.05 Hz | 97.8 ms | 800.00 Hz | 97.8 ms | 1.000 |

| balance (dB, high / low) | 30 ms | 60 | 100 | 200 | 300 | 400 |
|---|---:|---:|---:|---:|---:|---:|
| machine | 15.35 | 14.86 | 14.58 | 14.17 | 12.87 | 11.33 |
| ours, revision 14 | 6.50 | 6.54 | 6.58 | 6.57 | 6.58 | 6.56 |

(Issue #107 quoted τ 120.4 / 101.1 ms from a run with no committed code; the
figures above are the committed tool's, `docs/scorecard/cowbell-107/`.)

## Decision

1. **`N_ENV` 18 → 20** (contract revision 15). Two envelopes, `E_CBLA` (18)
   and `E_CBLB` (19), are appended, so no earlier index moves. Their registers
   are `0x88..0x8F` -- the 8-byte gap revision 10 left below `PATH` at `0x90`.
   `drum_dp.v`/`drum_regs.v`/`drum_kit.v`/`synth_top.v` change `ENVS` and
   nothing else; the envelope stage is 70 clocks per frame, was 68.
2. **The low partial's path reads `E_CBLA + E_CBLB`**; the high partial keeps
   `E_CBA + E_CBB`. Both keep reference 9's two-slope shape (τ 5 ms fast slope).
3. **Each tail is programmed with that line's measured τ**, not fitted to the
   balance: `rate_reg(106.6 ms)` = 13 for the high line (was 14 from 100 ms),
   `rate_reg(121.4 ms)` = 11 for the low. Q0.16 at 48 kHz steps τ by ~8 %
   here; 13 and 11 are the nearest steps (effective 105.0 and 124.1 ms), and
   their rate *difference* is also the nearest any pair of steps gets to the
   machine's.
4. **The low line's level, `PEAK_CBL` = 0.1652** (both slopes; the high line
   stays at 0.5), is the ONE number set from the balance table: the mean,
   over its six instants, of the offset left once the two taus are in
   (9.62 dB). Our low line was ~9 dB too loud; the high line matched.

Result (`docs/scorecard/cowbell-107/measure-after.log`, `decay-after.log`):
τ 124.9 / 105.3 ms, ratio 1.186 (machine 1.139).

| balance (dB) | 30 ms | 60 | 100 | 200 | 300 | 400 |
|---|---:|---:|---:|---:|---:|---:|
| machine | 15.35 | 14.86 | 14.58 | 14.17 | 12.87 | 11.33 |
| ours, revision 15 | 15.73 | 15.36 | 14.93 | 13.64 | 12.34 | 11.02 |
| error | +0.37 | +0.50 | +0.36 | −0.53 | −0.53 | −0.31 |

Every instant is inside the estimator's ±1.1 dB. The level was set from this
table, so matching its MEAN is by construction; its SHAPE -- the fall of 4.7
dB against the machine's 4.0, residuals of ±0.5 dB with no level parameter
left to absorb them -- is what the two measured taus predicted. The
scorecard's own D13A estimator (`tone_ratio_db`, 0-100 ms) reads 15.60 dB
against the machine's 15.15; it read 6.78.

## Alternatives considered

- **Lower the low partial's level and keep the shared tail rate.** This is
  the compromise #107 forbids: it matches at one instant and drifts
  everywhere else. With the level refitted it still misses by +1.5 dB at
  30 ms and −2.5 dB at 400 ms, and its fall is 0 dB against the machine's
  4.0. `model/test_cowbell_partial_decay.py` carries it as a control that
  must fail.
- **One new envelope, sharing `E_CBA`'s fast slope.** Saves one envelope
  (and leaves 4 bytes of the gap) but the fast slope's level is then the
  high line's, so the low line's attack-to-tail ratio is forced by the other
  line again -- the same coupling one level down. Not chosen.
- **Rates 14 and 12** (keep `E_CBB` at DR 0010's value, move only the new
  one). Identical rate difference, so an identical balance trajectory, but
  each line 4-6 % shorter than the machine's per-line τ. Not chosen: the
  per-line τ is the measurement and 13/11 is what it converts to.
- **Per-partial decay in the modal bank** (issue #107's second direction).
  The cowbell's partials are oscillators through ONE band-pass mode (1100 Hz,
  Q 2.8: τ ≈ 0.8 ms); the decay lives in the envelope, not the resonator. A
  second band-pass mode would split the filter, not the decay, and costs a
  mode where this costs two envelopes.

## Consequences

- **The `ENV` block now ends exactly at `PATH`.** A 21st envelope moves
  `PATH` (and so `MODE`), which is revision 10's kind of change. Recorded
  here so the next envelope is budgeted as an address-map revision, not as
  a free slot.
- **KIT808 moves a sixth time**, 148 → 154 writes: six new writes
  (`ENV_CTL/PEAK/RATE` of 18 and 19), `ENV_RATE[11]` 14 → 13, `PATH[14]`
  re-pointed, and `MODE_AMP[5]` 0.02176 → 0.02656: with the 540 Hz line
  9.6 dB down the voice peaked at 0.41 of its 0.5 chart target
  (`test_kit_voices_sit_at_the_chart_levels`), so the band-pass's level is
  re-balanced by `drums_fx_render.py --balance`'s rule. It scales both lines
  equally and moves no balance. A revision-14 image reads envelope 18/19 as zero and would
  play the cowbell with its 540 Hz line SILENT, so `drums_fx.kit_808_rev14()`
  is the frozen kit for that image (hash-pinned, REFUSES if it drifts), and
  `KITS_BY_REVISION` gains 15.
- **Area**: two envelopes of control (27+24+16+16 bits) and state
  (24+24+24+11 bits) -- ≈170 flops before the register file's muxing. Not
  re-synthesised here (rule 3: a cell count is not evidence; quote one only
  beside a simulation).
- **Scope of #107's first question -- which voices share an envelope across
  simultaneous partials.** Read from `kit_808()`'s path table: only the
  cowbell had two live oscillator partials summed under one pair. RS shares
  `E_RSX`/`E_RSG` across its two bridged-T taps, but those taps are
  resonator modes, whose decay is their own Q -- the shared gate is a
  window over two independently decaying rings, not the ring's rate. Its
  measured failure is the attack window (#106). The cymbal's three bands
  already have independent envelopes. No other voice needs this change.
- A negative control, `INJECT_BUG_DRUM_ENVS_REV14` (revision 14's
  18-envelope decode), must turn `verify_drums.py` red, which shows the bench
  exercises the envelopes this revision added.
