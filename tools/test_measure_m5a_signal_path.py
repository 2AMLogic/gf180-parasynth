from __future__ import annotations

import numpy as np
import pytest

import measure_m5a_signal_path as probe
import json
from pathlib import Path


def test_q15_trace_conversion_uses_one_full_scale_for_every_stage():
    got = probe._q15_to_full_scale(np.array([-32768, 0, 32767], dtype=np.int16))
    assert got.tolist() == [-1.0, 0.0, pytest.approx(32767 / 32768)]


def test_full_scale_conversion_is_gain_linear():
    q15 = np.array([-16384, 8192, 24576], dtype=np.int32)
    assert probe._q15_to_full_scale(q15) == pytest.approx([-0.5, 0.25, 0.75])


def test_polyblep_off_is_an_executed_distinct_oscillator_challenger():
    candidate = probe.measure([14073], [1.0], blep=False)
    baseline_path = Path(probe.ROOT) / "docs/scorecard/mono-m5a-miniv3/signal-path-alias-energy-v1.json"
    baseline = json.loads(baseline_path.read_text())["runs"][0]
    run = candidate["runs"][0]

    assert "PolyBLEP disabled" in candidate["oscillator_config"]
    assert candidate["reference_sha256"] == json.loads(baseline_path.read_text())["reference_sha256"]
    for got, old in zip(run["events"], baseline["events"], strict=True):
        assert got["midi"] == old["midi"]
        assert got["stages"]["oscillator"]["harmonic_error_db_model_minus_reference"] != \
               old["stages"]["oscillator"]["harmonic_error_db_model_minus_reference"]
        assert "excess_alias_db" in got["stages"]["output"]
        assert "output_gain_error_db" in got
