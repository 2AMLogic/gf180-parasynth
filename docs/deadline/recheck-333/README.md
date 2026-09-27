# skip2xwin re-checked on current sources (#333, plan098 §5C)

`docs/deadline/README.md` §7 proposed a correction but did not commit it: an
oscillator whose output comes from the 2x bank still runs the scalar PolyBLEP
window loop, whose `c_pp`/`c_ps`/`b_pp`/`b_ps` feed only the scalar path. This
directory re-checks that finding against the RTL on `origin/main`, whose
`voice_dp.v` is byte-identical to R1's pin `67d59af7…`. The records were
produced on the build box with the build-time mutant; **`voice_dp.v` is not
changed here**, so the published R1 image stays bound.

**By inspection.** The finding still holds:

- `c_pp`/`c_ps` reach only `osc_two` and `sawc_raw`, and `b_pp`/`b_ps` reach only the shark residual. All of these select `osc_raw`, i.e. the scalar path.
- For `shape_osc2x`, S_MIX goes to S_OSCWAIT and takes `osc2_sample`. The scalar `osc_d1`/`osc_d2` history is not updated.
- The 2x bank's inputs (`phase_os2`, `inc_mod`, `sh`, `r`, `dutyv`) are set before S_WIN.
- The anchor `if (!blep) state <= S_MIX;` occurs once.

**By simulation.** `stress-pulse` is the PULSE2X=1 musical-range stress: square,
pulse29 and pulse15, all 2x, with glide, modulation and the full kit.

