# One target, one turn. That is the whole point of this file.
#
# Agents kept firing verifiers one at a time -- fire, wake, fire, wake -- which
# cost 89 context reprocesses in a single session for no work at all. Writing
# "do not do that" in CLAUDE.md did not stop it: two more happened within five
# minutes of the rule being committed.
#
# The focused development gate is explicit so sound iteration need not wait on
# the broad model suite. Both gates persist per-job results as they complete.

PY  := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
RUN := $(PY) tools/run_all.py

.PHONY: help verify verify-fast verify-full controls test dag board

help:
	@echo "make verify       broad repository checks, run independently in parallel"
	@echo "make verify-fast  focused sound/scorer tests and selected M5A path"
	@echo "make verify-full  adds the hour-long runs (voice full set, drums)"
	@echo "make controls     every injected defect that must turn something red"
	@echo "make test         the Python suites only"
	@echo "make board        fill the scorecard's first batch and re-render the board"
	@echo "make dag          re-run the evidence and regenerate the README diagram"

## Everything a push should run.
## The 7200s per-job cap is a runaway kill, not a schedule: measured on the
## 2026-09-22 M2 (2026-09-23), the broad pytest job alone runs 2631s solo and
## exceeded the old 3600s cap under this target's parallel fan-out, reporting
## NO-VERDICT twice. verify-full already used 7200.
verify:
	@$(RUN) --timeout 7200 --json build/verification/verify.json \
	  "$(PY) -m pytest model/ spec/ tools/ fpga/ -q" \
	  "$(PY) rtl-sketch/verify_ladder.py" \
	  "$(PY) rtl-sketch/verify_modal.py" \
	  "$(PY) rtl-sketch/verify_ctl.py" \
	  "$(PY) rtl-sketch/verify_synth_top.py --osc2x" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --osc2x --outdir build/voice-osc2x" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --only waves3 --filter2x --outdir build/voice-filter2x" \
	  "$(PY) rtl-sketch/verify_synth_top.py --m5a-smoke --filter2x --outdir build/top-m5a-filter2x" \
	  "$(PY) tools/gen_rate_conv_2x.py --check" \
	  "$(PY) tools/verify_rate_conv_2x.py" \
	  "$(PY) tools/verify_mono_case.py" \
	  "$(PY) fpga/verify_fixture.py --outdir build/fx-base" \
	  "$(PY) fpga/verify_uart_bridge.py --scenario all --outdir build/uart-controls" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick" \
	  "$(PY) tools/check_decimator_saturation.py"

## Fast sound-development checks, separate from the broad repository suite.
## A valid M5A mismatch remains a passing verification job: this checks that
## the measurement and selected-path smoke produced a trustworthy verdict.
##
## fpga/test_uart_host_pty.py is deliberately NOT a gate here: it is an
## apparatus-sensitive harness whose own guards SKIP under wire/timeline
## noise, and a shared CI runner's noise floor puts its module-scoped
## apparatus check past its bound (m5a-fast went red within minutes of
## being gated, on hardware the harness correctly does not trust). Run it
## focused, on a quiet machine:
##   python3 -m pytest fpga/test_uart_host_pty.py -q
## fpga/test_uart_host_rolling.py IS gated: the same host logic against the
## same device contract (uart_device_sim) on SIMULATED time -- no pty, no
## thread, no wall clock -- so a shared runner's noise cannot move it.
verify-fast:
	@$(RUN) --timeout 600 --json build/verification/verify-fast.json \
	  "$(PY) -m pytest model/test_filter_rate_chain.py tools/test_rate_conv_2x.py tools/test_mono_m5a_score.py tools/test_measure_m5a_saw_cutoff.py tools/test_score_m5a_i2s.py tools/test_compare_m5a_i2s_candidate.py tools/test_verify_m5a_filter2x_i2s.py tools/test_measure_m5a_filter_oversample.py tools/test_measure_m5a_filter_headroom.py tools/test_measure_m5a_pulse_duty.py tools/test_measure_m5a_signal_path.py tools/test_measure_m5a_attack_bias.py tools/test_measure_mono_attack_context.py tools/test_measure_mono_m1a_reference.py tools/test_mono_m1a_score.py tools/test_qualify_m1a_attack.py tools/test_measure_m1a_volume_mapping.py tools/test_m5a_fast_workflow.py tools/test_run_case.py tools/test_run_all.py rtl-sketch/test_m5a_stimulus.py -q" \
 	  "$(PY) -m pytest fpga/test_selected_preset.py fpga/test_build_selected.py fpga/test_build_arty.py fpga/test_publish_arty.py fpga/test_publish_selected.py fpga/test_uart_host.py fpga/test_uart_host_rolling.py fpga/test_uart_replay_reuse.py tools/test_setup_ci_oss_cad.py fpga/test_spi_host.py -q" \
 	  "$(PY) -m pytest model/test_pulse_oversample.py tools/test_measure_mono_pulse_2x.py tools/test_pulse2x_configuration.py -q" \
	  "$(PY) -m pytest model/test_audio_measure.py -q -k foldback" \
	  "$(PY) tools/measure_m5a_signal_path.py --cutoff 14073 --drive 1.0 0.75 --out build/verification/m5a-signal-path-fast.json" \
	  "$(PY) tools/verify_mono_case.py" \
	  "$(PY) tools/check_workflows.py"

