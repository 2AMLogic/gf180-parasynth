"""Independent controls for causal rate-converter measurement instrumentation."""

import json

import pytest

import measure_m5a_filter_headroom as headroom


def test_control_latency_diagnostic_requires_and_measures_the_injected_step():
    result = headroom._measure_control_response()
    assert result["step_frame"] == 100
    assert 0 <= result["first_changed_latency_frames"] <= 10
    quantiles = result["squared_response_energy_quantiles_frames_from_start"]
    assert quantiles["0.1"] <= quantiles["0.5"] <= quantiles["0.9"]
    assert quantiles["0.9"] < 512


@pytest.mark.parametrize("value", [10**400, None, "not-a-number"],
                         ids=["beyond-float", "null", "non-numeric-string"])
def test_recompare_report_refuses_a_partial_that_float_cannot_read(tmp_path, capsys, value):
    """#1680, the second shipped path: `--recompare-report` hands JSON-loaded
    rows straight to `_compare_incremental`, with no record gate in front, so
    its unguarded `float()` left as a traceback -- exit 1 -- here too. The
    reason is asserted because any refusal would give the exit code."""
    shipped = headroom.ROOT / "docs/scorecard/mono-m5a-miniv3/filter-headroom-v1.json"
    report = json.loads(shipped.read_text())
    row = report["configurations"]["pulse29"]["headroom"]
    row["event_diagnostics"][0]["harmonic_error_db_model_minus_reference"]["h2"] = value
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))

    assert headroom.main(["--recompare-report", str(path)]) == 2

    assert ("REFUSED: candidate saw/84 harmonic partial h2 cannot be read as a float"
            in capsys.readouterr().out)
    assert path.read_text() == json.dumps(report)
