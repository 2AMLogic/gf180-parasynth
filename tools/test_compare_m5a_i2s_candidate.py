import copy
import hashlib
import json
import subprocess
import sys

import pytest

import compare_m5a_i2s_candidate as compare
import mono_m5a_score as m5a


def _record(*, ref="a", engine="integrated-rtl", events=None, harmonic=-2.0, case_id="M5A"):
    units = {"Pitch": "cents", "Harmonic shape": "dB", "Foldback energy": "dB",
             "Envelope attack": "ms", "Envelope release": "ms", "Gain": "dB", "Clipping": "%"}
    metrics = {name: {"error": 0.5, "tolerance": limit, "units": units[name],
                      "tolerance_basis": basis, "valid": True}
               for name, (limit, basis) in m5a.TOLERANCES.items()}
    if events is None:
        events = [_event("saw", 84, alias=0.0, harmonic=harmonic)]
    # Clipping 0.5 % against a 0.01 % limit, so THE BOARD fails this record --
    # which is the state it stores. The provenance is the complete set the
    # board requires for a verdict (#1601).
    return {"engine": engine, "case_id": case_id, "scorecard_state": "fail",
            "analysis_version": m5a.ANALYSIS_VERSIONS[case_id],
            "reference_profile": "frozen Mini V3",
            "tolerance_policy": copy.deepcopy(m5a.TOLERANCES),
            "metrics": metrics, "diagnostics": {
                "reference_sha256": ref, "reference_manifest_sha256": hashlib.sha256(
                    m5a.MANIFESTS[case_id].read_bytes()).hexdigest(),
                "decoded_i2s_sha256": "audio", "events": events},
            "provenance": {"engine": engine, "worktree": {"commit": "deadbeef"},
                           "command": "tools/score_m5a_i2s.py --case M5A",
                           "inputs": {"docs/scorecard/cases.csv": "sha256:1"},
                           "config": {"filter_config": "causal reconstructed 2x, headroom preserved",
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
    (lambda rec: rec.update(analysis_version="m5a-score-v999"), "was scored by"),
    (lambda rec: rec["metrics"]["Gain"].update(tolerance=30.0), "measurement contract"),
    (lambda rec: rec["tolerance_policy"].update(Gain=(30.0, "loose")), "tolerance policy"),
    (lambda rec: rec.update(reference_profile="different patch"), "reference profiles differ"),
])
def test_refuses_invalid_properties_or_incomparable_measurement_basis(mutation, match):
    baseline, candidate = _record(), _record()
    mutation(candidate)
    with pytest.raises(m5a.Refused, match=match):
        compare.compare_records(baseline, candidate)


@pytest.mark.parametrize("mutation,match", [
    (lambda rec: rec.update(provenance=None), "provenance"),
    (lambda rec: rec.update(provenance=[]), "provenance"),
    (lambda rec: rec.update(provenance="frozen Mini V3"), "provenance"),
    (lambda rec: rec.update(provenance={}), "provenance.*disagree about the engine"),
    (lambda rec: rec["provenance"].update(config=None), "provenance config"),
    (lambda rec: rec["provenance"].update(config=[]), "provenance config"),
])
def test_refuses_null_or_non_object_baseline_provenance_or_config(mutation, match):
    baseline, candidate = _record(), _record()
    mutation(baseline)
    with pytest.raises(m5a.Refused, match=f"baseline {match}"):
        compare.compare_records(baseline, candidate)


@pytest.mark.parametrize("mutation,match", [
    (lambda rec: rec.update(provenance=None), "provenance"),
    (lambda rec: rec.update(provenance=[]), "provenance"),
    (lambda rec: rec.update(provenance="frozen Mini V3"), "provenance"),
    (lambda rec: rec.update(provenance={}), "provenance.*disagree about the engine"),
    (lambda rec: rec["provenance"].update(config=None), "provenance config"),
    (lambda rec: rec["provenance"].update(config=[]), "provenance config"),
])
def test_refuses_null_or_non_object_candidate_provenance_or_config(mutation, match):
    baseline, candidate = _record(), _record()
    mutation(candidate)
    with pytest.raises(m5a.Refused, match=f"candidate {match}"):
        compare.compare_records(baseline, candidate)


# ---- THE BOARD's verdict, recomputed -- never the record's own (#1601) ----
# Until #1601 this gate trusted a stored `scorecard_state`, never asked the
# board, and accepted records the board grades `no verdict`.

def _committed(name):
    return json.loads((compare.ROOT / "docs/scorecard" / name).read_text())


def test_compares_the_committed_m5b_confirmation_pair():
    """#282 confirms an M5A-selected change on the untouched M5B condition, and
    `score_m5a_i2s.py` scores both cases. The gate hardcoded M5A, so no M5B
    record could ever be compared."""
    baseline = _committed("mono-pulse-2x/rtl/M5B.json")
    candidate = _committed("m5-artifacts-333/pulse2x-rtl/M5B.json")
    report = compare.compare_records(baseline, candidate)
    assert report["case_id"] == "M5B"
    assert report["baseline"]["scorecard_state"] == "fail"
    assert report["candidate"]["scorecard_state"] == "fail"
    assert report["comparison"]["case_passes"] is False


def test_refuses_an_m5a_record_against_an_m5b_one():
    with pytest.raises(m5a.Refused, match="M5A and candidate is M5B"):
        compare.compare_records(_record(), _record(case_id="M5B"))


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("state", ["pass", None])
def test_refuses_a_record_whose_stored_state_contradicts_the_board(side, state):
    """The record fails on the board (Clipping). A stored `pass` contradicts
    it; a stored null is an unknown in the place where a result belongs."""
    baseline, candidate = _record(), _record()
    (baseline if side == "baseline" else candidate)["scorecard_state"] = state
    with pytest.raises(m5a.Refused, match=f"{side} stores state {state!r} but the board "
                                          r"evaluates it as 'fail'"):
        compare.compare_records(baseline, candidate)


def test_reports_the_boards_state_whether_or_not_the_record_stores_one():
    baseline, candidate = _record(), _record()
    candidate.pop("scorecard_state")
    report = compare.compare_records(baseline, candidate)
    assert report["baseline"]["scorecard_state"] == "fail"
    assert report["candidate"]["scorecard_state"] == "fail"


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("field,why", [
    ("worktree", "no provenance: worktree"),
    ("command", "no provenance: command"),
    ("inputs", "no provenance inputs"),
])
def test_refuses_a_record_the_board_grades_no_verdict_for_want_of_provenance(side, field, why):
    baseline, candidate = _record(), _record()
    (baseline if side == "baseline" else candidate)["provenance"].pop(field)
    with pytest.raises(m5a.Refused, match=f"{side}.*{why}"):
        compare.compare_records(baseline, candidate)


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("engine", ["fixed-model", "absent"])
def test_refuses_a_record_whose_provenance_disagrees_about_the_engine(side, engine):
    baseline, candidate = _record(), _record()
    provenance = (baseline if side == "baseline" else candidate)["provenance"]
    if engine == "absent":
        provenance.pop("engine")
    else:
        provenance["engine"] = engine
    with pytest.raises(m5a.Refused, match=f"{side}.*disagree about the engine"):
        compare.compare_records(baseline, candidate)


@pytest.mark.parametrize("mutation,match", [
    (lambda rec: rec.update(metrics=[]), "carries no metrics"),
    (lambda rec: rec["metrics"].update(Pitch=5), "Pitch is malformed"),
    (lambda rec: rec["metrics"]["Pitch"].update(valid=1), "Pitch is invalid"),
    (lambda rec: rec["metrics"]["Pitch"].pop("valid"), "Pitch is invalid"),
    (lambda rec: rec["metrics"]["Pitch"].update(value=float("nan")),
     r"Pitch \(invalid number: value"),
    (lambda rec: rec["metrics"]["Pitch"].update(reference=float("inf")),
     r"Pitch \(invalid number: reference"),
    (lambda rec: rec["metrics"]["Pitch"].update(error=False),
     r"Pitch \(invalid number: error is False\)"),
    (lambda rec: rec["metrics"]["Pitch"].update(tolerance="1.0"),
     r"Pitch \(invalid number: tolerance"),
    (lambda rec: rec["metrics"]["Pitch"].update(error=10**400),
     "no pass/fail verdict on the board: int too large"),
    (lambda rec: rec["metrics"]["Clipping"].update(error=1.7976931348623157e308),
     "no finite distance on the board"),
])
def test_refuses_a_property_the_board_cannot_score(mutation, match):
    """Compared as data until #1601: a NaN `value`, an infinite `reference`,
    `error: false` (which read as 0.0, a perfect score), `tolerance: "1.0"`,
    and an error so large the board's distance for it overflows to `inf`.
    Ended in a traceback until #1601: an integer beyond float range. Already
    refused, under another message: `metrics: []`, a property that is not an
    object, and the two `valid` rows."""
    baseline, candidate = _record(), _record()
    mutation(candidate)
    with pytest.raises(m5a.Refused, match=f"candidate.*{match}"):
        compare.compare_records(baseline, candidate)


def test_a_record_whose_stored_state_agrees_with_the_board_is_compared():
    """The control for the stored-state refusal tests above: the gate refuses
    a record whose stored state CONTRADICTS the board, not one for carrying
    a `scorecard_state` at all."""
    baseline, candidate = _record(), _record()
    assert candidate["scorecard_state"] == "fail"
    assert compare.compare_records(baseline, candidate)["comparison"]["case_passes"] is False


@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_a_non_object_json_document_is_refused_not_a_crash(side):
    """A valid JSON file that is not an object (`[]`) reached `.get` and died
    with AttributeError: exit 1 and a traceback, where REFUSED is exit 2."""
    baseline, candidate = [], _record()
    if side == "candidate":
        baseline, candidate = _record(), []
    with pytest.raises(m5a.Refused, match=f"{side} is not a record"):
        compare.compare_records(baseline, candidate)


@pytest.mark.parametrize("limit", ["3.0", True])
def test_refuses_a_tolerance_policy_limit_that_only_reaches_the_limit_by_coercion(limit):
    """The frozen contract is three dB. `"3.0"` was coerced to it by a raw
    `float()`; `True` reads as 1.0. Neither is the JSON number 3.0."""
    candidate = _record()
    candidate["tolerance_policy"]["Gain"] = [limit, m5a.TOLERANCES["Gain"][1]]
    with pytest.raises(m5a.Refused, match="candidate tolerance policy for Gain"):
        compare.compare_records(_record(), candidate)


_PER_NOTE_NUMBERS = [
    ("pitch_cents_from_midi", "model_minus_reference"),
    ("envelope_ms", "attack_model"), ("envelope_ms", "attack_reference"),
    ("envelope_ms", "release_model"), ("envelope_ms", "release_reference"),
    ("gain_dbfs", "model"), ("gain_dbfs", "reference"),
    ("harmonic_error_db_model_minus_reference", "h2"),
]


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("section,key", _PER_NOTE_NUMBERS)
@pytest.mark.parametrize("value", [False, True, "0.5", float("nan")])
def test_refuses_a_per_note_measurement_that_is_not_a_json_number(side, section, key, value):
    """`False` read as 0.0 -- a perfect score -- and `"0.5"` as 0.5."""
    baseline, candidate = _record(), _record()
    record = baseline if side == "baseline" else candidate
    record["diagnostics"]["events"][0][section][key] = value
    with pytest.raises(m5a.Refused, match=f"{side}:saw/84 .*must be (?:a JSON number|finite)"):
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


# --- review of #1638: four controls, each starting red ----------------------

def _relabel(record, case_id):
    record["case_id"] = case_id
    return record


def test_refuses_the_committed_m5a_pair_relabelled_as_m5b():
    """Only the headers change; the events (MIDI 84/96) and the manifest digest
    are still M5A's. The genuine pair above is the positive control."""
    baseline = _relabel(_committed("mono-pulse-2x/rtl/M5A.json"), "M5B")
    candidate = _relabel(_committed("m5-artifacts-333/pulse2x-rtl/M5A.json"), "M5B")
    with pytest.raises(m5a.Refused, match="says it is M5B.*manifest"):
        compare.compare_records(baseline, candidate)


def test_refuses_the_relabelled_pair_with_its_manifest_digest_also_edited():
    """Second variant of the relabel: header AND manifest digest both say M5B,
    but the record is still scored by M5A's analysis version, so it
    contradicts itself in a field the comparator already reads."""
    m5b_digest = hashlib.sha256(m5a.MANIFESTS["M5B"].read_bytes()).hexdigest()
    baseline = _relabel(_committed("mono-pulse-2x/rtl/M5A.json"), "M5B")
    candidate = _relabel(_committed("m5-artifacts-333/pulse2x-rtl/M5A.json"), "M5B")
    for record in (baseline, candidate):
        record["diagnostics"]["reference_manifest_sha256"] = m5b_digest
    with pytest.raises(m5a.Refused, match="says it is M5B but was scored by"):
        compare.compare_records(baseline, candidate)


@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_refuses_a_changed_scoring_direction_on_one_property(side):
    """Pitch error -2 cents against a `defect ceiling` clamps to zero: the same
    values flip from fail to pass with no change in the device."""
    baseline, candidate = _record(), _record()
    for record in (baseline, candidate):
        record["metrics"]["Pitch"]["error"] = -2.0
    (baseline if side == "baseline" else candidate)["metrics"]["Pitch"]["purpose"] = "defect ceiling"
    with pytest.raises(m5a.Refused, match=rf"{side} property Pitch is scored as 'defect ceiling'"):
        compare.compare_records(baseline, candidate)


def test_an_explicit_match_purpose_is_the_same_as_an_absent_one():
    baseline, candidate = _record(), _record()
    candidate["metrics"]["Pitch"]["purpose"] = "match"
    compare.compare_records(baseline, candidate)


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("value", [None, 10**400], ids=["null", "overflow"])
def test_refuses_a_harmonic_partial_that_is_not_a_finite_json_number(side, value):
    """Was exit 1 with TypeError / OverflowError from a raw float() in
    measure_m5a_filter_oversample, reached before the per-note validation."""
    baseline, candidate = _record(), _record()
    record = baseline if side == "baseline" else candidate
    record["diagnostics"]["events"][0]["harmonic_error_db_model_minus_reference"]["h2"] = value
    with pytest.raises(m5a.Refused, match=rf"{side}:saw/84 harmonic_error_db_model_minus_reference\['h2'\]"):
        compare.compare_records(baseline, candidate)


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
                       match=rf"{side}:saw/84 harmonic_error_db_model_minus_reference\['h2'\]"):
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
        assert ("compare_m5a_i2s_candidate: REFUSED -- candidate:pulse/84 "
                "harmonic_error_db_model_minus_reference['h2']") in capsys.readouterr().out
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
    assert ("compare_m5a_i2s_candidate: REFUSED -- candidate:saw/84 "
            "harmonic_error_db_model_minus_reference['h2']") in done.stdout
    assert not report.exists()