## Adds the runs that take an hour. Still one turn.
verify-full:
	@$(RUN) --timeout 7200 --json build/verification/verify-full.json \
	  "$(PY) -m pytest model/ spec/ tools/ fpga/ -q" \
	  "$(PY) rtl-sketch/verify_ladder.py" \
	  "$(PY) rtl-sketch/verify_modal.py" \
	  "$(PY) rtl-sketch/verify_ctl.py" \
	  "$(PY) rtl-sketch/verify_synth_top.py --osc2x" \
	  "$(PY) rtl-sketch/verify_voice.py --set full --osc2x --outdir build/voice-full-osc2x" \
	  "$(PY) fpga/verify_fixture.py --outdir build/fx-base" \
	  "$(PY) rtl-sketch/verify_voice.py --set full" \
	  "$(PY) rtl-sketch/verify_drums.py" \
	  "$(PY) tools/verify_m5a_filter2x_i2s.py" \
	  "$(PY) rtl-sketch/verify_synth_top.py --simulator verilator --m5a-smoke --filter2x --inject VOICE_FILTER2X_OFF --expect-fail --outdir build/top-filter2x-verilator-control"

## Every injected control that must turn something red, together.
## A run where these do not fire is a broken run, not a quiet one.
##
## THE TWO CASE CONTROLS MOVED FROM D01A TO D09A, and the reason is a control
## design point rather than a convenience. #118's length guard refuses the bass
## drum REFERENCE's decay -- its record holds 1.57 T20s past the -25 dB point
## and the guard wants 2 -- so D01A now comes back `no verdict` whatever is
## injected into it. REF_F0_20PCT could then never turn it red, and worse,
## REF_MISSING would have gone on "passing" against a case that was already a
## no-verdict for an unrelated reason: a control that would fire with the
## injection removed is a false green, which is the only failure mode a control
## exists to catch.
##
## The replacement has to PASS when clean, for the same reason. D06A was the
## first choice and the re-run then turned it into a genuine fail -- its body
## spectrum had been flattered by the very #101 artefact this branch removed --
## which would have made `--expect fail` fire whatever happened. So D09A
## (claves): clean pass at 0.61, a direct `Pitch` metric at the 10 % frequency
## tolerance so a 20 % shift is twice it, injected fail at worst 2.39.
##
## THE THREE FILTER CONTROLS need the frozen reference cache. Without it the
## clean baseline refuses; the runner reports NO-VERDICT, and this aggregate
## target fails. That is intentional: these controls cannot be called caught
## without a valid clean comparison. Restore the frozen profile first
## (`python3 tools/refprofile_restore.py`, which needs no plugin).
##
## THE TWO PROFILE CONTROLS NOW COVER F1B AND F1C. REF_CORNER_2X compares the
## injected run with a clean run, because all three F1 cases now fail cleanly;
## state-only `--expect fail` would be the D01A false green. `--expect changed`
## requires the injected result to change state or measured distance, so it
## cannot pass when the injection is removed. `--expect 'no verdict'`
## still discriminates on F1B and F1C: both produce a verdict when clean (fail,
## worst 1.41 and 1.62), so a missing clip or a tampered hash turning them into
## `no verdict` is a state CHANGE and the control can come back green only by
## firing. `--expect fail` cannot discriminate on them -- they are already fail
## -- so adding them to the octave control would have bought a line that passes
## with the injection removed, which is the false green D01A was moved for.
## Measured, not assumed: injected, F1B and F1C read worst 6.65 and 6.59.
##
## TWO CONTROLS ARE DELIBERATELY NOT HERE, and both were MEASURED, not assumed:
##
##   I2S_SWAP -- no longer discriminates at the whole-chip level. i2s_tx re-reads
##   `held` for the right slot at cycle 127; the core used to strobe its sample by
##   cycle 124 and now, with revision 10's drum section, strobes as late as 156, so
##   `held` still holds the LEFT word and the swapped stream is bit-identical to
##   the correct one. Measured both ways with verify_synth_top.py --rtl against
##   the pre-integration drum section: 124 of 256 before, 156 of 256 after, no
##   overrun either way. verify_synth_top.py prints a NOTE whenever the strobe is
##   past 128. The control still fires against i2s_tx on its own bench.
##
##   VOICE_MIX_SAT -- the voice's pre-ladder mixer never reaches its rail on this
##   patch, so the control is silent here. It is verify_voice.py's (BUGS) and
##   test_rtl.py's, and it fires there. An unsatisfiable gate is worse than no
##   gate, so it is not listed as one.
##
## NOTE the per-variant --outdir. These are several VARIANTS OF THE SAME
## verifier running concurrently, and the verifiers here write fixed filenames
## under their output directory -- so without this they would overwrite each
## other's intermediate files and the results would be meaningless in a way
## that still looks like a clean run. Any future concurrent variants of one
## verifier need the same treatment.
controls:
	@$(RUN) --timeout 3600 --json build/verification/controls.json \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --only gate --inject ENV_RATE_EXP --expect-fail --outdir build/voice-env-rate-exp" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --only default --osc2x --inject OSC2X_HEADROOM --expect-fail --outdir build/voice-osc2x-headroom" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --only default --osc2x --inject OSC2X_OFF --expect-fail --outdir build/voice-osc2x-off" \
	  "$(PY) tools/verify_rate_conv_2x.py --inject-clamp --expect-fail" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --only default --inject OSC_SMOOTH_ON --expect-fail --outdir build/voice-smooth-on" \
	  "$(PY) rtl-sketch/verify_ctl.py --link dr7rev1 --expect-fail --outdir build/ctl-rev1" \
	  "$(PY) rtl-sketch/verify_ctl.py --inject SPI_ADDR7 --expect-fail --outdir build/ctl-addr7" \
	  "$(PY) rtl-sketch/verify_ctl.py --inject SPI_DATA24 --expect-fail --outdir build/ctl-data24" \
	  "$(PY) rtl-sketch/verify_ctl.py --inject SPI_NOSEC --expect-fail --outdir build/ctl-nosec" \
	  "$(PY) rtl-sketch/verify_ctl.py --inject SPI_ANYLEN --expect-fail --outdir build/ctl-anylen" \
	  "$(PY) rtl-sketch/verify_ctl.py --inject SPI_DRAIN_LATE --expect-fail --outdir build/ctl-drainlate" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject VOICE_MASTER_PRESHIFT --expect-fail --outdir build/top-preshift" \
	  "$(PY) rtl-sketch/verify_synth_top.py --osc2x --inject VOICE_OSC2X_OFF --expect-fail --outdir build/top-osc2x-off" \
	  "$(PY) rtl-sketch/verify_synth_top.py --m5a-smoke --osc2x --inject VOICE_OSC2X_OFF --expect-fail --outdir build/top-m5a-smoke-off" \
	  "$(PY) rtl-sketch/verify_synth_top.py --m5a-smoke --filter2x --inject VOICE_FILTER2X_OFF --expect-fail --outdir build/top-m5a-filter2x-off" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject VOICE_DRUM_CLAMP16 --expect-fail --outdir build/top-dclamp16" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject VOICE_OUT_SAT --expect-fail --outdir build/top-outsat" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject I2S_SHIFT --expect-fail --outdir build/top-i2sshift" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject I2S_DELAY --expect-fail --outdir build/top-i2sdelay" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject SPI_ADDR7 --expect-fail --outdir build/top-addr7" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject SPI_DATA24 --expect-fail --outdir build/top-data24" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject SPI_NOSEC --expect-fail --outdir build/top-nosec" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject SPI_DRAIN_LATE --expect-fail --outdir build/top-drainlate" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject MODAL_NUM_HOLD --expect-fail --outdir build/top-numhold" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject DRUM_ENV_FLOOR --expect-fail --outdir build/top-envfloor" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject DRUM_LFSR_TAP --expect-fail --outdir build/top-lfsrtap" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject DRUM_RESET_ALIAS --expect-fail --outdir build/top-resetalias" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject DRUM_STOPS8 --expect-fail --outdir build/top-stops8" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject DRUM_BUS_STALE --expect-fail --outdir build/top-busstale" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject DRUM_DONE_NOWAIT --expect-fail --outdir build/top-nowait" \
	  "$(PY) tools/run_case.py --inject MONO_PITCH_UP_25_CENTS M5B --results build/case-m5b-pitch --expect changed" \
	  "$(PY) fpga/verify_fixture.py --wrong no-coef-seq --expect-fail --outdir build/fx-nocoef" \
	  "$(PY) fpga/verify_fixture.py --wrong drop-restore --expect-fail --outdir build/fx-droprest" \
	  "$(PY) fpga/verify_fixture.py --wrong late-window --expect-fail --outdir build/fx-late" \
	  "$(PY) fpga/verify_fixture.py --wrong no-tom-bend --expect-fail --outdir build/fx-notom" \
	  "$(PY) fpga/verify_fixture.py --wrong drop-tom-step --expect-fail --outdir build/fx-tomstep" \
	  "$(PY) fpga/verify_fixture.py --wrong burst --expect-fail --outdir build/fx-burst" \
	  "$(PY) tools/run_case.py --inject REF_F0_20PCT D09A --results build/case-detune --expect fail" \
	  "$(PY) tools/run_case.py --inject REF_MISSING D09A --results build/case-noref --expect 'no verdict'" \
	  "$(PY) -m pytest tools/test_run_case.py -q -k ref_corner_2x_control_moves_a_known_reference_corner" \
	  "$(PY) tools/run_case.py --inject REF_PROFILE_MISSING F1A F1B F1C --results build/case-noclip --expect 'no verdict'" \
	  "$(PY) tools/run_case.py --inject REF_PROFILE_TAMPERED F1A F1B F1C --results build/case-badhash --expect 'no verdict'"

test:
	@$(PY) -m pytest model/ spec/ tools/ fpga/ -q

dag:
	@$(PY) tools/compile_dag.py --run && $(PY) tools/compile_dag.py

## Fill the scorecard and re-render the board from what came back. The runner's
## own exit convention is 0 match / 1 mismatch / 2 no evidence, and a first
## batch that holds deliberate not-runs exits 2 by design -- so the board, not
## the status, is the report.
board:
	-@$(PY) tools/run_case.py --batch "First 32"
	@$(PY) tools/scorecard.py --markdown --readme
	@$(PY) tools/scorecard.py --check
