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

.PHONY: help verify verify-fast verify-full controls test dag board claims reference-integration trial trial-bootstrap

help:
	@echo "make verify       broad repository checks, run independently in parallel"
	@echo "make verify-fast  focused sound/scorer tests and selected M5A path"
	@echo "make verify-full  adds the hour-long runs (voice full set, drums)"
	@echo "make controls     every injected defect that must turn something red"
	@echo "make test         the Python suites only"
	@echo "make claims       re-derive every marked prose claim in the tree from evidence"
	@echo "make board        fill the scorecard's first batch and re-render the board"
	@echo "make reference-integration  the Fischer-corpus tests as a REQUIRED gate (refuses if absent)"
	@echo "make dag          re-run the evidence and regenerate the README diagram"
	@echo "make trial T=<id> [ARGS=...]  one product question -> PASS/FAIL/NO VERDICT + receipt (docs/trials.json)"
	@echo "make trial-bootstrap [ARGS=--venv DIR]  install spec/trial-environment.json (box and CI)"

## Everything a push should run.
## The 7200s per-job cap is a runaway kill, not a schedule: measured on the
## 2026-09-22 M2 (2026-09-23), the broad pytest job alone runs 2631s solo and
## exceeded the old 3600s cap under this target's parallel fan-out, reporting
## NO-VERDICT twice. verify-full already used 7200.
##
## check_f1_rtl_record.py is the same shape of check for the F1 `integrated-rtl`
## anchor (#94, #291): it refuses a board record that claims the instrument but
## carries model provenance, and it binds the record to the bytes of
## tb_f1_chain.v / ladder_dp_n.v / rate_conv_2x.v / tanh16.hex. Change any of
## those and this goes red until the anchor is re-measured
## (`tools/score_f1_rtl.py --case F1A`, about twenty minutes) -- which is the
## point: a changed ladder means the anchor is no longer about this tree.
##
## check_arty_evidence_binding.py is here for legibility, not coverage: the
## broad pytest job already catches a stale wrapper proof, but it catches it
## 39 minutes in as 23 failures across three files, and the one sentence that
## explains all 23 is buried in a traceback. The same question answered in
## 0.2s, naming the source file that moved, is worth a job slot.
##
## check_arty_evidence_binding.py --scope publication is a SECOND rung on the
## same tool asking a different question, and it could not be one until #436:
## the constraint file had no committed evidence at all, so the mode could only
## REFUSE and an unsatisfiable gate is worse than no gate. It is answered by two
## records now, each covering what its own bench read -- the UART digital record
## for sources()+roms(), fpga/reports/arty/xdc-binding/binding.json for the XDC
## -- and it goes red when the constraint file moves without its bench being
## re-run. That re-run is `fpga/verify_xdc_binding.py --outdir
## fpga/reports/arty/xdc-binding` and takes 0.2 s, which is what makes the rung
## satisfiable rather than merely strict. The default rung's question, verdict
## and output are unchanged.
##
## verify_xdc_binding.py itself is here for the reason the publication rung is
## not enough on its own: the gate compares hashes, and this is the thing that
## decides whether the constraint file still BINDS -- every get_ports naming a
## real port, every hierarchical path joining generate blocks with a dot and
## instances with a slash (#315, which shipped dead in R0 and R1), every
## output delay equal to the datasheet budget ext_io_timing derives. 0.2 s,
## pure Python, no Vivado.
##
## check_doc_claims.py ran THREE TIMES until #435 and now runs once, which is a
## widening rather than a saving. Its default set was docs/*.md -- one
## directory, not even recursive -- so fpga/ARTY.md and
## docs/scorecard/cymbal-369/tone-render/README.md had to be named here to be
## checked at all, and that is exactly how #429's three transcribed figures sat
## under a green 44/44 gate. Naming files kept the widening auditable but could
## only ever cover the ones somebody remembered: eleven further markers
## (docs/scorecard/README.md, docs/scorecard/ensemble-e1a/rtl/README.md,
## decision record 0018) were evaluated by nothing under a green "51 ok".
##
## The default set is now an explicit include list in the tool
## (DEFAULT_INCLUDES), and the no-argument run REFUSES by name on any marker in
## a tracked Markdown file outside it -- so a document in a new corner of the
## tree turns this red instead of being silently unchecked, and no Makefile
## line has to be remembered. 167 documents, 69 claims, 116s measured against
## the old 48/51/88s.
##
## holdout.py check is 0.2s and asks the one question a holdout's value rests
## on: did the settings exist in the repository before the render that read
## them, and have they moved since? The seal's own git state answers the first;
## the ledger's record of WHAT was read answers the second, and that second half
## is the failure the seal alone cannot catch -- settings genuinely committed
## first, then edited once the error was known. Where a commit is missing from
## the clone (squash merges do this) the ordering half reports a note rather
## than a failure: an unsatisfiable gate is worse than no gate, and the seal-hash
## half is always answerable. It is green on a tree with no seals at all, which
## is the state every Holdout case but F1D is in.
##
## verify_sd_dac.py (#406) carries its own four injected-defect controls and
## runs them every time, so it is here and not in `controls`: ~15 s, 4 vvp
## workers. Its PASS record in build/sd-dac is what fpga/build_arty_sd.py binds.
##
## check_decision_record_numbers.py is here because a DR number cannot be
## allocated correctly from one branch: two PRs each took 0017 within two
## minutes in September, on branches that never saw each other, and both merges
## were clean because the FILENAMES differ (#250). The directory is the only
## place the answer exists, so the directory is what gets read. 0.05s.
##
## tools/external_claim.py (#123) refuses external-tool claims without their
## environment tuple and host_per_plugin entries that are neither 'unverified'
## nor backed by a passing claim. Its test is also in verify-fast. <0.1s.
##
## THE FOUR ESTIMATOR GROUND-TRUTH JOBS (#517, #158) are here and not in
## `controls`, and the split is the runtime: `estimator_ground_truth.py check`
## is 11 s of pure Python -- 170 synthetic fixtures over seven signal types,
## each swept across frequency, duration, phase, SNR and level, against every
## public callable in model/audio_measure.py -- while the mutant runs it needs
## are a minute and live in `controls` beside the other injected defects.
##
## It is a gate on the MEASURING APPARATUS, which is why it runs with the fast
## set rather than once before a release: every sound claim in docs/scorecard
## is read through those estimators, so a change that moves one of them moves
## every number quoted anywhere, and the answer must be known in the same turn
## as the change. Its fixtures' answers come from their own synthesis
## parameters and closed forms derived from them, never from another estimator,
## so it needs no corpus and cannot go red on a reference file's absence.
##
## estimator_fixtures.py (1 s) is the catalogue's own axis-coverage guard: it
## refuses a family whose swept axis took fewer than three distinct values or
## spanned less than its stated minimum, so "swept" cannot quietly degrade to
## "two values a per cent apart". estimator_domains.py (2 s) and
## verify_109_claims.py (0.5 s) were committed one-off probes that ran
## nowhere; they now share estimator_fixtures' synthesis primitives, so they
## are gated here for the same reason the shared module is -- a change to the
## synthesis must not be able to move their numbers unnoticed. Both print the
## TR-808 corpus's absence as a result and exit 0 without it.
verify:
	@$(RUN) --timeout 7200 --json build/verification/verify.json \
	  "$(PY) -m pytest model/ spec/ tools/ fpga/ pnr/ rtl-sketch/test_verify_ctl_blindness.py rtl-sketch/test_synth_count.py -q" \
	  "$(PY) rtl-sketch/verify_ladder.py" \
	  "$(PY) rtl-sketch/verify_modal.py" \
	  "$(PY) rtl-sketch/verify_ctl.py" \
	  "$(PY) fpga/verify_late_events.py --json build/late-events/verification.json" \
	  "$(PY) rtl-sketch/verify_synth_top.py --osc2x" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --osc2x --outdir build/voice-osc2x" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --only waves3 --filter2x --outdir build/voice-filter2x" \
	  "$(PY) rtl-sketch/verify_synth_top.py --m5a-smoke --filter2x --outdir build/top-m5a-filter2x" \
	  "$(PY) rtl-sketch/verify_synth_top.py --drum-solo SD --drum-seconds 0.15 --outdir build/top-drum-solo-smoke" \
	  "$(PY) tools/gen_rate_conv_2x.py --check" \
	  "$(PY) tools/verify_rate_conv_2x.py" \
	  "$(PY) tools/verify_mono_case.py" \
	  "$(PY) fpga/verify_fixture.py --outdir build/fx-base" \
	  "$(PY) fpga/verify_uart_bridge.py --scenario all --outdir build/uart-controls" \
	  "$(PY) fpga/verify_pads_top.py --scenario all --jobs 1 --outdir build/pads" \
	  "$(PY) fpga/verify_pads_top.py --start-red --outdir build/pads" \
	  "$(PY) fpga/verify_sd_dac.py --outdir build/sd-dac" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick" \
	  "$(PY) tools/check_decimator_saturation.py" \
	  "$(PY) tools/check_arty_evidence_binding.py" \
	  "$(PY) tools/check_arty_evidence_binding.py --scope publication" \
	  "$(PY) fpga/verify_xdc_binding.py" \
	  "$(PY) tools/check_doc_claims.py" \
	  "$(PY) tools/check_f1_rtl_record.py" \
	  "$(PY) tools/holdout.py check" \
	  "$(PY) tools/check_decision_record_numbers.py" \
	  "$(PY) tools/external_claim.py" \
	  "$(PY) fpga/verify_live_midi.py --outdir build/live-midi" \
	  "$(PY) tools/probes/estimator_ground_truth.py check" \
	  "$(PY) tools/probes/estimator_fixtures.py" \
	  "$(PY) tools/probes/estimator_domains.py" \
	  "$(PY) tools/probes/verify_109_claims.py" \
	  "$(PY) tools/sensitivity.py check"

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
##
## rtl-sketch/test_verify_ctl_blindness.py is here despite being named for an
## RTL bench because THIS target is the only one CI runs (rungs.yml `make
## verify-fast`), and it is the gate on verify_ctl's blindness matrix
## (docs/verification-rules.md 4). It runs no simulator at all -- it calls
## `print_blindness` against injected counters and reads the text back, 0.18 s
## measured with iverilog removed from PATH -- so it costs the shared runner
## nothing and cannot go red on simulator noise. The rest of rtl-sketch's
## pytest files DO need iverilog and stay in `make verify` only.
##
## fpga/test_verify_xdc_binding.py and tools/test_check_arty_evidence_binding.py
## are in the fpga bundle rather than only in `make verify` because THIS target
## is what CI runs (rungs.yml `make verify-fast`) and #404 -- no CI job collects
## tools/ or fpga/ wholesale -- is still open. Without them the constraint
## bench's start-red on the pre-#315 file, and the publication rung's controls,
## would be checks that only ever ran on a developer's machine. 2.5 s measured
## together, against a 600 s cap.
##
## THE TIMEOUT IS PER JOB, SO THE SHAPE OF THE SPLIT IS THE GATE'S HEADROOM.
## tools/test_run_case.py is its own job rather than a member of the big one
## (review of PR #405). That bundle was one 292 s job on main against a 600 s
## cap -- 49 % of it, which is 97 % of it on the 2x-slower runner this repo's own
## logs show `ubuntu-latest` handing out (`measure_m5a_signal_path` 24.0 s vs
## 48.2 s, `verify_mono_case` 43.5 s vs 76.2 s, the fpga bundle 13.9 s vs 22.5 s,
## same commit-adjacent jobs) -- and #389's six new tests took it over the cap
## twice, NO-VERDICT at 600.0 s. A NO-VERDICT is not a FAIL, which is exactly
## why it must not be tolerated: it is the gate reporting nothing in the place a
## result belongs.
##
## Measured on an 8-vCPU worker before choosing the split (serial, so the two
## numbers are each job's own cost and not a scheduling artifact):
##   tools/test_run_case.py alone   282.9 s   112 tests
##   every other file in the bundle 278.7 s   424 tests
## AND THEN MEASURED IN CI, WHICH DISAGREED ABOUT THE BALANCE -- worth leaving
## here rather than quoting only the local numbers, because the runner's own
## speed is the variable this gate keeps tripping over. Two m5a-fast runs of the
## same commit, with `measure_m5a_signal_path` alongside as the runner's ruler
## (main measured it at 24.0 s):
##   ruler 49.5 s   test_run_case 274.6 s   other files 323.6 s   worst 54 % of cap
##   ruler 74.1 s   test_run_case 335.8 s   other files 444.9 s   worst 74 % of cap
## so the split is even to ~15 % rather than to 1.5 %, the OTHER-FILES job is now
## the longer one, and a 3.1x runner still leaves a quarter of the cap spare --
## against 97 % of it for main's single 292 s bundle on a 2x runner, and past the
## cap entirely for the unsplit version. `run_all.py` runs jobs concurrently with
## `min(len(cmds), cpu_count)` workers and these two are submitted FIRST, so they
## both start at t=0 on the runner's two cores; the remaining jobs queue behind
## whichever finishes first, so the target's own wall clock does not grow.
## Adding a file to either job is fine; adding a 150 s test to one is the thing
## that broke this, so put the next expensive file in whichever job is shorter --
## which the CI rows above, not the local ones, say is tools/test_run_case.py.
verify-fast:
	@$(RUN) --timeout 600 --json build/verification/verify-fast.json \
	  "$(PY) -m pytest tools/test_run_case.py -q" \
	  "$(PY) -m pytest model/test_filter_rate_chain.py tools/test_rate_conv_2x.py tools/test_mono_m5a_score.py tools/test_measure_m5a_saw_cutoff.py tools/test_score_m5a_i2s.py tools/test_compare_m5a_i2s_candidate.py tools/test_score_drum_i2s.py tools/test_compare_drum_i2s_candidate.py tools/test_verify_m5a_filter2x_i2s.py tools/test_measure_m5a_filter_oversample.py tools/test_measure_m5a_filter_headroom.py tools/test_measure_m5a_pulse_duty.py tools/test_measure_m5a_signal_path.py tools/test_mono_artifact_probe.py tools/test_diagnose_tom_body.py tools/test_measure_m5a_attack_bias.py tools/test_measure_mono_attack_context.py tools/test_measure_mono_m1a_reference.py tools/test_mono_m1a_score.py tools/test_qualify_m1a_attack.py tools/test_measure_m1a_volume_mapping.py tools/test_verify_attack_context_model.py tools/test_m5a_fast_workflow.py tools/test_score_ensemble_i2s.py tools/test_compare_ensemble_candidate.py tools/test_result_destination.py tools/test_run_all.py tools/test_manifest.py tools/test_external_claim.py tools/test_check_workflows.py tools/test_provenance_retention.py pnr/test_report_synth_area.py pnr/orfs/test_area_provenance.py rtl-sketch/test_m5a_stimulus.py rtl-sketch/test_verify_ctl_blindness.py rtl-sketch/test_synth_count.py -q" \
 	  "$(PY) -m pytest fpga/test_selected_preset.py fpga/test_build_selected.py fpga/test_build_arty.py fpga/test_publish_arty.py fpga/test_xdc_bindings.py fpga/test_verify_xdc_binding.py tools/test_check_arty_evidence_binding.py fpga/test_publish_selected.py fpga/test_uart_host.py fpga/test_uart_host_rolling.py fpga/test_uart_replay_reuse.py tools/test_setup_ci_oss_cad.py fpga/test_spi_host.py fpga/test_spi_host_nominal.py fpga/test_midi_session.py fpga/test_late_events.py fpga/test_coremidi_input.py fpga/test_measure_mac_midi_latency.py fpga/test_image_kit.py fpga/test_midi_image_kit.py fpga/test_pads_rom.py fpga/test_build_arty_pads.py -q" \
	  "$(PY) fpga/verify_live_midi.py --outdir build/live-midi-fast" \
 	  "$(PY) -m pytest model/test_pulse_oversample.py tools/test_measure_mono_pulse_2x.py tools/test_pulse2x_configuration.py -q" \
	  "$(PY) -m pytest model/test_audio_measure.py -q -k foldback" \
	  "$(PY) tools/measure_m5a_signal_path.py --cutoff 14073 --drive 1.0 0.75 --out build/verification/m5a-signal-path-fast.json" \
	  "$(PY) tools/verify_mono_case.py" \
	  "$(PY) tools/check_workflows.py" \
	  "$(PY) model/sound_report.py --check-locks" \
	  "$(PY) tools/inject_manifest_defects.py"

