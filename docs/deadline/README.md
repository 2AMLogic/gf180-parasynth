# The production frame deadline: three 2x saws with the 2x filter (plan075 T1)

**Verdict.**

- **Published baseline (OSC2X=1 FILTER2X=1 PULSE2X=0): fits.** The production schedule finishes every frame for the configuration PR #235's component bench refused, with 79 cycles of sample slack. Under musical stress (three audible 2x saws, glide and modulation, the whole drum kit, the drum filter, control writes over SPI and over the Arty UART), the worst slack is 14 cycles. At the register-legal worst case it is 6 cycles, and that bound is reached in simulation. There were zero missed frames, zero sticky `overrun` and the I2S output matched the model exactly.
- **The refusal was the bench's, not the chip's.** The bench now launches where the chip does. Its late-completion control fails for the deadline reason, to the cycle.
- **Pulse-enabled (PULSE2X=1): misses.** At register-legal increments of 2^23 and above, 1597 frames missed their deadline and the sticky overrun set. In the musical range the analytic bound is exactly the deadline, with 0 cycles of slack, and it was not observed. **PULSE2X=1 is excluded from the first release.** That exclusion is a checkpoint, not a fulfilment. A bounded correction has been identified and measured (below). It was deliberately not committed.

Every number here comes from `docs/deadline/runs/`: `run_all.json` records each job's own exit status, and each run has its own record. The batch is `tools/deadline_batch.py`.

## 1. What was measured

