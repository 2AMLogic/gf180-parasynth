# pytest --durations=0 report

- duration lines: 2900 (hidden under the print threshold: 16824); tests: 2751; files: 190
- listed total: 4347.0 s of pytest wall 4366.77 s (6550 passed, 27 skipped, 2 xfailed) -- listed / wall = 99.5%
- by phase: call 4088.9 s, setup 258.0 s, teardown 0.0 s
- completeness: reconciled against junit (6579 testcases; 3828 only in junit, all under 0.0155s); worst per-test gap 0.0100 s; junit counts match the summary line: True

## Tests by duration bracket

| bracket (s) | tests | of tests | s | share of listed |
|---|---:|---:|---:|---:|
| >=60 | 11 | 0.4% | 1193.6 | 27.5% |
| 30-60 | 17 | 0.6% | 658.5 | 15.1% |
| 10-30 | 65 | 2.4% | 1192.7 | 27.4% |
| 1-10 | 300 | 10.9% | 953.2 | 21.9% |
| <1 | 2358 | 85.7% | 349.0 | 8.0% |

- tests at 10 s or more: 93 of 2751 (3.4%) hold 70.0% of listed time

## Top 25 tests (share of listed time)

| # | s | share | call s | nodeid |
|---:|---:|---:|---:|---|
| 1 | 372.39 | 8.6% | 372.39 | `tools/test_r0_capture.py::test_t_physical_on_a_synthetic_bundle_is_no_verdict_with_its_control_caught` |
| 2 | 91.21 | 2.1% | 91.21 | `tools/test_audio_distance_floor_refusal.py::test_full_run_on_an_unmodified_corpus_exits_0` |
| 3 | 90.83 | 2.1% | 90.83 | `tools/test_audio_distance_floor_refusal.py::test_full_run_with_mt50_cut_into_the_strike_exits_refused_2` |
| 4 | 90.45 | 2.1% | 90.45 | `tools/test_audio_distance_floor_refusal.py::test_full_run_with_bd5050_cut_into_the_strike_exits_refused_2` |
| 5 | 90.02 | 2.1% | 90.02 | `tools/test_cymbal_tone_render.py::test_the_hats_are_bit_exact_at_the_corners` |
| 6 | 89.11 | 2.0% | 89.11 | `tools/test_run_case.py::test_balance_trajectory_db_refuses_white_noise_at_both_operating_points` |
| 7 | 80.23 | 1.8% | 0.00 | `tools/test_cymbal_tone_render.py::test_the_clean_row_passes_every_decay_property` |
| 8 | 78.22 | 1.8% | 0.00 | `tools/probes/test_tom_distortion.py::test_every_clean_property_passes_so_the_matrix_is_not_vacuous` |
| 9 | 74.17 | 1.7% | 74.17 | `tools/test_performance_suite.py::test_synthtop_backend_full_suite_passes` |
| 10 | 69.51 | 1.6% | 69.51 | `tools/test_mutation_record_baselines.py::test_fpga_real_suite_kills_all_mutants_at_recorded_assertions` |
| 11 | 67.49 | 1.6% | 67.49 | `tools/test_pulse2x_headroom_refusal.py::test_normal_gain_in_probe_still_measures_and_writes_the_report` |
| 12 | 51.99 | 1.2% | 51.99 | `tools/test_cymbal_refusal_exit.py::test_cymbal_low_tail_full_run_records_prepare_refusal_not_traceback` |
| 13 | 51.44 | 1.2% | 51.44 | `tools/test_r0_capture.py::test_a_real_sound_defect_is_still_a_measured_fail_at_both_entry_points` |
| 14 | 45.79 | 1.1% | 45.79 | `tools/probes/dc_blocker.py::test_the_screen_brackets_the_rendered_attenuation_on_every_voice` |
| 15 | 45.02 | 1.0% | 45.02 | `tools/probes/dc_blocker.py::test_coupling_off_is_the_block_bit_for_bit` |
| 16 | 41.56 | 1.0% | 41.56 | `tools/test_stage_case.py::test_the_controls_command_refuses_every_injection` |
| 17 | 41.30 | 1.0% | 41.30 | `tools/test_mono_m5a_score.py::test_saw_cutoff_override_is_scored_on_complete_phrase_and_leaves_pulse_fixed` |
| 18 | 40.41 | 0.9% | 40.41 | `tools/test_pulse2x_headroom_refusal.py::test_silenced_pulse_gain_exits_refused_2_without_partial_report` |
| 19 | 38.62 | 0.9% | 38.62 | `tools/test_m1a_refusal_boundaries.py::test_unqualified_reference_manifest_exits_refused_2[analyse_m1a_phase_cycle.py-docs/scorecard/mono-m1a-miniv3/phase-cycle-capture/report.json]` |
| 20 | 38.57 | 0.9% | 38.57 | `tools/test_m1a_refusal_boundaries.py::test_non_refused_exception_is_not_swallowed_by_cli_boundary[analyse_m1a_phase_cycle.py-docs/scorecard/mono-m1a-miniv3/phase-cycle-capture/report.json]` |
| 21 | 36.88 | 0.8% | 36.88 | `tools/test_run_case.py::test_line_is_resolved_refuses_white_noise` |
| 22 | 36.53 | 0.8% | 36.53 | `tools/test_mono_wav_destination.py::test_the_unconditional_m5_write_is_a_control_that_stays_red` |
| 23 | 34.46 | 0.8% | 34.46 | `tools/probes/dc_blocker.py::test_the_coupled_switch_step_does_not_exceed_the_uncoupled_by_the_truncation_bound` |
| 24 | 34.42 | 0.8% | 34.42 | `tools/test_cymbal_tone_render.py::test_the_historical_saturation_is_caught` |
| 25 | 30.62 | 0.7% | 30.62 | `tools/test_pekonen_coloration_probe.py::test_every_injected_control_behaves` |

