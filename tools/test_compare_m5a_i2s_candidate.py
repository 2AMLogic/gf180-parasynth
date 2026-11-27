import copy
import json

import pytest

import compare_m5a_i2s_candidate as compare
import mono_m5a_score as m5a


def _record(*, ref="a", engine="integrated-rtl", events=None, harmonic=-2.0):
    units = {"Pitch": "cents", "Harmonic shape": "dB", "Foldback energy": "dB",
             "Envelope attack": "ms", "Envelope release": "ms", "Gain": "dB", "Clipping": "%"}
    metrics = {name: {"error": 0.5, "tolerance": limit, "units": units[name],
                      "tolerance_basis": basis, "valid": True}
               for name, (limit, basis) in m5a.TOLERANCES.items()}
    if events is None:
        events = [_event("saw", 84, alias=0.0, harmonic=harmonic)]
    return {"engine": engine, "case_id": "M5A", "scorecard_state": "fail",
            "analysis_version": "m5a-score-v3",
            "reference_profile": "frozen Mini V3",
            "tolerance_policy": copy.deepcopy(m5a.TOLERANCES),
            "metrics": metrics, "diagnostics": {
                "reference_sha256": ref, "reference_manifest_sha256": "manifest",
                "decoded_i2s_sha256": "audio", "events": events},
            "provenance": {"config": {"filter_config": "causal reconstructed 2x, headroom preserved",
                                       "saw_cutoff_hz": 20_000,
                                       "saw_volume_correction_db": -0.45428}}}


def _event(wave, midi, *, alias, harmonic=-2.0):
    return {"wave": wave, "midi": midi,
            "pitch_cents_from_midi": {"model_minus_reference": 0.1},
            "harmonic_error_db_model_minus_reference": {"h2": harmonic},
            "foldback_db": {"excess_over_reference_db": alias},
            "envelope_ms": {"attack_model": 8.0, "attack_reference": 7.0,
                            "release_model": 1200.0, "release_reference": 1201.0},
            "gain_dbfs": {"model": -19.0, "reference": -19.2}}


@pytest.fixture
def scored_records():
    path = compare.ROOT / "docs/scorecard/mono-m5a-miniv3/saw-cutoff-20khz-i2s.json"
    baseline = json.loads(path.read_text())
    return baseline, copy.deepcopy(baseline)


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("excess", [
    "NaN", -1, "Infinity", "-Infinity", False, None, pytest.param(10**400, id="overflow"),
])
def test_refuses_damaged_per_note_foldback(scored_records, side, excess):
    baseline, candidate = scored_records
    record = baseline if side == "baseline" else candidate
    record["diagnostics"]["events"][0]["foldback_db"]["excess_over_reference_db"] = excess
    with pytest.raises(m5a.Refused, match=rf"{side}:saw/84.*foldback"):
        compare.compare_records(baseline, candidate)


@pytest.mark.parametrize("excess, category", [
    (1.9171, "preserved_per_note_passes"),
    (0.0, "preserved_per_note_passes"),
    (3.0, "preserved_per_note_passes"),
    (3.01, "lost_per_note_passes"),
])
def test_reports_recorded_saw_foldback(scored_records, excess, category):
    baseline, candidate = scored_records
    candidate["diagnostics"]["events"][0]["foldback_db"]["excess_over_reference_db"] = excess
    report = compare.compare_records(baseline, candidate)["comparison"]
    assert {"wave": "saw", "midi": 84, "property": "Foldback energy",
            "baseline_error": 1.9171, "candidate_error": excess, "limit": 3.0} in report[category]


def test_reports_full_integrated_property_vector_and_acceptance():
    baseline = _record(events=[_event("saw", 84, alias=0.0, harmonic=-4.0)])
    candidate = _record(events=[_event("saw", 84, alias=1.9, harmonic=-3.0)])
    result = compare.compare_records(baseline, candidate)
    assert result["comparison"]["accepts_incremental_improvement"] is True
    assert result["comparison"]["regressed_components"] == []
    assert result["comparison"]["improved_components"]
    assert result["comparison"]["degraded_but_within_per_note_limit"][0]["property"] == "Foldback energy"


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


@pytest.mark.parametrize("mutation,match", [
    (lambda rec: rec["metrics"]["Pitch"].update(valid=False), "Pitch is invalid"),
    (lambda rec: rec.update(analysis_version="m5a-score-v999"), "analysis-version"),
    (lambda rec: rec["metrics"]["Gain"].update(tolerance=30.0), "measurement contract"),
    (lambda rec: rec["tolerance_policy"].update(Gain=(30.0, "loose")), "tolerance policy"),
    (lambda rec: rec.update(reference_profile="different patch"), "reference profiles differ"),
])
def test_refuses_invalid_properties_or_incomparable_measurement_basis(mutation, match):
    baseline, candidate = _record(), _record()
    mutation(candidate)
    with pytest.raises(m5a.Refused, match=match):
        compare.compare_records(baseline, candidate)


@pytest.mark.parametrize("mutation", [
    lambda rec: rec.update(provenance=None),
    lambda rec: rec.update(provenance=[]),
    lambda rec: rec.update(provenance="frozen Mini V3"),
    lambda rec: rec.update(provenance={}),
    lambda rec: rec["provenance"].update(config=None),
    lambda rec: rec["provenance"].update(config=[]),
])
def test_refuses_null_or_non_object_baseline_provenance_or_config(mutation):
    baseline, candidate = _record(), _record()
    mutation(baseline)
    with pytest.raises(m5a.Refused, match="baseline provenance"):
        compare.compare_records(baseline, candidate)


@pytest.mark.parametrize("mutation", [
    lambda rec: rec.update(provenance=None),
    lambda rec: rec.update(provenance=[]),
    lambda rec: rec.update(provenance="frozen Mini V3"),
    lambda rec: rec.update(provenance={}),
    lambda rec: rec["provenance"].update(config=None),
    lambda rec: rec["provenance"].update(config=[]),
])
def test_refuses_null_or_non_object_candidate_provenance_or_config(mutation):
    baseline, candidate = _record(), _record()
    mutation(candidate)
    with pytest.raises(m5a.Refused, match="candidate provenance"):
        compare.compare_records(baseline, candidate)


def test_rejects_candidate_that_loses_a_per_note_alias_pass_hidden_by_pulse():
    baseline = _record(events=[_event("saw", 84, alias=0.0),
                               _event("pulse", 84, alias=10.0)])
    candidate = _record(events=[_event("saw", 84, alias=3.01),
                                _event("pulse", 84, alias=10.0)])
    result = compare.compare_records(baseline, candidate)
    assert result["comparison"]["case_passes"] is False
    assert result["comparison"]["accepts_incremental_improvement"] is False
    assert result["comparison"]["lost_per_note_passes"] == [{
        "wave": "saw", "midi": 84, "property": "Foldback energy",
        "baseline_error": 0.0, "candidate_error": 3.01, "limit": 3.0}]