| | |
|---|---|
| Source | batch run at `2221d2c` (`docs/deadline/runs/HEAD.txt`), which is `origin/main` `9337ece` (#235 merged at `ffc1c00`) plus this branch. The tree was dirty only with the batch's own output directory. |
| RTL | `rtl-sketch/voice_dp.v` `a15752570543`, `synth_top.v` `e4f8d97b9785`, `fpga/rtl/arty_a7_top.v` `664a4359af90`. All three are **byte-identical to the published Arty baseline's `source_sha256`** (`fpga/reports/arty/integrated-baseline-2025.1/publication.json`), and the other sources are hashed in each run record. |
| ROMs | `tanh16.hex` `a5c4862a7e90`, `tanh256.hex` `c6e81324b225`, `g_rom128.hex` `97bd3bc7774f`, `k_rom32.hex` `723f288d7bc1`, `sine_q256.hex` `040736830a6b`, `exp_rom65.hex` `c29b6f815d80` |
| Flags | baseline `VOICE_OSC_2X VOICE_FILTER_2X`; pulse-enabled adds `VOICE_PULSE_2X` |
| Clock / rate | 12.288 MHz core, 256 cycles per frame, 48 kHz. The Arty wrapper runs with `SIM_NO_MMCM=1`, so the bench supplies 12.288 MHz directly. |
| Benches | `tb_top_bx.v` drives synth_top through the SPI pins. `tb_uart_bx.v` drives `arty_a7_top` through the UART pins and holds SPI idle. Both are unedited. `rtl-sketch/deadline_mon*.v(h)` is attached as an extra simulation root that reads synth_top hierarchically and drives nothing. |
| Simulator | Icarus Verilog. Python is `/Users/joseph/dev/gf180-parasynth/.venv`, which is a symlink to the gf180-monosynth venv, so the logs show that path. |

**The frame, from the RTL.** `go` is at cycle 8 (`GO_CYCLE`). The voice and drum datapaths are busy from cycle 9. The SPI drain writes in cycles 1–5 and the UART in cycles 6–7; the runs observed cycle 2 (SPI) and cycle 6 (UART). No write ever landed while a datapath was busy.

`i2s_tx` loads the next period's word at cycle 255, so the **sample deadline is a strobe by cycle 254**. `synth_top` latches `overrun` if either datapath is busy in cycle 0, so the **busy deadline is cycle 255**. The next frame's boundary is cycle 256, which is cycle 0 of the next frame. The drum section finishes at cycle 125 in every frame of every run, a constant, so it never gates the voice.

## 2. The refused case, with legal setup

This is PR #235's `f1cal` configuration with SAW instead of revsaw: three 2x saw oscillators with mix 1/0/0, the 2x filter, and `surge-type2-clean-v1` at 250, 1000 and 4000 Hz with resonance 0, then 1000 Hz at resonance 1.0 and 1.1. The words come from the landed calibration and are asserted equal to #235's record: gain 42598, ogain 100825 / 302474 / 322639.

In production, every register of the image was preloaded over the SPI pins before the note. Frames were computed from reset as always, but the gate stayed off until the image was complete. The note (three INC jumps, TRACK, GATE_ON) followed, and each F1 point was then a control write during playback.

| run | frames | missed | worst strobe | sample slack | busy slack | overrun | I2S vs model |
|---|---:|---:|---:|---:|---:|---:|---|
| production, PULSE2X=0 | 3691 | 0 | 175 | **79** | 81 | 0 | 3692 periods, 0 differ |
| production, PULSE2X=1 | 3691 | 0 | 175 | **79** | 81 | 0 | 3692 periods, 0 differ |
| component bench, production launch (frame 0 = whole image + note) | 7200 | 0 | 217 | **37** | 39 | — | 7200 frames, every sample and tap identical |
| component bench, **old launch** (`--go 48`, diagnostic) | — | frame 0 | — | — | — | OVERRUN | reproduces #235's refusal, now as a deadline FAIL |

Frame 0 of the component scenario is the heaviest frame this configuration produces: every register plus three new increments, so three reciprocal divisions. At go = 8 its strobe is at cycle 217. At go = 48 the same frame would strobe at cycle 257. That is the whole refusal.

## 3. Production results for every configuration

Each "stress" run preloads the voice image and the complete 808 kit, all 147 kit words plus the accents. It then plays three audible oscillators (mix 1/0.8/0.7, noise 0.3) with glide and oscillator and filter modulation on. That means every increment moves every frame, so all three reciprocals are recomputed.

Over those notes the run strikes all eleven stops and subsets of them. It switches the drum filter (ROUTE.DFILT) on and off and moves DCUT. It sends cutoff, resonance, volume, wheel and waveform writes during playback. It includes legato glides, retriggers, a gate-off and a three-increment jump. It then sweeps legato runs across MIDI 24–127.

"Extreme" runs set every increment to 2^23 or above by register writes. That is legal, but above Nyquist; note 127 is 0x42xxxx. They glide between such values with the drum filter on, which opens both PolyBLEP windows of every edge.

| configuration | scenario | frames | missed | worst strobe | **sample slack** | busy slack | overrun / busy-at-tick | I2S vs model |
|---|---|---:|---:|---:|---:|---:|---|---|
| baseline | stress-saw (SPI) | 2155 | 0 | 240 | **14** | 16 | 0 / 0 | 2156 periods, 0 differ |
| baseline | arty-uart (UART pins, Arty wrapper) | 3496 | 0 | 240 | **14** | 16 | 0 / 0 | 3300 periods, 0 differ |
| baseline | extreme-saw | 2167 | 0 | 245 | **9** | 11 | 0 / 0 | 1809 differ, see §7 |
| baseline | extreme-saw-mod | 2167 | 0 | 248 | **6** | 8 | 0 / 0 | 1809 differ, see §7 |
| PULSE2X=1 | stress-saw | 2155 | 0 | 242 | **12** | 14 | 0 / 0 | 2156 periods, 0 differ |
| PULSE2X=1 | stress-pulse (square / pulse29 / pulse15) | 2155 | 0 | 250 | **4** | 6 | 0 / 0 | 2156 periods, 0 differ |
| PULSE2X=1 | extreme-pulse | 2167 | **1597** | 255 | **−1** | 0 | **1** / 1293 | FAIL |
| PULSE2X=1 | extreme-pulse-mod | 2167 | **1739** | — | — | — | **1** / 1675 | FAIL |

Every run delivered all its writes (for example 460 of 460 SPI and 71 of 71 UART). None were corrupted, none landed off the frame the pin predicted, and the link `overflow` stayed at 0.

For the baseline stress-saw, the gate was on with three audible 2x saws for 1644 frames: 1188 of them with the drum filter and 44 with a strike in the same frame. The arty-uart run is the published Arty configuration under UART control, checked by `fpga/verify_uart_bridge.py`'s own contract and model comparison. Its drum page stays at its reset image, because the kit would take about 5000 frames at 115200 baud.

## 4. Why these runs cover the schedule, and what they do not prove

The per-frame record attributes every cycle. In **every frame of every run that met its deadline**, the voice's strobe cycle is exactly the formula below. That is 30,333 frames over 12 runs, including the candidate-mutant runs. The `late:14` control gives 101 = 87 + 14 in all 2155 of its frames, so the stall is exactly additive.

```
strobe = 87 + R + O + D + W + 2·A + I + 3·K
```

- R: reciprocal waits, 19 for each oscillator whose modulated increment changed; at most 57.
- O: 2x-bank waits, 20 for each oscillator whose shape goes through the bank; at most 60.
- D: the drum-filter wait, 1 without ROUTE.DFILT and 23 with it.
- W: PolyBLEP windows examined. That is 1 for tri or sine, 2 for a single-edge shape and 4 for a rectangular one.
- A: active windows. A single edge can have 2 active only if the increment is 2^23 or more; below that it has 1.
- I: modulated increments, at most 3.
- K: shark-tooth oscillators.

The constant 87 has no other value in any such frame, so nothing else varies. The glide slews and envelope updates run in the ladder's shadow and trade cycles with the ladder wait. The drum section ends at cycle 125, before the voice waits for it. Writes are confined to cycles before `go` by `synth_top.v`'s drain and UART window.

So the worst case is a sum of per-term maxima set by the RTL structure. It is not the minimum of a trace:

| configuration | worst oscillator | bound (all increments < 2^23) | bound (any register value) | observed worst |
|---|---|---:|---:|---:|
| PULSE2X=0 | 2x saw: 20 + 2 + 2·2 = 26 | 242 (slack 12) | **248 (slack 6)** | 248, reached (extreme-saw-mod) |
| PULSE2X=1 | 2x rectangular: 20 + 4 + 2·4 = 32 | **254 (slack 0)** | **266 (−12: miss)** | 255, miss |

Both bounds take the drum filter (D = 23), three reciprocals, osc modulation and three of the worst oscillator together.

**Limits of this argument.**

- The constant 87 and the per-term costs are measured on these traces. They are not derived formally from the RTL.
- States no run visited are covered only by their structural cost, for example a shark-tooth under the 2x config, where K ≤ 3 per oscillator is still cheaper than a 2x saw.
- The rectangular extreme's A = 12 was not observed (8 was), because frames desynchronise after the first overrun.
- Drum latency is constant here with every stop and pair exercised. A drum-engine change would need a re-run.

This is a bound from a validated cost model, not an exhaustive proof.

## 5. The controls: late completion rejected for that reason

The late-completion control is a **build-time mutant**. `verify_deadline.py --mutant late:N` generates `voice_dp.v` with the master mix held N extra cycles in `S_OUT2`. Every value is unchanged; only completion is later. It is generated from the current file, and each anchor must occur exactly once or the run refuses (`tools/test_verify_deadline.py`). It is not an `ifdef` in `voice_dp.v`, because any byte change there unbinds the published Arty image: 16 `fpga/test_publish_arty.py` failures, measured.

| control | result | reason recorded |
|---|---|---|
| production SPI, threesaw-f1cal, late:160 | caught (exit 0 with `--expect-fail`) | 3691 missed frames, busy at 1846 ticks, sticky overrun 1 |
| Arty UART, late:160 | caught | 3496 missed, busy at 1749 ticks, overrun 1 |
| production stress-saw, **late:14** | PASS: slack exactly **0** (strobe 254), 0 missed, I2S exact | the measured slack of 14 is exact |
| production stress-saw, **late:15** | caught: 41 missed frames, strobe 255, first frame 538 (the recorded worst frame) | one cycle past the measured slack |
| component bench, late:160 | caught | `OVERRUN at frame 0`, a deadline FAIL, status 1 |
| component bench, **late:37** | PASS, strobe 254 | the component slack of 37 is exact |
| component bench, **late:38** | caught: `LATE SAMPLE` at frame 0, cycle 255 | one cycle past |
| component bench, `--go 48` | caught as a deadline FAIL | the original refusal, reproduced |

`--expect-fail` in `verify_deadline.py` and `--expect-deadline-fail` in `verify_voice.py` accept **only a deadline failure**. An I2S or value mismatch alone does not count; `tools/test_verify_deadline.py` pins that.

Pre-existing controls still behave. OSC2X_OFF is caught. The all-X stub red run now compiles and fails with X, exit 1. It had been dying at compile since `bd7ba32` added the `phase_os2`/`inc_mod` taps, and the stub now declares them.

## 6. The component bench's setup/launch contract (repaired)

`tb_voice.v` now pulses `go` at **synth_top's `GO_CYCLE`** (8). `verify_voice.py` REFUSES a bench whose GO differs from `synth_top.v`; `--go N` is available for diagnostics only and is labelled as such.

A frame's writes occupy the n cycles ending at go−1. When n > 7, they begin in the **previous frame's tail**. Every such cycle is asserted idle, and a write that would land mid-computation REFUSES the run rather than being applied. The register state at `go` is therefore the one the chip would hold, and **no write lands after the launch**. The chip itself delivers at most six writes per frame, all before cycle 8. The bench's larger bursts are whole-image loads for verification convenience, placed where they cannot overlap computation.

An overrun (busy in the frame's last cycle) and a strobe after cycle 254 are now FAILs of the design (status 1), not "did not run".

Regressions at the new launch all passed, bit-exact: quick default, quick `--osc2x` (64,416 frames), `waves3 --filter2x`, `waves3 --filter2x --pulse2x`, #235's `f1cal` (revsaw), and the new `threesaw` with and without `--pulse2x`.

## 7. Disposition

| configuration | disposition |
|---|---|
| **OSC2X=1 FILTER2X=1 PULSE2X=0** (published Arty baseline), any waveform combination, drums, controls | **Qualified for the frame deadline.** The bound is 6 cycles over the whole register space and 12 in the musical range. It was reached, with the measurement exact to the cycle. |
| **PULSE2X=1** | **Excluded from the first release** (not supported, not published as supported). It misses at register-legal increments, and in the musical range the bound is 0 cycles of slack. **Unqualified.** |
| Model/RTL agreement at glides between increments of 2^23 and above | **Separate defect, #247.** Frames 310–357 (jumps) match; the first difference is 7 frames after the first glide write. Every frame still met its deadline. It is outside the musical range but register-legal. |

**Candidate correction, measured and not shipped.** An oscillator whose output comes from the 2x bank still runs the scalar PolyBLEP window loop. That loop's `c_pp`/`c_ps` feed only the scalar path, which the oscillator does not use. Skipping it is one condition in `S_WIN`: `if (!blep || (use_osc2x && shape_osc2x)) state <= S_MIX;` (`--mutant skip2xwin`).

With the loop skipped, every 2x oscillator costs 21 cycles, so the bound becomes **233 (slack 21) for every combination in both configurations**, and the runs reach it. `extreme-pulse` went from −1 (1597 missed frames) to +24. `extreme-pulse-mod` and `extreme-saw-mod` both went to +21. On `stress-pulse` the slack went from 4 to 21 and the I2S stayed bit-exact against the unchanged model. The extreme candidate runs show the same 1809 differences as the unmodified RTL, which is #247.

It is not committed. It changes `voice_dp.v`, which would unbind the published baseline image. The baseline does not need it; it fits. And the pulse-enabled image needs a rebuild anyway (#205, plan075 T4). **It belongs in T4, with the full verify_voice and synth_top evidence at that head.**

## 8. Wrong-then-right, and apparatus incidents

1. **Launch at 48 versus 8.** The original refusal came from the bench's schedule. Re-run at the chip's launch, the same scenario passes with 37 cycles of slack.
2. **The late mutant was absorbed.** Its first version stalled in `S_DWAIT`, alongside the drum filter's own 23-cycle wait. The 1-cycle boundary control (late:15) reported NOT CAUGHT; nothing had moved. It now stalls in `S_OUT2`, and late:14 and late:15 bracket the measured slack exactly.
3. **An `ifdef` control broke the published-image binding.** It was compiled into `voice_dp.v` and caused 16 `fpga/test_publish_arty.py` failures. It was reverted and replaced by a build-time mutant.
4. **Tick-relative indexing.** `tb_voice` reads outputs just after the clock edge, so their chip cycle is `cyc + 1`. Caught when writing the deadline fields, before any run was quoted.
5. Apparatus, not results:
   - A zsh batch did not word-split `$D` and ran nothing (exit 2). It is now `tools/deadline_batch.py`.
   - The stub red run was invoked with the wrong relative path (NO-VERDICT). It was re-run with the right path (`runs/run_all-stub.json`, PASS).
   - The stub itself had been uncompilable since `bd7ba32`; it is fixed.

## 9. Reproduce

```
.venv/bin/python tools/deadline_batch.py      # writes docs/deadline/runs/, ~35 min
.venv/bin/python -m pytest tools/test_verify_deadline.py -q
```
