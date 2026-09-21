import copy

import pytest

import compare_m5a_i2s_candidate as compare
import mono_m5a_score as m5a


def _record(*, ref="a", engine="integrated-rtl", events=None, harmonic=-2.0):
    units = {"Pitch": "cents", "Harmonic shape": "dB", "Foldback energy": "dB",
             "Envelope attack": "ms", "Envelope release": "ms", "Gain": "dB", "Clipping": "%"}
    metrics = {name: {"error": 0.5, "tolerance": limit, "units": units[name], "valid": True}
               for name, (limit, _basis) in m5a.TOLERANCES.items()}
    if events is None:
        events = [{"wave": "saw", "midi": 84,
                   "harmonic_error_db_model_minus_reference": {"h2": harmonic}}]
    return {"engine": engine, "case_id": "M5A", "scorecard_state": "fail",
            "metrics": metrics, "diagnostics": {
                "reference_sha256": ref, "reference_manifest_sha256": "manifest",
                "decoded_i2s_sha256": "audio", "events": events},
            "provenance": {"config": {"filter_config": "causal reconstructed 2x, headroom preserved",
                                       "saw_cutoff_hz": 20_000,
                                       "saw_volume_correction_db": -0.45428}}}


def test_reports_full_integrated_property_vector_and_acceptance():
    baseline, candidate = _record(harmonic=-4.0), _record(harmonic=-3.0)
    result = compare.compare_records(baseline, candidate)
    assert result["comparison"]["accepts_incremental_improvement"] is True
    assert result["comparison"]["regressed_components"] == []
    assert result["comparison"]["improved_components"]


@pytest.mark.parametrize("mutation,match", [
    (lambda rec: rec.update(engine="fixed-model"), "integrated-rtl"),
    (lambda rec: rec["diagnostics"].update(reference_sha256="different"), "reference"),
    (lambda rec: rec["diagnostics"].update(events=[]), "event evidence"),
])
def test_refuses_unrelated_or_incomplete_baselines(mutation, match):
    baseline, candidate = _record(), _record()
    mutation(baseline)
    with pytest.raises(m5a.Refused, match=match):
        compare.compare_records(baseline, candidate)