## Adds the runs that take an hour. Still one turn.
##
## THE ENSEMBLE ANCHOR is here and not in `verify`, and not in `controls`.
## `score_ensemble_i2s.py --case E1A` is thirteen six-second whole-chip runs --
## the four bus parts, the eight per-stop rows, and the DRUM_BUS_STALE negative
## control -- at about 2.1 minutes each on Verilator (measured, 2026-09-26, this
## host): 27 minutes at --jobs 1, which is what it uses so it does not fan out
## underneath run_all.py's own fan-out. `--reuse` makes a re-run on an unchanged
## tree nearly free, so this is cheap on a warm build/ and honest on a cold one.
##
## It writes to build/, NOT to docs/scorecard/results/E1A.json: this job is a
## regression gate on the anchor's machinery and its control, not a licence to
## quietly re-cut the committed record. Re-cut that deliberately, by running the
## tool without --out, and commit the record with the reports that produced it.
##
## It needs Verilator. On iverilog the same thirteen runs are about three hours,
## which is past this target's cap -- so a runner without Verilator gets a
## NO-VERDICT from run_all.py rather than a quiet skip.
verify-full:
	@$(RUN) --timeout 7200 --json build/verification/verify-full.json \
	  "$(PY) tools/score_ensemble_i2s.py --case E1A --jobs 1 --reuse --simulator verilator --out build/scorecard/E1A-integrated-rtl.json --audio build/scorecard/E1A-integrated-rtl-mix.wav --twin build/scorecard/E1A-fixed-model-twin.json" \
	  "$(PY) -m pytest model/ spec/ tools/ fpga/ pnr/ rtl-sketch/test_verify_ctl_blindness.py rtl-sketch/test_synth_count.py -q" \
	  "$(PY) rtl-sketch/verify_ladder.py" \
	  "$(PY) rtl-sketch/verify_modal.py" \
	  "$(PY) rtl-sketch/verify_ctl.py" \
	  "$(PY) rtl-sketch/verify_synth_top.py --osc2x" \
	  "$(PY) rtl-sketch/verify_voice.py --set full --osc2x --outdir build/voice-full-osc2x" \
	  "$(PY) fpga/verify_fixture.py --outdir build/fx-base" \
	  "$(PY) rtl-sketch/verify_voice.py --set full" \
	  "$(PY) rtl-sketch/verify_drums.py" \
	  "$(PY) tools/verify_m5a_filter2x_i2s.py" \
	  "$(PY) rtl-sketch/verify_synth_top.py --simulator verilator --m5a-smoke --filter2x --inject VOICE_FILTER2X_OFF --expect-fail --outdir build/top-filter2x-verilator-control" \
	  "$(PY) fpga/verify_live_midi.py --rtl coverage alternates pressure sustained --rtl-inject WRONG_DRUM_MAP DELAYED_EVENT WRONG_ALT --outdir build/live-midi-full"

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
## plan074/075: F1A-F1C now PASS cleanly on the selected filter under
## surge-type2-clean-v1, so REF_CORNER_2X is a discriminating `--expect fail`
## (the runner refuses a fail control whose clean baseline does not pass). It
## shifts ONLY the reference axis; the DUT keeps the frozen probe grid
## (run_case.dut_probe_grid). F1_LEGACY_SUBSTITUTE must refuse by identity.
## The paragraph below is the pre-plan074 history.
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
## THE THREE DRIFT CONTROLS MUST NAME `--only drift`, and this is not a speed
## optimisation. Contract 6.11 makes DRIFT = 0 bit-identical to no drift path at
## all, and every other scenario runs DRIFT = 0 -- so on `default`, `gate` or
## any other key all three are silent and would be three more unsatisfiable
## gates. Measured on the `drift` key: SHARED first differs at frame 56,
## LEAKFLOOR at 3444, MEANSTEP at 1066, and each also moves the final
## drift_acc state.
##
## NOTE the per-variant --outdir. These are several VARIANTS OF THE SAME
## verifier running concurrently, and the verifiers here write fixed filenames
## under their output directory -- so without this they would overwrite each
## other's intermediate files and the results would be meaningless in a way
## that still looks like a clean run. Any future concurrent variants of one
## verifier need the same treatment.
##
## THE TWO sound_report CONTROLS reinstate defects this project actually
## SHIPPED at the measurement layer, rather than defects someone invented --
## the same principle as SPI_ADDR7/SPI_DATA24 above, which replay the exact
## broken SPI frame that shipped (issue #52, docs/verification-rules.md 4).
## Their exit convention is sound_report's own and is the inverse of
## `--expect-fail`: 0 means at least one property MOVED past its tolerance --
## the control fired -- and 1 means nothing moved, i.e. a coverage hole. So
## they are listed bare, with no `--expect-fail`. They write no files and so
## need no --outdir; each takes about 90 s (two full kit renders, clean and
## injected). Measured on this tree, 2026-09-26:
##   bd-ma-envelope            BD T20 308 -> 207 ms, BD attack 14.56 -> 9.94 ms
##                             (BD fundamental and decay tau stay BLIND)
##   sd-centroid-amp-weighted  SD brightness 1918 -> 5868 Hz
##                             (all five other SD properties stay BLIND)
##
## THE FIVE DECISION-RECORD NUMBERING CONTROLS (issue #250) are rule 5 applied
## to a bookkeeping defect: `0017` was allocated twice on `origin/main` by two
## PRs that never saw one another, and `DUPLICATE_NUMBER` re-creates a second
## file under an already-used number, so the exact shipped defect stays runnable.
## THE CLEAN CASE IS LISTED FIRST for condition 1 -- all four injections stage a
## COPY of `spec/decision-records/` and never touch the tree, so a checker broken
## for every input would look like four firing controls without it. `--expect`
## names the verdict, not merely that something was red: `UNNUMBERED_FILE` and
## `EMPTY_DIRECTORY` must REFUSE (nothing checked), and a uniqueness test over
## zero files passing vacuously is the failure this pair exists to catch. The
## `-k issue_250` pytest job replays the historical two-file `0017` state
## itself. All six are pure Python, about 1.5 s together.
##
## THE TWO BUILD/REPORT-TOOL CONTROLS (issue #245) are the other half of rule 5:
## the two bugs its debt marker named, X-propagation quoted as an area and a die
## area recovered from its own utilisation input. Both are REFUSAL controls, so
## their exit convention is 0 = the tool refused as intended, 1 = it answered
## anyway, 2 = nothing was measured -- and `--expect` names the REASON, so a
## missing yosys cannot look like a control that fired.
##
## THEIR CLEAN BASELINES ARE LISTED HERE TOO, next to the injections rather than
## in `verify`, because a refusal control whose clean case also refuses proves
## nothing (condition 1) and the pair is only readable together. Measured on
## this tree, 2026-09-26 (yosys 0.67+post, Icarus 13.0):
##   report_synth_area clean            PASS, 1,679 cells
##   report_synth_area TANH_INDEX_OOR   REFUSED (outputs-are-x), 1,677 cells
##                                      WITHHELD. rtl-simulation MOVED (y is x on
##                                      506 of 512 cycles); netlist-simulation,
##                                      netlist-constant-x and the cell count
##                                      itself all BLIND -- yosys resolves the
##                                      don't-care, so the netlist simulates
##                                      x-free and the area moves 0.1 %
##   area_provenance clean              summarize.py exit 0, ratio 1.87 printed
##   area_provenance UTILIZATION_TARGET summarize.py exit 2, ratio withheld
##   area_provenance CORE_UTILIZATION_SET  ditto, the ORFS spelling
##
## THE CONSTRAINT-BINDING CONTROLS (#436) close rule 5 on #315: the UART-RX
## synchroniser constraints joined their generate block with a slash, matched
## nothing, and were DROPPED by Vivado from R0 and R1 while every text-level
## gate passed. `--inject UART_SLASH_JOIN` is those exact bytes, and `--matrix`
## runs all eight injections and prints the properties x defects matrix
## (docs/verification-rules.md 4) -- it exits 1 if any injection moves NO
## property, which is the only way a control can be a no-op and still look like
## one. probe_arty_constraint_scope.py is the gate-level pair: ten arms, each
## printing the gate's own exit code, including the arm that must stay GREEN
## (the default rung, blind to the constraints on purpose) and a start-red that
## runs the bench against the pre-#315 file from git history. All three are pure
## Python, about 2 s together.
##
## report_synth_area is THE ONLY JOB IN THIS FILE THAT NEEDS yosys (it also needs
## iverilog, which everything here already needs). It REFUSES rather than skips
## when either is absent, which is why it is not in the nightly's controls job:
## that image installs iverilog only. The area_provenance jobs are pure Python
## and DO run there.
##
## measure_promoted_bands.py validate (#138) is here because it IS a set of
## injected controls -- thirteen mutants of the three promoted estimators and
## of the floor reader, each named against the one known case it must turn red,
## plus the two start-red stub runs. It shipped in PR #507 wired to nothing,
## which is the state this target exists to prevent: a control nobody runs is
## indistinguishable from a control that passes. Pure Python, no corpus, ~3 s.
##
## permitted_differences.py --controls (#519) is the same shape for the OTHER
## half of #158: nine injected defects against a false-alarm suite, each
## declaring the rows it must turn red so a control that reds nothing and a
## control that reds the wrong row are both failures. It is ALSO in the broad
## pytest job via tools/probes/test_permitted_differences.py -- deliberately
## both, because `make verify` is what CI runs and this target is where a
## reader looks for the injected-defect inventory. Pure Python, no corpus,
## 49 s measured beside one other job on 8 cores.
##
## Its two slow modes are NOT here and are not meant to be: `--false-alarm-rate
## 50` (186 s alone) and `--safety-sweep 20` (765 s beside two other jobs; it is
## seven whole false-alarm runs plus a detection pass) are the measurements BEHIND the
## committed thresholds, quoted in the probe's docstring, and the pytest file
## re-runs a cheap decisive slice of each on every pass. A three-minute
## measurement that cannot change without a threshold changing does not belong
## in a per-push target.
##
## coupling_controls.py (#551) is the injected-defect inventory for contract
## 15.10's shared-bus DC coupling: eighteen named defects of the model (a
## bypassed enable, a misplaced bus or clamp, the wrong signed shift, a charge
## or output word one bit narrow, a charge cleared by a hit, retune or accent,
## a reset that keeps the charge or the enable, a frozen-while-bypassed charge,
## reserved bits, an aliased address), each declared against the ONE property of
## model/test_drums_fx.py::test_coupling_* that must catch it, plus the clean
## run, which must be green. CAUGHT / BLIND / NO VERDICT, and a properties x
## defects matrix. Pure Python, no corpus, about 12 minutes on one core
## (the model is an integer Python loop; 13 properties x 19 runs).
##
## estimator_ground_truth.py controls (#517) is the same shape one level up:
## the ground-truth suite in `verify` is a gate on sixty-four estimator
## checks, and this is the run in which those checks are REQUIRED to go red.
## Two start-red stubs with audio_measure's names and no behaviour (one
## answering a confident constant, one refusing everything) plus twenty-two
## named mutants, each declared against the ONE (estimator, family) pair it
## must redden -- including two that remove #517's own damped_sinusoid repair,
## because a repair with no injection is not closed (rule 5). It prints the
## checks x defects matrix rule 4 asks for and FAILS on any check that
## answered in the clean run and was reddened by nothing at all. Pure Python,
## no corpus, ~65 s.
controls:
	@$(RUN) --timeout 3600 --json build/verification/controls.json \
	  "$(PY) tools/control_capability_verdicts.py" \
	  "$(PY) tools/measure_promoted_bands.py validate" \
	  "$(PY) tools/probes/estimator_ground_truth.py controls" \
	  "$(PY) tools/probes/coupling_controls.py" \
	  "$(PY) tools/pytest_collection_inventory.py controls" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --only gate --inject ENV_RATE_EXP --expect-fail --outdir build/voice-env-rate-exp" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --only default --osc2x --inject OSC2X_HEADROOM --expect-fail --outdir build/voice-osc2x-headroom" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --only default --osc2x --inject OSC2X_OFF --expect-fail --outdir build/voice-osc2x-off" \
	  "$(PY) tools/verify_rate_conv_2x.py --inject-clamp --expect-fail" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --only default --inject OSC_SMOOTH_ON --expect-fail --outdir build/voice-smooth-on" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --only drift --inject DRIFT_SHARED --expect-fail --outdir build/voice-drift-shared" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --only drift --inject DRIFT_LEAKFLOOR --expect-fail --outdir build/voice-drift-leakfloor" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --only drift --inject DRIFT_MEANSTEP --expect-fail --outdir build/voice-drift-meanstep" \
	  "$(PY) rtl-sketch/verify_voice.py --set quick --only waves3 --inject SHARK_BLAMP_SIGN --expect-fail --outdir build/voice-shark-blamp-sign" \
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
	  "$(PY) rtl-sketch/verify_drums.py --short --inject DRUM_FINAL_WEAK --expect-fail --outdir build/drum-finalweak" \
	  "$(PY) rtl-sketch/verify_drums.py --short --inject DRUM_FINAL_SHORT --expect-fail --outdir build/drum-finalshort" \
	  "$(PY) rtl-sketch/verify_drums.py --short --inject DRUM_FINAL_SHIFT --expect-fail --outdir build/drum-finalshift" \
	  "$(PY) rtl-sketch/verify_drums.py --short --inject DRUM_FCAP_STALE --expect-fail --outdir build/drum-fcapstale" \
	  "$(PY) rtl-sketch/verify_synth_top.py --clap-phrase --inject DRUM_FINAL_WEAK --expect-fail --outdir build/top-clap-finalweak" \
	  "$(PY) rtl-sketch/verify_synth_top.py --inject DRUM_LFSR_TAP --expect-fail --outdir build/top-lfsrtap" \
	  "$(PY) rtl-sketch/verify_synth_top.py --drum-solo SD --drum-seconds 0.15 --inject DRUM_LFSR_TAP --expect-fail --outdir build/top-drum-solo-lfsrtap" \
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
	  "$(PY) fpga/verify_pads_top.py --inject PADS_DEBOUNCE_DOUBLE --outdir build/pads-controls" \
	  "$(PY) fpga/verify_pads_top.py --inject PADS_TRIG_STUCK --outdir build/pads-controls" \
	  "$(PY) fpga/verify_pads_top.py --inject PADS_SRC_STUCK --outdir build/pads-controls" \
	  "$(PY) fpga/verify_pads_top.py --inject PADS_KIT_HASH --outdir build/pads-controls" \
	  "$(PY) tools/run_case.py --inject REF_F0_20PCT D09A --results build/case-detune --expect fail" \
	  "$(PY) tools/run_case.py --inject REF_MISSING D09A --results build/case-noref --expect 'no verdict'" \
	  "$(PY) -m pytest tools/test_run_case.py -q -k ref_corner_2x_control_moves_a_known_reference_corner" \
	  "$(PY) tools/run_case.py --inject REF_PROFILE_MISSING F1A F1B F1C --results build/case-noclip --expect 'no verdict'" \
	  "$(PY) tools/run_case.py --inject REF_PROFILE_TAMPERED F1A F1B F1C --results build/case-badhash --expect 'no verdict'" \
	  "$(PY) tools/run_case.py --inject REF_CORNER_2X F1A F1B F1C --results build/case-f1-corner2x --expect fail" \
	  "$(PY) tools/run_case.py --inject F1_LEGACY_SUBSTITUTE F1A F1B F1C --results build/case-f1-legacy --expect 'no verdict'" \
	  "$(PY) -m pytest tools/test_check_surge_waveform_comment.py -q -k issue_271" \
	  "$(PY) -m pytest model/test_rig_qualification.py -q -k discrimination_matrix" \
	  "$(PY) -m pytest model/test_modeld_pedalboard_rig.py -q -k 'refuses or REFUS or uncorrectable'" \
	  "$(PY) tools/check_decision_record_numbers.py --expect ok" \
	  "$(PY) tools/check_decision_record_numbers.py --inject DUPLICATE_NUMBER --expect collision" \
	  "$(PY) tools/check_decision_record_numbers.py --inject HEADER_MISMATCH --expect misnumbered" \
	  "$(PY) tools/check_decision_record_numbers.py --inject UNNUMBERED_FILE --expect refused" \
	  "$(PY) tools/check_decision_record_numbers.py --inject EMPTY_DIRECTORY --expect refused" \
	  "$(PY) -m pytest tools/test_check_decision_record_numbers.py -q -k issue_250" \
	  "$(PY) model/sound_report.py --inject bd-ma-envelope" \
	  "$(PY) model/sound_report.py --inject sd-centroid-amp-weighted" \
	  "$(PY) tools/stage_case.py controls --root build/provenance-controls" \
	  "$(PY) tools/inject_manifest_defects.py" \
	  "$(PY) pnr/report_synth_area.py --expect area --outdir build/pnr-area-clean" \
	  "$(PY) pnr/report_synth_area.py --inject TANH_INDEX_OOR --expect refused-x --outdir build/pnr-area-tanh-oor" \
	  "$(PY) pnr/orfs/area_provenance.py --expect ok --outdir build/pnr-die-clean" \
	  "$(PY) pnr/orfs/area_provenance.py --inject UTILIZATION_TARGET --expect refused-circular --outdir build/pnr-die-utilreq" \
	  "$(PY) pnr/orfs/area_provenance.py --inject CORE_UTILIZATION_SET --expect refused-circular --outdir build/pnr-die-utilmk" \
	  "$(PY) tools/f1_rtl_filter_path.py --frames 30000 --inject F1_CHAIN_SKIP_INTERP --expect-mismatch" \
	  "$(PY) tools/f1_rtl_filter_path.py --frames 30000 --inject F1_CHAIN_DROP_DECIM --expect-mismatch" \
	  "$(PY) fpga/verify_xdc_binding.py --matrix" \
	  "$(PY) fpga/verify_xdc_binding.py --inject UART_SLASH_JOIN --expect-fail" \
	  "$(PY) tools/probe_arty_constraint_scope.py" \
	  "$(PY) tools/sensitivity.py check --inject VERDICT_ASSERTED --expect fail" \
	  "$(PY) tools/sensitivity.py check --inject POINT_TRANSCRIBED --expect fail" \
	  "$(PY) tools/sensitivity.py check --inject GRID_CHERRY_PICKED --expect fail" \
	  "$(PY) tools/sensitivity.py check --inject SHIPPED_OFF_GRID --expect fail" \
	  "$(PY) tools/sensitivity.py check --inject PREDICTION_WRONG --expect fail" \
	  "$(PY) tools/sensitivity.py check --inject RULE_UNSTATED --expect refused" \
	  "$(PY) tools/probes/permitted_differences.py --controls"

