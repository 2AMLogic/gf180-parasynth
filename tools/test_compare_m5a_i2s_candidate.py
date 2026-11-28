import copy
import json
import subprocess
import sys

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
    "0_0", "-1e-400", "NaN", "Infinity", "-Infinity", False, None,
    -1, float("nan"), float("inf"), pytest.param(10**400, id="overflow"),
])
def test_refuses_damaged_per_note_foldback(scored_records, side, excess):
    baseline, candidate = scored_records
    record = baseline if side == "baseline" else candidate
    record["diagnostics"]["events"][0]["foldback_db"]["excess_over_reference_db"] = excess
    with pytest.raises(m5a.Refused, match=rf"{side}:saw/84.*foldback"):
        compare.compare_records(baseline, candidate)


_EVENT_NUMBERS = (("pitch_cents_from_midi", "model_minus_reference"),
                  ("envelope_ms", "attack_model"), ("envelope_ms", "attack_reference"),
                  ("envelope_ms", "release_model"), ("envelope_ms", "release_reference"),
                  ("gain_dbfs", "model"), ("gain_dbfs", "reference"))


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("section,key", _EVENT_NUMBERS)
@pytest.mark.parametrize("integer", [10**400, -(10**400)],
                         ids=["beyond-float", "beyond-float-negative"])
def test_refuses_per_note_numbers_beyond_float_range(scored_records, side, section, key, integer):
    """#1669: `float()` raises OverflowError on a JSON integer beyond float
    range. The handler around these numbers did not list it, so it left as a
    traceback -- exit 1, which tools/exit_contract.py defines as FAIL -- where
    the foldback excess above already REFUSES the same input. The second
    wording is the shared record gate's (#1638), so this control outlives a
    rebase onto it."""
    baseline, candidate = scored_records
    record = baseline if side == "baseline" else candidate
    record["diagnostics"]["events"][0][section][key] = integer
    with pytest.raises(m5a.Refused,
                       match=rf"{side}:saw/84 (?:lacks complete per-note|.*must be finite)"):
        compare.compare_records(baseline, candidate)


def test_a_per_note_integer_within_float_range_is_still_compared(scored_records):
    """The other half: a repair that refused every large number would pass
    the test above as easily."""
    baseline, candidate = scored_records
    candidate["diagnostics"]["events"][0]["pitch_cents_from_midi"]["model_minus_reference"] = 10**300
    comparison = compare.compare_records(baseline, candidate)["comparison"]
    assert [row["property"] for row in comparison["lost_per_note_passes"]] == ["Pitch"]


@pytest.mark.parametrize("section,key", [
    ("pitch_cents_from_midi", "model_minus_reference"),
    ("envelope_ms", "attack_model"),
    ("gain_dbfs", "model"),
])
def test_cli_refuses_a_per_note_integer_beyond_float_range_with_exit_2(
        scored_records, tmp_path, section, key):
    """Through the shipped command: REFUSED is exit 2 and writes no report."""
    baseline, candidate = scored_records
    candidate["diagnostics"]["events"][0][section][key] = 10**400
    baseline_path, candidate_path = tmp_path / "baseline.json", tmp_path / "candidate.json"
    baseline_path.write_text(json.dumps(baseline))
    candidate_path.write_text(json.dumps(candidate))
    report = tmp_path / "delta.json"
    done = subprocess.run(
        [sys.executable, str(compare.ROOT / "tools" / "compare_m5a_i2s_candidate.py"),
         str(baseline_path), str(candidate_path), "--out", str(report)],
        capture_output=True, text=True)
    assert done.returncode == 2, done.stderr
    assert "Traceback" not in done.stderr
    assert "REFUSED" in done.stdout
    assert not report.exists()


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("token", ["-1e-400", "0.0", "-0.0", "-0e-400"])
def test_foldback_at_zero_from_json(scored_records, tmp_path, side, token):
    original, modified = scored_records
    original_path = tmp_path / "original.json"
    original_path.write_text(json.dumps(original))
    modified["diagnostics"]["events"][0]["foldback_db"]["excess_over_reference_db"] = "TOKEN"
    modified_path = tmp_path / "modified.json"
    modified_path.write_text(json.dumps(modified).replace('"TOKEN"', token))
    paths = (modified_path, original_path) if side == "baseline" else (original_path, modified_path)

    if token == "-1e-400":
        with pytest.raises(m5a.Refused, match=rf"{side}:saw/84.*foldback"):
            compare.compare_files(*paths)
    else:
        report = compare.compare_files(*paths)
        passes = report["comparison"]["preserved_per_note_passes"]
        [foldback] = [row for row in passes if (row["wave"], row["midi"], row["property"])
                      == ("saw", 84, "Foldback energy")]
        assert foldback[f"{side}_error"] == 0.0