## Files (top 190 of 190)

| # | s | share | cum | tests | file |
|---:|---:|---:|---:|---:|---|
| 1 | 600.6 | 13.8% | 13.8% | 59 | `tools/test_r0_capture.py` |
| 2 | 285.2 | 6.6% | 20.4% | 26 | `tools/test_mono_wav_destination.py` |
| 3 | 282.4 | 6.5% | 26.9% | 23 | `tools/test_audio_distance_floor_refusal.py` |
| 4 | 256.4 | 5.9% | 32.8% | 61 | `tools/test_run_case.py` |
| 5 | 233.5 | 5.4% | 38.1% | 17 | `tools/test_cymbal_tone_render.py` |
| 6 | 209.3 | 4.8% | 43.0% | 34 | `tools/test_mutation_record_baselines.py` |
| 7 | 174.9 | 4.0% | 47.0% | 10 | `tools/probes/dc_blocker.py` |
| 8 | 169.0 | 3.9% | 50.9% | 44 | `tools/test_performance_suite.py` |
| 9 | 152.6 | 3.5% | 54.4% | 19 | `tools/test_pulse2x_headroom_refusal.py` |
| 10 | 149.5 | 3.4% | 57.8% | 21 | `tools/test_mono_m1a_score.py` |
| 11 | 131.6 | 3.0% | 60.8% | 30 | `tools/probes/test_tom_distortion.py` |
| 12 | 97.4 | 2.2% | 63.1% | 5 | `tools/test_analyse_m1a_phase_cycle.py` |
| 13 | 83.4 | 1.9% | 65.0% | 10 | `tools/test_stage_case.py` |
| 14 | 82.6 | 1.9% | 66.9% | 13 | `tools/test_m1a_refusal_boundaries.py` |
| 15 | 79.1 | 1.8% | 68.7% | 22 | `tools/test_mono_m5a_score.py` |
| 16 | 75.3 | 1.7% | 70.5% | 17 | `tools/test_r1_capture.py` |
| 17 | 64.2 | 1.5% | 71.9% | 12 | `tools/probes/test_rate_tol_sweep.py` |
| 18 | 62.6 | 1.4% | 73.4% | 143 | `tools/probes/test_tom_pitch_drop_fischer.py` |
| 19 | 62.4 | 1.4% | 74.8% | 24 | `tools/probes/test_tom_conga_gate.py` |
| 20 | 61.5 | 1.4% | 76.2% | 36 | `tools/probes/test_permitted_differences.py` |
| 21 | 53.9 | 1.2% | 77.5% | 5 | `tools/test_cymbal_refusal_exit.py` |
| 22 | 46.4 | 1.1% | 78.5% | 27 | `tools/test_m5_refusal_boundaries.py` |
| 23 | 46.4 | 1.1% | 79.6% | 3 | `tools/test_pekonen_coloration_probe.py` |
| 24 | 40.5 | 0.9% | 80.5% | 4 | `tools/test_measure_m5a_signal_path.py` |
| 25 | 40.5 | 0.9% | 81.5% | 29 | `tools/test_bd_pitch_predeclaration_mutants.py` |
| 26 | 37.0 | 0.9% | 82.3% | 25 | `tools/probes/test_check_doc_claims_789_marker_controls.py` |
| 27 | 32.8 | 0.8% | 83.1% | 14 | `tools/test_measure_mono_m1a_reference.py` |
| 28 | 32.6 | 0.7% | 83.8% | 14 | `tools/test_verify_109_corpus_gate.py` |
| 29 | 29.7 | 0.7% | 84.5% | 23 | `tools/test_cymbal_vca_clip.py` |
| 30 | 27.6 | 0.6% | 85.1% | 8 | `tools/probes/test_tier2_empty_records.py` |
| 31 | 27.0 | 0.6% | 85.8% | 7 | `tools/test_noise_stage_attribution.py` |
| 32 | 24.6 | 0.6% | 86.3% | 2 | `tools/probes/test_rate_fold_db_control.py` |
| 33 | 24.4 | 0.6% | 86.9% | 24 | `tools/test_perceptual_gate.py` |
| 34 | 22.2 | 0.5% | 87.4% | 28 | `tools/test_crash_rate.py` |
| 35 | 19.1 | 0.4% | 87.8% | 6 | `tools/test_perceptual_gate_neighbours.py` |
| 36 | 17.5 | 0.4% | 88.2% | 12 | `tools/test_qualify_m1a_attack.py` |
| 37 | 17.4 | 0.4% | 88.6% | 28 | `tools/test_cymbal_tone_nodal.py` |
| 38 | 17.0 | 0.4% | 89.0% | 31 | `tools/test_cymbal_low_tail.py` |
| 39 | 16.8 | 0.4% | 89.4% | 7 | `tools/test_corpus_path_single_source.py` |
| 40 | 16.4 | 0.4% | 89.8% | 9 | `tools/probes/test_dc_blocker_apparatus.py` |
| 41 | 15.8 | 0.4% | 90.2% | 11 | `tools/test_measure_osc_drift_reference.py` |
| 42 | 14.4 | 0.3% | 90.5% | 26 | `tools/test_diagnose_m1a_harmonic_phase.py` |
| 43 | 14.2 | 0.3% | 90.8% | 1 | `tools/test_metric_purpose.py` |
| 44 | 13.8 | 0.3% | 91.1% | 9 | `tools/probes/test_repeat_period_multiple.py` |
| 45 | 13.6 | 0.3% | 91.4% | 107 | `tools/test_check_workflows.py` |
| 46 | 13.4 | 0.3% | 91.8% | 28 | `tools/probes/test_holdout_control_apparatus.py` |
| 47 | 13.2 | 0.3% | 92.1% | 30 | `tools/test_bd_pitch_predeclaration.py` |
| 48 | 13.0 | 0.3% | 92.4% | 43 | `tools/test_cymbal_tone_knob.py` |
| 49 | 12.6 | 0.3% | 92.6% | 35 | `tools/probes/test_residual_dc.py` |
| 50 | 12.0 | 0.3% | 92.9% | 46 | `tools/test_measure_repeatability.py` |
| 51 | 11.9 | 0.3% | 93.2% | 11 | `tools/test_pytest_collection_inventory.py` |
| 52 | 11.8 | 0.3% | 93.5% | 32 | `tools/test_cymbal_vca_drive.py` |
| 53 | 11.8 | 0.3% | 93.7% | 12 | `tools/test_mono_artifact_probe.py` |
| 54 | 11.1 | 0.3% | 94.0% | 133 | `tools/test_trial.py` |
| 55 | 10.7 | 0.2% | 94.2% | 2 | `tools/test_refusal_exit_census.py` |
| 56 | 9.6 | 0.2% | 94.5% | 33 | `tools/test_wav_destination.py` |
| 57 | 9.2 | 0.2% | 94.7% | 14 | `tools/test_f1_level_scaling.py` |
| 58 | 8.5 | 0.2% | 94.9% | 22 | `tools/test_check_doc_claims.py` |
| 59 | 8.4 | 0.2% | 95.1% | 20 | `tools/test_run_all.py` |
| 60 | 8.3 | 0.2% | 95.3% | 22 | `tools/test_pitch_trajectory.py` |
| 61 | 7.3 | 0.2% | 95.4% | 15 | `tools/test_measure_mono_case.py` |
| 62 | 7.2 | 0.2% | 95.6% | 9 | `tools/test_rs_guard_band_refusal.py` |
| 63 | 7.1 | 0.2% | 95.8% | 2 | `tools/test_bd_pitch_baseline.py` |
| 64 | 6.8 | 0.2% | 95.9% | 12 | `tools/test_vacuous_guard_audit.py` |
| 65 | 6.8 | 0.2% | 96.1% | 7 | `tools/probes/test_fold_rate_sweep.py` |
| 66 | 6.7 | 0.2% | 96.2% | 38 | `tools/probes/test_cymbal_400_production_tail.py` |
| 67 | 6.6 | 0.2% | 96.4% | 4 | `tools/probes/test_perturbation_ladder.py` |
| 68 | 6.5 | 0.1% | 96.5% | 9 | `tools/test_rounded_verdict_census.py` |
| 69 | 6.3 | 0.1% | 96.7% | 7 | `tools/test_f1_rtl_filter_path.py` |
| 70 | 6.2 | 0.1% | 96.8% | 31 | `tools/test_manifest.py` |
| 71 | 5.5 | 0.1% | 96.9% | 32 | `tools/test_gate_calibrate.py` |
| 72 | 5.3 | 0.1% | 97.1% | 8 | `tools/probes/test_estimator_ground_truth_summaries.py` |
| 73 | 5.3 | 0.1% | 97.2% | 28 | `tools/test_f1_response_destination.py` |
| 74 | 5.2 | 0.1% | 97.3% | 11 | `tools/test_holdout_injected_controls.py` |
| 75 | 5.1 | 0.1% | 97.4% | 12 | `tools/test_measure_saw_alias_after_2x.py` |
| 76 | 3.7 | 0.1% | 97.5% | 3 | `tools/test_prose_literal_audit.py` |
| 77 | 3.6 | 0.1% | 97.6% | 12 | `tools/test_compare_m5a_i2s_candidate.py` |
| 78 | 3.6 | 0.1% | 97.7% | 6 | `tools/test_dphase_floor_census.py` |
| 79 | 3.4 | 0.1% | 97.7% | 11 | `tools/test_probe_labels.py` |
| 80 | 3.1 | 0.1% | 97.8% | 7 | `tools/test_checkout_staleness.py` |
| 81 | 3.0 | 0.1% | 97.9% | 20 | `tools/test_midi_session_kit_refusal.py` |
| 82 | 3.0 | 0.1% | 97.9% | 10 | `tools/probes/excitation_energy.py` |
| 83 | 3.0 | 0.1% | 98.0% | 7 | `tools/test_capture_m1a_phase_cycle.py` |
| 84 | 2.9 | 0.1% | 98.1% | 32 | `tools/test_measure_conga_body_spread.py` |
| 85 | 2.8 | 0.1% | 98.2% | 3 | `tools/test_ab_808_refusal.py` |
| 86 | 2.8 | 0.1% | 98.2% | 3 | `tools/test_probe_tom_numerator_refusal.py` |
| 87 | 2.7 | 0.1% | 98.3% | 2 | `tools/test_check_refused_base.py` |
| 88 | 2.7 | 0.1% | 98.3% | 3 | `tools/test_balance_line_shape_refusal.py` |
| 89 | 2.7 | 0.1% | 98.4% | 3 | `tools/test_estimator_defects_refusal.py` |
| 90 | 2.6 | 0.1% | 98.5% | 8 | `tools/test_cymbal_m_origin.py` |
| 91 | 2.6 | 0.1% | 98.5% | 24 | `tools/test_cymbal_mid.py` |
| 92 | 2.6 | 0.1% | 98.6% | 21 | `tools/test_check_arty_evidence_binding.py` |
| 93 | 2.5 | 0.1% | 98.6% | 10 | `tools/test_qualify_modeld_pedalboard.py` |
| 94 | 2.5 | 0.1% | 98.7% | 2 | `tools/test_control_harness_baselines.py` |
| 95 | 2.4 | 0.1% | 98.8% | 27 | `tools/test_cymbal_band_balance.py` |
| 96 | 2.3 | 0.1% | 98.8% | 15 | `tools/test_run_controls.py` |
| 97 | 2.2 | 0.1% | 98.9% | 4 | `tools/probes/test_octave_margin_sweep.py` |
| 98 | 2.2 | 0.1% | 98.9% | 12 | `tools/test_verify_attack_context_model.py` |
| 99 | 2.1 | 0.0% | 99.0% | 1 | `tools/test_confirm_m5_saw_drive.py` |
| 100 | 2.0 | 0.0% | 99.0% | 16 | `tools/test_compile_dag_evidence.py` |
| 101 | 1.9 | 0.0% | 99.0% | 19 | `tools/test_verify_deadline.py` |
| 102 | 1.9 | 0.0% | 99.1% | 12 | `tools/probes/test_estimator_defects.py` |
| 103 | 1.9 | 0.0% | 99.1% | 1 | `tools/test_measure_repeatability_refusal.py` |
| 104 | 1.9 | 0.0% | 99.2% | 10 | `tools/test_cymbal_bands.py` |
| 105 | 1.8 | 0.0% | 99.2% | 16 | `tools/test_reference_freeze.py` |
| 106 | 1.7 | 0.0% | 99.3% | 16 | `tools/test_refusal_candidate_trace.py` |
| 107 | 1.4 | 0.0% | 99.3% | 20 | `tools/test_run_with_rss.py` |
| 108 | 1.4 | 0.0% | 99.3% | 11 | `tools/test_capability_pack.py` |
| 109 | 1.2 | 0.0% | 99.3% | 51 | `tools/test_signoff_klt_version.py` |
| 110 | 1.2 | 0.0% | 99.4% | 18 | `tools/test_check_decision_record_numbers.py` |
| 111 | 1.2 | 0.0% | 99.4% | 5 | `tools/probes/hihat/test_hh_probe5_refused.py` |
| 112 | 1.2 | 0.0% | 99.4% | 14 | `tools/test_tone_stage_schematic.py` |
| 113 | 1.1 | 0.0% | 99.5% | 8 | `tools/test_fpga_uncovered_workflow.py` |
| 114 | 1.1 | 0.0% | 99.5% | 4 | `tools/test_measure_m1a_volume_mapping.py` |
| 115 | 1.1 | 0.0% | 99.5% | 3 | `tools/probes/test_dominant_period_octave.py` |
| 116 | 1.1 | 0.0% | 99.5% | 5 | `tools/test_child_failure_escape_census.py` |
| 117 | 0.9 | 0.0% | 99.6% | 29 | `tools/test_promoted_bands.py` |
| 118 | 0.9 | 0.0% | 99.6% | 1 | `tools/test_wav_reader_refusal.py` |
| 119 | 0.9 | 0.0% | 99.6% | 20 | `tools/test_holdout.py` |
| 120 | 0.9 | 0.0% | 99.6% | 2 | `tools/test_r1_scorecard.py` |
| 121 | 0.8 | 0.0% | 99.6% | 13 | `tools/test_characterize_synth_top.py` |
| 122 | 0.8 | 0.0% | 99.6% | 3 | `tools/test_f1_selected_path.py` |
| 123 | 0.8 | 0.0% | 99.7% | 20 | `tools/test_sensitivity.py` |
| 124 | 0.8 | 0.0% | 99.7% | 7 | `tools/probes/test_estimator_fixtures.py` |
| 125 | 0.8 | 0.0% | 99.7% | 18 | `tools/test_diff_scorecard_record.py` |
| 126 | 0.7 | 0.0% | 99.7% | 20 | `tools/test_reference_freshness.py` |
| 127 | 0.7 | 0.0% | 99.7% | 70 | `tools/test_refusal_sibling_scan.py` |
| 128 | 0.7 | 0.0% | 99.8% | 12 | `tools/test_werner_fig9.py` |
| 129 | 0.6 | 0.0% | 99.8% | 6 | `tools/test_compare_r2_pulse.py` |
| 130 | 0.6 | 0.0% | 99.8% | 10 | `tools/test_dsp_dpreg_refusal_cases.py` |
| 131 | 0.5 | 0.0% | 99.8% | 4 | `tools/test_measure_m5_budget_refusal.py` |
| 132 | 0.5 | 0.0% | 99.8% | 4 | `tools/test_measure_pulse2x_headroom.py` |
| 133 | 0.5 | 0.0% | 99.8% | 16 | `tools/test_ci_changes.py` |
| 134 | 0.5 | 0.0% | 99.8% | 2 | `tools/test_rate_conv_2x.py` |
| 135 | 0.4 | 0.0% | 99.8% | 6 | `tools/test_regen_discrimination_ours.py` |
| 136 | 0.4 | 0.0% | 99.8% | 6 | `tools/test_r1_harvest.py` |
| 137 | 0.4 | 0.0% | 99.9% | 10 | `tools/test_cymbal_tone_realisation.py` |
| 138 | 0.4 | 0.0% | 99.9% | 12 | `tools/test_setup_ci_verilator.py` |
| 139 | 0.4 | 0.0% | 99.9% | 7 | `tools/test_check_surge_waveform_comment.py` |
| 140 | 0.3 | 0.0% | 99.9% | 1 | `tools/test_measure_mono_attack_context.py` |
| 141 | 0.3 | 0.0% | 99.9% | 2 | `tools/test_measure_shark_blamp.py` |
| 142 | 0.3 | 0.0% | 99.9% | 1 | `tools/test_measure_mono_pulse_2x.py` |
| 143 | 0.3 | 0.0% | 99.9% | 1 | `tools/test_cymbal_tone_writeup_figures.py` |
| 144 | 0.3 | 0.0% | 99.9% | 5 | `tools/test_gate_between.py` |
| 145 | 0.2 | 0.0% | 99.9% | 8 | `tools/test_render_miniv4_suite.py` |
| 146 | 0.2 | 0.0% | 99.9% | 10 | `tools/test_check_m1a_selection.py` |
| 147 | 0.2 | 0.0% | 99.9% | 3 | `tools/probes/test_balance_fit_columns.py` |
| 148 | 0.2 | 0.0% | 99.9% | 4 | `tools/test_measure_dag_churn.py` |
| 149 | 0.2 | 0.0% | 99.9% | 8 | `tools/test_check_iverilog_generate_case_string.py` |
| 150 | 0.2 | 0.0% | 99.9% | 6 | `tools/test_measure_m5a_attack_bias.py` |
| 151 | 0.2 | 0.0% | 99.9% | 12 | `tools/test_compare_candidate_tails.py` |
| 152 | 0.2 | 0.0% | 99.9% | 4 | `tools/test_dsp_dpreg_analyse.py` |
| 153 | 0.2 | 0.0% | 99.9% | 6 | `tools/test_compile_dag_churn.py` |
| 154 | 0.2 | 0.0% | 100.0% | 2 | `tools/probes/test_audio_distance_floor_sr.py` |
| 155 | 0.2 | 0.0% | 100.0% | 4 | `tools/test_score_f1_rtl.py` |
| 156 | 0.2 | 0.0% | 100.0% | 3 | `tools/test_i2s_transcript.py` |
| 157 | 0.1 | 0.0% | 100.0% | 4 | `tools/test_dsp_dpreg_extract.py` |
| 158 | 0.1 | 0.0% | 100.0% | 4 | `tools/probes/trajectory_within_rate_drift.py` |
| 159 | 0.1 | 0.0% | 100.0% | 10 | `tools/test_refprofile_post_render.py` |
| 160 | 0.1 | 0.0% | 100.0% | 5 | `tools/test_refaudio_s3.py` |
| 161 | 0.1 | 0.0% | 100.0% | 1 | `tools/test_audit_tracked_json_writers.py` |
| 162 | 0.1 | 0.0% | 100.0% | 11 | `tools/test_score_ensemble_i2s.py` |
| 163 | 0.1 | 0.0% | 100.0% | 8 | `tools/test_refaudio_fetch.py` |
| 164 | 0.1 | 0.0% | 100.0% | 4 | `tools/test_check_attack_context_rebind.py` |
| 165 | 0.1 | 0.0% | 100.0% | 7 | `tools/test_provenance_git_failure.py` |
| 166 | 0.1 | 0.0% | 100.0% | 1 | `tools/test_measure_partial_balance.py` |
| 167 | 0.1 | 0.0% | 100.0% | 4 | `tools/test_refprofile.py` |
| 168 | 0.1 | 0.0% | 100.0% | 4 | `tools/test_probe_orphan_reap.py` |
| 169 | 0.1 | 0.0% | 100.0% | 1 | `tools/test_scorecard.py` |
| 170 | 0.1 | 0.0% | 100.0% | 3 | `tools/test_rungs_gate.py` |
| 171 | 0.1 | 0.0% | 100.0% | 5 | `tools/test_preparation_contract.py` |
| 172 | 0.0 | 0.0% | 100.0% | 1 | `tools/test_score_m5a_i2s.py` |
| 173 | 0.0 | 0.0% | 100.0% | 2 | `tools/probes/test_noise_fixture_duration.py` |
| 174 | 0.0 | 0.0% | 100.0% | 3 | `tools/test_werner_fig4.py` |
| 175 | 0.0 | 0.0% | 100.0% | 4 | `tools/test_measure_m5a_filter_headroom.py` |
| 176 | 0.0 | 0.0% | 100.0% | 1 | `tools/test_m5a_fast_workflow.py` |
| 177 | 0.0 | 0.0% | 100.0% | 2 | `tools/probes/test_dc_t20_gate_qualification.py` |
| 178 | 0.0 | 0.0% | 100.0% | 2 | `tools/test_envelope_history_337.py` |
| 179 | 0.0 | 0.0% | 100.0% | 1 | `tools/test_deadline_binding_probe.py` |
| 180 | 0.0 | 0.0% | 100.0% | 1 | `tools/test_measure_m5a_saw_cutoff.py` |
| 181 | 0.0 | 0.0% | 100.0% | 2 | `tools/test_kweight_single_source.py` |
| 182 | 0.0 | 0.0% | 100.0% | 2 | `tools/test_clap_d12a_probe.py` |
| 183 | 0.0 | 0.0% | 100.0% | 2 | `tools/test_diagnose_tom_body.py` |
| 184 | 0.0 | 0.0% | 100.0% | 2 | `tools/test_provenance_retention.py` |
| 185 | 0.0 | 0.0% | 100.0% | 2 | `tools/test_atomic_record_writers.py` |
| 186 | 0.0 | 0.0% | 100.0% | 1 | `tools/test_compile_dag_run_persistence.py` |
| 187 | 0.0 | 0.0% | 100.0% | 1 | `tools/probes/test_corner_reference_convention.py` |
| 188 | 0.0 | 0.0% | 100.0% | 1 | `tools/test_compile_dag_deps.py` |
| 189 | 0.0 | 0.0% | 100.0% | 1 | `tools/probes/test_octave_incidence_corpus.py` |
| 190 | 0.0 | 0.0% | 100.0% | 1 | `tools/test_corpus_lineage.py` |

## Candidate groups (they may overlap; shares do not sum to 100%)

| group | files | s | share | files with no duration line |
|---|---:|---:|---:|---|
| iverilog-5 | 5 | 13.7 | 0.3% | `tools/test_check_f1_rtl_record.py` |
| probe-refusal-1773 | 1 | 282.4 | 6.5% |  |
| refusal-named | 20 | 659.0 | 15.2% | `tools/test_refusal.py` |
| iverilog-mentions | 10 | 19.3 | 0.4% | `tools/test_check_f1_rtl_record.py`, `tools/test_score_drum_i2s.py` |
| imports-subprocess | 60 | 1543.4 | 35.5% | `tools/test_pulse2x_configuration.py`, `tools/test_push_dag_records.py`, `tools/test_verify_mono_case.py` |

## Concentration

- files to reach 50 / 80 / 90% of listed time: 8 / 24 / 41 of 190
- tests to reach 50 / 80 / 90% of listed time: 41 / 150 / 321 of 2751
- top-5 files hold 38.1%; top-10 files 57.8%; top-25 tests 40.5%