test:
	@$(PY) -m pytest model/ spec/ tools/ fpga/ pnr/ rtl-sketch/test_verify_ctl_blindness.py rtl-sketch/test_synth_count.py -q

## Re-derive every marked prose claim in this tree from the evidence it names.
## Three outcomes, and the third is the point: OK, STALE (the tree contradicts
## the prose -- exit 1), REFUSED (the claim could not be evaluated at all --
## exit 2, this repository's "no evidence", not "no problem"). Both are red.
##
## One invocation, because the tool's own default set is now the whole authored
## tree (#435) AND it refuses on any marker outside that set -- so a document
## this line forgot to name is red rather than silent. Which directories are
## scanned, and which are deliberately not: docs/claim-markers.md.
claims:
	@$(PY) tools/check_doc_claims.py

dag:
	@$(PY) tools/compile_dag.py --run && $(PY) tools/compile_dag.py

## A trial: one product question answered by EXISTING checkers (docs/trials.md,
## docs/trials.json). All logic is in tools/trial.py; this line only forwards.
## Exit 0 PASS, 1 FAIL, 2 NO VERDICT -- the receipt it prints is the record.
##   make trial T=T-DEADLINE
##   make trial T=T-DEADLINE ARGS="--mode reanalyse"
trial:
	@$(PY) tools/trial.py run $(T) $(ARGS)