@pytest.mark.parametrize("excess, category", [
    (1.9171, "preserved_per_note_passes"),
    (0.0, "preserved_per_note_passes"),
    (3, "preserved_per_note_passes"),
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


def _event_index(record, wave, midi):
    [index] = [n for n, event in enumerate(record["diagnostics"]["events"])
               if (event["wave"], event["midi"]) == (wave, midi)]
    return index


def _set_partial(record, index, value):
    record["diagnostics"]["events"][index]["harmonic_error_db_model_minus_reference"]["h2"] = value


#: #1680: `filter_gate._compare_incremental` reads the harmonic partials
#: before `_event_errors` does. Each value below raised out of its unguarded
#: `float()` -- a traceback, exit 1, which tools/exit_contract.py defines as
#: FAIL -- where the foldback excess above REFUSES the same value.
_UNREADABLE_PARTIALS = [pytest.param(10**400, id="beyond-float"),
                        pytest.param(-(10**400), id="beyond-float-negative"),
                        pytest.param(None, id="null"),
                        pytest.param("not-a-number", id="non-numeric-string")]


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("value", _UNREADABLE_PARTIALS)
def test_refuses_a_harmonic_partial_that_float_cannot_read(scored_records, side, value):
    baseline, candidate = scored_records
    record = baseline if side == "baseline" else candidate
    _set_partial(record, _event_index(record, "saw", 84), value)
    with pytest.raises(m5a.Refused,
                       match=rf"{side} saw/84 harmonic partial h2 cannot be read as a float"):
        compare.compare_records(baseline, candidate)


@pytest.mark.parametrize("integer", [3, 10**300], ids=["small", "large-in-range"])
def test_a_readable_integer_partial_is_still_compared(scored_records, integer):
    """The other half of the control above: a repair that refused every
    integer partial would satisfy it as easily."""
    baseline, candidate = scored_records
    _set_partial(candidate, _event_index(candidate, "pulse", 84), integer)
    comparison = compare.compare_records(baseline, candidate)["comparison"]
    assert comparison["regressed_components"] == ["partial:2:pulse:84:h2"]
    assert comparison["accepts_incremental_improvement"] is False
    assert {"wave": "pulse", "midi": 84, "property": "Harmonic shape/h2",
            "baseline_error": 0.4802, "candidate_error": float(integer),
            "limit": 1.0} in comparison["lost_per_note_passes"]


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")],
                         ids=["nan", "inf", "-inf"])
def test_a_non_finite_partial_is_refused_by_the_second_read(scored_records, side, number):
    """The adversary of the first read's guard: `float()` raises nothing for
    a non-finite number, so that guard never sees it. `compare_records` reads
    the partials twice and `_event_errors` refuses it there. Only the identity
    is matched, so the control outlives the shared record gate's wording."""
    baseline, candidate = scored_records
    record = baseline if side == "baseline" else candidate
    _set_partial(record, _event_index(record, "saw", 84), number)
    with pytest.raises(m5a.Refused, match=rf"{side}:saw/84"):
        compare.compare_records(baseline, candidate)


def _sandbox_repository(root, files):
    """A throwaway git repository holding `files`, so the shipped command can
    write its report inside the tree it describes."""
    for rel, body in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    env = ("-c", "user.email=t@t", "-c", "user.name=t")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), *env, "commit", "-qm", "sandbox"], check=True)


@pytest.mark.parametrize("partial, verdict", [(10**400, 2), (3, 0)],
                         ids=["beyond-float-is-refused", "in-range-is-reported"])
def test_main_refuses_a_partial_beyond_float_range_and_reports_one_in_range(
        scored_records, tmp_path, monkeypatch, capsys, partial, verdict):
    """Through `main`, with a report path inside the tree: the refusal writes
    no report, and the same apparatus does write one for a readable partial
    that regresses -- so "no report" is the refusal and not the out path."""
    baseline, candidate = scored_records
    _set_partial(candidate, _event_index(candidate, "pulse", 84), partial)
    _sandbox_repository(tmp_path, {rel: f"# {rel}\n" for rel in compare.SOURCES})
    monkeypatch.setattr(compare, "ROOT", tmp_path)
    baseline_path, candidate_path = tmp_path / "baseline.json", tmp_path / "candidate.json"
    baseline_path.write_text(json.dumps(baseline))
    candidate_path.write_text(json.dumps(candidate))
    report = tmp_path / "out" / "delta.json"

    rc = compare.main([str(baseline_path), str(candidate_path), "--out", "out/delta.json"])

    assert rc == verdict
    if verdict == 2:
        assert ("compare_m5a_i2s_candidate: REFUSED -- candidate pulse/84 harmonic partial h2 "
                "cannot be read as a float") in capsys.readouterr().out
        assert not report.exists()
    else:
        comparison = json.loads(report.read_text())["comparison"]
        assert comparison["regressed_components"] == ["partial:2:pulse:84:h2"]
        assert comparison["accepts_incremental_improvement"] is False


def test_cli_refuses_a_harmonic_partial_beyond_float_range_with_exit_2(scored_records, tmp_path):
    """#1680's repro, as the shipped command: exit 2, no traceback, no report.
    The reason is asserted, not only the word REFUSED: an `--out` outside the
    repository is refused with that word after the comparison has run."""
    baseline, candidate = scored_records
    _set_partial(candidate, _event_index(candidate, "saw", 84), 10**400)
    baseline_path, candidate_path = tmp_path / "baseline.json", tmp_path / "candidate.json"
    baseline_path.write_text(json.dumps(baseline))
    candidate_path.write_text(json.dumps(candidate))
    report = tmp_path / "delta.json"
    done = subprocess.run(
        [sys.executable, str(compare.ROOT / "tools" / "compare_m5a_i2s_candidate.py"),
         str(baseline_path), str(candidate_path), "--out", str(report)],
        capture_output=True, text=True)
    assert done.returncode == 2, done.stderr
    assert "Traceback" not in done.stderr
    assert ("compare_m5a_i2s_candidate: REFUSED -- candidate saw/84 harmonic partial h2 "
            "cannot be read as a float") in done.stdout
    assert not report.exists()
