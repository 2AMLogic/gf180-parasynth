"""The selection check must be able to fail: controls on the committed records."""
import copy
import json

import pytest

import check_m1a_selection as sel


def _inputs():
    record = json.loads(sel.RECORD.read_text())
    candidate = json.loads(sel.CANDIDATE.read_text())
    baseline = json.loads(sel.BASELINE.read_text())
    manifest, _ = sel.bass.load_reference()
    return record, candidate, baseline, manifest


def test_promoted_record_is_the_selected_candidate():
    assert sel.main([]) == 0


def test_controls_turn_it_red():
    record, candidate, baseline, manifest = _inputs()
    # the pre-selection baseline presented as the record
    stale = copy.deepcopy(record)
    stale["diagnostics"]["configuration"]["patch_id"] = "m1a-provisional-v1"
    stale["diagnostics"]["model_audio_sha256"] = baseline["sha256"]
    assert any("patch_id" in p for p in sel.check(stale, candidate, baseline, manifest))
    # one property moved in the fifth decimal
    moved = copy.deepcopy(record)
    moved["diagnostics"]["properties"]["Gain"]["error"] += 1e-5
    assert any("Gain" in p for p in sel.check(moved, candidate, baseline, manifest))
    # one partial moved beyond the event tolerance
    drift = copy.deepcopy(record)
    drift["diagnostics"]["events"][2]["harmonic_error_db_model_minus_reference"]["h8"] += 1e-3
    assert any("events" in p for p in sel.check(drift, candidate, baseline, manifest))
    # a candidate that lost a baseline pass violates the preservation policy
    lossy = copy.deepcopy(candidate)
    lossy["measurements"]["properties"]["Pitch"]["error"] = 2.0
    assert any("preservation" in p for p in sel.check(record, lossy, baseline, manifest))


def test_event_deviation_refuses_structure_changes():
    assert sel.event_deviation({"a": [1., 2.]}, {"a": [1., 2.5]}) == .5
    assert sel.event_deviation({"a": 1.}, {"b": 1.}) is None
    assert sel.event_deviation({"a": None}, {"a": 0.}) is None
    assert sel.event_deviation({"a": "x"}, {"a": "y"}) is None


# ---- a record missing a key the check reads with [] is REFUSED, not a
# traceback (#1604). One control per key: each deletes ONE key from the
# committed record and runs the tool's own main().
_MISSING = object()


def _main_over(monkeypatch, tmp_path, capsys, which, key, value=_MISSING):
    """`main()` over the committed records with ONE key of ONE record deleted
    (#1604) or replaced by `value` (#1679)."""
    record, candidate, baseline, _ = _inputs()
    records = {"record": record, "candidate": candidate, "baseline": baseline}
    if value is _MISSING:
        del records[which][key]
    else:
        records[which][key] = value
    paths = {}
    for name, obj in records.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(obj))
    monkeypatch.setattr(sel, "CANDIDATE", paths["candidate"])
    monkeypatch.setattr(sel, "BASELINE", paths["baseline"])
    code = sel.main(["--record", str(paths["record"])])
    return code, capsys.readouterr().out


@pytest.mark.parametrize("which, key", [
    ("record", "diagnostics"), ("record", "audio"), ("record", "metrics"),
    ("candidate", "patch"), ("candidate", "sha256"),
    ("candidate", "measurements"), ("candidate", "metrics"),
    ("baseline", "measurements"),
])
def test_a_record_missing_a_key_the_check_reads_is_refused(monkeypatch, tmp_path, capsys,
                                                            which, key):
    code, out = _main_over(monkeypatch, tmp_path, capsys, which, key)
    assert code == 2 and "REFUSED" in out and f"'{key}'" in out


# ---- a key that is PRESENT but of a type the check dereferences is REFUSED
# the same way (#1679). Measured on the tree before the repair, one control
# per key and wrong type: each of these was a TypeError out of main(), exit 1.
@pytest.mark.parametrize("which, key, value", [
    ("record", "diagnostics", 5), ("record", "diagnostics", []),
    ("record", "audio", 5),
    ("record", "metrics", "x"),
    ("candidate", "sha256", 5),
    ("candidate", "measurements", []), ("candidate", "measurements", True),
    ("candidate", "metrics", 5),
    ("baseline", "measurements", 5),
])
def test_a_record_key_of_a_type_the_check_dereferences_is_refused(monkeypatch, tmp_path, capsys,
                                                                  which, key, value):
    code, out = _main_over(monkeypatch, tmp_path, capsys, which, key, value)
    assert code == 2 and "REFUSED" in out and f"'{key}'" in out
    assert "MISMATCH" not in out


@pytest.mark.parametrize("value", [5, "x", [], True])
def test_a_candidate_patch_of_the_wrong_type_is_a_mismatch_not_a_refusal(monkeypatch, tmp_path,
                                                                         capsys, value):
    # measured, and left alone on purpose: `patch` is only COMPARED (through
    # `_norm`), never dereferenced, so every wrong type is a clean exit 1 that
    # names it. The check's question -- is this the selected candidate? -- is
    # answered, not avoided, so `patch` declares no kind.
    code, out = _main_over(monkeypatch, tmp_path, capsys, "candidate", "patch", value)
    assert code == 1 and "MISMATCH" in out and "patch differs" in out
    assert "REFUSED" not in out
