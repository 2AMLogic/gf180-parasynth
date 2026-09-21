from __future__ import annotations

import numpy as np
import pytest

import measure_m5a_signal_path as probe


def test_q15_trace_conversion_uses_one_full_scale_for_every_stage():
    got = probe._q15_to_full_scale(np.array([-32768, 0, 32767], dtype=np.int16))
    assert got.tolist() == [-1.0, 0.0, pytest.approx(32767 / 32768)]


def test_full_scale_conversion_is_gain_linear():
    q15 = np.array([-16384, 8192, 24576], dtype=np.int32)
    assert probe._q15_to_full_scale(q15) == pytest.approx([-0.5, 0.25, 0.75])


def test_stage_probe_constructs_the_selected_production_filter_path():
    voice = probe._selected_candidate_voice()
    assert voice.oversample_2x
    assert voice.rate_converted_ladder
    assert voice.ladder_cfg["oversample"] == 2
    assert voice.preserve_filter_headroom
    assert voice.causal_filter


def test_polyblep_off_is_an_executed_distinct_oscillator_challenger():
    baseline = probe.measure([14073], [0.75], blep=True)
    candidate = probe.measure([14073], [0.75], blep=False)
    base_run, run = baseline["runs"][0], candidate["runs"][0]

    assert "PolyBLEP disabled" in candidate["oscillator_config"]
    assert "causal reconstructed 2x ladder with headroom preserved" in candidate["filter_config"]
    assert baseline["filter_config"] == candidate["filter_config"]
    assert candidate["reference_sha256"] == baseline["reference_sha256"]
    for got, old in zip(run["events"], base_run["events"], strict=True):
        assert got["midi"] == old["midi"]
        assert got["stages"]["oscillator"]["harmonic_error_db_model_minus_reference"] != \
               old["stages"]["oscillator"]["harmonic_error_db_model_minus_reference"]
        assert "excess_alias_db" in got["stages"]["output"]
        assert "output_gain_error_db" in got