## The one environment spec, installed idempotently (spec/trial-environment.json).
trial-bootstrap:
	@$(PY) tools/trial_env.py bootstrap $(ARGS)

## Fill the scorecard and re-render the board from what came back. The runner's
## own exit convention is 0 match / 1 mismatch / 2 no evidence, and a first
## batch that holds deliberate not-runs exits 2 by design -- so the board, not
## the status, is the report.
## The tests that need the Fischer TR-808 corpus, as a REQUIRED gate. Locally
## they skip, marked OPTIONAL, when the corpus is absent; here a missing corpus
## is REFUSED (non-zero), because a required job green through skips checked
## nothing. Location: $GF180_TR808_REFS, else /tmp/tr808-ref.
## #369 step 9's TONE knob law is here for the half of it CI cannot decide: its
## two corpus-gated controls (WRONG_ANALYSIS_BANDS, SHORT_RECORD) report NO
## VERDICT in the no-corpus job, and NO VERDICT is not a pass. `--require-corpus`
## is what turns them into a failure, so this is the only place they are
## actually enforced -- the claim its docstring makes, made true.
reference-integration:
	GF180_REQUIRE_TR808_REFS=1 $(PY) -m pytest tools/test_run_case.py tools/test_metric_purpose.py tools/test_cymbal_tone_knob.py -q
	GF180_REQUIRE_TR808_REFS=1 $(PY) tools/cymbal_tone_knob.py --check --require-corpus

board:
	-@$(PY) tools/run_case.py --batch "First 32"
	@$(PY) tools/scorecard.py --markdown --readme
	@$(PY) tools/scorecard.py --check