| run | config | mutant | missed | worst sample slack | I²S periods compared, differing | writes |
|---|---|---|---:|---:|---|---|
| prod-stress-pulse-p2x | PULSE2X=1 | — | 0 | **3** | 2158/2158, 0 | 461/461 |
| cand-stress-pulse-p2x-skip2xwin | PULSE2X=1 | skip2xwin | 0 | **20** | 2158/2158, 0 | 461/461 |
| prod-stress-saw | R1 | — | 0 | **13** | 2158/2158, 0 | 461/461 |
| cand-stress-saw-skip2xwin | R1 | skip2xwin | 0 | **20** | 2158/2158, 0 | 461/461 |
| cand-threesaw-f1cal-p2x-skip2xwin | PULSE2X=1 | skip2xwin | 0 | 83 | 3692/3692, 0 | 60/60 |
| cand-arty-uart-skip2xwin | R1, UART pins | skip2xwin | 0 | 20 | 3300/3300, 0 | 71/71 |
| prod-extreme-pulse-p2x | PULSE2X=1, above Nyquist | — | **1294** | — | 2170/2170, 1812 | 358/358 |
| cand-extreme-pulse-p2x-skip2xwin | PULSE2X=1, above Nyquist | skip2xwin | **0** | 23 | 2170/2170, 1809 (#247) | 358/358 |
| **ctl-stress-saw-skipallwin** (control) | R1 | skip for *every* oscillator | 0 | 20 | 2158/2158, **358 differ** | 461/461 |

Component bench with the skip2xwin mutant (`build/dl4/mut/mutant-skip2xwin/voice_dp.v`):

- `verify_voice --set quick --only waves3 --filter2x --pulse2x`: **2,880 frames**, every sample and tap identical, final state identical.
- `verify_voice --set quick --osc2x`: **74,144 frames**, identical.

The run_all records are `b4.json` and `b5.json`.

## What this shows

1. The skipped work is unused. With every 2x oscillator skipping the loop, every compared I²S period is identical to the unchanged model, in both configurations and at both the SPI and UART pins.
2. The equivalence bench can see the skip when it matters. The negative control skips the loop for R1's **base-rate** square too, and 358 of 2,158 periods differ. The deadline is unaffected, which isolates the failure to the missing edge correction.
3. The saving is 7 cycles in R1's stress case (13 → 20) and 17 cycles in the pulse-enabled one (3 → 20).
4. The documentation has drifted by one cycle. On current sources PULSE2X=1 `stress-pulse` has slack 3, where `docs/deadline/README.md` says 4. R1 `stress-saw` has 13, where it says 14; the R1 scorecard already says 13. With skip2xwin the worst frame is strobe 234 (slack 20), against the documented 233 (21). The one extra cycle is common to every configuration and was not localized here.
5. Register-legal increments above Nyquist miss 1,294 frames without the correction and none with it. The host refuses them (`INC_RANGE`), so they are outside the player domain. They remain design counterexamples, with #247's model/RTL mismatch unchanged.

**Wrong-then-right.** The control was first run with `--expect-fail`, which
accepts only a *deadline* reason. It printed "NEGATIVE CONTROL NOT CAUGHT FOR ITS
REASON" although its intended assertion, the I²S comparison, fired. The record
keeps `reasons: ["wire_mismatch 358"]` and `deadline_failed: false`. Judge it on
that reason, not on `--expect-fail`.

## Hand-off spec for the next image (integration owner)

This is the RTL step for the pulse2x sound candidate. Its sound evidence is in
`docs/scorecard/m5-artifacts-333/`: M5A/M5B foldback 10.27/8.85 dB → 2.21/1.91 dB
through the PULSE2X=1 SPI→I²S path, bit-exact. None of this is sound delivered
by the R1 (`PULSE2X=0`) image.

1. **RTL.** In `voice_dp.v` S_WIN, change `if (!blep) state <= S_MIX;` to `if (!blep || (use_osc2x && shape_osc2x)) state <= S_MIX;`.
   - This unbinds R1's `voice_dp.v` pin, so it belongs only to the new image.
   - Keep `skipallwin` as the standing control (`rtl-sketch/verify_deadline.py`). After the change it must be regenerated against the new anchor.
2. **Build.** `OSC2X=1 FILTER2X=1 PULSE2X=1` (`-verilog_define VOICE_OSC_2X VOICE_FILTER_2X VOICE_PULSE_2X`), under #205. Derive timing, I/O, DSP and warnings from that image.
3. **Host.** `fpga/release/qualified_domain.py` currently refuses `PULSE2X`, and it must admit it for the new image only. The `m5a-pulse` preset's effective duty stays 47.90 %.
4. **Required evidence at the new head, with counts:**
   - `verify_voice --set quick` with `--osc2x`, `waves3 --filter2x --pulse2x`, and the full set once;
   - `verify_synth_top` M5A/M5B full phrases with `--pulse2x`: 1,305,533 and 729,533 periods;
   - the full-kit phrase: 2,851 periods;
   - `verify_deadline` `stress-pulse --pulse2x`, `stress-saw` and `arty-uart`, plus late-completion boundary controls re-bracketed at the new slack.
5. **Rectangle headroom (resolved by the sound owner; include it in the same image).** At 0.85 every rectangle saturates the 2x decimator's Q1.15 output near the top of the range (pulse15 from MIDI 99, pulse25 from 108, 47.9 % and square from 119). The chosen value is **rectangles 24248/32768 (0.74), saw unchanged at 27853**. It was selected by a rule frozen in advance: zero clipped samples and ≥ 0.1 dB margin on a 1/8-semitone sweep.
   - **Model:** in `voice_fx.py`, add `_OS2_RECT_GAIN_Q15 = 24248`, used in `_render_2x` when `o.shape in TWO_EDGE`.
   - **RTL:** in `polyblep_saw_pair.v`, under `ifdef VOICE_PULSE_2X`, select `rectangular ? 16'sd24248 : SUBSTEP_GAIN_Q15`. This file is in R1's pin set, so the change belongs to the new image only.
   - **Control:** `INJECT_BUG_VOICE_PULSE2X_RECT_HEADROOM` restores 27853 and must fail the RTL/model comparison.
   - **Expected phrase numbers at the new head:** M5A/M5B foldback 2.21/1.91 dB, Gain 1.54/2.05 dB (limit 3).
   - Evidence and costs are in `docs/scorecard/m5-artifacts-333/pulse2x-headroom/` (PR #343).
6. The CI Verilator 5.020 discrepancy (#8442) is still open.
